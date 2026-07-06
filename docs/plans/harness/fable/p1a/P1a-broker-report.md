# P1a-BROKER — Coder Report (P1a-2/3/4/5, the send/action broker)

**Role:** CODER (backlog, GOVERNANCE_EXEMPT) · **Branch verified:** `factory` (== verify) ·
**Mode:** unit tests RED-first inline, no mocks of internal deps (real SQLite temp file, real
sockets via socketpair). **Scope kept to the broker package + tests/broker/** — no edits to
`send_message_tool.py`, `send_cmd.py`, `env_loader.py`, `factory_health.py`, or any gateway file
(those are P1a-h). The broker exposes the interface P1a-h will call.

---

## Files created (exact list)

Broker package + client:
- `broker/__init__.py` — package marker + version
- `broker/action_id.py` — ULID generation (26-char Crockford base32, monotonic within a ms)
- `broker/jsonrpc.py` — newline-framed JSON-RPC 2.0 codec (half-line discard)
- `broker/credentials.py` — broker-only secret store (broker_secret 0600 + egress cred lookup)
- `broker/held_store.py` — durable SQLite store (commit-before-ack, one-way transitions, no truncation)
- `broker/safe_lane.py` — 5-condition evaluator (C1–C5)
- `broker/approval.py` — approval authority: mint/validate/burn single-use action-bound nonce
- `broker/server.py` — UDS + JSON-RPC dispatch loop, `main()` entrypoint, `serve_connection()` core
- `broker_client.py` (repo root) — thin assistant-side stub, no `send`/`git_push`, fail-closed
- `broker/launchd/com.hermes.broker.plist` — own launchd service (separate PID)

Policy files (single-owner, write-blocked set):
- `broker/policy/safe-lane.md`, `allow-list.md`, `strategic-markers.md`, `irreversible-actions.md`

Tests (tests/broker/): `test_action_id.py`, `test_jsonrpc.py`, `test_credentials.py`,
`test_held_store.py`, `test_safe_lane.py`, `test_approval.py`, `test_server_client.py` + `__init__.py`.

---

## RED (before implementation) — verbatim tail

```
tests/broker/test_safe_lane.py:8: in <module>
    from broker.safe_lane import SafeLane, Decision
E   ModuleNotFoundError: No module named 'broker.safe_lane'
...
=========================== short test summary info ============================
ERROR tests/broker/test_action_id.py
ERROR tests/broker/test_approval.py
ERROR tests/broker/test_credentials.py
ERROR tests/broker/test_held_store.py
ERROR tests/broker/test_jsonrpc.py
ERROR tests/broker/test_safe_lane.py
ERROR tests/broker/test_server_client.py
!!!!!!!!!!!!!!!!!!! Interrupted: 7 errors during collection !!!!!!!!!!!!!!!!!!!!
7 errors in 0.09s
```

## GREEN (after implementation) — verbatim tail

```
..............................................s.......                   [100%]
53 passed, 1 skipped in 0.55s
```

Run command: `./venv/bin/python -m pytest tests/broker/ -q -p no:cacheprovider`
(the repo's `venv` — `.venv` does not exist; `run_tests.sh` probes both.)

The 1 skip is `test_socket_mode_is_0600` — see the environment note below.

---

## Security-test list — each guard proven REAL (a test FAILS if the guard is removed)

I neutered `ApprovalAuthority._validate` to `return True` (guard removed) and re-ran; **7
security tests failed**, then restored and re-ran: all green. Verbatim failure set with the
guard removed:

```
FAILED tests/broker/test_approval.py::test_approve_without_nonce_rejected
FAILED tests/broker/test_approval.py::test_approve_with_wrong_nonce_rejected
FAILED tests/broker/test_approval.py::test_nonce_bound_to_action_not_transferable
FAILED tests/broker/test_approval.py::test_nonce_single_use_replay_rejected
FAILED tests/broker/test_approval.py::test_cannot_mint_without_secret_matching
FAILED tests/broker/test_server_client.py::test_assistant_cannot_self_approve_over_socket
FAILED tests/broker/test_server_client.py::test_removing_nonce_check_would_break_self_approve_guard
7 failed, 9 passed, 1 skipped
```
After restore: `16 passed, 1 skipped`. This is the anti-theatre proof: the self-approval wall
is load-bearing, not decorative.

Guard inventory (each with the enforcing test):
| Guard | Enforcing test(s) | Real-because |
|---|---|---|
| approve without nonce rejected | `test_approve_without_nonce_rejected`, `test_assistant_cannot_self_approve_over_socket` | fails when `_validate` neutered |
| wrong/forged nonce rejected | `test_approve_with_wrong_nonce_rejected`, `test_cannot_mint_without_secret_matching` | HMAC over broker-only secret; a different secret can't forge |
| nonce is action-bound (not transferable) | `test_nonce_bound_to_action_not_transferable` | nonce for A rejected on B |
| nonce single-use (replay rejected) | `test_nonce_single_use_replay_rejected` | burned on first use; validity checked before idempotency short-circuit |
| fail-closed (broker down => raise) | `test_fail_closed_when_broker_down_client_raises` | connect to dead socket raises `BrokerUnavailable`, no fallback |
| client has no direct egress verb | `test_client_has_no_send_or_git_push_method` | `BrokerClient` lacks `send`/`git_push` |
| commit-before-ack | `test_commit_before_ack_row_on_disk_before_return` | separate reader connection sees the committed row the instant enqueue returns |
| one-way transitions | `test_illegal_backward_transition_rejected`, `test_executed_is_terminal`, `test_transition_from_wrong_expected_state_rejected` | state machine + SQL-conditioned UPDATE |
| no payload truncation | `test_long_payload_not_truncated` (100 KB byte-identical) | unbounded TEXT column |

---

## Requirement evidence (REQ-01..05)

**REQ-01 — Broker server (UDS + JSON-RPC, method set, own process).** `broker/server.py`
implements the design's method set: `enqueue_action`, `approve`, `reject`, `list_pending`,
`health`, plus the internal `send`/`git_push` executor path (auto-send runs the injected
message_executor). `_CLIENT_METHODS` deliberately excludes `send`/`git_push` from the socket
(one policy funnel). `main()` is the `__main__` entrypoint so it runs as its own PID. Round-trip
(`health` + `enqueue_action` + `list_pending`) proven in `test_health_enqueue_list_round_trip`
over real socket bytes. **Assumption noted:** the design lists `send`/`git_push` as broker-
INTERNAL verbs (§1.2 "Why the assistant only ever calls enqueue_action"); I implemented them as
the internal executor path invoked by enqueue_action/approve, not as client-reachable RPCs.

**REQ-02 — Durable held-action store (commit-before-ack, restart survival, one-way transitions,
ULID).** `broker/held_store.py`: WAL + `synchronous=FULL`; `enqueue()` COMMITs before returning
the id (`test_commit_before_ack_row_on_disk_before_return` opens a *separate* connection and
finds the row already committed). `test_held_action_survives_restart` deletes the store object
and rebuilds `HeldStore(same_file)` — action still pending. One-way state machine
(`held→approved/rejected/failed`, `approved→executed/failed`, terminals have no exits) with
SQL-conditioned UPDATE for atomicity. ULID ids via `broker/action_id.py`.

**REQ-03 — Self-approval-proof approval (P0-3 closure).** `broker/approval.py`: broker mints a
single-use, action-bound nonce = `HMAC(broker_secret, action_id ∥ salt)`; `broker_secret` lives
only in `broker/credentials.py` (0600 file), never on the socket. `approve()` rejects any call
without a valid unburned nonce; over the real socket the assistant's `approve(id, nonce=None)`
and `approve(id, nonce="made-up")` both raise `BrokerError` and **no egress occurs**
(`test_assistant_cannot_self_approve_over_socket`). Replay of a burned nonce rejected; a re-minted
nonce on an already-executed action returns the cached result without re-sending (idempotent).
The trust-me `approver` string is gone from the approve signature.

**REQ-04 — Fail-closed + 5-condition safe-lane.** `broker_client.BrokerClient` raises
`BrokerUnavailable` when the socket is unreachable — no silent direct-send fallback
(`test_fail_closed_when_broker_down_client_raises`). `broker/safe_lane.py` encodes C1–C5 as
independent predicates; auto-send iff all five TRUE. Plan examples classify correctly:
"got it, will do" to an allow-listed colleague via an allowed origin ⇒ `auto_sent`
(`test_all_five_hold_auto_sends`); "email the board a status update" ⇒ `held`
(`test_email_the_board_holds`, fails C3 strategic). Each condition independently forces a hold
when flipped (`test_each_single_failure_holds`, plus one test per condition). P1a conservative
default (empty allowed_origins ⇒ C4 FALSE ⇒ near-nothing auto-sends):
`test_conservative_default_holds_most` and the server's `_default_safe_lane`.

**REQ-05 — RED-first, no internal mocks, health for P0.8.** RED shown above (7 collection
errors). Real SQLite temp files, real sockets (socketpair drives the *production*
`serve_connection` path — not a mock; the only uncovered slice is the filesystem `bind`, gated
below). `health` RPC returns `{status, pending_count}` for P0.8's future `check_broker()` to
consume (`test_health_enqueue_list_round_trip` asserts the shape). Test count: **53 passed, 1
skipped** across 7 files.

---

## Environment limitation (honest, UNVERIFIED-in-sandbox slice)

`AF_UNIX` `bind()` on a filesystem path is **blocked in this execution sandbox** — verified:
`bind()` under `/tmp`, `$HOME/.hermes`, and the repo cwd all raise `PermissionError: [Errno 1]
Operation not permitted`, and `dangerouslyDisableSandbox` did not lift it (mkdir under `/tmp`
was also denied by an outer MAC policy). `socket.socketpair()` IS permitted.

Consequence: the socket-integration tests drive the broker's **real** per-connection dispatch
(`server.serve_connection()`, the identical production code path) over one end of a socketpair,
with `BrokerClient(connector=...)` on the other end — real bytes, real JSON-RPC framing, real
nonce validation. The single test that needs an actual pathname socket bound 0600
(`test_socket_mode_is_0600`) is **skipped** with a probe (`_bind_available`) and a reason string;
it will run and assert `0o600` on any host where `bind()` is permitted. The server's `_bind()`
sets the mode via umask(0o177) + explicit `chmod 0o600` (code present, path-bind untested here).
**This is the one slice UNVERIFIED in-sandbox** — flagged, not hidden. Everything else
(durability, transitions, nonce, safe-lane, fail-closed, dispatch, framing) is fully exercised.

---

## Notes / assumptions for downstream tasks (P1a-h)

- The `message_executor` is INJECTED into `BrokerServer` (a `Callable[[row], dict]`). P1a-h wires
  the real send path here; `broker/server.py:main()` currently installs a fail-closed stub that
  RAISES ("no message executor wired") so a standalone launch never silently sends.
- `broker/credentials.py::egress_cred()` currently reads the broker process env; P1a-h owns the
  actual relocation of egress secrets out of every assistant-loaded source (§1.6). The interface
  (return `None` when absent ⇒ executor must refuse) is in place.
- Gateway Telegram-callback wiring (`gateway/broker_approval_buttons.py`) is P1a-d's gateway file,
  out of THIS task's file-disjoint scope; the broker side (mint/validate/burn + `mint_approval_nonce`)
  is complete and tested. The callback handler will call `server.mint_approval_nonce(aid)` when
  emitting the button and `broker_client.approve(aid, nonce=...)` on tap.

No commit/push performed (per instructions).
