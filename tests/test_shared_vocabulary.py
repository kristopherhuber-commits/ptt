"""
The shared vocabulary file (FR-10): `~/agent-configs/vocabulary.json`.

When it exists it is the only store of the replacement rules. These tests pin
the four things that make that safe: the file wins over config.json, an edit
made outside the application is picked up, the tab's writes leave every other
key in the file alone, and a file that is missing or broken never empties the
vocabulary.
"""

import json
import os

from ptt import config, vocabulary

RULE_A = {"heard": "home depot", "typed": "Home Deco", "scope": "always"}
RULE_B = {"heard": "gun link", "typed": "GunLink", "scope": "always"}
CONTEXT = [{"heard": "mine", "means": "MIND", "note": "only in a MIND context"}]


def write_shared(path, always, **extra):
    document = {"version": 1, "always": always, **extra}
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return str(path)


def bump(path):
    """Move the file's mtime on, so a rewrite within one tick still registers."""
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def load(config_file, tmp_path, local=(), shared=None):
    cfg = config_file({"vocabulary": list(local)})
    vocab = tmp_path / "vocabulary.json"
    if shared is not None:
        write_shared(vocab, shared, context=CONTEXT)
    return config.load(cfg, vocabulary_path=str(vocab)), vocab


def test_the_shared_file_replaces_config_json_rules(config_file, tmp_path):
    s, _ = load(config_file, tmp_path, local=[RULE_B], shared=[RULE_A])
    assert s.shared_vocabulary_active()
    assert s.vocabulary == (vocabulary.Rule("home depot", "Home Deco"),)


def test_without_the_file_config_json_rules_are_used(config_file, tmp_path, log_lines):
    s, _ = load(config_file, tmp_path, local=[RULE_B])
    assert not s.shared_vocabulary_active()
    assert s.vocabulary == (vocabulary.Rule("gun link", "GunLink"),)
    assert any("not found" in line for line in log_lines())


def test_loading_never_rewrites_config_json(config_file, tmp_path):
    cfg = config_file({"vocabulary": [RULE_B]})
    before = open(cfg, encoding="utf-8").read()
    write_shared(tmp_path / "vocabulary.json", [RULE_A])
    config.load(cfg, vocabulary_path=str(tmp_path / "vocabulary.json"))
    assert open(cfg, encoding="utf-8").read() == before


def test_an_explicit_config_path_never_reads_the_default_file(config_file):
    s = config.load(config_file({}))
    assert s.vocabulary_path is None


def test_an_edit_made_elsewhere_is_picked_up_and_saved_as_the_fallback(config_file, tmp_path):
    s, vocab = load(config_file, tmp_path, shared=[RULE_A])
    write_shared(vocab, [RULE_A, RULE_B], context=CONTEXT)
    bump(vocab)
    assert s.refresh_vocabulary() is True
    assert len(s.vocabulary) == 2
    saved = json.loads(open(s.path, encoding="utf-8").read())
    assert saved["vocabulary"] == [RULE_A, RULE_B]


def test_an_unchanged_file_is_not_reread(config_file, tmp_path):
    s, _ = load(config_file, tmp_path, shared=[RULE_A])
    assert s.refresh_vocabulary() is False


def test_a_tab_write_goes_to_the_file_and_keeps_its_other_keys(config_file, tmp_path):
    s, vocab = load(config_file, tmp_path, shared=[RULE_A])
    ok, reason = s.set("vocabulary", (vocabulary.Rule("gun link", "GunLink"),))
    assert ok, reason
    document = json.loads(vocab.read_text(encoding="utf-8"))
    assert document["always"] == [RULE_B]
    assert document["context"] == CONTEXT
    assert document["version"] == 1
    # Its own write is not mistaken for an outside edit.
    assert s.refresh_vocabulary() is False


def test_a_broken_file_keeps_the_rules_and_refuses_writes(config_file, tmp_path, log_lines):
    s, vocab = load(config_file, tmp_path, shared=[RULE_A])
    vocab.write_text('{"always": [', encoding="utf-8")
    bump(vocab)
    assert s.refresh_vocabulary() is False
    assert s.vocabulary == (vocabulary.Rule("home depot", "Home Deco"),)
    assert any("could not be read" in line for line in log_lines())

    ok, reason = s.set("vocabulary", ())
    assert not ok
    assert "fix it" in reason
    assert vocab.read_text(encoding="utf-8") == '{"always": ['
    assert s.vocabulary == (vocabulary.Rule("home depot", "Home Deco"),)


def test_a_file_that_disappears_keeps_the_rules(config_file, tmp_path):
    s, vocab = load(config_file, tmp_path, shared=[RULE_A])
    vocab.unlink()
    assert s.refresh_vocabulary() is False
    assert s.vocabulary == (vocabulary.Rule("home depot", "Home Deco"),)


def test_a_malformed_row_is_dropped_and_the_rest_survive(config_file, tmp_path, log_lines):
    s, _ = load(config_file, tmp_path, shared=[{"heard": "", "typed": "x"}, RULE_A])
    assert s.vocabulary == (vocabulary.Rule("home depot", "Home Deco"),)
    assert any("vocabulary.json vocabulary[0] is invalid" in line for line in log_lines())


def test_the_context_list_is_never_applied(config_file, tmp_path):
    s, _ = load(config_file, tmp_path, shared=[])
    assert vocabulary.apply_rules("the mine is open", s.vocabulary) == "the mine is open"
