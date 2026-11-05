"""
Compare a trained model with stock on the held-out clips (FR-18, crude).

    C:\\Users\\huber\\tools\\ptt-train\\.venv\\Scripts\\python.exe training\\report.py

Three columns, each decoded exactly as PTT dictates (the app's own
`transcribe_clip`: same beam, same silence filter, same cleanup):

- **stock:** `openai/whisper-large-v3-turbo` converted locally, no rules;
- **stock + rules:** the same output with vocabulary.json's rules applied,
  which is what dictation types today;
- **trained:** the trained model, no rules.

Measures: WER (word error rate) = (S + D + I) / N over all held-out words, and
the share of short replies (1 to 7 words) typed exactly right, both on lower-case
text with punctuation removed. Increment 1 gives no uncertainty; increment 6
adds it. The report quotes what was dictated, so it stays in
`ptt-train\\reports\\` (CON-6).

Must not import torch: CTranslate2 here uses the CUDA 12 libraries from the
nvidia-*-cu12 wheels, and PyTorch's CUDA 13 copies must not be loaded first.
"""

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

sys.path.insert(0, common.REPO_APP)
import ptt.logging_setup as logging_setup  # noqa: E402
logging_setup.log_debug = lambda _msg: None     # keep the app's debug log untouched
from ptt import paths, transcribe, vocabulary  # noqa: E402


def shared_rules():
    """vocabulary.json's `always` rules, parsed as the app parses them."""
    with open(paths.shared_vocabulary_path(), encoding="utf-8") as f:
        document = json.load(f)
    return tuple(r for r, _ in (vocabulary.parse_rule(v) for v in document.get("always", []))
                 if r)


def newest_model():
    folders = [p for p in glob.glob(os.path.join(common.MODELS_DIR, "*"))
               if os.path.exists(os.path.join(p, "model.bin"))]
    if not folders:
        sys.exit(f"No trained model in {common.MODELS_DIR}.")
    return os.path.basename(max(folders, key=os.path.getmtime))


def run(model_dir, clips):
    """Each clip's text from one model, and the mean seconds per clip."""
    model, device, status = transcribe.load_model_with_fallback(model_dir, True, True)
    if model is None:
        sys.exit(f"Could not load {model_dir}: {status}")
    transcribe.transcribe_clip(model, common.load_wav(clips[0]["wav"])[:16_000])  # warm-up, untimed
    texts, seconds = [], 0.0
    for clip in clips:
        audio = common.load_wav(clip["wav"])
        t0 = time.time()
        texts.append(transcribe.transcribe_clip(model, audio).text)
        seconds += time.time() - t0
    del model
    return texts, seconds / max(len(clips), 1), device


def score(clips, texts):
    errors = sum(common.word_errors(c["reference"], t)[0] for c, t in zip(clips, texts))
    n = sum(len(common.words(c["reference"])) for c in clips)
    short = [(c, t) for c, t in zip(clips, texts) if common.is_short_reply(c["reference"])]
    exact = sum(common.words(c["reference"]) == common.words(t) for c, t in short)
    return {"wer": errors / n if n else None, "errors": errors, "words": n,
            "short": len(short), "short_exact": exact}


def cell(text):
    return text.replace("|", "\\|").replace("\n", " ")


def main():
    parser = argparse.ArgumentParser(description="Stock against trained on held-out clips.")
    parser.add_argument("--trained", default=None, help="a folder name in ptt-train\\models")
    parser.add_argument("--clips", default=common.CLIPS_ROOT)
    args = parser.parse_args()
    name = args.trained or newest_model()
    trained_dir = os.path.join(common.MODELS_DIR, name)

    clips = [c for c in common.labelled_clips(args.clips) if c["held_out"]]
    if not clips:
        sys.exit("No held-out clips: label more (1 in 5 labelled clips is held out).")
    rules = shared_rules()

    print(f"{len(clips)} held-out clips; {len(rules)} vocabulary rules.")
    stock, stock_s, device = run(common.STOCK_CT2, clips)
    with_rules = [vocabulary.apply_rules(t, rules) for t in stock]
    trained, trained_s, _ = run(trained_dir, clips)

    systems = [("stock", stock), ("stock + rules", with_rules), (name, trained)]
    scores = {label: score(clips, texts) for label, texts in systems}

    stamp = time.strftime("%Y-%m-%d-%H%M")
    os.makedirs(common.REPORTS_DIR, exist_ok=True)
    out = os.path.join(common.REPORTS_DIR, f"{stamp}-{name}-vs-stock.md")
    lines = [
        f"# {name} against stock",
        "",
        f"{time.strftime('%Y-%m-%d %H:%M')}, {common.machine_name()}, {device}. "
        f"{len(clips)} held-out clips. Increment 1's crude report: no uncertainty, "
        f"and the numbers mean little on so few clips.",
        "",
        "| | WER | errors / words | short replies exact | seconds per clip |",
        "|---|---|---|---|---|",
    ]
    for label, _ in systems:
        s = scores[label]
        seconds = {"stock": stock_s, name: trained_s}.get(label)
        lines.append(
            f"| {label} | {s['wer']:.1%} | {s['errors']} / {s['words']} | "
            f"{s['short_exact']} / {s['short']} | "
            f"{'' if seconds is None else f'{seconds:.2f}'} |")
    lines += ["", "## Per clip", "",
              "| clip | s | correct | stock | stock + rules | trained |",
              "|---|---|---|---|---|---|"]
    for clip, a, b, c in zip(clips, stock, with_rules, trained):
        marks = [f"{cell(t)} ({common.word_errors(clip['reference'], t)[0]})" for t in (a, b, c)]
        lines.append(f"| {clip['id']} | {clip['duration_s']} | {cell(clip['reference'])} | "
                     + " | ".join(marks) + " |")
    lines += ["", "The number after each text is its word errors against the correct text.", ""]
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    for label, _ in systems:
        s = scores[label]
        print(f"{label:>24}: WER {s['wer']:.1%}, short exact {s['short_exact']}/{s['short']}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
