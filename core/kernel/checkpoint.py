"""Kernel-owned factory for the default graph snapshot backend."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver


def default_checkpointer() -> BaseCheckpointSaver[Any]:
    """Create an independent in-process saver, without enabling recovery."""
    return MemorySaver()
