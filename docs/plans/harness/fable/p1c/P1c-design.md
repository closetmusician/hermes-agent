# ABOUTME: P1c design — daily chief-of-staff essentials (inbox sweep, morning triage,
# ABOUTME: broker-routed sends with exactly-one-send, calendar + meeting-prep to Telegram)
# ABOUTME: built ON TOP of the already-shipped P1a broker safe-lane. Every send is a held
# ABOUTME: action on the ONE broker path; exactly-once rides the broker's stable action IDs.
# ABOUTME: Design doc only — no code. Backlog mode, GOVERNANCE_EXEMPT (verifier follows).

# P1c — Assistant Essentials Design

**Phase:** P1c (parallel lane with P2). **Branch:** `factory`. **Status:** DESIGN.
**Plan source:** `docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md` §P1c (P1C-1..4).

**One-line verdict:** All four requirements are designable as thin orchestration on top of
already-built primitives. The exactly-one-send invariant (REQ-03) is *already solved at the
broker layer* (`broker/approval.py` idempotent-by-action_id); P1c's only new correctness work
is a **send-intent ledger** that guarantees one broker action_id per logical send so a retry
never *enqueues a second action*. Inbox/triage/calendar reuse pm_os scripts wholesale.

---

## §0 — Repo split (which piece lands where)

| Piece | Repo | Why |
|---|---|---|
| Inbox sweep + `processed.json` ledger (REQ-01) | **pm_os** (`~/Code/pm_os`) | Reads Teams/Outlook via pm_os scripts; ledger lives beside pm_os state. |
| Triage skill + `priority-map.md` (REQ-02) | **pm_os** | Reuses `config/routing-policy.json` + `classify-messages.js`. |
| Send-intent ledger + confirm-gate reconciliation (REQ-03) | **hermes** (`~/Code/hermes`) | Sits between the assistant and `BrokerClient`; broker is a hermes component. |
| Calendar read + meeting-prep note (REQ-04) | **pm_os** (assembly) → **hermes** (delivery) | pm_os owns calendar/mail plumbing; delivery rides the hermes Telegram gateway. |
| The scheduling wrapper that fires the daily sweep | **pm_os** (skill) + **hermes** (Telegram morning channel egress) | pm_os generates; hermes delivers via broker. |

**Rule enforced throughout:** the assistant NEVER sends directly. Every consequential
egress is `BrokerClient.enqueue_action(...)` (`broker_client.py:93`). There is no second send
path (the client deliberately exposes no `send()`/`git_push()` — `broker_client.py:34`).

---

## §1 — Reused interfaces (cited, real file:line)

### Broker (hermes — SHIPPED, P1a)
- **`BrokerClient.enqueue_action(type, summary, payload, origin, channel, recipient)`**
  → returns `{action_id, disposition}` — `broker_client.py:93-119`. `disposition` is `"held"`
  or `"auto_sent"`. This is the assistant's SOLE egress verb.
- **`BrokerClient` exposes NO direct send** — `broker_client.py:28-35` (ABOUTME + class doc).
  Fail-closed: broker unreachable ⇒ `BrokerUnavailable` raised, never a silent direct send
  (`broker_client.py:16-22, 86-88`).
- **Safe-lane 5-condition evaluator** `SafeLane.evaluate(action)` — `broker/safe_lane.py:63-109`.
  Auto-sends IFF C1..C5 all True; conservative default (C4 authority fails on empty
  `allowed_origins`) so almost nothing auto-sends (`broker/safe_lane.py:96-100`). Known types:
  `{message, git_push, git_push_force, pm_os_write, merge}` (`broker/safe_lane.py:15`).
- **Stable action IDs** `new_action_id()` — ULID, time-sortable, **NEVER derived from payload**
  (`broker/action_id.py:39-61`). This is the exact fix for the /approve-email hash bug.
- **Durable held store** `HeldStore` — commit-before-ack, WAL+synchronous=FULL, one-way state
  machine `held→approved→executed|failed`, `rejected` terminal (`broker/held_store.py:21-27,
  76-115`). Payloads never truncated (`broker/held_store.py:117-127`). Survives restart.
- **Idempotent approval** `ApprovalAuthority.approve(action_id, nonce, executor)` —
  `broker/approval.py:78-118`. **Already exactly-once by action_id:** if the row is already
  `executed`, returns the cached `result_json` WITHOUT re-running the executor
  (`broker/approval.py:105-106`); nonce burned on use so a replay is *rejected*
  (`broker/approval.py:100-101`). One-way transition guards double-decide
  (`broker/held_store.py:144-168`).

### pm_os (SHIPPED — REUSE, do not rebuild)
- **Classification** `bin/classify-messages.js` — each message → `{classification:
  action_required|fyi|noise, urgency:0-3, reasoning}` (`classify-messages.js:5, 87-88`).
- **Routing/priority policy** `config/routing-policy.json` — sender tiers
  (`elt/direct/skip_level/peer/unknown`), keyword triggers, fallback-by-tier
  (`routing-policy.json:115-128`). **This IS the priority-map source** for REQ-02 (§3).
- **Morning pipeline** `bin/run-morning.js` — token check → parallel fetch → merge/dedup →
  classify → prioritize → draft → report (`run-morning.js:4`). Reused as the sweep engine.
- **Mail/Teams read** `bin/outlook-read-mail.js`, `bin/teams-read-chats.js`.
- **Send dispatch** `bin/run-send.js` — "Never auto-sends; caller responsible for
  confirmation" (`run-send.js:317`). In P1c this is REPLACED at the egress boundary by the
  broker (§4); run-send's *draft discovery/sort* is reused, its *dispatch* is not.
- **Telegram inbox seam** `~/Code/pm_os/state/telegram-inbox.jsonl` exists (0B, ready). The
  pm-cmd forwarder (REQ-04 of an earlier commit) appends YK DM lines to it.
  **UNVERIFIED:** the forwarder function is not present in the current `factory`-branch
  telegram platform tree (`plugins/platforms/telegram/adapter.py` has 0 hits for
  `PMCMD_INBOX_PATH`/`pmcmd_forward`) — the clean-base migration appears to have dropped it.
  P1c task **P1c-a** must re-verify/re-land the forwarder or the sweep has no Telegram intake.

### hermes gateway (SHIPPED)
- Telegram platform: `plugins/platforms/telegram/adapter.py` (+ `telegram_ids.py`,
  `telegram_network.py`). The morning channel (REQ-04 delivery) egresses as a broker
  `message` action, NOT a direct `sendMessage`.

---

## §2 — REQ-01: Daily inbox sweep + idempotency ledger (pm_os)

**Goal:** a second sweep over the same inbox acts on ZERO already-handled items.

### Ledger design
Two files under `~/Code/pm_os/state/` (mirrors plan 1c.1: `tasks.md` + `processed.json`):

- **`processed.json`** — the idempotency ledger. A JSON object keyed by **`processed_key`**:
  ```json
  {
    "<processed_key>": { "first_seen_ts": 1720300000000, "handled_ts": 1720300005000,
                         "source": "outlook|teams|telegram", "disposition": "action|fyi|noise",
                         "action_id": "<broker action_id or null>" }
  }
  ```
- **`tasks.md`** — human-readable canonical list of open ACTION items (the triage output,
  §3). Not the dedup authority; `processed.json` is.

### `processed_key` (the dedup key) — chosen deliberately
```
processed_key = "<source>:<stable_source_id>"
```
- **Outlook:** `stable_source_id` = the Graph message `id` (immutable server id). NOT the
  subject/body (those change on edits/forwards → the /approve-email hash-bug class).
- **Teams:** `stable_source_id` = `chatId + "/" + messageId` (Graph chat message id).
- **Telegram DM:** `stable_source_id` = Telegram `message_id` (already in the inbox JSONL line).

**Why an id, not a hash:** the plan's own crux (§0) and `broker/action_id.py:5-6` both call out
that deriving keys from a *copy of the body* is the four-way /approve-email bug. We key on the
provider's immutable message id. A content hash is kept ONLY as a secondary collision check
for sources with no stable id (none of the three above — so hash is unused in v1, noted for
future sources).

### Sweep flow
```
1. run-morning.js fetch stage → merged messages (each carries source + stable id)  [REUSE]
2. For each message m:
     key = processed_key(m)
     if key in processed.json: SKIP (already handled — zero re-action)   ← dedup gate
     else: mark first_seen, pass to triage (§3)
3. Triage assigns disposition; write processed.json[key].handled_ts + disposition
4. processed.json write is atomic: write tmp + os.rename (crash-safe; partial sweep leaves
   only fully-handled keys committed)
```
**Idempotency proof (the gate):** run the sweep twice on the same fixture inbox. Run 2 finds
every key already in `processed.json` at the dedup gate ⇒ zero items pass to triage ⇒ zero
sends/files/notifies. Ledger is the single source of truth; a crash mid-sweep re-processes only
*un-committed* keys (at-least-once fetch, exactly-once *handling* via the key).

**REQ-01 done-when:** sweep flow above + `processed.json` schema + `processed_key` rule +
atomic-write + dedup-gate. ✔

---

## §3 — REQ-02: Morning triage skill (ACTION / FYI / NOISE) (pm_os)

**Goal:** every item lands in exactly one bucket via a `priority-map.md`, with literal pm_os
commands; no item unclassified.

### Buckets (closed set)
`ACTION` (needs a reply/decision) · `FYI` (read, no reply) · `NOISE` (suppress). Maps directly
onto `classify-messages.js`'s `action_required | fyi | noise` (`classify-messages.js:87`) — no
new classifier, reuse the haiku one.

### `priority-map.md` schema (the sender/topic → tier map)
A thin, human-editable Markdown wrapper that **references** `config/routing-policy.json` (the
real engine) rather than duplicating it (DRY — one canonical home, per memory-routing anti-rules):

```markdown
# priority-map.md  (sender/topic → ACTION tier)
Engine: config/routing-policy.json (authoritative). This file documents overrides + intent.

## Sender tiers  (→ routing-policy.json sender_tiers.tier_scores)
| Tier | Senders (examples) | Fallback bucket |
|------|--------------------|-----------------|
| elt/direct | <names> | ACTION |
| skip_level/peer | <names> | FYI |
| unknown | * | NOISE |

## Topic keyword triggers  (→ routing-policy.json keyword_triggers)
| Keyword/topic | Forces bucket |
| "approval needed", "blocker", "P0" | ACTION |
| "FYI", "no action" | FYI |
| newsletters, digests, automated senders | NOISE |
```

### Bucket rules (deterministic, one bucket each)
Resolution order = `routing-policy.json` `family_order`
(`routing-policy.json:17`): `system_senders → incident_overrides → deterministic →
keyword_triggers → reply_awareness → noise_defaults → sender_tiers`, `on_conflict:
highest_priority_wins` (`routing-policy.json:18`). This ordering *guarantees exactly one bucket*
(first family to match wins; `sender_tiers.fallback_by_tier` is the last-resort default so
nothing is unclassified — `routing-policy.json:121-127`).

### Triage flow (literal commands)
```
node ~/Code/pm_os/bin/classify-messages.js --in <merged.json> --out <classified.json>   [REUSE]
→ ACTION items → tasks.md (canonical, REQ-01) + draft (run-send draft discovery only)
→ FYI items    → briefing "FYI" section
→ NOISE items  → dropped (logged to processed.json disposition="noise")
```
**REQ-02 done-when:** triage skill design + `priority-map.md` schema + deterministic
one-bucket rules (family_order + fallback default). ✔

---

## §4 — REQ-03: Broker-routed sends + EXACTLY-ONE-SEND (hermes) — the crux

**Goal:** every consequential send goes through the broker safe-lane; a pm-send confirm-gate
guarantees EXACTLY ONE approval per send; a **retry after approval must be reconciled and
BLOCKED from double-sending**.

### What the broker already gives us (do NOT rebuild)
The broker is *already* exactly-once **per action_id**: `ApprovalAuthority.approve()` returns
the cached result on an already-`executed` row without re-running egress
(`broker/approval.py:105-106`), and the one-way state machine forbids re-executing
(`broker/held_store.py:21-27`). **So a double-*approval* of the same action_id cannot
double-send.**

### The gap P1c must close (the only new correctness work)
The residual double-send risk is NOT double-approval — it is **a retry creating a SECOND
action_id for the same logical send.** If the assistant's send step runs twice (crash + retry,
or the sweep re-emitting a draft), a naive implementation calls `enqueue_action` twice →
two held actions → two approvals → two sends. The broker can't dedup these because they have
different (correct, non-payload-derived) action_ids.

**Fix: a send-intent ledger, assistant-side, keyed by a caller-chosen idempotency key.**

### Send-intent ledger design (hermes — `~/.hermes/broker/send_intents.db`, SQLite)
```
send_intents(
  intent_key   TEXT PRIMARY KEY,   -- caller-derived, stable per logical send (below)
  action_id    TEXT,               -- the broker action_id this intent mapped to (nullable until enqueued)
  created_ts   INTEGER NOT NULL,
  state        TEXT NOT NULL        -- 'enqueued' (has action_id) — terminal for dedup purposes
)
```
- **`intent_key`** derivation (stable across retries, unique per logical send):
  `intent_key = sha256(draft_source_key + ":" + recipient + ":" + normalized_body_len_and_head)`
  where `draft_source_key` is the `processed_key` (§2) of the item being replied to (or the
  draft file path for composed sends). The key ties the *intent to reply to message X* — a
  retry of the same reply computes the same key. (This hash is an **intent key**, never the
  broker action_id — the broker id stays ULID, `broker/action_id.py`.)

### Enqueue-with-reconciliation flow (the exactly-once wrapper)
```
def enqueue_send(intent_key, *, type, summary, payload, recipient, origin):
    # 1. Reconcile: has this logical send already been enqueued?
    row = send_intents.get(intent_key)
    if row and row.action_id:
        return {"action_id": row.action_id, "deduped": True}   # ← retry BLOCKED here
    # 2. First time: enqueue on the broker (the SOLE egress path)
    res = broker_client.enqueue_action(type=type, summary=summary, payload=payload,
                                       recipient=recipient, origin=origin)   # broker_client.py:93
    # 3. Record intent→action_id BEFORE returning (commit-before-ack, mirrors held_store)
    send_intents.insert(intent_key, action_id=res["action_id"], state="enqueued")  # atomic upsert
    return res
```
- Step 3 uses `INSERT ... ON CONFLICT(intent_key) DO NOTHING` then re-reads: if two racing
  retries both reach step 2, exactly one INSERT wins; the loser re-reads the winner's
  action_id and returns `deduped` — so **at most one broker action per intent_key**, even
  under concurrency. (Same optimistic-concurrency shape as `held_store.transition`,
  `broker/held_store.py:157-168`.)

### End-to-end exactly-once chain (both halves)
1. **One action per intent** — the send-intent ledger (above) blocks a retry from creating a
   second action_id.
2. **One send per action** — the broker's idempotent approve (`broker/approval.py:105-106`)
   blocks a second approval from re-sending.
Together: **retry-before-approval** → deduped at the intent ledger; **retry-after-approval**
(re-tap / re-emit) → the same action_id is already `executed` → cached result, no re-send.

### Confirm-gate (pm-send)
The draft goes out ONLY as a broker held action; the Telegram card (from P1a's generic
held-action surface) carries the summary + [Approve]/[Reject] + nonce. `origin` is set so the
safe-lane holds by default (C4 conservative — `broker/safe_lane.py:96-100`); a consequential
reply is NOT on the near-empty allow-list, so it holds for one human approval. Exactly one
approval ⇒ exactly one send.

**REQ-03 done-when:** send flow through broker (enqueue_action, no separate path) + exactly-once
reconciliation using the broker's stable action IDs (send-intent ledger + idempotent approve). ✔

---

## §5 — REQ-04: Calendar + meeting-prep note (pm_os assemble → hermes deliver)

**Goal:** read calendar; for a meeting assemble attendees + past-week related threads + open
items → a prep note to the Telegram morning channel, zero manual steps.

### Data sources (all existing pm_os plumbing — no new integrations)
| Datum | Source | Script |
|---|---|---|
| Calendar events (attendees, time) | Graph calendar via FOCI token | `bin/outlook-read-mail.js` calendar mode / Graph calendar endpoint (same auth as mail) |
| Related past-week mail threads | Outlook | `bin/outlook-read-mail.js` filtered by attendee/subject |
| Related past-week Teams threads | Teams | `bin/teams-read-chats.js` filtered by attendee |
| Open action items | `tasks.md` (REQ-01/02 output) | grep tasks.md for attendee/topic |

### Assembly flow (zero manual steps)
```
1. Read calendar events for the day (attendees, subject, start time).           [pm_os]
2. For the next meeting M:
     attendees   = M.attendees
     threads     = mail+teams from last 7d where sender/participant ∈ attendees
                   OR subject ~ M.subject                                        [REUSE readers]
     open_items  = tasks.md rows tagged to any attendee or M.subject
3. Render prep note (Markdown): who's attending · related threads (links/snippets) · open items
4. Deliver: broker_client.enqueue_action(type="message", channel="telegram",
            recipient=<morning-channel>, summary="Prep: <M.subject>", payload=<note>,
            origin="meeting-prep")                                               [hermes/broker]
```
Delivery is a broker `message` action like every other send — the morning channel is not a
special-cased direct `sendMessage`. Whether it auto-sends or holds is the safe-lane's call
(a prep note to the owner's own channel is a candidate for the allow-list once trust widens;
default = held, which is acceptable — the owner taps once).

**Real-meeting run is STAGED** (needs a live calendar) — see §7.

**REQ-04 done-when:** meeting-prep flow + data sources + assembly + broker delivery. ✔

---

## §6 — Task breakdown (file-disjoint P1c-a..d)

All rows depend on the P1a broker approval surface being live (SHIPPED). Files are disjoint so
the four tasks parallelize.

| ID | Task | Repo | Primary files (disjoint) | Depends |
|---|---|---|---|---|
| **P1c-a** | Inbox sweep + `processed.json` ledger (REQ-01). Re-verify/re-land the Telegram inbox forwarder (UNVERIFIED, §1) OR document it as a staged gate. | pm_os | `bin/inbox-sweep.js`, `state/processed.json` (schema), `state/tasks.md`; forwarder re-check in hermes `plugins/platforms/telegram/adapter.py` | P1a |
| **P1c-b** | Triage skill + `priority-map.md` (REQ-02). Wrap `classify-messages.js`; author `priority-map.md` referencing `routing-policy.json`. | pm_os | `skills/pm-morning/priority-map.md`, triage wrapper in `bin/inbox-sweep.js` triage stage (distinct function from P1c-a's fetch/dedup) | P1c-a |
| **P1c-c** | Send-intent ledger + confirm-gate reconciliation (REQ-03). New `send_intents.db` + `enqueue_send()` wrapper over `BrokerClient`. | hermes | `hermes_cli/send_intents.py` (ledger + `enqueue_send`), tests `tests/broker/test_send_intents.py` | P1a |
| **P1c-d** | Calendar read + meeting-prep note (REQ-04). Assemble note from calendar+mail+teams+tasks.md; deliver via broker. | pm_os (assemble) + hermes (deliver) | pm_os `bin/meeting-prep.js`; hermes delivery reuses P1c-c `enqueue_send` | P1c-a |

**File-disjointness note:** P1c-a owns the sweep *fetch/dedup*; P1c-b owns the *triage* stage
(separate function) + `priority-map.md`; P1c-c is hermes-only (`send_intents.py`); P1c-d is a
new `meeting-prep.js` reusing P1c-c's egress. No two tasks write the same function.

---

## §7 — Acceptance-test plan (RED first) + staged gates

TDD is MANDATORY. Tests use **fixtures** for inbox/calendar (real-data runs are staged). The
**exactly-once test is the critical gate.**

| Test | REQ | Fixture | Assertion (RED → GREEN) |
|---|---|---|---|
| `test_sweep_no_double_process` | 01 | two identical fixture inboxes | 2nd sweep: every `processed_key` hits the dedup gate; zero items reach triage; zero enqueue_action calls. |
| `test_processed_key_is_message_id_not_body` | 01 | two msgs, same body, different ids | both processed (distinct keys); an *edited* body with same id ⇒ still one key (no re-process). |
| `test_triage_exactly_one_bucket` | 02 | sample Teams/Outlook items across tiers | each item → exactly one of ACTION/FYI/NOISE per `family_order`; none unclassified. |
| **`test_exactly_one_send_on_retry`** ⭐ | 03 | one draft, same `intent_key` | **approve, then trigger a retry (re-emit + re-tap): assert exactly ONE broker action_id, ONE `executed` row, ONE send. Second enqueue_send returns `deduped:True`; second approve returns cached result, executor NOT re-invoked.** |
| `test_retry_before_approval_dedups` | 03 | two enqueue_send, same key, race | at most one held action created (ON CONFLICT DO NOTHING); loser gets winner's action_id. |
| `test_send_only_via_broker` | 03 | — | no code path sends outside `BrokerClient.enqueue_action`; broker down ⇒ `BrokerUnavailable`, no fallback. |
| `test_meeting_prep_note_shape` | 04 | fixture calendar+mail+teams+tasks.md | note contains attendees + ≥1 related thread + open items; delivered as a broker `message` action (mock broker asserts `enqueue_action` called once). |

⭐ = **the critical exactly-once gate.** It must be RED first (fails without the send-intent
ledger + relies on the shipped idempotent approve), then GREEN. It encodes the plan's binding
gate: "a retry cannot double-send."

### Staged gates (need real data — verifier records, owner flips)
| ID | Gate | Why staged | Action to flip |
|---|---|---|---|
| SG-P1c-1 | Real inbox sweep processes no item twice | Needs a live Outlook/Teams inbox + valid FOCI token | Run `bin/inbox-sweep.js` twice against the live inbox; confirm `processed.json` grows only on run 1. |
| SG-P1c-2 | Real meeting gets a zero-manual-step prep note | Needs a live calendar with a real meeting | Run `bin/meeting-prep.js`; confirm a prep note (attendees+threads+open items) lands in the Telegram morning channel before the meeting. |
| SG-P1c-3 | Telegram inbox forwarder live | Forwarder UNVERIFIED on `factory` branch (§1) | Re-land/verify `PMCMD_INBOX_PATH` forwarder in `adapter.py`; send a YK DM, confirm a line appends to `state/telegram-inbox.jsonl`. |

---

## §8 — Binding exit gate (from plan §P1c)
1. Repeated inbox sweep processes no item twice (idempotency ledger holds) — REQ-01, §2.
2. Every consequential comms action rides the ONE P1a broker approval surface, with **exactly
   one approval per send** (a retry cannot double-send) — REQ-03, §4, ⭐ test.
3. ≥1 real meeting gets a zero-manual-step prep note (calendar → attendees+threads+open items →
   Telegram) — REQ-04, §5 (real-meeting run = SG-P1c-2, staged).

**P1c must not delay P2's first-magic gate** (parallel lane; if effort contends, P2 wins).
