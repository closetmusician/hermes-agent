# ABOUTME: The BROKER-SIDE auto-merge gate (P1b-e, design §V2.1) — the server-side wall
# ABOUTME: that makes tier-1 auto-merge safe (closes review P0-1). On any auto (non-human)
# ABOUTME: merge the broker RE-RUNS a ring-path check + never-graduates check + tier recompute
# ABOUTME: over the merge diff and FAILS CLOSED to 'held' on ANY failure/error/unavailability.
# ABOUTME: Owns its OWN ring path-list (imports NOTHING from factory — layering is factory→broker).
"""
Broker-side auto-merge gate — the wall the design's §4.4 only claimed until v2.

Why this lives in the broker and NOT by importing ``factory.immutable_ring``:
the broker is the lower, credential-holding trust root; it imports nothing from
``factory/`` and must not, or its own security would depend on worker-guard code
outside its boundary (design §V2.1). So this module carries a VERBATIM copy of
the ring path tuple (``BROKER_RING_PATHS``) plus a small port of the diff
path-canonicalize-and-match logic. A parity test (AT-RING-2) asserts
``BROKER_RING_PATHS ⊇ factory.immutable_ring.RING_PATHS`` so the broker can never
silently check LESS than the worker-side gate.

Fail-closed is total: ANY exception, unreadable diff, unparseable header,
unavailable policy, or tier < 1 → ("held", 0, reason). The broker never
auto-merges on a check it could not run.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# BROKER-OWNED ring path-list (design §V2.1). A verbatim copy of
# factory.immutable_ring.RING_PATHS — NOT an import, to preserve the
# factory→broker layering. AT-RING-2 asserts this is a superset of the factory
# list so the two can never drift with the broker checking less.
# ---------------------------------------------------------------------------
BROKER_RING_PATHS: Tuple[str, ...] = (
    "broker/",
    "broker_client.py",
    "trust-policy.md",
    "docs/factory/trust-policy.md",
    "docs/factory/never-graduates.md",
    "factory/immutable_ring.py",
    "factory/injection_scan.py",
    "factory/cost_stops.py",
    "factory/worker_env.py",
    # The retro propose-gate wrapper (P5 v2-C2). Mirrors the factory RING_PATHS
    # addition so AT-RING-2 (BROKER_RING_PATHS ⊇ factory RING_PATHS) stays green.
    "factory/retro_ring_gate.py",
)

_DEV_NULL = "/dev/null"
_SYMLINK_MODE_RE = re.compile(r"\bmode\s+120000\b")

# The two capabilities the auto-merge decision can return, and the fail-closed one.
_HELD = "held"
_AUTO = "auto"


class RingViolation(Exception):
    """Raised internally when the merge diff touches (or resolves onto) a ring path."""


# ---------------------------------------------------------------------------
# Ring-check port (design §V2.1). A small, self-contained copy of the
# factory-side check_diff canonicalize-and-match logic. Kept deliberately close
# to the original so parity is auditable; fails CLOSED on an un-parseable entry.
# ---------------------------------------------------------------------------

def _canonical_relative(path: str, worktree_root: str) -> Optional[str]:
    """
    Purpose: resolve a raw diff path to its real worktree-relative form so a ``..``
    traversal or symlinked component matches on its true target, not the string.
    Usage: rel = _canonical_relative("sub/../../broker/x", wt)
    Gotchas: returns None if the path escapes the worktree (caller rejects it).
    """
    root = os.path.realpath(worktree_root)
    resolved = os.path.realpath(os.path.join(root, path))
    if resolved != root and not resolved.startswith(root + os.sep):
        return None
    return os.path.relpath(resolved, root).replace(os.sep, "/")


def is_ring_path(rel_path: str) -> bool:
    """
    Purpose: test one canonicalized worktree-relative path against BROKER_RING_PATHS.
    Usage: if is_ring_path("broker/approval.py"): reject.
    Gotchas: directory members end in "/" and match any file beneath; file members
    match exactly. rel_path must already be canonicalized (forward slashes, no ..).
    """
    for ring in BROKER_RING_PATHS:
        if ring.endswith("/"):
            if rel_path == ring.rstrip("/") or rel_path.startswith(ring):
                return True
        elif rel_path == ring:
            return True
    return False


def _parse_diff_entries(diff: str) -> List[dict]:
    """
    Purpose: split a unified git diff into per-file entries (old path, new path,
    symlink flag, link targets), one dict per ``diff --git`` header.
    Usage: entries = _parse_diff_entries(diff_text)
    Gotchas: fails CLOSED at the call site — an entry whose paths cannot be
    recovered carries parse_ok=False so check_ring rejects rather than skips it.
    """
    entries: List[dict] = []
    current: Optional[dict] = None

    def _strip_prefix(p: str) -> Optional[str]:
        p = p.strip()
        if p == _DEV_NULL:
            return None
        if p.startswith('"') and p.endswith('"'):
            p = p[1:-1]
        if p.startswith("a/") or p.startswith("b/"):
            p = p[2:]
        return p

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                entries.append(current)
            current = {"old": None, "new": None, "symlink": False,
                       "link_targets": [], "header": line, "parse_ok": False}
            m = re.match(r"diff --git (\S+) (\S+)$", line)
            if m:
                current["old"] = _strip_prefix(m.group(1))
                current["new"] = _strip_prefix(m.group(2))
                current["parse_ok"] = True
            continue
        if current is None:
            continue
        if _SYMLINK_MODE_RE.search(line):
            current["symlink"] = True
        elif line.startswith("rename from ") or line.startswith("copy from "):
            current["old"] = line.split(" from ", 1)[1].strip()
            current["parse_ok"] = True
        elif line.startswith("rename to ") or line.startswith("copy to "):
            current["new"] = line.split(" to ", 1)[1].strip()
            current["parse_ok"] = True
        elif line.startswith("--- "):
            current["old"] = _strip_prefix(line[4:])
            current["parse_ok"] = True
        elif line.startswith("+++ "):
            current["new"] = _strip_prefix(line[4:])
            current["parse_ok"] = True
        elif current["symlink"] and line.startswith("+") and not line.startswith("+++"):
            current["link_targets"].append(line[1:].strip())

    if current is not None:
        entries.append(current)
    return entries


def check_ring(diff: str, *, worktree_root: str) -> None:
    """
    Reject a merge diff that touches any ring path (design §V2.1 — the broker wall).

    Purpose: the server-side re-check. Scans every changed path (old AND new) plus
    any symlink target; canonicalizes each under the worktree; raises RingViolation
    on the FIRST ring hit, on a path escaping the worktree, or on an un-parseable
    entry (fail-closed). No raise == the diff is app-only, allowed to auto-merge.
    Usage: check_ring(git_diff_text, worktree_root=card["worktree"])  # raises on hit.
    Gotchas: matches on the REALPATH-canonicalized path, not the raw string, so
    ``..`` traversal and symlinked components are caught on their true target.
    """
    entries = _parse_diff_entries(diff)
    if not entries:
        # An empty/whitespace diff carries no changes — nothing to auto-merge, and
        # a merge whose diff we could not read is suspicious: fail closed.
        raise RingViolation("empty or unreadable diff (fail-closed)")
    for entry in entries:
        if not entry["parse_ok"] or (entry["old"] is None and entry["new"] is None):
            raise RingViolation(f"un-parseable diff entry (fail-closed): {entry['header']!r}")

        for raw in [p for p in (entry["old"], entry["new"]) if p]:
            rel = _canonical_relative(raw, worktree_root)
            if rel is None:
                raise RingViolation(f"diff path escapes the worktree: {raw!r}")
            if is_ring_path(rel):
                raise RingViolation(f"diff touches ring path {rel!r} (via {raw!r})")

        if entry["symlink"]:
            base_dir = os.path.dirname(entry["new"] or entry["old"] or "")
            for target in entry["link_targets"]:
                candidates = [os.path.join(base_dir, target), target]
                rels = [_canonical_relative(c, worktree_root) for c in candidates]
                for rel in rels:
                    if rel is not None and is_ring_path(rel):
                        raise RingViolation(
                            f"symlink resolves onto ring path {rel!r} (target {target!r})"
                        )
                if all(rel is None for rel in rels):
                    raise RingViolation(f"symlink target escapes the worktree: {target!r}")


# ---------------------------------------------------------------------------
# never-graduates reader — reads the on-disk ring-protected doc, not a hardcode.
# ---------------------------------------------------------------------------

def _default_never_graduates_path() -> str:
    """The canonical on-disk never-graduates doc (a ring member)."""
    # Resolve relative to the repo root: this module lives at <root>/broker/.
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "docs", "factory", "never-graduates.md")


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------

# Injected boundaries (design §V2.1): the git-diff runner, the tier-compute
# function (factory-side compute_tier, wired in by the supervisor at broker
# construction — NOT imported here), and the never-graduates capability reader.
GitDiff = Callable[[str, str, str], str]            # (base, branch, worktree) -> diff text
ComputeTier = Callable[..., int]                    # (repo, task_type, capability, now_ms) -> int
NeverGraduates = Callable[[], Set[str]]             # () -> forbidden capability set


class MergeGate:
    """
    Purpose: the broker's server-side auto-merge decision — re-runs ring + never-
    graduates + tier over the merge diff and FAILS CLOSED to held on any failure.
    Usage: gate = MergeGate(git_diff=..., compute_tier=..., never_graduates=...);
           disp, tier, reason = gate.evaluate(card, now_ms=now).
    Gotchas: the ring/never/tier boundaries are INJECTED (compute_tier is the
    factory-side pure fn wired in by the supervisor at broker construction — this
    module imports nothing from factory). ANY exception → ("held", 0, "error").
    The mint/burn of the approval nonce is NOT here — that stays in the broker
    server's _rpc_auto_merge (this gate only decides; the server executes).
    """

    def __init__(
        self,
        *,
        git_diff: GitDiff,
        compute_tier: ComputeTier,
        never_graduates: Optional[NeverGraduates] = None,
        _skip_ring_for_anti_theater_proof: bool = False,
    ) -> None:
        self._git_diff = git_diff
        self._compute_tier = compute_tier
        self._never = never_graduates or _read_never_graduates_capabilities
        # Escape hatch used ONLY by the anti-theater test to demonstrate that the
        # ring check is load-bearing (a neutered gate returns 'auto' on a ring diff).
        self._skip_ring = _skip_ring_for_anti_theater_proof

    def evaluate(self, card: Dict[str, Any], *, now_ms: int) -> Tuple[str, int, str]:
        """
        Decide auto|held for one merge card, server-side, fail-closed (design §V2.1).

        Purpose: the load-bearing re-check. In order: (1) ring re-check over the
        merge diff; (2) never-graduates re-check on the TRUSTED capability; (3) tier
        recompute from the ledger (never trust a tier stamped on the card). AUTO only
        if ALL pass; HELD on any failure/error/unavailability.
        Usage: disp, tier, reason = gate.evaluate(card, now_ms=now_ms).
        Gotchas: the capability + task_type come from the TRUSTED card fields the
        supervisor recorded at job creation (design §V2.4), never worker free-text.
        Any exception anywhere → ("held", 0, "error") — the broker never auto-merges
        on a check it could not complete.
        """
        try:
            repo = card.get("repo")
            branch = card.get("branch")
            base = card.get("base") or card.get("base_branch") or "main"
            worktree = card.get("worktree") or card.get("worktree_path")
            capability = card.get("capability")
            task_type = card.get("task_type")
            if not repo or not branch or not worktree:
                return (_HELD, 0, "malformed_card")

            # 1. RING RE-CHECK over the diff the merge would land (the P0-1 fix).
            if not self._skip_ring:
                diff = self._git_diff(base, branch, worktree)
                try:
                    check_ring(diff, worktree_root=worktree)
                except RingViolation:
                    return (_HELD, 0, "ring")

            # 2. NEVER-GRADUATES re-check on the trusted capability.
            if capability is not None and capability in self._never():
                return (_HELD, 0, "never")

            # 3. TIER RECOMPUTE from the ledger — never trust the card's tier.
            tier = self._compute_tier(repo, task_type, capability, now_ms)
            if tier is None or tier < 1:
                return (_HELD, 0, "tier")

            return (_AUTO, tier, "ok")
        except Exception:
            # Fail closed on ANY error: unreadable diff, unavailable policy/ledger,
            # unparseable header — the broker never auto-merges on an unrun check.
            return (_HELD, 0, "error")


def _read_never_graduates_capabilities(path: Optional[str] = None) -> Set[str]:
    """
    Purpose: read the ring-protected never-graduates doc and return a capability set.
    Usage: forbidden = _read_never_graduates_capabilities()
    Gotchas: the doc is prose+bullets; we derive a coarse capability keyword set from
    it so a capability label matching a forbidden family is caught. On a read error
    returns a conservative built-in set (fail-closed toward MORE holding), never {}.
    """
    # Conservative built-in floor — always forbidden regardless of the doc read.
    forbidden: Set[str] = {
        "prod_deploy", "production_deploy", "deploy_prod",
        "deletion", "delete", "financial", "payment",
        "self_approve", "self_approval",
    }
    try:
        p = path or _default_never_graduates_path()
        with open(p, "r", encoding="utf-8") as fh:
            text = fh.read().lower()
        # Map doc language to capability keywords (coarse but fail-safe).
        if "protected branch" in text:
            forbidden.add("protected_branch_push")
        if "enforcement boundary" in text:
            forbidden.add("edit_enforcement_boundary")
        if "egress credential" in text:
            forbidden.add("egress_credential")
    except OSError:
        pass
    return forbidden


def evaluate_auto_merge(
    card: Dict[str, Any],
    *,
    git_diff: GitDiff,
    compute_tier: ComputeTier,
    never_graduates: Optional[NeverGraduates] = None,
    now_ms: int,
) -> Tuple[str, int, str]:
    """
    Purpose: function-shaped entry point (design §V2.1) — builds a MergeGate and
    evaluates one card. Fail-closed on any error.
    Usage: disp, tier, reason = evaluate_auto_merge(card, git_diff=..., compute_tier=...,
           never_graduates=..., now_ms=now).
    Gotchas: a thin wrapper over MergeGate.evaluate; the same fail-closed guarantees
    apply. Prefer constructing a MergeGate once and reusing it in the broker server.
    """
    gate = MergeGate(git_diff=git_diff, compute_tier=compute_tier, never_graduates=never_graduates)
    return gate.evaluate(card, now_ms=now_ms)
