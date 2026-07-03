# AGENTS.md

WOWHelper —— 魔兽世界 rpglogs 战斗日志时间轴查看器。单文件 Python tkinter 应用，把 rpglogs 时间轴（HTML 或文本表格）解析为可筛选的施法序列，并输出 MRT 格式。

## 运行
```
.venv\Scripts\python.exe timeline_gui.py
```
GUI 程序，需要桌面会话（tkinter）。唯一第三方依赖为 `pillow`（venv 已就绪；无 requirements 文件，重装用 `pip install pillow`）。

## 目录结构
- `timeline_gui.py` —— 整个应用。解析入口为 `parse_content()`（并非独立脚本）。辅助函数：`_parse_text_format`、`_extract_print_events`、`_compute_calibration`。
- `rpglogs_timeline_export.user.js` —— 油猴脚本；把 rpglogs/warcraftlogs 的时间轴 HTML 复制到剪贴板，作为 GUI 的输入。
- `parse_timeline_fields.md` —— 说明输出 JSON 字段，但其中提到的 `parse_timeline.py` 与 CLI（`python parse_timeline.py ...`）均已不存在。真实逻辑在 `timeline_gui.py`；以代码为准，勿沿用该文档的用法示例。
- `strong_skills.json`、`icon_map.json`、`icons/*.png` —— 运行时配置/缓存。应用使用时会读写这些文件（如 `icon_map.json` 会自动把 `.jpg` 迁移为 `.png` 并回写）。手改可能被覆盖。

## 注意事项
- `parse_content` 自动识别两种输入格式：HTML（含 `timeline-box`）与文本表格（`casts`/`begins casting`）。输出显示一致，但 HTML 格式多出 `timestamp`、`source_id`、`ability_icon`、`failed` 等字段；文本格式这些字段为 `null`。
- 文件与配置均为 UTF-8（含中文）；配置用 `utf-8-sig` 读取。
- 仓库无测试、无 lint/typecheck、无 CI、无 `.gitignore`。venv 为 Python 3.11.9（代码用 `Image.Resampling.LANCZOS`，并保留 3.11 前的回退分支）。