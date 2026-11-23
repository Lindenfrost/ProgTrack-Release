# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Nonmodal, generation-safe managed-storage warning for the main window."""

from __future__ import annotations

import re
from queue import Empty, SimpleQueue
from threading import Event, Thread

from PyQt6.QtCore import QObject, QRunnable, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .backend.managed_storage import ManagedStorageUnavailable


def _warning_text(root: str, messages) -> str:
    if not root.strip():
        return messages.get(
            "backend.storage.warning_unset",
            "No managed-document storage path is configured. Enter it in the Backend dialog.",
        )
    if any(ord(char) < 32 for char in root) or re.search(
        r"://|(?:password|passwd|secret|token)\s*=|[^\\/ ]+:[^\\/ ]+@", root, re.IGNORECASE,
    ):
        return messages.get(
            "backend.storage.warning_invalid",
            "The configured managed-document storage path is invalid. Check it in the Backend dialog.",
        )
    return messages.get(
        "backend.storage.warning",
        "Managed documents are unavailable at {path}. Check the path in the Backend dialog.",
    ).format(path=root)


def _check_in_background(probe, cancellation: Event, results: SimpleQueue) -> None:
    try:
        probe(cancellation=cancellation)
    except (ManagedStorageUnavailable, OSError, ValueError):
        available = False
    else:
        available = True
    if not cancellation.is_set():
        results.put((cancellation, available))


class _ProbeSignals(QObject):
    finished = pyqtSignal(object, str)


class ManagedStorageProbeWorker(QRunnable):
    def __init__(self, check):
        super().__init__()
        self.check = check
        self.cancelled = Event()
        self.signals = _ProbeSignals()

    def run(self) -> None:
        try:
            self.check(cancellation=self.cancelled)
        except (ManagedStorageUnavailable, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", "not_accessible")
        else:
            reason = ""
        if not self.cancelled.is_set():
            self.signals.finished.emit(self, reason)


class ManagedStorageNotice(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("managedStorageNotice")
        self._root = ""
        self._probe = None
        self._messages = {}
        self._active = None
        self._dismissed = False
        self._results = SimpleQueue()
        self._poll = QTimer(self, interval=40)
        self._poll.timeout.connect(self._receive)
        self.message = QLabel(self)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.retry = QPushButton(self)
        self.dismiss = QPushButton(self)
        self.retry.clicked.connect(self.recheck)
        self.dismiss.clicked.connect(self._dismiss)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 0, 4, 0)
        row.addWidget(self.message, 1)
        row.addWidget(self.retry)
        row.addWidget(self.dismiss)
        self.hide()

    @property
    def root(self) -> str:
        return self._root

    def set_messages(self, messages) -> None:
        self._messages = messages
        self.message.setText(_warning_text(self._root, messages))
        self.retry.setText(messages.get("backend.storage.retry", "Retry"))
        self.dismiss.setText(messages.get("backend.storage.dismiss", "Dismiss"))

    def watch(self, root: str, probe) -> None:
        self._root = root
        self._probe = probe
        self.set_messages(self._messages)
        self.hide()
        self.recheck()

    def recheck(self) -> None:
        if self._probe is None:
            return
        self.stop()
        self._dismissed = False
        self._active = Event()
        self.retry.setEnabled(False)
        Thread(target=_check_in_background, args=(self._probe, self._active, self._results), daemon=True).start()
        self._poll.start()

    def stop(self) -> None:
        if self._active is not None:
            self._active.set()
            self._active = None
        self._poll.stop()

    def _dismiss(self) -> None:
        self._dismissed = True
        self.hide()

    def _receive(self) -> None:
        while True:
            try:
                ticket, available = self._results.get_nowait()
            except Empty:
                return
            if ticket is self._active:
                self.stop()
                self.retry.setEnabled(True)
                self.setVisible(not available and not self._dismissed)
