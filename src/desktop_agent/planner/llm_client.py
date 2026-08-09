"""OpenAI-compatible chat completions client (tool calling)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from desktop_agent.config import LlmConfig
from desktop_agent.errors import AgentError


class LlmClientError(AgentError):
    code = "LLM_ERROR"


class OpenAICompatibleClient:
    def __init__(self, config: LlmConfig):
        self.config = config

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict[str, Any] = "auto",
    ) -> dict[str, Any]:
        if not self.config.configured:
            raise LlmClientError(
                "LLM not configured. Set llm.api_base, llm.model in agent.yaml "
                "and DESKTOP_AGENT_API_KEY in the environment."
            )

        url = f"{self.config.api_base}/chat/completions"
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        self._apply_thinking_controls(body)

        headers = {
            "Authorization": f"Bearer {self.config.api_key or 'ollama'}",
            "Content-Type": "application/json",
        }
        # Local tool-calling can exceed a short read timeout (esp. cold model load).
        timeout = httpx.Timeout(
            connect=min(30.0, self.config.timeout_s),
            read=self.config.timeout_s,
            write=min(30.0, self.config.timeout_s),
            pool=min(30.0, self.config.timeout_s),
        )
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as e:
            raise LlmClientError(
                f"LLM request timed out after {self.config.timeout_s:.0f}s "
                f"(model={self.config.model}). Increase llm.timeout_s or disable thinking."
            ) from e
        except httpx.HTTPStatusError as e:
            detail = e.response.text[:500] if e.response is not None else str(e)
            raise LlmClientError(f"LLM HTTP {e.response.status_code}: {detail}") from e
        except httpx.HTTPError as e:
            raise LlmClientError(f"LLM request failed: {e}") from e

        choices = data.get("choices") or []
        if not choices:
            raise LlmClientError("LLM returned no choices")
        return choices[0].get("message") or {}

    def _apply_thinking_controls(self, body: dict[str, Any]) -> None:
        """Disable/enable model thinking. Ollama /v1 ignores bare `think` for Qwen3."""
        if self.config.think is None:
            return
        body["think"] = self.config.think
        # OpenAI-compatible Ollama mapping for Qwen3/etc.
        body["reasoning_effort"] = "none" if not self.config.think else "medium"

    def probe(self) -> dict[str, Any]:
        """Lightweight connectivity check for doctor."""
        if not self.config.api_base:
            return {"ok": False, "error": "llm.api_base empty"}
        if not self.config.model:
            return {"ok": False, "error": "llm.model empty"}
        if not self.config.api_key:
            return {"ok": False, "error": "DESKTOP_AGENT_API_KEY / llm.api_key empty"}

        url = f"{self.config.api_base}/models"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        try:
            with httpx.Client(timeout=min(15.0, self.config.timeout_s)) as client:
                resp = client.get(url, headers=headers)
                if resp.status_code >= 400:
                    # Some gateways don't expose /models; try a tiny chat.
                    return self._probe_chat()
                return {"ok": True, "endpoint": self.config.api_base, "model": self.config.model}
        except httpx.HTTPError:
            return self._probe_chat()

    def _probe_chat(self) -> dict[str, Any]:
        try:
            msg = self.chat(
                [{"role": "user", "content": "ping"}],
                tools=None,
            )
            content = (msg.get("content") or "")[:80]
            return {
                "ok": True,
                "endpoint": self.config.api_base,
                "model": self.config.model,
                "reply": content,
            }
        except Exception as e:
            return {"ok": False, "error": str(e), "endpoint": self.config.api_base}


def parse_tool_arguments(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise LlmClientError(f"Invalid tool arguments JSON: {e}") from e
        if not isinstance(data, dict):
            raise LlmClientError("Tool arguments must be a JSON object")
        return data
    raise LlmClientError(f"Unexpected tool arguments type: {type(raw)}")

