"""Native Windows trial desktop entry point."""

import argparse
import json
import os
import queue
import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog

from . import documents, filelock, live_store, review_marks, windows_settings
from .library import Library
from .runtime import write_json
from .windows_capture import RATE, Capture
from .windows_commands import Commands
from .windows_models import WINDOWS_CATALOG, FileSpeech, translation_server
from .windows_review import Review
from .windows_ui import FONT, WorkspaceView, clock


class App:
    def __init__(self, root, lib, args):
        self.root, self.lib, self.args = root, lib, args
        self.cap = self.overlay = self.current = None
        self.closing = False
        self.reconfigure = False
        self.discard_pending = False
        self.activation_requested = lambda: False
        self.shell = None
        self.caption = tk.StringVar(value="准备就绪")
        self.status = tk.StringVar()
        self.mark_label = tk.StringVar(value="标记当前")
        root.title("LectureFlow · Windows 试用版")
        self.pref_path = lib.root / ".windows-preferences.json"
        self.options = windows_settings.load(self.pref_path)
        self.worker = None
        self.worker_stop = lib.root / f".worker-stop-{os.getpid()}"
        self.worker_stop.unlink(missing_ok=True)
        self.destroying = False
        self.failed_course = None
        self.failed_materials = []
        self.cpu = tk.BooleanVar(value=args.cpu or self.options["cpu"])
        self.lecture_id = self.course_id = None
        self.view = WorkspaceView(self)
        self.commands = Commands(self)
        self.apply_options()
        self.reload_courses()
        self.root.after(500, self.start_worker)
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

    def settings(self):
        if not self.cap or self.cap.paused.is_set():
            windows_settings.show(self)

    def apply_options(self):
        size = self.options["font_size"]
        self.view.text.tag_configure("zh", font=(FONT, size))
        self.view.text.tag_configure("en", font=("Segoe UI", size - 1))
        flags = self.options
        mode = (
            ("录音与字幕" if flags["english"] or flags["chinese"] else "仅录音")
            if flags["audio"]
            else "仅字幕 · 不保存音频"
        )
        self.view.mode.configure(text=mode + "  ·  设置")

    def start_worker(self):
        if self.cap or self.closing or self.worker and self.worker.poll() is None:
            return
        needed = self.lib.db.execute(
            "SELECT 1 FROM tasks WHERE state IN ('queued','running')"
            " UNION ALL SELECT 1 FROM knowledge_jobs WHERE state IN ('queued','running') LIMIT 1"
        ).fetchone()
        if not needed:
            return
        cmd = [sys.executable]
        if not getattr(sys, "frozen", False):
            cmd += ["-m", "lectureflow.windows_app"]
        cmd += [
            "--stop-token",
            str(self.worker_stop),
            "--worker",
            "--library",
            str(self.lib.root),
            "--assets",
            str(self.args.assets),
        ]
        if self.cpu.get():
            cmd.append("--cpu")
        self.worker = subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW)

    def reload_courses(self, course_id=None, lecture_id=None):
        courses = self.lib.overview()["courses"]
        if not courses:
            self.lib.create_course("我的课程")
            courses = self.lib.overview()["courses"]
        tree = self.view.tree
        tree.delete(*tree.get_children())
        self.nodes = {}
        for course in courses:
            cid = course["id"]
            tree.insert("", "end", iid=cid, text=course["name"], open=True)
            self.nodes[cid] = (course, None)
            for lecture in course["lectures"]:
                key = lecture["id"]
                tree.insert(cid, "end", iid=key, text=lecture["title"])
                self.nodes[key] = (course, lecture)
        selection = lecture_id or course_id
        if selection is None:
            try:
                saved = json.loads(
                    (self.lib.root / ".windows-selection.json").read_text(encoding="utf8")
                )
                if isinstance(saved, str) and saved in self.nodes:
                    selection = saved
            except (FileNotFoundError, ValueError):
                pass
        if not selection:
            lectures = courses[0]["lectures"]
            selection = lectures[-1]["id"] if lectures else courses[0]["id"]
        tree.selection_set(selection)
        tree.see(selection)
        self.open_node(selection)

    def choose(self, _event):
        chosen = self.view.tree.selection()
        if not chosen:
            return
        current = self.lecture_id or self.course_id
        if chosen[0] != current:
            if self.cap:
                self.view.tree.selection_set(current)
            else:
                self.open_node(chosen[0])

    def open_node(self, key):
        course, lecture = self.nodes[key]
        write_json(self.lib.root / ".windows-selection.json", key)
        self.course_id = course["id"]
        self.lecture_id = lecture["id"] if lecture else None
        rows = live_store.history(self.lib, key)["captions"] if lecture else []
        name = lecture["title"] if lecture else course["name"]
        self.view.breadcrumb.configure(text=course["name"] if lecture else "课程资料库")
        self.view.title.configure(text=name)
        self.view.section.configure(text="课堂转写" if lecture else "课程记录")
        self.recent = rows[-6:]
        if self.overlay and self.overlay.winfo_exists():
            self.overlay.render(self.recent)
        self.current = None
        self.mark_label.set("标记当前")
        self.caption.set(
            "\n\n".join(r["english"] + "\n" + r["chinese"] for r in rows[-2:]) or "等待录音"
        )
        if lecture:
            self.view.render(rows, "" if rows else "本节还没有字幕。")
            duration = sum(seg["duration"] for seg in lecture["segments"])
            self.status.set(f"{len(lecture['segments'])} 段录音 · {len(rows)} 段字幕")
        else:
            lectures = course["lectures"]
            self.status.set(f"{len(lectures)} 节 Lecture · 准备录音")
            duration = 0
            self.view.course_home(lectures)
        self.view.timer.configure(text=clock(duration))
        self.view.controls(False, bool(lecture))

    def new_course(self):
        name = simpledialog.askstring("新建课程", "课程名称")
        if name:
            self.reload_courses(self.lib.create_course(name)["id"])

    def import_material(self, retry=False, paths=None):
        if retry and self.failed_course != self.course_id:
            self.status.set("当前课程没有待重试的导入")
            return
        names = paths
        if names is None:
            names = (
                self.failed_materials
                if retry
                else filedialog.askopenfilenames(filetypes=[("课件", "*.pptx *.pdf *.ppt")])
            )
        if not names:
            return
        failed = []
        self.failed_course = self.course_id
        self.failed_materials = []
        imported = duplicates = 0
        for name in names:
            try:
                from .windows_ocr import extract_pdf

                path = Path(name)
                extracted = (
                    extract_pdf(path, self.options["force_ocr"])
                    if path.suffix.lower() == ".pdf"
                    else None
                )
                result = documents.import_document(
                    self.lib, "course", self.course_id, path, extracted=extracted
                )
                if result.get("duplicate"):
                    duplicates += 1
                else:
                    imported += 1
            except Exception as error:
                self.failed_materials.append(name)
                failed.append(f"{Path(name).name} · {error}")
        self.status.set(f"已导入 {imported} 份 · 重复 {duplicates} 份 · 失败 {len(failed)} 份")
        self.start_worker()
        if failed:
            messagebox.showwarning("以下课件导入失败", "\n".join(failed), parent=self.root)

    def start(self, append=False, previous=None):
        if self.cap:
            return
        options = self.options
        name = options["device"]
        device = None
        if name and not self.args.replay:
            available = windows_settings.inputs()
            if name not in available:
                raise ValueError("所选麦克风不可用，请在采集设置中重新选择")
            device = available[name]
        if append and not self.lecture_id:
            raise ValueError("请选择要继续录制的 Lecture")
        if not append:
            course = self.nodes[self.course_id][0]
            title = f"Lecture {len(course['lectures']) + 1:02d}"
            lec = self.lib.create_lecture(self.course_id, title)
            self.reload_courses(self.course_id, lec["id"])
        session = (
            previous.session
            if previous
            else live_store.create(self.lib, self.lecture_id, language=options["language"])["id"]
        )
        self.cap = Capture(
            self.lib,
            self.lecture_id,
            session,
            self.args.assets,
            self.args.replay,
            self.cpu.get(),
            previous=previous,
            audio=options["audio"],
            english=options["english"],
            chinese=options["chinese"],
            asr=options["asr"],
            device=device,
            language=None if options["language"] == "auto" else "English",
        )
        self.current = self.previous = None
        self.show_previous = False
        self.active_options = options.copy()
        if not previous:
            self.marked = set()
        self.first_en = self.first_zh = None
        self.samples = []
        self.display_times = {} if self.args.metrics else None
        self.mark_label.set("标记当前")
        self.caption.set(
            "正在加载本地模型" if options["english"] or options["chinese"] else "仅录音"
        )
        if options["floating"] and (options["english"] or options["chinese"]):
            self.float_window()
        if not previous:
            self.view.render(hint=self.caption.get())
        self.recent = []
        self.last_meter_frame = -1
        self.view.section.configure(text="实时转写")
        self.apply_options()
        self.view.controls(True, True)

    def pause(self):
        if self.cap and not self.cap.finished.is_set():
            if self.cap.paused.is_set():
                keys = ("audio", "english", "chinese", "language", "asr", "cpu", "device")
                if any(self.active_options[k] != self.options[k] for k in keys):
                    self.reconfigure = True
                    self.cap.stop.set()
                    return
                self.cap.paused.clear()
                self.view.pause.configure(text="暂停")
            else:
                self.cap.paused.set()
                self.view.pause.configure(text="继续")

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
        from .windows_overlay import Overlay

        self.overlay = Overlay(self)
        self.view.overlay_mark = self.overlay.mark
        self.overlay.render(self.recent)
        if not self.cap or not self.cap.frames or self.cap.finished.is_set():
            self.view.overlay_mark.state(["disabled"])

    def row_menu(self, event):
        tags = self.view.text.tag_names(f"@{event.x},{event.y}")
        row = next((self.view.rows.get(tag[4:]) for tag in tags if tag.startswith("row_")), None)
        if not row:
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(
            label="复制本段",
            command=lambda: self.commands.clipboard(
                (row["english"] + "\n" + row["chinese"]).strip()
            ),
        )

        def bookmark():
            review_marks.create(
                self.lib,
                self.lecture_id,
                row["session_id"],
                (row["start"] + row["end"]) / 2,
                caption_id=row["id"],
            )
            if self.cap and row["session_id"] == self.cap.session:
                self.marked.add(row["id"])
                if self.current and self.current["id"] == row["id"]:
                    self.mark_label.set("已标记")

        menu.add_command(label="标记此段", command=bookmark)
        menu.add_command(label="复习书签", command=self.review)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def review(self):
        if self.lecture_id:
            return Review(self.root, self.lib, self.lecture_id)

    def audio_folder(self):
        if self.lecture_id:
            os.startfile(self.lib.lecture_path(self.lecture_id) / "Audio")

    def cancel_segment(self):
        if not self.cap or not self.cap.audio or self.cap.stop.is_set():
            return
        if not messagebox.askyesno("取消当前录音段", "本段原音将移入废纸篓，已生成字幕会保留。"):
            return
        self.discard_pending = True
        self.reconfigure = self.cap.english or self.cap.chinese
        self.cap.stop.set()

    def stop(self):
        self.reconfigure = False
        if self.cap:
            self.cap.stop.set()

    def close(self):
        self.closing = True
        if self.worker and self.worker.poll() is None:
            self.worker_stop.touch()
        if self.cap:
            self.stop()
        elif self.worker and self.worker.poll() is None:
            self.status.set("正在保存后台任务检查点…")
        else:
            self.worker_stop.unlink(missing_ok=True)
            self.destroying = True
            self.root.destroy()

    def poll(self):
        try:
            if self.shell:
                if self.activation_requested():
                    self.shell.show()
                self.shell.poll()
            if self.cap:
                self.refresh()
            elif self.worker and self.worker.poll() is not None:
                code = self.worker.returncode
                self.worker = None
                if self.closing:
                    self.close()
                    return
                if code:
                    self.status.set(f"后台任务进程退出 ({code})，请查看后台队列")
                else:
                    self.root.after(1500, self.start_worker)
        finally:
            if not self.destroying and self.root.winfo_exists():
                self.root.after(100, self.poll)

    def display_caption(self, event):
        """Keep a late first translation readable without moving the bookmark target."""
        if self.current is None or event["start"] > self.current["start"]:
            self.previous, self.current = self.current, event
            self.show_previous = False
        elif event["id"] == self.current["id"]:
            self.current = event
        elif self.previous and event["id"] == self.previous["id"]:
            if not self.previous["chinese"] and event["chinese"]:
                self.show_previous = True
            self.previous = event
            if not self.show_previous:
                return
        else:
            return

        text = self.current["english"] + "\n\n" + self.current["chinese"]
        if self.show_previous:
            text = (
                "当前句\n"
                + text.rstrip()
                + "\n\n上一句\n"
                + self.previous["english"]
                + "\n"
                + self.previous["chinese"]
            )
        self.caption.set(text)
        self.mark_label.set("已标记" if self.current["id"] in self.marked else "标记当前")
        elapsed = time.monotonic() - self.cap.started
        if self.display_times is not None:
            timing = self.display_times.setdefault(event["id"], {"start": event["start"]})
            for field in ("english", "chinese"):
                if event[field]:
                    timing.setdefault(field, elapsed)
        if self.first_en is None and event["english"]:
            self.first_en = elapsed
        if self.first_zh is None and event["chinese"]:
            self.first_zh = elapsed

    def refresh(self):
        cap = self.cap
        while True:
            try:
                event, at = cap.events.get_nowait()
            except queue.Empty:
                break
            live_store.save(
                self.lib, cap.session, event, english=cap.english, translate=cap.chinese
            )
            self.view.update_row(dict(event, session_id=cap.session))
            matching = next((i for i, r in enumerate(self.recent) if r["id"] == event["id"]), None)
            if matching is not None:
                self.recent[matching] = event
            elif not self.recent or event["start"] >= self.recent[-1]["start"]:
                self.recent = [*self.recent[-5:], event]
            self.display_caption(event)
            if self.overlay and self.overlay.winfo_exists():
                self.overlay.render(self.recent)
        lag = (cap.frames - cap.consumed) / RATE if cap.english or cap.chinese else 0.0
        elapsed = time.monotonic() - cap.started
        if not self.samples or elapsed - self.samples[-1][0] >= 10:
            self.samples.append((round(elapsed, 2), round(lag, 2)))
        state = (
            "正在保存" if cap.finished.is_set() else ("暂停中" if cap.paused.is_set() else "录音中")
        )
        self.view.timer.configure(text=clock((cap.prior_frames + cap.frames) / RATE))
        writable = not cap.stop.is_set() and not cap.finished.is_set()
        self.view.controls(True, True, writable and cap.frames > 0)
        if cap.paused.is_set() and writable:
            self.view.mode.state(["!disabled"])
        self.view.stop.state(["!disabled" if writable else "disabled"])
        if self.last_meter_frame != cap.frames:
            self.view.level(cap.level)
            self.last_meter_frame = cap.frames
        if not cap.audio:
            state += " · 不保存音频"
            if cap.buffer.dropped:
                state += f" · 字幕超载 {cap.buffer.dropped} 包"
        self.status.set(f"{state} · 待识别 {lag:.1f} 秒  " + " ".join(cap.errors))
        if not cap.writer.is_alive() and not cap.reader.is_alive() and cap.events.empty():
            self.cap = None
            discarded = self.discard_pending
            self.discard_pending = False
            if discarded and not cap.frames:
                cap.lock.close()
            if discarded and cap.frames:
                from .recordings import discard

                cap.lock.close()
                discard(self.lib, self.lecture_id, cap.directory, confirmed=True)
            if self.reconfigure and not self.closing:
                self.reconfigure = False
                preserve = cap.audio and self.options["audio"] and not discarded
                if preserve:
                    cap.lock.close()
                elif not discarded:
                    cap.register(self.lib, self.lecture_id)
                try:
                    if discarded:
                        self.options = dict(self.options, audio=False)
                    self.start(append=True, previous=cap)
                    self.view.pause.configure(text="暂停")
                except Exception:
                    if preserve:
                        cap.register(self.lib, self.lecture_id)
                    self.reload_courses(self.course_id, self.lecture_id)
                    raise
                return
            try:
                registered = None if discarded else cap.register(self.lib, self.lecture_id)
                self.reload_courses(self.course_id, self.lecture_id)
                saved = f"已保存 {cap.frames / RATE:.1f} 秒" if registered else "没有录到音频"
                if discarded:
                    saved = "录音已移入废纸篓"
                if not cap.audio:
                    saved = "已结束仅字幕采集"
                self.status.set(f"{saved} · 输入溢出 {cap.overflows} 次  " + " ".join(cap.errors))
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
                            display_times=self.display_times,
                            overflows=cap.overflows,
                            errors=cap.errors,
                            progress=getattr(cap, "progress", {}),
                            session=cap.session,
                        ),
                    )
            except Exception as error:
                self.status.set(f"登记失败，原音保留在录音文件夹：{error}")
            finally:
                self.view.controls(False, True)
                self.view.pause.configure(text="暂停")
                self.view.samples = [0.0] * 15
                self.view.level(0.0)
                if self.closing:
                    self.root.after_idle(self.close)
                else:
                    self.start_worker()


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--library", type=Path, default=Path(os.environ["LOCALAPPDATA"]) / "LectureFlow" / "Library"
    )
    p.add_argument("--assets", type=Path, default=Path(sys.executable).resolve().parent)
    p.add_argument("--replay", type=Path)
    p.add_argument("--metrics", type=Path)
    p.add_argument("--cpu", action="store_true")
    p.add_argument("--worker", action="store_true")
    p.add_argument("--stop-token", type=Path)
    args = p.parse_args()
    if args.worker:
        from .worker import run_worker

        run_worker(
            args.library,
            args.assets / "models",
            exit_requested=args.stop_token.exists if args.stop_token else lambda: False,
            catalog=WINDOWS_CATALOG,
            load_asr=lambda alias: FileSpeech(args.assets / "models", alias),
            load_mt=lambda: translation_server(
                args.assets / "llama", args.assets / "models", args.cpu
            ),
        )
        return
    args.library.mkdir(parents=True, exist_ok=True)
    from tkinterdnd2 import TkinterDnD

    from .windows_shell import Shell, activation_event

    with (
        activation_event(args.library) as (activate, requested),
        (args.library / ".desktop.lock").open("a+") as lock,
    ):
        try:
            filelock.flock(lock, filelock.LOCK_EX | filelock.LOCK_NB)
        except BlockingIOError:
            activate()
            return
        root = TkinterDnD.Tk()
        with Library(args.library) as lib:
            app = App(root, lib, args)
            app.activation_requested = requested
            app.shell = Shell(app)
            try:
                root.mainloop()
            finally:
                app.shell.icon.stop()


if __name__ == "__main__":
    main()
