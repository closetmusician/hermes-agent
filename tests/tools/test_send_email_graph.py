"""Tests for Graph API email path in _send_email().

Verifies that _send_email() prefers Microsoft Graph API (via
outlook-send-mail.js) when a FOCI token exists, and falls back to
SMTP when it does not. Mocks only system boundaries (subprocess.run,
os.path.exists) -- never internal modules.
"""

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from tools.send_message_tool import _send_email


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Graph API path -- token + tool exist
# ---------------------------------------------------------------------------


class TestGraphApiPath:
    """When FOCI token and outlook-send-mail.js both exist, _send_email
    should shell out to the Node tool instead of using SMTP."""

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_uses_graph_api_when_token_exists(self, mock_exists, mock_run):
        """Graph API path is taken when both files exist."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")

        result = _run(_send_email({}, "user@example.com", "Hello"))

        assert result["success"] is True
        assert result["method"] == "graph-api"
        assert result["platform"] == "email"
        # SMTP should NOT have been attempted
        mock_run.assert_called_once()

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_command_construction(self, mock_exists, mock_run):
        """Subprocess command includes correct --to, --subject, --body,
        --content-type args. Body is HTML-converted markdown."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        _run(_send_email({}, "alice@corp.com", "Test body"))

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "node"
        assert "--to" in cmd
        assert cmd[cmd.index("--to") + 1] == "alice@corp.com"
        assert "--subject" in cmd
        assert cmd[cmd.index("--subject") + 1] == "Hermes Agent"
        assert "--body" in cmd
        body = cmd[cmd.index("--body") + 1]
        assert "<html>" in body
        assert "Test body" in body
        assert "--content-type" in cmd
        assert cmd[cmd.index("--content-type") + 1] == "HTML"

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_graph_api_failure_returns_error(self, mock_exists, mock_run):
        """When outlook-send-mail.js exits non-zero, return an error dict
        (do NOT fall back to SMTP)."""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Token expired"
        )

        result = _run(_send_email({}, "user@example.com", "Hello"))

        assert "error" in result
        assert "Token expired" in result["error"]
        assert "success" not in result

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_graph_api_timeout_returns_error(self, mock_exists, mock_run):
        """Timeout from subprocess should return an error, not crash."""
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="node", timeout=30)

        result = _run(_send_email({}, "user@example.com", "Hello"))

        assert "error" in result
        assert "timed out" in result["error"].lower()


# ---------------------------------------------------------------------------
# SMTP fallback -- no FOCI token
# ---------------------------------------------------------------------------


class TestSmtpFallback:
    """When FOCI token does NOT exist, _send_email should use the
    existing SMTP path."""

    @patch("os.path.exists", return_value=False)
    def test_smtp_fallback_when_no_token(self, mock_exists):
        """Without FOCI token, falls through to SMTP (which needs config)."""
        # No EMAIL_ADDRESS/PASSWORD/SMTP_HOST set, so SMTP will return
        # a config-missing error -- that's fine, it proves we reached SMTP.
        result = _run(_send_email({}, "user@example.com", "Hello"))

        assert "error" in result
        assert "not configured" in result["error"].lower()

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_smtp_fallback_when_only_token_exists(self, mock_exists, mock_run):
        """If token exists but outlook-send-mail.js does NOT, fall through
        to SMTP."""
        import os

        token_path = os.path.expanduser("~/.pm-os-foci-token.json")
        tool_path = os.path.expanduser("~/Code/pm_os/bin/outlook-send-mail.js")

        def exists_side_effect(path):
            if path == token_path:
                return True
            if path == tool_path:
                return False
            return False

        mock_exists.side_effect = exists_side_effect

        result = _run(_send_email({}, "user@example.com", "Hello"))

        # Should reach SMTP path (config-missing error proves it)
        assert "error" in result
        assert "not configured" in result["error"].lower()
        mock_run.assert_not_called()
