"""VLM fallback: locate UI targets from a screenshot + natural language query."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from desktop_agent.config import LlmConfig
from desktop_agent.errors import AgentError
from desktop_agent.models import Bounds


class VlmError(AgentError):
    code = "VLM_ERROR"


@dataclass
class VlmMatch:
    label: str
    confidence: float
    bounds: Bounds  # screen coordinates
    notes: str = ""


_LOCATE_PROMPT = """You are a desktop UI vision assistant.
Given a screenshot, find UI targets matching the user query.

Return ONLY valid JSON (no markdown) with this shape:
{
  "matches": [
    {
      "label": "short name of the control/text",
      "x": <int left in screenshot pixels>,
      "y": <int top in screenshot pixels>,
      "w": <int width>,
      "h": <int height>,
      "confidence": <float 0-1>
    }
  ],
  "notes": "optional short note"
}

Rules:
- Coordinates are relative to the provided screenshot (origin top-left).
- Prefer the smallest tight box around the clickable control/text.
- If nothing matches, return {"matches": [], "notes": "..."}.
- Do not narrate. Final answer must be the JSON object only.
"""


class VlmLocator:
    def __init__(self, llm: LlmConfig, *, model: str | None = None):
        self.llm = llm
        self.model = (model or "").strip() or llm.model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from desktop_agent.planner.llm_client import OpenAICompatibleClient

            self._client = OpenAICompatibleClient(self.llm)
        return self._client

    def probe(self) -> dict[str, Any]:
        if not self.llm.configured:
            return {"ok": False, "error": "LLM not configured for VLM"}
        base = self._get_client().probe()
        if not base.get("ok"):
            return {"ok": False, "error": base.get("error"), "model": self.model}
        return {
            "ok": True,
            "model": self.model,
            "api_base": self.llm.api_base,
            "transport": "ollama_native" if self._is_ollama() else "openai_compatible",
        }

    def locate(
        self,
        image_path: str | Path,
        query: str,
        *,
        origin: tuple[int, int] = (0, 0),
        top_k: int = 5,
    ) -> list[VlmMatch]:
        path = Path(image_path)
        if not path.exists():
            raise VlmError(f"Screenshot not found: {path}")
        if not query.strip():
            raise VlmError("vlm query must be non-empty")
        if not self.llm.configured:
            raise VlmError("LLM not configured for VLM")

        user_text = f"{_LOCATE_PROMPT}\n\nQuery: {query}\nReturn up to {top_k} matches."
        try:
            if self._is_ollama():
                content = self._locate_ollama_native(path, user_text)
            else:
                content = self._locate_openai_compatible(path, user_text, top_k=top_k)
        except VlmError:
            raise
        except Exception as e:
            raise VlmError(str(e)) from e

        data = _parse_json_object(content)
        ox, oy = origin
        matches: list[VlmMatch] = []
        for item in (data.get("matches") or [])[:top_k]:
            if not isinstance(item, dict):
                continue
            try:
                x = int(item.get("x"))
                y = int(item.get("y"))
                w = max(1, int(item.get("w") or 1))
                h = max(1, int(item.get("h") or 1))
            except (TypeError, ValueError):
                continue
            conf = item.get("confidence", 0.7)
            try:
                confidence = float(conf)
            except (TypeError, ValueError):
                confidence = 0.7
            matches.append(
                VlmMatch(
                    label=str(item.get("label") or query)[:120],
                    confidence=max(0.0, min(1.0, confidence)),
                    bounds=Bounds(x=ox + x, y=oy + y, w=w, h=h),
                    notes=str(data.get("notes") or ""),
                )
            )
        return matches

    def _is_ollama(self) -> bool:
        base = (self.llm.api_base or "").lower()
        return "11434" in base or "ollama" in base

    def _ollama_root(self) -> str:
        raw = (self.llm.api_base or "").rstrip("/")
        if raw.endswith("/v1"):
            raw = raw[:-3]
        parsed = urlparse(raw)
        if not parsed.scheme:
            return "http://127.0.0.1:11434"
        return f"{parsed.scheme}://{parsed.netloc}"

    def _locate_ollama_native(self, path: Path, user_text: str) -> str:
        """Native /api/chat is more reliable for Qwen3-VL than OpenAI /v1."""
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            "stream": False,
            "think": False,
            "options": {
                "temperature": float(self.llm.temperature),
                "num_predict": 320,
            },
            "messages": [
                {
                    "role": "user",
                    "content": user_text,
                    "images": [b64],
                }
            ],
        }
        url = f"{self._ollama_root()}/api/chat"
        timeout = httpx.Timeout(
            connect=min(30.0, self.llm.timeout_s),
            read=max(self.llm.timeout_s, 180.0),
            write=min(60.0, self.llm.timeout_s),
            pool=min(30.0, self.llm.timeout_s),
        )
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        message = data.get("message") or {}
        return _extract_answer_text(message)

    def _locate_openai_compatible(self, path: Path, user_text: str, *, top_k: int) -> str:
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            }
        ]
        client = self._get_client()
        original_model = client.config.model
        try:
            if self.model:
                client.config.model = self.model
            message = client.chat(
                messages,
                tools=None,
                tool_choice="none",
                max_tokens=512,
            )
        finally:
            client.config.model = original_model
        return _extract_answer_text(message)


def _extract_answer_text(message: dict[str, Any]) -> str:
    """Prefer final content; fall back to thinking/reasoning for Qwen3-VL."""
    parts = [
        _message_text(message.get("content")),
        _message_text(message.get("thinking")),
        _message_text(message.get("reasoning")),
    ]
    # Prefer a part that already contains JSON matches.
    for part in parts:
        if part and "matches" in part:
            return part
    return "\n".join(p for p in parts if p).strip()


def _message_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts).strip()
    return str(value).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise VlmError("VLM returned empty content")
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # Last resort: first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    raise VlmError(f"VLM did not return JSON object: {text[:240]}")
