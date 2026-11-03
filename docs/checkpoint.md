# Checkpoint: ptt

Updated 2026-11-03 on main, darklord. Resume with: no skill.

## Goal

Kris asked (2026-11-03) whether the dictation model can be fine-tuned for him: "Tune it so that it gets used to translating stuff like Claude and Mind Technology and Seamap and just the way that I speak". His complaint: on short statements Whisper types the phrase it expects, not what he said. He asked how to do it, then explored hardware (Darklord's GPU, ECC), versioning, the PRD skill, success metrics, recording on both machines, where clips live, read-aloud text, low-effort labelling, data sufficiency, LoRA, and encoder versus decoder training. He said: "we're just exploring how best to proceed."

Finished for this stage: Kris has decided the open decisions in `docs/background/personalization.md`. Nothing gets built until he says go. His standing rules: a question is not a go-ahead, and for bigger work, recite the plan back and wait.

## Status

- **Done:** the exploration was answered in conversation and written up in `docs/background/personalization.md` (commit `cb622f3`). It holds the plan (steps 1–6), the facts checked on 2026-11-03, the metrics and the open decisions.
- **No app code changed.** PTT is still at `__version__ = "3.2.1"` (`app/ptt/__init__.py`).
- **Side change, done and pushed:** agent-configs commit `d5eacc5` added the vocabulary rule "Mind Technology" → "MIND Technology" to `C:\Users\huber\agent-configs\vocabulary.json`.

## Next action

Wait for Kris's answers to *Waiting on the user*. If he says go on the trial (steps 1–4 of the note), plan that increment and recite the plan to him, then wait for his OK before writing code. The increment covers:
- opt-in clip saving and the folder path;
- copying clips to the NAS;
- the label/review flow;
- a word error rate (WER) baseline harness;
- the `hotwords`/`initial_prompt` hint, built from the `typed` terms in vocabulary.json.

Done means Kris approves that plan.

## Decisions and preferences

- **Record on both machines,** Darklord and Nightlord ("I use both a lot"). Kris, 2026-11-03.
- **Keep his data-cleaning effort to a minimum** ("That can take a long time"). Kris, 2026-11-03. The note's answer is read-aloud clips plus a second-pass check, with review limited to the flagged clips.
- **Read-aloud text:** Kris raised it, and Claude recommended it as a minority source, with contrast pairs and short phrases. Not yet confirmed by Kris.
- **Push back on the "2.0" idea:** Kris asked whether this would be v2.0. PTT is already at 3.2.1; the proposal is 3.x releases, then 4.0. Undecided.

## Dead ends

None.

## Waiting on the user

All asked on 2026-11-03; they are numbered as in the note's *Open decisions*.

1. **Run the one-week trial (steps 1–4)?** Recommendation: yes, before any PRD (product requirements document).
2. **Name and path of the clip folder on each machine?** Kris's call. Constraints: outside this repo (it's public on GitHub), outside the install folder (the uninstaller deletes it), and outside anything OneDrive syncs.
3. **NAS as the hub, or Nightlord copying straight to Darklord?** Kris floated Nightlord → Darklord. Recommendation: the NAS, because it's always on (Darklord sleeps) and doubles as the backup.
4. **Versioning?** Recommendation: 3.x for recording and hints, 4.0 for the personalized model.
5. **When and how to write the PRD?** Recommendation: after the trial. The prd and builder skills target a new product (builder covers v1.0 only), so either adapt them or follow the v2/v3 pattern (a design doc in `docs/ptt-vN-*/`).
6. **ECC off?** Recommendation: leave it off. Can wait.
7. **Success thresholds?** Set them after the baseline. Can wait.

## Agents in flight

None.

## Not in git

- **`docs/development_history.html`:** untracked and not made by this session. Leave it alone; it belongs to Kris or another session.
- **Installed app on Darklord:** `C:\Users\huber\AppData\Local\Programs\ptt_dictate` (v3.2.1), with its own `.venv` (faster-whisper 1.2.1, ctranslate2 4.7.2, no torch, transformers or peft).
  - Its log, `app\debug_log.txt`, is the source of the note's usage figures. It is rotated to `debug_log.prev.txt` at each start, so the figures can't be re-measured after a restart.
- **Python on Darklord:** only `C:\Users\huber\AppData\Local\Python\pythoncore-3.14-64\python.exe`, without PyTorch. Training will need a separate environment.
- **Darklord GPU:** RTX PRO 6000 Blackwell, 97,887 MiB, ECC Disabled (`nvidia-smi --query-gpu=ecc.mode.current --format=csv`).
- **Master vocabulary:** `C:\Users\huber\agent-configs\vocabulary.json`, in the agent-configs repo, not this one. PTT reads it live.

## Read

1. `docs/background/personalization.md`: the whole exploration, the checked facts, the plan and the open decisions.
2. `app/ptt/transcribe.py`: the model tiers (`MODELS`); the `model.transcribe` call at line 401, where the hint prompt would go; and the CON-4 note (float16 is required on Blackwell).
