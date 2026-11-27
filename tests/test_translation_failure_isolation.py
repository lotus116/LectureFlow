"""Failure isolation must preserve progress, output privacy and unresolved work."""
import json

import pytest
from test_live_context_revision import finish_utterance, make_engine

from lectureflow import live_store
from lectureflow.library import Library


@pytest.mark.parametrize('deferred', [True, False])
def test_persistent_failures_bound_memory_and_stop_without_false_success(monkeypatch, deferred):
    e, t, events, clock = make_engine(monkeypatch, deferred=deferred)
    failures = []
    e.translation_audit = failures.append
    t.fail = True
    for i in range(100):
        clock[0] = i
        finish_utterance(e, i)
        e.service(0)
        assert len(e.recent) <= 3 and len(e.pending_translations) <= 3
    e.lock_all()
    assert not e.pending_translations and not e.recent
    assert e.translations_completed == 0 and e.translations_deferred == (100 if deferred else 297)
    assert len({x['id'] for x in events}) == 100
    assert not any(x['status'] == 'locked' for x in events)
    assert len({x['id'] for x in failures if x['state'] == 'deferred'}) == 100


def test_transient_failure_retries_once_with_full_following_context(monkeypatch):
    e, t, events, clock = make_engine(monkeypatch)
    for i in range(3):
        finish_utterance(e, i)
    key = e.recent[0]['id']
    t.fail = True
    e.complete_translation(key)
    t.fail = False
    clock[0] = 6
    e.complete_translation(key)
    e.lock_all()
    assert e.translations_deferred == 0
    assert all(x['chinese'] for x in events if x['status'] == 'locked')
    assert ('Utterance 1', '', 'Utterance 2\nUtterance 3') in t.calls


def test_failed_old_job_does_not_block_other_first_translations(monkeypatch):
    e, t, _, clock = make_engine(monkeypatch)
    finish_utterance(e, 0)
    t.fail = True
    e.service(0)
    finish_utterance(e, 1)
    e.complete_translation(e.recent[0]['id'])  # New following context also failed.
    # First job is cooling down; the second first translation can progress.
    t.fail = False
    e.service(0)
    assert e.recent[1]['chinese']
    assert not e.recent[0]['chinese']
    clock[0] = 6
    e.lock_all()
    assert e.translations_deferred == 0


@pytest.mark.parametrize('english', [False, True])
def test_failed_work_survives_reopen_without_leaking_hidden_english(tmp_path, monkeypatch, english):
    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course('Synthetic')['id'], 'Synthetic')
        session = live_store.create(lib, lecture['id'])
        e, t, _, _ = make_engine(monkeypatch, publish=lambda event, en, zh:
            live_store.save(lib, session['id'], event, english=en, translate=zh))
        e.english = english
        e.translation_audit = lambda event: live_store.save_translation_failure(
            lib, session['id'], event, english=e.english)
        t.fail = True
        for i in range(4):
            finish_utterance(e, i)
        e.configure(False, False)
        assert e.translations_deferred == 4
    with Library(tmp_path) as lib:
        data = live_store.history(lib, lecture['id'])
        failed = data['sessions'][0]['translation_failures']
        assert len(failed) == 4 and all(x['state'] == 'deferred' for x in failed)
        assert all(x['status'] == 'stable' for x in data['captions'])
        assert ('Utterance' in json.dumps(data)) == english
        live_store.export(lib, lecture['id'])


def test_recovered_work_removes_persisted_failure(tmp_path, monkeypatch):
    with Library(tmp_path) as lib:
        lecture = lib.create_lecture(lib.create_course('Synthetic')['id'], 'Synthetic')
        session = live_store.create(lib, lecture['id'])
        e, t, _, _ = make_engine(monkeypatch)
        e.translation_audit = lambda event: live_store.save_translation_failure(
            lib, session['id'], event, english=True)
        finish_utterance(e, 0)
        t.fail = True
        e.service(0)
        assert len(live_store.translation_failures(lib, session['id'])) == 1
        t.fail = False
        e.lock_all()
        assert not live_store.translation_failures(lib, session['id'])


def test_persistence_failure_is_not_swallowed_as_model_failure(monkeypatch):
    e, t, _, _ = make_engine(monkeypatch)
    def unavailable(_):
        raise OSError('synthetic disk failure')
    e.translation_audit = unavailable
    t.fail = True
    finish_utterance(e, 0)
    with pytest.raises(OSError, match='disk failure'):
        e.service(0)
    assert e.pending_translations


def test_refresh_interval_is_configurable_without_delaying_first_chinese(monkeypatch):
    e, t, _, clock = make_engine(monkeypatch)
    e.temporary_refresh_seconds = 3
    import numpy as np
    e.feed(np.full(4000, .1, dtype=np.float32), 0)
    clock[0] = 1
    e.service(0)
    assert len(t.calls) == 1
    e.current['english'] += ' more'
    clock[0] = 2
    e.service(0)
    assert len(t.calls) == 1
    e.temporary_refresh_seconds = 1
    e.service(0)
    assert len(t.calls) == 2
    e.finish()
    e.lock_all()
    assert not e.pending_translations


def test_default_refresh_and_explicit_experimental_cli_option(monkeypatch):
    from lectureflow.cli import parser
    e, _, _, _ = make_engine(monkeypatch)
    assert e.temporary_refresh_seconds == 1
    args = ["live", "run", "lecture", "session"]
    assert parser().parse_args(args).temporary_refresh_seconds == 1
    chosen = parser().parse_args(args + ["--temporary-refresh-seconds", "3"])
    assert chosen.temporary_refresh_seconds == 3


def test_recovery_is_acknowledged_only_after_caption_persistence(monkeypatch):
    e, t, _, _ = make_engine(monkeypatch)
    audit = []
    e.translation_audit = audit.append
    finish_utterance(e, 0)
    key = e.recent[0]['id']
    t.fail = True
    e.complete_translation(key)
    t.fail = False
    publish = e.publish
    def broken(*_):
        raise OSError('synthetic save failure')
    e.publish = broken
    with pytest.raises(OSError, match='save failure'):
        e.complete_translation(key)
    assert key in e.pending_translations
    assert not any(x['state'] == 'resolved' for x in audit)
    e.publish = publish
    e.complete_translation(key)
    assert audit[-1]['state'] == 'resolved'
    assert key not in e.pending_translations


@pytest.mark.parametrize('interval', [1, 3])
def test_cli_passes_selected_refresh_to_live_backend(monkeypatch, interval):
    from lectureflow import cli, live
    calls = []
    monkeypatch.setattr(cli, 'configure', lambda _: None)
    monkeypatch.setattr(live, 'run_live', lambda *a, **k: calls.append(k))
    monkeypatch.setattr(cli.sys, 'argv', [
        'lectureflow', 'live', 'run', 'lecture', 'session',
        '--temporary-refresh-seconds', str(interval),
    ])
    assert cli.main() == 0
    assert calls == [{'temporary_refresh_seconds': interval, 'isolated_translation': False}]
