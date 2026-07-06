# P1a Design — Adversarial Review (bypass-impossibility audit)

**Reviewer role:** Adversarial reviewer, investigate-audit mode. Read-only, findings-only.
**Design under review:** `docs/plans/harness/fable/p1a/P1a-design.md` @ commit `4e70ce981`
**Plan:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P1a
**Branch verified against:** `factory` (HEAD `4e70ce981`)
**Date:** 2026-07-06

---

## Verdict: **SHIP-WITH-FIXES**

The design's *thesis is correct and honestly stated*: the only durable wall is credential
absence (P1a-8), and the guards are defense-in-depth. That intellectual honesty is the
design's biggest strength and I could not break the thesis. **But the design under-scopes
what "egress" is.** It re-routes exactly one tool (`send_message`) and names one CLI-shell
vector (`git push`), while the repo contains **at least five other live egress paths that
share the same `~/.hermes/.env` credentials** and are never mentioned. Because the whole
boundary collapses to "is `~/.hermes/.env` (and the process env loaded from it) actually
starved of live credentials," and because `cli.py` + `run_agent.py` **both load that file
into `os.environ` with `override=True` today**, P1a-8 is not just "load-bearing" (as the
design says) — it is the *entire* wall, and its scope in the design is too narrow to
guarantee closure. Fixable in the design before coding; hence SHIP-WITH-FIXES, not BLOCK.

**Counts:** P0 = 2 · P1 = 4 · P2 = 3

### Top 3 findings (each with the exact required fix)

1. **[P0] `hermes send` CLI subcommand is an unrouted direct send path the fresh-context
   gate explicitly forbids.** `hermes_cli/send_cmd.py` is a full CLI subcommand that calls
   `load_dotenv(~/.hermes/.env)` itself (`send_cmd.py:220-250`, `_load_hermes_env`) and
   then sends via `send_message_tool` internals — with **no broker involvement**. The
   assistant reaches it with one `terminal` call: `hermes send telegram "..."`. The design
   mentions `send_cmd` **zero times** (grep-confirmed). The plan's gate says "direct
   send-CLI ... must be impossible" — this *is* the direct send-CLI, and the design does
   not close it.
   **Fix:** Add `hermes_cli/send_cmd.py` to the §1.3 integration points and to P1a-h's
   owned files: `hermes send` must (a) refuse to run in an assistant context, or (b)
   re-route through `broker_client.enqueue_action` exactly like the tool. Add a RED test
   `test_hermes_send_cli_routes_to_broker_or_refuses`. Add a `terminal`/`execute_code`
   control-room deny policy for the literal `hermes send` command as belt.

2. **[P0] The design's credential-absence plan omits the process-wide `.env` loaders and
   the managed-scope env — the exact mechanisms that put creds in the assistant's process
   today.** `cli.py:177` and `run_agent.py:119` both call
   `hermes_cli.env_loader.load_hermes_dotenv`, which loads `~/.hermes/.env` into
   `os.environ` with `override=True` (`env_loader.py:238`), and `_apply_managed_env`
   re-applies a managed `.env` last with override. `env_loader.py`'s own docstring admits
   the boundary "does NOT prevent the agent from later mutating `os.environ` ... that hard
   boundary is a documented v2 item." So *removing keys from the broker's launchd env is
   necessary but not sufficient*: if any send/push/API key remains in `~/.hermes/.env` (or
   managed `.env`, or Bitwarden via `_apply_external_secret_sources`), every assistant
   process re-hydrates it into `os.environ` at startup, and `send_cmd`/`microsoft_graph`/
   `discord`/`GITHUB_TOKEN` readers all find it live.
   **Fix:** P1a-8's scope must explicitly enumerate: `~/.hermes/.env`, managed-scope
   `/etc/hermes/.env`, Bitwarden secret sources, and `config.yaml` bridged scalars — and
   assert *the assistant/gateway process env contains none of the send/push/API keys after
   P1a-8*. The RED test `test_token_file_read_finds_no_live_cred` (design 5.2/P1a-h #2)
   must be strengthened to also assert `os.environ` in a fresh assistant process holds no
   live send credential — not merely that a token *file* read finds nothing.

3. **[P1] `send_message_tool.py:142` is NOT the single egress chokepoint the design's
   reuse table claims.** Line 142 is the `SEND_MESSAGE_SCHEMA` dict, not the send body.
   The real send fan-out is `_handle_send` (`:298`) → `_send_to_platform` (`:723`) → nine
   per-platform senders (`_send_telegram :1017`, `_send_signal :1283`, `_send_weixin
   :1603`, `_send_bluebubbles :1624`, `_send_qqbot :1682`, `_send_yuanbao :1754`,
   `_send_via_adapter :628`, `_send_matrix_via_adapter :1482`). Re-routing "line 142" is
   imprecise; and **egress lives in other tools too**: `tools/microsoft_graph_auth.py`
   (`httpx.post` to Graph `/sendMail`, token from env), `tools/discord_tool.py` (direct
   Discord API), plus `GITHUB_TOKEN`/`GH_TOKEN` readers (`skills_hub.py:336`,
   `tirith_security.py:286`) enabling `git push https://$TOKEN@host`.
   **Fix:** Correct the reuse table's cited locus to `_handle_send` (`:298`) and add a
   design subsection "Full egress inventory" listing every tool that sends/posts/pushes,
   each mapped to its closure (re-route vs. cred-absence). The bypass table (§2) must gain
   rows B10 (MS Graph tool), B11 (Discord tool), B12 (`git push` via HTTPS token), each
   resolved by P1a-8 cred-absence + a note that the closure depends *only* on cred-absence,
   never on the (incomplete) regex guards.

---

## REQ-01 — Upstream fact verification (every claim opened at file:line)

| # | Claim (design cite) | Cited loc | Verdict | Actual finding |
|---|---|---|---|---|
| 1 | `pre_tool_call` hook + `{"action":"block",...}` protocol | `hermes_cli/plugins.py:2140` | **CONFIRMED** | `:2140` is `set_thread_tool_whitelist`; `get_pre_tool_call_block_message` immediately follows with the documented block protocol and `invoke_hook("pre_tool_call", ...)`. Mechanism real. |
| 2 | Per-thread tool whitelist | `plugins.py:2161` | **CONFIRMED (approx)** | `:2161` is inside the docstring; the actual whitelist enforcement is `:2162-2165` (`allowed is not None and tool_name not in allowed`). Off by ~2 lines, mechanism real. |
| 3 | control-room `pre_tool_call` policy hook | `plugins/control-room/__init__.py:699` | **CONFIRMED** | `:699` is exactly `def pre_tool_call`. Fail-closed on exception is real (docstring + `terminal` hardcoded outlook block at `:711+`). |
| 4 | SQLite tables `audit_log`/`policy_log`/`workflow_state` | `:36/:48/:58` | **CONFIRMED** | `_DB_SCHEMA` defines all three at those offsets. |
| 5 | control-room uses SQLite | `:81` | **CONFIRMED** | `AuditDB.__init__` opens `sqlite3.connect` at `:81`. |
| 6 | `register()` wires hooks | `:990` | **CONFIRMED** | `ctx.register_hook("pre_tool_call", cr.pre_tool_call)` at `:990`. |
| 7 | `send_message` egress chokepoint | `send_message_tool.py:142` | **WRONG (locus)** | `:142` = `SEND_MESSAGE_SCHEMA`. Real send = `_handle_send :298` → `_send_to_platform :723` → 9 platform senders. See P1 finding #3. |
| 8 | Creds resolved via `gateway/config.py` adapters | `send_message_tool.py:244` | **CONFIRMED** | `:244` = `from gateway.config import Platform, load_gateway_config`; creds also via `os.getenv` (`WEIXIN_TOKEN :356`, QQ `:1695`, etc.). |
| 9 | `approval.py` `_fire_approval_hook` | `:94/:102` | **CONFIRMED** | `:94` = `def _fire_approval_hook`; fires `pre_approval_request`/`post_approval_response`. |
| 10 | `DANGEROUS_PATTERNS` | `approval.py:546` | **CONFIRMED** | `DANGEROUS_PATTERNS = [` at `:546`. |
| 11 | gateway-context detection | `approval.py:178` | **CONFIRMED** | `_is_gateway_approval_context` at `:177`. |
| 12 | Only `git push --force` guarded today | `approval.py:713` | **CONFIRMED** | `:713-714` guard `--force`/`-f` only; plain `git push` is NOT guarded. Design's "insufficient" claim is correct. |
| 13 | `delegate_tool` uses `ThreadPoolExecutor`, in-process threads | `delegate_tool.py:28,:60` | **CONFIRMED** | `ThreadPoolExecutor` imported `:27-30`; comment "Subagents run inside a ThreadPoolExecutor worker" at `:60`. |
| 14 | `factory_health.py` section model | `:561` | **CONFIRMED (approx)** | `:565` wires `sections["workers"] = check_running_workers()`; `check_running_workers` def at `:198`. |
| 15 | exit-code map | `factory_health.py:357` | **CONFIRMED** | `compute_exit_code` at `:357`. |

**Egress paths the design does NOT route (the REQ-01 escalation):**

| Egress path | file:line | Reads creds from | Design routes it? |
|---|---|---|---|
| `hermes send` CLI subcommand | `hermes_cli/send_cmd.py:220` | `load_dotenv(~/.hermes/.env)` itself | **NO** → P0 #1 |
| MS Graph sendMail | `tools/microsoft_graph_auth.py:180` (`client.post`) | token from env (`from_env`) | **NO** → B10 |
| Discord API direct | `tools/discord_tool.py:40` (`discord.com/api/v10`) | bot token from env | **NO** → B11 |
| `git push` over HTTPS token | `terminal` + `GITHUB_TOKEN` (`skills_hub.py:336`, `tirith_security.py:286`) | env | Partially (B3 regex only) → B12 |
| 9 per-platform senders | `send_message_tool.py:1017..1754` | env/config | Via §1.3.1 re-route (if `_handle_send` is the real hook) |
| `process_registry`-spawned shells | `:774` (`start_new_session`) | inherit parent env | Implicit (cred-absence) |

> **ESCALATION (REQ-01 trigger met):** there ARE egress paths the design doesn't route
> through the broker. The thesis (cred-absence closes all of them) survives *only if*
> P1a-8 is scoped to every env source, which the design does not currently specify.

---

## REQ-02 — Bypass-impossibility attack table

Fresh-context gate = {direct send-CLI, token-read+curl, raw `git push`} all produce no effect.

| Vector | Design's claimed closure | Does it actually hold? | Severity |
|---|---|---|---|
| B1: call `send_message` tool | Re-routed to broker client; no adapter cred in assistant env | Holds **iff** `_handle_send` (not `:142`) is the rewrite point AND P1a-8 starves env. Locus imprecise. | P1 |
| B2: read `.env`/token file then curl | Creds physically removed; `*-token-read-deny` regex blocks read | **Partially aspirational.** Existing `token-read-deny` policies match ONLY `pm-os-foci-token` filenames (`execute-code-token-read-deny.yaml`), NOT `~/.hermes/.env` or generic token JSON. Regex is porous by inspection. Real closure = cred-absence = P1a-8. Design admits this. | P1 (belt is weaker than implied) |
| B3: raw `git push` via terminal | NEW `terminal-git-push-deny` policy + broker-only git creds | Regex `git ... push` catches the literal command, but `matches_trigger` requires exact `tool_name` equality (`__init__.py` Policy). Need policies for BOTH `terminal` AND `execute_code` AND any other shell-capable tool name; newline/`git -C`/alias evasion possible. Wall = cred-absence. | P1 |
| B4: `execute_code` imports adapter and sends | `execute-code-*` regex + cred-absence | `execute-code-email-block.yaml` regex catches `smtplib\|sendMail\|graph.microsoft` — but NOT a novel provider (e.g. a new Telegram bot POST, Twilio SMS, Slack webhook). Cred-absence is the only real wall. Design says exactly this. | P1 (as designed) |
| B5: subagent via `delegate_tool` sends | Subagent inherits cred-absence + same guards | **Stronger than design states**: `DELEGATE_BLOCKED_TOOLS` (`delegate_tool.py:45`) already strips `send_message` AND `execute_code` from subagents. BUT `terminal` is NOT in that set — a subagent can still `curl`/`hermes send`/`git push` via terminal. Same UID, same env ⇒ same cred-absence dependency. | P2 (note the residual terminal path) |
| B6/B7: edit plugin/skill/broker source to send | Write-block policy now; immutable ring P2.8 | Honestly flagged as RESIDUAL closed in P2.8. Acceptable for P1a as a *named* obligation. | P2 (accepted) |
| B8: point `broker_client` at a fake socket (self-approve) | Config value assistant can't rewrite once write-blocks hold; broker verifies 0600/own-UID | In P1a (before immutable ring) the client's socket path IS assistant-writable config. If the assistant can edit its own config, it can point at a fake broker and "approve" itself. Design rates Medium; agree — **this is a self-approval vector until P2.8**. | **P1** (self-approval risk, time-boxed) |
| B9: kill broker, fall back to direct | Fail-closed: no direct fallback; held actions persist | Sound *by construction* — but ONLY if every egress path (incl. B10/B11/B12/`hermes send`) has no cred to fall back to. If ANY key survives in env, "fail-closed" is actually "fail-open via the other path." | **P0** (couples to #2) |
| B10: MS Graph sendMail tool | *(not in design)* | Open unless P1a-8 removes MS token from env. Not enumerated. | P1 |
| B11: Discord API tool | *(not in design)* | Open unless Discord token removed from env. Not enumerated. | P1 |
| B12: `git push https://$GITHUB_TOKEN@host` | *(B3 regex only)* | `git ... push` regex may catch it, but closure must be token-absence, not regex. Not called out. | P1 |

**Overall REQ-02 verdict:** the *thesis* (cred-absence is the true wall; guards are loud
belt) is **sound and I could not falsify it**. What is NOT sound is the design's implicit
scoping: it treats `send_message` + `git push` as the egress universe. The fresh-context
gate can be passed by a naive implementation that re-routes the tool, moves the *named*
keys, and still leaves `hermes send` + MS/Discord/GitHub tokens live in the process env.
**The gate tests must exercise those paths or they are theater** (see REQ-04).

---

## REQ-03 — Durability / approval / safe-lane / fail-closed

| Area | Verdict | Failure scenario / note |
|---|---|---|
| **Held-action durability across restart** | **SOUND** | SQLite + WAL + `state` transitions is the right choice and matches control-room's proven in-tree pattern. `test_held_action_survives_restart` covers it. One gap: design says "half-written line discarded" for JSON-RPC framing but does NOT specify the enqueue is committed to SQLite *before* the RPC returns `held`. If the broker crashes after acking `held` but before `COMMIT`, the action is lost and the assistant believes it's queued. **Fix:** specify write-then-ack ordering (fsync/commit before returning `action_id`), and add `test_crash_between_ack_and_commit_loses_nothing`. |
| **ULID collision** | **SOUND** | ULID (48-bit ms time + 80-bit random) collision is negligible; lexicographic sort is a real property. No issue. |
| **ULID forgery / self-approval** | **PARTIAL RISK** | The ID scheme itself is fine, but *approval authority* is the weak point. `approve(action_id, approver)` takes `approver` as a **client-supplied string** ("yk"). Nothing in the design authenticates that the approve RPC actually came from Yu-Kuan's Telegram tap versus the assistant calling `broker_client.approve(id, approver="yk")` directly over the same UDS. **If the assistant can reach the socket to `enqueue`, it can also call `approve`.** This is a **self-approval hole**: the assistant enqueues a held action, then approves its own action. **Fix (P0-adjacent):** approvals must carry an unforgeable proof the assistant cannot mint — e.g., the approve RPC is accepted ONLY from the gateway's Telegram-callback code path bearing a broker-issued nonce tied to the specific `action_id` and delivered out-of-band (the Telegram button payload), never from a generic client `approve`. Without this, the safe-lane "hold" is decorative. |
| **5 conditions distinguish the plan's examples** | **SOUND (conservative)** | With C4 defaulting FALSE (tier-0), "email the board" fails C3(strategic)+C5(irreversible)+C4 → held; "got it to a known colleague" passes all five only if the colleague is on the P1a-seeded allow-list. The examples are distinguished. Caveat: correctness depends entirely on `allow-list.md`/`strategic-markers.md` seed content, which P1a-e owns — verify the seed at build time, not just the evaluator. |
| **Fail-closed is real (not fail-open)** | **CONDITIONAL** | Fail-closed holds for the `send_message` tool path. It does NOT hold globally while other egress paths retain live creds (couples to REQ-02 #2/B9/B10-12). "Fail-closed" is only true once cred-absence is complete across ALL paths. |

**REQ-03 escalations:** (1) **self-approval via direct `approve` RPC** — the assistant can
approve its own held actions unless approval authority is cryptographically separated from
the enqueue client. This meets the P0 bar ("the assistant can self-approve"). (2) commit-
before-ack ordering unspecified (held-action loss window).

---

## REQ-04 — Worker contract + RED test theater check

**Process-group kill:**
- `os.setsid` (preexec) + `os.killpg(os.getpgid(pid), SIGTERM→SIGKILL)` is **correct and
  already proven in-tree** (`process_registry.py:774/806`, `environments/local.py:1044/
  1050/1105`). The design's mechanism is sound.
- **Leak case (P2):** `killpg` kills the *process group*. A grandchild that calls `setsid`
  itself (or double-forks into a new session/pgid) escapes the group and is orphaned
  (reparented to launchd/init), surviving the kill. `claude -p`/`codex exec` don't normally
  do this, but a worker that launches a nested daemon (a dev server, a `caffeinate`, a
  background `npm`) can. **Fix:** `test_kill_terminates_children` must spawn a grandchild
  that *itself* calls `setsid` and assert detection (scan for orphaned descendants by pgid
  *and* by process tree), or document the escape as a known residual. As written, the test
  can pass against a compliant child while the real hang case (a detached grandchild)
  survives.

**Liveness / STALLED detection:**
- mtime-of-output-file + `os.kill(pid,0)` to separate STALLED from EXITED is a **genuine
  upgrade** over `delegate_tool`'s thread `is_alive()` and is not falsifiable as theater.
- **Fooled-by-noise case (P2):** a worker that emits keep-alive output (a spinner, a
  periodic log line) but makes no real progress advances mtime and reads as RUNNING
  forever. mtime detects "process is writing," not "process is progressing." The design
  should state this limit; the staleness window is a liveness heuristic, not a
  progress guarantee. Acceptable for P1a if named.

**RED tests real vs. theater:**

| Test | Real or theater? | Note |
|---|---|---|
| `test_direct_send_cli_fails_no_creds` | **Real IF** it invokes the actual `hermes send` subcommand / send path with env scrubbed, not a stub. As written ("call the send path") it's ambiguous — must hit `hermes_cli/send_cmd.py`, not a mock. | Strengthen to name the real entry point. |
| `test_token_file_read_finds_no_live_cred` | **Theater risk** — reading a *file* and finding nothing proves little; the creds live in `os.environ` (loaded by `env_loader`). Must assert a fresh assistant process's `os.environ` has no live send key. | Rewrite per P0 #2. |
| `test_raw_git_push_blocked` | Real if it drives the actual `terminal` tool through control-room, and also covers `execute_code` + HTTPS-token push. | Add the token-push variant. |
| `test_execute_code_import_adapter_fails` | Real (cred-absence assertion). Good. | — |
| `test_subagent_inherits_cred_absence` | Real, and should also assert the subagent's `terminal` path can't send (since `send_message`/`execute_code` are already stripped but `terminal` is not). | Add terminal variant. |
| `test_remote_stub_same_interface` | Real dispatch proof; good design. | — |
| `test_kill_terminates_children` | **Theater risk** unless it uses a `setsid`-escaping grandchild (above). | Strengthen. |
| `test_long_payload_not_truncated` (100KB) | Real, directly targets the `/approve-email` bug class. Good. | — |

**Missing gate test (add):** `test_assistant_cannot_self_approve` — the assistant client
calling `approve(id, approver="yk")` on its own enqueued action must be REJECTED (ties to
REQ-03 self-approval P0). Without this test the self-approval hole ships silently.

---

## Summary of required fixes before BUILD

**P0 (block a naive-but-gate-passing implementation):**
1. Route or refuse `hermes send` (`hermes_cli/send_cmd.py`); add to P1a-h scope + RED test.
2. Scope P1a-8 to ALL cred sources (`~/.hermes/.env`, managed `/etc/hermes/.env`,
   Bitwarden, `config.yaml` scalars) and assert the assistant *process env* is starved —
   rewrite `test_token_file_read_finds_no_live_cred` to check `os.environ`.
3. (REQ-03) Separate approval authority from the enqueue client so the assistant cannot
   call `approve` on its own action; add `test_assistant_cannot_self_approve`.

**P1:**
4. Correct the reuse-table egress locus (`_handle_send :298`, not `:142`) and add a full
   egress inventory (MS Graph, Discord, GitHub-token push) as bypass rows B10–B12, each
   closed by cred-absence.
5. State that `token-read-deny`/`email-block` regexes cover only named patterns (belt,
   not wall) and do not match `~/.hermes/.env` — so the design's B2/B4 "guard" language
   isn't over-read by a coder.
6. Specify commit-before-ack ordering for held-action enqueue (loss window).
7. Note B8 self-approval-via-fake-socket is open in P1a (config is assistant-writable
   until P2.8) — accepted residual, but name it.

**P2 (cosmetic / documentation):**
8. `killpg` grandchild-escape residual — strengthen `test_kill_terminates_children`.
9. mtime liveness ≠ progress — state the STALLED heuristic's limit.
10. Note subagent `terminal` remains an egress path (only `send_message`/`execute_code`
    are stripped by `DELEGATE_BLOCKED_TOOLS`).

---

## What the design got RIGHT (credit where due)

- The central thesis — **credential absence is the wall, guards are loud defense-in-depth**
  — is correct and unusually honest; I could not falsify it.
- Reuse decisions (native `pre_tool_call`, control-room policy engine, SQLite pattern) are
  accurate and avoid rebuilding the interceptor.
- Not conflating the async durable held-action surface with `approval.py`'s synchronous
  dangerous-shell gate is the right call and directly avoids repeating the `/approve-email`
  mistake.
- ULID-not-body-hash is the correct fix for the four-way `/approve-email` truncation bug.
- The process-group launcher + mtime liveness is a real, in-tree-proven upgrade over
  `delegate_tool`'s thread model.
- Residuals B6/B7/B8 are honestly flagged and handed to P2.8 as a named obligation.

The gap is scope, not concept: the design correctly identifies the ONE wall, then draws
the egress perimeter too small around it. Widen the perimeter (fixes 1, 2, 4) and separate
approval authority (fix 3), and this design is sound to build.
