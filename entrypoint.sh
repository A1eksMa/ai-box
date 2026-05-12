#!/bin/bash
set -e

# Load additional env vars from volume (ANTHROPIC_API_KEY, overrides, etc.)
if [ -f "$HOME/.env" ]; then
    export $(grep -v '^#' "$HOME/.env" | xargs)
fi

SESSION="${TMUX_SESSION:-main}"
ROWS="${PTY_ROWS:-60}"
COLS="${PTY_COLS:-220}"

# Create detached tmux session with Claude Code if not already running
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -x "$COLS" -y "$ROWS" "cd $HOME/projects && claude"
fi

# Start FastAPI
exec /app/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080
