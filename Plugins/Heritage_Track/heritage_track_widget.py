# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track main plugin implementation.

from __future__ import annotations

import math
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QColorDialog,
    QLineEdit,
    QFormLayout,
    QDialogButtonBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QWidget,
    QComboBox,
    QCheckBox,
    QGroupBox,
    QRadioButton,
    QButtonGroup,
    QSpinBox,
    QStackedWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FixedLocator, FuncFormatter, MultipleLocator
import matplotlib.patheffects as path_effects

from Plugins.core.animal_identity import (
    animal_base_name,
    animal_identity_key,
    normalize_birth_date,
    resolve_animal_reference_text,
    split_animal_identity_key,
)
from Plugins.core.animal_roles import ROLE_VALUE_AMME, ROLE_VALUE_SAMENSP, ROLE_VALUE_SPENDER, canonical_role_value
from Plugins.core.ui_icons import apply_icon

from .display_context import DisplayContext, DisplayContextBuilder
from .display_strategies import AllAnimalsStrategy, SelectedAnimalsStrategy
from .ghost_strategies import (
    ArchivedGhostStrategy,
    CompositeGhostStrategy,
    OffspringAndSiblingsGhostStrategy,
    ScopeGhostStrategy,
)
from .engine_cache import PedigreeEngineCache
from .heritage_store import HeritageStore
from .inbreeding import InbreedingCalculator
from .layout_pipeline import (
    LayoutPipeline,
    VERTICAL_LAYOUT_CHRONOLOGICAL,
    VERTICAL_LAYOUT_PARTNER_NORMALIZED,
    compute_chronological_positions,
    parse_complete_birth_date_ordinal,
)
from .pedigree_engine import PedigreeEngine
from .pedigree_router import PedigreeRouter, RoutePlan
from .scope_provider import ProjectsTrackScopeProvider
from .ui_parent_fields import ParentSelector, build_parent_group, extract_parent_values


class NodeEditDialog(QDialog):
    """Node editor for visuals + manual genetic mother/father values."""

    def __init__(
        self,
        parent: QWidget,
        messages: Dict[str, Any],
        animal_name: str,
        fill_color: str,
        genotype: str,
        mother_options: Optional[List[str]] = None,
        father_options: Optional[List[str]] = None,
        mother: str = "",
        father: str = "",
        sex: str = "",
        allow_name_edit: bool = False,
        allow_remove: bool = False,
        animal_species: str = "",
        species_options: Optional[List[str]] = None,
        birth_date: str = "",
        sex_editable: bool = True,
        parent_options_provider: Optional[Callable[[str, str], List[str]]] = None,
    ):
        super().__init__(parent)
        self.messages = messages
        self._animal_name = (animal_name or "").strip()
        self._selected_color = fill_color or ""
        self._allow_name_edit = bool(allow_name_edit)
        self._none_label = messages.get("heritage_track.value.none", "none")
        self._sex = self._normalize_sex(sex)
        self._remove_requested = False
        self._species_options = species_options or []
        self._parent_options_provider = parent_options_provider

        self.setWindowTitle(messages.get("heritage_track.node.edit.title", "Edit Animal Node"))
        self.setModal(True)

        root = QVBoxLayout(self)
        form = QFormLayout()

        if self._allow_name_edit:
            self.name_edit = QLineEdit(self._animal_name)
            form.addRow(messages.get("heritage_track.node.edit.name", "Name:"), self.name_edit)
            self.birth_date_edit = QLineEdit(str(birth_date or "").strip())
            self.birth_date_edit.setPlaceholderText("DD.MM.YYYY")
            form.addRow(
                messages.get("heritage_track.node.edit.birth_date", "Birth date:"),
                self.birth_date_edit,
            )
            # Species dropdown for new animals (only when creating)
            if self._species_options:
                self.species_combo = QComboBox()
                self.species_combo.addItem(messages.get("dialog.species.placeholder", "(Please select)"), "")
                for sp in sorted(self._species_options, key=str.lower):
                    self.species_combo.addItem(sp, sp)
                form.addRow(messages.get("dialog.field.species", "Species:"), self.species_combo)
            else:
                self.species_combo = None
        else:
            self.name_edit = None
            self.birth_date_edit = None
            self.species_combo = None
            form.addRow(
                messages.get("heritage_track.node.edit.animal", "Animal:"),
                QLabel(self._animal_name),
            )

        self.mother_combo = ParentSelector(
            messages,
            mother_options or [],
            mother,
            allow_custom=True,
            parent=self,
        )
        self.father_combo = ParentSelector(
            messages,
            father_options or [],
            father,
            allow_custom=True,
            parent=self,
        )
        if animal_species:
            _sp_hint = messages.get("heritage_track.node.edit.species_filter_hint", "(filtered by species: {sp})").replace("{sp}", animal_species)
            form.addRow(messages.get("heritage_track.node.edit.mother", "Mother:"), self.mother_combo)
            form.addRow(messages.get("heritage_track.node.edit.father", "Father:"), self.father_combo)
            sp_lbl = QLabel(_sp_hint)
            sp_lbl.setStyleSheet("color:grey;font-size:8pt;")
            form.addRow("", sp_lbl)
        else:
            form.addRow(messages.get("heritage_track.node.edit.mother", "Mother:"), self.mother_combo)
            form.addRow(messages.get("heritage_track.node.edit.father", "Father:"), self.father_combo)

        self.sex_combo = QComboBox()
        self.sex_combo.addItem(messages.get("heritage_track.sex.male", "Male"), "male")
        self.sex_combo.addItem(messages.get("heritage_track.sex.female", "Female"), "female")
        self.sex_combo.addItem(messages.get("heritage_track.sex.unknown", "Unknown"), "unknown")
        sex_idx = self.sex_combo.findData(self._sex)
        if sex_idx < 0:
            sex_idx = self.sex_combo.findData("unknown")
        self.sex_combo.setCurrentIndex(sex_idx)
        self.sex_combo.setEnabled(bool(sex_editable))
        if not sex_editable:
            self.sex_combo.setToolTip(
                messages.get(
                    "heritage_track.sex.read_only_core",
                    "Sex is maintained by the main animal record.",
                )
            )
        form.addRow(messages.get("heritage_track.node.edit.sex", "Sex:"), self.sex_combo)

        if self.species_combo is not None and self._parent_options_provider is not None:
            self.species_combo.currentIndexChanged.connect(self._refresh_parent_options)

        self.color_display = QLineEdit(self._selected_color or messages.get("heritage_track.node.edit.fill_color.none", "No fill"))
        self.color_display.setReadOnly(True)

        color_row = QHBoxLayout()
        color_row.addWidget(self.color_display, 1)

        pick_btn = QPushButton(messages.get("heritage_track.node.edit.pick_color", "Pick..."))
        clear_btn = QPushButton(messages.get("heritage_track.node.edit.clear_color", "Clear"))
        color_row.addWidget(pick_btn)
        color_row.addWidget(clear_btn)

        form.addRow(messages.get("heritage_track.node.edit.fill_color", "Fill color:"), color_row)

        self.genotype_edit = QLineEdit(genotype or "")
        form.addRow(messages.get("heritage_track.node.edit.genotype", "Genotype:"), self.genotype_edit)

        root.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        if allow_remove:
            remove_btn = QPushButton(messages.get("heritage_track.node.edit.remove", "Remove animal"))
            self.buttons.addButton(remove_btn, QDialogButtonBox.ButtonRole.DestructiveRole)
            remove_btn.clicked.connect(self._request_remove)

        root.addWidget(self.buttons)

        pick_btn.clicked.connect(self._pick_color)
        clear_btn.clicked.connect(self._clear_color)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

    def _refresh_parent_options(self) -> None:
        if self._parent_options_provider is None:
            return
        species = ""
        if self.species_combo is not None:
            species = str(self.species_combo.currentData() or "").strip()
        self.mother_combo.set_options(self._parent_options_provider("female", species))
        self.father_combo.set_options(self._parent_options_provider("male", species))

    def _request_remove(self) -> None:
        self._remove_requested = True
        self.accept()

    def remove_requested(self) -> bool:
        return self._remove_requested

    def _pick_color(self) -> None:
        selected = QColorDialog.getColor(parent=self)
        if selected.isValid():
            self._selected_color = selected.name()
            self.color_display.setText(self._selected_color)

    def _clear_color(self) -> None:
        self._selected_color = ""
        self.color_display.setText(self.messages.get("heritage_track.node.edit.fill_color.none", "No fill"))

    def _normalize_parent_value(self, value: str) -> str:
        text = (value or "").strip()
        if not text:
            return ""
        lowered = text.lower()
        if lowered in {self._none_label.lower(), "none"}:
            return ""
        return text

    def _normalize_sex(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        if text in {"m", "male", "man", "maschio", "maschile", "männlich", "mannlich", "м", "муж", "мужской", "самец"}:
            return "male"
        if text in {"f", "female", "woman", "femmina", "femminile", "weiblich", "w", "ж", "жен", "женский", "самка"}:
            return "female"
        if text in {"u", "unknown", "unknown sex", "unbekannt", "sconosciuto", "sconosciuta"}:
            return "unknown"
        return ""

    def values(self) -> Dict[str, Any]:
        animal_name = self._animal_name
        if self.name_edit is not None:
            animal_name = self.name_edit.text().strip()
        result = {
            "animal_name": animal_name,
            "fill_color": self._selected_color,
            "genotype": self.genotype_edit.text().strip(),
            "mother": self._normalize_parent_value(self.mother_combo.selected_value()),
            "father": self._normalize_parent_value(self.father_combo.selected_value()),
            "mother_allows_missing": self.mother_combo.allows_missing_value(),
            "father_allows_missing": self.father_combo.allows_missing_value(),
            "sex": self._normalize_sex(self.sex_combo.currentData()),
        }
        if self.species_combo is not None:
            result["species"] = self.species_combo.currentData() or ""
        if self.birth_date_edit is not None:
            result["birth_date"] = self.birth_date_edit.text().strip()
        return result


class CoefficientDialog(QDialog):
    """Displays pairwise phi/r/F coefficients for selected animals."""

    def __init__(self, parent: QDialog, messages: Dict[str, Any], names: List[str], calculator: InbreedingCalculator):
        super().__init__(parent)
        self.messages = messages
        self.names = names
        self.calculator = calculator

        self.setWindowTitle(messages.get("heritage_track.compare.title", "Inbreeding Coefficients"))
        self.resize(820, 520)

        root = QVBoxLayout(self)
        subtitle = QLabel(messages.get("heritage_track.compare.subtitle", "Pairwise coefficients for selected animals"))
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._rebuild_tabs()

    def update_data(self, messages: Dict[str, Any], names: List[str], calculator: InbreedingCalculator) -> None:
        self.messages = messages
        self.names = names
        self.calculator = calculator
        self.setWindowTitle(messages.get("heritage_track.compare.title", "Inbreeding Coefficients"))
        self._rebuild_tabs()

    def _rebuild_tabs(self) -> None:
        idx = self.tabs.currentIndex()
        self.tabs.clear()
        self.tabs.addTab(
            self._build_table(lambda a, b: self.calculator.kinship_phi(a, b)),
            self.messages.get("heritage_track.compare.tab.phi", "Kinship φ"),
        )
        self.tabs.addTab(
            self._build_table(lambda a, b: self.calculator.relationship_r(a, b)),
            self.messages.get("heritage_track.compare.tab.relationship", "Relationship r"),
        )
        if 0 <= idx < self.tabs.count():
            self.tabs.setCurrentIndex(idx)

    def _build_table(self, fn) -> QTableWidget:
        n = len(self.names)
        table = QTableWidget(n, n)
        table.setHorizontalHeaderLabels(self.names)
        table.setVerticalHeaderLabels(self.names)

        for row, a in enumerate(self.names):
            for col, b in enumerate(self.names):
                if row == col:
                    item = QTableWidgetItem("-")
                    item.setBackground(QColor("#e0e0e0"))
                    item.setForeground(QColor("#707070"))
                else:
                    value = fn(a, b)
                    item = QTableWidgetItem(f"{value:.4f}")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, col, item)

        header_h = table.horizontalHeader()
        header_v = table.verticalHeader()
        if header_h is not None:
            header_h.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        if header_v is not None:
            header_v.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

        return table


class HeritageTrackWidget(QWidget):
    """Top-level graph window for Heritage_Track."""

    def __init__(self, plugin: "HeritageTrackPlugin"):
        super().__init__(plugin.app)
        self.plugin = plugin
        self.app = plugin.app
        self.messages = plugin.messages

        self.selected_nodes: Set[str] = set()
        self.node_positions: Dict[str, Tuple[float, float]] = {}
        self.family_positions: Dict[str, Tuple[float, float]] = {}
        self.family_members: Dict[str, Set[str]] = {}
        self.family_routes: Dict[str, Dict[str, List[Tuple[float, float]]]] = {}
        self.node_meta: Dict[str, Dict[str, Any]] = {}
        self._pedigree_router = PedigreeRouter()
        self._route_plan: Optional[RoutePlan] = None
        self.coeff_dialog: Optional[CoefficientDialog] = None
        self._coeff_dialog_pos = None  # type: Optional[Any]  # QPoint
        self._ghost_nodes: Set[str] = set()
        self._chronological_undated_nodes: Set[str] = set()
        self.settings: Dict[str, Any] = self.plugin.get_settings()
        self.collapsed_families: Set[str] = set(self.plugin.store.get_collapsed_families())

        # View/interaction state (FlowTrack-like)
        self.temp_positions: Dict[str, Tuple[float, float]] = {}
        self.current_xlim: Optional[Tuple[float, float]] = None
        self.current_ylim: Optional[Tuple[float, float]] = None
        self.pan_active = False
        self.pan_start: Optional[Tuple[float, float]] = None
        self._pan_background: Optional[Any] = None
        self.drag_active = False
        self.drag_node: Optional[str] = None
        self.drag_group_nodes: Set[str] = set()
        self.drag_offset: Tuple[float, float] = (0.0, 0.0)
        self.click_start_pos: Optional[Tuple[float, float]] = None
        self.is_dragging = False
        self.drag_threshold = 0.05
        self._drag_background: Optional[Any] = None
        self._drag_artist_map: Dict[str, Tuple[Any, ...]] = {}
        self.all_animals_mode = True
        self._force_relayout = False

        # Double-click detection state (timer-based for reliability across backends)
        self._last_click_time: float = 0.0
        self._last_click_node: Optional[str] = None
        self._last_empty_click_time: float = 0.0
        self._double_click_threshold_ms: float = 500.0

        # Pending selection for double-click detection (delayed to avoid refresh interfering with 2nd click)
        self._pending_selection: Optional[str] = None
        self._pending_selection_timer: Optional[QTimer] = None

        # Generation limit (max ancestor depth in no-selection mode)
        session_max = None
        if hasattr(self.app, 'master_track') and self.app.master_track:
            try:
                sess = self.app.master_track.session_mgr
                if self.app.master_track.current_username:
                    sess_data = sess.load(self.app.master_track.current_username)
                    session_max = sess_data.get('max_parent_generations')
            except Exception:
                pass
        self._max_generations: int = int(session_max) if session_max is not None else 3

        self.figure = Figure(figsize=(11, 7))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setToolTip(
            self.messages.get("heritage_track.tooltip.double_click_edit", "Double-click a node to edit")
        )
        self.ax = self.figure.add_subplot(111)
        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)

        self._hover_annotation = self.ax.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox={"boxstyle": "round", "fc": "#fff9db", "ec": "black", "alpha": 0.95},
            arrowprops={"arrowstyle": "->", "color": "black"},
        )
        self._hover_annotation.set_visible(False)

        self._build_ui()
        self._connect_canvas_events()

        # Register with ProjectsTrack so scope changes (project/species) refresh Heritage
        pt = getattr(self.app, 'projects_plugin', None)
        if pt is not None and hasattr(pt, 'add_scope_changed_callback'):
            pt.add_scope_changed_callback(lambda: self.refresh_graph(keep_view=False))

    def _build_ui(self) -> None:
        self.setWindowTitle(self.messages.get("heritage_track.title", "Heritage Track"))
        self.resize(1240, 820)

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.title_label = QLabel()
        self.subtitle_label = QLabel()

        left = QVBoxLayout()
        left.addWidget(self.title_label)
        left.addWidget(self.subtitle_label)
        top.addLayout(left, 1)

        # Generation limit control
        gen_label = QLabel(self.messages.get("heritage_track.label.gen_limit", "Ancestors:"))
        self.gen_dec_btn = QPushButton()
        apply_icon(self.gen_dec_btn, "control.decrement", fallback="Decrease")
        self.gen_dec_btn.setIconSize(QSize(27, 27))
        self.gen_dec_btn.setFixedWidth(28)
        # These controls are icon-only symbols; keep the hit area but remove
        # the platform button chrome/background so the arrows sit cleanly in
        # the toolbar.
        self._configure_symbol_button(self.gen_dec_btn)
        self.gen_spin = QSpinBox()
        self.gen_spin.setMinimum(1)
        self.gen_spin.setMaximum(999)
        self.gen_spin.setValue(self._max_generations)
        self.gen_spin.setFixedWidth(48)
        self.gen_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.gen_spin.setToolTip(
            self.messages.get("heritage_track.tooltip.gen_limit",
                              "Max ancestor generations shown in no-selection mode"))
        self.gen_inc_btn = QPushButton()
        apply_icon(self.gen_inc_btn, "control.increment", fallback="Increase")
        self.gen_inc_btn.setIconSize(QSize(27, 27))
        self.gen_inc_btn.setFixedWidth(28)
        self._configure_symbol_button(self.gen_inc_btn)
        self.gen_dec_btn.clicked.connect(lambda: self.gen_spin.setValue(
            max(1, self.gen_spin.value() - 1)))
        self.gen_inc_btn.clicked.connect(lambda: self.gen_spin.setValue(
            min(self.gen_spin.maximum(), self.gen_spin.value() + 1)))
        self.gen_spin.valueChanged.connect(self._on_gen_limit_changed)
        top.addWidget(gen_label)
        top.addWidget(self.gen_dec_btn)
        top.addWidget(self.gen_spin)
        top.addWidget(self.gen_inc_btn)

        self.refresh_btn = QPushButton()
        apply_icon(self.refresh_btn, "action.refresh", fallback="Refresh")
        self.refresh_btn.setIconSize(QSize(30, 30))
        self.add_placeholder_btn = QPushButton()
        self.settings_btn = QPushButton()
        apply_icon(self.add_placeholder_btn, "heritage.placeholder_animal", fallback="Placeholder animal")
        apply_icon(self.settings_btn, "action.settings", fallback="Settings")
        self.add_placeholder_btn.setIconSize(QSize(30, 30))
        self.settings_btn.setIconSize(QSize(30, 30))

        self.add_placeholder_btn.setToolTip(
            self.messages.get("heritage_track.tooltip.add_placeholder", "Placeholder animal")
        )

        top.addWidget(self.refresh_btn)
        top.addWidget(self.add_placeholder_btn)
        top.addWidget(self.settings_btn)

        root.addLayout(top)

        # Create stacked widget for splash screen and canvas
        self.stack = QStackedWidget()

        # Splash widget (index 0)
        self.splash_widget = QWidget()
        splash_layout = QVBoxLayout(self.splash_widget)
        splash_layout.addStretch(1)

        # Add disclaimer/footer text above splash image
        disclaimer_label = QLabel(
            self.messages.get("footer.rights", "ProgTrack").format(year=datetime.now().year)
        )
        disclaimer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splash_layout.addWidget(disclaimer_label, alignment=Qt.AlignmentFlag.AlignCenter)

        # Add spacing between disclaimer and image
        spacer = QWidget()
        spacer.setFixedHeight(20)
        splash_layout.addWidget(spacer)

        img_label = QLabel()
        pix_path = Path("icons/Splash.png")
        if pix_path.exists():
            pix = QPixmap(str(pix_path))
            pix = pix.scaled(800, 800, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            img_label.setPixmap(pix)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splash_layout.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)
        splash_layout.addStretch(1)
        self.stack.addWidget(self.splash_widget)

        # Canvas widget (index 1)
        self.stack.addWidget(self.canvas)

        root.addWidget(self.stack, 1)

        self.status_label = QLabel("")
        root.addWidget(self.status_label)

        self.settings_btn.clicked.connect(self._open_settings_dialog)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        self.add_placeholder_btn.clicked.connect(self._open_create_animal_dialog)

        self._refresh_texts_only()

    @staticmethod
    def _configure_symbol_button(button: QPushButton) -> None:
        """Render an icon-only toolbar symbol without a button background."""
        button.setFlat(True)
        button.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0px; }"
            "QPushButton:hover, QPushButton:pressed { background: transparent; }"
        )

    def _on_gen_limit_changed(self, value: int) -> None:
        """Persist the new generation limit (refresh on button click only)."""
        self._max_generations = value
        # Persist to session if Master_Track is active
        if hasattr(self.app, 'master_track') and self.app.master_track:
            try:
                uname = self.app.master_track.current_username
                if uname:
                    sess = self.app.master_track.session_mgr
                    data = sess.load(uname)
                    data['max_parent_generations'] = value
                    sess.save(uname, data)
            except Exception:
                pass
        # Mark for relayout on next refresh; do not auto-refresh
        self._force_relayout = True

    def _get_no_selection_seed_set(self, engine: "PedigreeEngine") -> Set[str]:
        """Compute the display set for no-selection (all-animals) mode.

        Strategy:
        1. Include ALL animals (alive and dead), optionally filtered by project/species scope.
        2. Assign generation levels: 0 = founders (oldest), max = youngest offshoots.
        3. Promote isolated nodes (no parents AND no children) to max_level so they
           appear alongside the youngest generation.
        4. Apply spinbox cutoff: only show animals within _max_generations ancestor
           levels of the youngest generation, i.e. level >= (max_level - _max_generations).
        """
        all_nodes = engine.all_nodes

        # Determine scope filter from Project_Track / session
        scope_animals: Optional[Set[str]] = None
        pt = getattr(self.app, 'projects_plugin', None)
        if pt is not None:
            current_project = getattr(pt, 'current_project', 'All')
            active_species  = getattr(pt, 'active_species', None)
            animals = getattr(self.app, 'animals', {}) or {}
            archived = getattr(self.app, 'archived', {}) or {}
            project_filter  = current_project and current_project != 'All'
            species_filter  = bool(active_species)
            # Build scope when at least one filter is active
            if project_filter or species_filter:
                scope_animals = set()
                # Include both active and archived animals matching filter
                for src in (animals, archived):
                    for name, rec in src.items():
                        if not isinstance(rec, dict):
                            continue
                        if project_filter and rec.get('project') != current_project:
                            continue
                        if species_filter and rec.get('species', '') != active_species:
                            continue
                        scope_animals.add(name)

        # When NO filter is active (All species + All projects), return empty set.
        # If at least one filter (project or species) is selected, proceed with filtering.
        if not project_filter and not species_filter:
            return set()

        # Collect ALL nodes in scope (alive and dead)
        candidates: Set[str] = set()
        for node in all_nodes:
            if scope_animals is not None and node not in scope_animals:
                continue
            candidates.add(node)

        if not candidates:
            return set(all_nodes)

        # Compute generation levels: 0 = oldest founders, max = youngest offshoots
        levels = engine.compute_levels(candidates)
        if not levels:
            return candidates

        max_level = max(levels.values(), default=0)

        # Helper to get siblings (share at least one parent)
        def _get_siblings(n: str) -> Set[str]:
            pvals = engine.child_to_parents.get(n, {})
            parents = {v for k, v in pvals.items()
                       if k in ('egg_donor', 'sperm_donor') and v}
            siblings: Set[str] = set()
            for p in parents:
                siblings.update(engine.parent_to_children.get(p, set()))
            siblings.discard(n)
            return siblings & candidates

        # Build parent -> children map from candidates for quick lookup
        parent_to_children_local: Dict[str, Set[str]] = defaultdict(set)
        for c in candidates:
            pvals = engine.child_to_parents.get(c, {})
            for k, v in pvals.items():
                if k in ('egg_donor', 'sperm_donor') and v:
                    parent_to_children_local[v].add(c)

        def _has_children_in_candidates(n: str) -> bool:
            return bool(parent_to_children_local.get(n, set()) & candidates)

        # Multi-pass: keep adjusting until stable, max 10 iterations
        for _ in range(10):
            changed = False
            for node in list(candidates):
                if _has_children_in_candidates(node):
                    continue  # Nodes with children keep their level

                siblings = _get_siblings(node)
                if not siblings:
                    continue

                # Find max level among all siblings (with or without children)
                # that have been assigned a level already
                sibling_levels = [levels.get(s, 0) for s in siblings]
                if not sibling_levels:
                    continue

                max_sibling_level = max(sibling_levels)
                current_level = levels.get(node, 0)

                # If any sibling is at a higher level, move to that level
                if max_sibling_level > current_level:
                    levels[node] = max_sibling_level
                    changed = True

            if not changed:
                break

        # Spinbox N = number of ancestor generations to show above the youngest.
        # Show only nodes at compute_level >= (max_level - N).
        cutoff = max(0, max_level - self._max_generations)
        return {node for node, lvl in levels.items() if lvl >= cutoff}

    def _connect_canvas_events(self) -> None:
        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("resize_event", self._on_resize)

    def update_language(self, messages: Dict[str, Any]) -> None:
        self.messages = messages
        self._refresh_texts_only()
        self.refresh_graph()

    def _refresh_texts_only(self) -> None:
        self.setWindowTitle(self.messages.get("heritage_track.title", "Heritage Track"))
        self.title_label.setText(f"<b>{self.messages.get('heritage_track.title', 'Heritage Track')}</b>")
        self.subtitle_label.setText(self.messages.get("heritage_track.subtitle", "Genealogy and inbreeding view"))
        self.settings_btn.setToolTip(self.messages.get("heritage_track.button.settings", "Settings"))
        self.refresh_btn.setToolTip(self.messages.get("heritage_track.button.refresh", "Refresh"))

    def _none_label(self) -> str:
        return self.messages.get("heritage_track.value.none", "none")

    def _draw_grid(self) -> None:
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        grid_spacing = 0.5

        x_start = math.floor(xlim[0] / grid_spacing) * grid_spacing
        x_end = math.ceil(xlim[1] / grid_spacing) * grid_spacing
        y_start = math.floor(ylim[0] / grid_spacing) * grid_spacing
        y_end = math.ceil(ylim[1] / grid_spacing) * grid_spacing

        x = x_start
        while x <= x_end:
            self.ax.axvline(x, color="#d9d9d9", linewidth=0.6, alpha=0.45, zorder=0)
            x += grid_spacing

        y = y_start
        while y <= y_end:
            self.ax.axhline(y, color="#d9d9d9", linewidth=0.6, alpha=0.45, zorder=0)
            y += grid_spacing

    def _configure_chronological_axis(self) -> None:
        """Show an adaptive calendar Y-axis for the chronological layout."""
        low, high = sorted(self.ax.get_ylim())
        span = max(0.1, high - low)
        year_step = max(1, int(math.ceil(span / 18.0)))
        if span <= 3.0:
            month_step = 1
        elif span <= 8.0:
            month_step = 2
        elif span <= 15.0:
            month_step = 3
        elif span <= 30.0:
            month_step = 6
        else:
            month_step = 12

        self.ax.yaxis.set_major_locator(MultipleLocator(float(year_step)))
        self.ax.yaxis.set_major_formatter(
            FuncFormatter(
                lambda value, _pos: str(int(round(value)))
                if abs(value - round(value)) < 1e-6
                else ""
            )
        )
        month_ticks: List[float] = []
        first_year = int(math.floor(low)) - 1
        last_year = int(math.ceil(high)) + 1
        month_indices = (6,) if month_step == 12 else range(0, 12, month_step)
        for year in range(first_year, last_year + 1):
            for month_index in month_indices:
                tick = year + (month_index / 12.0)
                if low <= tick <= high and abs(tick - round(tick)) > 1e-6:
                    month_ticks.append(tick)
        self.ax.yaxis.set_minor_locator(FixedLocator(month_ticks))
        self.ax.tick_params(axis="y", which="major", length=6, labelsize=8)
        self.ax.tick_params(axis="y", which="minor", length=2.5)
        self.ax.tick_params(axis="x", bottom=False, labelbottom=False)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.ax.spines["bottom"].set_visible(False)
        self.ax.spines["left"].set_visible(True)
        self.ax.set_ylabel(
            self.messages.get("heritage_track.axis.birth_date", "Birth date"),
            fontsize=9,
        )
        self.ax.grid(axis="y", which="major", color="#d4d4d4", linewidth=0.65, zorder=0)
        self.ax.grid(axis="y", which="minor", color="#ededed", linewidth=0.35, zorder=0)
        self.figure.subplots_adjust(left=0.075, right=0.995, top=0.995, bottom=0.02)

    def _snap_to_grid(self, x: float, y: float) -> Tuple[float, float]:
        if not self.settings.get("snap_to_grid", False):
            return x, y
        spacing = 0.5
        return round(x / spacing) * spacing, round(y / spacing) * spacing

    def _open_settings_dialog(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get("heritage_track.settings.title", "Heritage Track Settings"))

        layout = QVBoxLayout(dlg)

        show_grid_check = QCheckBox(self.messages.get("heritage_track.settings.show_grid", "Show grid"))
        show_grid_check.setChecked(self.settings.get("show_grid", False))
        layout.addWidget(show_grid_check)

        snap_grid_check = QCheckBox(self.messages.get("heritage_track.settings.snap_to_grid", "Snap to grid"))
        snap_grid_check.setChecked(self.settings.get("snap_to_grid", False))
        layout.addWidget(snap_grid_check)

        show_heritage_only_check = QCheckBox(
            self.messages.get("heritage_track.settings.show_heritage_only", "Show Heritage-only animals")
        )
        show_heritage_only_check.setChecked(self.settings.get("show_heritage_only", True))
        layout.addWidget(show_heritage_only_check)

        show_legend_check = QCheckBox(self.messages.get("heritage_track.settings.show_legend", "Show legend"))
        show_legend_check.setChecked(self.settings.get("show_legend", True))
        layout.addWidget(show_legend_check)

        exclude_archived_check = QCheckBox(
            self.messages.get("heritage_track.settings.exclude_archived", "Exclude archived animals")
        )
        exclude_archived_check.setChecked(self.settings.get("exclude_archived", False))
        layout.addWidget(exclude_archived_check)

        vertical_group = QGroupBox(
            self.messages.get("heritage_track.settings.vertical_placement", "Vertical placement")
        )
        vertical_box = QVBoxLayout(vertical_group)
        vertical_buttons = QButtonGroup(vertical_group)
        partner_normalized_radio = QRadioButton(
            self.messages.get(
                "heritage_track.settings.vertical_placement.partner_normalized",
                "Partner-normalized pedigree",
            )
        )
        partner_normalized_radio.setToolTip(
            self.messages.get(
                "heritage_track.settings.vertical_placement.partner_normalized.tooltip",
                "Align partners and pedigree generations for a compact family tree.",
            )
        )
        chronological_radio = QRadioButton(
            self.messages.get(
                "heritage_track.settings.vertical_placement.chronological",
                "Chronological birth-date axis",
            )
        )
        chronological_radio.setToolTip(
            self.messages.get(
                "heritage_track.settings.vertical_placement.chronological.tooltip",
                "Place dated animals at their birth month and show a calendar Y-axis.",
            )
        )
        vertical_buttons.addButton(partner_normalized_radio)
        vertical_buttons.addButton(chronological_radio)
        vertical_box.addWidget(partner_normalized_radio)
        vertical_box.addWidget(chronological_radio)
        current_vertical_mode = self.settings.get(
            "vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED
        )
        chronological_radio.setChecked(current_vertical_mode == VERTICAL_LAYOUT_CHRONOLOGICAL)
        partner_normalized_radio.setChecked(not chronological_radio.isChecked())
        layout.addWidget(vertical_group)

        detail_group = QGroupBox(
            self.messages.get("heritage_track.settings.animal_label_detail", "Animal label detail")
        )
        detail_box = QVBoxLayout(detail_group)
        detail_buttons = QButtonGroup(detail_group)
        detail_radios: Dict[str, QRadioButton] = {}
        for value, fallback in (
            ("nothing", "Nothing"),
            ("inbreeding_f", "Inbreeding F"),
            ("birth_date", "Birth date"),
            ("animal_id", "Animal ID"),
        ):
            radio = QRadioButton(
                self.messages.get(f"heritage_track.settings.animal_label_detail.{value}", fallback)
            )
            detail_buttons.addButton(radio)
            detail_box.addWidget(radio)
            detail_radios[value] = radio
        current_detail = str(self.settings.get("animal_label_detail", "inbreeding_f"))
        detail_radios.get(current_detail, detail_radios["inbreeding_f"]).setChecked(True)
        layout.addWidget(detail_group)

        self.canvas.setToolTip(self.messages.get("heritage_track.tooltip.double_click_edit", "Double-click a node to edit"))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        self.settings["show_grid"] = show_grid_check.isChecked()
        self.settings["snap_to_grid"] = snap_grid_check.isChecked()
        self.settings["show_heritage_only"] = show_heritage_only_check.isChecked()
        self.settings["show_legend"] = show_legend_check.isChecked()
        self.settings["exclude_archived"] = exclude_archived_check.isChecked()
        self.settings["vertical_layout_mode"] = (
            VERTICAL_LAYOUT_CHRONOLOGICAL
            if chronological_radio.isChecked()
            else VERTICAL_LAYOUT_PARTNER_NORMALIZED
        )
        self.settings["animal_label_detail"] = next(
            (value for value, radio in detail_radios.items() if radio.isChecked()),
            "inbreeding_f",
        )
        self.plugin.set_settings(self.settings)
        placement_changed = self.settings["vertical_layout_mode"] != current_vertical_mode
        if placement_changed:
            self._force_relayout = True
            self.current_xlim = None
            self.current_ylim = None
        self.refresh_graph(keep_view=not placement_changed)

    def _clear_selection(self) -> None:
        self.selected_nodes.clear()
        self._show_coefficients_dialog()
        self.refresh_graph(keep_view=True)

    def _clear_filter_selection(self) -> None:
        """Clear filter-based selection and start fresh (show selected animals mode).

        When clicking empty space, this switches from 'all animals by filter' mode
        to 'selected animals' mode (initially empty, ready for multi-select).
        """
        if not hasattr(self.app, 'selected_animals'):
            return

        # Switch to selected-animals mode by clearing the list
        # This removes filter-based "all animals" display
        self.app.selected_animals.clear()

        # Also clear heritage-only selections
        if hasattr(self.app, '_selected_heritage_only'):
            self.app._selected_heritage_only.clear()

        # Update the main list UI - clear all selections
        if hasattr(self.app, 'lst') and self.app.lst is not None:
            self.app.lst.clearSelection()

        # Trigger the app's selection update handler
        if hasattr(self.app, '_on_select'):
            try:
                self.app._on_select()
            except Exception:
                pass

        # Force relayout when switching modes
        self._force_relayout = True

        # Refresh graph - will now show "selected animals" mode (empty)
        self.refresh_graph(keep_view=True)

    def _add_animal_to_selection(self, animal_name: str) -> None:
        """Add the given animal to the current selection (multi-select).

        This adds animals to the selection list without limit.
        If the animal was a ghost node, it becomes a normal (solid) node.
        Heritage-only animals are also added to the selection.
        Archived animals are included when exclude_archived is off.
        """
        if not hasattr(self.app, 'selected_animals'):
            return

        # Check if animal exists in main ProgTrack data (active or archived)
        in_active_list = (
            hasattr(self.app, 'animals') and
            animal_name in self.app.animals
        )
        archived = getattr(self.app, "archived", {}) or {}
        in_archived = isinstance(archived, dict) and animal_name in archived
        in_main_list = in_active_list or in_archived

        # Check if this is a heritage-only animal
        is_heritage_only = self.plugin.is_heritage_only(animal_name)

        # For heritage-only animals, add to both lists for compatibility
        if is_heritage_only:
            # Track in heritage-only list for internal use
            if not hasattr(self.app, '_selected_heritage_only'):
                self.app._selected_heritage_only = []
            if animal_name not in self.app._selected_heritage_only:
                self.app._selected_heritage_only.append(animal_name)
            # Also add to main selected_animals so the rest of the system sees it
            if animal_name not in self.app.selected_animals:
                self.app.selected_animals.append(animal_name)
        else:
            # Skip if already selected (avoid redundant work)
            if animal_name in self.app.selected_animals:
                return
            # Add to selection (no limit on number of animals)
            self.app.selected_animals.append(animal_name)

        # Update the main list UI if accessible and animal exists there
        if in_main_list and hasattr(self.app, 'lst') and self.app.lst is not None:
            self.app.lst.blockSignals(True)
            try:
                # Find and select the item
                for i in range(self.app.lst.count()):
                    item = self.app.lst.item(i)
                    if not item:
                        continue
                    user_data = item.data(Qt.ItemDataRole.UserRole)
                    if user_data == animal_name:
                        item.setSelected(True)
                        self.app.lst.setCurrentItem(item)
                        break
            finally:
                self.app.lst.blockSignals(False)

        # If animal was a ghost, remove from ghost set (make it normal/solid)
        if hasattr(self, '_ghost_nodes') and animal_name in self._ghost_nodes:
            self._ghost_nodes.discard(animal_name)

        # Trigger the app's selection update handler (only for main-list animals)
        # Heritage-only animals are not in the main list, so _on_select would clear them
        if not is_heritage_only and hasattr(self.app, '_on_select'):
            try:
                self.app._on_select()
            except Exception:
                pass

        # Clear previous positions cache to force proper layout of new animals
        # This ensures placement rules distribute new animals properly
        self._force_relayout = True

        # Refresh graph to show the updated selection
        self.refresh_graph(keep_view=True)

    def _find_icons_dir(self) -> Path:
        """Find the icons directory, same logic as ProgTrack main."""
        # Start from the plugin directory
        base = Path(__file__).resolve().parent
        # Go up to ProgTrack root and look for icons
        for parent in [base] + list(base.parents):
            icons_path = parent / "icons"
            if icons_path.is_dir():
                return icons_path
        # Fallback to parent directory
        return base.parent / "icons"

    def _on_refresh_clicked(self) -> None:
        """Handle refresh button click with confirmation to reset positions."""
        # Check if there are any saved positions
        saved_positions = self.plugin.store.get_node_positions()
        
        if saved_positions:
            # Ask user if they want to reset positions
            # Create custom message box with warning.png icon
            msg = QMessageBox(self)
            msg.setWindowTitle(self.messages.get("heritage_track.refresh.title", "Refresh Layout"))
            msg.setText(self.messages.get(
                "heritage_track.refresh.confirm",
                "Reset all manually positioned animals to automatic layout?",
            ))
            msg.setIcon(QMessageBox.Icon.NoIcon)
            
            # Load custom warning icon from icons directory
            icon_dir = self._find_icons_dir()
            icon_path = icon_dir / "warning.png"
            pix = QPixmap(str(icon_path))
            if not pix.isNull():
                msg.setIconPixmap(pix)
            else:
                # Fallback to default warning icon
                msg.setIcon(QMessageBox.Icon.Warning)
            
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.No)
            
            confirm = msg.exec()
            if confirm != QMessageBox.StandardButton.Yes:
                return
        
        # Normal refresh without clearing positions
        self._force_relayout = True
        self.selected_nodes.clear()
        self.temp_positions.clear()
        self.current_xlim = None
        self.current_ylim = None
        self._show_coefficients_dialog()
        self.refresh_graph()
    
    def _cleanup_stale_positions(self, current_nodes: Set[str]) -> None:
        """Remove saved positions for animals that no longer exist in the graph."""
        saved_positions = self.plugin.store.get_node_positions()
        for node in list(saved_positions.keys()):
            if node not in current_nodes:
                self.plugin.store.remove_node_position(node)
    
    def _clear_all_saved_positions(self) -> None:
        """Clear all saved animal positions (for reset functionality)."""
        engine = self.plugin.build_engine()
        selected_animals = list(getattr(self.app, "selected_animals", []) or [])
        display_nodes = engine.get_display_nodes(selected_animals)
        
        for node in display_nodes:
            self.plugin.store.remove_node_position(node)
        
        self.temp_positions.clear()
        self._force_relayout = True
        self.refresh_graph()

    def _can(self, action: str) -> bool:
        fn = getattr(self.app, '_master_can', None)
        return fn(action) if fn else True

    def _deny(self) -> None:
        if hasattr(self.app, '_show_permission_denied'):
            self.app._show_permission_denied()

    def _create_heritage_animal(self) -> None:
        if not self._can('heritage.edit_links'):
            self._deny()
            return
        engine = self.plugin.build_engine()
        mother_options, father_options = self._get_parent_dropdown_options(engine)

        dlg = NodeEditDialog(
            self,
            self.messages,
            animal_name="",
            fill_color="",
            genotype="",
            mother_options=mother_options,
            father_options=father_options,
            mother="",
            father="",
            sex="",
            allow_name_edit=True,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        values = dlg.values()
        name = values.get("animal_name", "").strip()
        if not name:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("heritage_track.error.name_required", "Animal name is required."),
            )
            return

        app_animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        if name in app_animals:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get(
                    "heritage_track.error.name_exists_core",
                    "This animal already exists in the main ProgTrack database.",
                ),
            )
            return

        if name in self.plugin.store.get_all_entries():
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get(
                    "heritage_track.error.name_exists_heritage",
                    "This animal already exists in heritage data.",
                ),
            )
            return

        validation_error = self._validate_parent_selection(
            name,
            values.get("mother", ""),
            values.get("father", ""),
            engine=engine,
            target_species=values.get("species", ""),
            explicit_missing_parents={
                values.get(field, "")
                for field, marker in (
                    ("mother", "mother_allows_missing"),
                    ("father", "father_allows_missing"),
                )
                if values.get(marker)
            },
        )
        if validation_error:
            QMessageBox.warning(self, self.messages.get("error.title", "Error"), validation_error)
            return

        created = self.plugin.create_heritage_only_animal(
            name=name,
            mother=values.get("mother", ""),
            father=values.get("father", ""),
            genotype=values.get("genotype", ""),
            fill_color=values.get("fill_color", ""),
            sex=values.get("sex", ""),
            birth_date=values.get("birth_date", ""),
            explicit_custom_parents={
                values.get(field, "")
                for field, marker in (
                    ("mother", "mother_allows_missing"),
                    ("father", "father_allows_missing"),
                )
                if values.get(marker)
            },
        )
        if not created:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("heritage_track.error.create_failed", "Failed to create Heritage animal."),
            )
            return

        self.refresh_graph()

    def _valid_fill(self, value: str) -> str:
        color = (value or "").strip()
        if not color:
            return "white"
        qcolor = QColor(color)
        return color if qcolor.isValid() else "white"

    def _effective_sex_for_node(self, node: str) -> str:
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        archived = getattr(self.app, "archived", {}) or {}
        if isinstance(archived, dict):
            record = animals.get(node) or archived.get(node, {})
        else:
            record = animals.get(node, {})
        return self.plugin.get_effective_sex(node, record if isinstance(record, dict) else None)

    def _get_parent_dropdown_options(
        self,
        engine: Optional[PedigreeEngine] = None,
        target_species: str = "",
        exclude_node: str = "",
    ) -> Tuple[List[str], List[str]]:
        # ``engine`` remains in the signature for compatibility with callers;
        # candidates deliberately come from actual records, not unresolved
        # graph labels or archived/dead animals.
        _ = engine
        return (
            self.plugin.parent_candidate_options("female", target_species, exclude_node),
            self.plugin.parent_candidate_options("male", target_species, exclude_node),
        )

    def _parent_map_has_cycle(self, parent_map: Dict[str, Tuple[Optional[str], Optional[str]]]) -> bool:
        visit_state: Dict[str, int] = {}

        def _visit(node: str) -> bool:
            state = visit_state.get(node, 0)
            if state == 1:
                return True
            if state == 2:
                return False

            visit_state[node] = 1
            mother, father = parent_map.get(node, (None, None))
            for parent in (mother, father):
                p = str(parent or "").strip()
                if not p:
                    continue
                if _visit(p):
                    return True
            visit_state[node] = 2
            return False

        all_nodes: Set[str] = set(parent_map.keys())
        for mother, father in parent_map.values():
            if mother:
                all_nodes.add(str(mother))
            if father:
                all_nodes.add(str(father))

        for node in all_nodes:
            if _visit(node):
                return True
        return False

    def _validate_parent_selection(
        self,
        animal_name: str,
        mother: str,
        father: str,
        engine: Optional[PedigreeEngine] = None,
        target_species: str = "",
        explicit_missing_parents: Optional[Set[str]] = None,
    ) -> Optional[str]:
        key = (animal_name or "").strip()
        raw_mother = (mother or "").strip()
        raw_father = (father or "").strip()
        allowed_missing = {
            str(value or "").strip()
            for value in (explicit_missing_parents or set())
            if str(value or "").strip()
        }
        if not key:
            return None

        mother_name, mother_status = self.plugin.resolve_parent_reference(raw_mother, target_species)
        father_name, father_status = self.plugin.resolve_parent_reference(raw_father, target_species)
        if mother_status == "ambiguous" or father_status == "ambiguous":
            return self.messages.get(
                "heritage_track.error.parent_ambiguous",
                "Parent name is ambiguous. Please select the full animal identity.",
            )
        if (
            (raw_mother and mother_status == "missing" and raw_mother not in allowed_missing)
            or (raw_father and father_status == "missing" and raw_father not in allowed_missing)
        ):
            return self.messages.get(
                "heritage_track.error.parent_not_selected",
                "Select an existing animal or use Add Heritage-only ancestor.",
            )

        if mother_name and mother_name == key:
            return self.messages.get(
                "heritage_track.error.parent_self",
                "An animal cannot be set as its own parent.",
            )
        if father_name and father_name == key:
            return self.messages.get(
                "heritage_track.error.parent_self",
                "An animal cannot be set as its own parent.",
            )
        if mother_name and father_name and mother_name == father_name:
            return self.messages.get(
                "heritage_track.error.parent_same",
                "Mother and father must be different animals.",
            )

        # Existing parent records must satisfy both the biological slot and
        # the child's species filter.  Explicit custom ancestors are checked
        # after they are materialized as Heritage-only records.
        if mother_name:
            mother_exists = self.plugin._parent_exists_in_system(mother_name)
            if mother_exists and self._effective_sex_for_node(mother_name) != "female":
                return self.messages.get(
                    "heritage_track.error.mother_not_female",
                    "Selected mother must be female.",
                )
        if father_name:
            father_exists = self.plugin._parent_exists_in_system(father_name)
            if father_exists and self._effective_sex_for_node(father_name) != "male":
                return self.messages.get(
                    "heritage_track.error.father_not_male",
                    "Selected father must be male.",
                )

        if target_species:
            for parent_name in (mother_name, father_name):
                if not parent_name or not self.plugin._parent_exists_in_system(parent_name):
                    continue
                parent_record = self.plugin._core_record(parent_name)
                if parent_record is None:
                    candidate = self.plugin.store.get_all_entries().get(parent_name, {})
                    parent_record = candidate if isinstance(candidate, dict) else {}
                parent_species = str((parent_record or {}).get("species", "") or "").strip()
                if parent_species != target_species:
                    return self.messages.get(
                        "heritage_track.error.parent_wrong_species",
                        "Selected parents must have the same species as the animal.",
                    )

        local_engine = engine or self.plugin.build_engine()
        parent_map = local_engine.get_genetic_parent_map()

        parent_to_children: Dict[str, Set[str]] = defaultdict(set)
        for child, (cur_mother, cur_father) in parent_map.items():
            for parent in (cur_mother, cur_father):
                parent_name = str(parent or "").strip()
                if parent_name:
                    parent_to_children[parent_name].add(child)

        descendants: Set[str] = set()
        seen: Set[str] = {key}
        stack: List[str] = [key]
        while stack:
            current = stack.pop()
            for child in parent_to_children.get(current, set()):
                if child in seen:
                    continue
                seen.add(child)
                descendants.add(child)
                stack.append(child)

        if (mother_name and mother_name in descendants) or (father_name and father_name in descendants):
            return self.messages.get(
                "heritage_track.error.circular_parentage",
                "Invalid parent assignment: it would create a circular pedigree.",
            )
        return None

    def _apply_aspect_fill(
        self,
        xlim: Tuple[float, float],
        ylim: Tuple[float, float],
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        canvas_width, canvas_height = self.canvas.get_width_height()
        if canvas_width <= 0 or canvas_height <= 0:
            return xlim, ylim

        x_range = xlim[1] - xlim[0]
        y_range = ylim[1] - ylim[0]
        if x_range <= 0 or y_range <= 0:
            return xlim, ylim

        fig_ratio = canvas_width / canvas_height
        data_ratio = x_range / y_range
        x_center = (xlim[0] + xlim[1]) / 2.0
        y_center = (ylim[0] + ylim[1]) / 2.0

        if data_ratio > fig_ratio:
            new_y_range = x_range / fig_ratio
            return xlim, (y_center - new_y_range / 2.0, y_center + new_y_range / 2.0)

        new_x_range = y_range * fig_ratio
        return (x_center - new_x_range / 2.0, x_center + new_x_range / 2.0), ylim

    def _order_partner_component(
        self,
        members: List[str],
        adjacency: Dict[str, Set[str]],
        engine: Optional[PedigreeEngine] = None,
        node_x: Optional[Dict[str, float]] = None,
    ) -> List[str]:
        ordered_members = sorted(set(members), key=str.lower)
        if len(ordered_members) <= 2:
            if len(ordered_members) == 2 and node_x:
                # Preserve the family-tree side already chosen by the layout.
                # Alphabetical ordering flipped Arwen/Aragorn and
                # Dorothea/Dáin, forcing their connectors through siblings.
                def ancestry_center(node: str) -> float:
                    parent_values = (
                        engine.child_to_parents.get(node, {}) if engine else {}
                    )
                    parent_x = [
                        float(node_x[parent])
                        for parent in (
                            str(parent_values.get("egg_donor") or ""),
                            str(parent_values.get("sperm_donor") or ""),
                        )
                        if parent in node_x
                    ]
                    if parent_x:
                        return sum(parent_x) / len(parent_x)
                    return float(node_x.get(node, 0.0))
                return sorted(
                    ordered_members,
                    key=lambda node: (ancestry_center(node), node.lower()),
                )
            return ordered_members

        def _pair_center(a: str, b: str) -> Optional[float]:
            if not engine or not node_x:
                return None

            # Prefer shared offspring center for this specific couple.
            shared = engine.parent_to_children.get(a, set()) & engine.parent_to_children.get(b, set())
            shared_x = [node_x[ch] for ch in shared if ch in node_x]
            if shared_x:
                return sum(shared_x) / len(shared_x)

            # Fallback to overall offspring center for both nodes.
            offspring_x: List[float] = []
            for parent in (a, b):
                for child in engine.parent_to_children.get(parent, set()):
                    if child in node_x:
                        offspring_x.append(node_x[child])
            if offspring_x:
                return sum(offspring_x) / len(offspring_x)
            return None

        partner_degree = {
            node: len([n for n in adjacency.get(node, set()) if n in ordered_members])
            for node in ordered_members
        }
        hub_node = max(
            ordered_members,
            key=lambda n: (
                partner_degree.get(n, 0),
                -len(adjacency.get(n, set())),
                n.lower(),
            ),
        )

        # If no node has multiple partners in this component, use offspring barycenter.
        if partner_degree.get(hub_node, 0) < 2:
            if node_x and engine:
                member_centers: Dict[str, float] = {}
                for member in ordered_members:
                    xs = [node_x[ch] for ch in engine.parent_to_children.get(member, set()) if ch in node_x]
                    if xs:
                        member_centers[member] = sum(xs) / len(xs)
                if member_centers:
                    return sorted(ordered_members, key=lambda n: (member_centers.get(n, 0.0), n.lower()))
            return ordered_members

        direct_partners = [n for n in ordered_members if n != hub_node and n in adjacency.get(hub_node, set())]
        direct_partners.sort(key=str.lower)

        partner_centers: Dict[str, Optional[float]] = {}
        for partner in direct_partners:
            center = _pair_center(hub_node, partner)
            if center is None and node_x and partner in node_x:
                center = node_x[partner]
            partner_centers[partner] = center

        if node_x and hub_node in node_x:
            hub_center = float(node_x[hub_node])
        else:
            known_centers = [c for c in partner_centers.values() if c is not None]
            hub_center = (sum(known_centers) / len(known_centers)) if known_centers else 0.0

        left: List[str] = []
        right: List[str] = []
        for partner in direct_partners:
            center = partner_centers.get(partner)
            if center is None:
                # Alternate unknowns to keep the hub near center.
                (left if len(left) <= len(right) else right).append(partner)
            elif center < hub_center:
                left.append(partner)
            else:
                right.append(partner)

        left.sort(key=lambda n: (partner_centers.get(n) is None, partner_centers.get(n, -10_000.0)))
        right.sort(key=lambda n: (partner_centers.get(n) is None, partner_centers.get(n, 10_000.0)))

        arranged = left + [hub_node] + right

        # Keep any non-directly-linked nodes (rare in partner components) at outer sides.
        remaining = [n for n in ordered_members if n not in arranged]
        if remaining:
            remaining.sort(key=str.lower)
            arranged = remaining + arranged

        return arranged

    def _build_partner_components(
        self,
        level_nodes: List[str],
        nodes_subset: Set[str],
        engine: PedigreeEngine,
        node_x: Optional[Dict[str, float]] = None,
    ) -> List[List[str]]:
        level_set = set(level_nodes)
        adjacency: Dict[str, Set[str]] = defaultdict(set)

        for child in nodes_subset:
            parent_values = engine.child_to_parents.get(child, {})
            mother = str(parent_values.get("egg_donor", "")).strip()
            father = str(parent_values.get("sperm_donor", "")).strip()
            if mother and father and mother in level_set and father in level_set:
                adjacency[mother].add(father)
                adjacency[father].add(mother)

        components: List[List[str]] = []
        visited: Set[str] = set()
        for node in level_nodes:
            if node in visited or node not in adjacency:
                continue
            stack = [node]
            component: List[str] = []
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                component.append(cur)
                for nxt in adjacency.get(cur, set()):
                    if nxt not in visited:
                        stack.append(nxt)

            components.append(self._order_partner_component(component, adjacency, engine, node_x))

        return components

    def _resolve_shape(
        self,
        node: str,
        role: str,
        sex: str,
        engine: PedigreeEngine,
        is_heritage_only: bool = False,
        pending_sex_updates: Optional[Dict[str, str]] = None,
    ) -> str:
        s = (sex or "").strip().lower()
        r = (role or "").strip()

        # Rendering is deliberately pure: redraws never mutate identity data.
        # Explicit Unknown is distinct from a blank legacy value and must not
        # be inferred or silently coerced to Female.
        resolved_sex = s
        if resolved_sex not in ("male", "female", "unknown"):
            male_hint = (
                node in engine.father_like_nodes
                or r == ROLE_VALUE_SAMENSP
            )
            if male_hint:
                resolved_sex = "male"
            else:
                female_hint = (
                    node in engine.mother_like_nodes
                    or r in (ROLE_VALUE_SPENDER, ROLE_VALUE_AMME)
                )
                if female_hint:
                    resolved_sex = "female"

        _ = is_heritage_only, pending_sex_updates
        if resolved_sex == "male":
            return "^"  # triangle
        if resolved_sex == "female":
            return "o"  # circle
        return "s"  # explicit Unknown or unresolved legacy value

    def _parse_birth_year(self, raw_value: Any) -> Optional[int]:
        if raw_value is None:
            return None

        if isinstance(raw_value, datetime):
            return int(raw_value.year)

        if isinstance(raw_value, (int, float)):
            year = int(raw_value)
            return year if 1800 <= year <= 2200 else None

        text = str(raw_value).strip()
        if not text:
            return None

        normalized = text.split("T", 1)[0].strip()
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
            try:
                return int(datetime.strptime(normalized, fmt).year)
            except ValueError:
                continue

        for token in reversed(normalized.replace("/", ".").replace("-", ".").split(".")):
            token = token.strip()
            if len(token) == 4 and token.isdigit():
                year = int(token)
                if 1800 <= year <= 2200:
                    return year

        return None

    def _iter_node_records(self, node: str) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for collection in (
            getattr(self.app, "animals", {}) or {},
            getattr(self.app, "archived", {}) or {},
        ):
            if not isinstance(collection, dict):
                continue
            record = collection.get(node)
            if isinstance(record, dict):
                records.append(record)

        store_animals = self.plugin.store.load().get("animals", {})
        if isinstance(store_animals, dict):
            store_record = store_animals.get(node)
            if isinstance(store_record, dict):
                records.append(store_record)

        return records

    def _get_node_record(self, node: str) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        for record in reversed(self._iter_node_records(node)):
            merged.update(record)
        return merged

    def _get_node_display_label(self, node: str, record: Optional[Dict[str, Any]] = None) -> str:
        source_record = record if isinstance(record, dict) else self._get_node_record(node)
        return animal_base_name(node, source_record) or str(node)

    def _get_node_birth_ordinal(self, node: str) -> Optional[int]:
        for record in self._iter_node_records(node):
            for key in ("birth_date", "geburtsdatum"):
                ordinal = parse_complete_birth_date_ordinal(record.get(key))
                if ordinal is not None:
                    parsed = datetime.fromordinal(ordinal)
                    if (parsed.year, parsed.month, parsed.day) == (1900, 1, 1):
                        continue
                    return ordinal
        return None

    def _get_node_birth_date_text(self, node: str) -> str:
        ordinal = self._get_node_birth_ordinal(node)
        if ordinal is None:
            return self.messages.get("heritage_track.node.detail.missing", "—")
        return datetime.fromordinal(ordinal).strftime("%d.%m.%Y")

    def _get_node_public_id(self, node: str, record: Optional[Dict[str, Any]] = None) -> str:
        source = record if isinstance(record, dict) else self._get_node_record(node)
        for key in ("id", "animal_id", "public_id"):
            value = str(source.get(key, "") or "").strip()
            if value:
                return value
        return self.messages.get("heritage_track.node.detail.missing", "—")

    def _get_node_detail_text(
        self,
        node: str,
        record: Optional[Dict[str, Any]] = None,
        f_value: Optional[float] = None,
    ) -> str:
        mode = str(self.settings.get("animal_label_detail", "inbreeding_f"))
        if mode == "nothing":
            return ""
        if mode == "birth_date":
            return self._get_node_birth_date_text(node)
        if mode == "animal_id":
            return self._get_node_public_id(node, record)
        if f_value is None:
            return ""
        rounded = round(float(f_value), 4)
        was_rounded = abs(rounded - float(f_value)) > 1e-9
        template_key = (
            "heritage_track.node.inbreeding_f_approx"
            if was_rounded
            else "heritage_track.node.inbreeding_f_exact"
        )
        fallback = "F: ~{value}" if was_rounded else "F: {value}"
        return self.messages.get(template_key, fallback).replace("{value}", f"{rounded:.4f}")

    def _get_node_obstacle_label(self, node: str, record: Optional[Dict[str, Any]] = None) -> str:
        source = record if isinstance(record, dict) else self._get_node_record(node)
        primary = self._get_node_display_label(node, source)
        mode = str(self.settings.get("animal_label_detail", "inbreeding_f"))
        if mode == "nothing":
            return primary
        if mode == "birth_date":
            detail = self._get_node_birth_date_text(node)
        elif mode == "animal_id":
            detail = self._get_node_public_id(node, source)
        else:
            detail = "F: ~0.0000"
        return max((primary, detail), key=len)

    def _get_node_birth_year(self, node: str) -> Optional[int]:
        ordinal = self._get_node_birth_ordinal(node)
        if ordinal is not None:
            return int(datetime.fromordinal(ordinal).year)

        for record in self._iter_node_records(node):
            for key in ("birth_year", "year_of_birth"):
                year = self._parse_birth_year(record.get(key))
                if year is not None:
                    return year
        return None

    def _family_node_id(self, mother: str, father: str) -> str:
        return f"__family__::{mother}:::{father}"

    def _is_family_node(self, node_id: Optional[str]) -> bool:
        return str(node_id or "").startswith("__family__::")

    def _set_family_collapsed(self, family_id: str, collapsed: bool) -> None:
        family_key = str(family_id or "").strip()
        if not self._is_family_node(family_key):
            return

        if collapsed:
            if family_key in self.collapsed_families:
                return
            self.collapsed_families.add(family_key)
        else:
            if family_key not in self.collapsed_families:
                return
            self.collapsed_families.remove(family_key)

        self.plugin.store.set_family_collapsed(family_key, collapsed)

    def _toggle_family_collapsed(self, family_id: str) -> None:
        family_key = str(family_id or "").strip()
        if not self._is_family_node(family_key):
            return
        self._set_family_collapsed(family_key, family_key not in self.collapsed_families)

    def _collect_descendants(
        self,
        roots: Set[str],
        engine: PedigreeEngine,
        allowed_nodes: Optional[Set[str]] = None,
    ) -> Set[str]:
        allowed = set(allowed_nodes) if allowed_nodes is not None else None
        seed_nodes = {
            node
            for node in roots
            if node and (allowed is None or node in allowed)
        }
        if not seed_nodes:
            return set()

        descendants: Set[str] = set(seed_nodes)
        visited: Set[str] = set(seed_nodes)
        stack: List[str] = sorted(seed_nodes, key=str.lower)

        while stack:
            current = stack.pop()
            for child in engine.parent_to_children.get(current, set()):
                if allowed is not None and child not in allowed:
                    continue
                if child in visited:
                    continue
                visited.add(child)
                descendants.add(child)
                stack.append(child)

        return descendants

    def _compute_collapsed_family_positions(
        self,
        collapsed_families: Dict[str, Dict[str, Any]],
        animal_positions: Dict[str, Tuple[float, float]],
    ) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Set[str]]]:
        family_positions: Dict[str, Tuple[float, float]] = {}
        family_members: Dict[str, Set[str]] = {}

        for family_id, family in collapsed_families.items():
            mother = str(family.get("mother", "")).strip()
            father = str(family.get("father", "")).strip()
            parent_points = [
                animal_positions[parent]
                for parent in (mother, father)
                if parent in animal_positions
            ]
            if not parent_points:
                continue

            family_x = sum(point[0] for point in parent_points) / len(parent_points)
            parent_center_y = sum(point[1] for point in parent_points) / len(parent_points)
            family_y = parent_center_y - 0.8
            family_positions[family_id] = (family_x, family_y)

            members: Set[str] = set()
            if mother in animal_positions:
                members.add(mother)
            if father in animal_positions:
                members.add(father)
            family_members[family_id] = members

        return family_positions, family_members

    def _build_family_units(
        self,
        nodes: Set[str],
        levels: Dict[str, int],
        engine: PedigreeEngine,
    ) -> Dict[str, Dict[str, Any]]:
        families_by_pair: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

        for child in sorted(nodes, key=str.lower):
            parent_values = engine.child_to_parents.get(child, {})
            mother = str(parent_values.get("egg_donor", "")).strip()
            father = str(parent_values.get("sperm_donor", "")).strip()
            if not mother or not father or mother == father:
                continue
            if mother not in nodes or father not in nodes:
                continue
            families_by_pair[(mother, father)].add(child)

        families: Dict[str, Dict[str, Any]] = {}
        for mother, father in sorted(families_by_pair.keys(), key=lambda pair: (pair[0].lower(), pair[1].lower())):
            children = sorted(families_by_pair[(mother, father)], key=str.lower)
            if not children:
                continue

            child_levels = [levels.get(child, 0) for child in children]
            family_id = self._family_node_id(mother, father)
            families[family_id] = {
                "id": family_id,
                "mother": mother,
                "father": father,
                "children": children,
                "parent_level": max(levels.get(mother, 0), levels.get(father, 0)),
                "child_level": min(child_levels) if child_levels else 0,
            }

        return families


    def _compute_family_positions(
        self,
        families: Dict[str, Dict[str, Any]],
        animal_positions: Dict[str, Tuple[float, float]],
    ) -> Tuple[Dict[str, Tuple[float, float]], Dict[str, Set[str]]]:
        family_positions: Dict[str, Tuple[float, float]] = {}
        family_members: Dict[str, Set[str]] = {}

        grouped: Dict[Tuple[float, float], List[Tuple[str, float, float, float, Set[str]]]] = defaultdict(list)

        for family_id, family in families.items():
            mother = str(family.get("mother", "")).strip()
            father = str(family.get("father", "")).strip()
            children = [child for child in family.get("children", []) if child in animal_positions]
            if not children:
                continue

            parent_points = [
                animal_positions[parent]
                for parent in (mother, father)
                if parent in animal_positions
            ]
            if not parent_points:
                continue

            child_points = [animal_positions[child] for child in children]
            child_center_x = sum(point[0] for point in child_points) / len(child_points)
            child_center_y = sum(point[1] for point in child_points) / len(child_points)
            parent_center_x = sum(point[0] for point in parent_points) / len(parent_points)
            parent_center_y = sum(point[1] for point in parent_points) / len(parent_points)

            combined_x = [point[0] for point in child_points] + [point[0] for point in parent_points]
            min_x = min(combined_x)
            max_x = max(combined_x)
            family_x = (child_center_x + parent_center_x) / 2.0
            family_x = max(min_x, min(max_x, family_x))

            members = set(children)
            if mother in animal_positions:
                members.add(mother)
            if father in animal_positions:
                members.add(father)
            family_members[family_id] = members

            band_key = (round(parent_center_y, 6), round(child_center_y, 6))
            grouped[band_key].append((family_id, family_x, parent_center_y, child_center_y, members))

        for (_, _), band_families in grouped.items():
            band_families.sort(key=lambda item: (item[1], item[0].lower()))
            count = len(band_families)
            for idx, (family_id, family_x, parent_y, child_y, _members) in enumerate(band_families):
                low_y = min(parent_y, child_y)
                high_y = max(parent_y, child_y)
                span = high_y - low_y
                if span <= 1e-9:
                    family_y = low_y
                else:
                    if count == 1:
                        frac = 0.50
                    else:
                        frac = 0.32 + (0.36 * idx / (count - 1))
                    family_y = low_y + (span * frac)
                family_positions[family_id] = (family_x, family_y)

        return family_positions, family_members

    def _compute_birth_year_bins(
        self,
        nodes: Set[str],
        levels: Dict[str, int],
        engine: PedigreeEngine,
    ) -> Dict[str, int]:
        def _collect_partner_components() -> List[List[str]]:
            partner_adj: Dict[str, Set[str]] = defaultdict(set)
            for child in nodes:
                parent_values = engine.child_to_parents.get(child, {})
                mother = str(parent_values.get("egg_donor", "")).strip()
                father = str(parent_values.get("sperm_donor", "")).strip()
                if not mother or not father:
                    continue
                if mother not in nodes or father not in nodes or mother == father:
                    continue
                partner_adj[mother].add(father)
                partner_adj[father].add(mother)

            components: List[List[str]] = []
            seen: Set[str] = set()
            for seed in sorted(partner_adj.keys(), key=str.lower):
                if seed in seen:
                    continue
                stack = [seed]
                component: List[str] = []
                while stack:
                    cur = stack.pop()
                    if cur in seen:
                        continue
                    seen.add(cur)
                    component.append(cur)
                    for nxt in partner_adj.get(cur, set()):
                        if nxt not in seen:
                            stack.append(nxt)
                if len(component) > 1:
                    components.append(component)
            return components

        def _align_partner_years(year_map: Dict[str, int], components: List[List[str]]) -> None:
            # Partners share one row; the younger partner (larger year) defines height.
            for component in components:
                known = [year_map[node] for node in component if node in year_map]
                if not known:
                    continue
                target_year = max(known)
                for node in component:
                    year_map[node] = target_year

        partner_components = _collect_partner_components()

        year_by_node: Dict[str, int] = {}
        for node in nodes:
            year = self._get_node_birth_year(node)
            if year is not None:
                year_by_node[node] = year

        unresolved = {node for node in nodes if node not in year_by_node}

        # Infer missing years from close relatives when possible.
        if unresolved and year_by_node:
            changed = True
            while changed and unresolved:
                changed = False
                for node in list(unresolved):
                    parent_values = engine.child_to_parents.get(node, {})
                    parent_years: List[int] = []
                    for parent_key in ("egg_donor", "sperm_donor"):
                        parent = str(parent_values.get(parent_key, "")).strip()
                        if parent in year_by_node:
                            parent_years.append(year_by_node[parent])

                    child_years = [
                        year_by_node[child]
                        for child in engine.parent_to_children.get(node, set())
                        if child in year_by_node
                    ]

                    inferred: Optional[int] = None
                    if parent_years:
                        inferred = max(parent_years) + 1
                    elif child_years:
                        inferred = min(child_years) - 1

                    if inferred is not None:
                        year_by_node[node] = int(inferred)
                        unresolved.remove(node)
                        changed = True

        _align_partner_years(year_by_node, partner_components)
        unresolved = {node for node in nodes if node not in year_by_node}

        # Final fallback to generation levels if no birth date information exists.
        if unresolved:
            base_year = min(year_by_node.values()) if year_by_node else 2000
            min_level = min((levels.get(node, 0) for node in nodes), default=0)
            for node in list(unresolved):
                inferred = base_year + (levels.get(node, 0) - min_level)
                year_by_node[node] = int(inferred)
                unresolved.remove(node)

        _align_partner_years(year_by_node, partner_components)

        ordered_years = sorted(set(year_by_node.values()))
        year_to_bin = {year: idx for idx, year in enumerate(ordered_years)}
        return {node: year_to_bin[year_by_node[node]] for node in nodes if node in year_by_node}


    def _compute_positions(
        self,
        nodes: Set[str],
        levels: Dict[str, int],
        engine: PedigreeEngine,
        families: Optional[Dict[str, Dict[str, Any]]] = None,
        locked_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """Compute node positions using the LayoutPipeline.

        This method delegates to the LayoutPipeline which breaks down the layout
        computation into separate, testable phases:
        1. Group formation (sibling groups and singletons)
        2. Component analysis
        3. Family mapping
        4. Row assignment
        5. X coordinate placement
        6. Component packing
        """
        node_set = set(nodes)
        if not node_set:
            return {}

        if families is None:
            families = self._build_family_units(node_set, levels, engine)

        # Get birth years for tie-breaking
        birth_year_by_node: Dict[str, Optional[int]] = {
            node: self._get_node_birth_year(node) for node in node_set
        }
        birth_ordinal_by_node: Dict[str, Optional[int]] = {
            node: self._get_node_birth_ordinal(node) for node in node_set
        }

        # Use the LayoutPipeline for horizontal family placement and the
        # default compact generation layout.
        pipeline = LayoutPipeline()
        base_positions = pipeline.compute_positions(
            nodes=node_set,
            levels=levels,
            engine=engine,
            families=families,
            locked_positions=locked_positions or {},
            birth_year_by_node=birth_year_by_node,
            birth_ordinal_by_node=birth_ordinal_by_node,
            birthdate_height_layout=(
                self.settings.get("vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED)
                == VERTICAL_LAYOUT_PARTNER_NORMALIZED
            ),
        )
        if (
            self.settings.get("vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED)
            != VERTICAL_LAYOUT_CHRONOLOGICAL
        ):
            self._chronological_undated_nodes = set()
            return base_positions

        chronological, undated = compute_chronological_positions(
            base_positions,
            families,
            birth_ordinal_by_node,
        )
        self._chronological_undated_nodes = undated
        return chronological

    def _bfs_relationship_path(
        self,
        node_a: str,
        node_b: str,
        engine: "PedigreeEngine",
        animal_positions: Dict[str, Tuple[float, float]],
        family_positions: Dict[str, Tuple[float, float]],
        families: Dict[str, Any],
    ) -> List[Tuple[Tuple[float, float], Tuple[float, float]]]:
        """Path between two animals routed via their last common ancestor (LCA).

        Uses two independent upward BFS passes (child→family→parent) rather than
        a single undirected BFS.  Undirected BFS fails in inbreeding cases because
        equal-length shortcuts through a shared parent are found before the path
        that passes through the actual LCA.

        Algorithm:
          1. BFS strictly upward from node_a → prev_a, ancestor_set_a
          2. BFS strictly upward from node_b → prev_b, ancestor_set_b
          3. LCA = common ancestor with minimum (hops_from_a + hops_from_b)
          4. Full path = node_a →(up)→ LCA →(down)→ node_b
        """
        from collections import deque

        # Index which family each animal belongs to as a child or parent.
        fam_parents: Dict[str, Set[str]] = {}   # fid → {mother, father}
        fam_children: Dict[str, Set[str]] = {}  # fid → {children}
        child_in: Dict[str, Set[str]] = defaultdict(set)   # animal → fids where it is a child
        parent_in: Dict[str, Set[str]] = defaultdict(set)  # animal → fids where it is a parent

        for fid, fdata in families.items():
            mother = str(fdata.get("mother", "")).strip()
            father = str(fdata.get("father", "")).strip()
            children = [str(c).strip() for c in fdata.get("children", []) if str(c).strip()]
            pset = {p for p in (mother, father) if p}
            cset = set(children)
            fam_parents[fid] = pset
            fam_children[fid] = cset
            for p in pset:
                parent_in[p].add(fid)
            for c in cset:
                child_in[c].add(fid)

        def _bfs_up(start: str) -> Dict[str, str]:
            """BFS strictly upward (child→family→parent). Returns prev[] map."""
            prev: Dict[str, str] = {}
            visited: Set[str] = {start}
            q: deque = deque([start])
            while q:
                cur = q.popleft()
                if cur in fam_parents:
                    # family node → go to its parent animals
                    for parent in sorted(fam_parents[cur]):
                        if parent not in visited:
                            visited.add(parent)
                            prev[parent] = cur
                            q.append(parent)
                else:
                    # animal → go to family nodes where it is a child
                    for fid in sorted(child_in.get(cur, set())):
                        if fid not in visited:
                            visited.add(fid)
                            prev[fid] = cur
                            q.append(fid)
            return prev

        prev_a = _bfs_up(node_a)
        prev_b = _bfs_up(node_b)

        # Animals reachable going upward from each node (exclude family-node keys).
        ancestors_a: Set[str] = {node_a} | {n for n in prev_a if n not in fam_parents}
        ancestors_b: Set[str] = {node_b} | {n for n in prev_b if n not in fam_parents}
        common = ancestors_a & ancestors_b
        if not common:
            return []

        def _hop_count(node: str, prev: Dict[str, str], start: str) -> int:
            if node == start:
                return 0
            count, cur = 0, node
            while cur != start:
                cur = prev.get(cur)
                if cur is None:
                    return 999999
                count += 1
            return count

        best_lca = min(
            common,
            key=lambda c: _hop_count(c, prev_a, node_a) + _hop_count(c, prev_b, node_b),
        )

        def _reconstruct_up(dest: str, prev: Dict[str, str], start: str) -> List[str]:
            """Walk prev[] back from dest to start; return [start, ..., dest]."""
            path: List[str] = []
            cur = dest
            while cur != start:
                path.append(cur)
                nxt = prev.get(cur)
                if nxt is None:
                    break
                cur = nxt
            path.append(start)
            path.reverse()
            return path

        path_up_a = _reconstruct_up(best_lca, prev_a, node_a)   # node_a → LCA
        path_up_b = _reconstruct_up(best_lca, prev_b, node_b)   # node_b → LCA
        path_down_b = list(reversed(path_up_b))                  # LCA → node_b

        full_path = path_up_a + path_down_b[1:]  # join at LCA (avoid duplicate)

        # Build drawable segments from the same semantic routes used by the
        # normal renderer. Highlights must never invent a direct shortcut.
        all_positions: Dict[str, Tuple[float, float]] = {**animal_positions, **family_positions}
        segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
        for i in range(len(full_path) - 1):
            n1, n2 = full_path[i], full_path[i + 1]
            route_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
            if self._route_plan is not None:
                if n1 in animal_positions and n2 in family_positions:
                    drawn = self._route_plan.draw_segments(n2, n1)
                    route_segments = [(second, first) for first, second in reversed(drawn)]
                elif n1 in family_positions and n2 in animal_positions:
                    route_segments = self._route_plan.draw_segments(n1, n2)
            if route_segments:
                segments.extend(route_segments)
                continue

            p1 = all_positions.get(n1)
            p2 = all_positions.get(n2)
            if p1 is not None and p2 is not None:
                segments.append((p1, p2))
        return segments

    def _compute_view_bounds(
        self,
        positions: Dict[str, Tuple[float, float]],
        extra_points: Optional[List[Tuple[float, float]]] = None,
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        points = list(positions.values()) + list(extra_points or [])
        if not points:
            return (-2.0, 2.0), (-2.0, 2.0)

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        margin_x = 2.0
        margin_y = 1.5
        return (min(xs) - margin_x, max(xs) + margin_x), (min(ys) - margin_y, max(ys) + margin_y)

    def _build_display_context(
        self,
        engine: PedigreeEngine,
        selected_animals: List[str],
        all_graph_families: Dict[str, Dict[str, Any]],
    ) -> DisplayContext:
        """Build display context using the new strategy-based architecture.

        This method uses DisplayContextBuilder with strategy objects to:
        1. Compute display set based on selection mode
        2. Find ghost nodes using appropriate strategies
        3. Compute levels with leaf promotion and pull-up
        """
        # Prepare archived set
        archived = getattr(self.app, "archived", {}) or {}
        archived_animals: Set[str] = set(archived.keys()) if isinstance(archived, dict) else set()
        exclude_archived = self.settings.get("exclude_archived", False)

        # Determine display strategy based on mode
        all_animals_mode = len(selected_animals) == 0

        if all_animals_mode:
            # All-animals mode with scope filtering
            scope_provider = ProjectsTrackScopeProvider(self.app)
            display_strategy = AllAnimalsStrategy(scope_provider=scope_provider)
        else:
            # Selected animals mode
            display_strategy = SelectedAnimalsStrategy()

        # Build ghost detection strategy
        ghost_strategies: list = []

        # Scope ghosts (when project/species filter is active)
        if all_animals_mode:
            pt = getattr(self.app, "projects_plugin", None)
            scope_active = (
                pt is not None and (
                    (getattr(pt, "current_project", "All") or "All") != "All"
                    or bool(getattr(pt, "active_species", None))
                )
            )
            if scope_active:
                ghost_strategies.append(ScopeGhostStrategy(families=all_graph_families))

        # Offspring and siblings ghosts (when specific animals are selected)
        if not all_animals_mode and selected_animals:
            ghost_strategies.append(
                OffspringAndSiblingsGhostStrategy(selected_animals=set(selected_animals))
            )

        # Archived ghosts
        if exclude_archived and archived_animals:
            ghost_strategies.append(ArchivedGhostStrategy())

        ghost_strategy = CompositeGhostStrategy(ghost_strategies) if ghost_strategies else None

        # Build settings dict
        settings = {
            "max_generations": self._max_generations,
            "exclude_archived": exclude_archived,
            "show_heritage_only": self.settings.get("show_heritage_only", True),
        }

        # Create builder and build context
        builder = DisplayContextBuilder(
            engine=engine,
            settings=settings,
            display_strategy=display_strategy,
            ghost_strategy=ghost_strategy,
            scope_provider=ProjectsTrackScopeProvider(self.app) if all_animals_mode else None,
        )

        context = builder.build(
            selected_animals=selected_animals,
            archived_animals=archived_animals,
        )

        # Handle heritage-only filtering (needs plugin access)
        if not self.settings.get("show_heritage_only", True):
            display_nodes = {n for n in context.display_nodes if not self.plugin.is_heritage_only(n)}
            context = context.copy_with(display_nodes=display_nodes)

        # Handle archived exclusion in all-animals mode
        if all_animals_mode and exclude_archived and archived_animals:
            display_nodes = context.display_nodes - archived_animals
            context = context.copy_with(display_nodes=display_nodes)

        return context

    def refresh_graph(self, keep_view: bool = False) -> None:
        engine = self.plugin.build_engine()
        selected_animals = list(getattr(self.app, "selected_animals", []) or [])
        # Also include selected heritage-only animals from the app
        selected_heritage_only = list(getattr(self.app, "_selected_heritage_only", []) or [])
        if selected_heritage_only:
            selected_animals = selected_animals + selected_heritage_only
        self.all_animals_mode = len(selected_animals) == 0

        # No-selection mode only shows the splash screen.  Avoid building the
        # complete pedigree, level map, families, layout, and routes merely to
        # discard them a few lines later.
        if self.all_animals_mode:
            self._show_splash_screen()
            return

        all_graph_nodes = engine.get_display_nodes([])
        all_graph_levels = engine.compute_levels(all_graph_nodes)
        all_graph_families = self._build_family_units(all_graph_nodes, all_graph_levels, engine)

        # Use the new strategy-based architecture to build display context
        # This handles: display set computation, ghost detection, level computation
        context = self._build_display_context(engine, selected_animals, all_graph_families)

        # Extract data from context
        display_nodes = context.display_nodes
        pre_collapse_levels = context.levels
        ghost_nodes = context.ghost_nodes
        self._ghost_nodes = ghost_nodes

        # Force relayout if display nodes have changed (new selection)
        # This ensures proper placement algorithms run on first selection
        if hasattr(self, '_prev_display_nodes') and self._prev_display_nodes != display_nodes:
            self._force_relayout = True
        self._prev_display_nodes = display_nodes.copy()

        # Switch to canvas for graph display
        self.stack.setCurrentWidget(self.canvas)

        # Build families from current display set
        all_families = self._build_family_units(display_nodes, pre_collapse_levels, engine)

        # Track stale collapsed families for deferred cleanup (don't save yet)
        stale_collapsed = self.collapsed_families - set(all_graph_families.keys())
        if stale_collapsed:
            self.collapsed_families = set(self.collapsed_families) - set(stale_collapsed)

        valid_collapsed_families = self.collapsed_families & set(all_families.keys())

        # Compute hidden nodes from collapsed families
        hidden_nodes: Set[str] = set()
        for family_id in valid_collapsed_families:
            family = all_families.get(family_id, {})
            family_children = {
                child
                for child in family.get("children", [])
                if child in display_nodes
            }
            hidden_nodes.update(self._collect_descendants(family_children, engine, allowed_nodes=display_nodes))

        # Filter display_nodes and levels together to maintain consistency
        if hidden_nodes:
            display_nodes = {node for node in display_nodes if node not in hidden_nodes}
            levels = {n: lvl for n, lvl in pre_collapse_levels.items() if n in display_nodes}
        else:
            levels = pre_collapse_levels

        # Build collapsed family nodes (parents without visible children)
        collapsed_family_nodes: Dict[str, Dict[str, Any]] = {}
        for family_id in sorted(valid_collapsed_families, key=str.lower):
            family = all_families.get(family_id, {})
            mother = str(family.get("mother", "")).strip()
            father = str(family.get("father", "")).strip()
            if mother not in display_nodes and father not in display_nodes:
                continue

            family_entry = dict(family)
            family_entry["children"] = []
            collapsed_family_nodes[family_id] = family_entry

        # Clean up selections and positions (deferred persistence)
        self.selected_nodes = {n for n in self.selected_nodes if n in display_nodes}
        self._cleanup_stale_positions(all_graph_nodes)

        # Build final families ONCE after all filtering
        families = self._build_family_units(display_nodes, levels, engine)
        families.update(collapsed_family_nodes)
        saved_positions_all = self.plugin.store.get_node_positions()
        saved_positions = {
            node: pos
            for node, pos in saved_positions_all.items()
            if node in display_nodes
        }

        locked_positions = (
            saved_positions
            if self.all_animals_mode and not self._force_relayout
            else {}
        )

        auto_positions = self._compute_positions(
            display_nodes,
            levels,
            engine,
            families,
            locked_positions=locked_positions,
        )
        chronological_mode = (
            self.settings.get("vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED)
            == VERTICAL_LAYOUT_CHRONOLOGICAL
        )

        def _respect_vertical_mode(
            stored: Tuple[float, float],
            automatic: Tuple[float, float],
        ) -> Tuple[float, float]:
            if chronological_mode:
                return float(stored[0]), float(automatic[1])
            return float(stored[0]), float(stored[1])

        previous_positions = {
            node: pos
            for node, pos in self.node_positions.items()
            if node in display_nodes and not self._is_family_node(node)
        }

        if self.all_animals_mode:
            animal_positions: Dict[str, Tuple[float, float]] = {}
            protected_nodes: Set[str] = set()
            for node, pos in auto_positions.items():
                if node in self.temp_positions:
                    animal_positions[node] = _respect_vertical_mode(self.temp_positions[node], pos)
                    protected_nodes.add(node)
                elif node in saved_positions and not self._force_relayout:
                    animal_positions[node] = _respect_vertical_mode(saved_positions[node], pos)
                    protected_nodes.add(node)
                else:
                    animal_positions[node] = pos

            if self._force_relayout:
                self.plugin.store.set_node_positions_batch(animal_positions)
            else:
                to_save = {n: p for n, p in animal_positions.items()
                           if n not in saved_positions}
                if to_save:
                    self.plugin.store.set_node_positions_batch(to_save)

            # temp_positions will be updated at the end of refresh
        else:
            # Selected animals mode - handle position computation with relayout support
            animal_positions = {}
            protected_nodes = set()
            for node, pos in auto_positions.items():
                if node in self.temp_positions:
                    # User-dragged position takes priority
                    animal_positions[node] = _respect_vertical_mode(self.temp_positions[node], pos)
                    protected_nodes.add(node)
                elif self._force_relayout:
                    # Force relayout: use computed positions for all nodes (ignore cached positions)
                    animal_positions[node] = pos
                elif node in previous_positions:
                    # Use existing position from previous frame
                    animal_positions[node] = _respect_vertical_mode(previous_positions[node], pos)
                    protected_nodes.add(node)
                elif node in saved_positions:
                    # Use saved position from store
                    animal_positions[node] = _respect_vertical_mode(saved_positions[node], pos)
                    protected_nodes.add(node)
                else:
                    # New node: use computed position
                    animal_positions[node] = pos

        # Horizontal ancestry-side correction is valid in both vertical modes;
        # chronological mode only owns Y coordinates.
        if animal_positions:
            def _ancestry_x(node: str) -> Optional[float]:
                values = engine.child_to_parents.get(node, {})
                xs = [
                    animal_positions[parent][0]
                    for parent in (
                        str(values.get("egg_donor") or ""),
                        str(values.get("sperm_donor") or ""),
                    )
                    if parent in animal_positions
                ]
                return (sum(xs) / len(xs)) if xs else None

            for family in families.values():
                mother = str(family.get("mother") or "")
                father = str(family.get("father") or "")
                if (
                    mother not in animal_positions
                    or father not in animal_positions
                    or mother in protected_nodes
                    or father in protected_nodes
                ):
                    continue
                mother_center = _ancestry_x(mother)
                father_center = _ancestry_x(father)
                if mother_center is None or father_center is None:
                    continue
                mother_x, mother_y = animal_positions[mother]
                father_x, father_y = animal_positions[father]
                if (mother_center - father_center) * (mother_x - father_x) < 0:
                    animal_positions[mother] = (father_x, mother_y)
                    animal_positions[father] = (mother_x, father_y)
                    protected_nodes.update((mother, father))

        obstacle_labels = {
            node: self._get_node_obstacle_label(node, self._get_node_record(node))
            for node in animal_positions
        }
        has_secondary_label = self.settings.get("animal_label_detail", "inbreeding_f") != "nothing"
        route_plan = self._pedigree_router.plan(
            animal_positions,
            families,
            labels=obstacle_labels,
            protected_nodes=protected_nodes,
            show_inbreeding=has_secondary_label,
            vertical_layout_mode=self.settings.get(
                "vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED
            ),
        )
        if route_plan.animal_positions:
            reroute_positions = dict(route_plan.animal_positions)
            reroute_protected = set(protected_nodes)
            changed = False

            def _routed_ancestry_x(node: str) -> Optional[float]:
                values = engine.child_to_parents.get(node, {})
                xs = [
                    reroute_positions[parent][0]
                    for parent in (
                        str(values.get("egg_donor") or ""),
                        str(values.get("sperm_donor") or ""),
                    )
                    if parent in reroute_positions
                ]
                return (sum(xs) / len(xs)) if xs else None

            for family in families.values():
                mother = str(family.get("mother") or "")
                father = str(family.get("father") or "")
                if mother not in reroute_positions or father not in reroute_positions:
                    continue
                mother_center = _routed_ancestry_x(mother)
                father_center = _routed_ancestry_x(father)
                if mother_center is None or father_center is None:
                    continue
                mother_x, mother_y = reroute_positions[mother]
                father_x, father_y = reroute_positions[father]
                if (mother_center - father_center) * (mother_x - father_x) < 0:
                    reroute_positions[mother] = (father_x, mother_y)
                    reroute_positions[father] = (mother_x, father_y)
                    reroute_protected.update((mother, father))
                    changed = True
            if changed:
                route_plan = self._pedigree_router.plan(
                    reroute_positions,
                    families,
                    labels=obstacle_labels,
                    protected_nodes=reroute_protected,
                    show_inbreeding=has_secondary_label,
                    vertical_layout_mode=self.settings.get(
                        "vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED
                    ),
                )
        animal_positions = route_plan.animal_positions
        family_positions = route_plan.family_positions
        family_members = route_plan.family_members
        attached = set().union(*family_members.values()) if family_members else set()
        singletons = sorted(
            (
                node for node in animal_positions
                if node not in attached and node not in protected_nodes
            ),
            key=lambda node: self._get_node_display_label(node).casefold(),
        )
        if singletons:
            connected_points = [
                point for node, point in animal_positions.items()
                if node not in singletons
            ]
            min_x = min((point[0] for point in connected_points), default=0.0)
            min_y = min((point[1] for point in connected_points), default=0.0)
            columns = min(6, max(1, int(math.ceil(math.sqrt(len(singletons))))))
            chronological = (
                self.settings.get("vertical_layout_mode")
                == VERTICAL_LAYOUT_CHRONOLOGICAL
            )
            chronological_columns: Dict[float, int] = {}
            for index, node in enumerate(singletons):
                if chronological and connected_points:
                    # Keep the birth-date Y coordinate, but reserve columns
                    # clearly outside the family-tree envelope.
                    y = animal_positions[node][1]
                    y_bucket = round(float(y), 3)
                    x_column = chronological_columns.get(y_bucket, 0)
                    chronological_columns[y_bucket] = x_column + 1
                    animal_positions[node] = (
                        min_x - 4.2 - x_column * 4.2,
                        y,
                    )
                else:
                    animal_positions[node] = (
                        min_x + (index % columns) * 4.2,
                        min_y - 4.0 - (index // columns) * 3.2,
                    )
        self._route_plan = route_plan
        self.family_routes = route_plan.routes

        positions: Dict[str, Tuple[float, float]] = dict(animal_positions)
        positions.update(family_positions)

        self._force_relayout = False

        prev_xlim = self.current_xlim
        prev_ylim = self.current_ylim
        fit_xlim, fit_ylim = self._compute_view_bounds(positions, route_plan.all_points())

        if keep_view and prev_xlim is not None and prev_ylim is not None:
            view_xlim = prev_xlim
            view_ylim = prev_ylim
        else:
            view_xlim = fit_xlim
            view_ylim = fit_ylim

        view_xlim, view_ylim = self._apply_aspect_fill(view_xlim, view_ylim)

        self.ax.clear()
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_xlim(view_xlim)
        self.ax.set_ylim(view_ylim)

        if self.settings.get("show_grid", False):
            self._draw_grid()

        self.family_positions = family_positions
        self.family_members = family_members
        self.node_positions = positions
        self.node_meta.clear()

        route_batches: Dict[
            Tuple[str, float, float],
            List[List[Tuple[float, float]]],
        ] = defaultdict(list)
        for family_id, family in families.items():
            family_pos = family_positions.get(family_id)
            if family_pos is None:
                continue

            mother = str(family.get("mother", "")).strip()
            father = str(family.get("father", "")).strip()

            for parent in (mother, father):
                if parent not in animal_positions:
                    continue
                _lc = "#cccccc" if parent in ghost_nodes else "#666666"
                for (x1, y1), (x2, y2) in route_plan.draw_segments(family_id, parent):
                    route_batches[(_lc, 0.9, 1.1)].append([(x1, y1), (x2, y2)])

            for child in family.get("children", []):
                if child not in animal_positions:
                    continue
                _lc = "#cccccc" if child in ghost_nodes else "black"
                for (x1, y1), (x2, y2) in route_plan.draw_segments(family_id, child):
                    route_batches[(_lc, 1.0, 1.0)].append([(x1, y1), (x2, y2)])

        for (color, linewidth, zorder), segments in route_batches.items():
            self.ax.add_collection(
                LineCollection(
                    segments,
                    colors=[color],
                    linewidths=[linewidth],
                    zorder=zorder,
                )
            )

        # --- Relationship path highlight (exactly 2 selected) ---
        # Only draw the orange path when the two animals share common descent
        # (kinship_phi > 0). Unrelated animals must never show a connecting line.
        if len(self.selected_nodes) == 2:
            sel_list = list(self.selected_nodes)
            calculator = InbreedingCalculator(engine.get_genetic_parent_map())
            if calculator.kinship_phi(sel_list[0], sel_list[1]) > 0:
                path_edges = self._bfs_relationship_path(
                    sel_list[0], sel_list[1], engine, animal_positions, family_positions, families)
                for (x1, y1), (x2, y2) in path_edges:
                    self.ax.plot([x1, x2], [y1, y2],
                                 color="#e67e22", linewidth=2.5, zorder=1.8, solid_capstyle="round")

        for family_id, (fx, fy) in family_positions.items():
            family_fill = "black" if family_id in collapsed_family_nodes else "white"
            self.ax.plot(
                [fx],
                [fy],
                linestyle="None",
                marker="o",
                markersize=8.4,
                markeredgecolor="black",
                markerfacecolor=family_fill,
                markeredgewidth=1.1,
                zorder=2.5,
            )
            family = families.get(family_id, {})
            self.node_meta[family_id] = {
                "kind": "family",
                "genotype": "",
                "fill_color_raw": family_fill,
                "fill_color": family_fill,
                "role": "family",
                "sex": "",
                "heritage_only": False,
                "mother": family.get("mother", ""),
                "father": family.get("father", ""),
            }

        legend_entries: Dict[str, Dict[str, str]] = {}

        # Compute F only when it is the selected secondary label.  The other
        # modes avoid an unnecessary full kinship calculation during redraw.
        label_detail_mode = str(self.settings.get("animal_label_detail", "inbreeding_f"))
        show_f = label_detail_mode == "inbreeding_f"
        f_values: Dict[str, float] = {}
        if show_f:
            _genetic_map = engine.get_genetic_parent_map()
            _f_calculator = InbreedingCalculator(_genetic_map)
            _store_anims = self.plugin.store.load().get("animals", {})
            _f_to_save: Dict[str, float] = {}
            for _node in display_nodes:
                if self._is_family_node(_node):
                    continue
                _anim_entry = _store_anims.get(_node, {}) if isinstance(_store_anims, dict) else {}
                _cached_f = _anim_entry.get("inbreeding_f") if isinstance(_anim_entry, dict) else None
                if _cached_f is not None:
                    try:
                        f_values[_node] = float(_cached_f)
                        continue
                    except (TypeError, ValueError):
                        pass
                _f_val = _f_calculator.self_inbreeding_F(_node)
                f_values[_node] = _f_val
                _f_to_save[_node] = _f_val
            if _f_to_save:
                self.plugin.store.set_inbreeding_f_batch(_f_to_save)

        for node in sorted(display_nodes, key=str.lower):
            x, y = animal_positions.get(node, (0.0, 0.0))
            is_ghost = node in ghost_nodes
            is_heritage_only = self.plugin.is_heritage_only(node)
            # Get record from main app data (active or archived) for proper sex/shape
            record = self._get_node_record(node)
            display_label = self._get_node_display_label(node, record)
            role = canonical_role_value(record.get("rolle", ""))
            sex = self.plugin.get_effective_sex(node, record)

            visual = self.plugin.store.get_node_visual(node, fallback_genotype=str(record.get("genotype", "")))
            fill_color_raw = visual.get("node_fill_color", "")
            fill_color = self._valid_fill(fill_color_raw)
            # _resolve_shape now handles saving resolved sex for heritage-only animals
            shape = self._resolve_shape(
                node,
                role,
                sex,
                engine,
                is_heritage_only=is_heritage_only,
            )
            is_dead = bool(
                record.get("death_date") or record.get("sterbedatum") or record.get("archived")
            )

            if is_ghost:
                node_edge_color = "#aaaaaa"
                node_face_color = "#e8e8e8"
                text_color = "#999999"
                lw = 1.0
            else:
                node_edge_color = "black"
                node_face_color = fill_color
                text_color = "black"
                lw = 3.0 if node in self.selected_nodes else 1.5

            marker_artist, = self.ax.plot(
                [x],
                [y],
                linestyle="None",
                marker=shape,
                markersize=16.7,
                markeredgecolor=node_edge_color,
                markerfacecolor=node_face_color,
                markeredgewidth=lw,
                zorder=3,
            )
            label_artist = self.ax.annotate(
                display_label,
                xy=(x, y),
                xytext=(0, -14),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=9,
                color=text_color,
                fontstyle="italic" if is_heritage_only else "normal",
                fontweight="normal" if (is_dead or is_ghost) else "bold",
                zorder=4,
                path_effects=[path_effects.withStroke(linewidth=1, foreground="white")],
            )

            detail_text = self._get_node_detail_text(node, record, f_values.get(node))
            f_artist = None
            if detail_text:
                f_artist = self.ax.annotate(
                    detail_text,
                    xy=(x, y),
                    xytext=(0, -27),
                    textcoords="offset points",
                    ha="center",
                    va="top",
                    fontsize=7,
                    color="#777777" if not is_ghost else "#bbbbbb",
                    zorder=4,
                    path_effects=[path_effects.withStroke(linewidth=1, foreground="white")],
                )

            undated_artist = None
            if chronological_mode and node in self._chronological_undated_nodes:
                undated_artist = self.ax.annotate(
                    self.messages.get("heritage_track.node.undated_marker", "//"),
                    xy=(x, y),
                    xytext=(10, 7),
                    textcoords="offset points",
                    ha="left",
                    va="bottom",
                    fontsize=7,
                    color="#777777" if not is_ghost else "#aaaaaa",
                    zorder=5,
                    path_effects=[path_effects.withStroke(linewidth=1, foreground="white")],
                )

            genotype_value = str(visual.get("genotype", "")).strip() or self.messages.get(
                "heritage_track.legend.no_genotype",
                "(no genotype)",
            )
            entry_key = genotype_value.lower()
            if entry_key not in legend_entries:
                legend_entries[entry_key] = {
                    "genotype": genotype_value,
                    "face_color": fill_color,
                }

            self.node_meta[node] = {
                "kind": "animal",
                "genotype": visual.get("genotype", ""),
                "fill_color_raw": fill_color_raw,
                "fill_color": fill_color,
                "role": role,
                "sex": sex,
                "heritage_only": is_heritage_only,
                "display_label": display_label,
                "ipid": node,
                "animal_id": self._get_node_public_id(node, record),
                "birth_date": str(record.get("birth_date", "") or record.get("geburtsdatum", "")).strip(),
                "marker_artist": marker_artist,
                "label_artist": label_artist,
                "f_artist": f_artist,
                "undated_artist": undated_artist,
            }

        if chronological_mode:
            self._configure_chronological_axis()
        else:
            self.ax.axis("off")
            self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)

        if self.settings.get("show_legend", True) and legend_entries:
            legend_handles: List[Line2D] = []
            for entry in sorted(
                legend_entries.values(),
                key=lambda item: str(item.get("genotype", "")).strip().casefold(),
            ):
                label = entry["genotype"]
                legend_handles.append(
                    Line2D(
                        [0],
                        [0],
                        marker="s",
                        linestyle="None",
                        markeredgecolor="black",
                        markerfacecolor=entry["face_color"],
                        color="black",
                        markersize=7,
                        label=label,
                    )
                )

            legend = self.ax.legend(
                handles=legend_handles,
                loc="lower right",
                title=self.messages.get("heritage_track.legend.title", "Genotype legend"),
                fontsize=8,
                title_fontsize=8,
                frameon=True,
            )
            legend.set_zorder(12)

        self.current_xlim = self.ax.get_xlim()
        self.current_ylim = self.ax.get_ylim()

        mode_text = (
            self.messages.get("heritage_track.status.mode_selected", "Selection mode")
            if selected_animals
            else self.messages.get("heritage_track.status.mode_all", "All animals mode")
        )
        selected_text = self.messages.get("heritage_track.status.selected", "Selected in graph: {count}").format(
            count=len(self.selected_nodes)
        )
        status_text = f"{mode_text} | {selected_text}"
        if route_plan.unresolved:
            routing_warning = self.messages.get(
                "heritage_track.status.routing_warning",
                "Warning: {count} pedigree route conflicts remain",
            ).format(count=len(route_plan.unresolved))
            status_text = f"{status_text} | {routing_warning}"
            self.status_label.setToolTip("\n".join(route_plan.unresolved))
        else:
            self.status_label.setToolTip("")
        self.status_label.setText(status_text)

        # DEFERRED PERSISTENCE: Save collapsed families and temp positions once at the end
        if stale_collapsed:
            self.plugin.store.set_collapsed_families(sorted(self.collapsed_families, key=str.lower))
        # Update temp_positions at the very end (only for all_animals_mode)
        if self.all_animals_mode:
            self.temp_positions = {n: pos for n, pos in animal_positions.items() if n in display_nodes}

        self._hover_annotation.set_visible(False)
        self.canvas.draw_idle()

    def _show_splash_screen(self) -> None:
        """Show splash screen when no animals are selected or no scope filter is active.

        Similar to ProgTrack main window behavior when no animal is selected.
        Displays the splash.png image with legal disclaimer text.
        """
        # Switch to splash widget
        self.stack.setCurrentWidget(self.splash_widget)

        self.node_positions = {}
        self.family_positions = {}
        self.family_routes = {}
        self._route_plan = None
        self.node_meta.clear()
        self._ghost_nodes = set()
        self._chronological_undated_nodes = set()
        self.selected_nodes.clear()
        self.temp_positions.clear()

        # Update status label
        mode_text = self.messages.get("heritage_track.status.mode_all", "All animals mode")
        instruction_status = self.messages.get("heritage_track.splash.status", "No scope selected")
        self.status_label.setText(f"{mode_text} | {instruction_status}")

        self._hover_annotation.set_visible(False)

    def _node_at_mouse(self, event, pixel_threshold: float = 14.0) -> Optional[str]:
        if event.x is None or event.y is None:
            return None

        nearest_name = None
        nearest_dist = float("inf")


        for name, (x, y) in self.node_positions.items():
            sx, sy = self.ax.transData.transform((x, y))
            dx = sx - event.x
            dy = sy - event.y
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_name = name

        if nearest_name is None or nearest_dist > pixel_threshold:
            return None
        return nearest_name

    def _on_mouse_press(self, event) -> None:
        # Middle button (wheel press) starts panning.
        if event.button == 2 and event.inaxes == self.ax and event.xdata is not None and event.ydata is not None:
            self.pan_active = True
            self.pan_start = (event.xdata, event.ydata)
            # Cache background for blitting during pan
            self.canvas.draw()
            self._pan_background = self.canvas.copy_from_bbox(self.ax.bbox)
            return

        if event.inaxes != self.ax:
            return

        node = self._node_at_mouse(event)
        node_kind = str(self.node_meta.get(node, {}).get("kind", "animal")) if node else ""

        # Right mouse button -> toggle kinship selection
        if event.button == 3 and node and node_kind == "animal":
            if node in self.selected_nodes:
                self.selected_nodes.remove(node)
            else:
                self.selected_nodes.add(node)
            self._show_coefficients_dialog()
            self.refresh_graph(keep_view=True)
            return

        if event.button == 3 and node:
            return

        # Left mouse button -> single click on node adds to selection (multi-select)
        # Double-click on empty space clears filter-based selection and starts fresh
        if event.button == 1:
            if not node:
                current_time = time.time() * 1000
                is_empty_double_click = (
                    current_time - self._last_empty_click_time
                ) < self._double_click_threshold_ms
                self._last_empty_click_time = current_time
                self._last_click_time = 0.0
                self._last_click_node = None
                if self._pending_selection_timer:
                    self._pending_selection_timer.stop()
                    self._pending_selection_timer = None
                self._pending_selection = None
                if is_empty_double_click:
                    self._last_empty_click_time = 0.0
                    self._clear_filter_selection()
                return

            # Check for double-click using timer-based detection (more reliable than event.dblclick)
            current_time = time.time() * 1000  # Convert to milliseconds
            is_double_click = (
                node_kind == "animal" and
                (
                    bool(getattr(event, "dblclick", False)) or (
                        self._last_click_node == node and
                        (current_time - self._last_click_time) < self._double_click_threshold_ms
                    )
                )
            )

            if is_double_click:
                # Double-click: open node editor for ALL animal nodes (selected or not, archived or not, heritage-only or not)
                # Reset click state BEFORE opening editor to prevent any interference
                self._last_click_time = 0.0
                self._last_click_node = None
                # Cancel any pending selection timer (don't add to selection on double-click)
                if self._pending_selection_timer:
                    self._pending_selection_timer.stop()
                    self._pending_selection_timer = None
                self._pending_selection = None
                # Cancel drag mode to prevent selection-add on release
                self.drag_active = False
                self.drag_node = None
                self.drag_group_nodes.clear()
                self._open_node_editor(node)
                return

            # Store click info for double-click detection
            self._last_click_time = current_time
            self._last_click_node = node

            if event.xdata is None or event.ydata is None:
                return

            # Start drag mode for all draggable nodes (animals and families)
            # On release without drag, we'll add to selection
            node_x, node_y = self.node_positions.get(node, (event.xdata, event.ydata))
            self.drag_active = True
            self.drag_node = node
            self.drag_group_nodes = set(self.family_members.get(node, set())) if node_kind == "family" else set()
            self.drag_offset = (node_x - event.xdata, node_y - event.ydata)
            self.click_start_pos = (event.xdata, event.ydata)
            self.is_dragging = False
            self._drag_background = None
            self._drag_artist_map.clear()

    def _on_mouse_move(self, event) -> None:
        # Pan with middle button held.
        if self.pan_active and self.pan_start is not None:
            if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
                return

            dx = event.xdata - self.pan_start[0]
            dy = event.ydata - self.pan_start[1]
            cur_xlim = self.ax.get_xlim()
            cur_ylim = self.ax.get_ylim()
            new_xlim = (cur_xlim[0] - dx, cur_xlim[1] - dx)
            new_ylim = (cur_ylim[0] - dy, cur_ylim[1] - dy)
            new_xlim, new_ylim = self._apply_aspect_fill(new_xlim, new_ylim)
            self.ax.set_xlim(new_xlim)
            self.ax.set_ylim(new_ylim)
            self.current_xlim = new_xlim
            self.current_ylim = new_ylim
            self.pan_start = (event.xdata, event.ydata)
            # Fast pan: restore cached background and blit without re-rendering axes
            if self._pan_background is not None:
                self.canvas.restore_region(self._pan_background)
                self.canvas.blit(self.ax.bbox)
            return

        # Drag selected node while left mouse is held.
        if self.drag_active and self.drag_node is not None:
            if event.xdata is None or event.ydata is None:
                return

            if not self.is_dragging and self.click_start_pos is not None:
                dx = event.xdata - self.click_start_pos[0]
                dy = event.ydata - self.click_start_pos[1]
                distance = (dx * dx + dy * dy) ** 0.5
                if distance > self.drag_threshold:
                    self.is_dragging = True
                    self._begin_drag_blit()
                    # Cancel pending selection when drag starts (this is a drag, not a click)
                    if self._pending_selection_timer:
                        self._pending_selection_timer.stop()
                        self._pending_selection_timer = None
                    self._pending_selection = None

            if self.is_dragging:
                new_x = event.xdata + self.drag_offset[0]
                new_y = event.ydata + self.drag_offset[1]
                if self.drag_group_nodes:
                    current_x, current_y = self.node_positions.get(self.drag_node, (new_x, new_y))
                    delta_x = new_x - current_x
                    delta_y = new_y - current_y
                    for member in self.drag_group_nodes:
                        base_x, base_y = self.node_positions.get(member, self.temp_positions.get(member, (0.0, 0.0)))
                        self.temp_positions[member] = (base_x + delta_x, base_y + delta_y)
                else:
                    self.temp_positions[self.drag_node] = (new_x, new_y)
                self._redraw_dragged_nodes()
            return

        # Normal hover behavior.
        if event.inaxes != self.ax:
            if self._hover_annotation.get_visible():
                self._hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return

        node = self._node_at_mouse(event, pixel_threshold=10.0)
        if not node:
            if self._hover_annotation.get_visible():
                self._hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return

        is_ghost = node in getattr(self, '_ghost_nodes', set())
        genotype = (self.node_meta.get(node, {}).get("genotype") or "").strip()

        # Show tooltip for ghost nodes or nodes with genotype
        if not genotype and not is_ghost:
            if self._hover_annotation.get_visible():
                self._hover_annotation.set_visible(False)
                self.canvas.draw_idle()
            return

        x, y = self.node_positions.get(node, (0.0, 0.0))
        self._hover_annotation.xy = (x, y)

        if is_ghost:
            # Show ghost node tooltip with click hints
            display_label = self.node_meta.get(node, {}).get("display_label") or self._get_node_display_label(node)
            tooltip_text = self.messages.get(
                "heritage_track.node.tooltip.ghost",
                "{name} (click to add to selection)"
            ).format(name=display_label)
        else:
            tooltip_text = self.messages.get(
                "heritage_track.node.tooltip.genotype",
                "Genotype: {genotype}"
            ).format(genotype=genotype)

        self._hover_annotation.set_text(tooltip_text)
        self._hover_annotation.set_visible(True)
        self.canvas.draw_idle()

    def _dragged_animal_nodes(self) -> Set[str]:
        nodes_to_draw: Set[str] = set()
        if self.drag_node and not self._is_family_node(self.drag_node):
            nodes_to_draw.add(self.drag_node)
        if self.drag_group_nodes:
            nodes_to_draw.update(self.drag_group_nodes)
        return nodes_to_draw

    def _begin_drag_blit(self) -> None:
        """Prepare reusable animated artists and a background without dragged nodes."""
        self._drag_artist_map.clear()
        for node in self._dragged_animal_nodes():
            meta = self.node_meta.get(node, {})
            marker = meta.get("marker_artist")
            label = meta.get("label_artist")
            f_artist = meta.get("f_artist")
            undated_artist = meta.get("undated_artist")
            if marker is None or label is None:
                continue
            artists = (marker, label, f_artist, undated_artist)
            for artist in artists:
                if artist is not None:
                    artist.set_animated(True)
            self._drag_artist_map[node] = artists

        if not self._drag_artist_map:
            self._drag_background = None
            return

        self.canvas.draw()
        self._drag_background = self.canvas.copy_from_bbox(self.ax.bbox)
        self._redraw_dragged_nodes()

    @staticmethod
    def _position_drag_artists(
        artists: Tuple[Any, ...],
        x: float,
        y: float,
    ) -> None:
        marker, label, f_artist, undated_artist = (*artists, None)[:4]
        if hasattr(marker, "set_offsets"):
            marker.set_offsets([[x, y]])
        else:
            marker.set_data([x], [y])
        label.xy = (x, y)
        label.set_position((0, -14))
        if f_artist is not None:
            f_artist.xy = (x, y)
            f_artist.set_position((0, -27))
        if undated_artist is not None:
            undated_artist.xy = (x, y)
            undated_artist.set_position((10, 7))

    def _redraw_dragged_nodes(self) -> None:
        """Move existing node artists and blit without allocating new artists."""
        if self._drag_background is None or not self._drag_artist_map:
            return

        self.canvas.restore_region(self._drag_background)

        for node, artists in self._drag_artist_map.items():
            x, y = self.temp_positions.get(node, self.node_positions.get(node, (0.0, 0.0)))
            self._position_drag_artists(artists, x, y)
            for artist in artists:
                if artist is not None:
                    self.ax.draw_artist(artist)
        self.canvas.blit(self.ax.bbox)

    def _finish_drag_blit(self) -> None:
        for artists in self._drag_artist_map.values():
            for artist in artists:
                if artist is not None:
                    artist.set_animated(False)
        self._drag_background = None
        self._drag_artist_map.clear()

    def _on_mouse_release(self, event) -> None:
        if event.button == 2:
            self.pan_active = False
            self.pan_start = None
            self._pan_background = None
            if self.settings.get("show_grid", False):
                self.refresh_graph(keep_view=True)
            else:
                # Force full redraw to ensure clean state after pan
                self.canvas.draw_idle()
            return

        if event.button == 1:
            if self.drag_active and self.drag_node is not None:
                if self.is_dragging:
                    chronological_mode = (
                        self.settings.get(
                            "vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED
                        )
                        == VERTICAL_LAYOUT_CHRONOLOGICAL
                    )
                    if self.drag_group_nodes:
                        positions_to_save: Dict[str, Tuple[float, float]] = {}
                        for member in sorted(self.drag_group_nodes, key=str.lower):
                            x, y = self.temp_positions.get(member, self.node_positions.get(member, (0.0, 0.0)))
                            sx, sy = self._snap_to_grid(x, y)
                            if chronological_mode:
                                sy = self.node_positions.get(member, (sx, sy))[1]
                            self.temp_positions[member] = (sx, sy)
                            if self.all_animals_mode:
                                positions_to_save[member] = (sx, sy)
                        if positions_to_save:
                            self.plugin.store.set_node_positions_batch(positions_to_save)
                    elif not self._is_family_node(self.drag_node):
                        x, y = self.temp_positions.get(self.drag_node, self.node_positions.get(self.drag_node, (0.0, 0.0)))
                        sx, sy = self._snap_to_grid(x, y)
                        if chronological_mode:
                            sy = self.node_positions.get(self.drag_node, (sx, sy))[1]
                        self.temp_positions[self.drag_node] = (sx, sy)
                        # Save the position to heritage_store for persistence
                        if self.all_animals_mode:
                            self.plugin.store.set_node_position(self.drag_node, (sx, sy))
                    self._finish_drag_blit()
                    self.refresh_graph(keep_view=True)
                elif self._is_family_node(self.drag_node):
                    self._toggle_family_collapsed(self.drag_node)
                    self.refresh_graph(keep_view=True)
                else:
                    # Click on animal node (not drag): delay selection-add to allow double-click detection
                    pending_node = self.drag_node
                    # Clear drag state FIRST before setting up timer
                    self.drag_active = False
                    self.drag_node = None
                    self.drag_group_nodes.clear()
                    self.drag_offset = (0.0, 0.0)
                    self.click_start_pos = None
                    self.is_dragging = False
                    self._finish_drag_blit()
                    # Now setup pending selection
                    self._pending_selection = pending_node
                    self._pending_selection_timer = QTimer(self)
                    self._pending_selection_timer.setSingleShot(True)
                    self._pending_selection_timer.timeout.connect(self._commit_pending_selection)
                    self._pending_selection_timer.start(int(self._double_click_threshold_ms))
                    return  # Return early since we already cleaned up

            # Cleanup for drag cases (not single-click)
            self.drag_active = False
            self.drag_node = None
            self.drag_group_nodes.clear()
            self.drag_offset = (0.0, 0.0)
            self.click_start_pos = None
            self.is_dragging = False
            self._finish_drag_blit()

    def _commit_pending_selection(self) -> None:
        """Commit pending selection after double-click threshold passes (single click case)."""
        if self._pending_selection:
            self._add_animal_to_selection(self._pending_selection)
        self._pending_selection = None
        self._pending_selection_timer = None

    def _on_scroll(self, event) -> None:
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            return

        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        zoom_factor = 1.2 if event.button == "down" else 0.8

        xdata, ydata = event.xdata, event.ydata
        new_xlim = (
            xdata - (xdata - cur_xlim[0]) * zoom_factor,
            xdata + (cur_xlim[1] - xdata) * zoom_factor,
        )
        new_ylim = (
            ydata - (ydata - cur_ylim[0]) * zoom_factor,
            ydata + (cur_ylim[1] - ydata) * zoom_factor,
        )
        new_xlim, new_ylim = self._apply_aspect_fill(new_xlim, new_ylim)

        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        self.current_xlim = new_xlim
        self.current_ylim = new_ylim
        if self.settings.get("vertical_layout_mode") == VERTICAL_LAYOUT_CHRONOLOGICAL:
            self._configure_chronological_axis()
        if self.settings.get("show_grid", False):
            self.refresh_graph(keep_view=True)
        else:
            self.canvas.draw_idle()

    def _on_resize(self, _event) -> None:
        if self.current_xlim is None or self.current_ylim is None:
            return
        new_xlim, new_ylim = self._apply_aspect_fill(self.current_xlim, self.current_ylim)
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        self.current_xlim = new_xlim
        self.current_ylim = new_ylim
        if self.settings.get("vertical_layout_mode") == VERTICAL_LAYOUT_CHRONOLOGICAL:
            self._configure_chronological_axis()
        self.canvas.draw_idle()

    def _open_node_editor(self, node: str) -> None:
        if not self._can('heritage.edit_links'):
            self._deny()
            return
        meta = self.node_meta.get(node, {})
        fill_color = str(meta.get("fill_color_raw", ""))
        genotype = str(meta.get("genotype", ""))

        animals = getattr(self.app, "animals", {}) or {}
        archived = getattr(self.app, "archived", {}) or {}
        record = animals.get(node) or (archived.get(node) if isinstance(archived, dict) else {}) or {}
        is_heritage_only = self.plugin.is_heritage_only(node)
        sex = self.plugin.get_effective_sex(node, record if isinstance(record, dict) else None)
        animal_species = (record.get("species") or "").strip() if isinstance(record, dict) else ""
        # For heritage-only animals, get species from heritage store
        if not animal_species and is_heritage_only:
            animal_species = self.plugin.store.get_species(node) or ""
        parentage = self.plugin.get_parentage(node, record if isinstance(record, dict) else None)
        mother = parentage.get("egg_donor", "") or self._none_label()
        father = parentage.get("sperm_donor", "") or self._none_label()

        engine = self.plugin.build_engine()
        mother_options, father_options = self._get_parent_dropdown_options(
            engine,
            target_species=animal_species,
            exclude_node=node,
        )

        dlg = NodeEditDialog(
            self,
            self.messages,
            node,
            fill_color,
            genotype,
            mother_options=mother_options,
            father_options=father_options,
            mother=mother,
            father=father,
            sex=sex,
            allow_name_edit=False,
            allow_remove=is_heritage_only,
            animal_species=animal_species,
            sex_editable=is_heritage_only,
            parent_options_provider=lambda required, species: self.plugin.parent_candidate_options(
                required,
                species,
                node,
            ),
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        if dlg.remove_requested():
            confirm = QMessageBox.warning(
                self,
                self.messages.get("heritage_track.node.remove.title", "Remove animal"),
                self.messages.get(
                    "heritage_track.node.remove.confirm",
                    "Remove this Heritage-only animal?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

            if not self.plugin.delete_heritage_only_animal(node):
                QMessageBox.warning(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get(
                        "heritage_track.error.remove_failed",
                        "Failed to remove the selected Heritage-only animal.",
                    ),
                )
                return

            # Remove from all selection tracking
            self.selected_nodes.discard(node)
            self.temp_positions.pop(node, None)
            # Remove from main selection lists
            if hasattr(self.app, 'selected_animals') and node in self.app.selected_animals:
                self.app.selected_animals.remove(node)
            if hasattr(self.app, '_selected_heritage_only') and node in self.app._selected_heritage_only:
                self.app._selected_heritage_only.remove(node)
            # Refresh main animal list so heritage-only animal disappears
            if hasattr(self.app, '_refresh_list'):
                self.app._refresh_list()
            self._show_coefficients_dialog()
            self.refresh_graph()
            return

        values = dlg.values()
        mother_value, mother_status = self.plugin.resolve_parent_reference(values.get("mother", ""), animal_species)
        father_value, father_status = self.plugin.resolve_parent_reference(values.get("father", ""), animal_species)
        if mother_status == "ambiguous" or father_status == "ambiguous":
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get(
                    "heritage_track.error.parent_ambiguous",
                    "Parent name is ambiguous. Please select the full animal identity.",
                ),
            )
            return

        validation_error = self._validate_parent_selection(
            node,
            mother_value,
            father_value,
            engine=engine,
            target_species=animal_species,
            explicit_missing_parents={
                values.get(field, "")
                for field, marker in (
                    ("mother", "mother_allows_missing"),
                    ("father", "father_allows_missing"),
                )
                if values.get(marker)
            },
        )
        if validation_error:
            QMessageBox.warning(self, self.messages.get("error.title", "Error"), validation_error)
            return

        updated_parentage = dict(parentage)
        updated_parentage["egg_donor"] = mother_value
        updated_parentage["sperm_donor"] = father_value

        self.plugin.save_parentage(node, updated_parentage, source="plugin")

        # Create heritage-only placeholders for new parents with correct sex and species
        mother_name = mother_value
        father_name = father_value
        if mother_name or father_name:
            self.plugin._ensure_parent_placeholders(mother_name, father_name, animal_species)

        if is_heritage_only:
            self.plugin.set_manual_sex(node, values.get("sex", ""))
        self.plugin.store.set_node_visual(node, values.get("genotype", ""), values.get("fill_color", ""))
        self.refresh_graph(keep_view=True)
        refresh_list = getattr(self.app, "_refresh_list", None)
        if callable(refresh_list):
            refresh_list()

    def _load_species_options(self) -> List[str]:
        """Load species list from ProgTrack's Species_List.txt file."""
        from pathlib import Path
        species_path = Path(__file__).resolve().parent.parent / "Resources" / "Species_List.txt"
        if not species_path.is_file():
            return []
        values: List[str] = []
        seen_lower: set = set()
        try:
            with open(species_path, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    entry = raw_line.strip()
                    if not entry or entry.startswith("#"):
                        continue
                    entry_lower = entry.lower()
                    if entry_lower in seen_lower:
                        continue
                    seen_lower.add(entry_lower)
                    values.append(entry)
        except Exception:
            return []
        return values

    def _open_create_animal_dialog(self) -> None:
        """Open dialog to create a new heritage-only (placeholder) animal."""
        if not self._can('heritage.edit_links'):
            self._deny()
            return

        # Load species options
        species_options = self._load_species_options()

        engine = self.plugin.build_engine()
        # Initially load all parent options (no species filter yet)
        mother_options, father_options = self._get_parent_dropdown_options(engine)

        dlg = NodeEditDialog(
            self,
            self.messages,
            "",  # empty name for new animal
            "",  # no fill color
            "",  # no genotype
            mother_options=mother_options,
            father_options=father_options,
            mother="",
            father="",
            sex="",
            allow_name_edit=True,  # allow entering name for new animal
            allow_remove=False,    # can't remove an animal that doesn't exist yet
            animal_species="",
            species_options=species_options,
            parent_options_provider=lambda required, selected_species: self.plugin.parent_candidate_options(
                required,
                selected_species,
            ),
        )
        dlg.setWindowTitle(self.messages.get("heritage_track.node.edit.title_new", "Add Placeholder Animal"))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        values = dlg.values()
        name = values.get("animal_name", "").strip()

        # Validate name
        if not name:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("heritage_track.error.name_required", "Animal name is required."),
            )
            return

        # Check if animal already exists in core
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        if name in animals:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("heritage_track.error.name_exists_core", "This animal already exists in the main ProgTrack database."),
            )
            return

        # Check if animal already exists in heritage
        existing_entries = self.plugin.store.get_all_entries()
        if name in existing_entries:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("heritage_track.error.name_exists_heritage", "This animal already exists in heritage data."),
            )
            return

        species = values.get("species", "")

        # Validate parent selection
        mother_value, mother_status = self.plugin.resolve_parent_reference(values.get("mother", ""), species)
        father_value, father_status = self.plugin.resolve_parent_reference(values.get("father", ""), species)
        if mother_status == "ambiguous" or father_status == "ambiguous":
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get(
                    "heritage_track.error.parent_ambiguous",
                    "Parent name is ambiguous. Please select the full animal identity.",
                ),
            )
            return

        validation_error = self._validate_parent_selection(
            name,
            mother_value,
            father_value,
            engine=engine,
            target_species=species,
            explicit_missing_parents={
                values.get(field, "")
                for field, marker in (
                    ("mother", "mother_allows_missing"),
                    ("father", "father_allows_missing"),
                )
                if values.get(marker)
            },
        )
        if validation_error:
            QMessageBox.warning(self, self.messages.get("error.title", "Error"), validation_error)
            return

        # Create the heritage-only animal
        success = self.plugin.create_heritage_only_animal(
            name=name,
            mother=mother_value,
            father=father_value,
            genotype=values.get("genotype", ""),
            fill_color=values.get("fill_color", ""),
            sex=values.get("sex", ""),
            species=species,
            birth_date=values.get("birth_date", ""),
            explicit_custom_parents={
                values.get(field, "")
                for field, marker in (
                    ("mother", "mother_allows_missing"),
                    ("father", "father_allows_missing"),
                )
                if values.get(marker)
            },
        )

        if not success:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("heritage_track.error.create_failed", "Failed to create Heritage animal."),
            )
            return

        # Create heritage-only placeholders for new parents with correct sex and species
        mother_name = mother_value
        father_name = father_value
        if mother_name or father_name:
            self.plugin._ensure_parent_placeholders(mother_name, father_name, species)

        refresh_list = getattr(self.app, "_refresh_list", None)
        if callable(refresh_list):
            refresh_list()
        self.refresh_graph(keep_view=True)

    def _save_coeff_dialog_pos(self) -> None:
        if self.coeff_dialog is not None:
            self._coeff_dialog_pos = self.coeff_dialog.pos()

    def _show_coefficients_dialog(self) -> None:
        names = sorted(self.selected_nodes, key=str.lower)
        if len(names) < 2:
            if self.coeff_dialog is not None:
                self._coeff_dialog_pos = self.coeff_dialog.pos()
                self.coeff_dialog.hide()
            return

        engine = self.plugin.build_engine()
        calculator = InbreedingCalculator(engine.get_genetic_parent_map())

        if self.coeff_dialog is None:
            self.coeff_dialog = CoefficientDialog(self, self.messages, names, calculator)
            self.coeff_dialog.finished.connect(self._save_coeff_dialog_pos)
        else:
            if self.coeff_dialog.isVisible():
                self._coeff_dialog_pos = self.coeff_dialog.pos()
            self.coeff_dialog.update_data(self.messages, names, calculator)

        if not self.coeff_dialog.isVisible():
            self.coeff_dialog.show()
        if self._coeff_dialog_pos is not None:
            self.coeff_dialog.move(self._coeff_dialog_pos)
        self.coeff_dialog.raise_()
        self.coeff_dialog.activateWindow()


class HeritageTrackPlugin:
    """Main plugin object used by ProgTrack integration hooks."""

    def __init__(self, app):
        self.app = app
        self.messages = getattr(app, "messages", {}) or {}
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.store = HeritageStore(self.plugin_dir, app.backend)
        self.store.load()
        self._engine_cache = PedigreeEngineCache()

        self.window: Optional[HeritageTrackWidget] = None

    def update_language(self, messages: Dict[str, Any]) -> None:
        self.messages = messages or {}
        if self.window is not None:
            try:
                self.window.update_language(self.messages)
            except RuntimeError:
                self.window = None

    def get_parentage(self, animal_name: Optional[str], record: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        return self.store.get_parentage(animal_name, record)

    def _core_record(self, animal_name: Optional[str]) -> Optional[Dict[str, Any]]:
        key = str(animal_name or "").strip()
        if not key:
            return None
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        if key in animals and isinstance(animals[key], dict):
            return animals[key]
        archived = getattr(self.app, "archived", {}) or {}
        if isinstance(archived, dict) and key in archived and isinstance(archived[key], dict):
            return archived[key]
        return None

    def _is_core_animal(self, animal_name: Optional[str]) -> bool:
        key = str(animal_name or "").strip()
        if not key:
            return False
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        archived = getattr(self.app, "archived", {}) or {}
        return key in animals or (isinstance(archived, dict) and key in archived)

    @staticmethod
    def _record_is_inactive(record: Dict[str, Any]) -> bool:
        return bool(
            record.get("archived")
            or record.get("death_date")
            or record.get("sterbedatum")
        )

    def parent_candidate_options(
        self,
        required_sex: str,
        target_species: str = "",
        exclude_animal: str = "",
    ) -> List[str]:
        """Return live, same-species candidates for a controlled parent picker."""
        required = self.store._normalize_sex(required_sex)
        species = str(target_species or "").strip()
        excluded = str(exclude_animal or "").strip()
        candidates: List[str] = []

        active = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        for key, record in active.items():
            if key == excluded or not isinstance(record, dict) or self._record_is_inactive(record):
                continue
            record_species = str(record.get("species", "") or "").strip()
            if species and record_species != species:
                continue
            if self.get_effective_sex(key, record) == required:
                candidates.append(key)

        archived = getattr(self.app, "archived", {}) or {}
        archived_keys = set(archived) if isinstance(archived, dict) else set()
        for key, record in self.store.get_all_entries().items():
            if (
                key == excluded
                or key in active
                or key in archived_keys
                or not isinstance(record, dict)
                or self._record_is_inactive(record)
            ):
                continue
            record_species = str(record.get("species", "") or "").strip()
            if species and record_species != species:
                continue
            if self.get_effective_sex(key, record) == required:
                candidates.append(key)

        return sorted(set(candidates), key=str.casefold)

    def create_parent_group(self, animal_name: Optional[str], record: Optional[Dict[str, Any]] = None):
        values = self.get_parentage(animal_name, record)
        source = record if isinstance(record, dict) else self._core_record(animal_name) or {}
        species = str(source.get("species", "") or "").strip()
        options = {
            "egg_donor": self.parent_candidate_options("female", species, str(animal_name or "")),
            "sperm_donor": self.parent_candidate_options("male", species, str(animal_name or "")),
            "surrogate_mother": self.parent_candidate_options("female", species, str(animal_name or "")),
            "surrogate_father": self.parent_candidate_options("male", species, str(animal_name or "")),
        }
        return build_parent_group(self.messages, values, options)

    def read_parent_group(self, parent_fields: Dict[str, Any]) -> Dict[str, str]:
        return extract_parent_values(parent_fields)

    def save_parentage(self, animal_name: str, parent_values: Dict[str, Any], source: str = "plugin") -> None:
        self.store.set_parentage(animal_name, parent_values, source=source)

    def get_settings(self) -> Dict[str, Any]:
        return self.store.get_settings()

    def set_settings(self, settings: Dict[str, Any]) -> None:
        self.store.set_settings(settings)

    def sync_from_record(self, animal_name: str, record: Dict[str, Any], in_main_animals: bool = True) -> None:
        self.store.sync_from_record(animal_name, record, in_main_animals=in_main_animals)

    def is_heritage_only(self, animal_name: str) -> bool:
        key = str(animal_name or "").strip()
        if not key or self._is_core_animal(key):
            return False
        return key in self.store.get_all_entries()

    def delete_heritage_only_animal(self, animal_name: str) -> bool:
        key = str(animal_name or "").strip()
        if not key:
            return False
        if not self.is_heritage_only(key):
            return False
        return self.store.delete_animal(key)

    def set_manual_sex(self, animal_name: str, sex: Optional[str]) -> None:
        if self._is_core_animal(animal_name):
            return
        self.store.set_manual_sex(animal_name, sex)

    def get_manual_sex(self, animal_name: str) -> str:
        if self._is_core_animal(animal_name):
            return ""
        return self.store.get_manual_sex(animal_name)

    def get_effective_sex(self, animal_name: Optional[str], fallback_record: Optional[Dict[str, Any]] = None) -> str:
        core_record = self._core_record(animal_name)
        if core_record is not None:
            explicit = self.store._normalize_sex(core_record.get("sex", ""))
            if explicit:
                return explicit
            role = canonical_role_value(core_record.get("rolle", ""))
            if role in (ROLE_VALUE_SPENDER, ROLE_VALUE_AMME):
                return "female"
            if role == ROLE_VALUE_SAMENSP:
                return "male"
            return ""
        return self.store.get_effective_sex(animal_name, fallback_record)

    def _all_identity_records(self) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        archived = getattr(self.app, "archived", {}) or {}
        records.update(animals)
        if isinstance(archived, dict):
            records.update(archived)
        for key, entry in self.store.get_all_entries().items():
            if isinstance(entry, dict):
                records.setdefault(key, entry)
        return records

    def resolve_parent_reference(self, parent_name: str, target_species: str = "") -> Tuple[str, str]:
        """Resolve a parent text value to an IPID when possible.

        Returns (value, status), where status is "resolved", "missing", or
        "ambiguous". Missing values are allowed so the caller can create a
        heritage-only placeholder.
        """
        text = str(parent_name or "").strip()
        if not text:
            return "", "resolved"

        key, _record, status = resolve_animal_reference_text(
            text,
            self._all_identity_records(),
            target_species=target_species,
        )
        return key, status

    def create_heritage_only_animal(
        self,
        name: str,
        mother: str,
        father: str,
        genotype: str,
        fill_color: str,
        sex: str,
        species: str = "",
        birth_date: str = "",
        origin: str = "Heritage Track",
        explicit_custom_parents: Optional[Set[str]] = None,
    ) -> bool:
        raw_name = str(name or "").strip()
        if not raw_name:
            return False
        parts = split_animal_identity_key(raw_name)
        review_required = False
        review_reason = ""
        if parts is None:
            base_name = animal_base_name(raw_name)
            species_value = str(species or "").strip() or "Unknown species"
            raw_birth_date = str(birth_date or "").strip()
            if raw_birth_date:
                try:
                    birth_date = normalize_birth_date(raw_birth_date, required=True)
                    key = animal_identity_key(
                        base_name, species_value, birth_date, origin
                    )
                except ValueError:
                    return False
            else:
                birth_date = ""
                key = f"{base_name} | {species_value} | undated | {origin}"
                review_required = True
                review_reason = "Heritage-only placeholder is intentionally undated."
        else:
            base_name, species_value, birth_date, origin = parts
            key = raw_name
            if str(birth_date).strip().casefold() == "undated":
                birth_date = ""
                review_required = True
                review_reason = "Heritage-only placeholder is intentionally undated."
            else:
                try:
                    birth_date = normalize_birth_date(birth_date, required=True)
                except ValueError:
                    return False

        if self._is_core_animal(key):
            return False

        existing_entries = self.store.get_all_entries()
        if key in existing_entries:
            return False

        raw_mother = str(mother or "").strip()
        raw_father = str(father or "").strip()
        allowed_missing = {
            str(value or "").strip()
            for value in (explicit_custom_parents or set())
            if str(value or "").strip()
        }
        resolved_mother, mother_status = self.resolve_parent_reference(raw_mother, species_value)
        if mother_status == "ambiguous" or (
            raw_mother and mother_status == "missing" and raw_mother not in allowed_missing
        ):
            return False
        resolved_father, father_status = self.resolve_parent_reference(raw_father, species_value)
        if father_status == "ambiguous" or (
            raw_father and father_status == "missing" and raw_father not in allowed_missing
        ):
            return False

        self.store.set_parentage(
            key,
            {
                "egg_donor": resolved_mother,
                "sperm_donor": resolved_father,
                "surrogate_mother": "",
                "surrogate_father": "",
            },
            source="plugin",
        )
        self.store.set_node_visual(key, genotype, fill_color)
        self.store.set_manual_sex(key, sex)
        self.store.set_heritage_only(key, True)
        self.store.set_identity_fields(
            key,
            display_name=base_name,
            species=species_value,
            birth_date=birth_date,
            review_required=review_required,
            review_reason=review_reason,
        )
        return True

    def _parent_exists_in_system(self, parent_name: str) -> bool:
        """Check if a parent exists in ProgTrack animals, archived, or heritage store."""
        name = (parent_name or "").strip()
        if not name:
            return False
        resolved, status = self.resolve_parent_reference(name)
        if status == "ambiguous":
            return True
        if status == "resolved":
            name = resolved
        # Check in active animals
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        if name in animals:
            return True
        # Check in archived animals
        archived = getattr(self.app, "archived", {}) or {}
        if isinstance(archived, dict) and name in archived:
            return True
        # Check in heritage store (existing heritage-only animals)
        if self.store.get_all_entries().get(name):
            return True
        return False

    def _ensure_parent_placeholders(
        self,
        mother: str,
        father: str,
        offspring_species: str = "",
    ) -> None:
        """Create heritage-only placeholders for parents that don't exist in the system.

        New mothers are created with female sex, fathers with male sex.
        Both inherit the species from the offspring.
        """
        mother_name = (mother or "").strip()
        father_name = (father or "").strip()

        # Check and create mother placeholder if needed
        if mother_name and not self._parent_exists_in_system(mother_name):
            self.create_heritage_only_animal(
                name=mother_name,
                mother="",
                father="",
                genotype="",
                fill_color="",
                sex="female",
                species=offspring_species,
            )

        # Check and create father placeholder if needed
        if father_name and not self._parent_exists_in_system(father_name):
            self.create_heritage_only_animal(
                name=father_name,
                mother="",
                father="",
                genotype="",
                fill_color="",
                sex="male",
                species=offspring_species,
            )

    def build_engine(self) -> PedigreeEngine:
        animals = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        # Include archived animals in the pedigree graph
        archived = getattr(self.app, "archived", {}) or {}
        if isinstance(archived, dict):
            animals = {**animals, **archived}
        # keep store up-to-date with native fields for offspring/zuchttier etc.
        self.store.sync_from_animals(animals)

        # Build base-name → [(key, species)] map for resolving same-name animals by species.
        _base_to_variants: Dict[str, List[tuple]] = {}
        _heritage_entries = self.store.get_all_entries()
        _identity_records = dict(_heritage_entries)
        _identity_records.update(animals)
        for _k, _r in _identity_records.items():
            if not isinstance(_r, dict):
                continue
            _base = animal_base_name(_k, _r).strip()
            _sp = (_r.get("species") or "").strip()
            _birth = (_r.get("birth_date") or "").strip()
            _base_to_variants.setdefault(_base.lower(), []).append((_k, _sp, _birth))

        _store_lookup = self.store.get_parentage

        def _species_aware_lookup(animal_name: str, record) -> Dict[str, str]:
            base_parentage = _store_lookup(animal_name, record)
            if not _base_to_variants:
                return base_parentage
            child_rec = _identity_records.get(animal_name, {})
            child_species = (child_rec.get("species") or "").strip() if isinstance(child_rec, dict) else ""
            resolved: Dict[str, str] = {}
            for fld, parent_name in base_parentage.items():
                if not parent_name:
                    resolved[fld] = parent_name
                    continue
                if parent_name in _identity_records:
                    resolved[fld] = parent_name  # exact key match, no disambiguation needed
                    continue
                variants = _base_to_variants.get(parent_name.lower(), [])
                if not variants:
                    resolved[fld] = parent_name
                    continue
                if len(variants) == 1:
                    resolved[fld] = variants[0][0]
                    continue
                # Multiple animals share this base name: prefer the one with same species as child
                sp_matches = [k for k, sp, _birth in variants if sp == child_species]
                if len(sp_matches) == 1:
                    resolved[fld] = sp_matches[0]
                else:
                    plain = [k for k, _sp, _birth in variants if k == parent_name]
                    resolved[fld] = plain[0] if len(plain) == 1 else parent_name
            return resolved

        # Use cached engine for better performance (3-5x speedup)
        return self._engine_cache.get_engine(
            animals=animals,
            parent_lookup=_species_aware_lookup,
            heritage_entries=_heritage_entries,
        )

    def get_tab_widget(self) -> HeritageTrackWidget:
        if self.window is not None:
            try:
                return self.window
            except RuntimeError:
                self.window = None

        self.window = HeritageTrackWidget(self)
        return self.window

    def show_window(self) -> None:
        widget = self.get_tab_widget()
        widget.refresh_graph()
        widget.show()
        widget.raise_()
        widget.activateWindow()

    def refresh_if_visible(self) -> None:
        if self.window is None:
            return
        try:
            if self.window.isVisible():
                self.window.refresh_graph()
        except RuntimeError:
            self.window = None
