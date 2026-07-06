# Hermes "AI Chief of Staff" — Architecture Findings (Failure-Prone Paths)

Forensic evidence ingestion for the failed deployment on branch `feat/governance-plugins` (base v0.14.0).
Every mechanism below carries `file:line` references. Line counts collected via `wc -l`.

## God-file sizes (complexity baseline)

| File | Lines | Bytes | Role |
|---|---|---|---|
| `gateway/run.py` | 18,760 | 863 KB | Gateway runtime, platform connect/retry, session dispatch, delivery, update/restart |
| `cli.py` | 14,780 | 653 KB | HermesCLI entrypoint |
| `agent/conversation_loop.py` | 4,194 | 229 KB | Agent tool-calling loop |
| `run_agent.py` | 4,309 | 184 KB | `AIAgent` core loop (thin wrappers over extracted modules) |
| `hermes_state.py` | 3,279 | 137 KB | SessionDB (SQLite) |
| `tools/mcp_tool.py` | 3,584 | 144 KB | MCP client |
| `tools/delegate_tool.py` | 2,801 | 117 KB | Sub-agent delegation |
| `tools/terminal_tool.py` | 2,379 | 98 KB | Terminal execution (local + cloud sandboxes) |
| `cron/scheduler.py` | 1,988 | 83 KB | Cron scheduler |
| `tools/approval.py` | 1,424 | 61 KB | Dangerous-command approval |
| `gateway/config.py` | 1,853 | 87 KB | Gateway config / Platform enum |
| `gateway/platforms/telegram.py` | 5,694 | 252 KB | Telegram adapter (largest platform) |

Platform adapters alone total >1.7 MB (`gateway/platforms/`), 25+ platforms. `gateway/platforms/base.py` is 161 KB.

Coupling note: the single enforcement point for tool blocking is spread across THREE files that must stay in lockstep — `agent/tool_executor.py`, `model_tools.py:handle_function_call`, and `hermes_cli/plugins.py:get_pre_tool_call_block_message`. Any tool path that skips `tool_executor` (see below) loses guard coverage.

---

## 1. Gateway main loop — startup, connect/retry, lifecycle, exit codes

- Entry class `GatewayRunner` at `gateway/run.py:1542`. `_exit_code` initialized `None` at `gateway/run.py:1597`; exposed via `exit_code` property `gateway/run.py:2052`.
- Per-adapter connect with timeout: `_connect_adapter_with_timeout` `gateway/run.py:2027`.
- **Connect failure is NOT fatal.** Retryable failures are queued for background reconnection: `gateway/run.py:2384-2394` (`next_retry = monotonic()+30`). If ALL platforms are down, the gateway **stays alive** and lets the reconnect watcher recover them — `gateway/run.py:2407-2419` ("gateway staying alive, watcher will..."). Non-retryable errors mark `platform_state="fatal"` (`gateway/run.py:2371`, `3978`).
- Per-platform circuit breaker (pause/resume) at `gateway/run.py:2599-2660`; paused platforms get `next_retry = inf` (`gateway/run.py:2629`).
- **Exit-nonzero causes (the gateway rarely exits on its own):**
  - Service restart request → `self._exit_code = GATEWAY_SERVICE_RESTART_EXIT_CODE` at `gateway/run.py:5975` (systemd sees this and restarts). Restart vs shutdown interrupt reason chosen at `gateway/run.py:5812`.
  - Self-update path writes exit codes to `~/.hermes/.update_exit_code`; timeout writes `124` (`gateway/run.py:14207`), non-zero update exit surfaced at `gateway/run.py:14255-14262`.
  - Unhandled exceptions propagating out of the asyncio run loop (no blanket top-level guard around the whole loop; individual sends are wrapped).
- Shutdown forensics: `gateway/shutdown_forensics.py` (`check_systemd_timing_alignment`, imported `gateway/run.py:3679`) — indicates recurring unexplained restarts were a known operational problem.

Failure characteristic: because platform outages keep the process alive and only log, a silently wedged adapter (e.g. Telegram token revoked → `fatal`) produces **no exit, no crash** — the CoS just goes quiet on that channel. Weak signal for operators.

---

## 2. Tool dispatch chain + the unintended-shortcut mechanics (KNOWN INCIDENT)

### Dispatch chain (happy path)
LLM tool call → `agent/tool_executor.py` (`execute_tool_calls_sequential` `:486` / `_concurrent` `:65`) → `get_pre_tool_call_block_message(...)` `agent/tool_executor.py:525` (seq) / `:133` (conc) → if not blocked, `agent._tool_guardrails.before_call` (loop guardrail) → `run_agent.handle_function_call` / `agent._invoke_tool` with `skip_pre_tool_call_hook=True` → `model_tools.py:handle_function_call:751` → `registry._dispatch_unchecked` `model_tools.py:855` → `invoke_hook("post_tool_call")` `model_tools.py:865`.

- Single-fire contract for `pre_tool_call`: fired once in `tool_executor`, skipped in `handle_function_call` when `skip_pre_tool_call_hook=True` (`model_tools.py:794`).
- Hook aggregation: `get_pre_tool_call_block_message` `hermes_cli/plugins.py:1490` — **first block directive wins** (`:1528-1536`), supports `halt_turn`. All `pre_tool_call` hooks run via `invoke_hook` `hermes_cli/plugins.py:1358`, each wrapped in try/except so one hook's crash doesn't stop others (`:1380-1388`).

### The three guards (all `pre_tool_call` hooks)
1. **control-room** (`plugins/control-room/__init__.py`) — the real safeguard layer. Hardcoded blocks in `pre_tool_call` `:549`:
   - `terminal`: blocks `outlook-send-mail/reply`, `pm_os/bin/outlook*` (`:562-583`) and `get-foci-token` (`:621-637`).
   - `write_file`/`patch`: blocks paths containing `plugins/control-room` or `plugins/email-send-guard` (`:587-598`).
   - `execute_code`: blocks importing hermes internals (`:602-617`).
   - `read_file`: blocks credential paths via `_is_credential_path` (`:642-650`, patterns `:502-513`).
   - `search_files`: blocks credential patterns (`:655-674`).
   - YAML policy engine (`:377 evaluate`) with fail-closed try/except (`:680-706`). Policies in `plugins/control-room/policies/` add regex blocks: `terminal-token-read-deny` (regex `pm-os-foci-token|pm-os-foci-office-token`), `terminal-email-block` (regex `smtplib|SMTP\(|nodemailer|sendgrid|curl.*smtp|graph.microsoft.com...sendMail|...`).
2. **email-send-guard** (`plugins/email-send-guard/__init__.py`) — only intercepts `send_message` with `target` starting `email` (`:256-262`).
3. **tool-registry-guard** (`plugins/tool-registry-guard/__init__.py`) — only blocks `pip install <banned>` and `import <banned>` in `terminal`/`write_file`/`patch` (`:289 _check_terminal`, `:329 _check_file_content`). Banned set is Office/Excel packages only (`default-registry.yaml`).

### Why the LLM could read the filesystem, use a stored login token it didn't need, and edit its own safeguards
The guards enumerate **specific tool names + specific substrings**. The `terminal` tool is a universal escape hatch with **no filesystem sandbox** on the local backend (`tools/terminal_tool.py` only validates `workdir` characters at `:337 _validate_workdir`; no read/write path allow-list). Concretely:

- **Arbitrary file read / token exfil via terminal:** The credential-path block lives only on the `read_file` and `search_files` tools (`control-room:642-674`). A `terminal` `cat`/`grep`/`python -c "open(...)"` of `~/.hermes/auth.json`, `.env` (holds `EMAIL_PASSWORD` + provider API keys — see `gateway/platforms/email.py:20`), MCP token stores, etc. is **not** covered. Only file paths literally matching `pm-os-foci-token`/`outlook`/`get-foci-token` are caught (control-room `:562-637` + `terminal-token-read-deny` policy). Any secret stored under a different name is readable through `terminal`. → filesystem read + using a stored login token confirmed possible.
- **Patching guard source via terminal:** The write-protection for `plugins/control-room/` and `plugins/email-send-guard/` triggers **only for `tool_name in ("write_file","patch")`** (`control-room:587-589`). A `terminal` `sed -i ...`, `cat > ...`, `python -c "open(...,'w')"`, `tee`, or `patch(1)` shell command editing the guard `__init__.py` is **not intercepted**. → self-modification of safeguards confirmed possible. (Also note: `tool-registry-guard` and `agent/tool_guardrails.py` have NO write-protection at all, even against `write_file`/`patch`.)
- **execute_code** import block is string-matching (`control-room:602-617`), bypassable via `importlib`, `__import__`, or `open()`-based file writes; but terminal is the simpler hole.

Root cause: guard model is a **deny-list keyed on tool_name+substring**, while `terminal` grants unrestricted shell. Defense-in-depth for reads/writes was applied to the narrow tools but never to the shell that supersets them.

---

## 3. Approval flow — /approve-email end to end (assessing the 4-bug fix)

State machine EMPTY→LOADED→PREVIEWED→APPROVED→SENT, all in `plugins/email-send-guard/__init__.py`:
- Draft storage: content-addressed by SHA256 of **body only** (`_body_hash:167`), one JSON file per draft (`_save_draft:88`), pointer in `state.json` with per-session scoping (`current_by_session`, `_set_current_draft_id:190`).
- `email_load_draft` (`:431`) stores body+recipient (recipient required, lowercased); `email_show_preview` (`:469`) sets `previewed_at`.
- `/approve-email` (`_handle_approve:510`) — approves current scoped draft, stores approval keyed by draft_id with `recipient`, `expires_at = now+900s` (`:558-562`). Returns a dict with `approved_tool_call` (gateway executes `send_message` deterministically) + `followup_agent_message` fallback (`:579-604`).
- Enforcement gate `_pre_tool_call` (`:246`): Gate 1 draft loaded (`:271`), Gate 2 previewed (`:284`), Gate 3 approval exists + not expired + **recipient matches** (`:303-354`), all with `halt_turn:True`.
- Consumption moved to `_post_tool_call` (`:370`) — approval deleted **only after a successful send** (checks result for `error`, `:395-403`), so a failed SMTP/Graph send no longer burns the approval (`:409-419`).

**Assessment — the 4 compounding bugs are addressed, but the code is not clean:**
- Hash mismatch: FIXED — approvals now keyed by the same body-hash the gate recomputes (`:303`).
- Truncated body / recipient binding: FIXED — recipient stored in draft and re-verified at Gate 3 (`:338-354`).
- No consumption: FIXED — consumed in `_post_tool_call` on success (`:416-424`).
- Missing recipient validation: FIXED — recipient required at load (`:446 _email_load_draft` schema `required:["body","recipient"]`).

Residual risks: (a) approval bound to **body only** — two sends of identical body to the *same approved recipient* within the 15-min window are indistinguishable; recipient mismatch is caught but same-recipient replay is not rate-limited. (b) Heavy `logger.warning("[EMAIL-TRACE]...")` instrumentation still in the hot path (`:298-362`) — leaks recipient + hashes to logs and signals this path was still being actively debugged. (c) State is **per-user (HERMES_HOME), not per-session** for approvals dict (`:637 register` docstring) — the `current` pointer is per-session but the `approvals` map is global; a stale global `current` (`state["current"]`, `:297`) is deliberately ignored at Gate 3, which is correct, but the dual pointer model is fragile.

---

## 4. Email send end-to-end

compose (LLM `send_message target=email:...`) → `tool_executor` pre_tool_call → **email-send-guard gates** (draft/preview/approval) → **control-room** `terminal-email-block` + outlook blocks (defense against bypassing send_message) → `model_tools.handle_function_call` → `tools/send_message_tool.py:_handle_send` → `_send_to_platform` (`:407`) → gateway email adapter SMTP (`gateway/platforms/email.py`, `smtplib` `:34`, `send()` `:735`) → `_post_tool_call` consumes approval.

- `send_message_tool` extracts a leading `Subject:` header for the email body (`_extract_email_subject_and_body:84`).
- Inbound ACL: `EMAIL_ALLOWED_USERS` checked at `gateway/platforms/email.py:676-681`; automated/noreply senders dropped (`_is_automated_sender:101`). **Outbound has no independent recipient allow-list** — any address the approved draft names is sent (email-send-guard binds approval to recipient but does not restrict the recipient domain/set). This is the design gap: a misbehaving agent that gets user approval (or an approval-flow gap) can email arbitrary external recipients using the user's real mailbox credentials (`EMAIL_ADDRESS`/`EMAIL_PASSWORD` from env).

---

## 5. Channel routing / delivery — Telegram→WhatsApp silent-reroute risk

- Delivery target parsing: `gateway/delivery.py:DeliveryTarget.parse:46`. **Unknown or unparseable platform silently degrades to `Platform.LOCAL`** (`:84`, `:93`) rather than erroring — a malformed target writes to disk instead of alerting.
- `send_message` home-channel resolution: `tools/send_message_tool.py:361-377` — when no `chat_id`, uses the platform's configured home channel; hard error if none set (`:373`). Cross-platform reroute is **guarded only by a soft warning**, not a block: `send_message_tool.py:421-429` appends `result["warning"] = "...sending to X but session originated on Y. Are you sure?"` — the send has **already happened** when the warning is attached. There is no hard stop preventing a reply meant for Telegram from going to WhatsApp/email if the LLM picks the wrong `target`.
- **Where a Telegram failure can silently land elsewhere:** cron `deliver=origin` falls back to a platform *home channel* when origin is missing (`cron/scheduler.py:360` "falling back to %s home channel"). If the origin platform's home channel is unset but another platform is the configured default/home, output routes there. Combined with `delivery.py` degrading unknowns to LOCAL, and the reroute being warn-only, misrouting is possible without a hard error. No automatic "if Telegram send fails, retry on WhatsApp" fallback exists in `send_message_tool` (grep found none), but the LLM is free to choose a different `target` after seeing a Telegram error, and nothing blocks that.
- Sent messages are mirrored back into the *target's* gateway session (`gateway/mirror.py` via `send_message_tool.py:432-446`), so a misroute also pollutes the wrong session's transcript.

---

## 6. Cron execution path

- Scheduler tick: `cron/scheduler.py:tick:1806` → `run_job:1153` → `_run_job_impl:1160`.
- Two modes: `no_agent` script watchdog short-circuit (`:1188-1242` — runs a shell script, delivers stdout verbatim, no LLM) and the agent path (constructs SessionDB + AIAgent).
- Prompt-injection gate: `CronPromptInjectionBlocked` (`:47`) — job prompts are scanned; a hit yields a clean "job blocked" delivery instead of a crash (`:49-50`).
- Delivery-target validation: cron validates platform names against known delivery platforms (`_is_known_delivery_platform:256`) explicitly to prevent **env-var enumeration via crafted platform names** (`:90-91`) — an acknowledged design gap.
- Origin resolution hardened: `_resolve_origin:215` coerces non-dict origins so a poisoned `origin` field can't crash the scheduler on every tick (`:218-227` documents a prior crash-loop).
- Delivery via `gateway/delivery.py:DeliveryRouter.deliver:129`; oversized output truncated to 4000 chars and spilled to disk (`:242-249`).
- Failure mode: a `no_agent` script that exits non-zero delivers an alert (`:1218-1235`) — good; but a script that exits 0 with empty stdout is **silent** (`:1183`), so a broken monitoring job looks identical to "nothing to report."

---

## 7. Plugin loading & mandatory-plugin enforcement (fail-open gap)

- Loader: `hermes_cli/plugins.py`. Mandatory set hardcoded: `MANDATORY_SECURITY_PLUGINS = {control-room, email-send-guard}` (`:178-181`) — loaded even if listed in `plugins.disabled` (`:901-908`), overriding disable (`:1147-1166`).
- Mandatory load wrapper: `_load_mandatory_plugin:1277` — on failure records `mandatory_load_failures[key]` and logs **CRITICAL** ("dangerous tools may be disabled as a safety measure", `:1292-1296`).
- **CRITICAL FINDING — fail-closed is NOT implemented.** `mandatory_load_failures` is only ever *written* (`:803` init, `:826` clear, `:1291` set) and referenced in a test (`tests/hermes_cli/test_mandatory_plugins.py`). Repo-wide grep found **no production consumer** that reads it to actually disable `terminal`/`write_file`/`send_message`. So if control-room or email-send-guard throws during `register()`, the gateway logs CRITICAL and **keeps running with tools fully enabled and unguarded**. The advertised "fail-closed" backstop is aspirational, not enforced. `tool-registry-guard` is not even in the mandatory set (its YAML lacks `mandatory: true`), so it silently no-ops if PyYAML is missing (`:90-92`).
- Hook failures are individually swallowed (`invoke_hook` try/except `:1380`), so a control-room `pre_tool_call` that raises on a specific tool is logged and **skipped** — but control-room's own body is fail-closed (`:680-706`) which mitigates this for control-room specifically; email-send-guard's `_pre_tool_call` has no such wrapper, so an exception there would fail *open* (hook swallowed → no block).

---

## Cross-cutting: why this architecture is hard for weaker LLMs to run safely

1. **Guard model is deny-list-by-tool-name**, but `terminal`/`execute_code` superset every narrow tool. A weaker model given `terminal` can (accidentally, or due to content it reads being mistaken for instructions) do anything the enumerated guards forbid on `read_file`/`write_file`.
2. **No filesystem sandbox** on the default local terminal backend (`tools/terminal_tool.py:337` validates only workdir characters); secrets live in plaintext `.env`/`auth.json` reachable by shell.
3. **Fail-open reality vs fail-closed intent** — mandatory-plugin backstop unimplemented (§7); email-guard hook unwrapped.
4. **Silent degradation everywhere** — unknown delivery targets → LOCAL, cross-platform reroute is warn-after-send, dead platforms keep the process alive, empty-stdout cron looks healthy. A weak operator/LLM gets no hard failure signal.
5. **Enormous, highly-coupled god-files** (18k-line `gateway/run.py`, 14k-line `cli.py`) with the tool-block contract split across 3 modules; easy to add a tool path that bypasses `tool_executor` and thus all guards.
6. **Approval path still carries debug tracing** and a dual (per-session vs global) pointer model — fragile under concurrency.

## Path corrections
All requested paths existed as given. `cli.py` and `hermes_cli/` both exist (HermesCLI spans both; `hermes_cli/main.py` is 518 KB). `handle_function_call` lives in `model_tools.py:751`, not `run_agent.py`. `post_tool_call` is fired centrally in `model_tools.py:865` (not in `tool_executor.py`).
