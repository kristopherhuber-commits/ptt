"""
Fetch and unpack the pinned llama.cpp runtime into `app/llama/`. **Build time only.**

CON-CG-2: the runtime is a bundled `llama-server` (llama.cpp, MIT), with no
dependence on Ollama, LM Studio or anything separately installed. This is the
step that puts it there, and it is deliberately a script at the repository root
rather than anything inside `app/`: FR-CG-10 says the Concierge makes no network
connection except the model download, and it means the *running* application.
Living outside the package is a stronger guarantee than a comment saying it must
never run there, and `fetch.bundle_llama_runtime` refuses to run at all without
an explicit token that only this file passes.

**The release tag carries no binaries** (spike setup finding 4). GitHub's latest
release for `ggml-org/llama.cpp` is `v0.3.0`, whose only asset is
`nightly-tag.txt` containing `b10621`; the artefacts live on that nightly tag.
A step that fetches assets from the versioned tag finds nothing and looks like a
network failure, so the indirection is resolved here.

**Two archives, not one.** The CUDA runtime DLLs are not in the binaries zip.
A build that ships only the first produces an executable that will not start on
a clean machine.

`cuda-12.4` rather than `cuda-13.3`: the reference machine's driver advertises
CUDA 13.0, so a 13.3 build would rest on minor-version compatibility, and 12.4
matches the CUDA 12 runtime the application already ships for CTranslate2.

    python build_llama_runtime.py [--tag b10621]

Session 5 adds the unpacked result to `build_portable.py`'s allowlist, with
llama.cpp's MIT LICENSE beside it -- the OFL precedent (`V-M-64`) is that a
bundled component's licence file travels with it.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))

from ptt.concierge import fetch     # noqa: E402

DESTINATION = os.path.join("app", "llama")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default=fetch.LLAMA_BUILD_TAG,
                        help="llama.cpp nightly build tag (default: the pinned one)")
    parser.add_argument("--nightly-tag-file",
                        help="a downloaded nightly-tag.txt to resolve instead of "
                             "using --tag; this is the indirection the versioned "
                             "release publishes")
    parser.add_argument("--to", default=DESTINATION,
                        help=f"where to unpack (default: {DESTINATION})")
    args = parser.parse_args()

    tag = args.tag
    if args.nightly_tag_file:
        with open(args.nightly_tag_file, "r", encoding="utf-8") as f:
            tag = fetch.resolve_nightly_tag(f.read())
        print(f"Resolved {args.nightly_tag_file} -> {tag}")

    if tag != fetch.LLAMA_BUILD_TAG:
        print(f"WARNING: {tag} is not the pinned build "
              f"({fetch.LLAMA_BUILD_TAG}). Every measurement in "
              f"spike_results.md and every qualification scorecard was taken "
              f"against the pinned one; a different build is a "
              f"re-qualification, not an upgrade.")

    print(f"Bundling llama.cpp {tag} ({fetch.LLAMA_CUDA_VARIANT}) into {args.to} ...")
    fetch.bundle_llama_runtime(args.to, build_time=fetch.BUILD_TIME_ONLY,
                               build_tag=tag)

    exe = os.path.join(args.to, "llama-server.exe")
    if os.path.exists(exe):
        print(f"Done: {exe}")
        return 0
    print(f"ERROR: {exe} is not there after unpacking. The archive layout may "
          f"have changed; check {args.to}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
