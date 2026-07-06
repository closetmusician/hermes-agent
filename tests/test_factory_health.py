# ABOUTME: Unit tests for scripts/factory_health.py — the jobs-first factory health command.
# ABOUTME: Tests cover all 4 health sections and both failure attribution modes (NETWORK vs HERMES).
# ABOUTME: All external HTTP and process calls are mocked; filesystem touches use tmp_path.
# ABOUTME: TDD: these tests were written RED before the implementation existed.
# ABOUTME: Exit codes: 0=all-healthy, 1=degraded (network/provider down), 2=internal error.

import importlib
import os
import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helper: import factory_health from scripts/ without installing it
# ---------------------------------------------------------------------------

def _import_factory_health():
    """Import factory_health module from scripts/.

    The module lives in scripts/ which is not on sys.path by default.
    We add it temporarily and import fresh each call so monkeypatching works.
    """
    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    # Force re-import so patches apply cleanly
    if "factory_health" in sys.modules:
        del sys.modules["factory_health"]
    return importlib.import_module("factory_health")


# ---------------------------------------------------------------------------
# REQ-01: Section 1 — provider reachability
# ---------------------------------------------------------------------------

class TestProviderReachability:
    """Section 1: Anthropic + OpenRouter reachability with latency (HEAD/models endpoint)."""

    def test_anthropic_reachable_returns_ok_with_latency(self, monkeypatch):
        """Mocked HEAD to Anthropic returns 200 — section should show REACHABLE with latency."""
        fh = _import_factory_health()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.return_value = mock_response
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("anthropic", "https://api.anthropic.com/v1/models")

        assert result["status"] == "reachable"
        assert result["latency_ms"] >= 0
        assert result["cause"] is None

    def test_openrouter_reachable_returns_ok(self, monkeypatch):
        """Mocked HEAD to OpenRouter returns 200 — section should show REACHABLE."""
        fh = _import_factory_health()

        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.return_value = mock_response
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("openrouter", "https://openrouter.ai/api/v1/models")

        assert result["status"] == "reachable"
        assert result["latency_ms"] >= 0
        assert result["cause"] is None

    def test_provider_network_failure_attributed_to_network(self):
        """Socket-level connect failure → cause='NETWORK', not 'HERMES'."""
        fh = _import_factory_health()

        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.side_effect = httpx_connect_error()
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("anthropic", "https://api.anthropic.com/v1/models")

        assert result["status"] == "unreachable"
        assert result["cause"] == "NETWORK"
        assert result["error"] is not None

    def test_provider_internal_bug_attributed_to_hermes(self):
        """Unexpected internal exception → cause='HERMES' with the exception text."""
        fh = _import_factory_health()

        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.side_effect = ValueError("unexpected bug in factory_health")
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("anthropic", "https://api.anthropic.com/v1/models")

        assert result["status"] == "unreachable"
        assert result["cause"] == "HERMES"
        assert "unexpected bug in factory_health" in result["error"]


def httpx_connect_error():
    """Return a socket-style connect error that httpx raises for unreachable hosts."""
    import httpx
    return httpx.ConnectError("connection refused")


# ---------------------------------------------------------------------------
# REQ-01: Section 2 — compute state
# ---------------------------------------------------------------------------

class TestComputeState:
    """Section 2: caffeinate assertion present, disk free, load average."""

    def test_caffeinate_present_when_process_running(self, monkeypatch):
        """psutil finds a caffeinate process → caffeinate=True."""
        fh = _import_factory_health()

        mock_proc = MagicMock()
        mock_proc.name.return_value = "caffeinate"

        with patch("psutil.process_iter", return_value=[mock_proc]):
            state = fh.check_compute_state()

        assert state["caffeinate"] is True

    def test_caffeinate_absent_when_no_process(self, monkeypatch):
        """No caffeinate process → caffeinate=False."""
        fh = _import_factory_health()

        with patch("psutil.process_iter", return_value=[]):
            state = fh.check_compute_state()

        assert state["caffeinate"] is False

    def test_disk_free_returns_numeric_gb(self, monkeypatch):
        """shutil.disk_usage returns a mocked value → disk_free_gb is numeric."""
        fh = _import_factory_health()

        mock_usage = MagicMock()
        mock_usage.free = 50 * 1024 ** 3  # 50 GiB

        with patch("shutil.disk_usage", return_value=mock_usage):
            with patch("psutil.process_iter", return_value=[]):
                state = fh.check_compute_state()

        assert abs(state["disk_free_gb"] - 50.0) < 0.5

    def test_load_avg_returns_tuple(self, monkeypatch):
        """os.getloadavg returns mocked triple → load_avg is a 3-tuple."""
        fh = _import_factory_health()

        with patch("os.getloadavg", return_value=(1.2, 0.9, 0.7)):
            with patch("psutil.process_iter", return_value=[]):
                with patch("shutil.disk_usage", return_value=MagicMock(free=10 * 1024 ** 3)):
                    state = fh.check_compute_state()

        assert len(state["load_avg"]) == 3
        assert state["load_avg"][0] == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# REQ-01: Section 3 — running workers
# ---------------------------------------------------------------------------

class TestRunningWorkers:
    """Section 3: factory worker processes; none expected (factory not provisioned)."""

    def test_no_workers_returns_not_provisioned_message(self):
        """With no factory worker processes, result describes 'not provisioned'."""
        fh = _import_factory_health()

        with patch("psutil.process_iter", return_value=[]):
            result = fh.check_running_workers()

        assert result["count"] == 0
        assert "not provisioned" in result["message"].lower()

    def test_worker_processes_counted_when_present(self):
        """When matching worker processes exist they are counted."""
        fh = _import_factory_health()

        # Simulate two 'claude' worker processes
        mock_proc1 = MagicMock()
        mock_proc1.cmdline.return_value = ["claude", "-p", "--some-flag"]

        mock_proc2 = MagicMock()
        mock_proc2.cmdline.return_value = ["claude", "-p", "--other-flag"]

        with patch("psutil.process_iter", return_value=[mock_proc1, mock_proc2]):
            result = fh.check_running_workers()

        assert result["count"] == 2
        assert "worker" in result["message"].lower()


# ---------------------------------------------------------------------------
# REQ-01: Section 4 — last scheduler tick
# ---------------------------------------------------------------------------

class TestSchedulerTick:
    """Section 4: last tick timestamp from a well-known state path, 'never' handled."""

    def test_never_when_tick_file_absent(self, tmp_path):
        """Missing tick file → last_tick='never'."""
        fh = _import_factory_health()

        tick_path = tmp_path / "scheduler-tick"
        result = fh.check_scheduler_tick(tick_path=tick_path)

        assert result["last_tick"] == "never"

    def test_reads_timestamp_from_tick_file(self, tmp_path):
        """Tick file with an ISO timestamp → last_tick reflects that value."""
        fh = _import_factory_health()

        tick_path = tmp_path / "scheduler-tick"
        ts = "2026-07-06T03:00:00+00:00"
        tick_path.write_text(ts + "\n")

        result = fh.check_scheduler_tick(tick_path=tick_path)

        assert result["last_tick"] == ts.strip()

    def test_stale_tick_flagged_in_seconds_ago(self, tmp_path):
        """Tick file mtime is old → seconds_ago is positive and reasonable."""
        fh = _import_factory_health()

        tick_path = tmp_path / "scheduler-tick"
        tick_path.write_text("2026-07-06T01:00:00+00:00\n")
        # Force mtime to 1 hour ago
        old_mtime = time.time() - 3600
        os.utime(tick_path, (old_mtime, old_mtime))

        result = fh.check_scheduler_tick(tick_path=tick_path)

        assert result["seconds_ago"] >= 3590  # at least ~1 hour


# ---------------------------------------------------------------------------
# REQ-02: Failure attribution — NETWORK vs HERMES
# ---------------------------------------------------------------------------

class TestFailureAttribution:
    """Both attribution causes are distinct and testable in isolation."""

    def test_socket_failure_is_network(self):
        """httpx.ConnectError → NETWORK."""
        fh = _import_factory_health()

        import httpx
        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.side_effect = httpx.ConnectError("refused")
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("anthropic", "https://api.anthropic.com/v1/models")

        assert result["cause"] == "NETWORK"

    def test_internal_exception_is_hermes(self):
        """Arbitrary exception not from socket layer → HERMES."""
        fh = _import_factory_health()

        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.side_effect = RuntimeError("oops, factory_health bug")
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("anthropic", "https://api.anthropic.com/v1/models")

        assert result["cause"] == "HERMES"
        assert "oops, factory_health bug" in result["error"]

    def test_timeout_is_network(self):
        """httpx.TimeoutException → NETWORK (network-layer failure)."""
        fh = _import_factory_health()

        import httpx
        with patch("httpx.Client") as mock_client_cls:
            ctx = MagicMock()
            ctx.head.side_effect = httpx.TimeoutException("timed out")
            ctx.__enter__ = lambda s: ctx
            ctx.__exit__ = MagicMock(return_value=False)
            mock_client_cls.return_value = ctx

            result = fh.check_provider("anthropic", "https://api.anthropic.com/v1/models")

        assert result["cause"] == "NETWORK"


# ---------------------------------------------------------------------------
# REQ-04: Exit code logic
# ---------------------------------------------------------------------------

class TestExitCodes:
    """Exit codes: 0=all-healthy, 1=degraded, 2=internal-error."""

    def test_all_healthy_sections_return_exit_0(self):
        """When all sections are OK, compute_exit_code returns 0."""
        fh = _import_factory_health()

        sections = {
            "providers": [
                {"name": "anthropic", "status": "reachable", "cause": None},
                {"name": "openrouter", "status": "reachable", "cause": None},
            ],
            "compute": {"caffeinate": True, "disk_free_gb": 50.0, "load_avg": (0.5, 0.4, 0.3)},
            "workers": {"count": 0, "message": "no workers, factory not provisioned"},
            "scheduler": {"last_tick": "never"},
        }
        assert fh.compute_exit_code(sections) == 0

    def test_provider_down_returns_exit_1(self):
        """One provider unreachable → exit code 1 (degraded)."""
        fh = _import_factory_health()

        sections = {
            "providers": [
                {"name": "anthropic", "status": "unreachable", "cause": "NETWORK", "error": "refused"},
                {"name": "openrouter", "status": "reachable", "cause": None},
            ],
            "compute": {"caffeinate": True, "disk_free_gb": 50.0, "load_avg": (0.5, 0.4, 0.3)},
            "workers": {"count": 0, "message": "no workers, factory not provisioned"},
            "scheduler": {"last_tick": "never"},
        }
        assert fh.compute_exit_code(sections) == 1

    def test_internal_error_cause_hermes_returns_exit_2(self):
        """A HERMES-attributed failure → exit code 2 (internal error)."""
        fh = _import_factory_health()

        sections = {
            "providers": [
                {"name": "anthropic", "status": "unreachable", "cause": "HERMES", "error": "bug"},
                {"name": "openrouter", "status": "reachable", "cause": None},
            ],
            "compute": {"caffeinate": True, "disk_free_gb": 50.0, "load_avg": (0.5, 0.4, 0.3)},
            "workers": {"count": 0, "message": "no workers, factory not provisioned"},
            "scheduler": {"last_tick": "never"},
        }
        assert fh.compute_exit_code(sections) == 2
