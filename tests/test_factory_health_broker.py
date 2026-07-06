# ABOUTME: RED-first tests for the broker health section in factory_health.py
# ABOUTME: (P1a-h REQ-04). Verifies check_broker() reports socket reachability +
# ABOUTME: pending-action count via broker_client.health, appears in the rendered
# ABOUTME: report, and drives the exit code (broker down + gateway up ⇒ HERMES).
# ABOUTME: Real broker_client fail-closed path; no live socket is created.
from __future__ import annotations

import importlib
import sys
from pathlib import Path


def _import_factory_health():
    scripts_dir = Path(__file__).parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    if "factory_health" in sys.modules:
        del sys.modules["factory_health"]
    return importlib.import_module("factory_health")


def test_check_broker_unreachable_reports_down(tmp_path):
    """No socket at the path ⇒ socket_reachable False, not a crash."""
    fh = _import_factory_health()
    result = fh.check_broker(socket_path=tmp_path / "broker.sock")
    assert result["socket_reachable"] is False
    assert "message" in result


def test_broker_section_in_report(tmp_path):
    """The rendered report includes a Broker section."""
    fh = _import_factory_health()
    sections = {
        "broker": fh.check_broker(socket_path=tmp_path / "broker.sock"),
    }
    report = fh.render_health_report(sections)
    assert "Broker" in report or "BROKER" in report


def test_broker_down_with_gateway_up_is_hermes_error(tmp_path):
    """Broker unreachable while gateway up ⇒ HERMES cause (exit 2)."""
    fh = _import_factory_health()
    sections = {
        "providers": [{"name": "anthropic", "status": "reachable", "cause": None}],
        "broker": {
            "socket_reachable": False,
            "cause": "HERMES",
            "message": "broker crashed",
            "pending_count": None,
        },
    }
    assert fh.compute_exit_code(sections) == 2
