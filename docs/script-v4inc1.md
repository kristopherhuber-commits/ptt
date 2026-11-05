# v4 increment 1: the walking skeleton (build 4.0.0.dev4)

**Where things are:**
- This file: `C:\Users\huber\git\ptt\docs\script-v4inc1.md`, in git (branch `feature/training`), so it is the same on both machines after a `git pull`.
- The training tools it runs: `C:\Users\huber\git\ptt\training\` (`label.py`, `train.py`, `report.py`).
- The build it installs: `C:\Users\huber\git\ptt\ptt_dictate_dist.zip` on Darklord (Nightlord copies it with `scp`).
- Clips, the training environment, models and reports: `C:\Users\huber\tools\ptt-v4-training\` on each machine.

Part A now; Part B once Darklord has a few dozen clips. 4.0.0.dev4 adds the flag: after a dictation, tap the dictation key twice quickly and the newest clip is flagged as wrong, with two red asterisks for half a second: a small one at the text cursor and a big one in the middle of the window. Nothing is deleted. There is no time limit after the dictation; only the two taps must be close together.

# Part A: saving and the flag, on both machines

## Step 1 — Darklord: install 4.0.0.dev4

```powershell
cd $env:USERPROFILE\Downloads
mkdir ptt_v4dev4
tar -xf C:\Users\huber\git\ptt\ptt_dictate_dist.zip -C ptt_v4dev4
ptt_v4dev4\install.bat
```

Click Yes on the UAC prompt. The installer should say "Installing PTT Dictation 4.0.0.dev4". Saving stays on from dev3 (the tray item keeps its check mark). Clips now go to `C:\Users\huber\tools\ptt-v4-training\ptt-clips`.

## Step 2 — Darklord: dictate, then flag

Dictate something, read it, then tap the dictation key twice quickly: two short taps, the second within half a second of the first, no other key. Two red asterisks show for half a second: a small one right at the text cursor, like a typed `*`, and a big one in the middle of the window. Nothing is typed. Do it for two or three dictations, then:

```powershell
$m = "$env:USERPROFILE\tools\ptt-v4-training\ptt-clips\$($env:COMPUTERNAME.ToLower())\$((Get-Date).ToUniversalTime().ToString('yyyy-MM'))"
Get-Content $m\recordings.jsonl | Select-Object -Last 3 | ForEach-Object { $r = $_ | ConvertFrom-Json; "$($r.id)  $($r.pasted_text)" }
Get-Content $m\flags.jsonl | ForEach-Object { ($_ | ConvertFrom-Json).id }
(Get-ChildItem $m -Filter *.wav).Count; (Get-Content $m\recordings.jsonl).Count
```

Each flagged id is the dictation just before its double-tap. The two counts at the end are equal and go up by one per dictation: every WAV has its record and nothing was deleted. `debug_log.txt` says "Double-tap: flagged <id> as wrong" for each. A second double-tap on the same clip adds nothing ("already flagged"). If the four lines just sit at the prompt after pasting, press Enter. The first line finds this machine's folder for this month by itself.

## Step 3 — Darklord: check right Ctrl+C is not a flag (no commands)

Press right Ctrl+C twice quickly in a terminal (release both keys between presses). The terminal gets Ctrl+C as usual, since PTT never blocks keys; what matters is no asterisk and nothing new in `flags.jsonl`. Holding right Ctrl through both presses is one long hold: an ordinary dictation of silence, saved as a clip with empty text.

## Step 4 — Nightlord: copy, install

```powershell
scp darklord:C:/Users/huber/git/ptt/ptt_dictate_dist.zip $env:USERPROFILE\Downloads\
cd $env:USERPROFILE\Downloads
mkdir ptt_v4dev4
tar -xf ptt_dictate_dist.zip -C ptt_v4dev4
ptt_v4dev4\install.bat
```

Then right-click the tray icon and switch on **Save clips for training** (it shows a check mark).

## Step 5 — Nightlord: dictate, flag, check

```powershell
$m = "$env:USERPROFILE\tools\ptt-v4-training\ptt-clips\$($env:COMPUTERNAME.ToLower())\$((Get-Date).ToUniversalTime().ToString('yyyy-MM'))"
Get-Content $m\recordings.jsonl | Select-Object -Last 3 | ForEach-Object { $r = $_ | ConvertFrom-Json; "$($r.id)  $($r.pasted_text)" }
Get-Content $m\flags.jsonl | ForEach-Object { ($_ | ConvertFrom-Json).id }
(Get-ChildItem $m -Filter *.wav).Count; (Get-Content $m\recordings.jsonl).Count
```

Dictate and double-tap first, as in Step 2, then run the check (the same lines as Step 2). If it says it cannot find the path, Nightlord's PTT is saving somewhere else (an older build there would have kept the old folders): tell Claude. Then dictate normally on both machines, flagging anything that comes out wrong.

# Part B: label, train, compare (Darklord, once it has a few dozen clips)

## Step 6 — Darklord: label the clips

```powershell
cd C:\Users\huber\git\ptt
python training\label.py
```

Flagged clips come first, marked FLAGGED. Each clip plays and shows what was pasted. Enter = right, type the correct text to replace it, `-` = discard, `?` = replay, `q` = stop. Every answer is saved at once, so you can stop and come back. Label at least 30.

## Step 7 — Darklord: train, merge, convert (a few minutes)

```powershell
cd C:\Users\huber\git\ptt
C:\Users\huber\tools\ptt-v4-training\ptt-train\.venv\Scripts\python.exe training\train.py --name v4inc1
```

It prints the clip counts, one line per epoch (held-out loss rising while training loss falls means it is memorising), then "Done: C:\Users\huber\tools\ptt-v4-training\ptt-train\models\v4inc1".

## Step 8 — Darklord: the report

```powershell
cd C:\Users\huber\git\ptt
C:\Users\huber\tools\ptt-v4-training\ptt-train\.venv\Scripts\python.exe training\report.py --trained v4inc1
code (Get-ChildItem C:\Users\huber\tools\ptt-v4-training\ptt-train\reports\*.md | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
```

A table of stock, stock + rules and v4inc1 (WER, short replies exact), then one row per held-out clip. The numbers mean little yet; the chain working is the point.

## Step 9 — Darklord: use the trained model (Settings, no commands)

Open Settings → Model. A row **v4inc1** ("turbo, trained") is under the six stock rows. Click it, dictate a few clips, then click **large-v3-turbo** to switch back. Each switch takes a few seconds.

## Step 10 — Nightlord: copy the trained model

```powershell
mkdir -Force $env:USERPROFILE\tools\ptt-v4-training\ptt-train\models
scp -r darklord:C:/Users/huber/tools/ptt-v4-training/ptt-train/models/v4inc1 $env:USERPROFILE\tools\ptt-v4-training\ptt-train\models\
```

About 1.6 GB.

## Step 11 — Nightlord: use the trained model (Settings, no commands)

As in Step 9: the v4inc1 row appears; select it, dictate, switch back to large-v3-turbo. This is the first run of a trained model on Nightlord's GPU, so check the tray says "Ready (CUDA)" after the switch.

# Only if you need to roll back

## Step 12 — reinstall v3.2.1 (either machine)

```powershell
cd $env:USERPROFILE\Downloads
gh release download v3.2.1 -R kristopherhuber-commits/ptt -p ptt_dictate_dist.zip -D v321
mkdir ptt_v321
tar -xf v321\ptt_dictate_dist.zip -C ptt_v321
ptt_v321\install.bat
```

About a minute; config.json and saved clips stay where they are. v3.2.1 ignores the new settings and keeps them.
