# Hermes Factory — Fable Orchestration Completion Report

**Run:** 2026-07-06 → 2026-07-07. **Branch:** `factory` (85 commits on upstream `a05b64d67`). **Pushed:** closetmusician/hermes-agent.
**Plan:** docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md (phases P0→P6).

## What was built — the autonomous software factory
Given a coding task (spec doc, Jira ticket, existing repo, voice note), the factory splits it into an
acceptance-test-first task graph, runs a supervised fleet of AI workers in isolated git worktrees, gates
every change through the repo's own tests + independent AI review + safety gates, and surfaces
batch-approvable PR cards the owner taps from Telegram. Trust is earned per repo×task-type; overnight
runs fail over across model tiers under a hard cost ceiling; the fleet watches itself and learns.

## Method
Every phase: **design (opus) → adversarial red-team (opus) → file-disjoint TDD implementation (sonnet/haiku)
→ independent verifier (opus) that RE-RUNS + neuters each guard to prove it's load-bearing.** Commit+push
per task. The red-team caught real P0s in P0-2, P1a, P1b, P2, P3, P4 (see per-phase reviews); the verifiers
caught a false-GREEN (P0), a guard left neutered on disk (P4-b), and a ring-superset gap (P6) — all fixed.

## Phase results (all PASS-WITH-STAGED, 0 BLOCKING at close)
| Phase | Delivered | Verifier |
|---|---|---|
| P0 | Upstream-current base; 3 governance plugins on native `pre_tool_call` (F1/F3 substring-evasion fixes); probe+capabilities.json; measured compute.md (11 tasks/night); caffeinate/prewarm launchd; jobs-first health; zombie platforms silenced | 26 VERIFIED / 2 STAGED |
| R | Fail-closed mandatory-plugin loading; WhatsApp verify-or-quiet; single token-refresh owner (+sp-checkout lock); SSO re-auth signal; degraded lane; behavioral SETUP-CHECKLIST; scheduled-skill preflight | 5 VERIFIED / 1→closed |
| P1a | Broker (UDS+JSON-RPC, SQLite held-store, HMAC single-use nonce = self-approval-proof, fail-closed, 5-cond safe-lane); Worker contract (process-group kill); cred-starving wall + egress routing (flags OFF = running gateway untouched) | 13 VERIFIED / 3 STAGED |
| P2 | Job store + state machine; intake; worker runner (replacement-whitelist scrub); injection scan; immutable ring (canonicalized); 3 cost stops (atomic); test+review gauntlet; broker merge path; supervisor (first-magic e2e, one approval) | 5 VERIFIED, safety proven |
| P1b | Trust ledger; compute_tier (hard gates: work/Diligent + never-graduates → tier-0); owner profiles; broker server-side auto-merge gate (fail-closed, no-mint-RPC); supervisor holds no mint (auto-merge over socket) | 6 VERIFIED (shared w/ P1c) |
| P1c | Exactly-one-send ledger (TOCTOU fixed); inbox sweep (stable-id key); morning triage; calendar meeting-prep | (shared w/ P1b) |
| P3 | Task-splitter (pure ambiguity rubric, AI self-score ignored); spec-review gate → questions.md; fleet scheduler (cap-3 atomic admission, fan-out/serialize, budget settlement); Jira poller (residency-gated); repo onboarding; multi-intake | 182 tests, 0 BLOCKING |
| P4 | Phase-checkpoint + real crash-resume (RESUMABLE state); 429 failover router (residency on every rung, WAIT_FOR_CAPACITY); $2 OpenRouter sub-ceiling two-CAS; overnight queue + confidence-ranked morning packet; PR cards; cost trend; confidence score; voice intake | 117 tests, 0 BLOCKING |
| P5 | Per-model scorecard + routing view (orders never adds); retro two-wall self-modification guard (propose-gate + apply-executor, both fail-closed); watchdogs (drift/regression/memory-zone/GC); failure forensics; skill-improve + pattern bank | 124 tests, 0 BLOCKING |
| P6 | Mission-control cards (no-bypass: read-only reader, injected reconciler, out-of-band nonce); voice consolidation (cross-note concat scan); proactive-contract (≤1 nudge, read-as-data); pm_os wiring (parked pipelines absent) | 50 tests, B1 fixed |

## Security invariants proven load-bearing (each verified by neuter-and-fail)
- Broker nonce is self-approval-proof; the assistant/supervisor cannot self-mint (P1a, P1b, P6).
- A work/Diligent job physically cannot receive an OpenRouter key — at the worker seam AND every failover rung (P3, P4).
- The immutable ring rejects any worker/retro/auto-merge diff touching broker source / trust-policy / cost-stops / never-graduates / the gate's own source; canonicalized against rename/symlink/traversal (P2, P5, P6).
- The retro cannot edit the factory's own guardrails (two walls: propose + apply) (P5).
- Merge/control actions never force-push and never bypass approval; a failed git step fails loud, no false "merged" (P2, P6).
- Cost: neither the $5 nightly nor $2 OpenRouter ceiling is breachable under concurrency (P4).

## What is STAGED (needs an owner-in-the-loop or wall-clock event) — see STAGED-GATES.md
- Live broker cutover: migrate egress secrets to broker-only, start the broker launchd service, restart the gateway so egress routes through it (SG-P1a-1..4). Until then the flags are OFF and the running gateway is unchanged.
- caffeinate/prewarm launchd bootstrap (needs a real Terminal); OpenRouter key in ~/.hermes/.env (owner secret).
- The P4 3-night flagship, P1b 30-day/10-merge graduation, P1c/P6 real-meeting/voice/live-steer, 2-week cost trend — mechanisms built + synthetically verified; flip as real runs accrue.
- pm_os R-phase + P1c code (single token owner, SSO signal, preflight, inbox/meeting-prep) is on disk in ~/Code/pm_os (separate repo), tested green, UNCOMMITTED — see PM-OS-HANDOFF.md.

## Honest limitations
- ~16 git-based tests fail under this session's macOS sandbox (`Operation not permitted` on `git init`); confirmed environmental (pass in writable tmp), not code. Re-run on a normal host/CI to see them green.
- repo-onboarding uses config-file detection, not the deep codebase-mapping/code-review-graph map (gate still met).
- code-review-graph cross-repo transfer + risk-scoring degrade to documented placeholders when the graph is unavailable offline.
