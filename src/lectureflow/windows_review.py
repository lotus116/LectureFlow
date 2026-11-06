import json
import queue
import threading
import tkinter as tk
import wave
from tkinter import ttk

import sounddevice as sd

from . import review_lookup, review_marks


class Review(tk.Toplevel):
    def __init__(self, parent, lib, lecture):
        super().__init__(parent)
        self.title("书签复习")
        self.geometry("780x510")
        self.lib, self.lecture = lib, lecture
        self.marks = review_marks.listing(lib, lecture)["marks"]
        self.docs = [{"name": "全部课程资料", "id": None}, *review_lookup.documents(lib, lecture)]
        self.cancel = threading.Event()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.result = tk.StringVar()
        self.rows = []
        self.play_errors = queue.SimpleQueue()
        self.after(100, self.play_status)
        self.scope = ttk.Combobox(self, values=[d["name"] for d in self.docs], state="readonly")
        self.scope.pack(fill="x", padx=14, pady=8)
        self.scope.current(0)
        self.scope.bind("<<ComboboxSelected>>", lambda e: self.scope_changed())
        self.markbox = ttk.Combobox(
            self,
            state="readonly",
            values=[
                f"书签 {i + 1}  ·  {m['capture_time']:.1f} 秒" for i, m in enumerate(self.marks)
            ],
        )
        self.markbox.pack(fill="x", padx=14)
        self.markbox.bind("<<ComboboxSelected>>", lambda e: self.show_mark())
        self.sentences = tk.Frame(self)
        self.sentences.pack(fill="x", padx=14, pady=12)
        self.selection = tk.StringVar()
        ttk.Label(self, text="可选：在下方英文中拖选连续的一至四个词").pack(anchor="w", padx=14)
        self.entry = entry = ttk.Entry(
            self, textvariable=self.selection, state="readonly", exportselection=False
        )
        entry.pack(fill="x", padx=14)
        entry.bind("<Return>", lambda e: self.lookup())
        ttk.Button(self, text="查课件拼写", command=self.lookup).pack(anchor="w", padx=14)
        ttk.Button(self, text="回听书签区间", command=self.play).pack(anchor="w", padx=14)
        ttk.Label(self, textvariable=self.result, wraplength=720).pack(anchor="w", padx=14, pady=12)
        if self.marks:
            self.markbox.current(0)
            self.show_mark()
        else:
            self.result.set("这节 Lecture 还没有书签")

    def close(self):
        self.cancel.set()
        self.destroy()

    def show_mark(self):
        self.cancel.set()
        self.result.set("")
        self.detail = review_marks.detail(
            self.lib, self.lecture, self.marks[self.markbox.current()]["id"]
        )
        self.rows = self.detail["captions"]
        for child in self.sentences.winfo_children():
            child.destroy()
        session = self.detail["mark"]["capture_id"]
        from .live_store import configuration

        chosen = configuration(self.lib, session).get("review_document")
        self.scope.current(next((i for i, d in enumerate(self.docs) if d["id"] == chosen), 0))
        self.selected = len(self.rows) - 1
        self.selection.set(self.rows[-1]["english"] if self.rows else "")
        for i, row in enumerate(self.rows):
            prefix = "当前句" if i == len(self.rows) - 1 else "上一句"
            tk.Button(
                self.sentences,
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
                    "UPDATE live_configuration SET config=? WHERE session_id=?",
                    (json.dumps(config), session),
                )
        self.lookup()

    def play_status(self):
        if not self.play_errors.empty():
            self.result.set(self.play_errors.get())
        self.after(100, self.play_status)

    def lookup(self):
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


def play(plan, cancel):
    for i, path in enumerate(plan["files"]):
        with wave.open(path, "rb") as src:
            rate = src.getframerate()
            start = round(plan["offset"] * rate) if i == 0 else 0
            end = src.getnframes()
            if i == len(plan["files"]) - 1 and plan["last_end"] is not None:
                end = min(end, round(plan["last_end"] * rate))
            src.setpos(start)
            with sd.RawOutputStream(
                samplerate=rate, channels=src.getnchannels(), dtype="int16"
            ) as out:
                while src.tell() < end and not cancel.is_set():
                    out.write(src.readframes(min(1024, end - src.tell())))
