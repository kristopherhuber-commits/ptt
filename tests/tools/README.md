# `tests/tools/` — the Concierge's instruments

Not tests. Instruments (`concierge_design.md` §7.2): the harness's equivalent of
the pinned-window probe. They ship in `tests/`, never in a distribution, and
they run the **real** agent loop against a **real** llama-server with no app and
no Qt anywhere near them.

| file | what it is |
|---|---|
| `rig.py` | the bench both instruments stand on: workspace, endpoint, seams, meter, transcripts |
| `concierge_cli.py` | a terminal REPL over the real agent loop. Where the prompt is iterated |
| `qualify.py` | the 41-scenario qualification suite runner (design §6). Emits a scorecard |
| `scoring.py` | the machine checks behind it — the derived settings whitelist and the rest |
| `scenarios.yaml` | the forty-one scenarios, as data |
| `seeds/` | seeded `debug_log.txt` files for the diagnosis and adversarial classes |
| `contention.py` | NFR-CG-3's instrument: dictation latency with the Concierge model absent, resident and generating |
| `runs/` | transcripts and scorecards (git-ignored) |

`tests/test_concierge_suite.py` is the L1 half: it pins the scorers and asserts
the scenario file is well formed, so a mistyped check name is a failing test
rather than a check that silently never runs.

## Running them

Both take the same endpoint flags, so a candidate model is one flag:

```
python tests/tools/concierge_cli.py                       # bundled runtime + pinned GGUF
python tests/tools/concierge_cli.py --tool-mode native --fake-tools
python tests/tools/concierge_cli.py --base-url http://127.0.0.1:8080

python tests/tools/qualify.py --dry-run                   # validate scenarios, no model
python tests/tools/qualify.py --model path\to\candidate.gguf --tool-mode native
python tests/tools/qualify.py --base-url http://127.0.0.1:8080 --label "20B MoE" --append
```

`contention.py` is the odd one out and takes neither set of flags: it needs a **GPU,
Whisper and llama-server at once**, because NFR-CG-3 is a question about two models
sharing one card.

```
python tests/tools/contention.py --rounds 10 --json out.json
python tests/tools/contention.py --rounds 3 --durations 5,10
```

Its output is `verification.md` §5.4's `V-M-75`. Re-run it if the pinned llama.cpp build
moves, if the Concierge's model changes, or on different hardware — `concierge_design.md`
§10 Q3 accepts GPU contention on the strength of these numbers and says to revisit only
if L3 breaches NFR-CG-3.

`--append` writes the scorecard into `docs/ptt-v3-concierge/model_qualification.md`,
which is gate 2.5's append-only record.

`--fake-tools` swaps PortAudio, Whisper and the installed-model scan for
deterministic stand-ins; without it those seams are real, except `run_benchmark`,
which needs `--real-benchmark` before it will put a Whisper model in VRAM beside
a 9.4 GB LLM.

## Two things they do not touch

**Your settings and your log.** `rig.open_workspace()` rebinds `paths.APP_DIR`
to `tests/tools/.rig/` before anything is constructed, so `config.json`,
`debug_log.txt`, the memory note, `concierge_state.json` and `concierge_key` all
land there. The suite's write class does nothing but call `set_config`; an
instrument that edited the developer's real configuration while measuring a
model is one nobody could trust twice.

**Qt.** Nothing here imports it, and `test_concierge_layering.py` is what keeps
that true rather than merely stated (CON-CG-6).

## What a run leaves behind

`runs/<kind>-<stamp>/` holds `transcript.jsonl` (to grade and to diff) and
`transcript.md` (to read), and a qualification run adds `scorecard.json`. Every
one of them opens with the provenance block: the model, the tool mode, the
reasoning budget, and **the SHA-256 of the system prompt and of the knowledge
pack** — without those last two the suite measures the prompt and the pack
rather than the model (Q17, Q20), and two candidates scored either side of a
prompt edit are not comparable.
