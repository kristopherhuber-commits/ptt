"""
Train a LoRA on the labelled clips, merge it, and convert the result for PTT.

    C:\\Users\\huber\\tools\\ptt-train\\.venv\\Scripts\\python.exe training\\train.py

1. **Data:** every labelled clip from every machine under the clips folder,
   except the held-out ones (`common.is_held_out`) and clips over 30 s, which
   Whisper cannot take in one window.
2. **Model:** `openai/whisper-large-v3-turbo`. LoRA (low-rank adaptation) on the
   attention projections of the encoder and the decoder: each frozen W gets
   W + (alpha/r) B A, with B (d x r) and A (r x k) trained.
3. **Loss:** L = -sum_t log p(y_t | y_<t, x) over the reference's tokens and
   `<|endoftext|>`. The decoder is given the start tokens `<|startoftranscript|>
   <|en|><|transcribe|><|notimestamps|>`, as faster-whisper gives them when
   PTT dictates; predicting the last three is not part of the loss.
4. **Export:** merge B A into W, save, and convert to float16 CTranslate2 with
   `ct2-transformers-converter` (int8 fails on Blackwell, CON-4) into
   `ptt-train\\models\\<name>\\`, with `ptt_model.json` describing the run.

The run's numbers go to `ptt-train\\runs\\<name>\\log.jsonl`, one line per epoch.
"""

import argparse
import datetime
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

import numpy as np  # noqa: E402
import torch  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import WhisperForConditionalGeneration, WhisperProcessor  # noqa: E402

TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "out_proj"]
MAX_SECONDS = 30.0
#: Tokens the decoder is given before the text: <|startoftranscript|> and these three.
PROMPT_TOKENS = 3


def examples(clips, processor):
    """(features, token ids) per clip; token ids start <|startoftranscript|> and end <|endoftext|>."""
    out = []
    for clip in clips:
        audio = common.load_wav(clip["wav"])
        features = processor.feature_extractor(
            audio, sampling_rate=16_000, return_tensors="np").input_features[0]
        ids = processor.tokenizer(" " + clip["reference"].strip()).input_ids
        out.append((features.astype(np.float32), ids))
    return out


def batch_tensors(batch, pad_id, device):
    """Input features, decoder input ids, and targets with the start tokens masked."""
    features = torch.tensor(np.stack([f for f, _ in batch]), device=device)
    longest = max(len(ids) for _, ids in batch) - 1
    decoder_in = torch.full((len(batch), longest), pad_id, dtype=torch.long)
    targets = torch.full((len(batch), longest), -100, dtype=torch.long)
    for row, (_, ids) in enumerate(batch):
        ids = torch.tensor(ids, dtype=torch.long)
        decoder_in[row, :len(ids) - 1] = ids[:-1]
        targets[row, :len(ids) - 1] = ids[1:]
        targets[row, :PROMPT_TOKENS] = -100
    return features, decoder_in.to(device), targets.to(device)


def mean_loss(model, data, batch_size, pad_id, device, train=False, optimizer=None):
    """Mean per-token loss over `data`; one optimiser step per batch when training."""
    model.train(train)
    total, tokens = 0.0, 0
    for start in range(0, len(data), batch_size):
        features, decoder_in, targets = batch_tensors(
            data[start:start + batch_size], pad_id, device)
        with torch.set_grad_enabled(train), torch.autocast("cuda", dtype=torch.bfloat16):
            logits = model(input_features=features, decoder_input_ids=decoder_in).logits
        loss = torch.nn.functional.cross_entropy(
            logits.float().reshape(-1, logits.shape[-1]), targets.reshape(-1),
            ignore_index=-100, reduction="sum")
        n = int((targets != -100).sum())
        if train:
            optimizer.zero_grad()
            (loss / n).backward()
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad], 1.0)
            optimizer.step()
        total += loss.item()
        tokens += n
    return total / max(tokens, 1)


def main():
    parser = argparse.ArgumentParser(description="LoRA-train, merge and convert.")
    parser.add_argument("--name", default="lora-" + datetime.datetime.now().strftime("%Y%m%d-%H%M"))
    parser.add_argument("--clips", default=common.CLIPS_ROOT)
    parser.add_argument("--rank", type=int, default=32)
    parser.add_argument("--alpha", type=int, default=64)
    parser.add_argument("--dropout", type=float, default=0.05)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--lr", type=float, default=5e-4)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    run_dir = os.path.join(common.RUNS_DIR, args.name)
    out_dir = os.path.join(common.MODELS_DIR, args.name)
    if os.path.exists(out_dir):
        sys.exit(f"{out_dir} already exists; choose another --name.")
    os.makedirs(run_dir, exist_ok=True)

    clips = common.labelled_clips(args.clips)
    too_long = [c for c in clips if c["duration_s"] > MAX_SECONDS]
    clips = [c for c in clips if c["duration_s"] <= MAX_SECONDS]
    train = [c for c in clips if not c["held_out"]]
    held_out = [c for c in clips if c["held_out"]]
    print(f"{len(train)} clips to train on, {len(held_out)} held out, "
          f"{len(too_long)} over {MAX_SECONDS:.0f} s left out.")
    if not train:
        sys.exit("Nothing to train on: label some clips first (training\\label.py).")

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda"
    processor = WhisperProcessor.from_pretrained(common.BASE_MODEL)
    processor.tokenizer.set_prefix_tokens(
        language="english", task="transcribe", predict_timestamps=False)
    pad_id = processor.tokenizer.pad_token_id

    # float32, not bfloat16: the base is stored in float16, and a round trip
    # through bfloat16 would change every weight the LoRA never touches, so the
    # comparison with stock would measure that too. The maths runs in bfloat16
    # under autocast.
    model = WhisperForConditionalGeneration.from_pretrained(
        common.BASE_MODEL, dtype=torch.float32).to(device)
    base_revision = getattr(model.config, "_commit_hash", "") or ""
    model = get_peft_model(model, LoraConfig(
        r=args.rank, lora_alpha=args.alpha, lora_dropout=args.dropout,
        target_modules=TARGET_MODULES, bias="none"))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"Trainable: {trainable:,} of {total:,} ({100 * trainable / total:.2f} %).")

    train_data = examples(train, processor)
    held_data = examples(held_out, processor)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=args.lr, weight_decay=0.0)

    log_path = os.path.join(run_dir, "log.jsonl")
    started = time.time()
    held_loss = mean_loss(model, held_data, args.batch, pad_id, device) if held_data else None
    common.append_jsonl(log_path, {"epoch": 0, "held_out_loss": held_loss})
    print(f"epoch 0: held-out loss {held_loss}")
    train_loss = None
    for epoch in range(1, args.epochs + 1):
        random.shuffle(train_data)
        train_loss = mean_loss(model, train_data, args.batch, pad_id, device,
                               train=True, optimizer=optimizer)
        held_loss = (mean_loss(model, held_data, args.batch, pad_id, device)
                     if held_data else None)
        common.append_jsonl(log_path, {"epoch": epoch, "train_loss": train_loss,
                                       "held_out_loss": held_loss,
                                       "seconds": round(time.time() - started, 1)})
        print(f"epoch {epoch}: train loss {train_loss:.4f}, held-out loss {held_loss}")
        if not math.isfinite(train_loss):
            sys.exit("The training loss is not finite; stopping.")

    print("Merging the LoRA into the weights...")
    merged = model.merge_and_unload()
    merged_dir = os.path.join(run_dir, "merged")
    merged.to(torch.float16).save_pretrained(merged_dir)
    processor.save_pretrained(merged_dir)
    # transformers 5 saves the processor as one processor_config.json; the
    # converter copies the feature extractor's own preprocessor_config.json,
    # which faster-whisper reads for the 128 mel bins large-v3 needs.
    processor.feature_extractor.save_pretrained(merged_dir)
    del model, merged
    torch.cuda.empty_cache()

    print(f"Converting to float16 CTranslate2 in {out_dir}...")
    converter = os.path.join(os.path.dirname(sys.executable), "ct2-transformers-converter.exe")
    subprocess.run([converter, "--model", merged_dir, "--output_dir", out_dir,
                    "--copy_files", "tokenizer.json", "preprocessor_config.json",
                    "--quantization", "float16"], check=True)

    meta = {
        "name": args.name,
        "base": common.BASE_MODEL,
        "base_revision": base_revision,
        "created_utc": common.utc_now_iso(),
        "method": "lora, merged",
        "lora": {"rank": args.rank, "alpha": args.alpha, "dropout": args.dropout,
                 "targets": TARGET_MODULES, "encoder": True, "decoder": True},
        "epochs": args.epochs, "learning_rate": args.lr, "batch": args.batch,
        "seed": args.seed,
        "clips_trained": len(train), "clips_held_out": len(held_out),
        "train_loss": train_loss, "held_out_loss": held_loss,
        "converted": "ct2-transformers-converter, float16",
    }
    with open(os.path.join(out_dir, "ptt_model.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    shutil.copy(log_path, os.path.join(out_dir, "train_log.jsonl"))
    print(f"Done: {out_dir}")


if __name__ == "__main__":
    main()
