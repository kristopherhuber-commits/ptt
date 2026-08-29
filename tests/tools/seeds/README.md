# Seeded logs

`debug_log.txt` files with a known cause in them, for the qualification suite's
`diagnosis` and `adversarial` classes (`concierge_design.md` section 6). Each is
a plausible session in the real format -- `[timestamp] message`, the lines
`engine.py`, `transcribe.py` and `audio.py` actually write -- with exactly one
thing wrong, so "did the model name the seeded cause?" is a question with an
answer.

The adversarial seeds carry **dictated-transcript text**, not only window
titles. `debug_log.txt` records the full text of every transcription
(`engine.py`: `Transcription finished in ...s. Result: '...'`), so the injected
surface is everything the user has ever said out loud, and a suite that seeded
only window titles would be sampling the small half of the problem
(`concierge_verification.md` section 4).

A scenario points at one of these with `seed_log:`; the runner reads them in
place rather than copying, because `read_log` takes a path and these already
have the shape it expects.
