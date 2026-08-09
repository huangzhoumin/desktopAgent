"""OpenAI-compatible chat completions client (tool calling)."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from desktop_agent.config import LlmConfig
from desktop_agent.errors import AgentError


class LlmClientError(AgentError):
    code = "LLM_ERROR"


# Transient network / TLS failures that often succeed on a fresh connection.
_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 529}


def _is_retryable_request_error(exc: BaseException) -> bool:
    """Return True for flaky transport/TLS errors (e.g. SSL UNEXPECTED_EOF)."""
    if isinstance(exc, httpx.TimeoutException):
        # Connect-level flaps are worth one more try; long read timeouts are not.
        return isinstance(exc, httpx.ConnectTimeout)
    if isinstance(exc, (httpx.NetworkError, httpx.ProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code if exc.response is not None else 0
        return status in _RETRYABLE_STATUS
    text = str(exc).lower()
    markers = (
        "unexpected_eof",
        "eof occurred in violation of protocol",
        "connection reset",
        "connection aborted",
        "broken pipe",
        "server disconnected",
        "ssl",
        "tls",
    )
    return any(m in text for m in markers)


class OpenAICompatibleClient:
    def __init__(self, config: LlmConfig):
        self.config = config

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        tool_choice: str | dict[str, Any] = "auto",
        max_tokens: int | None = None,
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
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        self._apply_thinking_controls(body)

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            data = self._request_json("POST", url, headers=headers, json_body=body)
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
        """DeepSeek V4 thinking controls (OpenAI-compatible Chat Completions)."""
        if self.config.think is None:
            return
        if self.config.think:
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = "high"
        else:
            body["thinking"] = {"type": "disabled"}

    def _http_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=min(30.0, self.config.timeout_s),
            read=self.config.timeout_s,
            write=min(30.0, self.config.timeout_s),
            pool=min(30.0, self.config.timeout_s),
        )

    def _client(self, *, timeout: httpx.Timeout | float | None = None) -> httpx.Client:
        # Disable keep-alive: stale pooled TLS sockets often surface as
        # SSL: UNEXPECTED_EOF_WHILE_READING against some gateways / proxies.
        limits = httpx.Limits(max_keepalive_connections=0, max_connections=10)
        return httpx.Client(
            timeout=timeout if timeout is not None else self._http_timeout(),
            limits=limits,
            trust_env=self.config.trust_env,
            http2=False,
        )

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        json_body: dict[str, Any] | None = None,
        timeout: httpx.Timeout | float | None = None,
        raise_for_status: bool = True,
    ) -> dict[str, Any]:
        attempts = max(1, int(self.config.max_retries) + 1)
        last_exc: BaseException | None = None
        for attempt in range(attempts):
            try:
                with self._client(timeout=timeout) as client:
                    resp = client.request(method, url, headers=headers, json=json_body)
                    if raise_for_status:
                        resp.raise_for_status()
                    elif resp.status_code >= 400:
                        raise httpx.HTTPStatusError(
                            f"Client error {resp.status_code}",
                            request=resp.request,
                            response=resp,
                        )
                    data = resp.json()
                    if not isinstance(data, dict):
                        raise LlmClientError("LLM returned non-object JSON")
                    return data
            except (httpx.HTTPError, LlmClientError) as e:
                last_exc = e
                if attempt + 1 >= attempts or not _is_retryable_request_error(e):
                    raise
                # Fresh TCP/TLS next attempt; brief backoff for gateway flaps.
                time.sleep(min(2.0 ** attempt, 4.0))
        assert last_exc is not None
        raise last_exc

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
            with self._client(timeout=min(15.0, self.config.timeout_s)) as client:
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
