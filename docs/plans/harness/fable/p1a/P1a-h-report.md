# P1a-h Report — Egress routing + credential-starving (the bypass-impossibility wall)

**Task:** P1a-8 + red-team P0 closures at the integration layer · **Mode:** backlog (GOVERNANCE_EXEMPT)
**Branch:** `factory` (verified) · **Role:** CODER · **Date:** 2026-07-06

Objective met: the broker is now the ONLY sanctioned egress path, enforced by two disjoint,
independently-staged mechanisms — a **credential-starving filter** (the wall: EGRESS secrets
leave the assistant's `os.environ`, MODEL keys stay) and a **route-or-refuse gate** (the belt:
send/push paths frame `enqueue_action` or fail-closed). Both are **feature-flagged OFF by
default**, so this change is a NO-OP for the currently-running gateway until the owner performs
the staged cutover (STAGED-GATES SG-P1a-1..4). Nothing live was touched: no real `~/.hermes/.env`
edit, no gateway restart, no secret relocation — fixtures only.

---

## Credential inventory + the split (REQ-01, design §1.6)

Classifier: `hermes_cli/egress_creds.py` — `is_egress_credential(name)`. MODEL keys checked
FIRST so an `_API_KEY` suffix never starves the reasoning key.

| Class | Keys | Fate after cutover | Who holds it |
|---|---|---|---|
| **MODEL** (stays in assistant env) | `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_AUTH_TOKEN` | kept — assistant must converse | assistant env |
| **EGRESS** (removed from assistant env) | `TELEGRAM_BOT_TOKEN`, `DISCORD_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SIGNAL_TOKEN`, `WEIXIN_TOKEN`, `QQ_BOT_TOKEN`/`QQBOT_TOKEN`, `BLUEBUBBLES_TOKEN`, `YUANBAO_TOKEN`, `MATRIX_ACCESS_TOKEN`, `WHATSAPP_TOKEN`, `TWILIO_AUTH_TOKEN`, `GITHUB_TOKEN`, `GH_TOKEN`, `MICROSOFT_GRAPH_CLIENT_SECRET`/`MS_GRAPH_CLIENT_SECRET`/`GRAPH_CLIENT_SECRET` | relocated to broker-only source | broker process |

**Split decision (escalation, REQ-01):** membership is a conservative **explicit allow-list**,
not a suffix-greedy rule. Rationale: an `_API_KEY`/`_TOKEN` suffix is EGRESS-shaped but some are
model-reasoning keys; over-stripping would break inference. No dual-use secret was found in the
enumerated set (MS Graph send secret and read secret are the same client secret — treated as
EGRESS because its send capability is the higher-privilege one; the broker holds it and the
assistant's Graph *read* tools lose it too, which is acceptable and noted for P1c follow-up).

---

## REQ-by-REQ evidence

**REQ-01 (starving mechanism)** — `hermes_cli/egress_creds.py` (new): `starve_egress_credentials(environ)`
strips EGRESS in place, returns removed names; `load_broker_egress_source(path)` reads the
broker-only 0600 source; `egress_starving_active()` gates on `HERMES_BROKER_EGRESS_STARVE`.
Wired into `hermes_cli/env_loader.py:249` (`_starve_egress_if_enabled`, runs LAST after
dotenv/Bitwarden/managed so it removes anything re-hydrated). Tests:
`tests/broker/test_egress_starving.py` (6) — classify, starve-only-egress, real-loader process-env
starve, off-by-default no-op, broker-source-gets-egress.

**REQ-02 (route-or-deny)** — `tools/egress_broker.py` (new): `broker_routing_active()`,
`_broker_enqueue()` (fail-closed via `broker_client`), `guard_git_push()`, `route_message()`.
`tools/send_message_tool.py` `_handle_send:298` re-routes to `enqueue_action` when routing is
active (with `_broker_direct` escape so the broker's own executor doesn't recurse). MS Graph /
Discord closed by cred-absence (their tokens are EGRESS-classified → starved). git push closed by
cred-absence + `terminal-git-push-deny.yaml` belt. Tests: `tests/broker/test_egress_routing.py` (5).

**REQ-03 (close P0-1, `hermes send`)** — `hermes_cli/send_cmd.py`: `_maybe_route_or_refuse()`
runs BEFORE `_load_hermes_env()`; when routing is active the CLI frames `enqueue_action` or
refuses (exit 1) — it never reaches the direct adapter. `--list` short-circuits first (read-only).
Test: `test_hermes_send_cli_refuses_or_routes` (real subcommand, not a stub).

**REQ-04 (health + the 4-gate suite)** — `scripts/factory_health.py`: `check_broker()` (socket
reachable? pending count via `broker_client.health`), `[3b] Broker` render section, exit-code rule
(socket-present-but-dead ⇒ HERMES/exit 2), wired into `run_health_check`. Tests:
`tests/test_factory_health_broker.py` (3). Gate suite `tests/broker/test_bypass_impossibility_gate.py` (6):
- (a) `test_gate_a_direct_send_cli_fails_no_creds` — PASS (direct send raises if reached; broker-down ⇒ error, no send)
- (b) `test_gate_b_env_starved_of_egress` — PASS (fresh loader run ⇒ no EGRESS key in `os.environ`)
- (c) `test_gate_c_raw_git_push_refused` — PASS (guard blocks + no `GITHUB_TOKEN`/`GH_TOKEN` in env)
- (d) `test_gate_d_assistant_can_still_converse` — PASS (MODEL key survives starving)
- (+) `test_ms_graph_sendmail_fails_no_cred`, `test_discord_direct_fails_no_cred` — PASS.

**REQ-05 (staged procedure)** — `docs/plans/harness/fable/STAGED-GATES.md`: rows SG-P1a-1..4
(relocate egress secrets → start broker launchd → restart gateway with flags ON → verify a real
send goes through approval) + rollback note.

---

## RED → GREEN

- RED: all 4 new test files failed on first run (15 failing: missing `egress_creds`, `egress_broker`,
  loader flag, git guard, `check_broker`). Captured before any implementation.
- GREEN: **19/19** new tests pass. Regression check: `test_send_message_tool.py` (150),
  `test_send_cmd.py` (21), `test_factory_health.py` (27), full `tests/broker/` + env_loader/
  bitwarden/managed-scope suites — **267 existing tests pass, 0 regressions**. New control-room
  policies load via the real `load_policies` loader (10 total). Git-push executor verified
  fail-closed in a clean env.

Gate-test command: `scripts/run_tests.sh tests/broker/test_bypass_impossibility_gate.py` ⇒ `6✓ 0✗`.

---

## Files changed

**New:** `hermes_cli/egress_creds.py`, `tools/egress_broker.py`,
`broker/executors/__init__.py`, `broker/executors/message_executor.py`,
`broker/executors/git_push_executor.py`,
`plugins/control-room/policies/terminal-git-push-deny.yaml`,
`plugins/control-room/policies/terminal-hermes-send-deny.yaml`,
`tests/broker/test_egress_starving.py`, `tests/broker/test_egress_routing.py`,
`tests/broker/test_bypass_impossibility_gate.py`, `tests/test_factory_health_broker.py`.

**Modified:** `hermes_cli/env_loader.py` (+`_starve_egress_if_enabled`, tail of `load_hermes_dotenv` ~L245-283);
`tools/send_message_tool.py` (`_broker_enqueue` seam + routing block at top of `_handle_send`, ~L298-355; `_broker_direct` escape);
`hermes_cli/send_cmd.py` (`_broker_enqueue`, `_maybe_route_or_refuse`, `cmd_send` gate ~L298-395);
`scripts/factory_health.py` (`DEFAULT_BROKER_SOCKET`, `check_broker`, exit-code rule, `[3b]` render, run wiring);
`broker/server.py` (`main()` wires `build_message_executor` instead of `_refuse`).

**Docs:** `docs/plans/harness/fable/STAGED-GATES.md` (SG-P1a-1..4).

---

## What's LIVE vs STAGED

- **LIVE now (flags OFF, no-op for running gateway):** all classifier/filter/routing/guard/executor
  code + tests + policies + health section. Verified by fixture tests.
- **STAGED (owner flips, SG-P1a-1..4):** relocating real EGRESS secrets out of `~/.hermes/.env`;
  starting the broker launchd service; setting `HERMES_BROKER_EGRESS_STARVE=1` +
  `HERMES_BROKER_ROUTE=1` and restarting the gateway; the live approval round-trip. These break the
  running gateway's direct-send until cutover, so they are owner-only.

## Residuals / follow-ups (not in P1a-h scope)

- MS Graph *read* tools lose the Graph client secret under starving (it's EGRESS-classified). Noted
  for P1c — split a read-only Graph credential if read access is needed assistant-side.
- Subagent `terminal` path (B14): closed by cred-absence here; `DELEGATE_BLOCKED_TOOLS` hardening to
  add `terminal` is deferred to P1c (out of file-disjoint scope), per design §2.
- Keychain-backed egress source (vs the 0600 `egress.env`) is UNVERIFIED under broker launchd — the
  design flags empirical validation at cutover; `credentials.egress_cred` already abstracts the source.
