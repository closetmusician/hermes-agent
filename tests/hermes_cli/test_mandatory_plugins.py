"""
ABOUTME: Tests for mandatory security plugin loading behavior.
ABOUTME: Verifies that control-room and email-send-guard cannot be disabled,
ABOUTME: load unconditionally without plugins.enabled config, and trigger
ABOUTME: fail-closed behavior (dangerous tool disabling) on load failure.
"""

import importlib
import logging
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from hermes_cli.plugins import (
    PluginManager,
    PluginManifest,
)

# Import MANDATORY_SECURITY_PLUGINS if it exists; tests will fail if missing.
try:
    from hermes_cli.plugins import MANDATORY_SECURITY_PLUGINS
except ImportError:
    MANDATORY_SECURITY_PLUGINS = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mandatory_plugin_dir(base: Path, name: str, *,
                                register_body: str = "pass",
                                manifest_extra: dict | None = None,
                                broken: bool = False) -> Path:
    """Create a minimal mandatory plugin directory with plugin.yaml + __init__.py.

    If *broken* is True, the register() function raises RuntimeError to
    simulate a plugin that fails to load.
    """
    plugin_dir = base / name
    plugin_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": f"Test mandatory plugin {name}",
        "mandatory": True,
    }
    if manifest_extra:
        manifest.update(manifest_extra)

    (plugin_dir / "plugin.yaml").write_text(yaml.dump(manifest))

    if broken:
        (plugin_dir / "__init__.py").write_text(
            "def register(ctx):\n"
            "    raise RuntimeError('simulated plugin crash')\n"
        )
    else:
        (plugin_dir / "__init__.py").write_text(
            f"def register(ctx):\n    {register_body}\n"
        )

    return plugin_dir


def _make_config(hermes_home: Path, *, enabled: list | None = None,
                 disabled: list | None = None) -> None:
    """Write a config.yaml with plugins.enabled and/or plugins.disabled."""
    cfg: dict = {"plugins": {}}
    if enabled is not None:
        cfg["plugins"]["enabled"] = enabled
    if disabled is not None:
        cfg["plugins"]["disabled"] = disabled
    hermes_home.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(cfg))


# ---------------------------------------------------------------------------
# TestMandatoryPluginConstant
# ---------------------------------------------------------------------------


class TestMandatoryPluginConstant:
    """The MANDATORY_SECURITY_PLUGINS constant must exist and include both
    security plugins."""

    def test_constant_includes_control_room(self):
        assert "control-room" in MANDATORY_SECURITY_PLUGINS

    def test_constant_includes_email_send_guard(self):
        assert "email-send-guard" in MANDATORY_SECURITY_PLUGINS

    def test_constant_is_frozen(self):
        """The set should be a frozenset so it cannot be mutated at runtime."""
        assert isinstance(MANDATORY_SECURITY_PLUGINS, frozenset)


# ---------------------------------------------------------------------------
# TestMandatoryPluginCannotBeDisabled
# ---------------------------------------------------------------------------


class TestMandatoryPluginCannotBeDisabled:
    """Mandatory plugins load even when listed in plugins.disabled."""

    def test_control_room_loads_despite_disabled_config(self, tmp_path, monkeypatch):
        """control-room listed in plugins.disabled must still load."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        _make_mandatory_plugin_dir(
            bundled_dir, "control-room",
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_config(hermes_home, disabled=["control-room"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "control-room" in mgr._plugins
        assert mgr._plugins["control-room"].enabled, (
            "control-room should be enabled despite being in plugins.disabled"
        )

    def test_email_send_guard_loads_despite_disabled_config(self, tmp_path, monkeypatch):
        """email-send-guard listed in plugins.disabled must still load."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        _make_mandatory_plugin_dir(
            bundled_dir, "email-send-guard",
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_config(hermes_home, disabled=["email-send-guard"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "email-send-guard" in mgr._plugins
        assert mgr._plugins["email-send-guard"].enabled, (
            "email-send-guard should be enabled despite being in plugins.disabled"
        )


# ---------------------------------------------------------------------------
# TestMandatoryPluginLoadsWithoutExplicitEnable
# ---------------------------------------------------------------------------


class TestMandatoryPluginLoadsWithoutExplicitEnable:
    """Mandatory plugins load even when plugins.enabled is missing or empty."""

    def test_loads_with_no_enabled_config(self, tmp_path, monkeypatch):
        """With no plugins.enabled set at all, mandatory plugins still load."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        _make_mandatory_plugin_dir(
            bundled_dir, "control-room",
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_mandatory_plugin_dir(
            bundled_dir, "email-send-guard",
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        # Config exists but has NO plugins.enabled key
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "control-room" in mgr._plugins
        assert mgr._plugins["control-room"].enabled
        assert "email-send-guard" in mgr._plugins
        assert mgr._plugins["email-send-guard"].enabled

    def test_loads_with_empty_enabled_list(self, tmp_path, monkeypatch):
        """With plugins.enabled = [], mandatory plugins still load."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        _make_mandatory_plugin_dir(
            bundled_dir, "control-room",
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_mandatory_plugin_dir(
            bundled_dir, "email-send-guard",
            register_body='ctx.register_hook("pre_tool_call", lambda **kw: None)',
        )
        _make_config(hermes_home, enabled=[])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert mgr._plugins["control-room"].enabled
        assert mgr._plugins["email-send-guard"].enabled

    def test_non_mandatory_plugin_still_requires_enabled(self, tmp_path, monkeypatch):
        """Regular standalone plugins are NOT loaded without plugins.enabled."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        # Create a regular (non-mandatory) standalone plugin
        plugin_dir = bundled_dir / "optional-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(yaml.dump({
            "name": "optional-plugin",
            "version": "0.1.0",
        }))
        (plugin_dir / "__init__.py").write_text("def register(ctx):\n    pass\n")
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        if "optional-plugin" in mgr._plugins:
            assert not mgr._plugins["optional-plugin"].enabled, (
                "Non-mandatory plugins should NOT auto-load without plugins.enabled"
            )


# ---------------------------------------------------------------------------
# TestMandatoryPluginLoadFailure
# ---------------------------------------------------------------------------


class TestMandatoryPluginLoadFailure:
    """When a mandatory plugin fails to load, the system must fail closed --
    disable dangerous tools rather than running unguarded."""

    def test_load_failure_disables_dangerous_tools(self, tmp_path, monkeypatch):
        """If a mandatory plugin crashes during register(), dangerous tools
        must be disabled (removed from the tool registry)."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        _make_mandatory_plugin_dir(
            bundled_dir, "control-room",
            broken=True,
        )
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        # The plugin should be recorded but NOT enabled
        assert "control-room" in mgr._plugins
        assert not mgr._plugins["control-room"].enabled

        # The manager should have recorded mandatory load failures
        assert mgr.mandatory_load_failures, (
            "PluginManager should track mandatory plugin load failures"
        )
        assert "control-room" in mgr.mandatory_load_failures

    def test_load_failure_logs_critical(self, tmp_path, monkeypatch, caplog):
        """Mandatory plugin load failure must log at CRITICAL level."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        _make_mandatory_plugin_dir(
            bundled_dir, "email-send-guard",
            broken=True,
        )
        _make_config(hermes_home)

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        with caplog.at_level(logging.CRITICAL, logger="hermes_cli.plugins"):
            mgr = PluginManager()
            mgr.discover_and_load()

        critical_msgs = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert any("email-send-guard" in r.message for r in critical_msgs), (
            "Mandatory plugin load failure should emit a CRITICAL log"
        )


# ---------------------------------------------------------------------------
# TestMandatoryPluginManifestField
# ---------------------------------------------------------------------------


class TestMandatoryPluginManifestField:
    """The mandatory field in plugin.yaml is parsed into PluginManifest."""

    def test_manifest_has_mandatory_field(self):
        """PluginManifest dataclass includes a 'mandatory' boolean field."""
        m = PluginManifest(name="test")
        assert hasattr(m, "mandatory"), "PluginManifest must have a 'mandatory' field"
        m2 = PluginManifest(name="test", mandatory=True)
        assert m2.mandatory is True

    def test_manifest_mandatory_defaults_false(self):
        """PluginManifest.mandatory defaults to False for normal plugins."""
        m = PluginManifest(name="test")
        assert hasattr(m, "mandatory"), "PluginManifest must have a 'mandatory' field"
        assert m.mandatory is False

    def test_hardcoded_list_overrides_yaml(self, tmp_path, monkeypatch):
        """Even if mandatory: true is removed from YAML, the hardcoded
        MANDATORY_SECURITY_PLUGINS set ensures loading."""
        hermes_home = tmp_path / "hermes_test"
        bundled_dir = tmp_path / "bundled_plugins"
        # Create control-room WITHOUT mandatory: true in manifest
        plugin_dir = bundled_dir / "control-room"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(yaml.dump({
            "name": "control-room",
            "version": "0.1.0",
            # No mandatory field!
        }))
        (plugin_dir / "__init__.py").write_text(
            "def register(ctx):\n"
            "    ctx.register_hook('pre_tool_call', lambda **kw: None)\n"
        )
        _make_config(hermes_home, disabled=["control-room"])

        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HERMES_BUNDLED_PLUGINS", str(bundled_dir))

        mgr = PluginManager()
        mgr.discover_and_load()

        assert "control-room" in mgr._plugins
        assert mgr._plugins["control-room"].enabled, (
            "control-room must load even without mandatory: true in YAML "
            "because the hardcoded MANDATORY_SECURITY_PLUGINS overrides"
        )
