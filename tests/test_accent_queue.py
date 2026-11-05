"""Ledger contracts; these do not claim model quality or production integration."""
from dataclasses import replace

import pytest

from lectureflow.accent import ReviewQueue, SourceStamp


def stamp(key='a'):
    return SourceStamp(key, 1, 1, 'A synthetic example.')


def test_coalescing_keeps_fifo_age_and_rejects_inflight_result():
    queue = ReviewQueue()
    old = queue.enqueue(stamp(), 1, 10)
    queue.enqueue(stamp('b'), 1, 11)
    current = queue.enqueue(stamp(), 2, 20)
    assert queue.oldest() is current
    assert queue.age(25) == 15
    assert not queue.acknowledge(old)
    assert queue.acknowledge(current)
    assert queue.oldest().source.key == 'b'


def test_overload_keeps_all_old_work_and_recovers_in_order():
    queue = ReviewQueue(capacity=2)
    first = queue.enqueue(stamp(), 1, 0)
    second = queue.enqueue(stamp('b'), 1, 1)
    with pytest.raises(BufferError):
        queue.enqueue(stamp('c'), 1, 2)
    assert queue.oldest() is first
    assert queue.acknowledge(first)
    third = queue.enqueue(stamp('c'), 1, 3)
    assert queue.acknowledge(second)
    assert queue.acknowledge(third)
    assert queue.oldest() is None and queue.completed == 3


@pytest.mark.parametrize('change', [
    {'generation': 2}, {'source_version': 2}, {'text': 'Manual correction.'},
])
def test_stale_source_never_commits(change):
    queue = ReviewQueue()
    job = queue.enqueue(stamp(), 1, 0)
    assert not queue.commit(job, replace(stamp(), **change), edited=False, locked=False,
                            apply=lambda: pytest.fail('stale mutation'))
    assert queue.oldest() is job


@pytest.mark.parametrize('edited,locked', [(True, False), (False, True)])
def test_human_and_locked_content_are_protected(edited, locked):
    queue = ReviewQueue()
    job = queue.enqueue(stamp(), 1, 0)
    assert not queue.commit(job, stamp(), edited=edited, locked=locked,
                            apply=lambda: pytest.fail('protected mutation'))


def test_failed_save_retains_obligation():
    queue = ReviewQueue()
    job = queue.enqueue(stamp(), 1, 0)
    def fail():
        raise OSError('synthetic disk failure')
    with pytest.raises(OSError):
        queue.commit(job, stamp(), edited=False, locked=False, apply=fail)
    assert queue.oldest() is job
    applied = []
    assert queue.commit(job, stamp(), edited=False, locked=False,
                        apply=lambda: applied.append('saved'))
    assert applied == ['saved'] and queue.completed == 1


def test_new_generation_cannot_silently_replace_pending_work():
    queue = ReviewQueue()
    job = queue.enqueue(stamp(), 1, 0)
    with pytest.raises(ValueError):
        queue.enqueue(replace(stamp(), generation=2), 1, 1)
    assert queue.oldest() is job
