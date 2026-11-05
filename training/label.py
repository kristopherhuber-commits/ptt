"""
Give saved clips their correct text, from the command line (v4 increment 1).

Plays each unlabelled clip and shows what was pasted. Then:

    Enter        the pasted text was right
    any text     replaces it: that is the correct text
    -            discard the clip (noise, half a word, not worth keeping)
    ?            play it again
    q            stop (Ctrl+C works too)

Each answer is appended to `<clips>\\<machine>\\labels.jsonl` straight away, so
stopping loses nothing. A later line for the same clip overrides an earlier one.

Correct text follows the style guide (training-data-plan.md section 5), printed
at the start. Standard library only: run it with any Python 3.

    python training\\label.py                    this machine's clips
    python training\\label.py --machine nightlord
"""

import argparse
import os
import sys
import winsound

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

STYLE = """Style guide for correct text:
  - Fix wrong words, not formatting: keep the model's capitals, punctuation and numbers.
  - Names as vocabulary.json types them: MIND Technology, Seamap, GunLink, Claude Code.
  - Numbers as digits (1, 42, 2026, v4). Acronyms in capitals, no spaces: WSL, CUDA, GPU.
  - Leave out fillers (um, uh) and false starts.
Enter = right as pasted   text = the correct text   - = discard   ? = replay   q = stop
"""


def play(path):
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--clips", default=common.CLIPS_ROOT)
    parser.add_argument("--machine", default=common.machine_name())
    args = parser.parse_args()

    machine_dir = os.path.join(args.clips, args.machine)
    labels_path = os.path.join(machine_dir, common.LABELS_FILE)
    done = common.labels(machine_dir)
    todo = [r for r in common.recordings(machine_dir)
            if r["id"] not in done and os.path.exists(r["wav"])]

    print(f"{machine_dir}: {len(done)} labelled, {len(todo)} to do.\n")
    print(STYLE)
    if not todo:
        return

    labelled = 0
    try:
        for n, record in enumerate(todo, 1):
            print(f"[{n}/{len(todo)}] {record['id']}  {record['duration_s']} s")
            print(f"  pasted: {record['pasted_text']}")
            play(record["wav"])
            while True:
                answer = input("  correct: ")
                if answer.strip() == "?":
                    play(record["wav"])
                    continue
                break
            winsound.PlaySound(None, 0)
            if answer.strip().lower() == "q":
                break
            if answer.strip() == "-":
                row = {"id": record["id"], "reference": None, "source": "discarded"}
            elif answer.strip() == "":
                row = {"id": record["id"], "reference": record["pasted_text"],
                       "source": "accepted"}
            else:
                row = {"id": record["id"], "reference": answer.strip(),
                       "source": "corrected"}
            row["labelled_at"] = common.utc_now_iso()
            common.append_jsonl(labels_path, row)
            labelled += 1
            print()
    except (KeyboardInterrupt, EOFError):
        print()
    finally:
        winsound.PlaySound(None, 0)
    print(f"Labelled {labelled} this time; {len(done) + labelled} in {labels_path}.")


if __name__ == "__main__":
    main()
