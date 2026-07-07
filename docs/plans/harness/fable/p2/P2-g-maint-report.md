# ABOUTME: Completion report for P2-g-maint — the maintainer-authored broker change
# ABOUTME: that adds a type→executor registry to BrokerServer (P2 REQ-01/02/03/04/05).
# ABOUTME: This is NOT a worker-authored diff; it is a reviewed maintainer commit.
# ABOUTME: See P2-design.md §4.2 + §7 closure log for the authoritative design.
# ABOUTME: Digest at the bottom for the parent orchestrator.

# P2-g-maint Completion Report

**Task:** P2-g-maint — type→executor registry + `resolve_model_key` RPC  
**Branch:** `factory`  
**Status:** COMPLETE — all 10 new tests GREEN; 79 existing broker tests PASSED, 1 skipped, 0 regressions.

---

## RED → GREEN trace

Tests were written first (`tests/broker/test_registry_dispatch.py`) and confirmed RED via
`ImportError: cannot import name 'ExecutorRegistry' from 'broker.executors'`.  
All 10 tests turned GREEN after the implementation. The existing 69 broker tests (80 total
including the 10 new ones, minus 1 pre-existing skip for socket-bind in sandbox) were run
and produced 0 regressions.

---

## REQ-01: Type→executor registry — done

**Design:** `BrokerServer.__init__` now takes `executors: Dict[str, Any]` (replacing the
old single `message_executor=` kwarg). An `ExecutorRegistry` wraps the dict and is the
authoritative dispatch table. Both the auto-sent path (`_rpc_enqueue_action`) and the
approval path (`_rpc_approve`) call `self._executor_for(row)` which delegates to
`self._registry.get(row["type"])`.

**Fail-closed:** `ExecutorRegistry.get` raises `UnknownActionType` (never `None`,
never falls back to a wrong executor). `_dispatch` catches it and encodes it as JSON-RPC
error `-32002` so the caller sees an explicit failure.

**Tests (RED confirmed, GREEN verified):**
- `test_message_action_dispatches_to_message_executor` — enqueue+approve a `message` row;
  assert the message executor was called and the git_push executor was NOT called.
- `test_unknown_action_type_fails_closed` — approve an `unknown_future_type` row; assert
  `BrokerError` is raised with a message matching `unknown|executor|type`.
- `test_server_rejects_old_single_executor_kwarg` — passing `message_executor=` to
  `BrokerServer(...)` raises `TypeError` (the old interface is gone).
- `test_existing_server_functionality_preserved` — health/enqueue/list_pending still work.

---

## REQ-02: Merge-executor registration seam — done

**Design:** P2-g will add the real merge logic by implementing `broker/executors/merge_executor.py`
and passing `"merge": build_merge_executor()` in the `executors` dict passed to `BrokerServer`.
No `broker/server.py` edits are needed by P2-g — the registry is the seam.

A stub `build_merge_executor()` is already present in `broker/executors/merge_executor.py`
and is registered under `"merge"` in `broker/server.py`'s `main()`. The stub raises
`NotImplementedError` with a clear message pointing to the implementation path.

**Tests:**
- `test_new_executor_registered_without_editing_server` — injects a `"merge"` executor
  via the `executors=` dict, enqueues+approves a `type="merge"` action, asserts the
  custom merge executor was called (and server.py was not edited to add it).
- `test_executor_registry_standalone` — `ExecutorRegistry` can be built and queried
  standalone.
- `test_executor_registry_unknown_type_raises` — `UnknownActionType` is raised (not
  `KeyError` or silent `None`).

---

## REQ-03: `resolve_model_key` RPC — done

**Design:** `BrokerServer._rpc_resolve_model_key` is a new RPC in `_CLIENT_METHODS`.
It maps `provider` → key name via `_PROVIDER_KEY_MAP` (`anthropic`→`ANTHROPIC_API_KEY`,
`openai`→`OPENAI_API_KEY`, etc.) and reads the value from the broker's own `os.environ`.
It returns `{"key_name": "ANTHROPIC_API_KEY", "value": <str_or_None>}` — ONLY inference
keys (`*_API_KEY`), NEVER egress keys (`GITHUB_TOKEN`, `GH_TOKEN`, `TELEGRAM_BOT_TOKEN`).

`BrokerClient.resolve_model_key(provider=...)` is the new client method.

**Tests:**
- `test_resolve_model_key_is_in_client_methods` — callable over the socket; returns a dict
  with a key name field.
- `test_resolve_model_key_is_never_an_egress_key` — for `anthropic` and `openai`, the
  returned `key_name` ends in `_API_KEY` AND is not in the egress denylist.
- `test_resolve_model_key_not_exposed_over_enqueue_socket_without_explicit_call` — seeds
  `GITHUB_TOKEN=ghp_fakepushtoken99` in the supervisor env; asserts the literal token
  value is NOT present in the `resolve_model_key` response.

---

## REQ-04: No regressions — done

All 69 pre-existing broker tests PASSED. The only change to existing test files was
`tests/broker/test_server_client.py` (two fixture usages updated from `message_executor=`
to `executors={"message": ...}`) — this is the mandated API migration, not a behavior
change.

---

## REQ-05: RED-first, real objects — done

RED state confirmed (import error before implementation). All dispatch tests use real
`BrokerServer` objects over socketpairs — no mocks of the dispatch path.

---

## Files changed

| File | Change |
|---|---|
| `broker/executors/__init__.py` | Added `ExecutorRegistry`, `UnknownActionType`, `Executor` type alias |
| `broker/executors/merge_executor.py` | NEW — stub merge executor (raises `NotImplementedError`) |
| `broker/server.py` | Replaced `message_executor=` with `executors=` registry; added `_executor_for`; added `_rpc_resolve_model_key`; `_PROVIDER_KEY_MAP`; updated `main()` |
| `broker_client.py` | Added `resolve_model_key(provider=)` method |
| `tests/broker/test_registry_dispatch.py` | NEW — 10 tests (RED-first, all GREEN) |
| `tests/broker/test_server_client.py` | Two fixture usages: `message_executor=` → `executors={"message":...}` |

---

## Digest (≤15 lines for orchestrator)

- **REQ-01** ✓ Registry dispatch: `ExecutorRegistry` + `_executor_for(row)` in server; `message` → message_executor, unknown type → `UnknownActionType` error -32002. Tests: `test_message_action_dispatches_to_message_executor`, `test_unknown_action_type_fails_closed`, `test_server_rejects_old_single_executor_kwarg`.
- **REQ-02** ✓ Merge seam: stub `merge_executor.py` registered under `"merge"` in `main()`. P2-g adds real logic without editing server.py. Test: `test_new_executor_registered_without_editing_server`.
- **REQ-03** ✓ `resolve_model_key` RPC: returns `{key_name, value}` for inference keys only; never egress keys. Tests: 3 covering key-name shape, egress exclusion, no push-token leakage.
- **REQ-04** ✓ 0 regressions: 79 passed (69 pre-existing + 10 new), 1 pre-existing skip, 0 failures.
- **REQ-05** ✓ RED confirmed (ImportError before impl). All tests use real BrokerServer via socketpair — no mocks of dispatch.
- **New files:** `broker/executors/merge_executor.py`, `tests/broker/test_registry_dispatch.py`
- **Modified:** `broker/executors/__init__.py`, `broker/server.py`, `broker_client.py`, `tests/broker/test_server_client.py`
- **Report:** `docs/plans/harness/fable/p2/P2-g-maint-report.md`
