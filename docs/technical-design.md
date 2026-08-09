# Windows Desktop UI Agent — 技术方案

> 版本：v0.3  
> 日期：2026-08-08  
> 目标场景：浏览器（Chrome / Edge）、办公软件（Microsoft Word/Excel + WPS + 记事本等）  
> 平台：Windows 10 / 11  
> 已确认：浏览器 MVP 用 Attach 日常浏览器（模式 B）；MS Office 与 WPS 均支持；MVP 交互为 CLI  
> 实现进度：M3 进行中（M2 Agent Loop 已接入；模式 A 降级 / 对话框适配 / T03–T12 评测与 dashboard 已落地；OCR/VLM 视觉兜底与 `replay` 已接入）

---

## 1. 背景与目标

### 1.1 要解决什么问题

用户用自然语言下达任务，Agent 在 Windows 桌面上：

1. 识别当前可见 UI（窗口、控件、文本）
2. 规划可执行步骤
3. 通过点击、输入、快捷键完成任务（填表、点菜单、保存文件等）
4. 对执行结果做校验，失败可重试或询问用户

### 1.2 非目标（本期不做）

- 游戏 / 纯自绘 Canvas UI 的通用自动化
- 跨机器远程控制
- 无监督的高危操作（支付、批量删除、系统设置变更）
- 移动端 / macOS

### 1.3 成功标准（MVP）

| 指标 | 目标 |
|---|---|
| 记事本：新建 → 输入 → 另存为 | 成功率 ≥ 95% |
| Edge/Chrome：打开页面 → 填表 → 提交（可控测试页） | 成功率 ≥ 85% |
| Excel：打开工作簿 → 写单元格 → 保存 | 成功率 ≥ 85% |
| Word：打开文档 → 输入段落 → 保存 | 成功率 ≥ 85% |
| 单步平均耗时（本地 UIA 路径） | < 2s（不含 LLM） |
| 危险操作误执行 | 0（必须确认） |

---

## 2. 设计原则

1. **结构感知优先，视觉兜底**  
   浏览器 / 办公软件优先走 Windows UI Automation（UIA）或应用专用 API；截图 + OCR/VLM 仅在结构路径失败时启用。

2. **语义动作优先于坐标点击**  
   优先 `Invoke` / `SetValue` / `Select` / 快捷键；坐标点击是最后手段。

3. **观察 → 行动 → 校验**  
   每一步执行后必须重新感知并判定是否达成预期状态。

4. **人在回路（Human-in-the-loop）**  
   歧义、高危、多次失败时暂停并询问用户。

5. **白名单与可审计**  
   默认可操作应用白名单；全程步骤日志 + 关键图，可回放。

6. **先垂类后通用**  
   MVP 钉死：Edge/Chrome、Excel、Word/WPS、记事本；抽象通用接口，但应用适配层可定制。

---

## 3. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                        User Interface                        │
│              CLI / 系统托盘 / 后续可选 GUI                     │
└─────────────────────────────┬───────────────────────────────┘
                              │ TaskRequest
┌─────────────────────────────▼───────────────────────────────┐
│                     Orchestrator（编排）                      │
│     会话 · 状态机 · 重试 · 确认门闩 · 超时 · 取消             │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
┌───────────────▼───────────────┐ ┌───────────▼───────────────┐
│     Planner（LLM + Tools）     │ │   Policy / Safety Guard    │
│  任务分解 · 工具选择 · 反思    │ │  白名单 · 高危确认 · 限流   │
└───────────────┬───────────────┘ └───────────────────────────┘
                │ ToolCall
┌───────────────▼─────────────────────────────────────────────┐
│                        Tool Runtime                          │
│  list_windows / get_ui_tree / find / click / type / wait …   │
└───────┬─────────────────┬─────────────────┬─────────────────┘
        │                 │                 │
┌───────▼───────┐ ┌───────▼───────┐ ┌───────▼───────┐
│  Perception   │ │    Action     │ │    Memory     │
│ UIA / OCR/VLM │ │ SendInput等   │ │ Trace/Cache   │
└───────┬───────┘ └───────────────┘ └───────────────┘
        │
┌───────▼───────────────────────────────────────────┐
│              App Adapters（应用适配）               │
│  BrowserAdapter · ExcelAdapter · WordAdapter …    │
└───────────────────────────────────────────────────┘
```

### 3.1 进程模型

| 进程 / 模块 | 说明 |
|---|---|
| `desktop-agent` 主进程 | Orchestrator + Tool Runtime + Perception/Action |
| LLM Provider（外置） | 云端 API 或本地推理服务；主进程只发 tool-calling 请求 |
| （可选）Watcher | 托盘常驻、热键唤起、任务队列 |

MVP 单进程即可；后续若 UIA 调用阻塞严重，再拆 Perception Worker。

### 3.2 技术选型（建议）

| 层级 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 原型快；生态成熟；后续热点可迁 C# |
| UIA | `uiautomation` 或 `pywinauto`（后端 UIA） | 覆盖 Win32/WPF/多数办公与浏览器外壳 |
| 浏览器增强 | Playwright（可选通道） | 对网页 DOM 比 UIA 稳一个数量级 |
| Office 增强 | `win32com`（Excel/Word COM） | 单元格/段落级操作比点 UI 稳 |
| OCR | Windows.Media.Ocr 或 PaddleOCR | 中文桌面场景够用 |
| VLM | 多模态 API（按需） | 仅兜底 |
| LLM | 支持 tool calling 的模型 | Planner 核心 |
| 配置 | YAML + JSON Schema | 白名单、adapter、策略 |
| 日志 | JSON Lines + 截图目录 | 便于评测与回放 |

> **双通道策略**：浏览器页面内操作优先 Playwright；浏览器壳子（标签、地址栏、下载栏）走 UIA。  
> Office 内容操作优先 COM；功能区/对话框走 UIA。

---

## 4. 核心概念与数据模型

### 4.1 Task

```json
{
  "task_id": "tsk_20260808_001",
  "goal": "打开桌面的 report.xlsx，在 A1 写入销售额，保存",
  "constraints": {
    "allowed_apps": ["excel", "explorer"],
    "require_confirm_on": ["save_as", "submit", "send"],
    "max_steps": 40,
    "timeout_sec": 600
  },
  "created_at": "2026-08-08T22:00:00+08:00"
}
```

### 4.2 UI Element（统一元素模型）

所有感知来源（UIA / DOM / COM / OCR）归一到：

```json
{
  "element_id": "el_a3f2",
  "source": "uia",
  "app": "excel",
  "window_id": "win_12",
  "role": "Edit",
  "name": "A1",
  "automation_id": "GridHost",
  "value": "",
  "states": ["enabled", "visible"],
  "bounds": { "x": 120, "y": 240, "w": 80, "h": 20 },
  "path": "Window/Pane/DataGrid/Cell[A1]",
  "actions": ["click", "set_value", "type"],
  "confidence": 0.96,
  "raw_ref": { "runtime_id": "..." }
}
```

字段约定：

- `element_id`：本次感知快照内稳定 ID（短命，不跨任务持久化）
- `source`：`uia` | `dom` | `com` | `ocr` | `vlm`
- `confidence`：匹配/识别置信度；低于阈值不得自动点击
- `actions`：该元素当前允许的动作集合

### 4.3 Observation（观察快照）

```json
{
  "obs_id": "obs_77",
  "timestamp": "2026-08-08T22:01:12+08:00",
  "foreground_window": {
    "window_id": "win_12",
    "title": "report.xlsx - Excel",
    "app": "excel",
    "pid": 4321
  },
  "elements": ["el_a3f2", "..."],
  "screenshot_path": "traces/tsk_.../obs_77.png",
  "notes": "ribbon visible; cell A1 focused"
}
```

### 4.4 ActionResult

```json
{
  "action": "type_text",
  "target": "el_a3f2",
  "ok": true,
  "error": null,
  "latency_ms": 180,
  "post_condition": {
    "checked": true,
    "passed": true,
    "detail": "value contains 销售额"
  }
}
```

### 4.5 Trace Event

每一步写入 append-only 日志，用于回放与评测：

```json
{
  "ts": "...",
  "task_id": "tsk_...",
  "type": "tool_call|tool_result|llm_message|confirm|error",
  "payload": {}
}
```

---

## 5. 模块设计

### 5.1 Orchestrator（编排器）

职责：

- 接收 Task，创建会话
- 驱动状态机
- 调用 Planner 获取下一步 ToolCall
- 执行前过 Safety Guard
- 管理重试、超时、取消、用户确认
- 任务结束产出 Summary

不负责：具体 UIA 调用、具体 prompt 细节。

### 5.2 Planner（规划器）

输入：

- 用户目标
- 最近 N 条 Observation / ActionResult 摘要
- 可用 Tools schema
- 当前应用 adapter 提示（如 Excel 优先用 COM）

输出：

- 一个或多个 ToolCall（MVP 每次只执行 1 个主动作，可附带 wait）
- 或 `ask_user` / `done`

策略：

- ReAct 风格：Thought（内部）→ Tool → Observe
- 对填表类任务允许先产出「字段映射草案」，再逐步执行
- Token 控制：不要把完整 UIA 树塞进上下文；先 `find_elements` 缩小范围

### 5.3 Perception（感知）

#### 5.3.1 感知管道

```text
foreground window
    → AppRouter 判断应用类型
    → Adapter.primary_sense()
         ├─ Browser: Playwright DOM snapshot (+ UIA chrome)
         ├─ Excel/Word: COM object model (+ UIA ribbon/dialogs)
         └─ Generic: UIA tree
    → 若失败或置信度低 → OCR/VLM fallback
    → 归一化为 Observation
```

#### 5.3.2 UIA 感知规范

- 默认深度：可见控件优先，过滤不可见/关闭状态节点
- 必取属性：`Name`, `ControlType`, `AutomationId`, `BoundingRectangle`, `IsEnabled`, `IsOffscreen`, `Value`（若有）
- 树过大时：按窗口区域 / 角色过滤；提供 `depth`、`max_nodes`
- 多显示器 / DPI：统一转换为物理像素坐标，并在 Action 层再换算

#### 5.3.3 浏览器感知（重点）

| 通道 | 用途 |
|---|---|
| Playwright attach / launch | 页面 DOM：input、button、label、select |
| UIA | 窗口标题、标签页、地址栏、下载栏、系统对话框 |
| OCR/VLM | 无法 DOM 访问的嵌入页、远程桌面式页面（兜底） |

DOM Element → 统一模型时：

- `role` 取 ARIA role / tag 映射
- `name` 取 label / aria-label / placeholder / text
- `source=dom`
- 动作优先 `fill` / `click`（Playwright），而不是屏幕坐标

#### 5.3.4 Office 感知（重点）

**Excel**

- COM：`Workbooks` / `ActiveSheet` / `Range("A1")` 读写值、公式
- UIA：功能区按钮、另存为对话框、受保护提示

**Word / WPS**

- COM（Word）或 UIA（WPS 视支持情况）：插入文本、选区、保存对话框
- 复杂排版不在 MVP 范围

**通用对话框（另存为 / 打开）**

- 统一由 `DialogAdapter` 处理：文件名编辑框、保存按钮（UIA）

### 5.4 Action（执行）

动作优先级：

1. Adapter 语义 API（Playwright `fill`、Excel COM `Range.Value`）
2. UIA pattern（Invoke / ValuePattern / Selection）
3. 焦点 + 键盘（`Ctrl+A`, 输入, `Enter`）
4. 坐标点击（bounds 中心，含 DPI 校正）

统一 Action API（内部）：

```text
activate_window(window_id)
click(target, button=left, click_count=1)
set_value(target, value)
type_text(target|focused, text, clear=true)
press_keys(keys[])
select(target, option)
scroll_into_view(target)
wait(condition, timeout_ms)
```

每个动作支持：

- `timeout_ms`
- `post_condition`（可选，Orchestrator 也可统一做）
- 失败时返回结构化错误码（见 §8）

### 5.5 Memory

| 类型 | 内容 | 生命周期 |
|---|---|---|
| Working Memory | 当前 obs、最近步骤、字段映射 | 单任务 |
| Element Cache | element_id → raw_ref | 单次 observation 有效 |
| Episode Trace | 全量事件 + 截图 | 持久化到磁盘 |
| App Profile | 某应用的定位偏好、已知 AutomationId | 长期配置 |

注意：`element_id` 不得跨 observation 复用而不校验；执行前若 ref 失效，自动重新 find。

### 5.6 App Adapters

接口（逻辑）：

```text
class AppAdapter:
  id: str
  match(window) -> bool
  sense(window, opts) -> Observation
  prefer_tools() -> list[str]          # 提示 Planner 优先工具
  normalize_goal_hints(goal) -> str    # 可选：注入领域提示
  healthcheck() -> ok
```

MVP Adapters：

1. `BrowserAdapter`（Chrome/Edge + Playwright Attach 日常浏览器）
2. `ExcelAdapter`（Microsoft Excel：COM + UIA）
3. `WordAdapter`（Microsoft Word：COM + UIA）
4. `WpsAdapter`（WPS：COM/兼容接口优先 + UIA）
5. `NotepadAdapter`（UIA）
6. `GenericUiaAdapter`（兜底）
7. `CommonDialogAdapter`（打开/保存对话框）

---

## 6. Tool Schema（给 LLM 的工具面）

> 设计目标：工具少而稳；避免让模型直接操作原始 Win32。

### 6.1 窗口与感知

#### `list_windows`

```json
{
  "name": "list_windows",
  "description": "列出当前可见顶层窗口",
  "parameters": {
    "type": "object",
    "properties": {
      "app_filter": { "type": "string", "description": "可选，如 excel/chrome/edge" }
    }
  }
}
```

#### `focus_window`

```json
{
  "name": "focus_window",
  "parameters": {
    "type": "object",
    "required": ["window_id"],
    "properties": {
      "window_id": { "type": "string" }
    }
  }
}
```

#### `get_ui_summary`

```json
{
  "name": "get_ui_summary",
  "description": "获取前景窗口结构化摘要（不要倾倒整棵树）",
  "parameters": {
    "type": "object",
    "properties": {
      "max_elements": { "type": "integer", "default": 80 },
      "roles": {
        "type": "array",
        "items": { "type": "string" },
        "description": "可选过滤：Edit, Button, ComboBox, Hyperlink..."
      }
    }
  }
}
```

#### `find_elements`

```json
{
  "name": "find_elements",
  "parameters": {
    "type": "object",
    "required": ["query"],
    "properties": {
      "query": {
        "type": "object",
        "properties": {
          "text": { "type": "string" },
          "role": { "type": "string" },
          "automation_id": { "type": "string" },
          "css": { "type": "string", "description": "仅浏览器 DOM" },
          "excel_range": { "type": "string", "description": "如 A1 或 Sheet1!B2" }
        }
      },
      "top_k": { "type": "integer", "default": 5 }
    }
  }
}
```

#### `screenshot`

```json
{
  "name": "screenshot",
  "description": "截取前景窗口或全屏，供人工或VLM分析",
  "parameters": {
    "type": "object",
    "properties": {
      "scope": { "enum": ["foreground", "full"], "default": "foreground" },
      "with_vision_hint": { "type": "boolean", "default": false }
    }
  }
}
```

### 6.2 执行

#### `click`

```json
{
  "name": "click",
  "parameters": {
    "type": "object",
    "required": ["target"],
    "properties": {
      "target": {
        "description": "element_id 或 {x,y}（不推荐）",
        "oneOf": [
          { "type": "string" },
          {
            "type": "object",
            "properties": {
              "x": { "type": "integer" },
              "y": { "type": "integer" }
            },
            "required": ["x", "y"]
          }
        ]
      },
      "button": { "enum": ["left", "right", "middle"], "default": "left" },
      "click_count": { "type": "integer", "default": 1 }
    }
  }
}
```

#### `type_text`

```json
{
  "name": "type_text",
  "parameters": {
    "type": "object",
    "required": ["text"],
    "properties": {
      "target": { "type": "string", "description": "element_id，可空表示当前焦点" },
      "text": { "type": "string" },
      "clear": { "type": "boolean", "default": true }
    }
  }
}
```

#### `press_keys`

```json
{
  "name": "press_keys",
  "parameters": {
    "type": "object",
    "required": ["keys"],
    "properties": {
      "keys": {
        "type": "array",
        "items": { "type": "string" },
        "description": "如 [\"ctrl\", \"s\"] 或 [\"enter\"]"
      }
    }
  }
}
```

#### `browser_navigate`（BrowserAdapter）

```json
{
  "name": "browser_navigate",
  "parameters": {
    "type": "object",
    "required": ["url"],
    "properties": {
      "url": { "type": "string" },
      "wait_until": { "enum": ["load", "domcontentloaded", "networkidle"], "default": "domcontentloaded" }
    }
  }
}
```

#### `browser_fill`（BrowserAdapter）

```json
{
  "name": "browser_fill",
  "parameters": {
    "type": "object",
    "required": ["locator", "value"],
    "properties": {
      "locator": {
        "type": "object",
        "properties": {
          "css": { "type": "string" },
          "role": { "type": "string" },
          "name": { "type": "string" },
          "label": { "type": "string" }
        }
      },
      "value": { "type": "string" }
    }
  }
}
```

#### `excel_set_range`（ExcelAdapter）

```json
{
  "name": "excel_set_range",
  "parameters": {
    "type": "object",
    "required": ["range", "value"],
    "properties": {
      "range": { "type": "string" },
      "value": {},
      "sheet": { "type": "string" }
    }
  }
}
```

#### `excel_get_range`

```json
{
  "name": "excel_get_range",
  "parameters": {
    "type": "object",
    "required": ["range"],
    "properties": {
      "range": { "type": "string" },
      "sheet": { "type": "string" }
    }
  }
}
```

#### `wait_for`

```json
{
  "name": "wait_for",
  "parameters": {
    "type": "object",
    "required": ["condition"],
    "properties": {
      "condition": {
        "type": "object",
        "properties": {
          "type": {
            "enum": [
              "element_exists",
              "element_gone",
              "window_title_contains",
              "excel_cell_equals",
              "timeout"
            ]
          },
          "query": { "type": "object" },
          "value": {},
          "timeout_ms": { "type": "integer", "default": 10000 }
        },
        "required": ["type"]
      }
    }
  }
}
```

### 6.3 控制流

#### `ask_user`

```json
{
  "name": "ask_user",
  "parameters": {
    "type": "object",
    "required": ["question"],
    "properties": {
      "question": { "type": "string" },
      "options": { "type": "array", "items": { "type": "string" } }
    }
  }
}
```

#### `done`

```json
{
  "name": "done",
  "parameters": {
    "type": "object",
    "required": ["summary"],
    "properties": {
      "summary": { "type": "string" },
      "success": { "type": "boolean", "default": true }
    }
  }
}
```

---

## 7. 状态机

```text
                    ┌──────────┐
                    │  Created │
                    └────┬─────┘
                         │ start
                    ┌────▼─────┐
              ┌─────┤ Planning │◄──────────────┐
              │     └────┬─────┘               │
              │          │ tool_call           │
              │     ┌────▼─────┐               │
              │     │ PolicyCheck│              │
              │     └────┬─────┘               │
              │     need_confirm│ ok           │
              │          │      │              │
              │     ┌────▼──┐ ┌─▼──────┐       │
              │     │Awaiting│ │Executing│      │
              │     │Confirm │ └───┬────┘       │
              │     └────┬──┘     │            │
              │    allow │   ┌────▼────┐       │
              │          └──►│ Verify  │───────┘
              │              └────┬────┘  continue
              │                   │
              │         fail retry│ / ask_user / done
              │              ┌────▼────┐
              │              │Degraded │（切换 fallback 通道）
              │              └────┬────┘
              │                   │
              │              ┌────▼────┐
              └─────────────►│ Failed  │
                             └─────────┘
                         success ┌─────────┐
                         ───────►│ Succeeded│
                                 └─────────┘
                                  ┌─────────┐
                     cancel ─────►│Cancelled│
                                  └─────────┘
```

### 7.1 关键规则（摘要）

| 当前状态 | 事件 | 下一状态 |
|---|---|---|
| Created | `start` | Planning |
| Planning | `tool_call` | PolicyCheck |
| Planning | `done` | Succeeded/Failed（按 success） |
| Planning | `ask_user` | AwaitingConfirm |
| PolicyCheck | 通过 | Executing |
| PolicyCheck | 需确认 | AwaitingConfirm |
| PolicyCheck | 拒绝 | Failed |
| Executing | 完成 | Verify |
| Verify | 通过且任务未完成 | Planning |
| Verify | 通过且完成 | Succeeded |
| Verify | 失败且 retries < N | Planning（带错误上下文）或 Degraded |
| Verify | 失败且 retries ≥ N | AwaitingConfirm 或 Failed |
| Degraded | 启用 OCR/VLM/坐标策略后再执行 | Executing |
| 任意可取消态 | `cancel` | Cancelled |

### 7.2 校验（Verify）策略

默认校验：

1. 动作自身返回 `ok=true`
2. 重新 `sense` 前景窗口
3. 若 ToolCall 声明了预期：元素值、窗口标题、URL、Excel 单元格等
4. 未声明预期时：至少确认目标元素仍存在且未出现崩溃对话框

浏览器额外校验：

- URL 变化、toast/错误提示、必填校验信息

Excel 额外校验：

- `excel_get_range` 读回比对

---

## 8. 错误模型与重试

### 8.1 错误码

| code | 含义 | 建议恢复 |
|---|---|---|
| `ELEMENT_NOT_FOUND` | 找不到目标 | 重新 sense / 放宽 query / OCR |
| `ELEMENT_STALE` | element_id 失效 | 重新 find |
| `ACTION_REJECTED` | 控件不可用 | 等待 / 换路径 |
| `WINDOW_NOT_FOUND` | 窗口丢失 | list_windows 再 focus |
| `PERMISSION_DENIED` | 策略拦截 | ask_user 或终止 |
| `TIMEOUT` | 等待超时 | 重试或降级 |
| `ADAPTER_UNAVAILABLE` | Playwright/COM 不可用 | 回退 UIA |
| `AMBIGUOUS_TARGET` | 多个候选 | ask_user |
| `USER_CANCELLED` | 用户取消 | Failed/Cancelled |
| `LLM_INVALID_TOOL` | 非法工具调用 | 要求模型重选 |

### 8.2 重试策略

```text
attempt 1: 原通道精确匹配
attempt 2: 重新感知 + 模糊匹配（同 role + 文本包含）
attempt 3: Adapter 降级（Playwright→UIA，COM→UIA）
attempt 4: OCR/VLM 定位（需更高确认级别）
之后: ask_user
```

同一步骤最大自动重试：3（可配置）。  
任务级最大步数：40（可配置）。

---

## 9. 安全与权限

### 9.1 应用白名单

```yaml
allowed_apps:
  - process: msedge.exe
    alias: edge
  - process: chrome.exe
    alias: chrome
  - process: EXCEL.EXE
    alias: excel
  - process: WINWORD.EXE
    alias: word
  - process: notepad.exe
    alias: notepad
  - process: wps.exe
    alias: wps
```

非白名单窗口：只读感知可配，默认禁止执行动作。

### 9.2 高危操作门闩

以下动作默认 `require_confirm=true`：

- 文件覆盖保存 / 另存为到非用户指定路径
- 表单 Submit / 发送邮件 / 分享
- 删除文件、关闭未保存文档（可能丢数据）
- 任意坐标点击（当 `source=vlm|ocr` 且 confidence < 0.85）
- 打开未知 URL（不在任务声明域内）

确认交互：

- CLI：stdin 提示 `y/n`
- 托盘：气泡通知 + 确认按钮

### 9.3 数据安全

- 默认不把完整截图上传，除非启用 `with_vision_hint`
- Trace 存本地：`%LOCALAPPDATA%/DesktopAgent/traces/`
- API Key 仅来自环境变量 / 系统凭据管理，不入库
- 日志对密码型输入框：记录「已输入长度」，不记录明文（检测 `Password` 类控件）

### 9.4 运行权限

- 普通用户权限运行即可（勿默认要求 Administrator）
- 若目标进程提权，可能出现 UIA 访问限制——记录为 `PERMISSION_DENIED` 并提示用户

---

## 10. 浏览器专项方案

### 10.1 连接方式（已决策）

网页自动化需要 Playwright 能「连上」浏览器进程。有两种模式：

| 模式 | 含义 | 优点 | 缺点 |
|---|---|---|---|
| **A. 受控浏览器（降级/备选）** | Agent 自己启动一个独立的 Chrome/Edge，使用单独的用户数据目录（与你日常浏览器隔离） | 稳定、可复现、不干扰日常浏览；实现简单 | 默认没有你日常浏览器里的登录态/Cookie；需重新登录目标站点 |
| **B. Attach 日常浏览器（MVP 默认）** | 连接到你已经打开的 Chrome/Edge，直接操作当前窗口里的页面 | 可复用已有登录态；更像「帮你点正在用的浏览器」 | 需用特殊参数启动浏览器（开启调试端口）；有安全与稳定性风险；实现与排障更复杂 |

**MVP 决策：默认模式 B（Attach 日常浏览器）；模式 A 作为 attach 失败时的降级备选。**

#### 模式 B 落地要求（MVP 必须做）

1. **浏览器需以调试端口启动**（CDP），例如用户或 Agent 助手脚本用类似参数启动：  
   `msedge.exe --remote-debugging-port=9222`  
   （Chrome 同理；具体端口可配置）
2. **`desktop-agent doctor`** 检测：调试端口是否可达、能否列出 page/target
3. **提供一键/文档化启动方式**：  
   - 推荐：Agent 附带 `scripts/start-browser-debug.ps1`，用**现有用户 profile** 拉起带调试端口的 Edge/Chrome  
   - 若用户已有无调试端口的浏览器实例占着 profile，需先关闭再按脚本启动（Windows 上同 profile 多实例限制要写清）
4. **安全**：调试端口默认仅监听 `127.0.0.1`；文档明确勿对公网暴露
5. **失败降级**：attach 失败 → 提示用户如何启动 → 可选回退模式 A（受控浏览器）→ 再失败则 UIA/OCR 兜底

理由：目标场景需要复用日常登录态与已打开页面，贴近真实桌面助手体验。

### 10.2 页面操作策略

| 任务 | 首选 |
|---|---|
| 打开 URL | `browser_navigate` |
| 填输入框 | `browser_fill`（label/role） |
| 点按钮 | Playwright click（按 role/name） |
| 下拉框 | Playwright select / click option |
| 文件上传 | 受控 `set_input_files`（需确认） |
| 下载 | 监听 download + UIA 下载栏兜底 |
| 浏览器原生对话框 | UIA CommonDialog |

### 10.3 反脆弱点

- 动态前端：click 后 `wait_for` DOM 变化
- 同名按钮多：用邻近 label / 在 form 作用域内查找
- iframe：Playwright frame 定位；UIA 通常无效
- 登录态 / SSO：不自动破解验证码；遇验证码 `ask_user`

---

## 11. 办公软件专项方案

### 11.1 Excel

**主路径（COM）**

- 打开：`Workbooks.Open(path)` 或让用户先打开后 attach
- 写入：`Range("A1").Value = ...`
- 读取校验：写后读回
- 保存：`Workbook.Save()` / `SaveAs`

**辅路径（UIA）**

- 功能区「数据」「审阅」等按钮
- 「另存为」对话框
- 合并单元格提示、宏提示等弹窗处理

**Planner 提示词约束（注入）**

- 能用 `excel_set_range` 时禁止坐标点单元格
- 批量写入鼓励一次写一块区域（后续可扩展二维数组接口）

### 11.2 Word

- COM：`Selection.TypeText` / `Range.Text` / `Save`
- 样式与复杂排版 MVP 不做
- 查找替换可二期加

### 11.3 WPS（与 Microsoft 同为一等公民）

- **已决策**：Microsoft Office（Excel/Word）与 WPS 均需支持，各自独立 Adapter，不共用假设
- `WpsAdapter`：先探测 COM/兼容自动化接口；可用则走语义读写，不可用则 UIA 主路径
- `ExcelAdapter` / `WordAdapter`：Microsoft COM 主路径 + UIA 辅路径
- `doctor` 需分别检测：Excel COM、Word COM、WPS 进程/自动化可用性
- 评测集对 MS 与 WPS 各保留对应用例（打开-写入-保存）

### 11.4 文件打开 / 保存对话框（通用）

标准化流程：

1. 等待对话框窗口出现（title 匹配）
2. 找 `Edit`（文件名）→ `set_value` 全路径或文件名
3. 如需切目录：地址栏 / 左侧树（UIA）
4. 点「保存/打开」
5. 处理「是否覆盖」二次确认（高危门闩）

---

## 12. Prompt 与上下文管理

### 12.1 System Prompt 要点

- 你是 Windows 桌面代理，只能通过给定 tools 操作
- 优先使用应用专用工具（browser_* / excel_*）
- 先观察再行动；不要假设控件一定存在
- 目标歧义时 `ask_user`
- 完成时调用 `done`
- 禁止编造 element_id

### 12.2 上下文窗口拼装

```text
[system]
[task goal + constraints]
[adapter hints for foreground app]
[tool schemas]
[last K steps compact trace]
[latest UI summary / find results]
```

压缩规则：

- 成功的旧步骤只保留 1 行摘要
- 失败步骤保留错误码与关键观测
- UI 树永远摘要化，不传 raw XML

### 12.3 字段映射（表单任务中间态）

```json
{
  "form_id": "expense_claim",
  "fields": [
    { "label": "姓名", "value": "张三", "target_hint": { "role": "Edit", "name": "姓名" }, "status": "pending" },
    { "label": "金额", "value": "120.5", "target_hint": { "css": "#amount" }, "status": "done" }
  ]
}
```

存入 Working Memory，供后续步骤续填，避免每步重新推断。

---

## 13. 配置体系

### 13.1 目录约定

```text
desktopAgent/
  docs/
    technical-design.md
  configs/
    agent.yaml
    apps.whitelist.yaml
    adapters/
      browser.yaml
      excel.yaml
      word.yaml
  src/
    desktop_agent/
      orchestrator/
      planner/
      tools/
      perception/
      action/
      adapters/
      memory/
      safety/
      cli/
  traces/
  evals/
    tasks/
    runners/
```

### 13.2 `agent.yaml`（示例）

```yaml
llm:
  provider: openai_compatible
  model: <your-model>
  temperature: 0.1
  max_tool_rounds: 40

runtime:
  step_timeout_ms: 30000
  max_retries_per_step: 3
  screenshot_every_step: true

perception:
  uia_max_nodes: 400
  enable_ocr_fallback: true
  enable_vlm_fallback: false
  min_confidence_to_act: 0.75

safety:
  confirm_coordinate_clicks: true
  confirm_submit: true
  mask_password_values: true

paths:
  traces_dir: traces
```

---

## 14. CLI / 交互接口（MVP）

```bash
# 执行自然语言任务
desktop-agent run "打开 Edge 访问 https://example.com/form 并填写姓名张三，提交前先问我"

# 仅感知调试
desktop-agent sense --dump ui.json

# 回放某次 trace（只读）
desktop-agent replay traces/tsk_xxx

# 健康检查
desktop-agent doctor
```

`doctor` 检查项：

- UIA 可用
- 白名单进程检测
- Excel/Word COM 可用
- Playwright / 浏览器 attach 可用
- LLM API 连通

---

## 15. 评测体系

### 15.1 任务集（首批）

| ID | 应用 | 任务 |
|---|---|---|
| T01 | Notepad | 输入指定文本并保存到临时目录 |
| T02 | Edge | 打开本地测试 HTML，填写 3 个字段并点击预览（不提交外网） |
| T03 | Chrome | 同 T02 |
| T04 | Excel（Microsoft） | 写入 A1:B2 并保存 |
| T05 | Word（Microsoft） | 写入一段话并保存 |
| T06 | Excel（Microsoft） | 打开已有文件，修改单元格，另存为新文件 |
| T07 | Edge | 处理「另存为」下载文件名 |
| T08 | 混合 | 从 Excel 读一个值填到网页表单 |
| T09 | WPS 表格 | 写入单元格并保存 |
| T10 | WPS 文字 | 写入一段话并保存 |
| T11 | Excel（Microsoft） | 脏工作簿 Alt+F4 → 关闭保存提示 → More options 本地另存为 |
| T12 | Edge | 触发下载后走 UIA 下载栏 / Save As（相对 T07 Playwright 拦截的兜底路径） |

### 15.2 指标

- Task Success Rate
- Step Success Rate
- 平均步数 / 平均耗时
- 人工介入次数
- 降级通道使用率（UIA/COM/DOM/OCR）

评测运行应可无 LLM 脚本化（直接调 tools）+ 有 LLM e2e 两套。

---

## 16. 里程碑与交付物

### M0 — 方案冻结（当前）

- 本文档关键决策已确认（v0.3）
- 技术栈：Python + UIA + Playwright（Attach 日常浏览器，失败可降级受控启动）+ Microsoft COM + WPS Adapter
- 交互：CLI MVP

### M1 — 可脚本执行的 Tool Runtime（约 1 周）

交付：

- `sense` / `click` / `type` / `excel_*` / `browser_*` 可 CLI 调用
- Trace 落盘
- 白名单与确认门闩

验收：T01/T04 脚本成功率达标

### M2 — LLM Agent Loop（约 1 周）

交付：

- Orchestrator 状态机
- tool-calling Planner
- `ask_user` / `done`

验收：T01–T05 在有 LLM 下可完成

### M3 — 浏览器/Office 打磨（约 1–2 周）✅ 核心已落地

交付：

- Attach 日常浏览器稳定化（调试端口启动脚本、doctor、失败降级）✅
- 对话框适配（`FileDialogHelper` / `dialog_save_as`）✅
- 评测集与基础 dashboard（`eval-dashboard`）✅
- `launch_app` + 浏览器模式 A（controlled）降级 ✅

验收：T01–T08 脚本可达；高危误执行 = 0（策略门闩保持）

### M4 — 体验与硬化（可选）

- 托盘可驻留
- 任务暂停/继续
- App Profile 沉淀
- VLM 兜底开关默认可用

---

## 17. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 浏览器无法 attach | 网页自动化失败或体验差 | doctor 明确报错；提供 debug 启动脚本；降级受控浏览器（A）；UIA 仅用于壳 |
| 同 profile 被已开浏览器占用 | 无法带调试端口启动 | 文档提示先关闭实例；或检测占用并引导用户 |
| 调试端口暴露风险 | 本机其他进程可控浏览器 | 仅绑定 127.0.0.1；端口可配置；doctor 检查 |
| UIA 树巨大/噪声 | LLM 胡点 | `get_ui_summary` + `find_elements`，禁止整树入模 |
| Office 点击单元格不稳 | 任务失败 | 强制 COM 写值 |
| DPI/多屏点偏 | 点错控件 | 统一坐标模块；优先语义动作 |
| 页面异步 | 假成功 | 强制 Verify + wait_for |
| Prompt 注入（网页文本） | 误操作 | 工具层策略校验；不把网页原文当指令；高危确认 |
| COM 被禁用/点击通知 | Excel 自动化失败 | doctor 检测；回退 UIA 并提示 |
| 法律/隐私 | 截图含敏感信息 | 本地 trace；上传需显式开关 |

---

## 18. 关键决策（已建议默认值）

| 决策项 | 默认 | 状态 |
|---|---|---|
| 主语言 | Python 3.11+ | 建议 |
| 浏览器主通道 | Playwright（DOM） | 建议 |
| 浏览器连接 | **Attach 日常浏览器（模式 B）**；失败可降级受控浏览器（模式 A） | **已确认** |
| Excel/Word | Microsoft COM 主路径 | **已确认支持** |
| WPS | 独立 Adapter，COM 优先、UIA 兜底 | **已确认支持** |
| 通用 UI 主通道 | UIA | 建议 |
| 视觉模型 | 默认关闭，失败才可选开 | 建议 |
| 每步是否截图 | 是（本地） | 建议 |
| 坐标点击 | 允许但需更高确认级别 | 建议 |
| 交互形态 MVP | **CLI**；托盘后置 | **已确认** |

---

## 19. 下一步

1. ~~评审关键决策（浏览器连接 / Office+WPS / CLI）~~ ✅  
2. ~~冻结 Tool Schema v1 / doctor / UIA+Notepad / Office+WPS / Attach+模式A / LLM Orchestrator~~ ✅  
3. 用 LLM e2e 稳定 T01–T05（DeepSeek / 云端 OpenAI-compatible）— 入口已接：`eval-llm-t01`…`eval-llm-t05` / `eval-dashboard --llm-suite`  
4. ~~对话框覆盖更多壳层场景（Office 提示框、浏览器下载栏 UIA）~~ ✅（`eval-t11` / `eval-t12`）  
5. M4：托盘驻留 / 暂停继续 / App Profile / VLM 兜底  

当前 CLI 评测入口：`desktop-agent eval-t01` … `eval-t12`；LLM：`eval-llm-t01` … `eval-llm-t05`；汇总：`eval-dashboard --suite` / `--llm-suite`。

---

## 附录 A — 一次「网页填表」理想时序

```text
User: 打开 Edge 访问 https://contoso.local/claim ，姓名填张三，金额填100，提交前问我
→ list_windows
→ focus_window(edge)
→ browser_navigate(url)
→ get_ui_summary / find_elements(label=姓名)
→ browser_fill(姓名=张三)
→ browser_fill(金额=100)
→ find_elements(role=button, text=提交)
→ ask_user("表单已填好，是否点击提交？")
→ (用户确认) click(提交)
→ wait_for(element_exists=成功提示)
→ done(success=true)
```

## 附录 B — 一次「Excel 改值保存」理想时序

```text
User: 打开 D:\data\report.xlsx，把 B2 改成 2026，保存
→ excel open/attach
→ excel_get_range(B2)          # 先读
→ excel_set_range(B2, 2026)
→ excel_get_range(B2)          # 校验
→ press_keys([ctrl,s]) 或 adapter.save()
→ done
```

## 附录 C — 术语

| 术语 | 含义 |
|---|---|
| UIA | Windows UI Automation |
| VLM | Vision-Language Model，多模态视觉语言模型 |
| Adapter | 针对某类应用的感知/执行适配器 |
| Trace | 任务执行的事件与截图记录 |
| Degraded Mode | 主通道失败后的降级执行模式 |
