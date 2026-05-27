"""Tests for Graph API email path in _send_email().

Verifies that _send_email() sends via Microsoft Graph API (via
outlook-send-mail.js) when a FOCI token exists, and returns an error
when Graph API is not configured. Mocks only system boundaries
(subprocess.run, os.path.exists) -- never internal modules.
"""

import asyncio
import subprocess
from unittest.mock import MagicMock, patch

from tools.send_message_tool import _send_email


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Graph API path -- token + tool exist
# ---------------------------------------------------------------------------


class TestGraphApiPath:
    """When FOCI token and outlook-send-mail.js both exist, _send_email
    should shell out to the Node tool."""

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
        mock_run.assert_called_once()

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_command_construction(self, mock_exists, mock_run):
        """Subprocess command includes correct --to, --subject, --body args.
        Body is raw markdown (JS tool handles formatting)."""
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
        # Body should be raw markdown, NOT HTML-wrapped
        assert "Test body" in body
        # No --content-type arg (JS tool auto-detects)
        assert "--content-type" not in cmd

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_graph_api_failure_returns_error(self, mock_exists, mock_run):
        """When outlook-send-mail.js exits non-zero, return an error dict."""
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
# No Graph API configured -- returns error (no SMTP fallback)
# ---------------------------------------------------------------------------


class TestNoGraphConfig:
    """When Graph API is not available, _send_email should return an error.
    There is no SMTP fallback."""

    @patch("os.path.exists", return_value=False)
    def test_error_when_no_graph_config(self, mock_exists):
        """Without FOCI token, returns a config-missing error."""
        result = _run(_send_email({}, "user@example.com", "Hello"))

        assert "error" in result
        assert "not configured" in result["error"].lower()

    @patch("subprocess.run")
    @patch("os.path.exists")
    def test_error_when_only_token_exists(self, mock_exists, mock_run):
        """If token exists but outlook-send-mail.js does NOT, returns error."""
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

        assert "error" in result
        assert "not configured" in result["error"].lower()
        mock_run.assert_not_called()
