# ABOUTME: The immutable-ring submission gate (P2 design §3.2, closes review P1-R2/R3).
# ABOUTME: check_diff() REJECTS any worker-produced git diff that touches a ring path —
# ABOUTME: the broker source, the guard files, and the graduation-policy docs. Every path
# ABOUTME: is realpath-canonicalized under the worktree so rename (old+new), symlink, and
# ABOUTME: ../ traversal cannot evade the match; an un-parseable diff fails CLOSED.
"""
Immutable-ring submission gate — the wall against a worker rewriting the guard.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §3.2
Closes: docs/plans/harness/fable/p2/P2-review.md P1-R2 (rename evasion) and
P1-R3 (symlink / ``..`` traversal evasion).

Scope boundary (load-bearing, design §7): this gate runs at merge-submission on
a factory JOB's ``git diff <base>...<branch>`` — i.e. on WORKER-produced
worktrees ONLY. It is the wall against a WORKER modifying the enforcement
boundary. It does NOT (and structurally cannot) gate a human maintainer's
reviewed commits to ``broker/**`` — those are the sanctioned development path
(e.g. the P2-g-maint registry commit) and are outside this gate by construction.

The hardening over the v1 raw-string match:
  1. Canonicalize every path with ``os.path.realpath`` under the worktree root and
     confirm it stays inside — a ``../../broker/x`` resolves onto the real ring
     file (caught) and a path escaping the worktree is rejected outright.
  2. Check BOTH the old-path AND new-path of every diff entry — a rename into OR
     out of the ring trips on the corresponding side.
  3. Reject a diff entry that creates/modifies a SYMLINK whose canonicalized
     target lands on a ring path or leaves the worktree.
  4. Fail CLOSED: a diff entry that cannot be parsed into old/new paths is
     rejected, not allowed — a malformed header does not slip through.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# The ring path-list (design §3.2). Categorically un-touchable by a worker diff.
# Paths are worktree-RELATIVE, normalized with forward slashes. Directory members
# end with "/" and match any file beneath them; file members match exactly.
# NOTE: this list contains factory/immutable_ring.py ITSELF — a diff that weakens
# the gate is the worst diff, so the gate protects its own source.
# ---------------------------------------------------------------------------
RING_PATHS: Tuple[str, ...] = (
    # The broker — the enforcement boundary itself.
    "broker/",
    "broker_client.py",
    # The graduation policy (P1b may not have written it yet; guard the path now).
    "trust-policy.md",
    "docs/factory/trust-policy.md",
    # The "what never graduates" list.
    "docs/factory/never-graduates.md",
    # The factory guard code — a diff weakening any of these is a ring-level attack.
    "factory/immutable_ring.py",
    "factory/injection_scan.py",
    "factory/cost_stops.py",
    "factory/worker_env.py",
    # The retro's propose-gate wrapper (P5 v2-C2): the crown self-modification wall
    # lives here, so a retro editing skills/prompts must not be able to edit the gate.
    "factory/retro_ring_gate.py",
)

# git's "/dev/null" placeholder for an added/deleted side of a diff — not a path.
_DEV_NULL = "/dev/null"

# Symlink is git file mode 120000; a "new/old mode 120000" or "... mode 120000"
# line in a diff entry marks the entry as a symlink whose target is the blob body.
_SYMLINK_MODE_RE = re.compile(r"\bmode\s+120000\b")


class RingViolation(Exception):
    """
    Raised when a worker diff touches (or resolves onto) a ring path.

    Purpose: signal the supervisor to categorically reject the job →
    NEEDS_ATTENTION, in plain code before any human sees the merge card.
    Usage: check_diff(diff, worktree_root=wt) raises this on any ring hit.
    Gotchas: this is fail-closed — an un-parseable diff also raises this, so a
    malformed header cannot be treated as "no ring paths, allow".
    """


def _canonical_relative(path: str, worktree_root: str) -> Optional[str]:
    """
    Purpose: resolve a raw diff path to its real, worktree-relative form so a
    ``..`` traversal or symlinked path component is matched on its true target,
    not the literal string. Returns None if the path escapes the worktree.
    Usage: rel = _canonical_relative("sub/../../broker/x.py", wt)
    Gotchas: uses realpath (follows existing symlink components); a path resolving
    outside worktree_root returns None so the caller rejects it. worktree_root is
    itself realpath'd so a symlinked temp dir (macOS /var -> /private/var) matches.
    """
    root = os.path.realpath(worktree_root)
    resolved = os.path.realpath(os.path.join(root, path))
    # Confirm the resolved path stays inside the worktree.
    if resolved != root and not resolved.startswith(root + os.sep):
        return None
    rel = os.path.relpath(resolved, root)
    return rel.replace(os.sep, "/")


def is_ring_path(rel_path: str) -> bool:
    """
    Purpose: test one worktree-relative path against RING_PATHS (directory-prefix
    for members ending in "/", exact match otherwise).
    Usage: if is_ring_path("broker/approval.py"): reject.
    Gotchas: rel_path must already be canonicalized/normalized (forward slashes,
    no ``..``); call _canonical_relative first for raw diff paths.
    """
    for ring in RING_PATHS:
        if ring.endswith("/"):
            if rel_path == ring.rstrip("/") or rel_path.startswith(ring):
                return True
        elif rel_path == ring:
            return True
    return False


def _parse_diff_entries(diff: str) -> List[dict]:
    """
    Purpose: split a unified ``git diff`` into per-file entries, extracting the
    old path, new path, whether the entry is a symlink, and its symlink target(s)
    (the added blob lines). One dict per ``diff --git`` header.
    Usage: entries = _parse_diff_entries(diff_text)
    Gotchas: fails CLOSED at the call site — an entry whose old/new paths cannot
    be recovered is flagged (parse_ok=False) so check_diff rejects it rather than
    skipping it. Handles rename/copy headers (`rename from/to`, `copy from/to`)
    and the `---`/`+++` path lines; strips the a//b/ prefixes and quoting.
    """
    entries: List[dict] = []
    current: Optional[dict] = None

    def _strip_prefix(p: str) -> Optional[str]:
        # git prefixes paths with a/ and b/; /dev/null means the side is absent.
        p = p.strip()
        if p == _DEV_NULL:
            return None
        # Handle quoted paths ("a/weird name").
        if p.startswith('"') and p.endswith('"'):
            p = p[1:-1]
        if p.startswith("a/") or p.startswith("b/"):
            p = p[2:]
        return p

    for line in diff.splitlines():
        if line.startswith("diff --git "):
            if current is not None:
                entries.append(current)
            current = {
                "old": None, "new": None, "symlink": False,
                "link_targets": [], "header": line, "parse_ok": False,
            }
            # The header itself carries "a/<old> b/<new>"; parse it as a fallback,
            # but the ---/+++ lines below are authoritative when present.
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
            # For a symlink entry the added body IS the link target path.
            current["link_targets"].append(line[1:].strip())

    if current is not None:
        entries.append(current)
    return entries


def check_diff(diff: str, *, worktree_root: str) -> None:
    """
    Reject a worker diff that touches any ring path (design §3.2 Layer 2 — the wall).

    Purpose: the submission-time gate. Scans every changed-path (old AND new) plus
    any symlink target in the diff; canonicalizes each under the worktree; raises
    RingViolation on the FIRST ring hit, on a path escaping the worktree, or on an
    un-parseable entry (fail-closed). No raise == the diff is app-only, allowed.

    Usage:
        check_diff(git_diff_text, worktree_root=job.worktree_path)
        # raises RingViolation → supervisor parks the job NEEDS_ATTENTION.

    Gotchas:
      * Matches on the REALPATH-canonicalized, worktree-relative path — never the
        raw diff string — so ``..`` traversal and symlinked path components are
        caught on their true target (removing canonicalization makes the traversal
        test leak, which is the intended RED failure).
      * Checks BOTH old and new path of every entry → a rename into OR out of the
        ring is rejected on the corresponding side.
      * A symlink entry whose target canonicalizes onto a ring path (or leaves the
        worktree) is rejected even though the link file itself is app-space.
      * Fails CLOSED on an un-parseable entry — a malformed header is never
        silently allowed.
    """
    entries = _parse_diff_entries(diff)
    for entry in entries:
        # Fail-closed: an entry we could not parse into any path is rejected.
        if not entry["parse_ok"] or (entry["old"] is None and entry["new"] is None):
            raise RingViolation(
                f"un-parseable diff entry (fail-closed): {entry['header']!r}"
            )

        raw_paths: List[str] = [p for p in (entry["old"], entry["new"]) if p]
        for raw in raw_paths:
            rel = _canonical_relative(raw, worktree_root)
            if rel is None:
                raise RingViolation(
                    f"diff path escapes the worktree (rejected): {raw!r}"
                )
            if is_ring_path(rel):
                raise RingViolation(
                    f"diff touches ring path {rel!r} (via {raw!r})"
                )

        # Symlink-into-ring: the link's TARGET must not resolve onto a ring path.
        # A stored symlink target is checked BOTH ways — relative to the link's
        # own directory (true FS resolution) AND relative to the worktree root (an
        # attacker may author a root-relative-looking target like "broker/x"). A
        # ring hit on EITHER interpretation rejects, and a target escaping the
        # worktree on BOTH interpretations rejects (fail-closed). This is stricter
        # than trusting one interpretation and cannot be evaded by choosing the
        # other.
        if entry["symlink"]:
            base_dir = os.path.dirname(entry["new"] or entry["old"] or "")
            for target in entry["link_targets"]:
                candidates = [
                    os.path.join(base_dir, target),  # relative to the link's dir
                    target,                          # relative to the worktree root
                ]
                rels = [_canonical_relative(c, worktree_root) for c in candidates]
                # Ring hit on ANY interpretation → reject.
                for rel in rels:
                    if rel is not None and is_ring_path(rel):
                        raise RingViolation(
                            f"symlink resolves onto ring path {rel!r} "
                            f"(target {target!r})"
                        )
                # Escapes the worktree on EVERY interpretation → reject.
                if all(rel is None for rel in rels):
                    raise RingViolation(
                        f"symlink target escapes the worktree (rejected): {target!r}"
                    )


def fs_scope_excludes() -> Set[str]:
    """
    Emit the path-exclusion set the worker's FS-tool scope should apply (REQ-04).

    Purpose: defense in depth (design §3.2 Layer 1 — best-effort, NOT the wall).
    The worker runner / allowlist can consume this so ring paths are not even
    writable in the worktree, before the Layer-2 submission gate ever runs.
    Usage: excludes = fs_scope_excludes(); pass to the worker's path guard.
    Gotchas: this is the BELT — check_diff (Layer 2) is the wall. A worker that
    writes around the FS scope is still caught at submission. The set mirrors
    RING_PATHS verbatim so the two layers can never drift apart.
    """
    return set(RING_PATHS)
