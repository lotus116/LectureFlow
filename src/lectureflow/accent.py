"""Local accent review, a bounded work ledger, and conservative acoustic contrast gates.

Scores are uncalibrated experimental evidence, never an accuracy estimate. The
single-owner scheduler retains original captions and records rejected candidates.
No PCM is persisted by this module.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class SourceStamp:
    key: str
    generation: int
    source_version: int
    text: str


@dataclass(frozen=True)
class ReviewJob:
    source: SourceStamp
    queued_at: float
    context_version: int


class ReviewQueue:
    """FIFO with explicit backpressure, preserved age, and stale-result fencing.

    Capacity exhaustion is a scheduling obligation, never permission to evict an
    old job. Callers must service or durably defer it before accepting more work.
    """

    def __init__(self, capacity: int = 3):
        if capacity < 1:
            raise ValueError('review capacity must be positive')
        self.capacity = capacity
        self.jobs: OrderedDict[str, ReviewJob] = OrderedDict()
        self.completed = 0

    def enqueue(self, source: SourceStamp, context_version: int, now: float) -> ReviewJob:
        previous = self.jobs.get(source.key)
        if previous is None and len(self.jobs) >= self.capacity:
            raise BufferError('accent review requires service or durable deferral')
        # New session identity is not a coalesced update of the old obligation.
        if previous and previous.source.generation != source.generation:
            raise ValueError('resolve previous generation before replacement')
        job = ReviewJob(source, previous.queued_at if previous else now, context_version)
        self.jobs[source.key] = job
        return job

    def oldest(self) -> ReviewJob | None:
        return next(iter(self.jobs.values()), None)

    def age(self, now: float) -> float:
        job = self.oldest()
        return max(0.0, now - job.queued_at) if job else 0.0

    def acknowledge(self, job: ReviewJob) -> bool:
        # Identity matters even when a new context happens to have equal values.
        if self.jobs.get(job.source.key) is not job:
            return False
        del self.jobs[job.source.key]
        self.completed += 1
        return True

    def commit(self, job: ReviewJob, current: SourceStamp, *, edited: bool,
               locked: bool, apply: Callable[[], None]) -> bool:
        if (edited or locked or current != job.source
                or self.jobs.get(job.source.key) is not job):
            return False
        # Failed persistence must retain the obligation for retry.
        apply()
        return self.acknowledge(job)


# These are conservative experimental gates, not calibrated accuracy scores.
def words(text):
    import re
    return re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?|[^\w\s]", text)


def candidate_from_context(original: str, following: str, decoded: str):
    """Only aligned substitutions wholly inside the target may change its words."""
    import difflib
    import re
    lexical_pattern = r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:\.\d+)?"
    left, right = re.findall(lexical_pattern, original), re.findall(lexical_pattern, following)
    proposed = re.findall(lexical_pattern, decoded)
    source = left + right
    edits = []
    for tag, a, b, c, d in difflib.SequenceMatcher(
            None, [x.lower() for x in source], [x.lower() for x in proposed],
            autojunk=False).get_opcodes():
        if a >= len(left) or tag == 'equal':
            continue
        if b > len(left) or tag != 'replace' or b-a != d-c:
            return None, 'alignment_or_insertion'
        edits.extend((i, proposed[c+i-a]) for i in range(a, b))
    if not edits:
        return None, 'unchanged'
    protected = set(('no not never none neither nor without cannot can could would should '
                     'may might must less more least most above below before after than '
                     'one two three four five six seven eight nine ten hundred thousand '
                     'million billion zero first second third percent percentage meter '
                     'meters metre metres kilogram kilograms gram grams volt volts watt '
                     'watts hz khz mhz gb mb kb seconds minutes hours degrees').split())
    for i, new in edits:
        old = left[i]
        if (old.lower() in protected or new.lower() in protected or "'" in old+new
                or not old.isalpha() or not new.isalpha()):
            return None, 'protected_change'
    lexical = sum(x.isalpha() for x in left)
    if len(edits) > 2 or len(edits) / max(1, lexical) > .15:
        return None, 'large_change'
    # Preserve source punctuation/spacing exactly; change only the matched token spans.
    spans = list(re.finditer(lexical_pattern, original))
    result = original
    for i, new in reversed(edits):
        span = spans[i]
        result = result[:span.start()] + new + result[span.end():]
    return result, 'candidate'


def audio_scores(session, pcm, texts):
    """Teacher-forced mean log probability; audio/silence contrast is NOT accuracy.

    Uses the pinned local Qwen runtime. No model state or streaming KV cache changes.
    Full logits are bounded by the <=12.25s target and <=256 text tokens.
    """
    import mlx.core as mx
    from mlx_qwen3_asr.audio import compute_features

    mel, lengths = compute_features(pcm)
    features, _ = session.model.audio_tower(mel.astype(session.dtype), lengths)
    prefix = session.tokenizer.build_prompt_tokens(
        n_audio_tokens=features.shape[1], language='English', context='')
    scores = []
    for text in texts:
        target = session.tokenizer.encode(text)
        if not target or len(target) > 256:
            raise ValueError('review text exceeds scoring bound')
        ids = mx.array([prefix + target])
        embeddings = session.model._embed_tokens(ids, validate_input_ids=True)
        embeddings = session.model._inject_audio_features(
            embeddings, features, ids == session.model.audio_token_id)
        pos = mx.arange(ids.shape[1])[None, :]
        hidden = session.model.model(inputs_embeds=embeddings,
                                     position_ids=mx.stack([pos, pos, pos], axis=1))
        logits = session.model.lm_head(hidden[:, len(prefix)-1:-1, :]).astype(mx.float32)
        logp = logits - mx.logsumexp(logits, axis=-1, keepdims=True)
        selected = mx.take_along_axis(logp, mx.array([target])[:, :, None], axis=-1)
        scores.append(float(mx.mean(selected).item()))
    return scores


class AccentReviewer:
    def __init__(self, session):
        self.session = session

    def review(self, original, following, target_pcm, following_pcm):
        import time

        import numpy as np
        started = time.monotonic()
        audit = {'original': original, 'candidate': '', 'accepted': False}
        try:
            if len(target_pcm) > 16000*13 or len(following_pcm) > 16000*13:
                audit["reason"] = "audio_bound_exceeded"
                return audit
            combined = np.concatenate([target_pcm, following_pcm])
            result = self.session.transcribe(combined, language='English', context='')
            if getattr(result, 'truncated', False):
                audit['reason'] = 'truncated'
                return audit
            audit["context_candidate"] = result.text.strip()
            candidate, reason = candidate_from_context(original, following, result.text.strip())
            audit.update(candidate=candidate or '', reason=reason)
            if candidate is None:
                return audit
            # Independent fresh decoding of target must support the entire candidate;
            # agreement alone never suffices: the audio-ablation gate below is required.
            direct = self.session.transcribe(target_pcm, language='English', context='')
            audit["target_decoded"] = direct.text.strip()
            if (getattr(direct, 'truncated', False)
                    or [x.lower() for x in words(direct.text) if x.isalpha()]
                    != [x.lower() for x in words(candidate) if x.isalpha()]):
                audit['reason'] = 'target_disagrees'
                return audit
            real = audio_scores(self.session, target_pcm, [original, candidate])
            silent = audio_scores(self.session, np.zeros_like(target_pcm), [original, candidate])
            margin = real[1]-real[0]
            contrast = margin-(silent[1]-silent[0])
            audit.update(audio_margin=margin, audio_contrast=contrast)
            audit['accepted'] = margin >= .08 and contrast >= .08
            audit['reason'] = (
                'audio_supported' if audit['accepted'] else 'insufficient_audio_evidence')
            return audit
        except Exception:
            # Optional enhancement failure must not kill required caption work.
            audit['reason'] = 'review_failed'
            return audit
        finally:
            audit['seconds'] = time.monotonic()-started


def recover_saved_reviews(library, lecture_id, session_id, session):
    """Retry journaled work from sealed original recordings, without reopening captions.

    An interrupted process has lost its live revision horizon. Recovered candidates
    therefore remain audit-only; a stale generation cannot modify the displayed text.
    No raw audio is newly persisted by this path (decoded PCM is read through a pipe).
    """
    import json
    import subprocess
    import uuid

    import imageio_ffmpeg
    import numpy as np

    from lectureflow import filelock as fcntl

    from .live_store import accent_audits, save_accent_audit, validate_session
    from .recordings import chunks_for, verify_source

    validate_session(library, session_id, lecture_id)
    pending = [a for a in accent_audits(library, session_id)
               if a.get('state') in {'pending', 'failed'}]
    if not pending:
        return {'recovered': 0, 'unavailable': 0}
    recordings = []
    for row in library.db.execute('SELECT * FROM segments WHERE lecture_id=?', (lecture_id,)):
        source = library.managed_path(library.lecture_path(lecture_id)/'Audio'/row['filename'])
        if source.name != 'segment.json':
            continue
        meta = json.loads(source.read_text(encoding="utf-8"))
        try:
            matches = uuid.UUID(meta.get('capture_id', '')).hex == uuid.UUID(session_id).hex
        except ValueError:
            matches = False
        if matches:
            verify_source(source, row['sha256'])
            recordings.append((source, meta))
    recovered = unavailable = 0
    for event in pending:
        with (library.root / '.live-priority.lock').open('a+') as priority:
            try:
                fcntl.flock(priority, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'recovered': recovered, 'unavailable': unavailable, 'yielded': True}
            fcntl.flock(priority, fcntl.LOCK_UN)
        parts = []
        covered = 0.0
        start, end = event['start'], event['end']
        if not (0 <= start < end and end-start <= 13):
            unavailable += 1
            continue
        for source, meta in recordings:
            cursor = float(meta.get('start_offset_s', 0))
            for chunk in chunks_for(source):
                a, b = max(start, cursor), min(end, cursor+chunk['duration'])
                if b > a:
                    raw = subprocess.check_output([
                        imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error',
                        '-ss', str(a-cursor), '-i',
                        str(source.parent/chunk['file']), '-t', str(b-a),
                        '-ar', '16000', '-ac', '1', '-f', 'f32le', 'pipe:1'],
                        stderr=subprocess.DEVNULL, timeout=30)
                    parts.append((a, np.frombuffer(raw, dtype='<f4')))
                    covered += b-a
                cursor += chunk['duration']
        if not event.get('original') or abs(covered-(end-start)) > .05 or not parts:
            # Keep the unresolved journal entry. No audio is fabricated or saved.
            unavailable += 1
            continue
        pcm = np.concatenate([p for _, p in sorted(parts, key=lambda x:x[0])])
        result = AccentReviewer(session).review(event['original'], '', pcm,
                                                np.zeros(0, dtype=np.float32))
        failed = result['reason'] == 'review_failed'
        result.update(id=event['id'], start=start, end=end,
                      source_version=event.get('source_version'), accepted=False,
                      state='failed' if failed else 'recovered_candidate',
                      recovery_policy='audit_only_expired_generation')
        save_accent_audit(library, session_id, result, english=True)
        recovered += not failed
    return {'recovered': recovered, 'unavailable': unavailable}
