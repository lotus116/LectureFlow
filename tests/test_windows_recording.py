import subprocess
import sys
import threading
import types
import wave

import pytest

from lectureflow.library import Library
from lectureflow.windows_recording import Recorder, recover_recordings


class CallbackStop(Exception):
    pass


class Stream:
    def __init__(self, **kwargs):
        self.callback = kwargs['callback']

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def fake_audio(monkeypatch):
    monkeypatch.setitem(sys.modules, 'sounddevice', types.SimpleNamespace(
        RawInputStream=Stream, CallbackStop=CallbackStop))


def test_killed_recorder_restarts_recovers_and_imports_all_frames(tmp_path, monkeypatch):
    # The child uses the real Recorder and writer; only microphone input is synthetic.
    code = '''import sys, types, time
from pathlib import Path
class Stream:
 def __init__(self, **kw): self.callback=kw['callback']
 def start(self): pass
 def stop(self): pass
 def close(self): pass
sys.modules['sounddevice']=types.SimpleNamespace(RawInputStream=Stream,CallbackStop=RuntimeError)
from lectureflow.windows_recording import Recorder
r=Recorder(Path(sys.argv[1]));r.start()
for _ in range(12):r.stream.callback(b'\\x01\\x00'*4000,4000,None,None)
while r.frames<48000:time.sleep(.01)
print('durable',flush=True)
time.sleep(300)
'''
    source = tmp_path / 'capture.wav'
    child = subprocess.Popen([sys.executable, '-c', code, str(source)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'durable'
    finally:
        child.kill()
        child.wait(timeout=10)
    original = source.read_bytes()
    paths = recover_recordings(tmp_path)
    assert paths == [source]
    with wave.open(str(paths[0])) as audio:
        assert audio.getnframes() == 48000
    with Library(tmp_path / 'lib') as lib:
        course = lib.create_course('Synthetic')
        lecture = lib.create_lecture(course['id'], 'Recovery')
        segment = lib.import_audio(lecture['id'], paths)[0]
        assert segment['duration'] == 3
    assert source.read_bytes() == original
    from lectureflow import windows_models
    from lectureflow.windows import transcribe

    class ASR:
        def __init__(self, path):
            pass

        def transcribe(self, path, context=''):
            with wave.open(str(path)) as audio:
                assert audio.getnframes() == 48000
            return {'text': 'Synthetic complete recovery.', 'truncated': False}

    class Translator:
        def __init__(self, path):
            pass

        def translate(self, text, *args):
            return {'text': '合成录音恢复测试。', 'truncated': False}

    monkeypatch.setattr(windows_models, 'WhisperASR', ASR)
    monkeypatch.setattr(windows_models, 'MarianTranslator', Translator)
    result = transcribe(tmp_path / 'lib', lecture['id'], tmp_path, tmp_path)
    assert result['task']['state'] == 'completed'
    assert result['units'][0]['source_end'] == 3


def test_stale_header_repairs_copy_without_touching_original(tmp_path):
    source = tmp_path / 'legacy.wav'
    with wave.open(str(source), 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b'\x01\x00' * 4000)
    with source.open('ab') as audio:
        audio.write(b'\x01\x00' * 44000)
    original = source.read_bytes()
    [recovered] = recover_recordings(tmp_path)
    with wave.open(str(recovered)) as audio:
        assert audio.getnframes() == 48000
    assert source.read_bytes() == original
    assert recover_recordings(tmp_path) == [recovered]


@pytest.mark.parametrize('fault', ['disk', 'queue', 'device'])
def test_interruption_signalled_without_user_stop(tmp_path, monkeypatch, fault):
    fake_audio(monkeypatch)
    recorder = Recorder(tmp_path / 'capture.wav')
    recorder.start()
    release = threading.Event()
    entered = threading.Event()
    try:
        if fault == 'device':
            recorder._finished()
        elif fault == 'disk':
            def fail(_):
                raise OSError('synthetic disk full')
            recorder.wave.writeframes = fail
            recorder.stream.callback(b'\0\0' * 4000, 4000, None, None)
        else:
            import queue
            original = recorder.wave.writeframes

            def blocked(packet):
                entered.set()
                assert release.wait(5)
                original(packet)
            recorder.wave.writeframes = blocked
            recorder.packets = queue.Queue(maxsize=1)
            recorder.stream.callback(b'\0\0' * 4000, 4000, None, None)
            assert entered.wait(5)
            recorder.stream.callback(b'\0\0' * 4000, 4000, None, None)
            with pytest.raises(CallbackStop):
                recorder.stream.callback(b'\0\0' * 4000, 4000, None, None)
        assert recorder.interrupted.wait(5)
        assert recorder.error
    finally:
        release.set()
        recorder.stop()


def test_active_capture_not_recovered(tmp_path, monkeypatch):
    fake_audio(monkeypatch)
    recorder = Recorder(tmp_path / 'active.wav')
    recorder.start()
    try:
        assert recover_recordings(tmp_path) == []
    finally:
        recorder.stop()
