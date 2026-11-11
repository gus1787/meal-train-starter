PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS claims (
    date TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    dish TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS comments_created_at_idx ON comments(created_at DESC);
