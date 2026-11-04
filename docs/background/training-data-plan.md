# Training data and evaluation plan

Concept note, started 2026-11-03, updated 2026-11-04. Expands steps 1 to 4 of `personalization.md`: what to save, how clips get their correct text, what training should achieve, and how to measure it. Items marked **Agreed** were settled with Kris; the rest are proposals. Open decisions are at the end.

## 1. What gets trained

- **Agreed:** the model trained is **large-v3-turbo**, the daily dictation model on both machines.
- **Agreed:** Darklord and Nightlord matter about equally. Turbo is the model because Nightlord is the slower machine and sets the speed limit; Darklord runs any of these models almost instantly.
- Training runs on Darklord. The trained model is then copied to Nightlord, after a check that it runs on Nightlord's GPU (untested so far).

## 2. Outcomes

In order of importance:

| | Outcome | Measured by |
|---|---|---|
| O1 | **Short replies (1 to 7 words) typed as said.** This is where the worst mistakes happen: with little audio, the model's sense of which phrases are likely outweighs the sound | exact-match rate: the share of short replies typed exactly right, ignoring case and punctuation |
| O2 | Vocabulary terms (Claude Code, MIND Technology, Seamap, GunLink, BuoyLink, SeaLink, Darklord, Nightlord, ...) typed right **without** the vocabulary rules | term recall: the share of terms said that are typed right |
| O3 | No over-correction: "cloud", "boy", "mind" still typed when said | false-alarm rate: a term typed when it wasn't said |
| O4 | Fewer errors overall | WER (word error rate) = (S + D + I) / N: words substituted, deleted and inserted, over the words in the correct text |
| G1 | Guardrail: general English no worse | WER on a public test set |
| G2 | Guardrail: no slower **on Nightlord** | latency on the bundled benchmark clip, on Nightlord |

Everyday signs of success: the vocabulary rules fire less often, and you flag fewer clips.

## 3. What gets saved

**Agreed:** clips live in `C:\Users\huber\tools\ptt-clips` (beside `gpu-bench`), outside this repo. The path is a setting, so Nightlord's can differ.

Every field is saved from day one, even those not needed yet: none can be recovered later. Saving the audio is what lets every future model be re-run on the same clips and compared fairly.

**Layout:**

```
ptt-clips\
  <machine>\
    labels.jsonl                  correct text, written later (section 5)
    <yyyy-mm>\
      <id>.wav                    the audio
      recordings.jsonl            one record per clip, written at dictation time
      flags.jsonl                 one line per flag (section 4)
```

Each machine writes only its own folder, so moving Nightlord's clips to Darklord never collides. The recordings file is only ever appended to.

**Audio.** The exact buffer handed to `transcribe_audio` (`samples`, `app/ptt/engine.py:457`): pre-roll included, before VAD (voice-activity detection, the silence trimming inside the model call). 16 kHz mono 16-bit WAV, about 115 MB per hour.

**One record per clip** (JSON Lines: one JSON object per line):

| Field | Why |
|---|---|
| `id`: machine + UTC time to the millisecond, e.g. `darklord-20260823T011534.000Z` | unique across both machines with no coordination |
| `machine`, `mic` (`Recorder.device_name`) | results per machine and microphone |
| `app_version`, `model` + `model_revision`, `compute_type`, `beam_size`, `vad`, `hint` (empty today) | the text depends on all of them; a change mid-collection must be visible |
| `duration_s`, `latency_s` | short-reply metric; speed guardrail |
| `raw_text`: the model's output before cleanup and the vocabulary rules | the model's real errors |
| `pasted_text`: after cleanup and the rules; what landed in the terminal | the default target (section 5) |
| `rules_fired` | which rules corrected the text: errors counted with no labelling |
| `segments`: `avg_logprob`, `no_speech_prob`, `compression_ratio` | how sure the model was. One score per 30 s decoding window, so effectively one per clip |

**Agreed:** the target app is not saved (90% of dictation is in the terminal).

**Agreed, the pause toggle:** a tray item. While it's on, dictation works as usual and nothing is saved.

**Storage. Agreed (corrected 2026-11-04): no NAS.** Each machine keeps the clips it records. When it's time to train, Nightlord's clips are moved to Darklord, where training, the second pass and the review run. No backup for now (Kris, 2026-11-04).

**Example:** one clip in this format is saved under `C:\Users\huber\tools\ptt-clips` (the benchmark clip, run through the model on 2026-11-04). Its `mic` is blank and its ID time is the clip's commit time, because the benchmark script recorded neither.

## 4. Flagging a wrong clip

**Agreed:** **double-tap the dictation key** (Right Ctrl on Darklord) to mark the last clip as wrong. Nothing else is done at that moment.

- **The rule:** two taps, each under 0.3 s, within about 0.5 s of each other, with no other key pressed. A tap that short is already discarded today ("ignore short holds", on by default), so the taps type nothing. The flag works only while that setting is on.
- **It follows the dictation key,** so it works on Nightlord too.
- **Agreed, feedback:** no sound. A red block or a small symbol is drawn over the terminal's text cursor for half a second, in a click-through window. Windows Terminal draws its own cursor and only the program inside it can change it, so the block is drawn on top instead.
  - Checked 2026-11-04: Windows Terminal reports its cursor position through UI Automation (the accessibility interface screen readers use): one character cell, 14 × 28 px. The Windows system caret reports nothing.
  - Still to confirm by eye that the block lands on the cursor; that's the first thing built.
  - The terminal first; editors later.
- **What a flag does:** it stops the pasted text becoming the target, and puts the clip in the review queue.

## 5. Targets: the text assumed correct

Training is supervised: each example is a pair (audio x, target y), and training minimises L = −Σ_t log p(y_t | y_<t, x). Neither `raw_text` nor `pasted_text` is the target; the target is `reference` in `labels.jsonl`.

| Clip | Target |
|---|---|
| Not flagged | `pasted_text`, assumed correct. Where a rule fired, this teaches the model to get the term right without the rule |
| Flagged, corrected in review | your correction. The most valuable examples |
| Read-aloud | the script |
| Second pass disagreed, resolved in review | what you confirmed |
| Flagged, never corrected | none: left out of training |

**The target is written the way you want it typed,** not spelled out as spoken. In the example clip, WER was 16/62 = 26% although every word was heard right: all 16 were formatting (digits against spelled-out numbers, "WSL" against "W S L", "push-to-talk"). Training on such a target would teach the model to type "forty-two" instead of "42".

**Style guide. Agreed 2026-11-04.** Correct text is written by these rules, and so are read-aloud scripts:
- **Fix wrong words, not formatting.** Keep the model's capitals, punctuation and number format unless they're wrong for Kris. Training then only has to learn the words, and there's less to type.
- **Names** as in `vocabulary.json`'s `typed` column: MIND Technology, Seamap, GunLink, Claude Code, Darklord.
- **Numbers as digits** (1, 42, 2026, v4), as the model already types them.
- **Acronyms** in capitals with no spaces: WSL, CUDA, GPU.
- **Fillers** ("um", "uh") and false starts left out, as the model already does.

**The second pass.** A batch job on Darklord re-transcribes the day's clips overnight with a bigger, slower model. It never types into your terminal.

- **Agreed:** large-v3 first (1,550 M parameters against turbo's 809 M), given the vocabulary terms as a hint. A stronger model from another family later. Several newer open models beat large-v3 on the public Open ASR Leaderboard; secondary sources put them about 17 to 26% below its WER. The candidates are chosen by their results on the dev set.
- **The hint:** the non-empty `typed` terms of `vocabulary.json`'s `always` rules, perhaps also `context[].means`, without common words such as "four" and "weights". Compare passing them as `hotwords` against `initial_prompt`; faster-whisper ignores `hotwords` when `prefix` is set. Dictation and the benchmark stay hint-free, as they already are for the vocabulary rules.
- **Darklord sleeps.** The overnight pass needs a wake timer, or must run while Darklord is awake. Copying from Nightlord may have to wake it first; the herbert-network skill has the wake-on-LAN command.
- **Agreed, the purpose is better targets:**
  - It compares its text with what was pasted, ignoring punctuation and case.
  - Where they agree, the pasted text is confirmed as the target.
  - Where they disagree, the clip goes to review, flagged or not. That catches errors you didn't notice.
  - For flagged clips it supplies the suggested correction.
- It never writes a target where it disagrees, and it is never the only source of a test-set target.

**The review window** (to be built). Opened when you choose, for example once a week; flagged clips first.

```
 Review - 12 clips waiting                        3 of 12
 > playing  2.1 s  Darklord  Nov 4 14:02
 Typed:    cloud code commit it
 Correct: [Claude Code, commit it            ]   <- suggestion, editable
 Enter = save   Ctrl+R = replay   Del = discard   Esc = stop for now
```

The suggestion is the second pass's text, or the clip dictated right after a flag (often the same words said again). If the queue grows too long, the disagreement threshold can be raised.

## 6. Data sets

**Agreed:** three sets, split by **whole day**: day number mod 10 = 0 or 1 → test, 2 → dev, otherwise train (20 / 10 / 70 %). The day number counts UTC days since 1970-01-01. Whole days, because phrases recur within a session, and a random split would put near-copies in train and test.

- **Train:** what the model learns from.
- **Dev** (development set): for choosing settings (LoRA rank, learning rate, when to stop) and the second-pass model.
- **Test:** used only for the final results. Its targets are checked by you or come from read-aloud.

**Agreed, collect for weeks, up to a month or more.** "Enough" is decided from the data:

- **Training data:** enough when the learning curve flattens, i.e. dev results stop improving as data is added.
- **Test set:** enough when the 95% interval on the difference between two models is narrow enough to show the gain that matters. Weighted towards short replies. Exact-match rate p on n clips has standard error √(p(1 − p) / n): 0.03 at p = 0.8 and n = 200.
- **Test targets:** natural clips are checked by ear. Short replies are tested on natural clips only, because a read reply sounds different from a spoken one.
- **Read-aloud, for O2 and O3:** natural dictation is too sparse in rare terms (in 139 clips: Claude 7, Seamap 1, GunLink, BuoyLink and SeaLink 0). The script carries the terms and contrast words ("cloud", "boy", "mind"), any length. Claude drafts it, Kris approves and records it, and the test part is never trained on.

## 7. Measuring progress during training

At fixed intervals in each run, on the dev set:
- **Training loss and dev loss** (per token). Dev loss rising while training loss falls means memorising.
- **Short-reply exact match, term recall, false-alarm rate, WER.** Loss can fall without these improving; they are what count.
- **Forgetting check:** WER on a fixed slice (~200 utterances) of a public English test set, e.g. LibriSpeech test-clean.
- Keep the checkpoint with the best dev result whose false-alarm rate and forgetting stay within bounds.

Across runs:
- **Learning curve:** 25 %, 50 %, 100 % of the training set against dev results.
- **Comparison runs:** stock, stock with hints, decoder only, encoder and decoder.

Every run writes its numbers to one log file, so runs can be plotted together.

## 8. Measuring final results

Once, on the test set, for each candidate: stock turbo (converted locally from `openai/whisper-large-v3-turbo`), stock with hints, fine-tuned, fine-tuned with hints. All re-run in a batch over the saved audio.

- **Every measure** in section 2, split by machine and by clip length.
- **Paired comparison:** every model sees the same clips. Resample the clips with replacement 10,000 times, recompute the difference each time, and report the 2.5 and 97.5 percentiles. If the interval includes zero, the gain isn't shown.
- **Normalisation:** lower-cased, punctuation removed, the same for every model. Term metrics are case-sensitive ("MIND", not "Mind").
- **Speed** on Nightlord.

Then a **live period** on both machines with the fine-tuned model, saving as before, compared with the baseline period: rules fired per 100 words, flags per 100 clips.

Ship thresholds are set after the baseline (open decision 7 in `personalization.md`).

## 9. Order of work

Superseded: the needs are in `docs/requirements.md` §8 and the build order in `docs/design.md` §11.

## Open decisions

1. ~~The target style guide.~~ Agreed 2026-11-04 (section 5).
2. ~~The clip folder on Nightlord.~~ Agreed 2026-11-04: the same path, `C:\Users\huber\tools\ptt-clips`.
3. Still open: ECC (can wait), and the success thresholds, set after the baseline (increment 6 in `docs/design.md` §11).
