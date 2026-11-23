# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Bound client-local managed-storage I/O to a supervised helper process."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Event, Thread
from time import monotonic

from Plugins.core.backend.managed_storage import (
    ManagedStorageUnavailable, StorageCredentials, _unc_share, require_storage_access,
)
from Plugins.core.backend.managed_files import (
    confined_payload_path, payload_fingerprint, publish_managed_payload, remove_verified_payload,
    stage_managed_payload, storage_entries,
)
from Plugins.core.runtime_paths import _shared_managed_path


PROBE_TIMEOUT = 5.0
IO_IDLE_TIMEOUT = 10.0
_WORKER_START_TIMEOUT = 10.0
_REQUEST_LIMIT = 128 * 1024
_REPLY_LIMIT = 16 * 1024
_CHUNK_SIZE = 1024 * 1024
_ROOT_CHILDREN = {"documents", "config-assets"}
_TRANSFER_OPERATIONS = {
    "digest", "source_digest", "copy", "stage", "publish", "download", "snapshot", "restore", "restore_new", "list", "retire",
}
_FAILURE_REASONS = {
    "invalid_path", "invalid_credentials", "credential_conflict", "authentication_failed",
    "not_accessible", "not_available", "mount_unavailable", "read_only", "missing_payload",
}


def _worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        # The Windows launcher executes the specified script as __main__.
        # Running the launcher without --script would start another GUI.
        return [sys.executable, "--script", str(Path(__file__).resolve())]
    return [sys.executable, "-m", "Plugins.core.managed_storage_worker"]


def _worker_env() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name.upper() not in {
        "PGPASSWORD", "PGSSLPASSWORD", "PROGTRACK_POSTGRES_DSN", "PROGTRACK_POSTGRES_PASSWORD",
    }}


def _send(proc, request: bytes) -> None:
    try:
        with proc.stdin:
            proc.stdin.write(request)
    except OSError:
        pass


def _receive(proc, responses) -> None:
    try:
        with proc.stdout:
            for line in iter(lambda: proc.stdout.readline(_REPLY_LIMIT), b""):
                responses.put(json.loads(line))
    except (OSError, ValueError):
        pass
    finally:
        responses.put(None)


def _kill(proc) -> None:
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass


def _reap(proc, completed_reply: bool) -> None:
    if completed_reply:
        try:
            proc.wait(timeout=1)
            return
        except subprocess.TimeoutExpired:
            _kill(proc)
    proc.wait()


def _run(request, *, timeout: float, cancellation: Event | None = None):
    """Use stdout for progress/results and send credentials only via stdin."""
    payload = json.dumps(request).encode("utf-8")
    if len(payload) > _REQUEST_LIMIT:
        raise ManagedStorageUnavailable("invalid_path")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        process = subprocess.Popen(
            _worker_command(), cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=flags, env=_worker_env(),
        )
    except OSError:
        raise ManagedStorageUnavailable("worker_unavailable") from None

    responses = SimpleQueue()
    Thread(target=_receive, args=(process, responses), daemon=True).start()
    completed_reply = False
    ready = False
    try:
        # Interpreter loading has its own bound. The I/O clock starts before
        # sending the request, and then only real transfer progress extends it.
        deadline = monotonic() + _WORKER_START_TIMEOUT
        progress = 0
        entries = []
        while True:
            if cancellation is not None and cancellation.is_set():
                raise ManagedStorageUnavailable("cancelled")
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ManagedStorageUnavailable("timeout")
            try:
                response = responses.get(timeout=min(remaining, 0.1))
            except Empty:
                continue
            if not ready:
                if response != {"ready": True}:
                    raise ManagedStorageUnavailable("worker_unavailable")
                ready = True
                deadline = monotonic() + timeout
                Thread(target=_send, args=(process, payload), daemon=True).start()
                continue
            if not isinstance(response, dict):
                raise ManagedStorageUnavailable("worker_unavailable")
            if response.get("success") is True:
                completed_reply = True
                if request["operation"] == "list":
                    return entries
                return response.get("value")
            reason = response.get("reason")
            if reason in _FAILURE_REASONS:
                completed_reply = True
                raise ManagedStorageUnavailable(reason)
            units = response.get("bytes")
            if request["operation"] not in _TRANSFER_OPERATIONS or type(units) is not int or units <= progress:
                raise ManagedStorageUnavailable("worker_unavailable")
            progress = units
            deadline = monotonic() + timeout
            if request["operation"] == "list":
                entry = response.get("entry")
                if not isinstance(entry, list) or len(entry) != 2:
                    raise ManagedStorageUnavailable("worker_unavailable")
                entries.append(entry)
    finally:
        # Do not let an unresponsive syscall hold the caller while the process
        # exits. The reaper refers to this exact Popen, never a process name.
        if not completed_reply:
            _kill(process)
        if not ready:
            process.stdin.close()
        Thread(target=_reap, args=(process, completed_reply), daemon=True).start()


def _absolute_path(value) -> Path:
    path = _shared_managed_path(value) if isinstance(value, str) else None
    if path is None:
        raise ManagedStorageUnavailable("invalid_path")
    return path


def _read_request():
    stream = _binary_stdio(sys.stdin, -10, "rb")
    return json.loads(stream.read(_REQUEST_LIMIT + 1))


def _binary_stdio(stream, descriptor: int, mode: str):
    if stream is not None:
        return stream.buffer
    # PyInstaller's windowed launcher leaves Python's sys.std* unset even
    # when CreateProcess inherited the private pipes used for this request.
    from ctypes import POINTER, WinDLL, byref
    from ctypes.wintypes import BOOL, DWORD, HANDLE
    from msvcrt import open_osfhandle

    kernel = WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = (DWORD,)
    kernel.GetStdHandle.restype = HANDLE
    kernel.GetCurrentProcess.restype = HANDLE
    kernel.DuplicateHandle.argtypes = (HANDLE, HANDLE, HANDLE, POINTER(HANDLE), DWORD, BOOL, DWORD)
    kernel.DuplicateHandle.restype = BOOL
    current = kernel.GetCurrentProcess()
    original = kernel.GetStdHandle(descriptor)
    copy = HANDLE()
    if not original or not kernel.DuplicateHandle(current, original, current, byref(copy), 0, False, 2):
        raise OSError("Cannot open the worker pipe.")
    flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY
    return os.fdopen(open_osfhandle(copy.value, flags | os.O_BINARY), mode)


def _transfer(source: Path, destination: Path | None, report):
    digest = hashlib.sha256()
    total = 0
    with source.open("rb") as reader:
        if destination is not None:
            with destination.open("wb") as writer:
                for block in iter(lambda: reader.read(_CHUNK_SIZE), b""):
                    writer.write(block)
                    total += len(block)
                    report({"bytes": total})
            shutil.copystat(source, destination)
            return True
        for block in iter(lambda: reader.read(_CHUNK_SIZE), b""):
            digest.update(block)
            total += len(block)
            report({"bytes": total})
    return digest.hexdigest()


def _snapshot(root: Path, destination: Path, report) -> bool:
    def raise_walk_error(error):
        raise error

    resolved_root = root.resolve()
    if destination.resolve().is_relative_to(resolved_root):
        raise ManagedStorageUnavailable("invalid_path")
    progress = 0
    for directory, subdirectories, files in os.walk(root, onerror=raise_walk_error):
        progress += 1
        report({"bytes": progress})
        subdirectories.sort()
        for name in sorted(files):
            if name.endswith(".pending"):
                continue
            source = Path(directory) / name
            if not source.resolve().is_relative_to(resolved_root) or not source.is_file():
                raise ManagedStorageUnavailable("not_accessible")
            target = destination / source.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)

            def report_file(message):
                report({"bytes": progress + message["bytes"]})

            _transfer(source, target, report_file)
            progress += target.stat().st_size + 1
            report({"bytes": progress})
    return True


def _restore(source: Path, path: Path, root: Path, report, *, exclusive=False) -> bool:
    parent = root
    for part in path.parent.relative_to(root).parts:
        parent = parent / part
        parent.mkdir(exist_ok=True)
    fd, name = tempfile.mkstemp(suffix=".pending", prefix=".progtrack-restore-", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        if exclusive:
            temporary.unlink()
            stage_managed_payload(source, temporary, report)
            completed = temporary.stat().st_size

            def relay(message):
                report({"bytes": completed + message["bytes"]})

            publish_managed_payload(temporary, path, relay)
        else:
            _transfer(source, temporary, report)
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _dispatch(request, report):
    root = _absolute_path(request["root"])
    operation = request["operation"]
    credentials = request.get("credentials")
    anchor = request.get("mount_anchor")
    require_storage_access(
        root, credentials=StorageCredentials(**credentials) if credentials else None,
        mount_anchor=Path(anchor) if anchor else None, writable=request["writable"],
    )
    children = request.get("children", ())
    if any(child not in _ROOT_CHILDREN for child in children):
        raise ManagedStorageUnavailable("invalid_path")
    for child in children:
        require_storage_access(root / child, writable=request["writable"])
    if operation == "probe":
        return str(root)
    if operation == "snapshot":
        return _snapshot(root, _absolute_path(request["destination"]), report)
    if operation == "source_digest":
        return payload_fingerprint(_absolute_path(request["source"]), report)
    if operation == "list":
        if len(children) != 1:
            raise ManagedStorageUnavailable("invalid_path")
        category_root = confined_payload_path(root, root / children[0])
        for count, entry in enumerate(storage_entries(category_root), start=1):
            report({"entry": entry, "bytes": count})
        return True

    path = _absolute_path(request["path"])
    confined_payload_path(root, path)
    if operation == "stat":
        try:
            info = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            return None
        return info.st_size if stat.S_ISREG(info.st_mode) else None
    if operation == "digest":
        return _transfer(path, None, report)
    if operation == "retire":
        return remove_verified_payload(path, request["size"], request["sha256"], report)
    if operation == "download":
        return _transfer(path, _absolute_path(request["destination"]), report)
    if operation in {"restore", "restore_new"}:
        return _restore(_absolute_path(request["source"]), path, root, report, exclusive=operation == "restore_new")
    if operation == "mkdir":
        path.mkdir(exist_ok=True)
    elif operation == "copy":
        return _transfer(_absolute_path(request["source"]), path, report)
    elif operation == "stage":
        stage_managed_payload(_absolute_path(request["source"]), path, report)
    elif operation in {"replace", "publish"}:
        source = _absolute_path(request["source"])
        if source.parent != path.parent:
            raise ManagedStorageUnavailable("invalid_path")
        confined_payload_path(root, source)
        if operation == "publish":
            publish_managed_payload(source, path, report)
        else:
            os.replace(source, path)
    elif operation == "unlink":
        path.unlink(missing_ok=request.get("missing_ok", False))
    else:
        raise ManagedStorageUnavailable("invalid_path")
    return True


class ManagedStorageClient:
    """The root and normal runtime identity associated with one client profile."""

    def __init__(self, configuration, root, *, runtime_dsn=None, username=None, password=None, error_message=None):
        self.configuration = configuration
        self.root = str(root) if root is not None else ""
        self.runtime_dsn = runtime_dsn
        self.username = username
        self.password = password
        self.error_message = error_message

    def _request(self, operation, *, writable, **details):
        request = dict(operation=operation, root=self.root, writable=writable, **details)
        if sys.platform == "win32" and _unc_share(self.root):
            credentials = self.configuration.storage_credentials(
                runtime_dsn=self.runtime_dsn, username=self.username, password=self.password,
            )
            request["credentials"] = {"username": credentials.username, "password": credentials.password}
        elif sys.platform != "win32":
            request["mount_anchor"] = self.configuration.environ.get("PROGTRACK_STORAGE_MOUNT_ANCHOR")
        return request

    def _call(self, operation, *, timeout, cancellation=None, **details):
        try:
            if cancellation is not None and cancellation.is_set():
                raise ManagedStorageUnavailable("cancelled")
            return _run(self._request(operation, **details), timeout=timeout, cancellation=cancellation)
        except ManagedStorageUnavailable as error:
            if self.error_message is not None:
                error.args = (self.error_message(),)
            raise

    def check(self, *, writable=False, children=(), cancellation=None) -> Path:
        return Path(self._call(
            "probe", writable=writable, children=tuple(children), timeout=PROBE_TIMEOUT, cancellation=cancellation,
        ))

    def file(self, operation, path: Path, *, source: Path | None = None, missing_ok=False):
        return self._call(
            operation, writable=operation in {"mkdir", "copy", "stage", "replace", "publish", "unlink", "restore", "restore_new"},
            path=str(path), source=os.path.abspath(source) if source is not None else None, missing_ok=missing_ok,
            timeout=IO_IDLE_TIMEOUT,
        )

    def source_fingerprint(self, path: Path) -> tuple[int, str]:
        size, checksum = self._call("source_digest", writable=False, source=os.path.abspath(path), timeout=IO_IDLE_TIMEOUT)
        return size, checksum

    def list_entries(self, child):
        return self._call("list", writable=False, children=(child,), timeout=IO_IDLE_TIMEOUT)

    def remove_retired(self, path, size, checksum):
        return self._call("retire", writable=True, path=str(path), size=size, sha256=checksum, timeout=IO_IDLE_TIMEOUT)

    def download(self, path: Path, destination: Path):
        return self._call("download", writable=False, path=str(path), destination=str(destination), timeout=IO_IDLE_TIMEOUT)

    def snapshot(self, destination: Path):
        return self._call("snapshot", writable=False, destination=str(destination), timeout=IO_IDLE_TIMEOUT)


def check_configured_storage(configuration, root, *, runtime_dsn=None, username=None, password=None,
                             writable=False, cancellation=None):
    """Same inputs as the profile service's check, using a hard I/O deadline."""
    client = ManagedStorageClient(configuration, root, runtime_dsn=runtime_dsn, username=username, password=password)
    return client.check(writable=writable, cancellation=cancellation)


def main() -> int:
    try:
        output = _binary_stdio(sys.stdout, -11, "wb")
    except (AttributeError, ImportError, OSError):
        return 1

    def report(message):
        output.write(json.dumps(message).encode("utf-8") + b"\n")
        output.flush()

    try:
        report({"ready": True})
        result = _dispatch(_read_request(), report)
    except ManagedStorageUnavailable as error:
        report({"reason": error.reason if error.reason in _FAILURE_REASONS else "not_accessible"})
    except (FileNotFoundError, NotADirectoryError):
        report({"reason": "missing_payload"})
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        report({"reason": "not_accessible"})
    else:
        report({"success": True, "value": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
