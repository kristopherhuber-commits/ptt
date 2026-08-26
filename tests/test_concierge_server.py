"""
The state machine, and llama-server's lifecycle: job object, launch, reap.

`V-CG-01` … `V-CG-09` and `V-CG-46` … `V-CG-55`. No process is started: `spawn`,
the Win32 calls and the HTTP probe are all seams, because the part that has to
be tested is the *logic* -- confirm before you kill, fall back when HTTP is
silent, leave a stranger's server alone -- and none of that can be tested
against a real orphan.
"""

import json
import os
import socket

import pytest

from ptt.concierge import server as server_mod
from ptt.concierge import state as state_mod


# -- the state machine (design 8) --------------------------------------------

def test_the_eight_states_are_the_documented_ones():
    assert state_mod.STATES == (
        "disabled", "not_downloaded", "downloading", "stopped",
        "loading", "ready", "generating", "unloading")
    assert set(state_mod.TRANSITIONS) == set(state_mod.STATES)


def test_disabled_has_no_way_out():
    """
    FR-CG-12 is decided once, from the hardware, before anything starts. A
    machine does not grow a CUDA device while the app is open, and a `disabled`
    that can be left is a `disabled` something will eventually leave by accident.
    """
    machine = state_mod.Machine(state_mod.DISABLED)
    for target in state_mod.STATES:
        if target == state_mod.DISABLED:
            continue
        assert machine.to(target) is False
    assert machine.state == state_mod.DISABLED


def test_the_happy_path_walks_from_not_downloaded_to_ready():
    machine = state_mod.Machine(state_mod.NOT_DOWNLOADED)
    for target in (state_mod.DOWNLOADING, state_mod.STOPPED, state_mod.LOADING,
                   state_mod.READY, state_mod.GENERATING, state_mod.READY,
                   state_mod.UNLOADING, state_mod.STOPPED):
        assert machine.to(target) is True, target


def test_an_illegal_transition_is_refused_and_logged_not_raised(log_lines):
    """
    Called from a health-poll thread and an idle timer. A state machine that can
    take the harness down when a server dies during a download is worse than one
    that reports the disagreement and stays put.
    """
    machine = state_mod.Machine(state_mod.NOT_DOWNLOADED)
    assert machine.to(state_mod.GENERATING, "wishful") is False
    assert machine.state == state_mod.NOT_DOWNLOADED
    assert any("refused an illegal transition" in line for line in log_lines())


def test_an_unknown_state_is_refused(log_lines):
    machine = state_mod.Machine(state_mod.STOPPED)
    assert machine.to("thinking") is False
    assert any("unknown state" in line for line in log_lines())


def test_a_re_entry_with_new_detail_still_reports_it():
    """`downloading` reports a percentage without inventing eight more states."""
    seen = []
    machine = state_mod.Machine(state_mod.NOT_DOWNLOADED,
                                on_change=lambda s, d: seen.append((s, d)))
    machine.to(state_mod.DOWNLOADING, "3%")
    machine.to(state_mod.DOWNLOADING, "47%")
    machine.to(state_mod.DOWNLOADING, "47%")
    assert seen == [("downloading", "3%"), ("downloading", "47%")]


def test_a_raising_state_callback_does_not_kill_the_poll(log_lines):
    def boom(_state, _detail):
        raise RuntimeError("panel is gone")

    machine = state_mod.Machine(state_mod.STOPPED, on_change=boom)
    assert machine.to(state_mod.LOADING) is True
    assert machine.state == state_mod.LOADING
    assert any("ERROR in Concierge on_change" in line for line in log_lines())


def test_only_ready_accepts_a_message():
    """
    The harness serialises sends (`-np 1`, Q14): a new send cancels the current
    generation rather than landing on a second slot and re-paying the pack.
    """
    assert state_mod.can_serve(state_mod.READY) is True
    for other in state_mod.STATES:
        if other != state_mod.READY:
            assert state_mod.can_serve(other) is False


def test_an_invalid_starting_state_is_a_programming_error():
    with pytest.raises(ValueError):
        state_mod.Machine("thinking")


# -- the pre-bound port (Q13) -------------------------------------------------

def test_a_pre_bound_port_is_free_when_it_is_handed_over():
    """
    `--port 0` was never verified against this build, and more decisively it
    would leave the port unknown until the server announced it -- while the
    state file has to name the port *before* `Popen`, because the model-loading
    window is where a crash is likeliest.
    """
    port = server_mod.pre_bind_port()
    assert 1024 < port < 65536
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", port))          # free again, which is the point


def test_two_pre_bound_ports_differ():
    assert server_mod.pre_bind_port() != server_mod.pre_bind_port()


# -- the per-launch key (Q19) -------------------------------------------------

def test_the_api_key_is_fresh_every_launch(tmp_path):
    path = str(tmp_path / "concierge_key")
    first, _ = server_mod.write_api_key(path)
    second, _ = server_mod.write_api_key(path)
    assert first != second
    assert len(first) >= 32
    assert open(path, encoding="utf-8").read() == second


# -- the state file (Q11) -----------------------------------------------------

def test_the_state_file_records_what_the_reap_needs(tmp_path):
    path = str(tmp_path / "concierge_state.json")
    server_mod.write_state(4321, 133_000_000, 8099, path)
    assert server_mod.read_state(path) == {
        "pid": 4321, "create_time": 133_000_000, "port": 8099,
        "alias": "ptt-concierge"}


def test_a_missing_state_file_is_no_orphan(tmp_path):
    assert server_mod.read_state(str(tmp_path / "nope.json")) is None


def test_an_unreadable_state_file_is_ignored_and_logged(tmp_path, log_lines):
    path = tmp_path / "concierge_state.json"
    path.write_text("{not json", encoding="utf-8")
    assert server_mod.read_state(str(path)) is None
    assert any("unreadable" in line for line in log_lines())


def test_a_state_file_with_no_pid_is_ignored(tmp_path, log_lines):
    path = tmp_path / "concierge_state.json"
    path.write_text(json.dumps({"port": 1}), encoding="utf-8")
    assert server_mod.read_state(str(path)) is None
    assert any("no pid/port" in line for line in log_lines())


def test_clearing_a_state_file_that_is_not_there_is_not_an_error(tmp_path):
    server_mod.clear_state(str(tmp_path / "nope.json"))


# -- the launch line (design 2) ----------------------------------------------

def test_the_four_non_optional_flags_are_all_there():
    args = server_mod.launch_args("llama-server.exe", "m.gguf", 8099, "key.txt")
    pairs = list(zip(args, args[1:]))
    assert ("--alias", "ptt-concierge") in pairs
    assert ("-rea", "off") in pairs          # Gemma 4 is a reasoning model
    assert ("-np", "1") in pairs             # one prefix, no eviction (Q14)
    assert ("--port", "8099") in pairs       # pre-bound in Python (Q13)
    assert ("--api-key-file", "key.txt") in pairs
    assert ("-c", "32768") in pairs


def test_the_persistence_flags_are_absent_unless_asked_for():
    """
    C6 measured that a restored slot is not reused by the chat endpoint, so
    `--slot-save-path` costs a 425 MB write for nothing -- and a flag that does
    nothing is a flag someone later assumes is doing something.
    """
    args = server_mod.launch_args("s.exe", "m.gguf", 1, "k")
    assert "--slot-save-path" not in args
    assert "-cram" not in args
    assert server_mod.KV_PERSISTENCE_WORKS is False


def test_the_persistence_flags_appear_when_they_are():
    args = server_mod.launch_args("s.exe", "m.gguf", 1, "k",
                                  slot_save_path="slots", cache_ram_mib=4096)
    assert list(zip(args, args[1:])).count(("--slot-save-path", "slots")) == 1
    assert ("-cram", "4096") in list(zip(args, args[1:]))


# -- the startup reap (Q11) ---------------------------------------------------

class FakeProbe:
    def __init__(self, props=None, healthy_after=0):
        self.props = props
        self.calls = 0
        self._healthy_after = healthy_after

    def get_json(self, port, path, host="127.0.0.1", api_key=""):
        return self.props

    def healthy(self, port, host="127.0.0.1", api_key=""):
        self.calls += 1
        return self.calls > self._healthy_after


class FakeWin32:
    def __init__(self, create_time=None, image="llama-server.exe",
                 terminate=(True, None), job=1234):
        self._create_time = create_time
        self._image = image
        self._terminate = terminate
        self._job = job
        self.terminated = []
        self.assigned = []

    def create_time(self, pid):
        return self._create_time

    def image_name(self, pid):
        return self._image

    def terminate(self, pid):
        self.terminated.append(pid)
        return self._terminate

    def create_job(self):
        if self._job is None:
            raise OSError("no job objects here")
        return self._job

    def assign(self, job, handle):
        self.assigned.append((job, handle))
        return True


def state_file(tmp_path, **overrides):
    payload = {"pid": 4321, "create_time": 133_000_000, "port": 8099}
    payload.update(overrides)
    path = tmp_path / "concierge_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def test_no_state_file_means_no_orphan_and_no_noise(tmp_path, log_lines):
    killed, note = server_mod.reap_orphan(str(tmp_path / "nope.json"),
                                          FakeProbe(), FakeWin32())
    assert (killed, note) == (False, "")
    assert not any("reap" in line for line in log_lines())


def test_our_own_alias_over_http_is_positive_identification(tmp_path):
    """
    The spike's `probe.json` records `/props/model_alias = ptt-concierge`, so the
    alias is a first-party queryable property -- identification before any kill,
    with no command line read anywhere.
    """
    path = state_file(tmp_path)
    win32 = FakeWin32()
    killed, note = server_mod.reap_orphan(
        path, FakeProbe({"model_alias": "ptt-concierge"}), win32)
    assert killed is True
    assert win32.terminated == [4321]
    assert "confirmed by /props" in note
    assert not os.path.exists(path)


def test_somebody_elses_llama_server_is_left_alone(tmp_path, log_lines):
    """
    The user is entitled to run their own llama-server on a port we happened to
    record before we died.
    """
    path = state_file(tmp_path)
    win32 = FakeWin32()
    killed, note = server_mod.reap_orphan(
        path, FakeProbe({"model_alias": "my-own-model"}), win32)
    assert killed is False
    assert win32.terminated == []
    assert "leaving it alone" in note
    assert any("my-own-model" in line for line in log_lines())


def test_a_wedged_server_is_identified_by_pid_and_create_time(tmp_path):
    """
    HTTP silent. All three must match: pid, creation time and image name.
    """
    path = state_file(tmp_path)
    win32 = FakeWin32(create_time=133_000_000)
    killed, note = server_mod.reap_orphan(path, FakeProbe(None), win32)
    assert killed is True
    assert win32.terminated == [4321]
    assert "create time and image name" in note


def test_a_reused_pid_is_not_killed(tmp_path, log_lines):
    """
    Windows reuses pids freely. Killing "the pid in the state file" after a
    reboot is how a reap turns into a bug report about something unrelated
    dying, and the creation time is what makes it safe.
    """
    path = state_file(tmp_path)
    win32 = FakeWin32(create_time=999_999_999)
    killed, note = server_mod.reap_orphan(path, FakeProbe(None), win32)
    assert killed is False
    assert win32.terminated == []
    assert "has been reused" in note
    assert not os.path.exists(path)


def test_a_pid_that_is_now_a_different_program_is_not_killed(tmp_path):
    path = state_file(tmp_path)
    win32 = FakeWin32(create_time=133_000_000, image="notepad.exe")
    killed, note = server_mod.reap_orphan(path, FakeProbe(None), win32)
    assert killed is False and win32.terminated == []
    assert "notepad.exe" in note


def test_a_pid_that_is_gone_just_clears_the_stale_file(tmp_path):
    path = state_file(tmp_path)
    win32 = FakeWin32(create_time=None)
    killed, note = server_mod.reap_orphan(path, FakeProbe(None), win32)
    assert killed is False
    assert "is gone" in note
    assert not os.path.exists(path)


def test_a_reap_that_cannot_open_its_target_says_so_audibly(tmp_path, log_lines):
    """
    The app normally runs elevated (FR-C5), and an orphan from an elevated run
    cannot be opened by a non-elevated one. A silent failed reap is `OBS-1`'s
    prohibition exactly, so the elevation case is named in the message.
    """
    path = state_file(tmp_path)
    win32 = FakeWin32(terminate=(False, "could not open pid 4321 (error 5); if "
                                        "the orphan was left by an elevated run, "
                                        "this process cannot reach it"))
    killed, note = server_mod.reap_orphan(
        path, FakeProbe({"model_alias": "ptt-concierge"}), win32)
    assert killed is False
    assert "elevated" in note
    assert os.path.exists(path)         # left for the next attempt
    assert any("could not reap" in line for line in log_lines())


# -- launching (design 2, 8.1) ------------------------------------------------

class FakeProcess:
    def __init__(self, pid=4321, exits_with=None):
        self.pid = pid
        self._handle = 777
        self.stderr = None
        self._exits_with = exits_with
        self.returncode = exits_with
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._exits_with

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        return 0


@pytest.fixture
def paths_for(tmp_path):
    exe = tmp_path / "llama-server.exe"
    model = tmp_path / "model.gguf"
    exe.write_text("", encoding="utf-8")
    model.write_text("", encoding="utf-8")
    return str(exe), str(model)


def make_server(tmp_path, paths_for, *, probe=None, win32=None, process=None,
                prewarm=None, **kwargs):
    exe, model = paths_for
    captured = {}

    def spawn(args, **spawn_kwargs):
        captured["args"] = args
        return process or FakeProcess()

    server = server_mod.Server(
        exe, model,
        state_path=str(tmp_path / "concierge_state.json"),
        key_path=str(tmp_path / "concierge_key"),
        probe=probe or FakeProbe(),
        win32=win32 or FakeWin32(),
        spawn=spawn, sleep=lambda _s: None,
        prewarm=prewarm, **kwargs)
    server._captured = captured
    return server


def test_a_successful_launch_reaches_ready_and_records_its_state(tmp_path, paths_for):
    warmed = []
    server = make_server(tmp_path, paths_for,
                         prewarm=lambda port, key: warmed.append(port))
    ok, reason = server.start()
    assert (ok, reason) == (True, None)
    assert server.machine.state == "ready"
    assert warmed == [server.port]

    recorded = server_mod.read_state(server.state_path)
    assert recorded["pid"] == 4321
    assert recorded["port"] == server.port


def test_the_state_file_names_the_port_before_the_child_exists(tmp_path, paths_for):
    """
    Q13's real argument. The model-loading window is where a crash is likeliest,
    so the file has to be complete from the first instant -- which is only
    possible because the port was pre-bound rather than discovered.
    """
    server = make_server(tmp_path, paths_for)
    server.start()
    assert f"--port {server.port}" in " ".join(server._captured["args"])


def test_the_launch_is_contained_by_a_job_object(tmp_path, paths_for):
    """
    Q10. The kernel terminates the child when the last handle to the job closes,
    which happens on *any* parent death -- including `TerminateProcess`, which no
    Python code can intercept and which `install.ps1` performs before every
    reinstall.
    """
    win32 = FakeWin32()
    server = make_server(tmp_path, paths_for, win32=win32)
    server.start()
    assert win32.assigned == [(1234, 777)]


def test_a_machine_with_no_job_objects_still_starts_and_says_so(
        tmp_path, paths_for, log_lines):
    """
    Not fatal, and honestly logged. Without the job object the crash clause of
    FR-CG-9 falls back to the startup reap, which is what the backstop is for;
    refusing to start would be a worse trade than the weaker guarantee.
    """
    server = make_server(tmp_path, paths_for, win32=FakeWin32(job=None))
    assert server.start()[0] is True
    assert any("no job object" in line for line in log_lines())


def test_a_missing_binary_fails_to_stopped_with_a_reason(tmp_path):
    server = server_mod.Server(str(tmp_path / "nope.exe"), str(tmp_path / "m.gguf"),
                               state_path=str(tmp_path / "s.json"),
                               key_path=str(tmp_path / "k"),
                               probe=FakeProbe(), win32=FakeWin32(),
                               spawn=lambda *a, **k: FakeProcess())
    ok, reason = server.start()
    assert ok is False and "llama-server is not at" in reason
    assert server.machine.state == "stopped"


def test_a_missing_model_fails_to_stopped_with_a_reason(tmp_path, paths_for):
    exe, _ = paths_for
    server = server_mod.Server(exe, str(tmp_path / "nope.gguf"),
                               state_path=str(tmp_path / "s.json"),
                               key_path=str(tmp_path / "k"),
                               probe=FakeProbe(), win32=FakeWin32(),
                               spawn=lambda *a, **k: FakeProcess())
    ok, reason = server.start()
    assert ok is False and "the model file is not at" in reason


def test_a_server_that_never_becomes_healthy_gives_up_at_the_bound(
        tmp_path, paths_for):
    """
    Design 4.3's third bound: launch to healthy, 60 s, against a measured
    baseline of 5.0-6.8 s. It fails to `stopped` with a visible reason rather
    than leaving the panel loading forever.
    """
    clock = iter([0.0, 0.0, 10.0, 70.0, 70.0, 70.0])
    server = make_server(tmp_path, paths_for,
                         probe=FakeProbe(healthy_after=999),
                         clock=lambda: next(clock))
    ok, reason = server.start()
    assert ok is False and "not healthy within 60s" in reason
    assert server.machine.state == "stopped"


def test_a_server_that_exits_while_loading_reports_its_exit_code(tmp_path, paths_for):
    server = make_server(tmp_path, paths_for,
                         probe=FakeProbe(healthy_after=999),
                         process=FakeProcess(exits_with=3))
    ok, reason = server.start()
    assert ok is False and "exited with code 3" in reason


def test_a_failed_prewarm_stops_the_server_rather_than_showing_ready(
        tmp_path, paths_for):
    """
    `ready` means the next message will be fast. A prewarm that failed has not
    earned it, and showing `ready` anyway is the hanging first message the
    loading state exists to prevent.
    """
    def boom(_port, _key):
        raise RuntimeError("connection reset")

    server = make_server(tmp_path, paths_for, prewarm=boom)
    ok, reason = server.start()
    assert ok is False and "prewarm failed" in reason
    assert server.machine.state == "stopped"


def test_starting_twice_is_refused(tmp_path, paths_for):
    server = make_server(tmp_path, paths_for)
    server.start()
    assert server.start() == (False, "the Concierge runtime is already running")


def test_stopping_clears_the_state_file_and_the_key(tmp_path, paths_for):
    server = make_server(tmp_path, paths_for)
    server.start()
    assert os.path.exists(server.state_path) and os.path.exists(server.key_path)
    server.stop("unloaded on close")
    assert not os.path.exists(server.state_path)
    assert not os.path.exists(server.key_path)
    assert server.machine.state == "stopped"


def test_stopping_something_that_is_not_running_is_harmless(tmp_path, paths_for):
    server = make_server(tmp_path, paths_for)
    server.stop()
    assert server.machine.state == "stopped"


# -- the idle timer (FR-CG-8) -------------------------------------------------

class Ticker:
    """A clock a test drives by hand, so no test waits on a real second."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_zero_minutes_means_the_panel_decides_not_the_timer(tmp_path, paths_for):
    """
    `0` means "unload when the chat panel closes", which is the panel's
    business. A timer that read it as "unload immediately" would unload the
    model between two sentences.
    """
    server = make_server(tmp_path, paths_for)
    server.start()
    server._last_activity = -10_000        # very idle indeed
    running = server.process
    ticks = []

    def sleep(_s):
        ticks.append(1)
        if len(ticks) >= 3:
            server.process = None          # ends the loop the way `stop` does

    server._sleep = sleep
    server.start_idle_timer(lambda: 0, tick=0).join(timeout=2)
    assert running.terminated is False     # nothing was unloaded


def test_touching_the_server_restarts_the_countdown(tmp_path, paths_for):
    clock = Ticker()
    server = make_server(tmp_path, paths_for, clock=clock)
    clock.now = 100.0
    assert server.idle_seconds() == 100.0
    server.touch()
    clock.now = 140.0
    assert server.idle_seconds() == 40.0


def test_the_residency_setting_is_read_live_not_captured(tmp_path, paths_for):
    """
    The same live-re-read discipline `Engine` uses for the hotkey, and for the
    same reason: moving the slider takes effect without a restart.
    """
    server = make_server(tmp_path, paths_for)
    server.start()
    minutes = [0]
    server._last_activity = -10_000
    ticks = []

    def sleep(_s):
        ticks.append(1)
        if len(ticks) == 2:
            minutes[0] = 1                 # the user moves the slider
        if len(ticks) > 5:
            server.process = None          # a safety net, not the expected exit

    server._sleep = sleep
    server.start_idle_timer(lambda: minutes[0], tick=0).join(timeout=2)
    assert server.process is None          # it unloaded once the slider moved
    assert len(ticks) <= 5


def test_an_unreadable_residency_setting_does_not_unload(tmp_path, paths_for):
    """
    A provider that throws is a bug somewhere else. Reading it as zero and
    unloading would turn that bug into a model reload the user cannot explain.
    """
    server = make_server(tmp_path, paths_for)
    server.start()
    server._last_activity = -10_000
    ticks = []

    def sleep(_s):
        ticks.append(1)
        if len(ticks) >= 3:
            server.process = None

    def broken():
        raise RuntimeError("no settings object")

    server._sleep = sleep
    server.start_idle_timer(broken, tick=0).join(timeout=2)
    assert len(ticks) == 3


# -- the base URL -------------------------------------------------------------

def test_the_base_url_is_loopback(tmp_path, paths_for):
    server = make_server(tmp_path, paths_for)
    server.start()
    assert server.base_url().startswith("http://127.0.0.1:")
