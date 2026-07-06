# P1a Design — Send/Action Broker, Approval Surface, Safe-Lane, Worker Launcher

**Phase:** P1a (Broker + approval lanes + worker launcher) · **Mode:** backlog · **Branch:** `factory`
**Role:** Architect (design only — zero implementation code in scope)
**Plan:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P1a (tasks P1a-1..8), §8/BD1
**Remediation:** `docs/plans/diagnosis-2026-07-03/remediation-plan.md` §1.1–1.4

---

## Revision v2 — red-team closures

Adversarial review (`P1a-review.md`, verdict SHIP-WITH-FIXES, 2 P0 · 4 P1 · 3 P2) proved the
thesis sound but the **egress perimeter too small**. v2 widens it and closes all three P0 holes.
All review file:lines re-verified against `factory` HEAD before revising.

| Hole (review) | Closure in v2 | Where |
|---|---|---|
| **P0-1** `hermes send` CLI is an unrouted direct send (`send_cmd.py:220` loads `.env` + sends) | `send_cmd` refuses in assistant context OR routes via `broker_client.enqueue_action`; deny-policy belt; wall = EGRESS creds gone from `~/.hermes/.env` | §1.7 E2, §2 B10, P1a-h |
| **P0-2** cred-absence is FALSE today — `cli.py:177`/`run_agent.py:119` load `~/.hermes/.env` into `os.environ` override=True | EGRESS/MODEL credential **split**: EGRESS secrets relocated out of every source the assistant loads (`~/.hermes/.env`, managed `/etc/hermes/.env`, Bitwarden, config scalars) → broker-only; MODEL keys stay so the assistant can converse. Gate test asserts **`os.environ`** is starved, not a file read. | §1.6, P1a-h test #2 |
| **P0-3** self-approval — `approve(id, approver)` trusts a client string over the enqueue socket | **Separate approval authority**: broker mints a single-use nonce bound to the `action_id`, delivered out-of-band in the Telegram button; `approve` without a valid nonce is rejected; assistant cannot mint it. | §2.1, §2 B15, P1a-d test #6 |
| **P1-#3** wrong egress locus + missed paths | Locus corrected to `_handle_send:298`; full egress inventory (send-CLI, MS Graph, Discord, git-push-over-token) as B10–B13. | §0 table, §1.7, §2 |
| **P1-#5/#6/#7, P2 (8/9/10)** regex-is-belt-not-wall, commit-before-ack, fake-socket residual, killpg grandchild-escape, mtime≠progress, subagent `terminal` | Stated explicitly / added as tests. | §1.6, §2 B14, §5.2 |

**The crux — credential split (do NOT get this wrong):** the assistant KEEPS its
MODEL-inference key (`ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY`/`OPENAI_API_KEY`, `cli.py:3843`)
so the conversation loop can think; ONLY EGRESS/SEND/PUSH creds leave it. The gate proves both
halves: EGRESS creds absent (send fails) **and** the assistant still converses.

---

## 0. What P1a must achieve (the one sentence)

Make it **architecturally impossible for the assistant to send a message, hit a
third-party API, or `git push` without going through a separate process that holds the
*only* copy of the credentials.** Everything else in this doc (approval surface, safe
lane, worker launcher) hangs off that single enforcement boundary.

The boundary is **not** "a process boundary magically stops the assistant." On a single
Mac user account the assistant and broker share a UID; a process line alone is porous.
The real wall is **credential absence**: keys are physically removed from every location
the assistant can read (`.env`, shell env, token files, keychain items the assistant's
login context can unlock) and placed only where the broker can reach them. The process
boundary is what lets that separation exist; the guards (control-room `pre_tool_call`)
are defense-in-depth that fail *loud* if the assistant tries the obvious bypasses.

### Upstream reuse decisions (what already exists — do NOT rebuild)

| Capability | Native mechanism (verified) | P1a decision |
|---|---|---|
| Tool-call interception / block | `pre_tool_call` hook, `hermes_cli/plugins.py:2140`; protocol `{"action":"block","message":...}`; per-thread whitelist `:2161` | **Reuse** — the broker-bypass guards are new control-room policies on this hook, not new plumbing |
| Policy engine + audit store | control-room plugin: `pre_tool_call` at `plugins/control-room/__init__.py:699`, SQLite tables `audit_log`/`policy_log`/`workflow_state` (`:36/:48/:58`), `register()` `:990`, declarative YAML policies in `plugins/control-room/policies/` | **Extend** — add broker-bypass policies; the broker's OWN durable store is separate (see §1.4) |
| Dangerous-command approval + hooks | `tools/approval.py` — `DANGEROUS_PATTERNS` (`:546`), `pre_approval_request`/`post_approval_response` fired via `_fire_approval_hook` (`:94/:102`), gateway-context detection `:178` | **Do NOT reuse as the held-action surface.** This is a *synchronous, in-process* approve-this-shell-command gate. The broker's held-action surface (§2) is async, durable, cross-restart. They are different problems; conflating them repeats the `/approve-email` mistake. Keep `approval.py` for its existing dangerous-shell role only. |
| Egress chokepoint (messages) | `send_message` tool: real send is `_handle_send` (`tools/send_message_tool.py:298`) → `_send_to_platform` (`:723`) → 9 platform senders; creds via `gateway/config.py` adapters (`:244`) + `os.getenv` (`WEIXIN_TOKEN :356`, etc.). **NOT `:142`** — that is `SEND_MESSAGE_SCHEMA` (corrected per review P1-#3, verified). | **Re-route at `_handle_send`** — `send_message` becomes a thin broker client; the adapter+credential path moves into the broker process |
| Worker helper (to be superseded) | `tools/delegate_tool.py` — `ThreadPoolExecutor`, in-process threads (`:28,:60`) | **Do NOT extend for the launcher.** Reuse only its orchestrator-side result-collection shape as reference; the process launcher is new code (§4) |
| Health signal (P0.8) | `scripts/factory_health.py` — section model (`sections["workers"]` etc., `:561`), exit-code map `:357` | **Extend** — add a `broker` section (§1.5) |
| Git-push guard (today) | `tools/approval.py:713` guards only `git push --force` | **Insufficient** — P1a adds a full `git push` broker-routing guard (§2 bypass table) |

> **Escalation surfaced (REQ-01):** the native `pre_tool_call` hook + control-room policy
> engine already implement *tool interception and a durable decision store*. P1a **reuses
> both** rather than inventing a parallel interceptor. What is genuinely new: the
> credential-holding broker *process*, its own held-action store, the safe-lane evaluator,
> and the worker launcher. Said plainly so no coder rebuilds the interceptor.

---

## REQ-01 — Broker process architecture

### 1.1 Component diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│  ASSISTANT / GATEWAY process  (NO send/API/push credentials present) │
│                                                                       │
│   send_message tool ─┐                                                │
│   git-push wrapper  ─┤                                                │
│   pm_os-write tool  ─┼──► broker_client.py  ──JSON-RPC over UDS──┐    │
│   merge-approve tool ┘      (thin stub: frames request, no creds) │    │
│                                                                    │    │
│   control-room pre_tool_call policies  ◄─── defense-in-depth       │    │
│     (block direct send / token-read / raw git push)                │    │
└────────────────────────────────────────────────────────────────────┼───┘
                                                                       │
        unix domain socket  ~/.hermes/broker/broker.sock  (0600)      │
                     JSON-RPC 2.0, newline-framed                      │
                                                                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│  BROKER process  (separate PID; own launchd service; HOLDS creds)    │
│                                                                       │
│   jsonrpc_server ─► dispatch ─► [ enqueue_action ]                    │
│                                 [ approve / reject ]                  │
│                                 [ list_pending ]                      │
│                                 [ send / git_push ]  (real egress)    │
│                                 [ health ]                            │
│                                     │            │                    │
│                     safe_lane.evaluate()    held_store (SQLite)       │
│                                     │            │                    │
│         creds: keychain + broker-only env ──► gateway adapters,       │
│                                                git, MS Graph, OpenRouter│
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 JSON-RPC method set

Transport = **unix domain socket + JSON-RPC 2.0** (BD1, RESOLVED — plan §8:548). Socket at
`~/.hermes/broker/broker.sock`, mode `0600`, owner-only. Newline-delimited JSON frames
(one request/response per line — simplest framing that survives a crash mid-write: a
half-written line is discarded, not misparsed). No network listener ever.

| Method | Params | Returns | Notes |
|---|---|---|---|
| `enqueue_action` | `{type, channel?, recipient?, summary, payload, origin}` | `{action_id, disposition}` | `disposition` ∈ `auto_sent` \| `held`. Safe-lane runs here. |
| `list_pending` | `{}` | `[{action_id, type, summary, created_ts, safe_lane_failed}]` | For the approval surface; never truncates payload (summary only in list). |
| `approve` | `{action_id, nonce}` | `{status, result}` | **`nonce` = broker-minted, single-use, action-bound (§2.1).** Executes the real egress; idempotent by `action_id`; REJECTS any call without a valid nonce (blocks assistant self-approve). No trust-me `approver` string. |
| `reject` | `{action_id, approver, reason?}` | `{status}` | Marks rejected; durable. |
| `send` | `{channel, recipient, payload}` | `{status}` | **Internal** — only the broker's own executor calls this after safe-lane/approval; not exposed to assistant client directly (assistant uses `enqueue_action`). |
| `git_push` | `{repo, remote, ref, force:false}` | `{status}` \| held | Same enqueue→safe-lane→hold/execute path as `send`. |
| `health` | `{}` | `{status, pending_count, last_send_ts, creds_present:[...names], uptime_s}` | Feeds P0.8 (§1.5). |

**Why the assistant only ever calls `enqueue_action`** (not `send`/`git_push` directly):
one entry point = one policy funnel. `send`/`git_push` are the broker's *internal*
executor verbs; exposing them to the client would create a second code path that skips
the safe lane. The client stub (`broker_client.py`) exposes only `enqueue_action`,
`list_pending`, `approve`, `reject`, `health`.

### 1.3 How egress is *forced* through the broker (integration points, file:line)

1. **`tools/send_message_tool.py` `_handle_send` (`:298`)** — the send body (NOT the
   `:142` schema) is rewritten to frame an `enqueue_action{type:"message", channel,
   recipient, payload}` to the broker client instead of reaching `_send_to_platform`
   (`:723`) / the 9 per-platform senders directly. The **adapter + credential resolution
   moves into the broker process** (new `broker/executors/message_executor.py`).
   After P1a-8 the assistant-side tool has no token to fall back to.
   *The complete egress surface (send-CLI, MS Graph, Discord, git push over token) is
   enumerated in §1.7 and dispositioned in the §2 bypass table (B10–B13).*
2. **New `git push` routing** — today only `tools/approval.py:713` guards force-push.
   P1a adds a control-room policy (`plugins/control-room/policies/terminal-git-push-deny.yaml`,
   NEW) that **blocks any `terminal`/`execute_code` invocation whose command matches
   `git ... push`** unless it carries the broker-issued one-shot token (which the assistant
   never holds). The sanctioned push path is `enqueue_action{type:"git_push"}`.
3. **control-room `pre_tool_call` (`plugins/control-room/__init__.py:699`)** — extended
   with the bypass policies in §2. This is the *guard* layer; the *wall* is credential
   absence (P1a-8).
4. **Broker health → P0.8** — `scripts/factory_health.py` gains a `broker` section
   (§1.5) calling `health`.

### 1.4 Durable held-action store — choice: **SQLite**

**Decision: SQLite** (`~/.hermes/broker/held_actions.db`), not an append-log.

Justification (falsifiable): the store must support (a) *lookup by stable action_id*,
(b) *state transitions* held→approved/rejected/executed with atomicity across a broker
restart, (c) *idempotent execute* (approve the same id twice ⇒ one send), (d) *no
payload truncation* for long bodies. SQLite gives ACID transitions + indexed id lookup +
`TEXT`/`BLOB` payload columns with no length cap, in one file, with WAL for crash safety.
An append-log would force a full replay to reconstruct current state and makes idempotent
execute awkward (must scan for a prior "executed" record). control-room already uses
SQLite (`plugins/control-room/__init__.py:81`), so the pattern and dependency are proven
in-tree.

**This DB is the broker's OWN store — separate from control-room's `audit`/`policy`
tables.** Rationale: the broker survives independently of the gateway; coupling its
durable queue to a plugin DB inside the gateway process would make the broker's
fail-closed guarantee depend on gateway liveness. control-room's audit rows still record
*that* an egress was attempted (observability); the broker's DB is the *authoritative*
held-action record.

Schema (one table + indexes):

```
held_actions(
  action_id     TEXT PRIMARY KEY,   -- stable, opaque, ULID (sortable by creation)
  type          TEXT NOT NULL,      -- message | git_push | pm_os_write | merge
  channel       TEXT,               -- e.g. telegram; NULL for git_push
  recipient     TEXT,
  summary       TEXT NOT NULL,      -- short human line for Telegram (never the payload)
  payload       TEXT NOT NULL,      -- FULL body/diff — NO truncation, ever
  origin        TEXT NOT NULL,      -- which tool/worker enqueued it (audit trail)
  safe_lane_json TEXT NOT NULL,     -- the 5 condition results (why held / why auto)
  state         TEXT NOT NULL,      -- held | approved | rejected | executed | failed
  created_ts    INTEGER NOT NULL,
  decided_ts    INTEGER,
  decided_by    TEXT,
  result_json   TEXT                -- execution outcome, for idempotency + audit
);
CREATE INDEX idx_held_state ON held_actions(state);
```

### 1.5 P0.8 health integration

New `check_broker()` in `scripts/factory_health.py` (mirrors `check_running_workers`
`:198`): connect to `broker.sock`, call `health`, return
`{status, pending_count, last_send_ts, creds_present, socket_reachable}`.
`compute_exit_code` (`:357`) gains: broker unreachable *and* gateway up ⇒ HERMES cause
(exit 2, the broker crashed — actionable bug); broker cleanly down with gateway down ⇒
not a broker-specific error. A stuck-pending count is surfaced but not itself an error
(a human simply hasn't approved yet).

### 1.6 Credential inventory + the starving mechanism (REQ-02 / review P0-2)

The wall is credential absence — but the review proved it is **false today**: `cli.py:177`
and `run_agent.py:119` both call `hermes_cli.env_loader.load_hermes_dotenv`, which loads
`~/.hermes/.env` into `os.environ` with `override=True` (`env_loader.py:238`), then
`_apply_external_secret_sources` pulls Bitwarden (`:245`) and `_apply_managed_env` re-applies
a managed `.env` last with override (`:246,:251`). `env_loader.py`'s own docstring admits it
"does NOT prevent the agent from later mutating `os.environ`" (`:261`). So **every assistant
process re-hydrates every send/push key at startup.** Removing keys from the broker's launchd
env is necessary but *not sufficient* — the keys must leave the *sources the assistant loads*.

**The split (the crux):** two disjoint credential classes.

- **MODEL-INFERENCE creds** — `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY`
  (assistant conversation loop needs these to think; verified as the resolution keys at
  `cli.py:3843/3845`). **These STAY in the assistant env.** Removing them breaks the product.
- **EGRESS/SEND creds** — every recipient-facing send/push key. **These are REMOVED from every
  source the assistant loads, and held ONLY by the broker.**

**Credential inventory** (source | secret | class | who-holds-it after P1a-8):

| Source | Secret(s) | Class | Who holds it |
|---|---|---|---|
| `~/.hermes/.env` | `TELEGRAM_BOT_TOKEN`, `WEIXIN_TOKEN` (`send_message_tool.py:356`), `DISCORD_BOT_TOKEN` (`discord_tool.py:55`), QQ/Signal/BlueBubbles send tokens, MS Graph client secret (`microsoft_graph_auth.py:from_env`), `GITHUB_TOKEN`/`GH_TOKEN` (`skills_hub.py:336`) | **EGRESS** | broker-only env (moved out of `~/.hermes/.env`) |
| `~/.hermes/.env` | `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | **MODEL** | assistant env (unchanged) |
| managed `/etc/hermes/.env` (`env_loader.py:251` `_apply_managed_env`, override=True) | any EGRESS key pinned here | **EGRESS** | must be moved to broker-managed scope; managed override is the sneakiest re-hydration path |
| Bitwarden (`env_loader.py:245` `_apply_external_secret_sources`) | any EGRESS key sourced from BW | **EGRESS** | broker fetches from BW; the assistant's BW-source map (`env_loader.py:24`) must exclude egress keys |
| `~/.hermes/config.yaml` bridged scalars (`send_cmd.py:225` step 2; `gateway/run.py` bridge) | `TELEGRAM_HOME_CHANNEL` etc. (routing, not secrets) + any secret scalar | **EGRESS if secret** | secret scalars must not bridge into assistant env; routing scalars (channel IDs) may stay (not a send credential) |
| macOS keychain / broker launchd env | egress creds | **EGRESS** | broker only (UNVERIFIED: keychain-unlock semantics under broker launchd session — validate empirically in P1a-8, §constraint notes) |

**Starving mechanism** (what P1a-8 actually does): a broker-provisioning step relocates every
EGRESS secret out of `~/.hermes/.env`, out of the managed `/etc/hermes/.env`, and out of the
assistant's Bitwarden source map — into a broker-only secret store (`broker/credentials.py`,
keychain + broker launchd env, mode 0600, unreadable by the assistant's login context). The
assistant's `env_loader` load path then re-hydrates **only** the MODEL class. **Assertion the
gate test enforces:** in a fresh assistant/gateway process, `os.environ` contains a live MODEL
key AND contains **no** live EGRESS secret — not merely that a *file read* found nothing (the
review's theater objection to the old `test_token_file_read_finds_no_live_cred`). See revised
P1a-h test #2.

### 1.7 Full egress inventory (REQ-01 / review P1-#3)

Every path that sends/posts/pushes, each mapped to its closure. Route-through-broker where a
tool is the sanctioned interface; otherwise cred-absence (P1a-8) is the wall and a control-room
guard is the loud belt.

| # | Egress path | file:line (verified) | Reads creds from | Disposition | Owning task |
|---|---|---|---|---|---|
| E1 | `send_message` 9 platform senders | `send_message_tool.py:298`→`:723`→`:1017..1754` | env/config adapters | **Re-route** at `_handle_send` → broker | P1a-h |
| E2 | `hermes send` CLI subcommand | `hermes_cli/send_cmd.py:220` (`_load_hermes_env` → `load_dotenv` override + config bridge) | `~/.hermes/.env` itself | **Refuse-or-route** (§2 B10; refuse in assistant context, else route via `broker_client.enqueue_action`) | P1a-h |
| E3 | MS Graph `sendMail` | `microsoft_graph_auth.py:180` (`client.post`), creds `from_env` (`:128`) | env | **Cred-absence** (MS token → broker); belt: `execute-code-email-block` regex | P1a-h |
| E4 | Discord API direct | `discord_tool.py:40` (`discord.com/api/v10`), token `os.getenv("DISCORD_BOT_TOKEN")` (`:55`) | env | **Cred-absence** (Discord token → broker) | P1a-h |
| E5 | `git push` over HTTPS token | `terminal`/`execute_code` + `GITHUB_TOKEN`/`GH_TOKEN` (`skills_hub.py:336`) | env | **Cred-absence** (token → broker) + `terminal-git-push-deny` policy belt | P1a-h |
| E6 | `git push` (force) — existing guard | `approval.py:713` (force only) | env | Superseded by E5 broker routing; force still guarded | P1a-h |

**Escalation (REQ-01):** all six close inside P1a via cred-absence + re-route. No path defers to
a later phase. The residual that defers is *immutability of the broker source/policy* (B6/B7/B8),
which the write-block policy covers as belt and the P2.8 immutable ring closes fully — unchanged
from v1.

### Files to CREATE (REQ-01)

| File | Role |
|---|---|
| `broker/__init__.py` | package marker + version |
| `broker/server.py` | UDS listener + JSON-RPC dispatch loop; fail-closed lifecycle |
| `broker/jsonrpc.py` | newline-framed JSON-RPC 2.0 encode/decode (no 3rd-party dep) |
| `broker/held_store.py` | SQLite held-action store (schema §1.4, atomic transitions, idempotent execute) |
| `broker/executors/message_executor.py` | real message send (owns gateway adapter + creds) |
| `broker/executors/git_push_executor.py` | real `git push` (owns git remote creds) |
| `broker/credentials.py` | keychain/broker-env credential resolution (broker-only) |
| `broker_client.py` (repo root, assistant-side) | thin stub: frame `enqueue_action`/`list_pending`/`approve`/`reject`/`health`; NO creds |
| `broker/launchd/com.hermes.broker.plist` | own launchd service (separate PID from gateway) |
| `plugins/control-room/policies/terminal-git-push-deny.yaml` | NEW guard: block raw `git push` outside broker |

### Files to MODIFY (REQ-01)

| File:line | Change |
|---|---|
| `tools/send_message_tool.py` `_handle_send` (`:298`) | body re-routes to `broker_client.enqueue_action`; strip adapter/cred resolution (NOT `:142` schema) |
| `hermes_cli/send_cmd.py:220` (`_load_hermes_env`) | refuse in assistant context OR re-route to `broker_client.enqueue_action`; drop the direct `load_dotenv`+send (review P0-1) |
| `hermes_cli/env_loader.py` | provisioning: relocate EGRESS secrets out of the assistant load path; leave MODEL keys (§1.6) — implemented in P1a-h |
| `scripts/factory_health.py:561`/`:357` | add `broker` section + exit-code rule |

---

## REQ-02 — Bypass-impossibility (the core objective)

Enumerate **every** way the assistant could send/push without the broker, and for each,
the closure. Fresh-context gate (plan §P1a exit gate): direct send-CLI + token-file read
+ raw `git push` must **all** fail to produce an effect.

| # | Bypass vector | Closure mechanism | Task | Residual risk |
|---|---|---|---|---|
| B1 | Call `send_message` tool directly | Tool re-routed to broker client (§1.3.1); no adapter creds in assistant env | P1a-2, P1a-8 | Low — tool has literally no token to send with |
| B2 | Read a token file (`.env`, `~/.hermes/.env`, token JSON) then `curl`/SMTP/Graph directly | **Creds physically removed** from all assistant-readable paths → keychain/broker-env (P1a-8); control-room `*-token-read-deny` policies block the read attempt (defense-in-depth) | P1a-8 | Low — even a successful file read finds no live credential |
| B3 | Raw `git push` via `terminal`/`execute_code` | NEW `terminal-git-push-deny` policy blocks the command (§1.3.2); git remote push creds live only in broker | P1a-2, P1a-8 | Low — sanctioned push is `enqueue_action{git_push}` |
| B4 | `execute_code` writing Python that imports the gateway adapter and sends | control-room `execute-code-*-block` policies (existing pattern) extended; adapter needs a credential the process env lacks | P1a-8 | Medium — a novel import path a policy regex misses; **mitigated by B2's cred absence** (the true wall). Regex is belt; cred-absence is suspenders. |
| B5 | Spawn a subagent (`delegate_tool`) that sends | Subagents inherit the same process env → same cred absence; subagent tool calls pass through the same `pre_tool_call` guards | P1a-8 | Low — subagent is same UID, same empty env |
| B6 | Write a plugin / edit a skill that sends | Broker source + policy files are in the **immutable ring (P2.8)**; assistant cannot edit them. In P1a (before the ring lands) this is the **one open residual** — see below | P1a-2 (design), P2.8 (closure) | **RESIDUAL — closed in P2.8.** In P1a, a control-room policy blocks writes to `broker/**` and `plugins/control-room/policies/**`; hardening to an OS-level immutable ring is P2.8. |
| B7 | Edit the broker source or safe-lane policy to weaken it, then use the broker | Same as B6 — write-block on `broker/**` and policy dirs now; immutable ring in P2.8 | P1a-2, P2.8 | **RESIDUAL — P2.8.** Noted per constraint: "the design must not let a worker/assistant edit broker source or its policy files." |
| B8 | Point `broker_client` at a *fake* socket the assistant controls (self-approve) | Client socket path is a broker-env/config value the assistant can't rewrite once B6/B7 write-blocks hold; broker verifies socket ownership (0600, own UID) | P1a-2 | Medium in P1a, Low after P2.8 |
| B9 | Kill the broker, then fall back to a direct path | **Fail-closed (P1a-3):** there IS no direct fallback path — with the broker down, `send_message`/git-push simply error; held actions persist in SQLite and resume on restart. **Sound ONLY if every path (B10–B13) is cred-starved — else "fail-closed" is "fail-open via the other path" (review B9→P0).** | P1a-3, P1a-h | Low *iff* P1a-8 starves all sources (§1.6) |
| B10 | `hermes send <platform> "..."` CLI via `terminal` (review P0-1) | `send_cmd.py` **refuses in assistant context OR routes** to `broker_client.enqueue_action`; belt: control-room deny policy for the literal `hermes send` command on `terminal`/`execute_code`; wall: EGRESS creds absent from `~/.hermes/.env` so the CLI's own `load_dotenv` finds nothing live | P1a-h | Low — no live token even if the deny regex is evaded |
| B11 | MS Graph `sendMail` tool (review P1) | **Cred-absence** (MS Graph secret → broker, §1.6); belt: existing `execute-code-email-block` regex | P1a-h | Low — wall is token-absence, not regex |
| B12 | Discord API tool direct POST (review P1) | **Cred-absence** (`DISCORD_BOT_TOKEN` → broker, §1.6) | P1a-h | Low — no live Discord token in assistant env |
| B13 | `git push https://$GITHUB_TOKEN@host` (review P1) | **Cred-absence** (`GITHUB_TOKEN`/`GH_TOKEN` → broker); `terminal-git-push-deny` policy is belt only — closure is token-absence, never the regex | P1a-h | Low — no live push token; sanctioned push is `enqueue_action{git_push}` |
| B14 | Subagent uses `terminal` to `curl`/`hermes send`/`git push` (review P2) | `DELEGATE_BLOCKED_TOOLS` (`delegate_tool.py:45`) strips `send_message`+`execute_code` but **NOT `terminal`** — verified. Same UID, same starved env ⇒ same cred-absence dependency. Residual P2: add `terminal` to the block set (out of P1a file-disjoint scope; noted for P1c). | P1a-8 (wall), P1c (block-set) | Low via cred-absence; terminal-strip deferred |
| B15 | Assistant calls `broker_client.approve(id, approver="yk")` over the enqueue socket to self-approve (review P0-3) | **Approval-authority split (§2.1):** `approve` requires a broker-issued single-use nonce bound to the specific `action_id`, minted only when the broker emits the Telegram button and delivered out-of-band in the button payload. A generic enqueue-socket `approve` WITHOUT the nonce is rejected. The assistant cannot mint the nonce. | P1a-d | Low — self-approve provably rejected (test §5.2) |

**Fresh-context gate mapping (plan §P1a exit gate):**
- *direct send-CLI* → B1/B4, closed by P1a-2 re-route + P1a-8 cred absence.
- *token-file read* → B2, closed by P1a-8 (no live cred on disk).
- *raw git push* → B3, closed by the new deny policy + P1a-8.

**Honest statement of the wall (remediation §1.1):** B2 and B4 prove the point — the
*only* durable closure is credential absence (P1a-8). The `pre_tool_call` guards raise the
cost and make bypass attempts *loud in the audit log*, but a same-UID process can always
find a new code path a regex didn't anticipate. Therefore **P1a-8 is the load-bearing
task; the guards are defense-in-depth.** Any review that treats the guards as the primary
defense has repeated the fork's mistake.

**Residuals escalated (per REQ-02 instruction):** B6, B7, B8 cannot be *fully* closed in
P1a because the immutable ring is P2.8. P1a lands the write-block *policy* (belt); the OS
immutable ring (suspenders) closes them. This is called out so P2.8 inherits a named
obligation, not a surprise. B14 (subagent `terminal`) is closed by cred-absence in P1a with
the block-set hardening deferred to P1c (out of P1a's file-disjoint scope).

### 2.1 Approval authority — separating "who may approve" from "who may enqueue" (review P0-3)

**The hole:** in v1, `approve(action_id, approver)` took `approver` as a client-supplied
string over the **same** UDS the assistant uses to `enqueue_action`. If the assistant can reach
the socket to enqueue, it can also call `approve(id, approver="yk")` on its own held action —
the "hold" is decorative. Verified: nothing in v1 authenticated that the approve RPC came from
Yu-Kuan's Telegram tap versus the assistant.

**The closure — broker-issued approval nonce bound to the action_id and the Telegram button:**

```
1. Broker holds action A (id=ULID).  Broker MINTS approval_nonce_A =
   HMAC(broker_secret, action_id ∥ created_ts ∥ random) — a single-use, opaque token.
   broker_secret lives ONLY in the broker (never on the socket, never in any client).
2. Broker delivers the Telegram button with callback payload = {action_id, approval_nonce_A}.
   This delivery is broker→Telegram (principal-directed notify, exempt from safe-lane per §3.2).
   The nonce travels OUT-OF-BAND — down the broker's own Telegram send path, never returned
   to any enqueue-socket client.
3. Yu-Kuan taps Approve → gateway Telegram-callback handler receives {action_id, nonce} from
   the button → calls broker.approve(action_id, nonce).
4. Broker validates: nonce matches the one minted for THIS action_id, is unused, unexpired →
   execute egress, burn the nonce (single-use ⇒ replay-safe, idempotent).
5. Any approve() arriving WITHOUT a valid nonce (e.g. the assistant calling
   broker_client.approve(id, approver="yk")) is REJECTED. The assistant never sees the nonce
   (it went to Telegram, not back over the enqueue socket), and cannot mint one (no broker_secret).
```

**Why this provably blocks self-approval:** the only way to obtain a valid nonce is to receive
the Telegram button payload, which requires being Yu-Kuan's Telegram client — an out-of-band
authority the assistant has no access to. The enqueue socket carries no nonce and no way to
derive one. `broker_client` (assistant-side) exposes `approve(action_id, nonce)` but the
assistant has no nonce to pass; the Telegram-callback path in the gateway is the only caller
that ever holds one. This is a **separate approval authority**, not a stronger string check.

**File impact:** `broker/approval.py` mints/validates/burns the nonce; the RPC signature becomes
`approve{action_id, nonce}` (drops the trust-me `approver` string); `gateway/broker_approval_buttons.py`
carries `{action_id, nonce}` in the inline-button callback data. `broker/credentials.py` holds
`broker_secret`. Gate test: `test_assistant_cannot_self_approve` (§5.2 P1a-d).

---

## REQ-03 — Safe-lane 5-condition policy + generic approval surface

### 3.1 The five conditions (from remediation §1.2 / plan 1a.3), each falsifiable

The broker auto-sends **iff ALL FIVE** hold; any failure ⇒ hold. Each condition is a
predicate the safe-lane evaluator computes over the `enqueue_action` params + policy
files (P1b will make several data-driven; P1a ships them **conservative — near-empty
allow, so most things hold**, per "start conservative, widen as trust builds").

| # | Condition | Falsifiable test (auto-send requires TRUE) | Fails ⇒ hold when |
|---|---|---|---|
| C1 | **Signal understood** | The action `type` ∈ known set AND payload parses (non-empty, well-formed for its channel) | payload is malformed, empty, or type unknown |
| C2 | **Source of truth known** | `recipient` ∈ recipient allow-list (`allow-list.md`, P1a-seeded) | recipient not on allow-list (first-contact / unknown) |
| C3 | **Operational not strategic** | payload matches no `strategic` marker (board/exec/legal/pricing keywords in `strategic-markers.md`) AND length ≤ operational cap | strategic marker hit OR payload exceeds operational size |
| C4 | **Authority clear** | origin is an approved-channel principal-directed flow OR a task whose autonomy tier permits it (P1a: tier-0 = none auto ⇒ this fails by default, conservative) | authority ambiguous; **P1a default: FALSE** (near-empty safe lane) |
| C5 | **Mistake recoverable** | action type is reversible (retractable message / non-force push to a personal repo) AND not on `irreversible-actions.md` | irreversible (external first-contact email, force-push, prod deploy) |

**Conservative launch:** with C4 defaulting FALSE for anything but an explicitly-allowed
operational reply to a known recipient, the P1a safe lane auto-sends *almost nothing* —
exactly the intended "start empty, widen later" posture. Positive example (all 5 hold):
"reply 'got it, will do' to a known colleague's scheduling confirmation." Negative
(fails C3+C5): "email the board a status update" — held, always.

**Where the 5 live:** `safe-lane.md` (human-readable spec, prose) + machine predicates in
`broker/safe_lane.py`. The `.md` is the single-owner policy file; the `.py` mirrors it.
Both are in the write-blocked set (B7).

### 3.2 Generic held-action approval surface (P1a-4) — replaces `/approve-email`

**Action-ID scheme:** ULID (Crockford base32, 26 chars). Stable, opaque,
lexicographically sortable by creation time, collision-free without coordination. The ID
is the *only* key — no hashing of message bodies (the exact bug that broke `/approve-email`
four ways per plan 1a.2).

**Data model** (the held-action record = §1.4 `held_actions` row). The Telegram surface
carries **only** `action_id + summary + buttons`; the authoritative full payload lives in
the broker store keyed by `action_id`. **Long payloads never truncate** — the summary is
a separate short field; the payload column is unbounded `TEXT`. A "show full" affordance
re-fetches the payload by id.

**Approve/reject flow from Telegram:**
```
broker holds action  ──►  notify Yu-Kuan on Telegram (principal-directed, NOT third-party
                          send → exempt from safe-lane, direct delivery per remediation §2)
                          message = summary + [Approve ✓] [Reject ✗] carrying
                          {action_id, approval_nonce} (nonce minted per §2.1)
Yu-Kuan taps Approve ──►  gateway Telegram-callback → broker.approve(action_id, nonce)
                     ──►  broker: validate+burn nonce → load row by id → execute real egress
                          → state=executed → idempotent (second tap = no-op, cached result)
                          (an assistant approve() WITHOUT the nonce is rejected — §2.1)
Yu-Kuan taps Reject  ──►  gateway callback → broker.reject(action_id, nonce) → state=rejected
```
The approval surface is **generic** — `{type, summary, payload}` — so message-sends,
pm_os writes, git pushes, and P2 merge approvals all ride the identical path. No
per-channel hash surgery ever again.

### Files to CREATE (REQ-03)

| File | Role |
|---|---|
| `broker/safe_lane.py` | 5-condition evaluator (predicates C1–C5) |
| `broker/action_id.py` | ULID generation |
| `broker/approval.py` | approve/reject state machine over `held_store` (idempotent) + approval-nonce mint/validate/burn (§2.1) |
| `broker/policy/safe-lane.md` | prose spec of the 5 conditions (single-owner) |
| `broker/policy/allow-list.md` | recipient allow-list (C2), P1a-seeded |
| `broker/policy/strategic-markers.md` | C3 strategic keywords |
| `broker/policy/irreversible-actions.md` | C5 irreversible list |
| `gateway/broker_approval_buttons.py` | Telegram inline-button → `approve`/`reject` wiring |

---

## REQ-04 — Worker contract (P1a-6/7)

### 4.1 The `Worker` interface (4 operations)

`worker/base.py` — abstract base, four methods (design signatures; not implementation):

```
class Worker(ABC):
    def launch(self, spec: WorkerSpec) -> WorkerHandle: ...
        # start the job; return a handle carrying pid/pgid, worktree, output_path
    def check(self, handle: WorkerHandle) -> WorkerStatus: ...
        # liveness by OUTPUT-FILE MTIME, not just proc.is_alive():
        #   RUNNING (mtime advanced within window) | STALLED (alive but mtime frozen)
        #   | EXITED (rc) | DEAD
    def kill(self, handle: WorkerHandle) -> None: ...
        # SIGTERM to the whole PROCESS GROUP (kills children), SIGKILL escalation on timeout
    def collect_result(self, handle: WorkerHandle) -> WorkerResult: ...
        # read the output artifact + return code; never blocks forever (bounded)
```

`WorkerSpec` = `{cmd, cwd/worktree, output_path, env, timeout_s}`.
`WorkerHandle` = `{worker_id, pid, pgid, output_path, worktree}`.

### 4.2 `local-subprocess` implementation — process-group mechanism

`worker/local_subprocess.py`:
- **`launch`**: `subprocess.Popen(cmd, preexec_fn=os.setsid, ...)` — `setsid` makes the
  child a **process-group leader** (new session, `pgid == pid`). Every grandchild
  (`claude -p`, `codex exec` and *their* children) inherits the pgid. stdout/stderr
  redirect to `output_path` (the mtime liveness anchor).
- **`check`**: liveness = **output-file mtime** advanced within a staleness window
  (`os.stat(output_path).st_mtime`), *combined with* `os.kill(pid, 0)` to distinguish
  STALLED (alive, output frozen — the hang case `delegate_tool`'s thread model can't
  detect) from EXITED. This is the key upgrade over "is the thread alive."
- **`kill`**: `os.killpg(os.getpgid(pid), signal.SIGTERM)` — SIGTERM to the **process
  group** so all children die together; after a grace window, `os.killpg(..., SIGKILL)`.
  This is what `delegate_tool`'s `ThreadPoolExecutor` (`tools/delegate_tool.py:28`)
  fundamentally cannot do — you can't kill a runaway subprocess-of-a-thread cleanly.
- **`collect_result`**: read `output_path` + reap `Popen.returncode`; bounded wait.

### 4.3 `remote-worker` STUB (P1a-7)

`worker/remote_stub.py` — implements the **exact same `Worker` ABC**, methods raise
`NotImplementedError("remote worker ships post-P1a")` OR return a canned
`WorkerStatus.EXITED`. Its purpose is a **compile/dispatch proof**: the fleet supervisor
(P2/P3) dispatches to `remote_stub` through the *identical* code path it uses for
`local_subprocess`, proving no local-only assumption (no bare `os.getpgid`, no local-path
coupling) leaked into the supervisor. "Design-now, implement-one."

### Files to CREATE (REQ-04)

| File | Role |
|---|---|
| `worker/__init__.py` | package marker |
| `worker/base.py` | `Worker` ABC + `WorkerSpec`/`WorkerHandle`/`WorkerStatus`/`WorkerResult` |
| `worker/local_subprocess.py` | process-group subprocess impl (§4.2) |
| `worker/remote_stub.py` | same-interface stub (§4.3) |

**Reuse note:** `delegate_tool.py`'s orchestrator-side result-collection *shape* is
reference-only; the launcher is new process code, per plan 1a.4 ("new code, not a wrapper
over the in-process helper").

---

## REQ-05 — Acceptance-test plan + file-disjoint task breakdown

### 5.1 Task breakdown (P1a-a..h → 8 plan tasks), file-disjoint

Ownership rule: **no two tasks write the same file.** Shared files (`send_message_tool.py`,
`factory_health.py`) are each owned by exactly one task.

| Task | Plan task | Owns (writes) | Depends on |
|---|---|---|---|
| **P1a-a** | P1a-1 | `docs/plans/harness/fable/p1a/IPC-decision.md` (record: UDS+JSON-RPC, rejects file-queue/HTTP) | — |
| **P1a-b** | P1a-2 | `broker/__init__.py`, `broker/server.py`, `broker/jsonrpc.py`, `broker/credentials.py`, `broker/launchd/com.hermes.broker.plist`, `broker_client.py` | P1a-a |
| **P1a-c** | P1a-3 | `broker/held_store.py` (durable + fail-closed restart) | P1a-b |
| **P1a-d** | P1a-4 | `broker/action_id.py`, `broker/approval.py`, `gateway/broker_approval_buttons.py` | P1a-c |
| **P1a-e** | P1a-5 | `broker/safe_lane.py`, `broker/policy/*.md` | P1a-c |
| **P1a-f** | P1a-6 | `worker/__init__.py`, `worker/base.py`, `worker/local_subprocess.py` | — (parallel with broker) |
| **P1a-g** | P1a-7 | `worker/remote_stub.py` | P1a-f |
| **P1a-h** | P1a-8 | `broker/executors/message_executor.py`, `broker/executors/git_push_executor.py`, `plugins/control-room/policies/terminal-git-push-deny.yaml`, `plugins/control-room/policies/terminal-hermes-send-deny.yaml` (NEW belt for B10), `tools/send_message_tool.py` (`_handle_send` re-route), `hermes_cli/send_cmd.py` (refuse-or-route, B10), `hermes_cli/env_loader.py` (EGRESS-cred starving, §1.6), `scripts/factory_health.py` (broker section) | P1a-b |

**Cross-task shared-file resolution:** `send_message_tool.py`, `hermes_cli/send_cmd.py`,
`hermes_cli/env_loader.py`, and `factory_health.py` are owned solely by **P1a-h** (all the
egress re-route + cred-starving edits land in one task, keeping the set file-disjoint). The
approval-nonce mint/validate/burn lives entirely in P1a-d's `broker/approval.py` +
`gateway/broker_approval_buttons.py` — no overlap with P1a-h. `broker/server.py` dispatch
references methods implemented in P1a-c/d/e via import — those tasks add *new files*, P1a-b's
server imports them (dispatch table wired in P1a-b, method bodies in the dependent tasks). No
shared-file write.

### 5.2 Per-component RED tests (TDD — assert the failure first)

**P1a-b broker process (4–6):**
1. `test_broker_listens_on_uds` — socket exists at path, mode 0600, `health` returns.
2. `test_broker_is_separate_pid` — broker PID ≠ gateway PID (`ps`).
3. `test_client_has_no_send_method` — `broker_client` exposes no `send`/`git_push` verb.
4. `test_jsonrpc_halfline_discarded` — a truncated frame is dropped, not misparsed.
5. `test_broker_health_in_p08` — `factory_health.py` reports the broker section.

**P1a-c fail-closed + durability (4–6):**
1. `test_held_action_survives_restart` — enqueue→hold→kill broker→restart→action present.
2. `test_no_direct_fallback_when_broker_down` — broker down ⇒ `send_message` errors, does NOT send.
3. `test_approve_is_idempotent` — approve same id twice ⇒ single execution.
4. `test_long_payload_not_truncated` — 100KB payload stored and re-read byte-identical.
5. `test_state_transition_atomic` — crash mid-transition leaves a consistent state.
6. `test_crash_between_ack_and_commit_loses_nothing` (review P1-#6) — the enqueue is
   committed/fsynced to SQLite **before** the RPC returns `action_id`; a crash after ack but
   before an in-flight write never leaves the assistant believing an action is queued that isn't.

**P1a-d approval surface + authority (6–7):**
1. `test_action_id_is_ulid_stable` — id is ULID, never derived from body hash.
2. `test_approve_with_valid_nonce_executes` — approve(id, valid nonce) runs the real (stubbed) egress.
3. `test_reject_by_id_no_send` — reject(id, nonce) ⇒ no egress, state=rejected.
4. `test_generic_types_share_path` — message/pm_os/git_push/merge all enqueue+approve identically.
5. `test_telegram_carries_id_not_payload` — surface message has id+summary (+nonce in callback data), not full body.
6. **`test_assistant_cannot_self_approve`** (review P0-3) — assistant client calling
   `broker_client.approve(id, approver="yk")` / any `approve` WITHOUT a valid broker-minted
   nonce on its own enqueued action is REJECTED; no egress occurs.
7. `test_nonce_single_use` — approve twice with the same nonce ⇒ second is rejected (burned),
   and a stale/expired nonce is rejected.

**P1a-e safe-lane (5–6, one per condition + integration):**
1. `test_all_five_hold_auto_sends` — the "got it to a known colleague" case auto-sends.
2. `test_recipient_off_allowlist_holds` (C2).
3. `test_strategic_marker_holds` — "email the board" holds (C3/C5).
4. `test_irreversible_holds` (C5) — force-push / external first-contact holds.
5. `test_any_single_failure_holds` — flip each condition FALSE in turn ⇒ held.
6. `test_p1a_conservative_default_holds_most` — default C4=FALSE ⇒ near-empty safe lane.

**P1a-f/g worker launcher (5–6) — incl. the process-group proof:**
1. `test_launch_creates_process_group` — child pgid == pid (`os.getpgid`).
2. `test_kill_terminates_children` — spawn parent+child, `kill`, assert **both** gone; AND
   spawn a grandchild that itself calls `setsid` (escapes the pgid) ⇒ assert the escape is
   detected (scan orphaned descendants by process tree, not just pgid) OR the residual is
   documented (review P2 — mtime/killpg cannot reach a re-`setsid`'d grandchild by group alone).
3. `test_stalled_detected_by_mtime` — alive but output frozen ⇒ STALLED, not RUNNING. (Named
   limit, review P2: mtime detects "writing," not "progressing" — a keep-alive spinner reads
   RUNNING forever; the staleness window is a liveness heuristic, not a progress guarantee.)
4. `test_collect_result_bounded` — never blocks past timeout.
5. `test_remote_stub_same_interface` — stub satisfies `Worker` ABC; dispatched by the
   *same* supervisor path as local (the P1a-7 gate).

**P1a-h bypass-impossibility (THE gate tests — assert egress FAILS; strengthened per review):**
1. `test_hermes_send_cli_refuses_or_routes` (review P0-1) — invoke the **real** `hermes send`
   subcommand (`hermes_cli/send_cmd.py`, not a stub) in an assistant context ⇒ it refuses OR
   frames `enqueue_action`; **no message leaves** and it does not fall back to a direct adapter.
2. **`test_assistant_process_env_starved_of_egress_creds`** (review P0-2, replaces the old
   file-read test) — spin up a fresh assistant/gateway process through the real `env_loader`
   load path; assert `os.environ` contains a live **MODEL** key (e.g. `ANTHROPIC_API_KEY` or
   `OPENROUTER_API_KEY`) AND contains **no** live EGRESS secret (`TELEGRAM_BOT_TOKEN`,
   `DISCORD_BOT_TOKEN`, MS Graph secret, `GITHUB_TOKEN`, `WEIXIN_TOKEN`). Covers `~/.hermes/.env`,
   managed `/etc/hermes/.env`, Bitwarden, and config.yaml scalar bridging (§1.6). File-read-finds-
   nothing is NOT sufficient — the assertion is on the process env.
3. `test_assistant_can_still_converse` — the same starved process can still resolve a MODEL key
   and run one model turn (proves the split didn't break the product).
4. `test_raw_git_push_blocked_and_no_token` — `terminal("git push ...")` ⇒ blocked by policy;
   AND with the deny bypassed (`git -C`/alias), `GITHUB_TOKEN`/`GH_TOKEN` is absent from the
   process env so `git push https://$TOKEN@host` finds no live token (wall = cred-absence).
5. `test_ms_graph_sendmail_fails_no_cred` (B11) — MS Graph `sendMail` path finds no live token ⇒ no send.
6. `test_discord_direct_fails_no_cred` (B12) — Discord tool finds no `DISCORD_BOT_TOKEN` ⇒ no post.
7. `test_execute_code_import_adapter_fails` — code importing the adapter can't send (cred absent).
8. `test_subagent_inherits_cred_absence_incl_terminal` (review P2/B14) — a delegated subagent
   cannot send via `send_message`/`execute_code` (stripped by `DELEGATE_BLOCKED_TOOLS`) AND
   cannot send via `terminal` (`curl`/`hermes send`/`git push`) because the shared env is starved.
9. `test_send_message_routes_to_broker` — the re-routed `_handle_send` frames `enqueue_action`, never touches an adapter.

### 5.3 Fresh-context exit-gate script (maps to plan §P1a binding gate)

A single reviewer-run checklist (no self-report): (a) approve a held action incl. one long
payload from Telegram → it executes, AND an assistant `approve()` WITHOUT the nonce on its own
action is **rejected** (self-approval blocked, §2.1); (b) safe-lane auto-sends one
recoverable/operational action and holds one strategic one; (c) attempt direct bypass across
the FULL egress surface — `hermes send` CLI, MS Graph sendMail, Discord tool, raw `git push`
(incl. HTTPS-token variant), and a fresh assistant process's `os.environ` — **every EGRESS
credential is absent while the MODEL key is present and the assistant can still converse** (all
produce no send effect); (d) `remote_stub` compiles and is dispatched by the same supervisor
path as local; (e) a local subprocess (incl. a `setsid`-escaping grandchild, or the escape
documented) is killed via SIGTERM-to-process-group. Each is one of the RED tests above going
GREEN, re-run by the verifier.

---

## Constraint compliance notes

- **Immutable ring (P2.8):** broker source (`broker/**`), the client stub, and policy
  files (`broker/policy/**`, `plugins/control-room/policies/**`) MUST be write-blocked
  from the assistant/worker. P1a lands a control-room write-block *policy* (belt); the
  OS-level immutable ring is P2.8 (suspenders). B6/B7/B8 are the named residuals P2.8
  inherits.
- **No native mechanism rebuilt:** `pre_tool_call` + control-room policy engine +
  `approval.py` dangerous-shell gate are reused/kept as-is; only the credential-holding
  broker process, its store, safe-lane, and worker launcher are new (§0 table).
- **UNVERIFIED:** exact keychain-item ownership semantics under the broker's launchd
  session (whether the assistant's login context can unlock a broker-scoped keychain item)
  — must be validated empirically in P1a-8 (remediation §Confidence: "validate the
  process-boundary approach against your macOS setup early"). Flagged, not assumed.
