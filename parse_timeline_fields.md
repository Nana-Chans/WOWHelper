# parse_timeline.py 事件字段文档

`parse_timeline.py` 解析 rpglogs 时间轴文件，自动识别两种格式，输出结构化施法序列 JSON。

- **HTML 格式**（如 `timeline0.txt`）：含 `timeline-box`、`printEvent`、CSS 像素，需实时反推 `fight_start`
- **文本表格格式**（如 `timeline1.txt`）：纯文本事件行 `MM:SS.mmm  施法者 casts 技能 [on 目标]`，时间已直接给出

输出顶层结构：

```json
{
  "meta": { ... },
  "events": [ ... ]
}
```

---

## meta 字段（元数据）

### 通用字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | 输入文件路径 |
| `format` | string | 格式标识：`"html"` 或 `"text"` |
| `total_events` | int | 输出事件总数 |
| `ruler` | object/null | 时间标尺信息（HTML 格式有，text 为 null） |
| `filtered_source_id` | int | （可选）仅当使用 `--source-id` 时存在 |

### 仅 HTML 格式（`format=="html"`）

由脚本每次读取文件时实时计算，非硬编码。

| 字段 | 类型 | 说明 |
|------|------|------|
| `px_per_s` | float | 每秒对应的 CSS 像素数（最小二乘拟合，典型值 70.0） |
| `px_per_ms` | float | 每毫秒对应的 CSS 像素数（= px_per_s / 1000） |
| `fight_start` | float | 战斗起始时间戳(ms)，由事件反推得到 |
| `calibration_samples` | int | 参与拟合的事件样本数 |
| `total_boxes` | int | 文件中 timeline-box 总数 |

### ruler 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `first` | string | 标尺首刻度标签，如 `"0:00"` |
| `last` | string | 标尺末刻度标签，如 `"7:14"` |
| `count` | int | 标尺秒刻度总数 |

---

## events 数组（施法序列）

按时间升序排列。每条代表一次施法事件。

### 通用字段（两种格式都有）

| 字段 | 类型 | 说明 |
|------|------|------|
| `display_sec` | float | 显示秒（战斗内相对时间），保留 3 位小数 |
| `display_time` | string | 显示时间，格式 `MM:SS.mmm`（如 `03:27.222`） |
| `type` | string | 事件类型：`"cast"`（施法完成）或 `"begincast"`（开始施法） |
| `source_name` | string | （仅 text 格式）施法者角色名 |
| `ability_name` | string | 技能名称（中文，HTML 格式已从 `\uXXXX` 还原） |
| `target_name` | string/null | 目标名称（text 格式友方目标有值；HTML 格式仅环境/Boss 目标有值） |
| `begincast_sec` | float | （可选）配对 begincast 的显示秒，仅当配对成功时存在 |
| `begincast_time` | string | （可选）配对 begincast 的显示时间，格式 `MM:SS.mmm` |
| `cast_time_ms` | int | （可选）施法耗时 = `cast - begincast`（毫秒），仅当配对时存在 |

### 仅 HTML 格式（`format=="html"`）

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | int | 事件原始时间戳（毫秒），来自 printEvent JSON |
| `source_id` | int | 施法者 ID |
| `source_is_friendly` | bool | 施法者是否友方 |
| `source_marker` | int | 施法者标记位（rpglogs 颜色标记） |
| `ability_guid` | int | 技能 ID（游戏内 spell id） |
| `ability_type` | int | 技能类型/学派（1=物理，8=自然等） |
| `target_id` | int | 目标 ID（环境目标为 -1） |
| `target_is_friendly` | bool | 目标是否友方 |
| `target_marker` | int | 目标标记位（可选，无则为 null） |
| `fight` | int | 战斗 ID |
| `css_left` | float | timeline-box 的 CSS `left` 像素值 |
| `css_width` | float | timeline-box 的 CSS `width` 像素值（瞬发为 0） |
| `failed` | bool | 施法是否失败（class 含 `failed`） |

> text 格式中以上 HTML 专有字段均置为 `null`。

---

## 字段含义补充

### 两种格式的来源
- **HTML 格式**：从 rpglogs 网页"时间轴"视图保存的 HTML，含 `timeline-box`、`printEvent({...})`、CSS 像素
- **文本表格格式**：从 rpglogs 网页"事件→文字展示"视图复制保存的文本，每行 `MM:SS.mmm  施法者 casts 技能 [on 目标]`

脚本通过检测 `timeline-box` 关键字判断格式，自动走对应解析逻辑。

### display_sec 计算
- **HTML 格式**：`display_sec = (timestamp - fight_start) / 1000`，`fight_start` 由最小二乘拟合反推
- **text 格式**：直接由行首 `MM:SS.mmm` 解析，`display_sec = MM*60 + SS.mmm`

### display_time 格式
两种格式统一为 `MM:SS.mmm`（如 `03:27.222`）。

### type 字段取值逻辑
- **HTML 格式**：每个 timeline-box 含 1~2 个 printEvent。有引导技能含 `begincast`+`cast`，主事件取 `cast`；瞬发仅 `cast`
- **text 格式**：每行一个事件。`casts`→`"cast"`，`begins casting`→`"begincast"`

### begincast/cast 配对
- **HTML 格式**：同一 timeline-box 内的 begincast 与 cast 天然配对
- **text 格式**：按"同施法者+技能、最近未配对 begincast"规则配对。文本是线性事件流，begins 与 casts 之间可能隔其他技能，配对为启发式

### 像素与时间的换算（仅 HTML 格式）
```
display_sec = css_left / px_per_s
display_sec = (timestamp - fight_start) / 1000
```
两条等价。脚本输出以 timestamp 换算为准（毫秒精度），像素仅作校验。

### failed 标记（仅 HTML 格式）
当 timeline-box 的 class 含 `failed` 时，`failed=true`，表示该次施法失败/取消。text 格式无此信息，`failed=null`。

---

## 用法

```powershell
# 交互选择（自动识别格式）
python parse_timeline.py -o out.json --pretty

# 指定文件（自动识别格式）
python parse_timeline.py timeline1.txt -o out.json --pretty

# 仅输出指定 sourceID 的事件（HTML 格式）
python parse_timeline.py timeline0.txt --source-id 23 -o out.json
```

## 输出示例

### HTML 格式（timeline0.txt）

```json
{
  "timestamp": 4541735,
  "display_sec": 1.778,
  "display_time": "00:01.778",
  "type": "cast",
  "source_id": 23,
  "source_is_friendly": true,
  "source_marker": 8,
  "ability_name": "野性成长",
  "ability_guid": 48438,
  "ability_type": 8,
  "target_id": -1,
  "target_name": "Environment",
  "target_is_friendly": false,
  "target_marker": null,
  "fight": 28,
  "css_left": 47.6,
  "css_width": 76.86,
  "failed": false,
  "begincast_sec": 0.68,
  "begincast_time": "00:00.680",
  "cast_time_ms": 1098
}
```

### 文本表格格式（timeline1.txt）

```json
{
  "display_sec": 2.922,
  "display_time": "00:02.922",
  "type": "cast",
  "source_name": "茭白茭白",
  "ability_name": "愈合",
  "target_name": "篬天的蓝耀",
  "source_id": null,
  "source_is_friendly": null,
  "source_marker": null,
  "ability_guid": null,
  "ability_type": null,
  "target_id": null,
  "target_is_friendly": null,
  "target_marker": null,
  "fight": null,
  "css_left": null,
  "css_width": null,
  "failed": null,
  "timestamp": null
}
```