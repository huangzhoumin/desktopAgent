"""OpenAI-compatible tool schemas for the planner."""

from __future__ import annotations

from typing import Any

# Control-flow tools handled by the orchestrator (not ToolRuntime).
CONTROL_TOOLS = frozenset({"ask_user", "done"})

# Tools dispatched by ToolRuntime.
RUNTIME_TOOLS = frozenset(
    {
        "list_windows",
        "focus_window",
        "launch_app",
        "get_ui_summary",
        "find_elements",
        "screenshot",
        "ocr_find",
        "vlm_locate",
        "click",
        "type_text",
        "press_keys",
        "wait_for",
        "dialog_save_as",
        "dialog_click_button",
        "notepad_type_text",
        "notepad_save_as",
        "verify_file",
        "browser_probe",
        "browser_navigate",
        "browser_fill",
        "browser_click",
        "browser_download",
        "browser_download_bar",
        "browser_snapshot",
        "excel_new",
        "excel_open",
        "excel_get_range",
        "excel_set_range",
        "excel_save",
        "word_new",
        "word_type_text",
        "word_save",
        "wps_probe",
        "wps_new",
        "wps_get_cell",
        "wps_set_cell",
        "wps_save",
        "wps_type_text",
        "wps_save_document",
    }
)

ALL_TOOLS = RUNTIME_TOOLS | CONTROL_TOOLS


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    _tool(
        "list_windows",
        "List visible top-level windows (optionally filter by app alias).",
        {
            "type": "object",
            "properties": {
                "app_filter": {
                    "type": "string",
                    "description": "Optional app alias, e.g. notepad, edge, excel",
                }
            },
        },
    ),
    _tool(
        "focus_window",
        "Bring a window to the foreground by window_id from list_windows.",
        {
            "type": "object",
            "required": ["window_id"],
            "properties": {"window_id": {"type": "string"}},
        },
    ),
    _tool(
        "launch_app",
        "Launch a whitelisted app by alias. Prefer this over ask_user whenever a window is missing. "
        "Aliases: notepad (记事本), excel, word, edge, chrome.",
        {
            "type": "object",
            "required": ["app"],
            "properties": {
                "app": {
                    "type": "string",
                    "description": "App alias: notepad/记事本, excel, word, edge, chrome",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional process arguments",
                },
            },
        },
    ),
    _tool(
        "get_ui_summary",
        "Get a structured summary of the foreground window UI. Prefer this over dumping the whole tree.",
        {
            "type": "object",
            "properties": {
                "max_elements": {"type": "integer", "default": 80},
                "roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional filter: Edit, Button, ComboBox, Hyperlink...",
                },
            },
        },
    ),
    _tool(
        "find_elements",
        "Find UI elements matching a query in the current observation.",
        {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "role": {"type": "string"},
                        "automation_id": {"type": "string"},
                    },
                },
                "top_k": {"type": "integer", "default": 5},
            },
        },
    ),
    _tool(
        "screenshot",
        "Capture the foreground window for human or vision analysis.",
        {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["foreground", "full"], "default": "foreground"}
            },
        },
    ),
    _tool(
        "ocr_find",
        "OCR fallback: screenshot + OCR, return text boxes as clickable elements (source=ocr). "
        "Use when UIA find_elements fails on custom-drawn UI.",
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Optional text filter (substring, case-insensitive)",
                },
                "top_k": {"type": "integer", "default": 8},
                "scope": {
                    "type": "string",
                    "enum": ["foreground", "full"],
                    "default": "foreground",
                },
            },
        },
    ),
    _tool(
        "vlm_locate",
        "VLM fallback: screenshot + vision model to locate a UI target by natural language. "
        "Requires perception.enable_vlm_fallback and a multimodal model.",
        {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to find, e.g. 'Save button' or '下载'",
                },
                "top_k": {"type": "integer", "default": 3},
                "scope": {
                    "type": "string",
                    "enum": ["foreground", "full"],
                    "default": "foreground",
                },
            },
        },
    ),
    _tool(
        "click",
        "Click a UI element by element_id (UIA/OCR/VLM) or screen coordinates {x,y}.",
        {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {
                    "description": "element_id string, or {x,y} screen coordinates. "
                    "Also accepts element_id as a top-level alias for target.",
                    "anyOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "required": ["x", "y"],
                            "properties": {
                                "x": {"type": "integer"},
                                "y": {"type": "integer"},
                            },
                        },
                    ],
                },
                "element_id": {
                    "type": "string",
                    "description": "Alias for target when clicking a previously observed element_id",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                },
                "click_count": {"type": "integer", "default": 1},
            },
        },
    ),
    _tool(
        "type_text",
        "Type text into an element or the focused control.",
        {
            "type": "object",
            "required": ["text"],
            "properties": {
                "target": {
                    "type": "string",
                    "description": "element_id; omit to type into current focus",
                },
                "text": {"type": "string"},
                "clear": {"type": "boolean", "default": True},
            },
        },
    ),
    _tool(
        "press_keys",
        "Send a key combination, e.g. [\"ctrl\", \"s\"] or [\"enter\"].",
        {
            "type": "object",
            "required": ["keys"],
            "properties": {
                "keys": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    _tool(
        "wait_for",
        "Wait until a condition is met or timeout.",
        {
            "type": "object",
            "required": ["condition"],
            "properties": {
                "condition": {
                    "type": "object",
                    "required": ["type"],
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "element_exists",
                                "element_gone",
                                "window_title_contains",
                                "file_exists",
                                "file_contains",
                                "timeout",
                            ],
                        },
                        "query": {
                            "type": "object",
                            "description": "For file_*: {path}; for file_contains also value/contains text",
                        },
                        "value": {},
                        "timeout_ms": {"type": "integer", "default": 10000},
                    },
                }
            },
        },
    ),
    _tool(
        "notepad_type_text",
        "Type into the active Notepad editor (closed-loop UIA). Prefer after launch_app notepad.",
        {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "clear": {"type": "boolean", "default": True},
            },
        },
    ),
    _tool(
        "notepad_save_as",
        "Save the active Notepad document via Save As and require the file to exist on disk.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Destination .txt path (absolute preferred)",
                },
            },
        },
    ),
    _tool(
        "verify_file",
        "Verify a local file exists (and optionally contains text). Use after save/download before done.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "contains": {
                    "type": "string",
                    "description": "Optional substring that must appear in the file",
                },
                "min_bytes": {"type": "integer", "default": 0},
            },
        },
    ),
    _tool(
        "browser_probe",
        "Check whether the daily browser CDP attach endpoint is reachable.",
        {"type": "object", "properties": {}},
    ),
    _tool(
        "browser_navigate",
        "Navigate the attached/controlled browser to a URL via Playwright. "
        "Prefer this over typing into the address bar or Google/Bing search box.",
        {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "wait_until": {
                    "type": "string",
                    "enum": ["load", "domcontentloaded", "networkidle"],
                    "default": "domcontentloaded",
                },
            },
        },
    ),
    _tool(
        "browser_fill",
        "Fill a form field via DOM. Prefer locator.index / css / placeholder from "
        "browser_snapshot (search boxes often use a hot-search placeholder, not 搜索框). "
        "role=searchbox|textbox also works. After fill, press_keys Enter to submit search.",
        {
            "type": "object",
            "required": ["locator", "value"],
            "properties": {
                "locator": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "Element index from the latest browser_snapshot",
                        },
                        "css": {"type": "string"},
                        "placeholder": {"type": "string"},
                        "role": {
                            "type": "string",
                            "description": "Playwright role, e.g. searchbox / textbox / button",
                        },
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                    },
                },
                "value": {"type": "string"},
            },
        },
    ),
    _tool(
        "browser_click",
        "Click an element in the attached browser via DOM locator "
        "(index/css/placeholder/role/name from browser_snapshot).",
        {
            "type": "object",
            "required": ["locator"],
            "properties": {
                "locator": {
                    "type": "object",
                    "properties": {
                        "index": {
                            "type": "integer",
                            "description": "Element index from the latest browser_snapshot",
                        },
                        "css": {"type": "string"},
                        "placeholder": {"type": "string"},
                        "role": {"type": "string"},
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                    },
                }
            },
        },
    ),
    _tool(
        "browser_snapshot",
        "Snapshot interactive DOM elements. Inputs near the top may have "
        "kind=search_candidate — use those with browser_fill (index/css/placeholder). "
        "Do not OCR for 搜索框 when an input is already listed.",
        {"type": "object", "properties": {}},
    ),
    _tool(
        "browser_download",
        "Click a download trigger and save the downloaded file to a local path.",
        {
            "type": "object",
            "required": ["locator", "path"],
            "properties": {
                "locator": {
                    "type": "object",
                    "properties": {
                        "css": {"type": "string"},
                        "role": {"type": "string"},
                        "name": {"type": "string"},
                        "label": {"type": "string"},
                    },
                },
                "path": {"type": "string", "description": "Destination file path"},
                "timeout_ms": {"type": "integer", "default": 15000},
            },
        },
    ),
    _tool(
        "browser_download_bar",
        "Handle Edge/Chrome download shelf via UIA (save/open/show/cancel). "
        "For action=save with path, fills Save As (shelf click or shortcuts when open_if_needed).",
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "save|open|show|cancel",
                    "default": "save",
                },
                "path": {
                    "type": "string",
                    "description": "Local destination when saving (fills Save As)",
                },
                "timeout_s": {"type": "number", "default": 6.0},
                "open_if_needed": {
                    "type": "boolean",
                    "default": True,
                    "description": "If no shelf/Save As yet, try Ctrl+Shift+S / Ctrl+S / Alt+F+A",
                },
            },
        },
    ),
    _tool(
        "dialog_save_as",
        "Fill a visible native Save As dialog with a path, confirm Save, and wait until the file exists. Prefer notepad_save_as for Notepad.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
                "timeout_s": {"type": "number", "default": 5.0},
                "wait_file_s": {"type": "number", "default": 6.0},
            },
        },
    ),
    _tool(
        "dialog_click_button",
        "Click a button on an Office/shell prompt. For Excel's OneDrive save flyout, "
        "pass action=save with path=local_file to use More options -> classic Save As.",
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "yes|no|cancel|save|discard or an exact button name",
                    "default": "yes",
                },
                "path": {
                    "type": "string",
                    "description": "Local destination when saving from a prompt (forces More options / Save As)",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit button names to try",
                },
                "title_contains": {
                    "type": "string",
                    "description": "Optional dialog title filter (Excel/Word/...)",
                },
                "timeout_s": {"type": "number", "default": 3.0},
            },
        },
    ),
    _tool(
        "excel_new",
        "Create a new blank workbook in Microsoft Excel via COM.",
        {"type": "object", "properties": {}},
    ),
    _tool(
        "excel_open",
        "Open a workbook in Microsoft Excel via COM.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    ),
    _tool(
        "excel_get_range",
        "Read Excel cell/range values via COM.",
        {
            "type": "object",
            "required": ["range"],
            "properties": {
                "range": {"type": "string"},
                "sheet": {"type": "string"},
            },
        },
    ),
    _tool(
        "excel_set_range",
        "Write Excel cell/range values via COM (prefer over UI clicking cells).",
        {
            "type": "object",
            "required": ["range", "value"],
            "properties": {
                "range": {"type": "string"},
                "value": {},
                "sheet": {"type": "string"},
            },
        },
    ),
    _tool(
        "excel_save",
        "Save the active Excel workbook. Pass path for Save As (.xlsx).",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    _tool(
        "word_new",
        "Create a new blank document in Microsoft Word via COM.",
        {"type": "object", "properties": {}},
    ),
    _tool(
        "word_type_text",
        "Type text into the active Word document via COM (creates a doc if none open).",
        {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    ),
    _tool(
        "word_save",
        "Save the active Word document (optional path).",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    _tool(
        "wps_probe",
        "Probe whether WPS automation is available.",
        {"type": "object", "properties": {}},
    ),
    _tool(
        "wps_new",
        "Create a new WPS Spreadsheets workbook via COM.",
        {"type": "object", "properties": {}},
    ),
    _tool(
        "wps_get_cell",
        "Read a WPS spreadsheet cell.",
        {
            "type": "object",
            "required": ["range"],
            "properties": {
                "range": {"type": "string"},
                "sheet": {"type": "string"},
            },
        },
    ),
    _tool(
        "wps_set_cell",
        "Write a WPS spreadsheet cell.",
        {
            "type": "object",
            "required": ["range", "value"],
            "properties": {
                "range": {"type": "string"},
                "value": {},
                "sheet": {"type": "string"},
            },
        },
    ),
    _tool(
        "wps_save",
        "Save the active WPS Spreadsheets workbook (optional path for Save As).",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    _tool(
        "wps_type_text",
        "Type text into the active WPS Writer document via COM.",
        {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        },
    ),
    _tool(
        "wps_save_document",
        "Save the active WPS Writer document (optional path for Save As).",
        {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        },
    ),
    _tool(
        "ask_user",
        "Ask only for a missing fact the user must supply (e.g. a filename choice, captcha, login). "
        "NEVER ask whether to open/launch/start an app or whether a window exists — call launch_app instead.",
        {
            "type": "object",
            "required": ["question"],
            "properties": {
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    _tool(
        "done",
        "Finish the task with a summary. Call only when the goal is complete or cannot proceed.",
        {
            "type": "object",
            "required": ["summary"],
            "properties": {
                "summary": {"type": "string"},
                "success": {"type": "boolean", "default": True},
            },
        },
    ),
]


def openai_tools(allowed: set[str] | list[str] | None = None) -> list[dict[str, Any]]:
    if not allowed:
        return TOOL_SCHEMAS
    allow = set(allowed)
    return [t for t in TOOL_SCHEMAS if str((t.get("function") or {}).get("name") or "") in allow]

