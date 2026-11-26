# Replaying a frozen re-listening rule on unseen SlideSpeech videos

## Design before decoding

Select four distinct dev videos, excluding the twelve source videos already used for the SlideSpeech experiments of 25 November. Order eligible sources by a fixed hash. Choose a 90–115 second continuous excerpt using official speech times only, preferring a start near the first fifth of each talk. Require at least 85% annotated coverage, at most three seconds between utterances, and no overlapping utterance boundaries. Preserve the gaps and the full audio between the first and last annotation. The four recordings are the units of comparison.

Use the previously checked Qwen3-ASR 0.6B INT8 CPU model, fixed English, two threads, without prompts. For each excerpt, form ten equal-frame windows, padding the end with fewer than ten zero samples if needed. In each half-window, decode a provisional prefix at its midpoint and then finalize the half from all audio received. The two finalized halves form the draft. Count words withdrawn from the displayed prefix at each update; pure appends contribute zero. Rank the equal-length windows by this count, breaking ties by earlier position, and retain four. Freeze the selected IDs before producing any full-window candidates. This is a controlled causal replay with two committed blocks per window. The target Mac uses a different backend, model size and live segmentation.

Score whole-excerpt transcripts against the official references. Keep the original draft, replace every window, replace just the four selected windows, or replace four windows in each of all 210 possible subsets. All subsets cost exactly the same audio duration within a recording. Report word errors by video and the summed expected errors under uniform random selection. Record preview, draft, and candidate decoding seconds separately. A useful transfer would lower error relative to both the draft and mean random selection on most videos. Mixed or unfavorable results limit the scope of the rule. No playback latency or classroom translation outcomes are measured in this experiment.

## Additional diagnostic set before scoring

For each video keep the best error count among the 210 possible masks. This reference-informed minimum estimates how many errors this particular candidate pool and window budget could remove with perfect selection. A gap between the deployed ranking and that minimum can distinguish a poor ranking from a poor candidate pool. We do not use the minimum to change the selected windows.

## Reproducing the experiment

Use the [SlideSpeech dev audio and related files from OpenSLR 144](https://www.openslr.org/144/). The publisher manually annotated the dev split. In a working folder, extract `dev/segments` and `dev/wav.scp` from `all_information.tar.gz` inside `related_files.tar.gz`. Add the publisher's `wavid2channel.tar.gz` to that folder. Keep `dev/text` out of the scoring folder until the selection mask and all candidate predictions have been written. The script excludes videos using the two existing public result tables in this repository.

Use the [pinned Qwen3-ASR 0.6B INT8 conversion](https://huggingface.co/thieunv/sherpa-onnx-qwen3-asr-0.6B-int8/tree/c12e7b0fc05e38e3949eacf3be13337c4df653b5) with sherpa-onnx 2.0.3, Python 3.12, NumPy, and RapidFuzz 3.14.3 for scoring. The recognizer uses fixed English, seed 42, no hotwords and at most 128 new tokens per call. The study retains the original logs and source manifests outside the repository; the shared results contain counts and public source identifiers. From the repository root run:

```bash
python scripts/research/classroom_pilot/slide_churn_transfer.py prepare WORKDIR /path/to/dev_audio.tar.gz
python scripts/research/classroom_pilot/slide_churn_transfer.py run WORKDIR /path/to/model
# Restore the official dev/text file to WORKDIR before scoring.
python scripts/research/classroom_pilot/slide_churn_transfer.py score WORKDIR
```

## Results

All 200 scored model calls completed on four previously unused dev videos, covering 385.28 seconds and 892 reference words. The project used the official, manually labeled development references only after the masks and candidates were saved.

| Source | Words | Draft | Highest retraction | Mean random | All windows | Best possible four windows |
|---|---:|---:|---:|---:|---:|---:|
| pet_0002 | 238 | 17 | 15 | 15.0 | 12 | 13 |
| pet_0004 | 230 | 29 | 26 | 27.0 | 24 | 21 |
| dance_0000 | 261 | 20 | 12 | 16.4 | 11 | 11 |
| health_0004 | 163 | 36 | 37 | 35.6 | 35 | 31 |
| Total | 892 | 102 | 90 | 94.0 | 82 | 76 |

The ranking beat the matched random mean on two recordings, tied on one and lost on one. It missed the predeclared majority-of-recordings criterion. Most of its four-error aggregate advantage came from the dance recording. On the health recording it made the draft worse even though at least one four-window selection could have saved five errors. We will report the strategy as a useful but variable signal.

Selected candidate decoding took 40.09 warm seconds versus 100.62 for full candidate decoding, recovering 12 of 20 net errors. The causal previews used 59.18 additional seconds and the committed draft halves used 97.65 seconds. When previews are already required to display streaming text, their cost is shared. If generated solely for selection, previews plus selected candidates take 99.28 seconds, nearly the full candidate cost. Costs are measurements summed across individual decode calls. These excerpts used Qwen3-ASR 0.6B on a CPU with time-based windows. The experiment addresses transfer of the signal across public sources and a different decoder configuration. [Per-recording counts, timings and checksums](results/2026-11-26-slide-churn-transfer.json)
