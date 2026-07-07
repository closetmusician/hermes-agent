# ABOUTME: Stub merge executor — registered under action type 'merge' by P2-g-maint
# ABOUTME: to provide the dispatch seam P2-g needs.  The REAL merge logic (rebase →
# ABOUTME: re-test → push under per-repo lock) is authored by P2-g; this stub raises
# ABOUTME: NotImplementedError with a clear message so callers know the seam exists
# ABOUTME: but is not yet functional.  P2-g replaces the body, NOT the registration.
from __future__ import annotations

from typing import Any, Dict


def build_merge_executor() -> Any:
    """
    Purpose: build the stub merge executor registered under type 'merge'.
    Usage: executor = build_merge_executor(); pass it in the executors dict.
    Gotchas: raises NotImplementedError on every call — this is intentional; the
    stub establishes the registration seam so P2-g can add real logic without
    editing broker/server.py.  The error message explains exactly what to implement.
    The stub is accepted by the type-system as a valid Executor callable.
    """

    def _execute(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: placeholder for P2-g merge logic (rebase → re-test → push).
        Usage: called by the broker on approval of a 'merge' held-action row.
        Gotchas: raises NotImplementedError — replace this body in P2-g.  Do NOT
        change the builder signature or registration key ('merge'); just implement
        the function body with the real rebase+retest+push logic.
        """
        raise NotImplementedError(
            "merge executor stub — P2-g must implement rebase → re-test → push logic. "
            "See docs/plans/harness/fable/p2/P2-design.md §4.2 for the full sequence. "
            "To implement: replace this body in broker/executors/merge_executor.py "
            "and keep the 'merge' key in the executors dict passed to BrokerServer."
        )

    return _execute
