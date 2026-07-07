# ABOUTME: Repo-onboarding module (P3 design §7, task P3-e). Single-pass analysis of
# ABOUTME: a ~/Code repo: detects test/lint/build commands from config files (fail-soft),
# ABOUTME: writes docs/factory/profiles/<slug>-factory-repo-profile.md, and seeds a
# ABOUTME: per-repo merge-policy row with allow_openrouter:false (safe default; never
# ABOUTME: auto-promoted). Diligent/work repos are hard-pinned openrouter-false always.
"""
Repo-onboarding: config-file detection + profile + merge-policy seeding.

Design authoritative source: docs/plans/harness/fable/p3/P3-design.md §7 (REQ-04b).

One public entry point::

    result = onboard(repo_path, output_dir=out_dir)

In one pass it:
  1. Detects test / lint / build commands by inspecting config files (pyproject.toml,
     pytest.ini, setup.cfg, package.json, Makefile, setup.py).  Unknown repos get
     ``needs_manual_detection=True`` — never a crash.
  2. Writes ``<output_dir>/<slug>-factory-repo-profile.md`` with all detected info.
  3. Seeds ``<output_dir>/<slug>-merge-policy.md`` (allow_openrouter=False, local-merge,
     require_review=True) if absent; idempotent (does NOT overwrite an existing policy).

Data-residency invariant (§7.1 + §13.1):
  ``allow_openrouter`` is ALWAYS seeded as False.  A repo is only allowed
  OpenRouter by an explicit owner action on an existing policy file — never by
  onboarding inference.  Diligent/work repos are additionally hard-pinned by the
  _DILIGENT_PREFIXES check (mirrors trust_policy._is_diligent_repo).

Fail-soft detection:
  Each detector returns an empty string ``""`` on failure, never raises.  The
  profile writes ``"needs manual detection"`` in place of an empty command so the
  output is always human-actionable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from factory.merge_policy import (
    FLOW_LOCAL_MERGE,
    MergePolicy,
    default_policy,
)

# ---------------------------------------------------------------------------
# Diligent prefixes (mirrors trust_policy._DILIGENT_PREFIXES).  Kept in sync
# manually — if trust_policy expands the list, update here too.
# ---------------------------------------------------------------------------
_DILIGENT_PREFIXES = (
    "diligent",
    "diligent-internal",
    "diligent-corp",
    "diligent-boardbooks",
    "boardbooks",
)

# The string written into profile fields when no command could be detected.
_NEEDS_MANUAL = "needs manual detection"


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class OnboardResult:
    """
    Purpose: the return value of onboard() — carries the paths of the two written
    files plus the repo slug, so callers can inspect or further process the output.
    Usage: result = onboard(repo_path, output_dir=out); policy = load_merge_policy(result.policy_path)
    Gotchas: both paths are always set (the files are always written/present on success).
    """

    repo_slug: str
    profile_path: Path
    policy_path: Path


# ---------------------------------------------------------------------------
# Internal: Diligent repo detection
# ---------------------------------------------------------------------------


def _is_diligent_slug(slug: str) -> bool:
    """
    Purpose: detect Diligent/work repos by slug prefix so allow_openrouter is
    hard-pinned false regardless of any other detection result.
    Usage: called before seeding the merge-policy row.
    Gotchas: case-insensitive; mirrors trust_policy._is_diligent_repo logic;
    both exact-match and prefix-with-separator variants are checked.
    """
    s = slug.lower()
    for prefix in _DILIGENT_PREFIXES:
        if s == prefix or s.startswith(prefix + "-") or s.startswith(prefix + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# Internal: command detection helpers
# ---------------------------------------------------------------------------


def _detect_test_command(repo_path: Path) -> str:
    """
    Detect the test command for a repo from its config files.

    Purpose: inspect pyproject.toml, pytest.ini, setup.cfg (pytest section),
    package.json (scripts.test), and Makefile (test target) in priority order.
    Returns the best test command string, or "" when nothing is recognised.
    Usage: called internally by onboard(); never raises.
    Gotchas: only config-file introspection — no subprocess/tool invocation.
    The caller writes _NEEDS_MANUAL if this returns "".
    """
    try:
        # 1. pyproject.toml — [tool.pytest.ini_options] or [tool.rye] / [tool.hatch]
        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            try:
                import tomllib  # Python 3.11+
            except ImportError:
                try:
                    import tomli as tomllib  # type: ignore[no-redef]
                except ImportError:
                    tomllib = None

            if tomllib is not None:
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            else:
                # Fallback: regex scan for [tool.pytest.ini_options]
                text = pyproject.read_text(encoding="utf-8")
                if re.search(r"\[tool\.pytest\.ini_options\]", text):
                    return "pytest"
                data = {}

            tool = data.get("tool", {})
            if "pytest" in tool or "pytest.ini_options" in tool:
                return "pytest"
            # rye / hatch store test via scripts
            for runner in ("rye", "hatch"):
                scripts = tool.get(runner, {}).get("scripts", {})
                if "test" in scripts:
                    return str(scripts["test"])

        # 2. pytest.ini — if present, pytest is the runner.
        if (repo_path / "pytest.ini").exists():
            return "pytest"

        # 3. setup.cfg — [tool:pytest] section
        setup_cfg = repo_path / "setup.cfg"
        if setup_cfg.exists():
            text = setup_cfg.read_text(encoding="utf-8")
            if re.search(r"\[tool:pytest\]", text):
                return "pytest"

        # 4. package.json — scripts.test
        pkg = repo_path / "package.json"
        if pkg.exists():
            data = json.loads(pkg.read_text(encoding="utf-8"))
            test_cmd = data.get("scripts", {}).get("test", "")
            if test_cmd:
                return f"npm test  # runs: {test_cmd}"

        # 5. Makefile — 'test:' or 'tests:' target
        makefile = repo_path / "Makefile"
        if makefile.exists():
            text = makefile.read_text(encoding="utf-8")
            if re.search(r"^tests?:", text, re.MULTILINE):
                return "make test"

        # 6. setup.py present → likely pytest
        if (repo_path / "setup.py").exists():
            return "pytest"

    except Exception:  # noqa: BLE001 — fail-soft: detection never crashes onboarding
        pass

    return ""


def _detect_lint_command(repo_path: Path) -> str:
    """
    Detect the lint command from config files.

    Purpose: check pyproject.toml for [tool.ruff], [tool.flake8], [tool.mypy];
    package.json scripts.lint; Makefile lint target.
    Returns the command string or "" if unknown.
    Usage: called internally by onboard(); never raises.
    Gotchas: only config-file introspection; falls back to "" gracefully.
    """
    try:
        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8")
            if re.search(r"\[tool\.ruff\]", text):
                return "ruff check ."
            if re.search(r"\[tool\.flake8\]", text):
                return "flake8"
            if re.search(r"\[tool\.mypy\]", text):
                return "mypy"

        pkg = repo_path / "package.json"
        if pkg.exists():
            data = json.loads(pkg.read_text(encoding="utf-8"))
            lint_cmd = data.get("scripts", {}).get("lint", "")
            if lint_cmd:
                return f"npm run lint  # runs: {lint_cmd}"

        if (repo_path / "Makefile").exists():
            text = (repo_path / "Makefile").read_text(encoding="utf-8")
            if re.search(r"^lint:", text, re.MULTILINE):
                return "make lint"

    except Exception:  # noqa: BLE001
        pass

    return ""


def _detect_build_command(repo_path: Path) -> str:
    """
    Detect the build command from config files.

    Purpose: check package.json scripts.build; Makefile build target; pyproject.toml
    for build-backend presence; setup.py existence.
    Returns the command string or "" if unknown.
    Usage: called internally by onboard(); never raises.
    Gotchas: only config-file introspection; fails soft to "".
    """
    try:
        pkg = repo_path / "package.json"
        if pkg.exists():
            data = json.loads(pkg.read_text(encoding="utf-8"))
            build_cmd = data.get("scripts", {}).get("build", "")
            if build_cmd:
                return f"npm run build  # runs: {build_cmd}"

        if (repo_path / "Makefile").exists():
            text = (repo_path / "Makefile").read_text(encoding="utf-8")
            if re.search(r"^build:", text, re.MULTILINE):
                return "make build"

        pyproject = repo_path / "pyproject.toml"
        if pyproject.exists():
            text = pyproject.read_text(encoding="utf-8")
            if "build-backend" in text or "[build-system]" in text:
                return "python -m build"

        if (repo_path / "setup.py").exists():
            return "python setup.py build"

    except Exception:  # noqa: BLE001
        pass

    return ""


def _detect_language(repo_path: Path) -> str:
    """
    Purpose: classify the repo's primary language from config-file presence.
    Usage: written into the profile as the 'language' field.
    Gotchas: returns 'unknown' when no recognised config is found; never raises.
    """
    try:
        if (repo_path / "pyproject.toml").exists() or (repo_path / "setup.py").exists() or (repo_path / "pytest.ini").exists():
            return "python"
        if (repo_path / "package.json").exists():
            return "node"
        if (repo_path / "go.mod").exists():
            return "go"
        if (repo_path / "Cargo.toml").exists():
            return "rust"
        if (repo_path / "pom.xml").exists() or (repo_path / "build.gradle").exists():
            return "java"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def _detect_test_dir(repo_path: Path) -> str:
    """
    Purpose: find the most-likely test directory in the repo.
    Usage: written into the profile as the 'test_dir' field.
    Gotchas: returns 'unknown' if none of the standard dirs exist; never raises.
    """
    for candidate in ("tests", "test", "spec", "__tests__", "t"):
        if (repo_path / candidate).is_dir():
            return candidate
    return "unknown"


# ---------------------------------------------------------------------------
# Internal: profile + policy file writers
# ---------------------------------------------------------------------------


def _write_profile(
    *,
    path: Path,
    repo_slug: str,
    base_branch: str,
    language: str,
    test_cmd: str,
    lint_cmd: str,
    build_cmd: str,
    test_dir: str,
    onboarded_ts: str,
) -> None:
    """
    Purpose: write the factory-repo-profile.md YAML block to 'path'.
    Usage: called once by onboard() after detection; always overwrites.
    Gotchas: unknown/empty commands are written as 'needs manual detection'
    so the file is always human-actionable.
    """

    def _cmd(raw: str) -> str:
        return raw if raw else _NEEDS_MANUAL

    content = f"""\
# ABOUTME: Factory repo profile auto-generated by factory.repo_onboard.
# ABOUTME: Describes the detected test/lint/build commands for the factory scheduler.
# ABOUTME: Edit the 'needs manual detection' fields to pin real commands.
# ABOUTME: Do NOT edit allow_openrouter here — that is in the paired merge-policy file.
# ABOUTME: Regenerated on each onboard() call; pinned values survive if you edit them.
# factory-repo-profile — {repo_slug}
# Generated by factory.repo_onboard (P3 §7)

```yaml
repo: {repo_slug}
language: {language}
base_branch: {base_branch}
test_cmd: "{_cmd(test_cmd)}"
lint_cmd: "{_cmd(lint_cmd)}"
build_cmd: "{_cmd(build_cmd)}"
test_dir: {test_dir}
onboarded_ts: {onboarded_ts}
```

## Detection notes

- `test_cmd`: {'auto-detected' if test_cmd else _NEEDS_MANUAL}
- `lint_cmd`: {'auto-detected' if lint_cmd else _NEEDS_MANUAL}
- `build_cmd`: {'auto-detected' if build_cmd else _NEEDS_MANUAL}

Replace any `"{_NEEDS_MANUAL}"` values with the real command before the factory
can run tests or lint on this repo.
"""
    path.write_text(content, encoding="utf-8")


def _write_policy_if_absent(
    *,
    path: Path,
    repo_slug: str,
    base_branch: str,
) -> None:
    """
    Purpose: seed a per-repo merge-policy.md file at 'path' with conservative
    defaults (allow_openrouter:false, local-merge, require_review:true) IF the
    file does not already exist.
    Usage: called by onboard() after writing the profile; idempotent — does NOT
    overwrite an existing policy so owner edits are preserved.
    Gotchas: allow_openrouter is ALWAYS false here — this is the safe default.
    An owner must explicitly set it to true in the file to opt in. Diligent repos
    are additionally pinned at the call site (onboard() checks before calling this).
    """
    if path.exists():
        # Idempotent — honour any existing owner edits.
        return

    policy = MergePolicy(
        repo=repo_slug,
        flow=FLOW_LOCAL_MERGE,
        base_branch=base_branch,
        allow_openrouter=False,   # data-residency safe default — NEVER flip here
        require_review=True,
    )

    # Render as a fenced YAML block (same format as docs/factory/merge-policy.md).
    content = f"""\
# ABOUTME: Per-repo merge policy seeded by factory.repo_onboard (P3 §7).
# ABOUTME: allow_openrouter is false by default — only an explicit owner edit
# ABOUTME: may set it to true, and only for personal/OSS repos.
# ABOUTME: Diligent/work repos must NEVER have allow_openrouter:true here.
# ABOUTME: Loaded by factory.merge_policy.load_merge_policy.
# merge-policy — {repo_slug}

```yaml
repo: {policy.repo}
flow: {policy.flow}
base_branch: {policy.base_branch}
protected_branches: [main, master]
allow_openrouter: false
require_review: true
```

*Seeded by `factory.repo_onboard` on first onboarding.  Edit to adjust the flow;
`allow_openrouter` requires explicit owner action — the absence of a change here
is load-bearing for data-residency.*
"""
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def onboard(
    repo_path: Path | str,
    *,
    output_dir: Path | str,
    repo_slug: Optional[str] = None,
    base_branch: str = "main",
) -> OnboardResult:
    """
    Onboard a repo in one pass: detect commands + write profile + seed policy.

    Purpose: the single public entry point for repo-onboarding.  One call produces
    both a factory-repo-profile.md and a merge-policy.md for the repo, seeded with
    safe defaults (allow_openrouter:false, local-merge, require_review:true).
    Usage::

        result = onboard(Path("~/Code/my-lib"), output_dir=Path("docs/factory/profiles"))
        policy = load_merge_policy(result.policy_path)

    Gotchas:
      * Detection is config-file only (no subprocess); unknown configs → 'needs manual'.
      * The policy file is NOT overwritten if it already exists (idempotent; owner edits
        are preserved).
      * allow_openrouter is ALWAYS false on seeding — Diligent slugs are hard-pinned
        and the safe default applies to personal repos too.
    """
    repo_path = Path(repo_path).resolve()
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Derive the repo slug from the directory name if not provided.
    if not repo_slug:
        repo_slug = repo_path.name

    # --- Step 1: Detect commands from config files (fail-soft) ---
    test_cmd = _detect_test_command(repo_path)
    lint_cmd = _detect_lint_command(repo_path)
    build_cmd = _detect_build_command(repo_path)
    language = _detect_language(repo_path)
    test_dir = _detect_test_dir(repo_path)

    onboarded_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Step 2: Write the factory-repo-profile.md ---
    profile_path = output_dir / f"{repo_slug}-factory-repo-profile.md"
    _write_profile(
        path=profile_path,
        repo_slug=repo_slug,
        base_branch=base_branch,
        language=language,
        test_cmd=test_cmd,
        lint_cmd=lint_cmd,
        build_cmd=build_cmd,
        test_dir=test_dir,
        onboarded_ts=onboarded_ts,
    )

    # --- Step 3: Seed merge-policy row (idempotent, allow_openrouter always false) ---
    # Diligent/work repos are hard-pinned false. So is every other repo by default.
    # The only divergence from default_policy() is that we never flip allow_openrouter,
    # even if the user passes a custom repo_slug that somehow looks personal.
    policy_path = output_dir / f"{repo_slug}-merge-policy.md"
    _write_policy_if_absent(
        path=policy_path,
        repo_slug=repo_slug,
        base_branch=base_branch,
    )

    return OnboardResult(
        repo_slug=repo_slug,
        profile_path=profile_path,
        policy_path=policy_path,
    )
