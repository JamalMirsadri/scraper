const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const db = require('./db');
const { SCRAPERS_DIR, OUTPUT_DIR, PYTHON_BIN, MAX_CAPTURED_BYTES } = require('./config');

// In-memory guard: filename -> { executionId, child }
// Prevents the same scraper from running twice at the same time.
const running = new Map();

const pad = (n) => String(n).padStart(2, '0');

/**
 * Daily subfolder name for a CSV's creation date, e.g. files-20-08-2026.
 * All CSVs produced on the same day share one folder.
 */
function outputFolderFor(date) {
  const d = new Date(date);
  return `files-${pad(d.getDate())}-${pad(d.getMonth() + 1)}-${d.getFullYear()}`;
}

function listOutputFiles() {
  try {
    return fs.readdirSync(SCRAPERS_DIR).filter((f) => {
      const name = f.toLowerCase();
      return name.endsWith('.csv') || name.endsWith('.json');
    });
  } catch {
    return [];
  }
}

/** Move CSV/JSON files from scrapers/ into output/<dated folder> based on their mtime. */
function moveFiles(filenames) {
  const moved = [];
  for (const f of filenames) {
    const src = path.join(SCRAPERS_DIR, f);
    if (!fs.existsSync(src)) continue;
    const targetDir = path.join(OUTPUT_DIR, outputFolderFor(fs.statSync(src).mtime));
    fs.mkdirSync(targetDir, { recursive: true });
    try {
      fs.renameSync(src, path.join(targetDir, f));
      moved.push(f);
    } catch (err) {
      console.error(`[runner] Failed to move ${f}: ${err.message}`);
    }
  }
  return moved;
}

/** Move every CSV/JSON currently sitting in scrapers/ (startup sweep). */
function moveAllOutputFiles() {
  const moved = moveFiles(listOutputFiles());
  if (moved.length) console.log(`[runner] Organized ${moved.length} file(s) into output/: ${moved.join(', ')}`);
  return moved;
}

function isRunning(filename) {
  return running.has(filename);
}

/** Stop a running scraper by killing its process tree. Returns true if it was running. */
function stopScraper(filename) {
  const entry = running.get(filename);
  if (!entry) return false;
  entry.stopped = true;
  const { child } = entry;
  try {
    if (process.platform === 'win32') {
      // Kill the whole tree (python + any browser subprocesses).
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    } else {
      child.kill('SIGTERM');
    }
  } catch (err) {
    console.error(`[runner] Failed to stop ${filename}: ${err.message}`);
  }
  return true;
}

function appendCapped(chunks, buffer, totalRef) {
  if (totalRef.bytes >= MAX_CAPTURED_BYTES) return;
  const room = MAX_CAPTURED_BYTES - totalRef.bytes;
  const slice = buffer.slice(0, room);
  chunks.push(slice);
  totalRef.bytes += slice.length;
}

/**
 * Execute a scraper file with python via child_process.spawn().
 * Captures stdout, stderr, exit code, start time and finish time.
 * Returns the created execution id.
 */
function runScraper(filename) {
  const filePath = path.join(SCRAPERS_DIR, filename);
  if (!fs.existsSync(filePath)) {
    const err = new Error(`Scraper not found: ${filename}`);
    err.status = 404;
    throw err;
  }
  if (running.has(filename)) {
    const err = new Error(`Scraper "${filename}" is already running`);
    err.status = 409;
    throw err;
  }

  const startedAt = new Date().toISOString();
  const filesBefore = new Set(listOutputFiles());
  const info = db
    .prepare(
      `INSERT INTO executions (filename, status, started_at) VALUES (?, 'running', ?)`
    )
    .run(filename, startedAt);
  const executionId = info.lastInsertRowid;

  const child = spawn(PYTHON_BIN, ['-u', filePath, '--restart'], {
    cwd: SCRAPERS_DIR,
    env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
  });
  running.set(filename, { executionId, child, stopped: false });

  const stdoutChunks = [];
  const stderrChunks = [];
  const stdoutBytes = { bytes: 0 };
  const stderrBytes = { bytes: 0 };

  child.stdout.on('data', (d) => appendCapped(stdoutChunks, d, stdoutBytes));
  child.stderr.on('data', (d) => appendCapped(stderrChunks, d, stderrBytes));

  child.on('error', (err) => {
    // e.g. python executable not found
    running.delete(filename);
    db.prepare(
      `UPDATE executions
         SET status = 'error', stderr = ?, finished_at = ?
       WHERE id = ?`
    ).run(`Failed to start: ${err.message}`, new Date().toISOString(), executionId);
  });

  child.on('close', (code) => {
    const entry = running.get(filename);
    running.delete(filename);
    const status = entry?.stopped ? 'stopped' : code === 0 ? 'success' : 'failed';
    const stdout = Buffer.concat(stdoutChunks).toString('utf-8');
    const stderr = Buffer.concat(stderrChunks).toString('utf-8');
    db.prepare(
      `UPDATE executions
         SET status = ?, exit_code = ?, stdout = ?, stderr = ?, finished_at = ?
       WHERE id = ?`
    ).run(
      status,
      code,
      stdout,
      stderr,
      new Date().toISOString(),
      executionId
    );

    // Move any CSV/JSON files created during this run into output/<dated folder>.
    const filesCreated = listOutputFiles().filter((f) => !filesBefore.has(f));
    if (filesCreated.length) {
      const moved = moveFiles(filesCreated);
      console.log(`[runner] ${filename} -> moved ${moved.length} file(s) into output/: ${moved.join(', ')}`);
    }
  });

  return executionId;
}

/** Last execution summary per scraper. */
function getLastRun(filename) {
  return db
    .prepare(
      `SELECT status, exit_code, started_at, finished_at
         FROM executions WHERE filename = ?
         ORDER BY id DESC LIMIT 1`
    )
    .get(filename);
}

/** Last execution summary for today only (status resets daily). */
function getLastRunToday(filename) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return db
    .prepare(
      `SELECT status, exit_code, started_at, finished_at
         FROM executions WHERE filename = ? AND started_at >= ?
         ORDER BY id DESC LIMIT 1`
    )
    .get(filename, startOfToday.toISOString());
}

function getHistory(filename, limit = 20) {
  return db
    .prepare(
      `SELECT id, status, exit_code, started_at, finished_at
         FROM executions WHERE filename = ?
         ORDER BY id DESC LIMIT ?`
    )
    .all(filename, limit);
}

function getExecution(id) {
  return db.prepare(`SELECT * FROM executions WHERE id = ?`).get(id);
}

module.exports = { runScraper, stopScraper, isRunning, getLastRun, getLastRunToday, getHistory, getExecution, moveAllOutputFiles };
