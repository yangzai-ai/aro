"""Dynamic loading of configured capability plugins.

The loader is the boundary between declarative project configuration and the
core kernel.  It imports only the capabilities named by the configuration and
returns them through the :class:`Capability` contract.
"""

import importlib
from collections.abc import Mapping
from typing import Any

from core.kernel.capability import Capability


class PluginLoadError(RuntimeError):
    """Raised when a configured capability cannot be loaded."""


class PluginLoader:
    """Load capability factories using the conventional plugin layout."""

    def __init__(self, package: str = "capabilities") -> None:
        """Create a loader for a capability package."""
        self.package = package

    def load(
        self,
        names: list[str],
        configurations: Mapping[str, Any] | None = None,
    ) -> list[Capability]:
        """Load configured capabilities in configuration order.

        Each plugin must expose ``get_capability(config)`` from
        ``<package>.<name>.capability``.  Imports happen inside this method so
        disabled capabilities are never imported.
        """
        config_map = configurations or {}
        loaded: list[Capability] = []
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise PluginLoadError("Capability names must be non-empty strings")
            plugin_config = dict(config_map.get(name, {}))
            module_name = plugin_config.pop("module", f"{self.package}.{name}.capability")
            if not isinstance(module_name, str) or not module_name:
                raise PluginLoadError(f"Capability '{name}' module must be a non-empty string")
            try:
                module = importlib.import_module(module_name)
            except (ImportError, ModuleNotFoundError) as exc:
                raise PluginLoadError(
                    f"Unable to import capability '{name}' from '{module_name}'"
                ) from exc

            factory = getattr(module, "get_capability", None)
            if not callable(factory):
                raise PluginLoadError(
                    f"Capability '{name}' must export callable get_capability(config)"
                )
            try:
                capability = factory(plugin_config)
            except Exception as exc:
                raise PluginLoadError(f"Failed to initialize capability '{name}'") from exc
            if not isinstance(capability, Capability):
                raise PluginLoadError(
                    f"Capability factory '{module_name}.get_capability' returned "
                    f"{type(capability).__name__}, expected Capability"
                )
            loaded.append(capability)
        return loaded


def load_capabilities(
    names: list[str],
    configurations: Mapping[str, Any] | None = None,
    package: str = "capabilities",
) -> list[Capability]:
    """Convenience wrapper for loading configured capabilities."""
    return PluginLoader(package=package).load(names, configurations)
