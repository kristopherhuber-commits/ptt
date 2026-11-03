# Checkpoint: ptt

Updated 2026-11-03 on main, darklord. Resume with: no skill.

## Goal

Kris asked (2026-11-03) whether the dictation model can be fine-tuned for him: "Tune it so that it gets used to translating stuff like Claude and Mind Technology and Seamap and just the way that I speak". His complaint: on short statements Whisper types the phrase it expects, not what he said. He asked how to do it, then explored hardware (Darklord's GPU, ECC), versioning, the PRD skill, success metrics, recording on both machines, where clips live, read-aloud text, low-effort labelling, data sufficiency, LoRA, and encoder versus decoder training. He said: "we're just exploring how best to proceed."

This stage is finished once Kris has answered the open decisions under *Waiting on the user*. None is answered yet. Nothing gets built until he says go. His standing rules: a question is not a go-ahead, and for bigger work, recite the plan back and wait.

## Status

- **Done:** the exploration was answered in conversation and written up in `docs/background/personalization.md` (first commit `cb622f3`, corrected after the checkpoint test). It holds the plan (steps 1–6), the facts checked on 2026-11-03, the metrics and the open decisions.
- **No app code changed.** PTT is still at `__version__ = "3.2.1"` (`app/ptt/__init__.py:32`).
- **Side change, done and pushed:** agent-configs commit `d5eacc5` added the vocabulary rule "Mind Technology" → "MIND Technology" to `C:\Users\huber\agent-configs\vocabulary.json`.

## Next action

Wait for Kris's answers to *Waiting on the user*. If he says go on the trial, plan it and recite the plan to him, then wait for his OK before writing code. Done means Kris approves the plan.

The trial is steps 1–4 of the note:
- Plan them as successive increments, each tried by Kris before the next.
- Step 1 (opt-in clip saving on each machine) ships first. Clips build up for about a week, then step 2 (labelling and review), step 3 (WER baseline) and step 4 (hint prompt).
- Step 1 doesn't need copying between machines. How the app would reach the NAS (share, path, login) was never discussed and depends on decision 3.

**Design points for the plan, none yet discussed with Kris; raise each in the plan:**
- **Read-aloud mode** (show a script, record against it): include it in the trial? Recommendation: Claude drafts the script from the vocabulary terms plus contrast pairs and short phrases; Kris approves it.
- **Test-set references:** check them by hand, or take them from read-aloud clips whose text is known. Second-pass labels would bias step 4's measurement. This is part of Kris's effort, so keep the set small.
- **Hint terms:** non-empty `always[].typed`, plus perhaps `context[].means`. Filter out common words: entries such as "four" and "weights" are no use as hints. Compare `hotwords` with `initial_prompt`. The benchmark path stays hint-free, as it already is for the vocabulary rules (`transcribe_audio` defaults to no rules).
- **The second pass** (large-v3 with hints) runs in a batch on Darklord when Kris isn't dictating, because the GPU is shared with dictation and the Concierge's LLM.
- **Stock model for comparisons** (steps 5–6): a local `ct2-transformers-converter` conversion of `openai/whisper-large-v3-turbo`, not the app's current `mobiuslabsgmbh/faster-whisper-large-v3-turbo`.
- **Process:**
  - 3.x releases were committed straight to `main` (e.g. `4c72944`). v2.0 and v3.0 were built on `feature/qt-gui` and `feature/concierge` and merged at release (`971a573`, `be24b05`).
  - Ask Kris before editing `docs/requirements.md` or `docs/design.md`; that's his rule.
  - A new setting goes into `config.FIELDS` (`app/ptt/config.py:349`). After that, regenerate `app/assets/concierge_kb.md` with `build_knowledge_pack.py`, which builds part 1 from `FIELDS`, as `0e25e6e` did for FR-10.

## Decisions and preferences

- **Record on both machines,** Darklord and Nightlord ("I use both a lot"). Kris, 2026-11-03.
- **Keep his data-cleaning effort to a minimum** ("That can take a long time"). Kris, 2026-11-03. The note's answer is read-aloud clips plus a second-pass check, with review limited to the flagged clips.
- **Read-aloud text:** Kris raised it as an idea, and Claude recommended it as a minority source, with contrast pairs and short phrases. Not yet confirmed by Kris.
- **Version number:** Kris asked whether this would be v2.0. Claude pointed out that PTT is already at 3.2.1. Undecided; see decision 4.

## Dead ends

None.

## Waiting on the user

All asked on 2026-11-03; they are numbered as in the note's *Open decisions*.

1. **Run the one-week trial (steps 1–4)?** Recommendation: yes, before any PRD (product requirements document).
2. **Name and path of the clip folder on each machine?** Kris's call. Constraints: outside this repo (it's public on GitHub), outside the install folder (the uninstaller deletes it), and outside anything OneDrive syncs.
3. **NAS as the hub, or Nightlord copying straight to Darklord?** Kris floated Nightlord → Darklord. Recommendation: the NAS, because it's always on (Darklord sleeps) and doubles as the backup.
4. **Versioning?** Recommendation: 3.x for recording and hints, 4.0 for the personalized model.
5. **When and how to write the PRD?** Recommendation: after the trial. The prd and builder skills target a new product (builder covers v1.0 only). The alternative is the v2/v3 pattern: a folder `docs/ptt-vN-*/` that includes a requirements document much like a PRD (e.g. `docs/ptt-v3-concierge/concierge_requirements.md`), on a feature branch.
6. **ECC off?** Recommendation: leave it off. Can wait.
7. **Success thresholds?** Set them after the baseline. Can wait.

## Agents in flight

None.

## Not in git

- **`docs/development_history.html`:** it was already untracked when this session started. Its owner is unknown (Kris, or another session). Don't touch or commit it without asking Kris.
- **Installed app on Darklord:** `C:\Users\huber\AppData\Local\Programs\ptt_dictate` (v3.2.1), with its own `.venv` (faster-whisper 1.2.1, ctranslate2 4.7.2, no torch, transformers or peft).
  - Its log, `app\debug_log.txt`, is the source of the note's usage figures.
  - At each start the log is moved to `debug_log.prev.txt`, so the figures survive one restart; a second restart loses them.
- **Python on Darklord:** only `C:\Users\huber\AppData\Local\Python\pythoncore-3.14-64\python.exe`, without PyTorch. Training will need a separate environment.
- **Darklord GPU:** RTX PRO 6000 Blackwell, 97,887 MiB, ECC Disabled (`nvidia-smi --query-gpu=ecc.mode.current --format=csv`).
- **Master vocabulary:** `C:\Users\huber\agent-configs\vocabulary.json`, in the agent-configs repo, not this one. PTT reads it live.

## Read

1. `docs/background/personalization.md`: the whole exploration, the checked facts, the plan and the open decisions.
2. `app/ptt/transcribe.py`: the model tiers (`MODELS`); the `model.transcribe` call at lines 401–407, where the hint prompt would go; and the CON-4 note (float16 is required on Blackwell, lines 78–80).
3. Only when planning the trial:
   - `app/ptt/audio.py` from line 271 (the `Recorder`, float32 at 16 kHz);
   - `app/ptt/engine.py` lines 430–500 (the handoff from recording to transcription);
   - `app/ptt/config.py` from line 349 (`FIELDS`) and lines 736–743 (vocabulary loading);
   - `docs/requirements.md` (whole).
