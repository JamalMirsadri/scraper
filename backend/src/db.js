const path = require('path');
const Database = require('better-sqlite3');

const db = new Database(path.join(__dirname, '..', 'data.sqlite'));
db.pragma('journal_mode = WAL');

db.exec(`
  CREATE TABLE IF NOT EXISTS scrapers (
    filename  TEXT PRIMARY KEY,
    enabled   INTEGER NOT NULL DEFAULT 1,
    added_at  TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS executions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL,
    status      TEXT NOT NULL,            -- running | success | failed | error
    exit_code   INTEGER,
    stdout      TEXT,
    stderr      TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT
  );

  CREATE INDEX IF NOT EXISTS idx_executions_filename ON executions (filename);
`);

module.exports = db;
