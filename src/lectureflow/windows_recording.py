"""Bounded microphone capture, independent of model inference."""
import queue
import threading
import wave


class Recorder:
    def __init__(self, path):
        self.path = path
        self.packets = queue.Queue(maxsize=128)
        self.stopping = threading.Event()
        self.error = None
        self.frames = 0
        self.stream = None
        self.thread = None

    def start(self):
        import sounddevice as sd

        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation protects any previously captured audio.
        self.handle = self.path.open('xb')
        self.wave = wave.open(self.handle, 'wb')
        self.wave.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))

        def callback(data, frames, time, status):
            if status:
                self.error = str(status)
            try:
                self.packets.put_nowait(bytes(data))
            except queue.Full:
                self.error = '录音写入跟不上采集，已请求停止；已写入原音保留。'
                self.stopping.set()
                raise sd.CallbackStop from None

        def writer():
            try:
                while not self.stopping.is_set() or not self.packets.empty():
                    try:
                        packet = self.packets.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    self.wave.writeframesraw(packet)
                    self.frames += len(packet) // 2
            except Exception as error:
                self.error = str(error)
                self.stopping.set()
            finally:
                self.wave.close()
                self.handle.close()

        self.thread = threading.Thread(target=writer, daemon=False)
        self.thread.start()
        try:
            self.stream = sd.RawInputStream(samplerate=16000, channels=1, dtype='int16',
                                            blocksize=4000, callback=callback)
            self.stream.start()
        except Exception:
            self.stop()
            raise

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.stopping.set()
        if self.thread is not None:
            self.thread.join()
