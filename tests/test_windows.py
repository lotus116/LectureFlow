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


@pytest.mark.parametrize("export_failure", [False, True])
@pytest.mark.parametrize("backend", ["whisper", "qwen"])
def test_windows_pipeline_preserves_original_and_exports(
    tmp_path, monkeypatch, backend, export_failure,
):
    from lectureflow import windows_models

    class ASR:
        def __init__(self, path, language="English"):
            pass

        def transcribe(self, audio, context=''):
            return {'text': 'A synthetic lesson.', 'truncated': False}

    class Translator:
        def __init__(self, path):
            pass

        def translate(self, text, *args):
            return {'text': '合成测试课程。', 'truncated': False}

    monkeypatch.setattr(windows_models, 'WhisperASR', ASR)
    monkeypatch.setattr(windows_models, 'QwenCPUASR', ASR)
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
    if export_failure:
        from lectureflow import worker

        def fail_export(*args):
            raise OSError('synthetic disk full')
        monkeypatch.setattr(worker, 'export_version', fail_export)
    for _ in range(2):
        result = transcribe(root, lecture['id'], tmp_path, tmp_path, backend=backend)
        assert result['task']['state'] == 'completed'
        if export_failure:
            import types

            assert result['task']['error']
            app = App.__new__(App)
            app.library_root = root
            app.require_lecture = lambda: lecture['id']
            app.status = types.SimpleNamespace(set=lambda text: None)
            app.retry_export()
            with Library(root) as lib:
                assert lib.row('tasks', result['task']['id'])['error'] is None
        assert result['units'][0]['chinese'] == '合成测试课程。'
        assert result['config']['backend'] == 'windows-cpu-experimental'
        assert result['config']['asr_backend'] == backend
        assert result['config']['inference_device'] == 'cpu'
        assert result['config']['model_memory'] == 'system_ram'
    with Library(root) as lib:
        assert lib.db.execute('SELECT COUNT(*) FROM versions').fetchone()[0] == 2
        exports = list(lib.lecture_path(lecture['id']).rglob('transcript.json'))
        assert len(exports) == 2
        exported = exports[0].read_text(encoding='utf-8')
        assert str(tmp_path) not in exported
        assert json.loads(exported)['units']
        # Old Windows snapshots must also be safe when exported again.
        from lectureflow.exporting import export_version

        config = result['config']
        config['models'] = {'asr': r'C:\Users\PrivateUser\secret-model'}
        lib.db.execute('UPDATE versions SET config=? WHERE id=?',
                       (json.dumps(config), result['task']['version_id']))
        export_version(lib, result['task']['version_id'])
        assert all('PrivateUser' not in p.read_text(encoding='utf-8') for p in exports)
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


def test_qwen_explicit_cpu_offline_and_language(tmp_path, monkeypatch):
    import types

    from lectureflow.windows_models import QwenCPUASR

    calls = {}

    class Model:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            calls.update(kwargs)
            return cls()

        def transcribe(self, **kwargs):
            calls['inference'] = kwargs
            return [types.SimpleNamespace(text='Mixed speech', language='Chinese')]

    monkeypatch.setitem(sys.modules, 'torch', types.SimpleNamespace(
        float32='float32', set_num_threads=lambda n: None))
    monkeypatch.setitem(sys.modules, 'qwen_asr', types.SimpleNamespace(Qwen3ASRModel=Model))
    (tmp_path / 'config.json').write_text('{}')
    adapter = QwenCPUASR(tmp_path, language='auto')
    result = adapter.transcribe(tmp_path / 'clip.wav')
    assert calls['device_map'] == 'cpu'
    assert calls['local_files_only'] is True
    assert calls['dtype'] == 'float32'
    assert calls['inference']['language'] is None
    assert result['truncation_status'] == 'not_exposed_by_qwen_wrapper'


def test_invalid_backend_rejected_before_loading(tmp_path):
    with pytest.raises(ValueError, match='识别后端'):
        transcribe(tmp_path, 'unused', tmp_path, tmp_path, backend='cuda')


def test_poll_reports_interruption_and_export_failure(monkeypatch, tmp_path):
    import queue
    import threading
    import types

    from lectureflow import windows

    shown = []
    monkeypatch.setattr(windows.messagebox, 'showwarning', lambda *args: shown.append(args))
    app = App.__new__(App)
    app.library_root = tmp_path
    app.events = queue.Queue()
    app.status = types.SimpleNamespace(set=shown.append)
    app.root = types.SimpleNamespace(after=lambda *args: None, update_idletasks=lambda: None)
    app.recorder = types.SimpleNamespace(interrupted=threading.Event())
    app.recorder.interrupted.set()
    app.stop_recording = lambda: setattr(app, 'recorder', None)
    app.display = lambda value: None
    app.poll()
    assert any('录音已中断' in item for item in shown if isinstance(item, str))
    app.events.put(('done', {'task': {'error': 'export disk full'}}))
    app.poll()
    assert any('导出失败' in item for item in shown if isinstance(item, str))
    assert not any('四种' in item for item in shown if isinstance(item, str))
