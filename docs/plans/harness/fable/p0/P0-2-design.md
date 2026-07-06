# ABOUTME: Design for re-applying the three governance plugins (control-room, email-send-guard,
# ABOUTME: tool-registry-guard) onto the clean upstream base (factory = a05b64d67 + docs).
# ABOUTME: Each plugin's objective is restated from its code, then wired to upstream-native hooks;
# ABOUTME: fork god-file edits are dropped with replacements designed explicitly.
# ABOUTME: Bypass analysis, RED-first acceptance-test plan, and coder task breakdown included.

# P0-2 — Governance Plugin Re-Application Design

**Task:** P0-2-DESIGN (backlog mode, GOVERNANCE_EXEMPT). **Base:** branch `factory` (upstream `a05b64d67` + docs). **Plugin sources:** branch `fable` (read-only; not in working tree).
**Status:** DRAFT for coder implementation. Adversarial review + independent verifier follow separately.

---

## §0 — Verified upstream facts this design rests on

Every claim below was spot-checked against `factory` (git show). Load-bearing facts:

| # | Fact | Evidence (factory) |
|---|---|---|
| F1 | **`mandatory: true` is dead.** The plugin.yaml `mandatory` field has **zero consumers** upstream. The 9602c2ecf mandatory-loading hook is obsolete. | `git show factory:hermes_cli/plugins.py` — `grep mandatory` returns nothing. |
| F2 | **Standalone plugins are opt-in.** `kind` defaults to `standalone` (plugins.py:306); standalone plugins load **only if their key is in `plugins.enabled`** in `~/.hermes/config.yaml` (plugins.py:1406-1440). `_get_enabled_plugins()` returns `None`/`set()` = nothing loads. | plugins.py:241-266, 1406-1440. |
| F3 | **Bundled `kind: backend` and `kind: platform` auto-load** (plugins.py:1386, 1401). Standalone does **not** auto-load even when bundled. | plugins.py:1382-1440. |
| F4 | **`pre_tool_call` is native and first-block-wins.** `get_pre_tool_call_block_message` (plugins.py:2049) calls `invoke_hook("pre_tool_call", …)`, iterates results, returns the first `{"action":"block","message":str}`. Invoked at tool_executor.py:418 (standard) and :1037 (subagent). | plugins.py:2049-2096; tool_executor.py:418, 1037. |
| F5 | **`invoke_hook` swallows callback exceptions** (manager.invoke_hook, plugins.py:1868-1883): each callback in its own try/except; a raise → logged warning → callback contributes no result → **tool proceeds**. So "raise to fail-closed" does NOT work; a plugin must *return* a block dict. | plugins.py:1868-1883. |
| F6 | **`skip_pre_tool_call_hook=True` (tool_executor.py:1413,1455) is NOT a bypass.** Those are CLI-render/quiet-mode dispatch branches reached only *after* the block gate ran at :1037 and `_execution_blocked` short-circuited real execution (:1058-1060). The skip prevents double-invocation, not enforcement. | tool_executor.py:1030-1060, 1405-1465. |
| F7 | **`pre_llm_call` returning `{"context": str}` is still consumed** and appended to the *user* message (never system prompt, preserving cache). | turn_context.py:434-485; plugins.py:1855-1866. |
| F8 | **The email send path is GONE upstream.** No `email:` target handling in `gateway/run.py` or `tools/delegate_tool.py`; `gateway/platforms/email.py` removed. `send_message` has no email platform to dispatch to. | `git grep "email:"` over factory gateway/tools = empty. |
| F9 | **All tool names the fork policies key on exist upstream:** `execute_code`, `patch`, `terminal`, `send_message`, `write_file`, `read_file`, `search_files`. | tools/code_execution_tool.py, file_tools.py, delegate_tool.py, approval.py. |
| F10 | **`register_hook / register_tool / register_command` signatures match fork usage.** `register_tool(name, toolset, schema, handler, …, description, …)` and `register_command(name, handler, description, args_hint)` are exactly what the fork calls. `get_hermes_home()` exists (hermes_constants.py:55). | plugins.py:389, 527, 1109; hermes_constants.py:55. |
| F11 | **`pre_gateway_dispatch` fires before auth** for user-originated MessageEvents (gateway/run.py ~8679). Available if a guard ever needs a gateway-layer egress choke. | gateway/run.py:8679. |

**The one structural change every plugin needs:** because of F1+F2, "mandatory" enforcement is gone. Re-application must (a) set each plugin's `kind` correctly, and (b) guarantee the plugin is actually loaded and its hooks registered — otherwise F5 means a load failure is a *silent* no-guard. This is closed by BR-1 (below) plus the Phase R.1 fail-closed-loading work, which this design depends on but does not implement.

---

## §1 — Cross-cutting decisions (apply to all three plugins)

**BR-1 — Load-guarantee, not `mandatory`.** `mandatory: true` is a no-op (F1). Two-part replacement:
1. **Config seed:** P0-2 adds the three plugin keys to `plugins.enabled` in the base `~/.hermes/config.yaml` (and documents them in the repo's example config). Keys are path-derived (F2): `control-room`, `email-send-guard`, `tool-registry-guard`.
2. **Load-failure is fatal (deferred to R.1).** Under R.1, a failed load of an *enabled* governance plugin makes hermes refuse to start / refuse the affected lane. P0-2 does **not** implement R.1; it (a) leaves `mandatory: true` in each plugin.yaml as a *declarative marker* R.1 will consume, and (b) documents the dependency. Until R.1 lands, the guards are enabled-but-fail-open on load error — an **accepted, documented interim gap** (see Bypass B7).

> Escalation note (REQ-01): The fork relied on `mandatory: true` to force-load these guards. Upstream has no such mechanism, and F5 makes silent load-failure the default. The objective "these guards are always active" **cannot be fully met by P0-2 alone** — it needs R.1's fail-closed loader. P0-2 gets us "enabled by config + declarative mandatory marker"; R.1 closes the loop. Documented, not hidden.

**BR-2 — Keep the block-dict return contract.** All enforcement returns `{"action":"block","message":str}` (F4). Never `raise` for enforcement (F5 swallows it). Control-room's existing internal try/except that *returns* a block dict on error (its "fail-closed wrapper", control-room `__init__.py`:701-706) is correct and must be preserved — it is fail-closed *within* the callback, which survives F5.

**BR-3 — Registration API is compatible (F10).** No changes needed to how the fork calls `ctx.register_hook/register_tool/register_command`. Do not touch `tools/registry.py` dispatch (scout caution confirmed: no hooks there).

**BR-4 — Self-protection must not reference dropped paths.** Control-room's hardcoded write-protection blocks writes to `plugins/control-room` and `plugins/email-send-guard` by literal path substring (control-room:589). On the clean base the plugins re-land at those same paths, so the literal still matches — **keep it**, and add `plugins/tool-registry-guard` (the fork omitted it). See CR code-delta.

---

## §2 — Plugin 1: control-room

### 2.1 Objective (extracted from code + policies)

Control-room is the **audit + policy-enforcement spine**. It must:
- **O1. Audit every tool call** (pre/post/blocked) to SQLite (`audit_log`), with duration, task/session id — the forensic record the factory's trust ledger later builds on.
- **O2. Evaluate declarative YAML policies** (`policies/*.yaml`) against each tool call: block-unless-precondition-met and allow-when-triggers-match semantics (PolicyEngine, `__init__.py`:353-413).
- **O3. Enforce hardcoded egress/exfil blocks** the YAML can't express cleanly (control-room `__init__.py`:549-706):
  - Block direct pm_os outlook JS invocation (`outlook-send-mail`/`outlook-reply-mail`).
  - **Write-protect the guard source itself** (`plugins/control-room`, `plugins/email-send-guard`) — closes the code-rewrite self-escalation that started this whole effort.
  - Block `execute_code` importing hermes internals (`hermes_tools`, `plugins.`, `agent`, `gateway`, …) — stops state forgery / internal-API abuse.
  - Block direct FOCI token extraction (`get-foci-token`), and credential-file **read**/**search** (`read_file`, `search_files` over token/`auth.json` paths).
  - The 8 policy YAMLs extend this to `execute_code`/`patch`/`terminal`/`write_file` carrying email-send or token-read patterns, gated on never-set override state keys (unconditional block).
- **O4. Fail closed on internal error** — any exception in policy eval returns a block dict, not an allow (F5-safe: it returns, doesn't raise).
- **O5. Expose read-only HTTP monitoring** (optional, `CONTROL_ROOM_MONITOR_PORT`).

### 2.2 Upstream-native wiring

| Aspect | Design |
|---|---|
| `kind` | `standalone` (has its own hooks; not a backend/platform). Keep `mandatory: true` as the R.1 marker (BR-1). Enable via `plugins.enabled` (BR-1). |
| Hooks | `pre_tool_call` → `ControlRoom.pre_tool_call`; `post_tool_call` → `ControlRoom.post_tool_call`. Both native (F4), both already in VALID_HOOKS. |
| Call sites | Fires at tool_executor.py:418 and :1037 automatically via `get_pre_tool_call_block_message`. No wiring code — registration is enough. |
| DB / state | `get_hermes_home()/control-room/audit.db` (exists upstream, F10). `workflow_state` table = the precondition store the policies read. Unchanged. |
| HTTP monitor | Keep, opt-in via env. No change. |

### 2.3 Code delta (what changes vs. fable)

1. **BR-4 self-protection widening:** add `plugins/tool-registry-guard` to the write-protection path check (control-room:589) so the third guard's source is also write-protected. One-line list extension.
2. **No structural change** to AuditDB, PolicyEngine, policy YAMLs, or the hardcoded blocks — F9 confirms every keyed tool name still exists; F4 confirms the hook contract is unchanged.
3. **Verify `get_hermes_home` import path** — control-room does `from hermes_constants import get_hermes_home` (control-room:480). Confirmed present (F10). No change, but the RED test asserts it imports.

### 2.4 Dropped fork god-file edits vs. rebuilt functionality

Control-room's objectives are met **entirely inside the plugin** via the native `pre_tool_call` hook — it never needed god-file edits. The fork's god-file edits (the mandatory-load hook and any `tools/registry.py` patching) are the *loader* scaffolding, not control-room logic. Dropped:

| Dropped fork edit | Commit (fork) | Why dropped | Rebuilt as |
|---|---|---|---|
| Mandatory-plugin force-load hook | `9602c2ecf` (per scout) | Upstream has native `plugins.enabled` + `kind`; `mandatory` field is inert (F1) | BR-1: config seed + R.1 fail-closed loader (declarative `mandatory` marker) |
| Any `tools/registry.py` dispatch patch to inject guards | (fork god-file) | Native `pre_tool_call` at tool_executor.py:418/:1037 already invokes plugin hooks (F4); registry dispatch has no hooks and must not be patched (scout caution) | Nothing to rebuild — native hook path replaces it |

**UNVERIFIED:** the exact fork commit hashes and precise line-ranges of the god-file edits are not readable from `fable` plugin paths alone (they live in non-plugin files). Scout named `9602c2ecf` for the mandatory hook; coders must confirm against the fork diff before dropping. Flagged, not fabricated.

---

## §3 — Plugin 2: email-send-guard

### 3.1 Objective (extracted from code)

Enforce a **deterministic draft→preview→approve→send state machine** for `send_message` with an `email:` target (email-send-guard `__init__.py`:246-363). Concretely it must:
- **E1.** Block `send_message(target="email:…")` unless a draft for that exact body hash is **loaded** (Gate 1), **previewed** (Gate 2), and **user-approved and unexpired** (Gate 3), with the approved **recipient matching** the send target.
- **E2.** Provide agent tools `email_load_draft`, `email_show_preview`, and the user-only `/approve-email` command.
- **E3.** Consume the one-time approval only *after* a successful send (post_tool_call), so a failed send doesn't burn approval.

### 3.2 The objective is moot upstream — escalation (REQ-01)

**F8: there is no email send path upstream.** `send_message` cannot target `email:` — the platform is gone. So the state machine guards a door that no longer exists: the guard would load, register its hook, and simply never fire (its `_pre_tool_call` early-returns `None` when `target` isn't `email…`).

Per the requirement map, this defers to the **P1a broker (Theme F superseded)**: the plan explicitly removes fork email workflow and makes *all* consequential egress — including any future email send — a **held action on the broker's single approval surface** (plan §1a.1/§1a.2). The email-send-guard's draft/preview/approve/recipient-binding logic is the **conceptual seed** for the broker's held-action approval (plan §153 "Generalizes the email-approval state machine"), but it is **not re-wired as an active tool guard** on the clean base.

**Disposition — three options, recommendation:**

| Option | What | Recommendation |
|---|---|---|
| **A. Port dormant (RECOMMENDED)** | Re-apply the plugin as-is, `kind: standalone`, **NOT** added to `plugins.enabled` (stays off). Preserves the code as the P1a design reference; zero runtime effect; no dead hook firing. | ✅ Adopt. Lowest risk, keeps the reference, honours "email workflow superseded." |
| B. Drop entirely | Don't re-apply; rely on P1a to rebuild from scratch. | ❌ Loses the battle-tested recipient-binding + one-time-approval + post-send-consume logic P1a wants to generalize. |
| C. Re-target now | Rewire the state machine onto the broker egress. | ❌ Out of scope — that IS P1a; would duplicate/pre-empt it. |

So P0-2 **re-applies email-send-guard to disk (Option A) but leaves it disabled**, and the control-room hardcoded email blocks (O3) provide the *interim* "no raw email egress" enforcement until P1a's broker owns egress. The control-room `execute_code`/`patch`/`terminal` email-pattern policies keep blocking any code path that tries to smuggle an email send — those fire regardless of the missing platform (they key on smtplib/Graph patterns, not on `send_message`).

### 3.3 Upstream-native wiring (if/when enabled)

| Aspect | Design |
|---|---|
| `kind` | `standalone`. Keep `mandatory: true` marker. **Omit from `plugins.enabled`** (Option A → dormant). |
| Hooks | `pre_tool_call`/`post_tool_call` native (F4). No-op until an `email:` platform exists. |
| Tools/command | `email_load_draft`, `email_show_preview`, `/approve-email` register via compatible API (F10). They register even when dormant-but-enabled; under Option A the plugin isn't enabled so they don't. |

### 3.4 Code delta vs. fable

**None functionally.** The plugin ports byte-for-byte. Two cleanups the coder SHOULD make (low-risk, matches code-style — the `[EMAIL-TRACE] logger.warning` spam is a debugging relic):
- Downgrade the `logger.warning("[EMAIL-TRACE] …")` calls (email-send-guard:298-596) to `logger.debug`. They are not enforcement; they pollute logs. (Optional; flag to owner — it's a behavioral change to log output only.)
- No other change. Do **not** attempt to fix the four historical `/approve-email` bugs here — the plan replaces this machinery with the P1a broker (plan §1a.2 explicitly targets "the exact bug class that broke `/approve-email` four ways"). Fixing them in a dormant plugin is wasted effort.

### 3.5 Dropped fork god-file edits

| Dropped fork edit | Commit | Why dropped | Rebuilt as |
|---|---|---|---|
| `gateway/platforms/email.py` send integration + any `send_message` email routing | fork (Theme F) | Platform removed upstream (F8); plan supersedes fork email workflow | P1a broker egress (not P0-2) |
| Mandatory force-load of email-send-guard | `9602c2ecf` | F1 | BR-1 (but plugin stays dormant under Option A) |

---

## §4 — Plugin 3: tool-registry-guard

### 4.1 Objective (extracted from code + default-registry.yaml)

Steer file-I/O toward the registered pm_os Office/SharePoint tools and **block banned DIY alternatives**. It must:
- **T1. Block** `pip install <banned>` and `python -c "import <banned>"` in `terminal` commands, and banned `import`/`from … import` statements in `write_file`/`patch` content (tool-registry-guard `__init__.py`:356-395). Banned sets derive from `default-registry.yaml` (pandas, openpyxl, python-docx, python-pptx, …).
- **T2. Inject** the condensed registry table into every LLM turn via `pre_llm_call` returning `{"context": …}` so the model prefers registered tools (tool-registry-guard:402-421).
- **T3. Audit** every codegen tool call (`write_file`/`patch`/`terminal`) to a JSONL with a violation flag (post_tool_call, tool-registry-guard:468-520).

### 4.2 Upstream-native wiring

| Aspect | Design |
|---|---|
| `kind` | `standalone`. `mandatory: false` in fork's plugin.yaml (note: this one was NOT marked mandatory) — **set `mandatory: true`** to match the other two so R.1 also protects it, and add to `plugins.enabled`. |
| Hooks | `pre_tool_call` (T1, native F4), `pre_llm_call` (T2 — **F7 confirms `{"context":…}` is still consumed** and appended to the user message, cache-safe), `post_tool_call` (T3). All three in VALID_HOOKS. |
| Registry file | `~/.hermes/tool-registry.yaml` override → bundled `default-registry.yaml` fallback (tool-registry-guard:51-74). Unchanged; ships with the plugin. |
| Audit path | `get_hermes_home()/tool-registry-guard/audit.jsonl`. Unchanged. |

### 4.3 Code delta vs. fable

1. **plugin.yaml:** add `mandatory: true` (declarative R.1 marker; parity with the other two). No behavior change from that field alone (F1).
2. **No functional code change.** T1/T2/T3 all ride native hooks with unchanged contracts (F4, F7). Every keyed tool name exists (F9).
3. **Confirm `pre_llm_call` kwargs compatibility:** the fork handler signature is `_pre_llm_call(session_id, user_message, conversation_history, is_first_turn, model, **_)`. Upstream invokes with `session_id, task_id, turn_id, user_message, conversation_history, is_first_turn, model, platform, sender_id` (turn_context.py:435-447). The `**_` absorbs the extras — **compatible**. RED test asserts the injected context appears.

### 4.4 Dropped fork god-file edits

| Dropped fork edit | Commit | Why dropped | Rebuilt as |
|---|---|---|---|
| Mandatory force-load | `9602c2ecf` (if it covered this plugin) | F1 | BR-1 config seed + R.1 marker |
| Any registry-context injection wired outside the plugin (god-file) | fork | Upstream `pre_llm_call` `{"context":…}` path handles it natively (F7) | Native `pre_llm_call` hook — no external edit |

---

## §5 — REQ-02: Bypass analysis

Every way a tool call or outbound send could evade the re-applied guards on the clean base. Disposition per row: **CLOSE** / **ACCEPT** (with rationale) / **DEFER-P1a** (broker owns egress later).

| # | Bypass vector | Reachable? | Disposition | Detail |
|---|---|---|---|---|
| B1 | `skip_pre_tool_call_hook=True` (tool_executor.py:1413,1455) skips the guard | **No** (F6) | **ACCEPT** | The block gate already ran at :1037; `_execution_blocked` short-circuits execution before those skip-branches. The skip only prevents double-invocation. RED test B1 proves a blocked tool never reaches the skip branch. |
| B2 | Plugin not in `plugins.enabled` → never loads → no guard | **Yes** (F2) | **CLOSE** | BR-1: seed the three keys into base `config.yaml`. Verifier greps loaded config + asserts hooks registered. |
| B3 | Agent removes its own key from `plugins.enabled` at runtime | **Yes** | **CLOSE** | Control-room O3 write-protects guard *source*, but config.yaml is not guard source. **Add** `~/.hermes/config.yaml` + `plugins.enabled` edits to control-room's hardcoded write-protection path list (extend BR-4). Also: config is read once at startup (F2), so a runtime edit has no effect until restart — and restart re-reads the seeded value. RED test B3. |
| B4 | `HERMES_SAFE_MODE=1` disables ALL plugin discovery (plugins.py:1243) | **Yes** | **ACCEPT (documented)** | Safe mode is an owner-invoked troubleshooting switch requiring shell/env access the agent shouldn't have (credentials + env live outside agent reach post-P1a-1a.5). Document: "SAFE_MODE disables guards — operator-only, never set in the launchd service env." Verifier confirms the launchd plist does NOT set it. |
| B5 | Plugin load *fails* (bad import, missing dep) → F5 swallows → silent no-guard | **Yes** (F5) | **DEFER-R.1** | This is exactly the R.1 fail-closed-loading deliverable. P0-2 leaves the `mandatory: true` marker for R.1 to consume. Interim = documented gap (B7). |
| B6 | Direct email send via `smtplib`/Graph in `execute_code`/`terminal`/`patch`/`write_file` | **Yes** | **CLOSE** | Control-room's 8 policy YAMLs + hardcoded blocks (O3) fire on these patterns independent of the missing `send_message` email path. RED test B6 (smtplib in execute_code → blocked). |
| B7 | Interim: guards enabled but fail-open on load error until R.1 lands | **Yes** | **ACCEPT (interim, documented)** | Between P0-2 and R.1, a load failure = silent no-guard. Bounded: (a) the base image is tested to load all three; (b) R.1 is on the critical path right after P0-2 (plan Phase R depends on P0-2). Escalated in BR-1. |
| B8 | Gateway path that reaches egress without `tool_executor` (e.g. a slash command or platform adapter sending directly) | Partial | **DEFER-P1a** | `pre_gateway_dispatch` (F11) fires before auth but is *inbound*. True outbound egress-without-tool-executor is the broker's remit (P1a owns *all* egress incl. `git push`). P0-2's control-room blocks the known tool-level paths; P1a closes the credential wall. |
| B9 | Subagent / delegated worker path skips guards | **No** (F4/F6) | **CLOSE** | The subagent path at tool_executor.py:1037 invokes `get_pre_tool_call_block_message` — same guard. RED test B9 asserts a blocked tool is blocked on the subagent path too. |
| B10 | `execute_code` imports hermes internals to forge guard `workflow_state` or call internal APIs | **Yes** | **CLOSE** | Control-room O3 hardcoded block on `execute_code` importing `hermes_tools/plugins./agent/gateway/…` (control-room:602-617). RED test B10. |
| B11 | Read/search credential/token files to exfiltrate, then send out-of-band | **Yes** | **CLOSE** (tool-level) / **DEFER-P1a** (credential wall) | Control-room blocks `read_file`/`search_files` over token/`auth.json` paths (O3). Full credential removal from agent reach = P1a §1a.5. RED test B11. |
| B12 | `pip install` / banned import to hand-roll Office I/O bypassing registered tools | **Yes** | **CLOSE** | tool-registry-guard T1 blocks pip-install + import patterns in terminal/write_file/patch. RED test B12. |

**No bypass is left un-dispositioned.** Two ACCEPTs (B1 native-safe, B4 operator-only), one interim ACCEPT (B7), three DEFER-P1a (B5→R.1, B8/B11-credential-wall→P1a), rest CLOSED.

---

## §6 — REQ-03: Acceptance-test plan (RED-first, per plugin)

QA writes these **before** coders implement; each must FAIL on the clean base (no plugin re-applied) and PASS after. pytest style. Location: `tests/plugins/<plugin>/test_*.py` (matches upstream test layout under `tests/`). No mocks of the guard itself; drive real `get_pre_tool_call_block_message` / `invoke_hook`.

### 6.1 control-room — `tests/plugins/control_room/`

| Test | Asserts |
|---|---|
| `test_pre_tool_call_blocks_smtplib_execute_code` | `ControlRoom().pre_tool_call("execute_code", {"code":"import smtplib"})` returns `{"action":"block", …}` (O3 + policy YAML). |
| `test_write_protect_own_source` | `pre_tool_call("write_file", {"path":".../plugins/control-room/__init__.py", …})` → block; **and** `plugins/tool-registry-guard/…` → block (BR-4 widening). |
| `test_block_get_foci_token` | `pre_tool_call("terminal", {"command":"node get-foci-token.js"})` → block. |
| `test_block_credential_read` | `pre_tool_call("read_file", {"path":".../pm-os-foci-token.json"})` → block; a benign `read_file` of `/tmp/x.txt` → `None` (no false positive). |
| `test_fail_closed_on_internal_error` | Corrupt/sabotage the policy state so eval raises; the callback **returns** a block dict (not raises), i.e. F5-safe fail-closed. |
| `test_audit_row_written` | After a permitted call, `audit_log` has a `pre` row; after a blocked call, a `blocked` row. |

### 6.2 email-send-guard — `tests/plugins/email_send_guard/`

Guard is dormant (Option A), so tests assert the **state-machine logic in isolation** (import the module, call `_pre_tool_call` directly) — this preserves the reference logic's correctness without needing an email platform.

| Test | Asserts |
|---|---|
| `test_block_email_send_without_draft` | `_pre_tool_call("send_message", {"target":"email:a@b.com","message":"hi"})` → block (Gate 1). |
| `test_full_state_machine_allows_send` | load_draft → show_preview → approve (within TTL) → `_pre_tool_call` returns `None` (allowed). |
| `test_recipient_mismatch_blocks` | Approved for `a@b.com`, send target `c@d.com` → block (Gate 3 recipient bind). |
| `test_approval_expires` | Approve, fast-forward past 15-min TTL → block. |
| `test_long_body_roundtrip` | A >100KB body loads/previews/approves/sends without truncation (the historical bug class). |
| `test_plugin_is_dormant_by_default` | The base `config.yaml` does **not** list `email-send-guard` in `plugins.enabled` (Option A) — proves it's ported-but-off. |

### 6.3 tool-registry-guard — `tests/plugins/tool_registry_guard/`

| Test | Asserts |
|---|---|
| `test_block_pip_install_pandas` | `_pre_tool_call("terminal", {"command":"pip install pandas"})` → block naming `graph-workbook`. |
| `test_block_banned_import_in_write_file` | `write_file` content `import openpyxl` → block. |
| `test_block_inline_python_import` | `terminal` `python -c "import docx"` → block. |
| `test_pre_llm_call_injects_registry_context` | `_pre_llm_call(user_message="x")` returns `{"context": <table>}` containing "TOOL REGISTRY" and a banned-package line. |
| `test_no_false_positive_benign_import` | `write_file` content `import os` → `None`. |
| `test_audit_jsonl_written` | A codegen call appends a JSONL entry with the `violation` flag. |

### 6.4 Cross-plugin wiring tests — `tests/plugins/test_governance_wiring.py`

| Test | Asserts (the bypass rows) |
|---|---|
| `test_all_three_enabled_in_base_config` | B2: base `config.yaml` `plugins.enabled` ⊇ {control-room, tool-registry-guard} (and excludes email-send-guard per Option A). |
| `test_hooks_registered_after_load` | After plugin manager load, `has_hook("pre_tool_call")` and `has_hook("pre_llm_call")` true. |
| `test_block_survives_subagent_path` | B9: a blocked tool is blocked when routed through the subagent call site. |
| `test_config_edit_is_write_protected` | B3: `pre_tool_call("write_file", {"path":"~/.hermes/config.yaml"})` → block (extended write-protection). |

---

## §7 — REQ-04: Implementation task breakdown

Ordered coder tasks. **No two tasks touch the same file.** Each sized for one agent. All target `~/Code/hermes` on branch `factory` (or the P0-1 clean-checkout path).

| ID | Task | Files touched (exclusive) | Depends on | Size |
|---|---|---|---|---|
| **P0-2a** | Re-apply **control-room** plugin from `fable` as a clean commit: `__init__.py` + `plugin.yaml` + all 8 `policies/*.yaml`. Apply CR code-delta: widen write-protection to include `plugins/tool-registry-guard` **and** `~/.hermes/config.yaml`/`plugins.enabled` (BR-4 + B3). Keep `mandatory: true` marker. | `plugins/control-room/**` | P0-1 | M |
| **P0-2b** | Re-apply **tool-registry-guard** from `fable`: `__init__.py` + `plugin.yaml` + `default-registry.yaml`. Set `mandatory: true` in plugin.yaml (parity/R.1 marker). No functional code change. | `plugins/tool-registry-guard/**` | P0-1 | S |
| **P0-2c** | Re-apply **email-send-guard** from `fable` byte-for-byte (`__init__.py` + `plugin.yaml`), Option A (dormant). Optional: `[EMAIL-TRACE]` warning→debug (flag to owner first). Keep `mandatory: true` marker. | `plugins/email-send-guard/**` | P0-1 | S |
| **P0-2d** | **Config seed (BR-1/B2):** add `control-room` and `tool-registry-guard` to `plugins.enabled` in the base `~/.hermes/config.yaml`; add the repo example-config documentation. Do **not** add `email-send-guard` (Option A). Document SAFE_MODE + R.1-dependency caveats. | `~/.hermes/config.yaml`, `docs/` example-config note (NOT any plugin file) | P0-2a, P0-2b, P0-2c | S |
| **P0-2e** | Write the RED acceptance tests (§6.1–6.4). QA-owned, committed RED before P0-2a–c implement (TDD). | `tests/plugins/**` | — (RED first) | M |

**Dependency graph:** P0-2e (RED) → {P0-2a, P0-2b, P0-2c} in parallel (disjoint files) → P0-2d (needs all three on disk) → GREEN. P0-2a is the long pole (policy files + delta).

**File-disjointness check:** control-room / tool-registry-guard / email-send-guard live in separate `plugins/<name>/` trees; P0-2d touches only config + docs; P0-2e touches only `tests/`. No overlap. ✅

**R.1 handoff (out of scope, flagged):** the `mandatory: true` markers left by P0-2a/b/c are the contract R.1's fail-closed loader consumes. P0-2 does not implement fail-closed loading (Bypass B5/B7).

---

## §8 — Open items for adversarial review / verifier

1. **UNVERIFIED fork god-file commit hashes** — `9602c2ecf` (mandatory hook) is scout-reported, not read from `fable` plugin paths. Verifier must confirm the fork diff's non-plugin edits are truly dropped before claiming done.
2. **Option A for email-send-guard** is a judgment call (dormant vs. drop). If the owner wants the reference code out of the tree entirely, switch to "Drop" and rely solely on control-room's email-pattern policies + P1a. Recommendation stands: port dormant.
3. **B3 config write-protection** extends control-room's hardcoded path list to `~/.hermes/config.yaml`. Confirm this doesn't break legitimate operator config edits routed through the agent (it should — operators edit config outside the agent; the agent editing its own allow-list is the threat).
4. **Interim gap B7** (fail-open on load error until R.1) is real and accepted. If R.1 slips, this gap widens — track the P0-2→R.1 ordering.
