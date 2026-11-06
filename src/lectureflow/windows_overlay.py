"""Always-on-top reading window with independent language, opacity and type size."""

import ctypes
import tkinter as tk
from ctypes import wintypes

from .runtime import write_json
from .windows_chrome import Button, scroll
from .windows_ui import FONT


class Overlay(tk.Toplevel):
    def __init__(self, app):
        bg = "#172230"
        super().__init__(app.root, bg=bg, padx=12, pady=5)
        self.app = app
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.title("LectureFlow · 字幕")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        width = min(540, self.winfo_screenwidth() - 32)
        height = 132
        x = (self.winfo_screenwidth() - width) // 2
        y = max(0, self.winfo_screenheight() - height - 88)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(300, 62)
        self.auto_size = True
        self.fit_job = None
        self._size = None
        self.bind("<Configure>", self.shape, add="+")
        self.bind("<Escape>", lambda _: self.close())
        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x")
        drag = tk.Label(bar, text="实时字幕", bg=bg, fg="#A8B9CC", font=(FONT, 9), bd=0)
        drag.pack(side="left", padx=(2, 0))

        def start_drag(e):
            self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

        def move(e):
            x = e.x_root - self._drag[0]
            y = e.y_root - self._drag[1]
            self.geometry(f"{x:+d}{y:+d}")

        for widget in (bar, drag):
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", move)
        self.lang = tk.StringVar(value=app.options.get("overlay_language", "中文"))
        self.size = tk.IntVar(value=app.options.get("overlay_size", 14))
        self.opacity = tk.DoubleVar(value=app.options.get("overlay_opacity", 0.94))
        self.history = tk.BooleanVar(value=app.options.get("overlay_history", False))
        menu = tk.Menu(bar, tearoff=False)
        menu.add_checkbutton(label="显示最近六段", variable=self.history, command=self.changed)
        menu.add_command(label="打开主窗口", command=app.root.deiconify)
        menu.add_command(label="返回最新字幕", command=lambda: self.text.see("end-1c"))
        for lang in ("中文", "英文", "双语"):
            menu.add_radiobutton(label=lang, variable=self.lang, value=lang, command=self.changed)
        for title, var, values in [
            ("字号", self.size, [11, 12, 14, 16, 18, 22, 28, 36]),
            ("不透明度", self.opacity, [0.5, 0.7, 0.86, 0.94, 1.0]),
        ]:
            sub = tk.Menu(menu, tearoff=False)
            menu.add_cascade(label=title, menu=sub)
            for val in values:
                sub.add_radiobutton(label=str(val), variable=var, value=val, command=self.changed)
        Button(bar, glyph="close", kind="overlay", command=self.close).pack(side="right")

        def popup():
            menu.tk_popup(more.winfo_rootx(), more.winfo_rooty() + more.winfo_height())

        more = Button(bar, glyph="more", kind="overlay", command=popup)
        more.pack(side="right", padx=1)
        self.mark = Button(
            bar, textvariable=app.mark_label, glyph="bookmark", kind="overlay", command=app.mark
        )
        self.mark.pack(side="right", padx=3)
        self.text = tk.Text(
            self,
            bg=bg,
            fg="#F1F5FB",
            wrap="word",
            bd=0,
            padx=3,
            pady=1,
            height=2,
            width=1,
            highlightthickness=0,
            state="disabled",
            cursor="arrow",
        )
        self.text.pack(fill="both", expand=True)
        scroll(self.text, self.text)
        self.attributes("-alpha", self.opacity.get())
        grip = tk.Canvas(
            self, width=10, height=10, bg=bg, bd=0, highlightthickness=0, cursor="size_nw_se"
        )
        for delta in (0, 4):
            grip.create_line(2 + delta, 9, 9, 2 + delta, fill="#526479")
        grip.place(relx=1, rely=1, anchor="se")

        def resize(event):
            self.auto_size = False
            width = max(300, event.x_root - self.winfo_x() + 4)
            height = max(92, event.y_root - self.winfo_y() + 4)
            self.geometry(f"{width}x{height}")

        grip.bind("<B1-Motion>", resize)
        self.after_idle(lambda: self.text.see("end-1c"))

    def shape(self, event):
        if event.widget is not self or self._size == (event.width, event.height):
            return
        self._size = (event.width, event.height)
        # Ownership of a successful SetWindowRgn transfers to Windows.
        user, gdi = ctypes.windll.user32, ctypes.windll.gdi32
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        gdi.CreateRoundRectRgn.argtypes = (ctypes.c_int,) * 6
        gdi.CreateRoundRectRgn.restype = wintypes.HANDLE
        user.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HANDLE, wintypes.BOOL)
        gdi.DeleteObject.argtypes = (wintypes.HANDLE,)
        region = gdi.CreateRoundRectRgn(0, 0, event.width + 1, event.height + 1, 16, 16)
        if region and not user.SetWindowRgn(user.GetAncestor(self.winfo_id(), 2), region, True):
            gdi.DeleteObject(region)

    def close(self):
        self.app.root.deiconify()
        self.destroy()

    def changed(self):
        self.app.options.update(
            overlay_history=self.history.get(),
            overlay_language=self.lang.get(),
            overlay_size=self.size.get(),
            overlay_opacity=self.opacity.get(),
        )
        write_json(self.app.pref_path, self.app.options)
        self.attributes("-alpha", self.opacity.get())
        self.render(self.app.recent)

    def render(self, rows):
        tail = self.text.yview()[1] >= 0.995
        before = self.text.yview()[0]
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.tag_configure("primary", font=(FONT, self.size.get()), spacing3=4)
        self.text.tag_configure(
            "secondary", font=("Segoe UI", max(10, self.size.get() - 3)), foreground="#BCD0E7"
        )
        if not rows:
            self.text.insert(
                "end", "等待讲话…" if self.app.cap else "字幕将显示在这里", "secondary"
            )
        if not self.history.get():
            # Keep a late first translation paired with its own English.
            rows = rows[-2:] if self.app.cap and self.app.show_previous else rows[-1:]
        for i, row in enumerate(rows):
            en, zh = row["english"], row["chinese"]
            if self.lang.get() == "双语" and en and zh:
                self.text.insert("end", en + "\n", "secondary")
            text = (en or zh) if self.lang.get() == "英文" else (zh or en)
            self.text.insert("end", text + ("\n" if i + 1 < len(rows) else ""), "primary")
        self.text.configure(state="disabled")
        if tail:
            self.text.see("end")
        else:
            self.text.yview_moveto(before)
        if self.auto_size and self.fit_job is None:
            self.fit_job = self.after_idle(self.fit_text)

    def fit_text(self):
        self.fit_job = None
        if not self.auto_size or not self.winfo_exists():
            return
        measured = int(self.tk.call(self.text._w, "count", "-update", "-ypixels", "1.0", "end"))
        content_top = self.text.winfo_y()
        wanted = max(62, min(172 if self.history.get() else 140, content_top + measured + 10))
        height = self.winfo_height()
        if height <= 1 or content_top < 6:
            self.fit_job = self.after(30, self.fit_text)
            return
        if wanted != height:
            bottom = self.winfo_y() + height
            self.geometry(f"{self.winfo_width()}x{wanted}{self.winfo_x():+d}{bottom - wanted:+d}")
