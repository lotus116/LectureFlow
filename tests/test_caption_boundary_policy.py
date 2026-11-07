import pytest

from lectureflow.caption_boundary import BoundaryPolicy


@pytest.mark.parametrize('text', ['Because.', 'A complete phrase.', 'No punctuation'])
def test_short_caption_does_not_end_at_brief_pause(text):
    policy = BoundaryPolicy()
    assert not policy.should_finish(3, .75, text)
    assert not policy.should_finish(3, 1.75, text)
    assert policy.should_finish(3, 2, text)


@pytest.mark.parametrize('text', ['We can think about.', 'Because', 'The value is', 'and'])
def test_dangling_words_keep_continuation_window_even_with_period(text):
    policy = BoundaryPolicy()
    assert not policy.should_finish(10, .75, text)
    assert policy.should_finish(10, 1.5, text)


def test_longer_caption_uses_pause_without_requiring_guessed_punctuation():
    policy = BoundaryPolicy()
    assert policy.should_finish(10, 1.25, 'The document has two terms')
    assert not policy.should_finish(10, .5, 'The document has two terms.')


def test_continuous_speech_and_incomplete_sentences_still_have_finite_limit():
    policy = BoundaryPolicy()
    assert not policy.should_finish(19.75, 0, 'because')
    assert policy.should_finish(20, 0, 'because')


def test_endpoint_energy_learns_steady_background_without_unbounded_history():
    from lectureflow.caption_boundary import EndpointEnergy
    detector = EndpointEnergy(BoundaryPolicy())
    # Original fixed .0021 endpoint threshold treats .004 room noise as speech.
    for _ in range(1000):
        quiet = detector.quiet(.004, .0021)
    assert quiet and len(detector.recent) == 24
    assert not detector.quiet(.025, .0021)


def test_endpoint_energy_is_capped_and_does_not_suppress_loud_constant_speech():
    from lectureflow.caption_boundary import EndpointEnergy
    detector = EndpointEnergy(BoundaryPolicy())
    for _ in range(100):
        assert not detector.quiet(.05, .0021)


def test_energy_observation_does_not_drop_or_change_audio():
    from types import SimpleNamespace

    import numpy as np

    from lectureflow.live import CaptionEngine

    packets = []

    class Speech:
        def init_streaming(self, **_):
            return SimpleNamespace(text='The document contains terms.')

        def feed_audio(self, pcm, state):
            packets.append(pcm.copy())
            return state

        def finish_streaming(self, state):
            return state

    engine = CaptionEngine(Speech(), None, lambda *_: None, translate=False)
    audio = [np.full(4000, .004 if i % 5 else .02, dtype=np.float32) for i in range(80)]
    for i, pcm in enumerate(audio):
        engine.feed(pcm, i*.25)
    engine.finish()
    assert len(packets) == len(audio)
    assert all(np.array_equal(a, b) for a, b in zip(audio, packets, strict=True))
