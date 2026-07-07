# P6-RING-GAP Fix Report

**Task:** P6-RING-GAP — close broker ring-superset gap for docs/factory/proactive-contract.md
**Branch:** factory
**Date:** 2026-07-07

## REQ-01 — RED repro

Missing path identified:

```
set(RING_PATHS) - set(BROKER_RING_PATHS) == {'docs/factory/proactive-contract.md'}
```

Test `test_broker_ring_list_is_superset_of_factory` (AT-RING-2) confirmed **FAIL**:
`AssertionError` — `docs/factory/proactive-contract.md` in `factory.immutable_ring.RING_PATHS`
but absent from `broker.merge_gate.BROKER_RING_PATHS`.

## REQ-02 — Fix applied

File: `broker/merge_gate.py`, lines 45–48 (BROKER_RING_PATHS tuple).

Added, mirroring the retro_ring_gate.py precedent exactly:

```python
    # The quiet-by-default proactive nudge contract (P6-c). Mirrors the factory
    # RING_PATHS addition so AT-RING-2 stays green — a worker weakening
    # nudge_max or repeat would be caught here too, not just at submission.
    "docs/factory/proactive-contract.md",
```

`factory/immutable_ring.py` not touched (its RING_PATHS was already correct).

## REQ-03 — GREEN verification

**Superset test:** PASS — `test_broker_ring_list_is_superset_of_factory` GREEN

**Ring rejection of proactive-contract.md diff:**
```
PASS: broker gate REJECTED proactive-contract.md diff:
  diff touches ring path 'docs/factory/proactive-contract.md' (via 'docs/factory/proactive-contract.md')
```

**Broker suite (direct call, 14 non-fixture tests):** 14 passed, 0 failed.
7 TypeError errors are pre-existing pytest-fixture-injection tests that require
`broker_with_gate` fixture — not regressions from this change.

## Summary

The one-line omission caused `BROKER_RING_PATHS ⊋ RING_PATHS` to become
`BROKER_RING_PATHS ⊊ RING_PATHS` after P6-c. Fixed by additive edit to
`broker/merge_gate.py` — superset invariant restored.
