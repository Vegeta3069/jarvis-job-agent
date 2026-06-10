#!/bin/bash
# Daily job search runner — called by cron/launchd at 8 AM
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"

# Load API key from .env if not already set
if [ -f "$DIR/.env" ]; then
  export $(grep -v '^#' "$DIR/.env" | xargs)
fi

# Run the agent
"$DIR/.venv/bin/python" "$DIR/job_agent.py"
