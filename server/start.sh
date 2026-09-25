#!/usr/bin/env bash
# Start the desk-robot brain on HAIL-E.
cd "$(dirname "$0")"
source .venv/bin/activate
export HF_HUB_OFFLINE=0   # /etc/environment sets offline mode on ai1; 0 also overrides TRANSFORMERS_OFFLINE
exec python -m brain.main
