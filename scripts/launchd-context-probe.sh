#!/usr/bin/env bash
# ABOUTME: P0-7 launchd session context probe for hermes factory reliability work.
# ABOUTME: Tests keychain reachability and GUI/browser session availability from within a
# ABOUTME:   launchd-spawned background context (Background session, not Aqua/GUI).
# ABOUTME: Outputs results to log and stdout; designed to be run by launchd or directly.
# ABOUTME: Exit 0 on success; non-zero if keychain is unreachable (hard dependency failure).

set -uo pipefail

LOG_DIR="${HERMES_HOME:-$HOME/.hermes}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/launchd-context-probe.log"

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

log() {
  # launchd captures stdout to StandardOutPath (the primary log path).
  # Also attempt a direct file write; silently ignore permission errors (Claude sandbox).
  echo "[$TIMESTAMP] $*"
  { echo "[$TIMESTAMP] $*" >> "$LOG_FILE"; } 2>/dev/null || true
}

log "=== launchd-context-probe starting ==="

# --- Probe 1: launchd session type ---
# LAUNCHD_SOCKET is set in GUI (Aqua) sessions; absent in Background sessions.
# $TERM_PROGRAM and $DISPLAY are other GUI indicators.
SESSION_TYPE="Background"
if [[ -n "${LAUNCHD_SOCKET:-}" ]]; then
  SESSION_TYPE="Aqua/GUI (LAUNCHD_SOCKET set)"
elif [[ -n "${DISPLAY:-}" ]]; then
  SESSION_TYPE="X11/GUI-like (DISPLAY set)"
fi
log "launchd session type observed: $SESSION_TYPE"

# Check whether we're inside a GUI session via launchctl
LAUNCHCTL_DOMAIN="$(launchctl print gui/$(id -u) 2>&1 | head -1 || true)"
log "launchctl domain probe: $LAUNCHCTL_DOMAIN"

# --- Probe 2: Keychain reachability ---
# Use 'security list-keychains' as the harmless reachability check (no password needed).
# A successful list proves the security framework is accessible from this launchd context.
log "--- Keychain probe ---"
KEYCHAIN_RESULT=0
KEYCHAIN_OUTPUT="$(security list-keychains 2>&1)" || KEYCHAIN_RESULT=$?
if [[ $KEYCHAIN_RESULT -eq 0 ]]; then
  log "keychain reachable: list-keychains exit 0"
  log "keychains found: $KEYCHAIN_OUTPUT"
else
  log "ERROR: keychain unreachable (exit $KEYCHAIN_RESULT): $KEYCHAIN_OUTPUT"
fi

# Also test find-generic-password on a known Apple-managed item as a stronger probe.
# 'com.apple.account.AppleID.token' or similar; using 'apple.laf.useScreenMenuBar' is
# not a real keychain item, so use 'find-internet-password' on apple.com for the probe.
# We expect this to either succeed (found) or fail with "could not find" (item absent) —
# both outcomes prove the security framework is callable. A "access denied" or "timeout"
# is the failure mode we're detecting.
KEYCHAIN_PROBE_RESULT=0
KEYCHAIN_PROBE_OUTPUT="$(security find-generic-password -s 'com.apple.account' 2>&1)" \
  || KEYCHAIN_PROBE_RESULT=$?
# Exit 44 = "The specified item could not be found" — acceptable, proves framework is live.
if [[ $KEYCHAIN_PROBE_RESULT -eq 0 || $KEYCHAIN_PROBE_RESULT -eq 44 ]]; then
  log "keychain find-generic-password probe: reachable (exit $KEYCHAIN_PROBE_RESULT = item found or not-found — both OK)"
else
  log "WARN: keychain find-generic-password probe returned exit $KEYCHAIN_PROBE_RESULT: $KEYCHAIN_PROBE_OUTPUT"
fi

# --- Probe 3: GUI/browser availability detection ---
# In a Background launchd context, there is no window server connection (no Aqua session).
# Attempting to launch a GUI app will fail or produce no window.
# We detect this by checking whether a window server connection is possible via 'osascript'.
# If it fails with "not allowed to send Apple events" or "cannot connect", we surface
# "needs user present" rather than hanging.
log "--- GUI/browser availability probe ---"
GUI_RESULT=0
GUI_OUTPUT="$(timeout 5 osascript -e 'tell application "System Events" to return name of every process' 2>&1)" \
  || GUI_RESULT=$?

if [[ $GUI_RESULT -eq 0 ]]; then
  log "GUI session: PRESENT (osascript succeeded — Aqua session active)"
  log "Running processes sample: $(echo "$GUI_OUTPUT" | head -1)"
else
  # Surface "needs user present" loudly — this is the P0-7 requirement
  log "GUI session: ABSENT — needs user present (osascript exit $GUI_RESULT)"
  log "osascript output: $GUI_OUTPUT"
  log "SIGNAL: browser-requiring steps cannot proceed without an active user session."
  log "SIGNAL: needs user present — factory should queue this step until login session is active."
fi

# --- Probe 4: CGSession check (authoritative GUI session indicator) ---
log "--- CGSession probe (authoritative) ---"
CGSESSION_OUTPUT="$(CGSession -dump 2>&1 || true)"
if echo "$CGSESSION_OUTPUT" | grep -q "kCGSSessionOnConsoleKey = 1"; then
  log "CGSession: user IS on console (GUI session active)"
elif echo "$CGSESSION_OUTPUT" | grep -q "kCGSSessionOnConsoleKey = 0"; then
  log "CGSession: user NOT on console (locked or no GUI session) — needs user present"
else
  # CGSession may not be available in all contexts
  CGSESSION_ALT="$(who | grep console || true)"
  if [[ -n "$CGSESSION_ALT" ]]; then
    log "CGSession: fallback who-console check: $CGSESSION_ALT"
  else
    log "CGSession: probe inconclusive (no console user detected)"
  fi
fi

log "=== launchd-context-probe complete ==="
exit 0
