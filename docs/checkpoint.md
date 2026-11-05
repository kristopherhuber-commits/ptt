# Checkpoint: ptt

Updated 2026-11-04 on feature/training, darklord. Resume with: no skill.

## Goal

PTT v4: train the dictation model on Kris's own speech. His words (2026-11-04): "we're working on the next version of the push-to-talk application ... version 4 deals with training. So what I need done now is to build the additional components to do the training." The origin (2026-11-03): on short statements Whisper types the phrase it expects, not what he said ("Tune it so that it gets used to translating stuff like Claude and Mind Technology and Seamap and just the way that I speak").

Finished = v4.0 released per `docs/design.md` §11: seven increments, each tried by Kris before the next, then a week of real use, the final report, the first `docs/changelog.md` entry, and `feature/training` merged into `main`.

This session (named `v4inc1`): "Read the checkpoint, then build v4 increment 1, the walking skeleton." Kris OK'd the recited plan ("okay", 2026-11-04).

## Status

**Increment 1 is built; Kris has not tried it yet.** Everything Claude can do before his try-out is done. Commits on `feature/training`:
- `86d6ed3` saving (`FR-11`): `app/ptt/clips.py` (new), `transcribe.transcribe_clip` + `Transcript` + `model_identity`, `vocabulary.apply_rules_traced`, settings `save_clips` / `clips_folder`, tray item "Save clips for training" (`app/ptt/ui/qt_tray.py`), engine hook after the paste (`Engine._save_clip`), `__version__ = "4.0.0.dev1"`, knowledge pack regenerated.
- `639c3ba` trained models in the Model tab (`FR-17`): `model` accepts `trained:<folder>` via `Field.pattern` (`config.py`); setting `trained_models_folder`; `transcribe.trained_models` / `trained_model_dir` / `TRAINED_PREFIX`; `Engine._reload` loads a trained model from its folder, or stock `large-v3-turbo` with a logged reason if missing; Model panel adds rows (label = folder name, params "turbo, trained") and refits height (`_fit_table_height`).
- `0bc5680`, `c67dfad` training tools in `training/`: `common.py`, `label.py`, `train.py`, `report.py`, `requirements.txt`.
- `c1d8609` `docs/verification.md`: §3.5 (`V-CL-01`…`05`, `V-TM-01`…`03`), §5.7 (`V-M-107`…`112`), §4 count, §7 row, §8 row.
- Suite: **913 passed** (`uvx --with-requirements requirements-dev.txt pytest`; pytest is not in `.venv`).

**Build:** `C:\Users\huber\git\ptt\ptt_dictate_dist.zip` (1,785,412,165 bytes), 4.0.0.dev1, built from `c67dfad` with saving and the trained-model listing together. **Not installed:** Darklord still runs v3.2.1 (`C:\Users\huber\AppData\Local\Programs\ptt_dictate`). Nightlord unchecked.

**Training environment:** `C:\Users\huber\tools\ptt-train\.venv` (Python 3.14, torch 2.14.1+cu130, transformers 5.22.1, peft 0.21.2, ctranslate2 4.7.2, faster-whisper 1.2.1, nvidia-*-cu12; pins in `training/requirements.txt`). PyTorch runs on the Blackwell card (`V-M-108`).

**Stock baseline:** `C:\Users\huber\tools\ptt-train\baseline\stock-large-v3-turbo-f16\` (float16 conversion of `openai/whisper-large-v3-turbo`, `V-M-109`). Outside `models\` on purpose, so it isn't listed in the Model tab.

**Dry run** of train → merge → convert → report on 39 synthetic clips passed (`V-M-110`); its files are in this session's scratchpad only and are not needed.

**Kris's try-out steps:** `C:\Users\huber\Downloads\script-v4inc1.md` (Darklord only; scripts go in Downloads, never the repo, by Kris's rule). What it says, so another machine knows:
- **Part A, both machines.** Darklord: `mkdir ptt_v4dev1; tar -xf C:\Users\huber\git\ptt\ptt_dictate_dist.zip -C ptt_v4dev1; ptt_v4dev1\install.bat` in Downloads. Nightlord (Darklord has no SSH route to it, so Kris pulls): `scp darklord:C:/Users/huber/git/ptt/ptt_dictate_dist.zip $env:USERPROFILE\Downloads\`, then the same `mkdir`/`tar`/`install.bat`. On each: tick the tray's "Save clips for training", dictate, then count `*.wav` and print each record's `pasted_text` under `%USERPROFILE%\tools\ptt-clips\<machine>`.
- **Part B, Darklord only for steps 6–9** (label at least 30 clips: `python training\label.py`; `C:\Users\huber\tools\ptt-train\.venv\Scripts\python.exe training\train.py --name v4inc1`; the same python with `training\report.py --trained v4inc1`; select the **v4inc1** row in Settings → Model, dictate, switch back to large-v3-turbo).
- **Part B on Nightlord is only steps 10–11:** `mkdir -Force $env:USERPROFILE\tools\ptt-train\models; scp -r darklord:C:/Users/huber/tools/ptt-train/models/v4inc1 $env:USERPROFILE\tools\ptt-train\models\`, then select the row, check the tray says "Ready (CUDA)", dictate, switch back.
- **Step 12:** rollback to v3.2.1 (`gh release download v3.2.1 -R kristopherhuber-commits/ptt -p ptt_dictate_dist.zip -D v321`, extract, `install.bat`).

## Next action

Wait for Kris's try-out results (none reported as of this save), then act on them:
1. **Part A reported** (both machines saving): if anything failed, fix, rebuild, and say what to reinstall. **Build on Darklord only**: a fresh clone can't build, because `.venv` and `app/llama/` are git-ignored and `build_portable.py` refuses without the llama runtime. Close anything running from `.venv` first, then `.venv\Scripts\python.exe build_portable.py`. Install is always `install.bat`; the installed copy's `config.json` survives an upgrade.
2. **Part B reported:** read the newest report in `C:\Users\huber\tools\ptt-train\reports\` (it quotes dictation: never publish it as an Artifact or copy it into the repo, `CON-6`), fix anything that failed.
3. Record the results in `docs/verification.md` §5.7 as `V-M-113` onwards and close the §7 row "v4 increment 1 on the real machines".

Done = Kris has run Part A and Part B on both machines. Then: update this checkpoint, and write the next session's start prompt (increment 2: flag, mark and pause, `FR-12`, `FR-13`, `OBS-5`, `docs/design.md` §11.3 item 2). Recite the increment 2 plan to Kris and wait for his OK before building it.

## Decisions and preferences

- **Save switch in increment 1 = a checkable tray item, "Save clips for training"** (Claude's recommended option; Kris answered the plan with "okay", 2026-11-04). Increment 2's pause (`FR-12`) is this same `save_clips` setting; increment 2 adds the red mark and showing failures (`OBS-5`).
- **One build instead of two** (Claude's call, 2026-11-04, told to Kris in the end-of-build summary; he hasn't reacted): build 1 wasn't installed yet when the trained-model listing was finished, so the zip was rebuilt with both and Kris installs once. Same version, 4.0.0.dev1. Design §11.3's "saving installed first" was so clips pile up while the rest is built; the rest was built before any install, so no clips were lost by it.
- **Dev-time defects** in v4's own unfinished code (e.g. the `preprocessor_config.json` fix) are recorded in `docs/verification.md` §5.7, not `docs/development_history.md`, which is for defects in built or shipped code (Claude's call, 2026-11-04).
- **Increment builds carry `__version__ = "4.0.0.devN"`** (N = increment); the release is `4.0.0`. `tests/test_concierge_layering.py::test_the_version_is_read_without_importing_the_package` now accepts `\d+\.\d+\.\d+(\.dev\d+)?`.
- **Folder settings are stored unexpanded** (`%USERPROFILE%\tools\ptt-clips`, `%USERPROFILE%\tools\ptt-train\models`) and expanded by `paths.user_folder`, so the generated knowledge pack (public repo) names no machine path. Defaults live in `paths.py`.
- **Record fields:** `model` is the HF repo id for stock (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`), `trained:<name>` for a trained model; `model_revision` is the cache's `main` commit for stock, `ptt_model.json`'s `created_utc` for trained. Looked up on the first save after each load. Clip month folder is by UTC. Empty transcriptions are saved too; clips skipped as too short are not.
- **Labels:** `<clips>\<machine>\labels.jsonl`, rows `{"id", "reference", "source": accepted|corrected|discarded, "labelled_at"}`; `reference` is null when discarded; a later row for the same id overrides. No `split` field: increment 1's held-out set is `sha256(id) % 5 == 0` (`training/common.py`); the whole-day split comes in increment 6.
- **Increment 1 labels Darklord's clips only.** Moving Nightlord's clips to Darklord is increment 4.
- **Training:** base loaded in float32 (bf16 would change every untouched weight and skew the comparison), bf16 autocast, LoRA r=32, alpha=64, dropout 0.05 on `q_proj, k_proj, v_proj, out_proj` (encoder self-attention, decoder self- and cross-attention), AdamW lr 5e-4, 5 epochs, batch 8; loss over text tokens and `<|endoftext|>` only (the `<|en|><|transcribe|><|notimestamps|>` prefix is given, not scored). Clips over 30 s are left out. Each model folder gets `ptt_model.json` and `train_log.jsonl`.
- **Report columns:** stock, stock + rules (what dictation types today), trained; WER and short-reply exact match, per-clip table, mean seconds per clip after a warm-up. Run in the training venv but never imports torch (CTranslate2 must use the CUDA 12 wheels).
- **The Concierge** can now `set_config("model", "trained:<name>")` (the pattern is part of `FIELDS["model"]`); its `list_models` and `run_benchmark` stay stock-only. Told to Kris in the end-of-build summary (2026-11-04); no answer, none needed unless he objects.
- **Docs now slightly behind the code, to fix with Kris's OK** (his rule for design files): `docs/design.md` §7/§7.1 say `model` is validated only against `transcribe.MODEL_NAMES` (it also accepts `trained:<name>`); `docs/background/training-data-plan.md` §3 still says the example clip is under `ptt-clips` and that the saved buffer is the one handed to `transcribe_audio` at `engine.py:457` (now `transcribe_clip`, in `Engine.run`'s release branch). Raise at the increment 1 wrap-up.
- Carried over: v4 doesn't use the prd or builder skills (needs in `docs/requirements.md`, plan in `docs/design.md`, tests in `docs/verification.md`, bugs in `docs/development_history.md`, `docs/changelog.md` at release). One session per increment; Kris tries each from written steps; write the next session's start prompt at the end. Every build is installed with `install.bat`. Training code in `training/`; environment, models and reports in `C:\Users\huber\tools\ptt-train\`. Clips at `C:\Users\huber\tools\ptt-clips` on both machines, no NAS, no backup. No sounds. The model trained is `large-v3-turbo`. Ask Kris before editing `docs/requirements.md` or `docs/design.md`. Don't touch the prd/builder skills (session "skills-rework").

## Dead ends

- **Deleting the spike's example clip with `rm`** was refused by the auto-mode permission classifier. It was moved instead (see Not in git); don't retry the delete unless Kris asks.
- **`processor.save_pretrained` alone before converting:** transformers 5 writes `processor_config.json`, and `ct2-transformers-converter --copy_files preprocessor_config.json` fails. `train.py` also calls `processor.feature_extractor.save_pretrained`.
- **`report.py` importing `ptt.config`:** pulls in `ptt.hotkey` → `keyboard`, which the training venv lacks. It parses `vocabulary.json` itself.
- From before: the NAS as clips hub (reversed by Kris); "ship saving first" as a step before the skeleton (superseded).

## Waiting on the user

- **The increment 1 try-out**, Parts A and B of `C:\Users\huber\Downloads\script-v4inc1.md`; since 2026-11-04.
- **ECC** on the GPU: recommendation is to leave it off. Can wait; since 2026-11-03.
- **Success thresholds:** set after the baseline (increment 6). Can wait.

## Agents in flight

None.

## Not in git

- `C:\Users\huber\git\ptt\ptt_dictate_dist.zip`: the 4.0.0.dev1 build (git-ignored; every build overwrites it). The v3.2.1 zip is the GitHub release asset of tag `v3.2.1`.
- `C:\Users\huber\tools\ptt-train\`: `.venv`, `baseline\stock-large-v3-turbo-f16\` (+ `baseline\convert-stock.log`), empty `models\`, `runs\`, `reports\`. Hugging Face cache now also holds `openai/whisper-large-v3-turbo`.
- `C:\Users\huber\tools\ptt-clips\darklord\`: empty, waiting for real clips.
- `C:\Users\huber\tools\ptt-spike-example\`: the spike's fake example clip (labels.jsonl, `2026-08\` with its WAV and record), moved out of the clips folder so training never sees it. Kris may delete it.
- `C:\Users\huber\Downloads\script-v4inc1.md`: Kris's try-out steps.
- `docs/development_history.html`: untracked, owner unknown. Don't touch or commit it without asking.
- Installed PTT on Darklord: v3.2.1 until Kris runs Part A.
- **Nightlord:** RTX 3080 Ti Laptop GPU, 16 GB. Its PTT version and whether it runs turbo in float16 are unchecked; a trained model has never run on it (Part B step 11 is the first).
- **Darklord's GPU** (RTX PRO 6000 Blackwell, 97,887 MiB) is shared by dictation, the Concierge's `llama-server` (port chosen at each launch, `app/ptt/concierge/server.py:97`, so check `nvidia-smi` rather than a port) and now training. Training while PTT dictates is untested (`NFR-14`; increment 4's concern).
- `C:\Users\huber\agent-configs\vocabulary.json`: 51 `always` rules at last read.

## Read

1. `docs/requirements.md` §8: the v4 needs.
2. `docs/design.md` §11: the v4 plan and increments.
3. `docs/background/training-data-plan.md`: record fields, layout, style guide, data sets, metrics.
4. `docs/verification.md` §3.5, §5.7 and the v4 row of §7: what is tested and what is left.
5. `app/ptt/clips.py`, and in `app/ptt/engine.py` `_reload`, `_save_clip` and the release branch of `run`.
6. `training/common.py`, `training/train.py`, `training/report.py`, `training/label.py`.
7. `C:\Users\huber\Downloads\script-v4inc1.md` (on Darklord): the steps Kris is running.
8. `README.md` §2: building and installing (its Option 2 omits `python build_llama_runtime.py`, which `build_portable.py` needs first on a fresh clone).
9. For increment 2: `spike/v4_caret_probe.ps1` (UI Automation gives Windows Terminal's cursor cell; the system caret gives nothing), the input to its open unknown. A new setting touches `config.FIELDS`, the `Settings` dataclass, `_KNOWN_KEYS` and `to_dict()`, then `build_knowledge_pack.py` (see `86d6ed3` for `save_clips`).
10. For increment 7: `docs/background/personalization.md` (the LoRA maths, encoder and decoder).
