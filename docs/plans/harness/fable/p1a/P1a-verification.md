# P1a Verification — Independent Re-Run (VERIFIER, GOVERNANCE_EXEMPT)

**Role:** VERIFIER — produced none of this; re-ran the work, fixed nothing.
**Branch:** `factory` (HEAD `2348a96a2`) · **Date:** 2026-07-06
**Method:** RE-RAN every suite (not re-read); neuter-and-restore anti-theater proof; loaded real
policies; confirmed both feature flags default OFF and existing suites unregressed.
**Gate verdict:** **PASS-WITH-STAGED** — every mechanism VERIFIED here as a genuine no-op for the
running gateway (flags OFF) and as a real wall when enabled; live cutover (SG-P1a-1..4) and the
one AF_UNIX socket-bind test are UNVERIFIED-BY-ENV / STAGED, honestly split below.

---

## Observed test counts (REQ-01 — my own run, `./venv/bin/python -m pytest ... -p no:cacheprovider`)

| Suite | Observed | Notes |
|---|---|---|
| `tests/broker/` (all) | **69 passed, 1 skipped** | skip = `test_socket_mode_is_0600` — AF_UNIX `bind()` blocked in sandbox (env-gated, correct) |
| `tests/worker/` | **22 passed** | incl. real 2-level process-tree kill + setsid-escape residual |
| `tests/test_factory_health_broker.py` | **3 passed** | broker health section + exit-code rule |
| `tests/broker/test_bypass_impossibility_gate.py` | **6 passed** | the exit-gate suite (a/b/c/d + MS Graph + Discord) |
| `tests/broker/test_egress_starving.py` | **5 passed** | classify / starve / real-loader process-env starve / off-by-default / broker-source |
| `tests/broker/test_egress_routing.py` | **5 passed** | routing flag / route / fail-closed / CLI refuse / git-push guard |
| Regression (existing): `tests/tools/test_send_message_tool.py` + `tests/hermes_cli/test_send_cmd.py` + `tests/hermes_cli/test_env_loader.py` + `tests/test_env_loader_secret_sources.py` + `tests/test_factory_health.py` | **212 passed, 0 regressions** | — |

No non-env-gated failure observed. The single skip is a sandbox AF_UNIX limitation (marked
UNVERIFIED-BY-ENV, not a failure): the test probes `_bind_available` and skips with a reason; the
identical production `serve_connection` path IS exercised over a socketpair in the same file.

---

## Claim verification table

| REQ | Claim | Verdict | Evidence (my run) |
|---|---|---|---|
| REQ-01 | Every P1a suite re-run, counts recorded | **VERIFIED** | table above; 69+22+3 core + 16 gate/starve/route + 212 regression |
| REQ-02 | Self-approval nonce guard is REAL (P0-3), not theater | **VERIFIED** | neutered `_validate`→`return True` ⇒ **7 security tests FAILED** incl. `test_assistant_cannot_self_approve_over_socket`; restored ⇒ 16 passed; `git status` clean |
| REQ-03a | Direct send-CLI/tool cannot send (creds absent) | **VERIFIED** | `_handle_send` routes to `_broker_enqueue` before `_send_to_platform`; broker down ⇒ `_error(...)`; monkeypatched `_send_to_platform=explode` never fired — genuine fail-closed, no mock |
| REQ-03b | `os.environ` starved of egress secrets | **VERIFIED** | `starve_egress_credentials` deletes from environ in place; real `env_loader.load_hermes_dotenv` run with flag ON ⇒ 0 egress keys, MODEL key present |
| REQ-03c | Raw git push refused | **VERIFIED** | `guard_git_push("git push origin main").blocked is True` (real regex); `GITHUB_TOKEN`/`GH_TOKEN` absent from starved env |
| REQ-03d | Assistant can still converse (MODEL key present) | **VERIFIED** | MODEL key survives starving (`test_gate_d`); classifier checks MODEL set FIRST |
| REQ-04 | NO-OP for running gateway — flags default OFF | **VERIFIED** | `egress_starving_active()`=False and `broker_routing_active()`=False with no env set; both gate on `in ("1","true","yes")`; `test_starve_off_by_default_is_noop` asserts BOTH classes still load |
| REQ-04 | Existing suites green (send/env/health) | **VERIFIED** | 212 existing tests pass, 0 regressions |
| REQ-05 | Grandchild-kill spawns real 2-level tree, all pids dead | **VERIFIED** | real `subprocess.Popen` parent→child+grandchild, all pids `ProcessLookupError` after `kill()` |
| REQ-05 | setsid-escape residual documented (not silent) | **VERIFIED** | `test_kill_setsid_escaping_grandchild_documented_residual` asserts DETECTION; docstring + report §residual |
| REQ-05 | Polymorphic dispatch unified (both worker types, one path) | **VERIFIED** | `_supervisor_dispatch(worker, spec)` routes Local + Remote identically |
| REQ-05 | Dual-use MS Graph secret escalation documented | **VERIFIED** | P1a-h-report §split L29-31 + §residuals L118-119: Graph read loses secret under starving, noted for P1c — not dropped |
| — | New control-room policies load via real loader | **VERIFIED** | `load_policies(Path(...))` ⇒ 10 policies incl. `terminal-git-push-deny` + `terminal-hermes-send-deny` |
| — | Nothing live touched | **VERIFIED** | no real `~/.hermes/.env` edit, no socket bound, no secret relocation; fixtures only; tree clean |

---

## Anti-theater proof (REQ-02) — verbatim

Edited `broker/approval.py::ApprovalAuthority._validate` body → `return True` (guard removed), re-ran:

```
FAILED tests/broker/test_server_client.py::test_assistant_cannot_self_approve_over_socket
FAILED tests/broker/test_server_client.py::test_removing_nonce_check_would_break_self_approve_guard
FAILED tests/broker/test_approval.py::test_approve_without_nonce_rejected
FAILED tests/broker/test_approval.py::test_approve_with_wrong_nonce_rejected
FAILED tests/broker/test_approval.py::test_nonce_bound_to_action_not_transferable
FAILED tests/broker/test_approval.py::test_nonce_single_use_replay_rejected
FAILED tests/broker/test_approval.py::test_cannot_mint_without_secret_matching
7 failed, 9 passed, 1 skipped
```

Restored the exact original body → `16 passed, 1 skipped`; `git status --short broker/approval.py`
= empty, `git diff` empty. **The self-approval wall is load-bearing, not decorative.** The nonce
(`_live` dict populated only by `mint_nonce`, which needs `broker_secret`) cannot be minted by the
assistant; `approve()` calls `_validate()` first and rejects None/wrong/burned/forged nonces.

---

## The 4 gate assertions test REAL behavior (REQ-03, no mock-into-passing)

- (a) `_handle_send` (real function) intercepts at `broker_routing_active()` and calls
  `_broker_enqueue` → `BrokerClient` → real UDS connect; with no broker up it raises
  `BrokerUnavailable` → returns `_error`. The test's `_send_to_platform=explode` sentinel is never
  invoked ⇒ proves no direct-send fallback. Not a mock of the wall.
- (b) `starve_egress_credentials(os.environ)` really deletes keys; asserted after the **real**
  `env_loader.load_hermes_dotenv` load path, on the process env (not a file read — closes the
  review's theater objection to the old `test_token_file_read_finds_no_live_cred`).
- (c) `guard_git_push` real regex `\bgit\b...\s+push\b` returns `blocked=True`; token-absence is
  the actual wall (`GITHUB_TOKEN`/`GH_TOKEN` not in env).
- (d) MODEL key resolves in the same starved process; classifier lists MODEL keys first.
- Plus `test_ms_graph_sendmail_fails_no_cred` / `test_discord_direct_fails_no_cred` assert real
  cred-absence (`discord_tool._get_bot_token()` returns `None`).

---

## STAGED vs VERIFIED (REQ-06 — honest split)

| Item | Status | Why |
|---|---|---|
| Broker core (UDS/JSON-RPC dispatch, held-store durability, commit-before-ack, one-way transitions, ULID, no-truncation) | **VERIFIED** | 53 broker-core tests green over real SQLite temp files + socketpair driving real `serve_connection` |
| Nonce approval authority (mint/validate/burn, action-bound, single-use, self-approve rejected) | **VERIFIED** | anti-theater proof above |
| Safe-lane 5 conditions (auto-send iff all 5; conservative default holds most) | **VERIFIED** | `test_safe_lane.py` green |
| Credential starving + MODEL/EGRESS split | **VERIFIED** | real env_loader run; flags-off no-op proven |
| Egress routing + git-push guard + `hermes send` refuse-or-route | **VERIFIED** | real `_handle_send` / `send_cmd` / `guard_git_push` |
| Worker process-group kill (same-pgid grandchild), mtime liveness, remote-stub dispatch | **VERIFIED** | real subprocess trees |
| Both new control-room belt policies load | **VERIFIED** | `load_policies` ⇒ 10 total |
| Existing gateway suites unregressed with flags OFF | **VERIFIED** | 212 pass |
| `test_socket_mode_is_0600` (real pathname `bind()` mode 0600) | **UNVERIFIED-BY-ENV** | AF_UNIX `bind()` blocked in sandbox; code present (`umask(0o177)`+`chmod 0o600`); runs on any host where bind works |
| Live secret migration + broker launchd + gateway cutover (SG-P1a-1..4) | **STAGED** | relocates real secrets / restarts gateway — owner-only; documented in STAGED-GATES.md with rollback |
| Keychain-backed egress source under broker launchd session | **STAGED/UNVERIFIED** | flagged in design §1.6 + report §residual; validate empirically at cutover |
| setsid-escaping grandchild full genealogy kill | **RESIDUAL (P2)** | honestly documented; P1a use-cases (`claude -p`, `codex exec`) don't self-daemonize |
| Subagent `terminal` block-set hardening | **DEFERRED (P1c)** | closed by cred-absence in P1a; `DELEGATE_BLOCKED_TOOLS` add is out of file-disjoint scope |

---

## BLOCKING list

**None.** No non-env-gated test failed; the security wall is proven real (anti-theater); the
change is a verified no-op for the running gateway (both flags default OFF, 212 existing tests
green, `test_starve_off_by_default_is_noop` asserts both credential classes still load).

## Overall gate verdict: **PASS-WITH-STAGED**

The bypass-impossibility mechanism actually works when enabled AND is a genuine no-op while flags
are OFF. The only unverified pieces are environment/live-cutover-gated (socket bind mode, real
secret relocation) and honestly staged in STAGED-GATES.md — not silent gaps. Git tree clean.
