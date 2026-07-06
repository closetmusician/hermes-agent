# ABOUTME: The broker's real `git push` executor — performs a push for an approved
# ABOUTME: git_push held-action. Runs in the broker process, which holds the push
# ABOUTME: credential (GITHUB_TOKEN/GH_TOKEN) the assistant lacks. Fail-closed: with
# ABOUTME: no push token present it refuses rather than attempting an unauthenticated
# ABOUTME: push. This is the sanctioned push path (design §1.7 E5/E6, bypass B3/B13).
from __future__ import annotations

import os
import subprocess
from typing import Any, Callable, Dict, Optional


def build_git_push_executor(
    egress_cred: Optional[Callable[[str], Optional[str]]] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Purpose: construct the executor that performs a real `git push` for the broker.
    Usage: executor = build_git_push_executor(creds.egress_cred); executor(row).
    Gotchas: the row payload carries {repo, remote, ref, force}. The executor
    refuses (fail-closed) when no push token is resolvable — it never runs an
    unauthenticated push that would prompt or silently no-op. `force` is honored
    only if the row explicitly sets it (force pushes are irreversible ⇒ the safe
    lane holds them for approval upstream). Uses subprocess with an explicit argv
    (no shell) so the token is never interpolated into a shell string.
    """

    def _resolve_token() -> Optional[str]:
        if egress_cred is not None:
            for name in ("GITHUB_TOKEN", "GH_TOKEN"):
                tok = egress_cred(name)
                if tok:
                    return tok
        for name in ("GITHUB_TOKEN", "GH_TOKEN"):
            tok = os.environ.get(name)
            if tok:
                return tok
        return None

    def _execute(row: Dict[str, Any]) -> Dict[str, Any]:
        """
        Purpose: perform one real git push for an approved git_push row.
        Usage: result = executor(row) — broker-only.
        Gotchas: parses {repo, remote, ref, force} from the row payload JSON (or
        the flat row fields); refuses if no token; returns rc/stdout/stderr.
        """
        import json

        payload = row.get("payload") or "{}"
        try:
            spec = json.loads(payload) if isinstance(payload, str) else dict(payload)
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": "git_push payload is not valid JSON"}

        repo = spec.get("repo") or row.get("recipient") or "."
        remote = spec.get("remote") or "origin"
        ref = spec.get("ref") or "HEAD"
        force = bool(spec.get("force", False))

        token = _resolve_token()
        if not token:
            # Fail-closed: the broker is the only holder of the push credential.
            return {
                "success": False,
                "error": "no push credential (GITHUB_TOKEN/GH_TOKEN) available in the broker",
            }

        argv = ["git", "-C", str(repo), "push"]
        if force:
            argv.append("--force")
        argv += [remote, ref]
        env = dict(os.environ)
        env["GITHUB_TOKEN"] = token
        env["GH_TOKEN"] = token
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, env=env, timeout=120
            )
            return {
                "success": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
            }
        except Exception as exc:  # noqa: BLE001 — never crash the broker
            return {"success": False, "error": f"git push failed: {exc}"}

    return _execute
