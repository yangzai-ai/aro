"""Configured entry point discovery, ownership and backwards-compatible assembly."""

from dataclasses import dataclass

import pytest
import yaml

from core.config_loader import AgentConfig, LLMConfig
from core.kernel import discovery
from core.kernel.capability import Capability
from core.kernel.engine import AgentEngine
from core.kernel.plugin_loader import PluginLoadError


class Plugin(Capability):
    def __init__(self):
        self.config = None
        self.closed = False

    @property
    def name(self):
        return "external"

    def initialize(self, config):
        self.config = config

    def hooks(self):
        return []

    def tools(self):
        return []

    async def close(self):
        self.closed = True


@dataclass
class Entry:
    name: str
    export: object = Plugin
    loads: int = 0

    def load(self):
        self.loads += 1
        if isinstance(self.export, Exception):
            raise self.export
        return self.export


class Entries(list):
    def select(self, *, group):
        assert group == "aro.capabilities"
        return self


def install(monkeypatch, *entries):
    monkeypatch.setattr(discovery, "entry_points", lambda: Entries(entries))


def config(tmp_path, **values):
    path = tmp_path / "agent.yml"
    path.write_text(yaml.safe_dump(values, sort_keys=False), encoding="utf-8")
    return AgentConfig(path)


def llm():
    return LLMConfig(OPENAI_API_KEY="test", OPENAI_API_BASE="http://unused.test")


def test_discovery_initializes_selected_class_and_factory(monkeypatch):
    first = Entry("external")
    factory_instance = Plugin()
    second = Entry("factory", lambda cfg: factory_instance)
    disabled = Entry("disabled", RuntimeError("must not import"))
    install(monkeypatch, first, second, disabled)
    found = discovery.discover_capabilities(["external", "factory"], {"external": {"path": "db"}})
    assert found["external"].config == {"path": "db"}
    assert found["factory"] is factory_instance
    assert first.loads == second.loads == 1 and disabled.loads == 0


def test_empty_discovery(monkeypatch):
    install(monkeypatch)
    assert discovery.discover_capabilities() == {}
    disabled = Entry("external")
    install(monkeypatch, disabled)
    assert discovery.discover_capabilities([]) == {} and disabled.loads == 0


def test_failed_discovery_recovers_without_leaking_exception_text(monkeypatch, caplog):
    install(monkeypatch, Entry("broken", RuntimeError("credential=secret")), Entry("external"))
    assert list(discovery.discover_capabilities()) == ["external"]
    assert "broken" in caplog.text and "RuntimeError" in caplog.text
    assert "credential=secret" not in caplog.text


async def test_engine_only_loads_configured_entries_in_yaml_order(tmp_path, monkeypatch):
    a, b, disabled = Entry("a"), Entry("b"), Entry("disabled")
    install(monkeypatch, a, b, disabled)
    engine = await AgentEngine.create(
        llm(), config(tmp_path, capabilities={"b": {"n": 2}, "a": {"n": 1}})
    )
    try:
        assert [cap.config for cap in engine.capabilities] == [{"n": 2}, {"n": 1}]
        assert disabled.loads == 0
    finally:
        await engine.close()


@pytest.mark.parametrize("empty", [True, False])
async def test_explicit_list_including_empty_skips_discovery(tmp_path, monkeypatch, empty):
    entry = Entry("external")
    install(monkeypatch, entry)
    explicit = [] if empty else [Plugin()]
    engine = await AgentEngine.create(
        llm(), config(tmp_path, capabilities=["external"]), capabilities=explicit
    )
    try:
        assert engine.capabilities == explicit and engine.capabilities is not explicit
        assert entry.loads == 0
    finally:
        await engine.close()


async def test_explicit_module_precedes_entry_point(tmp_path, monkeypatch):
    entry = Entry("basic_tools", RuntimeError("not selected"))
    install(monkeypatch, entry)
    engine = await AgentEngine.create(
        llm(),
        config(
            tmp_path,
            capabilities=["basic_tools"],
            capability_config={"basic_tools": {"module": "capabilities.basic_tools.capability"}},
        ),
    )
    try:
        assert entry.loads == 0 and engine.capabilities[0].name == "basic_tools"
    finally:
        await engine.close()


async def test_entry_failure_rolls_back_prior_and_partial_instances(tmp_path, monkeypatch):
    owned = []

    class Partial(Plugin):
        def __init__(self):
            super().__init__()
            owned.append(self)

        def initialize(self, cfg):
            raise ValueError("credential=secret")

    first = Plugin()
    install(monkeypatch, Entry("first", lambda cfg: first), Entry("partial", Partial))
    with pytest.raises(PluginLoadError, match="partial"):
        await AgentEngine.create(llm(), config(tmp_path, capabilities=["first", "partial"]))
    assert first.closed and owned[0].closed


async def test_selected_failure_never_falls_back_to_conventional_module(tmp_path, monkeypatch):
    entry = Entry("basic_tools", RuntimeError("broken"))
    install(monkeypatch, entry)
    with pytest.raises(PluginLoadError, match="entry point"):
        await AgentEngine.create(llm(), config(tmp_path, capabilities=["basic_tools"]))
    assert entry.loads == 1


def test_duplicate_entry_points_fail_closed(monkeypatch):
    first, second = Entry("external"), Entry("external")
    install(monkeypatch, first, second)
    with pytest.raises(PluginLoadError):
        discovery.discover_capabilities(["external"], strict=True)
    assert first.loads == 1 and second.loads == 0


@pytest.mark.parametrize(
    "values",
    [
        {"capabilities": {"external": {}}, "capability_config": {"external": {}}},
        {"capabilities": {"external": "invalid"}},
    ],
)
def test_mapping_config_rejects_duplicates_and_invalid_values(tmp_path, values):
    with pytest.raises(ValueError):
        _ = config(tmp_path, **values).capability_config


async def test_sync_engine_discovers_but_does_not_start_hooks(tmp_path, monkeypatch):
    entry = Entry("external")
    install(monkeypatch, entry)
    engine = AgentEngine(
        llm(), config(tmp_path, capabilities=["external"], capability_config={"external": {"x": 1}})
    )
    try:
        assert engine.capabilities[0].config == {"x": 1}
        assert engine._start_task is None
    finally:
        await engine.close()
