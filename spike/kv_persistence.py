"""
Mini-spike C6 -- does the knowledge pack's KV prefix survive a server restart?

`concierge_design.md` section 5, decision Q6. The spike measured that the pack is
processed on the **first request that carries it** (7.17 s to first token), not
at model load, and that design 5 therefore needs one of two ready paths:

- **Persistence.** If llama-server can save a slot's KV cache to disk and restore
  it into a fresh process, the pack costs nothing on a reopen and NFR-CG-1 [2 s]
  and NFR-CG-2 [10 s] both hold as originally bracketed.
- **Prewarm fallback.** Otherwise the cost is paid inside the `loading` state as a
  throwaway `max_tokens: 1` request, measured at 13.34 s to genuinely ready, and
  NFR-CG-2 restates to [15 s].

This script decides which. Standalone and stdlib-only, under `spike/`, and it is
not part of the shipped application.

    python spike/kv_persistence.py

`-cram/--cache-ram` is *not* the mechanism under test: it is an in-process RAM
cache and cannot outlive the process by construction. `--slot-save-path` plus the
`/slots/{id}?action=save|restore` endpoints is the only path that could.
`-np 1` throughout (Q14), which is also what makes "slot 0" unambiguous.
"""

import http.client
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(HERE, "llama", "bin", "llama-server.exe")
MODEL = os.path.join(HERE, "models", "gemma-4-12B-it-Q4_K_M.gguf")
PACK = os.path.join(ROOT, "app", "assets", "concierge_kb.md")
SLOTS = os.path.join(HERE, "out", "slots")
OUT = os.path.join(HERE, "out", "c6_kv_persistence.json")
PORT = 8099
HOST = "127.0.0.1"


def request(method, path, body=None, timeout=120):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    try:
        return response.status, json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return response.status, {"raw": raw[:400].decode("utf-8", errors="replace")}


def healthy():
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=2)
        conn.request("GET", "/health")
        status = conn.getresponse().status
        conn.close()
        return status == 200
    except Exception:
        return False


def start(log_name):
    os.makedirs(SLOTS, exist_ok=True)
    os.makedirs(os.path.join(HERE, "out"), exist_ok=True)
    args = [
        EXE, "-m", MODEL, "--alias", "ptt-concierge",
        "-c", "32768", "-ngl", "999",
        "--host", HOST, "--port", str(PORT),
        "-np", "1", "-rea", "off",
        "--slot-save-path", SLOTS,
    ]
    log = open(os.path.join(HERE, "out", log_name), "wb")
    t0 = time.perf_counter()
    proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    while time.perf_counter() - t0 < 180:
        if proc.poll() is not None:
            raise RuntimeError(f"llama-server exited with {proc.returncode}; see {log_name}")
        if healthy():
            return proc, time.perf_counter() - t0
        time.sleep(0.25)
    proc.kill()
    raise RuntimeError("llama-server never became healthy")


def stop(proc):
    try:
        proc.terminate()
        proc.wait(timeout=30)
    except Exception:
        proc.kill()


def pack_text():
    with open(PACK, "r", encoding="utf-8") as f:
        return f.read()


def ask(pack, question, max_tokens=16):
    """One request carrying the pack. Returns (ttft, timings)."""
    body = {
        "messages": [
            {"role": "system", "content": pack},
            {"role": "user", "content": question},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "stream": False,
    }
    t0 = time.perf_counter()
    status, payload = request("POST", "/v1/chat/completions", body)
    elapsed = time.perf_counter() - t0
    timings = payload.get("timings") or {}
    usage = payload.get("usage") or {}
    return {
        "status": status,
        "seconds": round(elapsed, 3),
        "prompt_n": timings.get("prompt_n"),
        "cache_n": timings.get("cache_n"),
        "prompt_ms": timings.get("prompt_ms"),
        "prompt_tokens": usage.get("prompt_tokens"),
    }


def main():
    if not os.path.exists(EXE):
        print(f"llama-server not found at {EXE}")
        return 1
    if not os.path.exists(PACK):
        print(f"knowledge pack not found at {PACK}; run build_knowledge_pack.py")
        return 1

    pack = pack_text()
    results = {"pack_characters": len(pack), "slot_save_path": SLOTS}
    question = "In one sentence: what does the pre-roll buffer do?"

    print("== run 1: cold server, warm the pack, save slot 0 ==")
    proc, load_seconds = start("c6_server_1.log")
    results["run1_load_seconds"] = round(load_seconds, 3)
    try:
        results["run1_first"] = ask(pack, question)
        print("  first request:", results["run1_first"])
        results["run1_second"] = ask(pack, question)
        print("  second request (in-process cache):", results["run1_second"])

        status, payload = request(
            "POST", "/slots/0?action=save", {"filename": "concierge_pack.bin"})
        results["save"] = {"status": status, "body": payload}
        print("  save:", status, json.dumps(payload)[:300])
    finally:
        stop(proc)

    saved = [f for f in os.listdir(SLOTS)] if os.path.isdir(SLOTS) else []
    results["slot_files"] = saved
    print("  slot files on disk:", saved)

    print("== run 2: fresh server, restore slot 0, ask again ==")
    proc, load_seconds = start("c6_server_2.log")
    results["run2_load_seconds"] = round(load_seconds, 3)
    try:
        status, payload = request(
            "POST", "/slots/0?action=restore", {"filename": "concierge_pack.bin"})
        results["restore"] = {"status": status, "body": payload}
        print("  restore:", status, json.dumps(payload)[:300])
        results["run2_first"] = ask(pack, question)
        print("  first request after restore:", results["run2_first"])
    finally:
        stop(proc)

    print("== run 3: control -- fresh server, no restore ==")
    proc, load_seconds = start("c6_server_3.log")
    results["run3_load_seconds"] = round(load_seconds, 3)
    try:
        results["run3_first"] = ask(pack, question)
        print("  first request, no restore:", results["run3_first"])
    finally:
        stop(proc)

    restored = results.get("run2_first") or {}
    control = results.get("run3_first") or {}
    persisted = (
        results.get("restore", {}).get("status") == 200
        and isinstance(restored.get("cache_n"), int)
        and isinstance(control.get("cache_n"), int)
        and restored["cache_n"] > control["cache_n"] + 100
    )
    results["verdict"] = "persistence works" if persisted else "persistence does not work"
    results["kv_persistence_works"] = bool(persisted)

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"\nVERDICT: {results['verdict']}  ->  {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
