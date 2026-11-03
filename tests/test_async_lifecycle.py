"""Generic lifecycle compatibility, rollback and API reservation races."""

import asyncio
import sys
import time
from types import ModuleType

import pytest
import yaml
from fastapi import HTTPException

from api.sessions import SessionStore
from api.settings import APISettings
from core.config_loader import AgentConfig
from core.kernel.capability import Capability
from core.kernel.engine import AgentEngine
from tests.capabilities.mcp_client.test_mcp import llm_settings


class LifecycleCapability(Capability):
    def __init__(self, name, log, fail=False, entered=None, hold=None):
        self._name, self.log, self.fail = name, log, fail
        self.entered, self.hold = entered, hold

    @property
    def name(self):
        return self._name

    async def start(self):
        self.log.append("start:" + self.name)
        if self.entered is not None:
            self.entered.set()
        if self.hold is not None:
            await self.hold.wait()
        if self.fail:
            raise ValueError("startup failed")

    async def close(self):
        self.log.append("close:" + self.name)

    def tools(self):
        self.log.append("tools:" + self.name)
        return []

    def middlewares(self):
        return []


def make_config(tmp_path, monkeypatch, caps):
    settings = {}
    for cap in caps:
        module = ModuleType("test_lifecycle_" + cap.name)
        module.get_capability = lambda config, cap=cap: cap
        monkeypatch.setitem(sys.modules, module.__name__, module)
        settings[cap.name] = {"module": module.__name__}
    path = tmp_path / "async.yml"
    path.write_text(
        yaml.safe_dump({"capabilities": list(settings), "capability_config": settings}),
        encoding="utf-8",
    )
    return AgentConfig(path)


async def test_start_before_collect_reverse_close_and_idempotency(tmp_path, monkeypatch):
    log = []
    cfg = make_config(
        tmp_path, monkeypatch, [LifecycleCapability("a", log), LifecycleCapability("b", log)]
    )
    engine = await AgentEngine.create(llm_settings(), cfg)
    assert log == ["start:a", "start:b", "tools:a", "tools:b"]
    await engine.close()
    await engine.close()
    assert log[-2:] == ["close:b", "close:a"]
    assert engine.llm._client.is_closed
    with pytest.raises(RuntimeError, match="closed"):
        await engine.run("hi")


async def test_start_failure_and_cleanup_error_do_not_leak(tmp_path, monkeypatch, caplog):
    log = []
    first = LifecycleCapability("a", log)
    second = LifecycleCapability("b", log, fail=True)

    async def bad_close():
        log.append("close:b")
        raise RuntimeError("cleanup failed")

    second.close = bad_close
    cfg = make_config(tmp_path, monkeypatch, [first, second])
    with pytest.raises(ValueError, match="startup failed"):
        await AgentEngine.create(llm_settings(), cfg)
    assert log == ["start:a", "start:b", "close:b", "close:a"]
    assert "cleanup failed" in caplog.text


async def test_factory_failure_closes_earlier_loaded_capability(tmp_path, monkeypatch):
    log = []
    cfg = make_config(
        tmp_path, monkeypatch, [LifecycleCapability("a", log), LifecycleCapability("b", log)]
    )

    def broken(config):
        raise ValueError("factory failed")

    sys.modules["test_lifecycle_b"].get_capability = broken
    with pytest.raises(RuntimeError, match="initialize"):
        await AgentEngine.create(llm_settings(), cfg)
    assert log == ["close:a"]


async def test_cancelled_initialization_closes_partial_resources(tmp_path, monkeypatch):
    log, entered = [], asyncio.Event()
    cfg = make_config(
        tmp_path,
        monkeypatch,
        [LifecycleCapability("a", log, entered=entered, hold=asyncio.Event())],
    )
    task = asyncio.create_task(AgentEngine.create(llm_settings(), cfg))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert log == ["start:a", "close:a"]


async def test_api_capacity_pending_cancel_and_busy(tmp_path, monkeypatch):
    log, entered, release = [], asyncio.Event(), asyncio.Event()
    cfg = make_config(
        tmp_path, monkeypatch, [LifecycleCapability("a", log, entered=entered, hold=release)]
    )

    async def factory():
        return await AgentEngine.create(llm_settings(), cfg)

    store = SessionStore(APISettings(_env_file=None, max_sessions=1), factory)
    pending = asyncio.create_task(store.reserve(None))
    await entered.wait()
    with pytest.raises(HTTPException) as exc:
        await store.reserve(None)
    assert exc.value.status_code == 503
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert store._creating == 0 and not store.sessions
    release.set()
    session = await store.reserve(None)
    with pytest.raises(HTTPException) as exc:
        await store.reserve(session.id)
    assert exc.value.status_code == 409
    session.busy = False
    session.updated = time.monotonic() - store.settings.session_ttl_seconds - 1
    await store.expire()
    assert not store.sessions and session.engine.llm._client.is_closed
    await store.close()


async def test_shutdown_cancels_pending_creation(tmp_path, monkeypatch):
    log, entered = [], asyncio.Event()
    cfg = make_config(
        tmp_path,
        monkeypatch,
        [LifecycleCapability("a", log, entered=entered, hold=asyncio.Event())],
    )

    async def factory():
        return await AgentEngine.create(llm_settings(), cfg)

    store = SessionStore(APISettings(_env_file=None), factory)
    task = asyncio.create_task(store.reserve(None))
    await entered.wait()
    await store.close()
    assert task.cancelled() and store._creating == 0 and not store.sessions
    assert log == ["start:a", "close:a"]
    with pytest.raises(HTTPException):
        await store.reserve(None)


async def test_assembly_failure_closes_started_capabilities(tmp_path, monkeypatch):
    log = []
    cfg = make_config(tmp_path, monkeypatch, [LifecycleCapability("a", log)])

    def broken(self):
        raise ValueError("assembly failed")

    monkeypatch.setattr(AgentEngine, "_assemble", broken)
    with pytest.raises(ValueError, match="assembly failed"):
        await AgentEngine.create(llm_settings(), cfg)
    assert log == ["start:a", "close:a"]
