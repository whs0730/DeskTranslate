"""Cross-platform single-instance support for DeskTranslate.

Windows uses named kernel objects so the guarantee also works for a packaged
``pythonw.exe`` application.  The accompanying auto-reset event lets a second
launch ask the already-running process to show its window again.

The public lifecycle is intentionally small::

    instance = SingleInstance()
    if not instance.acquire():
        instance.signal_existing()
        raise SystemExit(0)

    # The first process can periodically call poll_activation().
    ...
    instance.release()

``release`` is idempotent, and instances can also be used as context managers.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import tempfile
import threading
from types import TracebackType
from typing import BinaryIO


ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


class AlreadyRunningError(RuntimeError):
    """Raised when context-manager acquisition finds another instance."""


class SingleInstance:
    """Own a process-wide application lock and activation signal.

    Args:
        name: Stable application identifier.  It is used as the Windows named
            mutex name and is hashed for the POSIX lock-file name.

    ``acquire()`` returns ``True`` for the first instance and ``False`` when an
    existing process already owns the lock.  A failed caller may then invoke
    ``signal_existing()``.  The owner observes that request through
    ``poll_activation()``; multiple requests may be coalesced on Windows.
    """

    def __init__(self, name: str = "DeskTranslate") -> None:
        name = name.strip()
        if not name:
            raise ValueError("Single-instance name must not be empty")

        self.name = name
        self._is_windows = os.name == "nt"
        self._state_lock = threading.RLock()
        self._acquired = False

        self._mutex_handle: int | None = None
        self._event_handle: int | None = None
        self._lock_file: BinaryIO | None = None
        self._activation_offset = 0

        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:20]
        temp_dir = Path(tempfile.gettempdir())
        self._lock_path = temp_dir / f"desktranslate-{digest}.lock"
        self._activation_path = temp_dir / f"desktranslate-{digest}.activate"

        windows_name = name if name.startswith(("Local\\", "Global\\")) else f"Local\\{name}"
        self._mutex_name = f"{windows_name}.SingleInstance"
        self._event_name = f"{windows_name}.Activate"

    @property
    def acquired(self) -> bool:
        """Whether this object currently owns the single-instance lock."""

        with self._state_lock:
            return self._acquired

    def acquire(self) -> bool:
        """Try to become the primary instance without blocking."""

        with self._state_lock:
            if self._acquired:
                return True

            acquired = (
                self._acquire_windows()
                if self._is_windows
                else self._acquire_posix()
            )
            self._acquired = acquired
            return acquired

    def release(self) -> None:
        """Release all operating-system resources; safe to call repeatedly."""

        with self._state_lock:
            if self._is_windows:
                self._release_windows()
            else:
                self._release_posix()
            self._acquired = False

    def signal_existing(self) -> bool:
        """Ask the primary instance to activate its window.

        Returns ``False`` when there is no primary instance (or its activation
        endpoint has already disappeared).
        """

        if self._is_windows:
            return self._signal_windows()
        return self._signal_posix()

    def poll_activation(self) -> bool:
        """Return once for each pending activation request when possible.

        Windows uses an auto-reset event and therefore intentionally coalesces
        a burst of launches into one activation.  This method never blocks.
        Only the object for which ``acquire()`` succeeded can observe signals.
        """

        with self._state_lock:
            if not self._acquired:
                return False
            if self._is_windows:
                return self._poll_windows()
            return self._poll_posix()

    def __enter__(self) -> SingleInstance:
        if not self.acquire():
            raise AlreadyRunningError(f"Another {self.name} instance is already running")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()

    def _kernel32(self):
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        kernel32.CreateMutexW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = (
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.SetEvent.argtypes = (wintypes.HANDLE,)
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        return kernel32

    @staticmethod
    def _windows_error(action: str) -> OSError:
        error = ctypes.get_last_error()
        return OSError(error, f"{action}: {ctypes.FormatError(error).strip()}")

    def _acquire_windows(self) -> bool:
        kernel32 = self._kernel32()

        ctypes.set_last_error(0)
        event_handle = kernel32.CreateEventW(None, False, False, self._event_name)
        if not event_handle:
            raise self._windows_error("Could not create activation event")

        ctypes.set_last_error(0)
        mutex_handle = kernel32.CreateMutexW(None, False, self._mutex_name)
        mutex_error = ctypes.get_last_error()
        if not mutex_handle:
            kernel32.CloseHandle(event_handle)
            raise self._windows_error("Could not create single-instance mutex")

        if mutex_error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(mutex_handle)
            kernel32.CloseHandle(event_handle)
            return False

        self._event_handle = event_handle
        self._mutex_handle = mutex_handle
        return True

    def _release_windows(self) -> None:
        kernel32 = self._kernel32()
        if self._event_handle:
            kernel32.CloseHandle(self._event_handle)
            self._event_handle = None
        if self._mutex_handle:
            kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None

    def _signal_windows(self) -> bool:
        kernel32 = self._kernel32()
        event_handle = kernel32.OpenEventW(
            EVENT_MODIFY_STATE,
            False,
            self._event_name,
        )
        if not event_handle:
            return False
        try:
            if not kernel32.SetEvent(event_handle):
                raise self._windows_error("Could not signal primary instance")
            return True
        finally:
            kernel32.CloseHandle(event_handle)

    def _poll_windows(self) -> bool:
        if not self._event_handle:
            return False
        result = self._kernel32().WaitForSingleObject(self._event_handle, 0)
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            raise self._windows_error("Could not poll activation event")
        raise OSError(f"Unexpected WaitForSingleObject result: {result:#x}")

    @staticmethod
    def _fcntl():
        # Imported lazily because fcntl does not exist on Windows.
        import fcntl

        return fcntl

    def _acquire_posix(self) -> bool:
        fcntl = self._fcntl()
        lock_file = self._lock_path.open("a+b")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            lock_file.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            raise

        self._lock_file = lock_file
        # Signals from a previous owner must not activate this fresh process.
        self._activation_path.write_bytes(b"")
        self._activation_offset = 0
        return True

    def _release_posix(self) -> None:
        if self._lock_file is None:
            return
        try:
            self._fcntl().flock(self._lock_file.fileno(), self._fcntl().LOCK_UN)
        finally:
            self._lock_file.close()
            self._lock_file = None

    def _signal_posix(self) -> bool:
        fcntl = self._fcntl()
        probe = self._lock_path.open("a+b")
        try:
            try:
                fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
            else:
                fcntl.flock(probe.fileno(), fcntl.LOCK_UN)
                return False
        finally:
            probe.close()

        with self._activation_path.open("ab") as activation_file:
            activation_file.write(b"1")
            activation_file.flush()
        return True

    def _poll_posix(self) -> bool:
        try:
            size = self._activation_path.stat().st_size
        except FileNotFoundError:
            return False

        if size < self._activation_offset:
            self._activation_offset = size
            return False
        if size == self._activation_offset:
            return False

        self._activation_offset += 1
        return True


__all__ = ["AlreadyRunningError", "SingleInstance"]
