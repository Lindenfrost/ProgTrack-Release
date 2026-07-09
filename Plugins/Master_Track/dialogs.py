# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track PyQt6 dialogs.

from __future__ import annotations

import difflib
import html
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QDate, Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .auth import UserDB
from .permissions import (
    ALL_PERMISSIONS,
    DEFAULT_JOB_BUNDLES,
    JOB_BUNDLES,
    PERM_CORE_EDIT_ANIMAL_IDENTITY,
    PERM_CORE_EDIT_ANIMAL_HOUSING,
    PERM_CORE_EDIT_ANIMAL_MEASUREMENTS,
    PERM_CORE_EDIT_ANIMAL_RESEARCH_DATA,
    PERM_CAGE_EXPORT_PDF,
    PERM_PROJECT_CREATE,
    PERM_SAMPLE_TRACK_USE,
    PERM_FLOW_TRACK_USE,
    get_permission_label,
    get_permission_namespace,
    resolve_effective_permissions,
    ROLE_GUEST,
    ROLE_LORD,
    ROLE_MASTER,
    ROLE_ANIMAL_WELFARE,
    ROLE_USER,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIN_PW_LEN = 6

# Human-readable display names for each permission namespace (used in group headers)
_NAMESPACE_LABELS: Dict[str, str] = {
    "core": "Core Functions",
    "master": "Master Track",
    "network": "Network Track",
    "heritage": "Heritage Track",
    "medi_track": "Medi Track",
    "cage": "Cage Track",
    "project": "Project Track",
    "reports": "Animal Reports",
    "research": "Research & Analysis",
    "sample_track": "Sample Track",
    "flow_track": "Flow Track",
}

_AUDIT_LINE_RE = re.compile(
    r"^\[(?P<timestamp>[^\]]+)\]\s+\[(?P<username>[^\]]*)\]\s+\[(?P<action>[^\]]*)\]\s+\[(?P<target>[^\]]*)\]\s*(?P<details>.*)$"
)
_AUDIT_DETAIL_RE = re.compile(
    r"(?:^|;\s*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*?)(?=;\s*[A-Za-z_][A-Za-z0-9_]*=|$)"
)


def _msg(messages: Dict[str, Any], key: str, fallback: str) -> str:
    return messages.get(key, fallback)


def _strength(pw: str) -> int:
    """Return 0-100 password strength score."""
    score = 0
    if len(pw) >= _MIN_PW_LEN:
        score += 30
    if len(pw) >= 10:
        score += 10
    if any(c.isupper() for c in pw):
        score += 15
    if any(c.islower() for c in pw):
        score += 15
    if any(c.isdigit() for c in pw):
        score += 15
    if any(not c.isalnum() for c in pw):
        score += 15
    return min(100, score)


# ===================================================================
# Create Lord Dialog (first-start wizard)
# ===================================================================

class CreateLordDialog(QDialog):
    """Modal dialog to create the initial Lord account."""

    def __init__(self, parent: Optional[QWidget], messages: Dict[str, Any],
                 user_db: UserDB):
        super().__init__(parent)
        self.messages = messages
        self.user_db = user_db
        self.created_user: Optional[str] = None

        self.setWindowTitle(_msg(messages, "master_track.create_lord.title",
                                 "Create Lord Account"))
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        info = QLabel(_msg(messages, "master_track.create_lord.info",
                           "No accounts exist yet. Create the administrator (Lord) account."))
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText(_msg(messages, "master_track.placeholder.username", "Username"))
        form.addRow(_msg(messages, "master_track.label.username", "Username:"), self.username_edit)

        self.display_edit = QLineEdit()
        self.display_edit.setPlaceholderText(_msg(messages, "master_track.placeholder.display_name", "Display Name (optional)"))
        form.addRow(_msg(messages, "master_track.label.display_name", "Display Name:"), self.display_edit)

        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_edit.textChanged.connect(self._update_strength)
        form.addRow(_msg(messages, "master_track.label.password", "Password:"), self.pw_edit)

        self.pw_confirm = QLineEdit()
        self.pw_confirm.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_msg(messages, "master_track.label.confirm", "Confirm:"), self.pw_confirm)
        layout.addLayout(form)

        self.strength_bar = QProgressBar()
        self.strength_bar.setRange(0, 100)
        self.strength_bar.setTextVisible(False)
        self.strength_bar.setFixedHeight(8)
        layout.addWidget(self.strength_bar)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_strength(self, text: str) -> None:
        val = _strength(text)
        self.strength_bar.setValue(val)
        if val < 40:
            self.strength_bar.setStyleSheet("QProgressBar::chunk { background: #e53935; }")
        elif val < 70:
            self.strength_bar.setStyleSheet("QProgressBar::chunk { background: #ffa726; }")
        else:
            self.strength_bar.setStyleSheet("QProgressBar::chunk { background: #66bb6a; }")

    def _accept(self) -> None:
        username = self.username_edit.text().strip()
        password = self.pw_edit.text()
        confirm = self.pw_confirm.text()

        if not username:
            self.error_label.setText(_msg(self.messages, "master_track.error.empty_username",
                                          "Username cannot be empty."))
            return
        if len(password) < _MIN_PW_LEN:
            self.error_label.setText(
                _msg(self.messages, "master_track.error.pw_too_short",
                     "Password must be at least {n} characters.").replace("{n}", str(_MIN_PW_LEN)))
            return
        if password != confirm:
            self.error_label.setText(_msg(self.messages, "master_track.error.pw_mismatch",
                                          "Passwords do not match."))
            return

        self.user_db.add_user(username, password, role="lord",
                              display_name=self.display_edit.text().strip())
        self.user_db.record_login(username)
        self.created_user = username
        self.accept()


# ===================================================================
# Login Dialog
# ===================================================================

class LoginDialog(QDialog):
    """Username / password login dialog."""

    MAX_ATTEMPTS = 5
    LOCKOUT_SECONDS = 60

    def __init__(self, parent: Optional[QWidget], messages: Dict[str, Any],
                 user_db: UserDB):
        super().__init__(parent)
        self.messages = messages
        self.user_db = user_db
        self.logged_in_user: Optional[str] = None
        self._attempts = 0
        self._locked_until: float = 0

        self.setWindowTitle(_msg(messages, "master_track.login.title", "Login"))
        self.setModal(True)
        self.setMinimumWidth(340)

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.username_edit = QLineEdit()
        form.addRow(_msg(messages, "master_track.label.username", "Username:"), self.username_edit)
        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_msg(messages, "master_track.label.password", "Password:"), self.pw_edit)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        self.login_btn = QPushButton(_msg(messages, "master_track.login.button", "Login"))
        self.login_btn.setDefault(True)
        self.login_btn.clicked.connect(self._try_login)
        btn_row.addWidget(self.login_btn)

        guest_btn = QPushButton(_msg(messages, "master_track.login.guest", "Continue as Guest"))
        guest_btn.clicked.connect(self.reject)
        btn_row.addWidget(guest_btn)
        layout.addLayout(btn_row)

        self.pw_edit.returnPressed.connect(self._try_login)
        self.username_edit.returnPressed.connect(lambda: self.pw_edit.setFocus())

    def _try_login(self) -> None:
        now = time.monotonic()
        if now < self._locked_until:
            remaining = int(self._locked_until - now)
            self.error_label.setText(
                _msg(self.messages, "master_track.error.locked",
                     "Too many attempts. Try again in {s}s.").replace("{s}", str(remaining)))
            return

        username = self.username_edit.text().strip()
        password = self.pw_edit.text()
        user = self.user_db.authenticate(username, password)
        if user is None:
            self._attempts += 1
            if self._attempts >= self.MAX_ATTEMPTS:
                self._locked_until = now + self.LOCKOUT_SECONDS
                self._attempts = 0
            self.error_label.setText(
                _msg(self.messages, "master_track.error.bad_credentials",
                     "Invalid username or password."))
            return

        self.user_db.record_login(username)
        self.logged_in_user = username
        self.accept()


# ===================================================================
# Change Password Dialog
# ===================================================================

class ChangePasswordDialog(QDialog):
    """Allows any logged-in user to change their own password."""

    def __init__(self, parent: Optional[QWidget], messages: Dict[str, Any],
                 user_db: UserDB, username: str, forced: bool = False):
        super().__init__(parent)
        self.messages = messages
        self.user_db = user_db
        self.username = username

        self.setWindowTitle(_msg(messages, "master_track.change_pw.title", "Change Password"))
        self.setModal(True)
        self.setMinimumWidth(360)
        if forced:
            self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)

        if forced:
            layout.addWidget(QLabel(
                _msg(messages, "master_track.change_pw.forced",
                     "You must change your password before continuing.")))

        form = QFormLayout()
        self.old_pw = QLineEdit()
        self.old_pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_msg(messages, "master_track.change_pw.old", "Current Password:"), self.old_pw)

        self.new_pw = QLineEdit()
        self.new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.new_pw.textChanged.connect(self._update_strength)
        form.addRow(_msg(messages, "master_track.change_pw.new", "New Password:"), self.new_pw)

        self.confirm_pw = QLineEdit()
        self.confirm_pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_msg(messages, "master_track.label.confirm", "Confirm:"), self.confirm_pw)
        layout.addLayout(form)

        self.strength_bar = QProgressBar()
        self.strength_bar.setRange(0, 100)
        self.strength_bar.setTextVisible(False)
        self.strength_bar.setFixedHeight(8)
        layout.addWidget(self.strength_bar)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        flags = QDialogButtonBox.StandardButton.Ok
        if not forced:
            flags |= QDialogButtonBox.StandardButton.Cancel
        buttons = QDialogButtonBox(flags)
        buttons.accepted.connect(self._accept)
        if not forced:
            buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_strength(self, text: str) -> None:
        val = _strength(text)
        self.strength_bar.setValue(val)
        if val < 40:
            self.strength_bar.setStyleSheet("QProgressBar::chunk { background: #e53935; }")
        elif val < 70:
            self.strength_bar.setStyleSheet("QProgressBar::chunk { background: #ffa726; }")
        else:
            self.strength_bar.setStyleSheet("QProgressBar::chunk { background: #66bb6a; }")

    def _accept(self) -> None:
        old = self.old_pw.text()
        new = self.new_pw.text()
        confirm = self.confirm_pw.text()

        if not self.user_db.authenticate(self.username, old):
            self.error_label.setText(
                _msg(self.messages, "master_track.error.wrong_old_pw",
                     "Current password is incorrect."))
            return
        if len(new) < _MIN_PW_LEN:
            self.error_label.setText(
                _msg(self.messages, "master_track.error.pw_too_short",
                     "Password must be at least {n} characters.").replace("{n}", str(_MIN_PW_LEN)))
            return
        if new != confirm:
            self.error_label.setText(
                _msg(self.messages, "master_track.error.pw_mismatch",
                     "Passwords do not match."))
            return

        self.user_db.set_password(self.username, new, must_change=False)
        self.accept()


# ===================================================================
# Manage Users Dialog (Lord only)
# ===================================================================

def _jobs_label(user: Dict[str, Any]) -> str:
    """Build the formatted jobs display string for a user row."""
    jobs = user.get("jobs", [])
    if not jobs:
        return "—"
    perms = user.get("permissions", {})
    has_granted = bool(perms.get("granted"))
    has_revoked = bool(perms.get("revoked"))
    suffix = ("+" if has_granted else "") + ("-" if has_revoked else "")
    return "/".join(f"[{j}]{suffix}" for j in jobs)


class ManageUsersDialog(QDialog):
    """Full user management table for Lord / Master accounts."""

    def __init__(self, parent: Optional[QWidget], messages: Dict[str, Any],
                 user_db: UserDB, current_username: str,
                 can_create_users: bool = True, lang: str = "en"):
        super().__init__(parent)
        self.messages = messages
        self.lang = lang
        self.user_db = user_db
        self.current_username = current_username

        self.setWindowTitle(_msg(messages, "master_track.manage.title", "Manage Users"))
        self.setModal(True)
        self.resize(820, 430)

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            _msg(messages, "master_track.manage.col_username",   "Username"),
            _msg(messages, "master_track.manage.col_display",    "Display Name"),
            _msg(messages, "master_track.manage.col_role",       "Role"),
            _msg(messages, "master_track.manage.col_jobs",       "Jobs"),
            _msg(messages, "master_track.manage.col_effective",  "Effective"),
            _msg(messages, "master_track.manage.col_last_login", "Last Login"),
            _msg(messages, "master_track.manage.col_profile",    "Profile"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(self._edit_user)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(_msg(messages, "master_track.manage.add", "Create User"))
        add_btn.clicked.connect(self._add_user)
        add_btn.setVisible(can_create_users)
        btn_row.addWidget(add_btn)

        edit_btn = QPushButton(_msg(messages, "master_track.manage.edit_user", "Edit User…"))
        edit_btn.clicked.connect(self._edit_user)
        btn_row.addWidget(edit_btn)

        reset_btn = QPushButton(_msg(messages, "master_track.manage.reset_pw", "Reset Password"))
        reset_btn.clicked.connect(self._reset_password)
        btn_row.addWidget(reset_btn)

        del_btn = QPushButton(_msg(messages, "master_track.manage.delete", "Delete User"))
        del_btn.clicked.connect(self._delete_user)
        btn_row.addWidget(del_btn)

        btn_row.addStretch()
        close_btn = QPushButton(_msg(messages, "master_track.manage.close", "Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self) -> None:
        users = self.user_db.users
        self.table.setRowCount(len(users))
        no_edit = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        for row, u in enumerate(self.user_db.users):
            u = self.user_db._ensure_user_defaults(dict(u))
            jobs_str = _jobs_label(u)
            perms = u.get("permissions", {})
            effective_label = u["role"]
            if u["role"] not in (ROLE_LORD, ROLE_GUEST) and u.get("jobs"):
                has_override = bool(perms.get("granted") or perms.get("revoked"))
                effective_label = jobs_str if not has_override else jobs_str
            _profile_parts = []
            for _pf, _pk in [(u.get("unit", ""), "unit"),
                              (u.get("profession", ""), "profession"),
                              (u.get("email", ""), "email")]:
                if _pf:
                    _profile_parts.append(_pf)
            _profile_str = " | ".join(_profile_parts) if _profile_parts else "\u2014"
            for col, val in enumerate([
                u["username"],
                u.get("display_name", ""),
                u["role"],
                jobs_str,
                effective_label,
                u.get("last_login", "\u2014") or "\u2014",
                _profile_str,
            ]):
                item = QTableWidgetItem(str(val) if val else "—")
                item.setFlags(no_edit)
                if u["role"] in (ROLE_LORD, ROLE_MASTER):
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(row, col, item)

    def _selected_username(self) -> Optional[str]:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self.table.item(rows[0].row(), 0)
        return item.text() if item else None

    def _add_user(self) -> None:
        dlg = _CreateUserSubDialog(self, self.messages, self.user_db)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _edit_user(self) -> None:
        uname = self._selected_username()
        if not uname:
            return
        dlg = _EditUserDialog(self, self.messages, self.user_db, uname, self.lang)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _reset_password(self) -> None:
        uname = self._selected_username()
        if not uname:
            return
        dlg = _ResetPasswordSubDialog(self, self.messages, self.user_db, uname)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._refresh()

    def _delete_user(self) -> None:
        uname = self._selected_username()
        if not uname:
            return
        if uname.lower() == self.current_username.lower():
            QMessageBox.warning(
                self,
                _msg(self.messages, "master_track.error.title", "Error"),
                _msg(self.messages, "master_track.error.delete_self",
                     "You cannot delete your own account."))
            return
        user = self.user_db.get_user(uname)
        if user and user["role"] == ROLE_LORD and self.user_db.lord_count() <= 1:
            QMessageBox.warning(
                self,
                _msg(self.messages, "master_track.error.title", "Error"),
                _msg(self.messages, "master_track.error.last_lord",
                     "Cannot delete the last Lord account."))
            return
        reply = QMessageBox.question(
            self,
            _msg(self.messages, "master_track.manage.confirm_delete_title", "Confirm"),
            _msg(self.messages, "master_track.manage.confirm_delete",
                 "Delete user '{user}'?").replace("{user}", uname),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            ok = self.user_db.delete_user(uname)
            if not ok:
                QMessageBox.warning(
                    self,
                    _msg(self.messages, "master_track.error.title", "Error"),
                    _msg(self.messages, "master_track.error.last_lord",
                         "Cannot delete the last Lord account."))
            self._refresh()


# ===================================================================
# Collapsible Section helper (mirrors Projects Track style)
# ===================================================================

class _CollapsibleSection(QWidget):
    """Collapsible section widget with toggle button and content area."""
    def __init__(self, title, collapsed=False, parent=None):
        super().__init__(parent)
        self._title = title
        self._btn = QPushButton()
        self._btn.setCheckable(True)
        self._btn.setChecked(not collapsed)
        self._btn.setFlat(True)
        self._btn.setStyleSheet("QPushButton{text-align:left;font-weight:bold;border:none;padding:4px;}")
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(12, 4, 4, 4)
        self._content.setVisible(not collapsed)
        self._btn.toggled.connect(self._content.setVisible)
        self._btn.toggled.connect(lambda on: self._set_title(title, on))
        self._set_title(title, not collapsed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._btn)
        outer.addWidget(self._content)

    def _set_title(self, title, expanded):
        self._btn.setText(("▼  " if expanded else "▶  ") + title)

    def content_layout(self):
        return self._content_lay

    def is_expanded(self):
        return self._btn.isChecked()

    def set_expanded(self, expanded):
        self._btn.setChecked(expanded)


# ===================================================================
# Edit User Dialog (job checkboxes + permission area)
# ===================================================================

def _build_permission_scroll(parent: QWidget, messages: Dict[str, Any],
                              base_perms: set, explicit_granted: set,
                              explicit_revoked: set, lang: str = "en") -> tuple:
    """Build a scrollable permission checkbox area grouped by namespace.

    Visual encoding:
    * Grey label  = permission comes from role/job baseline; no manual override.
    * Normal label (checked)   = explicitly granted beyond baseline.
    * Normal label (unchecked) = explicitly revoked from baseline, or absent with
                                 no override applied.

    Returns (scroll_widget, checkbox_dict) where checkbox_dict maps
    permission_name → QCheckBox.
    """
    scroll = QScrollArea(parent)
    scroll.setWidgetResizable(True)
    scroll.setFixedHeight(280)

    container = QWidget()
    vbox = QVBoxLayout(container)
    vbox.setSpacing(4)

    _EXCLUDED = set()
    checkboxes: Dict[str, QCheckBox] = {}
    current_ns = None
    current_group_layout: Optional[QVBoxLayout] = None

    for perm in ALL_PERMISSIONS:
        if perm in _EXCLUDED:
            continue
        ns = get_permission_namespace(perm)
        if ns != current_ns:
            current_ns = ns
            group = QGroupBox(_NAMESPACE_LABELS.get(ns, ns.replace("_", " ").title()))
            current_group_layout = QVBoxLayout(group)
            current_group_layout.setSpacing(2)
            vbox.addWidget(group)

        label = get_permission_label(perm, lang)
        cb = QCheckBox(label)

        in_base = perm in base_perms
        is_revoked = perm in explicit_revoked
        is_granted = perm in explicit_granted
        checked = (in_base and not is_revoked) or is_granted
        cb.setChecked(checked)
        cb.setProperty("perm_in_base", in_base)

        def _make_style_handler(checkbox):
            def _update(_state=None):
                in_b = checkbox.property("perm_in_base") or False
                if bool(checkbox.isChecked()) == bool(in_b):
                    checkbox.setStyleSheet("color: #888888;")
                else:
                    checkbox.setStyleSheet("")
            return _update

        _handler = _make_style_handler(cb)
        _handler()  # apply initial style
        cb.stateChanged.connect(lambda _state, _h=_handler: _h())

        checkboxes[perm] = cb
        if current_group_layout is not None:
            current_group_layout.addWidget(cb)

    vbox.addStretch()
    scroll.setWidget(container)
    return scroll, checkboxes


class _EditUserDialog(QDialog):
    """Edit role, jobs, and direct permission overrides for one user."""

    def __init__(self, parent: QWidget, messages: Dict[str, Any],
                 user_db: UserDB, username: str, lang: str = "en"):
        super().__init__(parent)
        self.messages = messages
        self.lang = lang
        self.user_db = user_db
        self.username = username

        user = user_db.get_user(username)
        if not user:
            self.reject()
            return
        self._user = user

        self.setWindowTitle(
            _msg(messages, "master_track.manage.edit_user", "Edit User") + f" — {username}")
        self.setModal(True)
        self.resize(560, 640)

        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)

        info_label = QLabel(f"<b>{username}</b>  ({user.get('display_name', '')})")
        layout.addWidget(info_label)

        # --- Profile fields (Collapsible) ---
        prof_section = _CollapsibleSection(
            _msg(messages, "master_track.label.profile", "Profile"),
            collapsed=False
        )
        prof_form = QFormLayout()
        prof_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        prof_form.setContentsMargins(4, 4, 4, 4)
        prof_form.setHorizontalSpacing(8)
        prof_form.setVerticalSpacing(4)
        self._p_display    = QLineEdit(user.get("display_name", ""))
        self._p_pronouns   = QLineEdit(user.get("pronouns",    ""))
        self._p_email      = QLineEdit(user.get("email",       ""))
        self._p_phone      = QLineEdit(user.get("phone",       ""))
        self._p_mobile     = QLineEdit(user.get("mobile",      ""))
        self._p_unit       = QLineEdit(user.get("unit",        ""))
        self._p_profession = QLineEdit(user.get("profession",  ""))
        prof_form.addRow(_msg(messages, "master_track.label.display_name", "Display Name:"), self._p_display)
        prof_form.addRow(_msg(messages, "master_track.label.pronouns",     "Pronouns:"),     self._p_pronouns)
        prof_form.addRow(_msg(messages, "master_track.label.email",        "E-Mail:"),       self._p_email)
        prof_form.addRow(_msg(messages, "master_track.label.phone",        "Phone:"),        self._p_phone)
        prof_form.addRow(_msg(messages, "master_track.label.mobile",       "Mobile:"),       self._p_mobile)
        prof_form.addRow(_msg(messages, "master_track.label.unit",         "Unit:"),         self._p_unit)
        prof_form.addRow(_msg(messages, "master_track.label.profession",   "Profession:"),   self._p_profession)
        prof_form_w = QWidget()
        prof_form_w.setLayout(prof_form)
        prof_section.content_layout().addWidget(prof_form_w)
        layout.addWidget(prof_section)

        is_lord_or_master = user["role"] in (ROLE_LORD, ROLE_MASTER)

        # --- Primary role ---
        role_group = QGroupBox(_msg(messages, "master_track.label.role", "Primary Role"))
        role_layout = QHBoxLayout(role_group)
        self._role_lord_rb   = QRadioButton(_msg(messages, "master_track.role.lord",   "Lord"))
        self._role_master_rb = QRadioButton(_msg(messages, "master_track.role.master", "Master"))
        self._role_welfare_rb = QRadioButton(_msg(
            messages, "master_track.role.animal_welfare_officer", "Animal Welfare Officer"))
        self._role_user_rb   = QRadioButton(_msg(messages, "master_track.role.user",   "User"))
        if user["role"] == ROLE_LORD:
            self._role_lord_rb.setChecked(True)
        elif user["role"] == ROLE_MASTER:
            self._role_master_rb.setChecked(True)
        elif user["role"] == ROLE_ANIMAL_WELFARE:
            self._role_welfare_rb.setChecked(True)
        else:
            self._role_user_rb.setChecked(True)
        self._role_lord_rb.toggled.connect(self._on_role_changed)
        self._role_master_rb.toggled.connect(self._on_role_changed)
        self._role_welfare_rb.toggled.connect(self._on_role_changed)
        self._role_user_rb.toggled.connect(self._on_role_changed)
        role_layout.addWidget(self._role_lord_rb)
        role_layout.addWidget(self._role_master_rb)
        role_layout.addWidget(self._role_welfare_rb)
        role_layout.addWidget(self._role_user_rb)
        role_layout.addStretch()
        layout.addWidget(role_group)

        # --- Jobs ---
        jobs_group = QGroupBox(_msg(messages, "master_track.label.jobs", "Jobs"))
        jobs_layout = QVBoxLayout(jobs_group)
        self._job_checks: Dict[str, QCheckBox] = {}
        current_jobs = set(user.get("jobs", []))
        for job_name in sorted(JOB_BUNDLES.keys()):
            job_label = _msg(
                messages,
                f"master_track.job.{job_name}",
                job_name.replace("_", " ").title(),
            )
            cb = QCheckBox(job_label)
            cb.setChecked(job_name in current_jobs)
            cb.setEnabled(not is_lord_or_master)
            cb.stateChanged.connect(self._on_job_changed)
            self._job_checks[job_name] = cb
            jobs_layout.addWidget(cb)
        layout.addWidget(jobs_group)

        # --- Direct permission overrides ---
        override_group = _CollapsibleSection(
            _msg(messages, "master_track.label.direct_permissions", "Direct Permission Overrides"),
            collapsed=True,
        )
        override_inner = override_group.content_layout()

        if is_lord_or_master:
            override_inner.addWidget(QLabel(
                _msg(messages, "master_track.info.overrides_unavailable",
                     "Direct overrides are not available for lord accounts.")))
            self._perm_checks: Dict[str, QCheckBox] = {}
            self._perm_scroll: Optional[QScrollArea] = None
        else:
            override_inner.addWidget(QLabel(
                _msg(messages, "master_track.info.overrides_hint",
                     "Grey = controlled by role/job (no manual override). "
                     "Normal = manual override active.")))

            # Compute what the user already has from role+jobs (baseline)
            current_granted = set(user.get("permissions", {}).get("granted", []))
            current_revoked = set(user.get("permissions", {}).get("revoked", []))
            base_perms = resolve_effective_permissions(
                user["role"], user.get("jobs", []), [], [])

            self._scroll, self._perm_checks = _build_permission_scroll(
                override_group, messages,
                base_perms=base_perms,
                explicit_granted=current_granted,
                explicit_revoked=current_revoked,
                lang=self.lang,
            )
            override_inner.addWidget(self._scroll)

        layout.addWidget(override_group)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer_layout.addWidget(buttons)

    def _on_role_changed(self, *_) -> None:
        new_role = self._get_selected_role()
        is_elevated = new_role in (ROLE_LORD, ROLE_MASTER)
        for cb in self._job_checks.values():
            cb.setEnabled(not is_elevated)
        self._refresh_perm_styles()

    def _get_selected_role(self) -> str:
        if self._role_lord_rb.isChecked():
            return ROLE_LORD
        if self._role_master_rb.isChecked():
            return ROLE_MASTER
        if self._role_welfare_rb.isChecked():
            return ROLE_ANIMAL_WELFARE
        return ROLE_USER

    def _on_job_changed(self) -> None:
        """Handle job checkbox changes - update permission checkboxes to reflect new baseline."""
        self._update_permission_checkboxes_for_new_jobs()
        self._refresh_perm_styles()

    def _update_permission_checkboxes_for_new_jobs(self) -> None:
        """Update permission checkbox states when jobs change, preserving manual overrides."""
        if not getattr(self, '_perm_checks', None):
            return
        
        new_role = self._get_selected_role()
        new_jobs = [j for j, cb in self._job_checks.items() if cb.isChecked()]
        new_base = resolve_effective_permissions(new_role, new_jobs, [], [])
        
        # Get current user's explicit grants/revokes to preserve manual overrides
        current_granted = set(self._user.get("permissions", {}).get("granted", []))
        current_revoked = set(self._user.get("permissions", {}).get("revoked", []))
        
        for perm, cb in self._perm_checks.items():
            in_base = perm in new_base
            is_granted = perm in current_granted
            is_revoked = perm in current_revoked
            
            # Set checkbox state: baseline unless manually overridden
            if is_revoked:
                cb.setChecked(False)
            elif is_granted:
                cb.setChecked(True)
            else:
                cb.setChecked(in_base)

    def _refresh_perm_styles(self) -> None:
        """Recompute grey/normal styles for all permission checkboxes based on current role+jobs."""
        if not getattr(self, '_perm_checks', None):
            return
        new_role = self._get_selected_role()
        new_jobs = [j for j, cb in self._job_checks.items() if cb.isChecked()]
        new_base = resolve_effective_permissions(new_role, new_jobs, [], [])
        for perm, cb in self._perm_checks.items():
            in_base = perm in new_base
            cb.setProperty("perm_in_base", in_base)
            # Update visual styling - grey if checkbox matches baseline, normal if manually overridden
            if bool(cb.isChecked()) == bool(in_base):
                cb.setStyleSheet("color: #888888;")
            else:
                cb.setStyleSheet("")

    def _save(self) -> None:
        new_role = self._get_selected_role()
        user = self._user

        if user["role"] == ROLE_LORD and new_role != ROLE_LORD:
            if self.user_db.lord_count() <= 1:
                QMessageBox.warning(
                    self,
                    _msg(self.messages, "master_track.error.title", "Error"),
                    _msg(self.messages, "master_track.error.last_lord",
                         "Cannot demote the last Lord account."))
                return

        self.user_db.set_role(self.username, new_role)

        if new_role not in (ROLE_LORD, ROLE_MASTER):
            new_jobs = [j for j, cb in self._job_checks.items() if cb.isChecked()]
            self.user_db.set_jobs(self.username, new_jobs)

            if self._perm_checks:
                base_perms = resolve_effective_permissions(new_role, new_jobs, [], [])
                granted, revoked = [], []
                for perm, cb in self._perm_checks.items():
                    if not cb.isEnabled():
                        continue
                    if cb.isChecked() and perm not in base_perms:
                        granted.append(perm)
                    elif not cb.isChecked() and perm in base_perms:
                        revoked.append(perm)
                self.user_db.set_direct_permissions(self.username, granted, revoked)
        else:
            self.user_db.set_jobs(self.username, [])
            self.user_db.set_direct_permissions(self.username, [], [])

        # Save profile fields
        self.user_db.update_user_profile(
            self.username,
            display_name=self._p_display.text().strip(),
            pronouns=self._p_pronouns.text().strip(),
            email=self._p_email.text().strip(),
            phone=self._p_phone.text().strip(),
            mobile=self._p_mobile.text().strip(),
            unit=self._p_unit.text().strip(),
            profession=self._p_profession.text().strip(),
        )
        self.accept()



# ===================================================================
# Edit Jobs Dialog (Lord only — edit job bundle definitions)
# ===================================================================

class EditJobsDialog(QDialog):
    """Dialog to view and edit job bundle permission sets."""

    def __init__(self, parent: QWidget, messages: Dict[str, Any], plugin: Any):
        super().__init__(parent)
        self.messages = messages
        self.plugin = plugin

        self.setWindowTitle(_msg(messages, "master_track.manage.edit_jobs", "Edit Jobs"))
        self.setModal(True)
        self.resize(600, 580)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(_msg(messages, "master_track.label.select_job", "Job:")))
        self.job_combo = QComboBox()
        self.job_combo.addItems(sorted(JOB_BUNDLES.keys()))
        self.job_combo.currentTextChanged.connect(self._load_job)
        top.addWidget(self.job_combo, 1)

        add_btn = QPushButton(_msg(messages, "master_track.manage.add_job", "New Job"))
        add_btn.clicked.connect(self._add_job)
        top.addWidget(add_btn)
        layout.addLayout(top)

        timeout_row = QHBoxLayout()
        timeout_row.addWidget(QLabel(_msg(
            messages, "master_track.manage.job_timeout", "Idle timeout (minutes):")))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 1440)
        timeout_row.addWidget(self.timeout_spin)
        timeout_row.addStretch()
        layout.addLayout(timeout_row)

        self._perm_checks: Dict[str, QCheckBox] = {}
        self._scroll_container = QWidget()
        self._scroll_vbox = QVBoxLayout(self._scroll_container)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._scroll_container)
        layout.addWidget(scroll, 1)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        save_btn = QPushButton(_msg(messages, "master_track.manage.save_job", "Save Job"))
        save_btn.clicked.connect(self._save_job)
        btn_row.addWidget(save_btn)
        btn_row.addStretch()
        close_btn = QPushButton(_msg(messages, "master_track.manage.close", "Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        if self.job_combo.count():
            self._load_job(self.job_combo.currentText())

    def _load_job(self, job_name: str) -> None:
        for i in reversed(range(self._scroll_vbox.count())):
            w = self._scroll_vbox.itemAt(i).widget()
            if w:
                w.setParent(None)  # type: ignore[arg-type]
        self._perm_checks = {}
        job_perms = JOB_BUNDLES.get(job_name, set())
        self.timeout_spin.setValue(self.plugin.get_job_timeout(job_name))
        current_ns = None
        _JOB_EXCLUDED = set()
        for perm in ALL_PERMISSIONS:
            if perm in _JOB_EXCLUDED:
                continue
            ns = get_permission_namespace(perm)
            if ns != current_ns:
                current_ns = ns
                lbl = QLabel(f"<b>{_NAMESPACE_LABELS.get(ns, ns.replace('_', ' ').title())}</b>")
                self._scroll_vbox.addWidget(lbl)
            cb = QCheckBox(get_permission_label(perm, self.plugin.app.lang))
            cb.setChecked(perm in job_perms)
            self._perm_checks[perm] = cb
            self._scroll_vbox.addWidget(cb)
        self._scroll_vbox.addStretch()

    def _save_job(self) -> None:
        job_name = self.job_combo.currentText()
        if not job_name:
            return
        reply = QMessageBox.question(
            self,
            _msg(self.messages, "master_track.manage.confirm_save_job_title", "Confirm"),
            _msg(self.messages, "master_track.manage.confirm_save_job",
                 "Update job bundle '{job}'? This will affect all users with this job.").replace(
                     "{job}", job_name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        new_perms = {p for p, cb in self._perm_checks.items() if cb.isChecked()}
        JOB_BUNDLES[job_name] = new_perms
        self.plugin.set_job_timeout(job_name, self.timeout_spin.value())
        self.plugin.save_job_bundles()
        affected = self.plugin.user_db.reset_permissions_for_job(job_name)
        msg = _msg(self.messages, "master_track.manage.job_saved", "Job saved.")
        if affected:
            msg += f" {affected} user(s) updated."
        self.error_label.setText(msg)

    def _add_job(self) -> None:
        from PyQt6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self,
            _msg(self.messages, "master_track.manage.add_job", "New Job"),
            _msg(self.messages, "master_track.label.job_name", "Job name:"))
        if not ok or not name.strip():
            return
        name = name.strip().lower()
        if name in JOB_BUNDLES:
            self.error_label.setText(
                _msg(self.messages, "master_track.error.job_exists",
                     "A job with this name already exists."))
            return
        JOB_BUNDLES[name] = set()
        self.plugin.save_job_bundles()
        self.job_combo.addItem(name)
        self.job_combo.setCurrentText(name)


# ===================================================================
# Audit Logs Dialog (Lord only)
# ===================================================================

class AuditLogsDialog(QDialog):
    """Read and display Master_Track audit logs in a filterable table."""

    def __init__(
        self,
        parent: Optional[QWidget],
        messages: Dict[str, Any],
        plugin_dir: str,
        audit_dir: Optional[str] = None,
    ):
        super().__init__(parent)
        self.messages = messages
        self.plugin_dir = plugin_dir
        self.audit_dir = audit_dir or plugin_dir
        self._cage_name_map: Dict[str, str] = {}
        self._all_rows: List[Dict[str, Any]] = []

        self.setWindowTitle(_msg(messages, "master_track.logs.title", "Audit Logs"))
        self.setModal(True)
        self.resize(1200, 640)

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel(_msg(messages, "master_track.logs.search", "Search:")))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            _msg(messages, "master_track.logs.search_placeholder", "Search in all columns...")
        )
        self.search_edit.textChanged.connect(self._apply_filters)
        search_row.addWidget(self.search_edit, 1)

        self.refresh_btn = QPushButton(_msg(messages, "master_track.logs.refresh", "Refresh"))
        self.refresh_btn.clicked.connect(self._reload)
        search_row.addWidget(self.refresh_btn)

        self.export_btn = QPushButton(_msg(messages, "master_track.logs.export_pdf", "Export PDF"))
        self.export_btn.clicked.connect(self._open_export_dialog)
        search_row.addWidget(self.export_btn)
        layout.addLayout(search_row)

        filter_row = QHBoxLayout()

        filter_row.addWidget(QLabel(_msg(messages, "master_track.logs.filter_date", "Date:")))
        self.date_filter_edit = QLineEdit()
        self.date_filter_edit.setPlaceholderText(
            _msg(messages, "master_track.logs.filter_date_placeholder", "YYYY-MM-DD")
        )
        self.date_filter_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.date_filter_edit)

        filter_row.addWidget(QLabel(_msg(messages, "master_track.logs.filter_animal", "Location:")))
        self.animal_filter_edit = QLineEdit()
        self.animal_filter_edit.setPlaceholderText(
            _msg(messages, "master_track.logs.filter_animal_placeholder", "Location name")
        )
        self.animal_filter_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.animal_filter_edit)

        filter_row.addWidget(QLabel(_msg(messages, "master_track.logs.filter_user", "User:")))
        self.user_filter_edit = QLineEdit()
        self.user_filter_edit.setPlaceholderText(
            _msg(messages, "master_track.logs.filter_user_placeholder", "Username")
        )
        self.user_filter_edit.textChanged.connect(self._apply_filters)
        filter_row.addWidget(self.user_filter_edit)

        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                _msg(messages, "master_track.logs.col_date", "Date"),
                _msg(messages, "master_track.logs.col_animal", "Location"),
                _msg(messages, "master_track.logs.col_change", "Change Type"),
                _msg(messages, "master_track.logs.col_old", "Old"),
                _msg(messages, "master_track.logs.col_new", "New"),
                _msg(messages, "master_track.logs.col_user", "User"),
            ]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for idx in (0, 1, 2, 5):
            hdr.setSectionResizeMode(idx, QHeaderView.ResizeMode.ResizeToContents)
        for idx in (3, 4):
            hdr.setSectionResizeMode(idx, QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        layout.addWidget(self.table)

        self.summary_label = QLabel("")
        layout.addWidget(self.summary_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(_msg(messages, "master_track.logs.close", "Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._reload()

    def _audit_files(self) -> List[str]:
        paths: List[str] = []
        for folder in dict.fromkeys([self.audit_dir, self.plugin_dir]):
            try:
                names = os.listdir(folder)
            except Exception:
                continue

            for name in names:
                low = name.lower()
                if low == "audit.log" or (low.startswith("audit_") and low.endswith(".log")):
                    full = os.path.join(folder, name)
                    if os.path.isfile(full):
                        paths.append(full)
        return sorted(paths)

    def _parse_details(self, details_text: str) -> Dict[str, str]:
        details: Dict[str, str] = {}
        for match in _AUDIT_DETAIL_RE.finditer(details_text or ""):
            key = match.group("key").strip().lower()
            value = match.group("value").strip()
            details[key] = value
        return details

    @staticmethod
    def _is_nullish_text(value: str) -> bool:
        lowered = (value or "").strip().lower()
        return lowered in {"", "null", "none", "<unknown>", '""', "''"}

    @classmethod
    def _is_empty_value(cls, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return cls._is_nullish_text(value)
        if isinstance(value, (list, dict)) and not value:
            return True
        return False

    @staticmethod
    def _try_parse_json(value_text: str) -> Any:
        raw = (value_text or "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return raw

    @staticmethod
    def _parse_timestamp_to_date(value: str) -> Optional[date]:
        text = (value or "").strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text).date()
        except Exception:
            try:
                return datetime.strptime(text[:10], "%Y-%m-%d").date()
            except Exception:
                return None

    @staticmethod
    def _strip_html_markup(value: str) -> str:
        text = str(value or "")
        if "<" not in text and "&" not in text:
            return text.strip()

        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</li\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "- ", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\s*\n\s*", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _diff_changed_words(previous_text: str, new_text: str) -> tuple[str, str]:
        old = (previous_text or "").strip()
        new = (new_text or "").strip()

        if old == new:
            return old, new
        if not old:
            return "-", new
        if not new:
            return old, "-"

        old_tokens = old.split()
        new_tokens = new.split()
        matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens)

        old_changed: List[str] = []
        new_changed: List[str] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            old_changed.extend(old_tokens[i1:i2])
            new_changed.extend(new_tokens[j1:j2])

        old_text = " ".join(old_changed).strip() or old
        new_text = " ".join(new_changed).strip() or new
        return old_text, new_text

    @staticmethod
    def _report_field_key(parameter: str) -> str:
        raw = (parameter or "").strip()
        if not raw.lower().startswith("report."):
            return ""
        field = raw.split(".", 1)[1]
        field = field.split("[", 1)[0].strip().lower()
        return field

    def _friendly_report_field_label(self, field_key: str) -> str:
        mapping = {
            "daily_data": (
                "master_track.logs.report_field.daily_data",
                "Daily data",
            ),
            "scores": (
                "master_track.logs.report_field.scores", "Scores"),
            "signatures": (
                "master_track.logs.report_field.signatures", "Signatures"),
            "locked": (
                "master_track.logs.report_field.locked", "Lock state"),
        }
        msg_key, fallback = mapping.get(
            field_key,
            ("master_track.logs.change_label.other", "{parameter}"),
        )
        value = _msg(self.messages, msg_key, fallback)
        return value.replace("{parameter}", field_key or "-")

    def _load_cage_name_map(self) -> Dict[str, str]:
        names: Dict[str, str] = {}
        plugins_dir = os.path.dirname(self.plugin_dir)
        cage_path = os.path.join(plugins_dir, "Cage__Track", "cage.json")
        if not os.path.isfile(cage_path):
            return names

        try:
            with open(cage_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return names

        structures = data.get("structures", {}) if isinstance(data, dict) else {}
        buildings = structures.get("buildings", {}) if isinstance(structures, dict) else {}
        rooms = structures.get("rooms", {}) if isinstance(structures, dict) else {}
        cages = structures.get("cages", {}) if isinstance(structures, dict) else {}

        for bid, entry in buildings.items():
            if not isinstance(entry, dict):
                continue
            display = str(entry.get("display_name") or bid).strip()
            names[str(bid)] = display

        for rid, entry in rooms.items():
            if not isinstance(entry, dict):
                continue
            room_name = str(entry.get("display_name") or rid).strip()
            building_id = str(entry.get("parent_building_id") or "").strip()
            building_name = names.get(building_id, building_id)
            names[str(rid)] = f"{building_name}/{room_name}" if building_name else room_name

        for cid, entry in cages.items():
            if not isinstance(entry, dict):
                continue
            cage_name = str(entry.get("display_name") or cid).strip()
            room_id = str(entry.get("parent_room_id") or "").strip()
            room_name = names.get(room_id, room_id)
            names[str(cid)] = f"{room_name}/{cage_name}" if room_name else cage_name

        return names

    def _translate_structure_ids(self, value: Any, key_hint: str = "") -> Any:
        if isinstance(value, dict):
            translated: Dict[str, Any] = {}
            for key, item in value.items():
                key_str = str(key)
                key_lower = key_str.lower()
                out_key = key_str
                if key_lower in {"building_id", "room_id", "cage_id"}:
                    out_key = key_str[:-3]
                translated[out_key] = self._translate_structure_ids(item, key_lower)
            return translated

        if isinstance(value, list):
            return [self._translate_structure_ids(item, key_hint) for item in value]

        if isinstance(value, str):
            text = value.strip()
            if not text:
                return text
            if key_hint in {"building_id", "room_id", "cage_id"}:
                return self._cage_name_map.get(text, text)
            if text.startswith(("bld_", "room_", "cage_")):
                return self._cage_name_map.get(text, text)
            return text

        return value

    def _format_value(self, value: Any) -> str:
        translated = self._translate_structure_ids(value)
        if self._is_empty_value(translated):
            return "-"

        if isinstance(translated, (dict, list)):
            try:
                return json.dumps(translated, ensure_ascii=False, sort_keys=True)
            except Exception:
                return str(translated)

        text = str(translated).strip()
        return "-" if self._is_nullish_text(text) else text

    def _derive_old_new_display(self, parameter: str, previous_raw: str, new_raw: str) -> tuple[str, str]:
        previous_value = self._try_parse_json(previous_raw)
        new_value = self._try_parse_json(new_raw)

        if isinstance(previous_value, list) and isinstance(new_value, list):
            if len(new_value) > len(previous_value) and new_value[: len(previous_value)] == previous_value:
                appended = new_value[len(previous_value):]
                new_payload: Any
                if len(appended) == 1:
                    new_payload = appended[0]
                else:
                    new_payload = appended
                return "-", self._format_value(new_payload)

        report_field = self._report_field_key(parameter)
        if report_field:
            if isinstance(previous_value, str):
                previous_value = self._strip_html_markup(previous_value)
            if isinstance(new_value, str):
                new_value = self._strip_html_markup(new_value)

            if (
                report_field in {"daily_data", "scores", "signatures"}
                and isinstance(previous_value, str)
                and isinstance(new_value, str)
            ):
                old_changed, new_changed = self._diff_changed_words(previous_value, new_value)
                return self._format_scalar(old_changed), self._format_scalar(new_changed)

        return self._format_value(previous_value), self._format_value(new_value)

    def _friendly_parameter_label(self, parameter: str, scope: str = "") -> str:
        raw = (parameter or "").strip()
        if self._is_nullish_text(raw):
            return _msg(self.messages, "master_track.logs.change_label.data", "Data")

        lower = raw.lower()
        if "gewicht" in lower or "weight" in lower:
            return _msg(self.messages, "master_track.logs.change_label.weight", "Weight data")
        if "progester" in lower:
            return _msg(self.messages, "master_track.logs.change_label.progesterone", "Progesterone data")
        if lower == "address":
            return _msg(self.messages, "master_track.logs.change_label.address", "Address data")
        if lower.startswith("report."):
            report_key = self._friendly_report_field_label(self._report_field_key(raw))
            template = _msg(self.messages, "master_track.logs.change_label.report", "Report {field}")
            return template.replace("{field}", report_key)
        if raw == "<record>":
            scope_lower = (scope or "").strip().lower()
            if scope_lower == "animals":
                return _msg(
                    self.messages,
                    "master_track.logs.change_label.animal_record",
                    "Animal record",
                )
            if scope_lower == "archived_animals":
                return _msg(
                    self.messages,
                    "master_track.logs.change_label.archived_animal_record",
                    "Archived animal record",
                )
            return _msg(self.messages, "master_track.logs.change_label.record", "Record")

        template = _msg(self.messages, "master_track.logs.change_label.other", "{parameter}")
        return template.replace("{parameter}", raw)

    def _format_change_type(self, action: str, parameter: str, details: Dict[str, str]) -> str:
        action_text = (action or "").strip()
        action_lower = action_text.lower()
        if action_lower in {"data_create", "data_edit", "data_delete"}:
            verb = {
                "data_create": _msg(self.messages, "master_track.logs.change_verb.create", "Create"),
                "data_edit": _msg(self.messages, "master_track.logs.change_verb.edit", "Edit"),
                "data_delete": _msg(self.messages, "master_track.logs.change_verb.delete", "Delete"),
            }[action_lower]
            what = self._friendly_parameter_label(parameter, details.get("scope", ""))
            pattern = _msg(self.messages, "master_track.logs.change_type.pattern", "{verb}: {what}")
            return pattern.replace("{verb}", verb).replace("{what}", what)

        if action_lower == "archive":
            return _msg(self.messages, "master_track.logs.action.archive", "Archive animal")
        if action_lower == "restore":
            return _msg(self.messages, "master_track.logs.action.restore", "Restore animal")
        if action_lower == "delete":
            return _msg(self.messages, "master_track.logs.action.delete", "Delete animal")

        if not action_text:
            return "-"
        return action_text

    def _format_scalar(self, value: str) -> str:
        text = (value or "").strip()
        return "-" if self._is_nullish_text(text) else text

    def _resolve_animal_display(self, details: Dict[str, str], target: str) -> str:
        animal_value = (details.get("animal") or "").strip()
        if not self._is_nullish_text(animal_value):
            return animal_value

        animals_raw = (details.get("animals") or "").strip()
        animals_parsed = self._try_parse_json(animals_raw)
        if isinstance(animals_parsed, list):
            names = [str(name).strip() for name in animals_parsed if str(name).strip()]
            if names:
                return ", ".join(names)
        elif isinstance(animals_parsed, str):
            animals_raw = animals_parsed.strip()

        if (
            animals_raw
            and animals_raw not in {"[]", "{}"}
            and not self._is_nullish_text(animals_raw)
        ):
            return animals_raw

        target_text = (target or "").strip()
        if target_text:
            target_parsed = self._try_parse_json(target_text)
            if isinstance(target_parsed, list):
                parsed_names = [
                    str(name).strip()
                    for name in target_parsed
                    if str(name).strip() and not self._is_nullish_text(str(name))
                ]
                return ", ".join(parsed_names) if parsed_names else "-"
            if isinstance(target_parsed, str):
                target_text = target_parsed.strip()

        if (
            target_text
            and target_text not in {"[]", "{}"}
            and target_text.lower() not in {"progtrack", "master_track", "<unknown>"}
        ):
            return target_text

        return "-"

    def _parse_line(self, line: str) -> Optional[Dict[str, Any]]:
        match = _AUDIT_LINE_RE.match((line or "").strip())
        if not match:
            return None

        details = self._parse_details(match.group("details") or "")
        timestamp = (match.group("timestamp") or "").strip()
        target = (match.group("target") or "").strip()
        parameter = details.get("parameter", "")
        previous = details.get("previous", details.get("old", ""))
        new_value = details.get("new", "")
        old_display, new_display = self._derive_old_new_display(parameter, previous, new_value)
        action = (match.group("action") or "").strip()
        animal_display = self._resolve_animal_display(details, target)

        return {
            "date": self._format_scalar(timestamp),
            "animal": self._format_scalar(animal_display),
            "change": self._format_change_type(action, parameter, details),
            "old": old_display,
            "new": new_display,
            "user": self._format_scalar((match.group("username") or "").strip()),
            "_date_obj": self._parse_timestamp_to_date(timestamp),
            "_raw_action": action.lower(),
            "_raw_parameter": (parameter or "").strip().lower(),
        }

    def _fill_legacy_action_animals(self, rows: List[Dict[str, Any]]) -> None:
        by_user_and_ts: Dict[tuple[str, str], List[str]] = {}

        for row in rows:
            if row.get("_raw_action") not in {"data_create", "data_delete"}:
                continue
            if row.get("_raw_parameter") != "<record>":
                continue

            animal_name = str(row.get("animal") or "").strip()
            if not animal_name or animal_name == "-":
                continue

            key = (str(row.get("date") or ""), str(row.get("user") or ""))
            bucket = by_user_and_ts.setdefault(key, [])
            if animal_name not in bucket:
                bucket.append(animal_name)

        for row in rows:
            if row.get("_raw_action") not in {"archive", "restore", "delete"}:
                continue

            current_animal = str(row.get("animal") or "").strip()
            if current_animal and current_animal != "-":
                continue

            key = (str(row.get("date") or ""), str(row.get("user") or ""))
            names = by_user_and_ts.get(key, [])
            if names:
                row["animal"] = ", ".join(names)

    def _load_rows(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in self._audit_files():
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        parsed = self._parse_line(line)
                        if parsed is not None:
                            rows.append(parsed)
            except Exception:
                continue

        self._fill_legacy_action_animals(rows)

        rows.sort(
            key=lambda row: (row.get("_date_obj") or date.min, row.get("date", "")),
            reverse=True,
        )
        return rows

    def _reload(self) -> None:
        self._cage_name_map = self._load_cage_name_map()
        rows = self._load_rows()
        self._all_rows = rows
        self.export_btn.setEnabled(bool(rows))

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))

        no_edit = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        for row_idx, row in enumerate(rows):
            values = [
                row.get("date", ""),
                row.get("animal", ""),
                row.get("change", ""),
                row.get("old", ""),
                row.get("new", ""),
                row.get("user", ""),
            ]
            for col_idx, val in enumerate(values):
                text = val if val else "-"
                item = QTableWidgetItem(text)
                item.setFlags(no_edit)
                if val and val != "-":
                    item.setToolTip(val)
                self.table.setItem(row_idx, col_idx, item)

        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.SortOrder.DescendingOrder)
        self._apply_filters()

    def _default_export_range(self) -> tuple[date, date]:
        dated_rows = [
            row.get("_date_obj")
            for row in self._all_rows
            if isinstance(row.get("_date_obj"), date)
        ]
        if not dated_rows:
            today = date.today()
            return today, today

        max_date = max(dated_rows)
        min_date = min(dated_rows)
        default_from = max(min_date, max_date - timedelta(days=30))
        return default_from, max_date

    def _open_export_dialog(self) -> None:
        if not self._all_rows:
            QMessageBox.information(
                self,
                _msg(self.messages, "master_track.logs.title", "Audit Logs"),
                _msg(self.messages, "master_track.logs.export.no_entries", "No log entries found for the selected period."),
            )
            return

        dlg = QDialog(self)
        dlg.setModal(True)
        dlg.setWindowTitle(_msg(self.messages, "master_track.logs.export.title", "Export Audit Logs"))
        dlg.setMinimumWidth(380)

        layout = QVBoxLayout(dlg)
        form = QFormLayout()

        default_from, default_to = self._default_export_range()
        from_edit = QDateEdit()
        from_edit.setCalendarPopup(True)
        from_edit.setDate(QDate(default_from.year, default_from.month, default_from.day))

        to_edit = QDateEdit()
        to_edit.setCalendarPopup(True)
        to_edit.setDate(QDate(default_to.year, default_to.month, default_to.day))

        form.addRow(_msg(self.messages, "master_track.logs.export.from", "From:"), from_edit)
        form.addRow(_msg(self.messages, "master_track.logs.export.to", "To:"), to_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_btn = QPushButton(_msg(self.messages, "master_track.logs.export_pdf", "Export PDF"))
        cancel_btn = QPushButton(_msg(self.messages, "button.cancel", "Cancel"))
        btn_row.addWidget(export_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        def do_export() -> None:
            from_date = from_edit.date().toPyDate()
            to_date = to_edit.date().toPyDate()
            if from_date > to_date:
                QMessageBox.warning(
                    dlg,
                    _msg(self.messages, "master_track.logs.title", "Audit Logs"),
                    _msg(self.messages, "master_track.logs.export.date_order", "Start date must be before end date."),
                )
                return

            export_rows = [
                row
                for row in self._all_rows
                if isinstance(row.get("_date_obj"), date)
                and from_date <= row["_date_obj"] <= to_date
            ]
            if not export_rows:
                QMessageBox.information(
                    dlg,
                    _msg(self.messages, "master_track.logs.title", "Audit Logs"),
                    _msg(self.messages, "master_track.logs.export.no_entries", "No log entries found for the selected period."),
                )
                return

            path, _ = QFileDialog.getSaveFileName(
                dlg,
                _msg(self.messages, "master_track.logs.export.save_title", "Save Audit Log PDF"),
                "",
                "PDF Files (*.pdf)",
            )
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path = f"{path}.pdf"

            try:
                self._export_rows_to_pdf(path, export_rows, from_date, to_date)
            except Exception as exc:
                QMessageBox.critical(
                    dlg,
                    _msg(self.messages, "master_track.logs.title", "Audit Logs"),
                    _msg(self.messages, "master_track.logs.export.failed", "Failed to export audit log PDF: {error}").replace("{error}", str(exc)),
                )
                return

            QMessageBox.information(
                dlg,
                _msg(self.messages, "master_track.logs.title", "Audit Logs"),
                _msg(self.messages, "master_track.logs.export.success", "Audit log PDF exported successfully."),
            )
            dlg.accept()

        export_btn.clicked.connect(do_export)
        cancel_btn.clicked.connect(dlg.reject)
        dlg.exec()

    def _export_rows_to_pdf(
        self,
        output_path: str,
        rows: List[Dict[str, Any]],
        from_date: date,
        to_date: date,
    ) -> None:
        try:
            from reportlab.lib import colors
            from reportlab.lib.enums import TA_CENTER
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
            from reportlab.lib.units import cm
            from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Table, TableStyle
        except Exception as exc:
            raise RuntimeError(str(exc)) from exc

        styles = getSampleStyleSheet()
        normal_style = ParagraphStyle(
            "AuditNormal",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=8,
            leading=9,
        )
        title_style = ParagraphStyle(
            "AuditTitle",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            textColor=colors.HexColor("#2C3E50"),
            alignment=TA_CENTER,
            spaceAfter=2,
        )
        subtitle_style = ParagraphStyle(
            "AuditSubtitle",
            parent=normal_style,
            fontName="Helvetica",
            fontSize=9,
            alignment=TA_CENTER,
        )

        title_text = _msg(self.messages, "master_track.logs.export.report_title", "Audit Log Report")
        subtitle_text = f"{from_date.strftime('%d.%m.%Y')} - {to_date.strftime('%d.%m.%Y')}"
        generated_label = _msg(self.messages, "master_track.logs.export.generated", "Generated")
        entries_label = _msg(self.messages, "master_track.logs.export.entries", "Entries")
        generated_value = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

        def _safe_paragraph_text(value: Any) -> str:
            text = str(value if value is not None else "-").strip()
            if not text:
                text = "-"
            text = html.escape(text)
            text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
            text = text.replace("{", "{{").replace("}", "}}")
            return text

        def _draw_header(canvas, doc) -> None:
            canvas.saveState()
            title = Paragraph(f"<b>{_safe_paragraph_text(title_text)}</b>", title_style)
            subtitle = Paragraph(_safe_paragraph_text(subtitle_text), subtitle_style)
            w, h = title.wrap(doc.width, doc.topMargin)
            title.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h - 0.2 * cm)

            w2, h2 = subtitle.wrap(doc.width, doc.topMargin)
            subtitle.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h - h2 - 0.35 * cm)

            header_data = [
                [
                    Paragraph(f"<b>{_safe_paragraph_text(generated_label)}:</b>", normal_style),
                    Paragraph(_safe_paragraph_text(generated_value), normal_style),
                    Paragraph(f"<b>{_safe_paragraph_text(entries_label)}:</b>", normal_style),
                    Paragraph(_safe_paragraph_text(str(len(rows))), normal_style),
                ]
            ]
            header_table = Table(header_data, colWidths=[3.0 * cm, 6.5 * cm, 2.5 * cm, 3.0 * cm])
            header_table.setStyle(TableStyle([
                ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ECF0F1")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.grey),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]))
            w3, h3 = header_table.wrap(doc.width, doc.topMargin)
            header_table.drawOn(canvas, doc.leftMargin, doc.height + doc.topMargin - h - h2 - h3 - 0.45 * cm)
            canvas.restoreState()

        doc = BaseDocTemplate(
            output_path,
            pagesize=landscape(A4),
            leftMargin=0.6 * cm,
            rightMargin=0.6 * cm,
            topMargin=4.2 * cm,
            bottomMargin=1.0 * cm,
        )
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="audit_content")
        doc.addPageTemplates([PageTemplate(id="audit_page", frames=frame, onPage=_draw_header)])

        table_data: List[List[Any]] = [
            [
                Paragraph(f"<b>{_safe_paragraph_text(_msg(self.messages, 'master_track.logs.col_date', 'Date'))}</b>", normal_style),
                Paragraph(f"<b>{_safe_paragraph_text(_msg(self.messages, 'master_track.logs.col_animal', 'Animal'))}</b>", normal_style),
                Paragraph(f"<b>{_safe_paragraph_text(_msg(self.messages, 'master_track.logs.col_change', 'Change Type'))}</b>", normal_style),
                Paragraph(f"<b>{_safe_paragraph_text(_msg(self.messages, 'master_track.logs.col_old', 'Old'))}</b>", normal_style),
                Paragraph(f"<b>{_safe_paragraph_text(_msg(self.messages, 'master_track.logs.col_new', 'New'))}</b>", normal_style),
                Paragraph(f"<b>{_safe_paragraph_text(_msg(self.messages, 'master_track.logs.col_user', 'User'))}</b>", normal_style),
            ]
        ]

        for row in rows:
            table_data.append(
                [
                    Paragraph(_safe_paragraph_text(row.get("date", "-")), normal_style),
                    Paragraph(_safe_paragraph_text(row.get("animal", "-")), normal_style),
                    Paragraph(_safe_paragraph_text(row.get("change", "-")), normal_style),
                    Paragraph(_safe_paragraph_text(row.get("old", "-")), normal_style),
                    Paragraph(_safe_paragraph_text(row.get("new", "-")), normal_style),
                    Paragraph(_safe_paragraph_text(row.get("user", "-")), normal_style),
                ]
            )

        col_widths = [3.2 * cm, 3.2 * cm, 4.2 * cm, 6.2 * cm, 6.2 * cm, 2.8 * cm]
        table = Table(table_data, colWidths=col_widths, repeatRows=1, splitByRow=1)
        table.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 7),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#3498DB")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("BOX", (0, 0), (-1, -1), 0.75, colors.black),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        doc.build([table])

    def _apply_filters(self) -> None:
        search_text = self.search_edit.text().strip().lower()
        date_text = self.date_filter_edit.text().strip().lower()
        animal_text = self.animal_filter_edit.text().strip().lower()
        user_text = self.user_filter_edit.text().strip().lower()

        total = self.table.rowCount()
        visible = 0

        for row in range(total):
            vals: List[str] = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                text = item.text().strip().lower() if item else ""
                vals.append("" if text == "-" else text)

            row_date = vals[0] if len(vals) > 0 else ""
            row_animal = vals[1] if len(vals) > 1 else ""
            row_user = vals[5] if len(vals) > 5 else ""
            all_text = " ".join(vals)

            matches = True
            if date_text and date_text not in row_date:
                matches = False
            if animal_text and animal_text not in row_animal:
                matches = False
            if user_text and user_text not in row_user:
                matches = False
            if search_text and search_text not in all_text:
                matches = False

            self.table.setRowHidden(row, not matches)
            if matches:
                visible += 1

        summary = _msg(
            self.messages,
            "master_track.logs.summary",
            "Showing {visible} of {total} entries",
        )
        self.summary_label.setText(
            summary.replace("{visible}", str(visible)).replace("{total}", str(total))
        )


# -- small helper sub-dialogs used by ManageUsersDialog -------------------

class _CreateUserSubDialog(QDialog):
    def __init__(self, parent: QWidget, messages: Dict[str, Any], user_db: UserDB):
        super().__init__(parent)
        self.messages = messages
        self.user_db = user_db
        self.setWindowTitle(_msg(messages, "master_track.manage.add", "Create User"))
        self.setModal(True)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)

        # Account fields
        acct_group = QGroupBox(_msg(messages, "master_track.label.account", "Account"))
        form = QFormLayout(acct_group)
        self.uname = QLineEdit()
        form.addRow(_msg(messages, "master_track.label.username",     "Username:"),     self.uname)
        self.dname = QLineEdit()
        form.addRow(_msg(messages, "master_track.label.display_name", "Display Name:"), self.dname)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_msg(messages, "master_track.label.password",     "Password:"),     self.pw)
        self.role_combo = QComboBox()
        self.role_combo.addItems(["user", "lord"])
        form.addRow(_msg(messages, "master_track.manage.col_role",    "Role:"),         self.role_combo)
        layout.addWidget(acct_group)

        # Profile fields
        prof_group = QGroupBox(_msg(messages, "master_track.label.profile", "Profile"))
        prof_form = QFormLayout(prof_group)
        self.f_pronouns   = QLineEdit()
        self.f_email      = QLineEdit()
        self.f_phone      = QLineEdit()
        self.f_mobile     = QLineEdit()
        self.f_unit       = QLineEdit()
        self.f_profession = QLineEdit()
        prof_form.addRow(_msg(messages, "master_track.label.pronouns",   "Pronouns:"),   self.f_pronouns)
        prof_form.addRow(_msg(messages, "master_track.label.email",      "E-Mail:"),     self.f_email)
        prof_form.addRow(_msg(messages, "master_track.label.phone",      "Phone:"),      self.f_phone)
        prof_form.addRow(_msg(messages, "master_track.label.mobile",     "Mobile:"),     self.f_mobile)
        prof_form.addRow(_msg(messages, "master_track.label.unit",       "Unit:"),       self.f_unit)
        prof_form.addRow(_msg(messages, "master_track.label.profession", "Profession:"), self.f_profession)
        layout.addWidget(prof_group)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        uname = self.uname.text().strip()
        pw = self.pw.text()
        if not uname:
            self.error_label.setText(_msg(self.messages, "master_track.error.empty_username",
                                          "Username cannot be empty."))
            return
        if self.user_db.get_user(uname):
            self.error_label.setText(_msg(self.messages, "master_track.error.user_exists",
                                          "A user with this name already exists."))
            return
        if len(pw) < _MIN_PW_LEN:
            self.error_label.setText(
                _msg(self.messages, "master_track.error.pw_too_short",
                     "Password must be at least {n} characters.").replace("{n}", str(_MIN_PW_LEN)))
            return
        role = self.role_combo.currentText()
        self.user_db.add_user(
            uname, pw, role=role,
            display_name=self.dname.text().strip(),
            pronouns=self.f_pronouns.text().strip(),
            email=self.f_email.text().strip(),
            phone=self.f_phone.text().strip(),
            mobile=self.f_mobile.text().strip(),
            unit=self.f_unit.text().strip(),
            profession=self.f_profession.text().strip(),
        )
        self.accept()


class _ResetPasswordSubDialog(QDialog):
    def __init__(self, parent: QWidget, messages: Dict[str, Any],
                 user_db: UserDB, username: str):
        super().__init__(parent)
        self.messages = messages
        self.user_db = user_db
        self.username = username
        self.setWindowTitle(
            _msg(messages, "master_track.manage.reset_pw", "Reset Password"))
        self.setModal(True)
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Reset password for: {username}"))
        form = QFormLayout()
        self.new_pw = QLineEdit()
        self.new_pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_msg(messages, "master_track.change_pw.new", "New Password:"), self.new_pw)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: red;")
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        pw = self.new_pw.text()
        if len(pw) < _MIN_PW_LEN:
            self.error_label.setText(
                _msg(self.messages, "master_track.error.pw_too_short",
                     "Password must be at least {n} characters.").replace("{n}", str(_MIN_PW_LEN)))
            return
        self.user_db.set_password(self.username, pw, must_change=True)
        self.accept()
