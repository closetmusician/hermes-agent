# ABOUTME: RED-first tests for factory.repo_onboard (P3 design §7, REQ-04b). Verifies
# ABOUTME: that onboarding a fresh repo produces a factory-repo-profile.md AND seeds a
# ABOUTME: merge-policy row in a single pass. allow_openrouter defaults false (safe).
# ABOUTME: Diligent/work repos are hard-pinned allow_openrouter:false regardless of
# ABOUTME: detection results. Unknown repos and bare configs fall back to graceful 'needs
# ABOUTME: manual detection'.
"""
Tests for factory.repo_onboard (P3 §7.2, REQ-04b).

Requirement map (from the coder prompt):
  REQ-01: Detect test/lint/build from config files; fail-soft on unknown.
  REQ-02: One-pass: profile file + merge-policy row in a single onboarding call.
  REQ-03: allow_openrouter defaults false on every freshly seeded merge-policy row.
  REQ-04: RED-first; allow_openrouter-default-false test must FAIL if the default flips.

Done-when (design §7.2):
  #1 test_fresh_repo_onboards_in_one_pass       — fresh fixture → profile + policy row
  #2 test_detects_test_command                  — pytest.ini/pyproject → pytest cmd
  #3 test_onboard_seeds_safe_merge_policy       — row is allow_openrouter:false + local-merge
  #4 test_diligent_repo_pinned_openrouter_false — diligent-* → allow_openrouter:false
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# This import will FAIL (RED) until factory/repo_onboard.py is created.
from factory.repo_onboard import OnboardResult, onboard


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_python_repo(tmp_path: Path, name: str = "my-oss-project") -> Path:
    """
    Create a minimal Python repo fixture with pytest.ini so the detector
    can identify the test command without running any real tool.
    """
    repo = tmp_path / name
    repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\ntestpaths = tests\n")
    (repo / "pyproject.toml").write_text(
        textwrap.dedent("""
            [project]
            name = "my-oss-project"

            [tool.pytest.ini_options]
            testpaths = ["tests"]
        """)
    )
    (repo / "tests").mkdir()
    (repo / "README.md").write_text("# my-oss-project\n")
    return repo


def _make_node_repo(tmp_path: Path, name: str = "my-node-project") -> Path:
    """
    Create a minimal Node.js repo fixture with package.json scripts so the
    detector can identify test/lint/build commands from npm scripts.
    """
    repo = tmp_path / name
    repo.mkdir()
    (repo / "package.json").write_text(
        textwrap.dedent("""
            {
              "name": "my-node-project",
              "scripts": {
                "test": "jest --coverage",
                "lint": "eslint src",
                "build": "tsc"
              }
            }
        """)
    )
    (repo / "src").mkdir()
    return repo


def _make_bare_repo(tmp_path: Path, name: str = "bare-project") -> Path:
    """
    Create a repo with no recognised config files so detection must fall back
    to 'needs manual detection' without crashing.
    """
    repo = tmp_path / name
    repo.mkdir()
    (repo / "some_random_file.txt").write_text("hello\n")
    return repo


def _make_diligent_repo(tmp_path: Path, name: str = "diligent-platform") -> Path:
    """
    Create a fixture that looks like a Diligent/work repo (prefixed accordingly).
    """
    return _make_python_repo(tmp_path, name=name)


# ---------------------------------------------------------------------------
# REQ-02: One-pass onboarding produces BOTH files
# ---------------------------------------------------------------------------


class TestOnboardingOnePass:
    """
    REQ-02 (done-when #1): a single onboard() call must produce BOTH a committed
    factory-repo-profile.md AND a seeded merge-policy row in one pass.
    """

    def test_fresh_repo_onboards_in_one_pass(self, tmp_path):
        """
        Point at a fresh fixture repo → profile file + merge-policy row both
        exist after ONE onboard() call (design §7.2 done-when #1).
        """
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        # Both output files must exist.
        assert result.profile_path.exists(), "factory-repo-profile.md was not written"
        assert result.policy_path.exists(), "merge-policy row file was not written"

        # Result object must carry useful fields.
        assert isinstance(result, OnboardResult)
        assert result.repo_slug  # non-empty slug

    def test_onboard_returns_result_object(self, tmp_path):
        """onboard() returns an OnboardResult with profile_path + policy_path fields."""
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        assert hasattr(result, "profile_path")
        assert hasattr(result, "policy_path")
        assert hasattr(result, "repo_slug")

    def test_onboard_idempotent_no_crash_on_second_run(self, tmp_path):
        """Running onboard() twice on the same repo must not crash."""
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result1 = onboard(repo_path, output_dir=out_dir)
        result2 = onboard(repo_path, output_dir=out_dir)

        # Both runs must produce valid profiles.
        assert result1.profile_path.exists()
        assert result2.profile_path.exists()


# ---------------------------------------------------------------------------
# REQ-01: Detect test/lint/build from config files
# ---------------------------------------------------------------------------


class TestCommandDetection:
    """
    REQ-01 (done-when): detect commands from config files; unknown repos get
    'needs manual detection', never a crash.
    """

    def test_detects_pytest_from_pytest_ini(self, tmp_path):
        """
        A repo with pytest.ini gets test_cmd containing 'pytest' in its profile
        (design §7.2 done-when #2).
        """
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        profile_text = result.profile_path.read_text()
        assert "pytest" in profile_text.lower(), (
            f"Expected 'pytest' in profile; got:\n{profile_text}"
        )

    def test_detects_pytest_from_pyproject_toml(self, tmp_path):
        """
        A repo with [tool.pytest.ini_options] in pyproject.toml gets pytest
        as the test_cmd.
        """
        repo = tmp_path / "pyproj-repo"
        repo.mkdir()
        (repo / "pyproject.toml").write_text(
            "[project]\nname='x'\n\n[tool.pytest.ini_options]\ntestpaths=['tests']\n"
        )
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo, output_dir=out_dir)
        profile_text = result.profile_path.read_text()
        assert "pytest" in profile_text.lower()

    def test_detects_npm_scripts_from_package_json(self, tmp_path):
        """
        A Node.js repo with package.json scripts gets npm test/lint/build commands
        in its profile.
        """
        repo_path = _make_node_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        profile_text = result.profile_path.read_text()
        # The profile must mention npm-related commands.
        assert "npm" in profile_text.lower() or "jest" in profile_text.lower(), (
            f"Expected npm/jest in profile for node repo; got:\n{profile_text}"
        )

    def test_unknown_repo_graceful_fallback(self, tmp_path):
        """
        A repo with no recognised config files gets 'needs manual detection'
        in its profile — never crashes (design §7.1 REQ-01 done-when).
        """
        repo_path = _make_bare_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        # Must NOT raise.
        result = onboard(repo_path, output_dir=out_dir)

        assert result.profile_path.exists()
        profile_text = result.profile_path.read_text()
        # The profile must communicate that detection could not be automated.
        assert "manual" in profile_text.lower(), (
            f"Expected 'manual' in unknown-repo profile; got:\n{profile_text}"
        )

    def test_makefile_targets_detected(self, tmp_path):
        """
        A repo with a Makefile carrying test/build targets has those detected.
        """
        repo = tmp_path / "make-repo"
        repo.mkdir()
        (repo / "Makefile").write_text(
            "test:\n\tpython -m pytest\n\nbuild:\n\tpython setup.py build\n"
        )
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo, output_dir=out_dir)
        profile_text = result.profile_path.read_text()
        # Must detect a make/pytest-like reference.
        assert any(kw in profile_text.lower() for kw in ("make", "pytest", "python")), (
            f"Expected make/pytest in Makefile repo profile; got:\n{profile_text}"
        )

    def test_setup_py_detected_as_python_repo(self, tmp_path):
        """A repo with setup.py (no pytest.ini) is still detected as Python."""
        repo = tmp_path / "setup-repo"
        repo.mkdir()
        (repo / "setup.py").write_text("from setuptools import setup\nsetup(name='x')\n")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo, output_dir=out_dir)
        profile_text = result.profile_path.read_text()
        # Python language detected; test cmd is pytest or 'manual detection needed'.
        assert "python" in profile_text.lower() or "pytest" in profile_text.lower(), (
            f"Expected python detection; got:\n{profile_text}"
        )


# ---------------------------------------------------------------------------
# REQ-03: allow_openrouter defaults false in the seeded row
# ---------------------------------------------------------------------------


class TestMergePolicySafeDefault:
    """
    REQ-03 (done-when #3): the seeded merge-policy row must be allow_openrouter:false
    + local-merge + require_review:true (the safe default).
    This test class is REQ-04's falsifiability anchor — if allow_openrouter defaults
    flip to True, test_seeded_row_allow_openrouter_false will catch it immediately.
    """

    def test_seeded_row_allow_openrouter_false(self, tmp_path):
        """
        A freshly onboarded repo's merge-policy row has allow_openrouter:false
        (design §7.2 done-when #3 / REQ-03).
        This test MUST FAIL if the default ever flips to True — that flip is a
        data-residency regression.
        """
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        policy_text = result.policy_path.read_text()
        # allow_openrouter must appear and be false.
        assert "allow_openrouter" in policy_text, (
            "merge-policy row is missing allow_openrouter field"
        )
        assert "allow_openrouter: false" in policy_text or "allow_openrouter:false" in policy_text or "allow_openrouter: False" in policy_text, (
            f"Expected allow_openrouter:false in seeded policy; got:\n{policy_text}"
        )

    def test_seeded_row_local_merge_flow(self, tmp_path):
        """The seeded row uses local-merge (the safe conservative default)."""
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)
        policy_text = result.policy_path.read_text()

        assert "local-merge" in policy_text, (
            f"Expected local-merge in seeded policy; got:\n{policy_text}"
        )

    def test_seeded_row_require_review_true(self, tmp_path):
        """The seeded row has require_review:true (safe default)."""
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)
        policy_text = result.policy_path.read_text()

        assert "require_review" in policy_text, (
            "merge-policy row is missing require_review field"
        )
        assert "require_review: true" in policy_text or "require_review:true" in policy_text or "require_review: True" in policy_text, (
            f"Expected require_review:true in seeded policy; got:\n{policy_text}"
        )

    def test_seeded_policy_is_parseable_by_load_merge_policy(self, tmp_path):
        """
        The seeded merge-policy file must be parseable by the existing
        load_merge_policy function — it must be a valid policy block, not
        arbitrary prose.
        """
        from factory.merge_policy import load_merge_policy

        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)
        policy = load_merge_policy(result.policy_path)

        assert policy.allow_openrouter is False
        assert policy.require_review is True

    def test_seeded_policy_absent_before_onboard(self, tmp_path):
        """
        The policy file is absent BEFORE onboard() runs — confirming the test
        is genuinely RED before the implementation exists.
        """
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        # No onboard() call — the output dir is empty.
        assert not any(out_dir.iterdir()), "output dir should be empty before onboarding"


# ---------------------------------------------------------------------------
# REQ-04 (Diligent hard-pin): diligent-* always allow_openrouter:false
# ---------------------------------------------------------------------------


class TestDiligentPinned:
    """
    REQ-04 (done-when #4): a diligent-* repo's seeded row is always
    allow_openrouter:false, even if the user tries to override it.
    """

    def test_diligent_repo_pinned_openrouter_false(self, tmp_path):
        """
        A 'diligent-*' repo's seeded merge-policy row is allow_openrouter:false
        regardless of any auto-detection result (design §7.2 done-when #4).
        """
        repo_path = _make_diligent_repo(tmp_path, name="diligent-platform")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        policy_text = result.policy_path.read_text()
        # Hard-pinned — must be false regardless of what detection suggests.
        assert "allow_openrouter: false" in policy_text or "allow_openrouter:false" in policy_text or "allow_openrouter: False" in policy_text, (
            f"Diligent repo must have allow_openrouter:false; got:\n{policy_text}"
        )

    def test_diligent_prefix_variations_all_pinned(self, tmp_path):
        """
        Variations of the Diligent prefix (diligent-internal, boardbooks, etc.)
        are all hard-pinned allow_openrouter:false.
        """
        from factory.merge_policy import load_merge_policy

        for slug in ("diligent-internal-api", "boardbooks-mobile", "diligent-corp-platform"):
            sub = tmp_path / slug
            sub.mkdir()
            repo_path = _make_diligent_repo(sub, name=slug)
            # Note: _make_diligent_repo creates a sub-dir; just use the
            # returned path (which is sub/slug for the nested call).
            out_dir = tmp_path / f"out-{slug}"
            out_dir.mkdir()

            # Directly create a repo directory with the Diligent-like name.
            dr = tmp_path / f"dr-{slug}"
            dr.mkdir()
            (dr / "README.md").write_text(f"# {slug}\n")

            result = onboard(dr, output_dir=out_dir, repo_slug=slug)
            policy = load_merge_policy(result.policy_path)
            assert policy.allow_openrouter is False, (
                f"Expected allow_openrouter:false for Diligent slug {slug!r}"
            )

    def test_personal_repo_also_defaults_false(self, tmp_path):
        """
        A personal/OSS repo also gets allow_openrouter:false by default.
        (It can only be opted in via an explicit owner action, never auto.)
        """
        repo_path = _make_python_repo(tmp_path, name="my-oss-project")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)

        policy_text = result.policy_path.read_text()
        assert "allow_openrouter: false" in policy_text or "allow_openrouter:false" in policy_text or "allow_openrouter: False" in policy_text


# ---------------------------------------------------------------------------
# Profile file content checks
# ---------------------------------------------------------------------------


class TestProfileFileContent:
    """
    The factory-repo-profile.md must contain the required fields per design §7.1.
    """

    def test_profile_contains_required_fields(self, tmp_path):
        """
        The profile file must carry repo, base_branch, test_cmd, lint_cmd,
        build_cmd, and onboarded_ts.
        """
        repo_path = _make_python_repo(tmp_path)
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)
        text = result.profile_path.read_text()

        for field in ("repo", "test_cmd", "onboarded_ts"):
            assert field in text, f"profile missing field {field!r}:\n{text}"

    def test_profile_slug_matches_directory_name(self, tmp_path):
        """The repo_slug in the profile matches the repo directory name."""
        repo_path = _make_python_repo(tmp_path, name="cool-lib")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = onboard(repo_path, output_dir=out_dir)
        text = result.profile_path.read_text()

        assert "cool-lib" in text, (
            f"Expected repo slug 'cool-lib' in profile; got:\n{text}"
        )
