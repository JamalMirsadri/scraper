const express = require('express');
const cors = require('cors');
const cron = require('node-cron');
const fs = require('fs');
const path = require('path');
const db = require('./db');
const { PORT, OUTPUT_DIR, SCRAPERS_DIR } = require('./config');
const { scanScrapers, getScrapers } = require('./scanner');
const { runScraper, stopScraper, isRunning, getLastRun, getLastRunToday, getHistory, getExecution, moveAllOutputFiles } = require('./runner');
const { listFolders, listFiles, deleteOutputFile, zipFolder, resolvePath } = require('./output');

const app = express();
app.use(cors());
app.use(express.json());

// Output folder for organized CSV files.
fs.mkdirSync(OUTPUT_DIR, { recursive: true });

// Executions stuck as 'running' belong to a previous session; mark them failed.
db.prepare(
  `UPDATE executions SET status = 'failed', finished_at = ? WHERE status = 'running'`
).run(new Date().toISOString());

// Initial discovery + periodic re-scan to pick up newly added .py files.
scanScrapers();
cron.schedule('* * * * *', () => scanScrapers());

// Organize any CSV/JSON files left in scrapers/ from previous runs.
moveAllOutputFiles();

// List all scrapers with status, enabled flag and last run.
app.get('/api/scrapers', (req, res) => {
  const scrapers = getScrapers().map((s) => ({
    filename: s.filename,
    enabled: !!s.enabled,
    status: isRunning(s.filename) ? 'running' : getLastRunToday(s.filename)?.status ?? 'never',
    lastRun: getLastRun(s.filename) ?? null,
    running: isRunning(s.filename),
  }));
  res.json(scrapers);
});

// Re-scan scrapers/ directory (Refresh Scrapers button).
app.post('/api/scrapers/scan', (req, res) => {
  const found = scanScrapers();
  res.json({ discovered: found.length });
});

// Upload a new .py scraper into scrapers/.
app.post('/api/scrapers/upload', (req, res) => {
  const { filename, content } = req.body || {};
  if (typeof content !== 'string' || content.length === 0) {
    return res.status(400).json({ error: 'File content is empty' });
  }
  const name = path.basename(String(filename || '').trim());
  if (!name.toLowerCase().endsWith('.py')) {
    return res.status(400).json({ error: 'Only .py files are allowed' });
  }
  try {
    fs.writeFileSync(path.join(SCRAPERS_DIR, name), content, 'utf-8');
  } catch (err) {
    return res.status(500).json({ error: `Failed to save file: ${err.message}` });
  }
  scanScrapers();
  res.json({ filename: name, saved: true });
});

// Toggle enabled/disabled.
app.patch('/api/scrapers/:filename', (req, res) => {
  const { filename } = req.params;
  const { enabled } = req.body;
  const scraper = getScrapers().find((s) => s.filename === filename);
  if (!scraper) return res.status(404).json({ error: 'Scraper not found' });
  db.prepare(`UPDATE scrapers SET enabled = ? WHERE filename = ?`).run(
    enabled ? 1 : 0,
    filename
  );
  res.json({ filename, enabled: !!enabled });
});

// Run a scraper now.
app.post('/api/scrapers/:filename/run', (req, res) => {
  const { filename } = req.params;
  const scraper = getScrapers().find((s) => s.filename === filename);
  if (!scraper) return res.status(404).json({ error: 'Scraper not found' });
  if (!scraper.enabled) return res.status(400).json({ error: 'Scraper is disabled' });
  try {
    const executionId = runScraper(filename);
    res.status(202).json({ executionId, filename, status: 'running' });
  } catch (err) {
    res.status(err.status || 500).json({ error: err.message });
  }
});

// Stop a running scraper.
app.post('/api/scrapers/:filename/stop', (req, res) => {
  const { filename } = req.params;
  const stopped = stopScraper(filename);
  if (!stopped) return res.status(409).json({ error: `Scraper "${filename}" is not running` });
  res.json({ filename, status: 'stopping' });
});

// Run all enabled scrapers that are not already running.
app.post('/api/scrapers/run-all', (req, res) => {
  const started = [];
  const skipped = [];
  for (const s of getScrapers()) {
    if (!s.enabled) {
      skipped.push({ filename: s.filename, reason: 'disabled' });
      continue;
    }
    if (isRunning(s.filename)) {
      skipped.push({ filename: s.filename, reason: 'already running' });
      continue;
    }
    try {
      runScraper(s.filename);
      started.push(s.filename);
    } catch (err) {
      skipped.push({ filename: s.filename, reason: err.message });
    }
  }
  res.json({ started, skipped });
});

// Stop all running scrapers.
app.post('/api/scrapers/stop-all', (req, res) => {
  const stopped = [];
  for (const s of getScrapers()) {
    if (isRunning(s.filename)) {
      stopScraper(s.filename);
      stopped.push(s.filename);
    }
  }
  res.json({ stopped });
});

// Execution history for a scraper.
app.get('/api/scrapers/:filename/history', (req, res) => {
  res.json(getHistory(req.params.filename));
});

// Full execution details (stdout/stderr).
app.get('/api/executions/:id', (req, res) => {
  const exec = getExecution(Number(req.params.id));
  if (!exec) return res.status(404).json({ error: 'Execution not found' });
  res.json(exec);
});

// List output folders (dates).
app.get('/api/output', (req, res) => {
  res.json(listFolders());
});

// List CSV/XLSX files inside a daily folder.
app.get('/api/output/files', (req, res) => {
  const { folder } = req.query;
  if (!folder) return res.status(400).json({ error: 'Missing folder' });
  res.json(listFiles(folder));
});

// Download a single file, or a whole folder as a zip.
app.get('/api/output/download', (req, res) => {
  const { folder, file } = req.query;
  if (!folder) return res.status(400).json({ error: 'Missing folder' });

  if (file) {
    const full = resolvePath(folder, file);
    if (!full || !fs.existsSync(full) || !fs.statSync(full).isFile()) {
      return res.status(404).json({ error: 'File not found' });
    }
    return res.download(full, file);
  }

  const full = resolvePath(folder);
  if (!full || !fs.existsSync(full) || !fs.statSync(full).isDirectory()) {
    return res.status(404).json({ error: 'Folder not found' });
  }
  const buffer = zipFolder(folder);
  if (!buffer) return res.status(500).json({ error: 'Failed to create archive' });
  res.setHeader('Content-Type', 'application/zip');
  res.setHeader('Content-Disposition', `attachment; filename="${folder}.zip"`);
  res.send(buffer);
});

// Delete a single output file.
app.delete('/api/output', (req, res) => {
  const { folder, file } = req.query;
  if (!folder || !file) return res.status(400).json({ error: 'Missing folder or file' });
  const ok = deleteOutputFile(folder, file);
  if (!ok) return res.status(404).json({ error: 'File not found' });
  res.json({ deleted: true });
});

app.listen(PORT, () => {
  console.log(`Scraper Manager backend listening on http://localhost:${PORT}`);
});
