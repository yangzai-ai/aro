"""Index configured Markdown docs: python scripts/index_helpdesk_docs.py [--config ...]."""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capabilities.knowledge.capability import KnowledgeCapability, KnowledgeConfig  # noqa: E402
from capabilities.knowledge.indexer import index_documents  # noqa: E402
from core.config_loader import load_agent_config  # noqa: E402


async def main() -> None:
    """Use precisely the same model, collection and path as the agent configuration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("projects/helpdesk/config/agent.yml"))
    parser.add_argument("--device", help="Override cuda with cpu when needed")
    args = parser.parse_args()
    settings = dict(load_agent_config(args.config).capability_config.get("knowledge", {}))
    settings.pop("module", None)
    if args.device:
        settings["device"] = args.device
    config = KnowledgeConfig.model_validate(settings)
    if config.qdrant_path == ":memory:":
        parser.error("The index script requires a disk path so the next CLI process can read it")
    logging.basicConfig(level=logging.INFO)
    capability = KnowledgeCapability(settings)
    try:
        await capability.start()
        database = capability.vectordb
        count = await index_documents(
            config.docs_dir,
            config.collection_name,
            capability.embedding_model,
            database,
            config.chunk_size,
            config.chunk_overlap,
            config.batch_size,
        )
        print(
            f"Indexed {count} chunks into {config.collection_name} ({'remote' if config.qdrant_url else 'local'})"
        )
    finally:
        await capability.close()


if __name__ == "__main__":
    asyncio.run(main())
