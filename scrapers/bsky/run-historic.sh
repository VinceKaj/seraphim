#!/bin/bash
cd "$(dirname "$0")" && exec uv run python bsky-scrape-historic.py
