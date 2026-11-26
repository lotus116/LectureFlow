# Paired microphones and adaptation to one lecturer

## Same speech recorded near and far

We used the [AMI Meeting Corpus](https://groups.inf.ed.ac.uk/ami/corpus/) to test whether the microphone result transferred to a publicly reproducible setting. The public session metadata link the same participant to headset channel 0 across ES2008a, b, c and d. We paired this headset with microphone 01 of array 1 at identical sample positions. From the manual word timings we formed 4–12 second speech windows, excluded overlaps with other annotated speakers including a 100 ms margin, and selected up to eight eligible windows per meeting by a deterministic hash. The recognizer was Qwen3-ASR 0.6B INT8, fixed English and no lexical prompts. The reference and input duration were identical for both channels. Across 22 windows, the experiment comprised 142.48 seconds of unique speech and 44 new decodes.

| Meeting | Windows | Reference words | Headset word errors | Array word errors |
|---|---:|---:|---:|---:|
| ES2008a | 1 | 10 | 2 | 2 |
| ES2008b | 8 | 167 | 16 | 23 |
| ES2008c | 5 | 74 | 10 | 18 |
| ES2008d | 8 | 139 | 35 | 31 |
| Total | 22 | 390 | 63 (16.15%) | 74 (18.97%) |

The headset had a lower error count in seven windows, the distant microphone in three, and the channels tied in twelve. In session d the array had four fewer total errors. Use the corpus for repeatable channel comparisons and paired training experiments. Its single participant and annotated non-overlapping meeting turns represent a different setting from classroom monologues. These results cannot estimate the size of the phone/Zoom difference for a particular teacher.

Reproduction requires the [AMI v1.6.2 manual annotations](https://groups.inf.ed.ac.uk/ami/download/), saved as `annotations.zip`, and the publisher's `Headset-0.wav` and `Array1-01.wav` files (prefixed with the meeting ID) for all four meetings. Use the `prepare`, `run` and `score` commands in the [study script](../../scripts/research/classroom_pilot/ami_channel_pilot.py) with the same public Qwen model documented in the [selection experiment](PUBLIC_RELISTEN_TRANSFER.md). [Per-meeting scoring output](results/2026-11-26-ami-channel.json)

## Cross-lecture speaker adaptation design

We selected four lectures taught by Gilbert Strang in [MIT 18.06](https://ocw.mit.edu/courses/18-06-linear-algebra-spring-2010/video_galleries/video-lectures/). Lectures 2 and 3 supply training, lecture 4 supplies development evaluation, and lecture 5 is held out. Each lecture belongs to a single split. We use the published WebVTT captions as supervision and references. They can include editorial decisions, mathematical notation and omissions.

For each recording we estimated a constant offset between the downloadable MP4 and the separately hosted captions. Three reserved 18-second anchors at 100, 330 and 600 seconds calibrated the clock. A fourth at 720 seconds checked the offset; no anchor is included in the training or reported evaluation set. Calibration used the same recognizer and predeclared error thresholds for each lecture. Consecutive captions with small gaps form 6–15 second windows after 760 seconds. A fixed hash selects 24 windows from each training lecture, 12 from the development lecture and 18 from the test lecture. The training set covers 534.30 seconds (8.91 minutes); the development and test sets cover 143.99 and 188.96 seconds. The two training arms use exactly the same audio.

The student is Whisper tiny.en. One LoRA adapter trains on publisher captions and a second on hard labels produced from the training audio by Qwen3-ASR 0.6B. The baseline uses the original student. The Qwen offline model is a candidate labeler whose measured error is reported separately. Both LoRA arms adapt the query and value projections (rank 4, alpha 8), using learning rate 0.0001, two epochs, batch size two, the same example order and seed 26. We use a fixed training schedule and greedy evaluation. We report basic project normalization and Whisper English normalization. This test targets adaptation to this instructor and course jointly; it does not isolate accent from subject matter. [Data preparation](../../scripts/research/classroom_pilot/mit_speaker_data.py) · [Training and evaluation](../../scripts/research/classroom_pilot/speaker_lora.py).

## What changed on a later lecture?

Two genuine adapters were fit on a Linux CPU. Each updated 73,728 trainable parameters in 48 optimizer steps. Both completed and their saved weight hashes differ. We used Whisper tiny.en at revision `87c7102498dcde7456f24cfd30239ca606ed9063`. The held-out test contains 18 windows. The two text normalizers generate different reference word counts.

| Method | Lecture 4 development, basic errors / 294 words | Lecture 5 test, basic errors / 397 words | Lecture 5 test, English-normalized errors / 407 words |
|---|---:|---:|---:|
| Frozen Whisper tiny.en | 61 | 72 | 46 |
| LoRA with publisher captions | 56 | 72 | 46 |
| LoRA with Qwen pseudo-labels | 56 | 67 | 46 |
| Qwen labeler itself, evaluated on identical windows | 39 | 23 | 16 |

Both adapters removed five development errors. On the held-out lecture, publisher-label training left every basic-normalized prediction unchanged. The pseudo-label adapter improved one window under basic scoring: five digits were written as their corresponding English number words. After normalizing the numbers, all eighteen outputs matched the baseline at the word level. This pilot supplies no observed improvement in recognized word content on the held-out lecture. The teacher's own test score shows a substantial student–teacher gap. The training pseudo-labels differed from the publisher captions at 160/1,136 basic-scored words, or 155/1,183 under English normalization.

The result concerns one instructor, one student model, one seed, and roughly nine minutes of training speech. A practical follow-up is a training-duration comparison (for example 10 versus 30 versus 60 minutes), with a distinct new lecture reserved for confirmation, including separately scored new terms, numbers and ordinary speech. The first four anchors in each lecture were used only to calibrate and check alignment and are disjoint from all reported windows. The publisher specifies the teacher at the course level; we have not manually assigned a speaker identity to each selected window. Existing model pretraining may have included public recordings.

[Aggregate scores and provenance](results/2026-11-26-public-speaker-lora.json). For the public course run, download the four MP4 files and WebVTT files at the URLs in that table, convert audio with ffmpeg to mono 16 kHz PCM16 WAV, and name each pair `lecN.wav` and `lecN.vtt` for N=2–5. Run the data-preparation script with the folder and the Qwen model directory. The training script takes the data folder, the pinned local Whisper model folder (with its commit in `SOURCE_COMMIT`), a fresh output directory and `--device cpu`. Dependencies for the executed student run were PyTorch 2.8.0+cpu, Transformers 4.55.4, and PEFT 0.17.1. The script can explicitly request CUDA or MPS and errors if the requested device is unavailable; timings for those devices have not been measured.
