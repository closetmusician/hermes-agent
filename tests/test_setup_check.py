#!/usr/bin/env python3
# ABOUTME: Unit tests for scripts/setup_check.py classification logic.
# ABOUTME: Tests the pure classification functions (no side effects, no network).
# ABOUTME: Follows RED-first TDD: all tests were written before implementation passed.
# ABOUTME: Covers: make_result, classify_env_key, classify_file_exists,
# ABOUTME: classify_factory_health_section, classify_caffeinate, classify_whatsapp_disabled.

"""
test_setup_check.py — unit tests for setup_check.py classification logic.

Run with:
    python -m pytest tests/test_setup_check.py -v
"""

import json
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.setup_check import (
    DONE,
    MANUAL,
    NOT_DONE,
    classify_caffeinate,
    classify_env_key,
    classify_factory_health_section,
    classify_file_exists,
    classify_jira_lane_phase,
    classify_calendar_lane_phase,
    classify_whatsapp_disabled,
    make_result,
)


# ---------------------------------------------------------------------------
# make_result
# ---------------------------------------------------------------------------


class TestMakeResult:
    """Tests for the make_result helper."""

    def test_done_result_has_correct_schema(self):
        r = make_result("telegram.token", DONE, "token present")
        assert r["lane"] == "telegram.token"
        assert r["status"] == DONE
        assert r["reason"] == "token present"
        assert r["automatable"] is True

    def test_manual_result_automatable_false(self):
        r = make_result("some.lane", MANUAL, "needs human", automatable=False)
        assert r["automatable"] is False
        assert r["status"] == MANUAL

    def test_not_done_result(self):
        r = make_result("provider.anthropic", NOT_DONE, "unreachable [NETWORK]")
        assert r["status"] == NOT_DONE

    def test_invalid_status_raises(self):
        with pytest.raises(AssertionError):
            make_result("foo", "UNKNOWN_STATUS", "should fail")


# ---------------------------------------------------------------------------
# classify_env_key
# ---------------------------------------------------------------------------


class TestClassifyEnvKey:
    """Tests for classify_env_key — pure function with a temp file."""

    def _write_env(self, content: str) -> Path:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False)
        tmp.write(content)
        tmp.flush()
        return Path(tmp.name)

    def test_key_present_and_non_empty_is_done(self):
        p = self._write_env("TELEGRAM_BOT_TOKEN=abc123\n")
        r = classify_env_key(p, "TELEGRAM_BOT_TOKEN")
        assert r["status"] == DONE

    def test_key_missing_is_not_done(self):
        p = self._write_env("OTHER_KEY=value\n")
        r = classify_env_key(p, "TELEGRAM_BOT_TOKEN")
        assert r["status"] == NOT_DONE

    def test_key_empty_value_is_not_done(self):
        p = self._write_env("TELEGRAM_BOT_TOKEN=\n")
        r = classify_env_key(p, "TELEGRAM_BOT_TOKEN")
        assert r["status"] == NOT_DONE

    def test_key_whitespace_value_is_not_done(self):
        p = self._write_env("TELEGRAM_BOT_TOKEN=   \n")
        r = classify_env_key(p, "TELEGRAM_BOT_TOKEN")
        assert r["status"] == NOT_DONE

    def test_file_missing_is_not_done(self):
        r = classify_env_key(Path("/nonexistent/path/.env"), "TELEGRAM_BOT_TOKEN")
        assert r["status"] == NOT_DONE

    def test_comment_lines_ignored(self):
        p = self._write_env("# TELEGRAM_BOT_TOKEN=fake\nOTHER=val\n")
        r = classify_env_key(p, "TELEGRAM_BOT_TOKEN")
        assert r["status"] == NOT_DONE

    def test_multiple_keys_correct_one_matched(self):
        p = self._write_env("OPENROUTER_API_KEY=key1\nTELEGRAM_BOT_TOKEN=bot789\n")
        r = classify_env_key(p, "TELEGRAM_BOT_TOKEN")
        assert r["status"] == DONE


# ---------------------------------------------------------------------------
# classify_file_exists
# ---------------------------------------------------------------------------


class TestClassifyFileExists:
    """Tests for classify_file_exists."""

    def test_existing_non_empty_file_is_done(self, tmp_path):
        f = tmp_path / "config.yaml"
        f.write_text("key: value\n")
        r = classify_file_exists(f, "hermes.config")
        assert r["status"] == DONE

    def test_missing_file_is_not_done(self):
        r = classify_file_exists(Path("/no/such/file.yaml"), "hermes.config")
        assert r["status"] == NOT_DONE

    def test_empty_file_is_not_done(self, tmp_path):
        f = tmp_path / "empty.yaml"
        f.write_text("")
        r = classify_file_exists(f, "hermes.config")
        assert r["status"] == NOT_DONE
        assert "empty" in r["reason"].lower() or "stub" in r["reason"].lower()


# ---------------------------------------------------------------------------
# classify_factory_health_section
# ---------------------------------------------------------------------------


class TestClassifyFactoryHealthSection:
    """Tests for classify_factory_health_section — stub detection included."""

    def _provider(self, name: str, status: str, latency_ms=None, cause=None, error=""):
        return {
            "name": name,
            "status": status,
            "latency_ms": latency_ms,
            "cause": cause,
            "error": error,
        }

    def test_reachable_with_real_latency_is_done(self):
        providers = [self._provider("anthropic", "reachable", latency_ms=142.5)]
        r = classify_factory_health_section(providers, "anthropic")
        assert r["status"] == DONE
        assert "142" in r["reason"]

    def test_reachable_with_null_latency_is_not_done(self):
        """A provider claiming reachable but with no latency is a stub."""
        providers = [self._provider("anthropic", "reachable", latency_ms=None)]
        r = classify_factory_health_section(providers, "anthropic")
        assert r["status"] == NOT_DONE
        assert "stub" in r["reason"].lower() or "latency" in r["reason"].lower()

    def test_reachable_with_zero_latency_is_not_done(self):
        """Zero ms latency on a live network call is physically impossible."""
        providers = [self._provider("anthropic", "reachable", latency_ms=0)]
        r = classify_factory_health_section(providers, "anthropic")
        assert r["status"] == NOT_DONE

    def test_unreachable_network_is_not_done(self):
        providers = [self._provider("anthropic", "unreachable", cause="NETWORK", error="conn refused")]
        r = classify_factory_health_section(providers, "anthropic")
        assert r["status"] == NOT_DONE
        assert "NETWORK" in r["reason"]

    def test_provider_not_in_list_is_not_done(self):
        r = classify_factory_health_section([], "anthropic")
        assert r["status"] == NOT_DONE

    def test_case_insensitive_name_match(self):
        providers = [self._provider("ANTHROPIC", "reachable", latency_ms=80.0)]
        r = classify_factory_health_section(providers, "anthropic")
        assert r["status"] == DONE

    def test_openrouter_reachable(self):
        providers = [self._provider("openrouter", "reachable", latency_ms=200.0)]
        r = classify_factory_health_section(providers, "openrouter")
        assert r["status"] == DONE


# ---------------------------------------------------------------------------
# classify_caffeinate
# ---------------------------------------------------------------------------


class TestClassifyCaffeinate:
    """Tests for classify_caffeinate."""

    def test_running_is_done(self):
        r = classify_caffeinate(True)
        assert r["status"] == DONE

    def test_not_running_is_not_done(self):
        r = classify_caffeinate(False)
        assert r["status"] == NOT_DONE
        assert "sleep" in r["reason"].lower() or "caffeinate" in r["reason"].lower()

    def test_none_state_is_not_done(self):
        r = classify_caffeinate(None)
        assert r["status"] == NOT_DONE


# ---------------------------------------------------------------------------
# classify_whatsapp_disabled
# ---------------------------------------------------------------------------


class TestClassifyWhatsappDisabled:
    """Tests for classify_whatsapp_disabled — stub detection for WhatsApp state."""

    def _write_config(self, content: str, tmp_path: Path) -> Path:
        f = tmp_path / "config.yaml"
        f.write_text(content)
        return f

    def test_whatsapp_absent_from_config_is_done(self, tmp_path):
        f = self._write_config("telegram:\n  enabled: true\n", tmp_path)
        r = classify_whatsapp_disabled(f)
        assert r["status"] == DONE

    def test_whatsapp_explicitly_disabled_is_done(self, tmp_path):
        f = self._write_config("whatsapp:\n  enabled: false\n", tmp_path)
        r = classify_whatsapp_disabled(f)
        assert r["status"] == DONE

    def test_whatsapp_enabled_true_is_not_done(self, tmp_path):
        """WhatsApp enabled:true before Phase R R-2 pairing = NOT DONE."""
        f = self._write_config("whatsapp:\n  enabled: true\n  token: abc\n", tmp_path)
        r = classify_whatsapp_disabled(f)
        assert r["status"] == NOT_DONE

    def test_missing_config_is_not_done(self):
        r = classify_whatsapp_disabled(Path("/nonexistent/config.yaml"))
        assert r["status"] == NOT_DONE

    def test_whatsapp_block_without_enabled_key_is_not_done(self, tmp_path):
        f = self._write_config("whatsapp:\n  host: ws://localhost:3000\n", tmp_path)
        r = classify_whatsapp_disabled(f)
        assert r["status"] == NOT_DONE


# ---------------------------------------------------------------------------
# classify_jira_lane_phase / classify_calendar_lane_phase
# ---------------------------------------------------------------------------


class TestPhasedLanes:
    """Tests for lanes that are NOT DONE because their phase hasn't shipped yet."""

    def test_jira_lane_is_not_done(self):
        r = classify_jira_lane_phase()
        assert r["status"] == NOT_DONE
        assert r["automatable"] is False
        assert "P3" in r["reason"] or "not yet built" in r["reason"].lower()

    def test_calendar_lane_is_not_done(self):
        r = classify_calendar_lane_phase()
        assert r["status"] == NOT_DONE
        assert r["automatable"] is False
        assert "P1c" in r["reason"] or "not yet built" in r["reason"].lower()

    def test_jira_lane_reason_mentions_phase(self):
        r = classify_jira_lane_phase()
        assert "Phase" in r["reason"] or "phase" in r["reason"]

    def test_calendar_lane_reason_mentions_phase(self):
        r = classify_calendar_lane_phase()
        assert "Phase" in r["reason"] or "phase" in r["reason"]
