# Personalizing PTT's speech model

Concept note, 2026-11-03. This is exploration only: nothing here is decided unless it says so. The open decisions are listed at the end.

## The problem

On short statements, dictation often types what the model expects to hear rather than what was said, for example "cloud code" for "Claude Code". The vocabulary rules (`vocabulary.json`) fix known cases after the fact. They can't fix a guess they haven't seen before.

Dictation doesn't use an LLM (large language model). It runs Whisper large-v3-turbo through faster-whisper and then applies the vocabulary rules (`app/ptt/transcribe.py`). Even so, half of Whisper, the decoder, works as a small language model listening to the audio:

    p(y | x) = Π_t p(y_t | y_<t, x)        x = audio, y = text

A short clip carries little sound evidence, so the habits the model learned from its training text win. PTT also gives the model no text hints: there is no prompt, and `condition_on_previous_text=False` is set (`app/ptt/transcribe.py:406`).

## Facts checked (2026-11-03)

- **The installed app** runs faster-whisper 1.2.1. It accepts hint text through `initial_prompt` and `hotwords` (`hotwords` is ignored when `prefix` is set). It also has CTranslate2 4.7.2, which ships `ct2-transformers-converter`.
- **The model to train from** is `openai/whisper-large-v3-turbo` on Hugging Face: width (d_model) 1280, an encoder of 32 layers, a decoder of 4 layers (large-v3's decoder has 32), 809 M parameters.
- **Darklord's GPU** is an RTX PRO 6000 Blackwell with 97,887 MiB of memory and ECC off.
  - PyTorch isn't installed on Darklord.
  - PyTorch has supported Blackwell since version 2.7 (its CUDA 12.8 builds), and Windows CUDA builds exist (2.14.1, CUDA 13.0).
  - Training hasn't been tested on this card yet.
- **Usage on Darklord**, from 2026-11-02 20:30 to 2026-11-03 07:22: 139 clips, 20.7 minutes of audio, 22 clips under 3 s. Terms in those transcripts: Claude 7, Seamap 1, and GunLink, BuoyLink and SeaLink 0.
- **This repo is public** on GitHub, and the uninstaller deletes the install folder.

## Proposed plan

Each step is tried before the next. Steps 1 to 4 make up the trial: step 1 ships first, clips build up for about a week, then steps 2 to 4 follow.

1. **Record.** An opt-in setting saves each clip and its transcript on the machine.
2. **Label with little effort.**
   - Read-aloud clips come with their text, so they need no cleanup.
   - Natural clips get a second, slower pass: large-v3 with the hint prompt and the vocabulary rules.
   - Only clips where the two passes disagree, or the model is unsure, go to a review screen: play, fix the word, press Enter.
3. **Measure the baseline** with the metrics below, on a held-out set of about 20% of the clips. The correct text for those clips must be checked by hand or come from read-aloud text. Using the second pass's labels would bias the comparison toward the hint prompt.
4. **Try the hint prompt.** Pass the `typed` terms from `vocabulary.json` as `hotwords` or `initial_prompt`, then measure again. Stop here if that's good enough.
5. **Fine-tune** on Darklord (see Training).
6. **Ship it** as a separate choice in the Model tab, so the stock model stays one click away. Keep it only if it meets the success thresholds.

## Data

- **Sources.** Mostly natural dictation, plus some read-aloud text:
  - sentences with rare terms;
  - contrast pairs ("cloud storage" / "Claude Code", "boy" / "buoy") to prevent over-correction;
  - short phrases for the short-clip problem.

  Reading sounds different from dictating, so read-aloud should stay the smaller share.
- **Storage.** A plain local folder on each machine. It must not be inside this repo (which is public), the install folder (which the uninstaller deletes) or anything OneDrive syncs.
  - Audio: 16 kHz mono 16-bit WAV, at about 115 MB per hour. The app records at 16 kHz, as float32 in memory, and saves nothing to disk today.
  - One text record per clip: the transcript, the corrected text, the machine and the microphone.
- **Two machines.** Both record.
  - Each saves locally first, then copies new clips to the NAS whenever it can reach it. Tailscale already works off-site.
  - Darklord takes the clips from the NAS to train.
  - The NAS is always on and doubles as the backup.
  - Not git: git keeps every audio file in its history forever and gains nothing for files that never change.
- **Privacy.** The recordings hold whatever is dictated, including MIND MNPI (material non-public information). They stay on the home machines and the NAS: never in a cloud service, and never in this repo.
- **How much data.** Unknown until measured. A learning curve settles it: train on 25%, 50% and 100% of the clips and plot WER against data size. If WER is still falling at 100%, more data will help. At the rate above, a month on two machines gives several hours of audio.

## Training

- **Where.** On Darklord.
  - A full fine-tune holds weights, gradients and Adam's two moment estimates, at 16 bytes per parameter: 809 M × 16 B ≈ 12 GiB, against 95.6 GiB on the card. Activations come on top and grow with batch size.
  - Nightlord's laptop GPU has 16 GB.
  - A cloud GPU would mean uploading the recordings.
- **Method: LoRA** (low-rank adaptation).
  - Each adapted weight matrix W (d × k) stays frozen. Training learns ΔW = BA, with B (d × r) and A (r × k), r ≪ d, and the model uses W + (α/r)·BA, where α is a fixed scale.
  - For d = k = 1280 and r = 32, that's r(d + k) = 81,920 trained numbers per matrix instead of dk = 1,638,400, i.e. 5%. Fewer trained numbers means less overfitting and less forgetting.
  - After training, BA is merged into W, so the speed doesn't change.
  - A full fine-tune also fits on the card, so the test set can choose between the two.
- **Encoder and decoder together.**
  - There's one loss, L = −Σ_t log p(y_t | y_<t, x). The gradient reaches the encoder through cross-attention, so one run adapts both halves.
  - One comparison run: decoder only, with the encoder frozen. If it does as well, the errors come from the language guessing, not the hearing, and the stock encoder can stay.
- **Pipeline.**
  - Train in a separate Python environment (PyTorch, `transformers`, `peft`).
  - Merge the LoRA weights into the model.
  - Convert it to float16 with `ct2-transformers-converter`. The app already requires float16 on this card, because int8 fails there (CON-4 in `app/ptt/transcribe.py`).
  - Compare against a stock model converted the same way. The app's turbo model today is `mobiuslabsgmbh/faster-whisper-large-v3-turbo`, a conversion made by someone else. Converting the untouched `openai/whisper-large-v3-turbo` locally keeps the conversion from skewing the result.
- **ECC** (error-correcting memory): leave it off. Runs are short, and every model is tested before use. Turning it on needs a reboot and may cost memory and speed; that hasn't been measured on this card.
- **When.** The same GPU runs dictation and the Concierge's local LLM, so train when you aren't dictating.

## Measuring success

The thresholds are set after the baseline.

- **WER** (word error rate) = (S + D + I) / N: words substituted, deleted and inserted, over the number of words actually said. Measured on the held-out clips, overall and for clips under 3 s.
- **Term accuracy:** the share of vocabulary terms typed correctly. Also count false alarms: a term typed when it wasn't said.
- **No forgetting:** WER on a public English test set must not rise.
- **Speed:** unchanged on the bundled benchmark clip.

**Expectations.**
- Good odds for the vocabulary terms, because they're consistent, repeated errors. The main risk is over-correction.
- Less certain for short clips in general and for accent, where Whisper probably has less to gain.
- No number until the baseline is measured.

## Versioning and process (proposals)

- **Versioning.** PTT is at 3.2.1. Steps 1 to 4 (recording and hints) would ship as 3.x releases. The personalized model would be 4.0, following v2 (the Qt interface) and v3 (the Concierge).
- **PRD (product requirements document).** Write it after the trial, once the baseline exists. The prd and builder skills are written for a new product (builder covers v1.0 only), so either adapt them or follow how v2 and v3 were done.
  - v2 and v3 each had a folder, `docs/ptt-vN-*/`. v3's holds requirements, design, handoff, verification and session-prompt documents, so that pattern already includes a requirements document much like a PRD.
  - Both were built on a feature branch (`feature/qt-gui`, `feature/concierge`) and merged at release.
  - The 3.x releases were committed straight to `main`.

## Open decisions

1. Run the one-week trial (steps 1 to 4)?
2. The name and path of the clip folder on each machine.
3. The NAS as the hub, or Nightlord copying straight to Darklord.
4. Versioning: 3.x releases, then 4.0.
5. When to write the PRD, and how: adapt the skills or follow the v2/v3 pattern.
6. Keep ECC off.
7. The success thresholds, once the baseline is in.
