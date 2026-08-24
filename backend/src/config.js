const path = require('path');

// scrapers/ lives at the project root, next to backend/ and frontend/
const SCRAPERS_DIR = path.join(__dirname, '..', '..', 'scrapers');

// Output folder for generated CSV files, organized into dated subfolders.
const OUTPUT_DIR = path.join(__dirname, '..', '..', 'output');

// Python interpreter used to run every scraper.
const PYTHON_BIN = process.env.PYTHON_BIN || 'python';

// Hard cap for captured output stored in the DB (per stream).
const MAX_CAPTURED_BYTES = 1024 * 1024;

const PORT = process.env.PORT || 4000;

module.exports = { SCRAPERS_DIR, OUTPUT_DIR, PYTHON_BIN, MAX_CAPTURED_BYTES, PORT };
