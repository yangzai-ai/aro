"""Run the helpdesk CLI: python -m projects.helpdesk.main [--offline-demo]."""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage

from core.config_loader import load_agent_config, load_llm_config
from core.kernel.engine import AgentEngine
from core.kernel.state import AgentState
from core.kernel.trace import print_trace
from core.llm.provider import LLMProvider, LLMResponse


class DemoProvider(LLMProvider):
    """Deterministic acceptance driver; does not pretend to be a real LLM."""

    async def chat(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Issue the acceptance query, then summarize its actual tool response."""
        if isinstance(messages[-1], ToolMessage):
            payload = json.loads(str(messages[-1].content))
            return LLMResponse(content=f"当前有 {payload['count']} 个待处理工单（模拟数据）。")
        return LLMResponse(
            content="查询所有待处理的工单。",
            tool_calls=[
                {
                    "id": "demo_query",
                    "name": "query_tickets",
                    "args": {"status": "open"},
                }
            ],
        )

    def chat_stream(
        self,
        messages: list[BaseMessage],
        tools: list[Any] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Offline acceptance uses non-streaming chat only."""
        raise NotImplementedError("Offline demo uses chat()")


async def complete_turn(
    engine: AgentEngine,
    text: str,
    state: AgentState | None,
    *,
    user_id: str | None = None,
    tenant_id: str = "local",
) -> AgentState:
    """Run a bounded tool loop until the model produces its final answer."""
    if state is None:
        source = engine.agent_config.capability_config.get("helpdesk_tools", {}).get(
            "data_source", "mock"
        )
        state = AgentState(
            messages=[
                SystemMessage(
                    content=(
                        f"当前日期：{date.today().isoformat()}\n数据源：{source}\n{engine.agent_config.system_prompt}"
                    )
                )
            ]
        )
        state["context"] = {"tenant_id": tenant_id}
        if user_id is not None:
            state["context"]["user_id"] = user_id
    return await engine.run(text, state)


async def main() -> None:
    """Load project configuration and start an interactive or offline session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Agent YAML (default: helpdesk config)")
    parser.add_argument("--user-id", help="Trusted local user identity for cross-session memory")
    parser.add_argument("--tenant-id", default="local", help="Trusted local tenant identity")
    parser.add_argument(
        "--offline-demo", action="store_true", help="Deterministic fake LLM; no API key/network"
    )
    arguments = parser.parse_args()
    config_path = arguments.config or Path(__file__).parent / "config" / (
        "agent_no_knowledge.yml" if arguments.offline_demo else "agent.yml"
    )
    config = load_agent_config(config_path)
    if arguments.offline_demo and (
        config.capabilities != ["helpdesk_tools"]
        or config.capability_config.get("helpdesk_tools", {}).get("data_source", "mock") != "mock"
        or config.capability_config.get("helpdesk_tools", {}).get("module")
        != "projects.helpdesk.capability"
    ):
        parser.error("--offline-demo requires only the bundled helpdesk_tools mock capability")
    state: AgentState | None = None
    if arguments.offline_demo:
        from core.config_loader import LLMConfig

        # No external requests: _init_llm only constructs a client.
        settings = LLMConfig(OPENAI_API_KEY="offline", OPENAI_API_BASE="http://offline.invalid/v1")
    else:
        settings = load_llm_config()
    engine = await AgentEngine.create(settings, config)
    try:
        if arguments.offline_demo:
            provider = DemoProvider()
            engine.loop.llm = provider
        print("已加载工具：" + ", ".join(tool.name for tool in engine.tools))
        if arguments.offline_demo:
            question = "当前有几个待处理的工单？"
            state = await complete_turn(engine, question, None)
            print(f"用户：{question}\n助手：{state['messages'][-1].content}")
            trace = engine.trace_hook.get_latest_trace()
            if trace:
                print_trace(trace)
            return
        print(
            "输入 quit 退出，/new 开新会话（保留用户身份与进程内 L2）。当前配置的数据源见 capability_config.helpdesk_tools。"
        )
        state = None
        while True:
            try:
                text = input("你：").strip()
                if text.lower() in {"quit", "exit", "q"}:
                    break
                if text == "/new":
                    state = None
                    print("已开始新会话，L1 上下文已清空。")
                    continue
                if text:
                    state = await complete_turn(
                        engine,
                        text,
                        state,
                        user_id=arguments.user_id,
                        tenant_id=arguments.tenant_id,
                    )
                    print(f"助手：{state['messages'][-1].content}")
                    trace = engine.trace_hook.get_latest_trace()
                    if trace:
                        print_trace(trace)
            except (EOFError, KeyboardInterrupt):
                break
            except Exception as exc:
                print(f"本轮执行失败：{exc}")
    finally:
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())
