#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
施法序列查看器 GUI（tkinter）。

从剪贴板或文件解析 rpglogs 时间轴，按技能勾选过滤，显示 cast 时间轴。

用法:
    python timeline_gui.py
"""
import json
import os
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from parse_timeline import parse_content

# 默认强力技能（首次运行写入配置文件，之后以配置文件为准）
DEFAULT_STRONG_ABILITIES = ["万灵之召", "宁静", "激活"]


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

        self._build_ui()

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
        # 鼠标滚轮
        self.canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"),
        )

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
        self.tree = ttk.Treeview(
            tab_timeline, columns=("time", "ability"), show="headings", selectmode="browse"
        )
        self.tree.heading("time", text="时间")
        self.tree.heading("ability", text="技能")
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

        # 重置阶段
        self._reset_phases()
        self._build_checkboxes()
        self._refresh_table()
        self.status_var.set(
            f"共 {len(self.cast_events)} 条 cast · {len(self.ability_counts)} 个技能"
        )

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
        # 重置阶段
        self.phases = []
        self._rebuild_phase_controls()

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
            self.phase_entries.append(var)
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
        if idx >= len(self.phase_entries):
            return
        sec = self._parse_time_str(self.phase_entries[idx].get())
        if sec is None or sec <= 0:
            # 无效输入，回滚显示
            if idx < len(self.phases):
                self.phase_entries[idx].set(self._sec_to_str(self.phases[idx]))
            return
        self.phases[idx] = sec
        self.phases.sort()
        self._rebuild_phase_controls()
        self._refresh_table()

    def _focus_phase_entry(self, idx: int):
        """聚焦指定阶段的 Entry 并全选文本。"""
        # phase_frame 内控件顺序: Label(P1), [cell...], Button(+)
        # 每个 cell 是 Frame，内含 Label/Entry/Button；Entry 是第2个子控件
        cells = [w for w in self.phase_frame.winfo_children()
                 if w.winfo_class() == "TFrame"]
        # 第一个 TFrame 可能是 phase_frame 自身的子 Frame？这里 cells 是直接子级
        # 实际 phase_frame 直接子级: Label(P1), cell0, cell1, ..., Button
        # cell 是 ttk.Frame，TFrame 类
        real_cells = [w for w in self.phase_frame.winfo_children()
                      if w.winfo_class() == "TFrame" and w.winfo_children()]
        if idx < len(real_cells):
            # cell 内: Label, Entry, Button → Entry 是类 TEntry
            entries = [c for c in real_cells[idx].winfo_children()
                       if c.winfo_class() == "TEntry"]
            if entries:
                entries[0].focus_set()
                entries[0].select_range(0, "end")

    def _reset_phases(self):
        self.phases = []
        self._rebuild_phase_controls()

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
                cb = ttk.Checkbutton(
                    container,
                    text=f"{name} ({cnt})",
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
        except (OSError, json.JSONDecodeError):
            pass
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
                self.tree.insert(
                    "",
                    "end",
                    values=(e.get("display_time", ""), e.get("ability_name", "")),
                )
        else:
            for e in shown:
                self.tree.insert(
                    "",
                    "end",
                    values=(e.get("display_time", ""), e.get("ability_name", "")),
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
