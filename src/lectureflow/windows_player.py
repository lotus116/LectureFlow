"""Play logical recordings across pause chunks without merging or modifying the originals."""

import queue
import threading
import tkinter as tk

from . import recordings
from .windows_chrome import Button, playback_scale
from .windows_review import play
from .windows_ui import CANVAS, clock, label


class Player(tk.Toplevel):
    def __init__(self, parent, source, title, duration):
        super().__init__(parent, bg=CANVAS, padx=18, pady=16)
        self.title("录音回放")
        self.transient(parent)
        self.previous_grab = self.grab_current()
        self.grab_set()
        self.geometry("570x180")
        self.files = recordings.source_files(source)
        self.lengths = (
            [c["duration"] for c in recordings.chunks_for(source)]
            if source.name == "segment.json"
            else [duration]
        )
        self.duration = sum(self.lengths)
        self.pos = 0.0
        self.messages = queue.SimpleQueue()
        self.cancel = threading.Event()
        self.running = False
        label(self, title, 14, bold=True).pack(fill="x")
        self.slider = playback_scale(self, self.duration)
        self.slider.pack(fill="x", pady=10)
        self.slider.bind("<ButtonPress-1>", self.begin_seek)
        self.slider.bind("<ButtonRelease-1>", self.end_seek)
        self.slider.bind("<KeyRelease>", self.end_seek)
        self.resume_on_seek = False
        self.status = label(self)
        self.status.pack(side="left")
        self.button = Button(self, text="播放", command=self.toggle)
        self.button.pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.update_clock)
        self.begin()

    def close(self):
        self.cancel.set()
        self.destroy()
        if self.previous_grab and self.previous_grab.winfo_exists():
            self.previous_grab.grab_set()

    def begin_seek(self, event):
        self.resume_on_seek = self.running
        self.pause()

    def end_seek(self, event):
        self.pos = self.slider.get()
        if self.resume_on_seek or self.running:
            self.begin()
        self.resume_on_seek = False

    def toggle(self):
        if self.running:
            self.pause()
        else:
            self.begin()

    def pause(self):
        self.cancel.set()
        self.running = False
        self.button.configure(text="播放")

    def begin(self):
        self.pause()
        if self.pos >= self.duration:
            self.pos = 0
        self.running = True
        self.button.configure(text="暂停")
        skip = self.pos
        index = 0
        while index < len(self.files) - 1 and skip >= self.lengths[index]:
            skip -= self.lengths[index]
            index += 1
        plan = {"files": [str(p) for p in self.files[index:]], "offset": skip, "last_end": None}
        self.cancel = token = threading.Event()

        def output():
            try:
                play(plan, token, lambda n: self.messages.put((token, n)))
                self.messages.put((token, None))
            except Exception as exc:
                self.messages.put((token, exc))

        threading.Thread(target=output, daemon=True).start()

    def update_clock(self):
        while not self.messages.empty():
            token, data = self.messages.get()
            if token is not self.cancel or token.is_set():
                continue
            if isinstance(data, Exception):
                self.pause()
                self.status.configure(text=f"回放失败：{data}")
                return
            if data is None:
                self.pos = self.duration
                self.pause()
            else:
                self.pos = min(self.duration, self.pos + data / 32000)
            self.slider.set(self.pos)
        self.status.configure(text=f"{clock(self.pos)}  /  {clock(self.duration)}")
        self.after(100, self.update_clock)
