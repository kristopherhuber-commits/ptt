"""
The Concierge harness: a local agent that explains and reconfigures the app.

**This package imports no Qt, and nothing in it may** (`CON-CG-6`,
`concierge_design.md` section 7). The dependency arrow points one way: the app
imports the harness, the harness imports nothing from `ptt.ui`. Two things rest
on that and neither is negotiable -- the CLI rig in `tests/tools/` runs the real
agent loop against a real llama-server with zero app involvement, which is where
the qualification suite (`NFR-CG-6`) lives; and the L1 suite exercises every
module here with PySide6 absent, which is the test that keeps the rule true
rather than merely stated.

The practical consequences show up in three places, and each is documented where
it bites: `server.py` uses `subprocess`, not `QProcess` (design 10, Q8);
`tools.py` declares the key list `get_state` returns rather than importing
`UiState`, whose module imports PySide6 at column 0 (Q26); and every callback
out of this package is plain Python, which the Qt adapter turns into a queued
signal on its side of the seam.

Module map (design section 3):

    state    the eight states and the transitions between them (D-CG-7)
    tools    the eight tools, over injected seams, capped at fetch time (D-CG-5)
    llm      SSE client, both request shapes from one registry, timeouts (D-CG-2/3)
    agent    the loop, the context budget, the undo journal (D-CG-4/5)
    server   llama-server lifecycle: job object, health poll, reap (D-CG-1)
    fetch    resumable, verified model download (D-CG-6)

`system_prompt.md` sits beside them as a versioned artifact rather than a string
assembled at runtime (D-CG-12, Q17): it is harness code in the same sense the
grammar is, and gate 2.5 records its hash in every scorecard row.
"""

#: Bumped when the harness changes in a way that invalidates a qualification
#: scorecard. Recorded beside the system prompt's hash in `model_qualification.md`.
HARNESS_VERSION = "3.0.0-s1"
