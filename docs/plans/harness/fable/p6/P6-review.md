# P6-REVIEW — Adversarial review of Factory Mission Control (P6-design.md)

**Reviewer role:** GOVERNANCE_EXEMPT adversarial reviewer (read-only; this artifact only).
**Branch verified:** `factory` (`git branch --show-current` → `factory`).
**Target:** `docs/plans/harness/fable/p6/P6-design.md` (design doc; **no P6 implementation exists yet** — this reviews the design, its reuse claims, and the RED test plan).
**Crown invariant under attack:** every consequential factory-control action rides the SAME broker held-action + out-of-band nonce surface as a merge; a control card is strictly weaker than a human tap and CANNOT bypass the broker.

**Verdict: SHIP-WITH-FIXES.** The no-bypass invariant is architecturally sound and grounded in confirmed seams. The nonce wall, out-of-band mint, executor-registry dispatch, read-only-reader structural guard, and read-as-data value-only parse are all real and correctly cited. Findings are all P2 (design-completeness gaps the builder must close), no P0/P1. The design's own fail-closed posture absorbs the two gaps I found.

- **P0:** 0
- **P1:** 0
- **P2:** 3

---

## REQ-01 — Reuse-claim verification (claim | loc | verdict)

| Claim (from design) | Cited loc | Verdict | Evidence |
|---|---|---|---|
| `enqueue_action` is the assistant's sole egress verb, credential-free, fail-closed | `broker_client.py:93` | **CONFIRMED** | Method at line 93; raises `BrokerUnavailable` (line 86-88), no direct-send fallback; docstring "SOLE egress verb". |
| Nonce is HMAC-minted broker-side, delivered out-of-band in the Telegram button, never over the enqueue socket | `broker/approval.py:48` (mint), `:108` (reject) | **CONFIRMED** | `mint_nonce` at 48 stores in `_live`, returns nonce to caller (the broker/gateway), never over the client socket. `_rpc_*` set (`server.py:28-39`) has NO mint verb. `approve` rejects invalid/absent nonce at `approval.py:108-109`. `server.py:410 mint_approval_nonce` explicitly "NOT an RPC". |
| A caller without a nonce (the assistant) is rejected | `broker/approval.py:108` | **CONFIRMED** | `_validate` returns False on `nonce=None` (line 71) → `ApprovalRejected` (109). `broker_client.approve` docstring (124-131) confirms the assistant has no nonce. |
| control_executor injected as a broker-side reconciler callable, mirroring the merge_gate injection ("broker imports nothing from factory") | `broker/server.py:104` | **CONFIRMED (with loc nuance — P2-1)** | The merge_gate injection is real: `merge_gate` kwarg at `server.py:91`, stored at `:113`, and `main()` (453-457) deliberately does NOT wire it — "the broker imports nothing from factory — the gate is constructed by the supervisor and passed in". Line 104 is inside the docstring, not the injection statement (kwarg is 91, assignment 113). Pattern claim CONFIRMED; exact line is cosmetic. |
| ExecutorRegistry dispatches by `type`; unknown type fails closed | `broker/server.py:109` / `:253` | **CONFIRMED** | `ExecutorRegistry` at `server.py:109`; `UnknownActionType` surfaced as error frame at `server.py:253`; registry `get()` raises on miss (`executors/__init__.py:59-63`). A new `job_reject`/`job_rescope`/`queue_reprioritize` type MUST be added to the `executors={...}` dict in `server.main` — additive, no `_rpc_approve` edit. Matches the P5 `retro_diff` precedent (`server.py:451`). |
| The merge card is enqueued `type="merge"` held by the supervisor (the pattern P6 mirrors) | `supervisor.py:427` | **CONFIRMED** | `self._held.enqueue(type="merge", ..., state="held")` at `supervisor.py:426`. Supervisor "does NOT approve — it only enqueues held" (407-408); `approve_merge` routes over the socket, holds no mint (459-467). Design cites :427; actual enqueue is :426 (off-by-one, cosmetic). |
| Voice single-scan: consolidation happens on already-scanned transcripts | `voice_intake.py:141` / `intake_sources.ingest_voice_note` | **CONFIRMED for the single-note path; threading path is UNBUILT (see P2-2)** | `process_voice_note` at `voice_intake.py:141`; `ingest_voice_note` scans+fences the transcript BEFORE `split()` (`intake_sources.py:290-293`). The per-note scan is real. The design's *threading* concatenation (§2.1) is new code not yet written — see REQ-03 attack. |
| trust-policy.md is read value-only (regex key-extract, no exec) | `trust_policy.py:84` | **CONFIRMED** | `load_trust_policy` at 84; `_extract` (99-106) is a `re.search` for `key: value`; unrecognized lines return the default (102). No `exec`, no model feed. |
| trust-policy.md is ring-protected | `immutable_ring.py:50` | **CONFIRMED** | `RING_PATHS` includes `trust-policy.md` and `docs/factory/trust-policy.md` (`immutable_ring.py:50-51`). The ring also guards `factory/injection_scan.py` (56) and itself (55). |
| `injection_scan.scan` / `fence` / `is_injection` exist | `injection_scan.py:118,166` | **CONFIRMED** | `scan` at 118, `is_injection` at 156, `fence` at 166. |
| `morning_packet.enqueue_question` reused for question-back (egress:false) | `morning_packet.py:209` | **UNVERIFIED (not opened)** | Cited but not read this pass. Low risk — the pattern (a `question` held-action) is consistent with the confirmed broker surface. Builder should confirm the exact signature at build time. |
| `job_store.log_tail` field for read-only tail | `job_store.py:158` | **CONFIRMED** | `log_tail TEXT` column at schema line 158. Tail read is a pure column read. |

**REQ-01 verdict:** all load-bearing seams CONFIRMED. Two cosmetic line-number drifts (server.py:104, supervisor.py:427 — see P2-1). One unopened low-risk cite (morning_packet). No WRONG cites → no escalation.

---

## REQ-02 — No-bypass ATTACK table (THE invariant)

| Control action | Routes through broker + nonce? | Can it bypass? | Severity |
|---|---|---|---|
| `job_approve` (= existing `type="merge"` card) | YES — reuses the proven `supervisor._enqueue_merge_card` + `approve_merge` nonce path | **NO** — merge is `irreversible_types` (`server.py:65`), never auto-sends; only nonce or the tier-gated `auto_merge` (itself broker-internal-mint) releases it | safe |
| `job_reject` | YES — held `type="job_reject"`; effect via broker-side `control_executor` → `reconciler.reject` → `JobStore.transition(...→NEEDS_ATTENTION)` | **NO** — `MissionControl` holds only a `job_store_reader` (no `transition`); the reconciler lives broker-side and is never referenced by the assistant layer | safe (with P2-3 caveat) |
| `job_rescope` | YES — held `type="job_rescope"`; effect via `reconciler.rescope` which re-enqueues through the scan-fenced intake seam | **NO** — same structural wall; re-scope spec is re-scanned before re-enqueue | safe |
| `queue_reprioritize` | YES — held type; effect sets a priority column `ready_jobs()` reads | **NO** — the column is broker-executor-written; the card cannot re-order the ready list itself | safe (with P2-2 caveat: column does not exist yet) |
| `tail` (read-only) | N/A — deliberately NOT a broker action | **NO mutation possible** — reads `job_store.log_tail` (`:158`) / output file only; no `transition`, no `enqueue_action`, no egress | correct classification, not a bypass |

**Can MissionControl hold a writable handle?** The design's structural guarantee (§1.3, §1.4) is that `MissionControl.__init__` takes a `job_store_reader` (read-only view wrapping `get`/`list_jobs`), NOT a `JobStore`. The mutation methods (`JobStore.transition` at `job_store.py:320`, `enqueue_job` at `:272`) are then **out of scope in the module** — there is no attribute through which a card reaches them. This is the correct design: **capability is removed by construction, not by discipline.** The test `test_mission_control_holds_no_writable_store` (P6-a test 3) is the right assertion. ⇒ CONFIRMED-safe, contingent on the builder actually passing a read-only reader and NOT a `JobStore` (the test enforces this).

**Can a card self-mint the nonce (the P1b P0 re-check)?** NO. Mint is `ApprovalAuthority.mint_nonce` (`approval.py:48`), called only in-process by the broker (`server.py:410 mint_approval_nonce`, docstring: "NOT an RPC method"). The `_CLIENT_METHODS` set (`server.py:28-39`) has no mint verb. The one no-nonce release path (`auto_merge`, `server.py:325`) mints **internally** only after its own server-side gate passes and only for `type=="merge"` (fail-closed on any other type, `server.py:348-349`). A control card cannot reach the mint. ⇒ CONFIRMED-safe.

**Is the tail path truly read-only?** YES — `log_tail` is a column read; the design explicitly forbids `tail()` from calling the broker or mutating (P6-a test 5). No exfiltration path: tail returns worker output lines to the owner's own panel, not to any external egress (egress is broker-only). ⇒ CONFIRMED-safe.

**REQ-02 verdict: the crown invariant holds.** No control action can mutate the fleet without a broker nonce. No card can self-mint. Tail is correctly read-only. **No P0.** The two caveats (P2-2 missing priority column, P2-3 QUEUED-reject transition gap) are design-completeness gaps that the builder must close; neither weakens the nonce wall — both fail *closed* (missing column = no reprioritize effect; illegal transition = `IllegalTransition` raised, no state change).

---

## REQ-03 — Read-as-data ATTACK table

| Attack | Reaches execution / bypasses scan / spams? | Severity |
|---|---|---|
| `trust-policy.md` body contains `ignore all previous instructions` / `disable the broker` | **NO** — `load_trust_policy._extract` (`trust_policy.py:99-106`) only matches recognized `key: value` regex; a directive line is not a recognized key → returns the default. The body is never `exec`d, never fed to a model. Injected text yields the SAME `TrustPolicy` as a clean file. Test `test_trust_policy_injection_is_inert` (P6-c test 4) asserts exactly this. | safe |
| `proactive-contract.md` says `nudge_max: 999` / `auto_approve: true` to loosen quiet-by-default | **NO** — read via the same value-only regex; only `nudge_max`/`repeat`/`anomaly_window_min` are recognized keys. An `auto_approve` line is ignored (no such key). AND the file is added to `immutable_ring.RING_PATHS` (design §3.3), so a worker cannot even commit an edit to it — `test_proactive_contract_is_ring_protected` (P6-c test 6). | safe |
| Injected directive surfaced to the owner as an *instruction* rather than fenced data | **NO** — `control_plane.read_config_file` scans raw bytes (`injection_scan.scan`) and the render layer `fence`s findings before display (design §3.4). Confirmed API exists (`injection_scan.py:118,166`). | safe |
| **Merged voice thread bypasses the single-scan** (concatenate N raw transcripts → split once → unscanned blob) | **PARTIAL RISK — P2-2.** `ingest_voice_note` scans EACH note's transcript before split (`intake_sources.py:290-293`), so each note IS scanned on ingest. BUT the design's threading (§2.1) persists transcripts to a `voice_threads` table and concatenates them at the morning tick, then hands the concatenation to `task_splitter.split` "once". If the *concatenation* is re-split without re-scanning, a two-note injection (payload split across notes so neither note alone trips `scan`) could evade per-note scanning and reach the splitter as a merged blob. The design SAYS "all consolidation happens on already-scanned transcripts" (§2, `voice_intake.py:32`) — but scanning each note independently is NOT equivalent to scanning the concatenation. **The builder MUST re-scan+fence the concatenated blob before the consolidated split**, and P6-b needs a RED test for a cross-note split-payload injection. The design's current test set (P6-b tests 1-6) does NOT include this case. Fail-mode is contained (the splitter still fences on its own scan IF the builder adds it), but the design as written leaves the door open. | **P2** |
| `proactive-contract` quiet semantics (≤1 nudge) defeated into spam | **NO** — `should_nudge(anomaly_key)` returns True iff no row exists, inserts on send; on-disk `nudges` ledger (WAL+FULL) survives restart; `anomaly_key` is deterministic (`f"{kind}:{job_id}"`). Same key 5× → 1 nudge (P6-c test 1); across restart → still 1 (test 2). A distinct key nudges once (test 3). To spam, an attacker would need to forge distinct `anomaly_key`s — but the key is code-derived from the event, not attacker-supplied. ⇒ safe. | safe |

**REQ-03 verdict:** the value-only parse + ring protection + deterministic ledger are all sound and grounded. **One design-completeness gap (P2-2):** the multi-note threading must re-scan the *concatenation*, not rely on per-note scans, and needs a cross-note-injection RED test. No injected content reaches execution in the built (single-note) path.

---

## REQ-04 — RED test-plan judgement (per-gate real/theater)

| Gate test | Real or theater? | Notes |
|---|---|---|
| `test_control_card_cannot_mutate_job_without_nonce` (no-bypass, the crown) | **REAL** *if built as specified* against a real `HeldStore` + `ApprovalAuthority` + real `JobStore` (not stubs). The model test `tests/broker/test_bypass_impossibility_gate.py` exists and is genuinely non-mocked ("No internal mocks on the wall"). The design says "modeled on" it. **Requirement for the builder:** the job-state-unchanged assertion must run against a REAL `JobStore`/reconciler, and the `approve()`-without-nonce assertion against the REAL `ApprovalAuthority` (whose `_validate` is confirmed real at `approval.py:64-76`). If the builder substitutes a fake authority that skips `_validate`, it becomes theater — the pre-QA gate must reject that. | real (contingent) |
| `test_mission_control_holds_no_writable_store` (structural no-bypass) | **REAL** — an attribute/introspection assertion that `MissionControl` exposes no write-capable `JobStore` is a genuine compile-time-ish guard, and `emit_control_card` calling `enqueue_action` exactly once is observable. Not theater. | real |
| mint-out-of-band | **REAL** — asserting the assistant/mission-control layer cannot obtain a nonce is verifiable: `_CLIENT_METHODS` (`server.py:28`) has no mint verb, and `mint_approval_nonce` is not an RPC. A test that calls every client verb and shows none returns a nonce is real. | real |
| `test_control_executor_rejects_illegal_transition` (job_reject on DONE) | **REAL** — `DONE` is terminal in `_ALLOWED` (`job_store.py:120`), so `transition(DONE→NEEDS_ATTENTION)` genuinely raises `IllegalTransition`. Grounded. | real |
| `test_tail_is_read_only_no_broker` | **REAL** — assert `tail()` issues no broker call and no `transition`; observable via a spy on the broker client. | real |
| `test_rescope_reenqueues_scan_fenced` | **REAL** — asserts an injection marker in a re-scope spec is fenced before re-enqueue; `injection_scan.fence` is confirmed. | real |
| voice single-scan / cross-note injection | **GAP (P2-2)** — the per-note tests (P6-b 1-6) are real but do NOT cover the concatenated-blob cross-note injection. Not theater, but **incomplete coverage of the stated single-scan invariant.** Add a RED test where two notes each individually pass `scan` but their concatenation carries an injection. | incomplete |
| `test_proactive_contract_quiet_one_nudge` + across-restart | **REAL** — an on-disk ledger with a real re-open between the two sends is a genuine restart simulation; not a mock. | real |
| pm_os wiring negatives (`test_no_pulse/weekly/exec_narrative_pipeline_added`) | **REAL** — grep/import assertions over the diff are objective. | real |

**REQ-04 verdict:** the no-bypass test is **real, not theater** (contingent on the builder using a real `ApprovalAuthority`/`JobStore`, which the pre-QA gate must enforce). The one gap is missing cross-note-injection coverage (P2-2).

---

## Findings (all P2 — no blockers)

- **P2-1 (cosmetic loc drift):** design cites `broker/server.py:104` for the merge_gate injection and `supervisor.py:427` for the merge-card enqueue; the actual injection kwarg is `server.py:91` (assignment `:113`) and the enqueue is `supervisor.py:426`. The *pattern* claims are CONFIRMED; only the line numbers drift by a few lines / land on the docstring. **Fix:** update the two cites to `server.py:91` and `supervisor.py:426`.
- **P2-2 (the substantive one — merged-voice-thread scan):** the design's threading (§2.1) concatenates per-note transcripts at the morning tick and splits once. Per-note scans (`intake_sources.py:290`) do NOT catch an injection payload split across two notes. **Fix:** `consolidate_thread` MUST run `injection_scan.scan` + `fence` on the *concatenated* transcript before the single `split`, and P6-b MUST add a RED test: two notes that each pass `scan` individually but whose concatenation trips it → fenced before split. Fail-mode is contained but the invariant as stated ("single-scan preserved") is not met by per-note scanning alone.
- **P2-3 (reject-a-QUEUED-job transition gap):** the design says `job_reject` handles "a held job / abandon a live job" and its reconciler does `transition(...→NEEDS_ATTENTION)`. But `_ALLOWED` (`job_store.py:102-121`) has NO `QUEUED→NEEDS_ATTENTION` edge — rejecting a still-QUEUED job would raise `IllegalTransition`. This fails *closed* (no bad state), matching the design's fail-closed claim, but a `job_reject` on a queued job would surface as an executor failure rather than a clean reject. **Fix:** either add `QUEUED→NEEDS_ATTENTION` to `_ALLOWED` (a ring-guarded file — needs the maintainer path) or have the reconciler map a QUEUED reject to a terminal `FAILED` (which QUEUED→FAILED *is* allowed), and add a test.

---

## Digest

**Verdict: SHIP-WITH-FIXES.** Crown no-bypass invariant holds — no P0/P1.
**Counts:** P0=0, P1=0, P2=3.
**Top 3 findings:**
1. **P2-2** — merged voice thread must re-scan the *concatenation* (per-note scans miss a cross-note split payload); add a cross-note-injection RED test. Fix: `injection_scan.scan+fence` on the concatenated transcript in `consolidate_thread` before `split`.
2. **P2-3** — `job_reject` reconciler `→NEEDS_ATTENTION` is illegal from `QUEUED` (`job_store.py:102`); fails closed but dirtily. Fix: map a QUEUED reject to `FAILED` (allowed) or add the ring-guarded edge; add a test.
3. **P2-1** — cite drift: merge_gate injection is `server.py:91` (not :104); merge-card enqueue is `supervisor.py:426` (not :427). Fix: correct the two line numbers.
**All confirmed:** enqueue_action (`broker_client.py:93`), out-of-band nonce mint + reject wall (`approval.py:48`,`:108`), merge_gate injection pattern (`server.py:91`/`:113`, main imports nothing from factory), executor registry fail-closed (`server.py:109`/`:253`), voice per-note single-scan (`intake_sources.py:290`), trust-policy value-only parse (`trust_policy.py:84`) + ring protection (`immutable_ring.py:50`). MissionControl-holds-no-writable-store is capability-removed-by-construction (correct); nonce is unmintable by a card (mint is not an RPC). The no-bypass RED test is real (contingent on builder using a real ApprovalAuthority/JobStore — pre-QA gate must enforce).
**Review-doc path:** `docs/plans/harness/fable/p6/P6-review.md`.
