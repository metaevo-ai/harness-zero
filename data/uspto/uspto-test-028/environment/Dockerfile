FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir httpx==0.28.1 rdkit==2026.3.5

WORKDIR /app

RUN python3 -m venv /opt/ahd/venv \
    && /opt/ahd/venv/bin/pip install --no-cache-dir 'openai>=2.54.0'
