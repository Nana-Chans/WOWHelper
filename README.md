# WOWHelper

把 rpglogs / warcraftlogs 战斗时间轴解析为可筛选的施法序列，并导出 MRT 格式文本。单文件 Python tkinter 桌面应用。

## 相关网站

- [warcraftlogs](https://cn.warcraftlogs.com/) —— 魔兽世界战斗日志分析平台（数据来源，油猴脚本匹配 `*.warcraftlogs.com/reports/*`）
- [lorrgs](https://lorrgs.io/) —— warcraftlogs 数据的团队时间轴查看工具（数据来源之一）

## 功能特性

- 自动识别两种输入格式：HTML（油猴脚本复制的 `timeline-box`）与文本表格（`casts` / `begins casting`）
- 技能勾选筛选，可仅展示关心的施法
- 多阶段 BOSS 手动设置转阶段时间，输出按阶段分块、相对阶段开始计时
- 双 Tab 预览：时间轴视图 / MRT 格式文本
- 一键复制 MRT 输出到剪贴板
- 技能图标自动下载缓存到 `icons/`，`icon_map.json` 维护映射
- `icon_map.json` 会自动把历史 `.jpg` 记录迁移为 `.png` 并回写

## 环境与依赖

- Windows 桌面会话（tkinter GUI）
- Python 3.11（代码用 `Image.Resampling.LANCZOS`，保留 3.10 前回退分支）
- 唯一第三方依赖：`pillow`
- 无 requirements.txt；重装用 `pip install pillow`

## 快速开始

```bash
git clone <repo>
cd WOWHelper
.venv\Scripts\python.exe timeline_gui.py
```

无 venv 时：

```bash
python -m venv .venv
.venv\Scripts\activate
pip install pillow
python timeline_gui.py
```

## 使用流程

1. 浏览器装 Tampermonkey，导入仓库内 `rpglogs_timeline_export.user.js`
2. 打开 rpglogs.cn 或 warcraftlogs.com 的报告页，点页面上的「📋 复制 Timeline」按钮（脚本注入）
3. 运行 GUI，点「📋 解析剪贴板」加载时间轴
4. 在「技能筛选」区勾选要展示的技能
5. 多阶段 BOSS 在「阶段」区设置各阶段起始时间（秒，绝对时间）
6. 切到「MRT 格式」Tab，点「📋 复制全部」
7. 也可「📁 打开文件」直接读本地时间轴文本/HTML

## 输入格式

- **HTML**：油猴脚本复制的 `.timeline-lines` 内容，含 `timeline-box`。解析后事件多出 `timestamp`、`source_id`、`ability_icon`、`failed` 字段
- **文本表格**：`casts` / `begins casting` 行的纯文本，上述字段为 `null`

两种格式输出显示一致，仅 HTML 格式字段更全。

## 目录结构

- `timeline_gui.py` —— 整个应用，解析入口 `parse_content()`
- `rpglogs_timeline_export.user.js` —— 油猴脚本，把时间轴 HTML 复制到剪贴板
- `parse_timeline_fields.md` —— 输出 JSON 字段说明（注意：其中提到的 `parse_timeline.py` 与 CLI 已不存在，以代码为准）
- `strong_skills.json` / `icon_map.json` / `icons/*.png` —— 运行时配置与缓存，应用会自动读写；手改可能被覆盖
- `timeline.txt` —— 示例输入

## 注意事项

- 文件与配置均为 UTF-8（含中文）；配置用 `utf-8-sig` 读取
- 仓库无测试、无 lint / typecheck、无 CI、无 `.gitignore`
- `parse_timeline_fields.md` 中提到的 `parse_timeline.py` 与 CLI 已不存在；真实逻辑以 `timeline_gui.py` 为准