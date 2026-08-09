# Desktop Agent

Windows 桌面 UI Agent：识别浏览器 / 办公软件 UI，按自然语言或 CLI 完成点击、填表等任务。

## 快速开始

```powershell
# 安装（开发模式）
py -m pip install -e .

# 若 Scripts 不在 PATH，用模块方式调用：
py -m desktop_agent doctor

# 列出窗口 / 感知前景 UI
py -m desktop_agent list-windows
py -m desktop_agent sense --dump ui.json

# 浏览器调试模式启动（Attach 模式 B，复用日常 profile）
powershell -File scripts/start-browser-debug.ps1 -Browser edge
py -m desktop_agent browser-probe
```

详见 [docs/technical-design.md](docs/technical-design.md)。

## 当前能力（M2）

- CLI：`doctor` / `run` / `list-windows` / `sense` / `click` / `type-text` / `press-keys`
- LLM Orchestrator 状态机 + tool-calling Planner（`ask_user` / `done`）
- UIA 感知与基础动作；`wait_for`
- 应用白名单、高危确认门闩与本地 Trace
- 浏览器 CDP Attach（模式 B）检测与连接
- Excel / Word COM；WPS 表格/文字读写与保存
- 无 LLM 闭环评测：
  - T01 Notepad：`py -m desktop_agent eval-t01`
  - T02 Edge 填表（需先 `scripts/start-browser-debug.ps1`）：`py -m desktop_agent eval-t02`
  - T04 Excel：`py -m desktop_agent eval-t04`
  - T05 Word：`py -m desktop_agent eval-t05`
  - T09 WPS 表格：`py -m desktop_agent eval-t09`
  - T10 WPS 文字：`py -m desktop_agent eval-t10`

### LLM 配置

默认已指向本机 Ollama：`qwen3:8b` @ `http://127.0.0.1:11434/v1`（本地已装模型里最适合 tool-calling；无需真 API Key，配置里 `api_key: ollama` 即可）。

若改用云端兼容接口，改 `llm.model` / `llm.api_base`，并设置 `DESKTOP_AGENT_API_KEY`。

```powershell
# 确保 ollama serve 已运行
py -m desktop_agent doctor
py -m desktop_agent run "打开记事本，输入 hello，然后告诉我完成了"
```
