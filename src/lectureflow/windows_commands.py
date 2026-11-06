"""Windows workspace commands backed by the same library operations as the Swift client."""

import os
import queue
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import documents, exporting, filelock, files, knowledge, live_store, purging, recordings
from .inference_policy import matching_glossary
from .terminology import snapshot_glossary
from .windows_chrome import Button, scroll
from .windows_models import WINDOWS_CATALOG, translation_server
from .windows_ui import CANVAS, clock, label


class Table(tk.Toplevel):
    def __init__(self, parent, title, columns, rows):
        super().__init__(parent, bg=CANVAS, padx=16, pady=14)
        self.title(title)
        self.geometry("830x460")
        self.transient(parent)
        self.old_grab = self.grab_current()
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.close)
        label(self, title, 16, bold=True).pack(anchor="w", pady=(0, 10))
        self.query = tk.StringVar()
        ttk.Entry(self, textvariable=self.query).pack(fill="x", pady=(0, 6))
        self.query.trace_add("write", lambda *_: self.refresh())
        self.fetch = rows
        area = ttk.Frame(self)
        area.pack(fill="both", expand=True)
        self.list = ttk.Treeview(area, columns=columns, show="headings", selectmode="browse")
        for i, col in enumerate(columns):
            self.list.heading(col, text=col)
            self.list.column(col, width=260 if i == 0 else 115, minwidth=48, anchor="w")
        self.list.pack(fill="both", expand=True)
        scroll(area, self.list)
        self.buttons = tk.Frame(self, bg=CANVAS)
        self.buttons.pack(fill="x", pady=(10, 0))
        self.refresh()

    def refresh(self):
        old = self.list.selection()
        self.list.delete(*self.list.get_children())
        for key, values in self.fetch():
            if self.query.get().casefold() in " ".join(map(str, values)).casefold():
                self.list.insert("", "end", iid=key, values=values)
        kids = self.list.get_children()
        if kids:
            self.list.selection_set(old[0] if old and old[0] in kids else kids[0])

    def action(self, title, fn, *, selected=True):
        def invoke():
            keys = self.list.selection()
            if selected and not keys:
                return
            if selected:
                fn(keys[0])
            else:
                fn()
            if self.winfo_exists():
                self.refresh()

        Button(self.buttons, text=title, command=invoke).pack(side="left", padx=(0, 4))

    def close(self):
        self.destroy()
        if self.old_grab and self.old_grab.winfo_exists():
            self.old_grab.grab_set()


class Commands:
    def __init__(self, app):
        self.app, self.lib = app, app.lib
        self.bar = tk.Menu(app.root)
        app.root.configure(menu=self.bar)
        self.menu(
            "课程",
            [
                ("新建课程", app.new_course),
                ("新建空 Lecture", self.new_lecture),
                ("导入录音到新 Lecture…", lambda: self.import_audio(True)),
                ("重命名所选项目…", self.rename),
                ("移动 Lecture…", self.move_lecture),
                ("排列课程…", self.order_courses),
                ("移入废纸篓…", self.archive),
                ("废纸篓…", self.trash),
            ],
        )
        self.menu(
            "录音",
            [
                ("在本节继续录制", lambda: app.start(append=True)),
                ("导入录音到本节…", self.import_audio),
                ("管理录音段…", self.segments),
                ("恢复未登记录音", self.recover),
                ("重新连接字幕", self.reconnect),
                ("取消当前录音段…", app.cancel_segment),
                ("采集设置…", app.settings),
            ],
        )
        self.menu(
            "课程资料",
            [
                ("导入 PDF / PPT…", app.import_material),
                ("重试导入失败项", lambda: app.import_material(retry=True)),
                ("资料文件…", self.materials),
                ("辅助术语…", self.terms),
            ],
        )
        self.menu(
            "转写",
            [
                ("生成整节课后转写", self.enqueue),
                ("课后版本与编辑…", self.versions),
                ("复制实时字幕", self.copy_live),
                ("复制英文", lambda: self.copy_live("english")),
                ("复制中文", lambda: self.copy_live("chinese")),
                ("搜索字幕…", self.find_caption),
                ("导出实时 MD / JSON", self.export_live),
                ("后台队列…", self.queue),
            ],
        )
        self.menu(
            "显示",
            [
                ("展开 / 收起课程栏", app.view.toggle_sidebar),
                ("仅显示悬浮字幕", self.only_overlay),
                ("打开资料库文件夹", lambda: os.startfile(self.lib.root)),
                ("复制诊断信息", self.diagnostics),
                ("悬浮字幕", app.float_window),
                ("采集设置…", app.settings),
            ],
        )

    def reconnect(self):
        cap = self.app.cap
        if not cap or cap.reader.is_alive() or cap.stop.is_set() or not cap.audio:
            return
        if cap.english or cap.chinese:
            cap.errors[:] = [e for e in cap.errors if not e.startswith("字幕：")]
            cap.reader = threading.Thread(target=cap.infer)
            cap.reader.start()

    def only_overlay(self):
        self.app.float_window()
        self.app.root.withdraw()

    def diagnostics(self):
        import json

        from .runtime import environment

        self.clipboard(
            json.dumps(
                dict(
                    runtime=environment(),
                    asr=self.app.options["asr"],
                    translation_cpu=self.app.cpu.get(),
                ),
                ensure_ascii=False,
                indent=2,
            )
        )

    def menu(self, title, items):
        menu = tk.Menu(self.bar, tearoff=False)
        self.bar.add_cascade(label=title, menu=menu)
        for text, fn in items:
            menu.add_command(label=text, command=lambda fn=fn: self.invoke(fn))

    def invoke(self, fn):
        if (
            self.app.cap
            and not (fn == self.app.settings and self.app.cap.paused.is_set())
            and fn
            not in (
                self.queue,
                self.app.float_window,
                self.app.view.toggle_sidebar,
                self.only_overlay,
                self.app.cancel_segment,
                self.reconnect,
                self.find_caption,
            )
        ):
            messagebox.showinfo("LectureFlow", "请先结束采集，再执行此操作。", parent=self.app.root)
            return
        fn()

    def lecture(self):
        key = self.app.lecture_id
        if not key:
            raise ValueError("请先从课程栏选择一节 Lecture")
        return key

    def refresh(self):
        self.app.reload_courses(self.app.course_id, self.app.lecture_id)

    def new_lecture(self):
        course = self.app.nodes[self.app.course_id][0]
        name = simpledialog.askstring(
            "新建 Lecture",
            "名称",
            initialvalue=f"Lecture {len(course['lectures']) + 1:02}",
            parent=self.app.root,
        )
        if name:
            row = self.lib.create_lecture(course["id"], name)
            self.app.reload_courses(course["id"], row["id"])

    def import_audio(self, new=False, paths=None):
        key = None if new else self.lecture()
        paths = paths or filedialog.askopenfilenames(
            parent=self.app.root, filetypes=[("录音", "*.wav *.mp3 *.m4a *.flac")]
        )
        if not paths:
            return
        if new:
            key = self.lib.create_lecture(self.app.course_id, Path(paths[0]).stem)["id"]
        self.lib.import_audio(key, [Path(p) for p in paths])
        self.app.reload_courses(self.app.course_id, key)

    def rename(self):
        lecture = self.app.lecture_id
        key = lecture or self.app.course_id
        kind = "lecture" if lecture else "course"
        name = simpledialog.askstring("重命名", "新名称", initialvalue=self.app.view.title["text"])
        if name:
            files.rename(self.lib, kind, key, name)
            self.refresh()

    def destination(self, title, options, fn):
        rows = dict(options)
        dialog = Table(self.app.root, title, ["目标"], lambda: [(k, (v,)) for k, v in rows.items()])

        def apply(key):
            fn(key)
            dialog.close()
            self.refresh()

        dialog.action("选择", apply)

    def move_lecture(self):
        key = self.lecture()
        self.destination(
            "移到课程",
            [
                (k, c["name"])
                for k, (c, lecture) in self.app.nodes.items()
                if not lecture and k != self.app.course_id
            ],
            lambda target: files.move_lecture(self.lib, key, target),
        )

    def order_courses(self):
        def rows():
            return [(c["id"], (c["name"],)) for c in self.lib.overview()["courses"]]

        d = Table(self.app.root, "课程顺序", ["课程"], rows)

        def move(key, step):
            ids = [k for k, _ in rows()]
            i = ids.index(key)
            j = i + step
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                files.order_courses(self.lib, ids)
                self.refresh()

        d.action("上移", lambda k: move(k, -1))
        d.action("下移", lambda k: move(k, 1))

    def archive(self):
        key = self.app.lecture_id or self.app.course_id
        if messagebox.askyesno("移入废纸篓", f"移入废纸篓？\n{self.app.view.title['text']}"):
            files.archive(
                self.lib, "lecture" if self.app.lecture_id else "course", key, confirmed=True
            )
            self.app.reload_courses()

    def trash(self):
        def items():
            return files.trash(self.lib)

        d = Table(
            self.app.root,
            "废纸篓",
            ["名称", "类型"],
            lambda: [(x["id"], (x["name"], x["kind"])) for x in items()],
        )

        def restore(key):
            row = next(x for x in items() if x["id"] == key)
            files.restore(self.lib, row["kind"], key)
            self.refresh()

        def empty():
            plan = purging.preview(self.lib)
            if messagebox.askyesno(
                "永久清空", f"永久删除废纸篓中的 {len(plan['items'])} 项？", parent=d
            ):
                purging.empty(self.lib, plan["digest"], confirmed=True)

        d.action("恢复", restore)
        d.action("清空…", empty, selected=False)

    def recover(self):
        result = recordings.recover_all(self.lib, self.lecture())
        self.refresh()
        messagebox.showinfo(
            "录音恢复",
            f"已登记 {len(result['recordings'])} 段\n"
            + "\n".join(e["message"] for e in result["errors"]),
        )

    def segments(self):
        key = self.lecture()

        def rows():
            return [
                (s["id"], (s["original_name"], clock(s["duration"])))
                for s in self.lib.segments(key)
            ]

        d = Table(self.app.root, "录音段", ["录音", "时长"], rows)

        def reorder(k, step):
            ids = [sid for sid, _ in rows()]
            i = ids.index(k)
            j = i + step
            if 0 <= j < len(ids):
                ids[i], ids[j] = ids[j], ids[i]
                self.lib.reorder(key, ids)

        def archive(k):
            if messagebox.askyesno("录音", "将选中的录音移入废纸篓？", parent=d):
                files.archive(self.lib, "segment", k, confirmed=True)
                self.refresh()

        def transfer(k, copy):
            targets = [
                (i, c["name"] + " / " + lecture["title"])
                for i, (c, lecture) in self.app.nodes.items()
                if lecture and i != key
            ]
            self.destination(
                "选择目标 Lecture", targets, lambda t: files.transfer(self.lib, k, t, copy=copy)
            )

        d.action("播放", lambda k: self.play_segment(k, d))
        d.action("上移", lambda k: reorder(k, -1))
        d.action("下移", lambda k: reorder(k, 1))
        d.action("导出 WAV", self.export_segment)
        d.action("单段转写", lambda k: self.enqueue([k]))
        d.action("移动…", lambda k: transfer(k, False))
        d.action("复制…", lambda k: transfer(k, True))
        d.action("移入废纸篓", archive)

    def play_segment(self, key, parent):
        from .windows_player import Player

        row = self.lib.row("segments", key)
        Player(parent, self.lib.segment_path(key), row["original_name"], row["duration"])

    def export_segment(self, key):
        row = self.lib.row("segments", key)
        dest = filedialog.asksaveasfilename(
            defaultextension=".wav", filetypes=[("PCM WAV", "*.wav")]
        )
        if not dest:
            return
        path = Path(dest).resolve()
        if path.is_relative_to(self.lib.root):
            raise ValueError("请选择资料库外的导出位置")
        with tempfile.TemporaryDirectory(dir=path.parent) as tmp:
            output = Path(tmp) / "export.wav"
            recordings.extract_source(self.lib.segment_path(key), output, 0, row["duration"])
            output.replace(path)

    def materials(self):
        course = self.app.course_id

        def docs():
            return documents.listing(self.lib, "course", course)["documents"]

        d = Table(
            self.app.root,
            "课程资料",
            ["文件", "页数"],
            lambda: [(x["id"], (x["original_name"], x["page_count"])) for x in docs()],
        )

        def remove(key):
            if messagebox.askyesno("移除资料", "移除所选课件的受管副本？", parent=d):
                documents.remove(self.lib, key, confirmed=True)

        d.action("打开", lambda k: os.startfile(next(x["path"] for x in docs() if x["id"] == k)))
        d.action("导入…", self.app.import_material, selected=False)
        d.action("移除…", remove)

    def terms(self):
        course = self.app.course_id

        def terms():
            return knowledge.terms(self.lib, "course", course)

        def rows():
            return [
                (
                    x["id"],
                    (
                        x["english"],
                        x["chinese"],
                        x["origin"],
                        "停用" if x["excluded"] else ("确认" if x["confirmed"] else "建议"),
                    ),
                )
                for x in terms()
            ]

        d = Table(self.app.root, "辅助术语", ["拼写", "译名", "来源", "状态"], rows)

        d.list.configure(selectmode="extended")

        def edit(key=None):
            row = next((x for x in terms() if x["id"] == key), {})
            en = simpledialog.askstring(
                "辅助术语", "英文拼写", initialvalue=row.get("english", ""), parent=d
            )
            if not en:
                return
            zh = simpledialog.askstring(
                "辅助术语", "中文译名（可空）", initialvalue=row.get("chinese", ""), parent=d
            )
            if zh is not None:
                documents.save_term(self.lib, "course", course, en, zh, True, key)

        def change(k, action):
            knowledge.batch(
                self.lib,
                dict(
                    kind="course",
                    owner_id=course,
                    term_ids=list(d.list.selection()) or [k],
                    operation=action,
                ),
            )
            if action == "translate":
                self.app.start_worker()

        d.action("添加", edit, selected=False)
        d.action("编辑", edit)
        for title, action in [
            ("停用", "exclude"),
            ("启用", "include"),
            ("确认", "confirm"),
            ("建议译名", "translate"),
        ]:
            d.action(title, lambda k, op=action: change(k, op))

        def fill_missing():
            candidates = [x for x in terms() if not x["chinese"] and not x["excluded"]]
            knowledge.enqueue(self.lib, "course", course, candidates)
            self.app.start_worker()

        d.action("补全缺失译名", fill_missing, selected=False)

    def enqueue(self, segments=None):
        key = self.lecture()
        self.lib.enqueue(
            key,
            asr=self.app.options["asr"],
            translate=self.app.options["chinese"],
            source_language=self.app.options["language"],
            segment_ids=segments,
            model_catalog=WINDOWS_CATALOG,
        )
        self.app.start_worker()
        self.app.status.set("课后转写已加入后台队列")

    def queue(self):
        def rows():
            tasks = [
                (
                    x["id"],
                    (
                        "转写 · " + x["title"],
                        x["state"],
                        f"{x['next_unit']}/{x['total_units']}",
                        x["error"] or "",
                    ),
                )
                for x in self.lib.db.execute(
                    "SELECT t.*, l.title FROM tasks t "
                    "JOIN versions v ON v.id=t.version_id JOIN lectures l ON l.id=v.lecture_id "
                    "ORDER BY t.updated_at DESC"
                )
            ]
            return tasks + [
                (
                    x["id"],
                    (
                        "辅助译名",
                        x["state"],
                        f"{x['next_unit']}/{x['total_units']}",
                        x["error"] or "",
                    ),
                )
                for x in knowledge.jobs(self.lib)
            ]

        d = Table(self.app.root, "后台队列", ["任务", "状态", "进度", "详情"], rows)

        def control(k, action):
            if self.lib.db.execute("SELECT 1 FROM tasks WHERE id=?", (k,)).fetchone():
                self.lib.control(k, action)
            else:
                knowledge.control(self.lib, k, action)
            if action in ("resume", "retry"):
                self.app.start_worker()

        for title, op in [
            ("暂停", "pause"),
            ("继续", "resume"),
            ("取消", "cancel"),
            ("重试", "retry"),
        ]:
            d.action(title, lambda k, op=op: control(k, op))

        def pause_all():
            for table in ("tasks", "knowledge_jobs"):
                keys = [
                    r[0]
                    for r in self.lib.db.execute(
                        f"SELECT id FROM {table} WHERE state IN ('queued','running')"
                    )
                ]
                for k in keys:
                    try:
                        control(k, "pause")
                    except ValueError:
                        pass  # The worker may already have completed the item.

        d.action("全部暂停", pause_all, selected=False)
        d.action("刷新", lambda: None, selected=False)

        def poll():
            if not d.query.get():
                d.refresh()
            d.after(2000, poll)

        d.after(2000, poll)

    def versions(self):
        key = self.lecture()

        def rows():
            return [
                (x["id"], (x["created_at"][:19], x["state"], x["next_unit"]))
                for x in self.lib.db.execute(
                    "SELECT v.id,v.created_at,t.state,t.next_unit FROM versions v "
                    "JOIN tasks t ON t.version_id=v.id "
                    "WHERE v.lecture_id=? ORDER BY v.created_at DESC",
                    (key,),
                )
            ]

        d = Table(self.app.root, "课后转写版本", ["创建时间（UTC）", "状态", "已完成区间"], rows)
        d.action("查看与编辑", lambda k: self.units(k, d))
        d.action(
            "导出四种格式",
            lambda k: os.startfile(exporting.export_version(self.lib, k)["directory"]),
        )
        d.action("生成新版本", self.enqueue, selected=False)

    def units(self, key, parent):
        def units():
            return self.lib.version_content(key)["units"]

        d = Table(
            parent,
            "课后转写区间",
            ["时间", "英文", "中文"],
            lambda: [
                (str(x["sequence"]), (clock(x["logical_start"]), x["english"], x["chinese"]))
                for x in units()
            ],
        )

        def edit(i):
            row = next(x for x in units() if x["sequence"] == int(i))
            en = simpledialog.askstring("编辑区间", "英文", initialvalue=row["english"], parent=d)
            if en is None:
                return
            zh = simpledialog.askstring("编辑区间", "中文", initialvalue=row["chinese"], parent=d)
            if zh is not None:
                self.lib.edit_unit(key, int(i), english=en, chinese=zh)

        def retranslate(index):
            content = self.lib.version_content(key)
            row = next(x for x in content["units"] if x["sequence"] == int(index))
            target = row["english"]
            if not target.strip():
                raise ValueError("本区间没有待翻译英文")
            glossary = matching_glossary(snapshot_glossary(content["config"]), target)
            replies = queue.SimpleQueue()
            cpu = self.app.cpu.get()
            assets = self.app.args.assets

            def generate():
                try:
                    with (self.lib.root / ".model.lock").open("a+") as lease:
                        filelock.flock(lease, filelock.LOCK_EX | filelock.LOCK_NB)
                        with translation_server(assets / "llama", assets / "models", cpu) as mt:
                            result = mt.translate(target, glossary)
                            if result.get("truncated"):
                                raise ValueError("译文超长，未覆盖此前结果")
                            replies.put(result["text"])
                except Exception as exc:
                    replies.put(exc)

            def finish():
                if replies.empty():
                    d.after(200, finish)
                    return
                result = replies.get()
                if isinstance(result, Exception):
                    raise ValueError(f"重译未完成：{result}")
                current = next(x for x in units() if x["sequence"] == int(index))
                if current["english"] != target or current["chinese"] != row["chinese"]:
                    raise ValueError("区间已被编辑，已保留新的编辑")
                self.lib.edit_unit(key, int(index), chinese=result)
                d.refresh()

            threading.Thread(target=generate, daemon=True).start()
            d.after(200, finish)

        d.action("编辑", edit)
        d.action("重译中文", retranslate)
        d.action("恢复原文", lambda i: self.lib.edit_unit(key, int(i), restore=True))
        d.action(
            "复制", lambda i: self.clipboard("\n".join(str(x) for x in d.list.item(i, "values")))
        )

    def clipboard(self, text):
        self.app.root.clipboard_clear()
        self.app.root.clipboard_append(text)

    def find_caption(self):
        text = self.app.view.text
        needle = simpledialog.askstring("搜索字幕", "英文或中文", parent=self.app.root)
        if not needle:
            return
        pos = text.search(needle, "1.0", stopindex="end", nocase=True)
        if pos:
            text.tag_remove("sel", "1.0", "end")
            text.tag_add("sel", pos, f"{pos}+{len(needle)}c")
            text.see(pos)
            text.focus_set()
        else:
            self.app.status.set("没有匹配字幕")

    def copy_live(self, field=None):
        rows = live_store.history(self.lib, self.lecture())["captions"]
        self.clipboard(
            "\n\n".join(
                r[field] if field else (r["english"] + "\n" + r["chinese"]).strip() for r in rows
            )
        )

    def export_live(self):
        os.startfile(live_store.export(self.lib, self.lecture())["directory"])
