from __future__ import annotations

from desktop_agent.config import AgentConfig


def resolve_app_alias(process_name: str, config: AgentConfig) -> str:
    alias = config.whitelist.get(process_name.lower())
    if alias:
        return alias
    base = process_name.lower().removesuffix(".exe")
    return base or "unknown"
