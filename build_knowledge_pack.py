"""
Generate `app/assets/concierge_kb.md` -- the Concierge's knowledge pack.

`concierge_design.md` section 5.05 (Q20). The pack is two parts with two
different drift stories, and the split exists because the earlier specification
asked for one step that both *distilled* 108,770 characters into ~8k tokens and
was *never hand-edited*. Those cannot both be true. A script can concatenate and
truncate -- which is what the spike's `pack.py` did, binary-searching a byte
offset and cutting mid-sentence -- but it cannot distil, and reorganising by
settings panel is authoring.

| Part | Source | Can it drift? |
|---|---|---|
| Per-setting | `config.FIELDS`'s `does` / `when` / `risk` | **No.** It *is* the code |
| Narrative | `docs/ptt-v3-concierge/concierge_narrative.md`, hand-written | Yes -- so its digest is recorded and an L1 test fails when it changes |

Three properties this step has that the spike's did not, each with an existing
pattern in this repo to copy:

- **It errors on a missing or unreadable source; it never skips.** `pack.py` did
  `if p.exists()` and silently listed a `docs/validation.md` that has never
  existed, while `gui_handoff.md` had *moved* -- a step written to the old
  source list would have dropped 55 KB, over half the named corpus, without a
  word. (`OBS-1`.)
- **It records `{path, size, sha256}` for every source in the pack's front
  matter**, so a test can tell "current" from "was current once". (`V-UI-12`'s
  pattern: the Advanced table fails when it drifts from the live constants.)
- **The settings whitelist is derived, never hand-listed** -- it is the same
  `FIELDS` table, so a field added to the application is a real setting from the
  moment it exists rather than something the qualification suite scores as an
  invention. (`V-HK-01`.)

Run it directly, or let `build_portable.py` run it.
"""

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from ptt import config, hotkey as hotkey_mod, transcribe   # noqa: E402

#: The hand-written half. One entry, and the list is the corpus -- three
#: documents named three different corpora before this was written down once.
NARRATIVE_SOURCES = (
    os.path.join("docs", "ptt-v3-concierge", "concierge_narrative.md"),
)

#: Where the generated pack is written. Under `app/assets/`, which
#: `build_portable.py`'s `os.walk` ships automatically.
OUTPUT = os.path.join("app", "assets", "concierge_kb.md")


class MissingSource(Exception):
    """A named source is absent or unreadable. Never skipped, always fatal."""


def digest_of(path):
    """
    SHA-256 of a source's **content**, with line endings normalised.

    Not of its bytes on disk, and that distinction is the whole of
    `development_history.md` #58. `core.autocrlf` is `true` on Windows and this
    repository has no `.gitattributes`, so git stores LF and checks out CRLF:
    `concierge_narrative.md` is 11 296 bytes in the object store and 11 492 in
    the working tree, and a byte-wise digest is therefore a fact about how the
    file arrived rather than about what it says.

    That mattered twice over. `test_the_shipped_pack_is_current` failed on a
    fresh `git checkout` with nothing edited, which is a check that cries wolf
    and gets ignored. And regenerating on such a checkout would have produced a
    pack whose manifest differed from the one gate 2.5 froze at `76a281c8a388`,
    breaking the digest every qualification scorecard is compared against --
    while the pack's *body* was identical, because `read_source` has always read
    text with universal newlines.

    Normalising here makes the recorded digest equal the content digest, which
    is the LF one git already stores.
    """
    with open(path, "r", encoding="utf-8", newline=None) as f:
        return hashlib.sha256(f.read().encode("utf-8")).hexdigest()


def read_source(path):
    """
    One source document, with its manifest row. Errors rather than skipping.

    The exception carries the path, because the whole point of the failure is to
    name the file somebody moved.
    """
    if not os.path.exists(path):
        raise MissingSource(
            f"{path} is named as a knowledge-pack source and does not exist. "
            f"The pack is not built. Fix the path or the file -- do not remove "
            f"the source from the list to make this go away.")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception as e:
        raise MissingSource(f"{path} could not be read: {str(e)}")
    if not text.strip():
        raise MissingSource(f"{path} is empty; a knowledge-pack source may not be.")
    return text, {
        "path": path.replace("\\", "/"),
        # The normalised length, for the same reason `digest_of` normalises: on
        # a CRLF checkout `os.path.getsize` reports 196 bytes more than the file
        # has lines' worth of content, and the manifest would record a size
        # nothing else in the project agrees with.
        "size": len(text.encode("utf-8")),
        "sha256": digest_of(path),
    }


def settings_section():
    """
    Part 1, generated from `config.FIELDS`.

    Rendered per setting in the order the table declares, with the three
    sentences the table already carries. Nothing here is written twice: change
    the prose in `config.py` and the pack changes with it, because there is no
    second copy to forget.
    """
    lines = ["## Every setting, and what it does", ""]
    lines.append(
        "These are the complete settings this application has. There are no "
        "others. A name that is not on this list is not a setting.")
    lines.append("")

    for key, rule in config.FIELDS.items():
        if rule.internal:
            continue
        lines.append(f"### `{key}`")
        lines.append("")
        lines.append(f"- **Type:** {_type_note(rule)}")
        lines.append(f"- **Default:** `{_default_note(rule)}`")
        lines.append(f"- **The Concierge may change it:** "
                     f"{'yes' if rule.agent_writable else 'no'}")
        if rule.does:
            lines.append(f"- **What it does:** {rule.does}")
        if rule.when:
            lines.append(f"- **When to change it:** {rule.when}")
        if rule.risk:
            lines.append(f"- **What can go wrong:** {rule.risk}")
        lines.append("")
    return "\n".join(lines)


def _type_note(rule):
    if rule.choices:
        return f"one of {', '.join(f'`{c}`' for c in rule.choices)}"
    if rule.kind == "bool":
        return "true or false"
    if rule.kind == "int":
        bounds = []
        if rule.minimum is not None:
            bounds.append(f"at least {rule.minimum}")
        if rule.maximum is not None:
            bounds.append(f"at most {rule.maximum}")
        note = "a whole number" + (f" ({', '.join(bounds)})" if bounds else "")
        return note + (", or null" if rule.nullable else "")
    if rule.json_type == "array":
        return "a list"
    if rule.json_type == "object":
        return "an object"
    return "text"


def _default_note(rule):
    if isinstance(rule.default, tuple):
        return list(rule.default)
    return rule.default


def reference_section():
    """
    The two catalogues a setting's `choices` points at but does not contain.

    `model` is validated against `transcribe.MODEL_NAMES` and `hotkey` against
    `hotkey.KEYS`; both are declarative tables elsewhere in the application, and
    a pack that names the setting without its vocabulary leaves the model to
    invent tier names and key names -- which is the one failure FR-CG-1 is
    scored on.
    """
    lines = ["## The two catalogues settings refer to", "",
             "### Whisper size tiers (`model`)", ""]
    for info in transcribe.MODELS:
        lines.append(f"- `{info.name}` -- {info.params}, about {info.disk} on "
                     f"disk, {info.character}.")
    lines.append("")
    lines.append(f"The shipped default is `{transcribe.DEFAULT_MODEL}`.")
    lines.append("")
    lines.append("### Hotkey names (`hotkey`)")
    lines.append("")
    lines.append("A chord is a list of these names, held together. An unsided "
                 "name matches either side of the keyboard.")
    lines.append("")
    for key in hotkey_mod.KEYS:
        note = "may be chosen in the picker" if key.bindable else "unsided alias"
        lines.append(f"- `{key.name}` -- {key.label} ({note}).")
    lines.append("")
    lines.append(f"The shipped default chord is "
                 f"`{list(hotkey_mod.DEFAULT_HOTKEY)}`.")
    lines.append("")
    return "\n".join(lines)


def build(output=OUTPUT, sources=NARRATIVE_SOURCES):
    """
    Write the pack. Returns `(path, manifest, characters)`.

    Front matter first, so the manifest is the first thing anyone reads and the
    first thing a test can parse.
    """
    manifest = []
    narrative = []
    for path in sources:
        text, row = read_source(path)
        narrative.append(text)
        manifest.append(row)

    body_parts = [settings_section(), reference_section()]
    body_parts.extend(narrative)
    body = "\n\n".join(part.strip() for part in body_parts)

    header = ["---", "generated-by: build_knowledge_pack.py", "sources:"]
    for row in manifest:
        header.append(f"  - path: {row['path']}")
        header.append(f"    size: {row['size']}")
        header.append(f"    sha256: {row['sha256']}")
    header.append("generated-parts:")
    header.append("  - config.FIELDS (settings)")
    header.append("  - transcribe.MODELS + hotkey.KEYS (catalogues)")
    header.append("---")

    text = "\n".join(header) + "\n\n" + _preamble() + "\n\n" + body + "\n"

    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        f.write(text)
    return output, manifest, len(text)


def _preamble():
    return (
        "# What you know about PTT Dictation\n\n"
        "Everything below is true of the build you are running in. Where it "
        "disagrees with your general knowledge, it is right and you are wrong. "
        "If something is not here, say you do not know it rather than reasoning "
        "from what sounds plausible."
    )


def main():
    try:
        output, manifest, size = build()
    except MissingSource as e:
        print(f"ERROR: {e}")
        return 1
    tokens = (size + 3) // 4
    print(f"Wrote {output}: {size} characters, about {tokens} tokens.")
    for row in manifest:
        print(f"  source {row['path']} ({row['size']} B) {row['sha256'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
