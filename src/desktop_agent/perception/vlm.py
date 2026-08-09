"""VLM fallback: locate UI targets from a screenshot + natural language query."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
        return {"ok": True, "model": self.model, "api_base": self.llm.api_base}

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

        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        messages = [
            {"role": "system", "content": _LOCATE_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Query: {query}\nReturn up to {top_k} matches."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ]

        client = self._get_client()
        # Temporarily override model if perception.vlm_model is set.
        original_model = client.config.model
        try:
            if self.model:
                client.config.model = self.model
            message = client.chat(messages, tools=None, tool_choice="none")
        except Exception as e:
            raise VlmError(str(e)) from e
        finally:
            client.config.model = original_model

        content = message.get("content") or ""
        if isinstance(content, list):
            # Some providers return multimodal content arrays.
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(parts)

        data = _parse_json_object(str(content))
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
