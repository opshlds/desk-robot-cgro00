#!/usr/bin/env bash
# Start the Xiaozhi bridge on HAIL-E (the Yahboom voice board <-> the brain).
# Runs next to the brain: start the brain first (start.sh), then this, e.g. in `tmux new -s bridge`.
cd "$(dirname "$0")"
source .venv/bin/activate
exec python -m bridge.xiaozhi
