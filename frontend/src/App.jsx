import { useCallback, useEffect, useState } from 'react';

const STATUS_LABELS = {
  running: 'Running',
  success: 'Success',
  failed: 'Failed',
  stopped: 'Stopped',
  error: 'Error',
  never: 'Never run',
};

function formatTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString();
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function StatusBadge({ status }) {
  return <span className={`badge badge-${status}`}>{STATUS_LABELS[status] ?? status}</span>;
}

function ScraperRow({ scraper, onRun, onStop, onToggle }) {
  return (
    <tr>
      <td className="filename">{scraper.filename}</td>
      <td>
        <StatusBadge status={scraper.status} />
      </td>
      <td>
        <label className="switch">
          <input
            type="checkbox"
            checked={scraper.enabled}
            onChange={(e) => onToggle(scraper.filename, e.target.checked)}
          />
          <span>{scraper.enabled ? 'Enabled' : 'Disabled'}</span>
        </label>
      </td>
      <td>
        {scraper.lastRun ? (
          <>
            <div>{formatTime(scraper.lastRun.started_at)}</div>
            {scraper.lastRun.finished_at && (
              <small className="muted">
                finished {formatTime(scraper.lastRun.finished_at)} (exit{' '}
                {scraper.lastRun.exit_code})
              </small>
            )}
          </>
        ) : (
          '—'
        )}
      </td>
      <td>
        <div className="actions">
          <button
            className="btn"
            disabled={!scraper.enabled || scraper.running}
            onClick={() => onRun(scraper.filename)}
          >
            {scraper.running ? 'Running…' : 'Run Now'}
          </button>
          {scraper.running && (
            <button className="btn danger" onClick={() => onStop(scraper.filename)}>
              Stop
            </button>
          )}
        </div>
      </td>
    </tr>
  );
}

export default function App() {
  const [scrapers, setScrapers] = useState([]);
  const [folders, setFolders] = useState([]);
  const [selectedFolder, setSelectedFolder] = useState('');
  const [files, setFiles] = useState([]);
  const [selected, setSelected] = useState({});
  const [error, setError] = useState(null);
  const [message, setMessage] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/scrapers');
      if (!res.ok) throw new Error(`Backend returned ${res.status}`);
      setScrapers(await res.json());
      setError(null);
    } catch (e) {
      setError(`Cannot reach backend: ${e.message}`);
    }
  }, []);

  const loadFolders = useCallback(async () => {
    try {
      const res = await fetch('/api/output');
      if (res.ok) setFolders(await res.json());
    } catch {
      // backend unreachable — ignore, scrapers poll will surface the error
    }
  }, []);

  const loadFiles = useCallback(async (folder) => {
    if (!folder) {
      setFiles([]);
      return;
    }
    try {
      const res = await fetch(`/api/output/files?folder=${encodeURIComponent(folder)}`);
      if (res.ok) setFiles(await res.json());
      else setFiles([]);
    } catch {
      setFiles([]);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    loadFolders();
    const timer = setInterval(loadFolders, 5000);
    return () => clearInterval(timer);
  }, [loadFolders]);

  useEffect(() => {
    loadFiles(selectedFolder);
    const timer = setInterval(() => loadFiles(selectedFolder), 5000);
    return () => clearInterval(timer);
  }, [loadFiles, selectedFolder]);

  const runScraper = async (filename) => {
    setMessage(null);
    try {
      const res = await fetch(`/api/scrapers/${encodeURIComponent(filename)}/run`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMessage(`Started ${filename} (--restart)`);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const stopScraper = async (filename) => {
    setMessage(null);
    try {
      const res = await fetch(`/api/scrapers/${encodeURIComponent(filename)}/stop`, {
        method: 'POST',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMessage(`Stopping ${filename}…`);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const toggleScraper = async (filename, enabled) => {
    try {
      const res = await fetch(`/api/scrapers/${encodeURIComponent(filename)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const rescan = async () => {
    setMessage(null);
    try {
      const res = await fetch('/api/scrapers/scan', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMessage(`Scan complete: ${data.discovered} scrapers discovered`);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const runAll = async () => {
    setMessage(null);
    try {
      const res = await fetch('/api/scrapers/run-all', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMessage(`Started ${data.started.length} scraper(s)`);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const stopAll = async () => {
    setMessage(null);
    try {
      const res = await fetch('/api/scrapers/stop-all', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMessage(`Stopped ${data.stopped.length} scraper(s)`);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const handleUpload = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setMessage(null);
    try {
      const content = await file.text();
      const res = await fetch('/api/scrapers/upload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: file.name, content }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMessage(`Uploaded ${data.filename}`);
      await load();
    } catch (err) {
      setError(err.message);
    }
  };

  const toggleSelect = (file) => {
    setSelected((prev) => {
      const next = { ...prev };
      if (next[file]) delete next[file];
      else next[file] = true;
      return next;
    });
  };

  const deleteFile = async (file) => {
    try {
      const res = await fetch(
        `/api/output?folder=${encodeURIComponent(selectedFolder)}&file=${encodeURIComponent(file)}`,
        { method: 'DELETE' }
      );
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      setSelected((prev) => {
        const next = { ...prev };
        delete next[file];
        return next;
      });
      await loadFiles(selectedFolder);
    } catch (err) {
      setError(err.message);
    }
  };

  const deleteSelected = async () => {
    const fileNames = Object.keys(selected);
    for (const file of fileNames) {
      try {
        await fetch(
          `/api/output?folder=${encodeURIComponent(selectedFolder)}&file=${encodeURIComponent(file)}`,
          { method: 'DELETE' }
        );
      } catch {
        // continue with the rest
      }
    }
    setSelected({});
    await loadFiles(selectedFolder);
  };

  const handleFolderChange = (e) => {
    setSelectedFolder(e.target.value);
    setSelected({});
  };

  const anyRunning = scrapers.some((s) => s.running);
  const selectedCount = Object.keys(selected).length;

  return (
    <div className="container">
      <header>
        <h1>Scraper Manager</h1>
        <div className="actions">
          <label className="btn secondary upload-label">
            Upload Scraper
            <input type="file" accept=".py" onChange={handleUpload} hidden />
          </label>
          <button className="btn secondary" onClick={rescan}>
            Refresh Scrapers
          </button>
        </div>
      </header>

      {error && <div className="alert alert-error">{error}</div>}
      {message && <div className="alert alert-info">{message}</div>}

      <div className="toolbar">
        <button className="btn" onClick={runAll}>Run All</button>
        <button className="btn danger" disabled={!anyRunning} onClick={stopAll}>
          Stop All
        </button>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Filename</th>
            <th>Status</th>
            <th>Enabled</th>
            <th>Last Run</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {scrapers.length === 0 ? (
            <tr>
              <td colSpan={5} className="empty">
                {error ? 'Waiting for backend…' : 'No scrapers found'}
              </td>
            </tr>
          ) : (
            scrapers.map((s) => (
              <ScraperRow
                key={s.filename}
                scraper={s}
                onRun={runScraper}
                onStop={stopScraper}
                onToggle={toggleScraper}
              />
            ))
          )}
        </tbody>
      </table>

      <section className="output-section">
        <div className="output-heading">
          <h2>Output Files</h2>
          <select
            className="date-select"
            value={selectedFolder}
            onChange={handleFolderChange}
          >
            <option value="">Select a date…</option>
            {folders.map((f) => (
              <option key={f} value={f}>{f}</option>
            ))}
          </select>
        </div>

        {!selectedFolder ? (
          <div className="empty">Choose a date to see its files</div>
        ) : files.length === 0 ? (
          <div className="empty">No CSV/XLSX files for this day</div>
        ) : (
          <div className="output-folder">
            <div className="output-folder-header">
              <span className="folder-name">{selectedFolder}</span>
              <span className="muted">{files.length} file(s)</span>
              <a
                className="btn small secondary"
                href={`/api/output/download?folder=${encodeURIComponent(selectedFolder)}`}
                download={`${selectedFolder}.zip`}
              >
                Download ZIP
              </a>
              {selectedCount > 0 && (
                <button className="btn small danger" onClick={deleteSelected}>
                  Delete Selected ({selectedCount})
                </button>
              )}
            </div>
            <ul className="output-files">
              {files.map((f) => (
                <li key={f.name}>
                  <input
                    type="checkbox"
                    checked={!!selected[f.name]}
                    onChange={() => toggleSelect(f.name)}
                  />
                  <span className="filename">{f.name}</span>
                  <span className="muted">{formatSize(f.size)}</span>
                  <a
                    className="btn small"
                    href={`/api/output/download?folder=${encodeURIComponent(selectedFolder)}&file=${encodeURIComponent(f.name)}`}
                    download={f.name}
                  >
                    Download
                  </a>
                  <button
                    className="btn small danger"
                    onClick={() => deleteFile(f.name)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>
    </div>
  );
}
