"""The harness adapters and the adapter registry.

An adapter implements the harness port in `delegate.ports.harness`. Adapters
register by name. The workflow names the adapter, and the command-line driver
looks it up here and hands it to the engine. The engine never imports this
package. A test registers a scripted adapter through the same interface, so the
engine runs end to end with no model.
"""

from __future__ import annotations

from ..ports.harness import Adapter

_REGISTRY: dict[str, Adapter] = {}


def register(name: str, adapter: Adapter) -> None:
    """Register an adapter under a name. Raise ValueError when the name is taken."""
    if name in _REGISTRY:
        raise ValueError(f"adapter {name!r} is already registered")
    _REGISTRY[name] = adapter


def unregister(name: str) -> None:
    """Remove an adapter. Raise KeyError when no adapter has the name."""
    del _REGISTRY[name]


def get(name: str) -> Adapter:
    """The adapter under the name. Raise KeyError when there is none.

    A registered adapter comes first. The built-in adapters follow; each one is
    imported when it is first asked for.
    """
    if name in _REGISTRY:
        return _REGISTRY[name]
    if name == "claude-code":
        from .claude_code import ClaudeCodeAdapter

        return ClaudeCodeAdapter()
    if name == "opencode":
        from .opencode import OpenCodeAdapter

        return OpenCodeAdapter()
    raise KeyError(name)


def registered_names() -> list[str]:
    return sorted(_REGISTRY)
