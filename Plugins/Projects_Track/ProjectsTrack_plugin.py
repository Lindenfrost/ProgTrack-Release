# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: Projects Track sidebar-filter plugin implementation.

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QLabel, QButtonGroup, QFrame, QScrollArea,
                             QSizePolicy)
from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QPainter, QFont


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
    FILENAME = "projects_history.json"

    def __init__(self, plugin_dir: str):
        self.path = os.path.join(plugin_dir, self.FILENAME)
        self._data: dict = {"version": 1, "projects": {}}
        self._load()

    def _load(self):
        if os.path.isfile(self.path):
            try:
                with open(self.path, 'r', encoding='utf-8') as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self._data = d
                    self._data.setdefault("version", 1)
                    self._data.setdefault("projects", {})
            except Exception as exc:
                logging.warning("HistoryStore: failed to load %s: %s", self.path, exc)

    def _save(self):
        try:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
        except Exception as exc:
            logging.warning("HistoryStore: failed to save %s: %s", self.path, exc)

    def _proj(self, name: str) -> dict:
        p = self._data["projects"]
        if name not in p:
            p[name] = {"archived": False, "animals": []}
        return p[name]

    def _today(self) -> str:
        from datetime import date
        return date.today().strftime("%d.%m.%Y")

    def record_added(self, project: str, animal: str):
        proj = self._proj(project)
        for r in proj["animals"]:
            if r["name"] == animal and r["status"] == "active":
                return
        proj["animals"].append({
            "name": animal, "status": "active",
            "date_entered": self._today(),
            "date_left": None, "last_severity": None})
        self._save()

    def record_removed(self, project: str, animal: str, severity: str = None):
        for r in self._proj(project)["animals"]:
            if r["name"] == animal and r["status"] == "active":
                r["status"] = "former"
                r["date_left"] = self._today()
                r["last_severity"] = severity or None
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
            if any(r["name"] == animal for r in pdata.get("animals", [])):
                result.append(pname)
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
        self.cache_file = os.path.join(
            os.path.dirname(__file__), 
            'projects_cache.json'
        )
        
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

        # History store for project membership tracking
        self._history = HistoryStore(os.path.dirname(__file__))

        # Load cached projects, species, and last session state
        self._load_projects()
        self._discover_species()
    
    def _load_projects(self):
        """Load projects from cache or discover from animals, and restore session state."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
                    self.all_projects = cached.get('projects', [])
                    # Restore last active selections
                    self.current_project = cached.get('active_project', 'All') or 'All'
                    self.active_species = cached.get('active_species') or None
                    return
            except Exception:
                pass
        self._discover_projects()
    
    def _discover_projects(self):
        """Scan all animals and collect unique project names."""
        projects = set()
        
        # Check if animals is a dict (it may be a list during early initialization)
        if not isinstance(self.app.animals, dict):
            self.all_projects = []
            self._save_cache()
            return
        
        for animal_name, animal_data in self.app.animals.items():
            # Check for 'project' field in animal record
            project = animal_data.get('project')
            if project and isinstance(project, str) and project.strip():
                projects.add(project.strip())
        
        # Sort alphabetically, case-insensitive
        self.all_projects = sorted(projects, key=str.lower)
        
        # Always save to cache (even if empty)
        self._save_cache()
    
    def _save_cache(self):
        """Save current project list to cache file, preserving existing session-state keys."""
        try:
            existing: dict = {}
            if os.path.exists(self.cache_file):
                try:
                    with open(self.cache_file, 'r', encoding='utf-8') as f:
                        existing = json.load(f)
                except Exception:
                    pass
            existing['projects'] = self.all_projects
            existing['version'] = self.version
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def _save_session_state(self) -> None:
        """Persist active project and species selections to the cache file."""
        try:
            cached: dict = {}
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cached = json.load(f)
            cached['active_project'] = self.current_project
            cached['active_species'] = self.active_species
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cached, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    
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
        self.refresh_btn = QPushButton('🔄')
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
        self._update_container_width()

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
        previous_project = self.current_project
        previous_species = self.active_species

        self._discover_projects()
        self._discover_species()
        self._rebuild_tabs()
        self._rebuild_species_tabs()
        pt_w = getattr(self.app, 'project_track_widget', None)
        if pt_w and hasattr(pt_w, '_refresh_project_list'):
            pt_w._refresh_project_list()

        if previous_project in self.tab_buttons:
            self.tab_buttons[previous_project].setChecked(True)
        elif "All" in self.tab_buttons:
            self.tab_buttons["All"].setChecked(True)

        if previous_species and previous_species in self.species_tab_buttons:
            self.species_tab_buttons[previous_species].setChecked(True)
        elif "All" in self.species_tab_buttons:
            self.species_tab_buttons["All"].setChecked(True)
    
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
        """Scan all animals and collect unique species names."""
        species: set = set()
        animals = getattr(self.app, 'animals', {}) or {}
        if not isinstance(animals, dict):
            self.all_species = []
            return
        for rec in animals.values():
            if isinstance(rec, dict):
                sp = rec.get('species', '')
                if sp and isinstance(sp, str) and sp.strip():
                    species.add(sp.strip())
        self.all_species = sorted(species, key=str.lower)

    def animals_in_scope(self) -> List[str]:
        """Return the list of animal names matching the current project + species filter."""
        animals = getattr(self.app, 'animals', {}) or {}
        result = []
        for name, rec in animals.items():
            if not isinstance(rec, dict):
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
            if project_name and project_name.strip():
                self.app.animals[animal_name]['project'] = project_name.strip()
            else:
                self.app.animals[animal_name].pop('project', None)
            
            # Refresh projects if this is a new project name
            if project_name and project_name.strip() and project_name.strip() not in self.all_projects:
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
        if is_new_project:
            self._discover_projects()
            self._rebuild_tabs()
        # Refresh the Project Track tab widget if it exists
        pt_w = getattr(self.app, 'project_track_widget', None)
        if pt_w and hasattr(pt_w, '_refresh_project_list'):
            pt_w._refresh_project_list()
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
                # Widget not loaded yet - write directly to JSON file
                data_path = os.path.join(os.path.dirname(__file__), 'project_data.json')
                project_data = {'version': 1, 'projects': {}}
                if os.path.isfile(data_path):
                    try:
                        with open(data_path, 'r', encoding='utf-8') as f:
                            d = json.load(f)
                            if isinstance(d, dict):
                                project_data = d
                                project_data.setdefault('version', 1)
                                project_data.setdefault('projects', {})
                    except Exception:
                        pass
                if project not in project_data['projects']:
                    project_data['projects'][project] = {}
                rec = project_data['projects'][project]
                if not rec.get('created_by') or not rec.get('created_at'):
                    rec['created_by'] = sig
                    rec['created_at'] = now_str
                    rec['modified_by'] = sig
                    rec['modified_at'] = now_str
                    try:
                        with open(data_path, 'w', encoding='utf-8') as f:
                            json.dump(project_data, f, indent=2, ensure_ascii=False)
                    except Exception as e:
                        logging.warning("Failed to save project metadata: %s", e)
    
    def on_animal_removed(self, animal_name):
        """Called by main app when an animal is removed.
        
        Optionally refresh to clean up orphaned projects.
        
        Args:
            animal_name: Name of the removed animal
        """
        pass  # Optional: could check if project is now empty
    
    def on_animal_project_removed(self, animal_name: str,
                                   old_project: str, severity: str = None) -> None:
        """Called when the project field is cleared or changed to a different value."""
        if old_project:
            self._history.record_removed(old_project.strip(), animal_name, severity)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._on_refresh_clicked)
