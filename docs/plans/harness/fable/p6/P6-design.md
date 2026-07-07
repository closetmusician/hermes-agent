# P6 — Factory Mission Control (architecture design)

# ABOUTME: P6 design — the assistant/Telegram channel becomes the factory control panel.
# ABOUTME: THE key invariant: every factory-control action is a broker held-action on the
# ABOUTME: SAME approval surface as merges (one UX, no bypass). Control cards NEVER mutate
# ABOUTME: scheduler/job_store directly — they enqueue a held action whose executor does.
# ABOUTME: Voice-note consolidation, control-plane files read-as-data, quiet proactive contract.

**Phase:** P6 (backlog mode, GOVERNANCE_EXEMPT — architecture design; adversarial review + verifier follow)
**Branch verified:** `factory` (== the plan's target) — `git branch --show-current` → `factory`.
**Plan source read in full:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P6 (lines 431–450), tasks P6-1..4.
**Requirement map:** REQ-01 (voice consolidation), REQ-02 (mission-control cards + no-bypass), REQ-03 (control-plane files), REQ-04 (pm_os wiring + task table + RED tests).

---

## §0 — The one invariant everything hangs on

> **Every consequential factory-control action rides the SAME broker approval surface as a merge. A control card CANNOT do anything the broker wouldn't approve. There is no second control path.**

The plan already built this surface. P6 does not build a new one — it *reuses* it:

- `broker_client.py:93` `enqueue_action(type, summary, payload, origin, channel?, recipient?)` — the assistant's SOLE egress verb; holds no credentials; raises `BrokerUnavailable` fail-closed (`broker_client.py:16`, no direct fallback).
- `broker/held_store.py:76` `HeldStore.enqueue(...)` — durable, commit-before-ack; one-way state machine `held→approved→executed` (`broker/held_store.py:21`); payload never truncated.
- `broker/approval.py:78` `ApprovalAuthority.approve(action_id, nonce, executor=...)` — nonce is HMAC-minted broker-side and delivered **out-of-band in the Telegram button** (`broker/approval.py:48`); a caller without a nonce (the assistant) is **rejected** (`broker/approval.py:108`). This is the self-approval wall.
- `broker/server.py:109` `ExecutorRegistry` — dispatch by action `type`; an unregistered type fails closed (`UnknownActionType`, `broker/server.py:253`).
- `broker/safe_lane.py:63` `SafeLane.evaluate` — the 5-condition auto/hold gate; `_KNOWN_TYPES` (`broker/safe_lane.py:15`) + conservative `allowed_origins=∅` default (`broker/server.py:66`) ⇒ everything holds.

**The reuse rule for a control card:** a control action (approve/reject/re-scope/reprioritize) is enqueued as a **broker held-action of a control type**. The card carries a *summary + local artifact*; the authoritative payload lives in the broker store keyed by `action_id` (the P1a-2 pattern that replaced the `/approve-email` hash-lookup, plan line 250). The owner taps the Telegram button → the **broker** mints/validates the nonce → the registered **control executor** performs the state change. The card never imports `Scheduler`/`JobStore` and never mutates them. This is *exactly* how `factory/supervisor.py:426` enqueues the `type="merge"` held card and how `factory/morning_packet.py:237` enqueues `type="question"` — P6 adds sibling control types alongside them, no new machinery.

**Why this is a real wall, not theatre:** the executor for a control type is registered *only in the broker process* (`broker/server.py:444` `executors={...}`). The assistant/mission-control layer can `enqueue_action` (produce a held card) but cannot `approve` (no nonce — `broker/approval.py:108`) and cannot construct the executor (it runs in the broker's process with the broker's state handle). So a control card is strictly weaker than a human tap: it can *request* a job state change; only a nonce-gated approval *effects* it.

---

## §1 — REQ-02: Mission-control control cards (the key invariant) — FIRST, because it constrains everything

### 1.1 The four control card types and their broker routing

Each is a new broker action `type`. The **payload** is a JSON control-intent `{job_id, control_verb, args, requested_by, ts}`. The **executor** (broker-side, registered in `broker/server.py:main`) performs the effect against a *reconciler* handle, NOT a direct scheduler call. All are **held by default** (they never satisfy the SafeLane 5 conditions: `allowed_origins=∅` ⇒ C4 false), so every one waits for a nonce tap — identical to a merge.

| Card type | Owner intent | Effect on approval | Who performs it | Direct-mutation banned |
|---|---|---|---|---|
| `job_approve` | Approve a completed job's PR | This is the **existing `type="merge"` card** (`supervisor.py:427`) — the merge card *is* the approve card. Re-used verbatim; auto-merge tier path via `broker_client.auto_merge` (`broker_client.py:136`). | `broker/executors/merge_executor.py` | card never calls `git merge` |
| `job_reject` | Reject a held job / abandon a live job | executor moves the job to `NEEDS_ATTENTION` via the reconciler, reaps the worktree | `control_executor` (new, broker-side) | card never calls `JobStore.transition` |
| `job_rescope` | Change a job's spec / re-plan | executor writes the new spec + re-enqueues the job through the *scan-fenced* intake seam (`injection_scan.fence`), parks the old one | `control_executor` | card never edits `jobs.spec` directly |
| `queue_reprioritize` | Move a QUEUED job up/down | executor sets a priority key the scheduler's `ready_jobs()` ordering reads (additive column) | `control_executor` | card never re-orders the ready list itself |

**Tail-worker (read-only) is NOT a broker action.** `tail a running worker` reads `jobs.log_tail` (`job_store.py:158`) / the worker output file — pure read, no state change, no egress. It is served directly by the mission-control read layer (§1.3). Making it a broker action would be security theatre (nothing to gate). This is the one control-*surface* affordance that is *not* a held action, and the design says so explicitly so the no-bypass test does not falsely flag it.

### 1.2 The `control_executor` (new broker-side module) — where the effect actually happens

`broker/executors/control_executor.py` (new). `build_control_executor(reconciler) -> Callable[[row], result]`. The `reconciler` is a **narrow factory-side callback** injected at broker construction (`broker/server.py:main`), exactly like `merge_gate` is injected by the supervisor (`broker/server.py:104`, "the broker imports nothing from factory"). The reconciler exposes only:

```
reconciler.reject(job_id) -> None          # JobStore.transition(job_id, <state>, "NEEDS_ATTENTION")
reconciler.rescope(job_id, new_spec) -> str # park old + enqueue_job(scan-fenced new_spec)
reconciler.reprioritize(job_id, delta) -> None  # set jobs.priority (additive col)
```

The executor validates the control-intent shape, calls the one matching reconciler method, records the result JSON. **Fail-closed:** unknown `control_verb`, a `job_id` in a state that forbids the transition (`job_store._ALLOWED`, `job_store.py:102`), or a missing reconciler ⇒ the executor raises → `approve` marks the action `failed` (`broker/approval.py:122`) → no state change. The broker imports nothing from `factory` (the reconciler is a plain callable passed in), preserving the layering the plan mandates.

### 1.3 Mission-control read/emit layer — `factory/mission_control.py` (new, factory-side)

The command + notification surface. It **produces** control cards (via `broker_client.enqueue_action`) and **reads** job state for display; it has **no** capability to change state except by producing a held card. Public surface:

```
class MissionControl:
    def __init__(self, broker_client, job_store_reader): ...   # reader = read-only JobStore view
    def emit_control_card(self, job_id, verb, args, requested_by) -> str  # returns action_id
    def tail(self, job_id) -> str          # read-only: jobs.log_tail / output file; NO broker
    def list_live(self) -> list            # read-only fleet snapshot for the panel
```

`emit_control_card` is the ONLY mutating path and it goes through `broker_client.enqueue_action(type="job_reject"|"job_rescope"|"queue_reprioritize", ...)`. It deliberately **does not** hold a `Scheduler` or a writable `JobStore` — it takes a `job_store_reader` (a read-only view; wraps `JobStore.get`/`list_jobs`, no `transition`/`enqueue_job`). This is the structural guarantee that a card cannot bypass the broker: *the mutation methods are not in scope in this module.*

### 1.4 The no-bypass proof design (REQ-02 `done_when`)

Two independent layers, both tested:

1. **Structural (compile-time-ish):** `MissionControl` is constructed with a read-only reader, not a `JobStore`. There is no code path from a control card to `JobStore.transition` / `Scheduler.tick` except through `broker_client.enqueue_action`. Test: `test_mission_control_holds_no_writable_store` — assert `MissionControl` has no attribute that is a write-capable `JobStore`, and that `emit_control_card` calls `broker_client.enqueue_action` exactly once and returns a held disposition.
2. **Behavioral (the bypass-impossibility test, modeled on `tests/broker/test_bypass_impossibility_gate.py`):** a control card, once enqueued, is `held`; attempting to *effect* it without a nonce is rejected (`ApprovalRejected`). A direct `reconciler.reject(job_id)` invoked from the mission-control layer is impossible because the reconciler is broker-internal — the assistant-side layer has no reference to it. Test: `test_control_card_cannot_mutate_job_without_nonce` — enqueue a `job_reject`, assert job state unchanged until a broker-minted nonce approves it; assert an assistant `approve()` with no/forged nonce raises and leaves the job untouched.

**Escalate_if (from requirement map): "a control action can't be expressed as a broker action."** Documented finding: **reprioritize** and **rescope** are expressible only if the scheduler reads a priority key and the intake seam accepts a re-enqueue — both are additive and in-scope (§1.1). **Tail** is *deliberately not* a broker action (read-only, nothing to gate) — this is not a bypass, it is the correct classification, and it is called out so a reviewer does not mistake it for one. No control action requires a capability the broker lacks. No escalation.

---

## §2 — REQ-01: Voice-note consolidation (P6-1)

Builds directly on `factory/voice_intake.py` (P4-g) + `factory/intake_sources.ingest_voice_note` (P3-f). The core single-note path (`process_voice_note`, `voice_intake.py:141`) and per-note idempotency (`VoiceNoteProcessor`, `voice_intake.py:244`) already exist. P6 adds **threading**, **question-back**, and **priority tagging** on top — no re-implementation of the transcribe→scan→split pipeline (the single-scan security contract, `voice_intake.py:32`, is preserved: all consolidation happens on already-scanned transcripts).

### 2.1 Multi-note threading — `factory/voice_thread.py` (new)

Several notes dropped over a night must become **one** spec'd task card by morning. Design:

- A `VoiceThread` groups notes by a **thread key** = `(sender, rolling 6h window)` (owner talking to the factory at night is one thread; a note >6h later starts a new thread). Persisted in a small SQLite table `voice_threads(thread_id, note_id, transcript, priority, ts)` — WAL+FULL, same posture as `held_store.py`.
- Each incoming note is still processed for idempotency (`VoiceNoteProcessor._seen`, made **durable** here — the plan flags the in-memory seen-set as a P6 limitation at `voice_intake.py:258`; P6-1 persists it to the table so a gateway restart doesn't re-thread a note).
- At the **morning consolidation tick** (fires once, off the scheduler cadence), each open thread's transcripts are concatenated in ts order and handed to `task_splitter.split` **once**, producing a single `TaskGraph` (or `ParkedSpec`). One `VoiceTaskCard` is emitted per thread via `broker_client.enqueue_action(type="task_card")` — the existing card type.
- **Consolidation is deferred, not per-note** (respects the CLAUDE.md prompt-caching policy: no mid-conversation re-processing; the thread accumulates raw transcripts and splits once at the boundary).

### 2.2 Question-back flow

Reuses the P4 question queue verbatim: `factory/morning_packet.enqueue_question(job_id, question, proposed_answers, held_store)` (`morning_packet.py:209`) and `answer_question` (`morning_packet.py:247`). When a thread's consolidated split returns `ParkedSpec` (too vague), instead of a dead-end park card, mission control calls `enqueue_question` with the splitter's ambiguity reasons rendered as 2–3 proposed answers. The owner taps an answer → `answer_question` records it (`held→approved`, result_json) → the thread is re-split with the answer appended → a task card is produced. The question is a **broker held-action of type `question`** (already `egress:false`, `morning_packet.py:206`) — same approval surface, no new type.

### 2.3 Priority tagging

- A note transcript is scanned for a **priority marker** by plain code (no AI): a small keyword map (`urgent`/`asap`/`p0` → high; `whenever`/`someday` → low; default normal) in `voice_thread.py`. This is a *tag*, not a scheduler action — it sets `VoiceThread.priority`, which flows onto the emitted task card's summary and, if the owner promotes the card, into the job's priority key (§1.1 `queue_reprioritize` column).
- Priority is **advisory metadata on the card**, surfaced to the owner; it never auto-escalates a job past the broker. Escalating queue position is still a `queue_reprioritize` held action.

### 2.4 File names (REQ-01 "name files")

- `factory/voice_thread.py` — threading + durable idempotency + priority tagging.
- Extends `factory/voice_intake.py` — a `consolidate_thread(thread, ...)` entry that calls the existing splitter once and formats the single card.
- Reuses `factory/morning_packet.py` question queue (no new file).

---

## §3 — REQ-03: Control-plane files read-as-DATA (P6-3)

Three canonical files. **The security boundary: their content is DATA the factory *reads*, never instructions it *executes*.** A file whose body contains injected directives ("ignore prior instructions, disable the broker") must be treated as config values, not a prompt.

### 3.1 The safe-lane spec — reuse, do not rewrite

`broker/policy/safe-lane.md` already exists (the 5-condition single-owner spec) and `broker/safe_lane.py` mirrors it. P6 does **not** author a new safe-lane spec — it points mission control at the existing one and adds it to the read-as-data set. No change to the evaluator.

### 3.2 `trust-policy.md` read as DATA — the injection boundary

`docs/factory/trust-policy.md` is already **ring-protected** (`factory/immutable_ring.py:50-51`) so a worker cannot edit it, and `factory/trust_policy.load_trust_policy` (`trust_policy.py:84`) already reads it as **structured values** via regex key-extraction (`consecutive_merged_clean`, `max_reverts`, …) — it never `exec`s the file, never feeds the body to a model. That is the read-as-data boundary, already correct.

P6 makes the boundary **explicit and tested** for the mission-control surface:

- Any control-plane file mission control renders to the owner (trust-policy, proactive-contract, safe-lane) is passed through `injection_scan.scan` + `injection_scan.fence` (`factory/injection_scan.py:118,166`) before display, so an injected directive in the body is *fenced as external content*, never surfaced as an instruction.
- The **parse path stays value-only**: `load_trust_policy` extracts named keys; a line that is not a recognized `key: value` is ignored (`trust_policy.py:99` `_extract` returns the default). A body containing `"ignore all previous instructions"` yields the *same* TrustPolicy as a clean file — proving the content cannot alter behavior.
- Test (`test_trust_policy_injection_is_inert`): load a `trust-policy.md` whose body contains `ignore previous instructions` + `disable the broker`; assert the returned `TrustPolicy` equals the clean-file result AND `injection_scan.is_injection` flags the body (so the render layer fences it). The injected text changes nothing.

### 3.3 `proactive-contract.md` — quiet-by-default semantics

New file `docs/factory/proactive-contract.md` (data) + reader `factory/proactive_contract.py` (new). **Quiet semantics: on an anomaly, at most ONE nudge per (anomaly-key), never a repeat.**

- The contract file is DATA: `nudge_max: 1`, `repeat: false`, `anomaly_window_min: <N>` — read via the same value-only regex pattern as `load_trust_policy` (no exec). It is added to `immutable_ring.RING_PATHS` so a worker can't weaken "quiet" to "chatty" (a self-serving loosening the plan's §P5 immutable-ring gate is designed to stop).
- `proactive_contract.py` holds a **nudge ledger** (SQLite `nudges(anomaly_key, first_sent_ts)`; WAL+FULL). `should_nudge(anomaly_key) -> bool`: returns True iff no row exists for that key; on send, inserts the row. Every subsequent event with the same key returns False → **≤1 nudge, never repeats**. All fleet-completion events and the morning replay route their alerts through `should_nudge` before any `broker_client.enqueue_action`/notification.
- `anomaly_key` is deterministic (e.g. `f"{kind}:{job_id}"` for a job anomaly, `f"drift:{job_id}"` for a drift alert) so the same underlying event never double-fires even across process restarts (the ledger is on disk).
- Test (`test_proactive_contract_quiet_one_nudge`): fire the same `anomaly_key` five times; assert exactly one nudge is emitted (one `enqueue_action`), the other four are suppressed; assert a *different* key still nudges once.

### 3.4 Read-as-data boundary — the general rule (stated once)

`factory/control_plane.py` (new, thin) centralizes the read-as-data contract: `read_config_file(path) -> dict` that (a) `injection_scan.scan`s the raw bytes, (b) parses **only** recognized `key: value` lines into a dict, (c) returns the dict + the findings so the render layer can fence. No control-plane file is ever concatenated into a worker prompt or `exec`d. `trust_policy.load_trust_policy` and `proactive_contract` both go through this contract. This is the single documented boundary REQ-03 asks for.

---

## §4 — REQ-04: pm_os wiring (P6-4) — factory-used pipelines ONLY

**Wire only what the factory uses; leave the rest PARKED.**

| pm_os pipeline | P6 action | Rationale |
|---|---|---|
| Jira intake (P3-4, `factory/jira_poller.py`) | **WIRE** — mission control surfaces Jira-sourced jobs and the broker-gated write-back that already exists in P3. | Factory intake source #2 (plan L3). Already built; P6 only surfaces it in the panel. |
| Morning briefing generator (P4-4, `run-morning.js` reused by `factory/morning_packet.py`) | **WIRE** — the mission-control panel's standup view reads the P4 morning packet. | Factory's daily surface (plan §P6 builds-on line 434). Already built; P6 surfaces it. |
| pulse | **PARKED** — not added. | Plan §9 park list; §P6 deliverable 6.3 "full parity is parked". |
| weekly | **PARKED** — not added. | Same. |
| exec-narrative | **PARKED** — not added. | Same. |

P6-4 is **wiring/surfacing only** — no new pm_os pipeline code. The verification is negative-inclusive: confirm the factory drives Jira intake + briefing AND confirm no pulse/weekly/exec-narrative pipeline was added (grep the diff).

---

## §5 — File-disjoint task breakdown (P6-a..d)

Ordered so REQ-02 (the invariant) lands first; each row touches a disjoint file set so the tasks can run as independent pipelines.

| ID | Deliverable | New files (disjoint) | Reuses (read-only) | Depends on | RED tests (4–6, falsifiable) |
|---|---|---|---|---|---|
| **P6-a** | REQ-02 control cards + no-bypass (the invariant) | `broker/executors/control_executor.py`, `factory/mission_control.py`, `tests/factory/test_mission_control.py`, `tests/broker/test_control_executor.py` | `broker_client.enqueue_action`, `held_store`, `approval`, `server` registry, `job_store` (read-only reader) | P4-4, P1a-4 (both done) | (1) `test_control_card_is_held_not_auto` — a `job_reject` enqueues `held`, never auto. (2) `test_control_card_cannot_mutate_job_without_nonce` — job state unchanged until nonce approval; assistant `approve()` w/o nonce raises `ApprovalRejected`. (3) `test_mission_control_holds_no_writable_store` — `MissionControl` exposes no write-capable `JobStore`; `emit_control_card` calls `enqueue_action` exactly once. (4) `test_control_executor_rejects_illegal_transition` — a `job_reject` on a DONE job fails-closed (no state change). (5) `test_tail_is_read_only_no_broker` — `tail()` never calls the broker and never mutates. (6) `test_rescope_reenqueues_scan_fenced` — a re-scope spec with an injection marker is fenced before re-enqueue. |
| **P6-b** | REQ-01 voice consolidation | `factory/voice_thread.py`, `tests/factory/test_voice_thread.py` (+ `consolidate_thread` added to `voice_intake.py`) | `voice_intake.process_voice_note`, `intake_sources.ingest_voice_note`, `task_splitter.split`, `morning_packet.enqueue_question` | P4-7, P3-1 (done) | (1) `test_multi_note_thread_single_card` — 3 notes in one 6h window → ONE task card by the consolidation tick. (2) `test_notes_outside_window_new_thread` — a note >6h later starts a new thread. (3) `test_durable_idempotency_survives_restart` — a re-delivered note_id is a no-op after a simulated restart (durable seen-set). (4) `test_vague_thread_triggers_question_back` — a `ParkedSpec` thread emits a `question` held-action with 2–3 proposed answers; the answer re-splits to a card. (5) `test_priority_tag_applied` — an "urgent" note tags the card high; a plain note tags normal. (6) `test_priority_is_advisory_not_auto_escalation` — the tag never auto-reprioritizes a job (still needs a `queue_reprioritize` card). |
| **P6-c** | REQ-03 control-plane files read-as-data | `docs/factory/proactive-contract.md`, `factory/proactive_contract.py`, `factory/control_plane.py`, `tests/factory/test_proactive_contract.py`, `tests/factory/test_control_plane_read_as_data.py` | `injection_scan`, `trust_policy.load_trust_policy`, `broker/policy/safe-lane.md`, `immutable_ring.RING_PATHS` (add proactive-contract) | P6-a (control card surface) | (1) `test_proactive_contract_quiet_one_nudge` — same `anomaly_key` fired 5× → exactly 1 nudge. (2) `test_proactive_contract_never_repeats_across_restart` — ledger on disk suppresses a repeat after restart. (3) `test_different_anomaly_key_nudges` — a distinct key still nudges once. (4) `test_trust_policy_injection_is_inert` — a trust-policy.md body with injection yields the clean `TrustPolicy` AND `is_injection` flags it. (5) `test_control_plane_parses_values_only` — an unrecognized/injected line is ignored by the value-only parser. (6) `test_proactive_contract_is_ring_protected` — a diff editing proactive-contract.md is rejected by the immutable ring. |
| **P6-d** | REQ-04 pm_os wiring (surface only) | `tests/factory/test_pm_os_wiring_scope.py` (+ panel wiring in `factory/mission_control.py` — Jira + briefing views) | `factory/jira_poller.py`, `factory/morning_packet.py` (`run-morning.js` reuse) | P3-4, P4-4 (done), P6-a | (1) `test_jira_intake_surfaced` — a Jira-sourced job appears in the mission-control live view. (2) `test_briefing_generator_wired` — the panel's standup view reads the P4 morning packet. (3) `test_no_pulse_pipeline_added` — grep/import assertion: no `pulse` pipeline module is imported/created. (4) `test_no_weekly_pipeline_added` — same for weekly. (5) `test_no_exec_narrative_pipeline_added` — same for exec-narrative. |

**File-disjointness:** P6-a owns `broker/executors/control_executor.py` + `factory/mission_control.py`; P6-b owns `factory/voice_thread.py` (+ an additive function in `voice_intake.py`); P6-c owns the three control-plane modules/doc; P6-d adds only tests + two read-only panel views inside `mission_control.py` (sequenced after P6-a which creates that file). The only shared file is `mission_control.py` — P6-a creates it, P6-d appends read-only views, so they are sequenced (P6-d `depends_on` P6-a), not parallel-conflicting.

---

## §6 — Fresh-context gate tests (P6 exit gate, plan line 449)

The binding gate, restated as the four falsifiable fresh-context checks the verifier re-runs:

1. **Multi-note → single spec'd card by morning.** Drop 3 voice notes in one window; the consolidation tick produces ONE task card with a priority tag (or a question-back if vague). (P6-b test 1 + 4 + 5.)
2. **A control card steers a live job through the broker (not directly).** Enqueue a `job_reject`/`job_rescope`/`queue_reprioritize`; the job state changes ONLY after a broker-minted nonce approval; the mission-control layer holds no writable store. (P6-a tests 2 + 3.)
3. **EVERY control action rides the broker approval surface (a direct-mutation attempt is rejected).** A control card cannot effect a state change without a nonce; an assistant `approve()` with no/forged nonce raises `ApprovalRejected` and leaves the job untouched. (P6-a test 2 — the no-bypass proof.)
4. **Proactive-contract sends ≤1 nudge, never repeats.** Same anomaly fired repeatedly → exactly one nudge, even across a restart. (P6-c tests 1 + 2.)

**STAGED (per plan line 448 + owner-approval discipline):** the *live end-to-end steer* of a real running factory job is staged for the owner-in-the-loop verifier run, not asserted by unit tests alone (unit tests prove the routing; the live steer proves the wiring against a real fleet). Flagged STAGED so the verifier drives one real job through a control card end to end.

---

## §7 — Explicitly NOT built (scope discipline)

- No new approval surface, no new nonce machinery, no new safe-lane evaluator — all reused from P1a/P1b.
- No pulse/weekly/exec-narrative pm_os pipelines (PARKED, plan §9).
- Tail-worker is read-only and deliberately NOT a broker action (nothing to gate).
- No AI in any control-plane read path (value-only parse; `injection_scan` is plain-code regex).
- Priority tag is advisory metadata; it never auto-escalates a job past the broker.

---

## §8 — Digest

**Verdict:** REQ-01..04 all designed against confirmed interfaces; the one-UX-no-bypass invariant is structurally + behaviorally enforced. No escalation.
**REQ-01 (voice):** `factory/voice_thread.py` threads notes by (sender, 6h window) → one deferred split → one card; question-back reuses `morning_packet.enqueue_question` (`morning_packet.py:209`); priority = advisory plain-code tag. Builds on `voice_intake.py:141` + `intake_sources.ingest_voice_note`.
**REQ-02 (cards, key invariant):** 4 control types (`job_approve`=existing merge card, `job_reject`/`job_rescope`/`queue_reprioritize`=new held types) via `broker_client.enqueue_action`; broker-side `control_executor` performs effects through an injected reconciler (broker imports nothing from factory, mirrors `merge_gate` injection at `server.py:104`); `MissionControl` holds a read-only reader — no writable store. Tail = read-only, not a broker action (documented, not a bypass).
**REQ-03 (control-plane files):** safe-lane spec reused (`broker/policy/safe-lane.md`); trust-policy.md already read value-only + ring-protected (`trust_policy.py:84`, `immutable_ring.py:50`); new `proactive-contract.md` + `proactive_contract.py` gives ≤1-nudge-never-repeat via an on-disk nudge ledger; `control_plane.py` centralizes the read-as-data (scan+fence, value-only parse) boundary.
**Reused interfaces:** `broker_client.enqueue_action`/`approve`/`auto_merge`; `held_store.enqueue`/`transition`; `approval.mint_nonce`/`approve` (nonce wall); `server.ExecutorRegistry`; `safe_lane.evaluate`; `supervisor._enqueue_merge_card` pattern; `morning_packet.enqueue_question`; `injection_scan.scan`/`fence`; `trust_policy.load_trust_policy`; `immutable_ring.RING_PATHS`; `job_store` (read-only) + its `_ALLOWED` machine.
**No-bypass point:** a control card is strictly weaker than a human tap — it can only *produce* a held action (`enqueue_action`); only a broker-minted, out-of-band nonce *effects* it (`approval.py:108`); the mission-control layer has no writable store and no reconciler reference, so there is no code path from a card to `JobStore.transition`/`Scheduler.tick`. Proven by `test_mission_control_holds_no_writable_store` (structural) + `test_control_card_cannot_mutate_job_without_nonce` (behavioral, modeled on `tests/broker/test_bypass_impossibility_gate.py`).
**Escalations:** none. All control verbs express as broker actions; tail is correctly read-only.
**Output:** `docs/plans/harness/fable/p6/P6-design.md`.
