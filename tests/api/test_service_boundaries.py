"""Generic API depends only on kernel contracts; project adapters supply services."""

import ast
import asyncio
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import yaml

from api.sessions import SessionStore
from api.settings import APISettings
from core.kernel.services import ApprovalService, hook_service
from projects.helpdesk.api import create_app
from tests.core.kernel.test_hook_lifecycle import HookPlugin, LifecycleHook, make_engine, stages


def test_core_and_api_never_import_concrete_capabilities():
    for root in (Path("core"), Path("api")):
        for path in root.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.module or "").startswith("capabilities"), str(path)
                elif isinstance(node, ast.Import):
                    assert all(not name.name.startswith("capabilities") for name in node.names), (
                        str(path)
                    )


@pytest.mark.parametrize("reason", ["delete", "expire", "shutdown"])
async def test_api_notifies_session_close_once_before_stop(tmp_path, monkeypatch, reason):
    hook = LifecycleHook()
    plugin = HookPlugin(hook)
    module = ModuleType("v15_probe_plugin")
    module.get_capability = lambda cfg: plugin
    monkeypatch.setitem(sys.modules, module.__name__, module)
    path = tmp_path / "api.yml"
    path.write_text(
        yaml.safe_dump(
            {"capabilities": ["probe"], "capability_config": {"probe": {"module": module.__name__}}}
        ),
        encoding="utf-8",
    )
    app = create_app(APISettings(_env_file=None, config=path, token=None))
    async with app.router.lifespan_context(app):
        store = app.state.sessions
        session = await store.reserve(None)
        session.busy = False
        assert stages(hook) == ["start"]
        if reason == "delete":
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://test"
            ) as client:
                response = await client.delete(
                    "/api/helpdesk/session", headers={"X-ARO-Session": session.id}
                )
                assert response.status_code == 200
                assert (
                    await client.delete(
                        "/api/helpdesk/session", headers={"X-ARO-Session": session.id}
                    )
                ).status_code == 404
        elif reason == "expire":
            session.updated -= store.settings.session_ttl_seconds + 1
            await store.expire()
        else:
            await store.close()
        await store.close_session(session)
        assert stages(hook) == ["start", "close", "stop"]
        assert hook.events[1][1] == session.state["context"]["thread_id"]
        assert plugin.closed and session.engine.llm._client.is_closed


async def test_host_service_supports_unknown_approval_implementation(tmp_path):
    class OtherApprovals:
        mode = "api"

        def decide(self, approval_id, thread_id, approved):
            self.decision = (approval_id, thread_id, approved)

    service = OtherApprovals()
    hook = LifecycleHook()
    hook.provides = {"approvals": service}
    engine = await make_engine(tmp_path, hook)
    store = SessionStore(APISettings(_env_file=None, token=None), lambda: engine)
    try:
        session = await store.reserve(None)
        assert session.approvals is service and isinstance(service, ApprovalService)
        session.approvals.decide("approval", session.id, True)
        assert service.decision == ("approval", session.id, True)
    finally:
        await store.close()


def test_ambiguous_host_service_is_rejected():
    first, second = LifecycleHook(), LifecycleHook()
    first.provides = second.provides = {"approvals": object()}
    with pytest.raises(ValueError, match="Multiple hooks"):
        hook_service([first, second], "approvals")
    assert hook_service([LifecycleHook()], "approvals") is None


async def test_session_cleanup_error_cannot_skip_engine_close(tmp_path):
    class Broken(LifecycleHook):
        async def on_session_close(self, session_id):
            await super().on_session_close(session_id)
            raise RuntimeError("external service failed")

    hook = Broken()
    engine = await make_engine(tmp_path, hook)
    store = SessionStore(APISettings(_env_file=None), lambda: engine)
    session = await store.reserve(None)
    with pytest.raises(RuntimeError, match="remaining cleanup"):
        await store.close_session(session)
    assert stages(hook) == ["start", "close", "stop"]
    assert engine.capabilities[0].closed and engine.llm._client.is_closed
    store.sessions.clear()


async def test_inflight_turn_is_cancelled_before_session_hook(tmp_path):
    hook = LifecycleHook()
    engine = await make_engine(tmp_path, hook)
    store = SessionStore(APISettings(_env_file=None), lambda: engine)
    session = await store.reserve(None)
    entered = asyncio.Event()

    async def inflight():
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            hook.events.append(("turn_cancelled",))

    session.task = asyncio.create_task(inflight())
    await entered.wait()
    await store.close_session(session)
    assert stages(hook) == ["start", "turn_cancelled", "close", "stop"]
    store.sessions.clear()


@pytest.mark.parametrize("reason", ["expire", "shutdown"])
async def test_failed_session_hook_does_not_abandon_other_sessions(tmp_path, caplog, reason):
    class Broken(LifecycleHook):
        async def on_session_close(self, session_id):
            await super().on_session_close(session_id)
            raise ValueError("credentials=never-log")

    engines = [await make_engine(tmp_path, Broken()), await make_engine(tmp_path, LifecycleHook())]
    queue = iter(engines)
    store = SessionStore(APISettings(_env_file=None), lambda: next(queue))
    first, second = await store.reserve(None), await store.reserve(None)
    for session in (first, second):
        session.busy = False
        session.updated -= store.settings.session_ttl_seconds + 1
    if reason == "expire":
        await store.expire()
    else:
        await store.close()
    assert not store.sessions
    assert all(engine.llm._client.is_closed and engine.capabilities[0].closed for engine in engines)
    assert "credentials=never-log" not in caplog.text
    assert "cleanup failed" in caplog.text
