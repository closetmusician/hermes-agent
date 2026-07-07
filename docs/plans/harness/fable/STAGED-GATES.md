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

## From Phase P1a (broker egress cutover — P1a-h)

The credential-starving + broker-routing mechanism is BUILT and tested with fixtures, but
flipping it live relocates real secrets and breaks the running gateway's direct-send path
until the broker is up and the gateway is restarted to route through it. Do these IN ORDER;
each step is reversible until step 3.

| ID | Gate | Why staged | Exact action to flip |
|---|---|---|---|
| SG-P1a-1 | Egress secrets relocated to broker-only source | Moving real send/push tokens out of `~/.hermes/.env` while the running gateway still reads them would break live egress immediately | 1. Create the broker egress source `~/.hermes/broker/egress.env` (mode 0600): move every EGRESS key from `~/.hermes/.env` — `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `WEIXIN_TOKEN`, `SLACK_BOT_TOKEN`, `SIGNAL_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN`, `MICROSOFT_GRAPH_CLIENT_SECRET`/`MS_GRAPH_CLIENT_SECRET` (full list = `hermes_cli/egress_creds._EGRESS_EXACT`). LEAVE the MODEL keys (`ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`/`OPENAI_API_KEY`) in `~/.hermes/.env`. 2. Also remove those EGRESS keys from any managed `/etc/hermes/.env` and from the assistant's Bitwarden source map. 3. `chmod 600 ~/.hermes/broker/egress.env`. |
| SG-P1a-2 | Broker launchd service running (separate PID) | The broker must own the socket + creds before the gateway is pointed at it | In a real Terminal: `cp broker/launchd/com.hermes.broker.plist ~/Library/LaunchAgents/`, then `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.hermes.broker.plist`; confirm `launchctl list \| grep com.hermes.broker` and `test -S ~/.hermes/broker/broker.sock`. The broker's launchd env (or its `egress.env`) must carry the EGRESS keys from SG-P1a-1; the assistant/gateway env must NOT. |
| SG-P1a-3 | Gateway restarted with starving + routing ON | Enabling `HERMES_BROKER_EGRESS_STARVE=1` + `HERMES_BROKER_ROUTE=1` mid-conversation would strip live creds from the running gateway; requires a clean restart | Set `HERMES_BROKER_EGRESS_STARVE=1` and `HERMES_BROKER_ROUTE=1` in the gateway's env (e.g. `~/.hermes/.env`), then restart the gateway. After restart, in a fresh assistant process: `hermes ... -c "python: import os; print([k for k in os.environ if 'TOKEN' in k or 'SECRET' in k])"` shows NO egress key, and a MODEL key is still present (the assistant can still converse). |
| SG-P1a-4 | Real send goes through approval | Only provable with a live broker + Telegram button round-trip | With the broker up, trigger a `send_message` from the assistant to a NON-allowlisted recipient → it must be HELD, not sent; approve via the Telegram button (nonce-gated); confirm the message arrives and the held-action row transitions to `executed` in `~/.hermes/broker/held_actions.db`. Then confirm a raw `hermes send` / `git push` from the assistant produces NO effect (cred-absence wall). |

> Rollback (before SG-P1a-4 is trusted): unset `HERMES_BROKER_ROUTE`/`HERMES_BROKER_EGRESS_STARVE`, move the egress keys back into `~/.hermes/.env`, restart the gateway — the routing gate and starving filter are both no-ops when the flags are unset, so the pre-cutover direct-send path is fully restored.

## From Phase P2 (supervisor — first magic on real hardware)

The P2 SUPERVISOR (`factory/supervisor.py`) drives one job intake→worktree→gauntlet→held
merge→single approval→merge end to end as plain code (no AI in the loop). The full loop is
proven in `tests/factory/test_supervisor.py` with the ONLY mock being the worker CLI
subprocess (`claude -p`/`codex exec`) and the network-push boundary (a local bare "remote").
The one thing tests cannot substitute is a real worker CLI producing a real feature on the
live host with a real Telegram approval — that is the honest "first magic" gate below.

| ID | Gate | Why staged | Exact action to flip |
|---|---|---|---|
| SG-P2-1 | One real first-magic job merges with a single live approval | Tests mock the worker CLI + network push; a real `claude -p` worker + real repo + real Telegram nonce cannot be exercised in-sandbox | On the live host with the broker running (SG-P1a-2/3 flipped) and a scratch GitHub repo: 1. `hermes` DM `/factory <scratch-repo>: add a hello endpoint` (or add a `factory: <repo>: <spec>` line to `tasks.md`). 2. Confirm ONE job row: `sqlite3 ~/.hermes/factory/jobs.db "select id,state,kind from jobs order by created_ts desc limit 1"`. 3. Watch QUEUED→RUNNING→TEST→REVIEW→AWAITING_APPROVAL via `hermes logs --follow`; a real `claude -p` worker builds the feature in `~/.hermes/factory/worktrees/<id>-*` and commits LOCALLY (no push — its scrubbed env has no `GITHUB_TOKEN`). 4. A held `merge` card arrives on Telegram (diff-stat + test + review findings + [Approve]). 5. Tap Approve ONCE (the only human touch). Confirm the broker merge executor rebases→re-tests→merges→pushes and the scratch repo's `main` advances; the job row → `DONE`. 6. Same run negative checks: exactly one approval was requested between intake and merge; rejecting instead parks the job `NEEDS_ATTENTION` without touching `main`. |
| SG-P2-2 | Real ring-diff rejection on the live host | Ring gate is unit-proven (neuter→fail); a live worker attempting a `broker/**` edit closes the loop | On the live host: `/factory <scratch-repo>: edit broker/server.py to add a comment`. Confirm the job ends `NEEDS_ATTENTION` with `fail_reason` containing `ring`, and NO `merge` row is ever enqueued (`sqlite3 ~/.hermes/broker/held_actions.db "select type,state from held_actions"`). |
| SG-P2-3 | Real cost-stop fires on a live over-budget worker | Budget metering off a live token stream (not a stub) is only real on the host | On the live host: `/factory <scratch-repo>: <a deliberately huge task>` with a tiny `budget_usd` (e.g. 0.05). Confirm the worker is killed (SIGTERM→SIGKILL to its pgid), the job ends `FAILED`/`fail_reason=budget`, and `hermes logs` shows `cost stop BUDGET fired`. |

> Prerequisite: SG-P1a-2/3 (broker running + gateway routing) must be flipped first — the
> merge card and the model-key injection both ride the live broker socket. Until then, the
> supervisor loop is exercisable only via the mocked-boundary tests (which are green).

## From Phase P1b-d (tier-1 auto-merge wiring)

The composition root (`factory/broker_launch.py`) injects the REAL factory `compute_tier` +
trust-ledger reader + owner-profile ceiling into the broker's `MergeGate`; the production
supervisor holds NO mint authority and REQUESTS auto-merge over the socket
(`BrokerClient.auto_merge`). The full path — graduated clean job auto-merges without a human,
ring-touching/tier-0 jobs stay held server-side — is proven over a real socketpair in
`tests/factory/test_broker_launch.py` (+ the no-mint contract in
`tests/factory/test_supervisor_trust.py`). The one thing tests cannot substitute is a REAL
graduated repo with a live worker producing a real diff that the broker auto-merges with NO
human tap — that is the honest gate below.

| ID | Gate | Why staged | Exact action to flip |
|---|---|---|---|
| SG-P1b-3 | A real graduated (repo×task-type) auto-merges with NO human tap on the live host | Auto-merge is unit+socket-proven with injected diff/ledger; a real graduated repo + live worker diff + broker-internal nonce mint can only be exercised on the host after SG-P1b-1 (30-day/10-clean record) is real | Prereq: SG-P1a-2/3 (broker+gateway live), SG-P2-1 (first-magic loop works), and SG-P1b-1 (a real `(repo,task_type)` has ≥10 consecutive `merged_clean` / 0 reverts / ≥30 days in `~/.hermes/factory/jobs.db` trust_ledger). 1. Set the owner profile: `echo auto-merge-personal-non-prod > ~/.hermes/factory/trust-profile.txt`. 2. Wire the prod supervisor with `build_production_supervisor(..., broker_client=BrokerClient(sock))` and construct the broker with `merge_gate=build_merge_gate(ledger=TrustLedger(jobs.db), repo_class_for=<classify>, profile=load_trust_profile())`. 3. `/factory <graduated-personal-repo>: <a clean feature of the graduated task-type>`. 4. Watch the loop reach a held `merge` card; the supervisor then calls `request_auto_merge(action_id)`. 5. Confirm the broker's server-side gate re-runs (ring + never-graduates + tier recompute over the diff), returns `auto`, mints+burns a nonce INTERNALLY, and the merge lands with `decided_by="trust:auto"` and NO Telegram tap. `sqlite3 ~/.hermes/broker/held_actions.db "select type,state,decided_by from held_actions order by created_ts desc limit 1"` → `merge|executed|trust:auto`. 6. Negative checks in the same session: a job whose diff touches a ring path (e.g. edits `broker/**`) → the `auto_merge` request returns `held` (reason `ring`), the executor NEVER runs, the card stays held for a human; a tier-0 (ungraduated) repo → `held` (reason `tier`); flipping the profile back to `ask-for-everything` → every request returns `held` regardless of record. |

> Prerequisite chain: SG-P1a-2/3 → SG-P2-1 → SG-P1b-1 (the 30-day/10-merge graduation, still
> pending real wall-clock) must all be real before SG-P1b-3 can be observed live. Until then
> the auto-merge path is exercisable only via the socketpair-driven tests (which are green).

## From later phases
(appended as each phase completes — P4 3-night flagship, P1b 30-day/10-merge graduation
SG-P1b-1/2 above, P1c real-meeting prep, etc.)
