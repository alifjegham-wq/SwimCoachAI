-- Swim Coach AI — Cloudflare D1 schema
-- Apply with:  npx wrangler d1 execute swim-coach-ai --remote --file=./schema.sql

CREATE TABLE IF NOT EXISTS users (
  id        TEXT PRIMARY KEY,
  email     TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  salt      TEXT NOT NULL,
  created   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token   TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created INTEGER NOT NULL,
  expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- One row per user holds their history + profiles as JSON blobs
-- (mirrors the local helper's file storage, so the same client code works).
CREATE TABLE IF NOT EXISTS user_data (
  user_id       TEXT PRIMARY KEY,
  history_json  TEXT,
  profiles_json TEXT,
  updated       INTEGER
);
