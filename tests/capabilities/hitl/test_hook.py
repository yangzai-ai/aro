"""Tool hooks preserve ordering and protect against authorization failures."""

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from capabilities.hitl.approval_manager import ApprovalManager
from capabilities.hitl.capability import HITLCapability
from capabilities.hitl.hook import HITLHook
from core.kernel.loop import KernelLoop
from core.kernel.state import AgentState
from core.kernel.trace import TraceHook
from core.tools.base import ToolResult
from projects.helpdesk.repositories.mock import MockRepository
from projects.helpdesk.tools.update_ticket import UpdateTicketTool
from tests.test_kernel_loop import FakeLLMProvider, FakeTool, SpyHook


@pytest.mark.parametrize("approved", [True, False])
async def test_write_approval_and_state(approved):
    repo = MockRepository()
    tool = UpdateTicketTool(repo)
    manager = ApprovalManager()
    manager.request_approval = AsyncMock(return_value=approved)
    hook = HITLHook(manager)
    trace = TraceHook()
    args = {"ticket_id": "TKT-001", "status": "resolved"}
    loop = KernelLoop(
        FakeLLMProvider("", [{"id": "a", "name": tool.name, "args": args}]),
        [tool],
        [trace, hook],
    )
    await loop.run_once(
        AgentState(messages=[HumanMessage(content="approve")], context={"thread_id": "s"})
    )
    manager.request_approval.assert_awaited_once_with(tool.name, args, "s")
    assert repo.tickets[0].status == ("resolved" if approved else "open")
    call = trace.get_latest_trace().tool_calls[0]
    assert call.success is approved
    if not approved:
        assert call.error == "User rejected the operation"


async def test_read_and_unknown_do_not_prompt():
    manager = ApprovalManager()
    manager.request_approval = AsyncMock(side_effect=AssertionError("must not prompt"))
    hook = HITLHook(manager, {"fake_tool": FakeTool()})
    state = AgentState(messages=[])
    assert await hook.before_tool("fake_tool", {}, state) == (True, None)
    assert await hook.before_tool("missing", {}, state) == (True, None)
    assert await hook.before(state) is state
    assert await hook.after(state) is state
    assert await hook.after_tool("fake_tool", {}, ToolResult(True), state) is state


async def test_permission_guard_fails_closed_on_exception():
    tool = UpdateTicketTool(MockRepository())
    manager = ApprovalManager()
    manager.request_approval = AsyncMock(side_effect=RuntimeError("approval unavailable"))
    trace = TraceHook()
    loop = KernelLoop(
        FakeLLMProvider(
            "",
            [
                {
                    "id": "a",
                    "name": tool.name,
                    "args": {"ticket_id": "TKT-001", "status": "resolved"},
                }
            ],
        ),
        [tool],
        [trace, HITLHook(manager)],
    )
    state = await loop.run_once(AgentState(messages=[]))
    assert "authorization failed" in state["messages"][-1].content
    assert tool.repository.tickets[0].status == "open"
    assert not trace.get_latest_trace().tool_calls[0].success


async def test_tool_hooks_both_forward_round_after_reverse():
    log = []

    class Hooks(SpyHook):
        async def before(self, state):
            log.append(f"{self.name}.before")
            return state

        async def after(self, state):
            log.append(f"{self.name}.after")
            return state

        async def before_tool(self, name, args, state):
            log.append(f"{self.name}.before_tool")
            return True, None

        async def after_tool(self, name, args, result, state):
            log.append(f"{self.name}.after_tool")
            return state

    loop = KernelLoop(
        FakeLLMProvider("", [{"id": "a", "name": "fake_tool", "args": {}}]),
        [FakeTool()],
        [Hooks("a"), Hooks("b")],
    )
    await loop.run_once(AgentState(messages=[]))
    assert log == [
        "a.before",
        "b.before",
        "a.before_tool",
        "b.before_tool",
        "a.after_tool",
        "b.after_tool",
        "b.after",
        "a.after",
    ]


def test_capability_and_invalid_settings():
    capability = HITLCapability({"mode": "cli"})
    assert capability.name == "hitl" and capability.tools() == []
    assert isinstance(capability.hooks()[0], HITLHook)
    assert HITLCapability({"mode": "api"}).approval_manager.mode == "api"
    with pytest.raises(ValueError):
        HITLCapability({"mode": "invalid"})
