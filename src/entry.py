"""Private meal train API and static shell for the Baby Train Worker."""

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from js import Date, Object
from pyodide.ffi import to_js as _to_js
from workers import Response, WorkerEntrypoint

BASE = "/babytrain"
COOKIE_NAME = "babytrain_session"
SESSION_SECONDS = 30 * 24 * 60 * 60
MAX_JSON_BYTES = 8192
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _headers(extra=None):
    headers = {
        "Cache-Control": "no-store, private",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; img-src 'self' blob:; object-src 'none'; "
            "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        ),
    }
    if extra:
        headers.update(extra)
    return headers


def _json(data, status=200, extra=None):
    return Response.from_json(data, status=status, headers=_headers(extra))


def _error(message, status):
    return _json({"error": message}, status)


def _now_iso():
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _safe_equal(left, right):
    return hmac.compare_digest(str(left).encode(), str(right).encode())


def _tojs(value):
    return _to_js(value, dict_converter=Object.fromEntries)


class BodyTooLarge(ValueError):
    pass


async def _read_body(request, limit):
    length = request.headers.get("Content-Length")
    if length and int(length) > limit:
        raise BodyTooLarge
    if request.body is None or not request.body:
        return b""
    reader = request.body.getReader()
    data = bytearray()
    try:
        while True:
            chunk = await reader.read()
            if chunk.done:
                return bytes(data)
            if len(data) + chunk.value.byteLength > limit:
                await reader.cancel()
                raise BodyTooLarge
            data.extend(chunk.value.to_bytes())
    finally:
        reader.releaseLock()


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        method = request.method.upper()
        path = urlsplit(request.url).path
        if path == BASE:
            return Response(status=308, headers=_headers({"Location": path + "/"}))

        if path == BASE + "/api" or path.startswith(BASE + "/api/"):
            return await self._api(request, path, method)
        if not path.startswith(BASE + "/"):
            return Response(status=404, headers=_headers())

        # Static assets contain only the invitation shell, never private records.
        try:
            response = await self.env.ASSETS.fetch(request.js_object)
            headers = {
                name: response.headers[name]
                for name in ("Content-Type", "Location")
                if name in response.headers
            }
            body = response.body if response.body else None
            return Response(body, status=response.status, headers=_headers(headers))
        except Exception:
            return Response(
                "Not found",
                status=404,
                headers=_headers({"Content-Type": "text/plain; charset=utf-8"}),
            )

    def _key(self, name):
        value = getattr(self.env, name, None)
        return value if isinstance(value, str) and len(value) >= 32 else ""

    def _signature(self, role, payload):
        owner_key = self._key("OWNER_KEY")
        invite_key = self._key("INVITE_KEY")
        if not owner_key or (role == "guest" and not invite_key):
            raise ValueError("Session keys are unavailable")
        if invite_key and _safe_equal(invite_key, owner_key):
            raise ValueError("Session keys must be distinct")
        scope = f"guest:{invite_key}:" if role == "guest" else "owner:"
        return _b64(
            hmac.new(
                owner_key.encode(), (scope + payload).encode(), hashlib.sha256
            ).digest()
        )

    def _make_cookie(self, role):
        expiry = int(datetime.now(UTC).timestamp()) + SESSION_SECONDS
        payload = _b64(
            json.dumps({"role": role, "exp": expiry}, separators=(",", ":")).encode()
        )
        return payload + "." + self._signature(role, payload)

    def _session(self, request):
        cookie_header = request.headers.get("Cookie") or ""
        token = None
        for part in cookie_header.split(";"):
            key, sep, value = part.strip().partition("=")
            if sep and key == COOKIE_NAME:
                token = value
                break
        if not token or len(token) > 256:
            return None
        try:
            payload, supplied = token.split(".", 1)
            data = json.loads(_unb64(payload))
            if data.get("role") not in ("guest", "owner") or int(
                data.get("exp", 0)
            ) < int(datetime.now(UTC).timestamp()):
                return None
            role = data["role"]
            return (
                role if _safe_equal(supplied, self._signature(role, payload)) else None
            )
        except Exception:
            return None

    def _cookie_header(self, request, value, max_age=SESSION_SECONDS):
        cookie = f"{COOKIE_NAME}={value}; Path={BASE}; HttpOnly; SameSite=Lax; Max-Age={max_age}"
        if urlsplit(request.url).hostname not in ("localhost", "127.0.0.1", "::1"):
            cookie += "; Secure"
        return cookie

    def _same_origin(self, request):
        origin = request.headers.get("Origin")
        if not origin:
            return False
        url = urlsplit(request.url)
        return origin == f"{url.scheme}://{url.netloc}"

    async def _body_json(self, request):
        raw = await _read_body(request, MAX_JSON_BYTES)
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("Expected a JSON object")
        return value

    async def _api(self, request, path, method):
        if method not in ("GET", "HEAD", "POST", "DELETE"):
            return _error("Method not allowed", 405)

        # Exchange credentials and logout are intentionally the only unauthenticated API calls.
        if path == BASE + "/api/session" and method == "POST":
            if not self._same_origin(request):
                return _error("Same-origin request required", 403)
            try:
                ip = request.headers.get("CF-Connecting-IP") or "local"
                attempt = await self.env.SIGN_IN_LIMIT.limit(_tojs({"key": ip}))
                if not attempt.success:
                    return _error("Too many attempts. Try again in a minute.", 429)
                body = await self._body_json(request)
            except Exception:
                return _error("Invalid request body", 400)
            key = body.get("key")
            if not isinstance(key, str) or not key or len(key) > 256:
                return _error("Invalid key", 401)
            owner_key = self._key("OWNER_KEY")
            invite_key = self._key("INVITE_KEY")
            if not owner_key or not invite_key or _safe_equal(owner_key, invite_key):
                return _error("Sign-in is temporarily unavailable", 503)
            guest_password = getattr(self.env, "GUEST_PASSWORD", None)
            role = (
                "owner"
                if _safe_equal(key, owner_key)
                else "guest"
                if _safe_equal(key, invite_key)
                or (guest_password and _safe_equal(key, guest_password))
                else None
            )
            if role is None:
                return _error("Invalid key", 401)
            return _json(
                {"role": role},
                extra={
                    "Set-Cookie": self._cookie_header(request, self._make_cookie(role))
                },
            )

        if path == BASE + "/api/session" and method == "DELETE":
            if not self._same_origin(request):
                return _error("Same-origin request required", 403)
            return Response(
                status=204,
                headers=_headers({"Set-Cookie": self._cookie_header(request, "", 0)}),
            )

        role = self._session(request)
        if role is None:
            return _error("Authentication required", 401)

        if method in ("POST", "DELETE") and not self._same_origin(request):
            return _error("Same-origin request required", 403)

        if path == BASE + "/api/state" and method == "GET":
            try:
                claims = await self.env.DB.prepare(
                    "SELECT date, name, dish FROM claims ORDER BY date"
                ).all()
                comments = await self.env.DB.prepare(
                    "SELECT id, name, text, created_at AS createdAt FROM comments ORDER BY created_at DESC, rowid DESC LIMIT 500"
                ).all()
                today, start, days = self._schedule()
                return _json(
                    {
                        "role": role,
                        "startDate": start.isoformat(),
                        "days": days,
                        "today": today.isoformat(),
                        "claims": claims.results,
                        "comments": comments.results,
                    }
                )
            except Exception:
                return _error("State is temporarily unavailable", 503)

        if path == BASE + "/api/claims" and method == "POST":
            try:
                body = await self._body_json(request)
                claim_date = body.get("date")
                name = body.get("name")
                dish = body.get("dish", "")
                if not isinstance(claim_date, str) or not DATE_RE.fullmatch(claim_date):
                    return _error("Invalid date", 400)
                day = date.fromisoformat(claim_date)
                today, start, days = self._schedule()
                if day < today:
                    return _error("Past dates cannot be claimed", 400)
                if not 0 <= (day - start).days < days:
                    return _error("Date is outside the meal train", 400)
                if (
                    not isinstance(name, str)
                    or not name.strip()
                    or len(name.strip()) > 60
                ):
                    return _error("Name must be 1 to 60 characters", 400)
                if not isinstance(dish, str) or len(dish.strip()) > 120:
                    return _error("Dish must be at most 120 characters", 400)
                claim = {"date": claim_date, "name": name.strip(), "dish": dish.strip()}
                result = (
                    await self.env.DB.prepare(
                        "INSERT INTO claims (date, name, dish, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(date) DO NOTHING"
                    )
                    .bind(claim["date"], claim["name"], claim["dish"], _now_iso())
                    .run()
                )
                if result.meta.changes == 0:
                    return _error("That date is already claimed", 409)
                return _json(claim, 201)
            except Exception as exc:
                if isinstance(exc, ValueError):
                    return _error("Invalid request body", 400)
                return _error("Claim could not be saved", 503)

        if path == BASE + "/api/comments" and method == "POST":
            try:
                body = await self._body_json(request)
                name, text = body.get("name"), body.get("text")
                if (
                    not isinstance(name, str)
                    or not name.strip()
                    or len(name.strip()) > 60
                ):
                    return _error("Name must be 1 to 60 characters", 400)
                if (
                    not isinstance(text, str)
                    or not text.strip()
                    or len(text.strip()) > 1000
                ):
                    return _error("Comment must be 1 to 1000 characters", 400)
                item = {
                    "id": secrets.token_urlsafe(18),
                    "name": name.strip(),
                    "text": text.strip(),
                    "createdAt": _now_iso(),
                }
                await (
                    self.env.DB.prepare(
                        "INSERT INTO comments (id, name, text, created_at) VALUES (?, ?, ?, ?)"
                    )
                    .bind(item["id"], item["name"], item["text"], item["createdAt"])
                    .run()
                )
                return _json(item, 201)
            except Exception as exc:
                if isinstance(exc, (ValueError, json.JSONDecodeError)):
                    return _error("Invalid request body", 400)
                return _error("Comment could not be saved", 503)

        return _error("Not found", 404)

    def _schedule(self):
        today = date.fromisoformat(self._today())
        start = str(getattr(self.env, "START_DATE", "") or "").strip()
        days = max(1, min(60, int(getattr(self.env, "TRAIN_DAYS", 21))))
        return today, date.fromisoformat(start) if start else today, days

    def _today(self):
        try:
            zone = str(
                getattr(self.env, "TRAIN_TIME_ZONE", "America/Los_Angeles")
                or "America/Los_Angeles"
            )
            date_text = Date.new().toLocaleDateString(
                "en-CA",
                _tojs(
                    {
                        "timeZone": zone,
                        "year": "numeric",
                        "month": "2-digit",
                        "day": "2-digit",
                    }
                ),
            )
            # Some ICU builds return slashes even for en-CA; normalize those too.
            if DATE_RE.fullmatch(date_text):
                return date_text
            pieces = re.findall(r"\d+", date_text)
            if len(pieces) == 3:
                if len(pieces[0]) == 4:
                    return f"{pieces[0]}-{int(pieces[1]):02d}-{int(pieces[2]):02d}"
                return f"{pieces[2]}-{int(pieces[0]):02d}-{int(pieces[1]):02d}"
        except Exception:
            pass
        return date.today().isoformat()
