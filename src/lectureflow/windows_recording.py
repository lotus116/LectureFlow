"""Bounded microphone capture, independent of model inference."""
import os
import queue
import struct
import threading
import wave
from pathlib import Path

from . import locking
from .runtime import write_json


class Recorder:
    def __init__(self, path):
        self.path = path
        self.packets = queue.Queue(maxsize=128)
        self.stopping = threading.Event()
        self.error = None
        self.interrupted = threading.Event()
        self.frames = 0
        self.stream = None
        self.thread = None

    def start(self):
        import sounddevice as sd

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lease = self.path.with_suffix('.lock').open('a+')
        locking.flock(self.lease, locking.LOCK_EX | locking.LOCK_NB)
        # Exclusive creation protects any previously captured audio.
        self.handle = self.path.open('xb')
        write_json(self.path.with_suffix('.capture.json'), {'sealed': False})
        self.wave = wave.open(self.handle, 'wb')
        self.wave.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))

        def callback(data, frames, time, status):
            if self.stopping.is_set():
                raise sd.CallbackStop
            if status:
                self.error = str(status)
                self.interrupted.set()
                self.stopping.set()
                raise sd.CallbackStop
            try:
                self.packets.put_nowait(bytes(data))
            except queue.Full:
                self.error = '录音写入跟不上采集，已请求停止；已写入原音保留。'
                self.interrupted.set()
                self.stopping.set()
                raise sd.CallbackStop from None

        def writer():
            try:
                while not self.stopping.is_set() or not self.packets.empty():
                    try:
                        packet = self.packets.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    self.wave.writeframes(packet)
                    self.handle.flush()
                    os.fsync(self.handle.fileno())
                    self.frames += len(packet) // 2
            except Exception as error:
                self.error = str(error)
                self.interrupted.set()
                self.stopping.set()
            finally:
                try:
                    self.wave.close()
                    self.handle.flush()
                    os.fsync(self.handle.fileno())
                except Exception as error:
                    self.error = str(error)
                    self.interrupted.set()
                finally:
                    try:
                        self.handle.close()
                    except OSError as error:
                        self.error = str(error)
                        self.interrupted.set()

        self.thread = threading.Thread(target=writer, daemon=False)
        self.thread.start()
        try:
            self.stream = sd.RawInputStream(samplerate=16000, channels=1, dtype='int16',
                                            blocksize=4000, callback=callback,
                                            finished_callback=self._finished)
            self.stream.start()
        except Exception:
            self.stop()
            raise

    def _finished(self):
        if not self.stopping.is_set():
            self.error = "采集设备已停止；已保存原音保留。"
            self.interrupted.set()
            self.stopping.set()

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
            except Exception as error:
                self.error = str(error)
                self.interrupted.set()
            finally:
                try:
                    self.stream.close()
                except Exception as error:
                    self.error = str(error)
                    self.interrupted.set()
                self.stream = None
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
        if getattr(self, 'lease', None) is not None:
            self.lease.close()
            self.lease = None


def recover_recordings(directory: Path) -> list[Path]:
    """Copy only this recorder's canonical PCM WAV layout; never rewrite originals."""
    import json
    import tempfile

    recovered = []
    for source in sorted(directory.glob('*.wav')):
        if source.name.startswith('recovered-'):
            continue
        receipt = source.with_suffix('.recovery.json')
        if receipt.exists() and json.loads(receipt.read_text(encoding='utf-8')).get('imported'):
            continue
        with source.with_suffix('.lock').open('a+') as lease:
            try:
                locking.flock(lease, locking.LOCK_EX | locking.LOCK_NB)
            except BlockingIOError:
                continue
            with source.open('rb') as handle:
                header = handle.read(44)
            if (len(header) != 44 or header[:4] != b'RIFF' or header[8:16] != b'WAVEfmt '
                    or header[36:40] != b'data'
                    or struct.unpack('<IHHIIHH', header[16:36]) != (16, 1, 1, 16000, 32000, 2, 16)):
                continue
            size = source.stat().st_size - 44
            size -= size % 2  # A torn final sample is not valid PCM.
            if size <= 0 or size > 0xffffffff - 36:
                continue
            if (struct.unpack('<I', header[40:44])[0] == size
                    and struct.unpack('<I', header[4:8])[0] == size + 36):
                if source.with_suffix('.capture.json').exists():
                    recovered.append(source)
                continue
            target = source.with_name('recovered-' + source.name)
            if not target.exists():
                fd, temporary = tempfile.mkstemp(prefix='.recovery-', dir=directory)
                try:
                    with os.fdopen(fd, 'wb') as output, source.open('rb') as original:
                        output.write(header[:4] + struct.pack('<I', size + 36) + header[8:40]
                                     + struct.pack('<I', size))
                        original.seek(44)
                        remaining = size
                        while remaining:
                            chunk = original.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise OSError('恢复时原音被截断')
                            output.write(chunk)
                            remaining -= len(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            # Validate a previous recovery attempt before presenting it.
            with wave.open(str(target)) as audio:
                if audio.getnframes() != size // 2 or target.stat().st_size != size + 44:
                    raise ValueError('恢复副本不完整，请保留原文件并检查磁盘空间')
            write_json(receipt, {'imported': False, 'file': target.name})
            recovered.append(target)
    return recovered
