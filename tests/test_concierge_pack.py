"""
The knowledge pack: generated from `FIELDS`, digest-manifested, budgeted.

`V-CG-69` … `V-CG-78`, and acceptance criterion **v3-12**. Design 5.05's whole
argument is that the pack has two halves with two different drift stories, and
that only one of them can be prevented from drifting by construction. These are
the four checks that cover the other one -- each with an existing pattern in
this repo to copy.
"""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_knowledge_pack as pack_mod           # noqa: E402

from ptt import config, hotkey as hotkey_mod, paths, transcribe   # noqa: E402
from ptt.concierge import agent as agent_mod                      # noqa: E402


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def in_the_repo(monkeypatch):
    """The build step takes repository-relative paths, as build_portable does."""
    monkeypatch.chdir(REPO)


# -- part 1: generated from FIELDS, so it cannot drift -----------------------

def test_every_setting_appears_with_its_own_prose():
    """
    Design 5.05's first row. This half *is* the code: the `does` / `when` /
    `risk` sentences come from `config.FIELDS`, so a setting cannot be in the
    application and missing from the pack.
    """
    section = pack_mod.settings_section()
    for key, rule in config.FIELDS.items():
        if rule.internal:
            continue
        assert f"`{key}`" in section, key
        assert rule.does in section, key
        assert rule.risk in section, key


def test_the_pack_says_which_settings_the_concierge_may_change():
    """
    The allowlist, in the model's own knowledge. Refusing a write is FR-CG-11's
    job; knowing in advance that it will be refused is what stops the model
    promising a change it cannot make.
    """
    section = pack_mod.settings_section()
    assert "The Concierge may change it:** no" in section
    assert "The Concierge may change it:** yes" in section


def test_the_pack_carries_the_two_catalogues_a_setting_points_at():
    """
    `model` is validated against `transcribe.MODEL_NAMES` and `hotkey` against
    `hotkey.KEYS`. A pack that names the setting without its vocabulary leaves
    the model to invent tier names and key names -- which is the one failure
    FR-CG-1 is scored on.
    """
    section = pack_mod.reference_section()
    for info in transcribe.MODELS:
        assert f"`{info.name}`" in section
    for key in hotkey_mod.KEYS:
        assert f"`{key.name}`" in section


def test_no_setting_name_is_written_by_hand_anywhere_in_the_builder():
    """
    `V-HK-01`'s idiom. A hand-listed name is a name that goes stale, and issue
    #12 is the recorded case of what that costs.
    """
    source = open(os.path.join(REPO, "build_knowledge_pack.py"),
                  encoding="utf-8").read()
    for key in config.FIELDS:
        assert f'"{key}"' not in source, key


# -- part 2: the digest manifest (criterion v3-12) ---------------------------

def test_the_manifest_records_path_size_and_digest_for_every_source(tmp_path):
    output = str(tmp_path / "kb.md")
    _, manifest, _ = pack_mod.build(output=output)
    assert manifest
    for row in manifest:
        assert set(row) == {"path", "size", "sha256"}
        assert os.path.getsize(os.path.join(REPO, row["path"])) == row["size"]
        with open(os.path.join(REPO, row["path"]), "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == row["sha256"]


def test_the_manifest_is_in_the_packs_front_matter(tmp_path):
    output = str(tmp_path / "kb.md")
    pack_mod.build(output=output)
    text = open(output, encoding="utf-8").read()
    head = text.split("---", 2)[1]
    assert "concierge_narrative.md" in head
    assert "sha256:" in head
    assert "config.FIELDS" in head


def test_the_shipped_pack_is_current(tmp_path):
    """
    **Acceptance criterion v3-12.** Edit a source without regenerating and this
    fails, naming the file. Without it, "the pack is current" and "the pack was
    current once" are the same statement.
    """
    shipped = paths.knowledge_pack_path()
    if not os.path.exists(shipped):
        pytest.skip("no pack has been built; run build_knowledge_pack.py")

    recorded = _manifest_of(open(shipped, encoding="utf-8").read())
    assert recorded, "the shipped pack has no source manifest"
    for path, digest in recorded.items():
        full = os.path.join(REPO, path)
        assert os.path.exists(full), (
            f"{path} is recorded in the knowledge pack and no longer exists")
        with open(full, "rb") as f:
            actual = hashlib.sha256(f.read()).hexdigest()
        assert actual == digest, (
            f"{path} has changed since the knowledge pack was generated. "
            f"Run build_knowledge_pack.py.")


def _manifest_of(text):
    """`{path: sha256}` out of the pack's front matter."""
    rows, path = {}, None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- path:"):
            path = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("sha256:") and path:
            rows[path] = stripped.split(":", 1)[1].strip()
            path = None
        elif stripped == "---" and rows:
            break
    return rows


# -- error, never skip (OBS-1) -----------------------------------------------

def test_a_missing_source_is_an_error_not_a_smaller_pack(tmp_path):
    """
    The spike's `pack.py` did `if p.exists()` and silently listed a
    `docs/validation.md` that has never existed -- while `gui_handoff.md` had
    *moved*, so a step written to the old source list would have dropped 55 KB,
    over half the named corpus, without a word.
    """
    with pytest.raises(pack_mod.MissingSource) as caught:
        pack_mod.build(output=str(tmp_path / "kb.md"),
                       sources=("docs/no_such_document.md",))
    assert "docs/no_such_document.md" in str(caught.value)
    assert "do not remove the source" in str(caught.value)
    assert not os.path.exists(tmp_path / "kb.md")


def test_an_empty_source_is_an_error_too(tmp_path):
    empty = tmp_path / "empty.md"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(pack_mod.MissingSource):
        pack_mod.build(output=str(tmp_path / "kb.md"), sources=(str(empty),))


def test_the_named_corpus_is_one_list_in_one_place():
    """
    Three documents named three different corpora before this was written down
    once, and every size bound in design 5 is pinned to the pack's size.
    """
    assert pack_mod.NARRATIVE_SOURCES == (
        os.path.join("docs", "ptt-v3-concierge", "concierge_narrative.md"),)
    for path in pack_mod.NARRATIVE_SOURCES:
        assert os.path.exists(os.path.join(REPO, path))


# -- the budget (design 5) ----------------------------------------------------

def test_the_pack_fits_the_budget(tmp_path):
    """
    Design 5 allots the pack about 8k tokens of a 32k window. Over that and the
    history allowance is being spent by the build rather than by the
    conversation.
    """
    output = str(tmp_path / "kb.md")
    _, _, size = pack_mod.build(output=output)
    tokens = agent_mod.approx_tokens(open(output, encoding="utf-8").read())
    assert tokens <= 8000, f"the pack is {tokens} tokens, over design 5's ~8k"


def test_the_pack_is_below_the_revisit_trigger(tmp_path):
    """
    A separate bound, deliberately. Design 5 says that above ~16k tokens RAG
    becomes a design change proposed through that document -- not something
    bolted on quietly once somebody notices the answers have got worse.
    """
    output = str(tmp_path / "kb.md")
    _, _, size = pack_mod.build(output=output)
    tokens = agent_mod.approx_tokens(open(output, encoding="utf-8").read())
    assert tokens < agent_mod.PACK_REVISIT_TOKENS


def test_the_whole_fixed_prefix_fits_the_budget(tmp_path):
    """
    The number that actually matters. Design 5's fixed block is pack + system
    rules + tool schema + note, and it is the *sum* that decides what is left
    for the conversation.
    """
    from ptt.concierge import tools as tools_mod

    output = str(tmp_path / "kb.md")
    pack_mod.build(output=output)
    settings = config.Settings(path=str(tmp_path / "config.json"))
    context = agent_mod.Context(
        open(output, encoding="utf-8").read(),
        agent_mod.load_system_prompt(paths.concierge_prompt_path()),
        tools_mod.Registry(settings))
    assert context.prefix_tokens() <= 11000
    assert context.history_budget_tokens() >= 17000


def test_the_pack_tells_the_model_what_it_does_not_know(tmp_path):
    """
    FR-CG-1's other half. "Without inventing settings that do not exist" is a
    behaviour the prompt asks for and the pack has to make possible, by saying
    plainly that the list is complete.
    """
    output = str(tmp_path / "kb.md")
    pack_mod.build(output=output)
    text = open(output, encoding="utf-8").read()
    assert "There are no" in text and "not a setting" in text


def test_the_narrative_half_answers_questions_about_the_concierge(tmp_path):
    """
    The gap the earlier corpus could not close (review section 6.4): the
    residency slider, the memory note and the Undo chips were documented only in
    the v3 files, none of which was in any candidate source list -- so the pack
    could not answer questions about the chat panel, which is the first thing a
    user asks a chat panel about.
    """
    output = str(tmp_path / "kb.md")
    pack_mod.build(output=output)
    text = open(output, encoding="utf-8").read()
    for topic in ["residency slider", "memory note", "Undo", "Delete model",
                  "pre-roll", "menu bar"]:
        assert topic in text, topic


def test_the_shipped_pack_matches_the_current_fields_table():
    """
    The other half of criterion v3-12, and the half the manifest cannot cover.

    The digest manifest records the *hand-written* source, so it catches an edit
    to `concierge_narrative.md`. Part 1 has no source file to digest -- it is
    generated from `config.FIELDS` -- so a setting whose prose changed without a
    rebuild would leave the shipped pack stale and the manifest green.
    "Cannot drift because it is the code" is true of the generator and not of the
    file it last wrote.
    """
    shipped = paths.knowledge_pack_path()
    if not os.path.exists(shipped):
        pytest.skip("no pack has been built; run build_knowledge_pack.py")

    text = open(shipped, encoding="utf-8").read()
    for key, rule in config.FIELDS.items():
        if rule.internal:
            continue
        assert f"`{key}`" in text, (
            f"{key} is a setting and is not in the shipped knowledge pack. "
            f"Run build_knowledge_pack.py.")
        assert rule.does in text, (
            f"{key}'s description has changed since the pack was generated. "
            f"Run build_knowledge_pack.py.")
