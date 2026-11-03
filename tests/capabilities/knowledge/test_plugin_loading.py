"""Fresh-interpreter proof that disabling knowledge imports no optional libraries."""

import subprocess
import sys
from pathlib import Path


def test_disabled_plugin_never_imports() -> None:
    """sys.modules is checked in isolation so prior tests cannot hide eager imports."""
    root = Path(__file__).parents[3]
    script = """
import sys
from core.config_loader import AgentConfig, LLMConfig
from core.kernel.engine import AgentEngine
engine = AgentEngine(LLMConfig(OPENAI_API_KEY='fake', OPENAI_API_BASE='https://unused.test'),
                     AgentConfig('projects/helpdesk/config/agent_no_knowledge.yml'))
assert 'search_knowledge_base' not in [tool.name for tool in engine.tools]
assert not any(name.startswith('capabilities.knowledge') for name in sys.modules)
assert not any(name in sys.modules for name in ['torch', 'FlagEmbedding', 'qdrant_client', 'rank_bm25', 'jieba'])
print('disabled: no knowledge imports')
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=root, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "no knowledge imports" in result.stdout


def test_default_api_disabled_import_isolation():
    script = """
import asyncio, sys
from api.app import create_app
from api.settings import APISettings
from core.kernel.engine import AgentEngine
from tests.api.helpers import OpenChat, StreamingProvider
from types import SimpleNamespace
from unittest.mock import AsyncMock
class Provider(StreamingProvider):
    def __init__(self):
        super().__init__()
        self._client = SimpleNamespace(close=AsyncMock())
AgentEngine._init_llm = lambda self: Provider()
async def main():
    app = create_app(APISettings(_env_file=None,token=None))
    async with app.router.lifespan_context(app), OpenChat(app,'query') as stream:
        await stream.until('done')
asyncio.run(main())
assert not any(n.startswith('capabilities.knowledge') for n in sys.modules)
assert not any(n in sys.modules for n in ['torch','FlagEmbedding','qdrant_client','rank_bm25','jieba'])
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
