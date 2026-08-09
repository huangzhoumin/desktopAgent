"""LLM HTTP client retries transient TLS/network errors."""

from __future__ import annotations

import httpx

from desktop_agent.config import LlmConfig
from desktop_agent.planner.llm_client import OpenAICompatibleClient, _is_retryable_request_error


def test_ssl_eof_is_retryable():
    err = httpx.ReadError(
        "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol (_ssl.c:1006)"
    )
    assert _is_retryable_request_error(err)


def test_chat_retries_then_succeeds(monkeypatch):
    cfg = LlmConfig(
        api_base="https://api.example.com",
        model="m",
        api_key="k",
        max_retries=3,
        timeout_s=5,
    )
    client = OpenAICompatibleClient(cfg)
    calls = {"n": 0}

    class BoomThenOkTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ReadError(
                    "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"
                )
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {"name": "list_windows", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
            return httpx.Response(200, json=payload)

    def fake_client(*, timeout=None):
        return httpx.Client(
            transport=BoomThenOkTransport(),
            timeout=timeout or 5.0,
            trust_env=False,
        )

    monkeypatch.setattr(client, "_client", fake_client)
    monkeypatch.setattr("desktop_agent.planner.llm_client.time.sleep", lambda *_a, **_k: None)

    msg = client.chat([{"role": "user", "content": "hi"}], tools=None)
    assert calls["n"] == 3
    assert msg["tool_calls"][0]["function"]["name"] == "list_windows"


def test_chat_does_not_retry_http_400(monkeypatch):
    cfg = LlmConfig(
        api_base="https://api.example.com",
        model="m",
        api_key="k",
        max_retries=3,
        timeout_s=5,
    )
    client = OpenAICompatibleClient(cfg)
    calls = {"n": 0}

    class BadRequestTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(400, text="bad request")

    monkeypatch.setattr(
        client,
        "_client",
        lambda *, timeout=None: httpx.Client(
            transport=BadRequestTransport(),
            timeout=timeout or 5.0,
            trust_env=False,
        ),
    )
    monkeypatch.setattr("desktop_agent.planner.llm_client.time.sleep", lambda *_a, **_k: None)

    try:
        client.chat([{"role": "user", "content": "hi"}], tools=None)
        assert False, "expected LlmClientError"
    except Exception as e:
        assert "LLM HTTP 400" in str(e)
    assert calls["n"] == 1
