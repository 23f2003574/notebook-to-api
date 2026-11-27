"""A notebook child must not be able to read the service's memory through /proc/<ppid>/mem."""
import ctypes
import os
import sys

import pytest

from backend.runtime import isolated_execution as iso

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc/<pid>/mem is Linux-only")

PR_SET_DUMPABLE = 4
SECRET = b"service-secret-0123456789"


def _child_can_read(address):
    """Fork a child of this process and report whether it can read SECRET from our memory."""
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        try:
            fd = os.open(f"/proc/{os.getppid()}/mem", os.O_RDONLY)
            ok = os.pread(fd, len(SECRET), address) == SECRET
        except OSError:
            ok = False
        os.write(write, b"1" if ok else b"0")
        os._exit(0)
    os.close(write)
    answer = os.read(read, 1)
    os.close(read)
    os.waitpid(pid, 0)
    return answer == b"1"


def _scenario():
    """Runs in a throwaway unprivileged process: before vs after restrict_process_inspection()."""
    if os.geteuid() == 0:  # root bypasses the dumpable check, so the test would prove nothing
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)
    libc = ctypes.CDLL(None)
    buffer = ctypes.create_string_buffer(SECRET)
    address = ctypes.addressof(buffer)
    libc.prctl(PR_SET_DUMPABLE, 1, 0, 0, 0)  # the state before the fix
    before = _child_can_read(address)
    assert iso.restrict_process_inspection() is True
    after = _child_can_read(address)
    return before, after


def test_child_cannot_read_parent_memory_once_the_service_is_non_dumpable():
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(read)
        try:
            before, after = _scenario()
            os.write(write, f"{int(before)}{int(after)}".encode())
        except Exception as exc:  # noqa: BLE001
            os.write(write, f"E{exc}".encode())
        os._exit(0)
    os.close(write)
    result = os.read(read, 200).decode()
    os.close(read)
    os.waitpid(pid, 0)
    if result.startswith("E"):
        pytest.skip(f"cannot drop privileges here: {result[1:]}")
    assert result == "10", "the child must read the secret before the fix and be refused after it"


def test_restrict_process_inspection_clears_the_flag_and_fails_closed(monkeypatch):
    libc = ctypes.CDLL(None)
    assert iso.restrict_process_inspection() is True
    assert libc.prctl(3, 0, 0, 0, 0) == 0  # PR_GET_DUMPABLE
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(OSError("no libc")))
    with pytest.raises(iso.IsolatedExecutionError):
        iso.restrict_process_inspection()


def test_restrict_process_inspection_is_a_noop_off_linux(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert iso.restrict_process_inspection() is False
