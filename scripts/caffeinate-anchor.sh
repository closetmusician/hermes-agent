#!/usr/bin/env bash
# ABOUTME: Sleep-block anchor for hermes overnight factory runs.
# ABOUTME: Runs caffeinate -i to prevent idle system sleep (PreventUserIdleSystemSleep).
# ABOUTME: Intended to run as its own launchd service (ai.hermes.caffeinate) independent
# ABOUTME:   of the gateway lifecycle — a gateway crash will NOT drop this sleep block.
# ABOUTME: launchd KeepAlive restarts this if it ever exits unexpectedly.

set -euo pipefail

LOG_DIR="${HERMES_HOME:-$HOME/.hermes}/logs"
mkdir -p "$LOG_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] caffeinate-anchor starting (PID $$)" \
  >> "$LOG_DIR/caffeinate-anchor.log"

# -i: prevent idle system sleep (PreventUserIdleSystemSleep assertion).
# Runs indefinitely; launchd KeepAlive will restart it if it exits.
exec /usr/bin/caffeinate -i
