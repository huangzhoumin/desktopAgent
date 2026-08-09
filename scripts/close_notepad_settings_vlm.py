"""One-shot: VLM-detect Notepad Settings page, then close the window (discard)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from desktop_agent.adapters.notepad import NotepadAdapter  # noqa: E402
from desktop_agent.config import load_config  # noqa: E402
from desktop_agent.perception.vlm import VlmLocator  # noqa: E402


def main() -> int:
    cfg = load_config()
    vlm = VlmLocator(cfg.llm, model=cfg.perception.vlm_model or None)
    probe = vlm.probe()
    if not probe.get("ok"):
        print(json.dumps({"ok": False, "error": "VLM unavailable", "probe": probe}, ensure_ascii=False))
        return 2
    np = NotepadAdapter()
    result = np.close_if_settings_vlm(vlm)
    payload = result.to_dict() if hasattr(result, "to_dict") else {"ok": True, "detail": result}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
