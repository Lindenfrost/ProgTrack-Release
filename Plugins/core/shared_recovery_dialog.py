# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Client-local profile correction while a Shared target is unavailable."""

from dataclasses import replace
from threading import Event

from PyQt6.QtCore import QThreadPool, Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSpinBox
)

from .backend.postgresql_admin import PostgreSQLAdministrationService
from .backend_admin_authorization import BackendAdminAuthorizationDialog
from .backend_secret_widgets import SavedSecretEdit
from .animal_dialog_sections import AnimalDialogSection
from .shared_connection import SharedAttempt


class SharedRecoveryDialog(QDialog):
    def __init__(self, configuration, messages, parent):
        super().__init__(parent)
        self.configuration = configuration
        self.messages = messages
        self.saved = configuration.saved_postgresql()
        self.discard = Event()
        self.test_generation = 0
        self.setWindowTitle(self.text("backend.shared.edit", "Shared PostgreSQL configuration"))
        form = QFormLayout(self)
        note = QLabel(self.text("backend.shared.edit_note", "Saving changes only this client's Shared PostgreSQL profile."))
        note.setWordWrap(True)
        form.addRow(note)
        overrides = configuration.environment_overrides()
        if overrides:
            priority = QLabel(self.text(
                "backend.overrides.active", "Deployment environment overrides take priority: {values}"
            ).format(values=", ".join(overrides)))
            priority.setWordWrap(True)
            form.addRow(priority)
        self.fields = {}
        for name, key, label in (
            ("host", "host", "Server"),
            ("database", "database", "Database"),
            ("user", "user", "Runtime username"),
            ("server_name", "server_name", "TLS server name"),
            ("ca_file", "ca_file", "CA certificate"),
        ):
            field = QLineEdit(str(getattr(self.saved, name)))
            form.addRow(self.text(f"backend.shared.field.{key}", label), field)
            self.fields[name] = field
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.saved.port)
        form.insertRow(2, self.text("backend.shared.field.port", "Port"), self.port)
        self.sslmode = QComboBox()
        self.sslmode.addItems(sorted(configuration.SSL_MODES))
        self.sslmode.setCurrentText(self.saved.sslmode)
        form.addRow(self.text("backend.shared.field.sslmode", "TLS mode"), self.sslmode)
        self.password = self._secret(form, "password", "Runtime password", configuration.read_password)
        mtls = AnimalDialogSection("mTLS", collapsed=True, parent=self)
        client_form = QFormLayout()
        for name, label in (
            ("client_cert_file", "Client certificate"), ("client_key_file", "Client key")
        ):
            field = QLineEdit(str(getattr(self.saved, name)))
            client_form.addRow(self.text(f"backend.shared.field.{name}", label), field)
            self.fields[name] = field
        self.passphrase = self._secret(
            client_form, "passphrase", "Client key passphrase", configuration.read_client_key_passphrase
        )
        mtls.content_layout().addLayout(client_form)
        form.addRow(mtls)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.test = self.buttons.addButton(
            self.text("backend.shared.test", "Test connection"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self.test.clicked.connect(self._test)
        for field in (*self.fields.values(), self.password, self.passphrase):
            field.textChanged.connect(self._invalidate_test)
        self.port.valueChanged.connect(self._invalidate_test)
        self.sslmode.currentTextChanged.connect(self._invalidate_test)
        form.addRow(self.buttons)
        self.finished.connect(lambda _: self.discard.set())

    def text(self, key, default):
        return self.messages.get(key, default)

    def _secret(self, form, key, label, lookup):
        field = SavedSecretEdit()
        try:
            present = bool(lookup())
        except OSError:
            present = False
        field.set_saved(
            present, self.text("backend.shared.keep_secret", "Leave blank to keep the saved credential")
        )
        form.addRow(self.text(f"backend.shared.field.{key}", label), field)
        return field

    def _settings(self):
        values = {name: field.text().strip() for name, field in self.fields.items()}
        return replace(self.saved, **values, port=self.port.value(), sslmode=self.sslmode.currentText())

    def _invalidate_test(self, *_):
        self.discard.set()
        self.discard = Event()
        self.test_generation += 1
        self.test.setEnabled(True)
        self.status.clear()

    def _save(self):
        try:
            self.configuration.save_disconnected_shared(
                postgresql=self._settings(), password=self.password.text(),
                client_key_passphrase=self.passphrase.text(),
            )
        except Exception:
            self.status.setText(self.text("backend.shared.save_failed", "Could not save the Shared profile. Check the settings and credentials."))
            return
        self.password.clear()
        self.passphrase.clear()
        self.accept()

    def _test(self):
        settings = self._settings()
        prompt = BackendAdminAuthorizationDialog(
            self, text=self.text, action=self.text("backend.shared.test", "Test connection"),
            server=f"{settings.host}:{settings.port}", database=settings.database,
        )
        if prompt.exec() != QDialog.DialogCode.Accepted:
            return
        login, password = prompt.login.text(), prompt.password.text()
        prompt.password.clear()
        same_endpoint, same_client_key = self.configuration.recovery_secret_identity(settings)
        runtime_password = self.password.text()
        if same_endpoint and not runtime_password:
            runtime_password = self.configuration.read_password()
        passphrase = self.passphrase.text()
        if same_client_key and not passphrase:
            passphrase = self.configuration.read_client_key_passphrase()
        self.test.setEnabled(False)
        self.status.setText(self.text("backend.shared.connecting", "Connecting to Shared PostgreSQL…"))

        def test_target():
            PostgreSQLAdministrationService.test_recovery_target(
                self.configuration, settings, login=login, password=password,
                runtime_password=runtime_password, client_key_passphrase=passphrase,
            )
            return None

        attempt = SharedAttempt(self.test_generation, test_target, self.discard)
        attempt.signals.completed.connect(self._test_finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(attempt)

    def _test_finished(self, generation, _value, error):
        if generation != self.test_generation or self.discard.is_set():
            return
        self.test.setEnabled(True)
        self.status.setText(
            self.text("backend.test.success", "The PostgreSQL connection succeeded. No profile was changed.")
            if error is None else
            self.text("backend.admin.failed", "Check the administrator credentials, permissions, target, and server connection.")
        )
