"""Lord-only Qt dialog for restart-only backend profile configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QSize, QTimer, QUrl
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
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .dialog_geometry import install_dialog_geometry_guard

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


class BackendConfigurationDialog(QDialog):
    def __init__(
        self,
        service: BackendConfigurationService,
        messages: dict[str, Any],
        *,
        authorized: bool,
        audit_callback: Callable[[dict[str, Any]], None] | None = None,
        backend: Any | None = None,
        actor_login: str = "",
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
        self._verified_backup_database = ""
        self.setWindowTitle(self._text("backend.dialog.title", "Backend"))
        self.setMinimumWidth(520)
        self._build()
        self._load()
        install_dialog_geometry_guard(self, minimum=QSize(520, 300))

    def _text(self, key: str, fallback: str) -> str:
        return str(self.messages.get(key, fallback))

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
        root.addWidget(self.buttons)

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
        QTimer.singleShot(0, self._resize_for_profile)

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
                item_height = current.sizeHint().height()
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
            current.adjustSize()
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
                item_height = current.sizeHint().height()
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

    def _build_postgres_page(self) -> None:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.pg_host = QLineEdit()
        self.pg_port = QSpinBox()
        self.pg_port.setRange(1, 65535)
        self.pg_database = QLineEdit()
        self.pg_user = QLineEdit()
        self.pg_password = QLineEdit()
        self.pg_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.pg_sslmode = QComboBox()
        self.pg_sslmode.addItems(BackendConfigurationService.SSL_MODES)
        self.pg_server_name = QLineEdit()
        self.pg_timeout = QSpinBox()
        self.pg_timeout.setRange(1, 300)
        self.pg_managed_root = QLineEdit()
        self.pg_pool_min = QSpinBox()
        self.pg_pool_min.setRange(1, 64)
        self.pg_pool_max = QSpinBox()
        self.pg_pool_max.setRange(1, 64)

        self.pg_ca_file = QLineEdit()
        self.pg_client_cert_file = QLineEdit()
        self.pg_client_key_file = QLineEdit()
        self.pg_client_key_passphrase = QLineEdit()
        self.pg_client_key_passphrase.setEchoMode(QLineEdit.EchoMode.Password)

        def certificate_row(widget: QLineEdit, title: str) -> QWidget:
            row = QWidget(page)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            browse = QPushButton(self._text("backend.browse", "Browse"))
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
            ("backend.postgresql.client_cert", "Client certificate", certificate_row(self.pg_client_cert_file, "Client certificate")),
            ("backend.postgresql.client_key", "Client private key", certificate_row(self.pg_client_key_file, "Client private key")),
            ("backend.postgresql.client_key_passphrase", "Client-key passphrase", self.pg_client_key_passphrase),
            ("backend.postgresql.timeout", "Connection timeout (s)", self.pg_timeout),
            ("backend.postgresql.managed_root", "Server-managed document root", self.pg_managed_root),
            ("backend.postgresql.pool_min", "Pool minimum", self.pg_pool_min),
            ("backend.postgresql.pool_max", "Pool maximum", self.pg_pool_max),
        )
        for key, fallback, widget in fields:
            form.addRow(self._text(key, fallback), widget)
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
        self.pg_database_table = QTableWidget(0, 5, admin_box)
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
        self.pages.addWidget(page)

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

    def _admin_service(self):
        from .backend.postgresql_admin import PostgreSQLAdministrationService
        return PostgreSQLAdministrationService(
            self.service,
            self.postgresql_settings(),
            password=self._password(),
            authorized=self.authorized,
            actor_login=self.actor_login,
            audit_callback=self._admin_audit,
        )

    def _admin_audit(self, action: str, actor: str, payload: dict[str, Any]) -> None:
        if self.audit_callback is not None:
            self.audit_callback({
                "action": action,
                "actor_login": actor,
                **payload,
            })

    def _refresh_pg_databases(self) -> None:
        try:
            rows = self._admin_service().list_databases()
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._text("backend.postgresql.admin_failed.title", "Database administration failed"),
                str(exc),
            )
            return
        self.pg_database_table.setRowCount(0)
        for row in rows:
            index = self.pg_database_table.rowCount()
            self.pg_database_table.insertRow(index)
            values = (
                row.name,
                row.owner,
                str(row.size_bytes),
                "archived" if row.archived else "available",
                "yes" if row.compatible is True else ("no" if row.compatible is False else "unknown"),
            )
            for column, value in enumerate(values):
                self.pg_database_table.setItem(index, column, QTableWidgetItem(str(value)))
        self.pg_database_table.resizeColumnsToContents()

    def _selected_pg_database(self) -> str:
        row = self.pg_database_table.currentRow()
        if row < 0 or self.pg_database_table.item(row, 0) is None:
            return self.pg_database.text().strip()
        return self.pg_database_table.item(row, 0).text().strip()

    def _create_pg_database(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            self._text("backend.postgresql.create_database.title", "Create PostgreSQL database"),
            self._text("backend.postgresql.create_database.prompt", "Database name:"),
        )
        if not accepted or not name.strip():
            return
        try:
            self._admin_service().create_database(
                name.strip(), initialize=self._initialize_created_database
            )
            self._refresh_pg_databases()
        except Exception as exc:
            QMessageBox.warning(self, self._text("backend.postgresql.admin_failed.title", "Database administration failed"), str(exc))

    def _initialize_created_database(self, database: str) -> None:
        from dataclasses import replace
        from .backend.adapters import PostgreSQLAdapter

        pg = replace(self.postgresql_settings(), database=database)
        dsn = self.service.connection_dsn(
            pg,
            password=self._password(),
            client_key_passphrase=self.pg_client_key_passphrase.text()
            or self.service.read_client_key_passphrase(),
            allow_environment=False,
        )
        adapter = PostgreSQLAdapter(
            dsn, min_size=pg.pool_min, max_size=pg.pool_max
        )
        try:
            adapter.migrate()
            adapter.set_installation_value(
                "managed_document_root", self.pg_managed_root.text().strip()
            )
        finally:
            adapter.close()

    def _use_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if selected:
            self.pg_database.setText(selected)

    def _archive_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if not selected:
            return
        try:
            row = next(r for r in self._admin_service().list_databases() if r.name == selected)
            self._admin_service().archive_database(selected, archived=not row.archived)
            self._refresh_pg_databases()
        except Exception as exc:
            QMessageBox.warning(self, self._text("backend.postgresql.admin_failed.title", "Database administration failed"), str(exc))

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
        try:
            service = self._admin_service()
            service.backup_database(target, managed_root=self.pg_managed_root.text().strip())
            self._verified_backup_database = selected
            QMessageBox.information(self, self._text("backend.postgresql.backup_database.success.title", "Backup created"), target)
        except Exception as exc:
            QMessageBox.warning(self, self._text("backend.postgresql.admin_failed.title", "Database administration failed"), str(exc))

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
        try:
            self._admin_service().restore_backup(
                source,
                managed_root=self.pg_managed_root.text().strip(),
                confirmed=True,
            )
            self._refresh_pg_databases()
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._text("backend.postgresql.admin_failed.title", "Database administration failed"),
                str(exc),
            )

    def _delete_selected_pg_database(self) -> None:
        selected = self._selected_pg_database()
        if not selected:
            return
        typed, accepted = QInputDialog.getText(
            self,
            self._text("backend.postgresql.delete_database.title", "Delete PostgreSQL database"),
            self._text("backend.postgresql.delete_database.prompt", "Type the exact database name to confirm:"),
        )
        if not accepted or typed.strip() != selected:
            return
        try:
            self._admin_service().delete_database(
                selected,
                backup_verified=self._verified_backup_database == selected,
            )
        except Exception as exc:
            QMessageBox.warning(self, self._text("backend.postgresql.admin_failed.title", "Database administration failed"), str(exc))

    def _migrate_sqlite_to_selected(self) -> None:
        """Export a local SQLite backend and import it into an empty PG target.

        This is deliberately an explicit canonical interchange operation.  It
        never changes the saved profile, never overwrites a non-empty target,
        and uses temporary backend instances so the active UI connection is
        not reconfigured while the transfer is reviewed or executed.
        """
        from dataclasses import replace
        import tempfile

        from .backend.facade import ProgTrackBackend

        source_path = Path(self.pg_sqlite_source.text().strip()).expanduser()
        target_name = self._selected_pg_database() or self.pg_database.text().strip()
        target_root = Path(self.pg_managed_root.text().strip()).expanduser()
        if (
            not source_path.is_file()
            or not target_name
            or not target_root.is_absolute()
            or not target_root.is_dir()
        ):
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

        source_backend = None
        target_backend = None
        temporary_source = False
        temporary_target = False
        package_path = Path(tempfile.mkstemp(prefix="progtrack-transfer-", suffix=".ptdb")[1])
        package_path.unlink(missing_ok=True)
        try:
            current_path = getattr(getattr(self.backend, "paths", None), "database_path", None)
            current_profile = getattr(getattr(self.backend, "paths", None), "profile", None)
            if (
                current_path is not None
                and current_profile is BackendProfile.STANDALONE_SQLITE
                and Path(current_path).resolve() == source_path.resolve()
            ):
                source_backend = self.backend
            else:
                source_managed = source_path.parent / "managed"
                source_paths = replace(
                    self.service.paths,
                    profile=BackendProfile.STANDALONE_SQLITE,
                    database_path=source_path,
                    managed_root=source_managed,
                    managed_documents=source_managed / "documents",
                    managed_config_assets=source_managed / "config-assets",
                )
                source_backend = ProgTrackBackend(
                    source_paths,
                    acquire_process_lock=False,
                    bootstrap_seed=False,
                )
                temporary_source = True

            target_settings = replace(
                self.postgresql_settings(),
                database=target_name,
                managed_root=str(target_root),
            )
            target_dsn = self.service.connection_dsn(
                target_settings,
                password=self._password(),
                client_key_passphrase=(
                    self.pg_client_key_passphrase.text()
                    or self.service.read_client_key_passphrase()
                ),
                allow_environment=False,
            )
            target_paths = replace(
                self.service.paths,
                profile=BackendProfile.SHARED_POSTGRESQL,
                database_path=None,
                managed_root=target_root,
                managed_documents=target_root / "documents",
                managed_config_assets=target_root / "config-assets",
            )
            target_backend = ProgTrackBackend(
                target_paths,
                postgres_dsn=target_dsn,
                postgres_pool_min=target_settings.pool_min,
                postgres_pool_max=target_settings.pool_max,
                acquire_process_lock=False,
                bootstrap_seed=False,
            )
            temporary_target = True
            source_backend.interchange.export_package(package_path)
            preview = target_backend.interchange.preview(package_path)
            if not preview.valid:
                QMessageBox.warning(
                    self,
                    self._text("backend.postgresql.migration.failed.title", "Transfer failed"),
                    "; ".join(preview.errors),
                )
                return
            counts = ", ".join(
                f"{key}: {value}" for key, value in sorted(preview.counts.items())
            )
            answer = QMessageBox.question(
                self,
                self._text(
                    "backend.postgresql.migration.confirm.title",
                    "Confirm canonical transfer",
                ),
                (
                    f"Source: {source_path}\nTarget: {target_name}\n"
                    f"Preflight counts: {counts}\n\n"
                    + self._text(
                        "backend.postgresql.migration.confirm",
                        "Transfer the validated package into the empty PostgreSQL target "
                        "without overwriting existing data?",
                    )
                ),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            target_backend.interchange.import_package(package_path, require_empty=True)
            self._admin_audit(
                "database_interchange_import",
                self.actor_login,
                {
                    "source": str(source_path),
                    "target": target_name,
                    "counts": preview.counts,
                },
            )
            QMessageBox.information(
                self,
                self._text("backend.postgresql.migration.success.title", "Transfer complete"),
                self._text(
                    "backend.postgresql.migration.success",
                    "The canonical dataset and managed files were imported successfully.",
                ),
            )
        except Exception as exc:
            QMessageBox.warning(
                self,
                self._text("backend.postgresql.migration.failed.title", "Transfer failed"),
                str(exc),
            )
        finally:
            if temporary_target and target_backend is not None:
                target_backend.close()
            if temporary_source and source_backend is not None:
                source_backend.close()
            package_path.unlink(missing_ok=True)

    def _load(self) -> None:
        document = self.service.load_document()
        profile = str(
            document.get("profile") or BackendProfile.STANDALONE_SQLITE.value
        )
        self.postgres_radio.setChecked(
            profile == BackendProfile.SHARED_POSTGRESQL.value
        )
        self.sqlite_radio.setChecked(not self.postgres_radio.isChecked())
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
        self.pg_database.setText(pg.database)
        self.pg_user.setText(pg.user)
        self.pg_password.setPlaceholderText(
            self._text(
                "backend.postgresql.password.saved",
                "Stored securely; leave blank to keep",
            )
            if self.service.read_password()
            else ""
        )
        self.pg_sslmode.setCurrentText(pg.sslmode)
        self.pg_server_name.setText(pg.server_name)
        self.pg_ca_file.setText(pg.ca_file)
        self.pg_client_cert_file.setText(pg.client_cert_file)
        self.pg_client_key_file.setText(pg.client_key_file)
        self.pg_client_key_passphrase.clear()
        self.pg_timeout.setValue(pg.connect_timeout)
        self.pg_managed_root.setText(pg.managed_root)
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
        QTimer.singleShot(0, self._resize_for_profile)

    def postgresql_settings(self) -> PostgreSQLSettings:
        return PostgreSQLSettings(
            host=self.pg_host.text().strip(),
            port=self.pg_port.value(),
            database=self.pg_database.text().strip(),
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
        try:
            self.service.test_connection(
                self.postgresql_settings(),
                password=self._password(),
                client_key_passphrase=self.pg_client_key_passphrase.text() or self.service.read_client_key_passphrase(),
                authorized=self.authorized,
            )
        except (BackendConfigurationPermissionError, BackendConfigurationValidationError) as exc:
            QMessageBox.warning(
                self,
                self._text("backend.test.failed.title", "Connection failed"),
                str(exc),
            )
            return
        QMessageBox.information(
            self,
            self._text("backend.test.success.title", "Connection successful"),
            self._text(
                "backend.test.success",
                "The PostgreSQL connection succeeded. No profile was changed.",
            ),
        )

    def _save(self) -> None:
        sqlite_target: Path | None = None
        sqlite_is_new = False
        try:
            pg = self.postgresql_settings()
            profile = (
                BackendProfile.SHARED_POSTGRESQL
                if self.postgres_radio.isChecked()
                else BackendProfile.STANDALONE_SQLITE
            )
            if profile is BackendProfile.SHARED_POSTGRESQL:
                self.service.test_connection(
                    pg,
                    password=self._password(),
                    authorized=self.authorized,
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
        except (BackendConfigurationPermissionError, BackendConfigurationValidationError) as exc:
            QMessageBox.warning(
                self,
                self._text("backend.save.failed.title", "Cannot save backend"),
                str(exc),
            )
            return
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
