from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from desktop_agent.config import AgentConfig
from desktop_agent.errors import PermissionDenied
from desktop_agent.models import ToolCall, UIElement, WindowInfo

_SUBMIT_HINTS = (
    "submit",
    "提交",
    "确认支付",
    "pay",
    "delete",
    "删除",
    "uninstall",
    "卸载",
)

# Win11 Notepad gear — clicking it strands the agent in Settings.
_NOTEPAD_SETTINGS_HINTS = (
    "设置",
    "settings",
    "app theme",
    "应用主题",
    "外观",
    "appearance",
)


@dataclass
class PolicyDecision:
    allow: bool
    require_confirm: bool = False
    reason: str = ""
    reject: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "allow": self.allow,
            "require_confirm": self.require_confirm,
            "reason": self.reason,
            "reject": self.reject,
        }


class SafetyGuard:
    def __init__(self, config: AgentConfig):
        self.config = config

    def alias_for_process(self, process_name: str) -> str | None:
        return self.config.whitelist.get(process_name.lower())

    def is_allowed_process(self, process_name: str) -> bool:
        if not self.config.safety.enforce_whitelist:
            return True
        return process_name.lower() in self.config.whitelist

    def assert_window_allowed(self, window: WindowInfo) -> None:
        if not self.is_allowed_process(window.process):
            raise PermissionDenied(
                f"Process not in whitelist: {window.process} ({window.title})"
            )

    def mask_value(self, role: str, name: str, value: str) -> str:
        if not self.config.safety.mask_password_values:
            return value
        blob = f"{role} {name}".lower()
        if "password" in blob or "密码" in blob:
            return f"*** ({len(value)} chars)"
        return value

    def check_tool(self, call: ToolCall, *, element: UIElement | None = None) -> PolicyDecision:
        """Policy gate before executing a runtime tool."""
        name = call.name
        args = call.arguments or {}

        if name == "click":
            target = args.get("target", args.get("element_id"))
            if isinstance(target, dict) and "x" in target and "y" in target:
                if self.config.safety.confirm_coordinate_clicks:
                    return PolicyDecision(
                        allow=True,
                        require_confirm=True,
                        reason="Coordinate click requires confirmation",
                    )
            if element is not None:
                el_blob = f"{element.name} {element.role} {element.automation_id}".lower()
                if any(h in el_blob for h in _NOTEPAD_SETTINGS_HINTS):
                    app = (element.app or "").lower()
                    if app in {"", "notepad", "notepad.exe"} or "notepad" in app:
                        return PolicyDecision(
                            allow=False,
                            reject=True,
                            reason="Refusing Notepad Settings UI (use notepad_type_text / notepad_save_as)",
                        )
            if element is not None and element.source in {"ocr", "vlm"}:
                threshold = self.config.min_confidence_to_act
                if self.config.safety.confirm_coordinate_clicks or element.confidence < threshold:
                    return PolicyDecision(
                        allow=True,
                        require_confirm=True,
                        reason=(
                            f"{element.source.upper()} click "
                            f"(confidence={element.confidence:.2f}, "
                            f"threshold={threshold:.2f}) requires confirmation"
                        ),
                    )

        if name in {"browser_click", "click"} and self.config.safety.confirm_submit:
            blob = " ".join(
                str(v) for v in (args.get("locator"), args.get("target"), args.values())
            ).lower()
            if any(h in blob for h in _SUBMIT_HINTS):
                return PolicyDecision(
                    allow=True,
                    require_confirm=True,
                    reason="Potentially destructive / submit action requires confirmation",
                )

        if name == "press_keys":
            keys = [str(k).lower() for k in (args.get("keys") or [])]
            # Win11 Notepad: Ctrl+, opens Settings — never useful for agent tasks.
            if ("ctrl" in keys or "control" in keys) and any(
                k in {",", "comma", "oem_comma"} for k in keys
            ):
                return PolicyDecision(
                    allow=False,
                    reject=True,
                    reason="Refusing Ctrl+, (opens Notepad Settings)",
                )
            if "delete" in keys or ("alt" in keys and "f4" in keys):
                return PolicyDecision(
                    allow=True,
                    require_confirm=True,
                    reason="Potentially destructive key chord requires confirmation",
                )

        return PolicyDecision(allow=True)
