# PTT Local Dictation Utility: Retrospective Log

This document is optimized for LLM parser consumption. It records solved issues: the
symptom observed, the underlying cause, and the fix applied. Entries are appended, not
rewritten.

## 📌 Scope of this document

This is the **append-only retrospective log**: symptoms, causes, and fixes, kept so that
solved problems stay solved. It is deliberately narrow.

* What the utility must do, and the constraints these issues produced -> [requirements.md](requirements.md)
* How it is built — configuration matrix, module layout, injection contract -> [design.md](design.md)
* The tests, what each verifies, and their results -> [verification.md](verification.md)

Those sections used to live here and drifted out of date (this file once documented a
`build_dist.py` that no longer existed). They are now maintained next to the code they
describe.

## 🐛 DLL & Resource Resolution Rules

When packaging using PyInstaller (`--onedir` mode):
1. **CUDA DLLs:** Must collect dynamic binaries from pip packages `nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, and `nvidia-cuda-nvrtc-cu12`.
2. **Directory Mapping:** DLL directories (`cublas\bin`, `cudnn\bin`, `cuda_nvrtc\bin`) must be copied to `nvidia/sub/bin` inside the PyInstaller output distribution directory.
3. **DLL Discovery Path:** `app/ptt_tray.py` uses `sys._MEIPASS` when frozen to programmatically locate and add these directories to the Windows search path via `os.add_dll_directory()`.
4. **VAD Model Assets:** Must pass `--collect-data=faster_whisper` to package `silero_vad_v6.onnx` models correctly.

## 📖 Solved Issues & Retrospective Log

### 1. Slow Transcription / CPU Fallback in Frozen Mode
* **Symptom:** Packaged binary transcribes in 30+ seconds instead of 1-2 seconds; GPU is not used.
* **Cause:** PyInstaller doesn't load implicit namespace packages correctly, failing to add CUDA DLL folders to the path.
* **Fix:** Programmatically search `sys._MEIPASS` for `nvidia` subdirectories and run `os.add_dll_directory()`.

### 2. Silero VAD Asset Error
* **Symptom:** `NoSuchFile: Load model from .../faster_whisper/assets/... failed` on execution.
* **Fix:** Bundled `faster-whisper` static assets into the build outputs via `--collect-data=faster_whisper`.

### 3. WhisperModel Missing Attribute
* **Symptom:** Crash Toast: `'WhisperModel' object has no attribute 'device'`.
* **Fix:** Replaced references to `model.device` with a tracking state variable `current_device`.

### 4. Transcription Cutoff and Character Repeat (dots)
* **Symptom:** Saying "testing one two three" typed `Testing .......`.
* **Cause:** `large-v3` hallucinations on trailing silence combined with `condition_on_previous_text=True`.
* **Fix:** Switched model to `large-v3-turbo` (faster, less prone to loops), set `condition_on_previous_text=False`, and added regex filter to strip consecutive periods `re.sub(r'\.{2,}', '', text)`.

### 5. Keystroke Injection Shortcut Conflicts
* **Symptom:** Typing cut off in Notepad (e.g. `Testing 1, 2, 3, ` instead of `Testing 1, 2, 3, 4, 5.`).
* **Cause:** `keyboard.write()` types character-by-character. If the user was still physically releasing the `Ctrl` modifier key, simulated keys were sent as shortcuts (e.g., `4` became `Ctrl+4` which switches Notepad tabs; `c` became `Ctrl+C` which triggers copy).
* **Fix:** Switched from `keyboard.write` character simulation to clipboard-based paste using `Shift + Insert` which is instant, single-event, and doesn't conflict with lingering physical `Ctrl` keys. Temporarily preserves and restores the user's previous clipboard contents.

### 6. Hotkey Recording Wake-up Delay (Headset Latency)
* **Symptom:** A ~1-second delay when starting to record after pressing `Ctrl+Space` (often with headset chime/clicks).
* **Cause:** PortAudio audio streams were started and stopped on every key press and release, triggering hardware wake-up latency.
* **Fix:** Kept the audio stream in the `started` state continuously while active. Implemented callback-level filtering via a `self.recording` flag and added a 200ms pre-roll buffer (`self._preroll`) to prevent clipping early words. Increased idle timeout from 120s to 240s to preserve low-power states when inactive.

### 7. Keyboard Hook Loss / Unresponsiveness after System Transitions
* **Symptom:** PTT stops responding to the `Ctrl+Space` hotkey entirely (the icon remains green), even though the process is running and responding.
* **Cause:** The Python `keyboard` library relies on Windows low-level keyboard hooks (`SetWindowsHookEx`), which Windows silently disables after UAC prompts, screen locks, or sleep timeouts.
* **Fix:** Replaced hook-based polling with the Win32 `GetAsyncKeyState` API in `chord_held()` to query keyboard driver states directly from the OS. This makes hotkey detection completely immune to hook unregistrations.

### 8. Pasting Failure After USB HID (Jabra) Connection & Zombie Process Accumulation
* **Symptom:** The application records audio and transcribes successfully (visible in the debug logs), but no text is pasted at the cursor when the hotkey is released. Additionally, multiple duplicate zombie `ptt_dictate.exe` instances accumulate in memory on restart.
* **Cause:** 
  1. **Hook Thread Failure**: Connecting or disconnecting USB HID devices (like Jabra headsets with physical call control buttons) resets the Windows keyboard hook chain. This silently invalidates the Python `keyboard` library's hook thread, causing simulated inputs (like `keyboard.press_and_release("shift+insert")`) to fail.
  2. **UWP Scancode Requirement**: Modern Windows 11 Notepad (a UWP application) rejects simulated virtual keys (like `VK_INSERT`) if they do not contain valid hardware scan codes and the `KEYEVENTF_EXTENDEDKEY` (0x01) flag (since the physical `Insert` key is in the extended navigation block).
  3. **UIPI Security Blocks**: Windows User Interface Privilege Isolation (UIPI) blocks simulated inputs sent from non-elevated scripts to other privilege contexts or UWP containers.
  4. **Zombie Processes**: Python's interpreter blocks exiting if background threads spawned by CTranslate2 thread pools or the `keyboard` listener hook remain alive, leaving zombie processes in memory.
* **Fix:** 
  1. **Native Input Injection**: Replaced the `keyboard` library's pasting with direct, native Win32 `keybd_event` calls via `ctypes`.
  2. **Hardware Scancodes & Extended Flag**: Resolved scan codes via `MapVirtualKeyW` and explicitly flagged `VK_INSERT` as extended (`0x01`), allowing UWP Notepad and command-line terminals to accept the simulated `Shift+Insert` keystroke.
  3. **Elevation Wrapper**: Standardized launcher and installers to self-elevate to Administrator, bypassing UIPI.
  4. **Forced Process Termination**: Added `os._exit(0)` directly inside the `__main__` entry point of both `ptt_dictate.py` and `ptt_tray.py` to immediately terminate the process and all background threads at the OS level upon exiting.

### 9. Space-Bar Leakage Moving the Cursor / Scrolling During Recording
* **Symptom:** Holding the `Ctrl+Space` hotkey would sometimes type a literal space or scroll the focused window (cursor "moving forward") instead of just starting a recording.
* **Cause:** `chord_held()` detects the hotkey via `GetAsyncKeyState` polling rather than a suppressing keyboard hook (see issue #7), so the physical keypress is never blocked from reaching the focused application. `Space` is a printable/actionable key, so every hold also delivered a real spacebar press to whatever had focus (typing a space, or scrolling in browsers/PDF viewers). `Ctrl` alone doesn't cause this because it has no character or default scroll action.
* **Fix:** Changed the hotkey chord from `Ctrl+Space` to `Shift+Alt` — two pure modifier keys that produce no character and no scroll action on their own, eliminating the leakage. Updated in both `ptt_dictate.py` and `app/ptt_tray.py` (`HOTKEY_MODS`); `VK_MAP` already contained entries for both keys so no new Win32 plumbing was needed.
* **Caveat:** `Alt+Shift` is Windows' default "switch input/keyboard language" hotkey when more than one input language is installed (Settings → Time & Language → Language). On machines with a second layout installed, this should be checked/disabled to avoid the hotkey also cycling keyboard layouts.

### 10. `pip.exe` Self-Upgrade Failure During Portable Build
* **Symptom:** `build_portable.py` failed with `ERROR: To modify pip, please run the following command: ...python.exe -m pip install --upgrade pip` when upgrading pip inside the fresh `.venv`.
* **Cause:** On Windows, `pip.exe` cannot overwrite its own running executable file during a self-upgrade.
* **Fix:** Changed the upgrade step in `build_portable.py` to invoke `python.exe -m pip install --upgrade pip` instead of calling `pip.exe` directly.

### 11. Dictation Silently Failing in Notepad (and every other menu-bar app)
* **Symptom:** Holding the hotkey in Windows 11 Notepad records and transcribes correctly - `debug_log.txt` shows a clean result - but no text ever appears at the cursor.
* **Cause:** Confirmed by direct Win32 probing against a live Notepad window. Windows activates a window's menu bar - or, in WinUI apps like Windows 11 Notepad, the access-key layer - when `Alt` goes **up with no other key pressed in between**. Activation moves keyboard focus off the document: `GetGUIThreadInfo` reports `GUI_CARETBLINKING` dropping to 0, i.e. the caret is gone. Every subsequently injected keystroke is discarded. Two separate triggers were present:
  1. **The user's own release.** `Shift` does not count as an intervening key, so releasing the `Shift+Alt` chord is a bare `Alt` tap.
  2. **The app's own injection.** `paste_text()` unconditionally injected a synthetic `Alt` keyup as its "release stuck modifiers" step, firing a second activation while `Alt` was still physically held.
* **Measured evidence:** with a bare `Alt` down/up before pasting, the caret dies and both `Shift+Insert` **and** `Ctrl+V` are swallowed - so this was never a paste-mechanism or UWP-scancode problem (contrast issue #8). Tapping `Esc` first restores the caret and the paste lands. A bare `Alt` **keyup alone**, with no preceding keydown, is harmless - activation requires the full press. End-to-end check against a pinned Notepad window: Right Ctrl pastes, `Shift+Alt` with the guard pastes (caret alive), `Shift+Alt` without it is swallowed (caret dead).
* **Fix:**
  1. **Default hotkey changed to `Right Ctrl`** (`HOTKEY_MODS = ("rctrl",)`): a lone modifier with no character, no scroll, and no menu activation. It also sidesteps the `Alt+Shift` language-switch caveat from issue #9. `VK_MAP` gained left/right variants so a single side can be bound.
  2. **`suppress_alt_menu()`**: taps `VK_NONAME` (0xFC - reserved and unassigned, so it produces no character and no command) while `Alt` is still held, supplying the missing intervening keypress that renders the release inert. Called on record start (covers the user's physical release) and again inside `paste_text()` (covers the app's synthetic one).
  3. **Conditional, side-aware modifier release**: only modifiers actually reported down are released, and both `VK_LCONTROL` and `VK_RCONTROL` are released explicitly - injecting the unsided `VK_CONTROL` release leaves the right-hand key state set.
  4. **Hotkey made configurable** via `config.json`, validated against `VK_MAP`, with the active chord shown in the tray menu.
  5. **Paste is now logged**: `target_accepts_keys()` checks for a caret before pasting and logs a warning when it is missing, along with the target window class. Previously a swallowed paste left no trace - the log recorded a successful transcription either way, which is why this went undiagnosed.
* **Scope:** not Notepad-specific. Any window with a menu bar or access keys - Explorer, VS Code, Office, Firefox - behaves identically.

### 12. Unsided `win` Chord Detected Only the Left Windows Key
* **Symptom:** A hotkey of `["win"]` in `config.json` responds to the **left** Windows key only. The right one does nothing, silently — the tray shows `Hotkey: Win`, the log shows no error, and the key simply never triggers a recording.
* **Cause:** `VK_MAP["win"]` was `0x5B`, and `0x5B` is `VK_LWIN`. `ctrl`, `shift` and `alt` have real unsided virtual keys (`0x11`, `0x10`, `0x12`) that `GetAsyncKeyState` reports for either side; **Windows has no unsided Win virtual key.** Each name mapped to exactly one code, so `chord_held(("win",))` could only ever poll the left key while `README.md` and `design.md` both documented unsided names as matching either side. The defect was unreachable in practice — nobody had written `["win"]` by hand — until the settings window's "Match either side" checkbox made it one click away.
* **Fix:** `hotkey.py` gained a declarative `KEYS` table in which every entry carries **all** the virtual keys that satisfy its name, and `chord_held` now tests `any` of them. `win` carries `(0x5B, 0x5C)`; `lwin` and `rwin` carry one each. `VK_MAP`, `KEY_LABELS`, `BINDABLE_KEYS` and `BINDABLE_BY_VK` are derived from that table, so the picker's bindable set comes from the same source as the detector's virtual keys.
* **Verified by:** [verification.md](verification.md) `V-HK-07`.
* **Note:** the docs were already correct and became true rather than needing changes. `hotkey.classify` also now warns that any `win` chord opens the Start menu on release — `inject.suppress_alt_menu` neutralises the `Alt` case (issue #11) and has no Win equivalent.

### 13. Context Trimming Rule 2 Was Dead Code (Concierge, session 1)
* **Symptom:** None visible, and that is the point — the Concierge's context budget would simply have degraded worse and sooner than designed. `concierge_design.md` §5.0 rule 2 says "replace the *body* of any tool result older than 2 turns with a one-line summary", before any dialogue is dropped. It never fired. Every over-budget turn went straight to rule 3 and threw away whole exchanges instead.
* **Cause:** The rule was keyed on `entry.role != "tool"`. Tool results are fed back to the model as **`user`** messages — that is the role an OpenAI-compatible endpoint accepts for one — with the tool's name in a separate `tool` field. So the condition was never true, and the branch was unreachable from the moment it was written.
* **Fix:** Key it on `entry.tool` instead: the field that marks an entry as bulk rather than conversation. `agent.Context.assemble` carries a comment saying so, because the mistake is a natural one to make twice.
* **Why it was caught:** Design §5.0 states the trimming rule as **five numbered rules specifically so the L1 suite can pin them one-to-one**, and `stage0_review_v3.md` §4.2 is why they are numbered at all — the single clause that stood before ("trimmed oldest-first, tool-result bodies dropped before dialogue") was not something a test could be written against. A test named "trimming works" would have passed, because rule 3 does work. `test_rule_2_an_old_tool_result_body_becomes_a_one_line_summary` failed on the first run.
* **Verified by:** [ptt-v3-concierge/concierge_verification.md](ptt-v3-concierge/concierge_verification.md) `V-CG-33`.

### 14. A Spike Validator Scored Two Correct Tool Calls as Grammar Breaks
* **Symptom:** The first run of session 1's C7a check — the real eight-tool schema against the pinned build — reported **8/10**, with `plain-write` and `mixed` failing as `"matched 0 oneOf branches"`. Both were textbook `set_config` calls: `{"key": "model", "value": "medium.en"}`.
* **Cause:** The check's own JSON-Schema validator had no `"type": "null"` branch. `set_config`'s `value` is a scalar union that includes `null` (for `audio_device`), so *every string* also "matched" the null member; `value` therefore matched two branches instead of one, the `set_config` branch failed, the tool union collapsed to zero, and two perfectly good calls read as a grammar failure.
* **Fix:** Implement the `null` type. C7a is **10/10** against the shipping schema.
* **Note:** This is the same species of defect `stage0_review_v3.md` §3.3 had already named in the original spike — its validator implemented `const`, `enum` and `minLength` but not `maxLength`, so a run violating `maxLength` would have scored PASS regardless. **"The validator scored it" is not the same claim as "the model produced it."** Any measurement whose instrument is a hand-written validator needs the validator checked against a case it should reject.

### 15. A Check That Measured a Code Path It Never Reached
* **Symptom:** The first run of C7b — "does llama.cpp's converter honour `maxLength`, or silently drop it?" — reported **dropped**, which would have forced an amendment to `concierge_design.md` §4.1 saying the mitigation never fires.
* **Cause:** The check sent the full two-level union with a question. The model chose `action: "tool"`. `reply` was never generated, so `maxLength` was never in the sampler's path at all, and the check dutifully reported the absence of a constraint it had never exercised.
* **Fix:** Run it against a reply-only schema, which puts the constraint on the only path available, and separately against the full union with an explicit instruction to answer. Both stop at **exactly 40 characters, mid-word, with `finish_reason: "stop"`** — the signature of a sampler-level constraint. `maxLength` is honoured; §4.1 needed no amendment.
* **Note:** Same family as #14, from the other end: there, the instrument was wrong about what it saw; here, it was right about something it never looked at. A negative result from a check that did not reach its subject is not a negative result.

### 16. A Result Cap That Could Exceed Itself
* **Symptom:** Caught in review before it shipped, then pinned by a test. `tools.cap()` enforces the uniform 16 KiB tool-result bound (`concierge_design.md` §4.4, Q16) by filling a result until the budget is spent — and then writes `returned_bytes`, `available_bytes`, `truncated` and `hint` into the body *afterwards*. Those counters are five- and six-digit integers. A result assembled to exactly the cap grows past it the moment it describes itself.
* **Fix:** Measure the overhead with deliberately over-wide placeholder integers (`999999999`), so the estimate can only ever be too generous. `test_the_cap_is_never_exceeded_whatever_it_holds` runs the real encoder over four sizes and asserts the encoded bytes, not the estimate.
* **Note:** A bound that is stated in the result it bounds has to include the statement in the bound. The same shape would appear in any "truncated: true, bytes: N" protocol.

### 17. A Derived-Table Test That Could Not Fail
* **Symptom:** `test_the_registry_is_built_from_config_not_from_a_hand_written_list` asserted `spec.enum == config.WRITABLE_KEYS`. The mutation check for **D-CG-13** — replace the derived enum with a hand-written tuple carrying today's values — left it green.
* **Cause:** Equality is the wrong assertion for a derivation. A copy is equal to its source on the day it is written; it is wrong only later, when the source changes and the copy does not. That is exactly the failure mode of issue #12, and exactly what `V-HK-01`'s idiom exists to prevent.
* **Fix:** Two assertions, at two strengths. `spec.enum is config.WRITABLE_KEYS` catches the copy directly. And `test_a_new_setting_reaches_every_consumer_with_no_other_edit` adds a field to `config.FIELDS` and asserts it appears in the generated grammar schema, the native tools array and the knowledge pack — which catches a copy in any of the three consumers, whatever it is spelled.
* **Verified by:** [verification.md](verification.md) §4.1's D-CG-13 mutations, and `V-CF-16`.
* **Note:** [verification.md](verification.md) §4.1 opens "a test that cannot fail is worse than no test, because it reads like coverage". This one read like coverage for about an hour.

### 18. The SSE Transport Died On The First Quiet Second (Concierge, session 2)
* **Symptom:** The CLI rig's first run against a real llama-server got exactly one chunk and then `OSError: cannot read from timed out object`. The knowledge-pack prewarm never completed, so the rig could not reach `ready` and no message was ever sent. The whole L1 suite was green throughout — 612 passing.
* **Cause:** `llm.Client` is built on a poll contract: the transport yields a line, or `None` for "nothing yet", and the *harness* decides what a stall is (design §4.3, Q18). `HttpTransport` implemented that with `http.client` — `response.fp.readline()` against a socket with a short timeout, catching `socket.timeout` and yielding `None`. But `response.fp` is a `BufferedReader` over a `socket.SocketIO`, and `SocketIO.readinto` **latches**: after any timeout it sets `_timeout_occurred` and raises `OSError("cannot read from timed out object")` on every later read. The first gap longer than `POLL_INTERVAL_SEC` poisons the response permanently. The contract is not implementable through that reader.
* **Why L1 never saw it:** L1 forbids HTTP (design §9), so every unit test runs against a fake transport that implements the contract correctly by construction. The suite pinned what the *client* does with `None`; nothing pinned that the real transport can produce one twice.
* **Fix:** `HttpTransport` no longer uses `http.client`. It opens a socket, writes the four request headers itself, and reads through a new `_Reader` that waits with `select` — so the socket never times out and nothing can latch — and de-chunks the body itself, because llama-server sends SSE chunked and a chunk boundary is free to fall in the middle of a `data:` line. The header phase yields `None` too, so a server that accepts the connection and then says nothing is a stall rather than a hang.
* **Verified by:** the rig, which is the only place it can be: a reproduction serving three SSE chunks two seconds apart failed on the first gap before the change and streams all three after it. `concierge_verification.md` §4 records that the real transport has no L1 coverage and why.
* **Note:** Two lessons, and the second is the larger one. A seam that exists so the unit suite can inject a fake is also a seam where the real implementation gets no coverage at all — the fake satisfies the contract by construction, which is exactly what makes it useless as evidence about the real one. And this is the defect the standalone rig was built to find: design §7.2 argues the rig catches what L1 structurally cannot, and it did so on its first run, before it had answered a single question about a prompt.

### 19. A Repair Loop Whose Repair Hint Could Not Be Acted On (Concierge, session 2)
* **Symptom:** Found by the qualification suite's first full run, not by a test. Asked to change the hotkey to Right Alt, Gemma 4 12B called `set_config {"key":"hotkey","value":"['ralt']"}` — the **string** `"['ralt']"`, a Python list literal — was correctly refused with `hotkey invalid (not a list)`, **sent the identical value again**, and then told the user Right Alt could not be used because Alt opens the menu bar. A fabricated reason for its own malformed call.
* **What worked:** `Settings.set()` rejected it at the moment of writing, nothing was saved, nothing was journalled, and the model did not claim success. FR-CG-11's machine half and its safety-absolute both held. This is the same defect class design §4.6 predicted from spike C2's `{"key":"use_gpu","value":"false"}`.
* **Cause:** The hint was `"read the setting's type before writing it"` — a sentence that names no type, no shape and no example. Design §4.3 makes the structured error the mechanism by which a wrong first attempt becomes a right second one, and §6's threshold ("writes correct **after** the repair loop, 100%") assumes it does. A hint that cannot be acted on is a repair loop that cannot repair.
* **Fix:** `tools._retry_hint()`, derived from the field's `FIELDS` entry — the type from `rule.schema_type()`, the `choices` and bounds if it has them, and the value it currently holds, which is a worked example of the shape in the field's own units. `hotkey takes array; it currently holds ["rctrl"], which is the shape a new value must have`. Nothing is hand-written per key and nothing is disclosed that `get_config` would not already have returned.
* **Verified by:** `test_a_refused_write_hints_the_shape_a_retry_needs` and `test_the_retry_hint_is_derived_from_fields_not_written_per_key`; re-measured through the suite's write and refusal classes.
* **Note:** The instrument found this, and it could not have been found anywhere else — every L1 test asserts the *reason*, which was correct, and no unit test can ask whether a sentence is actionable. The fabricated rationale is the more interesting half and is a model finding for gate 2.5 rather than a harness defect: the harness made the refusal structural, and the model still invented a story about why the user's request was impossible.

### 20. A Pin With No Date, And The Hour It Cost (Concierge, session 2)
* **Symptom:** Not a defect in the code — a defect in what the code's documentation recorded. A candidate review reported that Google had shipped a Gemma 4 update on 2026-07-15 with tool-calling fixes, observed that `lmstudio-community/gemma-4-12B-it-GGUF` had a `lastModified` of 2026-07-20, and concluded our pin was probably stale and that the session-2 prompt iteration was aimed at superseded weights. The correct procedural call — stop and resolve the pin before iterating further.
* **What was actually true:** The pin is the **post**-update artefact. Verified three ways: the repo's current `main` LFS oid, the pin in `fetch.py`, and `sha256sum` of the file in `spike/models/` are all `95d83ba3…73f8`. The 2026-07-20 commit *did* change the file — the previous revision was `e4db6f8c…bc8c` at 7 381 384 864 bytes against our 7 381 382 944 — but the spike downloaded on 2026-08-25, five weeks later. No re-pin, and C7a did not need re-running.
* **Cause:** `concierge_handoff.md` §1 recorded the SHA-256 and nothing else. A hash answers "is this the file I mean?" and cannot answer "is the file I mean the current one?" — for that you need the date it was taken, which nobody wrote down. So a question that should have been a glance took four Hugging Face API calls and a 7.4 GB hash, and its plausible-but-wrong answer ("`lastModified` is after the update, therefore stale") was one step away from a needless re-pin and a re-run of every candidate.
* **Fix:** `concierge_handoff.md` §1.1 — the three-way match, the upstream commit history behind it, the date the pin was taken, and what each consequence is. Any future pin records when it was taken, not only what it is.
* **Note:** The inference that trapped the review is worth naming, because it will recur: **a repository's `lastModified` is not the pinned file's `lastModified`.** It moves for a README edit. The tree API's per-file LFS `oid` is the thing to compare, which is exactly what `fetch.py` already does before a download (FR-CG-7, Q26) — the harness had the right check and the documentation did not.

## 🛠️ Maintenance & Execution Protocols

### Native Terminal Execution
```powershell
.venv\Scripts\python.exe ptt_dictate.py
```

### Native Headless Tray Execution
```powershell
.venv\Scripts\pythonw.exe app\ptt_tray.py
```

### Clean Recompile & Rebuild
```powershell
python build_portable.py
```

### Unit Tests
See [verification.md](verification.md) section 1.
