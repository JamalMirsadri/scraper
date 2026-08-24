const fs = require('fs');
const path = require('path');
const AdmZip = require('adm-zip');
const { OUTPUT_DIR } = require('./config');

function isValidName(name) {
  return (
    typeof name === 'string' &&
    name.length > 0 &&
    name !== '.' &&
    name !== '..' &&
    !name.includes('/') &&
    !name.includes('\\')
  );
}

/** Resolve a folder (and optional file) inside OUTPUT_DIR, guarding against path traversal. */
function resolvePath(folder, file) {
  if (!isValidName(folder)) return null;
  const base = path.join(OUTPUT_DIR, folder);
  const full = file ? path.join(base, file) : base;
  const rel = path.relative(OUTPUT_DIR, full);
  if (rel.startsWith('..') || path.isAbsolute(rel)) return null;
  return full;
}

/** List daily output folders (newest first). */
function listFolders() {
  const result = [];
  let entries;
  try {
    entries = fs.readdirSync(OUTPUT_DIR, { withFileTypes: true });
  } catch {
    return result;
  }
  for (const e of entries) {
    if (e.isDirectory()) result.push(e.name);
  }
  result.sort((a, b) => b.localeCompare(a));
  return result;
}

/** List CSV/XLSX files inside a daily folder, sorted by name. */
function listFiles(folder) {
  const full = resolvePath(folder);
  if (!full || !fs.existsSync(full) || !fs.statSync(full).isDirectory()) return [];
  const files = [];
  for (const f of fs.readdirSync(full, { withFileTypes: true })) {
    if (!f.isFile()) continue;
    const lower = f.name.toLowerCase();
    if (!lower.endsWith('.csv') && !lower.endsWith('.xlsx')) continue;
    const fp = path.join(full, f.name);
    const st = fs.statSync(fp);
    files.push({ name: f.name, size: st.size, mtime: st.mtime.toISOString() });
  }
  files.sort((a, b) => a.name.localeCompare(b.name));
  return files;
}

/** Delete a single file inside a daily folder. Returns true on success. */
function deleteOutputFile(folder, file) {
  if (!file) return false;
  const full = resolvePath(folder, file);
  if (!full || !fs.existsSync(full) || !fs.statSync(full).isFile()) return false;
  fs.unlinkSync(full);
  return true;
}

/** Zip an entire daily folder. Returns a Buffer, or null if the folder is invalid. */
function zipFolder(folder) {
  const full = resolvePath(folder);
  if (!full || !fs.existsSync(full) || !fs.statSync(full).isDirectory()) return null;
  const zip = new AdmZip();
  zip.addLocalFolder(full, folder);
  return zip.toBuffer();
}

module.exports = { listFolders, listFiles, deleteOutputFile, zipFolder, resolvePath };
