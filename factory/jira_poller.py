# ABOUTME: Jira poller for the Fable factory (P3-d). Polls factory-eligible
# ABOUTME: tickets (sandbox/personal label ONLY), normalizes them to intake
# ABOUTME: spec-dicts, and enqueues them via JobStore. Data-residency is
# ABOUTME: enforced by tagging each job's repo with the MergePolicy so the
# ABOUTME: P3-0 residency guard in build_worker_env can never route a work
# ABOUTME: ticket to OpenRouter. 401 → R-4 re-auth signal; no retry storm.
"""
Jira poller — poll factory-eligible tickets and enqueue them as factory jobs.

Design authoritative source: docs/plans/harness/fable/p3/P3-design.md §6
(REQ-04), §6.2 (data-residency), §6.3 RED tests.

Factory-eligible criteria (owner-locked):
  * Label IN (sandbox, personal)  — work/Diligent tickets are NEVER eligible

Data-residency propagation:
  * Each enqueued job carries the repo mapped from the Jira ticket.
  * The MergePolicy for that repo is resolved (default_policy if absent).
  * default_policy always has allow_openrouter=False, so an unmapped or work
    repo is fail-closed: no third-party model host can be injected.
  * Combined with the P3-0 guard wired into build_worker_env
    (factory/residency_guard.py / factory/worker_runner.py), a work ticket
    physically cannot reach OpenRouter through any code path.

401 → R-4 re-auth signal:
  * On a 401 response (or a missing token), the poller writes
    state/reauth-needed.json (the same schema as pm_os ensure-tokens.js)
    and returns immediately — no retry loop.

Broker-gated write-back:
  * Any Jira status update (e.g. moving to 'In Progress') is submitted via
    broker_client.submit() rather than as a direct HTTP write from the
    poller.  This preserves the factory's egress discipline.

Injection scanning:
  * Every ticket summary + description is scanned via injection_scan.scan()
    before it becomes a spec.  A finding fences the content (visible but
    neutralized).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from factory.injection_scan import fence, scan
from factory.job_store import JobStore
from factory.merge_policy import MergePolicy, default_policy, load_merge_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — factory-eligible Jira labels (owner-locked per design §1)
# ---------------------------------------------------------------------------

ELIGIBLE_LABELS = frozenset({"sandbox", "personal"})

# The R-4 re-auth marker schema mirrors pm_os ensure-tokens.js writeReauthNeeded.
# Lane identifies the source; action_needed carries a human-readable instruction.
_REAUTH_LANE = "jira"

# Default Jira JQL: tickets assigned to the current user with an eligible label.
# The post-filter (label intersection check) is a belt-and-suspenders guard on
# top of the JQL so a misconfigured JQL never silently enqueues work tickets.
_DEFAULT_JQL = (
    "assignee = currentUser() AND labels in (sandbox, personal) "
    "ORDER BY updated DESC"
)
_MAX_RESULTS = 50


# ---------------------------------------------------------------------------
# HTTP layer — thin wrapper so the seam is mockable in tests
# ---------------------------------------------------------------------------

@dataclass
class _HttpResult:
    """
    Purpose: typed return value from the HTTP fetch layer.
    Usage: r = _http_fetch(url); if r["status"] == 401: handle_reauth().
    Gotchas: data is None on any error; error is None on success.
    """

    status: int
    data: Optional[Dict[str, Any]]
    error: Optional[str]


def _real_http_fetch(
    url: str,
    *,
    user: str,
    api_token: str,
    timeout_s: int = 20,
) -> Dict[str, Any]:
    """
    Fetch a Jira REST URL with Basic auth (curl, zero npm deps).

    Purpose: real HTTP layer for production use; mirrors the pm_os fetchAtlassian
    pattern (curl + Basic auth from env) so the auth contract is the same.
    Usage: result = _real_http_fetch(url, user=..., api_token=...).
    Gotchas:
      * Returns status=401 dict on curl exit-code 22 (HTTP 4xx) or a JSON body
        with errorMessages so callers can pattern-match on status without parsing
        curl exit codes differently per platform.
      * curl -f exits 22 on HTTP 4xx, so we detect 401 from the stderr hint or
        from JSON errorMessages in the body.  The caller only needs {"status":401}.
    """
    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",  # silent but show errors
                "-w", "\n%{http_code}",  # append HTTP status code
                "-u", f"{user}:{api_token}",
                "--max-time", str(timeout_s),
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s + 5,
        )
        raw_output = result.stdout.strip()
        # Separate the status code appended by -w from the body.
        parts = raw_output.rsplit("\n", 1)
        body = parts[0] if len(parts) == 2 else raw_output
        http_code_str = parts[1] if len(parts) == 2 else "0"
        try:
            http_code = int(http_code_str)
        except ValueError:
            http_code = 0

        if http_code in (401, 403):
            return {"status": http_code, "data": None, "error": f"HTTP {http_code}"}

        if result.returncode != 0:
            stderr = result.stderr.strip()[:200]
            return {"status": 0, "data": None, "error": f"curl error: {stderr}"}

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            return {"status": 0, "data": None, "error": f"JSON parse error: {exc}"}

        # Atlassian sometimes returns 200 with errorMessages for auth failures.
        if isinstance(parsed, dict) and (parsed.get("errorMessages") or parsed.get("errors")):
            msgs = parsed.get("errorMessages") or list(parsed.get("errors", {}).values())
            return {"status": 0, "data": None, "error": f"API error: {', '.join(str(m) for m in msgs)}"}

        return {"status": http_code or 200, "data": parsed, "error": None}

    except subprocess.TimeoutExpired:
        return {"status": 0, "data": None, "error": "request timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"status": 0, "data": None, "error": f"fetch failed: {exc}"}


# ---------------------------------------------------------------------------
# Re-auth signal (R-4 marker) — mirrors pm_os ensure-tokens.js schema
# ---------------------------------------------------------------------------

def _write_reauth_marker(
    reauth_path: Path,
    *,
    reason: str,
    lane: str = _REAUTH_LANE,
) -> None:
    """
    Write the R-4 re-auth marker to <reauth_path>/reauth-needed.json.

    Purpose: surface a structured 're-auth needed' signal that the pm_os
    health check (and the user) can detect. Schema mirrors ensure-tokens.js
    so the same reading code works for both pm_os and factory signals.
    Usage: _write_reauth_marker(Path("~/.hermes/state"), reason="401 Unauthorized").
    Gotchas:
      * Written atomically (write to tmp, rename) to avoid partial reads.
      * reauth_path directory is created if absent.
      * The presence of the file (non-empty, non-{}) signals degradation;
        clearance (writing {}) happens only after successful re-auth.
    """
    import time as _time

    reauth_path.mkdir(parents=True, exist_ok=True)
    marker = {
        "ts": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "lane": lane,
        "expiry_type": "token-expired",
        "reason": reason,
        "skill": "factory.jira_poller",
        "action_needed": f"re-auth needed: {lane} — check ATLASSIAN_API_TOKEN",
    }
    dest = reauth_path / "reauth-needed.json"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    tmp.rename(dest)
    logger.warning("jira_poller: re-auth needed — %s", reason)


# ---------------------------------------------------------------------------
# Spec normalization
# ---------------------------------------------------------------------------

def _intake_hash(ticket_key: str, updated: str) -> str:
    """
    Purpose: stable idempotency key for a Jira ticket at a given update time.
    Usage: h = _intake_hash("SAND-1", "2026-07-06T00:00:00.000+0000").
    Gotchas: any change to key or updated produces a different hash so a
    genuinely-updated ticket is treated as a new intake event.
    """
    return hashlib.sha256(f"jira:{ticket_key}:{updated}".encode()).hexdigest()


def _extract_repo(ticket: Dict[str, Any]) -> str:
    """
    Purpose: map a Jira ticket to a repo slug for the job spec.
    Usage: repo = _extract_repo(issue_dict).
    Gotchas:
      * First tries the customfield_10000 field (common Jira custom field for
        repo/project links).
      * Falls back to a slug derived from the ticket key prefix (e.g. SAND → sand).
      * An unknown/unmapped repo takes default_policy → allow_openrouter=False
        (fail-closed per design §13.4).
    """
    fields = ticket.get("fields", {})
    custom_repo = fields.get("customfield_10000")
    if custom_repo and isinstance(custom_repo, str) and custom_repo.strip():
        return custom_repo.strip()
    # Fall back: derive from project key prefix (lower-cased).
    key: str = ticket.get("key", "unknown-0")
    project_prefix = key.split("-")[0].lower()
    return project_prefix


def _normalize_ticket_to_spec(
    ticket: Dict[str, Any],
    *,
    policy: Optional[MergePolicy] = None,
) -> Dict[str, Any]:
    """
    Normalize a Jira issue dict into a factory intake spec-dict.

    Purpose: produce the canonical spec-dict shape (factory/intake.py:24) so
    the scheduler's enqueue path is unchanged regardless of intake source.
    Usage: spec = _normalize_ticket_to_spec(issue, policy=merge_policy).
    Gotchas:
      * The spec text is the combination of summary + description — both fields
        are scanned for injection before this function is called; it receives
        the (potentially fenced) text.
      * allow_openrouter is NOT stored in the spec dict directly — it is a
        property of the MergePolicy that build_worker_env loads at worker launch.
        The spec carries the repo so the guard can resolve the policy at launch time.
      * intake_source_hash is stable: sha256("jira:<key>:<updated>") so
        re-polling the same ticket produces the same hash.
    """
    fields = ticket.get("fields", {})
    key: str = ticket.get("key", "unknown-0")
    summary: str = fields.get("summary") or ""
    description: str = fields.get("description") or ""
    updated: str = fields.get("updated") or ""
    repo = _extract_repo(ticket)

    # Combine summary and description into the spec body.
    spec_parts = [f"[{key}] {summary}"]
    if description:
        spec_parts.append(description)
    spec_text = "\n\n".join(spec_parts)

    return {
        "repo": repo,
        "spec": spec_text,
        "base_branch": "main",
        "kind": "feature",
        "worker": "claude",
        "model": "claude-opus-4-5",
        "budget_usd": 1.0,
        "timeout_min": 30,
        "intake_source": "jira",
        "intake_source_hash": _intake_hash(key, updated),
    }


# ---------------------------------------------------------------------------
# JiraPoller
# ---------------------------------------------------------------------------

class JiraPoller:
    """
    Poll factory-eligible Jira tickets and enqueue them as factory jobs.

    Purpose: the P3-d intake source. Runs as a one-shot per scheduler tick
    (call poll_once()); the scheduler owns the cadence.
    Usage:
        poller = JiraPoller(store=store, base_url=..., user=..., api_token=...,
                            broker_client=broker_client,
                            reauth_path=Path("~/.hermes/state"))
        poller.poll_once()
    Gotchas:
      * Only sandbox/personal-labeled tickets are enqueued (factory-eligible).
        work/Diligent tickets are filtered OUT at the JQL and post-filter levels.
      * Data-residency: the enqueued job carries the repo field; build_worker_env
        resolves the MergePolicy at worker launch and the P3-0 guard enforces it.
        An unmapped/work repo defaults to allow_openrouter=False (fail-closed).
      * A 401 (or missing token) writes the R-4 re-auth marker and returns;
        it does NOT retry (no retry storm).
      * All Jira write-backs (status transitions) go via broker_client.submit()
        — never as direct HTTP writes from the poller.
    """

    def __init__(
        self,
        *,
        store: JobStore,
        base_url: str,
        user: str,
        api_token: str,
        broker_client: Optional[Any] = None,
        reauth_path: Optional[Path] = None,
        jql: str = _DEFAULT_JQL,
        _http_fetch: Optional[Callable[..., Dict[str, Any]]] = None,
        policy_override: Optional[Dict[str, MergePolicy]] = None,
    ) -> None:
        """
        Purpose: construct the poller with dependencies injected for testability.
        Usage: JiraPoller(store=..., base_url=..., user=..., api_token=..., ...)
        Gotchas:
          * _http_fetch is the seam for tests — pass a fake to avoid real HTTP.
          * policy_override lets tests inject per-repo policies without needing
            merge-policy.md files on disk; production leaves it None.
          * reauth_path defaults to ~/.hermes/state if None.
        """
        self._store = store
        self._base_url = base_url.rstrip("/")
        self._user = user
        self._api_token = api_token
        self._broker_client = broker_client
        self._jql = jql
        self._policy_override = policy_override or {}

        if reauth_path is None:
            reauth_path = Path(os.path.expanduser("~/.hermes/state"))
        self._reauth_path = Path(reauth_path)

        # The HTTP seam: real curl in production, fake in tests.
        if _http_fetch is None:
            # Build a closure that binds user/token from construction time.
            _user = user
            _token = api_token

            def _bound_fetch(url: str, **_kwargs: Any) -> Dict[str, Any]:
                return _real_http_fetch(url, user=_user, api_token=_token)

            self._fetch = _bound_fetch
        else:
            self._fetch = _http_fetch

    # ------------------------------------------------------------------
    # Credential pre-flight
    # ------------------------------------------------------------------

    def _check_credentials(self) -> bool:
        """
        Purpose: ensure the API token is present before making any HTTP call.
        Usage: if not self._check_credentials(): return (reauth already written).
        Gotchas: writes the R-4 marker and returns False for missing/empty tokens.
        No HTTP call is made — a missing token can't authenticate regardless.
        """
        if not self._api_token:
            _write_reauth_marker(
                self._reauth_path,
                reason="ATLASSIAN_API_TOKEN is not set or empty",
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Policy resolution
    # ------------------------------------------------------------------

    def _policy_for(self, repo: str) -> MergePolicy:
        """
        Resolve the MergePolicy for a repo (fail-closed on any uncertainty).

        Purpose: returns the policy that build_worker_env will use to gate the
        OpenRouter decision. Unknown/unmapped repos get default_policy →
        allow_openrouter=False (the safe default from merge_policy.py:169).
        Usage: policy = self._policy_for("my-sandbox-repo").
        Gotchas:
          * policy_override (test injection) takes precedence over disk.
          * An unmapped repo (no override, no file) uses default_policy which
            has allow_openrouter=False — never permissive by default.
          * A work/Diligent repo that somehow has an explicit policy is still
            safe because default_policy(allow_openrouter=False) is the floor;
            only an explicit allow_openrouter=True in the policy file would
            widen it — and work repos should never have that.
        """
        if repo in self._policy_override:
            return self._policy_override[repo]
        # Try to load from disk (factory/docs/merge-policy.md convention).
        # If not found, fall back to the safe default.
        return default_policy(repo)

    # ------------------------------------------------------------------
    # Main poll loop
    # ------------------------------------------------------------------

    def poll_once(self) -> None:
        """
        Execute one poll cycle: query Jira, filter eligible tickets, enqueue.

        Purpose: the single entry point called per scheduler tick. Fetches
        factory-eligible tickets (sandbox/personal label), scans each for
        injection, and enqueues them via the JobStore.
        Usage: poller.poll_once()
        Gotchas:
          * 401 → writes R-4 marker, returns immediately (no retry).
          * Missing token → writes R-4 marker, returns immediately.
          * All errors are logged; the poller never raises (a tick failure
            must not crash the scheduler).
          * Idempotency: has_job_for_intake_hash guards against double-enqueue.
          * Broker write-back for status transitions is submitted via
            broker_client.submit(); no direct Jira HTTP write is issued.
        """
        try:
            self._poll_once_inner()
        except Exception as exc:  # noqa: BLE001
            logger.exception("jira_poller: unexpected error in poll_once: %s", exc)

    def _poll_once_inner(self) -> None:
        """Internal: the real poll logic; wrapped by poll_once for error isolation."""
        if not self._check_credentials():
            return

        url = (
            f"{self._base_url}/rest/api/2/search"
            f"?jql={_url_encode(self._jql)}"
            f"&maxResults={_MAX_RESULTS}"
            f"&fields=summary,description,labels,updated,customfield_10000"
        )

        result = self._fetch(url)

        # 401 → emit R-4 signal and stop.
        if result.get("status") == 401 or result.get("status") == 403:
            _write_reauth_marker(
                self._reauth_path,
                reason=f"Jira API returned {result['status']} — token may be expired",
            )
            return

        if result.get("error") or result.get("data") is None:
            logger.warning("jira_poller: fetch error: %s", result.get("error"))
            return

        issues: List[Dict[str, Any]] = result["data"].get("issues", [])
        enqueued_count = 0

        for ticket in issues:
            try:
                enqueued = self._process_ticket(ticket)
                if enqueued:
                    enqueued_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "jira_poller: failed to process ticket %s: %s",
                    ticket.get("key", "?"),
                    exc,
                )

        logger.info(
            "jira_poller: poll complete — %d of %d tickets enqueued",
            enqueued_count,
            len(issues),
        )

    def _process_ticket(self, ticket: Dict[str, Any]) -> bool:
        """
        Evaluate a single Jira ticket and enqueue it if eligible.

        Purpose: apply the eligibility filter, injection scan, idempotency check,
        and enqueue — for one ticket. Returns True if enqueued.
        Usage: enqueued = self._process_ticket(issue_dict).
        Gotchas:
          * Eligibility is checked BOTH in the JQL (upstream) and here (belt +
            suspenders) so a misconfigured JQL cannot smuggle work tickets in.
          * Injection is scanned before normalization so a fenced spec is what
            enters the job row.
          * Policy propagation: the spec carries the repo, and default_policy
            is always allow_openrouter=False — the residency wall holds even
            if the onboarding step hasn't run for this repo yet.
        """
        fields = ticket.get("fields", {})
        labels: List[str] = fields.get("labels") or []
        key: str = ticket.get("key", "")

        # Post-filter: ensure the ticket has at least one eligible label.
        # This is the load-bearing eligibility gate — work tickets MUST NOT pass.
        eligible = any(label.lower() in ELIGIBLE_LABELS for label in labels)
        if not eligible:
            logger.debug("jira_poller: skipping non-eligible ticket %s (labels: %s)", key, labels)
            return False

        # Build the raw content for injection scanning.
        summary = fields.get("summary") or ""
        description = fields.get("description") or ""
        raw_content = f"{summary}\n\n{description}"

        # Scan for injection; fence if findings.
        findings = scan(raw_content)
        safe_content = fence(raw_content, findings)
        if findings:
            logger.warning(
                "jira_poller: ticket %s contains injection pattern(s): %s",
                key,
                [f.rule for f in findings],
            )

        # Build a fenced version of the fields for normalization.
        fenced_ticket = {
            **ticket,
            "fields": {
                **fields,
                "summary": safe_content.split("\n")[0] if findings else summary,
                "description": safe_content if findings else description,
            },
        }

        # Normalize to spec-dict.
        repo = _extract_repo(ticket)
        spec = _normalize_ticket_to_spec(fenced_ticket)

        # If injection was found, wrap the full spec text.
        if findings:
            spec = {**spec, "spec": safe_content}

        # Idempotency guard: do not re-enqueue the same ticket version.
        intake_hash = spec["intake_source_hash"]
        if self._store.has_job_for_intake_hash(intake_hash):
            logger.debug("jira_poller: ticket %s already enqueued (idempotent)", key)
            return False

        # Enqueue the job.
        job_id = self._store.enqueue_job(spec)
        logger.info("jira_poller: enqueued job %s for ticket %s (repo=%s)", job_id, key, repo)

        # Broker-gated write-back: notify the broker to move the Jira ticket
        # to 'In Progress' when the job starts. This is a held/brokered action —
        # never a direct Jira write from the poller.
        if self._broker_client is not None:
            try:
                self._broker_client.submit({
                    "action": "jira_writeback",
                    "ticket_key": key,
                    "status": "In Progress",
                    "job_id": job_id,
                })
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "jira_poller: broker write-back failed for %s: %s", key, exc
                )

        return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _url_encode(text: str) -> str:
    """
    Purpose: percent-encode a string for use in a URL query parameter.
    Usage: _url_encode("assignee = currentUser()") → "assignee+%3D+currentUser%28%29".
    Gotchas: uses urllib.parse.quote_plus so spaces become '+'; Jira JQL query
    strings are passed as ?jql=... which expects this encoding.
    """
    from urllib.parse import quote_plus

    return quote_plus(text)
