"""
Record the benchmark sample clip.

The Model panel measures each Whisper model's latency against one fixed clip, so
the same audio must be used every time or the numbers are not comparable. Record
it once, commit the WAV, and never re-record it — a new clip invalidates every
cached measurement in config.json.

Run from the project root with the project's venv:

    .venv\\Scripts\\python.exe docs\\gui_handoff\\record_sample.py

Writes app/assets/benchmark_sample.wav (16 kHz mono, 16-bit PCM), which is what
faster-whisper wants and is small enough to commit (~940 KB for 30 s).

Read the passage to the end and then stop talking. The trailing silence is
deliberate: it is the condition that made large-v3 emit runs of full stops, which
is what transcribe.clean_text exists to strip.
"""

import os
import sys
import wave

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
SECONDS = 30
OUT = os.path.join("app", "assets", "benchmark_sample.wav")

PASSAGE = """
The quick brown fox jumps over the lazy dog. Push to talk dictation runs
locally on the GPU, transcribes with faster-whisper, and pastes the text at
the cursor. Right Control is the hotkey. Numbers: one, seven, sixteen,
forty-two, two thousand and twenty-six. Spell out W S L, CUDA, and Jabra.
This sentence ends on a falling tone, and then there is silence.
"""


def main():
    print(__doc__.strip())
    print("\nRead this aloud at your normal dictation pace and volume. Read it to the"
          "\nend and then stop talking \u2014 the trailing silence is part of the test.\n")
    print(PASSAGE.strip())
    print(f"\n{SECONDS} seconds, which is comfortably more than the passage needs."
          f"\nPress Enter to start recording.")
    input()

    print("Recording…")
    audio = sd.rec(int(SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="int16")
    sd.wait()
    print("Done.")

    peak = int(np.abs(audio).max())
    if peak < 3000:
        print(f"WARNING: peak amplitude {peak} of 32767 — too quiet to be a fair "
              f"benchmark. Move closer to the microphone and record again.")
    elif peak > 32000:
        print(f"WARNING: peak amplitude {peak} of 32767 — clipping. Back off the "
              f"input gain and record again.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with wave.open(OUT, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(audio.tobytes())

    size_kb = os.path.getsize(OUT) / 1024
    print(f"\nWrote {OUT} ({size_kb:.0f} KB, peak {peak}/32767).")
    print("Play it back and confirm it is intelligible end to end, then commit it.")


if __name__ == "__main__":
    sys.exit(main())
