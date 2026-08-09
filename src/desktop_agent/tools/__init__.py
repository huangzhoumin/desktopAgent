from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from desktop_agent.tools.runtime import ToolRuntime

__all__ = ["ToolRuntime"]


def __getattr__(name: str):
    if name == "ToolRuntime":
        from desktop_agent.tools.runtime import ToolRuntime as _ToolRuntime

        return _ToolRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
