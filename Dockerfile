FROM mcr.microsoft.com/playwright/python:v1.61.0-noble

# Node.js 22 for the Express backend and the Vite frontend build.
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Xvfb provides a virtual display so scrapers using headless=False can launch Chromium.
RUN apt-get update && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/* \
    && printf '#!/bin/sh\nexec xvfb-run -a python3 "$@"\n' > /usr/local/bin/python-xvfb \
    && chmod +x /usr/local/bin/python-xvfb

WORKDIR /app

# Python dependencies (Playwright browsers are already in the base image).
COPY requirements.txt ./
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# Backend dependencies.
COPY backend ./backend
RUN cd backend && npm install

# Frontend dependencies + production build.
COPY frontend ./frontend
RUN cd frontend && npm install && npm run build

# Scrapers (Python scripts).
COPY scrapers ./scrapers

ENV PYTHON_BIN=/usr/local/bin/python-xvfb
ENV PORT=10000

WORKDIR /app/backend
CMD ["node", "src/index.js"]
