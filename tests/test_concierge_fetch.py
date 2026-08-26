"""
The model download: the allowlist, the two hash controls, and resuming.

`V-CG-56` … `V-CG-68`. The transport is a fake range server, so nothing here
opens a socket -- and one test asserts exactly that, by monkeypatching
`socket.socket` and running the whole harness import surface underneath it
(FR-CG-10).
"""

import hashlib
import io
import json
import os

import pytest

from ptt.concierge import fetch


# -- FR-CG-10's enumerated allowlist -----------------------------------------

def test_the_allowlist_is_the_two_hosts_the_requirement_names():
    """
    "huggingface.co (the tree API) and the LFS CDN host its download redirect
    names -- nothing else." Suffixes for the CDN because its hostnames are
    regional and rotate; the constraint that matters is that a redirect cannot
    walk us off Hugging Face entirely.
    """
    assert fetch.ALLOWED_HOSTS == ("huggingface.co",)
    assert fetch.ALLOWED_HOST_SUFFIXES == (".huggingface.co", ".hf.co")


@pytest.mark.parametrize("host", [
    "huggingface.co", "cdn-lfs-us-1.hf.co", "cas-bridge.xethub.huggingface.co"])
def test_permitted_hosts_are_permitted(host):
    assert fetch.is_allowed_host(host) is True


@pytest.mark.parametrize("host", [
    "example.com", "huggingface.co.evil.net", "hf.co.attacker.io",
    "nothuggingface.co", "", None])
def test_everything_else_is_refused(host):
    assert fetch.is_allowed_host(host) is False


def test_a_lookalike_suffix_does_not_pass():
    """`evilhf.co` ends with `hf.co` as a string but not as a domain."""
    assert fetch.is_allowed_host("evilhf.co") is False


def test_plain_http_is_refused_even_on_a_permitted_host():
    ok, reason = fetch.check_url("http://huggingface.co/api/models")
    assert ok is False and "not https" in reason


def test_the_pinned_urls_pass_their_own_check():
    spec = fetch.MODELS["gemma-4-12b-q4_k_m"]
    assert fetch.check_url(spec.tree_url())[0] is True
    assert fetch.check_url(spec.download_url())[0] is True


def test_the_transport_refuses_a_disallowed_url_before_opening_it():
    transport = fetch.HttpsTransport()
    with pytest.raises(PermissionError):
        transport.get_json("https://example.com/anything")


# -- what is pinned -----------------------------------------------------------

def test_the_pinned_spec_matches_the_handoff():
    """
    `concierge_handoff.md` section 1's artefact, exactly. A digest that drifts
    from the document is a qualification record that describes a different file.
    """
    spec = fetch.MODELS["gemma-4-12b-q4_k_m"]
    assert spec.repo == "lmstudio-community/gemma-4-12B-it-GGUF"
    assert spec.filename == "gemma-4-12B-it-Q4_K_M.gguf"
    assert spec.sha256 == (
        "95d83ba36642b1f385fb906b5962a71763361be3bac930a709945f72d97473f8")
    assert spec.size_bytes == 7381382944
    assert round(spec.gigabytes, 2) == 6.87


def test_the_model_keys_are_the_settings_choices():
    """
    One name for one thing: `concierge.model`'s enum and this table are the same
    strings, so adding a tier is a row and a choice rather than a lookup nobody
    maintains.
    """
    from ptt import config
    assert set(fetch.MODELS) == set(config.CONCIERGE_MODELS)


# -- the fake range server ---------------------------------------------------

class FakeTransport:
    """
    A range server that can also be told to ignore `Range`, to fail part-way,
    and to publish whatever `oid` a test wants.
    """

    def __init__(self, body, oid=None, honour_range=True, fail_after=None,
                 tree_error=None, listing=None):
        self.body = body
        self.oid = oid
        self.honour_range = honour_range
        self.fail_after = fail_after
        self.tree_error = tree_error
        self.listing = listing
        self.requests = []

    def get_json(self, url):
        self.requests.append(("tree", url))
        if self.tree_error:
            raise self.tree_error
        if self.listing is not None:
            return self.listing
        return [{"path": "gemma-4-12B-it-Q4_K_M.gguf",
                 "lfs": {"oid": self.oid, "size": len(self.body)}}]

    def open_range(self, url, start=0):
        self.requests.append(("get", url, start))
        if not self.honour_range:
            start = 0
            status = 200
        else:
            status = 206 if start else 200
        data = self.body[start:]
        if self.fail_after is not None:
            data = data[:self.fail_after]
            return status, len(self.body), _FailingReader(data)
        return status, len(self.body), io.BytesIO(data)


class _FailingReader(io.BytesIO):
    def read(self, size=-1):
        block = super().read(size)
        if not block:
            raise ConnectionResetError("the connection dropped")
        return block


def spec_for(body):
    return fetch.ModelSpec(
        key="test", repo="acme/test-gguf", filename="test.gguf",
        sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body),
        label="Test")


def download(tmp_path, body, **transport_kwargs):
    spec = spec_for(body)
    transport_kwargs.setdefault("oid", spec.sha256)
    transport = FakeTransport(body, **transport_kwargs)
    transport.listing = transport_kwargs.get("listing") or [
        {"path": "test.gguf", "lfs": {"oid": transport.oid, "size": len(body)}}]
    return fetch.Download(spec, str(tmp_path), transport=transport), transport


# -- the pre-download cross-check (Q26) --------------------------------------

def test_a_matching_oid_lets_the_download_proceed(tmp_path):
    dl, transport = download(tmp_path, b"weights" * 100)
    assert dl.verify_remote() == (True, None)
    assert dl.run() == (True, None)
    assert os.path.getsize(dl.path) == dl.spec.size_bytes


def test_a_mismatched_oid_refuses_before_a_single_byte_is_fetched(tmp_path, log_lines):
    """
    FR-CG-7's substitution control. The `oid` catches a re-upload; the pin is
    what makes "a new GGUF is a re-qualification, never a silent bump"
    enforceable, because without one a changed upstream file verifies cleanly
    against its own new digest.
    """
    dl, transport = download(tmp_path, b"weights", oid="0" * 64)
    ok, reason = dl.run()
    assert ok is False
    assert "not the pinned" in reason
    assert "will not be downloaded" in reason
    assert not os.path.exists(dl.path)
    assert [kind for kind in (r[0] for r in transport.requests)] == ["tree"]
    assert any("REFUSED the download" in line for line in log_lines())


def test_an_unreachable_tree_api_is_not_a_refusal(tmp_path, log_lines):
    """
    The pin still guards the bytes after they land, so an unreachable metadata
    endpoint costs the early warning and nothing else. Refusing to download
    because it was briefly unavailable would be a worse failure than the one it
    prevents.
    """
    body = b"weights" * 50
    dl, _ = download(tmp_path, body, tree_error=OSError("no route to host"))
    assert dl.verify_remote() == (True, None)
    assert dl.run() == (True, None)
    assert any("could not read the Hugging Face tree API" in line
               for line in log_lines())


def test_a_listing_with_no_oid_falls_back_to_the_pin(tmp_path, log_lines):
    body = b"weights" * 50
    dl, _ = download(tmp_path, body, listing=[{"path": "test.gguf"}])
    assert dl.verify_remote() == (True, None)
    assert any("listed no LFS oid" in line for line in log_lines())


# -- the transfer -------------------------------------------------------------

def test_progress_is_reported_against_the_whole_file(tmp_path):
    body = b"x" * (3 * fetch.Download.CHUNK)
    dl, _ = download(tmp_path, body)
    seen = []
    dl.on_progress = lambda done, total: seen.append((done, total))
    dl.run()
    assert seen[-1] == (len(body), len(body))
    assert all(total == len(body) for _done, total in seen)


def test_a_download_that_stops_leaves_a_resumable_partial(tmp_path):
    body = b"y" * (2 * fetch.Download.CHUNK)
    dl, _ = download(tmp_path, body, fail_after=fetch.Download.CHUNK)
    ok, reason = dl.run()
    assert ok is False and "stopped at" in reason
    assert os.path.getsize(dl.partial_path) == fetch.Download.CHUNK
    assert not os.path.exists(dl.path)


def test_relaunching_resumes_from_the_partial_file(tmp_path):
    """
    Acceptance criterion v3-5. A resume, not a restart of 6.87 GB.
    """
    body = b"z" * (2 * fetch.Download.CHUNK)
    dl, _ = download(tmp_path, body, fail_after=fetch.Download.CHUNK)
    dl.run()

    resumed, transport = download(tmp_path, body)
    assert resumed.run() == (True, None)
    assert ("get", resumed.spec.download_url(), fetch.Download.CHUNK) in transport.requests
    assert open(resumed.path, "rb").read() == body


def test_a_server_that_ignores_the_range_header_starts_over(tmp_path, log_lines):
    """
    Appending to a partial file would produce a corrupt one that only the final
    digest catches -- after another 6.87 GB.
    """
    body = b"w" * (2 * fetch.Download.CHUNK)
    dl, _ = download(tmp_path, body, fail_after=fetch.Download.CHUNK)
    dl.run()

    retried, _ = download(tmp_path, body, honour_range=False)
    assert retried.run() == (True, None)
    assert open(retried.path, "rb").read() == body
    assert any("ignored the resume request" in line for line in log_lines())


def test_a_corrupt_download_is_discarded_rather_than_used(tmp_path, log_lines):
    """
    The post-download control. The `oid` says nothing about a transfer that
    mangled the bytes on the way.
    """
    body = b"good" * 100
    spec = spec_for(body)
    transport = FakeTransport(b"bad!" * 100, oid=spec.sha256)
    transport.listing = [{"path": "test.gguf", "lfs": {"oid": spec.sha256}}]
    dl = fetch.Download(spec, str(tmp_path), transport=transport)
    ok, reason = dl.run()
    assert ok is False and "did not match its pinned digest" in reason
    assert not os.path.exists(dl.path)
    assert not os.path.exists(dl.partial_path)


def test_a_file_at_the_final_path_is_always_a_verified_file(tmp_path):
    """
    The rename is last, so a half-written GGUF never sits where the loader looks
    for one -- which would be a model load failure with no explanation.
    """
    body = b"v" * 4096
    dl, _ = download(tmp_path, body)
    assert dl.already_have() is False
    dl.run()
    assert dl.already_have() is True
    assert fetch.sha256_of(dl.path) == dl.spec.sha256


def test_an_existing_verified_file_is_not_fetched_again(tmp_path):
    body = b"u" * 2048
    dl, transport = download(tmp_path, body)
    dl.run()
    transport.requests.clear()
    assert dl.run() == (True, None)
    assert transport.requests == []


def test_the_digest_is_read_in_blocks(tmp_path):
    """A 6.87 GB file is not a 6.87 GB read."""
    path = tmp_path / "big.bin"
    path.write_bytes(b"q" * (fetch.Download.CHUNK + 7))
    assert fetch.sha256_of(str(path), chunk=1024) == hashlib.sha256(
        path.read_bytes()).hexdigest()


# -- FR-CG-10, asserted against the socket layer ------------------------------

def test_nothing_in_the_harness_opens_a_socket_by_being_imported(monkeypatch):
    """
    The socket monkeypatch FR-CG-10 names. Importing the harness -- every module
    of it -- must not reach the network, and the only outbound path in the whole
    package is the one this module owns.
    """
    import importlib
    import socket as socket_mod

    opened = []

    class Tripwire(socket_mod.socket):
        def connect(self, address):
            opened.append(address)
            raise AssertionError(f"the harness connected to {address}")

    monkeypatch.setattr(socket_mod, "socket", Tripwire)
    for name in ("state", "tools", "llm", "agent", "server", "fetch"):
        importlib.reload(importlib.import_module(f"ptt.concierge.{name}"))
    assert opened == []


def test_the_only_outbound_urls_are_built_from_the_pinned_repo():
    """
    Nothing constructs a host from user or model input. Both URLs are derived
    from `ModelSpec`, and both are checked against the allowlist before use.
    """
    spec = fetch.MODELS["gemma-4-12b-q4_k_m"]
    assert spec.tree_url().startswith("https://huggingface.co/api/models/")
    assert spec.download_url().startswith("https://huggingface.co/")


# -- the build-time bundling helper (spike findings 4-5) ---------------------

def test_the_nightly_tag_indirection_is_resolved():
    """
    Spike finding 4: the *versioned* release carries no binaries. `v0.3.0`'s
    only asset is `nightly-tag.txt` containing `b10621`, and a bundling step
    that fetches assets from the versioned tag finds nothing and looks like a
    network failure.
    """
    assert fetch.resolve_nightly_tag("b10621\n") == "b10621"
    assert fetch.resolve_nightly_tag("  b12345  \nignored\n") == "b12345"


@pytest.mark.parametrize("text", ["", "v0.3.0", "nightly", "b", "bxyz", None])
def test_something_that_is_not_a_nightly_tag_is_refused(text):
    with pytest.raises(ValueError):
        fetch.resolve_nightly_tag(text)


def test_both_runtime_assets_are_named():
    """
    The CUDA runtime DLLs are not in the binaries zip. A build that ships only
    the first produces an executable that will not start on a clean machine.
    """
    urls = fetch.llama_asset_urls("b10621")
    assert len(urls) == 2
    assert any("llama-b10621-bin-win-cuda-12.4-x64.zip" in u for u in urls)
    assert any("cudart-llama-bin-win-cuda-12.4-x64.zip" in u for u in urls)
    assert all(u.startswith("https://github.com/ggml-org/llama.cpp/releases/download/b10621/")
               for u in urls)


def test_the_bundler_refuses_to_run_without_the_build_time_token(tmp_path):
    """
    FR-CG-10 means the *running* app. This is the one function in the harness
    that reaches a host outside the allowlist, so the guard is an argument
    somebody has to pass on purpose rather than a comment.
    """
    with pytest.raises(RuntimeError) as caught:
        fetch.bundle_llama_runtime(str(tmp_path))
    assert "never runs in the shipped app" in str(caught.value)

    with pytest.raises(RuntimeError):
        fetch.bundle_llama_runtime(str(tmp_path), build_time=True)


def test_no_module_under_app_calls_the_bundler():
    """
    The other half of the guard, checked rather than asserted in prose: the only
    call site is `build_llama_runtime.py`, which is not inside `app/`.
    """
    import ptt
    root = os.path.dirname(os.path.dirname(os.path.abspath(ptt.__file__)))
    callers = []
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            text = open(path, encoding="utf-8").read()
            if "bundle_llama_runtime(" in text and not path.endswith("fetch.py"):
                callers.append(path)
    assert callers == []
