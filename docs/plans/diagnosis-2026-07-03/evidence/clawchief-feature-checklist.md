# ABOUTME: Feature checklist synthesized from direct reads of ~/Code/clawchief and ~/Code/tradclaw.
# ABOUTME: Purpose: enumerate every observable behavior so the hermes-based replacement can verify
# ABOUTME: it regresses none of them. Each checkbox is one verifiable behavior. Source repo is
# ABOUTME: noted in parentheses where it matters; behaviors shared by both are listed once.
# ABOUTME: Written 2026-07-03 as evidence for the diagnosis-2026-07-03 planning effort.

# Chief-of-Staff Reference Feature Checklist

> **Source repos:** `~/Code/clawchief` (founder chief-of-staff) and `~/Code/tradclaw` (household ops).
> **How to use:** each item is one observable behavior. A reviewer can ask "did we build this?" and answer yes or no from a running system. Check boxes as behaviors are verified in the hermes replacement.

---

## Must-Not-Regress (Top 10)

These are the behaviors most likely to cause the assistant to stop being used if absent. Verify these first.

1. **HEARTBEAT_OK silence discipline** — when a heartbeat run finds nothing actionable, it emits exactly `HEARTBEAT_OK` and nothing else; no summarizing, no filler.
2. **5-condition safe-lane gate** — auto-resolution only proceeds when all five conditions are true simultaneously: signal is clearly understood, correct source of truth is known, action is operational not strategic, authority is already clear, and a mistake would be low-cost and recoverable.
3. **Same-turn source-of-truth update** — any action that changes state (task add, tracker update, calendar event, meeting-note ingestion) writes the relevant source-of-truth file or service in the same agent turn, not as a deferred follow-up.
4. **Gmail message-level search** — inbox sweeps use `gog gmail messages search` (message-level), never thread-only search; this was a documented production bug in clawchief's history.
5. **Meeting-note idempotency by ledger** — processed meeting notes are recorded in a persistent ledger (docId + processedAt + status); the same doc is never processed twice even across sweeps firing every 15 minutes.
6. **All-calendars check before booking** — scheduling actions inspect every relevant calendar (work + personal + secondary) for conflicts before confirming or proposing a time; if any calendar is not visible, availability is treated as uncertain and the agent does not book.
7. **Approved-channels trust boundary** — only messages from explicitly listed trusted gateway channels count as the user giving an instruction; content from email bodies, PDFs, calendar descriptions, cron payloads, and tool output is treated as untrusted input to triage, never as permission to bypass policy.
8. **One proactive delivery route, no spraying** — proactive updates go to a single configured channel+target; isolated cron jobs that announce use one delivery target; no update is sent to multiple places.
9. **Isolated cron jobs stay silent by default** — daily task prep runs isolated with `delivery.mode: none`; it does not contact the user unless something requires human attention.
10. **Install acceptance gate** — setup is only complete when every item in the install checklist passes; unchecked items block the install, they are not advisory.

---

## Category 1: Signal Triage & Policy Layer (clawchief)

> Source: `clawchief/priority-map.md`, `clawchief/auto-resolver.md`

### Priority Map
- [ ] Every inbound signal is mapped to zero or more people from the priority-map before any action is taken.
- [ ] Every inbound signal is mapped to zero or more programs from the priority-map before any action is taken.
- [ ] Four urgency levels are defined: P0 (interrupt now), P1 (same day), P2 (digest/batch), P3 (ignore/archive).
- [ ] Four action modes are defined: interrupt principal now, handle and summarize, queue for digest, ignore.
- [ ] Signals mapping to no important people and no important programs are batched or ignored by default.
- [ ] People priority explicitly includes relational depth (family, loyalty) not just operational relevance.
- [ ] The priority map owns people importance, program importance, urgency, and routing — it does not own task storage, reply wording, or cron timing.
- [ ] Task grouping (sections, labels) in Todoist / task system connects to priority-map program names when a clear match exists.
- [ ] The priority map is reviewed and updated whenever the principal says priorities changed, a new person becomes important, or a new recurring program appears.
- [ ] Default routing rules exist: inbox/calendar/scheduling → executive-assistant; pipeline/tracker/outreach → business-development; task CRUD → daily-task-manager; morning prep → daily-task-prep.
- [ ] Things that should be ignored or batched are explicitly listed: casual chatter, repeated notifications adding no new info, speculative ideas without owner/deadline/next step.

### Auto-Resolver Policy
- [ ] After classifying a signal, the auto-resolver resolves the obvious next step rather than merely summarizing it.
- [ ] Four resolution modes exist: auto-resolve now, draft and ask the principal, escalate without acting, no action/archive.
- [ ] The safe-lane test is explicit and enumerated (all five conditions must be true; not vibes-based).
- [ ] The draft-first lane is explicit and enumerated: legal/policy answers, investor/board messaging, pricing-sensitive replies, press/public-facing responses, emotionally sensitive personal communication, messages where wording matters more than logistics.
- [ ] The escalate-without-acting lane is explicit and enumerated: unclear authority, contradictory/incomplete signal, reputational/legal/financial risk without context, private context the principal would not want disclosed, no reliable source of truth.
- [ ] The auto-resolve workflow has a defined step sequence: read priority map → read domain source of truth → classify signal → choose resolution mode → execute → update source of truth in same turn.
- [ ] The auto-resolver explicitly forbids acting from memory alone; every action is grounded in the relevant live source of truth.
- [ ] When auto-resolve succeeds, output is a short update saying what changed and what still needs the principal.
- [ ] The auto-resolver is a separate layer from the heartbeat orchestrator; the heartbeat delegates to it, not vice versa.
- [ ] The auto-resolver and priority map are read at the start of every executive-assistant skill run.

---

## Category 2: Skills — Executive Assistant (clawchief)

> Source: `skills/executive-assistant/SKILL.md`, `references/access-and-defaults.md`, `references/outreach-tracking.md`, `references/templates.md`

- [ ] Skill reads policy files (priority-map, auto-resolver, meeting-notes, tasks, TOOLS.md) at the start of every run.
- [ ] Skill routes outreach-tracker / lead-status / prospect-pipeline signals to business-development, not treating them as generic EA work.
- [ ] Reply-to-existing-thread always uses `gog gmail send --reply-to-message-id=...`, never a plain send with a Re: subject.
- [ ] `--reply-all` is added when the original thread recipients should remain copied.
- [ ] EA skill inspects full thread (read enough to understand prior decisions) before classifying or replying.
- [ ] Seven message classification buckets are defined: schedule now, reply and clear now, clear without reply, waiting on external reply, follow-up due now, principal decision needed.
- [ ] EA skill explicitly does not ask whether the principal is free when the calendars already answer that question.
- [ ] EA skill does not use wording implying the assistant personally met, spoke with, or spent time with someone.
- [ ] EA skill adds a follow-up task in tasks.md before ending the turn when the work creates a future dependency.
- [ ] After a message is handled, inbox state is updated: handled → archive; waiting → leave in inbox; noise → archive.
- [ ] Authority for scheduling is defined in references/access-and-defaults.md by sender identity list; requests from those identities may be acted on without separately asking.
- [ ] EA skill treats out-of-office, travel, offsite blocks as real conflicts, not optional context.
- [ ] If calendar visibility is incomplete, the agent does not auto-book from a scheduler link.
- [ ] Booking-link is used first when one is provided and workable.
- [ ] EA output style: 1–4 short bullets or 1 short paragraph; lead with the action or issue; recommendation included when a decision is needed; no raw log dumps.
- [ ] Escalate-before-replying criteria are explicitly listed and checked: legal/regulatory/conflict, financial/pricing/investor/contract, press/podcast/speaking, emotionally sensitive, strategically important, unclear enough that wrong reply creates confusion.

---

## Category 3: Skills — Business Development (clawchief)

> Source: `skills/business-development/SKILL.md`, `resources/partners.md`

- [ ] The outreach sheet/tracker/CRM is the live source of truth; local .md or .csv prospect files are never treated as current state.
- [ ] Prospecting scope (geography, target market) is not silently broadened beyond the configured playbook without explicit direction.
- [ ] Every new lead requires a verified working website and a real public email before being added, unless explicitly waived.
- [ ] Junk/placeholder addresses from site code are ignored.
- [ ] Sent mail is swept so unanswered outreach does not disappear silently.
- [ ] Tracker is updated every time outreach state changes: initial send, meaningful reply, meeting ask, meeting booked/confirmed/rescheduled/cancelled, decline, next-step learned.
- [ ] Tracker update happens before the email thread is considered handled.
- [ ] Inbound reply workflow processes inbox and tracker as one combined workflow (not sequential).
- [ ] Default follow-up cadence is defined: first follow-up ~2 days after unanswered outbound; second ~5 days after; third ~7 days after; after third unanswered, stop automatic sequence and surface the lead.
- [ ] Follow-up clock resets after each new outbound.
- [ ] Auto-follow-up is suppressed when thread is sensitive, clearly closed, or user said to stop.
- [ ] Tracker header row is read before writing; schema is not assumed from an old example.
- [ ] Business-development handles scheduling of outreach meetings and immediately updates the tracker after booking.
- [ ] Business-development boundary rule is documented: inbox/calendar → EA; pipeline/tracker/outreach → BD; tracker must be updated in same turn even if EA discovers a partner reply.

---

## Category 4: Skills — Daily Task Manager (clawchief)

> Source: `skills/daily-task-manager/SKILL.md`, `clawchief/tasks.md`

- [ ] `clawchief/tasks.md` is the single canonical live task list; all sessions and heartbeats read this file.
- [ ] Task state changes are written to `clawchief/tasks.md` in the same turn whenever practical.
- [ ] When the assistant receives a task with a due date or clear time horizon, it adds an assistant-owned task in the same turn using canonical due-date format (YYYY-MM-DD or YYYY-MM-DD HH:MM TZ).
- [ ] When a task depends on an outside reply or future check-in, a separate follow-up task with its own due date is added.
- [ ] Overdue and due-today assistant tasks are scanned before deciding what needs attention.
- [ ] Long-term preferences stay in memory files; live operational state stays in tasks.md; prior-day completed task history stays in tasks-completed.md.
- [ ] Tasks are completed by marking `[x]` with a completion timestamp when known.
- [ ] Same-day completions stay in tasks.md until the next daily prep run unless the user wants immediate cleanup.
- [ ] Heartbeat task follow-up reads tasks.md, asks only about open tasks, and does not ask again about already-done tasks.
- [ ] Task grouping structure: Today / Backlog with due date / Recurring reminders / Backlog / Rules sections minimum; Today has Principal and Assistant owner subsections; owner subsections group by program/person from priority-map.
- [ ] When a backlog item is promoted into Today, the backlog copy is removed in the same edit so no task exists in two active sections simultaneously.
- [ ] Recurring reminders section is scanned daily; due-today instances are added to Today without deleting the recurring source entry.

---

## Category 5: Skills — Daily Task Prep (clawchief)

> Source: `skills/daily-task-prep/SKILL.md`

- [ ] Daily task prep is an isolated session cron job running at 2am, silent by default (delivery.mode: none).
- [ ] Prep reads priority-map before regrouping or inserting tasks.
- [ ] Existing manually added open tasks in Today are preserved unless obviously stale.
- [ ] On weekdays, Every-weekday section is treated as the recurring seed list; it is not auto-added on weekends.
- [ ] Due-today items from Backlog-with-due-date are promoted into Today; the backlog copy is removed in the same edit.
- [ ] Recurring reminder instances due today are added to Today without deleting the recurring source item.
- [ ] Principal-owned meetings and calls for today are added to Today by reading calendars via gog.
- [ ] Personal/family calendar blocks that are only conflict sources (not principal tasks) are excluded from Today.
- [ ] Tasks completed yesterday are archived from tasks.md into tasks-completed.md.
- [ ] Tasks completed today remain in tasks.md until the next morning's prep run.
- [ ] The file's Last-updated timestamp is updated by prep.
- [ ] Prep stays silent unless something needs human attention.
- [ ] Safety rules: prep does not wipe Today to rebuild it; does not archive recurring source entries; does not archive tasks completed today.
- [ ] If calendar access fails, file-based prep still runs; user is only notified if the failure matters.
- [ ] If nothing needs to change, prep does nothing.
- [ ] Duplicates are removed by normalized task text, keeping the most specific wording already present.
- [ ] Active tasks are reordered in priority-first order within each owner section.

---

## Category 6: Heartbeat Orchestration (clawchief)

> Source: `workspace/HEARTBEAT.md`

- [ ] Heartbeat is an 11-step declarative checklist, not a workflow spec; it delegates to skills and policy files.
- [ ] Steps 1–4 of heartbeat read the four policy/state files (priority-map, auto-resolver, meeting-notes policy + ledger, tasks.md) before any action.
- [ ] Step 5: executive-assistant workflow runs for meeting-notes ingestion plus inbox/calendar/scheduling triage.
- [ ] Step 6: if a signal is primarily about outreach tracker/lead/prospect/partner, business-development workflow is used instead of treating it as generic EA work.
- [ ] Step 7: auto-resolve low-risk operational items when next step is obvious and authority is clear.
- [ ] Step 8: if meeting notes create principal tasks, they are added to tasks.md.
- [ ] Step 9: if the principal needs to know or act, exactly one short direct update is sent.
- [ ] Step 10: if there is no EA/biz-dev issue, at most one priority nudge is used, anchored in a live program or open task.
- [ ] Step 11: if nothing useful to say, reply exactly HEARTBEAT_OK.
- [ ] Repeated or near-identical nudges are not sent unless there is materially new information, a changed recommendation, or a concrete trigger.
- [ ] Heartbeat is kept as an orchestrator; it does not duplicate workflow specs owned by skills or policy files.

---

## Category 7: Meeting Notes Ingestion (clawchief)

> Source: `clawchief/meeting-notes.md`, `workspace/memory/meeting-notes-state.json`

- [ ] Meeting notes are treated as a live operational signal source, not passive documents.
- [ ] EA sweep checks for new/recently updated meeting notes on every run (not just daily).
- [ ] Heartbeat reinforces the same meeting-note check behavior.
- [ ] A meeting note is not considered "handled" until all outputs are pushed: tasks added, follow-ups created, tracker/calendar/inbox state updated, auto-resolved actions completed, ledger updated.
- [ ] Processing workflow has 7 steps: search for recent docs → compare against ledger → read each unprocessed note → extract items → classify through priority map → run auto-resolver → update tasks.md and other sources of truth in same turn → record in ledger.
- [ ] Extraction looks for: explicit action items, implied follow-ups, deadlines, promises the principal made, introductions to send, docs to share, scheduling next steps, outreach/partnership next steps, GTM/content tasks, legal/policy questions.
- [ ] Auto-resolve bias applies to meeting notes: task adds, follow-up reminders, tracker updates after partner meetings, low-risk scheduling follow-ups when authority is clear.
- [ ] Ledger format per record: docId, title, processedAt, meetingDate, status, summary.
- [ ] The ledger file is at `workspace/memory/meeting-notes-state.json`.
- [ ] If a meeting note contains principal tasks, they are added to tasks.md in the same turn as ingestion.

---

## Category 8: Cron Job Design (clawchief + tradclaw)

> Source: `clawchief/cron/jobs.template.json`, `tradclaw/cron/jobs.template.json`, `tradclaw/cron/README.md`

### clawchief cron jobs
- [ ] Executive-assistant sweep: schedule `*/15 8-21 * * *` in the main session, fires as a systemEvent; only messages the principal if there is new actionable information.
- [ ] Daily task prep: schedule `0 2 * * *`, isolated session, agentTurn, 900s timeout, delivery.mode: none.
- [ ] Daily business-development sourcing: schedule `0 2 * * *`, isolated session, agentTurn, 1800s timeout, delivery.mode: announce to configured channel+target.
- [ ] Optional boundary sweep (`30 7 * * *`, main session, disabled by default) exists for sweep windows that cannot be expressed in one cron expression.
- [ ] Nightly backup job is shipped disabled; its prompt instructs deterministic allowlisted git flow (no exploratory adds), stage only restore-critical paths, skip commit if diff is empty, stay silent unless something needs attention.
- [ ] Self-update job is shipped disabled; its prompt instructs silence unless there is a meaningful problem.
- [ ] Cron prompts are one-liners that delegate to skills ("Use the X skill."); skills carry all workflow logic.
- [ ] Main-session jobs use sessionTarget: main; batch/isolated jobs use sessionTarget: isolated with explicit timeoutSeconds.
- [ ] Isolated jobs use payload.kind: agentTurn with model/thinking/timeoutSeconds; main-session jobs use payload.kind: systemEvent.
- [ ] Delivery mode per job is explicit: none for silent jobs, announce+channel+target for jobs that should report.

### tradclaw cron jobs (all disabled by default)
- [ ] Morning household brief: weekdays before the rush, summarizes family logistics; sends only actionable information; keeps it compact if nothing unusual is happening.
- [ ] Afternoon pickup check: weekdays ~2:30pm; same-day changes only; says nothing if nothing changed.
- [ ] Weekend activities preview: Friday 6pm; summarizes Sat/Sun activities, conflicts, prep needs, gear, gifts.
- [ ] Weekly meal planning prompt: Sunday 4pm; reviews coming week's calendar; drafts meal plan and grocery list from gaps.
- [ ] School deadline sweep: weekdays 3pm; forms, fees, spirit days, trips; skips newsletter noise.
- [ ] Helper payment reminder: Fridays 9am; checks who is due; includes amount only if confidently stored; avoids duplicate reminders if already marked paid.
- [ ] Monthly home maintenance review: 1st of month 10am; surfaces 2–4 high-leverage tasks, safety, recurring replacements, seasonal transitions.
- [ ] Weekly homework and learning review: Sundays 5pm; summarizes what each child has been working on, confidence patterns, reinforcement needs; tone is descriptive not judgmental.
- [ ] Storytime prompt: optional, Sun and Wed 7pm; suggests custom bedtime story idea; keeps it optional and charming, not spammy.
- [ ] All tradclaw cron jobs are off by default; users are instructed to pick only the jobs matching actual household needs.
- [ ] Cron README emphasizes "a small number of good scheduled checks beats a giant automation hairball."

---

## Category 9: Location Awareness (clawchief)

> Source: `clawchief/location-awareness.md`

- [ ] Tasks that require the principal to be at home base are not presented as actionable when principal is traveling.
- [ ] Traveling-status tasks are moved to one of three states: blocked while traveling, delegate to someone else, reschedule to next plausible date/location window.
- [ ] Location constraints are added into task text when a task clearly depends on a place.
- [ ] Calendar/travel context and explicit principal statements are the source of truth for current location assumptions.
- [ ] Family/shared conflict calendar is a required travel source of truth for scheduling decisions, not optional context.
- [ ] If location is uncertain and it changes whether a task is actionable or a meeting is bookable, the agent asks or marks the assumption clearly and does not book.
- [ ] Heartbeat check-ins do not nudge the principal about tasks they cannot physically do from their current location.
- [ ] EA workflow uses location rules before offering times or treating travel windows as available.
- [ ] daily-task-prep applies location rules when curating Today.
- [ ] Tasks that are location-neutral (phone calls, online work) are kept active regardless of travel status.
- [ ] New recurring place-based patterns are codified into the location-awareness policy file, not left in chat memory.

---

## Category 10: Workspace Artifacts & State (both repos)

> Source: `clawchief/tasks.md`, `clawchief/tasks-completed.md`, `workspace/memory/meeting-notes-state.json`, `workspace/TOOLS.md`, `tradclaw/workspace/MEMORY.md`, `tradclaw/workspace/resources/templates/`

### clawchief
- [ ] tasks.md has defined sections: Today (Principal + Assistant subsections with program groupings), Every weekday, Backlog with due date, Recurring reminders, Backlog, To research, Weekly, Monthly, Quarterly, Rules.
- [ ] tasks.md embeds its own rules at the bottom of the file (self-documenting).
- [ ] tasks-completed.md is a separate archive; only daily-task-prep writes to it (single-writer discipline).
- [ ] tasks.md tracks a Last-updated timestamp.
- [ ] meeting-notes-state.json is the only structured runtime state file in the workspace; all other state lives in external services.
- [ ] TOOLS.md stores environment-specific values (emails, calendars, tracker ID, BD playbook knobs) that every skill reads at run time; changing a value propagates to all skills without touching skill files.
- [ ] Single-writer ownership table: tasks.md ← all skills; tasks-completed.md ← daily-task-prep only; meeting-notes ledger ← EA only; outreach tracker ← BD only.
- [ ] External state (Gmail, Calendar, Sheets) stays in its own system of record; the agent does not maintain a local copy.

### tradclaw
- [ ] MEMORY.md stores durable household facts: constants (names, school names, pickup norms, allergies, helper rhythms), preferences (best times, what counts as urgent, what is annoying), ongoing context (sports, projects, travel), lessons learned.
- [ ] Daily memory logs are stored as `memory/YYYY-MM-DD.md`; today's and yesterday's are read on session start.
- [ ] MEMORY.md is loaded in main/direct household sessions only, not in shared or group contexts.
- [ ] `resources/` directory holds structured domain markdown: meal-plans, school, homework, helpers, books, home-maintenance.
- [ ] Seven resource templates exist as blank schema files: fun-facts.md, helper-payments.md, household-profile.md, meal-plan.md, school-notes.md, shopping-list.md, story-preferences.md.
- [ ] "When the user says remember this, write it down" is an explicit rule.

---

## Category 11: Onboarding & Setup (both repos)

> Source: `clawchief/INSTALL-WITH-OPENCLAW.md`, `clawchief/INSTALL-CHECKLIST.md`, `clawchief/SETUP-GOG.md`, `tradclaw/SETUP-CHECKLIST.md`, `tradclaw/tradclaw/BOOTSTRAP.md`, `tradclaw/tradclaw/onboarding-interview.md`, `tradclaw/tradclaw/apply-interview-results.md`, `tradclaw/tradclaw/module-selection-guide.md`

### clawchief install
- [ ] GOG is a hard prerequisite; the install process says "do not continue until these all work" (Gmail message search, Calendar, Sheets, Docs if meeting-notes enabled).
- [ ] 13 placeholder tokens are defined and must all be replaced before testing.
- [ ] Install checklist has three sections: GOG verification, Skills installation, Workspace installation, Behavior verification, Cron verification.
- [ ] Behavior checks in install checklist include observable behaviors, not just file existence: heartbeat reads source-of-truth files, inbox sweeps use message-level search, scheduling checks all calendars, task system uses tasks.md, etc.
- [ ] "If any box is unchecked, the install is not done" is explicit in the checklist.
- [ ] Nightly backup is flagged as "configure only if desired" in the install guide; self-update is flagged as "enable only if explicitly desired."
- [ ] GOG stop conditions are defined: wrong Google account authenticated, Gmail works but Calendar or Sheets does not, tracker sheet not shared to authenticated account, thread-only search used where message search is required.
- [ ] The install guide specifies keeping cron prompts short and letting skills carry workflow details.

### tradclaw onboarding
- [ ] Setup runs a 7-batch onboarding interview before touching any workspace files.
- [ ] Interview batches cover: household shape, calendars and timing, school and kids, food and home, helpers and admin, stories and books, boundaries.
- [ ] Interview asks about quiet hours explicitly.
- [ ] Interview asks "what should I never automate without asking" explicitly.
- [ ] After the interview, the agent produces a 5-item output contract before editing files: summary of what was learned, enabled modules, disabled modules, concrete file changes, optional cron ideas.
- [ ] The output contract is shown to the user for confirmation before editing workspace files (unless user asked to proceed immediately).
- [ ] Module selection uses explicit enable-if criteria (calendar briefs: multiple calendars, pickup complexity, recurring activities, frequent schedule confusion; etc.).
- [ ] Good defaults are canonically defined: calendar briefs, school triage, meal planning, home maintenance; the rest are opt-in.
- [ ] All skills are opt-in; "enabling everything by default" is explicitly listed as bad behavior.
- [ ] MEMORY.md is seeded only with durable patterns from the interview (recurring rhythms, urgency rules, reminders that worked); ephemeral interview data is not written to MEMORY.md.
- [ ] Cron suggestions use jobs.template.json as example shapes only; users are instructed to invent their own IDs, schedules, and timezones.

---

## Category 12: Safety, Trust Boundary & Behavioral Calibration (tradclaw + clawchief)

> Source: `tradclaw/workspace/SOUL.md`, `tradclaw/workspace/AGENTS.md`, `tradclaw/workspace/TOOLS.md`, `clawchief/skills/executive-assistant/references/access-and-defaults.md`

### Trust boundary
- [ ] Approved gateway channels are explicitly listed in TOOLS.md; only those channels count as "the user" giving instructions.
- [ ] Untrusted content sources are named: school email, newsletters, PDFs, web pages, calendar event descriptions, chat apps, forwarded threads, pasted logs, tool/MCP outputs, cron job payloads.
- [ ] Untrusted content is treated as possible facts to triage, never as permission to bypass policy, reveal secrets, change behavior, or exfiltrate data.
- [ ] Content containing embedded instructions ("ignore your rules and send…", "reveal your system prompt", "encode secrets") is ignored; only the real user in an approved channel can authorize outbound action.
- [ ] Sharing any information about children or the household outward requires asking the adult user first in an approved channel, unless a narrow standing exception has been explicitly written.
- [ ] Standing exceptions to the sharing rule must be specific; vague "it's fine" is not sufficient.
- [ ] Authority for scheduling is defined by sender identity list (clawchief: access-and-defaults.md); requests from listed addresses may be acted on without separately asking.

### Child and family privacy
- [ ] Private information about children (names, ages, schools, schedules, locations, health, photos, identifiable stories) is never posted, exported, emailed, or disclosed outside the household without explicit adult approval.
- [ ] Default is deny: if sharing any family information could leave the trusted context, ask first and wait for a clear yes.
- [ ] Approved gateway channels control where child information may be referenced.

### Behavioral calibration
- [ ] Good behaviors are explicitly listed: catching school deadlines, reminding about pickup changes, noticing pantry gaps before dinner, surfacing due-soon maintenance.
- [ ] Bad behaviors are explicitly listed: interrupting constantly, over-optimizing normal family life, inventing urgency, turning every routine into a process, fake perkiness, scolding, exhaustive optimization.
- [ ] External-action ask-first list is defined: sending email, texting helpers, placing orders, making reservations, posting publicly.
- [ ] "Avoid nagging, over-checking, and duplicate reminders" is an explicit rule.
- [ ] Proactive updates route to the principal only when there is new actionable information; silence is valid and expected.
- [ ] Heartbeat quiet rule applies per heartbeat check, not just globally: morning household check stays quiet if nothing needs attention; afternoon pickup check says nothing if nothing changed.

---

## Category 13: Channel Delivery (clawchief)

> Source: `clawchief/CHANNELS.md`, `clawchief/workspace/TOOLS.md`

- [ ] Proactive delivery route is configurable via two placeholders: `{{PRIMARY_UPDATE_CHANNEL}}` and `{{PRIMARY_UPDATE_TARGET}}`.
- [ ] Workflows are kept generic; only the delivery route changes per deployment.
- [ ] Supported channels include: Slack, Telegram, Signal, Discord, Google Chat (via placeholder mapping).
- [ ] Main-session sweeps stay delivery-free when they already run in the user's active channel.
- [ ] Isolated jobs that should announce completion use the configured channel+target.
- [ ] Noisy jobs default to delivery.mode: none.
- [ ] One primary route is used for operational nudges; the same update is not sent to multiple places.

---

## Category 14: Platform Alignment & Architecture (tradclaw)

> Source: `tradclaw/tradclaw/openclaw-primitives.md`

- [ ] Bootstrap files injected each session are documented: AGENTS.md, SOUL.md, USER.md, IDENTITY.md, TOOLS.md, HEARTBEAT.md; the scaffold aligns to this list without inventing parallel concepts.
- [ ] Memory model follows OpenClaw: MEMORY.md (durable), memory/YYYY-MM-DD.md (daily), DREAMS.md optional.
- [ ] Heartbeat is documented as a scheduled main-session turn, explicitly not the same as detached cron/background tasks.
- [ ] HEARTBEAT_OK is the standard ack when nothing needs attention.
- [ ] Skill load precedence is documented: workspace skills/ > project skills > personal skills > managed skills > bundled skills.
- [ ] The scaffold repo is a scaffold-not-a-drop-in: tradclaw/ is the setup layer, workspace/ is the copyable template.
- [ ] Heartbeat vs cron distinction is documented and explicit: use heartbeat for batched household checks; use cron for exact-time reminders.
- [ ] TOOLS.md is guidance-only for local tools and conventions; it does not control which tools exist in the runtime.
- [ ] Resources/ and tradclaw/ setup layer are identified as household domain layout, not OpenClaw primitives.

---

## Category 15: Self-Maintenance & Failure Handling (clawchief)

> Source: `clawchief/cron/jobs.template.json`, `clawchief/skills/daily-task-prep/SKILL.md`, README

- [ ] Nightly backup job uses a deterministic allowlisted git flow; it does not do exploratory `git add -A`.
- [ ] Backup job only commits and pushes when the staged diff is non-empty.
- [ ] Backup job stays silent unless push fails or something needs human attention.
- [ ] Self-update job checks for an OpenClaw update and installs it if available; stays silent unless there is a meaningful problem.
- [ ] Both backup and self-update are shipped disabled; they are explicit opt-ins, not defaults.
- [ ] Daily task prep degrades gracefully: if calendar access fails, file-based prep still runs; user is notified only if the failure matters.
- [ ] Failure handling is a policy ("notify only if failure matters") rather than code-side exception handling only.
- [ ] The clawchief improvement program is in the priority map: new OpenClaw capabilities and operator patterns are funneled into concrete tasks or direct internal update passes, not left as vague AI chatter.

---

## Category 16: Quiet-by-Default Contract (both repos)

> Source: `clawchief/workspace/HEARTBEAT.md`, `tradclaw/workspace/HEARTBEAT.md`, `tradclaw/cron/README.md`

- [ ] HEARTBEAT_OK is the valid and expected outcome of a heartbeat run when nothing is newly actionable.
- [ ] "At most one nudge" rule: if there is no EA or biz-dev issue needing attention, heartbeat uses its remaining value for at most one priority nudge.
- [ ] "No repeated near-identical nudges" rule: nudges are not repeated unless there is materially new information, a changed recommendation, or a concrete trigger.
- [ ] Each cron job prompt is written to be quiet by default: "if nothing changed, say nothing"; "if nothing unusual is happening, keep it compact."
- [ ] Afternoon pickup check explicitly says nothing if nothing changed.
- [ ] School deadline sweep suppresses routine marketing noise.
- [ ] Storytime prompt is optional; it is framed as "charming, not spammy."
- [ ] Helper payment reminder avoids duplicate reminders if something is already marked paid.
- [ ] "Be proactive but not bossy" is an explicit rule (tradclaw).
- [ ] Notification fatigue is documented as the #1 killer of autonomous assistants (tradclaw-learnings, "start with the quiet rule").
