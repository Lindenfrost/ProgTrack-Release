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
            # Profile-switch dialogs publish an explicit target geometry; for
            # those resize events clamp synchronously so native Qt cannot
            # briefly paint the stale (larger) page size before the deferred
            # callback runs.  Other dialogs retain the deferred behavior.
            target = getattr(self.dialog, "_progtrack_geometry_target", None)
            if event.type() is QEvent.Type.Resize and isinstance(target, QSize):
                self.clamp()
            else:
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
            # ``setGeometry`` addresses the client rectangle, while
            # ``frameGeometry`` includes the native title bar/borders.  The
            # previous implementation clamped the client origin directly to
            # the work area, leaving a few pixels of the frame (and therefore
            # the header) outside the screen.  Account for the frame margins
            # explicitly so the complete window remains visible.
            frame = self.dialog.frameGeometry()
            client = self.dialog.geometry()
            frame_left = max(0, client.left() - frame.left())
            frame_top = max(0, client.top() - frame.top())
            frame_right = max(0, frame.right() - client.right())
            frame_bottom = max(0, frame.bottom() - client.bottom())

            max_frame_width = max(1, int(available.width() * 0.95))
            max_frame_height = max(1, int(available.height() * 0.95))
            max_width = max(1, max_frame_width - frame_left - frame_right)
            max_height = max(1, max_frame_height - frame_top - frame_bottom)
            min_width = min(min_width, max_width)
            min_height = min(min_height, max_height)
            self.dialog.setMinimumSize(min_width, min_height)
            self.dialog.setMaximumSize(max_width, max_height)

            target = getattr(self.dialog, "_progtrack_geometry_target", None)
            if isinstance(target, QSize):
                # Profile-aware dialogs can request a compact geometry while
                # switching pages.  Keep the request active while native Qt
                # settles any delayed stacked-page relayout; the owning
                # dialog clears it once that transition is complete. Ordinary
                # user resizes then continue to use the client geometry.
                width = min(max(target.width(), min_width), max_width)
                height = min(max(target.height(), min_height), max_height)
            else:
                width = min(max(client.width(), min_width), max_width)
                height = min(max(client.height(), min_height), max_height)
            frame_width = width + frame_left + frame_right
            frame_height = height + frame_top + frame_bottom
            frame_x = max(
                available.left(),
                min(frame.x(), available.right() - frame_width + 1),
            )
            frame_y = max(
                available.top(),
                min(frame.y(), available.bottom() - frame_height + 1),
            )
            self.dialog.setGeometry(
                QRect(frame_x + frame_left, frame_y + frame_top, width, height)
            )
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
