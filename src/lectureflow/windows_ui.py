"""Windows presentation of the ice-blue palette used by LFChrome on macOS."""

import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .windows_chrome import Button, scroll


def dark_appearance():
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


DARK = dark_appearance()
INK = "#E5EEFF" if DARK else "#17345C"
MUTED = "#B0C6E2" if DARK else "#496681"
ACCENT = "#3671DB" if DARK else "#2563EB"
ACCENT_INK = "#B5D6FF" if DARK else ACCENT
ICE = "#192B43" if DARK else "#EEF5FC"
PAPER = "#172438" if DARK else "#FFFFFF"
CANVAS = "#101827" if DARK else "#F8FBFF"
EDGE = "#35475D" if DARK else "#C9DBEB"
FONT = "Microsoft YaHei UI"


def clock(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}:{seconds:02d}"


def theme(root):
    root.configure(bg=CANVAS)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(FONT, 9), foreground=INK, background=CANVAS)
    style.configure(
        "Treeview",
        background=ICE,
        fieldbackground=ICE,
        borderwidth=0,
        rowheight=27,
        font=(FONT, 10),
        foreground=INK,
    )
    style.map(
        "Treeview",
        background=[("selected", "#203D66" if DARK else "#D3E6FF")],
        foreground=[("selected", ACCENT_INK)],
    )
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.configure("TScrollbar", arrowsize=11, borderwidth=0, troughcolor=CANVAS, background=EDGE)
    for field in ("TEntry", "TCombobox"):
        style.configure(field, fieldbackground=PAPER, foreground=INK)
        style.map(field, fieldbackground=[("readonly", PAPER)], foreground=[("readonly", INK)])
    style.configure("Treeview.Heading", foreground=INK, background=ICE)
    style.configure("TCheckbutton", background=ICE, font=(FONT, 9))


def label(parent, text="", size=10, *, bold=False, color=INK, **options):
    return tk.Label(
        parent,
        text=text,
        bg=parent["bg"],
        fg=color,
        bd=0,
        font=(FONT, size, "bold" if bold else "normal"),
        anchor="w",
        **options,
    )


def card(parent):
    return tk.Frame(parent, bg=PAPER, highlightbackground=EDGE, highlightthickness=1)


class WorkspaceView:
    def __init__(self, app):
        root = app.root
        theme(root)
        root.geometry("1080x700")
        root.minsize(740, 520)
        self.sidebar = side = tk.Frame(root, bg=ICE, width=206)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=ICE)
        brand.pack(fill="x", padx=18, pady=(20, 17))
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        from PIL import Image, ImageDraw, ImageTk

        with Image.open(assets / "docs/assets/logo.png") as source:
            logo = source.convert("RGBA").resize((96, 96), Image.Resampling.LANCZOS)
        mask = Image.new("L", logo.size)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, 95, 95), radius=22, fill=255)
        logo.putalpha(mask)
        self.logo = ImageTk.PhotoImage(logo.resize((32, 32), Image.Resampling.LANCZOS), master=root)
        root.iconphoto(True, self.logo)
        tk.Label(brand, image=self.logo, bg=ICE, bd=0).pack(side="left", padx=(0, 9))
        label(brand, "LectureFlow", 13, bold=True).pack(side="left")
        label(side, "课程资料库", 9, color=MUTED).pack(anchor="w", padx=18, pady=(4, 2))
        self.add = Button(side, text="新建课程", glyph="plus", command=app.new_course)
        self.add.pack(side="bottom", padx=10, pady=12, anchor="w")
        tree_area = tk.Frame(side, bg=ICE)
        tree_area.pack(fill="both", expand=True, padx=(8, 0), pady=8)
        self.tree = ttk.Treeview(tree_area, show="tree", selectmode="browse")
        self.tree.column("#0", width=180, minwidth=64)
        self.tree.pack(fill="both", expand=True)
        scroll(tree_area, self.tree)
        self.tree.bind("<<TreeviewSelect>>", app.choose)

        self.body = body = tk.Frame(root, bg=CANVAS)
        body.pack(side="left", fill="both", expand=True)
        head = tk.Frame(body, bg=CANVAS)
        head.pack(fill="x", padx=20, pady=(12, 0))
        Button(head, glyph="sidebar", text="", command=self.toggle_sidebar).pack(side="left")
        self.breadcrumb = label(head, "", 9, color=MUTED)
        self.breadcrumb.pack(side="left", padx=4)
        self.importer = Button(head, text="添加课件", glyph="plus", command=app.import_material)
        self.importer.pack(side="right")
        Button(head, text="悬浮字幕", glyph="float", command=app.float_window).pack(
            side="right", padx=3
        )
        self.title = label(body, "", 17, bold=True)
        self.title.pack(fill="x", padx=24, pady=(5, 14))

        island = tk.Canvas(body, bg=CANVAS, height=64, highlightthickness=0, bd=0)
        island.pack(fill="x", padx=20)
        row = tk.Frame(island, bg=ICE)
        window = island.create_window(12, 6, anchor="nw", window=row, height=52)

        def resize(e):
            w, h = e.width, e.height
            island.delete("surface")
            r = 12
            island.create_polygon(
                r,
                1,
                w - r,
                1,
                w - 1,
                1,
                w - 1,
                r,
                w - 1,
                h - r,
                w - 1,
                h - 1,
                w - r,
                h - 1,
                r,
                h - 1,
                1,
                h - 1,
                1,
                h - r,
                1,
                r,
                1,
                1,
                fill=ICE,
                outline=EDGE,
                smooth=True,
                splinesteps=16,
                tags="surface",
            )
            island.tag_lower("surface")
            island.itemconfigure(window, width=max(1, w - 24))

        island.bind("<Configure>", resize)
        self.meter = tk.Canvas(row, bg=ICE, width=45, height=30, highlightthickness=0)
        self.meter.pack(side="left", padx=(1, 4))
        self.samples = [0.0] * 14
        self.level(0)
        self.timer = tk.Label(
            row, text="00:00:00", font=("Segoe UI Semibold", 19), bg=ICE, fg=INK, bd=0
        )
        self.timer.pack(side="left", padx=(2, 9))
        info = tk.Frame(row, bg=ICE)
        info.pack(side="left", fill="y")
        self.mode = Button(info, text="采集设置", glyph="settings", command=app.settings)
        self.mode.pack(anchor="w")
        self.status = label(info, textvariable=app.status, size=9, color=MUTED)
        self.status.pack(anchor="w", padx=7)
        transport = tk.Frame(row, bg=ICE)
        transport.pack(side="right")
        self.start = Button(
            transport, text="开始录音", style="Action.TButton", glyph="record", command=app.start
        )
        self.pause = Button(transport, text="暂停", kind="soft", command=app.pause)
        self.stop = Button(transport, text="结束并保存", command=app.stop)
        self.start.pack(side="right")

        tools = tk.Frame(body, bg=CANVAS)
        tools.pack(fill="x", padx=22, pady=(15, 7))
        self.section = label(tools, "实时转写", 11, bold=True)
        self.section.pack(side="left")
        self.language = tk.StringVar(value="双语")
        segments = tk.Frame(tools, bg=ICE, padx=2, pady=2)
        segments.pack(side="left", padx=10)
        self.segments = []

        def language_changed(value):
            self.language.set(value)
            for widget in self.segments:
                selected = widget["text"] == value
                widget.configure(bg=PAPER if selected else ICE, fg=INK if selected else MUTED)
            if self.rows:
                self.render(list(self.rows.values()))

        for language in ("双语", "中文", "英文"):
            button = tk.Button(
                segments,
                text=language,
                relief="flat",
                overrelief="flat",
                bd=0,
                highlightthickness=0,
                padx=5,
                pady=1,
                font=(FONT, 9),
                bg=PAPER if language == "双语" else ICE,
                fg=INK if language == "双语" else MUTED,
                activebackground=PAPER,
                command=lambda v=language: language_changed(v),
            )
            button.pack(side="left")
            self.segments.append(button)
        self.rows = {}

        self.mark = Button(
            tools, textvariable=app.mark_label, glyph="bookmark", kind="soft", command=app.mark
        )
        self.mark.pack(side="right")
        self.review = Button(tools, text="复习书签", glyph="book", command=app.review)
        self.review.pack(side="right", padx=6)
        self.audio = Button(tools, text="录音", glyph="folder", command=app.audio_folder)
        self.audio.pack(side="right")

        read = card(body)
        read.pack(fill="both", expand=True, padx=22, pady=(0, 18))
        self.text = tk.Text(
            read,
            wrap="word",
            height=4,
            width=1,
            padx=20,
            pady=14,
            bg=PAPER,
            fg=INK,
            bd=0,
            highlightthickness=0,
            state="disabled",
            cursor="arrow",
            selectbackground="#CCE1FF",
        )
        self.text.pack(fill="both", expand=True)
        scroll(read, self.text)
        self.text.bind("<Button-3>", lambda event: app.row_menu(event))

        for tag, font, color, above, below in [
            ("time", ("Segoe UI", 10, "bold"), ACCENT_INK, 5, 5),
            ("en", ("Segoe UI", 14), INK, 0, 3),
            ("zh", (FONT, 13), MUTED if DARK else "#324E69", 0, 15),
            ("hint", (FONT, 11), MUTED, 10, 4),
        ]:
            self.text.tag_configure(
                tag, font=font, foreground=color, spacing1=above, spacing3=below
            )
        self.overlay_mark = None

    def toggle_sidebar(self):
        if self.sidebar.winfo_manager():
            self.sidebar.pack_forget()
        else:
            self.sidebar.pack(side="left", fill="y", before=self.body)

    def render(self, rows=(), hint=""):
        self.rows = {}
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        if hint:
            self.text.insert("end", hint, "hint")
        for row in rows:
            self.insert_row(row, "end")
        self.text.configure(state="disabled")
        self.text.yview_moveto(0)
        self.has_rows = bool(rows)

    def insert_row(self, row, position):
        tag = "row_" + row["id"]
        self.rows[row["id"]] = row
        en = row["english"] if self.language.get() != "中文" else ""
        zh = row["chinese"] if self.language.get() != "英文" else ""

        self.text.insert(
            position,
            clock(row["start"]) + "\n",
            ("time", tag),
            en + "\n" if en else "",
            ("en", tag),
            zh + "\n" if zh else "",
            ("zh", tag),
        )

    def update_row(self, row):
        tail = self.text.yview()[1] >= 0.995
        self.text.configure(state="normal")
        if not self.has_rows:
            self.text.delete("1.0", "end")
        span = self.text.tag_ranges("row_" + row["id"])
        position = str(span[0]) if span else "end"
        if span:
            self.text.delete(*span)
        self.insert_row(row, position)
        self.text.configure(state="disabled")
        self.has_rows = True
        if tail:
            self.text.see("end")

    def course_home(self, lectures):
        self.render()
        if not lectures:
            self.render(hint="本课程还没有录音。点击「开始新 Lecture」开始采集。")
            return
        self.text.configure(state="normal")
        for lecture in reversed(lectures):
            key = lecture["id"]
            tag = "open_" + key
            seconds = sum(row["duration"] for row in lecture["segments"])
            self.text.insert("end", lecture["title"] + "\n", ("en", tag))
            self.text.insert(
                "end", f"{len(lecture['segments'])} 段录音   ·   {clock(seconds)}\n", ("zh", tag)
            )
            self.text.tag_bind(tag, "<Button-1>", lambda e, k=key: self.tree.selection_set(k))
        self.text.configure(state="disabled")

    def level(self, amplitude):
        self.samples = self.samples[1:] + [min(1.0, amplitude * 5)]
        self.meter.delete("all")
        for i, value in enumerate(self.samples):
            h = 1 + int(value * 12)
            self.meter.create_line(
                2 + i * 3, 15 - h, 2 + i * 3, 15 + h, fill=ACCENT if value else "#A2C5E7", width=2
            )

    def controls(self, active, lecture, writable=False):
        for widget in [self.add, self.importer, self.mode]:
            widget.state(["disabled" if active else "!disabled"])
        for widget in [self.review, self.audio]:
            widget.state(["!disabled" if lecture else "disabled"])
        for widget in [self.mark, self.overlay_mark]:
            if widget and widget.winfo_exists():
                widget.state(["!disabled" if writable else "disabled"])
        if active and not self.stop.winfo_manager():
            self.start.pack_forget()
            self.stop.pack(side="right")
            self.pause.pack(side="right", padx=6)
        if not active:
            self.stop.pack_forget()
            self.pause.pack_forget()
            self.start.pack(side="right")
        for widget in [self.pause, self.stop]:
            widget.state(["!disabled" if active and writable else "disabled"])
