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

    def __init__(self, spec, directory, transport=None, on_progress=None):
        self.spec = spec
        self.directory = directory
        self.transport = transport or HttpsTransport()
        self.on_progress = on_progress or (lambda _done, _total: None)

    @property
    def path(self):
        return os.path.join(self.directory, self.spec.filename)

    @property
    def partial_path(self):
        return self.path + ".part"

    def already_have(self):
        """Whether the verified file is already on disk."""
        return os.path.exists(self.path) and os.path.getsize(self.path) == self.spec.size_bytes

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
            reason = (
                f"the file published as {self.spec.filename} has digest {oid}, "
                f"not the pinned {self.spec.sha256}. This is not the model this "
                f"build was qualified against, so it will not be downloaded.")
            log_debug(f"Concierge: REFUSED the download -- {reason}")
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
        """
        if self.already_have():
            return True, None

        ok, reason = self.verify_remote()
        if not ok:
            return False, reason

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
        try:
            with open(self.partial_path, mode) as f:
                while True:
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
    return destination
