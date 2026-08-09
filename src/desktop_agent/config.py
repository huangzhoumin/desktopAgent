from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "agent.yaml"
DEFAULT_WHITELIST = ROOT / "configs" / "apps.whitelist.yaml"
DEFAULT_ENV_FILE = ROOT / ".env"


def load_dotenv(path: Path | None = None, *, override: bool = False) -> None:
    """Load KEY=VALUE pairs from a local .env into os.environ (no extra dependency)."""
    env_path = path or DEFAULT_ENV_FILE
    if not env_path.exists():
        return
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value


@dataclass
class BrowserConfig:
    mode: str = "attach"
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 9222
    fallback_to_controlled: bool = True
    controlled_channel: str = "chrome"  # chrome | msedge
    controlled_user_data_dir: str = "data/browser-controlled/chrome"


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
    # Extra attempts after the first try for transient TLS/network flaps.
    max_retries: int = 3
    # httpx trust_env: honor HTTP(S)_PROXY. Set false if a broken proxy causes SSL EOF.
    trust_env: bool = True
    # DeepSeek V4: set False to disable thinking (faster tool-calling).
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
class PerceptionConfig:
    uia_max_nodes: int = 400
    enable_ocr_fallback: bool = False
    enable_vlm_fallback: bool = False
    min_confidence_to_act: float = 0.75
    ocr_engine: str = "auto"  # auto | rapidocr | windows
    vlm_model: str = ""  # optional multimodal model override


@dataclass
class AgentConfig:
    raw: dict[str, Any] = field(default_factory=dict)
    traces_dir: Path = field(default_factory=lambda: ROOT / "traces")
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    llm: LlmConfig = field(default_factory=LlmConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    perception: PerceptionConfig = field(default_factory=PerceptionConfig)
    uia_max_nodes: int = 400
    screenshot_every_step: bool = True
    whitelist: dict[str, str] = field(default_factory=dict)  # process_lower -> alias

    @property
    def cdp_endpoint(self) -> str:
        return f"http://{self.browser.cdp_host}:{self.browser.cdp_port}"

    @property
    def min_confidence_to_act(self) -> float:
        return self.perception.min_confidence_to_act


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
    load_dotenv()
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

    # Prefer process/.env over yaml so secrets stay out of agent.yaml.
    api_key = str(os.environ.get("DESKTOP_AGENT_API_KEY") or llm_raw.get("api_key") or "")
    max_rounds = int(llm_raw.get("max_tool_rounds", 40))
    think_raw = llm_raw.get("think", None)
    think: bool | None
    if think_raw is None:
        think = None
    else:
        think = bool(think_raw)
    trust_env_raw = os.environ.get("DESKTOP_AGENT_HTTP_TRUST_ENV")
    if trust_env_raw is None:
        trust_env = bool(llm_raw.get("trust_env", True))
    else:
        trust_env = str(trust_env_raw).strip().lower() not in {"0", "false", "no", "off"}

    return AgentConfig(
        raw=raw,
        traces_dir=traces,
        browser=BrowserConfig(
            mode=str(browser_raw.get("mode", "attach")),
            cdp_host=str(browser_raw.get("cdp_host", "127.0.0.1")),
            cdp_port=int(browser_raw.get("cdp_port", 9222)),
            fallback_to_controlled=bool(browser_raw.get("fallback_to_controlled", True)),
            controlled_channel=str(browser_raw.get("controlled_channel", "chrome")),
            controlled_user_data_dir=str(
                browser_raw.get("controlled_user_data_dir", "data/browser-controlled/chrome")
            ),
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
            max_retries=int(llm_raw.get("max_retries", 3)),
            trust_env=trust_env,
            think=think,
        ),
        runtime=RuntimeConfig(
            step_timeout_ms=int(runtime_raw.get("step_timeout_ms", 30000)),
            max_retries_per_step=int(runtime_raw.get("max_retries_per_step", 3)),
            screenshot_every_step=bool(runtime_raw.get("screenshot_every_step", True)),
            max_steps=int(runtime_raw.get("max_steps", max_rounds)),
        ),
        perception=PerceptionConfig(
            uia_max_nodes=int(perception.get("uia_max_nodes", 400)),
            enable_ocr_fallback=bool(perception.get("enable_ocr_fallback", False)),
            enable_vlm_fallback=bool(perception.get("enable_vlm_fallback", False)),
            min_confidence_to_act=float(perception.get("min_confidence_to_act", 0.75)),
            ocr_engine=str(perception.get("ocr_engine", "auto")),
            vlm_model=str(perception.get("vlm_model", "") or ""),
        ),
        uia_max_nodes=int(perception.get("uia_max_nodes", 400)),
        screenshot_every_step=bool(runtime_raw.get("screenshot_every_step", True)),
        whitelist=load_whitelist(whitelist_path),
    )