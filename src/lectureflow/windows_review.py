import json
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import sounddevice as sd

from . import review_lookup, review_marks
from .windows_chrome import Button, scroll
from .windows_ui import CANVAS, FONT, ICE, INK, PAPER, label


class Review(tk.Toplevel):
    def __init__(self, parent, lib, lecture):
        super().__init__(parent, bg=CANVAS, padx=18, pady=12)
        self.title("书签复习")
        self.geometry("820x640")
        self.minsize(580, 500)
        label(self, "书签复习", 17, bold=True).pack(anchor="w", pady=(0, 8))
        self.lib, self.lecture = lib, lecture
        lib.db.execute(
            "CREATE TABLE IF NOT EXISTS windows_review_scope (lecture_id TEXT PRIMARY KEY "
            "REFERENCES lectures(id) ON DELETE CASCADE, document_id TEXT)"
        )
        self.marks = review_marks.listing(lib, lecture)["marks"]
        self.docs = [{"name": "全部课程资料", "id": None}, *review_lookup.documents(lib, lecture)]
        self.cancel = threading.Event()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.result = tk.StringVar()
        self.source = None
        self.rows = []
        self.play_errors = queue.SimpleQueue()
        self.after(100, self.play_status)
        top = tk.Frame(self, bg=CANVAS)
        top.pack(fill="x")
        label(top, "查询范围").pack(side="left")
        self.scope = ttk.Combobox(top, values=[d["name"] for d in self.docs], state="readonly")
        self.scope.pack(side="left", padx=8, fill="x", expand=True)
        self.scope.current(0)
        self.scope.bind("<<ComboboxSelected>>", lambda e: self.scope_changed())
        self.removed = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="已移除", variable=self.removed, command=self.refresh_marks).pack(
            side="right"
        )
        actions = tk.Frame(self, bg=CANVAS)
        actions.pack(fill="x", pady=8)
        self.markbox = ttk.Combobox(actions, state="readonly", width=34)
        self.markbox.pack(side="left", fill="x", expand=True)
        self.markbox.bind("<<ComboboxSelected>>", lambda e: self.show_mark())
        self.reviewed = Button(actions, text="标为已复习", command=self.toggle_reviewed)
        self.reviewed.pack(side="left", padx=4)
        self.remove = Button(actions, text="移除标记", command=self.toggle_removed)
        self.remove.pack(side="left")
        self.sentences = tk.Frame(self, bg=CANVAS)
        self.sentences.pack(fill="x", pady=6)
        self.selection = tk.StringVar()
        ttk.Label(self, text="可选：在下方英文中拖选连续的一至四个词").pack(anchor="w", padx=14)
        self.entry = entry = ttk.Entry(
            self, textvariable=self.selection, state="readonly", exportselection=False
        )
        entry.pack(fill="x", padx=14)
        entry.bind("<Return>", lambda e: self.lookup())
        tools = tk.Frame(self, bg=CANVAS)
        tools.pack(fill="x", padx=14, pady=8)
        Button(tools, text="查课件拼写", style="Action.TButton", command=self.lookup).pack(
            side="left"
        )
        Button(tools, text="回听书签区间", command=self.play).pack(side="left", padx=6)
        Button(tools, text="停止回听", command=lambda: self.cancel.set()).pack(side="left")
        self.open_source = Button(
            tools, text="打开原课件", command=lambda: os.startfile(self.source)
        )
        self.open_source.pack(side="right")
        self.open_source.state(["disabled"])
        self.evidence = tk.Text(
            self,
            wrap="word",
            font=(FONT, 11),
            fg=INK,
            bg=PAPER,
            bd=0,
            padx=12,
            pady=10,
            width=1,
            height=3,
        )
        self.evidence.pack(fill="both", expand=True)
        scroll(self.evidence, self.evidence)
        self.result.trace_add("write", self.paint_result)
        self.refresh_marks()

    def paint_result(self, *_):
        self.evidence.configure(state="normal")
        self.evidence.delete("1.0", "end")
        self.evidence.insert("end", self.result.get())
        self.evidence.configure(state="disabled")

    def refresh_marks(self, key=None):
        self.marks = review_marks.listing(self.lib, self.lecture, removed=self.removed.get())[
            "marks"
        ]
        self.markbox.configure(
            values=[
                f"{'已复习' if m['reviewed'] else '待复习'} · 书签 {i + 1}"
                f" · {m['capture_time']:.1f} 秒"
                for i, m in enumerate(self.marks)
            ]
        )
        self.reviewed.state(["!disabled" if self.marks and not self.removed.get() else "disabled"])
        self.remove.configure(text="恢复标记" if self.removed.get() else "移除标记")
        self.remove.state(["!disabled" if self.marks else "disabled"])
        if self.marks:
            self.markbox.current(next((i for i, m in enumerate(self.marks) if m["id"] == key), 0))
            self.show_mark()
        else:
            self.cancel.set()
            self.rows = []
            self.open_source.state(["disabled"])
            self.markbox.set("")
            self.selection.set("")
            for widget in self.sentences.winfo_children():
                widget.destroy()
            self.result.set("这里还没有书签")

    def toggle_reviewed(self):
        mark = self.marks[self.markbox.current()]
        review_marks.update(self.lib, self.lecture, mark["id"], reviewed=not mark["reviewed"])
        self.refresh_marks(mark["id"])

    def toggle_removed(self):
        mark = self.marks[self.markbox.current()]
        restore = self.removed.get()
        if restore or messagebox.askyesno(
            "移除书签", "移除此标记？可在已移除列表恢复。", parent=self
        ):
            review_marks.update(
                self.lib, self.lecture, mark["id"], restore=restore, remove=not restore
            )
            self.refresh_marks()

    def close(self):
        self.cancel.set()
        self.destroy()

    def show_mark(self):
        self.cancel.set()
        self.source = None
        self.open_source.state(["disabled"])
        self.result.set("")
        self.detail = review_marks.detail(
            self.lib, self.lecture, self.marks[self.markbox.current()]["id"]
        )
        self.reviewed.configure(
            text="设为待复习" if self.detail["mark"]["reviewed"] else "标为已复习"
        )
        self.rows = self.detail["captions"]
        for child in self.sentences.winfo_children():
            child.destroy()
        session = self.detail["mark"]["capture_id"]
        from .live_store import configuration

        pref = self.lib.db.execute(
            "SELECT document_id FROM windows_review_scope WHERE lecture_id=?", (self.lecture,)
        ).fetchone()
        chosen = pref[0] if pref else configuration(self.lib, session).get("review_document")
        self.scope.current(next((i for i, d in enumerate(self.docs) if d["id"] == chosen), 0))
        self.selected = len(self.rows) - 1
        self.selection.set(self.rows[-1]["english"] if self.rows else "")
        for i, row in enumerate(self.rows):
            prefix = "当前句" if i == len(self.rows) - 1 else "上一句"
            tk.Button(
                self.sentences,
                bg=PAPER,
                fg=INK,
                activebackground=ICE,
                relief="flat",
                padx=12,
                pady=6,
                font=(FONT, 10),
                anchor="w",
                justify="left",
                wraplength=700,
                text=f"{prefix}  {row['english']}\n{row['chinese']}",
                command=lambda i=i: self.select(i),
            ).pack(fill="x")
        self.result.set(self.detail["audio_message"] or "点击一句查课件拼写")

    def select(self, index):
        self.selected = index
        self.entry.selection_clear()
        self.selection.set(self.rows[index]["english"])
        self.lookup()

    def scope_changed(self):
        if self.marks:
            session = self.marks[self.markbox.current()]["capture_id"]
            from .live_store import configuration

            config = configuration(self.lib, session)
            config["review_document"] = self.docs[self.scope.current()]["id"]
            with self.lib.transaction():
                self.lib.db.execute(
                    "INSERT OR REPLACE INTO windows_review_scope VALUES(?,?)",
                    (self.lecture, config["review_document"]),
                )
                self.lib.db.execute(
                    "UPDATE live_configuration SET config=? WHERE session_id=?",
                    (json.dumps(config), session),
                )
        self.lookup()

    def play_status(self):
        if not self.play_errors.empty():
            self.result.set(self.play_errors.get())
        self.after(100, self.play_status)

    def lookup(self):
        self.source = None
        self.open_source.state(["disabled"])
        self.result.set("")
        if not self.rows:
            return
        row = self.rows[self.selected]
        try:
            focus = ""
            if self.entry.selection_present():
                focus = self.selection.get()[
                    self.entry.index("sel.first") : self.entry.index("sel.last")
                ]
            result = review_lookup.lookup(
                self.lib,
                self.lecture,
                {
                    "document": self.docs[self.scope.current()]["id"],
                    "sentence": row["english"],
                    "neighbor": " ".join(r["english"] for r in self.rows if r is not row),
                    "selection": focus.strip(),
                },
            )
            lines = []
            match = result["spelling"]
            if match:
                source = match["source"]
                lines.append(
                    f"{match['old']} → {match['term']}\n{source['name']} · 第 "
                    f"{source['page']} 页\n{match['line']}"
                )
            for page in result["pages"]:
                lines.append(f"{page['name']} · 第 {page['page']} 页\n{page['text'][:320]}")
            source = match["source"] if match else next(iter(result["pages"]), None)
            if source:
                self.source = source["path"]
                self.open_source.state(["!disabled"])
            self.result.set("\n\n".join(lines) or "所选资料中没有匹配原文")
        except ValueError as error:
            self.result.set(str(error))

    def play(self):
        if not self.marks:
            return
        plan = self.detail["audio"]
        if not plan:
            self.result.set(self.detail["audio_message"])
            return
        self.cancel.set()
        self.cancel = threading.Event()

        def output(cancel):
            try:
                play(plan, cancel)
            except Exception as error:
                self.play_errors.put(f"回听失败：{error}")

        threading.Thread(target=output, args=(self.cancel,), daemon=True).start()


def play(plan, cancel, progress=None):
    import imageio_ffmpeg

    for i, path in enumerate(plan["files"]):
        if cancel.is_set():
            break
        start = plan["offset"] if i == 0 else 0
        end = plan["last_end"] if i == len(plan["files"]) - 1 else None
        args = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-nostdin",
            "-v",
            "error",
            "-ss",
            str(start),
            "-i",
            path,
        ]
        if end is not None:
            if end <= start:
                continue
            args.extend(["-t", str(end - start)])
        args.extend(["-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"])
        with subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        ) as proc:
            try:
                with sd.RawOutputStream(samplerate=16000, channels=1, dtype="int16") as out:
                    while not cancel.is_set() and (chunk := proc.stdout.read(2048)):
                        out.write(chunk)
                        if progress:
                            progress(len(chunk))
                if not cancel.is_set() and proc.wait(timeout=5):
                    raise ValueError("音频无法解码")
            finally:
                if proc.poll() is None:
                    proc.kill()
