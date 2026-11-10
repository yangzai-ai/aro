"""Discover trusted capability entry points without implicitly enabling plugins."""

import logging
from collections.abc import Iterable, Mapping
from importlib.metadata import entry_points
from typing import Any

from core.kernel.capability import Capability
from core.kernel.plugin_loader import PluginLoadError

CAPABILITY_ENTRY_POINT_GROUP = "aro.capabilities"
logger = logging.getLogger(__name__)


def discover_capabilities(
    names: Iterable[str] | None = None,
    configurations: Mapping[str, Any] | None = None,
    *,
    strict: bool = False,
    owned: list[Capability] | None = None,
) -> dict[str, Capability]:
    """Load only selected entries (None means all for explicit discovery).

    An entry exports either a no-argument Capability class (then initialize is
    called with config) or a get_capability(config)-compatible factory. Engine
    passes its ownership list so rollback includes partially initialized objects.
    Selected failures never silently fall back to a different implementation.
    """
    selected = None if names is None else set(names)
    if selected == set():
        return {}
    discovered: dict[str, Capability] = {}
    entries = entry_points().select(group=CAPABILITY_ENTRY_POINT_GROUP)
    attempted: set[str] = set()
    for entry in entries:
        if selected is not None and entry.name not in selected:
            continue
        try:
            if entry.name in attempted:
                raise ValueError("Duplicate capability entry point")
            attempted.add(entry.name)
            exported = entry.load()
            config = dict((configurations or {}).get(entry.name, {}))
            if isinstance(exported, type) and issubclass(exported, Capability):
                instance = exported()
                if owned is not None:
                    owned.append(instance)
                instance.initialize(config)
            else:
                instance = exported(config)
                if not isinstance(instance, Capability):
                    raise TypeError("Entry point must produce Capability")
                if owned is not None:
                    owned.append(instance)
            discovered[entry.name] = instance
        except Exception as exc:
            logger.warning("Capability entry point %s failed (%s)", entry.name, type(exc).__name__)
            if strict:
                raise PluginLoadError(
                    f"Failed to load capability entry point '{entry.name}'"
                ) from exc
    return discovered
