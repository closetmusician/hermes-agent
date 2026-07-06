# Codex-A raw output — Accuracy & validity [ACCURACY]

1. [ACCURACY] Process boundary is overclaimed as a credential boundary  
Where: `hermes-fable-plan.md` lines 219, 222, 224; contradicted by `diagnosis.md` lines 27-30  
Why it matters: A separate broker process on the same macOS user account does not by itself stop an unrestricted assistant shell from reaching key material, pm_os token tooling, launchd env, or alternate send paths.  
Suggested fix: Specify the actual boundary: separate OS user/keychain ACL/sandboxed assistant environment, removal of direct send credentials and token tools from assistant reach, and a bypass test that tries pm_os send CLIs and token/key access, not just `cat .env`.

2. [COMPLETENESS] Calendar-aware P4 has no documented calendar capability  
Where: `hermes-fable-plan.md` line 251; `diagnosis.md` line 131; `pmos-capability-inventory.md` lines 323-386  
Why it matters: P4 requires “check ALL calendars,” but the pm_os inventory lists Outlook mail, Teams, SharePoint, JIRA, Oracle, Glean, etc., and no calendar read tool or `Calendars.*` scope.  
Suggested fix: Add a P4 prerequisite to build or verify a calendar-read CLI/wrapper with the needed Microsoft Graph scopes before calendar-aware acceptance gates.

3. [ACCURACY] “Drive all pm_os as bin CLIs” is contradicted by pm_os evidence  
Where: `hermes-fable-plan.md` lines 28, 38, 262, 323; `pmos-capability-inventory.md` lines 237-240, 258-259, 430-445  
Why it matters: pm-jira uses direct `curl`, pm-pulse uses browser skill logic with no bin tools, and yk-voice/o365 skills live outside pm_os; a bin-CLI-only contract will not cover the actual workflows.  
Suggested fix: Reframe P5 as “wrap pm_os skills/workflows through stable entrypoints,” or add bin wrappers for pm-jira, pm-pulse, yk-voice, and any browser/MCP paths before claiming CLI parity.

4. [ACCURACY] Safe-lane auto-send conflicts with pm-send’s mandatory approval model  
Where: `hermes-fable-plan.md` lines 238-241; `pmos-capability-inventory.md` lines 201-220  
Why it matters: The plan says safe replies auto-send in P3, but pm-send explicitly says “NEVER auto-send” and requires AskUserQuestion for every draft.  
Suggested fix: Decide whether broker replaces pm-send dispatch for safe-lane items, or modify pm-send to delegate approval to the broker; do not claim auto-send works through the current pm-send path.

5. [OPERABILITY] Broker-wrapping a pm_os pipeline cannot gate internal writes precisely  
Where: `hermes-fable-plan.md` lines 263, 366; `pmos-capability-inventory.md` lines 207-214, 408-413  
Why it matters: Many pm_os writes happen inside broad scripts or browser/MCP flows; wrapping the top-level invocation gives one coarse approval, not enforcement at each external write.  
Suggested fix: Split local artifact writes from external-world writes, and make lower-level send/write tools broker-aware instead of approving whole pipelines blindly.

6. [ACCURACY] pm-pulse “submitted on cadence” contradicts broker-gated writes  
Where: `hermes-fable-plan.md` lines 162, 263; `pmos-capability-inventory.md` lines 249-259 and 486-488  
Why it matters: pm-pulse currently submits immediately with hardcoded answers and no review gate, so it violates the plan’s own “every pm_os write is broker-gated” rule.  
Suggested fix: Change pm-pulse acceptance to “ready-to-submit surfaced for approval,” or add an explicit broker approval/configured-answer gate before submit.

7. [COMPLETENESS] P5 omits pm-weekly’s fatal dependency despite choosing it as first slice  
Where: `hermes-fable-plan.md` lines 262, 271; `pmos-capability-inventory.md` lines 170-180 and 471-473  
Why it matters: The first P5 slice can fail at draft time if `prompts/draft-weekly.md` is missing or misconfigured, but this is not in the P5 fragility work items.  
Suggested fix: Add a pm-weekly preflight gate for raw scan files, prompt path, config path, token status, and report output before using it as the first parity slice.

8. [ACCURACY] P6 cost controls only apply cleanly to Claude, not Codex workers  
Where: `hermes-fable-plan.md` lines 280-286; `ai-factory-research.md` lines 31, 43, 82-99, 280-288  
Why it matters: `--max-budget-usd` and `total_cost_usd` are documented for Claude, while the Codex worker path has no equivalent budget flag or cost output in the evidence.  
Suggested fix: Make the first factory version Claude-only for budgeted workers, or define Codex-specific accounting and cap behavior before allowing Codex workers.

9. [ACCURACY] Factory cost estimate excludes required review/resume costs  
Where: `hermes-fable-plan.md` lines 284-286, 339; `ai-factory-research.md` lines 262-288  
Why it matters: The quoted `$1–3.50/job` and `$10–35` for 10 jobs are worker estimates, but every planned job also runs `codex review` and may include test-fix resumes.  
Suggested fix: Separate worker cost, review cost, and retry/resume cost in the estimate; remove “for free” from the review-gate rationale.

10. [OPERABILITY] The merge command is not a concrete valid flow  
Where: `hermes-fable-plan.md` lines 117, 177, 285; `ai-factory-research.md` lines 247-249  
Why it matters: `--ff-only/--squash merge` is ambiguous; fast-forward and squash are different git flows, and treating them as one step will produce implementation mistakes.  
Suggested fix: Pick one per repo in `merge-policy.md`: either rebase then `git merge --ff-only branch`, or `git merge --squash branch && git commit`, with exact commands.

11. [OPERABILITY] SIGTERM of a worker PID is insufficient for “no orphaned workers”  
Where: `hermes-fable-plan.md` lines 278, 286-287; `ai-factory-research.md` lines 236-239, 301  
Why it matters: On macOS, a Claude/Codex worker can spawn child shells, test servers, or package scripts; killing only the parent PID may leave children running.  
Suggested fix: Spawn workers in their own process group/session, track child PIDs, kill the process tree on timeout, and make health v2 check process groups plus worktree locks.

12. [ACCURACY] WhatsApp can be disabled in P0 but remains a fixed channel later  
Where: `hermes-fable-plan.md` lines 16, 56-87, 208, 408; `phase0-state-snapshot.md` lines 31-33, 60-62  
Why it matters: P0 allows disabling WhatsApp if pairing fails, but later architecture and channel decisions still assume WhatsApp exists.  
Suggested fix: Add a later “WhatsApp paired and verified” gate, or mark WhatsApp as conditional until the bridge passes send/receive acceptance.

---
exit_code: 0
