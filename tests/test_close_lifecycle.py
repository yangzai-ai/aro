"""Approved R1 lifecycle semantics, including failed internal cleanup."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from anyio import CancelScope

from core.kernel.engine import AgentEngine
from core.kernel.lifecycle import wait_for_cleanup
from tests.capabilities.mcp_client.test_mcp import llm_settings
from tests.test_async_lifecycle import LifecycleCapability, make_config


@pytest.mark.parametrize("failure", [None, ValueError, asyncio.CancelledError])
async def test_concurrent_close_cancellation_and_internal_failure(
    tmp_path, monkeypatch, caplog, failure
):
    log = []
    a, b = LifecycleCapability("a", log), LifecycleCapability("b", log)
    entered, finish, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def held_close():
        log.append("close:b")
        entered.set()
        await finish.wait()
        if failure:
            raise failure("SECRET_SENTINEL")

    b.close = held_close
    engine = await AgentEngine.create(llm_settings(), make_config(tmp_path, monkeypatch, [a, b]))
    llm_close = AsyncMock(wraps=engine.llm._client.close)
    monkeypatch.setattr(engine.llm._client, "close", llm_close)

    async def second_waiter():
        waiting.set()
        await engine.close()

    cancelled = asyncio.create_task(engine.close())
    await entered.wait()
    normal = asyncio.create_task(second_waiter())
    await waiting.wait()
    cancelled.cancel()
    await asyncio.sleep(0)
    cancelled.cancel()
    await asyncio.sleep(0)
    assert not cancelled.done() and not normal.done()
    with pytest.raises(RuntimeError, match="closed"):
        await engine.run("cannot run")
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    if failure is asyncio.CancelledError:
        with pytest.raises(RuntimeError, match="interrupted"):
            await normal
        with pytest.raises(RuntimeError, match="interrupted"):
            await engine.close()
    else:
        await normal
        await engine.close()
    assert log[-2:] == ["close:b", "close:a"]
    llm_close.assert_awaited_once()
    assert engine.llm._client.is_closed()
    assert engine._close_task.done()
    assert "SECRET_SENTINEL" not in caplog.text


async def test_anyio_cancelled_close_finishes_before_scope_exits(tmp_path, monkeypatch):
    entered, finish, scoped, exited = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    a = LifecycleCapability("a", [])

    async def held_close():
        entered.set()
        await finish.wait()

    a.close = held_close
    engine = await AgentEngine.create(llm_settings(), make_config(tmp_path, monkeypatch, [a]))
    scope = CancelScope()

    async def closer():
        with scope:
            scoped.set()
            await engine.close()
            await asyncio.sleep(0)  # AnyIO delivers pending cancellation after shield exits.
        exited.set()

    closing = asyncio.create_task(closer())
    await scoped.wait()
    await entered.wait()
    scope.cancel()
    await asyncio.sleep(0)
    assert not exited.is_set()
    finish.set()
    await closing
    assert scope.cancelled_caught and engine.llm._client.is_closed()


@pytest.mark.parametrize("caller_cancelled", [False, True])
@pytest.mark.parametrize("internal_cancelled", [False, True])
async def test_wait_distinguishes_internal_failure_from_waiter_cancel(
    caller_cancelled, internal_cancelled, caplog
):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def cleanup():
        entered.set()
        await finish.wait()
        if internal_cancelled:
            raise asyncio.CancelledError()
        raise ValueError("SECRET_SENTINEL")

    task = asyncio.create_task(cleanup())
    waiter = asyncio.create_task(wait_for_cleanup(task))
    await entered.wait()
    if caller_cancelled:
        waiter.cancel()
        await asyncio.sleep(0)
        assert not waiter.done()
    finish.set()
    expected = (
        asyncio.CancelledError
        if caller_cancelled
        else RuntimeError
        if internal_cancelled
        else ValueError
    )
    with pytest.raises(expected):
        await asyncio.wait_for(waiter, 2)
    assert task.done()
    assert "SECRET_SENTINEL" not in caplog.text


async def test_internal_engine_task_cancel_still_closes_remaining(tmp_path, monkeypatch):
    log = []
    entered = asyncio.Event()
    a, b = LifecycleCapability("a", log), LifecycleCapability("b", log)

    async def held_close():
        log.append("close:b")
        entered.set()
        await asyncio.Event().wait()

    b.close = held_close
    engine = await AgentEngine.create(llm_settings(), make_config(tmp_path, monkeypatch, [a, b]))
    closing = asyncio.create_task(engine.close())
    await entered.wait()
    engine._close_task.cancel()  # Fault injection, not a supported caller operation.
    with pytest.raises(RuntimeError, match="interrupted"):
        await asyncio.wait_for(closing, 2)
    assert log[-2:] == ["close:b", "close:a"]
    assert engine.llm._client.is_closed()


async def test_start_failure_preserved_when_cleanup_cancels(tmp_path, monkeypatch, caplog):
    log = []
    a, b = LifecycleCapability("a", log), LifecycleCapability("b", log, fail=True)
    closed = asyncio.Event()

    async def cancelled_close():
        log.append("close:b")
        raise asyncio.CancelledError()

    b.close = cancelled_close
    original_init = AgentEngine._init_llm

    def observed_init(self):
        llm = original_init(self)
        original = llm._client.close

        async def close():
            await original()
            closed.set()

        monkeypatch.setattr(llm._client, "close", close)
        return llm

    monkeypatch.setattr(AgentEngine, "_init_llm", observed_init)
    with pytest.raises(ValueError, match="startup failed"):
        await AgentEngine.create(llm_settings(), make_config(tmp_path, monkeypatch, [a, b]))
    assert closed.is_set() and log[-2:] == ["close:b", "close:a"]
    assert "Startup rollback failed (RuntimeError)" in caplog.text


async def test_simultaneous_internal_and_waiter_cancel_prefers_waiter_cancel():
    entered = asyncio.Event()

    async def cleanup():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(cleanup())
    waiter = asyncio.create_task(wait_for_cleanup(task))
    await entered.wait()
    task.cancel()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert task.cancelled()
