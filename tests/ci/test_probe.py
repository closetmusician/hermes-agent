# ABOUTME: Unit tests for scripts/probe.py — capability probe for the hermes factory.
# ABOUTME: Covers catalog parsing, capability schema validation, and exit-code policy.
# ABOUTME: ALL external HTTP is mocked; no real network calls here.
# ABOUTME: Written RED-first (before implementation) per TDD discipline.
# ABOUTME: See docs/plans/harness/fable/p0/P0-3-4-report.md for the RED run output.

"""Unit tests for scripts/probe.py capability probe.

These tests cover the pure-logic layers only:
  - catalog response parsing (extract model IDs from /v1/models JSON)
  - capability schema validation (required keys, types, constraints)
  - exit-code policy (missing capability → non-zero; all present → zero)
  - phantom model detection (GLM-5.2 must produce a failure entry, not silent omission)

External HTTP (OpenRouter /v1/models, ping calls) is mocked at the
requests/urllib level — never at the internal logic level.
"""

import importlib
import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import probe module from scripts/ (not on sys.path by default)
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).parent.parent.parent / "scripts"


def _import_probe():
    """Import probe.py from scripts/ regardless of sys.path."""
    spec = importlib.util.spec_from_file_location(
        "probe", SCRIPTS_DIR / "probe.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Catalog parsing tests
# ---------------------------------------------------------------------------


class TestParseCatalog:
    """parse_catalog() extracts model IDs from the OpenRouter /v1/models response."""

    def test_extracts_ids_from_data_list(self):
        """Standard OpenRouter response: {data: [{id: ...}, ...]}."""
        probe = _import_probe()
        payload = {
            "data": [
                {"id": "anthropic/claude-3-haiku"},
                {"id": "zhipuai/glm-5"},
                {"id": "moonshot/kimi-k2"},
            ]
        }
        result = probe.parse_catalog(payload)
        assert result == {
            "anthropic/claude-3-haiku",
            "zhipuai/glm-5",
            "moonshot/kimi-k2",
        }

    def test_empty_data_returns_empty_set(self):
        """Empty catalog produces an empty set, not an error."""
        probe = _import_probe()
        result = probe.parse_catalog({"data": []})
        assert result == set()

    def test_missing_data_key_raises_value_error(self):
        """A response without a 'data' key is malformed and must raise."""
        probe = _import_probe()
        with pytest.raises((ValueError, KeyError)):
            probe.parse_catalog({"models": []})

    def test_model_without_id_skipped(self):
        """Models lacking an 'id' field are silently skipped."""
        probe = _import_probe()
        payload = {"data": [{"id": "a/real-model"}, {"name": "no-id-here"}]}
        result = probe.parse_catalog(payload)
        assert result == {"a/real-model"}


# ---------------------------------------------------------------------------
# Capability schema validation tests
# ---------------------------------------------------------------------------


class TestCapabilitySchema:
    """validate_capabilities() enforces the capabilities.json schema."""

    def _valid_caps(self):
        """Return a minimal valid capabilities dict."""
        return {
            "probed_at": "2026-07-06T00:00:00Z",
            "cli": {
                "claude": {
                    "version": "2.1.170",
                    "present": True,
                    "max_budget_usd_flag": True,
                    "json_schema_output": True,
                },
                "codex": {
                    "version": "0.139.0",
                    "present": True,
                    "budget_flag": None,
                    "budget_flag_name": None,
                    "json_output": True,
                },
            },
            "git_worktree": {"supported": True},
            "openrouter": {
                "catalog_fetched": True,
                "key_present": True,
                "models": {},
            },
        }

    def test_valid_caps_passes(self):
        """A well-formed capabilities dict passes validation without error."""
        probe = _import_probe()
        caps = self._valid_caps()
        # must not raise
        probe.validate_capabilities(caps)

    def test_missing_cli_key_fails(self):
        """Capabilities without a 'cli' key must fail validation."""
        probe = _import_probe()
        caps = self._valid_caps()
        del caps["cli"]
        with pytest.raises((ValueError, KeyError, AssertionError)):
            probe.validate_capabilities(caps)

    def test_missing_git_worktree_fails(self):
        """Capabilities without 'git_worktree' must fail validation."""
        probe = _import_probe()
        caps = self._valid_caps()
        del caps["git_worktree"]
        with pytest.raises((ValueError, KeyError, AssertionError)):
            probe.validate_capabilities(caps)

    def test_missing_openrouter_key_fails(self):
        """Capabilities without 'openrouter' must fail validation."""
        probe = _import_probe()
        caps = self._valid_caps()
        del caps["openrouter"]
        with pytest.raises((ValueError, KeyError, AssertionError)):
            probe.validate_capabilities(caps)

    def test_per_model_entry_has_required_keys(self):
        """Each model entry must have live_catalog, ping_ok, latency_ms."""
        probe = _import_probe()
        caps = self._valid_caps()
        caps["openrouter"]["models"]["some/model"] = {
            "live_catalog": True,
            "ping_ok": True,
            "latency_ms": 123,
        }
        probe.validate_capabilities(caps)  # must not raise

    def test_per_model_entry_missing_live_catalog_fails(self):
        """A model entry without live_catalog must fail."""
        probe = _import_probe()
        caps = self._valid_caps()
        caps["openrouter"]["models"]["some/model"] = {
            "ping_ok": True,
            "latency_ms": 100,
            # live_catalog missing
        }
        with pytest.raises((ValueError, KeyError, AssertionError)):
            probe.validate_capabilities(caps)


# ---------------------------------------------------------------------------
# Phantom model detection tests
# ---------------------------------------------------------------------------


class TestPhantomModelDetection:
    """GLM-5.2 and other non-catalog models must produce loud failure entries."""

    def test_phantom_model_gets_live_catalog_false(self):
        """A model absent from the live catalog gets live_catalog: False."""
        probe = _import_probe()
        catalog = {"zhipuai/glm-5", "moonshot/kimi-k2"}
        # GLM-5.2 is not in the catalog
        entry = probe.build_model_entry(
            model_id="zhipuai/GLM-5.2",
            catalog=catalog,
            ping_result=None,  # never pinged — not in catalog
        )
        assert entry["live_catalog"] is False

    def test_phantom_model_ping_ok_is_false(self):
        """A phantom model's ping_ok must be False, not None."""
        probe = _import_probe()
        catalog = {"zhipuai/glm-5"}
        entry = probe.build_model_entry(
            model_id="zhipuai/GLM-5.2",
            catalog=catalog,
            ping_result=None,
        )
        assert entry["ping_ok"] is False

    def test_real_model_in_catalog(self):
        """A model in the catalog gets live_catalog: True."""
        probe = _import_probe()
        catalog = {"zhipuai/glm-5"}
        entry = probe.build_model_entry(
            model_id="zhipuai/glm-5",
            catalog=catalog,
            ping_result={"ok": True, "latency_ms": 200},
        )
        assert entry["live_catalog"] is True
        assert entry["ping_ok"] is True


# ---------------------------------------------------------------------------
# Exit-code policy tests
# ---------------------------------------------------------------------------


class TestExitCodePolicy:
    """compute_exit_code() returns 0 on success and non-zero on named failure."""

    def test_all_present_exits_zero(self):
        """All capabilities present → exit code 0."""
        probe = _import_probe()
        failures = []
        assert probe.compute_exit_code(failures) == 0

    def test_any_failure_exits_nonzero(self):
        """One or more named failures → non-zero exit code."""
        probe = _import_probe()
        failures = ["claude_cli_missing"]
        assert probe.compute_exit_code(failures) != 0

    def test_absent_cli_produces_named_failure(self):
        """When 'claude' is not on PATH, a named failure is produced."""
        probe = _import_probe()
        # simulate PATH without claude
        with patch.dict("os.environ", {"PATH": "/usr/bin:/bin"}, clear=False):
            with patch("shutil.which", return_value=None):
                failures = probe.check_cli_capabilities()["failures"]
        assert any("claude" in f.lower() for f in failures)

    def test_present_cli_no_failure(self):
        """When 'claude' is on PATH, no failure entry for it."""
        probe = _import_probe()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "2.1.170 (Claude Code)"
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            with patch("subprocess.run", return_value=mock_result):
                result = probe.check_cli_capabilities()
        assert not any("claude_missing" in f for f in result.get("failures", []))


# ---------------------------------------------------------------------------
# Catalog fetch mocking tests
# ---------------------------------------------------------------------------


class TestCatalogFetchMocked:
    """fetch_openrouter_catalog() is mockable; real HTTP never called in unit tests."""

    def test_fetch_uses_api_key_header(self):
        """The catalog fetch sends the Authorization header with the API key."""
        probe = _import_probe()
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "data": [{"id": "x/y"}]
        }
        with patch("requests.get", return_value=fake_response) as mock_get:
            probe.fetch_openrouter_catalog(api_key="sk-or-test-key")
        call_kwargs = mock_get.call_args[1]
        headers = call_kwargs.get("headers", {})
        assert "Authorization" in headers
        assert "sk-or-test-key" in headers["Authorization"]

    def test_missing_api_key_returns_none(self):
        """If api_key is empty/None, fetch_openrouter_catalog returns None."""
        probe = _import_probe()
        result = probe.fetch_openrouter_catalog(api_key="")
        assert result is None

    def test_http_error_returns_none(self):
        """Non-200 status returns None, not an exception."""
        probe = _import_probe()
        fake_response = MagicMock()
        fake_response.status_code = 401
        fake_response.raise_for_status.side_effect = Exception("401")
        with patch("requests.get", return_value=fake_response):
            result = probe.fetch_openrouter_catalog(api_key="some-key")
        assert result is None


# ---------------------------------------------------------------------------
# Cost estimate test
# ---------------------------------------------------------------------------


class TestCostEstimate:
    """estimate_ping_cost() produces a reasonable per-ping cost bound."""

    def test_cost_per_ping_under_threshold(self):
        """A 1-token ping at the cheapest provider pricing stays under $0.001."""
        probe = _import_probe()
        # cheapest OpenRouter models are ~$0.10/M tokens input
        cost = probe.estimate_ping_cost(num_models=1, tokens_per_ping=1)
        assert cost < 0.001  # $0.001 per ping is very conservative

    def test_total_cost_under_budget(self):
        """Total estimated cost for all candidate models stays under $0.05."""
        probe = _import_probe()
        # worst case: 20 models at the highest-priced free tier
        cost = probe.estimate_ping_cost(num_models=20, tokens_per_ping=1)
        assert cost < 0.05
