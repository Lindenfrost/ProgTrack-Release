# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Projects Track main tab widget.

from __future__ import annotations
import json, logging, os, shutil, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)
from Plugins.core.animal_identity import animal_base_name
from Plugins.core.animal_roles import (
    ROLE_VALUE_AMME,
    ROLE_VALUE_EXPERIMENTAL,
    ROLE_VALUE_OFFSPRING,
    ROLE_VALUE_PARTNER,
    ROLE_VALUE_SAMENSP,
    ROLE_VALUE_SPENDER,
    ROLE_VALUE_ZUCHTTIER,
    canonical_role_value,
)
from Plugins.core.project_visibility import diff_project_associated_users
from Plugins.core.project_species import remove_mismatched_assignments
from Plugins.core.platform_helpers import open_local_path
from Plugins.core.backend_store import BackendJsonStore
from Plugins.core.ui_icons import apply_icon
logger = logging.getLogger(__name__)
_DOCS_SUBDIR   = "documents"
_SOP_SUBDIR    = "sop"
_ALL_DOC_FILTER = "Documents (*.jpg *.jpeg *.png *.pdf *.txt *.md *.xls *.xlsx *.csv)"

def _clean_text(value) -> str:
    if value is None:
        return ''
    return str(value).strip()

def _history_animal_key(record: dict) -> str:
    if not isinstance(record, dict):
        return ''
    return _clean_text(record.get('ipid') or record.get('name'))

def _m(messages, key, fallback):
    return messages.get(key, fallback) if isinstance(messages, dict) else fallback

def _load_species_list() -> list:
    """Load species from Resources/Species_List.txt each time (dynamic)."""
    txt = Path(__file__).parent.parent / 'Resources' / 'Species_List.txt'
    try:
        return [ln.strip() for ln in txt.read_text(encoding='utf-8').splitlines() if ln.strip()]
    except Exception:
        return []

def _doc_icon(fname: str) -> str:
    ext = Path(fname).suffix.lower()
    return {'.pdf': '\U0001f4c4', '.jpg': '\U0001f5bc\ufe0f', '.jpeg': '\U0001f5bc\ufe0f',
            '.png': '\U0001f5bc\ufe0f', '.xls': '\U0001f4ca', '.xlsx': '\U0001f4ca',
            '.csv': '\U0001f4ca', '.txt': '\U0001f4dd', '.md': '\U0001f4dd'}.get(ext, '\U0001f4ce')

_ROLE_KEY_MAP: dict = {
    ROLE_VALUE_SPENDER: 'role.egg_cell_donor',
    ROLE_VALUE_AMME: 'role.surrogate',
    ROLE_VALUE_SAMENSP: 'role.sperm_donor',
    ROLE_VALUE_OFFSPRING: 'role.offspring',
    ROLE_VALUE_PARTNER: 'role.partner_animal',
    ROLE_VALUE_ZUCHTTIER: 'role.breeding_animal',
    ROLE_VALUE_EXPERIMENTAL: 'role.experimental_animal',
}

def _localize_role(role: str, messages: dict) -> str:
    role_value = canonical_role_value(role)
    if not role_value:
        return messages.get('role.unknown', 'Unknown')
    return messages.get(_ROLE_KEY_MAP.get(role_value, ''), role_value)

def _mk_form() -> QFormLayout:
    f = QFormLayout()
    f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    f.setContentsMargins(4, 4, 4, 4)
    f.setHorizontalSpacing(8)
    f.setVerticalSpacing(4)
    return f

class CollapsibleSection(QWidget):
    def __init__(self, title, collapsed=False, parent=None):
        super().__init__(parent)
        self._title = title
        self._btn = QPushButton()
        self._btn.setCheckable(True); self._btn.setChecked(not collapsed)
        self._btn.setFlat(True)
        self._btn.setStyleSheet("QPushButton{text-align:left;font-weight:bold;border:none;padding:4px;}")
        self._content = QWidget()
        self._content_lay = QVBoxLayout(self._content)
        self._content_lay.setContentsMargins(12, 4, 4, 4)
        self._content.setVisible(not collapsed)
        self._btn.toggled.connect(self._content.setVisible)
        self._btn.toggled.connect(lambda on: self._set_title(title, on))
        self._set_title(title, not collapsed)
        outer = QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        outer.addWidget(self._btn); outer.addWidget(self._content)
    def _set_title(self, title, expanded):
        self._btn.setText(title)
        apply_icon(
            self._btn,
            "toggle.collapse" if expanded else "toggle.expand",
            fallback=title,
        )
    def content_layout(self): return self._content_lay
    def is_expanded(self): return self._btn.isChecked()
    def set_title(self, title):
        self._title = title; self._set_title(title, self._btn.isChecked())

class _UserInfoDialog(QDialog):
    def __init__(self, user_data, messages, can_edit=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_m(messages,"project.user_info.title","User Information"))
        self.setMinimumWidth(300); self._removed = False
        v = QVBoxLayout(self); form = QFormLayout()
        
        # Display Name and Unit fields (as requested)
        for field, label in [("display_name","Name"),("unit","Unit")]:
            val = str(user_data.get(field,"") or "").strip()
            if val: form.addRow(QLabel(f"{label}:"), QLabel(val))
        
        # Additional profile fields from Master_Track user data
        profile_fields = [
            ("pronouns", "Pronouns"),
            ("email", "Email"), 
            ("phone", "Phone"),
            ("mobile", "Mobile"),
            ("profession", "Profession")
        ]
        
        for field, label in profile_fields:
            val = str(user_data.get(field,"") or "").strip()
            if val: form.addRow(QLabel(f"{label}:"), QLabel(val))
        
        v.addLayout(form)
        btns = QHBoxLayout(); btns.addStretch()
        if can_edit:
            rem = QPushButton(_m(messages,"project.user_info.remove","Remove from Protocol"))
            rem.clicked.connect(lambda: self._on_remove(messages)); btns.addWidget(rem)
        ok = QPushButton("OK"); ok.clicked.connect(self.accept); btns.addWidget(ok)
        v.addLayout(btns)
    def _on_remove(self, messages):
        if QMessageBox.question(self,"",_m(messages,"project.user_info.remove_confirm","Remove this user from the protocol?"),
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self._removed = True; self.accept()
    def was_removed(self): return self._removed

class UserSearchField(QWidget):
    def __init__(self, messages, app, can_edit=True, parent=None, role_filter: Optional[str] = None):
        super().__init__(parent)
        self._messages=messages; self._app=app; self._can_edit=can_edit
        self._role_filter = role_filter
        self._login=None; self._user_data={}
        self._combo=QComboBox(); self._combo.setEditable(True)
        self._combo.setEnabled(can_edit)
        self._combo.lineEdit().setPlaceholderText(_m(messages,"project.user_field.placeholder","Search user\u2026"))
        self._name_btn=QPushButton(); self._name_btn.setFlat(True)
        self._name_btn.setEnabled(can_edit)
        self._name_btn.setStyleSheet("QPushButton{color:#0078d4;text-decoration:underline;text-align:left;border:none;padding:0;}")
        self._unit_lbl=QLabel(); self._unit_lbl.setStyleSheet("color:grey;font-size:9pt;")
        v=QVBoxLayout(self); v.setContentsMargins(0,0,0,0); v.setSpacing(2)
        v.addWidget(self._combo); v.addWidget(self._name_btn); v.addWidget(self._unit_lbl)
        self._name_btn.hide(); self._unit_lbl.hide()
        self._populate_combo()
        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        self._name_btn.clicked.connect(self._on_name_clicked)
        # Refresh user list when dropdown opens to show newly created users
        self._combo.view().viewport().installEventFilter(self)
    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        if event.type() == QEvent.Type.Show:
            self._refresh_combo()
        return super().eventFilter(obj, event)
    def _refresh_combo(self):
        """Refresh the combo box while preserving the current selection."""
        current_login = self._login
        self._populate_combo()
        if current_login:
            idx = self._combo.findData(current_login)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
    def _populate_combo(self):
        self._combo.blockSignals(True); self._combo.clear(); self._combo.addItem("",None)
        mt=getattr(self._app,'master_track',None)
        if mt and hasattr(mt,'user_db'):
            for ud in (mt.user_db.users or []):
                if self._role_filter and ud.get('role') != self._role_filter:
                    continue
                login=ud.get('username','')
                if login:
                    self._combo.addItem(str(ud.get('display_name') or login), login)
        self._combo.blockSignals(False)
    def _on_combo_changed(self, _idx):
        login=self._combo.currentData()
        if login:
            self._login=login
            mt=getattr(self._app,'master_track',None)
            ud={}
            if mt and hasattr(mt,'user_db'):
                ud=next((u for u in (mt.user_db.users or []) if u.get('username')==login), {})
            self._user_data=ud
            self._name_btn.setText(str(ud.get('display_name') or login))
            unit=str(ud.get('unit','') or '')
            self._unit_lbl.setText(unit)
            self._combo.hide(); self._name_btn.show(); self._unit_lbl.setVisible(bool(unit))
        else:
            self._login=None; self._user_data={}
    def _on_name_clicked(self):
        dlg=_UserInfoDialog(self._user_data,self._messages,can_edit=self._can_edit,parent=self)
        dlg.exec()
        if dlg.was_removed(): self.set_login(None)
    def get_login(self): return self._login
    def set_login(self, login):
        self._combo.blockSignals(True)
        if login:
            self._login=login
            mt=getattr(self._app,'master_track',None)
            ud={}
            if mt and hasattr(mt,'user_db'):
                ud=next((u for u in (mt.user_db.users or []) if u.get('username')==login), {})
            self._user_data=ud
            idx=self._combo.findData(login)
            if idx>=0: self._combo.setCurrentIndex(idx)
            self._name_btn.setText(str(ud.get('display_name') or login))
            unit=str(ud.get('unit','') or '')
            self._unit_lbl.setText(unit)
            self._combo.hide(); self._name_btn.show(); self._unit_lbl.setVisible(bool(unit))
        else:
            self._login=None; self._user_data={}; self._combo.setCurrentIndex(0)
            self._combo.show(); self._name_btn.hide(); self._unit_lbl.hide()
        self._combo.blockSignals(False)

class DynamicUserList(QWidget):
    def __init__(self, messages, app, can_edit=True, parent=None):
        super().__init__(parent)
        self._messages=messages; self._app=app; self._can_edit=can_edit; self._fields=[]
        self._v=QVBoxLayout(self); self._v.setContentsMargins(0,0,0,0); self._v.setSpacing(4)
        self._add_btn=QPushButton(_m(messages,"project.dynamic_user.add","+ Add user"))
        self._add_btn.setFlat(True); self._add_btn.setEnabled(can_edit)
        self._add_btn.clicked.connect(lambda: self._add_row(None)); self._v.addWidget(self._add_btn)
    def _add_row(self, login):
        row_w=QWidget(); row_h=QHBoxLayout(row_w); row_h.setContentsMargins(0,0,0,0)
        field=UserSearchField(self._messages,self._app,can_edit=self._can_edit)
        rem=QPushButton("\u2212"); rem.setFixedWidth(24); rem.setEnabled(self._can_edit)
        row_h.addWidget(field,1); row_h.addWidget(rem)
        self._fields.append(field)
        self._v.insertWidget(self._v.count()-1, row_w)
        rem.clicked.connect(lambda: self._remove_row(row_w,field))
        if login: field.set_login(login)
        return field
    def _remove_row(self, row_w, field):
        if field in self._fields: self._fields.remove(field)
        idx=self._v.indexOf(row_w)
        if idx>=0:
            item=self._v.takeAt(idx)
            if item and item.widget(): item.widget().deleteLater()
    def get_logins(self): return [f.get_login() for f in self._fields if f.get_login()]
    def set_logins(self, logins):
        for f in list(self._fields):
            for i in range(self._v.count()):
                it=self._v.itemAt(i)
                if not it or not it.widget(): continue
                lay=it.widget().layout()
                if lay and any(lay.itemAt(j) and lay.itemAt(j).widget() is f for j in range(lay.count())):
                    if f in self._fields: self._fields.remove(f)
                    item=self._v.takeAt(i)
                    if item and item.widget(): item.widget().deleteLater()
                    break
        for login in (logins or []): self._add_row(login)

class _RoleCountList(QWidget):
    def __init__(self, messages, can_edit=True, parent=None):
        super().__init__(parent)
        self._messages=messages; self._can_edit=can_edit; self._rows=[]
        self._v=QVBoxLayout(self); self._v.setContentsMargins(0,0,0,0); self._v.setSpacing(2)
        add=QPushButton(_m(messages,"project.roles.add","+ Add role"))
        add.setFlat(True); add.setEnabled(can_edit); add.clicked.connect(lambda: self._add_row())
        self._v.addWidget(add)
    _VALID_ROLES = [
        ROLE_VALUE_SPENDER,
        ROLE_VALUE_AMME,
        ROLE_VALUE_SAMENSP,
        ROLE_VALUE_OFFSPRING,
        ROLE_VALUE_PARTNER,
        ROLE_VALUE_ZUCHTTIER,
    ]

    def _add_row(self, role='', count=0):
        row_w = QWidget(); row_h = QHBoxLayout(row_w); row_h.setContentsMargins(0, 0, 0, 0)
        role_cb = QComboBox(); role_cb.setEnabled(self._can_edit)
        for r in self._VALID_ROLES:
            role_cb.addItem(_localize_role(r, self._messages), r)
        idx = role_cb.findData(canonical_role_value(role))
        if idx >= 0: role_cb.setCurrentIndex(idx)
        elif role: role_cb.insertItem(0, _localize_role(role, self._messages) or role, role); role_cb.setCurrentIndex(0)
        count_sb = QSpinBox(); count_sb.setRange(0, 999999); count_sb.setValue(count)
        count_sb.setEnabled(self._can_edit); count_sb.setFixedWidth(70)
        rem = QPushButton("\u2212"); rem.setFixedWidth(24); rem.setEnabled(self._can_edit)
        row_h.addWidget(role_cb, 1); row_h.addWidget(count_sb); row_h.addWidget(rem)
        rd = {'role_cb': role_cb, 'count_sb': count_sb, 'row_w': row_w}; self._rows.append(rd)
        self._v.insertWidget(self._v.count() - 1, row_w)
        rem.clicked.connect(lambda: self._remove_row(rd))
    def _remove_row(self, rd):
        if rd in self._rows: self._rows.remove(rd)
        idx = self._v.indexOf(rd['row_w'])
        if idx >= 0:
            item = self._v.takeAt(idx)
            if item and item.widget(): item.widget().deleteLater()
    def get_roles(self):
        return [{'role': canonical_role_value(r['role_cb'].currentData() or r['role_cb'].currentText()), 'count': r['count_sb'].value()}
                for r in self._rows if r['role_cb'].currentData() or r['role_cb'].currentText()]
    def set_roles(self, roles):
        for r in list(self._rows): self._remove_row(r)
        for item in (roles or []):
            # Older seed/catalog records stored role IDs as bare strings.
            # Keep those records readable while the next save writes the
            # canonical ``{"role": ..., "count": ...}`` shape.
            if isinstance(item, dict):
                role = item.get('role', '')
                count = item.get('count', 0)
            else:
                role = str(item or '').strip()
                count = 0
            self._add_row(role, count)


def _project_role_specs(animals_config):
    """Normalize project role counts from current and legacy catalog shapes."""
    if not isinstance(animals_config, dict):
        return []
    raw_roles = animals_config.get('roles') or []
    if isinstance(raw_roles, dict):
        raw_roles = [
            {'role': role, 'count': count}
            for role, count in raw_roles.items()
        ]
    if not isinstance(raw_roles, (list, tuple)):
        return []
    legacy_strings = [item for item in raw_roles if isinstance(item, str) and item.strip()]
    try:
        approved_count = max(0, int(animals_config.get('approved_count') or 0))
    except (TypeError, ValueError):
        approved_count = 0
    specs = []
    for item in raw_roles:
        if isinstance(item, dict):
            role = str(item.get('role') or item.get('value') or '').strip()
            count = item.get('count', 0)
        else:
            role = str(item or '').strip()
            # The old Ringbearer seed had one role string plus an approved
            # cohort count. Preserve that meaning when displaying it.
            count = approved_count if len(legacy_strings) == 1 else 0
        if not role:
            continue
        try:
            count = max(0, int(count or 0))
        except (TypeError, ValueError):
            count = 0
        specs.append({'role': canonical_role_value(role), 'count': count})
    return specs

# ── ProjectTrackTab ───────────────────────────────────────────────────────────

class ProjectTrackTab(QWidget):
    def __init__(self, app, messages, history_store, parent=None):
        super().__init__(parent)
        self._app=app; self._messages=messages; self._history=history_store
        self._store = BackendJsonStore(app.backend, "projects", "catalog")
        self._current_project=None
        self._docs_base=os.path.join(os.path.dirname(__file__), _DOCS_SUBDIR)
        self._sop_base=os.path.join(os.path.dirname(__file__), _SOP_SUBDIR)
        self._project_data={'version':1,'projects':{}}
        self._load_data(); self._build_ui()

    def _load_data(self):
        d = self._store.load({"version": 1, "projects": {}})
        if isinstance(d, dict):
            self._project_data = d
            self._project_data.setdefault('version', 1)
            self._project_data.setdefault('projects', {})

    def _save_data(self):
        try:
            self._store.save(self._project_data)
        except Exception as exc: logger.warning("ProjectTrackTab save: %s", exc)

    def _project_record(self, name):
        p=self._project_data['projects']
        if name not in p: p[name]={}
        r=p[name]
        for k in ('created_by','created_at','modified_by','modified_at'): r.setdefault(k,'')
        for k in ('summary','iacuc','assoc_users','animals_config','arrive'): r.setdefault(k,{})
        return r

    def _can(self, perm):
        can_fn = getattr(self._app, '_master_can', None)
        if callable(can_fn):
            return bool(can_fn(perm))
        mt = getattr(self._app, 'master_track', None)
        if mt is None:
            return True
        if "master_track" in getattr(self._app, '_disabled_plugins', set()):
            return True
        return bool(getattr(mt, 'can', lambda _: False)(perm))

    def _current_sig(self):
        mt=getattr(self._app,'master_track',None)
        if not mt: return ''
        dname=getattr(mt,'current_display_name',None)
        return str(dname or getattr(mt,'current_username','') or '').strip()

    def _build_ui(self):
        main_h=QHBoxLayout(self); main_h.setContentsMargins(0,0,0,0)
        splitter=QSplitter(Qt.Orientation.Horizontal); splitter.setHandleWidth(4)
        # Left
        left_w=QWidget(); left_v=QVBoxLayout(left_w); left_v.setContentsMargins(4,4,0,0)
        left_v.setSpacing(5)
        hdr=QLabel(_m(self._messages,"project.tab.list_header","Projects"))
        hdr.setStyleSheet("font-weight:bold;"); left_v.addWidget(hdr)
        self._new_project_btn = QPushButton(_m(self._messages, "project.tab.new_btn", "New Project"))
        apply_icon(self._new_project_btn, "action.add", fallback="New Project")
        self._new_project_btn.setEnabled(self._can('project.create'))
        self._new_project_btn.clicked.connect(self._on_new_project)
        top_btn_row = QHBoxLayout()
        top_btn_row.setContentsMargins(0, 0, 0, 0)
        top_btn_row.setSpacing(4)
        self._refresh_project_btn = QPushButton()
        apply_icon(self._refresh_project_btn, "action.refresh", fallback="Refresh")
        self._refresh_project_btn.setToolTip(_m(self._messages, "projects.tooltip.refresh", "Refresh project list"))
        self._refresh_project_btn.clicked.connect(self._on_refresh_clicked)
        top_btn_row.addWidget(self._new_project_btn, 5)
        top_btn_row.addWidget(self._refresh_project_btn, 1)
        left_v.addLayout(top_btn_row)
        self._list=QListWidget(); self._list.currentRowChanged.connect(self._on_list_selection)
        left_v.addWidget(self._list,1)
        left_v.addSpacing(2)
        self._archive_btn = QPushButton(_m(self._messages, "button.sidebar.archive", "Archive"))
        apply_icon(self._archive_btn, "action.archive", fallback="Archive")
        self._archive_btn.setEnabled(False); self._archive_btn.clicked.connect(self._on_archive)
        left_v.addWidget(self._archive_btn)
        self._show_archived_chk=QCheckBox(_m(self._messages,"project.tab.show_archived","Show archived"))
        self._show_archived_chk.toggled.connect(self._refresh_project_list); left_v.addWidget(self._show_archived_chk)
        arch_row_h = QHBoxLayout()
        arch_row_h.setContentsMargins(0, 0, 0, 0); arch_row_h.setSpacing(2)
        self._restore_btn = QPushButton(_m(self._messages, "button.sidebar.restore", "Restore"))
        apply_icon(self._restore_btn, "action.restore", fallback="Restore")
        self._restore_btn.setEnabled(False); self._restore_btn.clicked.connect(self._on_restore_project)
        self._delete_btn = QPushButton(_m(self._messages, "button.sidebar.delete", "Delete"))
        apply_icon(self._delete_btn, "action.delete", fallback="Delete")
        self._delete_btn.setEnabled(False); self._delete_btn.clicked.connect(self._on_delete_project)
        arch_row_h.addWidget(self._restore_btn); arch_row_h.addWidget(self._delete_btn)
        left_v.addLayout(arch_row_h)
        left_w.setMinimumWidth(280); left_w.setMaximumWidth(280); splitter.addWidget(left_w)
        # Right
        right_scroll=QScrollArea(); right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._right_w=QWidget(); self._right_v=QVBoxLayout(self._right_w)
        self._right_v.setContentsMargins(8,8,8,8); self._right_v.setSpacing(6)
        self._right_v.addStretch(); right_scroll.setWidget(self._right_w)
        splitter.addWidget(right_scroll); splitter.setSizes([220,600])
        splitter.setStretchFactor(0,0); splitter.setStretchFactor(1,1)
        main_h.addWidget(splitter); self._splitter=splitter
        self._refresh_project_list()

    def _clear_right(self):
        while self._right_v.count():
            item = self._right_v.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_project_list(self):
        self._list.blockSignals(True); prev=self._current_project; self._list.clear()
        show_arch=self._show_archived_chk.isChecked()
        all_names=sorted(set(list(self._history.all_projects())+list(self._project_data['projects'].keys())))
        for src in [getattr(self._app,'animals',{}) or {}, getattr(self._app,'archived_animals',{}) or {}]:
            for ad in src.values():
                if isinstance(ad,dict):
                    proj=(ad.get('project') or '').strip()
                    if proj: all_names=sorted(set(all_names)|{proj})
        if hasattr(self._app, '_project_visibility_scope'):
            unrestricted, visible_projects = self._app._project_visibility_scope()
            if not unrestricted:
                all_names = [n for n in all_names if n in visible_projects]
        active_names = [n for n in all_names if not self._history.is_archived(n)]
        arch_names   = [n for n in all_names if self._history.is_archived(n)]
        for name in active_names:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._list.addItem(item)
        if show_arch and arch_names:
            sep = QListWidgetItem('\u2500' * 20)
            sep.setFlags(Qt.ItemFlag.NoItemFlags)
            sep.setForeground(QColor('gray'))
            sep.setData(Qt.ItemDataRole.UserRole, '__sep__')
            self._list.addItem(sep)
            for name in arch_names:
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, name)
                self._list.addItem(item)
        self._list.blockSignals(False)
        if prev:
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole)==prev:
                    self._list.setCurrentRow(i); return
        if self._list.count()>0: self._list.setCurrentRow(0)
        else:
            self._current_project=None; self._clear_right()
            lbl=QLabel("Select a project"); lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._right_v.addWidget(lbl); self._right_v.addStretch()

    def _on_list_selection(self, row):
        if row < 0:
            self._current_project = None
            self._archive_btn.setEnabled(False)
            self._restore_btn.setEnabled(False)
            self._delete_btn.setEnabled(False)
            return
        item = self._list.item(row)
        if item is None: return
        if item.data(Qt.ItemDataRole.UserRole) == '__sep__': return
        name = item.data(Qt.ItemDataRole.UserRole)
        self._current_project = name
        is_arch = self._history.is_archived(name)
        can_arch = self._can('project.archive_project')
        can_manage = self._can('project.manage')
        self._archive_btn.setEnabled(can_arch and not is_arch)
        self._restore_btn.setEnabled(can_arch and is_arch)
        self._delete_btn.setEnabled(can_manage and is_arch)
        self._build_detail(name)

    def _on_new_project(self):
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(
            self,
            _m(self._messages, 'project.tab.new_title', 'New Project'),
            _m(self._messages, 'project.tab.new_prompt', 'Project name:'))
        if not ok or not text.strip(): return
        pname = text.strip()
        if pname in self._project_data['projects']:
            QMessageBox.warning(self, '', _m(self._messages, 'project.tab.new_exists',
                'A project with this name already exists.'))
            return
        sig = self._current_sig()
        now_str = datetime.now().strftime('%d.%m.%Y %H:%M')
        self._project_data['projects'][pname] = {
            'created_by': sig, 'created_at': now_str,
            'modified_by': sig, 'modified_at': now_str,
            'summary': {}, 'iacuc': {}, 'assoc_users': {}, 'animals_config': {}
        }
        self._save_data()
        pt_plugin = getattr(self._app, 'projects_plugin', None)
        if pt_plugin and hasattr(pt_plugin, '_on_refresh_clicked'):
            pt_plugin._on_refresh_clicked()
        self._current_project = pname
        self._refresh_project_list()

    def _audit_project_action(self, action: str, project_name: str) -> None:
        mt = getattr(self._app, 'master_track', None)
        if mt and hasattr(mt, 'audit'):
            try:
                mt.audit(action, project_name)
            except Exception as exc:
                logger.warning(
                    "Project audit failed: action=%s project=%s error=%s",
                    action,
                    project_name,
                    exc,
                    exc_info=True,
                )

    def _on_archive(self):
        name = self._current_project
        if not name or self._history.is_archived(name): return
        msg = _m(self._messages, "project.tab.archive_confirm", "Archive project '{name}'?").replace('{name}', name)
        if QMessageBox.question(self, "", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        self._history.set_archived(name, True)
        self._audit_project_action('archive_project', name)
        self._refresh_project_list()

    def _on_restore_project(self):
        name = self._current_project
        if not name or not self._history.is_archived(name): return
        msg = _m(self._messages, "project.tab.restore_confirm", "Restore project '{name}'?").replace('{name}', name)
        if QMessageBox.question(self, "", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        self._history.set_archived(name, False)
        self._audit_project_action('restore_project', name)
        self._refresh_project_list()

    def _on_delete_project(self):
        name = self._current_project
        if not name: return
        msg = _m(self._messages, "project.tab.delete_confirm", "Permanently delete project '{name}'?").replace('{name}', name)
        if QMessageBox.question(self, "", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes: return
        self._project_data['projects'].pop(name, None)
        self._save_data()
        # Also delete from the backend project history.
        if self._history and hasattr(self._history, 'delete_project'):
            self._history.delete_project(name)
        self._audit_project_action('delete_project', name)
        self._current_project = None
        self._archive_btn.setEnabled(False)
        self._restore_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._refresh_project_list()

    def _build_detail(self, name):
        self._clear_right()
        can_manage = self._can('project.manage')
        can_assign = self._can('project.project_assign')
        rec = self._project_record(name)

        # ── Meta ──────────────────────────────────────────────────────────
        meta_w = QWidget(); meta_v = QVBoxLayout(meta_w); meta_v.setContentsMargins(0, 0, 0, 4)
        cb = (rec.get('created_by') or '').strip()
        if cb:
            l = QLabel(f"{_m(self._messages,'project.label.created_by','Created by:')} {cb}")
            l.setStyleSheet("color:grey;font-size:9pt;"); meta_v.addWidget(l)
        mb = (rec.get('modified_by') or '').strip(); ma = (rec.get('modified_at') or '').strip()
        if mb or ma:
            t = _m(self._messages, "project.label.last_modified", "Last modified by: {user}, {date}")
            l2 = QLabel(t.replace('{user}', mb).replace('{date}', ma))
            l2.setStyleSheet("color:grey;font-size:9pt;"); meta_v.addWidget(l2)
        self._right_v.addWidget(meta_w)

        # ── Summary (1 column) ───────────────────────────────────────────
        sec_s = CollapsibleSection(_m(self._messages, "project.section.summary", "Summary"))
        cl = sec_s.content_layout()
        summ = rec.get('summary', {})

        form_s = _mk_form()
        self._s_title = QLineEdit(summ.get('title', '') or ''); self._s_title.setEnabled(can_manage)
        form_s.addRow(_m(self._messages, "project.label.title", "Project title:"), self._s_title)
        species_list = _load_species_list()
        self._s_species = QComboBox(); self._s_species.setEditable(True); self._s_species.setEnabled(can_manage)
        for sp in species_list:
            self._s_species.addItem(sp)
        csp = summ.get('species', '') or ''
        if csp:
            idx = self._s_species.findText(csp)
            if idx >= 0:
                self._s_species.setCurrentIndex(idx)
            else:
                self._s_species.insertItem(0, csp); self._s_species.setCurrentIndex(0)
        form_s.addRow(_m(self._messages, "project.label.species", "Species:"), self._s_species)
        self._s_c1 = UserSearchField(self._messages, self._app, can_edit=can_manage)
        self._s_c1.set_login(summ.get('contact1_login', '') or '')
        form_s.addRow(_m(self._messages, "project.label.contact1", "1st Contact:"), self._s_c1)
        self._s_c2 = UserSearchField(self._messages, self._app, can_edit=can_manage)
        self._s_c2.set_login(summ.get('contact2_login', '') or '')
        form_s.addRow(_m(self._messages, "project.label.contact2", "2nd Contact:"), self._s_c2)
        self._s_co = DynamicUserList(self._messages, self._app, can_edit=can_manage)
        self._s_co.set_logins(summ.get('contacts_other_logins', []) or [])
        form_s.addRow(_m(self._messages, "project.label.contacts_other", "Other Contacts:"), self._s_co)
        self._s_comment = QTextEdit(); self._s_comment.setPlainText(summ.get('comment', '') or '')
        self._s_comment.setEnabled(can_manage); self._s_comment.setMaximumHeight(72)
        form_s.addRow(_m(self._messages, "project.label.comment", "Comment:"), self._s_comment)
        form_s_w = QWidget(); form_s_w.setLayout(form_s)
        cl.addWidget(form_s_w)
        self._right_v.addWidget(sec_s)

        # ── IACUC Protocol (3 columns) ────────────────────────────────────
        sec_i = CollapsibleSection(_m(self._messages, "project.section.iacuc", "IACUC Protocol"), collapsed=True)
        cl2 = sec_i.content_layout()
        iacuc = rec.get('iacuc', {})
        iacuc_w = QWidget(); iacuc_h = QHBoxLayout(iacuc_w)
        iacuc_h.setContentsMargins(0, 0, 0, 0); iacuc_h.setSpacing(16)
        self._iacuc_fields = {}

        # IACUC col 1: sort title, protocol ID, internal number, auth nr
        form_i1 = _mk_form()
        sort_val = iacuc.get('short_title', '') or name
        le_sort = QLineEdit(sort_val); le_sort.setEnabled(can_manage)
        le_sort.setToolTip(_m(self._messages, "project.iacuc.short_title.tooltip",
                              "This value is used as the project name in animal records."))
        form_i1.addRow(_m(self._messages, "project.iacuc.short_title", "Sort title:"), le_sort)
        self._iacuc_fields['short_title'] = le_sort
        for fk, mk, fb in [
            ('protocol_id',      "project.iacuc.protocol_id",      "Protocol ID:"),
            ('internal_number',  "project.iacuc.internal_number",  "Internal Number:"),
            ('authorization_nr', "project.iacuc.authorization_nr", "Authorization Nr:"),
        ]:
            le = QLineEdit(iacuc.get(fk, '') or ''); le.setEnabled(can_manage)
            form_i1.addRow(_m(self._messages, mk, fb), le); self._iacuc_fields[fk] = le
        iw1 = QWidget(); iw1.setLayout(form_i1)

        # IACUC col 2: PI, DI, Welfare Officer, Unit
        form_i2 = _mk_form()
        self._iacuc_pi = UserSearchField(self._messages, self._app, can_edit=can_manage)
        self._iacuc_pi.set_login(iacuc.get('pi_login', '') or '')
        form_i2.addRow(_m(self._messages, "project.iacuc.pi", "PI:"), self._iacuc_pi)
        self._iacuc_di = UserSearchField(self._messages, self._app, can_edit=can_manage)
        self._iacuc_di.set_login(iacuc.get('di_login', '') or '')
        form_i2.addRow(_m(self._messages, "project.iacuc.di", "DI:"), self._iacuc_di)
        self._iacuc_welfare = UserSearchField(
            self._messages,
            self._app,
            can_edit=can_manage,
            role_filter='animal_welfare_officer',
        )
        self._iacuc_welfare.set_login(iacuc.get('welfare_login', '') or '')
        form_i2.addRow(_m(self._messages, "project.iacuc.welfare_officer", "Welfare Officer:"), self._iacuc_welfare)
        le_unit = QLineEdit(iacuc.get('unit', '') or ''); le_unit.setEnabled(can_manage)
        form_i2.addRow(_m(self._messages, "project.iacuc.unit", "Unit:"), le_unit)
        self._iacuc_fields['unit'] = le_unit
        iw2 = QWidget(); iw2.setLayout(form_i2)

        # IACUC col 3: Purpose, Authorized, Approved
        form_i3 = _mk_form()
        for fk, mk, fb in [
            ('purpose',    "project.iacuc.purpose",    "Purpose:"),
            ('authorized', "project.iacuc.authorized", "Authorized:"),
            ('approved',   "project.iacuc.approved",   "Approved:"),
        ]:
            le = QLineEdit(iacuc.get(fk, '') or ''); le.setEnabled(can_manage)
            form_i3.addRow(_m(self._messages, mk, fb), le); self._iacuc_fields[fk] = le
        iw3 = QWidget(); iw3.setLayout(form_i3)

        iacuc_h.addWidget(iw1, 1); iacuc_h.addWidget(iw2, 1); iacuc_h.addWidget(iw3, 1)
        cl2.addWidget(iacuc_w)
        self._right_v.addWidget(sec_i)

        # ── Associated Users (1 column, full width) ───────────────────────
        sec_a = CollapsibleSection(_m(self._messages, "project.section.assoc_users", "Associated Users"), collapsed=True)
        cl3 = sec_a.content_layout()
        assoc = rec.get('assoc_users', {})
        form_a = _mk_form()
        self._a_applicant = UserSearchField(self._messages, self._app, can_edit=can_assign)
        self._a_applicant.set_login(assoc.get('applicant_login', '') or '')
        form_a.addRow(_m(self._messages, "project.assoc.applicant", "Applicant:"), self._a_applicant)
        self._a_planning = UserSearchField(self._messages, self._app, can_edit=can_assign)
        self._a_planning.set_login(assoc.get('planning_login', '') or '')
        form_a.addRow(_m(self._messages, "project.assoc.planning", "Planning:"), self._a_planning)
        self._a_staff = DynamicUserList(self._messages, self._app, can_edit=can_assign)
        self._a_staff.set_logins(assoc.get('staff_logins', []) or [])
        form_a.addRow(_m(self._messages, "project.assoc.staff", "Staff:"), self._a_staff)
        form_a_w = QWidget(); form_a_w.setLayout(form_a)
        cl3.addWidget(form_a_w)
        self._right_v.addWidget(sec_a)

        # ── Animals (1 column with expandable sub-sections) ───────────────
        sec_an = CollapsibleSection(_m(self._messages, "project.section.animals", "Animals"), collapsed=True)
        sec_an._btn.toggled.connect(lambda on: self._refresh_animals_section_safe(name) if on else None)
        cl4 = sec_an.content_layout()
        animals_cfg = rec.get('animals_config', {})
        form_an = _mk_form()
        self._an_approved = QSpinBox(); self._an_approved.setRange(0, 999999)
        self._an_approved.setValue(animals_cfg.get('approved_count', 0)); self._an_approved.setEnabled(can_manage)
        form_an.addRow(_m(self._messages, "project.animals.approved_count", "Approved animal count:"), self._an_approved)
        self._an_roles = _RoleCountList(self._messages, can_edit=can_manage)
        self._an_roles.set_roles(_project_role_specs(animals_cfg))
        form_an.addRow(_m(self._messages, "project.animals.roles", "Roles:"), self._an_roles)
        form_an_w = QWidget(); form_an_w.setLayout(form_an)
        cl4.addWidget(form_an_w)
        no_leg_w = QWidget(); no_leg_h = QHBoxLayout(no_leg_w); no_leg_h.setContentsMargins(4, 4, 4, 0)
        no_leg_lbl = QLabel(_m(self._messages, "project.animals.departed_with_sev_no_legal",
                               "Departed with severity without legal framework:"))
        no_leg_lbl.setWordWrap(True)
        self._an_no_legal = QSpinBox(); self._an_no_legal.setRange(0, 999999); self._an_no_legal.setEnabled(can_manage)
        no_leg_h.addWidget(no_leg_lbl, 1); no_leg_h.addWidget(self._an_no_legal)
        cl4.addWidget(no_leg_w)
        (_, _, former_sev, _) = self._compute_animal_stats(name)
        self._an_no_legal.setValue(min(animals_cfg.get('departed_with_sev_no_legal', 0), len(former_sev)))
        self._animals_stats_w = QWidget()
        self._animals_stats_v = QVBoxLayout(self._animals_stats_w)
        self._animals_stats_v.setContentsMargins(0, 4, 0, 0); self._animals_stats_v.setSpacing(2)
        cl4.addWidget(self._animals_stats_w)
        self._right_v.addWidget(sec_an)

        # ── ARRIVE Protocol (1 column, text areas) ────────────────────────
        sec_ar = CollapsibleSection(_m(self._messages, "project.section.arrive", "ARRIVE Protocol"), collapsed=True)
        cl_ar = sec_ar.content_layout()
        arrive = rec.get('arrive', {})
        self._arrive_fields = {}
        arrive_fields_config = [
            ('study_design', 'project.arrive.study_design', 'Study design:'),
            ('sample_size', 'project.arrive.sample_size', 'Sample size:'),
            ('inclusion_exclusion', 'project.arrive.inclusion_exclusion', 'Inclusion and exclusion criteria:'),
            ('randomisation', 'project.arrive.randomisation', 'Randomisation:'),
            ('blinding', 'project.arrive.blinding', 'Blinding:'),
            ('outcome_measures', 'project.arrive.outcome_measures', 'Outcome measures:'),
            ('statistical_methods', 'project.arrive.statistical_methods', 'Statistical methods:'),
            ('experimental_animals', 'project.arrive.experimental_animals', 'Experimental animals:'),
            ('experimental_procedures', 'project.arrive.experimental_procedures', 'Experimental procedures:'),
            ('results', 'project.arrive.results', 'Results:'),
            ('abstract', 'project.arrive.abstract', 'Abstract:'),
            ('background', 'project.arrive.background', 'Background:'),
            ('objectives', 'project.arrive.objectives', 'Objectives:'),
            ('ethical_statement', 'project.arrive.ethical_statement', 'Ethical statement:'),
            ('housing_husbandry', 'project.arrive.housing_husbandry', 'Housing and husbandry:'),
            ('animal_care', 'project.arrive.animal_care', 'Animal care and monitoring:'),
            ('interpretation', 'project.arrive.interpretation', 'Interpretation/scientific implications:'),
            ('protocol_registration', 'project.arrive.protocol_registration', 'Protocol registration:'),
            ('data_access', 'project.arrive.data_access', 'Data access:'),
            ('declaration_interests', 'project.arrive.declaration_interests', 'Declaration of interests:'),
        ]
        form_ar = _mk_form()
        for fk, mk, fb in arrive_fields_config:
            te = QTextEdit()
            te.setPlainText(arrive.get(fk, '') or '')
            te.setEnabled(can_manage)
            te.setMinimumHeight(100)
            te.setMaximumHeight(300)
            form_ar.addRow(_m(self._messages, mk, fb), te)
            self._arrive_fields[fk] = te
        form_ar_w = QWidget()
        form_ar_w.setLayout(form_ar)
        cl_ar.addWidget(form_ar_w)
        self._right_v.addWidget(sec_ar)

        # ── SOPs ──────────────────────────────────────────────────────────
        sec_sop = CollapsibleSection(_m(self._messages, "project.section.sops", "SOPs"), collapsed=True)
        sec_sop._btn.toggled.connect(lambda on: self._refresh_sops(name) if on else None)
        cl_sop = sec_sop.content_layout()
        self._sops_list = QListWidget(); self._sops_list.setMaximumHeight(180)
        self._sops_list.itemDoubleClicked.connect(lambda it: self._on_sop_open(name, it))
        cl_sop.addWidget(self._sops_list)
        can_up = self._can('project.upload_sop'); can_del = self._can('project.delete_sop')
        sop_btn_w = QWidget(); sop_btn_h = QHBoxLayout(sop_btn_w); sop_btn_h.setContentsMargins(0, 4, 0, 0)
        self._sop_upload_btn = QPushButton('\U0001f4ce  ' + _m(self._messages, "project.sops.upload", "Upload"))
        self._sop_upload_btn.setEnabled(can_up)
        self._sop_upload_btn.clicked.connect(lambda: self._on_sop_upload(name))
        self._sop_delete_btn = QPushButton('\U0001f5d1  ' + _m(self._messages, "project.sops.delete", "Delete Selected"))
        self._sop_delete_btn.setEnabled(can_del)
        self._sop_delete_btn.clicked.connect(lambda: self._on_sop_delete(name))
        sop_btn_h.addWidget(self._sop_upload_btn); sop_btn_h.addWidget(self._sop_delete_btn); sop_btn_h.addStretch()
        cl_sop.addWidget(sop_btn_w)
        self._right_v.addWidget(sec_sop)

        # ── Documents ─────────────────────────────────────────────────────
        sec_d = CollapsibleSection(_m(self._messages, "project.section.documents", "Documents"), collapsed=True)
        sec_d._btn.toggled.connect(lambda on: self._refresh_docs(name) if on else None)
        cl5 = sec_d.content_layout()
        self._docs_list = QListWidget(); self._docs_list.setMaximumHeight(180)
        self._docs_list.itemDoubleClicked.connect(lambda it: self._on_doc_open(name, it))
        cl5.addWidget(self._docs_list)
        can_up = self._can('project.upload_document'); can_del = self._can('project.delete_document')
        doc_btn_w = QWidget(); doc_btn_h = QHBoxLayout(doc_btn_w); doc_btn_h.setContentsMargins(0, 4, 0, 0)
        self._doc_upload_btn = QPushButton('\U0001f4ce  ' + _m(self._messages, "project.docs.upload", "Upload"))
        self._doc_upload_btn.setEnabled(can_up)
        self._doc_upload_btn.clicked.connect(lambda: self._on_doc_upload(name))
        self._doc_delete_btn = QPushButton('\U0001f5d1  ' + _m(self._messages, "project.docs.delete", "Delete Selected"))
        self._doc_delete_btn.setEnabled(can_del)
        self._doc_delete_btn.clicked.connect(lambda: self._on_doc_delete(name))
        doc_btn_h.addWidget(self._doc_upload_btn); doc_btn_h.addWidget(self._doc_delete_btn); doc_btn_h.addStretch()
        cl5.addWidget(doc_btn_w)
        self._right_v.addWidget(sec_d)

        # ── Single Save button at bottom ──────────────────────────────────
        save_w = QWidget(); save_h = QHBoxLayout(save_w); save_h.setContentsMargins(0, 8, 0, 8)
        save_btn = QPushButton(_m(self._messages, "project.btn.save", "Save"))
        save_btn.setEnabled(can_manage); save_btn.clicked.connect(lambda: self._save_detail(name))
        save_h.addStretch(); save_h.addWidget(save_btn)
        self._right_v.addWidget(save_w)
        self._right_v.addStretch()

    def _compute_animal_stats(self, name):
        ah = self._history.get_animals(name)
        aa = getattr(self._app, 'animals', {}) or {}
        arch_aa = getattr(self._app, 'archived', {}) or {}
        history_records = [r for r in ah if isinstance(r, dict)]
        active_hist = []
        for record in history_records:
            animal_name = _history_animal_key(record)
            if not animal_name:
                logger.warning("Project Track: skipping history record without animal name for project %s", name)
                continue
            if record.get('status') == 'active' and (animal_name in aa or animal_name in arch_aa):
                active_hist.append(animal_name)
        direct_active   = [n for n, d in aa.items()
                           if (d.get('project') or '').strip() == name and n not in active_hist]
        direct_archived = [n for n, d in arch_aa.items()
                           if (d.get('project') or '').strip() == name and n not in active_hist]
        active = list(dict.fromkeys(active_hist + direct_active + direct_archived))
        former_no = []
        former_sev = []
        previous = []
        for record in history_records:
            animal_name = _history_animal_key(record)
            if not animal_name:
                continue
            if record.get('status') == 'former':
                if _clean_text(record.get('last_severity')):
                    former_sev.append(animal_name)
                else:
                    former_no.append(animal_name)
            snapshot = record.get('previous_project_snapshot')
            if isinstance(snapshot, list):
                has_previous = bool(snapshot)
            else:
                has_previous = bool(self._history.previous_projects(animal_name, exclude=name))
            if has_previous:
                previous.append(animal_name)
        return (
            active,
            list(dict.fromkeys(former_no)),
            list(dict.fromkeys(former_sev)),
            list(dict.fromkeys(previous)),
        )

    def _refresh_animals_section_safe(self, name):
        try:
            self._refresh_animals_section(name)
        except Exception:
            logger.exception("Project Track: failed to refresh Animals section for project %s", name)
            if not hasattr(self, '_animals_stats_v'):
                return
            while self._animals_stats_v.count():
                item = self._animals_stats_v.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            msg = QLabel(_m(
                self._messages,
                'project.animals.refresh_failed',
                'Animal statistics could not be loaded. See technical logs.',
            ))
            msg.setWordWrap(True)
            msg.setStyleSheet('color:#b00020;padding:4px;font-size:9pt;')
            self._animals_stats_v.addWidget(msg)

    def _refresh_animals_section(self, name):
        from collections import defaultdict
        if not hasattr(self, '_animals_stats_v'): return
        while self._animals_stats_v.count():
            item = self._animals_stats_v.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        aa = getattr(self._app, 'animals', {}) or {}
        arch_aa = getattr(self._app, 'archived', {}) or {}
        all_known = {**arch_aa, **aa}
        ah = self._history.get_animals(name)
        active, former_no, former_sev, previous = self._compute_animal_stats(name)
        in_exp   = [n for n in active if all_known.get(n, {}).get('in_experiment', False)]
        not_in   = [n for n in active if n not in in_exp]
        m = self._messages
        rec = self._project_record(name)
        role_max = {
            canonical_role_value(item.get('role')): item.get('count', 0)
            for item in _project_role_specs(rec.get('animals_config', {}))
            if item.get('role')
        }

        _ROLE_ORDER = [
            ROLE_VALUE_SPENDER,
            ROLE_VALUE_AMME,
            ROLE_VALUE_SAMENSP,
            ROLE_VALUE_OFFSPRING,
            ROLE_VALUE_PARTNER,
            ROLE_VALUE_ZUCHTTIER,
            ROLE_VALUE_EXPERIMENTAL,
        ]
        _ROLE_COLORS = {
            ROLE_VALUE_SPENDER: 'deeppink',
            ROLE_VALUE_AMME: 'mediumpurple',
            ROLE_VALUE_SAMENSP: '#1a1aff',
            ROLE_VALUE_OFFSPRING: 'gray',
            ROLE_VALUE_PARTNER: 'darkorange',
            ROLE_VALUE_ZUCHTTIER: 'gray',
            ROLE_VALUE_EXPERIMENTAL: '#00AAAA',
        }

        def _name_color(aname):
            data = all_known.get(aname)
            if data is None:
                return '#888888'
            role_val = canonical_role_value(data.get('rolle') or '')
            sex = (data.get('sex') or '').lower()
            if role_val in (ROLE_VALUE_OFFSPRING, ROLE_VALUE_ZUCHTTIER):
                if 'female' in sex or 'weiblich' in sex:
                    return 'deeppink' if role_val == ROLE_VALUE_OFFSPRING else '#C71585'
                if 'male' in sex or 'männlich' in sex:
                    return '#1a1aff' if role_val == ROLE_VALUE_OFFSPRING else '#00008B'
                return 'gray'
            if role_val == ROLE_VALUE_EXPERIMENTAL:
                return '#FF7788' if ('female' in sex or 'weiblich' in sex) else '#00FFDD'
            return _ROLE_COLORS.get(role_val, '#333333')

        def _association_records(aname):
            result = []
            for rec in ah:
                if not isinstance(rec, dict):
                    continue
                if _history_animal_key(rec) != aname:
                    continue
                if rec.get('status') not in ('active', 'former'):
                    continue
                result.append(rec)
            return result

        def _snapshot_records(aname, key, fallback_lookup_name):
            records = []
            snapshot_seen = False
            for assoc in _association_records(aname):
                snapshot = assoc.get(key)
                if isinstance(snapshot, list):
                    snapshot_seen = True
                    records.extend(
                        dict(rec) for rec in snapshot if isinstance(rec, dict)
                    )
            if snapshot_seen:
                return records
            lookup = getattr(self._history, fallback_lookup_name, None)
            if lookup is None:
                return []
            return lookup(aname, exclude=name)

        def _previous_history_text(aname):
            lookup = getattr(self._history, 'previous_project_records', None)
            if lookup is None:
                projects = self._history.previous_projects(aname, exclude=name)
                return '; '.join(projects)
            records = _snapshot_records(
                aname,
                'previous_project_snapshot',
                'previous_project_records',
            )
            parts = []
            no_sev = _m(self._messages, 'project.animals.no_severity_recorded', 'no severity recorded')
            left_label = _m(self._messages, 'project.animals.left_on', 'left')
            for rec in records:
                project = (rec.get('project') or '').strip()
                severity = (rec.get('last_severity') or '').strip() or no_sev
                left = (rec.get('date_left') or '').strip()
                text = f"{project}: {severity}" if project else severity
                if left:
                    text += f", {left_label} {left}"
                parts.append(text)
            return '; '.join(parts)

        def _previous_experimental_records(aname):
            records = _snapshot_records(
                aname,
                'previous_experimental_snapshot',
                'previous_experimental_records',
            )
            if records:
                return records
            snapshot_seen = any(
                isinstance(assoc.get('previous_experimental_snapshot'), list)
                for assoc in _association_records(aname)
            )
            return [] if snapshot_seen else records

        def _departed_severity_details():
            template = _m(
                self._messages,
                'project.animals.departed_with_severity_detail',
                'departed with {severity}',
            )
            details = {}
            for rec in ah:
                if not isinstance(rec, dict):
                    continue
                animal_name = _history_animal_key(rec)
                severity = _clean_text(rec.get('last_severity'))
                if rec.get('status') != 'former' or not animal_name or not severity:
                    continue
                details[animal_name] = str(template).replace('{severity}', severity)
            return details

        total_all = active + former_no + former_sev
        history_scope = list(dict.fromkeys(total_all))
        previous_experimental = [
            n for n in history_scope if _previous_experimental_records(n)
        ]
        no_prev = [n for n in history_scope if n not in previous_experimental]

        def _make_name_btn(aname, detail=''):
            is_arch = aname in arch_aa and aname not in aa
            color = '#888888' if is_arch else _name_color(aname)
            animal_record = all_known.get(aname, {})
            display_name = animal_base_name(aname, animal_record)
            animal_id = (animal_record.get('id') or '').strip()
            display = ('   \u2022 ' + display_name + ' (' + animal_id + ')') if animal_id else ('   \u2022 ' + display_name)
            if detail:
                display += ' - ' + detail
            lbl = QLabel(display)
            style = 'color:' + color + ';padding:0;font-size:9pt;'
            if is_arch:
                style += 'font-style:italic;'
            lbl.setStyleSheet(style)
            if is_arch:
                archived_tip = _m(self._messages, 'project.animals.is_archived',
                                  'This animal is archived.')
                lbl.setToolTip(f"{archived_tip}\nIPID: {aname}")
            else:
                lbl.setToolTip(f"IPID: {aname}")
            return lbl

        def _grp(lkey, fb, names, details=None):
            details = details or {}
            sec = CollapsibleSection(f"{_m(m, lkey, fb)}  {len(names)}", collapsed=True)
            if names:
                role_groups = defaultdict(list)
                for aname in names:
                    role_val = canonical_role_value(all_known.get(aname, {}).get('rolle') or '')
                    role_groups[role_val].append(aname)
                for r in role_groups:
                    role_groups[r].sort()
                ordered = [r for r in _ROLE_ORDER if r in role_groups]
                ordered += sorted(r for r in role_groups if r not in _ROLE_ORDER)
                show_hdg = len(ordered) > 1 or (len(ordered) == 1 and ordered[0])
                for role_val in ordered:
                    grp_names = role_groups[role_val]
                    cnt = len(grp_names)
                    display_role = _localize_role(role_val, m) if role_val else _m(m, 'project.animals.role_unknown', 'Unknown Role')
                    if show_hdg:
                        max_val = role_max.get(role_val)
                        if max_val is None:
                            hdg_html = f'<b style="color:red;">{display_role} [{cnt}/?]</b>'
                        elif cnt > max_val:
                            hdg_html = f'<b style="color:red;">{display_role} [{cnt}/{max_val}]</b>'
                        else:
                            hdg_html = f'<b>{display_role} [{cnt}/{max_val}]</b>'
                        hdg_lbl = QLabel(hdg_html)
                        hdg_lbl.setStyleSheet('padding-left:8px;font-size:9pt;')
                        sec.content_layout().addWidget(hdg_lbl)
                    for aname in grp_names:
                        sec.content_layout().addWidget(_make_name_btn(aname, details.get(aname, '')))
            self._animals_stats_v.addWidget(sec)

        previous_details = {aname: _previous_history_text(aname) for aname in previous}
        previous_experimental_details = {
            aname: _previous_history_text(aname) for aname in previous_experimental
        }
        departed_severity_details = _departed_severity_details()
        _grp('project.animals.total_booked',          'Total booked',                          total_all)
        _grp('project.animals.currently_in_exp',      'Currently in experiment',               in_exp)
        _grp('project.animals.alive_not_in_exp',      'Alive, not in experiment',              not_in)
        _grp('project.animals.departed_total',        'Departed total',                        former_no + former_sev)
        _grp('project.animals.departed_no_severity',  'Departed without severity',             former_no)
        _grp('project.animals.departed_with_severity','Departed with severity',                former_sev, departed_severity_details)
        _grp('project.animals.previously_in_projects','Previously in other projects',           previous, previous_details)
        _grp('project.animals.no_prev_history',       'No previous experimental history',      no_prev)
        _grp('project.animals.with_prev_history',     'With previous experimental history',    previous_experimental, previous_experimental_details)

    def _doc_dir(self, name):
        safe="".join(c if (c.isalnum() or c in '-_()') else '_' for c in name)
        return os.path.join(self._docs_base,safe)

    def _refresh_docs(self, name):
        if not hasattr(self, '_docs_list'): return
        self._docs_list.clear()
        for record in self._app.backend.documents.list_for_owner(
            "project-document", name
        ):
            item = QListWidgetItem(str(record["original_name"]))
            item.setData(Qt.ItemDataRole.UserRole, str(record["document_id"]))
            self._docs_list.addItem(item)

    def _on_doc_open(self, name, item):
        document_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            record = self._app.backend.documents.get(str(document_id))
            path = self._app.backend.documents.payload_path(record)
        except (KeyError, OSError):
            return
        if not open_local_path(path):
            logger.error('doc open failed: %s', path)

    def _on_doc_upload(self, name):
        if not self._can('project.upload_document'):
            return
        files,_=QFileDialog.getOpenFileNames(self,_m(self._messages,"project.docs.upload_dialog","Select Documents"),"",_ALL_DOC_FILTER)
        if not files: return
        for fp in files:
            try:
                self._app.backend.documents.add(
                    fp,
                    owner_type="project-document",
                    owner_id=name,
                    actor=self._current_sig(),
                )
            except Exception as exc: logger.error("doc upload: %s",exc)
        self._refresh_docs(name)

    def _on_doc_delete(self, name):
        if not self._can('project.delete_document'):
            return
        item = self._docs_list.currentItem()
        if not item: return
        fname = item.text()
        tmpl = _m(self._messages, "project.docs.delete_confirm", "Delete '{name}'?")
        if QMessageBox.question(self, "", tmpl.replace('{name}', fname),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._app.backend.documents.remove(
                str(item.data(Qt.ItemDataRole.UserRole))
            )
        except Exception as exc: logger.error('doc delete: %s', exc)
        self._refresh_docs(name)

    def _sop_dir(self, name):
        safe="".join(c if (c.isalnum() or c in '-_()') else '_' for c in name)
        return os.path.join(self._sop_base,safe)

    def _refresh_sops(self, name):
        if not hasattr(self, '_sops_list'): return
        self._sops_list.clear()
        for record in self._app.backend.documents.list_for_owner(
            "project-sop", name
        ):
            item = QListWidgetItem(str(record["original_name"]))
            item.setData(Qt.ItemDataRole.UserRole, str(record["document_id"]))
            self._sops_list.addItem(item)

    def _on_sop_open(self, name, item):
        document_id = item.data(Qt.ItemDataRole.UserRole)
        try:
            record = self._app.backend.documents.get(str(document_id))
            path = self._app.backend.documents.payload_path(record)
        except (KeyError, OSError):
            return
        if not open_local_path(path):
            logger.error('sop open failed: %s', path)

    def _on_sop_upload(self, name):
        if not self._can('project.upload_sop'):
            return
        files,_=QFileDialog.getOpenFileNames(self,_m(self._messages,"project.sops.upload_dialog","Select SOPs"),"",_ALL_DOC_FILTER)
        if not files: return
        for fp in files:
            try:
                self._app.backend.documents.add(
                    fp,
                    owner_type="project-sop",
                    owner_id=name,
                    actor=self._current_sig(),
                )
            except Exception as exc: logger.error("sop upload: %s",exc)
        self._refresh_sops(name)

    def _on_sop_delete(self, name):
        if not self._can('project.delete_sop'):
            return
        item = self._sops_list.currentItem()
        if not item: return
        fname = item.text()
        tmpl = _m(self._messages, "project.sops.delete_confirm", "Delete '{name}'?")
        if QMessageBox.question(self, "", tmpl.replace('{name}', fname),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._app.backend.documents.remove(
                str(item.data(Qt.ItemDataRole.UserRole))
            )
        except Exception as exc: logger.error('sop delete: %s', exc)
        self._refresh_sops(name)

    def _save_detail(self, name):
        rec = self._project_record(name); sig = self._current_sig()
        before_rec = json.loads(json.dumps(rec))
        old_species = str((rec.get("summary") or {}).get("species") or "")
        now_str = datetime.now().strftime('%d.%m.%Y %H:%M')
        if not rec.get('created_by'): rec['created_by'] = sig; rec['created_at'] = now_str
        rec['modified_by'] = sig; rec['modified_at'] = now_str
        rec['summary'] = {
            'title': self._s_title.text().strip(),
            'contact1_login': self._s_c1.get_login() or '',
            'contact2_login': self._s_c2.get_login() or '',
            'contacts_other_logins': self._s_co.get_logins(),
            'species': self._s_species.currentText().strip(),
            'comment': self._s_comment.toPlainText().strip(),
        }
        new_species = rec["summary"]["species"]
        removed_animals: List[str] = []
        if new_species != old_species:
            animals = getattr(self._app, "animals", {})
            if isinstance(animals, dict):
                removed_animals = remove_mismatched_assignments(
                    animals, name, new_species)
                for animal_id in removed_animals:
                    animal = animals.get(animal_id, {})
                    animal.setdefault("project_history", []).append({
                        "project": name,
                        "leave_date": datetime.now().strftime("%d.%m.%Y"),
                        "reason": "project_species_changed",
                        "actor": sig,
                    })
        iacuc_d = {k: v.text().strip() for k, v in self._iacuc_fields.items()}
        iacuc_d['pi_login']      = self._iacuc_pi.get_login() or ''
        iacuc_d['di_login']      = self._iacuc_di.get_login() or ''
        iacuc_d['welfare_login'] = self._iacuc_welfare.get_login() or ''
        rec['iacuc'] = iacuc_d
        rec['assoc_users'] = {
            'applicant_login': self._a_applicant.get_login() or '',
            'planning_login':  self._a_planning.get_login() or '',
            'staff_logins':    self._a_staff.get_logins(),
        }
        (_, _, former_sev, _) = self._compute_animal_stats(name)
        if self._an_no_legal.value() > len(former_sev):
            QMessageBox.warning(self, "", _m(self._messages, "project.animals.no_legal_overflow_warn",
                "Value exceeds the computed 'Departed with severity' count. The data will be saved as entered."))
        rec['animals_config'] = {
            'approved_count': self._an_approved.value(),
            'roles': self._an_roles.get_roles(),
            'departed_with_sev_no_legal': self._an_no_legal.value(),
        }
        rec['arrive'] = {k: v.toPlainText().strip() for k, v in self._arrive_fields.items()}
        self._save_data()
        if removed_animals:
            save_app = getattr(self._app, "_save_persistence", None)
            if callable(save_app):
                save_app()
            QMessageBox.warning(
                self,
                _m(self._messages, "title.warning", "Warning"),
                _m(
                    self._messages,
                    "project.species_changed_removed",
                    "{count} mismatched animal assignment(s) were removed after "
                    "the project species changed.",
                ).replace("{count}", str(len(removed_animals))),
            )
        changed_users = diff_project_associated_users(before_rec, rec)
        mt_dirty = getattr(self._app, 'master_track', None)
        if changed_users and mt_dirty and hasattr(mt_dirty, 'mark_project_visibility_dirty'):
            mt_dirty.mark_project_visibility_dirty(sorted(changed_users))
        pt_plugin = getattr(self._app, 'projects_plugin', None)
        if pt_plugin:
            invalidate = getattr(pt_plugin, 'invalidate_user_caches', None)
            if changed_users and callable(invalidate):
                invalidate(sorted(changed_users))
            refresh = getattr(pt_plugin, 'refresh_projects', None)
            if callable(refresh):
                refresh(force_discovery=True)
        self._audit_project_action('edit_project', name)
        self._refresh_animals_section(name)
        self._refresh_project_list()
        QMessageBox.information(
            self,
            "",
            _m(self._messages, "project.info.saved", "Project changes saved!"),
        )

    def select_project(self, name):
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole)==name:
                self._list.setCurrentRow(i); return

    def update_language(self, messages):
        self._messages=messages
        if hasattr(self, '_refresh_project_btn'):
            self._refresh_project_btn.setToolTip(_m(self._messages, "projects.tooltip.refresh", "Refresh project list"))
        self._refresh_project_list()
        if self._current_project: self._build_detail(self._current_project)

    def on_user_login(self):
        """Refresh UI permissions when user logs in or out."""
        # Update button states based on current permissions
        self._new_project_btn.setEnabled(self._can('project.create'))
        self._refresh_project_list()
        if self._current_project:
            is_arch = self._history.is_archived(self._current_project)
            can_arch = self._can('project.archive_project')
            can_manage = self._can('project.manage')
            self._archive_btn.setEnabled(can_arch and not is_arch)
            self._restore_btn.setEnabled(can_arch and is_arch)
            self._delete_btn.setEnabled(can_manage and is_arch)
            # Rebuild detail view to refresh field enable/disable states
            self._build_detail(self._current_project)

    def _on_refresh_clicked(self):
        pt_plugin = getattr(self._app, 'projects_plugin', None)
        refresh = getattr(pt_plugin, 'refresh_projects', None) if pt_plugin else None
        if callable(refresh):
            refresh(force_discovery=True)
        else:
            self._refresh_project_list()
