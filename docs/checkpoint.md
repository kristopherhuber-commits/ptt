# Checkpoint: ptt

Updated 2026-11-04 on feature/training, darklord. Resume with: no skill.

## Goal

PTT v4: train the dictation model on Kris's own speech. His words (2026-11-04): "we're working on the next version of the push-to-talk application ... version 4 deals with training. So what I need done now is to build the additional components to do the training." The origin (2026-11-03): on short statements Whisper types the phrase it expects, not what he said ("Tune it so that it gets used to translating stuff like Claude and Mind Technology and Seamap and just the way that I speak").

Finished = v4.0 released per `docs/design.md` §11: seven increments, each tried by Kris before the next, then a week of real use, the final report, the first `docs/changelog.md` entry, and `feature/training` merged into `main`.

## Status

- **Needs approved:** `docs/requirements.md` §8 (`FR-11`–`FR-18`, `NFR-8`–`NFR-14`, `OBS-5`, `CON-6`, `CON-7`, out of scope). Section 1's non-goal now reads "Audio never leaves the user's own machines." Commit `8857021`.
- **Plan approved** ("okay", 2026-11-04): `docs/design.md` §11 (parts, where things live, increments 1–7). Commit `16fe702`.
- **Style guide for correct text approved:** `docs/background/training-data-plan.md` §5. Commit `f70747e`.
- Spikes kept: `spike/v4_example_clip.py` (runs the benchmark clip through the stock model with the app's code; shows a record, the decoder's four start tokens, per-window segment scores) and `spike/v4_caret_probe.ps1` (UI Automation gives Windows Terminal's cursor cell; the system caret gives nothing). The `.ps1` is force-added past `.gitignore`'s `spike/*` rule (commit `7ffedc1`).
- **No app code changed yet.** PTT is still `__version__ = "3.2.1"` (`app/ptt/__init__.py`). Nothing built or installed.

## Next action

Build **increment 1, the walking skeleton** (`docs/design.md` §11.3, item 1) in this fresh session. First recite the session's plan to Kris and wait for his OK (his standing rule).

Done = Kris has tried it on both machines from written steps:
1. **Saving first** (`FR-11`, `OBS-5`). Build and install it before the rest, so real clips pile up while the rest is built. Kris dictates normally; clips go to `C:\Users\huber\tools\ptt-clips\<machine>\`, layout per the data plan §3. Off unless turned on (a setting).
2. **Command-line labeller** in `training/` for a few dozen clips: plays the clip, shows `pasted_text`, Enter accepts, typing replaces. Follows the style guide.
3. **Training environment** at `C:\Users\huber\tools\ptt-train\.venv`: PyTorch with CUDA for Blackwell, `transformers`, `peft`, `ctranslate2`, `faster-whisper`. Never install these into the repo's `.venv`: `build_portable.py` zips it wholesale (`docs/design.md` §8).
4. **A tiny LoRA run** on Darklord from `openai/whisper-large-v3-turbo`; merge; convert to float16 with `ct2-transformers-converter` (`CON-4`: int8 fails on Blackwell). Output to `C:\Users\huber\tools\ptt-train\models\<name>\`.
5. **PTT lists trained models** from that folder beside the stock tiers (`FR-17`). Copy the model to the same path on Nightlord.
6. **A first report** (`FR-18`, crude) in `C:\Users\huber\tools\ptt-train\reports\`: stock against trained on a few held-out clips. The numbers mean nothing yet; the chain working is the point.

Implementation facts a fresh agent would otherwise have to dig for:
- `transcribe.transcribe_audio` (`app/ptt/transcribe.py:382`) returns only the final text. Saving needs the raw text (before `clean_text` and the rules), the rules that fired and the segment scores, so that function or a sibling has to return them.
- The hook point is the engine loop after transcription (`app/ptt/engine.py:483`–`495`): `samples` (float32, 16 kHz, pre-roll included) and the text are both in hand there.
- A new setting goes into `config.FIELDS` (`app/ptt/config.py:349`); afterwards regenerate `app/assets/concierge_kb.md` with `build_knowledge_pack.py`, as commit `0e25e6e` did for `FR-10`.
- `config.py` validates `model` against `transcribe.MODEL_NAMES`, and `paths.local_model_dir()` looks only in `<app>\models\`. A trained-model folder therefore needs both changed.
- Build with `.venv\Scripts\python.exe build_portable.py`, closing anything running from `.venv` first. Install with `install.bat`, as README §2 describes. The installed copy is `C:\Users\huber\AppData\Local\Programs\ptt_dictate` (v3.2.1); its `config.json` survives an upgrade (fix #63 in `docs/development_history.md`).
- **Before the first v4 build,** keep a copy of the v3.2.1 release archive for rollback. Check what `ptt_dictate_dist.zip` in the repo root is first. Where to keep it was never discussed: ask Kris (suggest `C:\Users\huber\tools\ptt-train\`).
- **Nightlord:** an RTX 3080 Ti Laptop GPU, 16 GB. It presumably runs turbo in float16 today (the app's CUDA setting), but that's unchecked. Files move from Nightlord over its SSH alias `darklord` (Tailscale). Darklord has no SSH route to Nightlord, so Kris runs Nightlord's steps there (for example `scp darklord:C:/Users/huber/...`). Nightlord's PTT version is unchecked.
- **Unverified:** whether PyTorch publishes CUDA wheels for Python 3.14 on Windows. The only Python on Darklord is `C:\Users\huber\AppData\Local\Python\pythoncore-3.14-64\python.exe`, so the training environment may need another Python version. Check before installing.
- **GPU:** RTX PRO 6000 Blackwell, 97,887 MiB, shared with dictation and the Concierge's `llama-server` (127.0.0.1:8080).

## Decisions and preferences

- **v4 doesn't use the prd or builder skills.** PTT predates them, and its own pattern applies: needs in `docs/requirements.md`, plan in `docs/design.md`, tests and results in `docs/verification.md`, bugs in `docs/development_history.md`, plus a new `docs/changelog.md` at release. Kris, 2026-11-04.
- **The walking skeleton has the industry meaning:** the thinnest end-to-end slice of the most important need. Kris's first idea was "every need present but rough"; he chose the industry meaning on 2026-11-04.
- **One session per increment.** Kris tries each increment from written steps before the next. At the end of each session, write the next session's start prompt.
- **Every build is installed with `install.bat`** (Kris, 2026-11-04). Running from source is not the way he tries builds.
- **Training code lives in the repo under `training/`** (code only). The environment, models and reports live in `C:\Users\huber\tools\ptt-train\`. Kris, 2026-11-04.
- **Clips** are kept on each machine at `C:\Users\huber\tools\ptt-clips` (same path on both). No NAS and no backup for now. When it's time to train, Nightlord's clips are moved to Darklord. Clips from any machine count, with no quota. All Kris, 2026-11-04.
- **No sounds.** The flag's confirmation is a red mark on the terminal's cursor (`FR-13`, increment 2).
- **The model trained is `large-v3-turbo`,** because Nightlord is the slower machine. Both machines matter about equally. Kris, 2026-11-04.
- **The overnight second pass** starts with large-v3; a stronger non-Whisper model may come later, chosen on the dev set (increment 4).
- **A separate session, "skills-rework"** (the conversation this one branched from on 2026-11-04), is reworking the prd and builder skills in agent-configs. Don't touch those skills from here.

## Dead ends

- **The NAS as the clips hub:** recorded as agreed on 2026-11-04 from Claude's misreading of Kris's answer about the test set, then reversed by him the same day. Don't reintroduce it unless he asks.
- **"Ship saving first" as a separate step before the skeleton:** superseded. Saving is the first thing built and installed inside increment 1.

## Waiting on the user

- **ECC** (error-correcting memory) on the GPU: recommendation is to leave it off. Can wait; since 2026-11-03.
- **Success thresholds:** set after the baseline (increment 6). Can wait.

## Agents in flight

None.

## Not in git

- `C:\Users\huber\tools\ptt-clips\darklord\`: one example clip, made from the benchmark clip by `spike/v4_example_clip.py`. Its `mic` is blank and its ID time is the benchmark clip's commit time. Holds `labels.jsonl`, `2026-08\recordings.jsonl` and the WAV. Delete it or keep it; it isn't real data.
- `docs/development_history.html`: untracked, owner unknown (Kris or another session). Don't touch or commit it without asking.
- Installed PTT on Darklord: `C:\Users\huber\AppData\Local\Programs\ptt_dictate` (v3.2.1), with its own `.venv` (faster-whisper 1.2.1, ctranslate2 4.7.2).
- `C:\Users\huber\agent-configs\vocabulary.json` gained the context entries "ppt" → PTT and "Jason" → JSON (agent-configs `a0be9ce`, `a7a0f62`, pushed).

## Read

1. `docs/requirements.md` §8: the v4 needs.
2. `docs/design.md` §8 (why nothing heavy goes into `.venv`) and §11 (the v4 plan).
3. `docs/background/training-data-plan.md`, whole: record fields, layout, targets, style guide, data sets, metrics.
4. `app/ptt/transcribe.py`, whole; `app/ptt/engine.py` lines 430–500; `app/ptt/config.py` from line 349 (`FIELDS`); `app/ptt/paths.py`; `app/ptt/ui/panels/model.py`.
5. `spike/v4_example_clip.py`: a working example of running the model and writing a record outside the app.
6. `README.md` §2: building and installing.
