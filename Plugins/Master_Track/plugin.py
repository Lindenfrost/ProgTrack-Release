# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.1-log-menu or newer.
# Module: Master Track core plugin logic.

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any, Dict, Optional

from .auth import UserDB
from .permissions import (
    ROLE_GUEST,
    ROLE_LORD,
    ROLE_MASTER,
    ROLE_ANIMAL_WELFARE,
    ROLE_USER,
    JOB_BUNDLES,
    ALL_PERMISSIONS,
    PERM_MASTER_CREATE_USERS,
    ROLE_BASELINES,
    can as _perm_can,
    resolve_effective_permissions,
)
from .session import SessionManager
from Plugins.core.platform_helpers import open_local_path

logger = logging.getLogger(__name__)


class MasterTrackPlugin:
    """Singleton-like plugin object created once and attached to the main app."""

    def __init__(self, app: Any):
        self.app = app
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.user_db = UserDB(self.plugin_dir, app.backend)
        self.session_mgr = SessionManager(self.plugin_dir, app.backend)

        self._current_username: Optional[str] = None
        self._current_role: str = ROLE_GUEST

        # Settings (timeout etc.)
        self._settings = self._load_settings()
        self._timeout_minutes: int = self._settings.get("timeout_minutes", 30)
        self._effective_timeout_minutes: int = self._timeout_minutes

        # Inactivity timeout timer
        self._idle_timer: Optional[Any] = None
        self._warning_timer: Optional[Any] = None
        self._idle_warned = False
        self._idle_warning_dialog: Optional[Any] = None
        self._idle_warning_countdown: Optional[Any] = None

        # Load job bundle overrides from jobs.json (if a Lord has edited them)
        self._load_job_bundles()

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _settings_path(self) -> str:
        return os.path.join(self.plugin_dir, "settings.json")

    def _jobs_path(self) -> str:
        return os.path.join(self.plugin_dir, "jobs.json")

    def _load_settings(self) -> Dict[str, Any]:
        value = self.app.backend.records.get(
            "configuration", "master-track", default={"timeout_minutes": 30}
        )
        return value if isinstance(value, dict) else {"timeout_minutes": 30}

    def _save_settings(self) -> None:
        try:
            self.app.backend.records.put(
                "configuration", "master-track", self._settings
            )
        except Exception as exc:
            logger.error("Failed to save Master_Track settings: %s", exc)

    def _load_job_bundles(self) -> None:
        """Load custom job bundle overrides from the backend."""
        try:
            raw = self.app.backend.records.get(
                "configuration", "job-bundles", default={}
            )
            if isinstance(raw, dict):
                known_permissions = set(ALL_PERMISSIONS)
                for job_name, perms in raw.items():
                    if isinstance(perms, list):
                        unknown = sorted(set(perms) - known_permissions)
                        if unknown:
                            logger.warning(
                                "Ignoring unknown Master Track permissions in job %s: %s",
                                job_name,
                                ", ".join(unknown),
                            )
                        JOB_BUNDLES[job_name] = {p for p in perms if p in known_permissions}
        except Exception as exc:
            logger.error("Failed to load jobs.json: %s", exc)

    def save_job_bundles(self) -> None:
        """Persist current job bundles through the backend."""
        try:
            serializable = {k: sorted(v) for k, v in JOB_BUNDLES.items()}
            self.app.backend.records.put(
                "configuration", "job-bundles", serializable
            )
        except Exception as exc:
            logger.error("Failed to save jobs.json: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def current_username(self) -> Optional[str]:
        return self._current_username

    @property
    def current_display_name(self) -> Optional[str]:
        record = self._current_user_record()
        if record:
            return record.get("display_name") or self._current_username
        return None

    @property
    def current_role(self) -> str:
        return self._current_role

    @property
    def is_logged_in(self) -> bool:
        return self._current_username is not None

    def _current_user_record(self) -> Optional[Dict[str, Any]]:
        if not self._current_username:
            return None
        return self.user_db.get_user(self._current_username)

    def get_primary_role(self) -> str:
        """Return the primary role string of the currently logged-in user."""
        return self._current_role

    def get_assigned_jobs(self):
        """Return the job list for the current user."""
        user = self._current_user_record()
        if not user:
            return []
        return list(user.get("jobs", []))

    def get_job_timeout(self, job_name: str) -> int:
        values = self._settings.get("job_timeouts", {})
        if not isinstance(values, dict):
            return max(1, int(self._timeout_minutes))
        return max(1, int(values.get(job_name, self._timeout_minutes)))

    def set_job_timeout(self, job_name: str, minutes: int) -> None:
        values = self._settings.setdefault("job_timeouts", {})
        values[str(job_name)] = max(1, int(minutes))
        self._save_settings()

    def delete_job_timeout(self, job_name: str) -> None:
        values = self._settings.get("job_timeouts", {})
        if isinstance(values, dict) and str(job_name) in values:
            del values[str(job_name)]
            self._save_settings()

    def _resolve_effective_timeout(self, jobs: Any) -> int:
        configured = [self.get_job_timeout(str(job)) for job in (jobs or [])]
        return max(configured, default=max(1, int(self._timeout_minutes)))

    def get_direct_permission_grants(self):
        """Return directly granted permissions for the current user."""
        user = self._current_user_record()
        if not user:
            return []
        return list(user.get("permissions", {}).get("granted", []))

    def get_direct_permission_revocations(self):
        """Return directly revoked permissions for the current user."""
        user = self._current_user_record()
        if not user:
            return []
        return list(user.get("permissions", {}).get("revoked", []))

    def get_effective_permissions(self):
        """Return the full effective permission set for the current user."""
        user = self._current_user_record()
        if not user:
            return resolve_effective_permissions(ROLE_GUEST, [], [], [])
        return resolve_effective_permissions(
            user["role"],
            user.get("jobs", []),
            user.get("permissions", {}).get("granted", []),
            user.get("permissions", {}).get("revoked", []),
        )

    def has_direct_overrides(self) -> bool:
        """Return True if the current user has any direct permission overrides."""
        user = self._current_user_record()
        if not user or user["role"] in (ROLE_LORD, ROLE_MASTER, ROLE_GUEST):
            return False
        perms = user.get("permissions", {})
        return bool(perms.get("granted") or perms.get("revoked"))

    def get_display_role_label(self) -> str:
        """Return a display label such as 'user', 'vet', or 'vet+'.

        A '+' suffix is appended only when the user has direct permission
        overrides that go beyond what the role baseline + job bundles already
        provide (extra grants or any explicit revocations).
        """
        if self._current_role == ROLE_LORD:
            return ROLE_LORD
        if self._current_role == ROLE_MASTER:
            return ROLE_MASTER
        if self._current_role == ROLE_ANIMAL_WELFARE:
            return "animal welfare officer"
        if self._current_role == ROLE_GUEST:
            return ROLE_GUEST
        user = self._current_user_record()
        if not user:
            return self._current_role
        jobs = user.get("jobs", [])
        perms = user.get("permissions", {})
        granted = set(perms.get("granted", []))
        revoked = set(perms.get("revoked", []))

        # Compute what role+jobs already cover (no direct overrides)
        baseline = set(ROLE_BASELINES.get(self._current_role, set()))
        job_combined: set = set()
        for j in jobs:
            job_combined |= JOB_BUNDLES.get(j, set())
        covered = baseline | job_combined

        # '+' only when there are grants not already covered, or any revocations
        extra_grants = granted - covered
        has_overrides = bool(extra_grants or revoked)
        suffix = "+" if has_overrides else ""

        if not jobs:
            return ROLE_USER + suffix
        return "/".join(f"{j}{suffix}" for j in jobs)

    def can(self, action: str) -> bool:
        """Central permission check used by ProgTrack core and all plugins."""
        if self._current_role == ROLE_LORD:
            return True
        user = self._current_user_record()
        if user:
            return _perm_can(
                user["role"],
                user.get("jobs", []),
                user.get("permissions", {}).get("granted", []),
                user.get("permissions", {}).get("revoked", []),
                action,
            )
        return _perm_can(ROLE_GUEST, [], [], [], action)

    def current_user_is_project_unrestricted(self) -> bool:
        return self.can("project.view_all")

    def get_project_visibility_cache(self) -> Dict[str, Any]:
        cache = self.load_session().get("project_visibility_cache")
        if not isinstance(cache, dict):
            cache = {"dirty": True, "projects": []}
        cache.setdefault("dirty", True)
        cache.setdefault("projects", [])
        return cache

    def set_project_visibility_cache(self, projects: list[str], dirty: bool = False) -> None:
        self.save_session({
            "project_visibility_cache": {
                "dirty": bool(dirty),
                "projects": sorted({str(p) for p in projects if str(p).strip()}),
            }
        })

    def mark_project_visibility_dirty(self, usernames) -> None:
        if isinstance(usernames, str):
            usernames = [usernames]
        for username in usernames or []:
            if not username:
                continue
            user = self.user_db.get_user(str(username))
            if not user:
                logger.warning(
                    "Skipped project visibility invalidation for unknown user %r",
                    username,
                )
                continue
            canonical_username = str(user.get("username") or "").strip()
            if not canonical_username or not self.session_mgr.exists(canonical_username):
                continue
            data = self.session_mgr.load(canonical_username)
            cache = data.get("project_visibility_cache")
            if not isinstance(cache, dict):
                cache = {"projects": []}
            cache["dirty"] = True
            data["project_visibility_cache"] = cache
            self.session_mgr.save(canonical_username, data)

    # ------------------------------------------------------------------
    # Login / Logout
    # ------------------------------------------------------------------

    def startup(self) -> None:
        """Run on app startup: show first-start wizard or login dialog."""
        from .dialogs import CreateLordDialog, LoginDialog

        messages = self.app.messages

        self.user_db.load()

        if not self.user_db.lord_exists():
            dlg = CreateLordDialog(self.app, messages, self.user_db)
            if dlg.exec():
                self._login_as(dlg.created_user)
                self._start_idle_timer()
            else:
                self._set_guest()
        else:
            dlg = LoginDialog(self.app, messages, self.user_db)
            if dlg.exec():
                self._login_as(dlg.logged_in_user)
                self._start_idle_timer()
            else:
                self._set_guest()

    def _login_as(self, username: str) -> None:
        user = self.user_db.get_user(username)
        if not user:
            self._set_guest()
            return
        self._current_username = username
        self._current_role = user["role"]
        self._effective_timeout_minutes = self._resolve_effective_timeout(user.get("jobs", []))
        logger.info("Logged in as %s (role=%s)", username, self._current_role)

        # forced password change
        if user.get("must_change_password"):
            from .dialogs import ChangePasswordDialog
            dlg = ChangePasswordDialog(self.app, self.app.messages,
                                       self.user_db, username, forced=True)
            dlg.exec()

        self.audit("login", username)

    def _set_guest(self) -> None:
        self._current_username = None
        self._current_role = ROLE_GUEST
        logger.info("Running in Guest (read-only) mode")

    def logout(self) -> None:
        """Log out the current user, save session, return to Guest."""
        self._stop_idle_timer()
        if self._current_username:
            self.save_session()
            self.audit("logout", self._current_username)
        self._set_guest()

    def login_interactive(self) -> bool:
        """Show login dialog interactively (e.g. from Ctrl+L). Returns True on success."""
        from .dialogs import LoginDialog

        if self.is_logged_in:
            self.save_session()

        dlg = LoginDialog(self.app, self.app.messages, self.user_db)
        if dlg.exec() and dlg.logged_in_user:
            self._login_as(dlg.logged_in_user)
            self._start_idle_timer()
            return True
        return False

    # ------------------------------------------------------------------
    # Inactivity timeout
    # ------------------------------------------------------------------

    def _start_idle_timer(self) -> None:
        """Start (or restart) the inactivity timeout timer."""
        if self._effective_timeout_minutes <= 0 or not self.is_logged_in:
            return
        from PyQt6.QtCore import QTimer

        self._stop_idle_timer()
        self._idle_warned = False

        timeout_ms = self._effective_timeout_minutes * 60 * 1000
        warning_ms = max(0, timeout_ms - 15 * 1000)

        # Warning timer (fires 15 seconds before logout)
        if warning_ms > 0 and warning_ms < timeout_ms:
            self._warning_timer = QTimer(self.app)
            self._warning_timer.setSingleShot(True)
            self._warning_timer.timeout.connect(self._on_idle_warning)
            self._warning_timer.start(warning_ms)

        # Logout timer
        self._idle_timer = QTimer(self.app)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._on_idle_timeout)
        self._idle_timer.start(timeout_ms)

    def _stop_idle_timer(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.stop()
            self._idle_timer = None
        if self._warning_timer is not None:
            self._warning_timer.stop()
            self._warning_timer = None
        self._idle_warned = False
        if self._idle_warning_countdown is not None:
            self._idle_warning_countdown.stop()
            self._idle_warning_countdown = None
        if self._idle_warning_dialog is not None:
            self._idle_warning_dialog.close()
            self._idle_warning_dialog = None

    def reset_idle_timer(self) -> None:
        """Call on user interaction to reset the inactivity countdown."""
        if self.is_logged_in and self._effective_timeout_minutes > 0:
            self._start_idle_timer()

    def _on_idle_warning(self) -> None:
        """Show a visible 15-second keep-alive dialog before auto-logout."""
        self._idle_warned = True
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

        dlg = QDialog(self.app)
        dlg.setModal(True)
        dlg.setWindowTitle(self.app.messages.get(
            "master_track.timeout.warning_title", "Session expiring"))
        layout = QVBoxLayout(dlg)
        label = QLabel()
        layout.addWidget(label)
        keep = QPushButton(self.app.messages.get(
            "master_track.timeout.keep_logged_in", "Keep me logged in"))
        layout.addWidget(keep)
        remaining = {"seconds": 15}

        def update_label() -> None:
            label.setText(self.app.messages.get(
                "master_track.timeout.warning",
                "Session expires in {seconds} seconds.").replace(
                    "{seconds}", str(remaining["seconds"])))
            remaining["seconds"] -= 1

        def keep_logged_in() -> None:
            dlg.accept()
            self.reset_idle_timer()

        keep.clicked.connect(keep_logged_in)
        countdown = QTimer(dlg)
        countdown.timeout.connect(update_label)
        countdown.start(1000)
        self._idle_warning_dialog = dlg
        self._idle_warning_countdown = countdown
        update_label()
        dlg.show()

    def _on_idle_timeout(self) -> None:
        """Auto-logout after inactivity period."""
        if not self.is_logged_in:
            return
        logger.info("Session timed out for %s", self._current_username)
        self.audit("timeout", self._current_username or "")
        # Trigger logout via the app's method so UI is also updated
        if hasattr(self.app, '_do_master_logout'):
            self.app._do_master_logout()

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def load_session(self) -> Dict[str, Any]:
        """Load the current user's session data (or defaults for Guest)."""
        if not self._current_username:
            return SessionManager._defaults("guest")
        return self.session_mgr.load(self._current_username)

    def save_session(self, extra: Optional[Dict[str, Any]] = None) -> None:
        """Persist the current user's session state."""
        if not self._current_username:
            return
        data = self.session_mgr.load(self._current_username)
        if extra:
            data.update(extra)
        self.session_mgr.save(self._current_username, data)

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def audit(self, action: str, target: str = "", details: str = "") -> None:
        username = self._current_username or "guest"
        ts = datetime.now().isoformat(timespec="seconds")
        month = date.today().strftime("%Y-%m")
        path = os.path.join(self.audit_logs_dir(), f"audit_{month}.log")
        line = f"[{ts}] [{username}] [{action}] [{target}] {details}\n"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as exc:
            logger.error("Audit log write failed: %s", exc)

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def show_manage_users(self) -> None:
        from .dialogs import ManageUsersDialog
        dlg = ManageUsersDialog(self.app, self.app.messages,
                                self.user_db, self._current_username or "",
                                can_create_users=self.can(PERM_MASTER_CREATE_USERS),
                                lang=self.app.lang)
        dlg.exec()

    def show_edit_jobs(self) -> None:
        from .dialogs import EditJobsDialog
        dlg = EditJobsDialog(self.app, self.app.messages, self)
        dlg.exec()

    def show_logs(self) -> None:
        from .dialogs import AuditLogsDialog
        dlg = AuditLogsDialog(self.app, self.app.messages, self.plugin_dir, self.audit_logs_dir())
        dlg.exec()

    def app_root(self) -> str:
        return os.path.abspath(os.path.join(self.plugin_dir, os.pardir, os.pardir))

    def runtime_root(self) -> str:
        app_root = self.app_root()
        runtime_root = os.path.join(app_root, "_internal")
        return runtime_root if os.path.isdir(runtime_root) else app_root

    def audit_logs_dir(self) -> str:
        return os.path.join(self.runtime_root(), "logs", "Master_Track")

    def logs_dir(self) -> str:
        return os.path.join(self.runtime_root(), "logs")

    def _write_log_locations_file(self, logs_dir: str) -> None:
        app_root = os.path.abspath(os.path.join(self.plugin_dir, os.pardir, os.pardir))
        locations_path = os.path.join(logs_dir, "log_locations.txt")
        lines = [
            "ProgTrack log locations",
            "",
            f"Application log: {os.path.join(logs_dir, 'progtrack.log')}",
            f"Launcher error log: {os.path.join(logs_dir, 'launcher_error.log')}",
            f"Launcher fault log: {os.path.join(logs_dir, 'launcher_fault.log')}",
            f"Master Track audit logs: {os.path.join(self.audit_logs_dir(), 'audit_YYYY-MM.log')}",
            "",
            f"Application folder: {app_root}",
        ]
        with open(locations_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def open_logs_folder(self) -> None:
        messages = getattr(self.app, "messages", {}) or {}
        title = messages.get("master_track.logs_folder.title", "Logs folder")
        if not self.can("master.view_audit"):
            return
        logs_dir = self.logs_dir()
        try:
            os.makedirs(logs_dir, exist_ok=True)
            self._write_log_locations_file(logs_dir)
            if not open_local_path(logs_dir):
                raise OSError("No desktop opener is available for the logs folder.")
        except Exception as exc:
            from PyQt6.QtWidgets import QMessageBox
            template = messages.get(
                "master_track.logs_folder.open_failed",
                "Could not open logs folder: {error}",
            )
            QMessageBox.warning(self.app, title, template.replace("{error}", str(exc)))

    def show_change_password(self) -> None:
        if not self._current_username:
            return
        from .dialogs import ChangePasswordDialog
        dlg = ChangePasswordDialog(self.app, self.app.messages,
                                   self.user_db, self._current_username)
        dlg.exec()
