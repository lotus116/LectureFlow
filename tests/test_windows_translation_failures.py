"""Exercise Windows capture's event-to-database path without a desktop or device."""
import json
import queue
import time

import pytest
from test_live_context_revision import Session, Translator, finish_utterance

from lectureflow import live_store
from lectureflow.library import Library
from lectureflow.live import CaptionEngine
from lectureflow.windows_capture import Capture, persist_capture_event


@pytest.mark.parametrize('english', [False, True])
@pytest.mark.parametrize('recover', [False, True])
def test_windows_failure_records_share_caption_fifo_and_respect_save_flags(
    tmp_path, english, recover,
):
    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course('Synthetic')['id'], 'Synthetic')
        session = live_store.create(lib, lecture['id'])
        cap = Capture.__new__(Capture)  # No threads, microphone or Windows model are started.
        cap.session, cap.english, cap.chinese = session['id'], english, True
        cap.events, cap.errors, cap.started = queue.SimpleQueue(), [], time.monotonic()
        class ChineseTranslator(Translator):
            def translate(self, *args, **kwargs):
                super().translate(*args, **kwargs)
                return {"text": "合成译文"}
        translator = ChineseTranslator()
        e = CaptionEngine(Session(), translator, cap.publish, deferred=True, english=english,
                          translation_audit=cap.translation_audit)
        finish_utterance(e, 0)
        translator.fail = True
        e.service(0)
        translator.fail = not recover
        e.lock_all()
        displayed = []
        event_kinds = []
        while not cap.events.empty():
            event, _ = cap.events.get_nowait()
            event_kinds.append(event.get('type', event.get('status')))
            if persist_capture_event(lib, cap, event):
                displayed.append(event)
        assert all('english' in row for row in displayed)
        history = live_store.history(lib, lecture['id'])
        assert ('Utterance' in json.dumps(history)) == english
        failures = history['sessions'][0]['translation_failures']
        if recover:
            assert not failures and not cap.errors
            assert history['captions'][0]['status'] == 'locked'
            # A successful caption must reach persistence before the recovery audit.
            assert event_kinds.index('revised') < len(event_kinds) - 2
            assert event_kinds[-2:] == ['translation_audit', 'locked']
        else:
            assert len(failures) == 1 and failures[0]['state'] == 'deferred'
            assert history['captions'][0]['status'] == 'stable'
            assert len(cap.errors) == 1
            # Repeated notifications cannot grow the UI warning list without bound.
            persist_capture_event(lib, cap, {'type': 'translation_audit', 'event': failures[0]})
            assert len(cap.errors) == 1
