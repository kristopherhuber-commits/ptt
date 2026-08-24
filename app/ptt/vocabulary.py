"""
Replacement rules applied to a transcript before it is pasted (FR-8, gui_handoff
section 6.4).

Pure: no Qt, no config file, no model, no Win32. The same shape as
`hotkey.parse_chord` and `transcribe.clean_text`, and for the same reason --
every rule of the matching semantics is testable without a microphone, and
`ptt.config` stays the only module that writes the OBS-3 line explaining a
rejected rule.

Where the substitution runs
--------------------------

Section 6.4 places it "**after** `transcribe.clean_text` and **before**
`inject.paste_text`". `clean_text` is not a step a frontend can wrap -- it is
called from inside `transcribe_audio` -- so the only point that is genuinely
between those two functions is inside `transcribe_audio` itself, immediately
after the cleanup. That is where `apply_rules` is called from, and it is also
the answer to the question section 6.4 left open: the text handed to `on_text`
is the text that gets pasted. A console frontend printing one string while the
clipboard received another is exactly the "the log said it worked" failure OBS-1
exists to close.

Matching
--------

Whole-word and case-insensitive, as section 6.4 requires. Three further rules
the specification left open (stage0_review section 5.12) are settled here,
because a substitution whose result depends on something the user cannot see is
worse than no substitution:

1. **One pass over the transcript.** Every rule is compiled into a single
   alternation, so a replacement's output is never rescanned. Rules cannot
   chain (`a`->`b` followed by `b`->`c` does not produce `c`) and cannot loop.
2. **The longest phrase wins.** Where two rules could match at the same place,
   the one matching more words is applied; ties go to the earlier rule. Without
   this, adding `w s l two`->`WSL2` beside an existing `w s l`->`WSL` would
   silently never fire, and there is no reordering control in this pass that
   would let the user fix it.
3. **The replacement is literal.** `\\n` in the Typed column is a backslash and
   an `n`, not a newline. The mockup shows a `new paragraph` -> `\\n\\n` rule,
   which would need an escape mini-language and an escaping rule for a literal
   backslash; that is a language, not a setting, and it is not in this pass.

Word boundaries are `(?<!\\w)` and `(?!\\w)` rather than `\\b`, so a phrase that
starts or ends with punctuation still matches at the ends of a word rather than
inverting its meaning the way `\\b` does there. Runs of whitespace inside a
phrase match any whitespace, because a rule typed with two spaces between words
would otherwise never match anything Whisper produces.
"""

import re
from typing import NamedTuple

#: The only scope this build honours. gui_handoff section 6.4 describes a scope
#: column of "Always / specific app classes", and section 11 puts
#: per-application behaviour rules out of scope for the first pass. So the
#: column exists, the field is stored and validated, and exactly one value is
#: accepted -- see `parse_rule` for why an unrecognised scope drops the rule
#: instead of widening it to Always.
SCOPE_ALWAYS = "always"

#: Every scope a stored rule may carry.
SCOPES = (SCOPE_ALWAYS,)


class Rule(NamedTuple):
    """
    One replacement.

    `typed` may be empty: deleting a filler word is a legitimate rule, and
    "replace with nothing" is how it is written.

    Immutable, which is not incidental. `Settings.vocabulary` is a tuple of
    these and the engine re-reads it on the transcription path while the
    settings window writes it -- `config.Settings`' docstring requires every
    field to be a value that is replaced wholesale rather than mutated, and a
    NamedTuple in a tuple cannot be edited in place even by accident.
    """
    heard: str
    typed: str
    scope: str = SCOPE_ALWAYS


def normalise_phrase(text):
    """
    The spoken phrase as it will be matched: stripped, with runs of whitespace
    collapsed to one space.

    Stored in this form as well as matched in it, so the table shows what the
    rule actually does rather than what was typed into it.
    """
    return " ".join(str(text).split())


def parse_rule(value):
    """
    Validate one stored rule.

    Returns ``(Rule, None)`` on success or ``(None, reason)`` on failure, the
    same contract `hotkey.parse_chord` has and for the same reasons: it never
    logs and never raises, so `ptt.config` owns the OBS-3 line and this stays
    testable without a filesystem.

    An unrecognised `scope` **drops the rule** rather than falling back to
    Always. Every other fallback in this codebase widens nothing -- it restores
    a default that does less. Coercing a future build's `{"scope": "editors"}`
    to Always would take a rule the user scoped to one application and apply it
    everywhere, which is a behaviour change dressed as a default.
    """
    if not isinstance(value, dict):
        return None, "not an object"

    heard = value.get("heard")
    if not isinstance(heard, str):
        return None, f"heard is not a string ({heard!r})"
    heard = normalise_phrase(heard)
    if not heard:
        return None, "heard is empty"

    typed = value.get("typed", "")
    if not isinstance(typed, str):
        return None, f"typed is not a string ({typed!r})"

    scope = value.get("scope", SCOPE_ALWAYS)
    if not isinstance(scope, str):
        return None, f"scope is not a string ({scope!r})"
    scope = scope.strip().lower()
    if scope not in SCOPES:
        return None, f"scope {scope!r} is not one of {list(SCOPES)}"

    return Rule(heard, typed, scope), None


def to_json(rules):
    """Serialise rules for config.json. `config.Settings.to_dict` calls this."""
    return [{"heard": r.heard, "typed": r.typed, "scope": r.scope} for r in rules]


def _phrase_pattern(heard):
    """
    One phrase as a whole-word, whitespace-tolerant regular expression.

    The phrase itself is escaped, so nothing a user types into the Heard column
    can be a metacharacter -- a rule of `c++` is three literal characters, not a
    quantifier applied to nothing.
    """
    words = r"\s+".join(re.escape(word) for word in heard.split())
    return rf"(?<!\w)({words})(?!\w)"


def compile_rules(rules):
    """
    One compiled pattern for every rule, and the replacements it selects between.

    Returns ``(pattern, replacements)`` or ``(None, ())`` when there is nothing
    to match. Split out from `apply_rules` so the ordering -- longest phrase
    first, ties by position in the list -- is inspectable by a test rather than
    only observable through its effect on a sentence.

    `sorted` is stable, which is the whole of the tie-break: two phrases of the
    same length come out in the order they were written.
    """
    ordered = sorted(rules, key=lambda rule: -len(rule.heard))
    if not ordered:
        return None, ()
    pattern = re.compile(
        "|".join(_phrase_pattern(rule.heard) for rule in ordered), re.IGNORECASE
    )
    return pattern, tuple(rule.typed for rule in ordered)


def apply_rules(text, rules):
    """
    Apply every rule to `text` in one pass and return the result.

    Never raises: a transcript the user has already spoken must reach the
    clipboard even if a rule is nonsense, so a failure here returns the
    unmodified text rather than losing it.
    """
    if not text or not rules:
        return text

    try:
        pattern, replacements = compile_rules(tuple(rules))
        if pattern is None:
            return text

        def replace(match):
            # `groups()` is one entry per alternative and exactly one of them is
            # not None -- the alternative that matched. That is how the pattern
            # says which rule fired without a second scan of the text.
            for index, matched in enumerate(match.groups()):
                if matched is not None:
                    return replacements[index]
            return match.group(0)

        return pattern.sub(replace, text)
    except Exception:
        return text
