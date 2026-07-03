#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
解析 rpglogs 风格的时间轴 HTML 文件（如 timeline0.txt），输出结构化施法序列 JSON。

用法:
    python parse_timeline.py timeline0.txt            # 输出到 stdout
    python parse_timeline.py timeline0.txt -o out.json
    python parse_timeline.py timeline0.txt --source-id 23
"""
import argparse
import glob
import json
import os
import re
import sys


def prompt_select_txt():
    """交互式选择当前目录下的 txt 文件，返回选中文件路径。"""
    files = sorted(glob.glob("*.txt"), key=str.lower)
    if not files:
        print("当前目录下没有 txt 文件。", file=sys.stderr)
        sys.exit(1)

    print("当前目录下的 txt 文件：", file=sys.stderr)
    for i, name in enumerate(files, 1):
        size = os.path.getsize(name)
        unit = "KB" if size >= 1024 else "B"
        size_val = size / 1024 if size >= 1024 else size
        size_str = f"{size_val:.0f} {unit}" if unit == "KB" else f"{size_val} {unit}"
        print(f"  {i}. {name} ({size_str})", file=sys.stderr)

    while True:
        try:
            choice = input("请输入序号选择文件（q 退出）: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。", file=sys.stderr)
            sys.exit(0)
        choice = choice.strip()
        if choice.lower() in ("q", "quit", "exit"):
            sys.exit(0)
        if not choice.isdigit():
            print("请输入数字序号。", file=sys.stderr)
            continue
        idx = int(choice)
        if 1 <= idx <= len(files):
            return files[idx - 1]
        print(f"序号超出范围，请输入 1~{len(files)}。", file=sys.stderr)


def html_unescape(s: str) -> str:
    """HTML 转义还原"""
    s = s.replace("&quot;", '"')
    s = s.replace("&amp;", "&")
    s = s.replace("&lt;", "<")
    s = s.replace("&gt;", ">")
    s = s.replace("&#39;", "'")
    return s


def unicode_decode(s: str) -> str:
    """将 \\uXXXX 形式的转义还原为字符"""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)


def _extract_balanced_json(s: str, start: int):
    """从 s[start] 处的 '{' 开始，匹配平衡花括号，返回 (json_str, end_index)。
    end_index 指向闭合 '}' 的下一个字符。若不匹配返回 (None, start)。"""
    if start >= len(s) or s[start] != "{":
        return None, start
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start : i + 1], i + 1
    return None, start


def extract_print_events(onmouseover_attr: str):
    """从 onmouseover 属性值中提取所有 printEvent({...}) 的 JSON 对象"""
    raw = html_unescape(onmouseover_attr)
    events = []
    pos = 0
    while True:
        m = re.search(r"printEvent\(\s*", raw[pos:])
        if not m:
            break
        brace_start = pos + m.end()
        if brace_start >= len(raw) or raw[brace_start] != "{":
            pos = brace_start
            continue
        json_str, end = _extract_balanced_json(raw, brace_start)
        if json_str is None:
            pos = brace_start + 1
            continue
        json_str = unicode_decode(json_str)
        try:
            obj = json.loads(json_str)
            events.append(obj)
        except json.JSONDecodeError as e:
            print(f"[WARN] JSON 解析失败: {e}", file=sys.stderr)
            print(f"       raw: {json_str[:200]}", file=sys.stderr)
        pos = end
    return events


def parse_ruler(content: str):
    """解析时间标尺：返回 (first_label, last_label, count)"""
    labels = re.findall(r'timeline-ruler-number">([^<]+)<', content)
    if not labels:
        return None
    return {"first": labels[0], "last": labels[-1], "count": len(labels)}


def label_to_sec(label: str) -> float:
    """'0:00' / '7:14' -> 秒数"""
    m, s = label.split(":")
    return int(m) * 60 + int(s)


def compute_calibration(boxes):
    """
    用最小二乘法从 (timestamp, css_left) 拟合 px_per_ms 与 fight_start。
    left = px_per_ms * (timestamp - fight_start)
        => timestamp = fight_start + left / px_per_ms
    对 left=0 的事件也可参与（left=0 时 timestamp=fight_start）。

    直接线性回归 timestamp = a * left + b，则
        px_per_ms = 1 / a
        fight_start = b
    """
    xs = []  # css_left
    ys = []  # timestamp
    for b in boxes:
        ev = b["events"][0] if b["events"] else None
        if ev is None:
            continue
        if "timestamp" not in ev:
            continue
        xs.append(b["css_left"])
        ys.append(ev["timestamp"])

    n = len(xs)
    if n < 2:
        # 退化：无法拟合
        return {"px_per_ms": 70.0 / 1000.0, "px_per_s": 70.0, "fight_start": 4539957.0, "n": n}

    # 最小二乘 ys = a*xs + b
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    if sxx == 0:
        return {"px_per_ms": 70.0 / 1000.0, "px_per_s": 70.0, "fight_start": mean_y, "n": n}
    a = sxy / sxx  # timestamp per px
    b = mean_y - a * mean_x
    px_per_ms = 1.0 / a
    fight_start = b
    return {
        "px_per_ms": px_per_ms,
        "px_per_s": px_per_ms * 1000.0,
        "fight_start": fight_start,
        "n": n,
    }


def format_display_time(sec: float) -> str:
    """将秒数格式化为 MM:SS.mmm，如 207.222 -> '03:27.222'"""
    if sec < 0:
        sec = 0.0
    total_ms = int(round(sec * 1000))
    mm, rest = divmod(total_ms, 60000)
    ss, ms = divmod(rest, 1000)
    return f"{mm:02d}:{ss:02d}.{ms:03d}"


def parse_text_format(path: str, content: str):
    """解析文本表格格式的时间轴（如 timeline1.txt）。

    事件行格式：
        MM:SS.mmm\t 施法者 casts 技能 [on 目标]
        MM:SS.mmm\t 施法者 begins casting 技能
    """
    # 时间标签 -> 秒
    def time_to_sec(t: str) -> float:
        m, rest = t.split(":")
        return int(m) * 60 + float(rest)

    event_pattern = re.compile(
        r"^(\d{2}:\d{2}\.\d{3})\s+(\S+(?:\s+\S+)*?)\s+(casts|begins casting)\s+(.+?)(?:\s+on\s+(.+))?$"
    )

    raw_events = []
    for line in content.splitlines():
        line = line.strip()
        m = event_pattern.match(line)
        if not m:
            continue
        time_str = m.group(1)
        source_name = m.group(2).strip()
        verb = m.group(3)
        ability_name = m.group(4).strip()
        target_name = m.group(5)
        if target_name is not None:
            target_name = target_name.strip()
        ev_type = "cast" if verb == "casts" else "begincast"
        sec = time_to_sec(time_str)
        raw_events.append(
            {
                "display_sec": round(sec, 3),
                "display_time": time_str,
                "type": ev_type,
                "source_name": source_name,
                "ability_name": ability_name,
                "target_name": target_name,
            }
        )

    # begincast/cast 配对：按时间顺序，同一施法者+技能，begincast 紧接 cast
    # 文本格式里 begins casting 后通常紧跟一条 casts（同技能）。
    # 构建 (source, ability) -> 最近未配对 begincast 映射
    pending = {}
    out_events = []
    for ev in raw_events:
        key = (ev["source_name"], ev["ability_name"])
        if ev["type"] == "begincast":
            pending[key] = ev
            # begincast 也单独作为一条事件输出（用户要求统一用 cast 时刻排序，
            # 文本格式无像素/CSS，begincast 单独列出便于完整性）
            out_events.append(ev)
        else:  # cast
            bc = pending.pop(key, None)
            if bc is not None:
                ev["begincast_sec"] = bc["display_sec"]
                ev["begincast_time"] = bc["display_time"]
                ev["cast_time_ms"] = int(
                    round((ev["display_sec"] - bc["display_sec"]) * 1000)
                )
            out_events.append(ev)

    # 按 display_sec 排序
    out_events.sort(key=lambda e: e["display_sec"])

    # 补齐 HTML 格式对齐的字段（缺失的置 None）
    for e in out_events:
        e.setdefault("source_id", None)
        e.setdefault("source_is_friendly", None)
        e.setdefault("source_marker", None)
        e.setdefault("ability_guid", None)
        e.setdefault("ability_type", None)
        e.setdefault("target_id", None)
        e.setdefault("target_is_friendly", None)
        e.setdefault("target_marker", None)
        e.setdefault("fight", None)
        e.setdefault("css_left", None)
        e.setdefault("css_width", None)
        e.setdefault("failed", None)
        # 统一字段名：文本格式用 source_name，补一个 source_id=None 已设
        e["timestamp"] = None

    result = {
        "meta": {
            "source": path,
            "format": "text",
            "total_events": len(out_events),
            "ruler": None,
        },
        "events": out_events,
    }
    return result


def parse_content(content: str, source: str = "<clipboard>"):
    """解析时间轴内容字符串，自动识别 HTML/text 格式。

    content: 文件内容或剪贴板文本
    source: 来源标识（文件路径或 '<clipboard>'），用于 meta.source
    """
    # 格式检测：HTML（timeline-box）或文本表格（casts/begins casting）
    if "timeline-box" not in content and re.search(r"casts\b", content):
        return parse_text_format(source, content)

    ruler = parse_ruler(content)

    # 匹配每个 timeline-box：捕获 onmouseover 属性、class、style(left/width)
    # 结构：<div onmouseover="..." ... class="timeline-box ..." style="width: Wpx; left: Lpx;">
    box_pattern = re.compile(
        r'<div\s+onmouseover="([^"]*)"[^>]*'
        r'class="timeline-box([^"]*)"[^>]*'
        r'style="width:\s*([\d.]+)px;\s*left:\s*([\d.]+)px;',
        re.DOTALL,
    )

    boxes = []
    for m in box_pattern.finditer(content):
        onmouseover = m.group(1)
        class_extra = m.group(2)  # 形如 " school-8-bg " 或 " school-8-bg failed "
        width = float(m.group(3))
        left = float(m.group(4))
        events = extract_print_events(onmouseover)
        failed = "failed" in class_extra
        boxes.append(
            {
                "events": events,
                "css_left": left,
                "css_width": width,
                "failed": failed,
            }
        )

    if not boxes:
        print("[WARN] 未找到任何 timeline-box", file=sys.stderr)

    cal = compute_calibration(boxes)

    # 构造施法序列：每个 box 可能有 1~2 个事件(begincast + cast)
    # 用户要求：统一用 cast 时刻排序，begincast 作为参考字段
    out_events = []
    for b in boxes:
        events = b["events"]
        begincast = None
        cast = None
        for ev in events:
            if ev.get("type") == "begincast":
                begincast = ev
            elif ev.get("type") == "cast":
                cast = ev
        # 选择主事件：优先 cast；若无 cast 用 begincast
        main = cast if cast else (begincast if begincast else (events[0] if events else None))
        if main is None:
            continue

        ts = main.get("timestamp")
        if ts is None:
            continue

        display_sec = (ts - cal["fight_start"]) / 1000.0

        ability = main.get("ability", {})
        target = main.get("target")
        target_id = main.get("targetID", target.get("id") if target else None)
        target_name = target.get("name") if target else None
        target_is_friendly = main.get("targetIsFriendly")

        entry = {
            "timestamp": ts,
            "display_sec": round(display_sec, 3),
            "display_time": format_display_time(display_sec),
            "type": main.get("type"),
            "source_id": main.get("sourceID"),
            "source_is_friendly": main.get("sourceIsFriendly"),
            "source_marker": main.get("sourceMarker"),
            "ability_name": ability.get("name"),
            "ability_guid": ability.get("guid"),
            "ability_type": ability.get("type"),
            "ability_icon": ability.get("abilityIcon"),
            "target_id": target_id,
            "target_name": target_name,
            "target_is_friendly": target_is_friendly,
            "target_marker": main.get("targetMarker"),
            "fight": main.get("fight"),
            "css_left": b["css_left"],
            "css_width": b["css_width"],
            "failed": b["failed"],
        }

        if begincast and cast and cast is main:
            bc_sec = (begincast["timestamp"] - cal["fight_start"]) / 1000.0
            entry["begincast_sec"] = round(bc_sec, 3)
            entry["begincast_time"] = format_display_time(bc_sec)
            entry["cast_time_ms"] = cast["timestamp"] - begincast["timestamp"]
        elif begincast and begincast is main and cast:
            # 主事件是 begincast 但也有 cast（不应发生，留作兼容）
            ct_sec = (cast["timestamp"] - cal["fight_start"]) / 1000.0
            entry["cast_sec"] = round(ct_sec, 3)
            entry["cast_time_ms"] = cast["timestamp"] - begincast["timestamp"]

        out_events.append(entry)

    # 按 timestamp 排序
    out_events.sort(key=lambda e: e["timestamp"])

    result = {
        "meta": {
            "source": source,
            "format": "html",
            "px_per_s": round(cal["px_per_s"], 4),
            "px_per_ms": round(cal["px_per_ms"], 6),
            "fight_start": round(cal["fight_start"], 1),
            "calibration_samples": cal["n"],
            "total_boxes": len(boxes),
            "total_events": len(out_events),
            "ruler": ruler,
        },
        "events": out_events,
    }
    return result


def parse_file(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return parse_content(f.read(), path)


def main():
    ap = argparse.ArgumentParser(description="解析 rpglogs 时间轴 HTML 为施法序列 JSON")
    ap.add_argument(
        "file",
        nargs="?",
        help="输入文件路径（如 timeline0.txt）；不提供则交互式选择",
    )
    ap.add_argument("-o", "--output", help="输出 JSON 文件路径（默认 stdout）")
    ap.add_argument(
        "--source-id",
        type=int,
        help="只输出指定 sourceID 的事件",
    )
    ap.add_argument("--pretty", action="store_true", help="美化输出（缩进）")
    args = ap.parse_args()

    # 选择输入文件：优先命令行参数，否则交互选择
    if args.file:
        src_file = args.file
    else:
        src_file = prompt_select_txt()

    result = parse_file(src_file)

    if args.source_id is not None:
        result["events"] = [
            e for e in result["events"] if e.get("source_id") == args.source_id
        ]
        result["meta"]["filtered_source_id"] = args.source_id
        result["meta"]["total_events"] = len(result["events"])

    indent = 2 if args.pretty else None
    out_str = json.dumps(result, ensure_ascii=False, indent=indent)

    if args.output:
        out_file = args.output
    elif not args.file:
        # 交互模式下默认保存为同名 _out.json
        base = os.path.basename(src_file)
        stem, _ = os.path.splitext(base)
        out_file = f"{stem}_out.json"
    else:
        # 命令行直传且未指定 -o：输出到 stdout
        print(out_str)
        return

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(out_str)
    m = result["meta"]
    if m.get("format") == "text":
        print(
            f"[OK] {os.path.basename(src_file)} -> {out_file}\n"
            f"     format=text  events={m['total_events']}",
            file=sys.stderr,
        )
    else:
        print(
            f"[OK] {os.path.basename(src_file)} -> {out_file}\n"
            f"     px_per_s={m['px_per_s']}  fight_start={m['fight_start']}  "
            f"samples={m['calibration_samples']}  events={m['total_events']}",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()