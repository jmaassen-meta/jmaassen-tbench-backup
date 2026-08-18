FROM python:3.12-slim

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# repo-type: greenfield (tbench-multi) — empty /app, agent fills from scratch
# Symlink for parser hard-coding /usr/bin/python3 (slim only has /usr/local/bin/python3)
RUN ln -sf /usr/local/bin/python3 /usr/bin/python3 || true

# Bake deps at build — verify runs offline. Quote specs, cap majors.
# greenfield — no clone, empty /app is working dir
RUN pip install --no-cache-dir \
  "pytest>=8.0,<9.0" \
  "pytest-xdist>=3.0,<4.0" \
  "hypothesis>=6.100,<7.0" \
  "click>=8.0,<9.0" \
  "coverage>=7.0,<8.0"

RUN mkdir -p /logs /app
