# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Lord-only Qt dialog for restart-only backend profile configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import uuid
import sys

from PyQt6.QtCore import QSignalBlocker, QSize, Qt, QThreadPool, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFileDialog,
    QGroupBox,
    QInputDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .dialog_geometry import install_dialog_geometry_guard
from .animal_dialog_sections import AnimalDialogSection
from .backend_secret_widgets import SavedSecretEdit
from .backend_admin_authorization import BackendAdminAuthorizationDialog
from .backend.errors import BackendError
from .backend.catalog_cache import context_key, read_snapshot, write_snapshot
from .backend_catalog_table import DatabaseCatalogTable, render_database_table, selected_database_row
from .backend_catalog_worker import BoundedCatalogReader, CatalogReadWorker

from .backend_configuration import (
    BackendConfigurationPermissionError,
    BackendConfigurationService,
    BackendConfigurationValidationError,
    PostgreSQLSettings,
)
from .runtime_paths import BackendProfile


class _ProfileStackedWidget(QStackedWidget):
    """Stacked pages whose geometry follows only the selected profile."""

    def _current_hint(self) -> QSize:
        current = self.currentWidget()
        if current is None:
            return QSize(0, 0)
        hint = current.sizeHint()
        frame = self.frameWidth() * 2
        return QSize(hint.width() + frame, hint.height() + frame)

    def sizeHint(self) -> QSize:
        return self._current_hint()

    def minimumSizeHint(self) -> QSize:
        return self._current_hint()


class _ProfileScrollArea(QScrollArea):
    """Scrollable backend page with a useful profile-level width hint.

    ``QScrollArea`` otherwise reports only its small default viewport hint,
    making the PostgreSQL page appear narrower than the compact SQLite page to
    callers that compare profile hints.  The actual viewport remains free to
    shrink on high-DPI screens; the wider hint is only used for normal layout
    negotiation and does not impose a minimum width on the page.
    """

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API name
        hint = super().sizeHint()
        return QSize(max(840, hint.width()), hint.height())


class BackendConfigurationDialog(QDialog):
    managed_storage_tested = pyqtSignal(str, bool)
    managed_storage_profile_saved = pyqtSignal()

    def __init__(
        self,
        service: BackendConfigurationService,
        messages: dict[str, Any],
        *,
        authorized: bool,
        audit_callback: Callable[[dict[str, Any]], None] | None = None,
        backend: Any | None = None,
        actor_login: str = "",
        validate_admin_initiator: Callable[[], str] | None = None,
        validate_managed_initiator: Callable[[], str] | None = None,
        managed_session=None,
        active_database: str = "",
        parent: QWidget | None = None,
    ):
        service.require_lord(authorized)
        super().__init__(parent)
        self.service = service
        self.messages = messages
        self.authorized = authorized
        self.audit_callback = audit_callback
        self.backend = backend
        self.actor_login = actor_login or "lord"
        self.validate_admin_initiator = validate_admin_initiator
        self.validate_managed_initiator = validate_managed_initiator
        self.managed_session = managed_session
        self.active_database = active_database
        self._verified_backup = None
        self._catalog_opened = False
        self._catalog_usable = False
        self._catalog_context = None
        self._draft_generation = None
        self._catalog_generation = 0
        self._catalog_request = None
        self._catalog_preferred = ""
        self._staged_storage_request = None
        self.finished.connect(self._cancel_staged_storage)
        self.setWindowTitle(self._text("backend.dialog.title", "Backend"))
        self.setMinimumWidth(520)
        self._build()
        self._load()
        for field in (self.pg_host, self.pg_server_name, self.pg_user,
                      self.pg_password, self.pg_ca_file, self.pg_client_cert_file,
                      self.pg_client_key_file, self.pg_client_key_passphrase, self.pg_managed_root):
            field.textChanged.connect(self._clear_verified_backup)
        for field in (self.pg_host, self.pg_server_name, self.pg_user, self.pg_ca_file,
                      self.pg_client_cert_file, self.pg_client_key_file):
            field.textChanged.connect(self._invalidate_catalog_context)
        self.pg_password.textChanged.connect(self._invalidate_catalog_secret)
        self.pg_client_key_passphrase.textChanged.connect(self._invalidate_catalog_secret)
        self.pg_database.currentIndexChanged.connect(self._update_catalog_actions)
        self.pg_database.currentIndexChanged.connect(self._clear_verified_backup)
        self.pg_sslmode.currentTextChanged.connect(self._clear_verified_backup)
        self.pg_sslmode.currentTextChanged.connect(self._invalidate_catalog_context)
        self.pg_port.valueChanged.connect(self._clear_verified_backup)
        self.pg_port.valueChanged.connect(self._invalidate_catalog_context)
        self.pg_database_table.itemSelectionChanged.connect(self._check_backup_selection)
        self.pg_database_table.itemSelectionChanged.connect(self._update_catalog_actions)
        self.pg_database_table.currentItemChanged.connect(self._update_catalog_actions)
        self._update_catalog_actions()
        install_dialog_geometry_guard(self, minimum=QSize(520, 300))

    def _text(self, key: str, fallback: str) -> str:
        return str(self.messages.get(key, fallback))

    def _clear_verified_backup(self):
        self._verified_backup = None

    def _check_backup_selection(self):
        if self._verified_backup is not None and self._verified_backup.database != self._selected_pg_database():
            self._clear_verified_backup()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        intro = QLabel(
            self._text(
                "backend.dialog.intro",
                "Choose the backend used after the next clean restart.",
            )
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        choice_row = QHBoxLayout()
        self.sqlite_radio = QRadioButton(
            self._text("backend.profile.sqlite", "Standalone SQLite")
        )
        self.postgres_radio = QRadioButton(
            self._text("backend.profile.postgresql", "Shared PostgreSQL")
        )
        self.profile_group = QButtonGroup(self)
        self.profile_group.addButton(self.sqlite_radio, 0)
        self.profile_group.addButton(self.postgres_radio, 1)
        choice_row.addWidget(self.sqlite_radio)
        choice_row.addWidget(self.postgres_radio)
        choice_row.addStretch(1)
        root.addLayout(choice_row)

        self.pages = _ProfileStackedWidget(self)
        root.addWidget(self.pages)
        self._build_sqlite_page()
        self._build_postgres_page()
        self.sqlite_radio.toggled.connect(self._select_profile_page)

        self.override_label = QLabel()
        self.override_label.setWordWrap(True)
        root.addWidget(self.override_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        if self.backend is not None and callable(getattr(self.backend, "reconcile_managed_storage", None)):
            check_documents = self.buttons.addButton(
                self._text("backend.documents.scan", "Check current managed documents"),
                QDialogButtonBox.ButtonRole.ActionRole,
            )
            check_documents.clicked.connect(self._check_managed_documents)
        root.addWidget(self.buttons)

    def _check_managed_documents(self):
        from .document_messages import document_cleanup_notice, document_error

        title = self._text("backend.documents.scan", "Check current managed documents")
        options = dict(session=self.managed_session, validate_local=self.validate_managed_initiator)
        try:
            report = self.backend.reconcile_managed_storage(**options)
            if report["status"] == "storage_unavailable":
                QMessageBox.warning(self, title, self._text(
                    "backend.storage.context_error", "Managed document storage is unavailable. Check its path and try again.",
                ))
                return
            findings = report["findings"]
            if not findings:
                QMessageBox.information(self, title, self._text(
                    "backend.documents.scan_ok", "No managed-document inconsistencies were found.",
                ))
                return
            finding = self._select_managed_finding(title, findings)
            if finding is None:
                return
            reason = finding["reason"]
            guidance = {
                "cleanup_pending": "Remove the verified retired payload?",
                "pending_operation": "Abandon this pending operation? Any files will be kept for inspection.",
                "stale_staging": "Remove this aged staging file from its finished or abandoned operation?",
            }
            if reason not in guidance:
                QMessageBox.information(self, title, self._text(
                    "backend.documents.scan_manual", "This finding requires manual review. No files were changed.",
                ))
                return
            question = self._text(f"backend.documents.repair.{reason}", guidance[reason])
            if QMessageBox.question(self, title, question) != QMessageBox.StandardButton.Yes:
                return
            result = self.backend.repair_managed_storage(finding, **options)
            if isinstance(result, dict) and result.get("cleanup_pending"):
                QMessageBox.warning(self, title, document_cleanup_notice(self.messages))
            else:
                QMessageBox.information(self, title, self._text("backend.documents.repair_done", "Repair recorded."))
        except (BackendError, OSError) as error:
            QMessageBox.warning(self, title, document_error(self.messages, error))

    def _select_managed_finding(self, title, findings):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        prompt = QLabel(self._text("backend.documents.scan_findings", "Select a finding to review."))
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        choices = QListWidget(dialog)
        choices.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        choices.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        detail = QPlainTextEdit(dialog)
        detail.setReadOnly(True)
        detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        details = []
        for finding in findings:
            reason = finding["reason"]
            label = self._text(f"backend.documents.finding.{reason}", reason.replace("_", " "))
            path = finding["relative_path"] or finding.get("document_id") or ""
            details.append(path)
            choices.addItem(f"{label} — {path}" if path else label)
            choices.item(choices.count() - 1).setToolTip(path)
        choices.currentRowChanged.connect(lambda row: detail.setPlainText(details[row] if row >= 0 else ""))
        layout.addWidget(choices, 2)
        layout.addWidget(detail, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, parent=dialog)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        choices.setCurrentRow(0)
        available = self.screen().availableGeometry()
        width, height = max(1, available.width() - 32), max(1, available.height() - 48)
        dialog.setMaximumSize(width, height)
        dialog.resize(min(680, width), min(440, height))
        dialog.move(available.left() + (available.width() - dialog.width()) // 2,
                    available.top() + (available.height() - dialog.height()) // 2)
        return findings[choices.currentRow()] if dialog.exec() == QDialog.DialogCode.Accepted else None

    def _build_sqlite_page(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        explanation = QLabel(
            self._text(
                "backend.sqlite.explanation",
                "For one local workstation only. Network and synchronized/cloud "
                "locations are refused.",
            )
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        form = QFormLayout()
        self.sqlite_filename = QLineEdit()
        form.addRow(
            self._text("backend.sqlite.filename", "Database file name"),
            self.sqlite_filename,
        )
        folder_row = QHBoxLayout()
        self.sqlite_folder = QLineEdit()
        self.sqlite_folder.setReadOnly(True)
        self.open_sqlite_folder = QPushButton(
            self._text("backend.open_folder", "Open folder")
        )
        self.change_sqlite_folder = QPushButton(
            self._text("backend.change_folder", "Change folder")
        )
        self.open_sqlite_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(self.sqlite_folder.text())
            )
        )
        folder_row.addWidget(self.sqlite_folder, 1)
        folder_row.addWidget(self.change_sqlite_folder)
        folder_row.addWidget(self.open_sqlite_folder)
        self.change_sqlite_folder.clicked.connect(self._change_sqlite_folder)
        form.addRow(
            self._text("backend.sqlite.folder", "Local storage folder"),
            folder_row,
        )
        layout.addLayout(form)
        self.pages.addWidget(page)

    def _change_sqlite_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            self._text("backend.change_folder.title", "Choose SQLite storage folder"),
            self.sqlite_folder.text(),
        )
        if selected:
            self.sqlite_folder.setText(str(Path(selected).resolve()))

    def _select_profile_page(self, sqlite_selected: bool) -> None:
        self.pages.setCurrentIndex(0 if sqlite_selected else 1)
        if self.isVisible() and not sqlite_selected:
            self._open_catalog_once()
        self._update_catalog_actions()
        QTimer.singleShot(0, self._resize_for_profile)

    def showEvent(self, event):
        super().showEvent(event)
        if self.postgres_radio.isChecked():
            self._open_catalog_once()

    def _profile_size_hint(self) -> QSize:
        """Return a top-level size hint for the visible backend profile only.

        ``QStackedWidget`` normally reports the largest page and Qt may call
        the dialog's size hint again after a layout request.  That made a
        compact SQLite page grow back to the PostgreSQL height on native
        styles, even after ``_resize_for_profile`` had explicitly shrunk it.
        Measure the visible page in the root layout and keep the profile
        width/height contract in one place so both the geometry guard and Qt's
        own post-event negotiation use the same value.
        """
        pages = getattr(self, "pages", None)
        root_layout = self.layout()
        if pages is None or root_layout is None:
            return super().sizeHint()
        current = pages.currentWidget()
        margins = root_layout.contentsMargins()
        height = margins.top() + margins.bottom()
        count = root_layout.count()
        for index in range(count):
            item = root_layout.itemAt(index)
            if item is None:
                continue
            if item.widget() is pages and current is not None:
                content = current.widget() if isinstance(current, QScrollArea) else current
                item_height = content.sizeHint().height()
            else:
                item_height = item.sizeHint().height()
            height += max(0, int(item_height))
            if index < count - 1:
                height += max(0, int(root_layout.spacing()))
        radio = getattr(self, "sqlite_radio", None)
        sqlite = True if radio is None else radio.isChecked()
        width = 560 if sqlite else 760
        return QSize(width, max(300, height))

    def _clear_profile_geometry_target(self, generation: int) -> None:
        """Release a profile-switch geometry request after native settling."""
        if generation == getattr(self, "_profile_geometry_generation", 0):
            self._progtrack_geometry_target = None

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API name
        """Keep native Qt layout negotiation tied to the active profile."""
        return self._profile_size_hint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API name
        """Keep the geometry guard profile-aware as well as ``sizeHint``.

        The shared native geometry guard uses ``minimumSizeHint``.  If that
        hint still reflects the hidden PostgreSQL page, Windows keeps the
        larger minimum after switching back to SQLite and the compact page is
        stretched again on the next event-loop turn.
        """
        return self._profile_size_hint()

    def _resize_for_profile(self) -> None:
        target_width = 560 if self.sqlite_radio.isChecked() else 760
        # The shared geometry guard may have raised the dialog's minimum
        # height for the previously visible PostgreSQL page.  Reset that
        # profile-independent floor before measuring the current page so a
        # SQLite -> PostgreSQL -> SQLite switch can really compact again.
        self.setMinimumSize(520, 300)
        self.setMaximumSize(16777215, 16777215)
        # QStackedWidget can retain the previous (PostgreSQL) page's cached
        # size hint for one event-loop turn.  Clear that cache before taking a
        # profile-specific measurement; otherwise switching back to SQLite
        # leaves the dialog with a large empty lower half on native Qt styles.
        self.pages.setMinimumHeight(0)
        self.pages.setMaximumHeight(16777215)
        self.pages.updateGeometry()
        current = self.pages.currentWidget()
        if current is not None:
            # The stack owns the active page's actual geometry. Calling
            # adjustSize() on its scroll area can reset the viewport to the
            # preferred scroll height, clipping it inside an expanded dialog.
            current.updateGeometry()
        self.pages.layout().invalidate()
        self.pages.layout().activate()
        self.layout().invalidate()
        self.layout().activate()
        # Use the profile-aware hint rather than QDialog's default hint,
        # which is based on the largest (PostgreSQL) stacked page.
        profile_hint = self._profile_size_hint()
        # Measure the visible page explicitly.  The top-level layout's cached
        # sizeHint may still include hidden controls from the other profile;
        # summing its visible items avoids inheriting that stale height while
        # retaining the intro, override and button rows.
        root_layout = self.layout()
        margins = root_layout.contentsMargins()
        content_height = margins.top() + margins.bottom()
        item_count = root_layout.count()
        for index in range(item_count):
            item = root_layout.itemAt(index)
            if item is None:
                continue
            widget = item.widget()
            if widget is self.pages and current is not None:
                content = current.widget() if isinstance(current, QScrollArea) else current
                item_height = content.sizeHint().height()
            else:
                item_height = item.sizeHint().height()
            content_height += max(0, int(item_height))
            if index < item_count - 1:
                content_height += max(0, int(root_layout.spacing()))
        target_height = max(
            self.minimumHeight(), min(content_height, profile_hint.height())
        )
        # The native geometry guard runs after a resize event.  Supplying the
        # requested profile geometry lets it preserve the compact SQLite size
        # instead of retaining the previous PostgreSQL height for one event
        # loop turn.
        generation = getattr(self, "_profile_geometry_generation", 0) + 1
        self._profile_geometry_generation = generation
        self._progtrack_geometry_target = QSize(target_width, target_height)
        # Native Qt styles can issue a second layout resize a few event-loop
        # turns after the stacked page changed.  Keep the requested geometry
        # active while that settles, then allow ordinary user resizing again.
        QTimer.singleShot(
            500,
            lambda: self._clear_profile_geometry_target(generation),
        )
        self.resize(target_width, target_height)

    def _section_geometry_changed(self, _expanded: bool) -> None:
        """Re-measure the visible PG content before and after Qt settles."""
        page = self.pg_page_scroll.widget()
        section = self.sender()
        if isinstance(section, AnimalDialogSection):
            content = section.content_widget()
            content.layout().invalidate()
            content.updateGeometry()
            section.layout().invalidate()
            section.updateGeometry()
        layout = page.layout()
        # While the dialog is hidden, invalidating the page does not invalidate
        # the nested form's cached height for newly shown/hidden section rows.
        for index in range(layout.count()):
            child = layout.itemAt(index).layout()
            if child is not None:
                child.invalidate()
        layout.invalidate()
        layout.activate()
        page.updateGeometry()
        self.pg_page_scroll.updateGeometry()
        self._resize_for_profile()
        QTimer.singleShot(0, self._resize_for_profile)

    def _build_postgres_page(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.pg_host = QLineEdit()
        self.pg_port = QSpinBox()
        self.pg_port.setRange(1, 65535)
        self.pg_database = QComboBox()
        self.pg_database.setEditable(False)
        self.pg_user = QLineEdit()
        self.pg_password = SavedSecretEdit()
        self.pg_sslmode = QComboBox()
        self.pg_sslmode.addItems(BackendConfigurationService.SSL_MODES)
        self.pg_server_name = QLineEdit()
        self.pg_timeout = QSpinBox()
        self.pg_timeout.setRange(1, 300)
        self.pg_managed_root = QLineEdit()
        self.pg_storage_username = QLineEdit(page)
        self.pg_storage_password = SavedSecretEdit(page)
        self._reset_storage_password = False
        self.pg_pool_min = QSpinBox()
        self.pg_pool_min.setRange(1, 64)
        self.pg_pool_max = QSpinBox()
        self.pg_pool_max.setRange(1, 64)

        self.pg_ca_file = QLineEdit()
        self.pg_client_cert_file = QLineEdit()
        self.pg_client_key_file = QLineEdit()
        self.pg_client_key_passphrase = QLineEdit()
        self.pg_client_key_passphrase.setEchoMode(QLineEdit.EchoMode.Password)

        certificate_browse: dict[QLineEdit, QPushButton] = {}

        def certificate_row(widget: QLineEdit, title: str) -> QWidget:
            row = QWidget(page)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            browse = QPushButton(self._text("backend.browse", "Browse"))
            certificate_browse[widget] = browse
            browse.clicked.connect(
                lambda: self._choose_certificate(widget, title)
            )
            row_layout.addWidget(widget, 1)
            row_layout.addWidget(browse)
            return row

        fields = (
            ("backend.postgresql.host", "Host", self.pg_host),
            ("backend.postgresql.port", "Port", self.pg_port),
            ("backend.postgresql.database", "Database", self.pg_database),
            ("backend.postgresql.user", "User", self.pg_user),
            ("backend.postgresql.password", "Password", self.pg_password),
            ("backend.postgresql.sslmode", "SSL mode", self.pg_sslmode),
            ("backend.postgresql.server_name", "TLS server name", self.pg_server_name),
            ("backend.postgresql.ca_file", "CA certificate/bundle", certificate_row(self.pg_ca_file, "CA certificate/bundle")),
        )
        for key, fallback, widget in fields:
            form.addRow(self._text(key, fallback), widget)

        self.pg_mtls_section = AnimalDialogSection("mTLS", collapsed=True, parent=page)
        client_form = QFormLayout()
        client_fields = (
            ("backend.postgresql.client_cert", "Client certificate", certificate_row(self.pg_client_cert_file, "Client certificate")),
            ("backend.postgresql.client_key", "Client private key", certificate_row(self.pg_client_key_file, "Client private key")),
            ("backend.postgresql.client_key_passphrase", "Client-key passphrase", self.pg_client_key_passphrase),
        )
        for key, fallback, widget in client_fields:
            client_form.addRow(self._text(key, fallback), widget)
        self.pg_mtls_section.content_layout().addLayout(client_form)
        self.pg_mtls_section.toggled.connect(self._section_geometry_changed)
        form.addRow(self.pg_mtls_section)

        advanced_fields = (
            ("backend.postgresql.timeout", "Connection timeout (s)", self.pg_timeout),
            ("backend.postgresql.pool_min", "Pool minimum", self.pg_pool_min),
            ("backend.postgresql.pool_max", "Pool maximum", self.pg_pool_max),
        )
        for key, fallback, widget in advanced_fields:
            form.addRow(self._text(key, fallback), widget)
        form.addRow(self._text("backend.storage.root", "Managed document storage"), self.pg_managed_root)
        self.pg_storage_section = AnimalDialogSection(
            self._text("backend.storage.credentials", "Storage credentials"), collapsed=True, parent=page,
        )
        storage_form = QFormLayout()
        if sys.platform == "win32":
            storage_form.addRow(self._text("backend.storage.username", "Storage user"), self.pg_storage_username)
            storage_form.addRow(self._text("backend.storage.password", "Storage password"), self.pg_storage_password)
            reset = QPushButton(self._text("backend.storage.reset", "Use database password"), self.pg_storage_section)
            reset.clicked.connect(self._use_database_storage_password)
            storage_form.addRow(reset)
        else:
            self.pg_storage_username.hide()
            self.pg_storage_password.hide()
            os_mount_note = QLabel(self._text(
                "backend.storage.mount_note", "Use a folder mounted by IT for this operating system account.",
            ))
            os_mount_note.setWordWrap(True)
            storage_form.addRow(os_mount_note)
        self.pg_storage_section.content_layout().addLayout(storage_form)
        self.pg_storage_section.toggled.connect(self._section_geometry_changed)
        form.addRow(self.pg_storage_section)
        layout.addLayout(form)

        self.test_button = QPushButton(
            self._text("backend.postgresql.test", "Test secure connection")
        )
        self.test_button.clicked.connect(self._test_connection)
        layout.addWidget(self.test_button)

        admin_box = QGroupBox(
            self._text("backend.postgresql.admin_group", "Server databases (Lord only)")
        )
        admin_layout = QVBoxLayout(admin_box)
        self.pg_database_table = DatabaseCatalogTable(0, 5, admin_box)
        self.pg_database_table.setHorizontalHeaderLabels([
            self._text("backend.postgresql.db_name", "Database"),
            self._text("backend.postgresql.db_owner", "Owner"),
            self._text("backend.postgresql.db_size", "Size"),
            self._text("backend.postgresql.db_state", "State"),
            self._text("backend.postgresql.db_compatibility", "ProgTrack"),
        ])
        self.pg_database_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.pg_database_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        admin_layout.addWidget(self.pg_database_table)
        self.pg_catalog_status = QLabel(admin_box)
        self.pg_catalog_status.setWordWrap(True)
        admin_layout.addWidget(self.pg_catalog_status)
        admin_buttons = QHBoxLayout()
        self.pg_refresh_databases = QPushButton(
            self._text("backend.postgresql.refresh_databases", "Refresh list")
        )
        self.pg_create_database = QPushButton(
            self._text("backend.postgresql.create_database", "Create database")
        )
        self.pg_use_database = QPushButton(
            self._text("backend.postgresql.use_database", "Use selected")
        )
        self.pg_archive_database = QPushButton(
            self._text("backend.postgresql.archive_database", "Archive/unarchive")
        )
        self.pg_backup_database = QPushButton(
            self._text("backend.postgresql.backup_database", "Backup")
        )
        self.pg_restore_database = QPushButton(
            self._text("backend.postgresql.restore_database", "Restore")
        )
        self.pg_delete_database = QPushButton(
            self._text("backend.postgresql.delete_database", "Delete")
        )
        for button in (
            self.pg_refresh_databases,
            self.pg_create_database,
            self.pg_use_database,
            self.pg_archive_database,
            self.pg_backup_database,
            self.pg_restore_database,
            self.pg_delete_database,
        ):
            admin_buttons.addWidget(button)
        admin_buttons.addStretch(1)
        admin_layout.addLayout(admin_buttons)
        self.pg_refresh_databases.clicked.connect(self._refresh_pg_databases)
        self.pg_create_database.clicked.connect(self._create_pg_database)
        self.pg_use_database.clicked.connect(self._use_selected_pg_database)
        self.pg_archive_database.clicked.connect(self._archive_selected_pg_database)
        self.pg_backup_database.clicked.connect(self._backup_selected_pg_database)
        self.pg_restore_database.clicked.connect(self._restore_selected_pg_database)
        self.pg_delete_database.clicked.connect(self._delete_selected_pg_database)
        layout.addWidget(admin_box)

        migration_row = QHBoxLayout()
        self.pg_sqlite_source = QLineEdit()
        self.pg_sqlite_source.setPlaceholderText(
            self._text("backend.postgresql.sqlite_source.placeholder", "Local SQLite database to transfer")
        )
        migrate_browse = QPushButton(self._text("backend.browse", "Browse"))
        migrate_browse.clicked.connect(self._choose_sqlite_source)
        self.pg_migrate_sqlite = QPushButton(
            self._text("backend.postgresql.migrate_sqlite", "Transfer SQLite to selected database")
        )
        self.pg_migrate_sqlite.clicked.connect(self._migrate_sqlite_to_selected)
        migration_row.addWidget(self.pg_sqlite_source, 1)
        migration_row.addWidget(migrate_browse)
        migration_row.addWidget(self.pg_migrate_sqlite)
        layout.addLayout(migration_row)
        # PostgreSQL has more controls than a highly scaled laptop display can
        # show at once.  Keep the profile's natural size hint for normal
        # displays, but provide a viewport so no connection/certificate/admin
        # control is clipped when the native geometry guard is screen-limited.
        scroll = _ProfileScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(page)
        self.pg_page_scroll = scroll
        self.pages.addWidget(scroll)
        # Reparenting the page into the scroll area rebuilds its focus chain.
        # Set the visual sequence of grouped controls after the page is attached.
        tab_order = (
            self.pg_server_name, self.pg_ca_file, certificate_browse[self.pg_ca_file],
            self.pg_mtls_section.toggle_button(),
            self.pg_client_cert_file, certificate_browse[self.pg_client_cert_file],
            self.pg_client_key_file, certificate_browse[self.pg_client_key_file],
            self.pg_client_key_passphrase, self.pg_timeout,
        )
        for first, second in zip(tab_order, tab_order[1:]):
            QWidget.setTabOrder(first, second)

    def _choose_certificate(self, widget: QLineEdit, title: str) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, title, widget.text(), "Certificate/key files (*)"
        )
        if selected:
            widget.setText(str(Path(selected).resolve()))

    def _choose_sqlite_source(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self._text("backend.postgresql.sqlite_source.title", "Choose SQLite database"),
            self.sqlite_folder.text(),
            "SQLite database (*.sqlite3)",
        )
        if selected:
            self.pg_sqlite_source.setText(str(Path(selected).resolve()))

    def _admin_service(self, *, discovery=False):
        from .backend.postgresql_admin import PostgreSQLAdministrationService
        service = BoundedCatalogReader if discovery else PostgreSQLAdministrationService
        return service(
            self.service,
            self.postgresql_settings(),
            discovery_password=self._password() if discovery else "",
            client_key_passphrase=self.pg_client_key_passphrase.text() or self.service.read_client_key_passphrase(),
            authorized=self.authorized,
            validate_initiator=None if discovery else self.validate_admin_initiator,
            active_database=self.active_database or self.service.saved_postgresql().database,
            audit_callback=None if discovery else self._admin_audit,
        )

    def _admin_action(self, action, target, execute):
        from .backend.postgresql_admin import BackendAdminError
        from .backend.catalog_revision import CatalogClockUnavailable

        if not target:
            return False, None
        labels = {
            "test": "Test PostgreSQL connection",
            "save": "Validate PostgreSQL profile before saving",
            "select": "Validate selected database",
            "create": "Create and initialize database",
            "archive": "Archive or unarchive database",
            "backup": "Create database backup",
            "restore": "Restore database backup",
            "delete": "Delete database",
            "transfer": "Transfer SQLite data to PostgreSQL",
        }
        service = self._admin_service()
        prompt = BackendAdminAuthorizationDialog(
            self, text=self._text, action=self._text(f"backend.admin.action.{action}", labels[action]),
            server=f"{service.settings.host}:{service.settings.port}", database=target,
        )
        password = ""
        operation = None
        try:
            if prompt.exec() != QDialog.DialogCode.Accepted:
                return False, None
            login, password = prompt.login.text(), prompt.password.text()
            prompt.password.clear()
            with service.authorize(action, target, login=login, password=password) as operation:
                return True, execute(service, operation)
        except (BackendError, BackendConfigurationPermissionError) as error:
            from .backend.document_operations import DocumentPublicationError
            from .backend.managed_storage import ManagedStorageUnavailable
            from .document_messages import document_error

            if getattr(operation, "catalog_touched", False):
                self._catalog_usable = False
                self._set_catalog_status("dirty", "The server catalog changed. Refresh the list before selecting a database.")
                self._update_catalog_actions()
            message = self._text("backend.admin.failed", "The operation failed. Check the administrator credentials, permissions, target, and server connection.")
            if isinstance(error, BackendAdminError):
                message = self._text(f"backend.admin.error.{error.reason}", str(error))
            elif isinstance(error, (DocumentPublicationError, ManagedStorageUnavailable)):
                message = document_error(self.messages, error)
            elif isinstance(error, CatalogClockUnavailable):
                message = self._text("backend.catalog.clock_required", "The server catalog clock cannot be written. Ask the database administrator to verify its setup, permissions, and current operation status.")
            QMessageBox.warning(
                self, self._text("backend.postgresql.admin_failed.title", "Database administration failed"),
                message,
            )
            return False, None
        finally:
            prompt.password.clear()
            password = ""
            prompt.deleteLater()

    def _admin_audit(self, action: str, actor: str, payload: dict[str, Any]) -> None:
        if self.audit_callback is not None:
            self.audit_callback({
                "action": action,
                "actor_login": actor,
                **payload,
            })

    def _set_catalog_status(self, key, fallback, timestamp=""):
        self.pg_catalog_status.setText(self._text(f"backend.catalog.{key}", fallback).replace("{time}", timestamp))

    def _database_choice(self):
        value = self.pg_database.currentData()
        return value if isinstance(value, str) else ""

    def _update_catalog_actions(self):
        usable = self._catalog_usable
        has_choice = usable and bool(self._database_choice())
        row = selected_database_row(self.pg_database_table)
        self.pg_database.setEnabled(usable and self.pg_database.count() > 0)
        self.pg_create_database.setEnabled(usable)
        self.test_button.setEnabled(has_choice and self._staged_storage_request is None)
        self.pg_migrate_sqlite.setEnabled(has_choice)
        self.pg_use_database.setEnabled(usable and row is not None and self.pg_database.findData(row.name) >= 0)
        for button in (self.pg_archive_database, self.pg_backup_database, self.pg_restore_database, self.pg_delete_database):
            button.setEnabled(usable and row is not None)
        can_save = self.sqlite_radio.isChecked() or has_choice
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(can_save and self._staged_storage_request is None)

    def _invalidate_catalog_secret(self):
        self._draft_generation = uuid.uuid4().hex if self.pg_password.text() or self.pg_client_key_passphrase.text() else None
        self._invalidate_catalog_context()

    def _invalidate_catalog_context(self):
        self._cancel_catalog_request()
        self._catalog_context = None
        self._catalog_usable = False
        self._saved_pg_database = ""
        self.pg_database.clear()
        render_database_table(self.pg_database_table, (), self._text)
        self._set_catalog_status("refresh_needed", "Refresh the database list for these connection settings.")
        self._update_catalog_actions()

    def _catalog_key(self):
        return context_key(self.service, self.postgresql_settings(), self._draft_generation)

    def _show_catalog(self, snapshot, *, usable, preferred=""):
        from .backend.postgresql_admin import PostgreSQLAdministrationService

        selected = self._database_choice() or preferred or self._saved_pg_database
        self._catalog_usable = usable
        with QSignalBlocker(self.pg_database):
            self.pg_database.clear()
            for row in sorted(snapshot.rows, key=lambda item: (item.name.casefold(), item.name)):
                if row.compatible is True and row.allow_connections and not row.is_template:
                    try:
                        PostgreSQLAdministrationService.validate_database_name(row.name)
                    except BackendError:
                        continue
                    self.pg_database.addItem(row.name, row.name)
            self.pg_database.setCurrentIndex(self.pg_database.findData(selected))
        render_database_table(self.pg_database_table, snapshot.rows, self._text)
        self._check_backup_selection()
        self._update_catalog_actions()

    def _open_catalog_once(self):
        if self._catalog_opened:
            return
        self._catalog_opened = True
        if not self.pg_host.text().strip() or not self.pg_user.text().strip():
            self._set_catalog_status("refresh_needed", "Refresh the database list for these connection settings.")
            return
        context = self._catalog_key()
        snapshot = read_snapshot(self.service.catalog_cache_path(), context)
        if snapshot is not None:
            self._show_catalog(snapshot, usable=False)
        self._request_catalog_read(context, cached=snapshot)

    def hideEvent(self, event):
        self._catalog_opened = False
        self._cancel_catalog_request()
        super().hideEvent(event)

    def _cancel_catalog_request(self):
        self._catalog_generation += 1
        if self._catalog_request is not None:
            self._catalog_request.cancelled.set()
            self._catalog_request = None

    def _request_catalog_read(self, context, *, cached=None, preferred=""):
        if not self.isVisible():
            return
        self._cancel_catalog_request()
        self._catalog_context = context
        self._catalog_preferred = preferred
        self._catalog_usable = False
        self._update_catalog_actions()
        self._set_catalog_status("checking", "Checking the database list…")
        worker = CatalogReadWorker(self._catalog_generation, self._admin_service(discovery=True), cached)
        self._catalog_request = worker
        worker.signals.finished.connect(self._finish_catalog_read, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(worker)

    def _finish_catalog_read(self, generation, result):
        if generation != self._catalog_generation or not self.isVisible():
            return
        self._catalog_request = None
        context = self._catalog_context
        if context != self._catalog_key():
            self._invalidate_catalog_context()
            return
        snapshot = result.snapshot
        if snapshot is None:
            self.pg_database.clear()
            self._set_catalog_status("discovery_failed", "The database list could not be verified. Refresh to try again.")
            self._update_catalog_actions()
            QMessageBox.warning(
                self,
                self._text("backend.postgresql.admin_failed.title", "Database administration failed"),
                self.pg_catalog_status.text(),
            )
            return
        self._show_catalog(snapshot, usable=result.usable, preferred=self._catalog_preferred)
        if result.status == "dirty":
            self._set_catalog_status("dirty", "The server catalog changed. Refresh the list before selecting a database.")
            return
        if result.status == "stale" or snapshot.revision is None:
            self._set_catalog_status("stale", "Catalog revision unavailable. Last discovery: {time}.", snapshot.discovered_at)
        else:
            self._set_catalog_status("current", "Last discovery: {time}.", snapshot.discovered_at)
        if result.persist:
            try:
                write_snapshot(self.service.catalog_cache_path(), context, snapshot)
            except OSError:
                self._set_catalog_status("cache_failed", "Last discovery: {time}. The local cache could not be saved.", snapshot.discovered_at)

    def _refresh_pg_databases(self) -> None:
        context = self._catalog_key()
        if context != self._catalog_context:
            self.pg_database.clear()
            render_database_table(self.pg_database_table, (), self._text)
        self._request_catalog_read(context)

    def _selected_pg_database(self) -> str:
        row = selected_database_row(self.pg_database_table)
        return row.name if self._catalog_usable and row is not None else ""

    def _create_pg_database(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            self._text("backend.postgresql.create_database.title", "Create PostgreSQL database"),
            self._text("backend.postgresql.create_database.prompt", "Database name:"),
        )
        if not accepted or not name.strip():
            return
        target = name.strip()
        success, _ = self._admin_action("create", target, lambda service, op: service.create_database(target, operation=op))
        if success:
            self._request_catalog_read(self._catalog_key(), preferred=target)

    def _use_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if self.pg_database.findData(selected) < 0:
            return
        success, settings = self._admin_action("select", selected, lambda service, op: service.switch_database(selected, operation=op))
        if success:
            self.pg_database.setCurrentIndex(self.pg_database.findData(settings.database))

    def _archive_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        success, _ = self._admin_action("archive", selected, lambda service, op: service.archive_database(selected, operation=op))
        if success:
            self._verified_backup = None
            self._refresh_pg_databases()

    def _effective_storage_root(self):
        return str(self.service.environ.get("PROGTRACK_MANAGED_ROOT", self.pg_managed_root.text().strip()))

    @staticmethod
    def _storage_endpoint(settings):
        return settings.host, settings.port, settings.server_name

    def _storage_root_for_target(self, database):
        saved = self.service.saved_postgresql()
        staged = self.postgresql_settings()
        if database == saved.database and self._storage_endpoint(saved) == self._storage_endpoint(staged):
            return self._effective_storage_root()
        root = QFileDialog.getExistingDirectory(
            self, self._text("backend.storage.choose_target", "Choose local managed storage for {database}").format(database=database),
            "",
        )
        return root or None

    def _prepare_staged_storage(self):
        saved = self.service.saved_postgresql()
        staged = self.postgresql_settings()
        if (
            saved.database
            and "PROGTRACK_MANAGED_ROOT" not in self.service.environ
            and (saved.database != staged.database or self._storage_endpoint(saved) != self._storage_endpoint(staged))
            and saved.managed_root == staged.managed_root
        ):
            root = self._storage_root_for_target(staged.database)
            if not root:
                return False
            self.pg_managed_root.setText(root)
        return True

    def _use_database_storage_password(self):
        self._reset_storage_password = True
        self.pg_storage_password.clear()
        self.pg_storage_password.set_saved(False, "")

    def _storage_password_replacement(self):
        return self.pg_storage_password.text() or ("" if self._reset_storage_password else None)

    def _storage_draft(self):
        return (
            self.postgres_radio.isChecked(), self.postgresql_settings(), self._effective_storage_root(),
            self.pg_password.text(), self.pg_client_key_passphrase.text(),
            self.pg_storage_username.text(), self._storage_password_replacement(),
        )

    def _cancel_staged_storage(self, *_args):
        if self._staged_storage_request is not None:
            worker, _, _ = self._staged_storage_request
            self._staged_storage_request = None
            worker.cancelled.set()

    def _check_staged_storage(self, on_available):
        from .managed_storage_worker import check_configured_storage
        from .managed_storage_notice import ManagedStorageProbeWorker

        if self._staged_storage_request is not None:
            return
        draft = self._storage_draft()
        _, settings, root, _, _, username, password = draft
        configuration = self.service
        try:
            runtime_dsn = configuration.connection_dsn(
                settings,
                password=self._password(),
                client_key_passphrase=self.pg_client_key_passphrase.text() or configuration.read_client_key_passphrase(),
            )
        except (BackendError, BackendConfigurationValidationError) as exc:
            self._report_staged_storage(root, getattr(exc, "reason", "not_accessible"))
            return

        def check(*, cancellation):
            check_configured_storage(
                configuration, root, runtime_dsn=runtime_dsn, username=username, password=password,
                writable=True, cancellation=cancellation,
            )

        worker = ManagedStorageProbeWorker(check)
        self._staged_storage_request = (worker, draft, on_available)
        worker.signals.finished.connect(self._finish_staged_storage, Qt.ConnectionType.QueuedConnection)
        self._update_catalog_actions()
        QThreadPool.globalInstance().start(worker)

    def _finish_staged_storage(self, worker, reason):
        request = self._staged_storage_request
        if request is None or request[0] is not worker:
            return
        self._staged_storage_request = None
        self._update_catalog_actions()
        _, draft, on_available = request
        if not self.isVisible() or draft != self._storage_draft():
            return
        self._report_staged_storage(draft[2], reason)
        if not reason:
            on_available()

    def _report_staged_storage(self, root, reason):
        self.managed_storage_tested.emit(root, not reason)
        if reason:
            fallback = self._text(
                "backend.storage.check_failed", "Cannot access managed document storage. Check the path and credentials.",
            )
            QMessageBox.warning(self, self._text("backend.storage.root", "Managed document storage"),
                                self._text(f"backend.storage.error.{reason}", fallback))

    def _backup_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if not selected:
            return
        target, _ = QFileDialog.getSaveFileName(
            self,
            self._text("backend.postgresql.backup_database.title", "Save PostgreSQL backup"),
            str(self.service.paths.exports / (selected + ".ptbackup")),
            "ProgTrack PostgreSQL backup (*.ptbackup)",
        )
        if not target:
            return
        self._verified_backup = None
        storage_root = self._storage_root_for_target(selected)
        if storage_root is None:
            return
        success, backup = self._admin_action("backup", selected, lambda service, op: service.backup_database(target, database=selected, operation=op, storage_root=storage_root))
        if success:
            self._verified_backup = backup
            QMessageBox.information(self, self._text("backend.postgresql.backup_database.success.title", "Backup created"), target)

    def _restore_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if not selected:
            return
        source, _ = QFileDialog.getOpenFileName(
            self,
            self._text("backend.postgresql.restore_database.title", "Restore PostgreSQL backup"),
            str(self.service.paths.exports),
            "ProgTrack PostgreSQL backup (*.ptbackup)",
        )
        if not source:
            return
        answer = QMessageBox.question(
            self,
            self._text("backend.postgresql.restore_database.confirm.title", "Confirm restore"),
            self._text(
                "backend.postgresql.restore_database.confirm",
                "Restore this complete backup into the selected database and replace its data?",
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        storage_root = self._storage_root_for_target(selected)
        if storage_root is None:
            return
        success, _ = self._admin_action("restore", selected, lambda service, op: service.restore_backup(source, database=selected, confirmed=True, operation=op, storage_root=storage_root))
        if success:
            self._verified_backup = None
            self._refresh_pg_databases()

    def _delete_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if not selected:
            return
        typed, accepted = QInputDialog.getText(
            self,
            self._text("backend.postgresql.delete_database.title", "Delete PostgreSQL database"),
            self._text("backend.postgresql.delete_database.prompt", "Type the exact database name to confirm:"),
        )
        if not accepted or typed != selected:
            return
        success, _ = self._admin_action("delete", selected, lambda service, op: service.delete_database(
            selected, typed_confirmation=typed, backup=self._verified_backup, operation=op,
        ))
        if success:
            self._verified_backup = None
            self._refresh_pg_databases()

    def _migrate_sqlite_to_selected(self) -> None:
        """Export a local SQLite backend and import it into an empty PG target.

        This is deliberately an explicit canonical interchange operation.  It
        never changes the saved profile, never overwrites a non-empty target,
        and uses temporary backend instances so the active UI connection is
        not reconfigured while the transfer is reviewed or executed.
        """
        from dataclasses import replace
        from contextlib import closing
        import sqlite3
        import tempfile

        from .backend.facade import ProgTrackBackend

        source_path = Path(self.pg_sqlite_source.text().strip()).expanduser()
        target_name = self._database_choice() if self._catalog_usable else ""
        if not source_path.is_file() or not target_name:
            QMessageBox.warning(
                self,
                self._text("backend.postgresql.migration.title", "Canonical transfer"),
                self._text(
                    "backend.postgresql.migration.missing",
                    "Choose an existing local SQLite database, a PostgreSQL target, "
                    "and an accessible managed-document root first.",
                ),
            )
            return
        if self.backend is None:
            QMessageBox.warning(
                self,
                self._text("backend.postgresql.migration.title", "Canonical transfer"),
                self._text(
                    "backend.postgresql.migration.unavailable",
                    "The running application backend is not available for this transfer.",
                ),
            )
            return

        target_root = self._storage_root_for_target(target_name)
        if target_root is None:
            return

        try:
            with tempfile.TemporaryDirectory(prefix="progtrack-transfer-") as folder:
                package_path = Path(folder) / "transfer.ptdb"
                current_path = getattr(self.backend.paths, "database_path", None)
                if current_path is not None and Path(current_path).resolve() == source_path.resolve():
                    self.backend.interchange.export_package(package_path)
                    preview = self.backend.interchange.preview(package_path)
                else:
                    copy_path = Path(folder) / "source.sqlite3"
                    with closing(sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)) as source, \
                         closing(sqlite3.connect(copy_path)) as copy:
                        source.backup(copy)
                    source_managed = source_path.parent / "managed"
                    if source_path.parent.name == "database" and not source_managed.is_dir():
                        source_managed = source_path.parent.parent / "managed"
                    source_paths = replace(
                        self.service.paths, profile=BackendProfile.STANDALONE_SQLITE,
                        database_path=copy_path, managed_root=source_managed,
                        managed_documents=source_managed / "documents", managed_config_assets=source_managed / "config-assets",
                    )
                    with closing(ProgTrackBackend(source_paths, acquire_process_lock=False, create_managed_roots=False)) as backend:
                        backend.interchange.export_package(package_path)
                        preview = backend.interchange.preview(package_path)
                if not preview.valid:
                    raise BackendError("Invalid canonical transfer package.")
                counts = ", ".join(f"{key}: {value}" for key, value in sorted(preview.counts.items()))
                answer = QMessageBox.question(
                    self, self._text("backend.postgresql.migration.confirm.title", "Confirm canonical transfer"),
                    self._text("backend.admin.transfer_summary", "Source: {source}\nTarget: {target}\nPreflight counts: {counts}\n\n").format(
                        source=source_path, target=target_name, counts=counts,
                    ) + self._text("backend.postgresql.migration.confirm", "Transfer the validated package into the empty PostgreSQL target without overwriting existing data?"),
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
                success, _ = self._admin_action("transfer", target_name, lambda service, op: service.migrate_interchange(
                    package_path, database=target_name, operation=op, storage_root=target_root,
                ))
            if success:
                self._clear_verified_backup()
                self._refresh_pg_databases()
                QMessageBox.information(
                    self, self._text("backend.postgresql.migration.success.title", "Transfer complete"),
                    self._text("backend.postgresql.migration.success", "The canonical dataset and managed files were imported successfully."),
                )
        except (BackendError, OSError, sqlite3.Error) as error:
            from .backend.document_operations import DocumentPublicationError
            from .backend.managed_storage import ManagedStorageUnavailable
            from .document_messages import document_error

            QMessageBox.warning(
                self,
                self._text("backend.postgresql.migration.failed.title", "Transfer failed"),
                document_error(self.messages, error) if isinstance(error, (DocumentPublicationError, ManagedStorageUnavailable))
                else self._text("backend.admin.transfer_source_failed", "The local SQLite source could not be read or the transfer package could not be verified."),
            )

    def _load(self) -> None:
        document = self.service.load_document()
        profile = str(
            document.get("profile") or BackendProfile.STANDALONE_SQLITE.value
        )
        sqlite_selected = profile != BackendProfile.SHARED_POSTGRESQL.value
        with QSignalBlocker(self.sqlite_radio), QSignalBlocker(self.postgres_radio):
            selected_radio = self.sqlite_radio if sqlite_selected else self.postgres_radio
            selected_radio.setChecked(True)
        configured_sqlite = Path(
            str(
                document.get("sqlite_path")
                or self.service.paths.database_path
                or "progtrack.sqlite3"
            )
        )
        self.sqlite_filename.setText(configured_sqlite.name)
        self.sqlite_folder.setText(
            str(
                configured_sqlite.parent
                if configured_sqlite.is_absolute()
                else self.service.paths.data_root / "database"
            )
        )
        pg = self.service.saved_postgresql()
        self.pg_host.setText(pg.host)
        self.pg_port.setValue(pg.port)
        self._saved_pg_database = pg.database
        self.pg_user.setText(pg.user)
        self.pg_password.set_saved(
            bool(self.service.read_password()),
            self._text(
                "backend.postgresql.password.saved",
                "Stored securely; leave blank to keep",
            )
        )
        self.pg_sslmode.setCurrentText(pg.sslmode)
        self.pg_server_name.setText(pg.server_name)
        self.pg_ca_file.setText(pg.ca_file)
        self.pg_client_cert_file.setText(pg.client_cert_file)
        self.pg_client_key_file.setText(pg.client_key_file)
        self.pg_client_key_passphrase.clear()
        self.pg_timeout.setValue(pg.connect_timeout)
        self.pg_managed_root.setText(pg.managed_root)
        self.pg_storage_username.setText(self.service.saved_storage_username())
        self.pg_storage_password.set_saved(
            sys.platform == "win32" and bool(self.service.read_storage_password()),
            self._text("backend.storage.password_saved", "A storage password is saved securely. Leave blank to keep it."),
        )
        self.pg_pool_min.setValue(pg.pool_min)
        self.pg_pool_max.setValue(pg.pool_max)
        overrides = self.service.environment_overrides()
        self.override_label.setText(
            self._text(
                "backend.overrides.active",
                "Deployment environment overrides take priority: {values}",
            ).replace("{values}", ", ".join(overrides))
            if overrides
            else self._text(
                "backend.overrides.none",
                "No deployment environment overrides are active.",
            )
        )
        self._select_profile_page(sqlite_selected)

    def postgresql_settings(self) -> PostgreSQLSettings:
        return PostgreSQLSettings(
            host=self.pg_host.text().strip(),
            port=self.pg_port.value(),
            database=self._database_choice(),
            user=self.pg_user.text().strip(),
            sslmode=self.pg_sslmode.currentText(),
            server_name=self.pg_server_name.text().strip(),
            ca_file=self.pg_ca_file.text().strip(),
            client_cert_file=self.pg_client_cert_file.text().strip(),
            client_key_file=self.pg_client_key_file.text().strip(),
            connect_timeout=self.pg_timeout.value(),
            managed_root=self.pg_managed_root.text().strip(),
            pool_min=self.pg_pool_min.value(),
            pool_max=self.pg_pool_max.value(),
        )

    def _password(self) -> str:
        return self.pg_password.text() or self.service.read_password()

    def _test_connection(self) -> None:
        if self._staged_storage_request is not None or not self._catalog_usable or not self._prepare_staged_storage():
            return
        success, _ = self._admin_action("test", self._database_choice(),
                                        lambda service, op: service.test_connection(operation=op))
        if success:
            self._check_staged_storage(self._show_connection_test_success)

    def _show_connection_test_success(self):
        QMessageBox.information(
            self,
            self._text("backend.test.success.title", "Connection successful"),
            self._text(
                "backend.test.success",
                "The PostgreSQL connection succeeded. No profile was changed.",
            ),
        )

    def _save(self) -> None:
        if self._staged_storage_request is not None:
            return
        pg = self.postgresql_settings()
        if self.postgres_radio.isChecked():
            if not self._catalog_usable or not pg.database or not self._prepare_staged_storage():
                return
            pg = self.postgresql_settings()
            success, _ = self._admin_action(
                "save", pg.database, lambda service, op: service.test_connection(operation=op, for_save=True),
            )
            if success:
                self._check_staged_storage(lambda: self._persist_profile(BackendProfile.SHARED_POSTGRESQL, pg))
        else:
            self._persist_profile(BackendProfile.STANDALONE_SQLITE, pg)

    def _persist_profile(self, profile, pg):
        sqlite_target: Path | None = None
        sqlite_is_new = False
        try:
            if profile is BackendProfile.SHARED_POSTGRESQL:
                if self.validate_admin_initiator is not None and not self.validate_admin_initiator():
                    raise BackendConfigurationPermissionError(
                        self._text("backend.permission.denied", "Only a Lord account may configure the backend.")
                    )
            else:
                sqlite_target = self.service.validate_sqlite_location(
                    self.sqlite_folder.text(), self.sqlite_filename.text()
                )
                sqlite_is_new = not sqlite_target.exists()
            document = self.service.save(
                profile=profile,
                sqlite_filename=self.sqlite_filename.text(),
                sqlite_folder=self.sqlite_folder.text(),
                postgresql=pg,
                password=self.pg_password.text(),
                client_key_passphrase=self.pg_client_key_passphrase.text(),
                storage_username=self.pg_storage_username.text(),
                storage_password=self._storage_password_replacement(),
                authorized=self.authorized,
            )
            if self.audit_callback is not None:
                self.audit_callback(
                    {
                        "profile": document["profile"],
                        "sqlite_filename": Path(document["sqlite_path"]).name,
                        "postgresql_host": document["postgresql"].get("host", ""),
                        "postgresql_database": document["postgresql"].get("database", ""),
                        "sslmode": document["postgresql"].get("sslmode", ""),
                        "restart_required": True,
                    }
                )
        except (BackendConfigurationPermissionError, BackendConfigurationValidationError, BackendError) as exc:
            QMessageBox.warning(
                self,
                self._text("backend.save.failed.title", "Cannot save backend"),
                str(exc),
            )
            return
        self.managed_storage_profile_saved.emit()
        message_key = (
            "backend.save.sqlite_created"
            if profile is BackendProfile.STANDALONE_SQLITE and sqlite_is_new
            else "backend.save.success"
        )
        fallback = (
            "A new empty database has been created. Restart ProgTrack to activate it."
            if message_key.endswith("sqlite_created")
            else "The backend profile was saved. Restart ProgTrack to activate it."
        )
        QMessageBox.information(
            self,
            self._text("backend.save.success.title", "Backend saved"),
            self._text(message_key, fallback),
        )
        self.accept()
