# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Supply a libpq service entry to one local Windows child process."""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes as win
from threading import Thread
import uuid


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("length", win.DWORD), ("descriptor", win.LPVOID), ("inherit", win.BOOL)]


class _Overlapped(ctypes.Structure):
    _fields_ = [("internal", ctypes.c_size_t), ("internal_high", ctypes.c_size_t),
                ("offset", win.DWORD), ("offset_high", win.DWORD), ("event", win.HANDLE)]


class _WindowsAPI:
    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        security = ctypes.WinDLL("advapi32", use_last_error=True)

        def bind(dll, name, returns, *arguments):
            function = getattr(dll, name)
            function.restype = returns
            function.argtypes = arguments
            setattr(self, name, function)

        pointer = ctypes.POINTER
        overlap = pointer(_Overlapped)
        bind(kernel, "GetCurrentProcess", win.HANDLE)
        bind(kernel, "CloseHandle", win.BOOL, win.HANDLE)
        bind(kernel, "LocalFree", win.LPVOID, win.LPVOID)
        bind(security, "OpenProcessToken", win.BOOL, win.HANDLE, win.DWORD, pointer(win.HANDLE))
        bind(security, "GetTokenInformation", win.BOOL, win.HANDLE, win.DWORD, win.LPVOID,
             win.DWORD, pointer(win.DWORD))
        bind(security, "ConvertSidToStringSidW", win.BOOL, win.LPVOID, pointer(win.LPWSTR))
        bind(security, "ConvertStringSecurityDescriptorToSecurityDescriptorW", win.BOOL,
             win.LPCWSTR, win.DWORD, pointer(win.LPVOID), pointer(win.DWORD))
        bind(kernel, "CreateEventW", win.HANDLE, win.LPVOID, win.BOOL, win.BOOL, win.LPCWSTR)
        bind(kernel, "SetEvent", win.BOOL, win.HANDLE)
        bind(kernel, "CreateNamedPipeW", win.HANDLE, win.LPCWSTR, win.DWORD, win.DWORD,
             win.DWORD, win.DWORD, win.DWORD, win.DWORD, pointer(_SecurityAttributes))
        bind(kernel, "ConnectNamedPipe", win.BOOL, win.HANDLE, overlap)
        bind(kernel, "GetNamedPipeClientProcessId", win.BOOL, win.HANDLE, pointer(win.ULONG))
        bind(kernel, "WriteFile", win.BOOL, win.HANDLE, win.LPCVOID, win.DWORD, pointer(win.DWORD), overlap)
        bind(kernel, "WaitForMultipleObjects", win.DWORD, win.DWORD, pointer(win.HANDLE), win.BOOL, win.DWORD)
        bind(kernel, "GetOverlappedResult", win.BOOL, win.HANDLE, overlap, pointer(win.DWORD), win.BOOL)
        bind(kernel, "CancelIoEx", win.BOOL, win.HANDLE, overlap)

    def current_sid(self):
        token = win.HANDLE()
        if not self.OpenProcessToken(self.GetCurrentProcess(), 0x0008, ctypes.byref(token)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = win.DWORD()
            self.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))  # TokenUser
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            token_user = ctypes.create_string_buffer(size.value)
            if not self.GetTokenInformation(token, 1, token_user, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            # TOKEN_USER begins with SID_AND_ATTRIBUTES whose first field is a PSID.
            sid = ctypes.cast(token_user, ctypes.POINTER(win.LPVOID))[0]
            string_sid = win.LPWSTR()
            if not self.ConvertSidToStringSidW(sid, ctypes.byref(string_sid)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return string_sid.value
            finally:
                self.LocalFree(ctypes.cast(string_sid, win.LPVOID))
        finally:
            self.CloseHandle(token)

    @contextmanager
    def private_security(self):
        # A protected DACL with one allow ACE. No inherited/default wider ACEs.
        descriptor = win.LPVOID()
        sddl = f"D:P(A;;GA;;;{self.current_sid()})"
        if not self.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield _SecurityAttributes(ctypes.sizeof(_SecurityAttributes), descriptor, False)
        finally:
            self.LocalFree(descriptor)


class WindowsPgServicePipe:
    """Single-use, same-user, PID-bound and local-only Windows named pipe.

    PostgreSQL's service parser uses fopen/fgets. The entry ends with another
    section header so libpq finishes reading without waiting for pipe EOF.
    """

    def __init__(self, payload: bytes):
        self.name = rf"\\.\pipe\ProgTrack.PgService.{uuid.uuid4().hex}"
        self._payload = payload
        self._api = _WindowsAPI()
        self._worker = None
        self.delivered = False
        self._cancel = self._api.CreateEventW(None, True, False, None)
        if not self._cancel:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            with self._api.private_security() as attributes:
                self._pipe = self._api.CreateNamedPipeW(
                    self.name,
                    0x40080002,  # FILE_FLAG_OVERLAPPED | FILE_FLAG_FIRST_PIPE_INSTANCE | PIPE_ACCESS_OUTBOUND
                    0x00000008,  # PIPE_REJECT_REMOTE_CLIENTS, byte mode, blocking semantics
                    1, max(4096, len(payload)), 0, 0, ctypes.byref(attributes),
                )
                if self._pipe == win.HANDLE(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self._api.CloseHandle(self._cancel)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._api.SetEvent(self._cancel)
        if self._worker is not None:
            self._worker.join()
        self._api.CloseHandle(self._pipe)
        self._api.CloseHandle(self._cancel)
        self._payload = b""

    def serve_process(self, pid: int) -> None:
        if self._worker is not None:
            raise RuntimeError("The Windows service pipe is single-use.")
        self._worker = Thread(target=self._send, args=(pid,), name="ProgTrack libpq service")
        try:
            self._worker.start()
        except BaseException:
            self._worker = None
            raise

    def wait_for_delivery(self, timeout: float) -> bool:
        if self._worker is None:
            return False
        self._worker.join(timeout)
        return not self._worker.is_alive() and self.delivered

    def _send(self, pid):
        api = self._api
        event = api.CreateEventW(None, True, False, None)
        if not event:
            return
        try:
            pending = _Overlapped(event=event)
            if not api.ConnectNamedPipe(self._pipe, ctypes.byref(pending)):
                status = ctypes.get_last_error()
                if status != 535:  # ERROR_PIPE_CONNECTED: client connected before this call
                    if status != 997 or self._result(pending) is None:  # ERROR_IO_PENDING
                        return
            client = win.ULONG()
            if not api.GetNamedPipeClientProcessId(self._pipe, ctypes.byref(client)) or client.value != pid:
                return
            self.delivered = self._write()
        finally:
            api.CloseHandle(event)

    def _write(self):
        api = self._api
        event = api.CreateEventW(None, True, False, None)
        if not event:
            return False
        try:
            pending = _Overlapped(event=event)
            written = win.DWORD()
            if api.WriteFile(self._pipe, self._payload, len(self._payload), ctypes.byref(written), ctypes.byref(pending)):
                return written.value == len(self._payload)
            return ctypes.get_last_error() == 997 and self._result(pending) == len(self._payload)
        finally:
            api.CloseHandle(event)

    def _result(self, pending):
        api = self._api
        events = (win.HANDLE * 2)(pending.event, self._cancel)
        read = win.DWORD()
        if api.WaitForMultipleObjects(2, events, False, 30_000) == 0:
            if api.GetOverlappedResult(self._pipe, ctypes.byref(pending), ctypes.byref(read), False):
                return read.value
            return None
        api.CancelIoEx(self._pipe, ctypes.byref(pending))
        # The OVERLAPPED and buffer must outlive cancellation completion.
        api.GetOverlappedResult(self._pipe, ctypes.byref(pending), ctypes.byref(read), True)
        return None
