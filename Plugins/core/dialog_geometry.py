"""Shared geometry guard for resizable ProgTrack dialogs.

Dialogs are allowed to be resized, but their title/header and primary actions
must remain reachable.  The guard keeps a content-derived minimum size and
clamps the window to the active screen's available work area.  It is deliberately
small and dependency-free so lazy-loaded plugins can use it without importing
the main application.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QEvent, QRect, QSize, QTimer
from PyQt6.QtWidgets import QApplication, QDialog


class _DialogGeometryGuard(QObject):
    def __init__(self, dialog: QDialog, minimum: QSize):
        super().__init__(dialog)
        self.dialog = dialog
        self.minimum = minimum
        self._busy = False

    def eventFilter(self, watched, event):  # noqa: N802 - Qt API name
        if watched is self.dialog and event.type() in {
            QEvent.Type.Show,
            QEvent.Type.Resize,
            QEvent.Type.WindowStateChange,
        }:
            # Let Qt finish layout negotiation before measuring the hint.
            QTimer.singleShot(0, self.clamp)
        return False

    def clamp(self) -> None:
        if self._busy or self.dialog.isMinimized():
            return
        self._busy = True
        try:
            self.dialog.ensurePolished()
            hint = self.dialog.minimumSizeHint()
            min_width = max(self.minimum.width(), hint.width(), self.dialog.minimumWidth())
            min_height = max(self.minimum.height(), hint.height(), self.dialog.minimumHeight())
            screen = QApplication.screenAt(self.dialog.frameGeometry().center())
            screen = screen or QApplication.primaryScreen()
            if screen is None:
                self.dialog.setMinimumSize(min_width, min_height)
                return
            available = screen.availableGeometry()
            max_width = max(1, int(available.width() * 0.95))
            max_height = max(1, int(available.height() * 0.95))
            min_width = min(min_width, max_width)
            min_height = min(min_height, max_height)
            self.dialog.setMinimumSize(min_width, min_height)
            self.dialog.setMaximumSize(max_width, max_height)

            rect = self.dialog.frameGeometry()
            width = min(max(rect.width(), min_width), max_width)
            height = min(max(rect.height(), min_height), max_height)
            x = max(available.left(), min(rect.x(), available.right() - width + 1))
            y = max(available.top(), min(rect.y(), available.bottom() - height + 1))
            self.dialog.setGeometry(QRect(x, y, width, height))
        finally:
            self._busy = False


def install_dialog_geometry_guard(
    dialog: QDialog,
    *,
    minimum: QSize = QSize(360, 220),
) -> None:
    """Install a persistent, content-aware guard on ``dialog``.

    The guard is retained by the dialog and therefore also works for dialogs
    created by lazy-loaded plugins.  Calling this after the layout is built is
    preferred; a deferred pass handles dialogs whose size hint is finalized by
    Qt only when shown.
    """

    guard = _DialogGeometryGuard(dialog, minimum)
    dialog._progtrack_geometry_guard = guard  # type: ignore[attr-defined]
    dialog.installEventFilter(guard)
    dialog.setSizeGripEnabled(True)
    QTimer.singleShot(0, guard.clamp)

