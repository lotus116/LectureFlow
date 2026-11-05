import json
import subprocess
import sys
import tkinter as tk
import wave

import pytest

from lectureflow import locking
from lectureflow.library import Library
from lectureflow.windows import App, transcribe


def test_lock_excludes_another_process(tmp_path):
    path = tmp_path / 'lock'
    code = '''import sys
from lectureflow import locking
with open(sys.argv[1], 'a+') as handle:
    try:
        locking.flock(handle, locking.LOCK_EX | locking.LOCK_NB)
    except BlockingIOError:
        sys.exit(17)
'''
    with path.open('a+') as handle:
        locking.flock(handle, locking.LOCK_EX | locking.LOCK_NB)
        assert subprocess.run([sys.executable, '-c', code, str(path)]).returncode == 17
    assert subprocess.run([sys.executable, '-c', code, str(path)]).returncode == 0


def test_windows_pipeline_preserves_original_and_exports(tmp_path, monkeypatch):
    from lectureflow import windows_models

    class ASR:
        def __init__(self, path):
            pass

        def transcribe(self, audio, context=''):
            return {'text': 'A synthetic lesson.', 'truncated': False}

    class Translator:
        def __init__(self, path):
            pass

        def translate(self, text, *args):
            return {'text': '合成测试课程。', 'truncated': False}

    monkeypatch.setattr(windows_models, 'WhisperASR', ASR)
    monkeypatch.setattr(windows_models, 'MarianTranslator', Translator)
    source = tmp_path / '测试.wav'
    with wave.open(str(source), 'wb') as out:
        out.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        out.writeframes(b'\0\0' * 16000)
    original = source.read_bytes()
    root = tmp_path / 'Library'
    with Library(root) as lib:
        course = lib.create_course('Windows: <> | ? *')
        lecture = lib.create_lecture(course['id'], 'Lecture 01')
        lib.import_audio(lecture['id'], [source])
    for _ in range(2):
        result = transcribe(root, lecture['id'], tmp_path, tmp_path)
        assert result['task']['state'] == 'completed'
        assert result['units'][0]['chinese'] == '合成测试课程。'
        assert result['config']['backend'] == 'windows-cpu-experimental'
    with Library(root) as lib:
        assert lib.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 2
        exports = list(lib.lecture_path(lecture['id']).rglob('transcript.json'))
        assert len(exports) == 2
        assert json.loads(exports[0].read_text(encoding='utf-8'))['units']
    assert source.read_bytes() == original


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows Tk smoke check')
def test_windows_ui_starts(tmp_path):
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root, tmp_path / 'Library')
        root.update()
        assert app.busy is False
        assert app.recorder is None
    finally:
        root.destroy()
