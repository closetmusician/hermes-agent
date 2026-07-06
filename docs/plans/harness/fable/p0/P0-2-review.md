# P0-2 Adversarial Review — Governance Plugin Re-Application Design

**Reviewer:** adversarial (investigate-audit, GOVERNANCE_EXEMPT, read-only).
**Design under review:** `docs/plans/harness/fable/p0/P0-2-design.md`.
**Base verified against:** branch `factory`. **Plugin sources:** branch `fable`.
**Verdict: SHIP-WITH-FIXES.** P0 = 0 · P1 = 4 · P2 = 3.

The design's upstream fact-base is unusually solid — every load-bearing claim (F1–F11) checks
out against real code, including the two the requirement map singled out (pre_tool_call call
sites, safe-mode). No false upstream claim ships. The gaps are on the *enforcement* side: the
design restates the fork's O3 hardcoded blocks as "closes the code-rewrite self-escalation" and
"no raw email egress" without re-testing whether those substring-based blocks actually hold on
the clean base. Three of them are trivially evadable, and two acceptance tests are theater
(cannot be RED on the clean base because upstream already enforces them). None is a *new* hole
this design opens — they are inherited weaknesses the design implicitly certifies as sound. Per
the owner's mandate ("must ACTUALLY achieve their intended objectives, not merely be ported"),
those must be dispositioned honestly before coder handoff.

---

## REQ-01 — Upstream fact verification (every claim opened personally)

| Claim | Cited location | Verdict | Actual finding (opened) |
|---|---|---|---|
| F1 `mandatory` is dead | plugins.py `grep mandatory` empty | **CONFIRMED** | `git show factory:hermes_cli/plugins.py \| grep mandatory` → zero hits. |
| F2 standalone opt-in via `plugins.enabled`; key path-derived | plugins.py:241-266, 1406-1440 | **CONFIRMED** | `_get_enabled_plugins()` (241-266) returns `None`/`set()`/`set(...)`. Load gate (1406-1414): `is_enabled = enabled is not None and (lookup_key in enabled or manifest.name in enabled)`. For flat plugin `plugins/control-room/` the key **is** `control-room` (name==key), so seeding either works. |
| F3 bundled backend/platform auto-load, standalone does not | plugins.py:1382-1440 | **CONFIRMED** | 1386 auto-loads `kind==backend`; 1401 defers `kind==platform`; else-branch (1406) gates on enabled. |
| F4 pre_tool_call native, first-block-wins, at :418 & :1037 | plugins.py:2049-2096; tool_executor:418,1037 | **CONFIRMED** | `get_pre_tool_call_block_message` (2049) iterates `hook_results`, returns first `{"action":"block","message":str}` (2091-2096). Fires at tool_executor:419 and :1038. |
| F5 invoke_hook swallows exceptions → tool proceeds | plugins.py:1868-1883 | **CONFIRMED** | invoke_hook (1867-1883): per-callback try/except; on raise → `logger.warning` → no result appended. "raise to fail-closed" does NOT work — must *return* a block dict. |
| F6 skip_pre_tool_call_hook is not a bypass | tool_executor:1030-1060, 1405-1465 | **CONFIRMED** | Block gate runs at :1038 → `_execution_blocked` (:1058) → `pass` (no execution). The `skip=True` at :1413/:1455 is on `handle_function_call` calls reached only in the **non-blocked** spinner/quiet branches; it prevents the inner re-fire, not enforcement. |
| F7 pre_llm_call `{"context":str}` consumed into user message | turn_context:434-485 | **CONFIRMED** | 434-485: results with `r.get("context")` collected into `plugin_user_context`, appended to user message (comment at :434 "not system prompt"). Cache-safe. Extra kwargs (`task_id,turn_id,platform,sender_id`) passed → fork's `**_` absorbs them. |
| F8 email send path gone upstream | gateway/tools grep `email:` empty | **CONFIRMED** | `git grep "email:" factory -- gateway/run.py tools/delegate_tool.py` → empty. `gateway/platforms/email.py` does not exist on factory. |
| F9 all keyed tool names exist | tools/*.py | **CONFIRMED** | `tools/file_tools.py` registers `read_file`(2170)/`write_file`(2171)/`patch`(2172)/`search_files`(2173); `execute_code`(1890); `terminal`(2991); `send_message` present. All exact names. |
| F10 register_* signatures + get_hermes_home | plugins.py:389,527,1109; hermes_constants:55 | **CONFIRMED** | `register_tool(name,toolset,…)` (389), `register_command(name,handler,…)` (527), `register_hook(hook_name,callback)` (1109). `get_hermes_home()` at hermes_constants.py:55. |
| F11 pre_gateway_dispatch fires before auth | gateway/run.py:8679 | **CONFIRMED (with correction)** | 8679-8710: fires for user-originated events before auth; actions are `skip`/`rewrite`/`allow` — it is **inbound** message routing, NOT an outbound egress choke. Design's B8 already states this correctly. |

**No WRONG or UNVERIFIED load-bearing claim.** The one UNVERIFIED item the design itself
flags (fork god-file commit `9602c2ecf`) lives in non-plugin files not readable from `fable`
plugin paths — correctly flagged, not fabricated. Verifier must still confirm the fork diff's
non-plugin edits are truly dropped (design §8.1).

**New fact the design UNDER-states (see F-DELTA below):** there are **four** block-gate call
sites, not two.

### F-DELTA (P2) — block gate has 4 call sites, design names 2

`get_pre_tool_call_block_message` is invoked at `tool_executor.py:419`, `tool_executor.py:1038`,
`model_tools.py:1178`, and `agent_runtime_helpers.py:2101`. All four call the same guard, so
enforcement is not weakened — but the design's F4/§2.2 "fires at :418 and :1037" is incomplete,
and B9's characterization of ":1037 = the subagent path" is imprecise: a delegated child is a
full `AIAgent` (`delegate_tool.py:5` "Spawns child AIAgent instances") whose own turn loop hits
the **standard** gate at :419. The MCP transport (`hermes_tools_mcp_server.py:165`) dispatches
via `handle_function_call(...)` **without** `skip_pre_tool_call_hook`, so `model_tools.py:1174`
fires the gate there too — a positive, but it exposes only a curated tool set (no
write_file/patch/terminal/read_file), so control-room's file/terminal blocks never see MCP
traffic. **Fix:** correct §2.2/F4 to list all four sites; reword B9 to "child agents route
through the standard gate at :419"; note MCP coverage is real but tool-limited.

---

## REQ-02 — Objective-preservation audit (per plugin)

### control-room — objectives O1–O5

| Obj | Design coverage verdict | Finding |
|---|---|---|
| O1 audit every call | **MET** | post/pre audit rows via native hook (F4). |
| O2 YAML policy eval | **MET** | PolicyEngine intact; keyed tools exist (F9). |
| O3 hardcoded egress/self-protection | **PARTIALLY MET — 3 evadable sub-objectives** | See F1-CR, F2-CR, F3-CR below. The design restates O3 verbatim and asserts it "closes the code-rewrite self-escalation that started this whole effort" (§2.1) — that specific claim is **false as written**: the block is substring-based and evadable. |
| O4 fail-closed on internal error | **MET** | Fork's try/except *returns* a block dict (control-room:701-706); F5-safe. BR-2 correctly preserves it. |
| O5 HTTP monitor | **MET** | Unchanged, opt-in. |

### email-send-guard — objectives E1–E3

| Obj | Verdict | Finding |
|---|---|---|
| E1/E2/E3 draft→preview→approve→send | **MOOT, correctly dispositioned** | F8 confirmed: no `email:` platform. Option A (port dormant, not enabled) is sound. The escalation to P1a is honest. |

**But** the "interim no-raw-email-egress" enforcement the design leans on (§3.2: control-room's
email-pattern policies cover the gap until P1a) inherits O3's evadability — see F3-CR (terminal
egress via non-matching commands).

### tool-registry-guard — objectives T1–T3

| Obj | Verdict | Finding |
|---|---|---|
| T1 block banned pip/import | **MET (with known regex limits)** | `_pre_tool_call` scans terminal/write_file/patch. Note it only blocks; obfuscated imports (`__import__("pandas")`) evade — but T1's objective is *steering*, not hard security, so acceptable. Flag, don't block. |
| T2 inject registry into every turn | **MET** | F7 confirmed the `{"context":…}` path; kwargs compatible. |
| T3 audit codegen calls | **MET** | post_tool_call JSONL unchanged. |

---

## REQ-03 — Bypass analysis: rows the design MISSED

The design's B1–B12 table is thorough and its dispositions for B1/B2/B4/B5/B6/B7/B9/B10/B12 are
sound and match the code. The following are **not in the table** or are **mis-dispositioned**:

| # | Bypass | Sev | Reachable? | Design disposition | Correct disposition |
|---|---|---|---|---|---|
| **F1-CR** | **Path-substring evasion of self-protection.** control-room:589 does `"plugins/control-room" in target_path` on the **raw model-supplied** `args["path"]` (tool_executor passes function_args verbatim; no normalization before the gate — confirmed tool_executor:380-419). `write_file(path="__init__.py")` executed from cwd `plugins/control-room/`, or `path="./x/../control-room/__init__.py"` phrased to avoid the literal substring, writes the guard source with the arg string never containing `plugins/control-room`. | **P1** | **Yes** | O3 claimed to "close code-rewrite self-escalation" (§2.1); B4 widening only adds more literal substrings. | **CLOSE before merge or DOWNGRADE the claim.** Either (a) resolve the path (`os.path.realpath`) and compare against the resolved plugins dir, or (b) explicitly document O3 self-protection as "defeatable by relative-path phrasing; true close is R.1 fail-closed + P1a credential wall." The current design *asserts closure it does not deliver*. |
| **F2-CR** | **execute_code import-block evasion.** control-room:604 substring-matches `"import agent"`, `"plugins."`, etc. `__import__("agent")`, `importlib.import_module("gateway")`, `exec("imp"+"ort agent")`, or `getattr(__import__("importlib"),"import_module")("plugins.control_room")` contain none of the blocked substrings. | **P1** | **Yes** | B10 claims O3 "hardcoded block" closes internal-API abuse. | **DOWNGRADE claim / defer to P1a.** Substring matching cannot close this class. Document as best-effort steering; real containment is the execute_code sandbox (`tools/code_execution_tool.py` env-scrubbing) + P1a. Do not represent B10 as CLOSED. |
| **F3-CR** | **terminal file-edit of guard source or config.yaml.** control-room's terminal branch blocks only `outlook-*`/`get-foci-token`/email-regex. `terminal("printf '…' > plugins/control-room/__init__.py")`, `sed -i`, `tee`, or `terminal("echo 'enabled: []' > ~/.hermes/config.yaml")` edits guard source AND the enabled-list with **zero** guard coverage (write_file/patch protection does not apply to `terminal`; upstream file_tools config protection also does not cover terminal — see F-B3). | **P1** | **Yes** | Not dispositioned. B3 only covers write_file/patch to config; self-protection (BR-4) only covers write_file/patch to plugin source. | **CLOSE or DEFER-P1a explicitly.** Add a terminal-command policy/hardcoded block matching redirection/sed/tee/cp targeting `plugins/<guard>/` or `config.yaml`, OR document that terminal-based tampering is out of scope for P0-2 and owned by P1a's egress/credential wall. Silent omission is the problem. |
| **F-B3** | **B3's own RED test is theater (see REQ-04).** Upstream `file_tools.py:_check_sensitive_path` (633-660) **already** blocks write_file/patch to the resolved `~/.hermes/config.yaml` (658). So control-room extending its substring check to config is *redundant for write_file/patch* and the RED test cannot fail on the clean base. | **P1** | n/a | B3 "CLOSE" via control-room extension; RED test `test_config_edit_is_write_protected`. | **Re-scope B3.** The real residual gap is (a) terminal (F3-CR) and (b) substring-vs-resolved-path parity. Reuse/extend upstream `_check_sensitive_path` (which resolves the path — immune to F1-CR-style evasion) rather than adding a second, weaker substring check in control-room. |
| **F-DELTA** | 4 call sites, not 2 (see REQ-01). | **P2** | — | §2.2 lists 2. | Correct the doc. |

**Sound ACCEPTs confirmed:** B1 (F6 verified), B4 (safe-mode = env access agent lacks; verifier
checks launchd plist — good), B7 (interim, escalated in BR-1). **Sound DEFERs:** B5→R.1,
B8/B11-credential-wall→P1a.

---

## REQ-04 — RED-test-plan judgment (real vs theater)

| Test | Verdict | Note |
|---|---|---|
| `test_pre_tool_call_blocks_smtplib_execute_code` | **REAL** | RED on clean base (no plugin), GREEN after. Drives real hook. |
| `test_write_protect_own_source` | **REAL** | Upstream `_SENSITIVE_PATH_PREFIXES` = `/etc /boot /usr/lib/systemd /private/etc /private/var` only — does NOT cover `plugins/`. So this genuinely fails on the clean base. Good. |
| `test_block_get_foci_token` / `test_block_credential_read` | **REAL** | No upstream equivalent. RED-able. |
| `test_fail_closed_on_internal_error` | **REAL** | Directly targets the F5-safe return-not-raise contract — the highest-value test. |
| `test_audit_row_written` | **REAL** | Behavioral. |
| **`test_config_edit_is_write_protected` (§6.4)** | **THEATER** | Upstream file_tools:658 already blocks write_file→config.yaml → this passes on the clean base with **no plugin loaded** → NOT RED-first, violating design §6's own contract. Either re-point it at the terminal path (F3-CR, which IS unguarded) or at path-resolution parity (F1-CR). As written it proves nothing about the plugin. |
| email-send-guard §6.2 suite | **REAL but ISOLATED** | Tests import `_pre_tool_call`/`_handle_approve` directly (pure state, no gateway) — good, and appropriate since the guard is dormant. `test_plugin_is_dormant_by_default` correctly asserts Option A. |
| tool-registry-guard §6.3 suite | **REAL** | `test_pre_llm_call_injects_registry_context` drives the real hook return shape (F7). Good. `test_block_pip_install_pandas` RED-able. |
| `test_all_three_enabled_in_base_config` / `test_hooks_registered_after_load` | **REAL** | Config + registration assertions. |
| `test_block_survives_subagent_path` (B9) | **REAL but mis-targeted** | Should exercise the **standard** gate a child AIAgent uses (:419), not the :1037 render branch the design names (F-DELTA). Assert via `get_pre_tool_call_block_message` directly + a child-agent integration path. |

**Test-importability gap (P2, not in design):** plugins load via
`importlib.util.spec_from_file_location` + `exec_module` (plugins.py:1810-1822) because the dir
names are hyphenated (`control-room`, `email-send-guard`, `tool-registry-guard`) and are **not
importable Python packages**. The §6 tests written as `from control_room import …` /
`ControlRoom()` will `ImportError`. The test harness must load each module by file path (mirror
`spec_from_file_location`) or add a conftest fixture. The design must tell the coder this, or
P0-2e will not even collect.

---

## Required fixes before coder handoff (ranked)

1. **(P1, F1-CR + F-B3)** Replace/augment control-room's substring self-protection and config
   protection with **path-resolution** (`os.path.realpath` vs the resolved guard/config dirs),
   or explicitly downgrade the O3 "closes self-escalation" claim to "best-effort; true close =
   R.1 + P1a." Prefer reusing upstream `_check_sensitive_path` (already resolves paths) over a
   second weaker check.
2. **(P1, F3-CR)** Disposition the terminal-based file-edit vector (redirect/sed/tee to guard
   source or config.yaml) — CLOSE with a terminal policy or DEFER-P1a in writing. It is
   currently un-dispositioned and defeats both BR-4 and B3.
3. **(P1, F2-CR)** Stop representing B10 (execute_code internal-import block) as CLOSED; it is
   substring-evadable. Document as steering + defer real containment to the sandbox/P1a.
4. **(P1, F-B3 test)** Rewrite `test_config_edit_is_write_protected` to target an actually-
   unguarded path (terminal edit), or delete it — as written it is RED-impossible theater.
5. **(P2, F-DELTA)** Correct §2.2/F4/B9 to the four real call sites and the child-agent path.
6. **(P2, test importability)** Instruct P0-2e to load hyphenated-dir plugins via
   `spec_from_file_location`, not `import`.
7. **(P2, T1 note)** Note tool-registry-guard T1 is steering, not security (obfuscated imports
   evade) — acceptable, but say so.

## What is genuinely sound (do not re-litigate)

- The entire F1–F11 upstream fact-base. It is correct; the scout did not err on the load-bearing
  claims. The `mandatory`-is-dead → config-seed → R.1-marker chain (BR-1) is the right shape.
- BR-2 (return-not-raise) is essential and correctly derived from F5.
- Option A for email-send-guard (dormant port) — correct given F8.
- The file-disjoint task breakdown (§7) is clean and parallelizable.
- B1/B4/B5/B7 dispositions are sound.

---

### Verifier handoff
- Confirm fork god-file edits (`9602c2ecf` + any non-plugin diff) are actually absent from the
  P0-2 commits (design §8.1) — not readable from `fable` plugin paths, so must diff the fork.
- Re-run each RED test against the clean base and assert it FAILS (especially the two flagged as
  theater) before accepting P0-2e.
