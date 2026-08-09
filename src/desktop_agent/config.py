from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "agent.yaml"
DEFAULT_WHITELIST = ROOT / "configs" / "apps.whitelist.yaml"


@dataclass
class BrowserConfig:
    mode: str = "attach"
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222
    fallback_to_controlled: bool = True


@dataclass
class SafetyConfig:
    confirm_coordinate_clicks: bool = True
    confirm_submit: bool = True
    mask_password_values: bool = True
    enforce_whitelist: bool = True


@dataclass
class LlmConfig:
    provider: str = "openai_compatible"
    model: str = ""
    temperature: float = 0.1
    max_tool_rounds: int = 40
    api_base: str = ""
    api_key: str = ""
    timeout_s: float = 60.0
    # Ollama/Qwen3: set False to prefer direct tool calls over long chain-of-thought.
    think: bool | None = None

    @property
    def configured(self) -> bool:
        return bool(self.api_base and self.model and self.api_key)


@dataclass
class RuntimeConfig:
    step_timeout_ms: int = 30000
    max_retries_per_step: int = 3
    screenshot_every_step: bool = True
    max_steps: int = 40


@dataclass
class AgentConfig:
    raw: dict[str, Any] = field(default_factory=dict)
    traces_dir: Path = field(default_factory=lambda: ROOT / "traces")
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    uia_max_nodes: int = 400
    screenshot_every_step: bool = True
    whitelist: dict[str, str] = field(default_factory=dict)  # process_lower -> alias

    @property
    def cdp_endpoint(self) -> str:
        return f"http://{self.browser.cdp_host}:{self.browser.cdp_port}"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config must be a mapping: {path}")
    return data


def load_whitelist(path: Path | None = None) -> dict[str, str]:
    data = _load_yaml(path or DEFAULT_WHITELIST)
    mapping: dict[str, str] = {}
    for item in data.get("allowed_apps", []):
        process = str(item.get("process", "")).lower()
        alias = str(item.get("alias", process))
        if process:
            mapping[process] = alias
    return mapping


def load_config(
    config_path: Path | None = None,
    whitelist_path: Path | None = None,
) -> AgentConfig:
    raw = _load_yaml(config_path or DEFAULT_CONFIG)
    perception = raw.get("perception", {})
    runtime_raw = raw.get("runtime", {})
    browser_raw = raw.get("browser", {})
    safety_raw = raw.get("safety", {})
    llm_raw = raw.get("llm", {})
    paths = raw.get("paths", {})

    traces = Path(paths.get("traces_dir", "traces"))
    if not traces.is_absolute():
        traces = ROOT / traces

    api_key = str(llm_raw.get("api_key") or os.environ.get("DESKTOP_AGENT_API_KEY") or "")
    max_rounds = int(llm_raw.get("max_tool_rounds", 40))
    think_raw = llm_raw.get("think", None)
    think: bool | None
    if think_raw is None:
        think = None
    else:
        think = bool(think_raw)

    return AgentConfig(
        raw=raw,
        traces_dir=traces,
        browser=BrowserConfig(
            mode=str(browser_raw.get("mode", "attach")),
            cdp_host=str(browser_raw.get("cdp_host", "127.0.0.1")),
            cdp_port=int(browser_raw.get("cdp_port", 9222)),
            fallback_to_controlled=bool(browser_raw.get("fallback_to_controlled", True)),
        ),
        safety=SafetyConfig(
            confirm_coordinate_clicks=bool(safety_raw.get("confirm_coordinate_clicks", True)),
            confirm_submit=bool(safety_raw.get("confirm_submit", True)),
            mask_password_values=bool(safety_raw.get("mask_password_values", True)),
            enforce_whitelist=bool(safety_raw.get("enforce_whitelist", True)),
        ),
        llm=LlmConfig(
            provider=str(llm_raw.get("provider", "openai_compatible")),
            model=str(llm_raw.get("model", "")),
            temperature=float(llm_raw.get("temperature", 0.1)),
            max_tool_rounds=max_rounds,
            api_base=str(llm_raw.get("api_base", "")).rstrip("/"),
            api_key=api_key,
            timeout_s=float(llm_raw.get("timeout_s", 60.0)),
            think=think,
        ),
        runtime=RuntimeConfig(
            step_timeout_ms=int(runtime_raw.get("step_timeout_ms", 30000)),
            max_retries_per_step=int(runtime_raw.get("max_retries_per_step", 3)),
            screenshot_every_step=bool(runtime_raw.get("screenshot_every_step", True)),
            max_steps=int(runtime_raw.get("max_steps", max_rounds)),
        ),
        uia_max_nodes=int(perception.get("uia_max_nodes", 400)),
        screenshot_every_step=bool(runtime_raw.get("screenshot_every_step", True)),
        whitelist=load_whitelist(whitelist_path),
    )