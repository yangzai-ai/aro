"""Configuration to approval, state mutation and final response in one trace."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import ToolMessage

from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from core.llm.provider import LLMResponse
from tests.test_kernel_loop import FakeLLMProvider


class DecisionProvider(FakeLLMProvider):
    """Issue one write then describe its actual result."""

    async def chat(self, messages, tools=None, **kwargs):
        if isinstance(messages[-1], ToolMessage):
            return LLMResponse(content="结果：" + str(messages[-1].content))
        return LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "write1",
                    "name": "update_ticket",
                    "args": {"ticket_id": "TKT-001", "status": "resolved"},
                }
            ],
        )


def make_engine(tmp_path, monkeypatch, enabled=True):
    path = tmp_path / "agent.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "capabilities": ["hitl", "helpdesk_tools"] if enabled else ["helpdesk_tools"],
                "capability_config": {"helpdesk_tools": {"module": "projects.helpdesk.capability"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(AgentEngine, "_init_llm", lambda self: DecisionProvider(""))
    return AgentEngine(
        LLMConfig(OPENAI_API_KEY="fake", OPENAI_API_BASE="http://unused.test"), AgentConfig(path)
    )


@pytest.mark.parametrize("answer,expected", [("y", "resolved"), ("n", "open")])
async def test_cli_decision_to_final_trace(tmp_path, monkeypatch, answer, expected):
    engine = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda _: answer)
    state = await engine.run("更新工单 TKT-001")
    repo = next(c.repository for c in engine.capabilities if c.name == "helpdesk_tools")
    assert repo.tickets[0].status == expected
    traces = engine.trace_hook.get_all_traces()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.agent_response == state["messages"][-1].content
    assert trace.thread_id == state["context"]["thread_id"]
    assert trace.user_input == "更新工单 TKT-001" and len(trace.tool_calls) == 1
    assert trace.tool_calls[0].success is (answer == "y")
    assert trace.duration_ms >= trace.tool_calls[0].duration_ms >= 0
    json.dumps(trace.to_dict(), ensure_ascii=False)


async def test_disabled_hitl_refuses_write(tmp_path, monkeypatch):
    engine = make_engine(tmp_path, monkeypatch, enabled=False)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("unexpected prompt"))
    state = await engine.run("更新工单 TKT-001")
    assert "HITL" in state["messages"][-1].content
    assert engine.capabilities[0].repository.tickets[0].status == "open"
    assert not engine.trace_hook.get_latest_trace().tool_calls[0].success


def test_disabled_import_isolation():
    code = """
import sys
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from projects.helpdesk.main import DemoProvider
AgentEngine._init_llm = lambda self: DemoProvider()
AgentEngine(LLMConfig(OPENAI_API_KEY="test", OPENAI_API_BASE="http://unused.test"), AgentConfig("projects/helpdesk/config/agent_no_knowledge.yml"))
assert not any(name.startswith("capabilities.hitl") for name in sys.modules)
assert "psycopg" not in sys.modules
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[3],
        check=True,
        capture_output=True,
        text=True,
    )
