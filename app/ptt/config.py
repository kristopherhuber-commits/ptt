"""
Settings persistence: config.json, next to the application (FR-8).

This is the only module that reads or writes the file (design.md section 7).
Nothing else opens it, and nothing else decides what a missing or malformed
value means.

Every field is validated and every fallback is logged with its reason (OBS-3).
A configuration that silently reverts to a default is indistinguishable from one
that was never applied, which is the class of failure the observability
requirements exist to close.

**`FIELDS` is the schema** (`concierge_design.md` section 4.6, D-CG-13). One
declarative table -- type, choices, range, parse, and the prose that describes
each setting -- with three consumers and no second copy of any rule:

- `load()`, whose fallback-with-a-logged-reason path reads it field by field;
- `Settings.set(key, value) -> (ok, reason)`, the validated **write** path
  FR-CG-11 requires, which every writer goes through including the settings
  panels, so the invariant belongs to this object rather than to its callers;
- the Concierge's tool registry, which derives `set_config`'s key enum, each
  tool's argument schema, and the knowledge pack's per-setting half from it.

Before that table existed the rules lived inside `load()` and a write was a bare
`setattr`, so a hallucinated value would have been accepted, saved, and reverted
at the *next* start with a log line nobody was watching -- the "rejection
reported as success" shape FR-CG-11 forbids. This is the `hotkey.KEYS` idiom
(V-HK-01); issue #12 is the recorded case of what a private copy of a derived
table costs.
"""

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, NamedTuple

from ptt import hotkey as hotkey_mod
from ptt import paths
from ptt import transcribe
from ptt import vocabulary as vocabulary_mod
from ptt.logging_setup import log_debug

#: Bumped only when a migration is needed. Files with no `version` key predate
#: versioning and are treated as v1; the key is written on the next save.
CONFIG_VERSION = 1

#: Keys this build owns. Anything else found in the file is preserved verbatim
#: so a newer build's settings survive a rollback.
_KNOWN_KEYS = (
    "version", "use_gpu", "hotkey", "model", "benchmarks",
    "audio_device", "keep_stream_warm", "ignore_short_holds", "start_click",
    "vocabulary", "concierge",
)

#: Serialises writers of config.json. See `Settings.save`.
_save_lock = threading.Lock()


# -- the declarative schema ---------------------------------------------------

class Field(NamedTuple):
    """
    One setting's complete rule, stated once.

    `kind` selects the type check. `"bool"`, `"int"` and `"str"` are checked
    here; `"parsed"` delegates wholly to `parse`, which is how the three
    structured settings reach the module that already owns their grammar --
    `hotkey.parse_chord` for a chord, `vocabulary.parse_rule` per rule -- rather
    than having it restated here.

    `parse` is `(raw, note, strict) -> (value, defect)`. **`strict` is the one
    place load and write legitimately differ**, and it is a disposition rather
    than a second rule: reading a hand-edited file, one malformed vocabulary
    rule is dropped with its own log line and the twenty beside it survive
    (`V-CF-13`); writing through `Settings.set`, the same rule is a *rejection*,
    because a partially-applied write reported as success is precisely what
    FR-CG-11 forbids.

    `does`, `when` and `risk` are the per-setting prose. They live here because
    `build_knowledge_pack.py` generates the pack's per-setting half from this
    table (`concierge_design.md` section 5.05): that half cannot drift from the
    application, because it *is* the application.

    `agent_writable` is the scope exclusion the requirements state as prose.
    "Editing vocabulary rules is out of scope for v3.0" is unenforceable as a
    sentence -- `set_config("vocabulary", ...)` reaches them -- so it is an
    allowlist here instead.
    """
    kind: str
    default: Any
    does: str = ""
    when: str = ""
    risk: str = ""
    choices: tuple = ()
    minimum: int | None = None
    maximum: int | None = None
    nullable: bool = False
    parse: Callable | None = None
    json_type: str = ""
    fallback_note: str = ""
    agent_readable: bool = True
    agent_writable: bool = True
    internal: bool = False

    @property
    def note(self):
        """What the log line says was done instead, when a value is rejected."""
        return self.fallback_note or f"using default {self.default}"

    def schema_type(self):
        """This field's JSON type, for the generated tool schema (design 4.1)."""
        if self.json_type:
            return self.json_type
        return {"bool": "boolean", "int": "integer", "str": "string"}[self.kind]

    def check(self, raw, note="config.json", strict=False):
        """
        Validate one candidate value.

        Returns ``(value, None)`` when it is acceptable, or ``(None, defect)``
        where `defect` is the phrase that follows the key name in the log line
        -- "is not a boolean (0)", "invalid (empty)". Never raises and never
        logs: the caller owns the disposition and therefore owns the message
        (`OBS-3` keeps `config.py` the only module that writes it).
        """
        if raw is None and self.nullable:
            return None, None

        if self.kind == "bool":
            if isinstance(raw, bool):
                return raw, None
            return None, f"is not a boolean ({raw!r})"

        if self.kind == "int":
            if isinstance(raw, bool) or not isinstance(raw, int):
                return None, f"is not an integer ({raw!r})"
            if self.minimum is not None and raw < self.minimum:
                if self.minimum == 0:
                    return None, f"is negative ({raw!r})"
                return None, f"is below {self.minimum} ({raw!r})"
            if self.maximum is not None and raw > self.maximum:
                return None, f"is above {self.maximum} ({raw!r})"
            return raw, None

        if self.kind == "str":
            if not isinstance(raw, str):
                return None, f"is not a string ({raw!r})"
            if self.choices and raw not in self.choices:
                return None, f"{raw!r} is not one of {list(self.choices)}"
            return raw, None

        return self.parse(raw, note, strict)


def _parse_chord(raw, note, strict):
    """A chord, through the module that owns the vocabulary of key names."""
    chord, reason = hotkey_mod.parse_chord(raw)
    if chord is None:
        return None, f"invalid ({reason})"
    return chord, None


def _parse_benchmarks(raw, note="config.json", strict=False):
    """
    Validate the measured-latency cache, dropping entries that make no sense.

    Shape: ``{"<model>|<device>": {"seconds": float, "at": str, "clip": str,
    "llm_resident": bool}}``. `clip` is a digest of the benchmark WAV, so
    re-recording the clip invalidates the numbers taken against the old one
    instead of silently comparing measurements of two different recordings.
    `llm_resident` records whether the Concierge model was in VRAM when the
    figure was taken (`concierge_design.md` section 10 Q23): spike C5 measured a
    1.46x Whisper penalty during active LLM decode, and a contended figure
    sitting in the Model tab beside a clean one looks comparable and is not.

    Per-entry, because one malformed entry someone hand-edited must not throw
    away the twenty beside it. Under `strict` -- the write path -- there is no
    such thing as a partial success, so the first bad entry rejects the write.
    """
    if not isinstance(raw, dict):
        return None, f"is not an object ({raw!r})"

    kept = {}
    for key, entry in raw.items():
        defect = _benchmark_defect(entry)
        if defect:
            if strict:
                return None, f"entry {key!r} {defect}"
            log_debug(f"{note} benchmarks[{key!r}] {defect}; dropping it.")
            continue
        kept[str(key)] = {
            "seconds": float(entry["seconds"]),
            "at": str(entry.get("at", "")),
            "clip": str(entry.get("clip", "")),
            "llm_resident": bool(entry.get("llm_resident", False)),
        }
    return kept, None


def _benchmark_defect(entry):
    """What is wrong with one benchmark entry, or None."""
    if not isinstance(entry, dict):
        return "is not an object"
    seconds = entry.get("seconds")
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
        return f"has no positive numeric 'seconds' ({seconds!r})"
    return None


def _parse_vocabulary(raw, note="config.json", strict=False):
    """
    Validate the replacement rules, dropping the ones that make no sense.

    Per entry, like `_parse_benchmarks` and for the same reason. The validation
    itself lives in `vocabulary.parse_rule`, which is pure and never logs, so
    this is the only place a rejected rule is explained (OBS-3).
    """
    if not isinstance(raw, list) and not isinstance(raw, tuple):
        return None, f"is not a list ({raw!r})"

    kept = []
    for index, entry in enumerate(raw):
        if isinstance(entry, vocabulary_mod.Rule):
            kept.append(entry)
            continue
        rule, reason = vocabulary_mod.parse_rule(entry)
        if rule is None:
            if strict:
                return None, f"rule {index} is invalid ({reason})"
            log_debug(f"{note} vocabulary[{index}] is invalid ({reason}); dropping it.")
            continue
        kept.append(rule)
    return tuple(kept), None


#: What `concierge.opt_in` may say. Three values, not two: `enabled` alone
#: cannot distinguish "never asked" from "said no" from "said yes, currently
#: switched off", and a pre-v3 config.json upgraded in place must arrive
#: `unset` rather than silently opted in (`concierge_design.md` 10, Q26).
OPT_IN_STATES = ("unset", "accepted", "declined")

#: How the Concierge asks the model for a decision. `grammar` constrains the
#: sampler with a generated JSON schema; `native` sends an OpenAI-style tools
#: array and trusts the model's own chat template. Both are generated from one
#: registry (Q15); which one ships is set by gate 2.5's qualification record.
TOOL_MODES = ("grammar", "native")

#: The one Concierge model tier v3.0 qualifies. A 24 GB+ tier is deferred and
#: the key exists so that adding one is configuration, not a code change.
CONCIERGE_MODELS = ("gemma-4-12b-q4_k_m",)


#: The schema. Adding a setting here is the only edit needed to make it
#: validated on load, validated on write, offerable to the Concierge, and
#: documented in the knowledge pack.
#:
#: Dotted keys are nested one level in the file: `concierge.opt_in` is
#: `{"concierge": {"opt_in": ...}}`. The table stays flat because both the
#: `key` enum in the generated tool schema and `Settings.set`'s addressing want
#: one name per setting.
FIELDS = {
    "version": Field(
        "int", CONFIG_VERSION,
        does="Which schema this file was written against.",
        fallback_note=f"treating as {CONFIG_VERSION}",
        agent_readable=False, agent_writable=False, internal=True,
    ),
    "use_gpu": Field(
        "bool", True,
        does="Run Whisper on the NVIDIA GPU (CUDA) rather than the CPU.",
        when="Turn it off if CUDA is unavailable or you want the GPU free for "
             "something else; transcription still works, several times slower.",
        risk="Hardware has the last word. If a CUDA load fails, the engine "
             "forces this to false and saves it, so the setting can change "
             "without anyone touching it (FR-6).",
    ),
    "keep_stream_warm": Field(
        "bool", True,
        does="Hold the microphone stream open between recordings (NFR-2, NFR-4).",
        when="Leave it on. Turning it off releases the device as soon as each "
             "recording ends.",
        risk="Off costs the hardware wake-up latency on every hold, and on a "
             "headset it re-triggers the connection chime that issue #6 exists "
             "to avoid. It does not zero the idle threshold -- the stream is "
             "still released after engine.IDLE_THRESHOLD_SEC of inactivity.",
    ),
    "ignore_short_holds": Field(
        "bool", True,
        does="Discard a hold shorter than engine.MIN_RECORD_SEC as an "
             "accidental tap (FR-3).",
        when="Turn it off if you dictate single words and they are being "
             "swallowed.",
        risk="Off means a brushed key transcribes whatever the microphone "
             "caught. An empty buffer is still never transcribed.",
    ),
    "start_click": Field(
        "bool", False,
        does="Play a short system sound when recording starts.",
        when="Turn it on if you cannot tell whether the hotkey registered.",
        risk="The sound goes to the Windows output device, so an open desktop "
             "microphone can hear it and it lands in the transcript.",
    ),
    "hotkey": Field(
        "parsed", hotkey_mod.DEFAULT_HOTKEY, parse=_parse_chord,
        json_type="array",
        does="The push-to-talk chord: a list of key names from hotkey.KEYS, "
             "held together (FR-4).",
        when="Change it if the default collides with something you use. The "
             "picker offers at most three keys.",
        risk="Detection does not suppress the keypress, so the chord must be "
             "keys that do nothing on their own (FR-C3). Alt opens the target "
             "window's menu bar; Win opens the Start menu; a lone unsided "
             "modifier fires during ordinary typing.",
    ),
    "model": Field(
        "str", transcribe.DEFAULT_MODEL, choices=transcribe.MODEL_NAMES,
        does="Which Whisper size tier transcribes (FR-5).",
        when="Larger is more accurate and slower; large-v3-turbo is near-large "
             "accuracy at about half the time, which is why it is the default.",
        risk="Validated against the catalogue, because an unrecognised name "
             "would be handed to faster-whisper, which tries to fetch it from "
             "Hugging Face by name.",
    ),
    "benchmarks": Field(
        "parsed", {}, parse=_parse_benchmarks, json_type="object",
        agent_writable=False, fallback_note="ignoring it",
        does="Measured transcription latencies, keyed by model and device.",
        when="Written by the Model tab's Measure button, not by hand.",
        risk="A figure taken while the Concierge model was generating is about "
             "1.46x slow and is not comparable with a clean one, so each entry "
             "records llm_resident. Re-recording benchmark_sample.wav changes "
             "the clip digest and invalidates the old numbers rather than "
             "leaving them on screen looking comparable.",
    ),
    "audio_device": Field(
        "int", None, nullable=True, minimum=0,
        fallback_note="using the system default",
        does="PortAudio input-device index, or null to follow the Windows "
             "default device.",
        when="Set it when you want a specific microphone regardless of what "
             "Windows considers default.",
        risk="PortAudio renumbers when a device is plugged in or removed, so a "
             "saved index is re-checked before it is used and falls back to the "
             "default with a reason in the log. Device 0 is a real device, not "
             "'none'. The saved choice is never rewritten, so an unplugged "
             "headset comes back.",
    ),
    "vocabulary": Field(
        "parsed", (), parse=_parse_vocabulary, json_type="array",
        agent_writable=False, fallback_note="ignoring it",
        does="Replacement rules applied to the transcript before it is pasted: "
             "whole-word, case-insensitive, literal.",
        when="Edited on the Vocabulary tab. Editing rules is out of scope for "
             "the Concierge in v3.0, which is why this key is not in its write "
             "allowlist.",
        risk="One pass, so a replacement is never itself replaced; the longest "
             "phrase wins where two could match; ties go in list order. An "
             "unrecognised scope drops the rule rather than widening it.",
    ),
    "concierge.opt_in": Field(
        "str", "unset", choices=OPT_IN_STATES, agent_writable=False,
        does="Whether the first-run Concierge card has been answered: unset, "
             "accepted or declined (FR-CG-6).",
        when="Set by the opt-in card, not by hand.",
        risk="Declined means nothing ever again except the menu entries. A "
             "pre-v3 config.json arrives 'unset', which is what stops an "
             "upgrade opting a user in on their behalf.",
    ),
    "concierge.enabled": Field(
        "bool", True,
        does="The Concierge switch, once opt-in has been accepted.",
        when="Turn it off to stop the runtime starting without forgetting that "
             "you accepted.",
        risk="Off is not the same as declined; see concierge.opt_in.",
    ),
    "concierge.model": Field(
        "str", CONCIERGE_MODELS[0], choices=CONCIERGE_MODELS,
        does="Which qualified Concierge model to run.",
        when="One tier ships in v3.0. The key exists so a 24 GB+ tier is "
             "configuration rather than a code change.",
        risk="A model that has not been through the qualification suite "
             "(NFR-CG-6) has no evidence behind it, so the choices are the "
             "qualified ones only.",
    ),
    "concierge.tool_mode": Field(
        "str", "grammar", choices=TOOL_MODES,
        does="How the Concierge asks the model for a decision: 'grammar' "
             "constrains the sampler with a generated JSON schema, 'native' "
             "sends an OpenAI-style tools array.",
        when="Set by the model's qualification record. Grammar is the "
             "conformance reference and the model-agnostic floor.",
        risk="Native depends on the model's own chat template being good. "
             "Grammar makes malformed calls structurally impossible but leaves "
             "one truncation mode, which the harness routes through its repair "
             "loop.",
    ),
    "concierge.idle_unload_minutes": Field(
        "int", 5, minimum=0, maximum=30,
        does="Minutes since the last message before the Concierge model is "
             "unloaded from VRAM (FR-CG-8).",
        when="0 unloads the moment the chat panel closes. 30 is the maximum.",
        risk="A longer residency holds about 9.4 GB of VRAM. Resident and idle "
             "costs dictation nothing measurable; a cold reload costs the "
             "load plus the knowledge pack prewarm.",
    ),
    "concierge.history_limit": Field(
        "int", 20, minimum=1, maximum=200,
        does="How many saved Concierge transcripts to keep for rereading.",
        when="Raise it if you refer back to old sessions often.",
        risk="Saved transcripts are never fed back to the model -- each "
             "session starts fresh with the knowledge pack and the memory note "
             "(FR-CG-13). They are for you, not for it.",
    ),
}

#: Setting names the Concierge may read, and the subset it may write. Derived,
#: never listed by hand: `concierge_design.md` section 5.05 makes this the
#: settings whitelist too, so a field added above is scored as a real setting
#: rather than as an invention the moment it exists (review 2.9).
READABLE_KEYS = tuple(k for k, f in FIELDS.items() if f.agent_readable)
WRITABLE_KEYS = tuple(
    k for k, f in FIELDS.items() if f.agent_writable and not f.internal
)


def benchmark_key(model_name, device):
    """
    How one measurement is keyed in `benchmarks`.

    Model *and* device: a CPU figure and a CUDA figure for the same model are
    different numbers about different hardware, and showing one where the other
    belongs would be the sort of quiet misreport OBS-3 exists to prevent.

    Here rather than in the Model panel, which is where it was written, because
    the Concierge's `list_models` tool reports the same measurements and may not
    import a module that imports Qt (CON-CG-6). Two copies of a key format is
    exactly the drift `FIELDS` exists to prevent, one level down.
    """
    return f"{model_name}|{device}"


def _split(key):
    """`"concierge.opt_in"` -> `("concierge", "opt_in")`; `"model"` -> `("model",)`."""
    return tuple(key.split("."))


@dataclass
class ConciergeSettings:
    """
    The `concierge` block of config.json (`concierge_handoff.md` section 6).

    A separate object rather than six dotted attributes on `Settings` so that
    the file's nesting and the schema's nesting are the same shape, and so a
    Qt-free harness can be handed the block alone.

    Rebound whole, never mutated: `Settings.set("concierge.enabled", False)`
    builds a **new** instance and rebinds the attribute, which is the same
    discipline `Settings`' docstring states for `hotkey`, `benchmarks` and
    `vocabulary` and for the same reason -- the engine and the harness read this
    object from other threads.

    There is deliberately no `port`. It is pre-bound in Python at every launch
    and recorded in `concierge_state.json`, not configured (design 10, Q13).
    """
    opt_in: str = "unset"
    enabled: bool = True
    model: str = CONCIERGE_MODELS[0]
    tool_mode: str = "grammar"
    idle_unload_minutes: int = 5
    history_limit: int = 20

    #: Unknown keys inside the block, preserved for the same reason
    #: `Settings.extra` preserves them at the top level: a newer build's
    #: settings must survive a rollback (acceptance criterion 8).
    extra: dict = field(default_factory=dict, repr=False)

    def to_dict(self):
        return {
            **self.extra,
            "opt_in": str(self.opt_in),
            "enabled": bool(self.enabled),
            "model": str(self.model),
            "tool_mode": str(self.tool_mode),
            "idle_unload_minutes": int(self.idle_unload_minutes),
            "history_limit": int(self.history_limit),
        }

    def replacing(self, name, value):
        """A new instance with one attribute changed. Never mutates this one."""
        values = {
            "opt_in": self.opt_in, "enabled": self.enabled, "model": self.model,
            "tool_mode": self.tool_mode,
            "idle_unload_minutes": self.idle_unload_minutes,
            "history_limit": self.history_limit, "extra": self.extra,
        }
        values[name] = value
        return ConciergeSettings(**values)


@dataclass
class Settings:
    """
    The live settings object. Exactly one exists per process.

    **Deliberately not frozen, and every field is an immutable value.**

    The engine holds this instance and re-reads `hotkey` on every poll
    iteration, so the chord can be changed while it runs. That is safe only
    because writes are whole-value rebinds -- `settings.hotkey = ("rshift",)`,
    never `settings.hotkey.append(...)`. An attribute rebind is a single
    bytecode, so a reader on another thread sees either the old tuple or the new
    one, never a half-built one. No lock is needed and none should be added.

    Making this frozen, which is the natural instinct, breaks the live re-read
    outright.

    `benchmarks` is a dict rather than a tuple, and the same rule covers it: a
    new measurement builds a **new** dict and rebinds the attribute. Mutating
    the existing one in place would reintroduce exactly the half-built read the
    rebind rule exists to prevent. `vocabulary` is a tuple of
    `vocabulary.Rule`, which is a NamedTuple, so the same discipline is
    enforced by the type rather than only by this docstring -- editing a rule
    builds a new tuple and rebinds it, and the engine's transcription path
    reads whichever tuple is current. `concierge` is a `ConciergeSettings`,
    replaced through `replacing()` for the same reason.

    **`set()` is now the only supported way to write a field**, and it is what
    makes the rebind rule and the validation rule properties of this object
    rather than of whoever happens to be calling. `Settings.save()` has three
    writers now -- the GUI thread on every control, the engine thread on a CUDA
    fallback, and the Concierge's worker thread -- and tool code has never read
    this docstring.

    The lock inside `save` is a different thing entirely and is not the lock
    this docstring forbids: it guards the **file**, which those three threads
    can now reach. It never covers a field read or write, so the live re-read
    stays lock-free.

    What each setting means, when to change it and what can go wrong is in
    `FIELDS`, not in comments here: the knowledge pack is generated from that
    table, and a second copy in this docstring is a second copy of the rule.
    """
    use_gpu: bool = True
    hotkey: tuple = hotkey_mod.DEFAULT_HOTKEY
    model: str = transcribe.DEFAULT_MODEL
    benchmarks: dict = field(default_factory=dict)
    audio_device: int | None = None
    keep_stream_warm: bool = True
    ignore_short_holds: bool = True
    start_click: bool = False
    vocabulary: tuple = ()
    concierge: ConciergeSettings = field(default_factory=ConciergeSettings)

    version: int = CONFIG_VERSION
    extra: dict = field(default_factory=dict, repr=False)
    path: str = field(default_factory=paths.config_path, repr=False)

    # -- reading and writing one field ---------------------------------------

    def get(self, key):
        """
        One setting's current value, addressed the way `FIELDS` names it.

        Raises `KeyError` for a name that is not a field, because a caller
        asking for a setting that does not exist has a bug; the *tool* path
        checks `FIELDS` first and reports a refusal instead.
        """
        if key not in FIELDS:
            raise KeyError(key)
        parts = _split(key)
        value = getattr(self, parts[0])
        for part in parts[1:]:
            value = getattr(value, part)
        return value

    def set(self, key, value):
        """
        Validate one write, apply it, and persist it. Returns `(ok, reason)`.

        This is FR-CG-11. A rejected write changes nothing, saves nothing, and
        comes back with the reason -- which the Concierge surfaces in the chat
        verbatim, and which the log records either way. The alternative the code
        had before this method existed was to accept the value, write it to
        disk, and revert it at the next application start: a rejection reported
        as a success, invisible until a restart.

        `reason` is None on success. On failure it names the key and the defect,
        in the same words `load()` uses for the same value in a hand-edited
        file, because they are the same rule.
        """
        rule = FIELDS.get(key)
        if rule is None:
            reason = f"{key!r} is not a setting"
            log_debug(f"Rejected write: {reason}.")
            return False, reason
        if rule.internal:
            reason = f"{key!r} is not writable"
            log_debug(f"Rejected write: {reason}.")
            return False, reason

        checked, defect = rule.check(value, note="write", strict=True)
        if defect:
            reason = f"{key} {defect}"
            log_debug(f"Rejected write: {reason}.")
            return False, reason

        before = self.get(key)
        self._assign(key, checked)
        self.save()
        log_debug(f"Set {key}: {before!r} -> {checked!r}")
        return True, None

    def override(self, key, value):
        """
        Force one field for **this run only**, without touching config.json.

        The one legitimate caller is hardware having the last word over a saved
        preference: `Engine.__init__` clears `use_gpu` on a machine with no
        CUDA device (FR-6) so that the tray checkmark and the model loader agree
        with the hardware. That is deliberately not a save. A driver that is
        broken this morning must not cost the user the preference they chose,
        and the *load failure* path -- `Engine._persist_cpu_fallback` -- is the
        one that does persist, because a load that actually failed is evidence
        about the machine rather than about the moment.

        Validated exactly as `set` is, so the distinction between the two is
        durability and nothing else. A bare `setattr` from outside this module
        is what this method exists to make unnecessary.
        """
        rule = FIELDS.get(key)
        if rule is None:
            reason = f"{key!r} is not a setting"
            log_debug(f"Refused override: {reason}.")
            return False, reason
        checked, defect = rule.check(value, note="override", strict=True)
        if defect:
            reason = f"{key} {defect}"
            log_debug(f"Refused override: {reason}.")
            return False, reason
        self._assign(key, checked)
        return True, None

    def _assign(self, key, value):
        """
        Rebind one field. Whole-value, never an in-place mutation.

        A dotted key rebuilds its block and rebinds that, so a reader on another
        thread sees either the whole old block or the whole new one.
        """
        parts = _split(key)
        if len(parts) == 1:
            setattr(self, parts[0], value)
            return
        block = getattr(self, parts[0])
        setattr(self, parts[0], block.replacing(parts[1], value))

    # -- the file -------------------------------------------------------------

    def to_dict(self):
        """Serialise, preserving unknown keys. Known keys are written last, so
        they win if a rolled-back build left a colliding value behind."""
        return {
            **self.extra,
            "version": CONFIG_VERSION,
            "use_gpu": bool(self.use_gpu),
            "hotkey": list(self.hotkey),
            "model": str(self.model),
            "benchmarks": dict(self.benchmarks),
            "audio_device": (
                None if self.audio_device is None else int(self.audio_device)
            ),
            "keep_stream_warm": bool(self.keep_stream_warm),
            "ignore_short_holds": bool(self.ignore_short_holds),
            "start_click": bool(self.start_click),
            "vocabulary": vocabulary_mod.to_json(self.vocabulary),
            "concierge": self.concierge.to_dict(),
        }

    def save(self):
        """
        Write config.json. Never raises -- a read-only disk must not take the
        application down mid-dictation.

        Written to a temporary file and moved into place under a lock, rather
        than opened `"w"` and dumped into. Both halves of that matter now that
        every control in the settings window saves the moment it is touched:

        - `"w"` truncates *first*, so a process that died between truncate and
          dump left a zero-byte config.json. `load` handles the garbage
          correctly -- it logs and falls back -- which means the user's symptom
          is their settings silently resetting, the exact failure OBS-3 exists
          to make impossible. `os.replace` is atomic on NTFS: the file is either
          entirely the old contents or entirely the new ones.
        - Three threads can reach this now. The GUI thread writes on every
          click; the engine thread writes on a CUDA fallback; the Concierge's
          worker thread writes through `set`. Interleaved `json.dump` calls into
          one handle produce a file that is neither version.
        """
        tmp = self.path + ".tmp"
        try:
            with _save_lock:
                try:
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(self.to_dict(), f, indent=2)
                    os.replace(tmp, self.path)
                except Exception:
                    # A half-written temp file is no use to anyone and would sit
                    # next to config.json looking like a real one.
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    raise
            log_debug(
                f"Saved config.json: use_gpu={self.use_gpu}, hotkey={self.hotkey}, "
                f"model={self.model}, audio_device={self.audio_device}, "
                f"vocabulary={len(self.vocabulary)}"
            )
        except Exception as e:
            log_debug(f"Failed to save config.json: {str(e)}")


def load(path=None):
    """
    Read config.json, falling back to defaults field by field. Never raises.

    Every fallback is logged with the reason that caused it (OBS-3), and every
    rule comes from `FIELDS` -- the same declaration `Settings.set` validates
    writes against, so a value the file may hold and a value the Concierge may
    write can never mean different things.
    """
    if path is None:
        path = paths.config_path()

    if not os.path.exists(path):
        s = Settings(path=path)
        log_debug(
            f"config.json not found, using defaults (use_gpu={s.use_gpu}, "
            f"hotkey={s.hotkey}, model={s.model})"
        )
        return s

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log_debug(f"Failed to read config.json: {str(e)}; using defaults.")
        return Settings(path=path)

    if not isinstance(raw, dict):
        log_debug(f"config.json is a {type(raw).__name__}, not an object; using defaults.")
        return Settings(path=path)

    s = Settings(path=path)

    block = raw.get("concierge")
    if block is not None and not isinstance(block, dict):
        log_debug(f"config.json concierge is not an object ({block!r}); using defaults.")
        block = None
    block = block or {}

    for key, rule in FIELDS.items():
        parts = _split(key)
        source = raw if len(parts) == 1 else block
        name = parts[-1]
        if name not in source:
            continue
        value, defect = rule.check(source[name], note="config.json")
        if defect:
            log_debug(f"config.json {key} {defect}; {rule.note}.")
            continue
        s._assign(key, value)

    s.extra = {k: v for k, v in raw.items() if k not in _KNOWN_KEYS}
    s.concierge = s.concierge.replacing("extra", {
        k: v for k, v in block.items()
        if f"concierge.{k}" not in FIELDS
    })

    log_debug(
        f"Loaded config.json: use_gpu={s.use_gpu}, hotkey={s.hotkey}, "
        f"model={s.model}, benchmarks={len(s.benchmarks)}, "
        f"audio_device={s.audio_device}, keep_stream_warm={s.keep_stream_warm}, "
        f"ignore_short_holds={s.ignore_short_holds}, start_click={s.start_click}, "
        f"vocabulary={len(s.vocabulary)}, "
        f"concierge={s.concierge.opt_in}/{s.concierge.model}, "
        f"version={s.version}, unknown_keys={sorted(s.extra)}"
    )
    return s
