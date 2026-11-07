"""Synthetic audio verifies boundaries and ownership, not real ASR/translation quality."""
from types import SimpleNamespace

import numpy as np
import pytest

from lectureflow.live import CaptionEngine


class Speech:
    def __init__(self, punctuated=True):
        self.sessions = []
        self.punctuated = punctuated

    def init_streaming(self, **options):
        state = SimpleNamespace(text='', packets=0, index=len(self.sessions))
        self.sessions.append(state)
        return state

    def feed_audio(self, pcm, state):
        state.packets += 1
        # Recognizer only changes text when more speech arrives.
        if pcm.any():
            suffix = '.' if self.punctuated else ''
            state.text = f'part {state.index} words {state.packets}' + suffix
        return state

    def finish_streaming(self, state):
        return state


class Translator:
    def __init__(self):
        self.calls = []

    def translate(self, text, **options):
        self.calls.append((text, options))
        return {'text': '翻译：' + text}


def setup(punctuated=True):
    events, speech, translator = [], Speech(punctuated), Translator()
    engine = CaptionEngine(speech, translator, lambda row, *_: events.append(row.copy()))
    return engine, speech, translator, events


def feed(engine, start, duration, voiced=True):
    for i in range(round(duration * 4)):
        engine.feed(np.full(4000, .05 if voiced else 0, dtype=np.float32), start + i * .25)


@pytest.mark.parametrize('punctuated', [True, False])
@pytest.mark.parametrize('pause', [.75, 1.25, 1.75])
def test_hesitation_keeps_same_caption_and_translation_target(punctuated, pause, monkeypatch):
    engine, speech, translator, events = setup(punctuated)
    monkeypatch.setattr('lectureflow.live.time.monotonic', lambda: 100)
    engine.last_temporary_translation_at = 0
    feed(engine, 0, 1)
    first = events[-1]['id']
    feed(engine, 1, pause, False)
    feed(engine, 1 + pause, 1)
    assert engine.current['id'] == first
    assert len(speech.sessions) == 1
    assert all(row['status'] == 'temporary' for row in events)
    engine.finish()
    assert translator.calls[-1][0] == events[-1]['english']


def test_streaming_initial_translation_precedes_endpoint(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('lectureflow.live.time.monotonic', lambda: clock[0])
    engine, _, translator, events = setup()
    feed(engine, 0, .25)
    clock[0] = 1.5
    feed(engine, .25, .25)
    assert translator.calls and events[-1]['chinese']
    assert events[-1]['status'] == 'temporary'


def test_twelve_second_asr_rollover_preserves_id_text_and_audio():
    engine, speech, translator, events = setup()
    feed(engine, 0, 12)
    first = engine.current['id']
    assert len(speech.sessions) == 2
    assert not any(row['status'] == 'stable' for row in events)
    prefix = engine.current['english']
    feed(engine, 12, 3)
    assert engine.current['id'] == first
    assert engine.current['english'].startswith(prefix + ' ')
    feed(engine, 15, 2, False)
    stable = [row for row in events if row['status'] == 'stable']
    assert len(stable) == 1 and stable[0]['start'] == 0 and stable[0]['end'] == 17
    assert stable[0]['english'].count('part 0') == 1
    assert stable[0]['english'].count('part 1') == 1
    assert [s.packets for s in speech.sessions] == [48, 20]
    assert translator.calls[-1][0] == stable[0]['english']


def test_continuous_speech_is_bounded_and_next_caption_has_no_old_prefix():
    engine, speech, _, events = setup()
    feed(engine, 0, 24)
    assert engine.current is None and engine.utterance_pcm is None
    stable = [row for row in events if row['status'] == 'stable']
    assert len(stable) == 1 and stable[0]['end'] == 24
    assert [s.packets for s in speech.sessions] == [48, 48]
    feed(engine, 24, 1)
    assert engine.current['start'] == 24
    assert 'part 0' not in engine.current['english']
    engine.finish()
    engine.lock_all()
    assert not engine.pending_translations
    assert engine.context_translations_completed > 0


@pytest.mark.parametrize('ending', ['finish', 'gap', 'configure'])
def test_explicit_boundaries_flush_without_waiting_for_silence(ending):
    engine, _, _, events = setup()
    feed(engine, 0, 12.25)
    if ending == 'finish':
        engine.finish()
    elif ending == 'gap':
        feed(engine, 14, .25)
    else:
        engine.configure(False, True)
    stable = [row for row in events if row['status'] == 'stable']
    assert len(stable) == 1 and stable[0]['end'] == 12.25
    assert 'part 0' in stable[0]['english'] and 'part 1' in stable[0]['english']


def test_stop_exactly_at_rollover_does_not_finalize_empty_asr():
    engine, speech, _, events = setup()
    original = speech.finish_streaming

    def finish_nonempty(state):
        assert state.packets > 0
        return original(state)

    speech.finish_streaming = finish_nonempty
    feed(engine, 0, 12)
    engine.finish()
    stable = [row for row in events if row['status'] == 'stable']
    assert len(stable) == 1 and stable[0]['end'] == 12
    assert stable[0]['english'] == 'part 0 words 48.'
