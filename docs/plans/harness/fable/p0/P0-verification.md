# P0 Verification — Independent Re-Run

**Verifier role:** GOVERNANCE_EXEMPT verifier. Did not produce this work. Re-ran every claim.
**Repo:** /Users/yklin/Code/hermes · **Branch:** factory · **Upstream base:** a05b64d67
**Date:** 2026-07-06

**Method:** Every "tests pass" claim = I ran pytest myself. Every "loads" claim = I ran the
plugin loader against `~/.hermes/config.yaml`. Every file/number claim = I opened the file /
ran the script. Producer reports were treated as leads, not proof.

---

## Overall Verdict: **PASS-WITH-STAGED**

The P0 exit gate is met on everything verifiable in this sandbox. The only items not
directly verified are STAGED-BY-DESIGN (need a Terminal login session or an OpenRouter
secret) and are correctly labeled as such by the producers. **One real defect found:** the
DORMANT email-send-guard suite has 1 failing test the P0-2c report claimed was GREEN —
non-blocking for the P0 gate (that plugin is intentionally off and superseded by P1a), but
the report's "GREEN" claim is CONTRADICTED and should be corrected.

---

## Claim Table

| Claim (≤15 words) | Where | Verdict | Evidence |
|---|---|---|---|
| factory tip descends from upstream a05b64d67 | `git merge-base --is-ancestor` | VERIFIED | exit 0: "a05b64d67 is ancestor of factory" |
| Only 12 commits sit on top of a05b64d67 | `git log a05b64d67..factory` | VERIFIED | 12 commits, all named P0-* / plan docs |
| factory does NOT carry the 5000-commit fork drift | `git rev-list --count` | VERIFIED | factory=14574 = origin/main 14563 + 12 P0; a05b64d67 ∈ origin/main |
| P0-2 commits touch only plugin/test/doc paths | `git show --name-only` ×4 | VERIFIED | P0-2a/b/c clean; P0-2d also adds cli-config.yaml.example (expected seed template) |
| God-file (plugins.py/run_agent/cli) untouched | `git log …-- <godfiles>` | VERIFIED | zero commits touch them |
| fable branch intact at original SHA | `git rev-parse fable` | VERIFIED | 1f82e08b3… = exact SHA in P0-1-report.md:12,246 |
| Remotes: origin=NousResearch, fork=closetmusician | `git remote -v` | VERIFIED | origin fetch=NousResearch; fork push=closetmusician |
| tests/ci/test_probe.py passes | pytest re-run | VERIFIED | 22 passed |
| tests/test_factory_health.py passes | pytest re-run | VERIFIED | 19 passed |
| tests/plugins/control_room/ passes | pytest re-run | VERIFIED | 40 passed |
| tests/plugins/tool-registry-guard/ passes | pytest re-run | VERIFIED | 31 passed |
| tests/plugins/email_send_guard/ ALL pass | pytest re-run | **CONTRADICTED** | 15 passed, **1 FAILED** (see BLOCKING-adjacent below) |
| control-room + tool-registry-guard load; email-send-guard does not | live loader | VERIFIED | control-room enabled=True/module loaded; tool-registry-guard same; email-send-guard enabled=False/module=None |
| `_get_enabled_plugins()` = exactly the two governance plugins | live loader | VERIFIED | returns `{'control-room','tool-registry-guard'}` |
| F1-CR fix is realpath, not substring | control-room/__init__.py | VERIFIED | `_resolve_guard_dirs`/`_is_under_guard_dir` use `os.path.realpath`+`relative_to` (L473-505,689-742) |
| Hooks RETURN block dicts, don't raise | grep hook bodies | VERIFIED | `{"action":"block",…}` returns; zero `raise` in hook functions (both plugins) |
| probe.py exits 0 on healthy, writes capabilities.json | ran `probe.py` | VERIFIED | true exit code 0; "Wrote capabilities.json" |
| GLM-5.2 recorded with live_catalog:false (phantom) | capabilities.json | VERIFIED | `zhipuai/GLM-5.2` → live_catalog:false, ping_ok:false |
| Phantom-vs-real catalog distinction works | build_model_entry sim | VERIFIED | with catalog present: GLM-5.2→false, glm-5→true |
| Breaking a capability → non-zero exit | compute_exit_code() | VERIFIED | `[]`→0, `['claude_missing']`→1 |
| compute.md has numeric tasks/night + shown arithmetic | compute.md | VERIFIED | 55,683 tok/task (n=5 real runs); binding ceiling **11 Sonnet/night** with full division shown |
| factory_health.py prints 4 sections, real values, exit 0 | ran script | VERIFIED | [1]Providers REACHABLE w/ latency; [2]caffeinate/disk/load; [3]workers; [4]tick; exit 0 |
| 3 P0 plists exist and are valid | `plutil -lint` | VERIFIED | caffeinate/prewarm/launchd-probe all "OK" |
| config: discord + whatsapp disabled | config.yaml | VERIFIED | both `enabled: false` (L340,355) |
| config: plugins.enabled = [control-room, tool-registry-guard] | config.yaml | VERIFIED | L499-501 exactly those two; email-send-guard omitted |
| Zero discord/whatsapp retry noise after disable | errors.log tail | VERIFIED | recent log (21:28+) shows only telegram network retries; discord/whatsapp entries all pre-disable (02:05-02:31) |
| launchd services bootstrapped in launchctl | `launchctl list` | UNVERIFIED (STAGED) | none loaded — bootstrap needs Terminal login session (sandbox blocks launchctl write); producer labels STAGED |
| Live OpenRouter catalog confirmation per model | capabilities.json | UNVERIFIED (STAGED) | all models error:key_missing — OPENROUTER_API_KEY absent; mechanism proven via simulation above |

---

## Counts

- **VERIFIED:** 26
- **UNVERIFIED (STAGED-by-design):** 2 (launchd bootstrap needs Terminal; live OpenRouter catalog needs key)
- **CONTRADICTED:** 1 (email-send-guard suite: 1 failing test vs P0-2c "GREEN" claim)

---

## BLOCKING / Attention Items

**None block the P0 exit gate.** One correctness item to fix (non-gating):

1. **email-send-guard test failure (CONTRADICTED, non-gating).**
   `TestOneTimeApprovalConsumption::test_approval_consumed_after_successful_send` FAILS.
   Root cause: `_post_tool_call` (plugins/email-send-guard/__init__.py:396-403) treats a
   `result=""` (empty-string success) as a failure — `json.loads("")` raises
   `JSONDecodeError`, caught at L401-403 setting `send_failed=True`, so the one-time approval
   is never consumed. The P0-2c report claims the suite is GREEN ("full test suite is
   GREEN-ready"); it is 15 passed / 1 failed. This plugin is **dormant** (not in
   `plugins.enabled`, module not loaded — confirmed) and explicitly superseded by the P1a
   broker, so it does not gate P0. But it is a genuine latent bug in ported code and the
   "GREEN" claim is false. Recommend: fix the empty-result classification OR fix the test to
   pass a valid JSON success payload, and correct the P0-2c report.

**STAGED (correctly deferred, not failures):**
- launchctl bootstrap of the 3 plists — needs a Terminal login session; plists are valid
  (`plutil -lint` OK). Owner runs the 3 `launchctl bootstrap` commands (P0-6-7-report §load note).
- Live OpenRouter model catalog confirmation — needs `OPENROUTER_API_KEY`. The probe's
  phantom-detection mechanism is verified by simulation (real catalog → GLM-5.2 false /
  glm-5 true); it will run live once the key is present.

---

## Notes for the reader

- **REQ-01 nuance:** the task's "5000-commit fork history" concern refers to the OLD
  `feat/governance-plugins` fork drift. factory does NOT contain it — factory = clean upstream
  (origin/main) + 12 P0 commits. The 14574 total-commit count is legitimate upstream history,
  not fork drift.
- `~/.hermes/config.yaml` is machine-local (not committed) — expected; P0-9 report notes this.
- Hook-callback count showed 0 against the bare `PluginManager` because callbacks wire into the
  runtime `PluginContext` at gateway startup, not the bare manager; the authoritative load
  signal is per-plugin `enabled` + `module loaded`, which I verified directly.
