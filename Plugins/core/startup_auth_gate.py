# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.3.0
"""Locked startup surface while the authoritative Shared backend is resolved."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout


class SharedStartupGate(QDialog):
    """Show only login state and client-local recovery before authentication."""

    _STATE_LABELS = {
        "waiting": ("backend.shared.connecting", "Connecting to Shared PostgreSQL…"),
        "starting": ("backend.shared.connecting", "Connecting to Shared PostgreSQL…"),
        "connecting": ("backend.shared.connecting", "Connecting to Shared PostgreSQL…"),
        "loading": ("backend.shared.loading", "Loading authorized database records…"),
        "login": ("backend.shared.login", "Sign in to load Shared PostgreSQL records."),
        "login_cancelled": (
            "backend.shared.login_cancelled",
            "Sign-in was cancelled. Reconnect to try again.",
        ),
        "unavailable": ("backend.shared.unavailable", "Database not found!"),
        "configuration": (
            "backend.shared.configuration",
            "Check the Shared PostgreSQL configuration.",
        ),
        "authentication_rejected": (
            "backend.shared.authentication_rejected",
            "The PostgreSQL server is reachable but rejected its database credentials.",
        ),
        "maintenance": (
            "backend.schema.maintenance.title",
            "The database schema is incompatible with this application version.",
        ),
        "failed": (
            "backend.shared.failed",
            "Shared PostgreSQL data could not be loaded.",
        ),
    }

    def __init__(self, messages, *, on_retry, on_settings, on_exit):
        super().__init__(None)
        self.messages = messages if isinstance(messages, dict) else {}
        self.setWindowTitle(
            self.messages.get("master_track.login.title", "Sign in to ProgTrack")
        )
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.retry_button = QPushButton()
        self.retry_button.clicked.connect(on_retry)
        buttons.addWidget(self.retry_button)
        self.settings_button = QPushButton()
        self.settings_button.clicked.connect(on_settings)
        buttons.addWidget(self.settings_button)
        self.exit_button = QPushButton(
            self.messages.get("menu.file.exit", "Exit")
        )
        self.exit_button.clicked.connect(self.reject)
        buttons.addWidget(self.exit_button)
        layout.addLayout(buttons)

        self.rejected.connect(on_exit)
        self.set_state("waiting")

    def text(self, key: str, fallback: str) -> str:
        return str(self.messages.get(key, fallback))

    def set_messages(self, messages) -> None:
        if isinstance(messages, dict):
            self.messages = messages
        self.setWindowTitle(
            self.text("master_track.login.title", "Sign in to ProgTrack")
        )
        self.exit_button.setText(self.text("menu.file.exit", "Exit"))
        self.set_state(getattr(self, "_state", "waiting"))

    def set_state(
        self, state: str, detail: str | None = None, *, recovery_allowed: bool = False
    ) -> None:
        self._state = state if state in self._STATE_LABELS else "failed"
        key, fallback = self._STATE_LABELS[self._state]
        message = self.text(key, fallback)
        if detail:
            message = f"{message}\n\n{detail}"
        self.status_label.setText(message)
        self.retry_button.setText(
            self.text("backend.shared.retry", "Reconnect")
        )
        self.settings_button.setText(
            self.text(
                "backend.shared.edit",
                "Shared PostgreSQL connection settings",
            )
        )
        offline_recovery = bool(
            recovery_allowed and self._state in {"unavailable", "configuration"}
        )
        login_retry = self._state == "login_cancelled"
        self.retry_button.setVisible(offline_recovery or login_retry)
        self.settings_button.setVisible(offline_recovery)
        self.retry_button.setEnabled(offline_recovery or login_retry)
        self.settings_button.setEnabled(offline_recovery)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
