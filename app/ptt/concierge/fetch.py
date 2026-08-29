"""
D-CG-6 -- the resumable, verified model download (FR-CG-7).

Two controls, and they catch different failures (Q26):

- **The pinned SHA-256 is the authority.** It is compared against the Hugging
  Face tree API's LFS `oid` *before* a single byte is downloaded, so a
  re-uploaded file is refused rather than accepted -- and only a pin makes "a
  new GGUF is a re-qualification, never a silent bump" enforceable. Without one,
  a changed upstream file verifies cleanly against its own new `oid` and every
  qualification scorecard is silently invalidated.
- **The post-download digest catches corruption** -- a truncated or mangled
  transfer, which an `oid` fetched from the same place tells you nothing about.

FR-CG-10's network allowlist is `ALLOWED_HOSTS` plus `ALLOWED_HOST_SUFFIXES`,
enumerated here and asserted by an L1 socket monkeypatch. Nothing else in the
harness opens an outbound connection: the chat is loopback, and loopback is not
a network connection.

**The llama.cpp bundling helper is build-time only and must never run in the
shipped app.** It is guarded by an explicit argument rather than by a comment,
and an L1 test asserts that no module under `app/` calls it.
"""

import hashlib
import json
import os
import urllib.parse
from typing import NamedTuple

from ptt.logging_setup import log_debug

# -- FR-CG-10's enumerated allowlist ------------------------------------------

#: The tree API's host, exactly.
ALLOWED_HOSTS = ("huggingface.co",)

#: The LFS CDN hosts a download redirect may name. Suffixes rather than exact
#: names because the CDN's hostnames are regional and rotate; the constraint
#: that matters is that a redirect cannot walk us off Hugging Face entirely.
ALLOWED_HOST_SUFFIXES = (".huggingface.co", ".hf.co")


def is_allowed_host(host):
    """Whether one hostname is inside FR-CG-10's permitted set."""
    host = (host or "").lower().split(":")[0]
    if host in ALLOWED_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in ALLOWED_HOST_SUFFIXES)


def check_url(url):
    """`(ok, reason)` for one URL against the allowlist. Never raises."""
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme != "https":
        return False, f"{parts.scheme or 'no'} is not https"
    if not is_allowed_host(parts.hostname):
        return False, f"{parts.hostname!r} is not a permitted host"
    return True, None


# -- what is pinned -----------------------------------------------------------

class ModelSpec(NamedTuple):
    """
    One qualified GGUF, pinned exactly (`concierge_handoff.md` 1).

    `sha256` is the authority, not a convenience. The text-only figure is the
    one that matters for the distribution: the multimodal `mmproj` projector is
    a separate ~175 MB file and is not downloaded, because the Concierge is a
    text agent.
    """
    key: str
    repo: str
    filename: str
    sha256: str
    size_bytes: int
    label: str

    @property
    def gigabytes(self):
        return self.size_bytes / (1024 ** 3)

    def tree_url(self):
        return f"https://huggingface.co/api/models/{self.repo}/tree/main"

    def download_url(self):
        return f"https://huggingface.co/{self.repo}/resolve/main/{self.filename}"


#: The v3.0 tier. Keyed by the value `concierge.model` takes, so `config.FIELDS`
#: and this table name the same thing and a second tier is one row plus one
#: choice.
MODELS = {
    "gemma-4-12b-q4_k_m": ModelSpec(
        key="gemma-4-12b-q4_k_m",
        repo="lmstudio-community/gemma-4-12B-it-GGUF",
        filename="gemma-4-12B-it-Q4_K_M.gguf",
        sha256="95d83ba36642b1f385fb906b5962a71763361be3bac930a709945f72d97473f8",
        size_bytes=7381382944,
        label="Gemma 4 12B",
    ),
}


def spec_for(key):
    return MODELS.get(key)


# -- the transport ------------------------------------------------------------

class HttpsTransport:
    """
    The one outbound seam. Stdlib `urllib`, checked against the allowlist.

    Every call goes through `check_url` first, including the URL a redirect
    names, so FR-CG-10 is enforced by code rather than described in a paragraph.
    """

    def __init__(self, timeout=30.0, user_agent="ptt-dictation/3.0"):
        self.timeout = timeout
        self.user_agent = user_agent

    def get_json(self, url):
        import urllib.request
        ok, reason = check_url(url)
        if not ok:
            raise PermissionError(f"refusing to fetch {url!r}: {reason}")
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def open_range(self, url, start=0):
        """
        Open `url` from byte `start`. Returns `(status, total_bytes, reader)`.

        `total_bytes` is the size of the **whole** file, not of this response:
        with a `Range` header the server answers 206 and a `Content-Range`
        whose last field is the full length, and the progress bar needs the
        whole so a resumed download does not restart at 0 %.
        """
        import urllib.request
        ok, reason = check_url(url)
        if not ok:
            raise PermissionError(f"refusing to fetch {url!r}: {reason}")
        headers = {"User-Agent": self.user_agent}
        if start:
            headers["Range"] = f"bytes={start}-"
        request = urllib.request.Request(url, headers=headers)
        response = urllib.request.urlopen(request, timeout=self.timeout)

        final = response.geturl()
        ok, reason = check_url(final)
        if not ok:
            response.close()
            raise PermissionError(f"refusing a redirect to {final!r}: {reason}")

        total = None
        content_range = response.headers.get("Content-Range")
        if content_range and "/" in content_range:
            tail = content_range.rsplit("/", 1)[1].strip()
            if tail.isdigit():
                total = int(tail)
        if total is None:
            length = response.headers.get("Content-Length")
            total = (int(length) + start) if length and length.isdigit() else None
        return response.status, total, response


# -- what the progress bar says -----------------------------------------------
#
# Pure, and here rather than in the panel, because the same two numbers are
# rendered in three places -- the panel's caption, the state machine's `detail`
# and the status bar -- and three copies of "divide by 1024 three times" is how
# the three come to disagree about whether 6.87 GB is 6.87 or 6.9.

def human_bytes(count):
    """`7381382944` -> `"6.87 GB"`. Binary units, matching handoff section 1."""
    try:
        count = float(count)
    except (TypeError, ValueError):
        return "?"
    for unit, size, places in (("GB", 1024 ** 3, 2), ("MB", 1024 ** 2, 0),
                               ("KB", 1024, 0)):
        if count >= size:
            return f"{count / size:.{places}f} {unit}"
    return f"{int(count)} B"


def percent_of(done, total):
    """`0`-`100`, clamped, and `0` rather than a crash when the total is unknown."""
    try:
        done, total = float(done), float(total)
    except (TypeError, ValueError):
        return 0
    if total <= 0:
        return 0
    return max(0, min(100, int(done * 100 // total)))


def progress_text(done, total):
    """
    The one sentence the download reports itself with.

    It carries both the fraction and the absolute figures on purpose: a
    percentage alone tells somebody watching a 6.87 GB transfer nothing about
    how long is left, and the absolute pair is what makes a resumed download
    legible as a resume rather than as a restart.
    """
    return f"{human_bytes(done)} of {human_bytes(total)} · {percent_of(done, total)}%"


# -- the download -------------------------------------------------------------

class Download:
    """
    One model file, fetched resumably and verified twice.

    The partial file is `<name>.part` beside the destination. Resuming is a
    `Range` request from its current length, which is what makes "kill during
    download, relaunch" (acceptance criterion v3-5) a resume rather than a
    restart of 6.87 GB.
    """

    CHUNK = 1024 * 1024

    #: `run()`'s reason when `should_cancel` asked it to stop. A sentinel rather
    #: than a sentence because the caller has to tell "the user quit" apart from
    #: "the transfer broke" -- one of those is worth reporting and the other is
    #: what the user just asked for.
    CANCELLED = "cancelled"

    def __init__(self, spec, directory, transport=None, on_progress=None,
                 should_cancel=None):
        self.spec = spec
        self.directory = directory
        self.transport = transport or HttpsTransport()
        self.on_progress = on_progress or (lambda _done, _total: None)
        #: Polled once per chunk. The application exits while a 6.87 GB transfer
        #: is in flight far more often than it finishes one, and a download that
        #: cannot be interrupted holds the shutdown path open for as long as the
        #: rest of the file takes.
        self.should_cancel = should_cancel or (lambda: False)
        #: Set by `run()` when the published `oid` disagreed with the pin. The
        #: caller latches on this: a refusal is a re-qualification event and not
        #: a retryable failure (FR-CG-7, Q26).
        self.refused = False

    @property
    def path(self):
        return os.path.join(self.directory, self.spec.filename)

    @property
    def partial_path(self):
        return self.path + ".part"

    def already_have(self):
        """Whether the verified file is already on disk."""
        return os.path.exists(self.path) and os.path.getsize(self.path) == self.spec.size_bytes

    def partial_bytes(self):
        """
        How much of a resumable transfer is already on disk, or 0.

        The panel reads it before a download starts, so the card can offer
        "Resume" over a figure rather than "Download" over nothing -- which is
        the visible half of criterion v3-5, and the only way a user can tell
        that relaunching resumed rather than started again.
        """
        try:
            return os.path.getsize(self.partial_path)
        except OSError:
            return 0

    # -- the pre-download cross-check (Q26) ---------------------------------

    def remote_oid(self):
        """
        The LFS `oid` the tree API publishes for this file, or None.

        Hugging Face reports it as `{"lfs": {"oid": "<sha256>", "size": N}}`, and
        the spike confirmed the value matches the file's real SHA-256 exactly.
        """
        listing = self.transport.get_json(self.spec.tree_url())
        for entry in listing or []:
            if entry.get("path") != self.spec.filename:
                continue
            lfs = entry.get("lfs") or {}
            oid = lfs.get("oid") or lfs.get("sha256")
            return (oid or "").lower() or None
        return None

    def verify_remote(self):
        """
        Compare the published `oid` with the pin, **before downloading**.

        `(ok, reason)`. A mismatch is a re-qualification event, not something
        the user can click past: the model that would arrive is not the model
        the scorecard in `model_qualification.md` was written about.

        A tree API that cannot be reached is *not* a refusal. The pin still
        guards the bytes after they land, so an unreachable API costs the early
        warning and nothing else -- and refusing to download because a metadata
        endpoint was briefly unavailable would be a worse failure than the one
        it prevents.
        """
        try:
            oid = self.remote_oid()
        except Exception as e:
            log_debug(f"Concierge: could not read the Hugging Face tree API "
                      f"({str(e)}); the pinned digest still verifies the file.")
            return True, None
        if oid is None:
            log_debug("Concierge: the tree API listed no LFS oid for "
                      f"{self.spec.filename}; the pinned digest still applies.")
            return True, None
        if oid != self.spec.sha256.lower():
            # Short digests in the sentence, full ones in the log. The sentence
            # is rendered in a 360 px panel and a 64-character hex string has no
            # break opportunity in it, so a full digest is a line that runs off
            # the edge -- and this is the one message in the application the
            # user is deliberately not able to click past, so it has to be
            # readable. `debug_log.txt` is where an investigator looks anyway.
            reason = (
                f"the file published as {self.spec.filename} has digest "
                f"{oid[:12]}…, not the pinned {self.spec.sha256[:12]}…. This is "
                f"not the model this build was qualified against, so it will "
                f"not be downloaded. Both digests in full are in "
                f"debug_log.txt.")
            log_debug(f"Concierge: REFUSED the download of {self.spec.filename} "
                      f"-- published oid {oid}, pinned {self.spec.sha256}.")
            return False, reason
        log_debug(f"Concierge: the published oid matches the pin for {self.spec.filename}.")
        return True, None

    # -- the transfer -------------------------------------------------------

    def run(self):
        """
        Fetch the file, resuming if a partial one is there. `(ok, reason)`.

        Order: cross-check the remote digest, transfer, hash what landed,
        compare with the pin, then rename into place. The rename is last so a
        file at the final path is always a verified file -- a half-written GGUF
        that looks complete is a model load failure with no explanation.

        A cancellation is **not** a failure and does not discard anything: the
        `.part` file it leaves is exactly what the next launch resumes from.
        """
        self.refused = False
        if self.already_have():
            return True, None

        ok, reason = self.verify_remote()
        if not ok:
            self.refused = True
            return False, reason
        if self.should_cancel():
            return False, self.CANCELLED

        os.makedirs(self.directory, exist_ok=True)
        start = os.path.getsize(self.partial_path) if os.path.exists(self.partial_path) else 0
        if start:
            log_debug(f"Concierge: resuming {self.spec.filename} at {start} bytes.")

        try:
            status, total, reader = self.transport.open_range(
                self.spec.download_url(), start)
        except PermissionError as e:
            return False, str(e)
        except Exception as e:
            return False, f"the download could not start: {str(e)}"

        if start and status != 206:
            # The server ignored the Range header. Starting over is correct and
            # rare; appending to a partial file would produce a corrupt one that
            # only the final digest would catch, after another 6.87 GB.
            log_debug("Concierge: the server ignored the resume request; "
                      "starting the download again.")
            start = 0
            mode = "wb"
        else:
            mode = "ab" if start else "wb"

        total = total or self.spec.size_bytes
        done = start
        cancelled = False
        try:
            with open(self.partial_path, mode) as f:
                self.on_progress(done, total)
                while True:
                    if self.should_cancel():
                        cancelled = True
                        break
                    block = reader.read(self.CHUNK)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    self.on_progress(done, total)
        except Exception as e:
            return False, f"the download stopped at {done} bytes: {str(e)}"
        finally:
            try:
                reader.close()
            except Exception:
                pass

        if cancelled:
            log_debug(f"Concierge: the download was cancelled at {done} bytes; "
                      f"the partial file is kept for the next launch.")
            return False, self.CANCELLED

        digest = sha256_of(self.partial_path)
        if digest != self.spec.sha256.lower():
            log_debug(f"Concierge: {self.spec.filename} hashed {digest}, expected "
                      f"{self.spec.sha256}; the partial file is discarded.")
            try:
                os.remove(self.partial_path)
            except OSError:
                pass
            return False, ("the downloaded file did not match its pinned "
                           "digest and was discarded")

        os.replace(self.partial_path, self.path)
        log_debug(f"Concierge: {self.spec.filename} downloaded and verified.")
        return True, None


def sha256_of(path, chunk=1024 * 1024):
    """The file's SHA-256, read in blocks so a 6.87 GB file is not a 6.87 GB read."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                return digest.hexdigest()
            digest.update(block)


# -- the llama.cpp bundling helper: BUILD TIME ONLY ---------------------------

#: The pinned runtime (`concierge_handoff.md` 1, spike setup 1).
LLAMA_RELEASE_TAG = "v0.3.0"
LLAMA_BUILD_TAG = "b10621"
LLAMA_CUDA_VARIANT = "cuda-12.4"

#: The two assets. The CUDA runtime DLLs are **not** in the binaries zip; the
#: separate `cudart-*` zip is required, and a build that ships only the first
#: produces an executable that will not start on a clean machine.
LLAMA_ASSETS = (
    f"llama-{LLAMA_BUILD_TAG}-bin-win-{LLAMA_CUDA_VARIANT}-x64.zip",
    f"cudart-llama-bin-win-{LLAMA_CUDA_VARIANT}-x64.zip",
)

#: llama.cpp's own MIT licence, which **neither archive contains**. The binaries
#: zip carries `LICENSE-LLVM-OpenMP` for the one vendored dependency and nothing
#: for llama.cpp itself, so a distribution assembled from the two zips alone
#: ships the MIT-licensed component without the notice MIT requires.
#:
#: Fetched from the pinned tag rather than from `master`: the licence that
#: travels with a binary is the licence of the source it was built from, and
#: `b10621` is what the pin names.
LLAMA_LICENSE_URL = (
    f"https://raw.githubusercontent.com/ggml-org/llama.cpp/{LLAMA_BUILD_TAG}/LICENSE"
)

#: What it is called on disk. `LICENSE` alone would sit in `app/llama/` next to
#: `LICENSE-LLVM-OpenMP` and read as though it covered both.
LLAMA_LICENSE_NAME = "LICENSE-llama.cpp"

#: The explicit token a build script passes. A flag rather than a comment
#: because "never runs in the shipped app" is a property somebody has to be able
#: to check, and `grep` for this constant is that check.
BUILD_TIME_ONLY = "build-time-only"


def resolve_nightly_tag(text):
    """
    Turn `nightly-tag.txt`'s contents into a build tag.

    Spike finding 4: **the versioned release carries no binaries.** `v0.3.0`'s
    only asset is `nightly-tag.txt`, containing `b10621`, and the actual
    artefacts live on that nightly tag. Any bundling step must resolve the
    indirection to find a download URL; fetching assets from the versioned tag
    finds nothing and looks like a network failure.

    Pure, so the indirection is unit-testable without GitHub.
    """
    tag = (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""
    if not tag.startswith("b") or not tag[1:].isdigit():
        raise ValueError(f"{tag!r} is not a llama.cpp nightly tag")
    return tag


def llama_asset_urls(build_tag=LLAMA_BUILD_TAG):
    """The two download URLs for a resolved build tag."""
    base = f"https://github.com/ggml-org/llama.cpp/releases/download/{build_tag}"
    return tuple(
        f"{base}/llama-{build_tag}-bin-win-{LLAMA_CUDA_VARIANT}-x64.zip"
        if name.startswith("llama-") else f"{base}/{name}"
        for name in LLAMA_ASSETS
    )


def bundle_llama_runtime(destination, build_time=None, transport=None,
                         build_tag=LLAMA_BUILD_TAG):
    """
    Download and unpack the pinned llama.cpp runtime. **Build time only.**

    This is the one function in the harness that reaches a host outside
    FR-CG-10's allowlist, and that is exactly why it may not run in the shipped
    application: the requirement says the Concierge makes no network connection
    except the model download, and it means the *running* app. The guard is an
    argument the caller has to pass on purpose, and `build_llama_runtime.py` is
    its only caller.
    """
    if build_time != BUILD_TIME_ONLY:
        raise RuntimeError(
            "bundle_llama_runtime is a build step and never runs in the shipped "
            "app (FR-CG-10). Call it from build_llama_runtime.py with "
            "build_time=fetch.BUILD_TIME_ONLY.")

    import urllib.request
    import zipfile

    os.makedirs(destination, exist_ok=True)
    for url in llama_asset_urls(build_tag):
        name = url.rsplit("/", 1)[1]
        archive = os.path.join(destination, name)
        if not os.path.exists(archive):
            print(f"  downloading {name} ...")
            with urllib.request.urlopen(url, timeout=120) as response, \
                    open(archive, "wb") as f:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    f.write(block)
        print(f"  unpacking {name} ...")
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)

    fetch_llama_licence(destination, build_time=build_time, build_tag=build_tag)
    return destination


def fetch_llama_licence(destination, build_time=None,
                        build_tag=LLAMA_BUILD_TAG):
    """
    Put llama.cpp's MIT licence beside the binaries. **Build time only.**

    Its own function, and idempotent, because the two archives are 640 MB and
    the licence is 1 KB: a build machine that already has the runtime unpacked
    must be able to acquire the notice without re-fetching the binaries it is a
    notice for.
    """
    if build_time != BUILD_TIME_ONLY:
        raise RuntimeError(
            "fetch_llama_licence is a build step and never runs in the shipped "
            "app (FR-CG-10). Call it from build_llama_runtime.py with "
            "build_time=fetch.BUILD_TIME_ONLY.")

    import urllib.request

    licence = os.path.join(destination, LLAMA_LICENSE_NAME)
    if os.path.exists(licence):
        return licence
    url = LLAMA_LICENSE_URL.replace(LLAMA_BUILD_TAG, build_tag)
    print(f"  downloading {LLAMA_LICENSE_NAME} ...")
    with urllib.request.urlopen(url, timeout=60) as response:
        text = response.read()
    if b"MIT License" not in text:
        raise RuntimeError(
            f"{url} does not look like llama.cpp's MIT licence "
            f"({len(text)} bytes). CON-CG-2 bundles an MIT component and the "
            f"notice has to travel with it; check the tag by hand rather than "
            f"shipping whatever came back.")
    os.makedirs(destination, exist_ok=True)
    with open(licence, "wb") as f:
        f.write(text)
    return licence
