#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
施法序列查看器 GUI（tkinter）。

从剪贴板或文件解析 rpglogs 时间轴，按技能勾选过滤，显示 cast 时间轴。

用法:
    python timeline_gui.py
"""
import io
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
import urllib.request
from tkinter import ttk, messagebox, filedialog

# 默认强力技能（首次运行写入配置文件，之后以配置文件为准）
DEFAULT_STRONG_ABILITIES = ["万灵之召", "宁静", "激活"]

ICON_BASE_URL = "https://assets.rpglogs.cn/img/warcraft/abilities/"
ICON_SIZE = (28, 28)  # 显示尺寸（原图 56×56，放大显示更清晰）

# 校准回退占位值：仅在样本不足（n<2）或 css_left 全相同时使用，产出时间不准。
# 这些值来自历史战斗，仅用于避免除零/NaN，不保证正确性。
FALLBACK_PX_PER_MS = 70.0 / 1000.0
FALLBACK_PX_PER_S = 70.0
FALLBACK_FIGHT_START = 4539957.0


def _warn(msg: str):
    """统一的警告输出。GUI 程序中 stderr 用户看不到，但仍打印便于调试。"""
    print(f"[WARN] {msg}", file=sys.stderr)


# ==================== 时间轴解析（原 parse_timeline.py） ====================


def _html_unescape(s: str) -> str:
    """HTML 转义还原"""
    s = s.replace("&quot;", '"')
    s = s.replace("&amp;", "&")
    s = s.replace("&lt;", "<")
    s = s.replace("&gt;", ">")
    s = s.replace("&#39;", "'")
    return s


def _unicode_decode(s: str) -> str:
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


def _extract_print_events(onmouseover_attr: str):
    """从 onmouseover 属性值中提取所有 printEvent({...}) 的 JSON 对象"""
    raw = _html_unescape(onmouseover_attr)
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
        json_str = _unicode_decode(json_str)
        try:
            obj = json.loads(json_str)
            events.append(obj)
        except json.JSONDecodeError as e:
            _warn(f"JSON 解析失败: {e}\n       raw: {json_str[:200]}")
        pos = end
    return events


def _parse_ruler(content: str):
    """解析时间标尺：返回 (first_label, last_label, count)"""
    labels = re.findall(r'timeline-ruler-number">([^<]+)<', content)
    if not labels:
        return None
    return {"first": labels[0], "last": labels[-1], "count": len(labels)}


def _compute_calibration(boxes):
    """用最小二乘法从 (timestamp, css_left) 拟合 px_per_ms 与 fight_start。

    返回 dict 含 px_per_ms/px_per_s/fight_start/n/calibration_failed。
    calibration_failed=True 表示样本不足，时间不可信，调用方应提示用户。
    """
    xs, ys = [], []
    for b in boxes:
        ev = b["events"][0] if b["events"] else None
        if ev is None or "timestamp" not in ev:
            continue
        xs.append(b["css_left"])
        ys.append(ev["timestamp"])
    n = len(xs)
    if n < 2:
        return {
            "px_per_ms": FALLBACK_PX_PER_MS, "px_per_s": FALLBACK_PX_PER_S,
            "fight_start": FALLBACK_FIGHT_START, "n": n, "calibration_failed": True,
        }
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    if sxx == 0:
        return {
            "px_per_ms": FALLBACK_PX_PER_MS, "px_per_s": FALLBACK_PX_PER_S,
            "fight_start": mean_y, "n": n, "calibration_failed": True,
        }
    a = sxy / sxx
    b = mean_y - a * mean_x
    px_per_ms = 1.0 / a
    return {
        "px_per_ms": px_per_ms, "px_per_s": px_per_ms * 1000.0,
        "fight_start": b, "n": n, "calibration_failed": False,
    }


def _format_display_time(sec: float) -> str:
    """将秒数格式化为 MM:SS.mmm，如 207.222 -> '03:27.222'"""
    if sec < 0:
        sec = 0.0
    total_ms = int(round(sec * 1000))
    mm, rest = divmod(total_ms, 60000)
    ss, ms = divmod(rest, 1000)
    return f"{mm:02d}:{ss:02d}.{ms:03d}"


def _parse_text_format(source: str, content: str):
    """解析文本表格格式的时间轴（如 timeline1.txt）。

    事件行格式：
        MM:SS.mmm\t 施法者 casts 技能 [on 目标]
        MM:SS.mmm\t 施法者 begins casting 技能
    """
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

    # begincast/cast 配对
    pending = {}
    out_events = []
    for ev in raw_events:
        key = (ev["source_name"], ev["ability_name"])
        if ev["type"] == "begincast":
            pending[key] = ev
            out_events.append(ev)
        else:
            bc = pending.pop(key, None)
            if bc is not None:
                ev["begincast_sec"] = bc["display_sec"]
                ev["begincast_time"] = bc["display_time"]
                ev["cast_time_ms"] = int(
                    round((ev["display_sec"] - bc["display_sec"]) * 1000)
                )
            out_events.append(ev)

    out_events.sort(key=lambda e: e["display_sec"])

    for e in out_events:
        e.setdefault("source_id", None)
        e.setdefault("source_is_friendly", None)
        e.setdefault("source_marker", None)
        e.setdefault("ability_guid", None)
        e.setdefault("ability_type", None)
        e.setdefault("ability_icon", None)
        e.setdefault("target_id", None)
        e.setdefault("target_is_friendly", None)
        e.setdefault("target_marker", None)
        e.setdefault("fight", None)
        e.setdefault("css_left", None)
        e.setdefault("css_width", None)
        e.setdefault("failed", None)
        e["timestamp"] = None

    return {
        "meta": {
            "source": source,
            "format": "text",
            "total_events": len(out_events),
            "ruler": None,
        },
        "events": out_events,
    }


def parse_content(content: str, source: str = "<clipboard>"):
    """解析时间轴内容字符串，自动识别 HTML/text 格式。

    content: 文件内容或剪贴板文本
    source: 来源标识（文件路径或 '<clipboard>'），用于 meta.source
    """
    # 格式检测：HTML（timeline-box）或文本表格（casts/begins casting）
    if "timeline-box" not in content and re.search(r"casts\b", content):
        return _parse_text_format(source, content)

    ruler = _parse_ruler(content)

    box_pattern = re.compile(
        r'<div\s+onmouseover="([^"]*)"[^>]*'
        r'class="timeline-box([^"]*)"[^>]*'
        r'style="width:\s*([\d.]+)px;\s*left:\s*([\d.]+)px;',
        re.DOTALL,
    )

    boxes = []
    for m in box_pattern.finditer(content):
        onmouseover = m.group(1)
        class_extra = m.group(2)
        width = float(m.group(3))
        left = float(m.group(4))
        events = _extract_print_events(onmouseover)
        failed = "failed" in class_extra
        boxes.append(
            {"events": events, "css_left": left, "css_width": width, "failed": failed}
        )

    if not boxes:
        _warn("未找到任何 timeline-box")

    cal = _compute_calibration(boxes)

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
            "display_time": _format_display_time(display_sec),
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
            entry["begincast_time"] = _format_display_time(bc_sec)
            entry["cast_time_ms"] = cast["timestamp"] - begincast["timestamp"]
        elif begincast and begincast is main and cast:
            ct_sec = (cast["timestamp"] - cal["fight_start"]) / 1000.0
            entry["cast_sec"] = round(ct_sec, 3)
            entry["cast_time_ms"] = cast["timestamp"] - begincast["timestamp"]

        out_events.append(entry)

    out_events.sort(key=lambda e: e["timestamp"])

    return {
        "meta": {
            "source": source,
            "format": "html",
            "px_per_s": round(cal["px_per_s"], 4),
            "px_per_ms": round(cal["px_per_ms"], 6),
            "fight_start": round(cal["fight_start"], 1),
            "calibration_samples": cal["n"],
            "calibration_failed": cal["calibration_failed"],
            "total_boxes": len(boxes),
            "total_events": len(out_events),
            "ruler": ruler,
        },
        "events": out_events,
    }


# ==================== GUI ====================


class TimelineViewer:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("施法序列查看器")
        self.root.geometry("760x720")
        self.root.minsize(560, 480)

        self.events = []          # 全部事件（含 begincast）
        self.cast_events = []     # 仅 cast 事件
        self.ability_vars = {}    # ability_name -> tk.BooleanVar
        self.ability_counts = {}  # ability_name -> count
        self.strong_skills = self._load_strong_skills()  # 从配置文件加载
        self.phases = []          # 阶段起始时间列表(秒,升序)；P1 隐含 0；空=单阶段
        self.phase_entries = []   # 阶段时间 Entry 控件列表(与 phases 对应)
        self.phase_vars = []      # 阶段时间 StringVar 列表(与 phase_entries 对应)
        self.icon_images = {}     # ability_name -> tk.PhotoImage (保持引用防止GC)
        self.name_to_iconfile = self._load_icon_map()  # name -> icon文件名
        # 后台图标下载：线程只负责下载+转换+落盘，PhotoImage 在主线程创建
        self._icon_queue = queue.Queue()  # (name, ok, err_msg)
        self._pending_icons = set()        # 正在下载的 ability_name

        self._build_ui()
        self._poll_icon_queue()

    # ---------- UI 构建 ----------
    def _build_ui(self):
        # 顶部工具栏
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(fill="x")

        ttk.Button(bar, text="📋 解析剪贴板", command=self.load_clipboard).pack(side="left")
        ttk.Button(bar, text="📁 打开文件", command=self.load_file).pack(side="left", padx=(4, 0))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
        ttk.Button(bar, text="全选", command=self.select_all).pack(side="left")
        ttk.Button(bar, text="全不选", command=self.select_none).pack(side="left", padx=(4, 0))

        self.status_var = tk.StringVar(value="未解析")
        ttk.Label(bar, textvariable=self.status_var).pack(side="right")

        # 技能筛选区
        filt = ttk.LabelFrame(self.root, text="技能筛选（勾选要显示的技能）", padding=6)
        filt.pack(fill="x", padx=8, pady=(6, 4))

        self.canvas = tk.Canvas(filt, highlightthickness=0, height=120)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(filt, orient="vertical", command=self.canvas.yview)
        sb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=sb.set)

        self.ability_frame = ttk.Frame(self.canvas)
        self.ability_window = self.canvas.create_window((0, 0), window=self.ability_frame, anchor="nw")
        self.ability_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.ability_window, width=e.width),
        )
        # 鼠标滚轮：仅在技能筛选区滚动其 canvas，其余区域交给控件原生处理
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        # 阶段控制区
        pf = ttk.LabelFrame(self.root, text="阶段（多阶段BOSS手动设置转阶段时间）", padding=6)
        pf.pack(fill="x", padx=8, pady=(4, 4))
        self.phase_frame = ttk.Frame(pf)
        self.phase_frame.pack(side="left", fill="x", expand=True)
        self._rebuild_phase_controls()

        # 预览区：两种风格切换（时间轴 / MRT格式）
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        # --- Tab 1: 时间轴 ---
        tab_timeline = ttk.Frame(nb)
        nb.add(tab_timeline, text="时间轴")
        # 设置 Treeview 行高适配图标(28px + 间距)
        style = ttk.Style()
        style.configure("Treeview", rowheight=32)
        self.tree = ttk.Treeview(
            tab_timeline, columns=("time", "ability"), show="tree headings", selectmode="browse",
            style="Treeview",
        )
        self.tree.heading("#0", text="")
        self.tree.heading("time", text="时间")
        self.tree.heading("ability", text="技能")
        self.tree.column("#0", width=34, stretch=False, anchor="center")
        self.tree.column("time", width=120, anchor="center", stretch=False)
        self.tree.column("ability", width=400, anchor="w", stretch=True)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb = ttk.Scrollbar(tab_timeline, orient="vertical", command=self.tree.yview)
        tsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=tsb.set)
        # 阶段分隔行样式
        self.tree.tag_configure("phase_div", background="#d9d9d9", font=("TkDefaultFont", 9, "bold"))

        # --- Tab 2: MRT 格式 ---
        tab_mrt = ttk.Frame(nb)
        nb.add(tab_mrt, text="MRT 格式")
        top_mrt = ttk.Frame(tab_mrt)
        top_mrt.pack(fill="x", padx=4, pady=(4, 2))
        ttk.Label(top_mrt, text="格式: {time:秒} 技能名").pack(side="left")
        ttk.Button(top_mrt, text="📋 复制全部", command=self.copy_mrt).pack(side="right")
        self.mrt_text = tk.Text(tab_mrt, wrap="none", height=20)
        self.mrt_text.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=(0, 4))
        msb = ttk.Scrollbar(tab_mrt, orient="vertical", command=self.mrt_text.yview)
        msb.pack(side="right", fill="y", pady=(0, 4))
        self.mrt_text.configure(yscrollcommand=msb.set, state="disabled")

    # ---------- 数据加载 ----------
    def load_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            self._show_error("剪贴板为空或无法读取。")
            return
        if not text or not text.strip():
            self._show_error("剪贴板内容为空，请先复制时间轴数据。")
            return
        self._parse_and_show(text, "<clipboard>")

    def load_file(self):
        initial_dir = os.path.dirname(os.path.abspath(__file__))
        # 若目录下只有一个 txt 文件，自动加载它，否则弹出选择对话框
        txts = [
            os.path.join(initial_dir, n)
            for n in os.listdir(initial_dir)
            if n.lower().endswith(".txt")
            and os.path.isfile(os.path.join(initial_dir, n))
        ]
        if len(txts) == 1:
            path = txts[0]
        else:
            path = filedialog.askopenfilename(
                title="选择时间轴文件",
                initialdir=initial_dir,
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            self._show_error(f"读取文件失败：\n{e}")
            return
        self._parse_and_show(content, os.path.basename(path))

    def _parse_and_show(self, content: str, source: str):
        try:
            result = parse_content(content, source)
        except Exception as e:
            self._show_error(f"解析失败：内容不是有效的时间轴数据。\n{e}")
            return

        self.events = result.get("events", [])
        # 仅保留 cast 事件用于显示
        self.cast_events = [e for e in self.events if e.get("type") == "cast"]
        if not self.cast_events:
            self._show_error("未解析到 cast 事件，请确认复制的是时间轴数据。")
            return

        # 统计技能计数
        self.ability_counts = {}
        for e in self.cast_events:
            name = e.get("ability_name") or "?"
            self.ability_counts[name] = self.ability_counts.get(name, 0) + 1

        # 收集图标映射（HTML 格式有 ability_icon）
        self._update_icon_map_from_events()

        self._build_checkboxes()
        self._refresh_table()
        meta = result.get("meta", {})
        base_status = f"共 {len(self.cast_events)} 条 cast · {len(self.ability_counts)} 个技能"
        if meta.get("calibration_failed"):
            base_status += " · ⚠ 校准样本不足，时间可能不准"
        self.status_var.set(base_status)

    def _show_error(self, msg: str):
        """解析失败时在预览区显示错误信息（不弹窗）。"""
        self.events = []
        self.cast_events = []
        self.ability_counts = {}
        self.ability_vars = {}
        for w in self.ability_frame.winfo_children():
            w.destroy()
        # 时间轴区显示错误
        self.tree.delete(*self.tree.get_children())
        self.tree.insert("", "end", values=("", msg))
        # MRT 区显示错误
        self.mrt_text.configure(state="normal")
        self.mrt_text.delete("1.0", "end")
        self.mrt_text.insert("1.0", msg)
        self.mrt_text.configure(state="disabled")
        self.status_var.set("解析失败")

    # ---------- 阶段 ----------
    def _parse_time_str(self, s: str):
        """解析时间字符串为秒数。支持 '30' / '00:30' / '1:30'。失败返回 None。"""
        s = s.strip()
        if not s:
            return None
        if ":" in s:
            parts = s.split(":")
            if len(parts) != 2:
                return None
            try:
                m = int(parts[0])
                sec = int(parts[1])
                return m * 60 + sec
            except ValueError:
                return None
        else:
            try:
                return int(s)
            except ValueError:
                try:
                    return int(float(s))
                except ValueError:
                    return None

    @staticmethod
    def _sec_to_str(sec: float) -> str:
        """秒数格式化为 MM:SS"""
        sec = int(round(sec))
        m, s = divmod(sec, 60)
        return f"{m:02d}:{s:02d}"

    def _phase_of(self, sec: float) -> int:
        """返回秒数所属阶段编号(1-based)。P1=1。"""
        idx = 1
        for p in self.phases:
            if sec >= p:
                idx += 1
            else:
                break
        return idx

    def _phase_start(self, phase_num: int) -> float:
        """阶段起始秒数。P1=0，P2=phases[0]，..."""
        if phase_num <= 1:
            return 0.0
        return self.phases[phase_num - 2]

    def _rebuild_phase_controls(self):
        """根据 self.phases 重建阶段控件。"""
        for w in self.phase_frame.winfo_children():
            w.destroy()
        self.phase_entries = []
        self.phase_vars = []

        # P1 固定显示
        ttk.Label(self.phase_frame, text="P1 (0:00)", padding=(4, 0)).pack(side="left")

        # 各阶段
        for i, p in enumerate(self.phases):
            cell = ttk.Frame(self.phase_frame)
            cell.pack(side="left", padx=(8, 0))
            ttk.Label(cell, text=f"P{i+2}").pack(side="left")
            var = tk.StringVar(value=self._sec_to_str(p))
            entry = ttk.Entry(cell, textvariable=var, width=7)
            entry.pack(side="left", padx=(2, 0))
            entry.bind("<Return>", lambda e, idx=i: self._on_phase_change(idx))
            entry.bind("<FocusOut>", lambda e, idx=i: self._on_phase_change(idx))
            self.phase_vars.append(var)
            self.phase_entries.append(entry)
            ttk.Button(cell, text="×", width=3,
                       command=lambda idx=i: self._remove_phase(idx)).pack(side="left", padx=(2, 0))

        ttk.Button(self.phase_frame, text="+ 添加阶段",
                   command=self._add_phase).pack(side="left", padx=(10, 0))

    def _add_phase(self):
        """添加一个阶段，默认时间=上一阶段+60s。"""
        if self.phases:
            default = self.phases[-1] + 60
        else:
            default = 60
        self.phases.append(default)
        self.phases.sort()
        self._rebuild_phase_controls()
        # 聚焦新增阶段的输入框并全选
        idx = self.phases.index(default)
        self._focus_phase_entry(idx)
        self._refresh_table()

    def _remove_phase(self, idx: int):
        """删除指定阶段。"""
        if 0 <= idx < len(self.phases):
            del self.phases[idx]
            self._rebuild_phase_controls()
            self._refresh_table()

    def _on_phase_change(self, idx: int):
        """阶段时间输入变化时解析并刷新。"""
        if idx >= len(self.phase_vars):
            return
        sec = self._parse_time_str(self.phase_vars[idx].get())
        if sec is None or sec <= 0:
            # 无效输入，回滚显示
            if idx < len(self.phases):
                self.phase_vars[idx].set(self._sec_to_str(self.phases[idx]))
            return
        self.phases[idx] = sec
        self.phases.sort()
        self._rebuild_phase_controls()
        self._refresh_table()

    def _focus_phase_entry(self, idx: int):
        """聚焦指定阶段的 Entry 并全选文本。"""
        if 0 <= idx < len(self.phase_entries):
            entry = self.phase_entries[idx]
            entry.focus_set()
            entry.select_range(0, "end")

    def _reset_phases(self):
        self.phases = []
        self._rebuild_phase_controls()

    def _on_mousewheel(self, e):
        """滚轮事件：仅当鼠标位于技能筛选区(canvas/ability_frame 及其子级)时滚动 canvas。
        其余区域(Treeview/MRT)交给控件原生处理，不干预。"""
        w = e.widget
        cur = w
        while cur is not None:
            if cur is self.canvas or cur is self.ability_frame:
                self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
                return
            cur = cur.master

    # ---------- 图标 ----------
    def _icons_dir(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")

    def _icon_map_path(self) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_map.json")

    def _load_icon_map(self):
        """从 icon_map.json 加载 name->PNG文件名 映射。自动迁移旧 .jpg 条目为 .png。"""
        path = self._icon_map_path()
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                result = {}
                migrated = False
                for k, v in data.items():
                    v = str(v)
                    if v.lower().endswith(".jpg"):
                        v = os.path.splitext(v)[0] + ".png"
                        migrated = True
                    result[str(k)] = v
                if migrated:
                    try:
                        with open(path, "w", encoding="utf-8") as f:
                            json.dump(result, f, ensure_ascii=False, indent=2)
                    except OSError as e:
                        _warn(f"icon_map.json 迁移写回失败: {e}")
                return result
        except (OSError, json.JSONDecodeError) as e:
            _warn(f"icon_map.json 读取失败: {e}")
        return {}

    def _save_icon_map(self):
        try:
            with open(self._icon_map_path(), "w", encoding="utf-8") as f:
                json.dump(self.name_to_iconfile, f, ensure_ascii=False, indent=2)
        except OSError as e:
            _warn(f"icon_map.json 保存失败: {e}")

    def _update_icon_map_from_events(self):
        """从当前事件中收集 name->PNG图标名，更新映射并保存。

        HTML 的 abilityIcon 是 .jpg 名，存入时转为 .png 名。
        """
        changed = False
        for e in self.events:
            name = e.get("ability_name")
            icon = e.get("ability_icon")
            if name and icon and name not in self.name_to_iconfile:
                png_name = os.path.splitext(icon)[0] + ".png"
                self.name_to_iconfile[name] = png_name
                changed = True
        if changed:
            self._save_icon_map()

    def _get_icon(self, ability_name):
        """返回 ability_name 对应的 tk.PhotoImage（下载+缓存）。无则返回 None。

        映射存的是 PNG 文件名。本地无 PNG 时，从 rpglogs 下载 JPG 到内存
        （BytesIO，JPG 不落盘），PIL 转 PNG 并缩放后保存，供 tk.PhotoImage 显示。
        下载在后台线程进行，主线程不阻塞；下载完成后通过 root.after 回调刷新。
        """
        if not ability_name:
            return None
        if ability_name in self.icon_images:
            return self.icon_images[ability_name]
        icon_png = self.name_to_iconfile.get(ability_name)
        if not icon_png:
            return None
        icons_dir = self._icons_dir()
        local_png = os.path.join(icons_dir, icon_png)
        if os.path.exists(local_png):
            # 本地已有，直接加载
            return self._load_photo(ability_name, local_png)
        # 本地无：派发后台下载（去重）
        if ability_name in self._pending_icons:
            return None
        self._pending_icons.add(ability_name)
        threading.Thread(
            target=self._download_icon_worker,
            args=(ability_name, icon_png, local_png, icons_dir, self._icon_queue),
            daemon=True,
        ).start()
        return None

    @staticmethod
    def _download_icon_worker(name, icon_png, local_png, icons_dir, out_queue):
        """后台线程：下载 JPG → PIL 转 PNG 并缩放 → 落盘。结果放入队列。"""
        try:
            icon_jpg = os.path.splitext(icon_png)[0] + ".jpg"
            url = ICON_BASE_URL + icon_jpg
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            data = urllib.request.urlopen(req, timeout=10).read()
            from PIL import Image
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            try:
                resample = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
            except AttributeError:
                resample = Image.LANCZOS  # type: ignore[attr-defined]
            img = img.resize(ICON_SIZE, resample)
            os.makedirs(icons_dir, exist_ok=True)
            img.save(local_png)
            out_queue.put((name, True, None))
        except Exception as e:
            _warn(f"图标下载失败 {name} ({icon_png}): {e}")
            out_queue.put((name, False, str(e)))

    def _load_photo(self, ability_name, local_png):
        """在主线程加载 PNG 为 tk.PhotoImage 并缓存。失败返回 None。"""
        try:
            photo = tk.PhotoImage(file=local_png)
            self.icon_images[ability_name] = photo
            return photo
        except Exception as e:
            _warn(f"图标加载失败 {ability_name}: {e}")
            return None

    def _poll_icon_queue(self):
        """主线程定期消费后台下载结果，批量刷新图标以避免多次重建闪烁。

        每次轮询把队列里所有已完成的结果一次性取空，仅当本批有成功下载
        且复选框已构建时，重建一次复选框（而非每条结果都重建）。
        """
        any_loaded = False
        try:
            while True:
                name, ok, err = self._icon_queue.get_nowait()
                self._pending_icons.discard(name)
                if ok:
                    icon_png = self.name_to_iconfile.get(name)
                    if icon_png:
                        local_png = os.path.join(self._icons_dir(), icon_png)
                        if os.path.exists(local_png):
                            if self._load_photo(name, local_png) is not None:
                                any_loaded = True
        except queue.Empty:
            pass
        # 本批有新图标加载成功，且复选框已构建 → 仅重建一次
        if any_loaded and self.ability_vars:
            prev_checked = {n for n, v in self.ability_vars.items() if v.get()}
            self._build_checkboxes(prev_checked)
        self.root.after(200, self._poll_icon_queue)

    # ---------- 复选框 ----------
    def _build_checkboxes(self, prev_checked=None):
        """构建技能复选框。

        prev_checked: 若为集合，则按它保留勾选状态（用于右键切换重建）；
                      若为 None（全新解析），默认强力技能全选、其他全不选。
        """
        for w in self.ability_frame.winfo_children():
            w.destroy()
        self.ability_vars = {}

        strong_set = set(self.strong_skills)
        # 分组：强力技能 / 其他技能
        strong = [(n, c) for n, c in self.ability_counts.items() if n in strong_set]
        others = [(n, c) for n, c in self.ability_counts.items() if n not in strong_set]
        # 强力技能按配置顺序（优先级），其他技能按计数降序
        strong.sort(key=lambda kv: (self._strong_priority(kv[0]), -kv[1]))
        others.sort(key=lambda kv: (-kv[1], kv[0]))

        cols = 4

        def _fill(container, items, is_strong):
            for i, (name, cnt) in enumerate(items):
                if prev_checked is None:
                    # 全新解析：强力技能默认勾选，其他默认不勾
                    checked = is_strong
                else:
                    checked = name in prev_checked
                var = tk.BooleanVar(value=checked)
                self.ability_vars[name] = var
                r, c = divmod(i, cols)
                icon = self._get_icon(name)
                cb = ttk.Checkbutton(
                    container,
                    text=f"{name} ({cnt})",
                    image=icon,
                    compound="left" if icon else "none",
                    variable=var,
                    command=self._refresh_table,
                )
                cb.grid(row=r, column=c, sticky="w", padx=4, pady=2)
                # 右键菜单：添加/移除强力技能
                cb.bind("<Button-3>", lambda e, n=name: self._on_skill_right_click(e, n))

        # 强力技能组（置顶，带边框标题）
        if strong:
            lf_strong = ttk.LabelFrame(
                self.ability_frame, text="★ 强力技能（右键移除）", padding=6
            )
            lf_strong.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
            _fill(lf_strong, strong, is_strong=True)

        # 其他技能组（分隔符下方）
        if others:
            lf_others = ttk.LabelFrame(
                self.ability_frame, text="其他技能（右键添加到强力技能）", padding=6
            )
            lf_others.grid(row=1, column=0, sticky="ew", padx=2, pady=2)
            _fill(lf_others, others, is_strong=False)

        self.ability_frame.columnconfigure(0, weight=1)

    def _strong_priority(self, name: str) -> int:
        """强力技能的优先级序号；不在列表中返回大值。"""
        try:
            return self.strong_skills.index(name)
        except ValueError:
            return len(self.strong_skills)

    # ---------- 强力技能配置 ----------
    def _config_path(self) -> str:
        return os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "strong_skills.json"
        )

    def _load_strong_skills(self):
        """从配置文件加载强力技能列表；文件不存在则用默认值并写入。"""
        path = self._config_path()
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, list):
                skills = [str(x) for x in data if str(x)]
                if skills:
                    return skills
        except (OSError, json.JSONDecodeError) as e:
            _warn(f"strong_skills.json 读取失败，使用默认值: {e}")
        # 用默认值并尝试写入
        skills = list(DEFAULT_STRONG_ABILITIES)
        self._save_strong_skills(skills)
        return skills

    def _save_strong_skills(self, skills=None):
        """保存强力技能列表到配置文件。"""
        if skills is None:
            skills = self.strong_skills
        try:
            with open(self._config_path(), "w", encoding="utf-8") as f:
                json.dump(skills, f, ensure_ascii=False, indent=2)
        except OSError as e:
            self.status_var.set(f"保存配置失败：{e}")

    def _on_skill_right_click(self, event, skill_name: str):
        """技能复选框右键：添加/移除强力技能。"""
        menu = tk.Menu(self.root, tearoff=0)
        if skill_name in self.strong_skills:
            menu.add_command(
                label="从强力技能移除", command=lambda: self._toggle_strong(skill_name)
            )
        else:
            menu.add_command(
                label="添加到强力技能", command=lambda: self._toggle_strong(skill_name)
            )
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_strong(self, skill_name: str):
        """切换技能的强力属性，保存配置并重建复选框（保留勾选状态）。"""
        if skill_name in self.strong_skills:
            self.strong_skills.remove(skill_name)
        else:
            self.strong_skills.append(skill_name)
        self._save_strong_skills()
        # 重建复选框，保留当前勾选状态
        prev_checked = {n for n, v in self.ability_vars.items() if v.get()}
        self._build_checkboxes(prev_checked)
        self._refresh_table()

    def select_all(self):
        for v in self.ability_vars.values():
            v.set(True)
        self._refresh_table()

    def select_none(self):
        for v in self.ability_vars.values():
            v.set(False)
        self._refresh_table()

    # ---------- 表格刷新 ----------
    def _refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        selected = {n for n, v in self.ability_vars.items() if v.get()}
        shown = [
            e for e in self.cast_events if (e.get("ability_name") or "?") in selected
        ]
        # 按 display_sec 升序
        shown.sort(key=lambda e: e.get("display_sec", 0.0))

        has_phases = len(self.phases) > 0
        if has_phases:
            # 插入阶段分隔行
            # 计算每个阶段边界的插入点
            phase_iter = iter(enumerate(self.phases))  # (阶段索引, 起始秒)
            next_phase_idx, next_phase_sec = next(phase_iter, (None, None))
            for e in shown:
                sec = e.get("display_sec", 0.0)
                # 在事件前插入所有已越过的阶段分隔行
                while next_phase_sec is not None and next_phase_idx is not None and sec >= next_phase_sec:
                    pnum = next_phase_idx + 2  # P2 对应 phases[0]
                    self.tree.insert(
                        "", "end",
                        values=("", f"───── P{pnum}  ({self._sec_to_str(next_phase_sec)}) ─────"),
                        tags=("phase_div",),
                    )
                    next_phase_idx, next_phase_sec = next(phase_iter, (None, None))
                name = e.get("ability_name", "")
                icon = self._get_icon(name)
                self.tree.insert(
                    "",
                    "end",
                    values=(e.get("display_time", ""), name),
                    image=icon if icon else "",
                )
        else:
            for e in shown:
                name = e.get("ability_name", "")
                icon = self._get_icon(name)
                self.tree.insert(
                    "",
                    "end",
                    values=(e.get("display_time", ""), name),
                    image=icon if icon else "",
                )
        total = len(self.cast_events)
        self.status_var.set(
            f"显示 {len(shown)} / 共 {total} 条 cast · {len(self.ability_counts)} 个技能"
        )
        # 同步刷新 MRT 格式文本
        self._refresh_mrt(shown)

    def _refresh_mrt(self, shown):
        """生成 MRT 格式文本。有阶段时按阶段分块(相对时间)，否则绝对时间。"""
        has_phases = len(self.phases) > 0
        if not has_phases:
            # 单阶段：绝对时间，无块头
            lines = []
            for e in shown:
                sec = int(e.get("display_sec", 0.0))
                name = e.get("ability_name", "")
                lines.append(f"{{time:{sec}}} {name}")
            text = "\n".join(lines)
        else:
            # 多阶段：按阶段分块，块内相对阶段开始时间
            blocks = []  # 每块: (phase_num, [lines])
            cur_phase = 0  # 1-based
            cur_lines = []
            for e in shown:
                sec = e.get("display_sec", 0.0)
                p = self._phase_of(sec)
                if p != cur_phase:
                    # 保存上一块
                    if cur_phase > 0:
                        blocks.append((cur_phase, cur_lines))
                    cur_phase = p
                    cur_lines = []
                rel_sec = int(sec - self._phase_start(p))
                name = e.get("ability_name", "")
                cur_lines.append(f"{{time:{rel_sec}}} {name}")
            if cur_phase > 0:
                blocks.append((cur_phase, cur_lines))
            # 拼装：每块 {PN} PN 头 + 事件 + 空行
            parts = []
            for pnum, plines in blocks:
                parts.append(f"{{P{pnum}}} P{pnum}")
                parts.extend(plines)
                parts.append("")  # 块间空行
            text = "\n".join(parts).rstrip()

        self.mrt_text.configure(state="normal")
        self.mrt_text.delete("1.0", "end")
        self.mrt_text.insert("1.0", text)
        self.mrt_text.configure(state="disabled")

    def copy_mrt(self):
        text = self.mrt_text.get("1.0", "end-1c")
        if not text:
            messagebox.showinfo("提示", "没有可复制的内容。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("已复制", f"已复制 {text.count(chr(10)) + 1} 行到剪贴板。")


def main():
    root = tk.Tk()
    TimelineViewer(root)
    root.mainloop()


if __name__ == "__main__":
    main()
