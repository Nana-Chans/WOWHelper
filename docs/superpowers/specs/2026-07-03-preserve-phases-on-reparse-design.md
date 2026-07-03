# 重新解析时保留阶段设置

## 目标

重新解析时间轴数据时保留用户手动设置的阶段数与时间点，仅在手动删除或关闭应用时清除。

## 背景

`timeline_gui.py` 当前在两处清空阶段：

- `_parse_content` 完成后调用 `self._reset_phases()`（line 514）
- `_show_error` 中 `self.phases = []` + `self._rebuild_phase_controls()`（line 539-540）

用户每次重新粘贴并解析数据后，手动添加的阶段都会被清空，体验上很挫败。

## 改动

1. **`timeline_gui.py:514`** — 删除 `self._reset_phases()` 调用。保留后续 `_build_checkboxes()` / `_refresh_table()`，它们会按现有 `self.phases` 重建表格与分隔行。
2. **`timeline_gui.py:539-540`** — 删除 `self.phases = []` 与 `self._rebuild_phase_controls()`。解析失败也保留阶段设置。

## 不改动的部分

- `self.phases = []` 初始化（line 359）保留：应用重启后默认无阶段。
- `_add_phase` / `_remove_phase` / `_on_phase_change`（line 616-649）不变：手动增删改仍生效。
- `_refresh_table` 阶段分隔行逻辑（line 936-952）与 MRT 分块逻辑（line 979-1007）不变。

## 边界行为

- **越界阶段**：保留不动。超出数据范围的阶段不插入分隔行；MRT 输出会为空阶段生成空块——可接受，符合用户选择。
- **应用重启**：`self.phases` 初始为 `[]`，回到默认无阶段。
- **手动删除**：仍可通过每阶段的 × 按钮删除。

## 测试方式

仓库无测试框架。手动验证：

1. 启动 GUI，解析数据，添加 2 个阶段并设置时间点。
2. 再次粘贴新数据并解析。
3. 确认阶段控件、分隔行、MRT 输出中原阶段设置保留。
4. 粘贴无法解析的内容，确认阶段设置仍保留。
5. 关闭并重启应用，确认阶段恢复默认（空）。
