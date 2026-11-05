"""Production scheduler contracts, using synthetic audio and controlled evidence."""
import json

import numpy as np
import pytest
from test_live_context_revision import Session, Translator

from lectureflow import live, live_store
from lectureflow.accent import candidate_from_context


def engine(monkeypatch, *, accept=False, accent=True):
    events, audits, calls = [], [], []
    def review(_, original, following, pcm, following_pcm):
        calls.append((original, following, len(pcm), len(following_pcm)))
        return {'original': original, 'candidate': original+' corrected',
                'accepted': accept, 'reason': 'audio_supported' if accept else 'unchanged'}
    monkeypatch.setattr(live.AccentReviewer, 'review', review)
    e = live.CaptionEngine(Session(), Translator(), lambda event,*_:events.append(event),
                           deferred=True, accent=accent, audit=audits.append)
    return e, events, audits, calls


def utterance(e, i):
    e.feed(np.full(4000, .1, dtype=np.float32), i*.25)
    e.finish()


def test_off_runs_no_review_and_preserves_context_jobs(monkeypatch):
    e, _, audits, calls = engine(monkeypatch, accent=False)
    for i in range(8):
        utterance(e, i)
        e.service(99)
    e.lock_all()
    assert not audits and not calls and not e.accent_audio
    assert e.context_translations_completed > 0


def test_overload_then_drain_no_discard_and_memory_bounded(monkeypatch):
    e, _, audits, calls = engine(monkeypatch)
    for i in range(50):
        utterance(e, i)
        e.service(99)
        assert len(e.accent_queue.jobs) <= 3 and len(e.accent_audio) <= 3
    e.lock_all()
    assert len(calls) == 50
    assert e.accent_queue.completed == 50 and not e.accent_queue.jobs
    assert not e.pending_translations and not e.accent_audio
    assert sum(a['state']=='reviewed' for a in audits) == 50
    assert e.context_translations_completed > 0


def test_revision_retranslates_source_and_previous_context(monkeypatch):
    e, events, _, _ = engine(monkeypatch, accept=True)
    utterance(e, 0)
    e.service(0)
    utterance(e, 1)
    e.lock_all()
    assert not e.pending_translations
    changed = [v for v in events if v['english'].endswith(' corrected')]
    assert changed and changed[0]['chinese'] == ''
    assert all(v['english'] in v['chinese'] for v in changed if v['chinese'])
    assert any('corrected' in following for _,_,following in e.translator.calls)
    assert events[-1]['status'] == 'locked'


def test_switch_drains_old_work_and_stops_new_reviews(monkeypatch):
    e, _, _, calls = engine(monkeypatch)
    utterance(e, 0)
    e.configure_accent(False)
    assert len(calls)==1 and not e.accent_queue.jobs
    utterance(e, 1)
    e.lock_all()
    assert len(calls)==1


def test_stale_result_cannot_change_source(monkeypatch):
    e, _, audits, _ = engine(monkeypatch)
    utterance(e, 0)
    def stale(*args):
        e.recent[0]['english']='Human edit'
        e.recent[0]['edited']=True
        return {'accepted':True,'candidate':'bad','reason':'audio_supported'}
    monkeypatch.setattr(live.AccentReviewer, 'review', stale)
    e.lock_all()
    assert audits[-1]['reason']=='stale_source' and not audits[-1]['accepted']


@pytest.mark.parametrize('old,new', [
    ('not','never'), ('one','two'), ('meters','metres'), ('10','11'), ('can','will'),
])
def test_sensitive_changes_abstain(old,new):
    text=f'This is a sufficiently long synthetic sentence with {old} tokens for checking.'
    candidate,reason=candidate_from_context(text,'',text.replace(old,new))
    assert candidate is None and reason=='protected_change'


def test_alignment_never_imports_following_words():
    text='We discuss a synthetic long sentence about paper and books today.'
    decoded = text+' entirely invented Next topic begins.'
    assert candidate_from_context(text, 'Next topic begins.', decoded)[0] is None


def test_audit_obeys_english_save_boundary(tmp_path):
    from lectureflow.library import Library
    with Library(tmp_path/'lib') as lib:
        c=lib.create_course('Synthetic')
        lecture=lib.create_lecture(c['id'],'Synthetic')
        session=live_store.create(lib,lecture['id'])
        live_store.save_accent_audit(lib,session['id'],
            {'id':'a','original':'private','candidate':'private revision','state':'pending',
             'context_candidate':'private context','target_decoded':'private target'},
            english=False)
        data=json.loads(lib.db.execute('SELECT event FROM live_accent_audit').fetchone()[0])
        assert all(k not in data for k in
                   ('original', 'candidate', 'context_candidate', 'target_decoded'))


def test_real_reviewer_requires_audio_contrast(monkeypatch):
    from types import SimpleNamespace

    import lectureflow.accent as accent
    old = 'We discuss this long synthetic sentence about the paper on this table.'
    new = old.replace('paper', 'book')
    class Model:
        def transcribe(self, *args, **kwargs):
            assert kwargs['language'] == 'English' and kwargs['context'] == ''
            return SimpleNamespace(text=new, truncated=False)
    pcm=np.ones(16000, dtype=np.float32)
    monkeypatch.setattr(accent, 'audio_scores',
                        lambda session, audio, texts: [-1.0, -.5] if audio.any() else [-1., -1.])
    result=accent.AccentReviewer(Model()).review(old, '', pcm, pcm[:0])
    assert result['accepted'] and result['audio_contrast'] == .5
    monkeypatch.setattr(accent, 'audio_scores', lambda *args: [-1., -.5])
    assert not accent.AccentReviewer(Model()).review(old, '', pcm, pcm[:0])['accepted']


def test_saved_recording_recovery_is_audit_only(monkeypatch, tmp_path):
    import uuid
    import wave

    from lectureflow.accent import recover_saved_reviews
    from lectureflow.library import Library
    from lectureflow.recordings import register
    with Library(tmp_path/'lib') as lib:
        c=lib.create_course('Synthetic')
        lec=lib.create_lecture(c['id'], 'Synthetic')
        session=live_store.create(lib, lec['id'])
        directory=lib.lecture_path(lec['id'])/'Audio'/('live-'+uuid.uuid4().hex)
        directory.mkdir()
        (directory/'capture.json').write_text(json.dumps({
            'id':uuid.uuid4().hex, 'capture_id':session['id'], 'sealed':True,
            'start_offset_s':0}))
        with wave.open(str(directory/'chunk-000.caf'), 'wb') as w:
            w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            w.writeframes(np.full(4000, 1000, dtype='<i2').tobytes())
        register(lib, lec['id'], directory)
        live_store.save_accent_audit(lib, session['id'], {
            'id':'caption','start':0,'end':.25,'original':'Synthetic source.',
            'state':'pending','source_version':1}, english=True)
        before=set(directory.iterdir())
        monkeypatch.setattr(live.AccentReviewer, 'review', lambda *args: {
            'original':'Synthetic source.', 'candidate':'Candidate text.',
            'accepted':True,'reason':'audio_supported'})
        result=recover_saved_reviews(lib,lec['id'],session['id'],None)
        assert result == {'recovered':1,'unavailable':0}
        audit=live_store.accent_audits(lib,session['id'])[0]
        assert audit['state']=='recovered_candidate' and not audit['accepted']
        assert set(directory.iterdir()) == before
        assert not live_store.history(lib,lec['id'])['captions']
        assert recover_saved_reviews(lib,lec['id'],session['id'],None)['recovered'] == 0


def test_candidate_alignment_ignores_model_punctuation_but_preserves_source():
    text='This long synthetic sentence mentions paper, beside several books on the table.'
    candidate,reason=candidate_from_context(
        text, 'Next topic starts here.',
        text.replace('paper,', 'pencil;')+' Next topic starts here!')
    assert reason=='candidate'
    assert candidate==text.replace('paper', 'pencil')


def test_off_keeps_provisional_chinese_until_next_translation(monkeypatch):
    e, events, _, _ = engine(monkeypatch, accent=False)
    e.feed(np.full(4000, .1, dtype=np.float32), 0)
    e.current['chinese']='Existing provisional translation'
    e.state.text='New partial source'
    e.feed(np.full(4000, .1, dtype=np.float32), .25)
    assert events[-1]['chinese']=='Existing provisional translation'


def test_old_translation_result_does_not_cover_new_english(monkeypatch):
    e, _, _, _ = engine(monkeypatch, accent=False)
    utterance(e, 0)
    key=e.recent[0]['id']
    age=e.pending_translations[key].queued_at
    original_translate=e.translator.translate
    def change_source(*args, **kwargs):
        entry=e.recent[0]
        entry['english']='A newer synthetic source.'
        e.emit(entry,'revised')
        return {'text':'STALE TRANSLATION'}
    e.translator.translate=change_source
    e.complete_translation(key)
    assert e.recent[0]['chinese'] != 'STALE TRANSLATION'
    assert e.pending_translations[key].queued_at == age
    e.translator.translate=original_translate
    e.lock_all()
    assert not e.pending_translations
