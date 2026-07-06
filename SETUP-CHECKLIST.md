# ABOUTME: Factory host setup checklist — behavioral round-trip checks for every messaging/tool lane.
# ABOUTME: Each item is verified by an observable action + expected observable result, not code presence.
# ABOUTME: A lane that is coded but returns canned/stub data is explicitly flagged NOT DONE here.
# ABOUTME: Automatable checks delegate to scripts/setup_check.py; manual checks require owner action.
# ABOUTME: Cross-references STAGED-GATES.md for items that need a real Terminal session or owner secret.

# Hermes Factory — Setup Checklist

**Rule:** An unchecked box means the lane is NOT DONE.
Code existing is not evidence. A stub that "succeeds" without an external round-trip is NOT DONE.
Every check below requires an *observable* action and a *verifiable* result.

Run the automatable checks with:

```bash
python scripts/setup_check.py
```

Each lane is classified `DONE` / `NOT DONE` / `MANUAL` (requires human/MFA action).
See [STAGED-GATES.md](docs/plans/harness/fable/STAGED-GATES.md) for gates that flip only via
an owner Terminal session or real-world event.

---

## How to Read This Checklist

| Column | Meaning |
|---|---|
| **Action** | The exact observable thing to do |
| **Expected result** | The observable outcome that means PASS |
| **Stub-detection heuristic** | How to tell if the lane returned a canned result instead of a real one |
| **Run how** | `AUTO` = `scripts/setup_check.py` covers it; `MANUAL` = requires human |

---

## §0 — Pre-flight: Config and Venv

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 0.1 | `source .venv/bin/activate && python -c "import hermes_agent"` | Exits 0, no ImportError | Any ImportError = NOT DONE | AUTO |
| 0.2 | `cat ~/.hermes/.env \| grep -c "="` | Count ≥ 1 (at least one key present) | Empty file or missing file = NOT DONE | AUTO |
| 0.3 | `cat ~/.hermes/config.yaml \| head -3` | File exists and is non-empty YAML | Missing file = NOT DONE | AUTO |

---

## §1 — Provider Reachability (AI model APIs)

Covered by `scripts/factory_health.py` (P0-8). Run it and check exit code = 0.

```bash
python scripts/factory_health.py
```

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 1.1 | `python scripts/factory_health.py` | `[1] Provider Reachability` shows `ANTHROPIC: REACHABLE` | "reachable" with no actual HTTP round-trip is impossible in factory_health.py (it does a live HEAD /v1/models) — if latency_ms is present, the check was real | AUTO |
| 1.2 | `python scripts/factory_health.py` | `OPENROUTER: REACHABLE` | Same as above — latency_ms must be ≥ 1ms | AUTO |
| 1.3 | Add `OPENROUTER_API_KEY` to `~/.hermes/.env` then `python scripts/probe.py` | `capabilities.json` committed; every routing-table model name shows `live_catalog:true` or a loud failure for phantoms | A `capabilities.json` where every model shows `live_catalog:true` without an API key in env = stub. Check that `OPENROUTER_API_KEY` was set before the run. | MANUAL — owner must add key (SG-P0-2) |

---

## §2 — Compute / Host State

Covered by `scripts/factory_health.py` Section [2].

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 2.1 | `python scripts/factory_health.py` | `caffeinate: YES (awake)` | `caffeinate: NO` = lane NOT DONE; the caffeinate service must be live, not just installed | AUTO |
| 2.2 | `python scripts/factory_health.py` | `disk free: ≥ 20.0 GiB` | Any value < 5 GiB blocks overnight runs | AUTO |
| 2.3 | `launchctl list \| grep ai.hermes` | `ai.hermes.caffeinate` and `ai.hermes.prewarm` both appear | Not appearing = services not bootstrapped. Run SG-P0-1 in a real Terminal. | MANUAL — see STAGED-GATES.md SG-P0-1 |

---

## §3 — Telegram Lane

**Stub-detection rule:** A Telegram check that "passes" but never contacts `api.telegram.org` is NOT DONE.
Observable proof requires a real message appearing in a real chat.

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 3.1 | `grep TELEGRAM_BOT_TOKEN ~/.hermes/.env` | Non-empty token string present | Missing or empty = NOT DONE | AUTO |
| 3.2 | `curl -s "https://api.telegram.org/bot$(grep TELEGRAM_BOT_TOKEN ~/.hermes/.env \| cut -d= -f2)/getMe" \| python -m json.tool \| grep '"ok": true'` | `"ok": true` and bot username returned | `"ok": false` or connection error = NOT DONE. A canned `{"ok":true}` from a local mock = NOT DONE. | AUTO |
| 3.3 | From the hermes gateway: send a message to the Telegram home channel (via `/test` or `python scripts/setup_check.py --send-telegram`) | Message appears in the real Telegram app within 10 seconds | No delivery receipt from Telegram API = NOT DONE. Delivery receipt with no message appearing in the app = stub. | MANUAL (check app) |
| 3.4 | Send a Telegram message TO hermes from the owner account; confirm hermes logs the inbound event | `~/.hermes/logs/gateway.log` shows a new inbound Telegram event within 30 seconds | Log entry with fake timestamp predating the send = stub | MANUAL |

---

## §4 — Email / M365 Lane

**Stub-detection rule:** Token validation that returns "valid" from a local cache without a live Graph API
call is NOT DONE. A send that returns success but no email arrives = NOT DONE.

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 4.1 | `node ~/Code/pm_os/bin/check-token-health.js` | All tokens show `VALID` (exit 0) | `VALID` with a token expiry in the past = stub. Check that expiry > now. | AUTO |
| 4.2 | `node ~/Code/pm_os/bin/ensure-tokens.js --check` | Exits 0 with no "expired" or "missing" output | "EXPIRED" or non-zero exit = NOT DONE | AUTO |
| 4.3 | `node ~/Code/pm_os/bin/outlook-read-mail.js --limit 1` | Returns at least one email object (JSON) from the live Graph API | Empty result set with no network call (check `--verbose` or proxy logs) = stub | MANUAL — may need MFA re-auth |
| 4.4 | `node ~/Code/pm_os/bin/outlook-send-mail.js --to yu_kuan@yahoo.com --subject "hermes-setup-test-$(date +%s)" --body "round-trip test"` | Email arrives at yu_kuan@yahoo.com within 5 minutes | No arrival = NOT DONE. Arrived with a subject timestamp that doesn't match the run = a cached/replayed send (stub). | MANUAL (check inbox) |
| 4.5 | Check FOCI token for Graph API write operations: `node ~/Code/pm_os/bin/get-foci-token.js --for graph` | Returns a non-empty bearer token that can authenticate a Graph POST | Token present but expired (check `check-token-health.js`) = NOT DONE | AUTO |

**SSO/Okta note:** If any step above returns 401 or "re-auth needed," follow the STAGED-GATES.md
guidance for SSO re-auth. This is a `MANUAL` lane gate. See also Phase R task R-4.

---

## §5 — WhatsApp Lane (DISABLED — not live)

WhatsApp is cleanly disabled until the paired-and-verified gate passes (Phase R, task R-2).

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 5.1 | `grep -i whatsapp ~/.hermes/config.yaml` | `enabled: false` or entry absent | Any `enabled: true` without a real paired QR = NOT DONE | AUTO |
| 5.2 | `python scripts/factory_health.py` + 30-minute wait | `errors.log` shows zero WhatsApp reconnect/retry lines | Any retry lines = lane is partially-live and misconfigured (NOT DONE, see P0-9) | AUTO |
| 5.3 | **To activate:** pair a real device (QR code scan), then run `python scripts/setup_check.py --whatsapp-roundtrip` | A test message round-trips: sent from hermes → received on the device, confirmed in logs | Pairing "success" with no real device = stub. Activation requires both QR scan AND a confirmed send+receive. | MANUAL |

---

## §6 — Jira Lane (via pm_os poller — Phase P3)

**Note:** The Jira poller is Phase P3 work (not yet built). Until P3 ships, this lane is NOT DONE
by definition. The checks below are the gates that must pass when P3 lands.

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 6.1 | `node ~/Code/pm_os/bin/run-jira.js --limit 1` | Returns at least one Jira ticket JSON from the live Atlassian API | Empty result with no outbound API call = stub | MANUAL — needs Atlassian token |
| 6.2 | Simulate a Jira 401: set `ATLASSIAN_TOKEN` to an invalid value and run `run-jira.js` | Process surfaces "re-auth needed" and exits with non-zero; does NOT hang or retry silently | Silent failure, zero exit, or hanging retry = NOT DONE (violates R-4 pattern) | MANUAL |
| 6.3 | A real assigned Jira ticket transitions to `In Progress` after a factory job starts on it | Ticket state visible in Jira UI changes to In Progress | State unchanged = NOT DONE (broker write-back not wired) | MANUAL |
| 6.4 | Confirm a work/Diligent ticket never routes to OpenRouter: inspect the job store after enqueueing | `worker` column shows `claude` or `codex`, never `openrouter` | `openrouter` present for a Diligent ticket = hard policy violation | AUTO (once P3 lands) |

**Current status:** NOT DONE — Phase P3 not yet built. Mark this section complete only after P3 delivery.

---

## §7 — Calendar Lane (via pm_os — Phase P1c)

**Note:** Calendar meeting-prep (P1c deliverable 1c.4) is Phase P1c work. Until P1c ships, this
lane is NOT DONE by definition. The checks below are the gates that must pass when P1c lands.

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 7.1 | `node ~/Code/pm_os/bin/outlook-read-mail.js` piped through calendar extraction | At least one calendar event returned for the next 24 hours | No events + no API call in network log = stub | MANUAL |
| 7.2 | Trigger a meeting-prep run manually | A prep note arrives in the Telegram morning channel containing: attendees, related past-week threads, open action items | Prep note with placeholder "no attendees found" when real attendees exist = stub | MANUAL (check Telegram) |
| 7.3 | Confirm the FOCI token has Calendar scope: `node ~/Code/pm_os/bin/get-foci-token.js --for calendar` | Non-empty token with `Calendars.Read` in the decoded scope | Missing `Calendars.Read` = token insufficient for calendar reads | AUTO |

**Current status:** NOT DONE — Phase P1c not yet built.

---

## §8 — P0 Deliverables: Probe, Health Command, Caffeinate

| # | Action | Expected result | Stub-detection heuristic | Run how |
|---|---|---|---|---|
| 8.1 | `python scripts/probe.py --out /tmp/cap-check.json && cat /tmp/cap-check.json \| python -m json.tool \| grep -c live_catalog` | Count ≥ 1 (at least one model entry with `live_catalog` key) | `live_catalog` key absent = probe didn't run the catalog check | AUTO |
| 8.2 | `python scripts/probe.py` then deliberately break one model name and re-run | Probe exits non-zero with the broken name in the error output | Probe exits 0 for a phantom model name = stub detection failure | MANUAL |
| 8.3 | `python scripts/factory_health.py` | Exit code 0, all four sections populated with real values | Exit 0 with no HTTP calls (check with `--verbose` or `strace`) = stub | AUTO |
| 8.4 | Kill the hermes gateway process; within 60s confirm `launchctl list \| grep ai.hermes.caffeinate` still shows the service | Caffeinate service survives gateway crash | Service absent after gateway kill = NOT DONE (SG-P0-1 not applied) | MANUAL |
| 8.5 | Confirm `~/.hermes/factory/scheduler-tick` exists and has an mtime within the last 10 minutes (once scheduler is provisioned) | File exists, mtime < 10m ago | File missing or stale > 10m = scheduler not ticking | AUTO (once P2/P3 land) |

---

## §9 — Stub-Detection Summary

A lane returns a "stub" response when:

1. **No external round-trip occurred.** The code ran but never left the machine (no HTTP call,
   no socket connection to `api.telegram.org`, `api.anthropic.com`, `openrouter.ai`, or the
   Microsoft Graph endpoint). Detection: check response latency (< 2ms on a stub), check logs
   for outbound connection entries, use `--verbose` or OS network monitoring.

2. **The response is structurally valid but temporally impossible.** A send timestamp predates
   the run, a token expiry is in the past but status is "VALID", or message content is identical
   across multiple runs (canned fixture).

3. **Code says "connected" but the downstream app shows nothing.** A Telegram send that returns
   HTTP 200 but no message appears in the app = stub or wrong bot token.
   An email send that returns 202 but no email arrives within 5 minutes = stub or misconfigured SMTP.

4. **The lane "succeeds" with all credentials blank.** If removing `TELEGRAM_BOT_TOKEN` from env
   and re-running still returns success = the check is not exercising the real path.

**The canonical stub test:** strip the relevant credential, re-run the check, and confirm it
now fails. If it still succeeds, the check was theater.

---

## §10 — Staged Gates Cross-Reference

The following checklist items require a real Terminal session or owner secret and cannot be
automated without interactive MFA. See [STAGED-GATES.md](docs/plans/harness/fable/STAGED-GATES.md):

| Staged gate | Checklist items blocked until it flips |
|---|---|
| SG-P0-1: caffeinate + prewarm launchd bootstrap in real Terminal | §2.3, §8.4 |
| SG-P0-2: `OPENROUTER_API_KEY` in `~/.hermes/.env` | §1.3, §8.1 |
| SG-P0-3: Zombie-platform log silence after real gateway restart | §5.2 |
| SG-P0-4: `compute.md` tasks/night from dedicated gauntlet run | §8.5 (ceiling sizing) |

---

## §11 — Lane Summary Table

| Lane | Phase when live | Automatable? | Current status |
|---|---|---|---|
| Provider reachability (Anthropic + OpenRouter) | P0 | AUTO | Check `factory_health.py` exit 0 |
| Compute / caffeinate | P0 | AUTO + MANUAL (SG-P0-1) | Check `factory_health.py` §2 |
| Telegram send + receive | P0 / always-on | AUTO (token check) + MANUAL (round-trip) | Verify §3.3 and §3.4 |
| Email / M365 (Outlook via Graph) | P1c | AUTO (token health) + MANUAL (send+receive) | Requires pm_os tokens live |
| WhatsApp | Phase R (R-2) | AUTO (disabled check) + MANUAL (pair+verify) | DISABLED until R-2 |
| Jira (pm_os poller) | P3 | AUTO (once built) + MANUAL (auth check) | NOT DONE — P3 not built |
| Calendar (pm_os + Graph) | P1c | AUTO (token scope) + MANUAL (prep note) | NOT DONE — P1c not built |
| Probe + capabilities.json | P0 | AUTO | Check `probe.py` exits 0 |
| Health command | P0 | AUTO | Check `factory_health.py` exits 0 |
| Caffeinate anchor (launchd) | P0 | AUTO (process check) + MANUAL (bootstrap) | SG-P0-1 required |

---

*Last updated: 2026-07-06. Maintained in the `factory` branch.*
