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
| `V-AU` | microphone capture and device selection | `design.md` §4, `gui_handoff` §6.3 |
| `V-VC` | the replacement-rule vocabulary | `gui_handoff` §6.4 |
| `V-UI` | the GUI's derived logic and data tables | `gui_handoff` §5, §6.1 – §6.6 |
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
| `V-CF-11` | §7 / `gui_handoff` §6.3 — `audio_device`, where **`None` means the Windows default** | The value every pre-GUI config carries by omission loads silently; a string, a float, a `bool` (which is an `int` in Python) and a negative index each fall back with their own reason; **device `0` is a real device**, not a falsy None | `test_config.py::test_a_null_device_means_the_system_default`, `::test_device_zero_is_a_real_device_and_not_a_falsy_none`, `::test_a_non_integer_device_falls_back_and_logs` (5 shapes), `::test_a_boolean_device_falls_back_and_logs`, `::test_a_negative_device_falls_back_and_logs` | `FR-8`, `OBS-3` |
| `V-CF-12` | §7 / `gui_handoff` §6.3 — the three behaviour flags validated **by type** | `"false"` is a truthy string; read naively it would switch `FR-3`'s minimum hold on when the file says off. Each flag round-trips and each non-boolean falls back with its own log line | `test_config.py::test_each_behaviour_flag_round_trips`, `::test_a_non_boolean_flag_falls_back_and_logs` (3 flags × 6 shapes) | `FR-8`, `OBS-3` |
| `V-CF-13` | §7 / `gui_handoff` §6.4 — `vocabulary` validated per rule | One malformed rule is dropped with its own reason and the good ones beside it survive; an **unrecognised scope drops the rule rather than widening it to Always**, which is the one fallback here that deliberately does nothing instead of doing less; order survives a round trip, because two phrases of the same length are applied in list order | `test_config.py::test_a_bad_rule_is_dropped_and_logged` (7 shapes), `::test_a_bad_rule_does_not_take_the_good_ones_with_it`, `::test_an_unknown_scope_is_dropped_rather_than_widened_to_always`, `::test_rule_order_survives_a_round_trip`, `::test_the_vocabulary_is_a_tuple_not_a_list` | `FR-8`, `OBS-3` |
| **`V-CF-14`** | §7 — **every setting added this session defaults to what the build before it did** | A `config.json` from any earlier build names none of the new keys and must behave identically after an upgrade; and a file written by this build keeps `future_setting` beside all ten known keys, with one of each | `test_config.py::test_the_defaults_are_the_behaviour_of_the_build_before_this_one`, `::test_a_file_from_the_pre_gui_build_loads_and_saves_unchanged_in_meaning`, `::test_an_unknown_key_survives_beside_every_setting_this_build_owns` | `FR-8` |
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
| `V-TR-07` | `gui_handoff` §6.4 — **the substitution point** | The vocabulary is applied inside `transcribe_audio`, immediately after `clean_text` — the only point that is genuinely both "after `clean_text`" and "before `paste_text`", because `clean_text` is called from in there. Order is asserted, not assumed: the rule used can only match once the run of full stops has been stripped. No rules is the identity, so the benchmark path measures the dictation path | `test_transcribe.py::test_the_vocabulary_is_applied_inside_transcribe_audio`, `::test_substitution_happens_after_the_cleanup_not_before`, `::test_no_vocabulary_leaves_the_cleaned_text_alone` | `FR-8` |
| `V-TR-08` | `gui_handoff` §6.5 — the inference flags are values, not literals | `BEAM_SIZE`, `VAD_FILTER` and `LANGUAGE` are what is passed to the model, which is what lets the Advanced panel call them the values in force | `test_transcribe.py::test_transcribe_audio_passes_the_flags_the_advanced_panel_reports` | — |
| `V-AU-01` | `gui_handoff` §6.3 — the enumeration | Only capture devices are listed; each row is labelled by name alone, with the host API added only for a device the picker did not offer; a nameless device still gets a label; a failed query reports nothing rather than raising | `test_audio.py::test_only_capture_devices_are_offered`, `::test_a_device_is_labelled_with_its_name_alone`, `::test_a_device_from_another_api_can_be_labelled_with_it`, `::test_a_nameless_device_still_has_a_label`, `::test_enumeration_reports_nothing_rather_than_raising` | `NFR-4` |
| **`V-AU-06`** | `gui_handoff` §6.3 — **the picker offers one host API's copies** | PortAudio reports every device once per Windows audio API — fourteen rows for one array microphone on the test machine, including two `PC Speaker` outputs, a `Stereo Mix` loopback and two "the default device" placeholders. The picker shows one API's copies, drops the placeholders, and **never offers WASAPI** (which cannot open at Whisper's 16 kHz: `Invalid sample rate`) **or WDM-KS**. An unfamiliar machine is offered every real device rather than an empty list | `test_audio.py::test_the_picker_shows_one_host_apis_copies`, `::test_wasapi_is_never_offered`, `::test_kernel_streaming_is_never_offered`, `::test_the_picker_falls_back_through_the_host_apis`, `::test_placeholders_are_not_devices`, `::test_an_unfamiliar_machine_is_offered_everything_real` | — |
| `V-AU-07` | `gui_handoff` §6.3 — nothing is hidden silently, and no name is cut off | The full enumeration is logged once per run with every index and a `[hidden]` marker, so `audio_device` can be set by hand to anything the picker omits. MME truncates names at 31 characters — which is where the popover's `Microphone Array (Intel® Smart ` came from — so a name at exactly that length is expanded from another API's copy, and one that is merely short never is | `test_audio.py::test_the_enumeration_is_logged_once_with_every_index`, `::test_a_truncated_name_is_expanded_from_another_apis_copy`, `::test_a_short_name_is_never_lengthened`, `::test_a_truncated_name_with_no_longer_copy_is_left_alone` | `OBS-3` |
| **`V-AU-02`** | `gui_handoff` §6.3 — **enumerating does not disturb a running stream** | PortAudio reference-counts `Pa_Initialize`, so the picker's query nests inside whatever the open stream holds and leaves the count as it found it. Terminating one time too many would close the stream out from under a recording; one too few leaves PortAudio initialised, which is what stops the machine sleeping (issue #6) | `test_audio.py::test_enumeration_leaves_portaudio_as_it_found_it` | `NFR-4` |
| `V-AU-03` | `gui_handoff` §6.3 — a saved index is checked before it is used | PortAudio renumbers when a device is plugged in or removed, so an index that no longer exists, or that now names an output, falls back to the Windows default **with a reason in the log**. Device `0` is opened rather than read as "no device" | `test_audio.py::test_an_index_that_no_longer_exists_falls_back_and_logs`, `::test_an_index_that_is_now_an_output_device_falls_back_and_logs`, `::test_device_zero_is_opened_and_not_read_as_no_device`, `::test_a_chosen_device_is_the_one_opened` | `OBS-3` |
| **`V-AU-04`** | `gui_handoff` §6.3 — **a device that refuses to open falls back** | PortAudio *lists devices it cannot open* — several WDM-KS entries on the test machine advertise input channels and then fail with `Invalid device`. Without the fallback, choosing one leaves no stream at all: the hotkey does nothing and only the log says why. If nothing opens, PortAudio is released rather than left initialised. The saved choice is never rewritten — an unplugged headset comes back | `test_audio.py::test_a_device_that_refuses_to_open_falls_back_to_the_default`, `::test_nothing_opening_at_all_releases_portaudio`, `::test_a_refused_device_is_not_forgotten` | `FR-8`, `OBS-3` |
| `V-AU-05` | `gui_handoff` §6.3 — the level the meter reads | The callback publishes the block's peak magnitude on a plain attribute; a closed stream reads as silent rather than freezing at the last block; metering did not cost the pre-roll, and a cold start correctly has none to seed from | `test_audio.py::test_the_callback_publishes_the_block_peak`, `::test_the_peak_is_magnitude_not_sign`, `::test_a_closed_stream_reads_as_silent`, `::test_the_callback_still_fills_the_preroll`, `::test_a_cold_start_has_no_preroll_to_seed_from` | `NFR-3` |
| `V-VC-01` | `gui_handoff` §6.4 — a rule is validated field by field | Non-object, missing or non-string `heard`, empty `heard`, non-string `typed`, non-string scope — each rejected with its own reason, and `parse_rule` never raises. An empty replacement is allowed, because deleting a filler word is a real rule. `heard` is normalised on the way in, since Whisper emits single spaces | `test_vocabulary.py` — 8 `parse_rule` tests | `FR-8` |
| `V-VC-02` | `gui_handoff` §6.4 — whole-word, case-insensitive, literal | Case is ignored and the replacement keeps its own; `w s l` does not fire inside `w s lot`; a phrase matches across any whitespace; every occurrence is replaced; metacharacters in the phrase and backreferences in the replacement are literal text | `test_vocabulary.py` — 9 matching tests | `FR-8` |
| **`V-VC-03`** | `gui_handoff` §6.4 — **the three semantics the specification left open** | One pass, so a replacement is never itself replaced and two rules that map into each other terminate; the longest phrase wins wherever two could match, so adding `w s l two` beside `w s l` does not silently never fire; ties go in list order. The ordering is asserted directly as well as through its effect | `test_vocabulary.py::test_a_replacement_is_never_itself_replaced`, `::test_the_longest_phrase_wins_wherever_two_rules_could_match`, `::test_two_phrases_of_the_same_length_go_in_list_order`, `::test_compile_rules_orders_longest_first` | `FR-8` |
| `V-VC-04` | `gui_handoff` §6.4 — substitution never costs the transcript | The user has already said the words; a rule that somehow arrives malformed loses the substitution, not the sentence | `test_vocabulary.py::test_a_broken_rule_returns_the_transcript_rather_than_losing_it` | `OBS-1` |
| **`V-EN-08`** | `gui_handoff` §6.3 — **the input device is re-read live** | The running loop picks up a new device and reopens the stream, and does **not** reload the model — the Audio panel applies with `reload_model=False` and relies entirely on this. A change made *while the hotkey is held* is deliberately not taken up until the recording ends: PortAudio binds the device when the stream is created, so acting on it sooner would leave the two indexes matching and the old stream open indefinitely | `test_engine.py::test_the_loop_picks_up_a_new_input_device_without_restart`, `::test_changing_the_device_reopens_the_stream`, `::test_a_device_chosen_mid_recording_applies_to_the_next_one` | `FR-8` |
| **`V-EN-09`** | `gui_handoff` §6.3 — **the two behaviour flags gate the constants, they do not zero them** | With the warm stream off the device is released between recordings and `rec.start()` opens it for the recording itself, then it is released again — a threshold of zero would instead close and reopen it every poll iteration, which is issue #6 at 50 Hz. With the minimum hold off a short tap is transcribed, but an **empty** buffer never is | `test_engine.py::test_the_warm_stream_holds_the_device_open_between_recordings`, `::test_turning_the_warm_stream_off_releases_the_device_when_idle`, `::test_a_recording_still_works_with_the_warm_stream_off`, `::test_turning_the_minimum_hold_off_transcribes_a_short_tap`, `::test_an_empty_recording_is_never_transcribed`, `::test_the_start_click_plays_only_when_it_is_switched_on` | `FR-3`, `NFR-2`, `NFR-4` |
| `V-EN-10` | `gui_handoff` §6.6 — the diagnostics figures are **kept, not parsed back out of the log** | The engine remembers the transcription time and the paste target it already computed; the history is capped, so the median describes now rather than the whole session; and every accessor tolerates being called before `run()` has built a recorder, which is when the settings window is constructed | `test_engine.py::test_the_engine_remembers_what_the_last_dictation_cost`, `::test_the_latency_history_is_capped`, `::test_the_level_and_device_readouts_tolerate_no_recorder` | `OBS-4` |

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
| `V-UI-11` | §6.3 — the meter is a **dB** scale, not a linear one | Silence reads as the floor rather than `-inf`, full scale is 0 dBFS, anything audible lights at least one bar, and ordinary speech (0.05 – 0.2 peak) lands in the middle rather than as a twitch at the left-hand end — which on a linear bar reads as a broken microphone | `test_panels.py::test_silence_reads_as_the_floor_rather_than_minus_infinity`, `::test_full_scale_is_zero_dbfs`, `::test_a_quiet_signal_is_floored_rather_than_reported_precisely`, `::test_the_meter_is_dark_only_in_silence`, `::test_the_meter_is_full_at_full_scale`, `::test_ordinary_speech_lands_in_the_middle_of_the_meter`, `::test_the_meter_never_overflows_its_bars` | — |
| **`V-UI-12`** | §6.5 — **the Advanced table reads the live constants** | Every row reports the value the engine is actually using, so the page a user consults when they doubt what is in force cannot drift away from it; a constant the Audio tab has switched off says so, so the two panels cannot disagree; the Startup shortcut is reached through `paths`, not by assembling `%APPDATA%` in the panel | `test_panels.py::test_every_advanced_row_reports_the_live_constant`, `::test_the_voice_activity_filter_row_reports_the_flag_inference_uses`, `::test_a_constant_the_audio_tab_has_switched_off_says_so`, `::test_every_advanced_row_says_what_it_is_for`, `::test_the_startup_row_reads_the_shortcut_through_paths` | — |
| `V-UI-13` | §6.6 — the log tail is read from the **end** of the file | The last lines in file order; a short log whole; a long one read through a window rather than in full, since this runs every 1.5 s while the tab is open; the partial first line a byte-offset seek produces is dropped; a missing log is empty rather than an exception; an undecodable byte does not lose the line, because this panel is where you look after a crash | `test_panels.py::test_the_tail_returns_the_last_lines_in_file_order`, `::test_a_short_log_is_returned_whole`, `::test_the_tail_reads_from_the_end_rather_than_the_whole_file`, `::test_a_partial_first_line_is_dropped`, `::test_a_missing_log_is_empty_rather_than_an_exception`, `::test_an_undecodable_byte_does_not_lose_the_line` | `OBS-4` |

---

## 4. Automated tests

**325 tests, 249 test functions, ~4 s.** Last run **2026-08-24** at the start of the
session-5 acceptance pass, on the working tree at commit `840a626`:
**325 passed, 0 failed, in 4.15 s.**

| Module | Tests | Covers |
|---|---:|---|
| `tests/test_config.py` | 91 | `V-CF-01` … `V-CF-14` |
| `tests/test_hotkey.py` | 56 | `V-HK-01` … `V-HK-14` |
| `tests/test_panels.py` | 35 | `V-UI-04` … `V-UI-13` |
| `tests/test_vocabulary.py` | 31 | `V-VC-01` … `V-VC-04` |
| `tests/test_transcribe.py` | 30 | `V-TR-01` … `V-TR-08` |
| `tests/test_statusview.py` | 25 | `V-UI-01` … `V-UI-03` |
| `tests/test_engine.py` | 25 | `V-EN-01` … `V-EN-10` |
| `tests/test_audio.py` | 32 | `V-AU-01` … `V-AU-07` |

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
| Apply vocabulary rules in list order rather than longest-phrase-first | `V-VC-03` | 2 tests failed ✅ |
| Remove the fall back to the default device when the chosen one refuses to open | `V-AU-04` | 1 test failed ✅ |
| Validate the behaviour flags by truthiness instead of by type | `V-CF-12` | 18 tests failed ✅ |
| Offer the whole enumeration in the picker instead of one host API's copies | `V-AU-06` | 3 tests failed ✅ |
| Stop expanding MME's truncated device names | `V-AU-07` | 1 test failed ✅ |

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

### 5.2 Session 4 — Audio, Vocabulary, Advanced and Diagnostics · 2026-08-24

Two kinds of item here, and the difference matters when reading the results.

`V-M-26` … `V-M-35` were run **instrumented**: a script builds the real widgets under a
real `QApplication` with a stub engine, drives the controls through the same handlers a
click reaches, and reads back `config.json`; the audio ones drive the real `Recorder`
against this machine's real PortAudio. They are not hand tests and they are not automated
either — nothing re-runs them — but they exercise Qt and PortAudio, which the suite in
section 4 deliberately does not.

`V-M-36` onward were run **by hand** against the running tray application, on the same
laptop as session 3 — one physical microphone, no numeric keypad. Six of them are
still outstanding and say so.

| ID | Test | Result |
|---|---|---|
| `V-M-26` | Render all six tabs at the default 880 × 800 and again at the stated minimum 820 × 620; no tab scrolls horizontally | ✅ pass — **after a fix.** The device combo sized itself to its longest entry (a 70-character WDM-KS name), giving the Audio panel a 1003 px minimum and a horizontal scrollbar under every tab |
| `V-M-27` | Toggle each Audio checkbox; `config.json` gains the key with the new value | ✅ pass — `keep_stream_warm: false`, `start_click: true`, `future_setting` still present |
| `V-M-28` | Choose a specific device in the picker; `audio_device` is written and no model reload is requested | ✅ pass — `audio_device: 24`, engine untouched |
| `V-M-29` | With **Keep the stream warm** cleared, the Advanced tab's *Release microphone when idle* row | ✅ pass — reads `240 s · bypassed from the Audio tab` |
| `V-M-30` | Add, edit, delete and undo a vocabulary rule; each writes `config.json` | ✅ pass — including the deleted rule returning to its own index |
| `V-M-31` | Edit a Heard cell to whitespace only | ✅ pass — the edit is refused and the cell keeps its text |
| `V-M-32` | Load a `config.json` naming device 9999 and open the Audio tab | ✅ pass — combo reads `Device 9999 — not connected` rather than silently showing the fallback as the choice |
| `V-M-33` | Open a real stream on the default device, on a listed WDM-KS device, and on an absent index | ✅ pass — **after a fix.** Device 24 is listed with two input channels and then fails with `Invalid device [PaErrorCode -9996]`; before the fallback the app had no stream at all. See `V-AU-04` |
| `V-M-34` | Enumerate devices while a stream is open | ✅ pass — 14 devices returned, the stream stayed open and kept capturing |
| `V-M-35` | Round-trip `config.json` through the **pre-GUI** `config.py` (`2a0a018`): this build writes all ten keys, the old build loads it, changes the hotkey and saves, this build reads it back | ✅ pass — the old build loaded it with no warnings, kept all five new keys plus `future_setting` as unknowns, and every setting survived the return trip |
| `V-M-36` | In Notepad, hold Right Ctrl, speak, release | ✅ pass — text pastes at the caret. The regression check on this session's changes to the poll loop's stream lifecycle and its transcription call |
| `V-M-37` | Add a rule, dictate a sentence containing the phrase | ✅ pass — the replacement is what lands, not what was said |
| `V-M-38` | Open **Audio** and speak at normal volume | ✅ pass — the bars track speech and fall away in the gaps; the dBFS readout is legible. The dB scale reads correctly at ordinary speaking level |
| `V-M-39` | Pick a specific microphone, dictate, and confirm from the log that the transcript came from that device | 🟡 **partial.** Six entries were each selected and dictated through against the *unfiltered* fourteen-row list, and all six recorded and pasted. That is weaker evidence than it looks: at least one entry on that list (`24`, WDM-KS `Input ()`) cannot be opened, and `V-AU-04`'s fallback means a device that refuses to open still dictates — successfully, from the default. **Only the log distinguishes the two.** What is confirmed is that selecting a non-default index never breaks dictation; what is not is that a chosen device was the one recorded from |
| `V-M-49` | Reduce the picker to one row per microphone and confirm what it offers | ✅ pass — fourteen entries became one, with the full 72-character name rather than MME's 31-character truncation; a hand-set hidden index still displays, labelled with its host API |
| `V-M-40` | Open **Diagnostics** after two or three dictations | ✅ pass — median latency and paste target both show figures, and the tail follows new lines |
| `V-M-41` | Hover the tray icon | ✅ pass — the popover's `Microphone` row names the device and `Last` shows the duration and word count. Both had shown an em dash since session 2; the word count is right and the timing looks right |
| `V-M-42` | Maximise the window and visit each tab | ✅ pass — no horizontal scrollbar on any tab. **Not the same test as `V-M-26`**: the minimum-size case for the four new tabs is still covered only by that programmatic check, and by `V-M-22` for the two older ones |
| `V-M-43` | Tick **Play a click when recording starts**, hold the hotkey, speak, release | ✅ pass — the sound plays and the transcript carries no spurious words from it. The open-microphone concern that kept it off by default did not materialise here |
| `V-M-44` | Clear **Keep the stream warm**, wait, then dictate | ⬜ **not run** |
| `V-M-45` | Clear **Ignore holds shorter than 0.30 s**, tap the hotkey, then tap it with no speech at all | ⬜ **not run** |
| `V-M-46` | Press **Open log folder**, then **Reload model** | ⬜ **not run** |
| `V-M-47` | Unplug the chosen microphone while the app runs, then dictate | ⬜ **not run** |
| `V-M-48` | Exit and relaunch: device, checkboxes and rules are as left; `future_setting` still in `config.json` | ⬜ **not run** |

**18 passed, 1 partial, 5 not run, 0 failed.** Both defects found this session were found by the
instrumented run and are fixed; neither would have been caught by the suite in section 4,
because one is a layout consequence of real device names and the other needs a device
that exists, advertises input channels, and refuses to open.

The five that carry the most weight all passed: dictation still works after the poll
loop changed (`V-M-36`), a rule reaches the clipboard through the real model and the real
paste (`V-M-37`), and the two judgement calls the instrumented run could not settle — the
meter's dB scale and whether an open microphone hears the start click — both came out the
way the design assumed (`V-M-38`, `V-M-43`).

---

### 5.3 Session 5 — the acceptance pass · 2026-08-24

Everything in this section was run **instrumented**, in the sense section 5.2 defines: a
script builds the real widgets under a real `QApplication`, drives them through the same
handlers a click reaches, and reads back `config.json` — but here the Win32 side is real
too. Keys are injected with `keybd_event`, the same call `inject.py` makes, and read back
through `GetAsyncKeyState`, the same call the panel polls; focus and Z-order are read from
`GetForegroundWindow` and `GWL_EXSTYLE`. **Nothing in this section was run by hand.** What
still needs a person or different hardware is listed in section 7 and named in the
criterion rows in section 6.

Build: working tree at commit `840a626`. Hardware: laptop, CUDA present, one physical
microphone, no numeric keypad.

| ID | Test | Result |
|---|---|---|
| `V-M-50` | Render all four state icons and compare them byte-for-byte with `tray.py::create_icon_image` from the last commit that had it (`0f70a76^`) | ✅ pass — all four identical, and distinct from each other. The five `QIcon` frames match the frame sizes PIL's ICO writer actually emits for a 64 px source (16, 24, 32, 48, 64), which is what `ICON_SIZES` claims |
| `V-M-51` | Drive `on_state_changed` through six state/status pairs and read the tooltip back | ✅ pass — `PTT Dictation (<status>)` in every case, and an empty status falls back to the capitalised state exactly as the pystray tray did |
| `V-M-52` | Enumerate the built `QMenu` and compare it item by item with the pystray menu | 🟡 **partial** — every pystray item is present, in order, with the same enabled and checkable flags. The Qt menu adds `Settings…`, which `gui_handoff` §4 requires. It does **not** have `Pause`, which §4 also lists; see section 7 |
| `V-M-53` | Inspect and run `QtTray._on_exit` | ✅ pass — `engine.stop()` is called, the icon is hidden, `QApplication.quit()` follows, and there is no `join` on the engine thread anywhere in the method |
| `V-M-54` | Put a text box in the foreground, raise the popover over it, then inject `X`, `Y`, `Z` — with a control run first, injecting with no popover up | ✅ pass — control typed `xyz`; with the popover visible the foreground window was unchanged, `isActiveWindow()` on the popover was False, the text box kept Qt focus, and it received `xyz` again |
| `V-M-55` | Launch a second **process** whose window is `WindowStaysOnTopHint`, overlap the popover with it, and read the Z-order | ✅ pass — `GWL_EXSTYLE` on the popover carries `WS_EX_TOPMOST`, and `EnumWindows` (which walks front to back) returns the popover ahead of the rival window |
| `V-M-56` | Compare all nine displayed fields of the popover's `StatusView` with the window's banner, then send a `MouseButtonPress` to the popover | ✅ pass — every field identical, footer on the popover only; the click hides the popover and shows the window, and `show_at_tray()` afterwards leaves it hidden |
| `V-M-57` | Emit `EngineBridge.on_state` from a worker thread and record which thread each end runs on | ✅ pass — the emit side is not the GUI thread, the slot side is, and the two `QThread` pointers differ. The `assert` in `QtTray.on_state_changed` did not fire, the tooltip updated, and both `THREAD-CHECK` lines were written |
| `V-M-58` | Inject ten real keys — including `Keypad 7`, `Keypad +`, `Home` and both `Enter`s — into the visible, active Hotkey panel and time the shade and the unshade | ✅ pass — every key shaded in 2–33 ms and unshaded in 29–34 ms, against a criterion of ~50 ms and a 30 ms poll. Both `Enter` caps shade together. **Synthetic keys, so this does not settle `V-M-04`/`V-M-05`**: what a physical keypad reports with Num Lock off is a property of the keyboard, not of this code |
| `V-M-59` | With a key held, switch tab, alt-tab to another window, and hide the window | ✅ pass — `_held` is empty after each, and the chord was unchanged by all the injecting |
| `V-M-60` | Click `Right Shift`, then click `Right Ctrl` to reduce the chord to Right Shift alone, then hold the real key and call `hotkey.chord_held` | ✅ pass — clicking **adds** a key rather than replacing (`('rctrl',)` → `('rctrl','rshift')` → `('rshift',)`), which is `V-M-10`'s documented behaviour; `config.json` and the readout agree; **no model reload was requested at any point**; clicking the last remaining key is a no-op; and `chord_held` is False/True/False as the real key goes down and up |
| `V-M-61` | Click **CPU** on the Model panel with a stub engine that reads `config.json` at the instant `request_model_reload` is called | ✅ pass — the file already said `use_gpu: false` when the reload was requested, so the order `InstantApplyPanel.apply_now` promises holds; the status bar showed `Saved · HH:MM:SS`; switching back requested exactly one more reload |
| `V-M-62` | Build the Model panel with `cuda_supported=False`, and construct a real `Engine` the same way | 🟡 **simulated** — the GPU radio is disabled, CPU is selected, and the reason reads "No CUDA device was found, so this build runs on the CPU. See the Diagnostics tab."; `Engine(cuda_supported=False)` forced `use_gpu` to False and the saved `config.json` says so. This is the software half only; see section 7 |
| `V-M-63` | Copy the live `app/config.json`, `load()` it, `save()` it, and diff | ✅ pass — 11 keys in, 11 keys out, no value changed, `future_setting` preserved, a second `load()` identical to the first, and **zero fallback warnings logged** |
| `V-M-64` | Run `python build_portable.py` and enumerate the archive | ✅ pass — 8531 entries, 1461.78 MB. `app/assets/` ships whole: the four registered faces, all 36 TTFs, **both `OFL.txt` files**, `style.qss` and `benchmark_sample.wav` (960 044 bytes). No `tests/`, no `requirements-dev.txt`, no `requirements.txt`, no `pyproject.toml`, no `docs/`, no `app/config.json`, no `debug_log*`, no `pyvenv.cfg`, no `__pycache__`. `app/ptt/ui/tray.py` is gone. **But `.venv` still carries `pystray` and `six`** — see section 7 |
| `V-M-65` | Extract the archive to a fresh directory and launch `.venv\Scripts\ptt_dictate.exe app\ptt_tray.py` from it | ✅ pass — 8531 files out, no extraction errors. The shipped copy registered all four bundled Barlow faces, loaded `style.qss` (19 287 chars) from its own `app/assets/`, resolved the CUDA DLL directories from its own `.venv`, loaded `large-v3-turbo` on CUDA in 5 s, opened the input stream, showed the tray icon on the first attempt, and wrote both correct `THREAD-CHECK` lines. It created `config.json` from defaults, confirming the runtime-artifact exclusion |

**13 passed, 3 partial, 0 failed.**

Two things the build surfaced that are worth keeping.

**The interpreter copies were skipped.** `build_portable.py` copies six files out of the
base Python into `.venv\Scripts`, and all six were locked by the running application.
The script's `PermissionError` branch warned and carried on. That was harmless *this
time* — `ptt_dictate.exe`, `python.exe` and `python314.dll` in `.venv` hash identical to
the base install's — but it is only harmless while the existing copies are already
current. **Close the application before building a release**, or a Python upgrade will
ship the old interpreter beside the new standard library.

**Long paths.** The longest entry in the archive is 175 characters
(`.venv/Lib/site-packages/PySide6/qml/…/qrc_qmake_Qt_labs_assetdownloader_init.cpp.obj`),
so extracting under a directory longer than about 80 characters exceeds `MAX_PATH`. A
normal `Downloads` folder is well inside that; a deep temporary directory is not.

---

## 6. Acceptance criteria

The ten criteria are stated in [gui_handoff.md](gui_handoff/gui_handoff.md) §10. Their
status is tracked here. Session 5 worked through all ten; the evidence is section 5.3
unless another item is named.

| # | Criterion | Status |
|---|---|---|
| 1 | Tray icon behaves exactly as today | 🟡 **partial.** Glyphs, colours, sizes, tooltip and the non-joining Exit are verified identical to the pystray build (`V-M-50`, `V-M-51`, `V-M-53`), and `qt_tray.py` has not changed since `V-M-01`/`V-M-02` ran against it by hand. The **menu is a superset**: it adds `Settings…`, which `gui_handoff` §4 requires, and omits `Pause`, which §4 also lists and which has never existed (`V-M-52`) |
| 2 | Popover raises on hover without stealing focus, **and is in front** | ✅ Both halves measured together, as §10 asks. Focus: the foreground window is unchanged, the popover is never activated, and injected keystrokes all land in the other window (`V-M-54`). Z-order: `WS_EX_TOPMOST` is set and the popover enumerates ahead of a **different process's** always-on-top window (`V-M-55`). Confirmed by hand in session 3 as `V-M-20`, `V-M-21` |
| 3 | Clicking the popover opens the window; banner matches | ✅ `V-M-56` — all nine displayed fields identical, because both hosts embed one `StatusView` fed from one `UiState`. Also session 2 by hand |
| 4 | Any key shades within ~50 ms and unshades on release; alt-tab clears | ✅ `V-M-58`, `V-M-59` — real injected keys, 2–33 ms to shade, against a 30 ms poll; tab switch, alt-tab and close each clear everything. Also `V-M-03`, `V-M-06`, `V-M-07` by hand. **The physical keypad with Num Lock off is still not covered** — injection cannot answer it (`V-M-04`, `V-M-05`) |
| 5 | Clicking `Right Shift` then holding it records, with no restart | ✅ `V-M-60` — the click writes the chord and requests no reload; the real detector fires on the real key. The running loop picking the new chord up without a restart is `V-EN-01`, which drives `Engine.run()` on a thread. Also `V-M-08`, `V-M-24` by hand. **What is not automated is the audio**: speaking and seeing text pasted |
| 6 | GPU→CPU reloads; `config.json` written before the reload; status bar confirms | ✅ `V-M-61` proves the ordering directly — the stub engine read the file at the moment it was asked to reload and found the new value already there. The banner passing `Loading Model...` → `Ready (CPU)` was seen by hand as `V-M-17` |
| 7 | On a machine without CUDA the GPU toggle is disabled with a visible reason | ⬜ **not verifiable here** — this machine has CUDA. The software half passes simulated (`V-M-62`); `V-EN-06` covers the engine half |
| 8 | `config.json` round-trips with the current build; unknown keys survive | ✅ `V-M-63` against the live file, with no warnings logged; `V-CF-02`, `V-CF-14`; and verified against the pre-GUI `config.py` in both directions as `V-M-35` |
| 9 | No UI object is touched from the engine thread | ✅ **upgraded from asserted to measured.** `V-M-57` records the two thread identities on either side of the queued hop and they differ; every one of the three bridge signals is connected with an explicit `QueuedConnection`. The runtime `assert` in `qt_tray.on_state_changed` and the paired `THREAD-CHECK` log lines both stand, and the shipped build wrote them too (`V-M-65`) |
| 10 | `build_portable.py` produces a zip that runs on a clean Windows 11 machine, and `install.bat` still creates both shortcuts | 🟡 **partial.** The archive builds, extracts and runs — model on CUDA, bundled fonts and stylesheet, tray icon (`V-M-64`, `V-M-65`) — but **on this machine, which is not a clean one**, and `install.bat` was not run. Both remain; see section 7 |

---

## 7. Not yet verified

Stated rather than omitted. Anything here is a known hole, not an oversight.

| Gap | Why | Owner |
|---|---|---|
| **Pinned-window probe harness** (`tests/tools/probe_paste.py`) | `design.md` §10 step 2. Injects real keystrokes into another process's window to reproduce the issue #11 evidence; cannot run unattended. Its non-negotiable rule: pin a target window handle and refuse to inject unless that window has focus. Session 5's probes inject keystrokes but only ever into a window this process owns, and each one checks it holds the foreground first — that is the same discipline at a smaller scale, not the harness | next session |
| `install.bat` on the built archive (criterion 10, second clause) | Not run. It stops every `ptt_dictate` process, deletes and rewrites `%LOCALAPPDATA%\Programs\ptt_dictate`, replaces the Desktop and Startup shortcuts and relaunches — so running it during a verification pass would overwrite the working installation. `install.ps1` is byte-identical to `main` and predates the GUI work | manual — see below |
| Criterion 10 on a **clean** Windows 11 machine | The archive was extracted and run on the machine that built it, which already has CUDA, a Python 3.14 install and the Hugging Face model cache. What is untested is a box with none of those | needs a second machine |
| Criterion 7 | Requires a machine without a CUDA device. `V-M-62` covers the software half by construction | — |
| Keypad shading with Num Lock off (`V-M-04`, `V-M-05`) | No numeric keypad on the test machine. `V-M-58` injects `Keypad 7` and `Keypad +` and both shade, but a synthetic key cannot answer what the OS reports for a **physical** keypad `7` with Num Lock off — which is the whole question | next desktop session |
| `Pause` in the tray menu (`gui_handoff` §4) | Listed in §4, absent from every build, and never in `pystray`'s menu either. `stage0_review.md` §3.2 flagged it before session 1 as decision 2 of 5 and it was never answered. It needs no engine change — `Engine.__init__` already takes a `chord_held` seam, so a frontend passing `lambda chord: (not self._paused) and hotkey_mod.chord_held(chord)` gets it for free. **Either build it or strike it from §4** | decision |
| `pystray` and `six` still ship inside `.venv` | `requirements.txt` dropped `pystray` and `app/ptt/ui/tray.py` is deleted, but `pip install -r` never uninstalls what a requirement removed, so 22 files and ~143 KB of a library nothing imports are in the archive. Harmless but wrong: the distribution should contain what `requirements.txt` says. Fixing it means rebuilding `.venv` from scratch, which is also the only way to prove the pinned set is complete | next release build |
| The end of criterion 5: dictation through the rebound chord | `V-M-60` proves the click, the write and the detector. What no probe can supply is a voice. `V-M-24` and `V-M-36` cover it by hand for `Right Ctrl` | manual |
| `FR-C1`, `FR-C4`, `FR-C5`, `FR-2` — insertion behaviour | Behaviours of *another process's* window: menu activation, caret loss, clipboard restoration, UIPI. Not unit-testable; the probe harness is the instrument | next session |
| `NFR-1`, `NFR-2`, `NFR-3` — latency and pre-roll | Need real audio hardware and a stopwatch. The Model panel's Measure button is the closest thing and is `V-EN-07` | — |
| `FR-9` — no zombie process on exit | Observable only against a real process tree. Session 5 terminated the extracted instance and confirmed no `ptt_dictate` survived, but that was `TerminateProcess`, not the app's own Exit path | manual |
| The `+` registration marks (`gui_handoff` §9) | Not implemented on any panel | later |
| **That a chosen device is the one recorded from** (`V-M-39`) | One physical microphone on the test machine, so every entry sounds identical — and `V-AU-04`'s open-time fallback means an unopenable device still dictates, from the default. Only `debug_log.txt` says which device each actually used. Needs a second physical microphone to settle | next desktop session |
| The warm stream switched off (`V-M-44`), the minimum hold switched off (`V-M-45`), the two Diagnostics buttons (`V-M-46`), unplugging the chosen device (`V-M-47`), and persistence across a restart (`V-M-48`) | Not run in session 4 and not run in session 5. Each is covered by the suite in section 4 at the engine level — `V-EN-09`, `V-AU-04`, `V-CF-14` — so what is missing is the behaviour of the real application, not the logic | next desktop session |
| The four new tabs at the window's **minimum** size | `V-M-42` was run maximised. The minimum-size case is covered programmatically by `V-M-26` and, for the two older tabs only, by hand in `V-M-22` | next desktop session |
| Per-application vocabulary scopes | `gui_handoff` §11 puts them out of scope for the first pass. The field is stored and validated and one value is accepted; a rule with any other scope is dropped and logged, so nothing silently applies more widely than it was written | — |
| Making an Advanced value editable | Every one of them fixed a documented failure, so none is exposed. §6.5's rule stands: exposing one makes it a validated `Settings` field with a logged fallback, and `Shift+Insert` additionally has to warn on change | — |
| "Start with Windows" as a control rather than a readout | Setting it means creating a `.lnk` through COM and re-applying `install.ps1`'s run-as-admin byte patch — the installer's logic, duplicated in the app | — |

### What a person has to do

Four things, in this order. The first two close criterion 10; the last two close the
keypad and the no-CUDA holes and need hardware this machine does not have.

1. Exit the running PTT Dictation from its tray icon. Confirm in Task Manager that no
   `ptt_dictate.exe` remains. *(This is also `V-M-01`, re-run against the current build.)*
2. Extract `ptt_dictate_dist.zip` somewhere short — `%USERPROFILE%\Downloads\ptt` — and
   double-click `install.bat`. Accept the UAC prompt. Confirm: a **PTT Dictation**
   shortcut on the Desktop, a second one in
   `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup`, both with **Run as
   administrator** ticked in Properties → Shortcut → Advanced, and the app relaunching by
   itself into the tray.
3. On a machine with a numeric keypad: open Settings → Hotkey, turn Num Lock **off**, and
   press keypad `7`. It must shade the `Home` cap, not the keypad `7` cap. Press either
   `Enter`; both Enter caps must shade.
4. On a machine with no NVIDIA GPU: launch the app and open Settings → Model. The
   **GPU (CUDA)** radio must be greyed out with the reason beside it, **CPU** must be
   selected, and `app/config.json` must say `"use_gpu": false`.

---

## 8. Change log

| Date | Commit | Change |
|---|---|---|
| 2026-08-24 | — | The acceptance pass. All ten criteria worked through; `V-M-50` … `V-M-65` executed instrumented, 13 passing and 3 partial; suite re-run at 325 passed; the distribution rebuilt, extracted and launched. Criterion 9 upgraded from asserted to measured. Three new holes recorded in section 7: `Pause` was never built, `pystray` still ships inside `.venv`, and `install.bat` has not been run against the archive |
| 2026-08-24 | `840a626` | Audio, Vocabulary, Advanced and Diagnostics panels. `V-CF-11` … `V-CF-14`, `V-TR-07`, `V-TR-08`, `V-AU-01` … `V-AU-05`, `V-VC-01` … `V-VC-04`, `V-EN-08` … `V-EN-10`, `V-UI-11` … `V-UI-13` added; suite 176 → 325; three more mutations checked; `V-M-26` … `V-M-49` executed, 18 of 24 passing; the device picker reduced from fourteen rows to one after review |
| 2026-08-23 | `3443a03` | Hotkey and Model panels; `V-M-01` … `V-M-25` executed |
| 2026-08-23 | `0722294` | Unit suite added — 176 tests, `V-HK`, `V-CF`, `V-EN`, `V-TR`, `V-UI`; mutation-checked |
| 2026-08-24 | — | This document created; test material moved out of `design.md` §8 and `development_history.md` |
