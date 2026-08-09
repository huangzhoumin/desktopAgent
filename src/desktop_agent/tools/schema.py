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
        "browser_snapshot",
        "excel_new",
        "excel_open",
        "excel_get_range",
        "excel_set_range",
        "excel_save",
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
        "Launch a whitelisted app by alias (notepad, excel, word, edge, chrome). Prefer this over asking the user.",
        {
            "type": "object",
            "required": ["app"],
            "properties": {
                "app": {
                    "type": "string",
                    "description": "App alias: notepad, excel, word, edge, chrome",
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
        "click",
        "Click a UI element by element_id from find_elements / get_ui_summary.",
        {
            "type": "object",
            "required": ["target"],
            "properties": {
                "target": {"type": "string", "description": "element_id"},
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
        "Navigate the attached browser to a URL (Playwright CDP attach).",
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
        "Fill a form field in the attached browser via DOM locator.",
        {
            "type": "object",
            "required": ["locator", "value"],
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
                "value": {"type": "string"},
            },
        },
    ),
    _tool(
        "browser_click",
        "Click an element in the attached browser via DOM locator.",
        {
            "type": "object",
            "required": ["locator"],
            "properties": {
                "locator": {
                    "type": "object",
                    "properties": {
                        "css": {"type": "string"},
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
        "Snapshot interactive DOM elements from the attached browser page.",
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
        "word_type_text",
        "Type text into the active Word document via COM.",
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
        "Ask the user a question when information is missing, ambiguous, or confirmation is needed.",
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


def openai_tools() -> list[dict[str, Any]]:
    return TOOL_SCHEMAS

