# Vocabulary backup

> **Since v3.1 the rules normally live in `%USERPROFILE%\agent-configs\vocabulary.json`**
> (`FR-10`, `design.md` §7.2), which is in git and survives a reinstall by itself. This
> backup and the restore below matter only on a machine without that file.

The replacement rules from the Vocabulary tab, kept in the repository so they
survive the one thing that will otherwise lose them: `app/config.json` is in
`.gitignore` (it is per-machine runtime state, and `build_portable.py` excludes
it from the distribution zip), so a reinstall on this machine or a first install
on another one starts with an empty table and no record of what was in it.

`vocabulary_backup.json` is exactly the array that `config.json` stores under
its `vocabulary` key — the same shape `vocabulary.to_json` writes and
`config._parse_vocabulary` reads back, one object per rule with `heard`,
`typed` and `scope`. It is not a config file: dropping it in beside the app does
nothing. It is restored by writing it into the `vocabulary` key of an existing
`config.json`, which is what the snippet below does.

## Restoring

Close the app first. It holds settings in memory and rewrites `config.json` on
exit, so a running instance will overwrite the restore with what it already had.

On a fresh install, launch the app once and close it again before restoring, so
that `config.json` exists to be edited.

```powershell
$cfg    = "$env:LOCALAPPDATA\Programs\ptt_dictate\app\config.json"
$backup = "$HOME\git\ptt\docs\vocabulary_backup.json"

$config = Get-Content $cfg -Raw | ConvertFrom-Json
$rules  = Get-Content $backup -Raw | ConvertFrom-Json
$config | Add-Member -NotePropertyName vocabulary -NotePropertyValue $rules -Force
$config | ConvertTo-Json -Depth 10 | Set-Content $cfg -Encoding utf8
```

Reopen the app and check the Vocabulary tab. A rule the parser rejects is
dropped rather than applied, and `app/debug_log.txt` names it (`config.json
vocabulary[N] is invalid ...`) — so an empty-looking row after a restore is a
question the log has already answered.

## Updating the backup

This file does not update itself. After adding or changing rules in the app,
copy the `vocabulary` array back out:

```powershell
$cfg = "$env:LOCALAPPDATA\Programs\ptt_dictate\app\config.json"
(Get-Content $cfg -Raw | ConvertFrom-Json).vocabulary |
  ConvertTo-Json -Depth 10 |
  Set-Content "$HOME\git\ptt\docs\vocabulary_backup.json" -Encoding utf8
```

then commit the change.
