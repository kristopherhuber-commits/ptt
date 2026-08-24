# PTT Dictation — Verification

**Did we build it right?** This document holds every test, what design element each one
verifies, and the result. It is the counterpart to [design.md](design.md), the way
`validation.md` — not yet written — is the counterpart to
[requirements.md](requirements.md).

The distinction is deliberate and load-bearing for how this file is written:

| Document | Question | Traces to |
|---|---|---|
| `verification.md` (this) | Was the design implemented correctly? | `design.md`, `gui_handoff/gui_handoff.md` |
| `validation.md` *(to be written)* | Was the right thing built? | `requirements.md` |

So **the spine of section 3 is the design element, not the requirement.** A requirement
column is carried alongside it, because each design element names the requirement it
exists to satisfy — but that column is context for validation, not a validation claim.
A passing test here proves the code matches the design. It does not prove the design
satisfies the user's need; that is the other document's job.

## 📌 Scope of this document

* Every automated test, what it pins, and where it lives.
* Every manual test that has been executed, with its actual result and date.
* The status of the GUI acceptance criteria.
* **What is not yet verified**, stated plainly rather than omitted.

Not here: what the utility must do ([requirements.md](requirements.md)), how it is built
([design.md](design.md)), or the symptom/cause/fix record of solved bugs
([development_history.md](development_history.md)). Solved issues cross-reference the
verification item that keeps them solved; the item itself is described here.

---

## 1. Running the tests

```powershell
uvx --with-requirements requirements-dev.txt pytest
```

Pure and fast — no GPU, no microphone, no model, no `QApplication`, about two seconds.

pytest is deliberately **not** in `.venv`. `build_portable.py` zips `.venv` wholesale, so
installing it there would ship the test framework to every target PC, and `CON-3` forbids
adding it to `requirements.txt`. `requirements-dev.txt` lists pytest plus the five runtime
packages the pure modules actually import, pinned to the same versions `requirements.txt`
pins so the test environment matches the shipped one. `items_to_zip` in
`build_portable.py` is an explicit allowlist, so neither `tests/` nor
`requirements-dev.txt` reaches a distribution.

`pyproject.toml` supplies `pythonpath = ["app"]` so `import ptt` resolves without
installing anything, and `testpaths = ["tests"]`.

### Where the tests reach hardware

They do not. Every point at which a module would touch Win32, an audio device, a model or
an event loop is reached through a seam **that already existed in the design for that
reason** — none was added to make testing possible:

| Seam | Declared in | Used by |
|---|---|---|
| `hotkey._key_state()` | `hotkey.py` | `chord_held`, `poll_vks` — the single `GetAsyncKeyState` call site |
| `Engine(chord_held=…)` | `engine.py` — its docstring calls it "a seam so the loop can be driven without a keyboard in step 2's tests" | the whole poll loop |
| `paths.asset_path()` | `paths.py` — the sole owner of every application-relative path | the benchmark clip |
| `on_state` / `on_text` / `on_benchmark` | `engine.py`'s callback contract | every state assertion |

### The log-capture fixture

`tests/conftest.py` redirects `paths.debug_log_path()` into each test's own directory,
autouse and not optional. Two reasons, and the second is the important one:

1. `log_debug` appends unconditionally and swallows every error, so an unguarded run would
   quietly append hundreds of lines to the log `logging_setup.init`'s docstring says is
   diffed against a captured baseline.
2. Returning those lines to the test is what makes `OBS-3` checkable at all. `load()`
   returning a default, and `load()` returning a default *for a logged reason*, are
   indistinguishable from the return value. Eleven tests assert both halves.

---

## 2. How to read the item IDs

`V-<area>-<nn>`. One ID per verified design element, not per test function — a single
element is often pinned by several tests, and the ID is what other documents cite.

| Prefix | Area | Design source |
|---|---|---|
| `V-HK` | chord vocabulary, detection, classifier | `design.md` §6 |
| `V-CF` | configuration | `design.md` §7 |
| `V-EN` | the state machine | `design.md` §4, §6 |
| `V-TR` | transcription and the model catalogue | `design.md` §6, `gui_handoff` §6.2 |
| `V-UI` | the GUI's derived logic and data tables | `gui_handoff` §5, §6.1, §6.2 |
| `V-M`  | manual, executed by hand against the running app | — |

---

## 3. Traceability matrix

### 3.1 Engine — `design.md`

| ID | Design element | What it guarantees | Verified by | Req. |
|---|---|---|---|---|
| `V-HK-01` | §6 *Chord representation* — one declarative `KEYS` table | `VK_MAP`, `KEY_LABELS`, `BINDABLE_KEYS`, `BINDABLE_BY_VK` all derive from it, so nothing can drift | `test_hotkey.py::test_vk_map_and_labels_derive_from_keys`, `::test_bindable_keys_are_the_bindable_entries`, `::test_every_family_has_one_unsided_name_and_two_sided_ones` | `FR-4` |
| `V-HK-02` | §6 *Chord representation* — a chord is a validated tuple of names | Unknown names, empty and non-list values are rejected with a reason; case and whitespace normalise | `test_hotkey.py` — 8 `parse_chord` tests | `FR-4` |
| `V-HK-03` | §6 *Default* — `("rctrl",)` | The shipped default parses and the classifier finds nothing to warn about | `test_hotkey.py::test_default_hotkey_is_valid_and_safe` | `FR-C3` |
| `V-HK-04` | §6 *Chord representation* — unsided names match either side | `ctrl`, `shift`, `alt` each have one real unsided virtual key that the OS reports for either side | `test_hotkey.py::test_the_other_unsided_names_have_a_real_unsided_virtual_key` | `FR-4` |
| `V-HK-05` | §6 — detection polls, never hooks | `chord_held` needs every key down, is order-independent, and falls back to the `keyboard` library when Win32 is unreachable | `test_hotkey.py::test_chord_held_needs_every_key_down`, `::test_chord_held_is_order_independent`, `::test_chord_held_falls_back_to_the_keyboard_library` | `FR-C2` |
| `V-HK-06` | §6 *The picker* — the picker and the detector share one code path | `poll_vks` returns the down subset, and an empty set rather than raising when Win32 is unavailable | `test_hotkey.py::test_poll_vks_returns_only_the_keys_that_are_down`, `::test_poll_vks_is_empty_rather_than_raising_without_win32` | `FR-C2` |
| **`V-HK-07`** | §6 *Chord representation* — **every name carries all the virtual keys that satisfy it** | `win` matches **either** Windows key. Regression for [development_history.md](development_history.md) issue #12 | `test_hotkey.py::test_win_carries_both_virtual_keys`, `::test_win_matches_either_side` (both sides), `::test_sided_win_does_not_match_the_other_side` | `FR-4` |
| `V-HK-08` | §6 *The picker* — a chord the picker builds has a canonical order | `KEYS` order, de-duplicated, idempotent, and never raises on an unknown name | `test_hotkey.py` — 4 `canonical` tests | `FR-4` |
| `V-HK-09` | §6 *Safety classifier* — printing / scrolling key | `space` warns that it types a character into the focused window | `test_hotkey.py::test_space_warns_that_it_types_a_character` | `FR-C3` |
| `V-HK-10` | §6 *Safety classifier* — any `Alt` | Warns that Alt activates the target's menu bar on release | `test_hotkey.py::test_any_alt_warns_about_the_menu_bar` | `FR-C3` |
| `V-HK-11` | §6 *Safety classifier* — any `Win` | Warns that Win opens the Start menu. `inject.suppress_alt_menu` neutralises Alt and has no Win equivalent | `test_hotkey.py::test_any_win_warns_about_the_start_menu` | `FR-C3` |
| `V-HK-12` | §6 *Safety classifier* — **exactly** `Alt+Shift` or `Ctrl+Shift` | Warns about the layout switch for those two, and **not** for `Win+Shift`, `Ctrl+Alt+Shift` or `Shift+Shift` — the narrowing that stops the box crying wolf. Neither of those chords is left with no warning at all | `test_hotkey.py::test_alt_shift_and_ctrl_shift_warn_about_the_layout_switch`, `::test_other_shift_combinations_do_not_warn_about_the_layout_switch`, `::test_none_of_those_chords_is_left_with_no_warning_at_all` | `FR-C3` |
| `V-HK-13` | §6 *Safety classifier* — a lone unsided common modifier | `ctrl` or `shift` alone warns that it fires during ordinary typing; the same name inside a chord does not | `test_hotkey.py::test_a_lone_unsided_common_modifier_warns_about_ordinary_typing`, `::test_an_unsided_modifier_in_a_chord_does_not_warn_about_ordinary_typing` | `FR-C3` |
| `V-HK-14` | §6 *Safety classifier* — empty is rejected, not warned | `classify(())` returns nothing; a lone sided modifier is clean | `test_hotkey.py::test_an_empty_chord_is_rejected_rather_than_warned`, `::test_a_lone_sided_modifier_is_safe` | `FR-C3` |
| `V-CF-01` | §7 — defaults when the file is absent or unusable | Missing, malformed and non-object files all fall back and log why | `test_config.py::test_missing_file_uses_defaults`, `::test_malformed_json_uses_defaults`, `::test_a_top_level_array_uses_defaults` | `FR-8`, `OBS-3` |
| `V-CF-02` | §7 — unknown keys are preserved on write | `future_setting` survives load → save → load; a known key wins a collision because it is serialised last | `test_config.py::test_unknown_keys_survive_a_round_trip`, `::test_a_known_key_wins_a_collision_with_a_preserved_unknown_one` | `FR-8` |
| `V-CF-03` | §7 — `version` is written back on save, not on read | Migration is lazy: loading a v0 file does not rewrite it | `test_config.py::test_version_is_written_back_on_save_not_on_read`, `::test_a_non_integer_version_falls_back` | `FR-8` |
| `V-CF-04` | §7 — **validated by type, not by truthiness** | `{"use_gpu": "false"}` is a truthy string and must not force GPU on | `test_config.py::test_use_gpu_as_a_string_falls_back_and_logs`, `::test_use_gpu_rejects_every_non_boolean` | `FR-8`, `OBS-3` |
| `V-CF-05` | §7 — an invalid chord falls back and logs the reason | Unknown name, empty, non-list — each with its own reason in the log | `test_config.py::test_an_invalid_hotkey_falls_back_and_logs_the_reason` | `OBS-3` |
| `V-CF-06` | §7 — a chord in the file is loaded unchanged | A hand-written unsided name and a four-key chord both survive being read. The three-key cap is the picker's rule, not the file format's | `test_config.py::test_an_unsided_hotkey_loads_unchanged`, `::test_a_four_key_hotkey_is_accepted` | `FR-4` |
| `V-CF-07` | §7 — `model` validated against the catalogue | An unrecognised name falls back rather than being handed to faster-whisper to fetch from Hugging Face | `test_config.py::test_an_unknown_model_falls_back_and_logs`, `::test_a_non_string_model_falls_back_and_logs`, `::test_every_catalogue_name_is_accepted` | `FR-8`, `OBS-3` |
| `V-CF-08` | §7 — `benchmarks` validated per entry | One malformed entry is dropped with its own log line; good entries beside it survive | `test_config.py::test_a_bad_benchmark_entry_is_dropped_and_logged` (6 shapes), `::test_a_bad_entry_does_not_take_the_good_ones_with_it` | `OBS-3` |
| `V-CF-09` | §7 — **`save()` is atomic** | A save that fails part-way through writing leaves the previous file intact and readable, and removes its temporary file | `test_config.py::test_a_save_that_fails_mid_write_leaves_the_previous_file_intact`, `::test_a_failed_save_leaves_no_temporary_file_behind`, `::test_a_successful_save_leaves_no_temporary_file_behind` | `FR-8` |
| `V-CF-10` | §7 — `save()` never raises | A read-only disk logs and continues; it must not take the application down mid-dictation | `test_config.py::test_save_never_raises_on_an_unwritable_path` | `FR-8` |
| **`V-EN-01`** | §6 *What makes the live re-read safe* | **The running loop picks up a rebound chord with no restart.** The real `Engine.run()` is driven on a thread, `settings.hotkey` is rebound mid-run, and the next poll asks about the new chord | `test_engine.py::test_hotkey_rebind_takes_effect_without_restart`, `::test_the_loop_never_caches_the_chord` | `FR-4`, `FR-C2` |
| `V-EN-02` | §4 — the engine reports state through a callback | Hold → `recording`, release → `transcribing` → text → paste → `idle` | `test_engine.py::test_holding_the_chord_records_and_releasing_transcribes` | `FR-1`, `FR-2`, `FR-7` |
| `V-EN-03` | §4 — a frontend bug cannot kill the poll loop | A raising `on_state` is swallowed and logged; the loop keeps polling | `test_engine.py::test_a_raising_state_callback_does_not_kill_the_poll_loop` | `FR-1` |
| `V-EN-04` | §4 — minimum hold | A recording shorter than `MIN_RECORD_SEC` is discarded without transcription | `test_engine.py::test_a_tap_shorter_than_the_minimum_is_not_transcribed` | `FR-3` |
| `V-EN-05` | §4 — the model name is re-read at reload time | Selecting a model reloads it, twice in succession, without a restart | `test_engine.py::test_request_model_reload_rebuilds_the_model`, `::test_the_model_name_is_read_from_settings_at_reload_time` | `FR-5` |
| `V-EN-06` | §4 — hardware has the last word | With no CUDA, the engine forces `use_gpu = False` at construction | `test_engine.py::test_cuda_unsupported_forces_cpu_at_construction` | `FR-6` |
| `V-EN-07` | `gui_handoff` §6.2 — measuring never loads a second model | The benchmark times the resident model, allocates no second `WhisperModel`, reuses the `transcribing` state, and returns to idle | `test_engine.py::test_request_benchmark_times_the_resident_model`, `::test_benchmarking_does_not_load_a_second_model`, `::test_benchmarking_reuses_the_transcribing_state`, `::test_the_engine_returns_to_idle_after_a_benchmark` | `CON-4` |
| `V-TR-01` | §6 — output free of silence artefacts | Runs of two or more full stops are stripped; a single full stop survives. Regression for issue #4 | `test_transcribe.py` — 5 `clean_text` tests | `NFR-5` |
| `V-TR-02` | `gui_handoff` §6.2 — one model catalogue | `MODEL_NAMES` derives from `MODELS`, the default is in it, names are unique, every row is populated, every disk figure is marked as an estimate | `test_transcribe.py` — 5 catalogue tests | `FR-8` |
| `V-TR-03` | §6 — a bundled model is preferred over a fetch | `resolve_model_path` returns the directory when one exists and the bare name otherwise | `test_transcribe.py::test_resolve_model_path_prefers_a_bundled_directory`, `::test_resolve_model_path_returns_the_bare_name_when_nothing_is_bundled` | `NFR-6` |
| `V-TR-04` | `gui_handoff` §6.2 — the benchmark clip is fixed | The bundled clip is mono 16-bit 16 kHz; loading returns float32 in range; a wrong-format clip **raises** rather than being measured silently | `test_transcribe.py::test_the_bundled_clip_is_the_format_the_benchmark_expects`, `::test_load_benchmark_clip_returns_float32_in_range`, `::test_load_benchmark_clip_refuses_the_wrong_format` (3 shapes) | — |
| `V-TR-05` | `gui_handoff` §6.2 — measurements are self-invalidating | The clip digest is stable, changes when the clip changes, and is empty when the clip is missing | `test_transcribe.py` — 3 `benchmark_clip_id` tests | — |
| `V-TR-06` | `gui_handoff` §6.2 — a model on disk is reported as on disk | A bundled directory is reported with its real byte count | `test_transcribe.py::test_installed_sizes_reports_a_bundled_model_directory` | — |

### 3.2 GUI — `gui_handoff/gui_handoff.md`

`design.md` §4's module table points at this document for the UI layers.

| ID | Design element | What it guarantees | Verified by | Req. |
|---|---|---|---|---|
| `V-UI-01` | §5 / §7 — the engine has **no** error state | Both failure strings the engine emits are recognised as errors and drive the dark-red dot; every ordinary status string is not | `test_statusview.py::test_the_two_failure_strings_the_engine_emits_are_errors`, `::test_the_ordinary_status_strings_are_not_errors`, `::test_error_is_matched_at_the_start_only`, `::test_error_text_overrides_the_state_for_the_dot` | `OBS-1` |
| `V-UI-02` | §5 — the detail line is *derived*, the headline is the engine's | Every branch: error, loading, recording, measuring, transcribing, CPU fallback, device known, nothing known | `test_statusview.py` — 8 `detail()` tests | `OBS-1` |
| `V-UI-03` | §5 — a value this build cannot obtain shows an em dash | Unsupplied fields default to the placeholder rather than to a plausible invention | `test_statusview.py::test_unsupplied_values_default_to_the_placeholder` | `OBS-1` |
| `V-UI-04` | §6.1 — a full 104-key board | Exactly 104 caps, every one labelled, no cap with a virtual key of zero | `test_panels.py::test_the_board_is_a_full_104_key_keyboard`, `::test_every_cap_has_a_label`, `::test_the_board_carries_no_virtual_key_of_zero` | `CON-3` |
| `V-UI-05` | §6.1 — the bindable set comes from the engine's table | Every key in `hotkey.BINDABLE_BY_VK` is drawn exactly once; nine caps are bindable and the rest are not | `test_panels.py::test_every_bindable_key_appears_exactly_once_on_the_board`, `::test_nine_caps_are_bindable_and_the_rest_are_not` | `FR-4` |
| `V-UI-06` | §6.1 — one virtual key per cap, with one true exception | Only the two `Enter` keys share a code, because Windows gives them one. Any other duplicate is a typo that would produce a cap that never shades | `test_panels.py::test_only_the_two_enter_keys_share_a_virtual_key` | `FR-4` |
| `V-UI-07` | §6.1 — keycap geometry | `32n − 4`; a run's width depends only on its total units, which is what keeps the nav cluster above the arrows and the keypad rows level; the keypad grid has no overlapping cell | `test_panels.py::test_cap_width_spans_the_caps_and_the_gaps_between_them`, `::test_a_prefix_of_a_row_always_ends_on_the_same_pixel_boundary`, `::test_the_keypad_grid_has_no_overlapping_cells`, `::test_the_keypad_is_four_columns_wide` | `CON-3` |
| `V-UI-08` | §6.1 — the picker's rules | The three-key cap; "match either side" expands to the right-hand key, which is the safer side and the same reason the default is Right Ctrl | `test_panels.py::test_the_chord_cap_matches_the_engines_limit`, `::test_the_preferred_side_is_the_right_hand_one` | `FR-4` |
| `V-UI-09` | §6.2 — a measurement is keyed by model **and** device | A CPU figure and a CUDA figure are different numbers about different hardware | `test_panels.py::test_benchmark_key_names_the_model_and_the_device` | — |
| `V-UI-10` | §6.2 — a measured size is never confusable with an estimate | MB below a gigabyte, GB above, and no `~` prefix on a real figure | `test_panels.py::test_format_bytes_uses_megabytes_below_a_gigabyte`, `::test_format_bytes_switches_to_gigabytes_at_the_boundary`, `::test_a_measured_size_is_not_marked_as_an_estimate` | — |

---

## 4. Automated tests

**176 tests, 134 test functions, ~2 s.** Last run **2026-08-24** against commit
`0722294`: **176 passed, 0 failed.**

| Module | Tests | Covers |
|---|---:|---|
| `tests/test_hotkey.py` | 56 | `V-HK-01` … `V-HK-14` |
| `tests/test_config.py` | 42 | `V-CF-01` … `V-CF-10` |
| `tests/test_transcribe.py` | 25 | `V-TR-01` … `V-TR-06` |
| `tests/test_statusview.py` | 25 | `V-UI-01` … `V-UI-03` |
| `tests/test_panels.py` | 16 | `V-UI-04` … `V-UI-10` |
| `tests/test_engine.py` | 12 | `V-EN-01` … `V-EN-07` |

A result recorded here is a snapshot and can go stale. The command in section 1 is the
authority; this row exists so a reader knows what the suite looked like when it was last
witnessed, not so it can be cited instead of running it.

### 4.1 The suite is checked against mutation

A test that cannot fail is worse than no test, because it reads like coverage. Each of
these deliberately reintroduces a defect the design forbids, and the matching item fails:

| Mutation | Expected to fail | Result |
|---|---|---|
| Revert `win` to a single virtual key (`0x5B`) | `V-HK-07` | 2 tests failed ✅ |
| Broaden the layout-switch rule to "any multi-key chord with a shift" | `V-HK-12` | 3 tests failed ✅ |
| Restore the truncating `open(path, "w")` in `Settings.save` | `V-CF-09` | 1 test failed ✅ |

The third initially did **not** fail, and that is the most useful thing in this section.
The first version of `V-CF-09` failed the save on a *missing directory*, which raises at
`open()` before any truncation and therefore passes against either implementation. It was
rewritten to fail a save part-way through writing the real target, which is where the
guarantee actually lives. It then failed the mutation as it should.

---

## 5. Manual verification

Behaviour of a live window against a live OS cannot be unit-tested. These were executed by
hand against the running application and their results recorded as reported.

### 5.1 Session 3 — Hotkey and Model panels · 2026-08-23

Build: commit `3443a03`, run via `run_tray.bat`. Hardware: laptop, no numeric keypad.

| ID | Test | Result |
|---|---|---|
| `V-M-01` | Exit the running copy from the tray; confirm no `ptt_dictate` process remains | ✅ pass |
| `V-M-02` | `run_tray.bat`; wait for the tooltip to read `Ready (CUDA)` | ✅ pass — `Loading Model...` shown while loading, as specified |
| `V-M-03` | Hold `A`, then `F5`; each shades while held and clears on release | ✅ pass |
| `V-M-04` | Num Lock off, keypad `7` shades `Home` rather than itself | ⬜ **not run** — no keypad on this machine |
| `V-M-05` | Either `Enter` shades both Enter caps | ⬜ **not run** — no keypad on this machine |
| `V-M-06` | Hold Left Shift, alt-tab away, **release while away**, return: nothing shaded | ✅ pass |
| `V-M-07` | Hold a key, switch to the MODEL tab, **release**, switch back: nothing shaded | ✅ pass |
| `V-M-08` | Click `Right Shift`: status bar flashes `Saved`, banner and tray tooltip both update | ✅ pass |
| `V-M-09` | Click `Right Shift` again: it stays bound, nothing is saved (a chord may never be empty) | ✅ pass |
| `V-M-10` | Click `Left Ctrl`, `Left Alt`, then `Space`: a fourth key replaces the chord | ✅ pass |
| `V-M-11` | Tick **Match either side** with Right Ctrl bound: readout shows `Ctrl` / `["ctrl"]`, both Ctrl caps fill, and the warning changes from Safe to the ordinary-typing warning | ✅ pass |
| `V-M-12` | Click `Left Ctrl` while unsided: narrows to `Right Ctrl`, checkbox clears | ✅ pass |
| `V-M-13` | `Left Win` alone names the Start menu; `Left Alt`+`Left Shift` shows two warnings | ✅ pass |
| `V-M-14` | `large-v3-turbo` is the selected row, reads `Downloaded`, disk figure has no `~` | ✅ pass |
| `V-M-15` | **Delete from disk** reports that it is not implemented and names the path | ✅ pass |
| `V-M-16` | **Measure on this machine**: banner reads `Measuring …`, the cell gains a figure and a bar | ✅ pass — noted that the panel did not explain what Measure was for; wording revised |
| `V-M-17` | Click **CPU**: banner passes `Loading Model...` → `Ready (CPU)`; `config.json` shows `use_gpu: false` | ✅ pass |
| `V-M-18` | Tray menu agrees with the panel, and choosing `Use GPU (CUDA)` there updates the panel's radio | ✅ pass |
| `V-M-19` | Select `tiny.en`: downloads, reloads, reads `Downloaded`; measuring it rescales both bars | ✅ pass |
| `V-M-20` | Task Manager set **Always on top** over the tray corner; hovering the tray draws the popover **over** it | ✅ pass |
| `V-M-21` | With Notepad focused, hover the tray and keep typing: the popover is up and every keystroke lands in Notepad | ✅ pass |
| `V-M-22` | Drag the window to its minimum size: the panel scrolls, keyboard rows do not overlap | ✅ pass |
| `V-M-23` | Exit and relaunch: hotkey, model and device are as left; `future_setting` still in `config.json` | ✅ pass |
| `V-M-24` | In Notepad, hold `Right Ctrl`, speak, release: text pastes at the caret | ✅ pass |
| `V-M-25` | After the warning-box restyle: an unsafe chord shows orange `Warning:` text | ✅ pass — re-run against the restyled build |

**22 passed, 2 not run, 0 failed.**

`V-M-06` and `V-M-07` were first reported as failures. They were not: the instruction did
not say to release the key while the window was out of focus, so the tester returned still
holding it — at which point re-shading is correct. The behaviour was then confirmed
against an instrumented harness across eight cases (in front / behind / tab switched /
window hidden × key held / released), all correct, and the two items re-run and passed.
The lesson is recorded because it cost a round trip: **a manual test step must state every
precondition that changes the expected result, or a correct implementation reads as a
defect.**

---

## 6. Acceptance criteria

The ten criteria are stated in [gui_handoff.md](gui_handoff/gui_handoff.md) §10. Their
status is tracked here.

| # | Criterion | Status |
|---|---|---|
| 1 | Tray icon behaves exactly as today | 🟡 partial — session 1; not re-verified since |
| 2 | Popover raises on hover without stealing focus, **and is in front** | ✅ `V-M-20`, `V-M-21` |
| 3 | Clicking the popover opens the window; banner matches | ✅ session 2 |
| 4 | Any key shades within ~50 ms and unshades on release; alt-tab clears | ✅ `V-M-03`, `V-M-06`, `V-M-07` — except the keypad (`V-M-04`, `V-M-05`) |
| 5 | Clicking `Right Shift` then holding it records, with no restart | ✅ `V-M-08`, `V-M-24`, and `V-EN-01` |
| 6 | GPU→CPU reloads; `config.json` written before the reload; status bar confirms | ✅ `V-M-17` |
| 7 | On a machine without CUDA the GPU toggle is disabled with a visible reason | ⬜ **not verifiable here** — this machine has CUDA. `V-EN-06` covers the engine half |
| 8 | `config.json` round-trips with the current build; unknown keys survive | ✅ `V-CF-02`, and verified against the pre-GUI `config.py` in both directions |
| 9 | No UI object is touched from the engine thread | 🟡 asserted at runtime in `qt_tray.on_state_changed`; no automated test |
| 10 | `build_portable.py` produces a zip that runs on a clean Windows 11 machine | ⬜ **not run** — session 5 |

---

## 7. Not yet verified

Stated rather than omitted. Anything here is a known hole, not an oversight.

| Gap | Why | Owner |
|---|---|---|
| **Pinned-window probe harness** (`tests/tools/probe_paste.py`) | `design.md` §10 step 2. Injects real keystrokes into another process's window to reproduce the issue #11 evidence; cannot run unattended. Its non-negotiable rule: pin a target window handle and refuse to inject unless that window has focus | session 5 |
| Keypad shading (`V-M-04`, `V-M-05`) | No numeric keypad on the test machine | next desktop session |
| Acceptance criterion 7 | Requires a machine without CUDA | — |
| Acceptance criterion 10 | Requires a clean Windows 11 machine | session 5 |
| `FR-C1`, `FR-C4`, `FR-C5`, `FR-2` — insertion behaviour | Behaviours of *another process's* window: menu activation, caret loss, clipboard restoration, UIPI. Not unit-testable; the probe harness is the instrument | session 5 |
| `NFR-1`, `NFR-2`, `NFR-3` — latency and pre-roll | Need real audio hardware and a stopwatch. The Model panel's Measure button is the closest thing and is `V-EN-07` | — |
| `FR-9` — no zombie process on exit | Observable only against a real process tree | manual |
| The `+` registration marks (`gui_handoff` §9) | Not implemented on any panel | session 4 or later |

---

## 8. Change log

| Date | Commit | Change |
|---|---|---|
| 2026-08-23 | `3443a03` | Hotkey and Model panels; `V-M-01` … `V-M-25` executed |
| 2026-08-23 | `0722294` | Unit suite added — 176 tests, `V-HK`, `V-CF`, `V-EN`, `V-TR`, `V-UI`; mutation-checked |
| 2026-08-24 | — | This document created; test material moved out of `design.md` §8 and `development_history.md` |
