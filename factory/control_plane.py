# ABOUTME: Centralized read-as-DATA boundary for control-plane config files (P6-c REQ-02).
# ABOUTME: read_config_file() applies injection_scan then parses ONLY recognized key:value
# ABOUTME: lines — injected directives are never executed, only inert text in the file.
# ABOUTME: Mirrors trust_policy.py:84 value-only regex parse; never execs file content.
# ABOUTME: Used by proactive_contract, trust_policy, and any render layer that displays config.
"""
factory.control_plane — single read-as-DATA boundary for control-plane files.

Design authority: docs/plans/harness/fable/p6/P6-design.md §3.4

The security boundary: control-plane file content is DATA the factory *reads*,
never instructions it *executes*.  A file whose body contains injected directives
("ignore prior instructions", "disable the broker") is parsed as UNRECOGNIZED
config lines — the recognized key: value pairs come through unchanged, the
injected text is returned as scan findings for the render layer to fence.

Mirrors trust_policy.py:84 value-only regex pattern (the existing correct boundary):
  * Only lines matching ``key: value`` (where key is [\\w]+) are extracted.
  * A line that is not in the recognized-keys set for the caller is returned in the
    raw dict; callers that want a strict whitelist filter further.
  * The raw file bytes are scanned with injection_scan.scan() BEFORE parse so any
    injection signatures are captured in findings — callers can fence them for display.

Anti-weakening: the read-as-data test FAILS if injected content (lines that look like
key: value for injected keys) alters the recognized-key values, OR if scan() is not
called on the raw content.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Tuple

from factory.injection_scan import Finding, scan


# Key extraction regex — mirrors trust_policy.py:_extract() pattern.
# Matches: <optional whitespace> key <colon> <value> <optional inline comment>
# Restrictions: key must be all word characters (no spaces, no special chars) so that
# prose headers ("# Some Title") and sentences do not match as keys.
_KEY_VALUE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z_]\w*)\s*:\s*(?P<value>[^\n#]+?)(?:\s+#.*)?$",
    re.MULTILINE,
)


def read_config_file(path: Path) -> Tuple[Dict[str, str], List[Finding]]:
    """
    Read a control-plane config file as DATA.

    Purpose: the single entry point for reading any control-plane config file
    (proactive-contract.md, trust-policy.md, safe-lane.md).  Applies
    injection_scan FIRST (returns findings for the render layer to fence), then
    extracts ONLY recognized key: value lines into a dict.  Injected directives
    that do not match the ``key: value`` pattern are silently ignored.  Injected
    lines that DO look like ``key: value`` are present in the raw dict but callers
    apply their own whitelist (e.g. trust_policy.load_trust_policy only reads
    named keys via _extract, ignoring everything else).

    Usage:
        values, findings = read_config_file(Path("docs/factory/proactive-contract.md"))
        if findings:
            display_fenced(fence(body, findings))
        nudge_max = int(values.get("nudge_max", "1"))

    Gotchas:
      * The returned dict contains ALL ``key: value`` matches, not just a fixed
        whitelist — the caller is responsible for taking only the keys it knows.
        This mirrors trust_policy._extract() which reads named keys and ignores the rest.
      * The scan is run over the raw file text, not the parsed values.  A finding
        in ``findings`` means the file body triggered an injection signature; the
        parsed values are still safe to use (they contain no executable code by
        construction of the regex).
      * Inline YAML-style comments (`` # ...``) are stripped from values.
    """
    text = Path(path).read_text()

    # Scan the raw body for injection signatures BEFORE any parse.
    findings: List[Finding] = scan(text)

    # Value-only parse: extract key: value lines, strip inline comments.
    values: Dict[str, str] = {}
    for m in _KEY_VALUE_RE.finditer(text):
        key = m.group("key").strip()
        raw_val = m.group("value").strip()
        # Strip trailing inline comment (anything after unquoted ' #')
        raw_val = re.sub(r"\s+#.*$", "", raw_val).strip()
        values[key] = raw_val

    return values, findings


def load_proactive_contract(path: Path) -> Dict[str, str]:
    """
    Load only the recognized proactive-contract keys as a strict whitelist.

    Purpose: convenience wrapper over read_config_file() that applies the
    proactive-contract whitelist (nudge_max, repeat, anomaly_window_min).
    Usage: cfg = load_proactive_contract(contract_path)
    Gotchas: unrecognized keys (including any injected ones) are silently dropped;
    missing recognized keys fall back to their quiet-by-default defaults.
    """
    _RECOGNIZED = {"nudge_max", "repeat", "anomaly_window_min"}
    all_values, _ = read_config_file(path)
    return {k: v for k, v in all_values.items() if k in _RECOGNIZED}
