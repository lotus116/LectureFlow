"""Native Windows trial desktop entry point."""

import argparse
import os
import queue
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import documents, filelock, live_store, review_marks
from .library import Library
from .runtime import write_json
from .windows_capture import RATE, Capture
from .windows_review import Review


class App:
    def __init__(self, root, lib, args):
        self.root, self.lib, self.args = root, lib, args
        self.cap = self.overlay = self.current = None
        self.closing = False
        self.caption = tk.StringVar(value="准备就绪")
        self.status = tk.StringVar()
        self.mark_label = tk.StringVar(value="标记当前")
        root.title("LectureFlow · Windows 试用版")
        root.geometry("840x540")
        panel = ttk.Frame(root, padding=18)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="LectureFlow", font=("Segoe UI", 22, "bold")).pack(anchor="w")
        nav = ttk.Frame(panel)
        nav.pack(fill="x", pady=12)
        self.course = ttk.Combobox(nav, state="readonly", width=20)
        self.course.pack(side="left")
        self.course.bind("<<ComboboxSelected>>", lambda e: self.load_course())
        self.add = ttk.Button(nav, text="新建课程", command=self.new_course)
        self.add.pack(side="left")
        self.lecture = ttk.Combobox(nav, state="readonly", width=26)
        self.lecture.pack(side="left", padx=6)
        self.lecture.bind("<<ComboboxSelected>>", lambda e: self.load_lecture())
        self.importer = ttk.Button(nav, text="添加课件", command=self.import_material)
        self.importer.pack(side="left")
        self.cpu = tk.BooleanVar(value=args.cpu)
        self.mode = ttk.Checkbutton(panel, text="仅用 CPU 翻译", variable=self.cpu)
        self.mode.pack(anchor="w")
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x", pady=10)
        self.start_button = ttk.Button(buttons, text="开始录音", command=self.start)
        self.pause_button = ttk.Button(buttons, text="暂停", command=self.pause)
        self.stop_button = ttk.Button(buttons, text="结束并保存", command=self.stop)
        for button in [self.start_button, self.pause_button, self.stop_button]:
            button.pack(side="left")
        ttk.Button(buttons, textvariable=self.mark_label, command=self.mark).pack(side="left")
        ttk.Button(buttons, text="置顶字幕", command=self.float_window).pack(side="left")
        ttk.Button(buttons, text="复习书签", command=self.review).pack(side="left")
        ttk.Button(buttons, text="录音文件夹", command=self.audio_folder).pack(side="left")
        ttk.Label(panel, textvariable=self.status).pack(anchor="w", pady=6)
        ttk.Separator(panel).pack(fill="x", pady=6)
        ttk.Label(
            panel,
            textvariable=self.caption,
            wraplength=775,
            justify="left",
            font=("Microsoft YaHei UI", 14),
        ).pack(fill="both", expand=True)
        self.reload_courses()
        for shortcut, action in [
            ("<F5>", self.start),
            ("<F6>", self.pause),
            ("<F7>", self.mark),
            ("<F8>", self.stop),
            ("<F9>", self.review),
            ("<F10>", self.float_window),
        ]:
            root.bind(shortcut, lambda event, action=action: action())
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.report_callback_exception = lambda typ, err, tb: messagebox.showerror(
            "LectureFlow", str(err)
        )
        root.after(100, self.poll)

    def reload_courses(self, course_id=None):
        self.courses = self.lib.overview()["courses"]
        if not self.courses:
            self.lib.create_course("我的课程")
            self.courses = self.lib.overview()["courses"]
        self.course.configure(values=[c["name"] for c in self.courses])
        self.course.current(
            next((i for i, c in enumerate(self.courses) if c["id"] == course_id), 0)
        )
        self.load_course()

    def new_course(self):
        name = simpledialog.askstring("新建课程", "课程名称")
        if name:
            self.reload_courses(self.lib.create_course(name)["id"])

    def load_course(self):
        course = self.courses[self.course.current()]
        self.course_id = course["id"]
        self.lectures = course["lectures"]
        self.lecture.configure(values=[r["title"] for r in self.lectures])
        if self.lectures:
            self.lecture.current(len(self.lectures) - 1)
        else:
            self.lecture.set("")
        self.load_lecture()

    def load_lecture(self):
        self.lecture_id = self.lectures[self.lecture.current()]["id"] if self.lectures else None
        rows = live_store.history(self.lib, self.lecture_id)["captions"] if self.lecture_id else []
        self.caption.set(
            "\n\n".join(r["english"] + "\n" + r["chinese"] for r in rows[-2:]) or "准备就绪"
        )

    def import_material(self):
        for name in filedialog.askopenfilenames(filetypes=[("课件", "*.pptx *.pdf *.ppt")]):
            documents.import_document(self.lib, "course", self.course_id, Path(name))
            self.status.set("已导入 " + Path(name).name)

    def start(self):
        if self.cap:
            return
        title = datetime.now().strftime("Lecture %m-%d %H时%M分%S秒")
        lec = self.lib.create_lecture(self.course_id, title)
        self.reload_courses(self.course_id)
        self.lecture_id = lec["id"]
        session = live_store.create(self.lib, self.lecture_id)["id"]
        self.cap = Capture(
            self.lib, self.lecture_id, session, self.args.assets, self.args.replay, self.cpu.get()
        )
        self.current = None
        self.marked = set()
        self.first_en = self.first_zh = None
        self.samples = []
        self.mark_label.set("标记当前")
        self.caption.set("正在加载本地模型")
        for widget in [
            self.course,
            self.lecture,
            self.importer,
            self.add,
            self.start_button,
            self.mode,
        ]:
            widget.configure(state="disabled")

    def pause(self):
        if self.cap and not self.cap.finished.is_set():
            if self.cap.paused.is_set():
                self.cap.paused.clear()
                self.pause_button.configure(text="暂停")
            else:
                self.cap.paused.set()
                self.pause_button.configure(text="继续")

    def mark(self):
        if self.cap and self.cap.frames and not self.cap.finished.is_set():
            cid = self.current["id"] if self.current else None
            review_marks.create(
                self.lib, self.lecture_id, self.cap.session, self.cap.audio_time(), caption_id=cid
            )
            self.marked.add(cid)
            self.mark_label.set("已标记")

    def float_window(self):
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.lift()
            return
        self.overlay = tk.Toplevel(self.root)
        self.overlay.title("LectureFlow · 字幕")
        self.overlay.attributes("-topmost", True)
        ttk.Label(
            self.overlay,
            textvariable=self.caption,
            wraplength=580,
            font=("Microsoft YaHei UI", 14),
            padding=12,
        ).pack()
        ttk.Button(self.overlay, textvariable=self.mark_label, command=self.mark).pack()

    def review(self):
        if self.lecture_id:
            return Review(self.root, self.lib, self.lecture_id)

    def audio_folder(self):
        if self.lecture_id:
            os.startfile(self.lib.lecture_path(self.lecture_id) / "Audio")

    def stop(self):
        if self.cap:
            self.cap.stop.set()

    def close(self):
        if self.cap:
            self.closing = True
            self.stop()
        else:
            self.root.destroy()

    def poll(self):
        try:
            if self.cap:
                self.refresh()
        finally:
            if self.root.winfo_exists():
                self.root.after(100, self.poll)

    def refresh(self):
        cap = self.cap
        while True:
            try:
                event, at = cap.events.get_nowait()
            except queue.Empty:
                break
            live_store.save(self.lib, cap.session, event, english=True, translate=True)
            if self.current is None or event["start"] >= self.current["start"]:
                self.current = event
                self.caption.set(event["english"] + "\n\n" + event["chinese"])
                self.mark_label.set("已标记" if event["id"] in self.marked else "标记当前")
                if self.first_en is None and event["english"]:
                    self.first_en = time.monotonic() - cap.started
                if self.first_zh is None and event["chinese"]:
                    self.first_zh = time.monotonic() - cap.started
        lag = (cap.frames - cap.consumed) / RATE
        elapsed = time.monotonic() - cap.started
        if not self.samples or elapsed - self.samples[-1][0] >= 10:
            self.samples.append((round(elapsed, 2), round(lag, 2)))
        state = (
            "正在保存" if cap.finished.is_set() else ("暂停中" if cap.paused.is_set() else "录音中")
        )
        self.status.set(
            f"{state} · 原音 {cap.frames / RATE:.1f} 秒 · 待识别 {lag:.1f} 秒  "
            + " ".join(cap.errors)
        )
        if not cap.writer.is_alive() and not cap.reader.is_alive() and cap.events.empty():
            self.cap = None
            try:
                cap.register(self.lib, self.lecture_id)
                self.status.set(
                    f"已保存 {cap.frames / RATE:.1f} 秒 · 输入溢出 {cap.overflows} 次  "
                    + " ".join(cap.errors)
                )
                if self.args.metrics:
                    write_json(
                        self.args.metrics,
                        dict(
                            seconds=cap.frames / RATE,
                            lag=lag,
                            elapsed=elapsed,
                            first_en=self.first_en,
                            first_zh=self.first_zh,
                            samples=self.samples,
                            overflows=cap.overflows,
                            errors=cap.errors,
                            progress=getattr(cap, "progress", {}),
                            session=cap.session,
                        ),
                    )
            finally:
                for widget in [self.importer, self.add, self.start_button, self.mode]:
                    widget.configure(state="normal")
                for widget in [self.course, self.lecture]:
                    widget.configure(state="readonly")
                self.pause_button.configure(text="暂停")
                if self.closing:
                    self.root.after_idle(self.root.destroy)


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--library", type=Path, default=Path(os.environ["LOCALAPPDATA"]) / "LectureFlow" / "Library"
    )
    p.add_argument("--assets", type=Path, default=Path(sys.executable).resolve().parent)
    p.add_argument("--replay", type=Path)
    p.add_argument("--metrics", type=Path)
    p.add_argument("--cpu", action="store_true")
    args = p.parse_args()
    args.library.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    with (args.library / ".desktop.lock").open("a+") as lock:
        try:
            filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
        except BlockingIOError:
            messagebox.showerror("LectureFlow", "此资料库已在另一个窗口中打开")
            return
        with Library(args.library) as lib:
            App(root, lib, args)
            root.mainloop()


if __name__ == "__main__":
    main()
