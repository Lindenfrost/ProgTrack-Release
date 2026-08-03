# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Projects Track sidebar-filter plugin implementation.

import logging
from typing import Any, Callable, Dict, List, Optional
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                             QLabel, QButtonGroup, QFrame, QScrollArea,
                             QSizePolicy, QApplication)
from PyQt6.QtCore import Qt, QRect, QSize, QTimer
from PyQt6.QtGui import QColor, QPainter, QFont
from Plugins.core.animal_identity import animal_base_name
from Plugins.core.project_visibility import animal_visible_by_project_scope
from Plugins.core.backend_store import BackendJsonStore
from Plugins.core.ui_icons import apply_icon


class ProjectTabButton(QPushButton):
    """Horizontal project tab button with vertically rotated text (90° CCW)."""

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.button_text = text
        # Height = text width in rotated space + padding, minimum 60 px
        from PyQt6.QtGui import QFont, QFontMetrics
        _font = QFont()
        _font.setPointSize(9)
        _fm = QFontMetrics(_font)
        _h = max(60, _fm.horizontalAdvance(text) + 24)
        self.setFixedSize(22, _h)
        self._update_style(False)
    
    def setActive(self, active):
        """Set visual active state."""
        self._update_style(active)
    
    def paintEvent(self, event):
        """Custom paint with rotated text."""
        from PyQt6.QtGui import QPainter, QFont
        
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw background based on state
        if self.isChecked() or self.isDown():
            painter.fillRect(self.rect(), QColor("#0078d4"))
            painter.setPen(QColor("white"))
        else:
            if self.underMouse():
                painter.fillRect(self.rect(), QColor("#e5e5e5"))
            painter.setPen(QColor("#333"))
        
        # Rotate painter 90° counter-clockwise
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        
        # Draw text centered
        font = QFont()
        font.setPointSize(9)
        font.setBold(self.isChecked())
        painter.setFont(font)
        
        # Calculate text position using actual button height as the text span
        half_h = self.height() // 2
        text_rect = painter.boundingRect(-half_h, -10, self.height(), 20, Qt.AlignmentFlag.AlignCenter, self.button_text)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.button_text)
        
        painter.end()
    
    def _update_style(self, active):
        """Update button styling."""
        # Style is handled in paintEvent
        self.update()


class _SidebarToggleButton(QPushButton):
    """Narrow checkable button with text rotated 90° CCW, used as column toggler."""
    TOGGLE_W = 18

    def __init__(self, text: str = '', parent=None):
        super().__init__(parent)
        self._label = text
        self.setCheckable(True)
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedWidth(self.TOGGLE_W)
        self.setToolTip(text)

    def setText(self, text: str):
        self._label = text
        self.setToolTip(text)
        self.update()

    def text(self) -> str:
        return self._label

    def sizeHint(self):
        return QSize(self.TOGGLE_W, 80)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.isChecked():
            painter.fillRect(self.rect(), QColor('#b8c8e8'))
        elif self.underMouse():
            painter.fillRect(self.rect(), QColor('#d8d8d8'))
        else:
            painter.fillRect(self.rect(), QColor('#e8e8e8'))
        painter.setPen(QColor('#aaaaaa'))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.save()
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        painter.setFont(font)
        col = QColor('#333333') if self.isEnabled() else QColor('#999999')
        painter.setPen(col)
        painter.drawText(
            QRect(-self.height() // 2, -self.width() // 2, self.height(), self.width()),
            Qt.AlignmentFlag.AlignCenter, self._label)
        painter.restore()
        painter.end()


class HistoryStore:
    def __init__(self, plugin_dir: str, backend):
        # ``plugin_dir`` is retained for constructor compatibility.  History
        # is stored exclusively in the shared backend.
        self._store = BackendJsonStore(backend, "projects", "history")
        self._data: dict = {"version": 1, "projects": {}}
        self._load()

    def _load(self):
        d = self._store.load({"version": 1, "projects": {}})
        if isinstance(d, dict):
            self._data = d
            self._data.setdefault("version", 1)
            self._data.setdefault("projects", {})

    def _save(self):
        try:
            self._store.save(self._data)
        except Exception as exc:
            logging.warning("HistoryStore: backend save failed: %s", exc)

    def _proj(self, name: str) -> dict:
        p = self._data["projects"]
        if name not in p:
            p[name] = {"archived": False, "animals": []}
        return p[name]

    def _today(self) -> str:
        from datetime import date
        return date.today().strftime("%d.%m.%Y")

    def _is_previous_experimental_record(self, record: dict) -> bool:
        if "previous_in_experiment" in record:
            return bool(record.get("previous_in_experiment"))
        return bool(record.get("last_severity"))

    def _animal_key(self, record: dict) -> str:
        return str(record.get("ipid") or record.get("name") or "").strip()

    def record_added(self, project: str, animal: str):
        animal = (animal or "").strip()
        if not animal:
            return
        proj = self._proj(project)
        for r in proj["animals"]:
            if self._animal_key(r) == animal and r.get("status") == "active":
                return
        previous_snapshot = [
            dict(record)
            for record in self.previous_project_records(animal, exclude=project)
        ]
        proj["animals"].append({
            "ipid": animal,
            "name": animal_base_name(animal),
            "status": "active",
            "date_entered": self._today(),
            "date_left": None, "last_severity": None,
            "previous_project_snapshot": previous_snapshot,
            "previous_experimental_snapshot": [
                dict(record)
                for record in previous_snapshot
                if self._is_previous_experimental_record(record)
            ]})
        self._save()

    def record_removed(self, project: str, animal: str, severity: str = None,
                       previous_in_experiment: bool = None):
        animal = (animal or "").strip()
        for r in self._proj(project)["animals"]:
            if self._animal_key(r) == animal and r.get("status") == "active":
                had_in_experiment = bool(
                    r.get("previous_in_experiment") or r.get("had_in_experiment"))
                r["status"] = "former"
                r["date_left"] = self._today()
                r["last_severity"] = severity or r.get("last_severity") or None
                if previous_in_experiment is not None:
                    r["previous_in_experiment"] = (
                        had_in_experiment or bool(previous_in_experiment))
                elif had_in_experiment:
                    r["previous_in_experiment"] = True
        self._save()

    def record_experiment_status(self, project: str, animal: str,
                                 had_in_experiment: bool,
                                 severity: str = None) -> None:
        project = (project or '').strip()
        animal = (animal or '').strip()
        if not project or not animal or not had_in_experiment:
            return
        changed = False
        for r in self._proj(project)["animals"]:
            if self._animal_key(r) == animal and r.get("status") == "active":
                if not r.get("previous_in_experiment"):
                    r["previous_in_experiment"] = True
                    changed = True
                if not r.get("had_in_experiment"):
                    r["had_in_experiment"] = True
                    changed = True
                if severity and r.get("last_severity") != severity:
                    r["last_severity"] = severity
                    changed = True
                break
        if changed:
            self._save()

    def is_archived(self, project: str) -> bool:
        return self._data["projects"].get(project, {}).get("archived", False)

    def set_archived(self, project: str, value: bool):
        self._proj(project)["archived"] = value
        self._save()

    def delete_project(self, project: str) -> bool:
        """Permanently delete a project from history. Returns True if deleted."""
        if project in self._data["projects"]:
            del self._data["projects"][project]
            self._save()
            return True
        return False

    def get_animals(self, project: str) -> list:
        return self._data["projects"].get(project, {}).get("animals", [])

    def all_projects(self) -> list:
        return list(self._data["projects"].keys())

    def previous_projects(self, animal: str, exclude: str = None) -> list:
        result = []
        for pname, pdata in self._data["projects"].items():
            if pname == exclude:
                continue
            if any(
                self._animal_key(r) == animal and r.get("status") == "former"
                for r in pdata.get("animals", [])
            ):
                result.append(pname)
        return result

    def previous_project_records(self, animal: str, exclude: str = None) -> list:
        result = []
        for pname, pdata in self._data["projects"].items():
            if pname == exclude:
                continue
            for record in pdata.get("animals", []):
                if self._animal_key(record) != animal:
                    continue
                if record.get("status") != "former":
                    continue
                entry = dict(record)
                entry["project"] = pname
                result.append(entry)
        return result

    def previous_experimental_records(self, animal: str, exclude: str = None) -> list:
        result = []
        for record in self.previous_project_records(animal, exclude=exclude):
            if self._is_previous_experimental_record(record):
                result.append(record)
        return result


class ProjectsTrackPlugin:
    """
    ProjectsTrack plugin for filtering animals by project assignment.
    
    Creates a vertical tab bar to the left of the animal list.
    """
    
    # Plugin metadata
    name = "ProjectsTrack"
    version = "1.0.0"
    description = "Filter animals by project assignment"
    
    def __init__(self, app):
        """Initialize the ProjectsTrack plugin.
        
        Args:
            app: The main ProgTrackApp instance
        """
        self.app = app
        # UI references (set by create_sidebar_tabs)
        self.tabs_container = None          # The scroll area containing tabs
        self.tabs_inner_widget = None       # Inner widget with buttons
        self.tab_buttons = {}               # project_name -> button
        self.button_group = None
        self.species_tabs_inner_widget = None
        self.species_tab_buttons = {}       # species_name -> button
        self.species_button_group = None
        self.refresh_btn = None
        self._proj_scroll = None            # scroll area for project buttons
        self._sp_scroll = None              # scroll area for species buttons
        self._sidebar_toggle_btn = None     # single combined toggle
        self._proj_content_w = None         # collapsible content widget (project)
        self._sp_content_w = None           # collapsible content widget (species)
        self._sidebar_visible = True        # single state for both columns

        # Current filter state
        self.current_project = "All"
        self.all_projects = []
        self.active_species: Optional[str] = None
        self.all_species: List[str] = []

        # Scope-change callbacks (called whenever project or species changes)
        self.scope_changed_callbacks: List = []
        self._project_ui_refresh_pending = False

        # History store for project membership tracking
        self._history = HistoryStore("", app.backend)

        # Load cached projects, species, and last session state
        self._load_projects()
        self._discover_species()

    def _cache_store(self, identity: Optional[str] = None) -> BackendJsonStore:
        return BackendJsonStore(
            self.app.backend,
            "project-cache",
            identity or self._cache_identity(),
        )

    def _cache_identity(self) -> str:
        """Return the identity whose sidebar state may be cached.

        Guest is deliberately an identity of its own.  Privileged users are
        *not* folded into one global cache: visibility, active selections and
        the underlying project list all belong to the current login context.
        """
        mt = getattr(self.app, 'master_track', None)
        disabled = "master_track" in getattr(self.app, '_disabled_plugins', set())
        if mt is None or disabled or not getattr(mt, 'is_logged_in', False):
            return 'guest'
        return str(getattr(mt, 'current_username', '') or 'guest').strip() or 'guest'

    def _uses_global_project_cache(self) -> bool:
        """Compatibility hook retained for callers; shared caches are unsafe."""
        return False

    def _cache_context(self) -> Dict[str, Any]:
        """Describe the permission scope represented by the current cache."""
        unrestricted = True
        visible_projects: List[str] = []
        scope_fn = getattr(self.app, '_project_visibility_scope', None)
        if callable(scope_fn):
            try:
                unrestricted, visible = scope_fn()
                visible_projects = sorted(
                    {str(project) for project in visible if str(project).strip()},
                    key=str.lower,
                )
            except Exception:
                logging.exception("ProjectsTrack: failed to resolve project cache scope")
        mt = getattr(self.app, 'master_track', None)
        can_view_all = bool(
            mt and getattr(mt, 'can', lambda _permission: False)('project.view_all')
        )
        return {
            'identity': self._cache_identity(),
            'role': str(getattr(mt, 'current_role', '') or ''),
            'can_view_all': can_view_all,
            'unrestricted': bool(unrestricted),
            'visible_projects': visible_projects,
        }
    
    def _load_projects(self, force_discovery: bool = False):
        """Load projects from cache or discover from animals, and restore session state."""
        current_context = self._cache_context()
        try:
            cached = self._cache_store().load({})
            cached_context = cached.get('context') or {}
            if cached_context.get('identity') == current_context['identity']:
                self.current_project = cached.get('active_project', 'All') or 'All'
                self.active_species = cached.get('active_species') or None
            if cached.get('invalidated') or cached_context != current_context:
                raise ValueError('stale project cache context')
            if not force_discovery and cached:
                self.all_projects = cached.get('projects', [])
                return
        except Exception:
            pass
        self._discover_projects()

    def _visibility_scope(self) -> Optional[tuple[bool, set[str]]]:
        scope_fn = getattr(self.app, '_project_visibility_scope', None)
        if callable(scope_fn):
            try:
                unrestricted, visible = scope_fn()
                return bool(unrestricted), {
                    str(project) for project in visible if str(project).strip()
                }
            except Exception:
                logging.exception("ProjectsTrack: failed to resolve visibility scope")
        return None

    def _record_is_visible(
        self,
        record: Dict[str, Any],
        scope: Optional[tuple[bool, set[str]]] = None,
    ) -> bool:
        if scope is not None:
            return animal_visible_by_project_scope(record, scope[0], scope[1])
        visible_fn = getattr(self.app, '_animal_visible_to_current_user', None)
        if callable(visible_fn):
            return bool(visible_fn(record))
        return True
    
    def _discover_projects(self):
        """Scan all animals and collect unique project names."""
        projects = set()
        
        # Check if animals is a dict (it may be a list during early initialization)
        if not isinstance(self.app.animals, dict):
            self.all_projects = []
            self._save_cache()
            return
        
        scope = self._visibility_scope()
        for animal_name, animal_data in self.app.animals.items():
            if not isinstance(animal_data, dict) or not self._record_is_visible(animal_data, scope):
                continue
            # Check for 'project' field in animal record
            project = animal_data.get('project')
            if project and isinstance(project, str) and project.strip():
                projects.add(project.strip())
        
        # Sort alphabetically, case-insensitive
        self.all_projects = sorted(projects, key=str.lower)
        if self.current_project != 'All' and self.current_project not in self.all_projects:
            self.current_project = 'All'

        # Always save to cache (even if empty)
        self._save_cache()
    
    def _save_cache(self):
        """Save current project list to cache file, preserving existing session-state keys."""
        try:
            existing = self._cache_store().load({})
            existing['projects'] = self.all_projects
            existing['version'] = self.version
            existing['active_project'] = self.current_project
            existing['active_species'] = self.active_species
            existing['context'] = self._cache_context()
            existing['invalidated'] = False
            self._cache_store().save(existing)
        except Exception:
            pass

    def _save_session_state(self) -> None:
        """Persist active project and species selections to the cache file."""
        try:
            cached = self._cache_store().load({})
            cached['active_project'] = self.current_project
            cached['active_species'] = self.active_species
            cached['context'] = self._cache_context()
            cached['invalidated'] = False
            self._cache_store().save(cached)
        except Exception:
            pass

    def invalidate_user_caches(self, identities) -> None:
        """Mark selected login caches stale without deleting session state."""
        if isinstance(identities, str):
            identities = [identities]
        for identity in identities or []:
            identity = str(identity or '').strip()
            if not identity:
                continue
            try:
                store = self._cache_store(identity)
                cached = store.load({})
                if not isinstance(cached, dict):
                    cached = {}
                cached['invalidated'] = True
                store.save(cached)
            except Exception:
                logging.exception(
                    "ProjectsTrack: failed to invalidate cache for %s", identity)
    
    def _make_scroll_column(self, parent, inner_widget) -> QScrollArea:
        """Helper: wrap *inner_widget* in a narrow vertical scroll area."""
        sa = QScrollArea(parent)
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sa.setFrameShape(QFrame.Shape.NoFrame)
        sa.setWidget(inner_widget)
        # Use native scrollbar styling (like the animal list)
        return sa

    def _load_sidebar_visibility(self) -> bool:
        """Load combined sidebar visible state from Master Track session."""
        mt = getattr(self.app, 'master_track', None)
        if mt and getattr(mt, 'is_logged_in', False):
            sess = mt.load_session()
            return sess.get('sidebar_visible', True)
        return True

    def _save_sidebar_visibility(self) -> None:
        """Persist combined sidebar visible state to current user's session."""
        mt = getattr(self.app, 'master_track', None)
        if mt and getattr(mt, 'is_logged_in', False):
            mt.save_session({'sidebar_visible': self._sidebar_visible})

    def create_sidebar_tabs(self, parent_widget):
        """
        Create the project + species tabs widget for the sidebar.

        A single vertical toggle button on the left controls both columns
        simultaneously.  Both species and project columns are always shown
        (even for a single species / single project).
        """
        self._discover_species()
        self._sidebar_visible = self._load_sidebar_visibility()

        btn_w  = _SidebarToggleButton.TOGGLE_W   # 18 px
        cont_w = 35                               # each content column width

        sp_lbl   = self.app.messages.get('projects.sidebar.toggle.species',  'Species')
        proj_lbl = self.app.messages.get('projects.sidebar.toggle.projects', 'Projects')
        combined_lbl = f"{sp_lbl} / {proj_lbl}"

        # Main container
        self.tabs_container = QWidget(parent_widget)
        self.tabs_container.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        outer = QHBoxLayout(self.tabs_container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Single combined toggle ──────────────────────────────────────────────
        self._sidebar_toggle_btn = _SidebarToggleButton(combined_lbl)
        self._sidebar_toggle_btn.setChecked(self._sidebar_visible)
        self._sidebar_toggle_btn.toggled.connect(self._on_toggle_sidebar)
        outer.addWidget(self._sidebar_toggle_btn)

        # ── Species content (always present) ──────────────────────────────────────
        self._sp_content_w = QWidget()
        self._sp_content_w.setFixedWidth(cont_w)
        sp_content_layout = QVBoxLayout(self._sp_content_w)
        sp_content_layout.setContentsMargins(2, 4, 2, 0)
        sp_content_layout.setSpacing(2)

        self.species_tabs_inner_widget = QWidget()
        sp_inner_layout = QVBoxLayout(self.species_tabs_inner_widget)
        sp_inner_layout.setContentsMargins(0, 0, 0, 0)
        sp_inner_layout.setSpacing(2)
        sp_inner_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.species_button_group = QButtonGroup(self.species_tabs_inner_widget)
        self.species_button_group.setExclusive(True)

        self._create_species_button('All', sp_inner_layout)
        for sp in self.all_species:
            self._create_species_button(sp, sp_inner_layout)
        sp_inner_layout.addStretch()

        self._sp_scroll = self._make_scroll_column(
            self._sp_content_w, self.species_tabs_inner_widget)
        sp_content_layout.addWidget(self._sp_scroll, 1)

        self._sp_content_w.setVisible(self._sidebar_visible)
        outer.addWidget(self._sp_content_w)

        # ── Project content (always present) ──────────────────────────────────────
        self._proj_content_w = QWidget()
        self._proj_content_w.setFixedWidth(cont_w)
        proj_content_layout = QVBoxLayout(self._proj_content_w)
        proj_content_layout.setContentsMargins(2, 4, 2, 0)
        proj_content_layout.setSpacing(2)

        self.tabs_inner_widget = QWidget()
        tabs_layout = QVBoxLayout(self.tabs_inner_widget)
        tabs_layout.setContentsMargins(0, 0, 0, 0)
        tabs_layout.setSpacing(2)
        tabs_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.button_group = QButtonGroup(self.tabs_inner_widget)
        self.button_group.setExclusive(True)

        self._create_tab_button('All', tabs_layout, is_all=True)
        for project in self.all_projects:
            self._create_tab_button(project, tabs_layout, is_all=False)
        tabs_layout.addStretch()

        self._proj_scroll = self._make_scroll_column(
            self._proj_content_w, self.tabs_inner_widget)
        proj_content_layout.addWidget(self._proj_scroll, 1)

        # Refresh button inside project content
        self.refresh_btn = QPushButton()
        apply_icon(self.refresh_btn, "action.refresh", fallback="Refresh")
        self.refresh_btn.setToolTip(
            self.app.messages.get('projects.tooltip.refresh', 'Refresh project list'))
        self.refresh_btn.setFixedSize(28, 20)
        self.refresh_btn.setStyleSheet(
            'QPushButton{font-size:11px;padding:0;background:#f0f0f0;'
            'border:1px solid #ccc;border-radius:3px;}'
            'QPushButton:hover{background:#e0e0e0;}')
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        proj_content_layout.addWidget(
            self.refresh_btn, 0, Qt.AlignmentFlag.AlignHCenter)

        self._proj_content_w.setVisible(self._sidebar_visible)
        outer.addWidget(self._proj_content_w)

        # Restore session selections without triggering signals
        def _restore_btn(btn_dict, key):
            target = btn_dict.get(key) or btn_dict.get('All')
            if target:
                for b in btn_dict.values():
                    b.blockSignals(True)
                target.setChecked(True)
                target.setActive(True)
                for b in btn_dict.values():
                    b.blockSignals(False)
                for name, b in btn_dict.items():
                    b.setActive(b is target)

        _restore_btn(self.tab_buttons, self.current_project or 'All')
        _restore_btn(self.species_tab_buttons, self.active_species or 'All')

        self._update_container_width()
        return self.tabs_container
    
    def _create_tab_button(self, project_name, layout, is_all=False):
        """Create a single project tab button."""
        btn = ProjectTabButton(project_name)
        tooltip_key = "projects.tooltip.all" if is_all else "projects.tooltip.project"
        default_text = "Show all animals" if is_all else f"Show animals in project: {project_name}"
        tooltip_text = self.app.messages.get(tooltip_key, default_text)
        btn.setToolTip(tooltip_text)

        self.button_group.addButton(btn)
        layout.addWidget(btn)

        self.tab_buttons[project_name] = btn

        btn.toggled.connect(lambda checked, p=project_name: self._on_tab_toggled(p, checked))

    def _create_species_button(self, species_name: str, layout) -> None:
        """Create a single species tab button."""
        btn = ProjectTabButton(species_name)
        tip = ("Show all species"
               if species_name == "All"
               else f"Filter by species: {species_name}")
        btn.setToolTip(tip)
        self.species_button_group.addButton(btn)
        layout.addWidget(btn)
        self.species_tab_buttons[species_name] = btn
        btn.toggled.connect(
            lambda checked, s=species_name: self._on_species_tab_toggled(s, checked))
    
    def _on_toggle_sidebar(self, checked: bool) -> None:
        """Show/hide both species and project content areas; persist state."""
        self._sidebar_visible = checked
        if self._proj_content_w:
            self._proj_content_w.setVisible(checked)
        if self._sp_content_w:
            self._sp_content_w.setVisible(checked)
        self._update_container_width()
        self._save_sidebar_visibility()

    def _update_container_width(self) -> None:
        """Set container fixed width based on toggle state."""
        if not self.tabs_container:
            return
        btn_w  = _SidebarToggleButton.TOGGLE_W
        cont_w = 35
        w = btn_w  # toggle always present
        if self._sidebar_visible:
            has_species = bool(self.all_species)
            if has_species:
                w += cont_w  # species column
            w += cont_w      # project column always
        self.tabs_container.setFixedWidth(w)

    def on_user_login(self) -> None:
        """Reload per-user sidebar toggle state after login/logout."""
        self._sidebar_visible = self._load_sidebar_visibility()
        if self._proj_content_w:
            self._proj_content_w.setVisible(self._sidebar_visible)
        if self._sp_content_w:
            self._sp_content_w.setVisible(self._sidebar_visible)
        if self._sidebar_toggle_btn:
            self._sidebar_toggle_btn.blockSignals(True)
            self._sidebar_toggle_btn.setChecked(self._sidebar_visible)
            self._sidebar_toggle_btn.blockSignals(False)
        self.refresh_projects(force_discovery=True)

    def update_language(self, messages):
        """Update UI texts when language changes.
        
        Called by ProgTrack's _refresh_ui() when the user changes language.
        """
        self.messages = messages

        # Update combined toggle button label
        if self._sidebar_toggle_btn:
            sp_lbl   = messages.get('projects.sidebar.toggle.species',  'Species')
            proj_lbl = messages.get('projects.sidebar.toggle.projects', 'Projects')
            lbl = f"{sp_lbl} / {proj_lbl}"
            self._sidebar_toggle_btn.setText(lbl)

        # Update refresh button tooltip
        if hasattr(self, 'refresh_btn') and self.refresh_btn:
            refresh_tooltip = messages.get("projects.tooltip.refresh", "Refresh project list")
            self.refresh_btn.setToolTip(refresh_tooltip)
        
        # Update tab tooltips
        for project_name, btn in self.tab_buttons.items():
            is_all = (project_name == "All")
            tooltip_key = "projects.tooltip.all" if is_all else "projects.tooltip.project"
            tooltip_text = messages.get(tooltip_key, 
                                      "Show all animals" if is_all else f"Show project: {project_name}")
            btn.setToolTip(tooltip_text)
    
    def _on_species_tab_toggled(self, species_name: str, checked: bool) -> None:
        """Handle species tab selection change."""
        if not checked:
            if species_name in self.species_tab_buttons:
                self.species_tab_buttons[species_name].setActive(False)
            return
        for name, btn in self.species_tab_buttons.items():
            btn.setActive(name == species_name)
        self.active_species = None if species_name == "All" else species_name
        self._save_session_state()
        self._apply_filter()

    def _on_tab_toggled(self, project_name, checked):
        """Handle tab selection change (toggled signal)."""
        if not checked:
            # Button was unchecked - just update style
            if project_name in self.tab_buttons:
                self.tab_buttons[project_name].setActive(False)
            return
        
        # Button was checked - update all styles and apply filter
        for name, btn in self.tab_buttons.items():
            btn.setActive(name == project_name)
        
        # Update filter state
        self.current_project = project_name
        self._save_session_state()

        # Notify main app to filter animal list
        self._apply_filter()
    
    def _on_refresh_clicked(self):
        """Handle refresh button click - rebuild project and species lists."""
        self.refresh_projects(force_discovery=True)

    def refresh_projects(self, force_discovery: bool = True) -> None:
        """Refresh every Projects Track consumer from one consistent scope.

        This is the public plugin-local refresh hook used by the sidebar,
        Project Track tab and save/login integration.  It deliberately
        refreshes discovery, both sidebar columns, the tab list and registered
        scope callbacks as one operation.
        """
        self._load_projects(force_discovery=force_discovery)
        self._discover_species()
        desired_project = self.current_project
        desired_species = self.active_species
        self._rebuild_tabs()
        self._rebuild_species_tabs()
        pt_w = getattr(self.app, 'project_track_widget', None)
        if pt_w and hasattr(pt_w, '_refresh_project_list'):
            pt_w._refresh_project_list()

        project_target = self.tab_buttons.get(desired_project)
        if project_target is None and "All" in self.tab_buttons:
            self.current_project = "All"
            project_target = self.tab_buttons["All"]
        if project_target is not None:
            for button in self.tab_buttons.values():
                button.blockSignals(True)
            project_target.setChecked(True)
            for button in self.tab_buttons.values():
                button.setActive(button is project_target)
                button.blockSignals(False)

        species_target = self.species_tab_buttons.get(desired_species or "All")
        if species_target is None and "All" in self.species_tab_buttons:
            self.active_species = None
            species_target = self.species_tab_buttons["All"]
        if species_target is not None:
            for button in self.species_tab_buttons.values():
                button.blockSignals(True)
            species_target.setChecked(True)
            for button in self.species_tab_buttons.values():
                button.setActive(button is species_target)
                button.blockSignals(False)
        self._save_session_state()
        self._update_container_width()
        self._apply_filter()
    
    def _clear_layout_buttons(self, layout, button_group, buttons_dict) -> None:
        """Helper: remove all buttons from a layout/group/dict."""
        for btn in list(buttons_dict.values()):
            button_group.removeButton(btn)
            btn.deleteLater()
        buttons_dict.clear()
        while layout.count() > 0:
            item = layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

    def _rebuild_tabs(self):
        """Rebuild the project tab buttons after project list changes."""
        if not self.tabs_inner_widget:
            return
        layout = self.tabs_inner_widget.layout()
        self._clear_layout_buttons(layout, self.button_group, self.tab_buttons)
        self._create_tab_button("All", layout, is_all=True)
        for project in self.all_projects:
            self._create_tab_button(project, layout, is_all=False)
        layout.addStretch()

    def _rebuild_species_tabs(self) -> None:
        """Rebuild the species tab buttons after species list changes."""
        if not self.species_tabs_inner_widget:
            return
        layout = self.species_tabs_inner_widget.layout()
        self._clear_layout_buttons(layout, self.species_button_group,
                                   self.species_tab_buttons)
        self._create_species_button("All", layout)
        for sp in self.all_species:
            self._create_species_button(sp, layout)
        layout.addStretch()
        # Species content always shown when sidebar is visible
        if self._sp_content_w:
            self._sp_content_w.setVisible(self._sidebar_visible)
        self._update_container_width()
        # Restore species selection without triggering signal side-effects
        saved_sp_key = self.active_species or 'All'
        target = self.species_tab_buttons.get(saved_sp_key) or self.species_tab_buttons.get('All')
        if target:
            for b in self.species_tab_buttons.values():
                b.blockSignals(True)
            target.setChecked(True)
            for b in self.species_tab_buttons.values():
                b.blockSignals(False)
            for name, b in self.species_tab_buttons.items():
                b.setActive(b is target)
        # Apply both project+species filters now that animals are loaded
        self._apply_filter()
    
    def add_scope_changed_callback(self, fn: Callable) -> None:
        """Register *fn* to be called whenever the active project or species changes."""
        if fn not in self.scope_changed_callbacks:
            self.scope_changed_callbacks.append(fn)

    def notify_scope_changed(self) -> None:
        """Call all registered scope-change callbacks."""
        for fn in list(self.scope_changed_callbacks):
            try:
                fn()
            except Exception:
                pass

    def set_active_species(self, species: Optional[str]) -> None:
        """Change the active species filter and notify callbacks."""
        self.active_species = species
        self._apply_filter()

    def _discover_species(self) -> None:
        """Collect species only from animals visible in the current scope."""
        species: set = set()
        animals = getattr(self.app, 'animals', {}) or {}
        if not isinstance(animals, dict):
            self.all_species = []
            return
        scope = self._visibility_scope()
        for rec in animals.values():
            if not isinstance(rec, dict) or not self._record_is_visible(rec, scope):
                continue
            sp = rec.get('species', '')
            if sp and isinstance(sp, str) and sp.strip():
                species.add(sp.strip())
        self.all_species = sorted(species, key=str.lower)

    def animals_in_scope(self) -> List[str]:
        """Return the list of animal names matching the current project + species filter."""
        animals = getattr(self.app, 'animals', {}) or {}
        result = []
        unrestricted, visible_projects = (
            self.app._project_visibility_scope()
            if hasattr(self.app, '_project_visibility_scope')
            else (True, set())
        )
        for name, rec in animals.items():
            if not isinstance(rec, dict):
                continue
            if not animal_visible_by_project_scope(rec, unrestricted, visible_projects):
                continue
            if self.current_project and self.current_project != 'All':
                if rec.get('project') != self.current_project:
                    continue
            if self.active_species:
                if rec.get('species', '') != self.active_species:
                    continue
            result.append(name)
        return result

    def _apply_filter(self):
        """Apply the current project filter to the animal list.
        
        This method calls back into the main app to update the animal list display.
        """
        # Call the main app's filter method (to be implemented in main code)
        if hasattr(self.app, '_apply_project_filter'):
            self.app._apply_project_filter(self.current_project)
        else:
            pass  # Main app does not support project filtering yet
        self.notify_scope_changed()

    def _schedule_project_ui_refresh(self) -> None:
        """Refresh project UI after the current dialog/save event has returned."""
        if self._project_ui_refresh_pending:
            return
        self._project_ui_refresh_pending = True
        QTimer.singleShot(0, self._refresh_project_ui_after_change)

    def _refresh_project_ui_after_change(self) -> None:
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(250, self._refresh_project_ui_after_change)
            return
        self._project_ui_refresh_pending = False
        try:
            self.refresh_projects(force_discovery=True)
        except Exception:
            logging.exception("ProjectsTrack: failed to refresh project UI after animal project change")
    
    def get_animal_project(self, animal_name):
        """Get the project assigned to an animal.
        
        Args:
            animal_name: Name of the animal
            
        Returns:
            str: Project name or None if not assigned
        """
        animal = self.app.animals.get(animal_name, {})
        return animal.get('project')
    
    def set_animal_project(self, animal_name, project_name):
        """Set/change the project for an animal.
        
        Args:
            animal_name: Name of the animal
            project_name: Project name to assign (None to clear)
        """
        if animal_name in self.app.animals:
            old_project = (self.app.animals[animal_name].get('project') or '').strip()
            new_project = (project_name or '').strip()
            if project_name and project_name.strip():
                self.app.animals[animal_name]['project'] = new_project
            else:
                self.app.animals[animal_name].pop('project', None)
            if old_project != new_project:
                cage = getattr(self.app, 'cage_track_plugin', None)
                mark_dirty = getattr(cage, 'mark_cage_assignments_dirty', None) if cage else None
                if callable(mark_dirty):
                    try:
                        mark_dirty()
                    except Exception:
                        logging.exception(
                            "ProjectsTrack: failed to mark Cage Track assignments dirty for %s project change",
                            animal_name)
            
            # Refresh projects if this is a new project name
            if new_project and new_project not in self.all_projects:
                self._discover_projects()
                self._rebuild_tabs()
    
    def on_animal_added(self, animal_name: str) -> None:
        """Called when a new animal is created that already has a project assigned."""
        animal = self.app.animals.get(animal_name, {})
        project = (animal.get('project') or '').strip()
        if not project:
            return
        self._history.record_added(project, animal_name)
        is_new_project = project not in self.all_projects
        pt_w = getattr(self.app, 'project_track_widget', None)
        # If this is a new project created via animal dialog, initialize its metadata
        if is_new_project:
            from datetime import datetime
            sig = ''
            mt = getattr(self.app, 'master_track', None)
            if mt:
                sig = str(getattr(mt, 'current_display_name', None) or getattr(mt, 'current_username', '') or '').strip()
            now_str = datetime.now().strftime('%d.%m.%Y %H:%M')
            
            if pt_w and hasattr(pt_w, '_project_record'):
                # Widget is loaded - use its method
                rec = pt_w._project_record(project)
                if not rec.get('created_by') or not rec.get('created_at'):
                    rec['created_by'] = sig
                    rec['created_at'] = now_str
                    rec['modified_by'] = sig
                    rec['modified_at'] = now_str
                    pt_w._save_data()
            else:
                # Widget not loaded yet - update the same backend record.
                project_store = BackendJsonStore(
                    self.app.backend, "projects", "catalog"
                )
                project_data = project_store.load(
                    {'version': 1, 'projects': {}}
                )
                project_data.setdefault('version', 1)
                project_data.setdefault('projects', {})
                if project not in project_data['projects']:
                    project_data['projects'][project] = {}
                rec = project_data['projects'][project]
                if not rec.get('created_by') or not rec.get('created_at'):
                    rec['created_by'] = sig
                    rec['created_at'] = now_str
                    rec['modified_by'] = sig
                    rec['modified_at'] = now_str
                    try:
                        project_store.save(project_data)
                    except Exception as e:
                        logging.warning("Failed to save project metadata: %s", e)
        self._schedule_project_ui_refresh()

    def on_animal_experiment_status_changed(self, animal_name: str,
                                            project: str,
                                            had_in_experiment: bool,
                                            severity: str = None) -> None:
        """Remember experiment history for the active project association."""
        self._history.record_experiment_status(
            project, animal_name, had_in_experiment, severity)
        self._schedule_project_ui_refresh()
    
    def on_animal_removed(self, animal_name):
        """Called by main app when an animal is removed.
        
        Optionally refresh to clean up orphaned projects.
        
        Args:
            animal_name: Name of the removed animal
        """
        self._schedule_project_ui_refresh()
    
    def on_animal_project_removed(self, animal_name: str,
                                   old_project: str, severity: str = None,
                                   previous_in_experiment: bool = None) -> None:
        """Called when the project field is cleared or changed to a different value."""
        if old_project:
            self._history.record_removed(
                old_project.strip(), animal_name, severity, previous_in_experiment)
        self._schedule_project_ui_refresh()
