# ABOUTME: Plain-code prompt-injection scanner (P2 design §3.1, closes review P0-2).
# ABOUTME: scan() runs at the two chokepoints hermes OWNS: INPUT before a WorkerSpec is
# ABOUTME: built, and OUTPUT/diff before the supervisor acts / before the ring gate +
# ABOUTME: merge card. It is a DETECTOR (regex/substring), not a wall — the wall is
# ABOUTME: tool/cred absence (§2.3) + the immutable ring (§3.2) + every merge held (§4.2).
"""
Injection scanner — two real chokepoints, no AI in the gate.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §3.1
Closes: docs/plans/harness/fable/p2/P2-review.md P0-2 (the v1 "intercept before
the model sees it" claim was unprovable for a ``claude -p`` subprocess, whose
in-loop tool-result seam hermes does not own). v2 enforces on the two seams
hermes DOES own, both plain code OUTSIDE the worker subprocess:

  * Chokepoint 1 — INPUT: every piece of supervisor-supplied content (job spec,
    externally-fetched attachments) is scanned before the ``WorkerSpec`` is built
    and the subprocess launched. A finding fences the offending content (it does
    not enter the spec raw) and flags the job.
  * Chokepoint 2 — OUTPUT/diff: the worker's collected JSON output AND its
    ``git diff <base>...<branch>`` are scanned before the supervisor acts and
    before the ring gate / merge-card build. A finding parks the job
    NEEDS_ATTENTION — an injected directive never reaches TEST / ring / merge.

Honest scope: this scanner shrinks the attack surface. It is a denylist and
therefore incomplete against novel encodings/phrasings. What makes a *miss*
non-fatal is defense in depth, documented in ``RESIDUAL`` below.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List

# ---------------------------------------------------------------------------
# The documented residual (REQ-02) — stated as a limitation, NOT a claim.
# Surfaced as a module constant so a test can assert it stays honest and the
# supervisor can echo it into forensics / the report.
# ---------------------------------------------------------------------------
RESIDUAL = (
    "Residual (honest, not a claim): content the worker fetches ITSELF mid-run "
    "through its own allowed tools (e.g. a repo file Read inside the claude -p / "
    "codex exec loop) is scanned at chokepoint 2 when it surfaces in the diff/"
    "output, but is NOT neutralized before the worker's model reasons over it — "
    "hermes does not own that in-worker-loop seam. The blast radius is bounded "
    "because a factory worker has NO egress credential and NO push tool "
    "(design §2.3 replacement env), cannot edit broker/** or the guard files "
    "(immutable ring §3.2), and every merge is held for owner approval (§4.2). "
    "So even a fully-missed injection cannot ACT. If a future phase gains a "
    "proven in-loop hook, a chokepoint 0 can be added; P2 does not depend on it."
)

# Zero-width / bidi / control code points that hide or reorder injected text.
# Stripped (or, for bidi overrides, themselves flagged) before matching so an
# attacker cannot splice a directive with invisible characters.
_ZERO_WIDTH = (
    "​"  # zero-width space
    "‌"  # zero-width non-joiner
    "‍"  # zero-width joiner
    "⁠"  # word joiner
    "﻿"  # zero-width no-break space / BOM
)
_BIDI_CONTROLS = (
    "‪‫‬‭‮"  # LRE RLE PDF LRO RLO
    "⁦⁧⁨⁩"  # LRI RLI FSI PDI
)

# Injection rules. Each is (rule_name, compiled_pattern). Patterns run over the
# NFKC-normalized, zero-width-stripped, whitespace-collapsed, lower-cased text so
# case / spacing / zero-width evasions are defeated before the regex sees it.
_RULES: List[tuple] = [
    ("ignore-instructions", re.compile(r"ignore\s+(all\s+)?(prior|previous)\s+instructions")),
    ("push-to-protected", re.compile(r"push\s+to\s+(main|master)")),
    ("merge-to-protected", re.compile(r"merge\s+(to|into)\s+(main|master|protected)")),
    ("skip-permissions", re.compile(r"--?dangerously-skip-permissions")),
    ("disable-guard", re.compile(r"disable\b.{0,40}\b(guard|safe.?lane|broker|ring)")),
    ("curl-pipe-shell", re.compile(r"curl\b.{0,120}\|\s*(sh|bash)")),
    # A long base64-ish blob adjacent to an eval/exec/atob sink is suspicious.
    ("base64-eval", re.compile(r"(eval|exec|atob)\s*\(.{0,20}[A-Za-z0-9+/]{16,}={0,2}")),
]


@dataclass(frozen=True)
class Finding:
    """
    One injection-scan hit.

    Purpose: name the rule that matched and carry a short excerpt for forensics
    and for the fence marker, without dumping the whole (possibly huge) text.
    Usage: findings = scan(text); f.rule / f.excerpt.
    Gotchas: excerpt is drawn from the ORIGINAL text (pre-normalization) so an
    owner reading the flag sees what actually arrived, not the folded form.
    """

    rule: str
    excerpt: str


def _normalize(text: str) -> str:
    """
    Purpose: fold evasion tricks (case, whitespace, zero-width, NFKC-equivalent
    homoglyphs) into a canonical form the regexes can match reliably.
    Usage: folded = _normalize(raw); run rules over folded.
    Gotchas: bidi controls are NOT stripped here — they are a signal in their own
    right (control-chars rule) so they must survive to be counted; only zero-width
    joiners/spaces are removed, since those splice words invisibly.
    """
    # NFKC collapses homoglyph/compatibility forms to their canonical characters.
    folded = unicodedata.normalize("NFKC", text)
    # Remove zero-width splicers so "pu<zwsp>sh" becomes "push".
    folded = folded.translate({ord(c): None for c in _ZERO_WIDTH})
    # Collapse runs of whitespace so "push   to    main" matches "push to main".
    folded = re.sub(r"\s+", " ", folded)
    return folded.lower()


def scan(text: str) -> List[Finding]:
    """
    Scan one piece of text for injection signatures (design §3.1).

    Purpose: the single detector used at BOTH chokepoints — INPUT (before a
    WorkerSpec is built) and OUTPUT/diff (before the supervisor acts / before the
    ring gate + merge card). Returns every rule that fired; empty list == clean.

    Usage:
        findings = scan(spec_text)         # chokepoint 1
        findings = scan(worker_json + diff)  # chokepoint 2

    Gotchas:
      * Runs over NFKC-normalized, zero-width-stripped, case-folded text so
        case/whitespace/zero-width evasions do not slip the denylist. Removing
        _normalize weakens the wall — the zero-width test asserts this.
      * Bidi/RLO control characters are flagged directly (control-chars) because
        they are the hidden-instruction trick itself, independent of any keyword.
      * This is a DETECTOR, not a wall (see module RESIDUAL). A clean result does
        NOT prove the absence of injection — it proves no ENUMERATED signature.
    """
    findings: List[Finding] = []

    # Flag bidi / RLO control chars on the raw text (they are the attack itself).
    if any(c in text for c in _BIDI_CONTROLS):
        findings.append(Finding(rule="control-chars", excerpt="<bidi control char>"))

    folded = _normalize(text)
    for rule_name, pattern in _RULES:
        m = pattern.search(folded)
        if m:
            # Draw the excerpt from the folded text (matched span) — bounded length.
            span = m.group(0)
            findings.append(Finding(rule=rule_name, excerpt=span[:120]))

    return findings


def is_injection(text: str) -> bool:
    """
    Purpose: boolean convenience over scan() for call sites that only branch.
    Usage: if is_injection(spec): fence + flag.
    Gotchas: same detector-not-wall caveat as scan(); False means "no enumerated
    signature", never "provably safe".
    """
    return len(scan(text)) > 0


def fence(text: str, findings: List[Finding]) -> str:
    """
    Neutralize flagged INPUT content by fencing it (design §3.1 chokepoint 1).

    Purpose: when supervisor-supplied INPUT is flagged, it must NOT enter the job
    spec raw. This wraps it in a visible marker naming the fired rules so the
    model (and a human) treat it as quarantined external data, not instructions.

    Usage: safe = fence(fetched_content, scan(fetched_content))

    Gotchas:
      * A no-op when findings is empty (clean content is returned unchanged) so
        the caller can fence() unconditionally without corrupting benign specs.
      * Fencing is not neutralization of meaning — it is a mitigation that pairs
        with the flag recorded on the job row; the wall remains tool/cred absence.
    """
    if not findings:
        return text
    rules = ",".join(sorted({f.rule for f in findings}))
    return f"[EXTERNAL CONTENT — flagged: {rules}]\n{text}\n[/EXTERNAL CONTENT]"
