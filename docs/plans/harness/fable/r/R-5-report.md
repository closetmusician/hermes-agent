# R-5 Report: Lane State — Degraded/Paused Visibility in factory_health

**Task:** Surface a paused/degraded lane state in the `factory_health.py` health output. A lane whose auth failed enters `degraded`, stops retrying (via R-4's exit mechanism), and clears back to `ok` on re-auth. This makes R-4's re-auth signal visible in the one-glance health command.

---

## REQ-01 — Lane-state reporting added to factory_health.py

**New section [5] Lane State** added to `scripts/factory_health.py`.

Reads the R-4 re-auth marker at `~/Code/pm_os/state/reauth-needed.json` (R-4 contract path, resolved robustly at import time as `DEFAULT_LANE_MARKER_PATH`).

Three read-contract cases (matching R-4 §REQ-04):
- File absent → `status: ok`, `degraded: []`
- File = `{}` (cleared) → `status: ok`, `degraded: []`
- File contains `{"lane": ..., "expiry_type": ..., ...}` → `status: degraded`, `degraded: [{lane, expiry_type, reason, action_needed}]`

Sample output when marker present:
```
[5] Lane State
  teams-chat: DEGRADED [sso-session] — re-auth needed: teams-chat — run /pm-login
    reason: SSO session expired, interactive re-login required
```

Sample output when absent/cleared:
```
[5] Lane State
  all lanes: ok
```

**Marker path used:** `~/Code/pm_os/state/reauth-needed.json` — derived from R-4 report §REQ-04 which states `writeReauthNeeded()` writes to `path.join(stateDir, 'reauth-needed.json')` where `stateDir = path.join(REPO_ROOT, 'state')` and `REPO_ROOT` is the pm_os repo root at `~/Code/pm_os`. Confirmed by `ls ~/Code/pm_os/state/reauth-needed.json` (2B = `{}` cleared state present on disk).

---

## REQ-02 — Auto-enter degraded on auth failure; auto-clear on re-auth

**Degraded → non-healthy exit code:**
- `compute_exit_code()` now checks `sections["lanes"]["status"] == "degraded"` and sets `has_network_error = True` (exit 1, not exit 2 — auth failure is attributable, not a hermes bug).
- A degraded lane is NOT counted as healthy; exit 0 requires `lanes.status == "ok"`.

**Auto-clear on re-auth:**
- R-4 calls `clearReauthNeeded()` at all 6 successful exit points in `ensure-tokens.js`, which writes `{}` to the file.
- `check_lane_state()` treats `{}` (no `lane` field) as `ok` — the lane returns to healthy on next `factory_health.py` run after re-auth.

**Verified by tests:**
- `test_exit_code_1_when_lane_degraded` — marker present → exit 1
- `test_exit_code_0_when_lane_cleared` — marker `{}` → exit 0

---

## REQ-03 — Connector-stops-retrying assertion

R-4 ensures a degraded lane is NOT retrying:
- `scheduledAbort()` in `ensure-tokens.js` writes the marker then calls `process.exit(SCHEDULED_EXIT_CODE)` (exit 10).
- The `run-morning.js` LLM preflight aborts cleanly on exit 10.
- No retry loop exists for exit 10; the marker persists until `clearReauthNeeded()` confirms recovery.

Therefore: when `factory_health.py` shows `teams-chat: DEGRADED`, no blind retry storm is occurring — the lane is silent and waiting for `/pm-login`. This satisfies the "health must show degraded rather than a silent retry storm" requirement.

---

## REQ-04 — Tests RED-first

**Test file:** `tests/test_factory_health.py` — extended `TestLaneState` class at the bottom.

**RED evidence:** 8 new tests all failed with `AttributeError: module 'factory_health' has no attribute 'check_lane_state'` before implementation (confirmed by running `scripts/run_tests.sh tests/test_factory_health.py` before adding the function).

**8 new tests:**

| Test | What it covers |
|---|---|
| `test_lane_healthy_when_marker_absent` | Absent file → `status=ok`, `degraded=[]` |
| `test_lane_healthy_when_marker_cleared` | `{}` file → `status=ok`, `degraded=[]` |
| `test_lane_degraded_when_sso_marker_present` | SSO marker → `status=degraded`, `lane=teams-chat`, `expiry_type=sso-session` |
| `test_lane_degraded_when_foci_marker_present` | FOCI marker → `status=degraded`, `lane=foci`, `expiry_type=token-revoked` |
| `test_exit_code_1_when_lane_degraded` | Degraded lane + healthy providers → exit 1 |
| `test_exit_code_0_when_lane_cleared` | Cleared lane + healthy providers → exit 0 |
| `test_render_shows_degraded_lane_with_reason` | Report contains `[5]`, lane name, `degraded` text |
| `test_render_shows_ok_when_no_marker` | Report contains `[5]`, `ok` text |

All 8 pass GREEN. Prior 19 tests unchanged — all still pass. **Total: 27/27.**

Mock strategy: only `marker_path` (file IO) is controlled via `tmp_path` fixture. No patching of `httpx`, `psutil`, or `json` — those are tested in existing classes.

---

## Diff digest

**`scripts/factory_health.py`** (+95 lines net):
- ABOUTME line 2: "Prints 5 sections" (was 4)
- Module docstring: added `[5] Lanes` entry + `--lane-marker-path` to Usage
- Added `import json`
- Added `DEFAULT_LANE_MARKER_PATH = Path.home() / "Code" / "pm_os" / "state" / "reauth-needed.json"`
- Added `check_lane_state(marker_path)` — 50-line function with ABOUTME-style docstring; reads file, parses JSON, returns `{status, degraded, marker_path}`
- `compute_exit_code()`: +4 lines — checks `sections["lanes"]["status"] == "degraded"` → sets `has_network_error = True`
- `render_health_report()`: +12 lines — Section 5 block with degraded entries or "all lanes: ok"
- `run_health_check()`: `lane_marker_path` param added; `check_lane_state` call with crash guard
- `main()`: `--lane-marker-path` arg added

**`tests/test_factory_health.py`** (+130 lines):
- Added `TestLaneState` class with 8 tests covering REQ-01, REQ-02, REQ-04

No pm_os files modified.
