"""Shared compact collapsible sections used by New/Edit Animal dialogs.

The section deliberately keeps the icon button and title label in separate
widgets.  This prevents translated titles from changing the hit target or
overlapping the expand/collapse icon.
"""

from __future__ import annotations

from typing import Optional, Callable

from PyQt6.QtCore import Qt, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from Plugins.core.ui_icons import apply_icon


class AnimalDialogSection(QFrame):
    """Stable ProjectTrack-style collapsible section.

    ``collapsed`` defaults to True because animal dialogs should open compact.
    Callers may connect to ``toggled`` to persist a user-specific preference.
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        *,
        collapsed: bool = True,
        parent: Optional[QWidget] = None,
        on_toggled: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("animalDialogCollapsibleSection")
        self._title_label = QLabel(str(title or ""), self)
        self._title_label.setObjectName("animalDialogSectionTitle")
        self._title_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._toggle = QPushButton(self)
        self._toggle.setObjectName("animalDialogSectionToggle")
        self._toggle.setCheckable(True)
        self._toggle.setFixedSize(QSize(24, 24))
        self._toggle.setIconSize(QSize(18, 18))
        self._toggle.setFlat(True)
        self._toggle.setToolTip(str(title or ""))
        self._toggle.setAccessibleName(str(title or ""))

        header = QWidget(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(self._toggle, 0, Qt.AlignmentFlag.AlignLeft)
        header_layout.addWidget(self._title_label, 1)

        self._content = QWidget(self)
        self._content.setObjectName("animalDialogSectionContent")
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(8, 2, 4, 4)
        self._content_layout.setSpacing(3)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(1)
        outer.addWidget(header)
        outer.addWidget(self._content)

        self._toggle.toggled.connect(self._apply_expanded)
        if on_toggled is not None:
            self.toggled.connect(on_toggled)
        self._toggle.setChecked(not bool(collapsed))
        self._apply_expanded(self._toggle.isChecked(), emit=False)

    def _apply_expanded(self, expanded: bool, *, emit: bool = True) -> None:
        self._content.setVisible(bool(expanded))
        apply_icon(
            self._toggle,
            "toggle.collapse" if expanded else "toggle.expand",
            fallback="",
        )
        self._toggle.setToolTip(self._title_label.text())
        if emit:
            self.toggled.emit(bool(expanded))

    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def content_widget(self) -> QWidget:
        return self._content

    def is_expanded(self) -> bool:
        return bool(self._toggle.isChecked())

    def title_text(self) -> str:
        return str(self._title_label.text() or "")

    def restore_expanded(self, expanded: bool) -> None:
        self._toggle.blockSignals(True)
        self._toggle.setChecked(bool(expanded))
        self._apply_expanded(bool(expanded), emit=False)
        self._toggle.blockSignals(False)

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.setChecked(bool(expanded))

    def set_title(self, title: str) -> None:
        self._title_label.setText(str(title or ""))
        self._toggle.setToolTip(str(title or ""))
