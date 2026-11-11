(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const state = {
    role: "guest",
    startDate: "",
    days: 21,
    today: "",
    claims: [],
    comments: [],
    firstIndex: 0,
  };
  const calendar = $("calendar");
  const setMessage = (id, message = "") => {
    $(id).textContent = message;
  };
  async function request(path, options = {}) {
    const response = await fetch(`/babytrain/api${path}`, {
      credentials: "same-origin",
      ...options,
    });
    if (!response.ok) {
      let detail = "";
      try {
        detail = (await response.json()).error || "";
      } catch (_) {}
      const error = new Error(
        detail ||
          (response.status === 401
            ? "Your session has ended. Please sign in again."
            : `Something went wrong (${response.status}). Please try again.`),
      );
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    return response.json();
  }
  const utcDate = (iso) => {
    const [year, month, day] = iso.split("-").map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  };
  const dayNumber = (iso) => Math.floor(utcDate(iso).getTime() / 86400000);
  const addDays = (iso, offset) => {
    const date = utcDate(iso);
    date.setUTCDate(date.getUTCDate() + offset);
    return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
  };
  const formatDate = (iso, options) =>
    new Intl.DateTimeFormat(undefined, { ...options, timeZone: "UTC" }).format(
      utcDate(iso),
    );
  function showLocked(message = "") {
    $("app-content").hidden = true;
    $("locked-panel").hidden = false;
    setMessage("access-message", message);
  }
  function showApp() {
    $("locked-panel").hidden = true;
    $("app-content").hidden = false;
  }
  function make(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function renderCalendar() {
    renderDishes();
    calendar.replaceChildren();
    const claims = new Map(state.claims.map((claim) => [claim.date, claim]));
    const firstFuture = Math.max(
      0,
      Math.min(
        state.days - 1,
        dayNumber(state.today) - dayNumber(state.startDate),
      ),
    );
    state.firstIndex = Math.min(state.firstIndex, Math.max(0, state.days - 1));
    for (let i = 0; i < state.days; i++) {
      const date = addDays(state.startDate, i);
      const past = date < state.today;
      const claim = claims.get(date);
      const card = make(
        "article",
        `day-card${i === firstFuture ? " is-first" : ""}${past ? " is-past" : ""}${claim ? " is-claimed" : ""}`,
      );
      card.dataset.date = date;
      const day = make(
        "div",
        "date-weekday",
        formatDate(date, { weekday: "long" }),
      );
      const numeral = make(
        "div",
        "date-number",
        formatDate(date, { month: "short", day: "numeric" }),
      );
      card.append(day, numeral);
      const status = make("div", "day-status");
      status.append(
        make("span", "status-dot"),
        document.createTextNode(claim ? "Claimed" : past ? "Past" : "Open"),
      );
      card.append(status);
      if (claim) {
        card.append(make("div", "claim-name", claim.name));
        if (claim.dish) card.append(make("div", "claim-dish", claim.dish));
      } else if (!past) {
        const form = document.createElement("form");
        form.className = "inline-claim";
        const name = document.createElement("input");
        name.name = "name";
        name.maxLength = 60;
        name.required = true;
        name.autocomplete = "given-name";
        name.placeholder = "Your first name";
        name.setAttribute(
          "aria-label",
          `Your first name for ${formatDate(date, { month: "long", day: "numeric" })}`,
        );
        const dish = document.createElement("input");
        dish.name = "dish";
        dish.maxLength = 120;
        dish.placeholder = "Dish (optional)";
        dish.setAttribute("aria-label", "Dish (optional)");
        const button = make("button", "button button-dark", "Save dinner");
        button.type = "submit";
        form.append(name, dish, button);
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          button.disabled = true;
          setMessage("meal-message");
          try {
            const result = await request("/claims", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                date,
                name: name.value.trim(),
                dish: dish.value.trim() || undefined,
              }),
            });
            state.claims.push(result);
            renderCalendar();
            setMessage(
              "meal-message",
              "Dinner saved. Thank you for showing up.",
            );
          } catch (error) {
            button.disabled = false;
            setMessage("meal-message", error.message);
            if (error.status === 409) {
              try {
                const fresh = await request("/state");
                state.claims = fresh.claims;
                renderCalendar();
              } catch (_) {}
            }
          }
        });
        card.append(form);
      }
      calendar.append(card);
    }
    requestAnimationFrame(() => scrollToIndex(state.firstIndex, false));
  }
  function updateCalendarButtons() {
    const end = calendar.scrollWidth - calendar.clientWidth;
    $("previous-days").disabled = calendar.scrollLeft < 2;
    $("next-days").disabled = calendar.scrollLeft >= end - 2;
  }
  function scrollToIndex(index, smooth = true) {
    state.firstIndex = Math.max(0, Math.min(state.days - 1, index));
    const card = calendar.children[state.firstIndex];
    if (card)
      calendar.scrollTo({
        left: card.offsetLeft - calendar.offsetLeft - 2,
        behavior: smooth ? "smooth" : "auto",
      });
    updateCalendarButtons();
  }
  function renderDishes() {
    const list = $("dish-list");
    list.replaceChildren();
    const meals = state.claims
      .filter((claim) => claim.dish && claim.date >= state.startDate
        && claim.date < addDays(state.startDate, state.days))
      .sort((a, b) => a.date.localeCompare(b.date));
    $("empty-menu").hidden = meals.length > 0;
    for (const meal of meals) {
      const row = make("li", "dish-row");
      const date = make("time", "dish-date",
        formatDate(meal.date, { month: "short", day: "numeric" }));
      date.dateTime = meal.date;
      row.append(make("strong", "", meal.dish), make("span", "", meal.name), date);
      list.append(row);
    }
  }
  function renderComments() {
    const list = $("comment-list");
    list.replaceChildren();
    for (const comment of state.comments) {
      const item = make("li", "comment-item");
      const meta = make("div", "comment-meta");
      meta.append(make("strong", "", comment.name));
      if (comment.createdAt) {
        const time = make("time", "", formatCommentTime(comment.createdAt));
        time.dateTime = comment.createdAt;
        meta.append(time);
      }
      item.append(make("p", "", comment.text), meta);
      list.append(item);
    }
  }
  function formatCommentTime(value) {
    const d = new Date(value);
    return Number.isNaN(d.valueOf())
      ? ""
      : new Intl.DateTimeFormat(undefined, {
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }).format(d);
  }
  $("previous-days").addEventListener("click", () =>
    scrollToIndex(state.firstIndex - 3),
  );
  $("next-days").addEventListener("click", () =>
    scrollToIndex(state.firstIndex + 3),
  );
  calendar.addEventListener(
    "scroll",
    () => {
      if (!calendar.children.length) return;
      let nearestIndex = 0;
      let nearestDistance = Infinity;
      for (let i = 0; i < calendar.children.length; i++) {
        const distance = Math.abs(
          calendar.children[i].offsetLeft -
            calendar.offsetLeft -
            calendar.scrollLeft -
            2,
        );
        if (distance < nearestDistance) {
          nearestIndex = i;
          nearestDistance = distance;
        }
      }
      state.firstIndex = nearestIndex;
      updateCalendarButtons();
    },
    { passive: true },
  );
  calendar.addEventListener("keydown", (event) => {
    if (event.target !== calendar) return;
    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      scrollToIndex(state.firstIndex + (event.key === "ArrowRight" ? 1 : -1));
    }
  });
  const noteDialog = $("note-dialog");
  $("add-note").addEventListener("click", () => noteDialog.showModal());
  $("cancel-note").addEventListener("click", () => noteDialog.close());
  $("comment-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector("button");
    button.disabled = true;
    setMessage("comment-message");
    try {
      const comment = await request("/comments", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("comment-name").value.trim() || "A friend",
          text: $("comment-text").value.trim(),
        }),
      });
      state.comments.unshift(comment);
      renderComments();
      form.reset();
      noteDialog.close();
      $("comment-list").querySelector("li")?.scrollIntoView({ block: "nearest" });
    } catch (error) {
      setMessage("comment-message", error.message);
    } finally {
      button.disabled = false;
    }
  });
  $("owner-login").addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = $("owner-key");
    const button = event.currentTarget.querySelector("button");
    button.disabled = true;
    setMessage("access-message");
    try {
      const result = await request("/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: input.value }),
      });
      input.value = "";
      state.role = result.role;
      await loadState();
    } catch (error) {
      input.value = "";
      setMessage("access-message", error.message);
    } finally {
      button.disabled = false;
    }
  });
  async function loadState() {
    try {
      const data = await request("/state");
      Object.assign(state, data);
      state.firstIndex = Math.max(
        0,
        Math.min(
          state.days - 1,
          dayNumber(state.today) - dayNumber(state.startDate),
        ),
      );
      showApp();
      renderCalendar();
      renderComments();
    } catch (error) {
      if (error.status === 401) showLocked();
      else showLocked(error.message);
    }
  }
  async function start() {
    const match = location.hash.match(/(?:^#|&)invite=([^&]*)/);
    if (match) {
      history.replaceState(null, "", `${location.pathname}${location.search}`);
      try {
        const key = decodeURIComponent(match[1]);
        if (key) {
          const result = await request("/session", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ key }),
          });
          state.role = result.role;
        }
      } catch (error) {
        showLocked(
          error instanceof URIError
            ? "The invite link is malformed."
            : error.message,
        );
        return;
      }
    }
    await loadState();
  }
  start();
})();
