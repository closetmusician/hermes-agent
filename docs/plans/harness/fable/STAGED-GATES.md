# Staged Gates — owner actions to flip real-world gates

These gates were built + verified with synthetic/controlled tests but need a real-world
event (Terminal session, wall-clock time, an owner secret, or a live run) to flip to fully
live. Each row has the exact command/procedure. Owner runs these as the world provides them.

## From Phase P0

| ID | Gate | Why staged | Exact action to flip |
|---|---|---|---|
| SG-P0-1 | caffeinate + prewarm launchd services running | `launchctl bootstrap` returns I/O error under the agent sandbox | In a real Terminal: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.hermes.caffeinate.plist` and `...ai.hermes.prewarm.plist`; then `launchctl list \| grep ai.hermes` shows both. |
| SG-P0-2 | Live OpenRouter catalog + phantom-fail proof | `OPENROUTER_API_KEY` not in `~/.hermes/.env` (owner-managed secret) | Add `OPENROUTER_API_KEY=...` to `~/.hermes/.env`, then `venv/bin/python scripts/probe.py`; confirm each ladder model gets `live_catalog:true/false` and `zhipuai/GLM-5.2` fails loudly. |
| SG-P0-3 | Zombie-platform log silence confirmed live | Running gateway loaded pre-P0-9 config; silence only provable after restart | After next gateway restart: `hermes logs --since 30m \| grep -Ei 'discord\|whatsapp'` shows zero reconnect/retry lines. |
| SG-P0-4 | compute.md tasks/night from a dedicated gauntlet | Figures derived from n=5 real fable-run subagents (proxy), not a dedicated metered gauntlet | Run the procedure in `compute.md §STAGED` (≥20 metered factory jobs) and update the ceiling. |

## From Phase R

| ID | Gate | Why staged | Exact action to flip |
|---|---|---|---|
| SG-R-2 | WhatsApp round-trip probe passes live | Probe requires `registered=true` + a live WhatsApp session; current `creds.json` has `registered=false` and the connector is `enabled: false` | After pairing: enable WhatsApp (`WHATSAPP_ENABLED=true` in `~/.hermes/.env`), run `hermes whatsapp` and scan QR, then restart gateway. `hermes logs` must show `Round-trip probe passed (messageId=...)` within 60s of startup. |

## From later phases
(appended as each phase completes — P4 3-night flagship, P1b 30-day/10-merge graduation,
P1c real-meeting prep, etc.)
