"""Local-only boundary replay. Input is private 16 kHz mono float32 .npy audio.

Never interpret generated transcripts or segment counts as reference accuracy.
Results include private transcripts; choose an ignored output directory.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
import types
from dataclasses import asdict
from pathlib import Path

import numpy as np

from lectureflow.adapters import HyMTTranslator, QwenASR
from lectureflow.caption_boundary import BoundaryPolicy
from lectureflow.live import CaptionEngine
from lectureflow.models import resolve
from lectureflow.runtime import configure


def historical_engine(ref):
    name = 'lectureflow._boundary_' + ref.replace('-', '_')
    module = types.ModuleType(name)
    module.__package__ = 'lectureflow'
    sys.modules[name] = module
    source = subprocess.check_output(['git', 'show', f'{ref}:src/lectureflow/live.py'], text=True)
    exec(compile(source, name, 'exec'), module.__dict__)
    return module.CaptionEngine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--audio', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--clips', default='0:90,380:90')
    parser.add_argument('--bilingual', action='store_true')
    parser.add_argument('--realtime', action='store_true')
    parser.add_argument('--profiles', default='legacy,current,balanced6,balanced8')
    args = parser.parse_args()
    configure(args.output / 'cache')
    args.output.mkdir(parents=True, exist_ok=True)
    pcm = np.load(args.audio, mmap_mode='r')
    session = QwenASR(Path(resolve('asr-large', args.models, 'asr')['path'])).session
    translator = (HyMTTranslator(Path(resolve('translate', args.models, 'translation')['path']))
                  if args.bilingual else None)
    if translator:
        translator.translate('This is a document.')
    # Warm-up excluded from per-clip compute time.
    state = session.init_streaming(language='English', chunk_size_sec=1, max_context_sec=12)
    session.feed_audio(np.zeros(16000, dtype=np.float32), state)
    profiles = {
        'adaptive120': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            noise_factor=1.2, pause_seconds=.75, asr_window_seconds=12)}),
        'adaptive135': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            noise_factor=1.35, pause_seconds=.75, asr_window_seconds=12)}),
        'adaptive125': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            noise_factor=1.2, pause_seconds=1.25, asr_window_seconds=12)}),
        'adaptive150': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            noise_factor=1.2, pause_seconds=1.5, asr_window_seconds=12)}),
        'window125': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            noise_factor=1.2, pause_seconds=1.25, asr_window_seconds=20)}),
        'window150': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            noise_factor=1.2, pause_seconds=1.5, asr_window_seconds=20)}),
        'legacy': (historical_engine('ddb5cb3'), {}),
        'current': (historical_engine('9ef8a1b'), {}),
        'balanced6': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            minimum_seconds=6, noise_factor=None, pause_seconds=.75, asr_window_seconds=12)}),
        'balanced8': (CaptionEngine, {'boundary_policy': BoundaryPolicy(
            minimum_seconds=8, noise_factor=None, pause_seconds=.75, asr_window_seconds=12)}),
    }
    summaries = []
    for clip in args.clips.split(','):
        offset, duration = map(float, clip.split(':'))
        audio = pcm[round(offset*16000):round((offset+duration)*16000)]
        for profile in args.profiles.split(','):
            cls, options = profiles[profile]
            events = []
            started = time.monotonic()

            def publish(row, *_, events=events, started=started):
                events.append({**row, 'wall_s': time.monotonic() - started})

            engine = cls(session, translator, publish, translate=args.bilingual,
                         deferred=args.bilingual, **options)
            max_backlog = 0
            for i in range(0, len(audio), 4000):
                due = (i + len(audio[i:i+4000])) / 16000
                if args.realtime:
                    while time.monotonic() - started < due:
                        engine.service(0)
                        time.sleep(min(.05, max(0, due - (time.monotonic() - started))))
                engine.feed(audio[i:i+4000], i/16000)
                backlog = max(0, int((time.monotonic()-started-due)*4)) if args.realtime else 0
                max_backlog = max(max_backlog, backlog)
                engine.service(backlog)
            engine.finish()
            engine.lock_all()
            elapsed = time.monotonic()-started
            rows = [r for r in events if r['status'] == 'stable']
            lengths = [r['end']-r['start'] for r in rows]
            limit = 12 if profile == 'legacy' else 24 if profile == 'current' else 20
            summary = dict(profile=profile, offset=offset, duration=duration,
                           segments=len(rows), median_s=statistics.median(lengths),
                           short=sum(x<3 for x in lengths),
                           hard=sum(x>=limit-.01 for x in lengths),
                           compute_s=round(elapsed, 2))
            summary['policy'] = (asdict(options['boundary_policy'])
                                 if 'boundary_policy' in options else profile)
            if args.bilingual:
                first_zh, first_en, last_revision, stable_at = {}, {}, {}, {}
                for row in events:
                    key = row['id']
                    if row['english']:
                        first_en.setdefault(key, row['wall_s'] - row['start'])
                    if row['chinese']:
                        first_zh.setdefault(key, row['wall_s'] - row['start'])
                    if row['status'] == 'stable':
                        stable_at[key] = row['wall_s']
                    if row['status'] == 'revised':
                        last_revision[key] = row['wall_s']
                revision_lags = [t-stable_at[k] for k, t in last_revision.items()]
                summary.update(
                    realtime=args.realtime, max_backlog=max_backlog,
                    first_english_median=statistics.median(first_en.values()),
                    first_chinese_median=statistics.median(first_zh.values()),
                    first_chinese_max=max(first_zh.values()),
                    revision_lag_median=statistics.median(revision_lags),
                    pending=len(engine.pending_translations),
                    context_revisions=engine.context_translations_completed,
                    temporary_events=sum(r['status']=='temporary' for r in events),
                )
            summaries.append(summary)
            (args.output/f'{int(offset)}-{profile}.json').write_text(
                json.dumps({'summary': summary, 'events': events}, ensure_ascii=False, indent=2))
            (args.output/'summary.json').write_text(json.dumps(summaries, indent=2))
            print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
