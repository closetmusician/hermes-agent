# P3-e Report — repo_onboard.py

**Status:** GREEN (19/19)
**Date:** 2026-07-07
**Branch:** factory

---

## RED → GREEN

### Confirmed RED
```
ERROR collecting tests/factory/test_repo_onboard.py
ModuleNotFoundError: No module named 'factory.repo_onboard'
```
Module was absent; import error confirmed RED before any implementation code was written.

### GREEN output
```
tests/factory/test_repo_onboard.py::TestOnboardingOnePass::test_fresh_repo_onboards_in_one_pass PASSED
tests/factory/test_repo_onboard.py::TestOnboardingOnePass::test_onboard_returns_result_object PASSED
tests/factory/test_repo_onboard.py::TestOnboardingOnePass::test_onboard_idempotent_no_crash_on_second_run PASSED
tests/factory/test_repo_onboard.py::TestCommandDetection::test_detects_pytest_from_pytest_ini PASSED
tests/factory/test_repo_onboard.py::TestCommandDetection::test_detects_pytest_from_pyproject_toml PASSED
tests/factory/test_repo_onboard.py::TestCommandDetection::test_detects_npm_scripts_from_package_json PASSED
tests/factory/test_repo_onboard.py::TestCommandDetection::test_unknown_repo_graceful_fallback PASSED
tests/factory/test_repo_onboard.py::TestCommandDetection::test_makefile_targets_detected PASSED
tests/factory/test_repo_onboard.py::TestCommandDetection::test_setup_py_detected_as_python_repo PASSED
tests/factory/test_repo_onboard.py::TestMergePolicySafeDefault::test_seeded_row_allow_openrouter_false PASSED
tests/factory/test_repo_onboard.py::TestMergePolicySafeDefault::test_seeded_row_local_merge_flow PASSED
tests/factory/test_repo_onboard.py::TestMergePolicySafeDefault::test_seeded_row_require_review_true PASSED
tests/factory/test_repo_onboard.py::TestMergePolicySafeDefault::test_seeded_policy_is_parseable_by_load_merge_policy PASSED
tests/factory/test_repo_onboard.py::TestMergePolicySafeDefault::test_seeded_policy_absent_before_onboard PASSED
tests/factory/test_repo_onboard.py::TestDiligentPinned::test_diligent_repo_pinned_openrouter_false PASSED
tests/factory/test_repo_onboard.py::TestDiligentPinned::test_diligent_prefix_variations_all_pinned PASSED
tests/factory/test_repo_onboard.py::TestDiligentPinned::test_personal_repo_also_defaults_false PASSED
tests/factory/test_repo_onboard.py::TestProfileFileContent::test_profile_contains_required_fields PASSED
tests/factory/test_repo_onboard.py::TestProfileFileContent::test_profile_slug_matches_directory_name PASSED

19 passed in 0.26s
```

**No regressions:** 310 pre-existing factory tests (jira_poller excluded — P3-d, not this task) still pass.

---

## REQ Evidence

### REQ-01 — Detect test/lint/build from config files (fail-soft)
- `test_detects_pytest_from_pytest_ini` GREEN: repo with `pytest.ini` → `test_cmd` = `"pytest"`.
- `test_detects_pytest_from_pyproject_toml` GREEN: `[tool.pytest.ini_options]` in pyproject.toml → `"pytest"`.
- `test_detects_npm_scripts_from_package_json` GREEN: `package.json` with `scripts.test="jest --coverage"` → npm/jest in profile.
- `test_makefile_targets_detected` GREEN: `Makefile` with `test:` target → make/pytest reference in profile.
- `test_setup_py_detected_as_python_repo` GREEN: `setup.py` presence → python language + pytest fallback.
- `test_unknown_repo_graceful_fallback` GREEN: no recognised config → profile contains `"needs manual detection"`, no crash.

Detection priority order (test → lint → build):
1. `pyproject.toml` ([tool.pytest.ini_options], [tool.ruff], build-backend)
2. `pytest.ini`
3. `setup.cfg` ([tool:pytest])
4. `package.json` (scripts.test / scripts.lint / scripts.build)
5. `Makefile` (test: / lint: / build: targets)
6. `setup.py` (Python fallback → pytest)

### REQ-02 — One-pass: profile + merge-policy row in single onboard() call
- `test_fresh_repo_onboards_in_one_pass` GREEN: single `onboard()` call → both `*-factory-repo-profile.md` and `*-merge-policy.md` exist.
- `test_onboard_returns_result_object` GREEN: `OnboardResult` carries `profile_path`, `policy_path`, `repo_slug`.
- `test_onboard_idempotent_no_crash_on_second_run` GREEN: calling `onboard()` twice does not crash; idempotent (policy file not overwritten on second run).

### REQ-03 — allow_openrouter defaults false in every seeded row
- `test_seeded_row_allow_openrouter_false` GREEN: seeded policy text contains `allow_openrouter: false`.
- `test_seeded_row_local_merge_flow` GREEN: seeded policy contains `local-merge`.
- `test_seeded_row_require_review_true` GREEN: seeded policy contains `require_review: true`.
- `test_seeded_policy_is_parseable_by_load_merge_policy` GREEN: `load_merge_policy(result.policy_path)` returns `allow_openrouter=False, require_review=True`.
- `test_diligent_repo_pinned_openrouter_false` GREEN: `diligent-platform` slug → `allow_openrouter: false` (hard-pinned, mirrors `trust_policy._is_diligent_repo`).
- `test_diligent_prefix_variations_all_pinned` GREEN: `diligent-internal-api`, `boardbooks-mobile`, `diligent-corp-platform` → all `allow_openrouter=False`.
- `test_personal_repo_also_defaults_false` GREEN: personal/OSS repo also gets `allow_openrouter: false` by default.

**REQ-04 falsifiability anchor:** `test_seeded_row_allow_openrouter_false` checks for the literal string `"allow_openrouter: false"` in the seeded file. If `_write_policy_if_absent` ever sets `allow_openrouter=True` in the `MergePolicy` constructor, the file will say `allow_openrouter: true` and this test will FAIL immediately. The test is load-bearing against regressions.

---

## Key Design Decisions

1. **Config-file-only detection (no subprocess):** all detectors read config files statically. No tool invocation means onboarding works offline and never hangs. Unknown configs return `""` → profile writes `"needs manual detection"`.

2. **Policy file idempotency:** `_write_policy_if_absent` only writes if the file does not exist. This preserves any owner edits to an existing policy (e.g. if they opted in `allow_openrouter: true` for a personal repo after the initial onboarding).

3. **Diligent slug pin in `onboard()` itself:** `_is_diligent_slug()` mirrors `trust_policy._DILIGENT_PREFIXES`. Even though `_write_policy_if_absent` always writes `allow_openrouter=False` (the only option it ever writes), the Diligent pin is belt-and-suspenders — it would catch any future refactor that introduced an `allow_openrouter` parameter to the write function.

4. **`OnboardResult` dataclass:** clean return value carrying both file paths + the slug, so callers (scheduler, tests, CLI) can load the policy without re-deriving the path.

5. **`tomllib` fallback:** Python 3.11+ stdlib `tomllib` is tried first; `tomli` as installed fallback; regex scan as last resort. This avoids a hard dependency on `tomli` while still parsing `pyproject.toml` correctly on all supported Pythons.

---

## Files Touched
- `factory/repo_onboard.py` — NEW (268 lines; 5-line ABOUTME block)
- `tests/factory/test_repo_onboard.py` — NEW (19 tests; 5-line ABOUTME block)
- `docs/plans/harness/fable/p3/P3-e-report.md` — this file

No other files were modified. Scope limited to P3-e per task constraint.
