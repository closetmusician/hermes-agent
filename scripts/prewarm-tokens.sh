#!/usr/bin/env bash
# ABOUTME: ~10pm credential pre-warm for hermes factory overnight runs.
# ABOUTME: Wraps pm_os ensure-tokens.js to refresh/validate tokens before 3am batches.
# ABOUTME: Runs non-interactively (--scheduled flag) so no browser popups are attempted.
# ABOUTME: Logs results with timestamp; exits non-zero if tokens unrecoverable (exit 10).
# ABOUTME: Called by launchd service ai.hermes.prewarm at StartCalendarInterval 21:57.

set -euo pipefail

LOG_DIR="${HERMES_HOME:-$HOME/.hermes}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/prewarm-tokens.log"

ENSURE_TOKENS="/Users/yklin/Code/pm_os/bin/ensure-tokens.js"
NODE_BIN="/opt/homebrew/opt/node@20/bin/node"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# Verify the token tool exists; surface loudly if missing rather than silently skipping.
if [[ ! -f "$ENSURE_TOKENS" ]]; then
  echo "[$TIMESTAMP] ERROR: token tool unavailable — $ENSURE_TOKENS not found" >> "$LOG_FILE"
  echo "[$TIMESTAMP] ERROR: token tool unavailable — $ENSURE_TOKENS not found" >&2
  exit 1
fi

if [[ ! -x "$NODE_BIN" ]]; then
  echo "[$TIMESTAMP] ERROR: node binary not found at $NODE_BIN" >> "$LOG_FILE"
  exit 1
fi

echo "[$TIMESTAMP] prewarm-tokens starting" >> "$LOG_FILE"

# PM_OS_CRON=1 activates scheduled/non-interactive mode in ensure-tokens.js:
#   no browser popups, no device-code prompts; writes state/needs-human.json on failure.
# --scheduled additionally signals check-token-health exit-code semantics to callers.
RESULT=0
PM_OS_CRON=1 "$NODE_BIN" "$ENSURE_TOKENS" --scheduled >> "$LOG_FILE" 2>&1 || RESULT=$?

TIMESTAMP_END="$(date '+%Y-%m-%d %H:%M:%S')"
if [[ $RESULT -eq 0 ]]; then
  echo "[$TIMESTAMP_END] prewarm-tokens: all tokens valid (exit 0)" >> "$LOG_FILE"
elif [[ $RESULT -eq 10 ]]; then
  echo "[$TIMESTAMP_END] prewarm-tokens: UNRECOVERABLE TOKENS — needs user present (exit 10)" >> "$LOG_FILE"
  echo "[$TIMESTAMP_END] prewarm-tokens: UNRECOVERABLE TOKENS — needs user present (exit 10)" >&2
else
  echo "[$TIMESTAMP_END] prewarm-tokens: token refresh failed (exit $RESULT)" >> "$LOG_FILE"
  echo "[$TIMESTAMP_END] prewarm-tokens: token refresh failed (exit $RESULT)" >&2
fi

exit $RESULT
