"""Windows MVP: isolated library, local offline transcription and versioned exports."""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import locking
from .library import Library
from .runtime import configure


def transcribe(root, lecture_id, asr_path, translation_path, *, backend="whisper",
               language="English"):
    from .windows_models import MarianTranslator, QwenCPUASR, WhisperASR
    from .worker import process_task

    if backend not in {'whisper', 'qwen'}:
        raise ValueError('不支持的识别后端')
    if language not in {'English', 'auto'} or (backend == 'whisper' and language != 'English'):
        raise ValueError('Whisper 英语模型仅支持固定英语；自动语言仅用于 Qwen 实验')
    with (root / '.worker.lock').open('a+') as handle:
        locking.flock(handle, locking.LOCK_EX | locking.LOCK_NB)
        # Load before enqueueing; missing weights must not leave a misleading task.
        asr = QwenCPUASR(asr_path, language) if backend == "qwen" else WhisperASR(asr_path)
        translator = MarianTranslator(translation_path)
        with Library(root, worker_lock_owned=True) as lib:
            task = lib.enqueue(lecture_id)
            version = lib.row('versions', task['version_id'])
            config = json.loads(version['config'])
            config['backend'] = 'windows-cpu-experimental'
            config['asr_backend'] = backend
            config['source_language'] = language
            config['inference_device'] = 'cpu'
            config['model_memory'] = 'system_ram'
            config['asr_dtype'] = 'float32' if backend == 'qwen' else 'int8'
            config['models'] = {'asr': str(asr_path), 'translation': str(translation_path)}
            # No courseware enhancement is claimed for this experimental backend.
            config['hint_terms'] = []
            config['documents'] = []
            lib.db.execute('UPDATE versions SET config=? WHERE id=?',
                           (json.dumps(config), version['id']))
            lib.db.execute("UPDATE tasks SET state='running' WHERE id=?", (task['id'],))
            try:
                process_task(lib, lib.row('tasks', task['id']), asr, translator)
            except Exception as error:
                lib.finish(task['id'], error=str(error))
                raise
            return lib.version_content(task['version_id'])


class App:
    def __init__(self, root, library_root):
        self.root, self.library_root = root, library_root
        self.events = queue.Queue()
        self.busy = False
        self.recorder = None
        self.selected = None
        root.title('LectureFlow · Windows 实验版')
        root.geometry('980x680')
        ttk.Label(root, text='Windows MVP · 本地音频 → 语音识别 → 中文翻译',
                  font=('', 16)).pack(pady=12)
        ttk.Label(root, text='独立实验资料库 · 课后转写预览版').pack()
        options = ttk.Frame(root)
        options.pack(pady=4)
        self.asr_backend = tk.StringVar(value='whisper')
        self.asr_language = tk.StringVar(value='English')
        ttk.Label(options, text='识别后端（CPU / 系统 RAM，无 GPU 加速）：').pack(side='left')
        ttk.Combobox(options, textvariable=self.asr_backend, state='readonly',
                     values=['whisper', 'qwen'], width=10).pack(side='left')
        ttk.Combobox(options, textvariable=self.asr_language, state='readonly',
                     values=['English', 'auto'], width=10).pack(side='left')
        bar = ttk.Frame(root)
        bar.pack(fill='x', padx=12, pady=10)
        for label, action in [('新建课程', self.course), ('新建 Lecture', self.lecture),
                              ('导入音频', self.import_audio), ('开始录音', self.record),
                              ('结束录音', self.stop_recording), ('课后双语转写', self.run),
                              ('查看最近结果', self.show_latest), ('打开导出目录', self.exports)]:
            ttk.Button(bar, text=label, command=lambda a=action: self.safe(a)).pack(side='left')
        self.tree = ttk.Treeview(root, show='tree', height=8)
        self.tree.pack(fill='x', padx=12)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.output = tk.Text(root, wrap='word', font=('', 12))
        self.output.pack(fill='both', expand=True, padx=12, pady=10)
        self.status = tk.StringVar(value='先创建课程和 Lecture，再导入录音。')
        ttk.Label(root, textvariable=self.status).pack(fill='x', padx=12, pady=8)
        self.refresh()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(150, self.poll)

    def safe(self, action):
        if self.recorder is not None and action != self.stop_recording:
            messagebox.showinfo('录音中', '请先结束当前录音。')
            return
        if self.busy:
            messagebox.showinfo('处理中', '请等待当前任务保存完成。')
            return
        try:
            action()
        except Exception as error:
            messagebox.showerror('操作失败', str(error))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.selected = None
        with Library(self.library_root) as lib:
            for course in lib.overview()['courses']:
                self.tree.insert('', 'end', iid=course['id'], text=course['name'], open=True)
                for lecture in course['lectures']:
                    self.tree.insert(course['id'], 'end', iid=lecture['id'],
                                     text=f"{lecture['title']} · {len(lecture['segments'])} 段录音")

    def select(self, _event):
        selection = self.tree.selection()
        self.selected = selection[0] if selection else None

    def require_lecture(self):
        if not self.selected or not self.tree.parent(self.selected):
            raise ValueError('请先选择一个 Lecture')
        return self.selected

    def course(self):
        name = simpledialog.askstring('新建课程', '课程名称：')
        if name:
            with Library(self.library_root) as lib:
                lib.create_course(name)
            self.refresh()

    def lecture(self):
        if not self.selected:
            raise ValueError('请先选择课程')
        course = self.tree.parent(self.selected) or self.selected
        name = simpledialog.askstring('新建 Lecture', 'Lecture 名称：')
        if name:
            with Library(self.library_root) as lib:
                lib.create_lecture(course, name)
            self.refresh()

    def import_audio(self):
        lecture = self.require_lecture()
        paths = filedialog.askopenfilenames(title='导入录音（保留源文件）')
        if paths:
            with Library(self.library_root) as lib:
                lib.import_audio(lecture, [Path(p) for p in paths])
            self.refresh()
            self.status.set('已保存受管副本；请重新选择 Lecture 开始转写。')

    def record(self):
        from .windows_recording import Recorder

        self.recording_lecture = self.require_lecture()
        path = self.library_root.parent / 'Recordings' / f'{uuid.uuid4().hex}.wav'
        recorder = Recorder(path)
        recorder.start()
        self.recorder = recorder
        self.status.set('正在录音 · 保存音频 · 结束后可生成课后转写')

    def stop_recording(self):
        if self.recorder is None:
            return
        recorder = self.recorder
        recorder.stop()
        self.recorder = None
        if recorder.frames:
            with Library(self.library_root) as lib:
                lib.import_audio(self.recording_lecture, [recorder.path])
        self.refresh()
        self.status.set('录音已结束；原始 WAV 保留在实验资料库旁的 Recordings 目录。')
        if recorder.error:
            messagebox.showwarning('录音出现异常', recorder.error)

    def run(self):
        lecture = self.require_lecture()
        backend, language = self.asr_backend.get(), self.asr_language.get()
        if backend == 'whisper' and language != 'English':
            raise ValueError('auto 仅适用于 Qwen；Whisper tiny.en 为英语模型')
        asr = filedialog.askdirectory(title=f'选择本地 {backend} 识别模型目录',
                                         initialdir=self.library_root.parent / 'Models' /
                                         ('qwen-asr' if backend == 'qwen' else 'asr'))
        if not asr:
            return
        translation = filedialog.askdirectory(title='选择本地 Marian 英译中模型目录',
                                                  initialdir=self.library_root.parent /
                                                  'Models' / 'translation')
        if not translation:
            return
        self.busy = True
        self.status.set('本地 CPU 转写中，结果按约 30 秒区间保存；请等待完成后退出。')

        def work():
            try:
                result = transcribe(self.library_root, lecture, Path(asr), Path(translation),
                                    backend=backend, language=language)
                self.events.put(('done', result))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=False).start()

    def poll(self):
        try:
            kind, value = self.events.get_nowait()
            self.busy = False
            if kind == 'error':
                self.status.set('处理失败；原录音和已提交区间保留。')
                messagebox.showerror('转写失败', value)
            else:
                self.display(value)
                self.status.set('已保存双语转写与 TXT / Markdown / SRT / JSON 导出。')
        except queue.Empty:
            pass
        self.root.after(150, self.poll)

    def display(self, content):
        self.output.delete('1.0', 'end')
        for unit in content['units']:
            self.output.insert('end', f"[{unit['logical_start']:.1f}s]\n"
                               f"{unit['english']}\n{unit['chinese']}\n\n")

    def show_latest(self):
        lecture = self.require_lecture()
        with Library(self.library_root) as lib:
            row = lib.db.execute('SELECT id FROM versions WHERE lecture_id=? '
                                 'ORDER BY created_at DESC LIMIT 1', (lecture,)).fetchone()
            if row is None:
                raise ValueError('还没有课后转写结果')
            self.display(lib.version_content(row['id']))

    def exports(self):
        lecture = self.require_lecture()
        with Library(self.library_root) as lib:
            path = lib.lecture_path(lecture) / 'Transcripts'
        os.startfile(str(path))

    def close(self):
        if self.recorder is not None:
            if not messagebox.askyesno('正在录音', '结束当前录音并退出？'):
                return
            try:
                self.stop_recording()
            except Exception as error:
                messagebox.showerror('保存失败，原始 WAV 已保留', str(error))
                return
        if self.busy:
            messagebox.showinfo('正在保存', '请等待转写完成后再退出，已完成区间会持续保存。')
            return
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description='LectureFlow Windows experimental MVP')
    parser.add_argument('--library-root', type=Path,
                        default=Path(os.environ.get('LOCALAPPDATA', Path.home())) /
                        'LectureFlow-Windows-Preview' / 'Library')
    parser.add_argument('--prepare-models', action='store_true',
                        help='联网下载固定版本模型后退出（约 0.4 GB）')
    parser.add_argument('--prepare-qwen', action='store_true',
                        help='下载 Qwen3-ASR 1.7B（约4.7 GB），CPU / 系统RAM')
    args = parser.parse_args()
    if args.prepare_qwen:
        from .windows_models import prepare_qwen

        prepare_qwen(args.library_root.parent / 'Models')
        return
    if args.prepare_models:
        from .windows_models import prepare

        prepare(args.library_root.parent / 'Models')
        print('模型已就绪：', args.library_root.parent / 'Models')
        return
    configure(args.library_root.parent / 'Cache')
    root = tk.Tk()
    App(root, args.library_root)
    root.mainloop()


if __name__ == '__main__':
    main()
