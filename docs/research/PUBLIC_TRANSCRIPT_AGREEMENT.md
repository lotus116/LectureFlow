# Using recognizer agreement to prioritize transcript review

Can two recognizers agreeing on every word identify speech that needs little manual review? We applied a fixed rule to a separate publisher's Whisper small.en and Canary 1B Flash outputs. We selected all paired examples from three natural-speech conditions and one matched noise condition before scoring. A clip qualified when both recognizers produced the same nonempty sequence after lowercasing and tokenization. We compared the proposed Whisper text with the source reference, retaining the same normalization throughout.

| Published condition | All errors / reference words | Clips retained | Errors / reference words retained | Retained clips with errors |
|---|---:|---:|---:|---:|
| LibriSpeech clean reading | 8/201 | 9/16 | 1/102 | 1/9 |
| LibriSpeech harder reading | 17/248 | 8/16 | 1/107 | 1/8 |
| EdAcc natural conversation | 72/281 | 6/16 | 15/88 | 5/6 |
| Clean reading with 10 dB babble | 14/201 | 9/16 | 4/126 | 3/9 |

On conversation, restricting the set to agreeing outputs reduced observed word error rate from 25.62% to 17.05%. In a second scoring pass we removed sound-event markup and the filled pauses *uh* and *um* from both sides, holding the original selection fixed. Ten errors remained across 83 reference words, and five of the six selected clips contained an error. The corresponding selected reading groups each had one word error. The babble group reuses the clean utterances and should be reported separately. Differences in speaking style, recording conditions and reference conventions limit comparisons across corpora.

For prospective adaptation, use agreement to prioritize review, audit some agreeing cases, and evaluate the trained recognizer on independently corrected speech. No weights were trained here. The predictions were supplied by the benchmark publisher. The computations performed for this study were pairing, filtering and rescoring. The three natural conditions each contain only 16 clips.

## Reproduction

Use the [source repository at the pinned commit](https://github.com/dakshjain-1616/Asr-Evaluation/tree/6aba1e38927578a68b968daca4de426a0534a419). From the LectureFlow checkout run the command below, replacing the last argument with the public benchmark checkout. The script verifies every prediction file against its pinned Git object, pairs the models by clip ID and checks each reference against the source manifest. The published output contains only condition totals and source file hashes.

```bash
python scripts/research/classroom_pilot/external_agreement.py /path/to/Asr-Evaluation
```

[Aggregate scores](results/2026-11-26-external-agreement.json) · [Scoring script](../../scripts/research/classroom_pilot/external_agreement.py)
