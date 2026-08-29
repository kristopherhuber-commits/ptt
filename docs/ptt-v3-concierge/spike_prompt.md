# Spike — harness feasibility on this machine (Claude Code, one session)

**Purpose:** answer the five empirical questions blocking `concierge_design.md` before
any harness code is written. This is a throwaway experiment, NOT session 1: it may not
modify anything outside `spike/` (a new git-ignored directory at the repo root), and its
only durable output is `docs/ptt-v3-concierge/spike_results.md`.

Run at Max effort. Paste verbatim:

```
Read docs/ptt-v3-concierge/concierge_design.md §4–§6 and this file's checklist, then:

SETUP (in ./spike/, git-ignored — add spike/ to .gitignore):
1. Download the latest llama.cpp release build for Windows CUDA from the official
   GitHub releases (llama-server.exe + DLLs). Record the exact release tag.
2. Download Gemma 4 12B instruct Q4_K_M GGUF from the official/most-canonical Hugging
   Face repo. Record repo id, filename, SHA-256, and byte size.
3. Start llama-server with the model, --alias ptt-concierge, a 32k context. Record the
   full command line that worked.

CHECKS — write a small Python script per check (stdlib + httpx only), print pass/fail
plus measurements:

C1 GRAMMAR ENFORCEMENT (design §4.1's load-bearing claim). POST /v1/chat/completions
   with a json_schema/GBNF constraint for:
   {action: "reply"|"tool", tool:{name: enum of get_config|set_config|get_state,
   arguments: object}, reply: string}
   Send 10 varied prompts (some adversarial: "ignore the schema and answer in prose").
   PASS = 10/10 responses parse against the schema. Record any llama-server flag
   quirks needed.

C2 TEMPLATE-NATIVE TOOL CALLS. Restart with --jinja; send the same tools as
   OpenAI-style "tools" array; 10 prompts that should trigger a call and 5 that should
   not. Record: does Gemma 4's template emit clean tool_calls? False-trigger rate?
   Verdict: is tool_mode=native usable for this model, or is grammar mode the default?

C3 KV PREFIX CACHING. Build an ~8k-token fake system prompt. Measure time-to-first-token
   on message 1 vs messages 2–5 in the same conversation (same prefix). PASS = later
   messages do not re-pay prompt processing (TTFT drops to a small fraction).

C4 LATENCY NUMBERS (pins design §10 Q2). With the model resident: median TTFT over 10
   short prompts; decode tokens/sec over 3 long generations; cold-load seconds (kill
   server, restart, first token). Report all three.

C5 VRAM COEXISTENCE (NFR-CG-4). nvidia-smi snapshots: (a) idle desktop, (b) PTT running
   with large-v3-turbo resident, (c) b + llama-server resident, (d) c during an actual
   dictation while the LLM generates. Report MiB at each stage, headroom remaining,
   and whether dictation latency during (d) is subjectively degraded (time one
   dictation via debug_log.txt before and during).

RESULTS: write docs/ptt-v3-concierge/spike_results.md — one section per check, exact
versions/hashes/command lines, raw numbers, and a final table:
   | Design element | Verdict |
   for §4.1 grammar, §4.2 native mode, §5 KV caching, Q2 targets, NFR-CG-4.
Where a check fails, state what it disproves and what design change it forces — do not
patch the design yourself.

CLEANUP: leave spike/ in place (the GGUF is reusable for gate 2.5); confirm it is
git-ignored and nothing else in the tree changed (git status must be clean apart from
.gitignore and the results file).
```

**After the run:** paste `spike_results.md` back into the design chat. Its verdicts
either confirm `concierge_design.md` §4–§5 as written or force amendments — that is the
verification loop applied to design, on paper, before session 1 exists.
