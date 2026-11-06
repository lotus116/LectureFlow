"""Windows equivalents of the macOS menu-extra and external file drop targets."""

import ctypes
import hashlib
import os
import queue
import sys
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path

from PIL import Image
from pystray import Icon, Menu, MenuItem
from tkinterdnd2 import DND_FILES


class Shell:
    def __init__(self, app):
        self.app = app
        self.actions = queue.SimpleQueue()

        def entry(title, fn, default=False):
            return MenuItem(title, lambda *_: self.actions.put(fn), default=default)

        menu = Menu(
            entry("打开主窗口", self.show, True),
            entry("显示 / 隐藏悬浮字幕", self.overlay),
            entry("仅显示悬浮字幕", app.commands.only_overlay),
            Menu.SEPARATOR,
            entry("开始 / 暂停 / 继续", lambda: app.pause() if app.cap else app.start()),
            entry("结束并保存", app.stop),
            entry("退出 LectureFlow", app.close),
        )
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
        with Image.open(assets / "docs/assets/logo.png") as im:
            self.icon = Icon("LectureFlow", im.resize((64, 64)), "LectureFlow", menu)
        self.icon.run_detached()
        self.power_callback = stop_on_suspend(app)
        for widget in (app.view.tree, app.view.text):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self.drop)

    def show(self):
        self.app.root.deiconify()
        self.app.root.lift()

    def overlay(self):
        if self.app.overlay and self.app.overlay.winfo_exists():
            self.app.overlay.close()
        else:
            self.app.float_window()

    def poll(self):
        while not self.actions.empty():
            self.actions.get()()

    def drop(self, event):
        app = self.app
        if app.cap:
            return "refuse_drop"
        if event.widget is app.view.tree:
            node = app.view.tree.identify_row(event.y_root - app.view.tree.winfo_rooty())
            if node:
                app.view.tree.selection_set(node)
                app.open_node(node)
        paths = [Path(p) for p in app.root.tk.splitlist(event.data)]
        materials = [p for p in paths if p.suffix.lower() in (".pdf", ".ppt", ".pptx")]
        audio = [p for p in paths if p.suffix.lower() in (".wav", ".flac", ".mp3", ".m4a")]
        if materials:
            app.import_material(paths=materials)
        if audio:
            app.commands.import_audio(not app.lecture_id, paths=audio)
        return "copy"


def stop_on_suspend(app):
    """Request normal sealing on the same Tk thread that receives WM_POWERBROADCAST."""
    common = ctypes.WinDLL("comctl32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    uint_ptr = ctypes.c_size_t
    result = ctypes.c_ssize_t
    args = (wintypes.HWND, wintypes.UINT, uint_ptr, result)
    callback_type = ctypes.WINFUNCTYPE(result, *args, uint_ptr, uint_ptr)
    common.DefSubclassProc.argtypes = args
    common.DefSubclassProc.restype = result
    common.SetWindowSubclass.argtypes = (wintypes.HWND, callback_type, uint_ptr, uint_ptr)
    common.RemoveWindowSubclass.argtypes = (wintypes.HWND, callback_type, uint_ptr)
    user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user.GetAncestor.restype = wintypes.HWND

    @callback_type
    def observe(hwnd, message, wparam, lparam, identity, _):
        if message == 0x218 and wparam == 4 and app.cap:  # PBT_APMSUSPEND
            app.stop()
        elif message == 0x82:  # WM_NCDESTROY
            common.RemoveWindowSubclass(hwnd, observe, identity)
        return common.DefSubclassProc(hwnd, message, wparam, lparam)

    app.root.update_idletasks()
    hwnd = user.GetAncestor(app.root.winfo_id(), 2)
    if not common.SetWindowSubclass(hwnd, observe, 1, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return observe


@contextmanager
def activation_event(library):
    """Second launches signal the existing window for this library, including a hidden one."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.SetEvent.argtypes = (wintypes.HANDLE,)
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    name = hashlib.sha256(os.path.normcase(str(library.resolve())).encode("utf8")).hexdigest()
    handle = kernel.CreateEventW(None, False, False, "Local\\LectureFlow-" + name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield (lambda: kernel.SetEvent(handle), lambda: kernel.WaitForSingleObject(handle, 0) == 0)
    finally:
        kernel.CloseHandle(handle)
