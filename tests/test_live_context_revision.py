"""Behavioral contracts for the production (deferred) translation scheduler."""
import base64
import io
import json
from types import SimpleNamespace

import numpy as np
import pytest

from lectureflow import live, live_store
from lectureflow.library import Library
from lectureflow.live import CaptionEngine, InputBuffer


class Session:
    def __init__(self):
        self.count = 0
        self.languages = []

    def init_streaming(self, **options):
        self.count += 1
        self.languages.append(options['language'])
        return SimpleNamespace(text=f'Utterance {self.count}')

    def feed_audio(self, pcm, state):
        return state

    def finish_streaming(self, state):
        return state


class Translator:
    def __init__(self):
        self.calls = []
        self.fail = False

    def translate(self, text, *, context='', following='', **options):
        if self.fail:
            raise RuntimeError('synthetic translation failure')
        self.calls.append((text, context, following))
        return {'text': f'译文 {text} / 前文 {context} / 后文 {following}'}


def make_engine(monkeypatch, *, deferred=True, translate=True, publish=None):
    clock = [0.0]
    monkeypatch.setattr(live.time, 'monotonic', lambda: clock[0])
    events = []
    translator = Translator()
    engine = CaptionEngine(Session(), translator, publish or (lambda e, *_: events.append(e)),
                           deferred=deferred, translate=translate)
    return engine, translator, events, clock


def finish_utterance(engine, index):
    engine.feed(np.full(4000, 0.1, dtype=np.float32), index * .25)
    engine.finish()


def test_short_backlog_catches_up_during_continuous_speech_without_idle_polish(monkeypatch):
    engine, translator, events, _ = make_engine(monkeypatch)
    finish_utterance(engine, 0)
    finish_utterance(engine, 1)
    engine.service(8)
    assert not translator.calls
    engine.feed(np.full(4000, .1, dtype=np.float32), .5)
    assert engine.state is not None  # The teacher is still speaking.
    engine.service(2)
    engine.service(2)
    assert translator.calls == [
        ('Utterance 1', '', 'Utterance 2'),
        ('Utterance 2', 'Utterance 1', ''),
    ]
    assert not engine.pending_translations
    assert any(e['status'] == 'revised' and '后文 Utterance 2' in e['chinese'] for e in events)
    engine.polish()
    engine.service(0)
    assert len(translator.calls) == 2  # No duplicate revision without new evidence.


def test_leaked_revision_keeps_previous_translation_pending_and_can_retry(monkeypatch):
    from lectureflow.adapters import HyMTTranslator

    engine, _, events, _ = make_engine(monkeypatch)
    translator = HyMTTranslator.__new__(HyMTTranslator)
    translator._generate = lambda _: {'text': '已有译文', 'truncated': False}
    engine.translator = translator
    finish_utterance(engine, 0)
    engine.service(0)
    entry = engine.recent[0]
    finish_utterance(engine, 1)
    key = entry['id']
    job = engine.pending_translations[key]
    completed = engine.translations_completed
    previous_events = len(events)
    translator._generate = lambda _: {
        'text': '后续讲话（仅作消歧参考，不要翻译或补写）：邻段', 'truncated': False,
    }
    with pytest.raises(ValueError, match='上下文提示'):
        engine.complete_translation(key)
    assert entry['chinese'] == '已有译文'
    assert entry['status'] != 'locked'
    assert engine.pending_translations[key] is job
    assert engine.translations_completed == completed
    assert len(events) == previous_events
    translator._generate = lambda _: {'text': '当前目标的新译文', 'truncated': False}
    engine.complete_translation(key)
    assert key not in engine.pending_translations
    assert entry['chinese'] == '当前目标的新译文'
    assert engine.translations_completed == completed + 1
    assert engine.context_translations_completed == 1


@pytest.mark.parametrize('deferred', [True, False])
def test_both_previous_utterances_receive_new_context_before_lock(monkeypatch, deferred):
    engine, translator, events, _ = make_engine(monkeypatch, deferred=deferred)
    for index in range(4):
        finish_utterance(engine, index)
        if index == 0:
            engine.service(0)  # First translation existed before later context.
    assert ('Utterance 1', '', 'Utterance 2\nUtterance 3') in translator.calls
    engine.lock_all()
    assert any(t == 'Utterance 2' and f == 'Utterance 3\nUtterance 4'
               for t, _, f in translator.calls)
    first = [e for e in events if e['english'] == 'Utterance 1']
    assert first[-1]['status'] == 'locked'
    assert 'Utterance 3' in first[-1]['chinese']
    assert 'Utterance 4' not in first[-1]['chinese']  # Finite revision horizon.
    assert all((e['start'], e['end']) == (0, .25) for e in first)
    locked = set()
    for event in events:
        assert event['id'] not in locked
        if event['status'] == 'locked':
            locked.add(event['id'])


def test_new_context_cannot_reset_oldest_deadline_or_starve_a_revision(monkeypatch):
    engine, translator, _, clock = make_engine(monkeypatch)
    finish_utterance(engine, 0)
    engine.service(0)
    clock[0] = 1
    finish_utterance(engine, 1)
    engine.service(0)  # Translate second, leave first's revision queued at t=1.
    clock[0] = 5
    finish_utterance(engine, 2)  # Merge extra context without resetting t=1.
    engine.service(8)
    assert len(translator.calls) == 2
    clock[0] = 7
    engine.service(8)
    assert translator.calls[-1] == ('Utterance 1', '', 'Utterance 2\nUtterance 3')


def test_incomplete_sentence_gets_initial_translation_after_one_second(monkeypatch):
    engine, translator, events, clock = make_engine(monkeypatch)
    engine.feed(np.full(4000, .1, dtype=np.float32), 0)
    clock[0] = .99
    engine.service(0)
    assert not translator.calls
    clock[0] = 1
    engine.service(0)
    assert translator.calls == [('Utterance 1', '', '')]
    assert events[-1]['status'] == 'temporary' and events[-1]['chinese']
    # Finalizing keeps the visible draft until the final translation is ready.
    engine.finish()
    assert events[-1]['status'] == 'stable' and events[-1]['chinese']


def test_queue_pressure_persists_context_revision_before_locking_and_export(tmp_path, monkeypatch):
    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course('Synthetic')['id'], 'Synthetic')
        session = live_store.create(lib, lecture['id'])
        engine, translator, _, _ = make_engine(monkeypatch, publish=lambda e, en, zh:
            live_store.save(lib, session['id'], e, english=en, translate=zh))
        for index in range(100):
            finish_utterance(engine, index)
            engine.service(8)
            assert len(engine.pending_translations) <= 3
            assert len(engine.recent) <= 3 and len(engine.translation_context) <= 3
        engine.lock_all()
        assert not engine.pending_translations and not engine.translation_context
        rows = live_store.history(lib, lecture['id'])['captions']
        assert len(rows) == 100
        for index, row in enumerate(rows):
            assert row['status'] == 'locked'
            assert row['english'] == f'Utterance {index + 1}'
            for later in range(index + 2, min(index + 4, 101)):
                assert f'Utterance {later}' in row['chinese']
        assert len(translator.calls) == 100  # Coalesced work retains both later utterances.
        original = lib.db.execute(
            "SELECT chinese FROM live_revisions WHERE status='stable' ORDER BY rowid LIMIT 1"
        ).fetchone()
        assert original[0] == ''
        exported = live_store.export(lib, lecture['id'])
        from pathlib import Path
        data = json.loads((Path(exported['directory']) / 'captions.json').read_text())
        assert data['captions'] == rows


def test_failure_preserves_pending_work_and_never_falsely_locks_caption(monkeypatch):
    engine, translator, events, _ = make_engine(monkeypatch)
    for i in range(3):
        finish_utterance(engine, i)
    translator.fail = True
    with pytest.raises(RuntimeError, match='synthetic'):
        finish_utterance(engine, 3)
    assert events[-1]['english'] == 'Utterance 4' and events[-1]['status'] == 'stable'
    assert not any(e['status'] == 'locked' for e in events)
    translator.fail = False
    engine.lock_all()
    locked = [e for e in events if e['status'] == 'locked']
    assert len(locked) == 4 and all(e['chinese'] for e in locked)
    assert 'Utterance 2\nUtterance 3' in locked[0]['chinese']


def test_mode_switch_completes_revisions_using_original_save_flags(tmp_path, monkeypatch):
    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course('Synthetic')['id'], 'Synthetic')
        session = live_store.create(lib, lecture['id'])
        engine, translator, _, _ = make_engine(monkeypatch, publish=lambda e, en, zh:
            live_store.save(lib, session['id'], e, english=en, translate=zh))
        engine.configure(False, True)
        for i in range(2):
            finish_utterance(engine, i)
        engine.configure(True, False)
        rows = live_store.history(lib, lecture['id'])['captions']
        assert all(r['english'] == '' and r['chinese'] and r['status'] == 'locked' for r in rows)
        assert '后文 Utterance 2' in rows[0]['chinese']
        assert not lib.db.execute("SELECT 1 FROM live_revisions WHERE english<>''").fetchone()
        calls = len(translator.calls)
        finish_utterance(engine, 2)
        engine.lock_all()
        assert len(translator.calls) == calls


def test_real_protocol_uses_required_revision_queue_and_flushes_on_stop(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    with Library(root) as lib:
        lecture = lib.create_lecture(lib.create_course('Synthetic')['id'], 'Synthetic')
        session = live_store.create(lib, lecture['id'])
    queue = InputBuffer(capacity=64)
    flags = {'english': True, 'translate': True, 'asr': 'asr-large'}
    queue.put({'type': 'configure', **flags})
    for i in range(28):  # Four utterances, each followed by silence; never await idle polish.
        pcm = np.full(4000, .1 if i % 7 == 0 else 0, dtype='<f4')
        queue.put({'type': 'audio', 'start': i * .25,
                   'pcm': base64.b64encode(pcm.tobytes()).decode(), **flags})
    queue.put({'type': 'stop'})
    model = Session()
    translator = Translator()
    monkeypatch.setattr(live, 'InputBuffer', lambda: queue)
    monkeypatch.setattr(live.threading, 'Thread', lambda **_: SimpleNamespace(start=lambda: None))
    monkeypatch.setattr(live, 'QwenASR', lambda _: SimpleNamespace(session=model))
    monkeypatch.setattr(live, 'HyMTTranslator', lambda _: translator)
    monkeypatch.setattr(live, 'resolve', lambda *args: {'path': str(tmp_path)})
    monkeypatch.setattr(live, 'configure', lambda _: None)
    monkeypatch.setattr(live, 'mlx_memory', lambda: {})
    monkeypatch.setattr(live.time, 'monotonic', lambda: 0)
    output = io.StringIO()
    monkeypatch.setattr(live.sys, 'stdout', output)
    live.run_live(root, tmp_path / 'models', lecture['id'], session['id'], 'asr-large')
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[-1]['type'] == 'stopped' and messages[-1]['dropped_packets'] == 0
    assert model.languages == ['English'] * 4
    with Library(root) as lib:
        rows = live_store.history(lib, lecture['id'])['captions']
        assert len(rows) == 4 and all(r['status'] == 'locked' for r in rows)
        assert '后文 Utterance 2\nUtterance 3' in rows[0]['chinese']
        assert '后文 Utterance 3\nUtterance 4' in rows[1]['chinese']


def test_repeated_phrase_in_new_utterance_still_gets_a_temporary_translation(monkeypatch):
    engine, translator, events, clock = make_engine(monkeypatch)
    engine.feed(np.full(4000, .1, dtype=np.float32), 0)
    clock[0] = 1
    engine.service(0)
    engine.finish()
    engine.lock_all()
    engine.session.count = 0  # Same words, genuinely spoken again.
    engine.feed(np.full(4000, .1, dtype=np.float32), .25)
    clock[0] = 2
    engine.service(0)
    assert events[-1]['status'] == 'temporary' and events[-1]['chinese']
    assert events[-1]['english'] == 'Utterance 1'


def test_production_input_buffer_retains_short_inference_stall_in_order():
    queue = InputBuffer()
    queue.put({'type': 'configure'})
    for i in range(160):  # Forty seconds at the production 250 ms packet size.
        queue.put({'type': 'audio', 'start': i * .25})
    queue.put({'type': 'stop'})
    queue.close()
    assert queue.dropped == 0
    assert queue.get()['type'] == 'configure'
    assert [queue.get()['start'] for _ in range(160)] == [i * .25 for i in range(160)]
    assert queue.get()['type'] == 'stop'
    assert queue.get() is None


@pytest.mark.parametrize('backlog,deadline', [(0, 1), (3, 6), (8, 6)])
@pytest.mark.parametrize('translation_cost', [0, .7])
def test_older_context_revision_does_not_restart_current_translation_wait(
    monkeypatch, backlog, deadline, translation_cost,
):
    engine, translator, events, clock = make_engine(monkeypatch)
    finish_utterance(engine, 0)
    engine.service(0)
    finish_utterance(engine, 1)
    engine.service(0)  # Second first-translation done, first's context revision remains.
    key, revision = next(iter(engine.pending_translations.items()))
    assert revision.following == 'Utterance 2'
    engine.feed(np.full(4000, .1, dtype=np.float32), .5)
    translate = translator.translate

    def timed_translate(*args, **kwargs):
        clock[0] += translation_cost
        return translate(*args, **kwargs)

    translator.translate = timed_translate
    clock[0] = deadline
    engine.service(backlog)  # Required FIFO still wins this turn.
    assert key not in engine.pending_translations
    assert translator.calls[-1] == ('Utterance 1', '', 'Utterance 2')
    assert engine.current['chinese'] == ''
    clock[0] += .25
    engine.service(backlog)  # No additional one/six-second wait after older work.
    assert translator.calls[-1] == ('Utterance 3', 'Utterance 1\nUtterance 2', '')
    assert events[-1]['status'] == 'temporary' and events[-1]['chinese']
    engine.finish()
    engine.lock_all()
    assert not engine.pending_translations
    assert all(e['chinese'] for e in events if e['status'] == 'locked')


@pytest.mark.parametrize('backlog,deadline', [(0, 1), (8, 6)])
def test_new_utterance_does_not_inherit_an_idle_temporary_timer(monkeypatch, backlog, deadline):
    engine, translator, events, clock = make_engine(monkeypatch)
    clock[0] = 30  # Long idle before this utterance, not thirty seconds of draft waiting.
    engine.feed(np.full(4000, .1, dtype=np.float32), 0)
    clock[0] = 30 + deadline - .01
    engine.service(backlog)
    assert not translator.calls
    clock[0] = 30 + deadline
    engine.service(backlog)
    assert len(translator.calls) == 1
    assert events[-1]['chinese']


@pytest.mark.parametrize('deferred', [True, False])
def test_first_asr_compute_time_counts_toward_current_wait(monkeypatch, deferred):
    engine, translator, events, clock = make_engine(monkeypatch, deferred=deferred)
    initialize = engine.session.init_streaming

    def slow_initialize(**options):
        clock[0] += 1.5
        return initialize(**options)

    engine.session.init_streaming = slow_initialize
    engine.feed(np.full(4000, .1, dtype=np.float32), 0)
    if deferred:
        engine.service(0)
    assert len(translator.calls) == 1
    assert events[-1]['chinese']


def test_current_partial_changes_keep_their_translation_cooldown(monkeypatch):
    engine, translator, events, clock = make_engine(monkeypatch)
    engine.feed(np.full(4000, .1, dtype=np.float32), 0)
    clock[0] = 1
    engine.service(0)
    clock[0] = 1.5
    engine.state.text += ' continues'
    engine.feed(np.full(4000, .1, dtype=np.float32), .25)
    clock[0] = 1.99
    engine.service(0)
    assert len(translator.calls) == 1
    clock[0] = 2
    engine.service(0)
    assert len(translator.calls) == 2
    assert events[-1]['english'] == 'Utterance 1 continues'
    engine.service(0)
    assert len(translator.calls) == 2  # Identical partial still deduplicates.


def test_failed_temporary_translation_does_not_restart_wait_or_mark_text_done(monkeypatch):
    engine, translator, events, clock = make_engine(monkeypatch)
    engine.feed(np.full(4000, .1, dtype=np.float32), 0)
    clock[0] = 6
    translator.fail = True
    with pytest.raises(RuntimeError, match='synthetic'):
        engine.service(8)
    assert engine.last_temporary_translation_at == 0
    assert engine.last_temporary_text == ''
    assert not events[-1]['chinese']
    translator.fail = False
    engine.service(8)
    assert events[-1]['chinese']
    assert engine.last_temporary_translation_at == 6


def test_reject_neighbour_echo_after_required_revision_and_clean_up(monkeypatch):
    engine, _, engine_events, _ = make_engine(monkeypatch)
    completed = {"Utterance 1": "这是本句原义。", "Utterance 2": "明天上午开始新的课程内容。"}
    calls = []
    def response(text, *, context="", following=""):
        calls.append((text, following))
        candidate = completed[text] + (completed["Utterance 2"] if following else "")
        return {"text": candidate}
    engine.translator.translate = response
    for i in range(2):
        finish_utterance(engine, i)
        engine.service(0)
    engine.lock_all()
    assert calls == [("Utterance 1", ""), ("Utterance 2", ""), ("Utterance 1", "Utterance 2")]
    assert engine.context_translations_completed == 1
    assert engine.revision_guard.anchors == {}
    locked = [e["chinese"] for e in engine_events if e["status"] == "locked"]
    assert locked == list(completed.values())
