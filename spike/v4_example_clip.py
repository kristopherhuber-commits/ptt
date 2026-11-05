"""
v4 spike (2026-11-04): save one example clip, its record and its label in the ptt-clips
layout of docs/background/training-data-plan.md, by running the bundled benchmark clip
through the stock model with the app's own code.

Answered: what a record holds, how to run the model and the vocabulary rules outside the
app, and what the decoder's training target looks like (the four start tokens). Found:
segment scores are one per 30 s decoding window, and WER against a spelled-out script
was 16/62 with every word heard right (all formatting). Run with the installed app's
.venv python. Throwaway: not product code.
"""
import datetime
import json
import os
import re
import sys
import time
import wave

import numpy as np

REPO_APP = r"C:\Users\huber\git\ptt\app"
CLIP_ROOT = r"C:\Users\huber\tools\ptt-v4-training\ptt-clips"
VOCAB = r"C:\Users\huber\agent-configs\vocabulary.json"
MACHINE = "darklord"
# The benchmark clip's commit time (0f70a76); the real recording time isn't stored.
RECORDED_UTC = datetime.datetime(2026, 8, 23, 1, 15, 34, tzinfo=datetime.timezone.utc)

sys.path.insert(0, REPO_APP)
import ptt.logging_setup as logging_setup
logging_setup.log_debug = lambda msg: None      # keep the app's debug log untouched
import ptt
from ptt import transcribe, vocabulary

src = os.path.join(REPO_APP, "assets", "benchmark_sample.wav")
with wave.open(src, "rb") as f:
    rate, frames = f.getframerate(), f.getnframes()
    raw_bytes = f.readframes(frames)
audio = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0

model, device, status = transcribe.load_model_with_fallback("large-v3-turbo", True, True)
kwargs = dict(language=transcribe.LANGUAGE, beam_size=transcribe.BEAM_SIZE,
              vad_filter=transcribe.VAD_FILTER, condition_on_previous_text=False)
list(model.transcribe(audio[:rate], **kwargs)[0])          # warm-up, not timed
t0 = time.time()
segments = list(model.transcribe(audio, **kwargs)[0])
latency = time.time() - t0

raw_text = "".join(s.text for s in segments)
cleaned = transcribe.clean_text(raw_text)
vocab = json.load(open(VOCAB, encoding="utf-8"))
rules = [r for r, _ in (vocabulary.parse_rule(v) for v in vocab["always"]) if r]
pasted = vocabulary.apply_rules(cleaned, rules)
fired = [f"{r.heard} -> {r.typed}" for r in rules
         if re.search(vocabulary._phrase_pattern(r.heard), cleaned, re.IGNORECASE)]

model_repo, model_rev = "large-v3-turbo", ""
try:
    from huggingface_hub import scan_cache_dir
    for repo in scan_cache_dir().repos:
        if repo.repo_type == "model" and repo.repo_id.endswith("large-v3-turbo"):
            model_repo = repo.repo_id
            model_rev = sorted(repo.revisions, key=lambda r: r.last_modified)[-1].commit_hash
except Exception as e:
    print(f"(model revision not found: {e})")

clip_id = f"{MACHINE}-{RECORDED_UTC:%Y%m%dT%H%M%S}.000Z"
month_dir = os.path.join(CLIP_ROOT, MACHINE, f"{RECORDED_UTC:%Y-%m}")
os.makedirs(month_dir, exist_ok=True)
with open(os.path.join(month_dir, clip_id + ".wav"), "wb") as out, open(src, "rb") as inp:
    out.write(inp.read())

record = {
    "id": clip_id,
    "machine": MACHINE,
    "mic": "",
    "app_version": ptt.__version__,
    "model": model_repo,
    "model_revision": model_rev,
    "compute_type": transcribe.CUDA_COMPUTE_TYPE if device == "cuda" else transcribe.CPU_COMPUTE_TYPE,
    "beam_size": transcribe.BEAM_SIZE,
    "vad": transcribe.VAD_FILTER,
    "hint": "",
    "duration_s": round(frames / rate, 2),
    "latency_s": round(latency, 2),
    "raw_text": raw_text,
    "pasted_text": pasted,
    "rules_fired": fired,
    "segments": [{"start": round(s.start, 2), "end": round(s.end, 2),
                  "avg_logprob": round(s.avg_logprob, 3),
                  "no_speech_prob": round(s.no_speech_prob, 4),
                  "compression_ratio": round(s.compression_ratio, 2)} for s in segments],
}
with open(os.path.join(month_dir, "recordings.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps(record, ensure_ascii=False) + "\n")

passage = re.search(r'PASSAGE = """(.*?)"""',
                    open(r"C:\Users\huber\git\ptt\docs\ptt-v2-gui\record_sample.py",
                         encoding="utf-8").read(), re.S).group(1)
reference = " ".join(passage.split())
day = (RECORDED_UTC.date() - datetime.date(1970, 1, 1)).days
split = "test" if day % 10 in (0, 1) else "dev" if day % 10 == 2 else "train"
label = {"id": clip_id, "reference": reference, "source": "read-aloud", "split": split}
with open(os.path.join(CLIP_ROOT, MACHINE, "labels.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps(label, ensure_ascii=False) + "\n")

# Word errors, on lower-cased text without punctuation.
def words(t):
    return re.sub(r"[^\w\s'-]", " ", t.lower()).split()
hyp, ref = words(pasted), words(reference)
d = np.zeros((len(ref) + 1, len(hyp) + 1), dtype=int)
d[:, 0], d[0, :] = range(len(ref) + 1), range(len(hyp) + 1)
for i in range(1, len(ref) + 1):
    for j in range(1, len(hyp) + 1):
        d[i, j] = min(d[i-1, j] + 1, d[i, j-1] + 1, d[i-1, j-1] + (ref[i-1] != hyp[j-1]))

tok = model.hf_tokenizer
prefix = ["<|startoftranscript|>", "<|en|>", "<|transcribe|>", "<|notimestamps|>"]
text_ids = tok.encode(" " + reference, add_special_tokens=False).ids

print("DEVICE", device, status)
print("RECORD", json.dumps(record, ensure_ascii=False, indent=1))
print("LABEL", json.dumps(label, ensure_ascii=False, indent=1))
print(f"WER {d[-1, -1]}/{len(ref)} = {d[-1, -1] / len(ref):.3f}")
print("TARGET TOKENS", len(text_ids), "text tokens; prefix", prefix,
      "[ids", [tok.token_to_id(t) for t in prefix], "]")
print("FIRST 12", [tok.id_to_token(i) for i in text_ids[:12]], "... + <|endoftext|>")
for root, _, files in os.walk(CLIP_ROOT):
    for name in files:
        p = os.path.join(root, name)
        print("FILE", p, os.path.getsize(p))
