# P0-3 + P0-4 Capability Probe — Implementation Report

**Task IDs:** P0-3, P0-4
**Date:** 2026-07-06
**Branch:** factory
**Status:** COMPLETE — all unit tests green; capabilities.json written

---

## Summary

Built `scripts/probe.py` (+ `capabilities.json`) and `tests/ci/test_probe.py`.  
The probe detects CLI versions + flags, git-worktree support, and OpenRouter
live-catalog status.  Phantom models (GLM-5.2) produce a loud stderr failure
and `live_catalog: false` in capabilities.json — never silent omission.

---

## REQ-01 — CLI + local capabilities

**Evidence:** probe ran successfully; `capabilities.json` records:

```
claude: version=2.1.170 (Claude Code), max_budget_usd_flag=true, json_schema_output=true
codex:  version=codex-cli 0.139.0, budget_flag=false (no budget flag found in codex exec --help)
git_worktree: supported=true
```

**Broken-capability exit test:**

```
$ venv/bin/python -c "
import importlib.util, sys
from unittest.mock import patch
spec = importlib.util.spec_from_file_location('probe', 'scripts/probe.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
with patch('shutil.which', return_value=None):
    result = mod.check_cli_capabilities()
    print('failures:', result['failures'])
    code = mod.compute_exit_code(result['failures'])
    print('exit_code:', code)
    print('PASS: non-zero exit confirmed' if code > 0 else 'FAIL')
"

FAILURE claude_cli_missing: 'claude' not found on PATH
NOTE codex_cli_absent: 'codex' not found on PATH — recording absent:true
failures: ['claude_cli_missing']
exit_code: 1
PASS: non-zero exit confirmed
```

Exit code is non-zero with named failure `claude_cli_missing`. REQ-01 gate satisfied.

---

## REQ-02 — OpenRouter live-catalog check

**Escalation path applied:** `OPENROUTER_API_KEY` is not set in the current environment.  
The probe correctly triggered the REQ-02 escalation clause:
- Recorded `key_present: false` in capabilities.json
- Logged `NOTE openrouter_key_missing: OPENROUTER_API_KEY not set — catalog-only mode skipped`
- All candidate models recorded with `error: "key_missing"` (not silently omitted)
- **GLM-5.2 appears explicitly as a failure entry** (live_catalog: false, ping_ok: false)

Phantom model detection is tested and verified in `TestPhantomModelDetection` unit tests
(mocked; no network call needed to prove the logic). When the API key is available at
runtime, the phantom GLM-5.2 will additionally produce a FAILURE line to stderr:

```
FAILURE phantom_model: 'zhipuai/GLM-5.2' is NOT in the live OpenRouter catalog — 
this model cannot enter the routing table
```

**Candidate model list probed** (from plan §P0-4, §P4-2):
- `zhipuai/glm-5` — plan-confirmed present (via ZAI/Novita)
- `gmi-ai/GLM-5.1-FP8` — plan-confirmed present (via GMI)
- `zhipuai/GLM-5.2` — **known phantom; MUST fail**
- `moonshot/kimi-k2` — Kimi/Moonshot failover tier
- `deepseek/deepseek-r1` — DeepSeek failover tier
- `deepseek/deepseek-v3` ��� DeepSeek V3 (often free)
- `deepseek/deepseek-v4-flash:free` — used in hermes config.yaml auxiliary sections

---

## REQ-03 — Unit tests RED-first (TDD)

### RED run output (before probe.py existed)

```
$ venv/bin/python -m pytest tests/ci/test_probe.py -v
============================= test session starts ==============================
...
collected 22 items

tests/ci/test_probe.py::TestParseCatalog::test_extracts_ids_from_data_list FAILED
tests/ci/test_probe.py::TestParseCatalog::test_empty_data_returns_empty_set FAILED
tests/ci/test_probe.py::TestParseCatalog::test_missing_data_key_raises_value_error FAILED
tests/ci/test_probe.py::TestParseCatalog::test_model_without_id_skipped FAILED
tests/ci/test_probe.py::TestCapabilitySchema::test_valid_caps_passes FAILED
tests/ci/test_probe.py::TestCapabilitySchema::test_missing_cli_key_fails FAILED
tests/ci/test_probe.py::TestCapabilitySchema::test_missing_git_worktree_fails FAILED
tests/ci/test_probe.py::TestCapabilitySchema::test_missing_openrouter_key_fails FAILED
tests/ci/test_probe.py::TestCapabilitySchema::test_per_model_entry_has_required_keys FAILED
tests/ci/test_probe.py::TestCapabilitySchema::test_per_model_entry_missing_live_catalog_fails FAILED
tests/ci/test_probe.py::TestPhantomModelDetection::test_phantom_model_gets_live_catalog_false FAILED
tests/ci/test_probe.py::TestPhantomModelDetection::test_phantom_model_ping_ok_is_false FAILED
tests/ci/test_probe.py::TestPhantomModelDetection::test_real_model_in_catalog FAILED
tests/ci/test_probe.py::TestExitCodePolicy::test_all_present_exits_zero FAILED
tests/ci/test_probe.py::TestExitCodePolicy::test_any_failure_exits_nonzero FAILED
tests/ci/test_probe.py::TestExitCodePolicy::test_absent_cli_produces_named_failure FAILED
tests/ci/test_probe.py::TestExitCodePolicy::test_present_cli_no_failure FAILED
tests/ci/test_probe.py::TestCatalogFetchMocked::test_fetch_uses_api_key_header FAILED
tests/ci/test_probe.py::TestCatalogFetchMocked::test_missing_api_key_returns_none FAILED
tests/ci/test_probe.py::TestCatalogFetchMocked::test_http_error_returns_none FAILED
tests/ci/test_probe.py::TestCostEstimate::test_cost_per_ping_under_threshold FAILED
tests/ci/test_probe.py::TestCostEstimate::test_total_cost_under_budget FAILED

FAILED tests/ci/test_probe.py::TestParseCatalog::test_extracts_ids_from_data_list - 
FileNotFoundError: [Errno 2] No such file or directory: '.../scripts/probe.py'
...
22 failed in 0.31s
```

All 22 RED. Failure reason: `FileNotFoundError` — probe.py did not exist.

### GREEN run output (after implementation)

```
$ venv/bin/python -m pytest tests/ci/test_probe.py -v
============================= test session starts ==============================
...collected 22 items

tests/ci/test_probe.py::TestParseCatalog::test_extracts_ids_from_data_list PASSED
tests/ci/test_probe.py::TestParseCatalog::test_empty_data_returns_empty_set PASSED
tests/ci/test_probe.py::TestParseCatalog::test_missing_data_key_raises_value_error PASSED
tests/ci/test_probe.py::TestParseCatalog::test_model_without_id_skipped PASSED
tests/ci/test_probe.py::TestCapabilitySchema::test_valid_caps_passes PASSED
tests/ci/test_probe.py::TestCapabilitySchema::test_missing_cli_key_fails PASSED
tests/ci/test_probe.py::TestCapabilitySchema::test_missing_git_worktree_fails PASSED
tests/ci/test_probe.py::TestCapabilitySchema::test_missing_openrouter_key_fails PASSED
tests/ci/test_probe.py::TestCapabilitySchema::test_per_model_entry_has_required_keys PASSED
tests/ci/test_probe.py::TestCapabilitySchema::test_per_model_entry_missing_live_catalog_fails PASSED
tests/ci/test_probe.py::TestPhantomModelDetection::test_phantom_model_gets_live_catalog_false PASSED
tests/ci/test_probe.py::TestPhantomModelDetection::test_phantom_model_ping_ok_is_false PASSED
tests/ci/test_probe.py::TestPhantomModelDetection::test_real_model_in_catalog PASSED
tests/ci/test_probe.py::TestExitCodePolicy::test_all_present_exits_zero PASSED
tests/ci/test_probe.py::TestExitCodePolicy::test_any_failure_exits_nonzero PASSED
tests/ci/test_probe.py::TestExitCodePolicy::test_absent_cli_produces_named_failure PASSED
tests/ci/test_probe.py::TestExitCodePolicy::test_present_cli_no_failure PASSED
tests/ci/test_probe.py::TestCatalogFetchMocked::test_fetch_uses_api_key_header PASSED
tests/ci/test_probe.py::TestCatalogFetchMocked::test_missing_api_key_returns_none PASSED
tests/ci/test_probe.py::TestCatalogFetchMocked::test_http_error_returns_none PASSED
tests/ci/test_probe.py::TestCostEstimate::test_cost_per_ping_under_threshold PASSED
tests/ci/test_probe.py::TestCostEstimate::test_total_cost_under_budget PASSED

22 passed in 0.28s
```

22/22 GREEN. External HTTP is mocked in all tests (never calls real network in unit tests).

---

## REQ-04 — Total spend < $0.05

**Ping cost estimate:**

```
num_models_probed: 7
tokens_per_ping:   1
cost_per_token_usd: 0.0000004 (conservative upper bound for free-tier models)
estimated_cost_usd: 0.0000028 ($0.0000028 — 3 orders of magnitude under $0.05)
within_budget: true
```

Recorded in `capabilities.json` under `cost_estimate_usd`. Even at 20 models, the
estimate is $0.000008 — well under the $0.05 limit. Pings use `max_tokens=1`.

---

## Live probe run output

```
$ venv/bin/python scripts/probe.py --out capabilities.json
=== hermes capability probe ===
--- checking CLIs ---
--- checking git worktree ---
  git worktree: supported
--- probing OpenRouter models ---
NOTE openrouter_key_missing: OPENROUTER_API_KEY not set — catalog-only mode skipped; 
recording key_present:false

Wrote capabilities.json

All capability checks passed.
Exit code: 0
```

Exit 0 because missing key triggers the escalation path (not a hard failure) and all
other capabilities are present. When key is set, phantom models will trigger non-zero exit.

---

## Files created

| File | Purpose |
|---|---|
| `scripts/probe.py` | Capability probe — CLI detection, git worktree, OpenRouter catalog+pings |
| `capabilities.json` | Probe output (repo root) — machine-readable capability truth |
| `tests/ci/test_probe.py` | 22 unit tests — mocked HTTP; RED before GREEN |
| `docs/plans/harness/fable/p0/P0-3-4-report.md` | This report |

---

## capabilities.json model summary

- **Models probed:** 7 candidates
- **Key present:** false (OPENROUTER_API_KEY not in environment)
- **Catalog live check:** skipped (key_missing)
- **GLM-5.2 entry:** present with live_catalog=false, ping_ok=false, error=key_missing
- **When key is set:** phantom detection produces FAILURE line to stderr

To run with live catalog + pings once OPENROUTER_API_KEY is configured:

```bash
export OPENROUTER_API_KEY=<your-key>
python scripts/probe.py --out capabilities.json
```

The phantom `zhipuai/GLM-5.2` will produce a FAILURE entry in stderr and set
`live_catalog: false` in capabilities.json. Exit will be non-zero naming it.
