const fs = require('fs');
const path = require('path');
const db = require('./db');
const { SCRAPERS_DIR } = require('./config');

/**
 * Scan the scrapers/ directory for *.py files and sync them into the DB.
 * Existing rows are kept (their enabled flag is preserved); newly found
 * files are inserted as enabled. Rows for files that no longer exist are
 * removed. Nothing is hardcoded.
 */
function scanScrapers() {
  const entries = fs.readdirSync(SCRAPERS_DIR, { withFileTypes: true });
  const pyFiles = entries
    .filter((e) => e.isFile() && e.name.toLowerCase().endsWith('.py'))
    .map((e) => e.name)
    .sort();

  const insert = db.prepare(
    `INSERT OR IGNORE INTO scrapers (filename, enabled) VALUES (?, 1)`
  );
  for (const name of pyFiles) insert.run(name);

  // Remove rows for scrapers whose files no longer exist.
  const stale = db.prepare(`SELECT filename FROM scrapers`).all()
    .filter((row) => !pyFiles.includes(row.filename));
  const del = db.prepare(`DELETE FROM scrapers WHERE filename = ?`);
  for (const row of stale) del.run(row.filename);

  return pyFiles;
}

/** Return all scrapers with DB state. */
function getScrapers() {
  return db.prepare(`SELECT filename, enabled FROM scrapers ORDER BY filename`).all();
}

module.exports = { scanScrapers, getScrapers };
