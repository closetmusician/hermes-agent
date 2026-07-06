# ABOUTME: Tests for mandatory-plugin fail-closed behavior (REQ-01 through REQ-04).
# ABOUTME: Verifies that a mandatory plugin load failure raises MandatoryPluginLoadError
# ABOUTME: (preventing gateway startup), while optional failures and SAFE_MODE are unaffected.
# ABOUTME: All tests follow upstream conventions: tmp_path + monkeypatch, no mocking of loader logic.
# ABOUTME: See docs/plans/diagnosis-2026-07-03/hermes-fable-plan.md §Phase R task R-1.

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest
import yaml

from hermes_cli.plugins import (
    MANDATORY_SECURITY_PLUGINS,
    MandatoryPluginLoadError,
    PluginManifest,
    PluginManager,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_plugin_dir(
    base: Path,
    name: str,
    *,
    register_body: str = "pass",
    manifest_extra: dict | None = None,
    broken: bool = False,
) -> Path:
    """Create a minimal plugin directory (plugin.yaml + __init__.py).

    Purpose: shared fixture factory for all test classes below.
    Usage: pass *base* as the bundled-plugins root; *name* becomes the dir name.
    Gotchas: if *broken* is True the register() raises RuntimeError to simulate
    a real load failure without touching the loader code path under test.
    """
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "name": name,
        "version": "0.1.0",
        "description": f"Test plugin {name}",
    }
    if manifest_extra:
        manifest.update(manifest_extra)

    (plugin_dir / "plugin.yaml").write_text(yaml.dump(manifest))

    if broken:
        (plugin_dir / "__init__.py").write_text(
            "def register(ctx):\n"
            "    raise RuntimeError('simulated mandatory plugin crash')\n"
        )
    else:
        (plugin_dir / "__init__.py").write_text(
            f"def register(ctx):\n    {register_body}\n"
        )
    return plugin_dir


def _make_config(hermes_home: Path, *, enabled: list | None = None,
                 disabled: list | None = None) -> None:
    """Write a minimal config.yaml under *hermes_home*.

    Purpose: configure plugins.enabled / plugins.disabled for a test run.
    Usage: call before constructing PluginManager in each test.
    Gotchas: writes only the supplied keys; omits the others entirely.
    """
    cfg: dict = {}
    plugins: dict = {}
    if enabled is not None:
        plugins["enabled"] = enabled
    if disabled is not None:
        plugins["disabled"] = disabled
    if plugins:
        cfg["plugins"] = plugins
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(cfg))


# ---------------------------------------------------------------------------
# REQ-01a: mandatory plugin load failure => MandatoryPluginLoadError (fail-closed)
# ---------------------------------------------------------------------------


class TestMandatoryFailClosed:
    """REQ-01: a broken mandatory plugin causes discover_and_load() to raise
    MandatoryPluginLoadError naming the plugin."""

    def test_broken_mandatory_raises_with_plugin_name(self, tmp_path, monkeypatch):
        """discover_and_load() must raise MandatoryPluginLoadError when a
        mandatory plugin's register() raises, and the error message names the plugin."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(bundled_dir, "control-room",
                         manifest_extra={"mandatory": True}, broken=True)
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        with pytest.raises(MandatoryPluginLoadError) as exc_info:
            mgr.discover_and_load()

        # The error message must name the plugin so operators know what failed.
        assert "control-room" in str(exc_info.value)

    def test_broken_mandatory_logs_critical(self, tmp_path, monkeypatch, caplog):
        """Mandatory plugin load failure must emit a CRITICAL-level log before
        raising, so it appears in logs regardless of exception capture."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(bundled_dir, "email-send-guard",
                         manifest_extra={"mandatory": True}, broken=True)
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        with caplog.at_level(logging.CRITICAL, logger="hermes_cli.plugins"):
            mgr = PluginManager()
            with pytest.raises(MandatoryPluginLoadError):
                mgr.discover_and_load()

        critical_records = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert any("email-send-guard" in r.message for r in critical_records), (
            "Must emit a CRITICAL log naming the failing mandatory plugin"
        )

    def test_broken_mandatory_records_in_failures_dict(self, tmp_path, monkeypatch):
        """mandatory_load_failures dict must contain the plugin name + error
        when a mandatory plugin fails, even though the exception is also raised."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(bundled_dir, "tool-registry-guard",
                         manifest_extra={"mandatory": True}, broken=True)
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        with pytest.raises(MandatoryPluginLoadError):
            mgr.discover_and_load()

        assert "tool-registry-guard" in mgr.mandatory_load_failures
        assert mgr.mandatory_load_failures["tool-registry-guard"]  # non-empty message


# ---------------------------------------------------------------------------
# REQ-01b: optional plugin failure => start proceeds (no raise)
# ---------------------------------------------------------------------------


class TestOptionalPluginFailDoesNotAbort:
    """REQ-01: a broken non-mandatory plugin must NOT prevent startup."""

    def test_optional_broken_plugin_does_not_raise(self, tmp_path, monkeypatch):
        """When an optional plugin fails to load, discover_and_load() must
        complete normally (no exception raised)."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        # Create a broken optional standalone plugin
        optional_dir = bundled_dir / "optional-guard"
        optional_dir.mkdir(parents=True)
        (optional_dir / "plugin.yaml").write_text(yaml.dump({
            "name": "optional-guard",
            "version": "0.1.0",
        }))
        (optional_dir / "__init__.py").write_text(
            "def register(ctx):\n"
            "    raise RuntimeError('optional plugin crash')\n"
        )
        # Enable the optional plugin so it actually tries to load
        _make_config(hermes_home, enabled=["optional-guard"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()  # must not raise

        # Plugin recorded as not-enabled (failed), but no abort
        loaded = mgr._plugins.get("optional-guard")
        assert loaded is not None
        assert not loaded.enabled

    def test_optional_failure_not_in_mandatory_failures(self, tmp_path, monkeypatch):
        """Optional plugin failures must not pollute mandatory_load_failures."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        optional_dir = bundled_dir / "flaky-plugin"
        optional_dir.mkdir(parents=True)
        (optional_dir / "plugin.yaml").write_text(yaml.dump({
            "name": "flaky-plugin",
            "version": "0.1.0",
        }))
        (optional_dir / "__init__.py").write_text(
            "def register(ctx):\n    raise RuntimeError('flaky')\n"
        )
        _make_config(hermes_home, enabled=["flaky-plugin"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "flaky-plugin" not in mgr.mandatory_load_failures


# ---------------------------------------------------------------------------
# REQ-02: healthy mandatory plugins load normally
# ---------------------------------------------------------------------------


class TestHealthyMandatoryPluginsLoadNormally:
    """REQ-02: with control-room + tool-registry-guard healthy, discover_and_load()
    completes without raising and both plugins are enabled."""

    def test_healthy_mandatory_plugins_start_normally(self, tmp_path, monkeypatch):
        """Two healthy mandatory plugins must load without raising and appear
        in mgr._plugins with enabled=True."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(
            bundled_dir, "control-room",
            manifest_extra={"mandatory": True},
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_plugin_dir(
            bundled_dir, "tool-registry-guard",
            manifest_extra={"mandatory": True},
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()  # must not raise

        assert mgr._plugins["control-room"].enabled
        assert mgr._plugins["tool-registry-guard"].enabled
        assert not mgr.mandatory_load_failures

    def test_healthy_mandatory_bypass_disabled_config(self, tmp_path, monkeypatch):
        """Mandatory plugins must load even when listed in plugins.disabled."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(
            bundled_dir, "control-room",
            manifest_extra={"mandatory": True},
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_config(hermes_home, disabled=["control-room"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert mgr._plugins["control-room"].enabled, (
            "mandatory plugin must load even when config says disabled"
        )

    def test_healthy_mandatory_bypass_absent_enabled_list(self, tmp_path, monkeypatch):
        """Mandatory plugins load even when plugins.enabled is absent (opt-in gate bypassed)."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(
            bundled_dir, "email-send-guard",
            manifest_extra={"mandatory": True},
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        # No enabled list at all
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert mgr._plugins["email-send-guard"].enabled


# ---------------------------------------------------------------------------
# REQ-03 + dataclass: PluginManifest.mandatory field and MANDATORY_SECURITY_PLUGINS
# ---------------------------------------------------------------------------


class TestMandatoryManifestField:
    """PluginManifest must expose a boolean mandatory field; constants must exist."""

    def test_manifest_has_mandatory_field_default_false(self):
        """PluginManifest.mandatory defaults to False."""
        m = PluginManifest(name="test-plugin")
        assert hasattr(m, "mandatory")
        assert m.mandatory is False

    def test_manifest_mandatory_true_accepted(self):
        """PluginManifest.mandatory can be set to True."""
        m = PluginManifest(name="test-plugin", mandatory=True)
        assert m.mandatory is True

    def test_mandatory_security_plugins_exists_and_is_frozenset(self):
        """MANDATORY_SECURITY_PLUGINS must be a frozenset (immutable)."""
        assert MANDATORY_SECURITY_PLUGINS is not None
        assert isinstance(MANDATORY_SECURITY_PLUGINS, frozenset)

    def test_mandatory_security_plugins_includes_expected_plugins(self):
        """MANDATORY_SECURITY_PLUGINS must include the three P0 security plugins."""
        assert "control-room" in MANDATORY_SECURITY_PLUGINS
        assert "tool-registry-guard" in MANDATORY_SECURITY_PLUGINS

    def test_hardcoded_set_overrides_absent_yaml_flag(self, tmp_path, monkeypatch):
        """A plugin named control-room with no mandatory: true in YAML is still
        treated as mandatory due to the hardcoded MANDATORY_SECURITY_PLUGINS set."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        # Create control-room WITHOUT mandatory: true in manifest
        cr_dir = bundled_dir / "control-room"
        cr_dir.mkdir(parents=True)
        (cr_dir / "plugin.yaml").write_text(yaml.dump({
            "name": "control-room",
            "version": "0.1.0",
            # deliberate: no mandatory key
        }))
        (cr_dir / "__init__.py").write_text(
            "def register(ctx):\n    ctx.register_hook('pre_tool_call', lambda **kw: None)\n"
        )
        # List it in disabled to ensure the bypass fires
        _make_config(hermes_home, disabled=["control-room"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert mgr._plugins["control-room"].enabled, (
            "control-room in MANDATORY_SECURITY_PLUGINS must bypass disabled even without YAML flag"
        )


class TestMandatoryPluginLoadErrorExistsAndIsRuntimeError:
    """MandatoryPluginLoadError must be importable and inherit from RuntimeError."""

    def test_error_class_exists(self):
        """MandatoryPluginLoadError must exist in hermes_cli.plugins."""
        assert MandatoryPluginLoadError is not None

    def test_error_is_runtime_error_subclass(self):
        """MandatoryPluginLoadError must be a RuntimeError subclass so generic
        RuntimeError catch blocks still work."""
        assert issubclass(MandatoryPluginLoadError, RuntimeError)


# ---------------------------------------------------------------------------
# REQ-04: HERMES_SAFE_MODE bypasses fail-closed path
# ---------------------------------------------------------------------------


class TestSafeModeBypass:
    """REQ-04: HERMES_SAFE_MODE=1 is an intentional operator override — it must
    skip ALL plugin discovery (including mandatory) without raising."""

    def test_safe_mode_skips_discovery_without_raising(self, tmp_path, monkeypatch):
        """With HERMES_SAFE_MODE=1, even broken mandatory plugins do not cause
        discover_and_load() to raise."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        # A broken mandatory plugin that would otherwise abort startup
        _make_plugin_dir(bundled_dir, "control-room",
                         manifest_extra={"mandatory": True}, broken=True)
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))
        monkeypatch.setenv("HERMES_SAFE_MODE", "1")

        mgr = PluginManager()
        mgr.discover_and_load()  # must NOT raise

        # Safe mode: no plugins loaded, no failures recorded
        assert not mgr._plugins
        assert not mgr.mandatory_load_failures

    def test_safe_mode_empty_registry(self, tmp_path, monkeypatch):
        """With HERMES_SAFE_MODE=1, no plugins appear in _plugins at all."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled"
        _make_plugin_dir(
            bundled_dir, "control-room",
            manifest_extra={"mandatory": True},
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))
        monkeypatch.setenv("HERMES_SAFE_MODE", "1")

        mgr = PluginManager()
        mgr.discover_and_load()

        assert not mgr._plugins, "SAFE_MODE must produce an empty plugin registry"
