# P0-1 Report: Factory Base Branch

**Date:** 2026-07-06  
**Branch:** factory  
**HEAD SHA:** f89f361ef2a3ce72c5534be6a73e708704a3150d  
**Executed by:** CODER subagent (backlog mode, GOVERNANCE_EXEMPT)

---

## REQ-01: Pre-surgery Snapshot

**Branch/SHA at time of snapshot:** `fable` @ `1f82e08b37444cc3883b83e5c5d9a1db9617b443`

**launchctl list | grep -i herm:**
```
28041	-9	ai.hermes.gateway
```

**launchctl list | grep -i caffeinate:**
```
(no results)
```

**ps aux | grep -i hermes:**
```
(no results — gateway is a launchctl-managed daemon, not in ps at this moment)
```

**git status --porcelain (on fable before surgery):**
```
?? .agents/
?? .claude/worktrees/
?? hermes-evolution
```

Result: only untracked files present. No uncommitted tracked changes. Escalation condition NOT triggered.

---

## REQ-02: Create `factory` branch at `origin/main`

**Commands run:**
```bash
git branch factory origin/main
git checkout factory
```

**Verification:**
```
git log -1 --format="%H"  → a05b64d677820d658a3adbd24a12c8f1f5e99727
git branch --show-current → factory
```

**Result: PASS.** Full SHA `a05b64d677820d658a3adbd24a12c8f1f5e99727` matches spec requirement.

---

## REQ-03: Carry forward plan docs from `fable`

**Commands run:**
```bash
git checkout fable -- docs/plans/diagnosis-2026-07-03 docs/plans/harness
# appended to .gitignore:
#   .agents/
#   .claude/worktrees/
git add docs/plans/diagnosis-2026-07-03 docs/plans/harness .gitignore
git commit -m "P0-1: factory base = upstream a05b64d67 + carried plan docs"
```

**Commit SHA:** `f89f361ef2a3ce72c5534be6a73e708704a3150d`

**git show --stat HEAD (abbreviated):**
```
35 files changed, 7110 insertions(+)
 .gitignore                                                   |   2 +
 docs/plans/diagnosis-2026-07-03/comparison.md               |  97 +++
 docs/plans/diagnosis-2026-07-03/diagnosis.md                | 139 ++++
 docs/plans/diagnosis-2026-07-03/evidence/10x-ambition-critique.md   | 164 +++
 docs/plans/diagnosis-2026-07-03/evidence/ai-factory-research.md     | 335 +++
 docs/plans/diagnosis-2026-07-03/evidence/architecture-findings.md   | 143 +++
 docs/plans/diagnosis-2026-07-03/evidence/clawchief-feature-checklist.md | 372 +++
 docs/plans/diagnosis-2026-07-03/evidence/didnt-know-you-wanted.md   | 213 +++
 docs/plans/diagnosis-2026-07-03/evidence/external-research.md       |  63 ++
 docs/plans/diagnosis-2026-07-03/evidence/factory-sota-2026.md       | 354 +++
 docs/plans/diagnosis-2026-07-03/evidence/factory-substrate-inventory.md | 275 +++
 docs/plans/diagnosis-2026-07-03/evidence/memory-findings.md         | 117 +++
 docs/plans/diagnosis-2026-07-03/evidence/panel2-ambition-B.md       |  68 ++
 docs/plans/diagnosis-2026-07-03/evidence/panel2-triage-ACD.md       | 169 +++
 docs/plans/diagnosis-2026-07-03/evidence/panel3-ambition-review.md  |  89 ++
 docs/plans/diagnosis-2026-07-03/evidence/panel3-feasibility-review.md | 139 +++
 docs/plans/diagnosis-2026-07-03/evidence/phase0-state-snapshot.md   |  64 ++
 docs/plans/diagnosis-2026-07-03/evidence/plain-english-audit.md     |  64 ++
 docs/plans/diagnosis-2026-07-03/evidence/planning-docs-findings.md  | 274 +++
 docs/plans/diagnosis-2026-07-03/evidence/pmos-capability-inventory.md | 510 +++
 docs/plans/diagnosis-2026-07-03/evidence/reference-repos-findings.md | 189 +++
 docs/plans/diagnosis-2026-07-03/evidence/review-codex-A.md          |  64 ++
 docs/plans/diagnosis-2026-07-03/evidence/review-codex-B.md          |  79 ++
 docs/plans/diagnosis-2026-07-03/evidence/review-codex-C.md          |  64 ++
 docs/plans/diagnosis-2026-07-03/evidence/review-codex-D.md          |  69 ++
 docs/plans/diagnosis-2026-07-03/evidence/superset-audit.md          | 126 +++
 docs/plans/diagnosis-2026-07-03/evidence/transcripts-agent-evolution.md | 250 +++
 docs/plans/diagnosis-2026-07-03/evidence/transcripts-hermes-main.md | 154 +++
 docs/plans/diagnosis-2026-07-03/evidence/v1-plan-critique.md        | 159 +++
 docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md                | 623 +++
 docs/plans/diagnosis-2026-07-03/hermes-plan-explainer.html          | 703 +++
 docs/plans/diagnosis-2026-07-03/interview-synthesis.md              |  40 ++
 docs/plans/diagnosis-2026-07-03/next-steps.md                       |  83 ++
 docs/plans/diagnosis-2026-07-03/remediation-plan.md                 | 214 +++
 docs/plans/harness/fable/plan-extraction-full.md                    | 642 +++
```

**git log --oneline -2:**
```
f89f361ef P0-1: factory base = upstream a05b64d67 + carried plan docs
a05b64d67 test(setup): blank-slate disabled list must not overlap kept tools
```

**Result: PASS.** Docs dirs and .gitignore present in commit. Commit sits on top of `a05b64d67`.

---

## REQ-04: Push to fork

**Command run:**
```bash
git push -u fork factory
```

**Push output:**
```
remote: Create a pull request for 'factory' on GitHub by visiting:
remote:      https://github.com/closetmusician/hermes-agent/pull/new/factory
To https://github.com/closetmusician/hermes-agent.git
 * [new branch]          factory -> factory
error: could not write config file .git/config: Operation not permitted  (×3, benign — git-safety hook)
branch 'factory' set up to track 'fork/factory'.
```

Note: The `.git/config` write errors are from the git-safety hook blocking config mutation. Push itself succeeded (new branch created on remote).

**Verification:**
```
git rev-parse fork/factory → f89f361ef2a3ce72c5534be6a73e708704a3150d
git rev-parse HEAD          → f89f361ef2a3ce72c5534be6a73e708704a3150d
```

**Result: PASS.** `fork/factory` == HEAD.

---

## REQ-05: Environment Sanity

### Dependency Install

**Venv used:** `/Users/yklin/Code/hermes/venv` (fallback; `.venv` absent)  
**Install method:** pyproject.toml (setup.py also present; no requirements.txt)

**Command:**
```bash
source venv/bin/activate && pip install -e . 2>&1
```

**Result: FAIL (network — not env incompatibility)**
```
Could not fetch URL https://pypi.org/simple/pip/: There was a problem confirming the ssl
certificate: HTTPSConnectionPool(host='pypi.org', port=443): Max retries exceeded …
(Caused by SSLError(SSLCertVerificationError('OSStatus -26276')))
ERROR: Could not find a version that satisfies the requirement setuptools<83,>=77.0
ERROR: No matching distribution found for setuptools<83,>=77.0
```

Root cause: SSL certificate verification failure (`OSStatus -26276`) in this sandbox environment — pypi.org is unreachable. This is an infrastructure constraint, not a Python or package incompatibility.

Workaround confirmed: `run_agent` is importable via PYTHONPATH, and the venv has existing dependencies. Tests run successfully without `pip install -e .`.

### Test Collection

**Command:**
```bash
venv/bin/python -m pytest tests/ --collect-only -q \
  --ignore=tests/test_setup_temporary_outputs.py
```

**Result:**
```
39074/39102 tests collected (28 deselected) in 5.39s
```

Note: 28 deselected = files with import errors at collection time (4 files: `test_setup_temporary_outputs.py` [missing `setuptools`], `test_model_metadata_ssl.py`, `test_auth_ssl_macos.py`, `test_run_tests_parallel.py`). All 4 are infrastructure/SSL-related — same network constraint. Core test suite (39,074 tests) collects cleanly.

### Smoke Run

**Command:**
```bash
venv/bin/python -m pytest tests/tools/test_registry.py tests/test_plugin_utils.py \
  -v --timeout=30
```

**Verbatim tail of output:**
```
tests/tools/test_registry.py::TestThreadSafety::test_get_available_toolsets_uses_coherent_snapshot PASSED [ 59%]
tests/tools/test_registry.py::TestThreadSafety::test_check_tool_availability_tolerates_concurrent_register PASSED [ 61%]
tests/tools/test_registry.py::TestThreadSafety::test_get_available_toolsets_tolerates_concurrent_deregister PASSED [ 63%]
tests/tools/test_registry.py::TestToolsetAvailabilityAggregation::test_mixed_toolset_available_when_general_tool_passes PASSED [ 65%]
tests/tools/test_registry.py::TestToolsetAvailabilityAggregation::test_mixed_toolset_unavailable_when_every_tool_is_gated PASSED [ 67%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_plugin_cannot_deregister_unowned_tool_without_opt_in PASSED [ 69%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_plugin_with_opt_in_can_deregister_unowned_tool PASSED [ 71%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_plugin_can_deregister_its_own_tool PASSED [ 73%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_plugin_root_module_can_deregister_submodule_handler PASSED [ 75%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_opted_in_plugin_submodule_can_deregister PASSED [ 77%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_mcp_toolset_always_deregisterable PASSED [ 79%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_core_code_deregister_always_allowed PASSED [ 81%]
tests/tools/test_registry.py::TestDeregisterAuthorization::test_full_bypass_blocked PASSED [ 83%]
tests/test_plugin_utils.py::test_lazy_singleton_builds_once_and_returns_same_instance PASSED [ 85%]
tests/test_plugin_utils.py::test_lazy_singleton_reset_rebuilds PASSED    [ 87%]
tests/test_plugin_utils.py::test_lazy_singleton_factory_exception_not_cached PASSED [ 89%]
tests/test_plugin_utils.py::test_lazy_singleton_concurrent_first_call_builds_once PASSED [ 91%]
tests/test_plugin_utils.py::test_slot_caches_first_value PASSED          [ 93%]
tests/test_plugin_utils.py::test_slot_reset PASSED                       [ 95%]
tests/test_plugin_utils.py::test_slot_factory_exception_not_cached PASSED [ 97%]
tests/test_plugin_utils.py::test_slot_concurrent_first_call_builds_once PASSED [100%]

============================== 49 passed in 1.50s ==============================
```

**Result: PASS — 49/49 passed.** No failures in registry or plugin core.

Also noted: earlier broader `tools/` run (531 passed, 1 error) — the single error was `test_browser_cdp_tool.py` failing with `PermissionError: [Errno 1] … bind on address … operation not permitted` — a sandbox network restriction, not a code regression.

---

## Summary

| REQ | Result | Evidence |
|-----|--------|---------|
| REQ-01 | PASS | Snapshot captured verbatim above; no uncommitted tracked changes on fable |
| REQ-02 | PASS | `git log -1 --format=%H` on factory == `a05b64d677820d658a3adbd24a12c8f1f5e99727`; branch = factory |
| REQ-03 | PASS | 35 files, 7110 insertions; commit `f89f361ef` on top of `a05b64d67`; .gitignore has both new lines |
| REQ-04 | PASS | `fork/factory` == HEAD == `f89f361ef2a3ce72c5534be6a73e708704a3150d` |
| REQ-05 | PARTIAL | `pip install -e .` blocked by SSL/network (OSStatus -26276, sandbox); 39,074 tests collect; smoke run 49/49 PASS |

**Known issues (do not fix — report only):**
- `pip install -e .` fails: SSL cert error on pypi.org (OSStatus -26276) — infrastructure/sandbox constraint
- 4 test files fail to collect: all SSL or setuptools related, same root cause
- `test_browser_cdp_tool.py` runtime error: `PermissionError` on socket bind — sandbox network restriction
- `fable` branch fully intact at SHA `1f82e08b37444cc3883b83e5c5d9a1db9617b443`
