"""
D-CG-1 -- the llama-server lifecycle (`concierge_design.md` 2 and 8.1).

**`subprocess`, not `QProcess`** (Q8). Three stated constraints force it:
CON-CG-6, design 2's one-way dependency arrow, and design 7.2's CLI rig, which
runs the real agent loop against a real server with zero app involvement. A
`QProcess` cannot start a server outside a Qt event loop, so the rig -- and with
it the whole qualification suite -- would not exist. The cost is that this
module owns its own health poll, stderr reader and idle timer, all on plain
threads, and reports state through plain-Python callbacks that the Qt adapter
turns into queued signals on its side of the seam.

**Containment is a Windows job object** (Q10, FR-CG-9). The earlier plan -- kill
on exit, reap on startup -- closes the clean case and leaves open the case the
requirement explicitly names. Under `TerminateProcess` no Python runs at all, so
a reap at *next* startup means the orphan does survive the exit, holding about
9.4 GB of VRAM, for however long it is until the user opens the app again. That
is routine rather than rare here: `install.ps1` runs
`Stop-Process -Name ptt_dictate -Force` before every reinstall, so the shipped
installer manufactures the condition. A job object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` makes the kernel terminate the child when
the last handle closes, which happens on *any* parent death -- and it dissolves
a second problem with it: `runtime.py` has four `os._exit` call sites, none of
which runs `atexit`, `finally` or a destructor, so a kill threaded onto "the
exit path" would have had to be threaded onto all four.

**The reap never reads another process's command line** (Q11). There is no
stdlib call for it; `wmic` is deprecated and not guaranteed present on current
Windows 11 builds; CIM/WMI means spawning PowerShell inside the `loading` state.
Instead the launch writes `{pid, create_time, port}` beside `config.json` --
complete from the first instant the child exists, because the port is pre-bound
in Python (Q13) rather than discovered from the server's own output -- and the
reap confirms identity over HTTP before it kills anything.
"""

import ctypes
import json
import os
import secrets
import socket
import subprocess
import threading
import time

from ptt import paths
from ptt.logging_setup import log_debug, log_exception
from ptt.concierge import state as state_mod
from ptt.concierge.llm import SERVER_READY_TIMEOUT_SEC

#: The `--alias` the harness launches with, and the identity the reap confirms.
#:
#: **Load-bearing** (design 8.1). It is nominally the model name in `/v1/models`,
#: and it is now also how a startup reap tells the app's own orphan from the
#: user's personal llama-server. Nobody may rename it or drop it.
SERVER_ALIAS = "ptt-concierge"

#: The context window the whole of design 5's budget is arithmetic against.
CONTEXT_SIZE = 32768

#: Offload everything. 999 rather than a real layer count so a different GGUF
#: needs no code change (CON-CG-5).
GPU_LAYERS = 999

#: How often the health poll asks. Cheap: a loopback GET against a server that
#: is loading a 6.87 GB file.
HEALTH_POLL_SEC = 0.25

#: Whether llama-server's prompt cache survives a restart, deciding which of
#: design 5's two ready paths runs. **Measured, not assumed** -- the mini-spike
#: is `spike/kv_persistence.py` and its result is recorded in `spike_results.md`
#: section C6. Until a build measures True, the prewarm fallback is the path,
#: which is what NFR-CG-2's [15 s] bracket is written against.
KV_PERSISTENCE_WORKS = False

#: Where a persisted prompt cache would live, if it worked.
SLOT_SAVE_DIRNAME = "slots"


# -- the pre-bound port (Q13) -------------------------------------------------

def pre_bind_port(host="127.0.0.1"):
    """
    Ask the OS for a free port, then let go of it and pass the number.

    `--port 0` was never verified against this build, and more decisively it
    would leave the port unknown until the server announced it -- while the
    state file has to name the port **before** `Popen`, because the model-loading
    window is where a crash is likeliest and an orphan with no recorded port
    cannot be identified over HTTP.

    The small race this accepts is that something else may take the port between
    the close here and the bind in the child. That is a launch failure, visible
    and retried once, rather than the silent misidentification the alternative
    risks.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


# -- the per-launch API key (Q19) ---------------------------------------------

def write_api_key(path=None):
    """
    A fresh key for this launch, written where only the app directory can read it.

    FR-CG-10 is about *outbound* connections and is silent about the inbound
    listener this design opens. Without a key, any local process -- and script
    in a page the user has open, since browsers may address loopback -- can
    reach `/v1/chat/completions` and consume the GPU.

    Stated honestly: this raises the bar from "anything that can reach the port"
    to "anything that can read the app directory". It is not a defence against a
    local attacker who already has the user's files.
    """
    path = path or paths.concierge_key_path()
    key = secrets.token_urlsafe(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(key)
    return key, path


# -- the state file (Q11) -----------------------------------------------------

def write_state(pid, create_time, port, path=None):
    """`{pid, create_time, port}`, written before `Popen` returns to the caller."""
    path = path or paths.concierge_state_path()
    payload = {"pid": int(pid), "create_time": create_time, "port": int(port),
               "alias": SERVER_ALIAS}
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
    except Exception as e:
        log_debug(f"Concierge: could not write {os.path.basename(path)}: {str(e)}")
        return None
    return payload


def read_state(path=None):
    path = path or paths.concierge_state_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        log_debug(f"Concierge: {os.path.basename(path)} is unreadable ({str(e)}); ignoring it.")
        return None
    if not isinstance(payload, dict) or "pid" not in payload or "port" not in payload:
        log_debug(f"Concierge: {os.path.basename(path)} has no pid/port; ignoring it.")
        return None
    return payload


def clear_state(path=None):
    """Delete the state file. Clean shutdown's half of the contract."""
    path = path or paths.concierge_state_path()
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        log_debug(f"Concierge: could not remove {os.path.basename(path)}: {str(e)}")


# -- Win32, behind a seam -----------------------------------------------------

class Win32:
    """
    The four Windows calls this module needs, in one injectable object.

    A seam rather than direct `ctypes` at the call sites, for the same reason
    `Engine` takes `chord_held`: the reap's *logic* -- confirm before you kill,
    fall back when HTTP is silent, leave a stranger's server alone -- is the part
    that has to be tested, and it cannot be tested against a real orphan.
    """

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_TERMINATE = 0x0001

    def __init__(self):
        self._kernel32 = None

    @property
    def kernel32(self):
        if self._kernel32 is None:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        return self._kernel32

    # -- the job object ---------------------------------------------------

    def create_job(self):
        """
        A job object whose closure kills everything in it.

        The handle is deliberately **never closed** while the app lives: closing
        the last handle is exactly what triggers the kill, and the kernel closes
        every handle a dying process holds, however it dies. That is the whole
        mechanism -- there is no code path to forget.
        """
        k = self.kernel32
        k.CreateJobObjectW.restype = ctypes.c_void_p
        handle = k.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k.SetInformationJobObject(
            ctypes.c_void_p(handle), self.JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info))
        if not ok:
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        return handle

    def assign(self, job, process_handle):
        ok = self.kernel32.AssignProcessToJobObject(
            ctypes.c_void_p(job), ctypes.c_void_p(int(process_handle)))
        if not ok:
            raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        return True

    # -- identifying and killing a pid ------------------------------------

    def create_time(self, pid):
        """
        A process's creation time, as a Windows FILETIME integer.

        This is what makes PID reuse safe. A pid on its own is not an identity:
        Windows reuses them freely, and killing "the pid in the state file"
        after a reboot is how a reap turns into a bug report about something
        unrelated dying.
        """
        k = self.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        handle = k.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            creation = ctypes.c_ulonglong()
            exited = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            ok = k.GetProcessTimes(
                ctypes.c_void_p(handle), ctypes.byref(creation),
                ctypes.byref(exited), ctypes.byref(kernel), ctypes.byref(user))
            return int(creation.value) if ok else None
        finally:
            k.CloseHandle(ctypes.c_void_p(handle))

    def image_name(self, pid):
        """The executable's file name, for the belt-and-braces identity check."""
        k = self.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        handle = k.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return None
        try:
            size = ctypes.c_ulong(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = k.QueryFullProcessImageNameW(
                ctypes.c_void_p(handle), 0, buf, ctypes.byref(size))
            return os.path.basename(buf.value) if ok else None
        finally:
            k.CloseHandle(ctypes.c_void_p(handle))

    def terminate(self, pid):
        """
        Kill one pid. `(ok, reason)`.

        **Elevation is the case that gets logged loudly.** The app normally runs
        elevated (`FR-C5`; `install.ps1` sets the run-as-administrator byte on
        both shortcuts), and an orphan from an elevated run cannot be opened by a
        non-elevated one or vice versa. A reap that cannot open its target says
        so audibly, because a silent failed reap is `OBS-1`'s prohibition
        exactly.
        """
        k = self.kernel32
        k.OpenProcess.restype = ctypes.c_void_p
        handle = k.OpenProcess(self.PROCESS_TERMINATE, False, int(pid))
        if not handle:
            code = ctypes.get_last_error()
            return False, (
                f"could not open pid {pid} (error {code}); if the orphan was "
                f"left by an elevated run, this process cannot reach it")
        try:
            ok = k.TerminateProcess(ctypes.c_void_p(handle), 1)
            return bool(ok), None if ok else f"TerminateProcess failed on pid {pid}"
        finally:
            k.CloseHandle(ctypes.c_void_p(handle))


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# -- the HTTP the lifecycle needs (not the chat's) ----------------------------

class Probe:
    """
    `/health` and `/props` over loopback. Stdlib only, never raises.

    Separate from `llm.HttpTransport` because these two calls are about the
    *process*, not the conversation: the reap uses `/props` before there is any
    client, and the health poll runs on a thread that must never block for
    longer than its own timeout.
    """

    def __init__(self, timeout=2.0):
        self.timeout = timeout

    def get_json(self, port, path, host="127.0.0.1", api_key=""):
        import http.client
        try:
            conn = http.client.HTTPConnection(host, int(port), timeout=self.timeout)
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            body = response.read(65536)
            conn.close()
            if response.status != 200:
                return None
            return json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            return None

    def healthy(self, port, host="127.0.0.1", api_key=""):
        import http.client
        try:
            conn = http.client.HTTPConnection(host, int(port), timeout=self.timeout)
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            conn.request("GET", "/health", headers=headers)
            status = conn.getresponse().status
            conn.close()
            # 503 is llama-server still loading the model, which is the normal
            # answer for most of the launch window and is not a failure.
            return status == 200
        except Exception:
            return False


# -- the startup reap (Q11) ---------------------------------------------------

def read_api_key(state_path=None):
    """
    The key a previous launch left behind, if it did.

    The reap needs it: the orphan it is trying to identify was started with
    `--api-key-file`, and `/props` behind an API key answers 401 to an
    unauthenticated probe. Without this the primary identification path -- the
    one that gives *positive* confirmation before a kill -- would almost never
    fire, and every reap would fall through to the pid-and-create-time backstop.
    A clean shutdown deletes the key file, so its presence is itself weak
    evidence that the last exit was not clean.
    """
    directory = os.path.dirname(state_path or paths.concierge_state_path())
    try:
        with open(os.path.join(directory, "concierge_key"), "r",
                  encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def reap_orphan(state_path=None, probe=None, win32=None):
    """
    Kill a llama-server left by a previous run, and *only* that one.

    Returns `(killed, note)`. The order is the point:

    1. No state file, no orphan. Nothing to do and nothing to log.
    2. `GET /props` on the recorded port. If `model_alias` is `ptt-concierge`,
       that is positive first-party identification -- the spike's `probe.json`
       records the field -- and the kill is safe.
    3. **Any other alias is somebody else's server.** Leave it alone and log it.
       The user is entitled to run their own llama-server on a port we happened
       to record before we died.
    4. HTTP silent -- a wedged server, or a pid that is now something else
       entirely. Fall back to `pid` + `create_time` + an image name of
       `llama-server.exe`. All three must match. The create time is what makes
       pid reuse safe.

    A reap that cannot do any of this logs why and leaves the file in place: a
    silent failure here is an orphan holding 9.4 GB that nobody will ever
    explain.
    """
    probe = probe or Probe()
    win32 = win32 or Win32()
    payload = read_state(state_path)
    if payload is None:
        return False, ""

    pid, port = payload.get("pid"), payload.get("port")
    props = probe.get_json(port, "/props", api_key=read_api_key(state_path))
    if props is None:
        # Once more unauthenticated, for an orphan from a build that predates
        # `--api-key-file` or whose key file was cleaned up separately.
        props = probe.get_json(port, "/props")
    if props is not None:
        alias = props.get("model_alias") or (props.get("default_generation_settings") or {}).get("model")
        if alias != SERVER_ALIAS:
            note = (f"a server is answering on port {port} with alias {alias!r}, "
                    f"not {SERVER_ALIAS!r}; leaving it alone")
            log_debug(f"Concierge reap: {note}")
            clear_state(state_path)
            return False, note
        ok, reason = win32.terminate(pid)
        note = (f"reaped the orphaned llama-server (pid {pid}, port {port}), "
                f"confirmed by /props alias" if ok else
                f"could not reap pid {pid}: {reason}")
        log_debug(f"Concierge reap: {note}")
        if ok:
            clear_state(state_path)
        return ok, note

    # HTTP is silent. Identity by pid + create_time + image name, all three.
    recorded = payload.get("create_time")
    actual = win32.create_time(pid)
    if actual is None:
        note = f"pid {pid} is gone; clearing the stale state file"
        log_debug(f"Concierge reap: {note}")
        clear_state(state_path)
        return False, note
    if recorded is not None and actual != recorded:
        note = (f"pid {pid} exists but was created at {actual}, not {recorded}; "
                f"the pid has been reused, so nothing is killed")
        log_debug(f"Concierge reap: {note}")
        clear_state(state_path)
        return False, note

    image = win32.image_name(pid)
    if image and image.lower() != "llama-server.exe":
        note = f"pid {pid} is {image!r}, not llama-server.exe; leaving it alone"
        log_debug(f"Concierge reap: {note}")
        clear_state(state_path)
        return False, note

    ok, reason = win32.terminate(pid)
    note = (f"reaped a wedged llama-server (pid {pid}), confirmed by create "
            f"time and image name" if ok else f"could not reap pid {pid}: {reason}")
    log_debug(f"Concierge reap: {note}")
    if ok:
        clear_state(state_path)
    return ok, note


# -- the server ---------------------------------------------------------------

def launch_args(exe, model, port, key_path, host="127.0.0.1",
                context_size=CONTEXT_SIZE, gpu_layers=GPU_LAYERS,
                slot_save_path=None, cache_ram_mib=None,
                reasoning_effort=None):
    """
    Design 2's launch line, as a list. Four of these are not optional.

    `-rea off` -- Gemma 4 12B is a reasoning model, which nothing anticipated.
    With llama-server's default `--reasoning auto` it deliberates into
    `reasoning_content` before emitting any `content`: the spike measured more
    than 512 tokens for an answer that takes 76 with reasoning off. Left on, a
    six-iteration repair loop costs thousands of tokens of invisible
    deliberation per user message and NFR-CG-1's [2 s] measures time to first
    *thought*.

    `-np 1` -- the default (-1, auto) gave the spike four slots sharing one
    unified KV pool, and C3 measured the consequence directly: a second 8k
    prefix evicted part of the first, and the return re-processed 517 tokens
    instead of about 50. One client, one conversation, one prefix.

    `--port <pre-bound>` and `--api-key-file` are argued at their own functions.
    """
    args = [
        exe,
        "-m", model,
        "--alias", SERVER_ALIAS,
        "-c", str(context_size),
        "-ngl", str(gpu_layers),
        "--host", host,
        "--port", str(port),
        "-np", "1",
        "-rea", "off",
        "--api-key-file", key_path,
    ]
    if slot_save_path:
        args += ["--slot-save-path", slot_save_path]
    if cache_ram_mib is not None:
        args += ["-cram", str(cache_ram_mib)]
    if reasoning_effort:
        # **Additive, never a replacement for `-rea off`** (design 6). The four
        # flags above stay exactly as they are; this appends a fifth for models
        # whose deliberation `-rea off` does not reach.
        #
        # Session 2's gate zero found the case: gpt-oss-20b is trained on the
        # harmony format, whose analysis channel `-rea off` does not suppress on
        # build b10621. Measured -- 1024 completion tokens, **253 deltas of
        # `reasoning_content` and one `content` delta that was null**, six
        # iterations running to the token cap and never yielding a decision.
        # With `--reasoning-effort low` the same question answers in 101 tokens
        # in grammar mode and 32 in native. Section 6 already says a reasoning
        # budget is a per-model qualification column and never a default; this
        # is the parameter that makes that sentence true of the code, which
        # hardcoded the flag and offered no way to set it per candidate.
        args += ["--reasoning-effort", str(reasoning_effort)]
    return args


class Server:
    """
    One llama-server process, from launch to unload.

    Every thread this owns is a daemon and every one of them reports through
    plain-Python callbacks: `machine` for state, and the log for everything
    else. The Qt adapter turns those into queued signals; the CLI rig reads them
    directly.
    """

    def __init__(self, exe, model, machine=None, *, host="127.0.0.1",
                 state_path=None, key_path=None, probe=None, win32=None,
                 spawn=None, clock=time.monotonic, sleep=time.sleep,
                 ready_timeout=SERVER_READY_TIMEOUT_SEC,
                 context_size=CONTEXT_SIZE, prewarm=None,
                 on_stderr=None, reasoning_effort=None):
        self.exe = exe
        self.model = model
        self.host = host
        self.machine = machine or state_mod.Machine(state_mod.STOPPED)
        self.state_path = state_path or paths.concierge_state_path()
        self.key_path = key_path or paths.concierge_key_path()
        self.context_size = context_size
        #: Per-model, per design 6's qualification column. `None` is the shipped
        #: default and means the launch line is exactly the four flags.
        self.reasoning_effort = reasoning_effort
        self._probe = probe or Probe()
        self._win32 = win32 or Win32()
        self._spawn = spawn or subprocess.Popen
        self._clock = clock
        self._sleep = sleep
        self._ready_timeout = ready_timeout
        self._prewarm = prewarm
        self._on_stderr = on_stderr or (lambda _line: None)

        self.port = None
        self.api_key = ""
        self.process = None
        self._job = None
        self._idle_timer = None
        self._last_activity = clock()
        self._lock = threading.Lock()

    # -- launch -------------------------------------------------------------

    def start(self):
        """
        Launch, contain, wait for health, warm the pack. `(ok, reason)`.

        The order is the contract and every step of it is argued in a decision:
        pre-bind the port so the state file is complete before the child exists;
        write the key; create the job **before** `Popen` so there is no window
        in which an unassigned child could outlive us; write the state file;
        spawn; assign; poll; prewarm. The machine reaches `ready` only after the
        last of those, because `ready` means the next message will be fast.
        """
        with self._lock:
            if self.process is not None:
                return False, "the Concierge runtime is already running"
            if not os.path.exists(self.exe):
                return self._fail(f"llama-server is not at {self.exe}")
            if not os.path.exists(self.model):
                return self._fail(f"the model file is not at {self.model}")

            self.machine.to(state_mod.LOADING, "starting the runtime")
            self.port = pre_bind_port(self.host)
            self.api_key, key_path = write_api_key(self.key_path)

            try:
                self._job = self._win32.create_job()
            except Exception as e:
                # Not fatal, and honestly logged: without the job object the
                # crash clause of FR-CG-9 falls back to the startup reap, which
                # is what the backstop is for. Refusing to start would be a
                # worse trade than running with the weaker guarantee.
                log_debug(f"Concierge: no job object ({str(e)}); "
                          f"crash containment falls back to the startup reap.")
                self._job = None

            slot_dir = None
            cram = None
            if KV_PERSISTENCE_WORKS:
                slot_dir = os.path.join(os.path.dirname(self.state_path),
                                        SLOT_SAVE_DIRNAME)
                os.makedirs(slot_dir, exist_ok=True)
            args = launch_args(self.exe, self.model, self.port, key_path,
                               self.host, self.context_size,
                               slot_save_path=slot_dir, cache_ram_mib=cram,
                               reasoning_effort=self.reasoning_effort)
            log_debug("Concierge: " + " ".join(args))

            try:
                self.process = self._spawn(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    creationflags=_no_window())
            except Exception as e:
                log_exception("Concierge: llama-server would not start")
                return self._fail(f"llama-server would not start: {str(e)}")

            created = self._win32.create_time(self.process.pid)
            write_state(self.process.pid, created, self.port, self.state_path)

            if self._job is not None:
                try:
                    self._win32.assign(self._job, self.process._handle)
                except Exception as e:
                    log_debug(f"Concierge: could not assign the runtime to the "
                              f"job object ({str(e)}); the startup reap is the "
                              f"only containment for this launch.")

            self._start_thread(self._read_stderr, "concierge-stderr")

        ok, reason = self._await_health()
        if not ok:
            self.stop(reason)
            return False, reason

        ok, reason = self._warm()
        if not ok:
            self.stop(reason)
            return False, reason

        self.machine.to(state_mod.READY, "the knowledge pack is warm")
        self.touch()
        return True, None

    def _await_health(self):
        deadline = self._clock() + self._ready_timeout
        while self._clock() < deadline:
            if self.process.poll() is not None:
                return False, (f"llama-server exited with code "
                               f"{self.process.returncode} while loading")
            if self._probe.healthy(self.port, self.host, self.api_key):
                log_debug(f"Concierge: llama-server healthy on port {self.port}")
                return True, None
            self._sleep(HEALTH_POLL_SEC)
        return False, (f"llama-server was not healthy within "
                       f"{self._ready_timeout:.0f}s")

    def _warm(self):
        """
        Pay the knowledge pack's cost inside `loading`, not on the first message.

        Spike C3: the pack is **not** processed at model load. It is processed on
        the first request that carries it, at 7.17 s to first token -- and
        design 5.1's fresh sessions mean every session would pay it. Firing it
        once as a throwaway `max_tokens: 1` request moves the cost here, where
        there is a loading state on screen to explain it, and the first real
        message then costs 0.345 s.

        Skipped when `KV_PERSISTENCE_WORKS`, because then the prefix survived
        the restart and there is nothing to pay.
        """
        if self._prewarm is None or KV_PERSISTENCE_WORKS:
            return True, None
        self.machine.to(state_mod.LOADING, "warming the knowledge pack")
        try:
            self._prewarm(self.port, self.api_key)
        except Exception as e:
            log_exception("Concierge: the knowledge-pack prewarm failed")
            return False, f"the knowledge-pack prewarm failed: {str(e)}"
        return True, None

    def _fail(self, reason):
        log_debug(f"Concierge: {reason}")
        self.machine.to(state_mod.STOPPED, reason)
        return False, reason

    # -- running ------------------------------------------------------------

    def base_url(self):
        return f"http://{self.host}:{self.port}"

    def touch(self):
        """Mark activity, restarting the residency countdown (FR-CG-8)."""
        self._last_activity = self._clock()

    def idle_seconds(self):
        return self._clock() - self._last_activity

    def start_idle_timer(self, minutes_provider, tick=15.0):
        """
        The residency timer, on its own thread.

        `minutes_provider` is read every tick rather than captured, so moving
        the slider takes effect without a restart -- the same live-re-read
        discipline `Engine` uses for the hotkey, and for the same reason.
        **0 does not mean "unload immediately"**: it means "unload when the chat
        panel closes", which is the panel's business, so this timer treats 0 as
        "never, on my account".
        """
        def run():
            while self.process is not None:
                self._sleep(tick)
                if self.process is None:
                    return
                try:
                    minutes = int(minutes_provider())
                except Exception:
                    minutes = 0
                if minutes <= 0:
                    continue
                if self.idle_seconds() >= minutes * 60:
                    log_debug(f"Concierge: idle for {minutes} minute(s); unloading.")
                    self.stop(f"unloaded after {minutes} minutes idle")
                    return

        self._idle_timer = self._start_thread(run, "concierge-idle")
        return self._idle_timer

    def _read_stderr(self):
        """
        Drain llama-server's stderr into the log.

        Not optional plumbing: a pipe nobody reads fills its buffer and blocks
        the child, which on this path looks exactly like a model that loads
        forever. The lines also *are* the diagnosis when a launch fails, and
        `OBS-1` will not accept a step that fails with nothing written down.
        """
        process = self.process
        if process is None or process.stderr is None:
            return
        try:
            for raw in process.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line:
                    continue
                log_debug(f"llama-server: {line}")
                try:
                    self._on_stderr(line)
                except Exception:
                    pass
        except Exception as e:
            log_debug(f"Concierge: the stderr reader stopped: {str(e)}")

    def _start_thread(self, target, name):
        thread = threading.Thread(target=target, name=name, daemon=True)
        thread.start()
        return thread

    # -- stop ---------------------------------------------------------------

    def stop(self, reason="stopped"):
        """
        Kill the runtime and clear the state file.

        Belt and braces with the job object rather than instead of it: this is
        the clean path, the job object is what covers every other one. The state
        file goes last, so a crash between the kill and the delete still leaves a
        reapable record rather than an orphan nobody can identify.
        """
        with self._lock:
            process, self.process = self.process, None
            if process is None:
                clear_state(self.state_path)
                return
            self.machine.to(state_mod.UNLOADING, reason)
            try:
                process.terminate()
                process.wait(timeout=20)
            except Exception:
                try:
                    process.kill()
                except Exception as e:
                    log_debug(f"Concierge: could not kill llama-server: {str(e)}")
            clear_state(self.state_path)
            try:
                os.remove(self.key_path)
            except OSError:
                pass
            log_debug(f"Concierge: llama-server stopped ({reason}).")
            self.machine.to(state_mod.STOPPED, reason)


def _no_window():
    """Keep a console window from flashing up under `pythonw.exe`."""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)
