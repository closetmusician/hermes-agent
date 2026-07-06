# ABOUTME: The worker environment scrubber (P2 design §2.3) — the security crux
# ABOUTME: that closes red-team P0-1. scrub_env() builds a COMPLETE REPLACEMENT
# ABOUTME: environment from {} (never inherits os.environ), containing only the
# ABOUTME: §2.3 whitelist + exactly one broker-injected model *_API_KEY. A worker
# ABOUTME: therefore holds no egress credential and cannot push/send from its worktree.
"""
Worker environment scrubber — replacement-whitelist, not overlay.

Design authoritative source: docs/plans/harness/fable/p2/P2-design.md §2.3
Closes: docs/plans/harness/fable/p2/P2-review.md P0-A / S1 (worker inherits
egress creds via ``{**os.environ, **spec.env}`` overlay in
``worker/local_subprocess.py:93``).

The wall this module builds:

  * ``scrub_env`` returns the ENTIRE environment the worker subprocess will see.
    It starts from an empty dict and adds only allowed keys. It never reads or
    copies ``os.environ`` wholesale, so a ``GITHUB_TOKEN`` / ``TELEGRAM_BOT_TOKEN``
    sitting in the supervisor's environment simply is not present in the result.
  * ``assert_no_egress`` is a hard pre-launch check: it raises ``EgressKeyLeaked``
    if the constructed env contains any egress-classed name OR any key outside
    the positive whitelist (the whitelist guard is strictly stronger than a
    denylist — a novel egress var name still gets caught).

The one credential the worker legitimately needs is its *model* key
(``ANTHROPIC_API_KEY`` for claude, ``OPENAI_API_KEY`` for codex). That is a
model-INFERENCE key, not an EGRESS key — it cannot push code, send Telegram, or
write SharePoint. It is injected by the broker (via the supervisor over the
intake path), never read from ``os.environ`` here.
"""

from __future__ import annotations

import shutil
import tempfile
from typing import Dict, Mapping, Optional, Set

# ---------------------------------------------------------------------------
# The positive whitelist — the ONLY environment-variable names a worker may see.
# Anything else (any inherited egress credential, any developer tool dir, any
# ambient secret) is categorically absent because scrub_env builds from {}.
# The single model key is admitted by suffix (`_API_KEY`), not by enumeration,
# so a new provider key name is covered without editing this set.
# ---------------------------------------------------------------------------
_FIXED_ALLOWED_KEYS: Set[str] = {
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TERM",
}

# Model *_API_KEY names the worker may legitimately carry — inference-only.
# Mirrors hermes_cli.egress_creds._MODEL_KEYS (the assistant's kept-key set) so
# a name classified MODEL there is classified MODEL here. Any other _API_KEY is
# still admissible IF the broker injected it as the job's single model key, but
# it must NOT collide with an egress name (guarded below).
_MODEL_KEY_NAMES: frozenset = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
    }
)

# Egress-classed denylist. Superset of hermes_cli.egress_creds._EGRESS_EXACT
# plus the prefix families the design §2.3(B) enumerates. Membership is by exact
# name OR by one of the egress prefixes below — a newly-added platform token is
# caught by class, not only by enumeration. This is the belt; the whitelist
# subset check (assert_no_egress) is the wall.
_EGRESS_EXACT: frozenset = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "SIGNAL_TOKEN",
        "WEIXIN_TOKEN",
        "QQ_BOT_TOKEN",
        "QQBOT_TOKEN",
        "BLUEBUBBLES_TOKEN",
        "YUANBAO_TOKEN",
        "MATRIX_ACCESS_TOKEN",
        "WHATSAPP_TOKEN",
        "TWILIO_AUTH_TOKEN",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
        "MS_GRAPH_CLIENT_SECRET",
        "GRAPH_CLIENT_SECRET",
        "SENDGRID_API_KEY",
    }
)

# Egress-classed name PREFIXES — catches whole families (TELEGRAM_*, SLACK_*,
# DISCORD_*, MS_GRAPH_*, AZURE_*, AWS_*, GOOGLE_*, SENDGRID_*) so a variant like
# TELEGRAM_API_HASH or AWS_SECRET_ACCESS_KEY is classified egress by family.
_EGRESS_PREFIXES: tuple = (
    "TELEGRAM_",
    "SLACK_",
    "DISCORD_",
    "MS_GRAPH_",
    "MICROSOFT_GRAPH_",
    "GRAPH_",
    "AZURE_",
    "AWS_",
    "GOOGLE_",
    "SENDGRID_",
    "GITHUB_",
    "GITLAB_",
)

# A fixed, minimal PATH — no user-specific tool directories, no credential
# helpers. The resolved directory of the worker CLI binary is appended at build
# time so the worker can exec claude/codex; git and the repo test runner come
# from the standard system dirs.
_FIXED_MINIMAL_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"


class EgressKeyLeaked(Exception):
    """
    Raised when a constructed worker env would carry a credential it must not.

    Purpose: fail-closed BEFORE the worker subprocess is spawned — a leaked
    egress key or any key outside the whitelist parks the job NEEDS_ATTENTION
    rather than launching a worker that could push/send.
    Usage: assert_no_egress(clean_env) raises this; the runner catches it.
    Gotchas: this is the falsifiable wall — a test that weakens scrub_env to
    inherit os.environ makes assert_no_egress raise, which is the intended failure.
    """


def is_egress_name(name: str) -> bool:
    """
    Purpose: classify one env-var name as egress-classed (send/push/recipient-
    facing) so assert_no_egress can reject it. Model *_API_KEY names are NOT
    egress (they are inference-only) and return False.
    Usage: if is_egress_name("GITHUB_TOKEN"): reject.
    Gotchas: model keys are checked first so an _API_KEY suffix never mis-flags
    the reasoning key; classification is exact-name OR egress-prefix family, so a
    novel platform token (SLACK_SIGNING_SECRET) is caught by its family prefix.
    """
    if name in _MODEL_KEY_NAMES:
        return False
    if name in _EGRESS_EXACT:
        return True
    return any(name.startswith(prefix) for prefix in _EGRESS_PREFIXES)


def allowed_keys(model_key_name: Optional[str]) -> Set[str]:
    """
    Purpose: the exact set of env-var names permitted for a given job — the fixed
    whitelist plus (optionally) the one broker-injected model key name.
    Usage: allowed = allowed_keys("ANTHROPIC_API_KEY").
    Gotchas: when model_key_name is None the worker gets no model key at all (a
    spec-only or offline job); the runner treats a required-but-missing model key
    as a launch error upstream.
    """
    keys = set(_FIXED_ALLOWED_KEYS)
    if model_key_name:
        keys.add(model_key_name)
    return keys


def _resolve_cli_dir(worker_cli: Optional[str]) -> Optional[str]:
    """
    Purpose: find the directory of the worker CLI binary (claude/codex) so it can
    be appended to the fixed minimal PATH — the worker must be able to exec it.
    Usage: cli_dir = _resolve_cli_dir("claude").
    Gotchas: uses shutil.which at build time; returns None if the binary is not
    found (the fixed PATH still lets the worker run git and the test runner).
    """
    if not worker_cli:
        return None
    resolved = shutil.which(worker_cli)
    if not resolved:
        return None
    # Strip the trailing binary name to get its directory.
    idx = resolved.rfind("/")
    return resolved[:idx] if idx > 0 else None


def scrub_env(
    *,
    model_env: Optional[Mapping[str, str]] = None,
    worker_cli: Optional[str] = None,
    home_dir: Optional[str] = None,
) -> Dict[str, str]:
    """
    Build the COMPLETE REPLACEMENT environment for a factory worker (design §2.3).

    Purpose: return the entire environment the worker subprocess will see — a
    replacement, never an overlay on os.environ. Starts from {} and adds only
    whitelisted keys + at most one broker-injected model key. This is the P0-1
    wall: an egress credential in the supervisor's os.environ cannot appear here
    because os.environ is never read.

    Usage:
        env = scrub_env(model_env={"ANTHROPIC_API_KEY": key},
                        worker_cli="claude")
        # env is the subprocess env; pass it as a replacement, not an overlay.

    Gotchas:
      * model_env MUST be a single-entry dict (the one model key the broker
        resolved for this job). More than one key, or a key that classifies as
        egress, raises EgressKeyLeaked here — fail-closed at construction.
      * HOME is a fresh mktemp dir per job (temp HOME) so no ~/.hermes/.env,
        ~/.ssh, ~/.git-credentials, gh hosts.yml, or broker socket is reachable.
      * Callers pass home_dir only in tests for determinism; production leaves it
        None so a fresh temp HOME is minted each call.
    """
    # Fresh temp HOME per job — blocks every dotfile/credential-file path.
    if home_dir is None:
        home_dir = tempfile.mkdtemp(prefix="factory-worker-home-")
    tmpdir = tempfile.mkdtemp(prefix="factory-worker-tmp-", dir=None)

    # Fixed minimal PATH + the resolved worker-CLI directory (binaries, not secrets).
    path_value = _FIXED_MINIMAL_PATH
    cli_dir = _resolve_cli_dir(worker_cli)
    if cli_dir and cli_dir not in path_value.split(":"):
        path_value = f"{path_value}:{cli_dir}"

    env: Dict[str, str] = {
        "PATH": path_value,
        "HOME": home_dir,
        "TMPDIR": tmpdir,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
    }

    # The single broker-injected model key. Validate it is exactly one entry and
    # is NOT an egress name before admitting it — a mis-shaped model_env is a
    # programming/injection error and must fail closed, not silently leak.
    if model_env:
        items = list(model_env.items())
        if len(items) != 1:
            raise EgressKeyLeaked(
                f"model_env must carry exactly one key, got {len(items)}: "
                f"{sorted(model_env.keys())}"
            )
        name, value = items[0]
        if is_egress_name(name):
            raise EgressKeyLeaked(
                f"model_env key {name!r} classifies as egress; a worker may only "
                f"carry an inference-only model key"
            )
        if not name.endswith("_API_KEY") and name not in _MODEL_KEY_NAMES:
            raise EgressKeyLeaked(
                f"model_env key {name!r} is not a recognized model key "
                f"(expected a *_API_KEY / known model key name)"
            )
        env[name] = value

    return env


def assert_no_egress(env: Mapping[str, str], *, model_key_name: Optional[str] = None) -> None:
    """
    Fail-closed pre-launch check that a constructed worker env is safe (design §2.3B).

    Purpose: the falsifiable wall. Raises EgressKeyLeaked if env contains any
    egress-classed key OR any key outside the positive whitelist. Run this on the
    exact dict about to become the subprocess env, BEFORE launch.

    Usage: assert_no_egress(clean_env, model_key_name="ANTHROPIC_API_KEY")

    Gotchas:
      * The whitelist-subset check is strictly stronger than the egress denylist:
        even a never-before-seen egress var name is caught because it is not in
        ALLOWED_KEYS. The denylist scan runs first only to give a precise error.
      * model_key_name names the single model key expected for this job; if a
        second _API_KEY appears it fails the subset check.
      * This raises against the v1 ``{**os.environ, **spec.env}`` overlay (which
        would carry GITHUB_TOKEN etc.) — that failure is the intended RED proof.
    """
    # (1) Denylist scan — precise error naming the offending egress key.
    leaked = sorted(k for k in env if is_egress_name(k))
    if leaked:
        raise EgressKeyLeaked(
            f"worker env contains egress-classed key(s): {leaked}"
        )

    # (2) Positive whitelist-subset guard — the strictly-stronger wall.
    permitted = allowed_keys(model_key_name)
    outside = sorted(set(env) - permitted)
    if outside:
        raise EgressKeyLeaked(
            f"worker env contains key(s) outside the whitelist {sorted(permitted)}: "
            f"{outside}"
        )
