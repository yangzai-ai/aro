"""Tests for configuration driven capability loading."""

from pathlib import Path
from unittest.mock import patch

import pytest

from core.config_loader import AgentConfig
from core.kernel.plugin_loader import PluginLoadError, load_capabilities


def test_loads_only_configured_capabilities() -> None:
    """An empty configuration must not import or instantiate capabilities."""
    assert load_capabilities([]) == []
    loaded = load_capabilities(["basic_tools"])
    assert [capability.name for capability in loaded] == ["basic_tools"]


def test_unknown_capability_has_actionable_error() -> None:
    """Missing plugins fail at the configuration boundary."""
    with pytest.raises(PluginLoadError, match="missing"):
        load_capabilities(["missing"])


def test_disabled_capability_does_not_import() -> None:
    """Configured settings alone must not import a capability absent from names."""
    with patch("core.kernel.plugin_loader.importlib.import_module") as import_module:
        assert (
            load_capabilities([], {"helpdesk_tools": {"module": "projects.helpdesk.capability"}})
            == []
        )
        import_module.assert_not_called()


def test_agent_config_validates_and_reads_yaml(tmp_path: Path) -> None:
    """AgentConfig exposes the declarative capability and tool selections."""
    path = tmp_path / "agent.yml"
    path.write_text(
        "capabilities: [basic_tools]\ntools: [get_current_time]\n"
        "capability_config:\n  basic_tools: {}\n",
        encoding="utf-8",
    )
    config = AgentConfig(path)
    assert config.capabilities == ["basic_tools"]
    assert config.tools == ["get_current_time"]
    assert config.capability_config == {"basic_tools": {}}
