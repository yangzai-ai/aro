"""Create isolated API applications with actual engines and a fake stream."""

import httpx
import pytest
import yaml

from api.app import create_app
from api.settings import APISettings
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
from tests.api.helpers import StreamingProvider


@pytest.fixture
async def service(tmp_path):
    path = tmp_path / "agent.yml"
    path.write_text(
        yaml.safe_dump(
            {
                "capabilities": ["helpdesk_tools", "hitl"],
                "capability_config": {
                    "helpdesk_tools": {"module": "projects.helpdesk.capability"},
                    "hitl": {"mode": "api", "timeout_seconds": 1},
                },
            }
        ),
        encoding="utf-8",
    )

    def factory():
        engine = AgentEngine(
            LLMConfig(
                OPENAI_API_KEY="BACKEND-SECRET-SENTINEL", OPENAI_API_BASE="https://unused.test"
            ),
            AgentConfig(path),
        )
        engine.loop.llm = StreamingProvider()
        return engine

    application = create_app(APISettings(_env_file=None, token=None), factory)
    async with (
        application.router.lifespan_context(application),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://test"
        ) as client,
    ):
        yield application, client
