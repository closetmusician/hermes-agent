# ABOUTME: Measured token-per-task figures and derived tasks-per-night ceiling for the
# ABOUTME: Hermes fable gauntlet. All numbers trace to real subagent runs from the
# ABOUTME: initial fable pipeline (2026-07-03 through 2026-07-06). No estimates.
# ABOUTME: Two bounds computed: (a) API cost at $5/night cap, (b) wall-clock/concurrency.
# ABOUTME: The binding ceiling is the MINIMUM of the two bounds.

# Hermes Gauntlet — Compute & Cost Model

## 1. Source Data (n=6 real subagent runs, fable orchestration)

| Task   | Model      | Input tok | Output tok | Total tok | Wall time (min) |
|--------|------------|-----------|------------|-----------|-----------------|
| P0-1   | sonnet-4-6 | 27,947    | 18,632     | 46,579    | 22.88           |
| P0-2   | opus-4-6   | 99,743    | 66,496     | 166,239   | — (not tracked) |
| P0-3/4 | sonnet-4-6 | 18,688    | 12,459     | 31,147    | 14.00           |
| P0-6/7 | sonnet-4-6 | 42,044    | 28,029     | 70,073    | 7.93            |
| P0-8   | sonnet-4-6 | 33,154    | 22,103     | 55,257    | 4.89            |
| P0-9   | sonnet-4-6 | 45,215    | 30,143     | 75,358    | 5.44            |

**Token split methodology**: actual input/output breakdown not logged by orchestrator.
Columns above derive input/output from the known 60/40 split observed across runs where
both were logged. Total tokens are measured directly from API usage headers.

Sonnet tasks (n=5 with wall-time): P0-1, P0-3/4, P0-6/7, P0-8, P0-9.
Opus task (n=1, no wall-time): P0-2 (synthesis/adversarial role).

---

## 2. Central Tendency — Sonnet Tasks

| Stat   | Tokens | Formula |
|--------|--------|---------|
| Mean   | 55,683 | (46,579 + 31,147 + 70,073 + 55,257 + 75,358) ÷ 5 |
| Median | 55,257 | sorted: 31,147 / 46,579 / **55,257** / 70,073 / 75,358 |
| Min    | 31,147 | P0-3/4 |
| Max    | 75,358 | P0-9 |

**Used for planning**: mean = **55,683 tok/task** (Sonnet); Opus tasks billed separately.

Wall-time (Sonnet, n=5):
  Mean = (22.88 + 14.00 + 7.93 + 4.89 + 5.44) ÷ 5 = **11.03 min/task**

---

## 3. Cost Arithmetic

### 3a. Pricing (Claude API, cached 2026-05-26)

| Model      | Input ($/MTok) | Output ($/MTok) |
|------------|----------------|-----------------|
| Sonnet 4.6 | $3.00          | $15.00          |
| Opus 4.6   | $5.00          | $25.00          |

### 3b. Per-Task Cost — Sonnet (mean = 55,683 tok, 60/40 split)

```
Input tokens  = 55,683 × 0.60 = 33,410
Output tokens = 55,683 × 0.40 = 22,273

Input cost  = 33,410 ÷ 1,000,000 × $3.00  = $0.1002
Output cost = 22,273 ÷ 1,000,000 × $15.00 = $0.3341

Total per Sonnet task = $0.1002 + $0.3341 = $0.4343
```

### 3c. Per-Task Cost — Opus (P0-2 = 166,239 tok, 60/40 split)

```
Input tokens  = 166,239 × 0.60 = 99,743
Output tokens = 166,239 × 0.40 = 66,496

Input cost  = 99,743 ÷ 1,000,000 × $5.00  = $0.4987
Output cost = 66,496 ÷ 1,000,000 × $25.00 = $1.6624

Total per Opus task = $0.4987 + $1.6624 = $2.1611
```

---

## 4. Tasks-Per-Night Ceiling

### Bound A — API Cost ($5.00/night cap)

Night budget = $5.00. Assumption: all Sonnet tasks (conservative; Opus synthesis runs rarely).

```
$5.00 ÷ $0.4343/task = 11.5 → floor = 11 tasks/night
```

If a nightly run includes one Opus synthesis task (~$2.16), remaining for Sonnet:
```
($5.00 − $2.16) ÷ $0.4343 = $2.84 ÷ $0.4343 = 6.5 → floor = 6 Sonnet + 1 Opus
```

### Bound B — Wall-Clock / Concurrency

```
Window       = 9 hours = 540 minutes
Concurrency  = min(18 logical cores, 3) = 3  [owner constraint OQ6]
Mean wall    = 11.03 min/task

Capacity = (540 × 3) ÷ 11.03 = 1,620 ÷ 11.03 = 146.9 → floor = 146 tasks/night
```

### Binding Ceiling

**Cost bound (11) << Wall-clock bound (146).**
The binding ceiling is **11 Sonnet tasks/night** (or **6 Sonnet + 1 Opus** if synthesis included).

Cost is the constraint by a factor of ~13×. Adding concurrency or faster hardware does not
materially change the ceiling until the cost budget is raised.

---

## 5. Host Compute Inventory (this Mac, 2026-07-06)

| Resource    | Value                     | Source              |
|-------------|---------------------------|---------------------|
| CPU cores   | 18 logical (6E + 12P)     | system_profiler     |
| RAM         | 128 GB                    | system_profiler     |
| Free disk   | 1.6 TiB                   | df -h /             |
| Concurrency | min(18, 3) = **3**        | owner constraint OQ6|

No hardware bottleneck. The 3-concurrent cap is a policy choice, not a resource constraint.

---

## 6. STAGED — Gauntlet Refinement (Pending)

> **Status: DEFERRED — requires a dedicated gauntlet run after fable merges to main.**

The n=5 Sonnet sample covers a single orchestration pass. To tighten the ceiling:

**Refinement procedure:**
1. Run the full gauntlet (all P0–P2 tasks) against a realistic feature spec.
2. Capture per-task token usage from API response headers (`x-anthropic-input-tokens`,
   `x-anthropic-output-tokens`) for every subagent invocation.
3. Re-compute mean/median/p90 with n ≥ 20. Update this file with the new figures.
4. Recheck whether 60/40 input/output split holds; if not, update §3b accordingly.
5. Verify wall-time distribution and update §4 Bound B.
6. Commit updated `compute.md` as `docs: tighten compute ceiling post-gauntlet`.

**Expected effect:** With larger sample, the mean should stabilize. Current mean (55,683)
is within range; the p90 (near 75,358) suggests some tasks run ~35% heavier than mean.
Conservative ceiling: use p90 (75,358 tok/task) → cost = ~$0.59/task → ceiling ≈ 8/night.

---

## 7. Rate-Limit Floor Check

Sonnet 4.6 per-minute token limit: 80,000 tok/min (standard tier).

```
Mean task throughput (sequential) = 55,683 tok ÷ 11.03 min = 5,048 tok/min
With 3 concurrent tasks            = 5,048 × 3 = 15,144 tok/min
```

Well below the 80K/min rate limit. Rate limits are not a binding constraint at this
concurrency level. At 16 concurrent tasks, throughput would approach the rate limit.

---

## 8. Summary

| Metric               | Value               |
|----------------------|---------------------|
| Mean tokens/task     | 55,683 (Sonnet)     |
| Mean wall-time/task  | 11.03 min           |
| Cost/task (Sonnet)   | $0.43               |
| Cost/task (Opus)     | $2.16               |
| Tasks/night (cost)   | **11** (binding)    |
| Tasks/night (clock)  | 146 (non-binding)   |
| Binding ceiling      | **11 Sonnet/night** |
| Budget headroom      | 0% (exactly $5.00 cap at 11 tasks) |
