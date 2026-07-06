# ABOUTME: Tests for WhatsApp pairing gate (REQ-01) and verify-before-active (REQ-02).
# ABOUTME: REQ-01: unpaired WhatsApp stays inactive, zero reconnect attempts.
# ABOUTME: REQ-02: paired connector reports ACTIVE only after round-trip probe succeeds.
# ABOUTME: All external transport (bridge HTTP, aiohttp) is mocked — no real phone needed.
# ABOUTME: Branch: factory — R-2 task for hermes-fable-plan.md Phase R.
"""
WhatsApp pairing-gate and verify-before-active tests.

Coverage:
  REQ-01 — _read_creds_registered(): unpaired (registered=false or missing key) returns
            False; a properly paired creds.json returns the owner JID.
  REQ-01 — connect(): creds.json with registered=false → fatal non-retryable error,
            no bridge launch, no reconnect queued.
  REQ-01 — connect(): creds.json absent → fatal non-retryable error (existing gate,
            re-verified here for completeness).
  REQ-02 — _run_round_trip_probe(): success path returns True; HTTP failure / bridge 503
            / exception returns False.
  REQ-02 — connect(): paired + probe OK → _mark_connected called, returns True.
  REQ-02 — connect(): paired + probe fails → _mark_connected NOT called, returns False.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter(*, bridge_port: int = 19877):
    """Minimal WhatsAppAdapter bypassing __init__."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    from gateway.config import Platform

    a = WhatsAppAdapter.__new__(WhatsAppAdapter)
    a.platform = Platform.WHATSAPP
    # name is a read-only property derived from platform.value.title() —
    # do not attempt to set it directly.
    a.config = MagicMock()
    a.config.extra = {}
    a._bridge_port = bridge_port
    a._bridge_script = "/tmp/test-bridge.js"
    a._session_path = Path("/tmp/test-wa-session-r2")
    a._bridge_log_fh = None
    a._bridge_log = None
    a._bridge_process = None
    a._reply_prefix = None
    a._running = False
    a._shutting_down = False
    a._message_handler = None
    a._fatal_error_code = None
    a._fatal_error_message = None
    a._fatal_error_retryable = True
    a._fatal_error_handler = None
    a._active_sessions = {}
    a._pending_messages = {}
    a._background_tasks = set()
    a._auto_tts_disabled_chats = set()
    a._message_queue = asyncio.Queue()
    a._http_session = None
    a._poll_task = None
    a._dm_policy = "pairing"
    a._allow_from = set()
    a._group_policy = "pairing"
    a._group_allow_from = set()
    a._mention_patterns = []
    a._text_batch_delay_seconds = 5.0
    a._text_batch_split_delay_seconds = 10.0
    a._pending_text_batches = {}
    a._pending_text_batch_tasks = {}
    return a


def _creds(registered: bool, owner_jid: str = "16504796118:12@s.whatsapp.net") -> dict:
    """Build a minimal creds.json payload matching Baileys format."""
    return {
        "registered": registered,
        "me": {"id": owner_jid, "name": "Test"},
    }


# ---------------------------------------------------------------------------
# REQ-01 — _read_creds_registered() unit tests
# ---------------------------------------------------------------------------

def _make_bare_adapter():
    """Minimal adapter with only platform set (for pure unit tests of helpers)."""
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    from gateway.config import Platform
    a = WhatsAppAdapter.__new__(WhatsAppAdapter)
    a.platform = Platform.WHATSAPP
    return a


class TestReadCredsRegistered:
    """Unit tests for the _read_creds_registered helper method."""

    def test_registered_true_returns_owner_jid(self, tmp_path):
        a = _make_bare_adapter()
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps(_creds(True)))

        result = a._read_creds_registered(creds_path)

        assert result == "16504796118:12@s.whatsapp.net"

    def test_registered_false_returns_none(self, tmp_path):
        a = _make_bare_adapter()
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps(_creds(False)))

        result = a._read_creds_registered(creds_path)

        assert result is None

    def test_missing_registered_key_returns_none(self, tmp_path):
        a = _make_bare_adapter()
        creds_path = tmp_path / "creds.json"
        # creds.json without a 'registered' key (legacy or partial file)
        creds_path.write_text(json.dumps({"me": {"id": "123@s.whatsapp.net"}}))

        result = a._read_creds_registered(creds_path)

        assert result is None

    def test_missing_me_id_returns_none(self, tmp_path):
        a = _make_bare_adapter()
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps({"registered": True}))

        result = a._read_creds_registered(creds_path)

        assert result is None

    def test_corrupt_json_returns_none(self, tmp_path):
        a = _make_bare_adapter()
        creds_path = tmp_path / "creds.json"
        creds_path.write_text("{not valid json")

        result = a._read_creds_registered(creds_path)

        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        a = _make_bare_adapter()
        creds_path = tmp_path / "nonexistent.json"

        result = a._read_creds_registered(creds_path)

        assert result is None


# ---------------------------------------------------------------------------
# REQ-01 — connect() pairing gate integration tests
# ---------------------------------------------------------------------------

class TestConnectPairingGate:
    """connect() must refuse to launch the bridge when creds show not-registered."""

    @pytest.mark.asyncio
    async def test_registered_false_sets_fatal_non_retryable(self, tmp_path):
        """creds.json with registered=false → fatal error, no bridge launch."""
        adapter = _make_adapter()
        adapter._session_path = tmp_path
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps(_creds(False)))

        bridge_js = tmp_path / "bridge.js"
        bridge_js.write_text("// stub")
        adapter._bridge_script = str(bridge_js)

        with patch(
            "plugins.platforms.whatsapp.adapter.check_whatsapp_requirements",
            return_value=True,
        ), patch(
            "subprocess.Popen"
        ) as mock_popen:
            result = await adapter.connect()

        assert result is False
        # Bridge was never spawned
        mock_popen.assert_not_called()
        # Fatal error is non-retryable so the reconnect watcher drops it
        assert adapter._fatal_error_code == "whatsapp_not_registered"
        assert adapter._fatal_error_retryable is False

    @pytest.mark.asyncio
    async def test_creds_absent_still_fatal_non_retryable(self, tmp_path):
        """No creds.json → existing gate fires as non-retryable (not-paired path)."""
        adapter = _make_adapter()
        adapter._session_path = tmp_path
        bridge_js = tmp_path / "bridge.js"
        bridge_js.write_text("// stub")
        adapter._bridge_script = str(bridge_js)

        with patch(
            "plugins.platforms.whatsapp.adapter.check_whatsapp_requirements",
            return_value=True,
        ), patch("subprocess.Popen") as mock_popen:
            result = await adapter.connect()

        assert result is False
        mock_popen.assert_not_called()
        assert adapter._fatal_error_retryable is False

    @pytest.mark.asyncio
    async def test_registered_false_does_not_enter_failed_platforms(self, tmp_path):
        """Non-retryable fatal → reconnect watcher drops it immediately.

        We can't test _failed_platforms directly here (that lives in GatewayRunner),
        but we verify the retryable=False contract that the watcher keys on.
        """
        adapter = _make_adapter()
        adapter._session_path = tmp_path
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps(_creds(False)))

        bridge_js = tmp_path / "bridge.js"
        bridge_js.write_text("// stub")
        adapter._bridge_script = str(bridge_js)

        with patch(
            "plugins.platforms.whatsapp.adapter.check_whatsapp_requirements",
            return_value=True,
        ):
            await adapter.connect()

        # The reconnect watcher only keeps platforms in _failed_platforms when
        # fatal_error_retryable is True. Verify the contract.
        assert adapter._fatal_error_retryable is False


# ---------------------------------------------------------------------------
# REQ-02 — _run_round_trip_probe() unit tests
# ---------------------------------------------------------------------------

class TestRunRoundTripProbe:
    """Unit tests for the round-trip probe helper."""

    @pytest.mark.asyncio
    async def test_success_returns_true(self):
        """Bridge returns HTTP 200 with success:true → probe passes."""
        adapter = _make_adapter()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"success": True, "messageId": "abc123"})

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_cm)
        adapter._http_session = mock_session

        result = await adapter._run_round_trip_probe(
            "16504796118:12@s.whatsapp.net"
        )

        assert result is True
        mock_session.post.assert_called_once()
        call_kwargs = mock_session.post.call_args
        # Probe must hit the /send endpoint
        assert "/send" in call_kwargs[0][0]

    @pytest.mark.asyncio
    async def test_http_non_200_returns_false(self):
        """Bridge returns HTTP 503 (not connected) → probe fails."""
        adapter = _make_adapter()

        mock_resp = MagicMock()
        mock_resp.status = 503
        mock_resp.text = AsyncMock(return_value="Not connected to WhatsApp")

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_cm)
        adapter._http_session = mock_session

        result = await adapter._run_round_trip_probe(
            "16504796118:12@s.whatsapp.net"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_bridge_success_false_returns_false(self):
        """Bridge returns HTTP 200 but success:false → probe fails."""
        adapter = _make_adapter()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"success": False, "error": "rate limited"})

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_cm)
        adapter._http_session = mock_session

        result = await adapter._run_round_trip_probe(
            "16504796118:12@s.whatsapp.net"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_network_exception_returns_false(self):
        """Network error (aiohttp raises) → probe fails gracefully."""
        import aiohttp
        adapter = _make_adapter()

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
        adapter._http_session = mock_session

        result = await adapter._run_round_trip_probe(
            "16504796118:12@s.whatsapp.net"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_no_http_session_returns_false(self):
        """If _http_session is None (not yet set up), probe fails."""
        adapter = _make_adapter()
        adapter._http_session = None

        result = await adapter._run_round_trip_probe(
            "16504796118:12@s.whatsapp.net"
        )

        assert result is False


# ---------------------------------------------------------------------------
# REQ-02 — connect() verify-before-active integration tests
# ---------------------------------------------------------------------------

class _AsyncCM:
    """Minimal async context manager wrapping a fixed mock response."""
    def __init__(self, value):
        self.value = value
    async def __aenter__(self):
        return self.value
    async def __aexit__(self, *exc):
        return False


class TestConnectVerifyBeforeActive:
    """connect() must not call _mark_connected until the round-trip probe passes."""

    def _make_health_response(self, status: str):
        """Build mock aiohttp response for /health."""
        r = MagicMock()
        r.status = 200
        r.json = AsyncMock(return_value={"status": status, "scriptHash": "abc123"})
        return r

    def _make_send_response(self, *, success: bool, http_status: int = 200):
        """Build mock aiohttp response for /send probe."""
        r = MagicMock()
        r.status = http_status
        if http_status == 200:
            r.json = AsyncMock(return_value={"success": success, "messageId": "probe-id"})
        else:
            r.text = AsyncMock(return_value="error")
        return r

    def _build_aiohttp_mock(self, responses: list):
        """Build mock aiohttp.ClientSession with a sequence of GET/POST responses.

        responses = list of mock response objects delivered in order.
        """
        call_count = [0]

        class _CM:
            def __init__(self, resp):
                self._resp = resp
            async def __aenter__(self):
                return self._resp
            async def __aexit__(self, *_):
                return False

        class _Session:
            def get(self, *args, **kwargs):
                resp = responses[call_count[0] % len(responses)]
                call_count[0] += 1
                return _CM(resp)
            def post(self, *args, **kwargs):
                resp = responses[call_count[0] % len(responses)]
                call_count[0] += 1
                return _CM(resp)
            async def close(self):
                pass

        class _SessionFactory:
            def __call__(self):
                return _Session()
            def __enter__(self):
                return _Session()
            def __exit__(self, *_):
                pass

        return _SessionFactory()

    @pytest.mark.asyncio
    async def test_paired_probe_ok_marks_connected(self, tmp_path):
        """Paired creds + bridge connected + probe succeeds → ACTIVE."""
        adapter = _make_adapter()
        adapter._session_path = tmp_path
        owner_jid = "16504796118:12@s.whatsapp.net"
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps(_creds(True, owner_jid)))
        bridge_js = tmp_path / "bridge.js"
        bridge_js.write_text("// stub")
        adapter._bridge_script = str(bridge_js)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        # Health says "connected", then probe /send succeeds
        health_resp = self._make_health_response("connected")
        send_resp = self._make_send_response(success=True)

        call_seq = [health_resp, send_resp]
        call_count = [0]

        class _CM:
            def __init__(self, resp):
                self._resp = resp
            async def __aenter__(self):
                return self._resp
            async def __aexit__(self, *_):
                return False

        class _Session:
            def get(self, *args, **kwargs):
                r = call_seq[min(call_count[0], len(call_seq) - 1)]
                call_count[0] += 1
                return _CM(r)
            def post(self, *args, **kwargs):
                r = call_seq[min(call_count[0], len(call_seq) - 1)]
                call_count[0] += 1
                return _CM(r)
            async def close(self):
                pass
            # async-CM support: startup loop uses "async with aiohttp.ClientSession() as s"
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return False

        mark_connected_called = []

        def _spy_mark():
            mark_connected_called.append(True)

        adapter._mark_connected = _spy_mark

        with patch(
            "plugins.platforms.whatsapp.adapter.check_whatsapp_requirements",
            return_value=True,
        ), patch.object(Path, "mkdir", return_value=None), \
           patch("subprocess.run", return_value=MagicMock(returncode=0)), \
           patch("subprocess.Popen", return_value=mock_proc), \
           patch("builtins.open", return_value=MagicMock()), \
           patch("plugins.platforms.whatsapp.adapter.asyncio.sleep", new_callable=AsyncMock), \
           patch("plugins.platforms.whatsapp.adapter.asyncio.create_task"), \
           patch("aiohttp.ClientSession", side_effect=lambda: _Session()), \
           patch.object(adapter, "_acquire_platform_lock", return_value=True), \
           patch.object(adapter, "_release_platform_lock", return_value=None), \
           patch("plugins.platforms.whatsapp.adapter._kill_stale_bridge_by_pidfile"), \
           patch("plugins.platforms.whatsapp.adapter._kill_port_process"), \
           patch("plugins.platforms.whatsapp.adapter._write_bridge_pidfile"), \
           patch("plugins.platforms.whatsapp.adapter._file_content_hash", return_value="abc123"):
            result = await adapter.connect()

        assert result is True
        assert len(mark_connected_called) == 1, "_mark_connected must be called exactly once"

    @pytest.mark.asyncio
    async def test_paired_probe_fail_does_not_mark_connected(self, tmp_path):
        """Paired creds + bridge connected + probe fails → NOT ACTIVE, returns False."""
        adapter = _make_adapter()
        adapter._session_path = tmp_path
        owner_jid = "16504796118:12@s.whatsapp.net"
        creds_path = tmp_path / "creds.json"
        creds_path.write_text(json.dumps(_creds(True, owner_jid)))
        bridge_js = tmp_path / "bridge.js"
        bridge_js.write_text("// stub")
        adapter._bridge_script = str(bridge_js)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        health_resp = self._make_health_response("connected")
        send_resp = self._make_send_response(success=False, http_status=503)

        call_seq = [health_resp, send_resp]
        call_count = [0]

        class _CM:
            def __init__(self, resp):
                self._resp = resp
            async def __aenter__(self):
                return self._resp
            async def __aexit__(self, *_):
                return False

        class _Session:
            def get(self, *args, **kwargs):
                r = call_seq[min(call_count[0], len(call_seq) - 1)]
                call_count[0] += 1
                return _CM(r)
            def post(self, *args, **kwargs):
                r = call_seq[min(call_count[0], len(call_seq) - 1)]
                call_count[0] += 1
                return _CM(r)
            async def close(self):
                pass
            # async-CM support: startup loop uses "async with aiohttp.ClientSession() as s"
            async def __aenter__(self):
                return self
            async def __aexit__(self, *_):
                return False

        mark_connected_called = []
        adapter._mark_connected = lambda: mark_connected_called.append(True)

        with patch(
            "plugins.platforms.whatsapp.adapter.check_whatsapp_requirements",
            return_value=True,
        ), patch.object(Path, "mkdir", return_value=None), \
           patch("subprocess.run", return_value=MagicMock(returncode=0)), \
           patch("subprocess.Popen", return_value=mock_proc), \
           patch("builtins.open", return_value=MagicMock()), \
           patch("plugins.platforms.whatsapp.adapter.asyncio.sleep", new_callable=AsyncMock), \
           patch("plugins.platforms.whatsapp.adapter.asyncio.create_task"), \
           patch("aiohttp.ClientSession", side_effect=lambda: _Session()), \
           patch.object(adapter, "_acquire_platform_lock", return_value=True), \
           patch.object(adapter, "_release_platform_lock", return_value=None), \
           patch("plugins.platforms.whatsapp.adapter._kill_stale_bridge_by_pidfile"), \
           patch("plugins.platforms.whatsapp.adapter._kill_port_process"), \
           patch("plugins.platforms.whatsapp.adapter._write_bridge_pidfile"), \
           patch("plugins.platforms.whatsapp.adapter._file_content_hash", return_value="abc123"):
            result = await adapter.connect()

        assert result is False
        assert len(mark_connected_called) == 0, "_mark_connected must NOT be called"
