# Codex-D raw output — Operability & failure modes [OPERABILITY]

1. [OPERABILITY] Broker outage semantics are undefined  
Where: `hermes-fable-plan.md` §2 governance unification, P1.1/P1.3, Open Questions #1/#3; reintroduces FM-4/FM-6.  
Why it matters: the broker becomes the single gate for sends, pm_os writes, and merges, but the plan does not say whether actions fail closed, queue durably, or fallback when the broker/Telegram approval path is down.  
Suggested fix: define broker-down behavior explicitly: no direct fallback, durable held-action queue, health check, and “approval path unavailable” surfaced to Yu-Kuan.

2. [OPERABILITY] pm_os write gating is asserted, not mechanically enforced  
Where: P5.1/P5.2; `pmos-capability-inventory.md` write tools like `outlook-send-mail.js`, `teams-send-chat.js`, Graph write CLIs; reintroduces FM-1/FM-3.  
Why it matters: if Hermes skills can run literal pm_os CLI commands and still see FOCI/send credentials, the agent can bypass the broker by invoking the write tools directly.  
Suggested fix: run Hermes without write credentials; expose write-capable pm_os operations only through broker-owned wrappers/env/keychain, with raw write binaries denied or credential-starved.

3. [OPERABILITY] Factory worker permissions are still too broad for one laptop  
Where: P6.3 worker contract; `ai-factory-research.md` worktree gotchas.  
Why it matters: worktrees do not isolate `.env`, hooks, ports, databases, caches, or network; `acceptEdits` plus `Read/Write` and test shell access can still damage local state or leak secrets.  
Suggested fix: give workers a scrubbed env, temporary `HOME`, no repo secrets, disabled hooks where possible, strict settings/MCP ignore flags, and per-repo generated tool allowlists.

4. [OPERABILITY] macOS sleep, login sessions, and launchd drift are under-modeled  
Where: P0.4 launchd sanity, P6.1 cron tick loop, P6.7 health v2; reintroduces FM-5/FM-9.  
Why it matters: “laptop reboot loses nothing” does not cover lid-close overnight runs, launchd without login/keychain/browser session, or jobs silently not ticking for hours.  
Suggested fix: add last-tick SLA alerts, missed-run digest, launchd session/keychain validation, and an explicit policy for sleep: prevent it for factory windows or schedule only while awake.

5. [OPERABILITY] Daily cost kill switch depends on data that may arrive too late  
Where: P6.6, risks #1, `ai-factory-research.md` cost accounting.  
Why it matters: the plan sums `total_cost_usd` from final worker JSON, but a hung/crashed worker or Codex run may not emit usable final cost before burning the per-invocation cap.  
Suggested fix: track live stream usage where available, reserve worst-case budget at spawn, enforce daily ceiling before starting/resuming, and kill all active workers when ceiling is reached.

6. [OPERABILITY] Job store corruption and overlapping ticks are treated as “low stakes”  
Where: P6.1/P6.2, Open Question #2.  
Why it matters: JSON/SQLite choice is not low stakes once it controls spawning, approval state, merge locks, and cleanup; overlapping launchd ticks can double-spawn or double-merge.  
Suggested fix: choose SQLite with transactions, a supervisor-wide lock, monotonic state transitions, atomic artifacts, and a corruption recovery path before enabling merges.

7. [OPERABILITY] Disk-fill failure mode is not gated before factory rollout  
Where: P6 cleanup, `ai-factory-research.md` disk blowup gotcha.  
Why it matters: N worktrees times dependency installs and failed-retention windows can fill the laptop disk, taking down Hermes, pm_os state, logs, and the browser profile.  
Suggested fix: add free-space preflight, max worktree count, max retained-failure bytes, cache policy, and cleanup-before-spawn as hard supervisor gates.

8. [OPERABILITY] Health checks are pull-oriented; silent stopped pipelines still win  
Where: P0.3, P5.5, P6.7, P7.3; reintroduces FM-5.  
Why it matters: a one-line health command helps only when checked; six months in, the owner needs to know when morning triage, pm-weekly, or factory ticks stopped.  
Suggested fix: publish a compact daily health heartbeat with last successful run per pipeline/job lane, and alert on state changes or “no success within X.”

9. [OPERABILITY] One-line cron prompts will rot as skills and pm_os drift  
Where: P2.2, P5.1, P7.1 dual upstream tracking.  
Why it matters: monthly contract checks are too slow for pipelines that run daily/weekly, and pm_os has fatal dependencies like missing prompt files and changed flags.  
Suggested fix: make every scheduled skill run a preflight: command exists, pinned flags match `--help`, required prompt/config files exist, token health passes, and dry-run/write gate is reachable.

10. [OPERABILITY] The auth fragility trio is named but not made operationally fail-closed  
Where: P5.3; `pmos-capability-inventory.md` §6.1–6.3 and §6.8.  
Why it matters: FOCI rotation, Okta browser expiry, Atlassian env sourcing, and missing `~/.age/pm-os.key` can still produce empty reports, broken writes, or repeated retries.  
Suggested fix: add hard wrappers denying destructive FOCI calls, token/key/browser-profile health in P0/P5 health output, and paused degraded states instead of repeated scheduled attempts.

11. [OPERABILITY] Approval payloads can repeat the old truncation/hash failure class  
Where: P1.3 broker approval, P6.5 merge approval; reintroduces FM-6.  
Why it matters: Telegram approvals carrying summaries, test output, review findings, and payloads can hit size/truncation limits or hash mismatches just like `/approve-email`.  
Suggested fix: use stable action IDs over durable broker artifacts; Telegram carries only summary/buttons plus local artifact links, never the authoritative payload body.

12. [OPERABILITY] Self-improving skills risk becoming a second maintenance treadmill  
Where: P7.2/P7.3.  
Why it matters: “agent proposes skills” without aging, linting, acceptance tests, and disable rules can accumulate stale cron behavior and recreate upgrade debt in markdown form.  
Suggested fix: require skill lint + behavioral test + owner enablement, add stale-skill review, and cap monthly self-maintenance scope.

13. [OPERABILITY] P5 scope invites effort inversion before factory reliability exists  
Where: Vision “all pm_os plus arbitrary future PM workflows,” P5.1–P5.4, P5 exit gate.  
Why it matters: all pipelines, fragility fixes, and video discovery in one phase can consume the project the same way email-guard work consumed v1.  
Suggested fix: split P5 into explicit slices: read-only pm-weekly/pm-morning first, then writes, then fragile browser/SSO paths, with video parked unless a named real use case exists.

---
exit_code: 0
