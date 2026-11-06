"""Capture preferences shared by the desktop controls, worker and floating captions."""

import json
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import sounddevice as sd

from .runtime import write_json
from .windows_chrome import Button
from .windows_ui import CANVAS, label
from .windows_weights import fetch_large, large_path

DEFAULT = dict(
    audio=True,
    english=True,
    chinese=True,
    language="English",
    asr="asr-small",
    device="",
    cpu=False,
    floating=True,
    font_size=12,
    force_ocr=False,
    overlay_history=False,
    overlay_language="中文",
    overlay_size=14,
    overlay_opacity=0.94,
)


def load(path):
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return DEFAULT.copy()
    return (
        {k: values.get(k, v) for k, v in DEFAULT.items()}
        if isinstance(values, dict)
        else DEFAULT.copy()
    )


def inputs():
    devices = {}
    for index, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"]:
            api = sd.query_hostapis(dev["hostapi"])["name"]
            devices[f"{dev['name']} · {api}"] = index
    return devices


def show(app):
    win = tk.Toplevel(app.root, bg=CANVAS, padx=20, pady=16)
    win.title("采集设置")
    win.transient(app.root)
    win.grab_set()
    label(win, "采集设置", 17, bold=True).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
    )
    values = app.options.copy()
    flags = {}
    row = 1
    for key, name in [
        ("audio", "保存原音"),
        ("english", "显示并保存英文"),
        ("chinese", "显示并保存中文"),
        ("cpu", "仅用 CPU 翻译"),
        ("floating", "开始时打开悬浮字幕"),
        ("force_ocr", "PDF 强制 OCR"),
    ]:
        var = tk.BooleanVar(win, value=values[key])
        flags[key] = var
        ttk.Checkbutton(win, text=name, variable=var).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=2
        )
        row += 1
    boxes = {}
    opts = [
        ("language", "讲话语言", ["English", "auto"]),
        ("asr", "识别模型", ["asr-small", "asr-large"]),
        ("device", "麦克风", ["系统默认", *inputs()]),
        ("font_size", "字幕字号", [str(i) for i in (12, 14, 16, 18, 20, 24)]),
    ]
    for key, title, choices in opts:
        label(win, title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
        box = ttk.Combobox(win, values=choices, state="readonly", width=38)
        boxes[key] = box
        val = str(values[key])
        box.set(val if val in choices else choices[0])
        box.grid(row=row, column=1, sticky="ew")
        row += 1
    progress = tk.StringVar(value="小模型 0.6B · 大模型 1.7B")
    label(win, textvariable=progress, size=9).grid(row=row, column=0, columnspan=2, sticky="w")
    row += 1
    cancel = threading.Event()
    running = None

    def close():
        cancel.set()
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", close)

    def install():
        nonlocal running
        if running and running.is_alive():
            cancel.set()
            return
        cancel.clear()
        state = ["正在连接模型仓库"]

        def download():
            try:
                fetch_large(
                    lambda n, t: state.__setitem__(0, f"已下载 {n / 1e6:.0f} / {t / 1e6:.0f} MB"),
                    cancel.is_set,
                )
                state[0] = "1.7B 模型已就绪"
            except Exception as e:
                state[0] = str(e)

        running = threading.Thread(target=download, daemon=True)
        running.start()

        def tick():
            progress.set(state[0])
            download_button.configure(text="暂停下载" if running.is_alive() else "安装 / 继续下载")
            if running.is_alive():
                win.after(250, tick)

        tick()

    download_button = Button(win, text="安装 / 继续下载 1.7B", command=install)
    download_button.grid(row=row, column=0, sticky="w", pady=10)

    def save():
        result = app.options.copy()
        result.update({k: v.get() for k, v in flags.items()})
        result.update({k: b.get() for k, b in boxes.items()})
        if not any(result[k] for k in ("audio", "english", "chinese")):
            messagebox.showwarning("采集设置", "至少选择一项保存内容", parent=win)
            return
        if result["asr"] == "asr-large" and not large_path().is_dir():
            messagebox.showwarning("采集设置", "请先安装 1.7B 模型", parent=win)
            return
        result["font_size"] = int(result["font_size"])
        if result["device"] == "系统默认":
            result["device"] = ""
        write_json(app.pref_path, result)
        app.options = result
        app.cpu.set(result["cpu"])
        app.apply_options()
        close()

    Button(win, text="保存设置", style="Action.TButton", command=save).grid(
        row=row, column=1, sticky="e"
    )
