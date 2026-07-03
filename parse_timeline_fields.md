# parse_timeline.py 事件字段文档

`parse_timeline.py` 将 rpglogs 时间轴 HTML（如 `timeline0.txt`）解析为结构化 JSON。

输出顶层结构：

```json
{
  "meta": { ... },
  "events": [ ... ]
}
```

---

## meta 字段（元数据）

由脚本每次读取文件时实时计算，非硬编码。

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | string | 输入文件路径 |
| `px_per_s` | float | 每秒对应的 CSS 像素数（最小二乘拟合，典型值 70.0） |
| `px_per_ms` | float | 每毫秒对应的 CSS 像素数（= px_per_s / 1000） |
| `fight_start` | float | 战斗起始时间戳(ms)，由事件反推得到 |
| `calibration_samples` | int | 参与拟合的事件样本数 |
| `total_boxes` | int | 文件中 timeline-box 总数 |
| `total_events` | int | 输出的事件总数（经 `--source-id` 过滤后为过滤后数量） |
| `ruler` | object | 时间标尺信息，见下 |
| `filtered_source_id` | int | （可选）仅当使用 `--source-id` 时存在 |

### ruler 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `first` | string | 标尺首刻度标签，如 `"0:00"` |
| `last` | string | 标尺末刻度标签，如 `"7:14"` |
| `count` | int | 标尺秒刻度总数 |

---

## events 数组（施法序列）

按 `timestamp` 升序排列。每条代表一次施法（cast），瞬发技能与有引导的技能均统一以 cast 时刻排序。

| 字段 | 类型 | 说明 |
|------|------|------|
| `timestamp` | int | 事件原始时间戳（毫秒），来自 HTML 中 printEvent 的 JSON |
| `display_sec` | float | 显示秒 = `(timestamp - fight_start) / 1000`，保留 3 位小数，与网页显示一致 |
| `type` | string | 事件类型：`"cast"`（施法完成）或 `"begincast"`（开始施法）。主事件优先取 cast |
| `source_id` | int | 施法者 ID |
| `source_is_friendly` | bool | 施法者是否友方 |
| `source_marker` | int | 施法者标记位（rpglogs 角色颜色标记） |
| `ability_name` | string | 技能名称（中文，已从 `\uXXXX` 还原） |
| `ability_guid` | int | 技能 ID（游戏内 spell id） |
| `ability_type` | int | 技能类型/学派（1=物理，8=自然等） |
| `target_id` | int | 目标 ID（友方目标用 `targetID` 字段；环境目标为 -1） |
| `target_name` | string | 目标名称（仅环境/Boss 目标有值，友方玩家目标为 null） |
| `target_is_friendly` | bool | 目标是否友方 |
| `target_marker` | int | 目标标记位（可选，无则为 null） |
| `fight` | int | 战斗 ID |
| `css_left` | float | 该 timeline-box 的 CSS `left` 像素值 |
| `css_width` | float | 该 timeline-box 的 CSS `width` 像素值（瞬发技能为 0） |
| `failed` | bool | 施法是否失败（class 含 `failed`） |
| `begincast_sec` | float | （可选）配对 begincast 的显示秒，仅当 box 内同时含 begincast+cast 时存在 |
| `cast_time_ms` | int | （可选）施法耗时 = `cast.timestamp - begincast.timestamp`，仅当配对时存在 |

---

## 字段含义补充

### display_sec 与网页对应
网页时间轴显示的 `X.XXX S` 即 `display_sec`。例：
- begincast `timestamp=4540637` → `display_sec = (4540637 - 4539957)/1000 = 0.680`
- cast `timestamp=4541735` → `display_sec = 1.778`

### type 字段取值逻辑
每个 timeline-box 的 onmouseover 里含 1~2 个 printEvent：
- 有引导技能：`begincast` + `cast` 两个事件，主事件取 `cast`，并附 `begincast_sec` 与 `cast_time_ms`
- 瞬发/无引导技能：仅 `cast` 一个事件，`css_width=0`，无 `begincast_sec`

### 像素与时间的换算关系
```
display_sec = css_left / px_per_s
display_sec = (timestamp - fight_start) / 1000
```
两条等价。脚本输出以 timestamp 换算为准（毫秒精度），像素仅作校验。

### failed 标记
当 timeline-box 的 class 含 `failed` 时，`failed=true`，表示该次施法失败/取消。

---

## 用法

```powershell
# 输出到 stdout
python parse_timeline.py timeline0.txt

# 输出到文件（推荐，美化缩进）
python parse_timeline.py timeline0.txt -o out.json --pretty

# 仅输出指定 sourceID 的事件
python parse_timeline.py timeline0.txt --source-id 23 -o out.json
```

## 输出示例

```json
{
  "timestamp": 4541735,
  "display_sec": 1.778,
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
  "cast_time_ms": 1098
}
```