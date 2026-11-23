# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Fresh modal prompt for a PostgreSQL administration command."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout


class BackendAdminAuthorizationDialog(QDialog):
    def __init__(self, parent, *, text, action, server, database):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(text("backend.admin.title", "Backend administrator authorization"))
        layout = QVBoxLayout(self)
        context = QLabel(f"{action}\n{server} / {database}", self)
        context.setTextFormat(Qt.TextFormat.PlainText)
        context.setWordWrap(True)
        layout.addWidget(context)
        intro = QLabel(text("backend.admin.intro", "Enter the separate PostgreSQL administrator account for this operation."), self)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.login = QLineEdit(self)
        self.password = QLineEdit(self)
        input_width = self.fontMetrics().horizontalAdvance("0000000000000000000000")
        self.login.setMinimumWidth(input_width)
        self.password.setMinimumWidth(input_width)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form = QFormLayout()
        form.addRow(text("backend.admin.login", "Backend administrator account"), self.login)
        form.addRow(text("backend.admin.password", "Administrator password"), self.password)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=self)
        authorize = buttons.button(QDialogButtonBox.StandardButton.Ok)
        authorize.setText(text("backend.admin.authorize", "Authorize"))
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(text("backend.admin.cancel", "Cancel"))
        authorize.setEnabled(False)

        def update_button():
            authorize.setEnabled(bool(self.login.text().strip() and self.password.text()))

        self.login.textChanged.connect(update_button)
        self.password.textChanged.connect(update_button)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.login.setFocus()
