# Codex-C raw output — Completeness & coherence [COMPLETENESS]

1. [COMPLETENESS] Item-level parity is deferred too late  
Where: `hermes-fable-plan.md` §3 lines 129-131, §7.4 lines 299-301  
Why it matters: P2-P4 can “ship” baseline CoS without proving the 216 checklist items landed; ADAPTED/N-A is only audited at the end.  
Suggested fix: Add an item-level traceability table before implementation: every checklist item gets phase, acceptance test, or pre-approved ADAPTED/N/A rationale.

2. [COHERENCE] “All pm_os” becomes “prioritized pm_os”  
Where: Vision line 28 vs M5/P5 lines 196, 259, 271, 356  
Why it matters: The promise is all 8 pipelines plus future PM workflows, but the done gate only requires “prioritized” pipelines.  
Suggested fix: Remove “prioritized” or define a registry/wrapper acceptance test for all 8 current pipelines plus the template for future workflows.

3. [COMPLETENESS] pm_os write governance is not enumerated across 34 write-capable tools  
Where: `hermes-fable-plan.md` §5.2 line 263; `pmos-capability-inventory.md` lines 502-503  
Why it matters: A generic “Graph writes” negative test will not prove coverage for Office bridge, SharePoint/OneDrive, scheduler installs, backtests, token writers, browser edits, and local-file write tools.  
Suggested fix: Add a 34-tool write manifest: broker-gated, approval-exempt local write, token-auth special case, or read-only, with one bypass test per write family.

4. [COMPLETENESS] The CoS-to-factory handoff is still undefined  
Where: factory flow line 91; P6 job store line 279; Open Question 7 line 409  
Why it matters: The build assistant may work as a standalone queue, but the plan does not define intake format, who creates the job record, or how status/completion updates return to `tasks.md`.  
Suggested fix: Define a concrete intake schema, e.g. Telegram `/factory` and `tasks.md` `factory:` tag, with supervisor-owned ledger writes and linked task-state updates.

5. [COHERENCE] P5 skill layer omits two pipelines listed in the matrix  
Where: §3.2 rows `pm-login` and `yk-comms / yk-voice` lines 163-164; P5.1 skill list line 262  
Why it matters: M5 says all §3.2 rows pass, but P5.1 only lists `pm-weekly`, `pm-jira`, `pm-pulse`, `pm-morning`, `pm-send`, and `glean`.  
Suggested fix: Add explicit P5 acceptance for `pm-login` and `yk-comms/yk-voice`, or stop counting them as pipelines.

6. [COMPLETENESS] Video can be “completed” by not building video  
Where: Vision line 28; matrix video row line 166; P5.4 line 268; subsystem row line 324  
Why it matters: The plan promises video/media creation, but the acceptance path allows a written park decision to close the row with no render.  
Suggested fix: Either descale the promise explicitly or require one end-to-end approved render as the P5 gate.

7. [COMPLETENESS] Business development parity is reduced to one follow-up task  
Where: matrix row 3 line 139; checklist Category 3 lines 85-101  
Why it matters: The plan drops tracker-as-source-of-truth, lead verification, sent-mail sweep, follow-up cadence, tracker header/schema reads, and tracker update-before-handled.  
Suggested fix: Build a stakeholder-tracker equivalent with those tests, or mark each BD item ADAPTED/N/A before P4, not at P7.

8. [COMPLETENESS] Executive-assistant acceptance misses core bucket and thread behaviors  
Where: matrix row 2 line 138; checklist Category 2 lines 62-82  
Why it matters: Three ACTION-item demos do not prove seven classification buckets, reply-to-thread/reply-all equivalents, inbox archive/waiting/noise updates, or scheduling authority handling.  
Suggested fix: Add a bucket-level EA test suite covering every classification bucket and Outlook/Teams equivalents for thread reply, reply-all, and inbox state.

9. [COMPLETENESS] Cron parity only covers “live” jobs  
Where: matrix row 8 line 144; checklist Category 8 lines 184-210  
Why it matters: Reference behavior includes disabled-by-default jobs: BD sourcing, boundary sweep, backup, self-update, and tradclaw household cron templates. These can silently vanish.  
Suggested fix: Create a cron manifest mapping every reference cron to enabled/adapted/disabled, with prompt, session mode, delivery mode, and acceptance rationale.

10. [COMPLETENESS] Workspace state omits the tradclaw memory/resource model  
Where: matrix row 10 line 146; checklist Category 10 lines 233-255  
Why it matters: `tasks.md` plus env notes does not cover `MEMORY.md`, daily memory logs, resource templates, or the “remember this, write it down” behavior.  
Suggested fix: Define the CPO-context equivalent memory/resource surfaces, or explicitly adapt/N-A each tradclaw state item.

11. [COMPLETENESS] Onboarding is collapsed into a checklist file  
Where: matrix row 11 line 147; checklist Category 11 lines 257-282  
Why it matters: The reference setup includes prerequisite verification, placeholder replacement, behavior checks, a 7-batch onboarding interview, module selection, and a user-confirmed output contract.  
Suggested fix: Expand P2 setup into an onboarding flow with those observable gates, adapted to Hermes/pm_os.

12. [COMPLETENESS] Self-maintenance acceptance drops self-update and improvement routing  
Where: matrix row 15 line 151; P7.3 line 297; checklist Category 15 lines 344-354  
Why it matters: The plan tests backup and graceful degradation, but not the disabled self-update job or the rule that improvement ideas become concrete tasks/internal update passes.  
Suggested fix: Add explicit acceptance for disabled self-update and a priority-map route for system-improvement work.

---
exit_code: 0
