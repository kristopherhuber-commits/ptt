"""
Replacement rules: validation, and every case in the matching semantics.

The whole point of `ptt.vocabulary` being a module of its own is that this file
needs no model, no microphone and no `QApplication` -- the same reason
`clean_text` and `parse_chord` are pure. What is pinned here is not "does
substitution work" but the three decisions `gui_handoff` section 6.4 left open
(stage0_review section 5.12): one pass, longest phrase wins, literal
replacement. Each of them is invisible in ordinary use and each produces a
different transcript when it is got wrong.
"""

import pytest

from ptt import vocabulary
from ptt.vocabulary import Rule

WSL = Rule("w s l", "WSL")
CT2 = Rule("see translate two", "ctranslate2")


def apply(text, *rules):
    return vocabulary.apply_rules(text, rules)


# -- parse_rule --------------------------------------------------------------

def test_a_valid_rule_parses():
    rule, reason = vocabulary.parse_rule(
        {"heard": "jabra", "typed": "Jabra", "scope": "always"})
    assert reason is None
    assert rule == Rule("jabra", "Jabra", "always")


def test_scope_defaults_to_always():
    """A rule hand-written without a scope is a rule that always applies."""
    rule, _ = vocabulary.parse_rule({"heard": "jabra", "typed": "Jabra"})
    assert rule.scope == vocabulary.SCOPE_ALWAYS


def test_an_empty_replacement_is_allowed():
    """Deleting a filler word is a legitimate rule, written as "replace with nothing"."""
    rule, reason = vocabulary.parse_rule({"heard": "um", "typed": ""})
    assert reason is None
    assert rule.typed == ""


def test_the_heard_phrase_is_normalised_on_the_way_in():
    """Whisper emits single spaces, so a rule typed with two would match nothing."""
    rule, _ = vocabulary.parse_rule({"heard": "  w   s  l ", "typed": "WSL"})
    assert rule.heard == "w s l"


@pytest.mark.parametrize("value, reason", [
    ("not a dict", "not an object"),
    ({"typed": "WSL"}, "heard is not a string"),
    ({"heard": 7, "typed": "WSL"}, "heard is not a string"),
    ({"heard": "   ", "typed": "WSL"}, "heard is empty"),
    ({"heard": "w s l", "typed": 7}, "typed is not a string"),
    ({"heard": "w s l", "typed": "WSL", "scope": 7}, "scope is not a string"),
])
def test_an_invalid_rule_is_rejected_with_a_reason(value, reason):
    rule, actual = vocabulary.parse_rule(value)
    assert rule is None
    assert reason in actual


def test_an_unrecognised_scope_drops_the_rule_rather_than_widening_it():
    """
    The one fallback in this codebase that deliberately does *nothing* instead
    of doing less. Coercing a future build's per-application scope to Always
    would take a rule the user scoped to one program and apply it everywhere,
    which is a behaviour change wearing a default's clothes.
    """
    rule, reason = vocabulary.parse_rule(
        {"heard": "w s l", "typed": "WSL", "scope": "editors"})
    assert rule is None
    assert "not one of" in reason


def test_parse_rule_never_raises():
    for value in (None, [], 7, {"heard": None}, {"heard": "x", "typed": None}):
        assert vocabulary.parse_rule(value)[0] is None


# -- the round trip ----------------------------------------------------------

def test_rules_serialise_back_to_the_shape_they_were_read_from():
    assert vocabulary.to_json((WSL,)) == [
        {"heard": "w s l", "typed": "WSL", "scope": "always"}
    ]


def test_a_serialised_rule_parses_again_unchanged():
    for raw in vocabulary.to_json((WSL, CT2)):
        rule, reason = vocabulary.parse_rule(raw)
        assert reason is None
        assert rule in (WSL, CT2)


# -- matching ----------------------------------------------------------------

def test_a_phrase_is_replaced():
    assert apply("run it in w s l", WSL) == "run it in WSL"


def test_matching_is_case_insensitive():
    assert apply("Run it in W S L", WSL) == "Run it in WSL"


def test_the_replacement_keeps_its_own_case():
    """The rule says what to type; the spoken casing does not vote."""
    assert apply("JABRA", Rule("jabra", "Jabra")) == "Jabra"


def test_matching_is_whole_word():
    """A rule for `w s l` must not fire inside `w s lot`."""
    assert apply("w s lot", WSL) == "w s lot"
    assert apply("in-w s l", WSL) == "in-WSL"


def test_a_phrase_matches_across_any_whitespace():
    assert apply("run it in w  s\tl now", WSL) == "run it in WSL now"


def test_every_occurrence_is_replaced():
    assert apply("w s l and w s l", WSL) == "WSL and WSL"


def test_a_rule_that_matches_nothing_leaves_the_text_alone():
    assert apply("nothing to see here", WSL) == "nothing to see here"


def test_no_rules_is_the_identity():
    assert vocabulary.apply_rules("unchanged", ()) == "unchanged"
    assert vocabulary.apply_rules("", (WSL,)) == ""


def test_metacharacters_in_a_phrase_are_literal():
    """A rule of `c++` is three characters, not a quantifier applied to nothing."""
    assert apply("i write c++ daily", Rule("c++", "C++")) == "i write C++ daily"


def test_a_replacement_is_literal_text():
    r"""
    `\n` in the Typed column is a backslash and an `n`. Interpreting it would
    need an escape language and a rule for a literal backslash, which is a
    language rather than a setting (gui_handoff section 6.4, stage0 5.12).
    """
    assert apply("new paragraph", Rule("new paragraph", r"\n\n")) == r"\n\n"


def test_a_replacement_containing_a_backreference_is_not_expanded():
    """`re.sub`'s replacement syntax must not reach a string the user typed."""
    assert apply("w s l", Rule("w s l", r"\1 group")) == r"\1 group"


# -- the three decided semantics ---------------------------------------------

def test_a_replacement_is_never_itself_replaced():
    """
    One pass. Chaining would make the result depend on rule order in a way
    nothing on the panel could show, and two rules that map into each other
    would not terminate at all.
    """
    rules = (Rule("alpha", "beta"), Rule("beta", "gamma"))
    assert vocabulary.apply_rules("alpha beta", rules) == "beta gamma"


def test_the_longest_phrase_wins_wherever_two_rules_could_match():
    """
    Otherwise adding `w s l two` beside an existing `w s l` would silently never
    fire, and there is no reordering control in this pass that could fix it.
    """
    rules = (WSL, Rule("w s l two", "WSL2"))
    assert vocabulary.apply_rules("w s l two is fine", rules) == "WSL2 is fine"
    assert vocabulary.apply_rules("w s l is fine", rules) == "WSL is fine"


def test_two_phrases_of_the_same_length_go_in_list_order():
    rules = (Rule("abc", "first"), Rule("abc", "second"))
    assert vocabulary.apply_rules("abc", rules) == "first"


def test_compile_rules_orders_longest_first():
    """The ordering is asserted directly, not only through its effect."""
    _pattern, replacements = vocabulary.compile_rules((WSL, CT2))
    assert replacements == ("ctranslate2", "WSL")


def test_compile_rules_has_nothing_to_compile_for_no_rules():
    assert vocabulary.compile_rules(()) == (None, ())


# -- it never loses the transcript -------------------------------------------

def test_a_broken_rule_returns_the_transcript_rather_than_losing_it():
    """
    The user has already said the words. A rule that somehow reaches this
    function malformed must cost them the substitution, not the sentence.
    """
    class Exploding:
        heard = property(lambda self: 1 / 0)

    assert vocabulary.apply_rules("still here", (Exploding(),)) == "still here"
