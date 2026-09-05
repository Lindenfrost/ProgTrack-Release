# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track main plugin implementation.

from __future__ import annotations

import logging
import hashlib
import json
import math
import os
import time
from copy import deepcopy
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
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
from matplotlib.transforms import Bbox
import matplotlib.patheffects as path_effects

from Plugins.core.animal_identity import (
    animal_base_name,
    animal_identity_key,
    animal_identity_label,
    normalize_birth_date,
    resolve_animal_reference_text,
    split_animal_identity_key,
)
from Plugins.core.animal_roles import ROLE_VALUE_AMME, ROLE_VALUE_SAMENSP, ROLE_VALUE_SPENDER, canonical_role_value
from Plugins.core.backend.errors import ConflictError
from Plugins.core.ui_icons import apply_icon
from Plugins.core.resource_catalogs import (
    UNKNOWN_SPECIES_VALUE,
    read_genotypes,
    ordered_species_for_display,
)

from .display_context import (
    DisplayContext,
    DisplayContextBuilder,
    RenderCacheEntry,
    RenderCacheKey,
    RenderCacheRegistry,
)
from .display_strategies import SelectedAnimalsStrategy
from .ghost_strategies import (
    ArchivedGhostStrategy,
    CompositeGhostStrategy,
    OffspringAndSiblingsGhostStrategy,
    VisibleFamilyCompletenessGhostStrategy,
)
from .engine_cache import PedigreeEngineCache
from .heritage_store import HeritageStore, PARENT_KEYS
from .inbreeding import InbreedingCalculator
from .layout_pipeline import (
    LayoutPipeline,
    VERTICAL_LAYOUT_CHRONOLOGICAL,
    VERTICAL_LAYOUT_PARTNER_NORMALIZED,
    compute_chronological_positions,
    family_node_id,
    parse_complete_birth_date_ordinal,
)
from .pedigree_engine import PedigreeEngine
from .pedigree_router import (
    GeometryValidationError,
    LAYOUT_MODE_FOCUSED,
    LAYOUT_MODE_OVERVIEW,
    PedigreeRouter,
    RoutePlan,
)
from .ui_parent_fields import ParentSelector, build_parent_group, extract_parent_values


class ParentageCommandError(ValueError):
    """Localized, user-facing validation error from the parentage boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code or "invalid_parentage")
        self.message = str(message)


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
        genotype_editable: bool = True,
        parents_editable: bool = True,
        parents_read_only_core: bool = True,
        genotype_options: Optional[List[str]] = None,
        color_editable: bool = True,
        genotype_options_provider: Optional[Callable[[str], List[str]]] = None,
        parent_options_provider: Optional[Callable[[str, str], List[str]]] = None,
    ):
        super().__init__(parent)
        self.messages = messages
        self._animal_name = (animal_name or "").strip()
        self._selected_color = fill_color or ""
        self._allow_name_edit = bool(allow_name_edit)
        self._none_label = messages.get("heritage_track.value.none", "none")
        self._sex = self._normalize_sex(sex)
        # Keep the editability decision with the dialog instance.  The
        # enabled state is only a UI affordance; ``values()`` also uses this
        # flag so a disabled Core field cannot be changed by programmatic
        # selection in a test or by a future caller.
        self._sex_editable = bool(sex_editable)
        self._remove_requested = False
        self._species_options = species_options or []
        self._parent_options_provider = parent_options_provider
        self._parents_editable = bool(parents_editable)
        self._parents_read_only_core = bool(parents_read_only_core)
        self._has_genotype_catalog = genotype_options is not None
        self._genotype_options = [str(value).strip() for value in (genotype_options or []) if str(value).strip()]
        self._color_editable = bool(color_editable)
        self._genotype_options_provider = genotype_options_provider

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
                for sp in ordered_species_for_display(self._species_options):
                    label = (
                        messages.get("species.unknown", UNKNOWN_SPECIES_VALUE)
                        if sp.casefold() == UNKNOWN_SPECIES_VALUE.casefold()
                        else sp
                    )
                    self.species_combo.addItem(label, sp)
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
            # Node editing is deliberately a controlled picker.  Typing is
            # still available as transient search (ParentSelector restores
            # the last committed identity when no option is selected), but a
            # new free-text parent can only be created through the explicit
            # Heritage command.
            allow_custom=False,
            parent=self,
        )
        self.father_combo = ParentSelector(
            messages,
            father_options or [],
            father,
            allow_custom=False,
            parent=self,
        )
        _sp_hint = messages.get("heritage_track.node.edit.species_filter_hint", "(filtered by species: {sp})").replace("{sp}", animal_species) if animal_species else ""
        form.addRow(messages.get("heritage_track.node.edit.mother", "Mother:"), self.mother_combo)
        form.addRow(messages.get("heritage_track.node.edit.father", "Father:"), self.father_combo)

        if not self._parents_editable:
            readonly_parent_key = (
                "heritage_track.parents.read_only_core"
                if self._parents_read_only_core
                else "heritage_track.parents.permission_required"
            )
            readonly_parent_tip = messages.get(
                readonly_parent_key,
                (
                    "Parents are maintained by the main animal record."
                    if self._parents_read_only_core
                    else "Heritage parent editing requires the edit-links permission."
                ),
            )
            for parent_widget in (self.mother_combo, self.father_combo):
                parent_widget.setEnabled(False)
                parent_widget.setToolTip(readonly_parent_tip)
                parent_widget.setAccessibleDescription(readonly_parent_tip)
                parent_widget.setStyleSheet("QComboBox { background: #f0f0f0; color: #666; }")
        if _sp_hint:
            sp_lbl = QLabel(_sp_hint)
            sp_lbl.setStyleSheet("color:grey;font-size:8pt;")
            form.addRow("", sp_lbl)

        self.sex_combo = QComboBox()
        self.sex_combo.addItem(messages.get("heritage_track.sex.male", "Male"), "male")
        self.sex_combo.addItem(messages.get("heritage_track.sex.female", "Female"), "female")
        self.sex_combo.addItem(messages.get("heritage_track.sex.unknown", "Unknown"), "unknown")
        sex_idx = self.sex_combo.findData(self._sex)
        if sex_idx < 0:
            sex_idx = self.sex_combo.findData("unknown")
        self.sex_combo.setCurrentIndex(sex_idx)
        self.sex_combo.setEnabled(self._sex_editable)
        if not self._sex_editable:
            readonly_sex_tip = messages.get(
                "heritage_track.sex.read_only_core",
                "Sex is maintained by the main animal record.",
            )
            # Match the greyed-out parent controls and expose the same reason
            # to screen readers and keyboard users.  A disabled combo cannot
            # receive focus or open its popup, while the canonical value stays
            # visible for copying/inspection.
            self.sex_combo.setToolTip(readonly_sex_tip)
            self.sex_combo.setAccessibleDescription(readonly_sex_tip)
            self.sex_combo.setStyleSheet(
                "QComboBox { background: #f0f0f0; color: #666; }"
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
        pick_btn.setEnabled(self._color_editable)
        clear_btn.setEnabled(self._color_editable)
        if not self._color_editable:
            color_tip = messages.get(
                "heritage_track.genotype_color.read_only",
                "Genotype display colour is managed by the assigned permissions.",
            )
            pick_btn.setToolTip(color_tip)
            clear_btn.setToolTip(color_tip)
        color_row.addWidget(pick_btn)
        color_row.addWidget(clear_btn)

        form.addRow(messages.get("heritage_track.node.edit.genotype_color", "Genotype display colour:"), color_row)

        if genotype_editable and (self._genotype_options or self._allow_name_edit or self._has_genotype_catalog):
            self.genotype_edit = QComboBox()
            self.genotype_edit.addItem(messages.get("heritage_track.value.none", "none"), "")
            for genotype_value in self._genotype_options:
                self.genotype_edit.addItem(genotype_value, genotype_value)
            genotype_index = self.genotype_edit.findData(str(genotype or "").strip())
            if genotype_index >= 0:
                self.genotype_edit.setCurrentIndex(genotype_index)
        else:
            self.genotype_edit = QLineEdit(genotype or "")
        self.genotype_edit.setEnabled(bool(genotype_editable))
        if not genotype_editable:
            self.genotype_edit.setToolTip(messages.get(
                "heritage_track.genotype.read_only_core",
                "Genotype is maintained by the main animal record.",
            ))
            self.genotype_edit.setStyleSheet(
                "QLineEdit { background: #f0f0f0; color: #666; }"
            )
        form.addRow(messages.get("heritage_track.node.edit.genotype", "Genotype:"), self.genotype_edit)
        if self.species_combo is not None and isinstance(self.genotype_edit, QComboBox):
            self.species_combo.currentIndexChanged.connect(self._refresh_genotype_options)

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

    def _refresh_genotype_options(self) -> None:
        if self.species_combo is None or not isinstance(self.genotype_edit, QComboBox):
            return
        species = str(self.species_combo.currentData() or "").strip()
        if self._genotype_options_provider is not None and species:
            options = list(self._genotype_options_provider(species) or [])
        else:
            options = []
        current = str(self.genotype_edit.currentData() or "").strip()
        self.genotype_edit.blockSignals(True)
        self.genotype_edit.clear()
        self.genotype_edit.addItem(self.messages.get("heritage_track.value.none", "none"), "")
        for genotype_value in options:
            self.genotype_edit.addItem(genotype_value, genotype_value)
        idx = self.genotype_edit.findData(current)
        self.genotype_edit.setCurrentIndex(idx if idx >= 0 else 0)
        self.genotype_edit.blockSignals(False)

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
        if text in {"f", "female", "woman", "femmina", "femminile", "weiblich", "w", "\u0436", "\u0436\u0435\u043d", "\u0436\u0435\u043d\u0441\u043a\u0438\u0439", "\u0441\u0430\u043c\u043a\u0430"}:
            return "female"
        if text in {"u", "unknown", "unknown sex", "unbekannt", "sconosciuto", "sconosciuta"}:
            return "unknown"
        return ""

    def values(self) -> Dict[str, Any]:
        animal_name = self._animal_name
        if self.name_edit is not None:
            animal_name = self.name_edit.text().strip()
        if isinstance(self.genotype_edit, QComboBox):
            genotype_value = str(self.genotype_edit.currentData() or "").strip()
        else:
            genotype_value = self.genotype_edit.text().strip()
        result = {
            "animal_name": animal_name,
            "fill_color": self._selected_color,
            "genotype": genotype_value,
            "mother": self._normalize_parent_value(self.mother_combo.selected_value()),
            "father": self._normalize_parent_value(self.father_combo.selected_value()),
            "mother_allows_missing": self.mother_combo.allows_missing_value(),
            "father_allows_missing": self.father_combo.allows_missing_value(),
            # Core sex is authoritative.  Preserve the value read from Core
            # even if code changes the disabled combo programmatically.
            "sex": (
                self._normalize_sex(self.sex_combo.currentData())
                if self._sex_editable
                else self._sex
            ),
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
        # Last accepted complete render frame.  Painting, hit testing and
        # export helpers may consult this immutable boundary; transient
        # matplotlib artists remain a view concern only.
        self._render_cache_entry: Optional[RenderCacheEntry] = None
        self._route_collections: List[LineCollection] = []
        self._relationship_highlight_collections: List[LineCollection] = []
        self._rendered_families: Dict[str, Dict[str, object]] = {}
        self._rendered_engine: Optional[Any] = None
        self._route_gap_pixel_scale: Tuple[float, float] = (1.0, 1.0)
        self._route_gap_radius_pixels = 2.75
        self._rendered_artist_scale = 1.0
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
        # The genotype legend is an in-axes overlay. Keep its artist and
        # normalized lower-left anchor separate from node dragging so a click
        # on the legend never starts a tree pan or node move.
        self._legend_artist: Optional[Any] = None
        self._cycle_nodes: Set[str] = set()
        self._legend_anchor_axes: Optional[Tuple[float, float]] = None
        self._legend_dragging = False
        self._legend_drag_start_px: Optional[Tuple[float, float]] = None
        self._legend_drag_start_anchor: Optional[Tuple[float, float]] = None
        self._malformed_f_nodes: Set[str] = set()
        # Screen-independent spatial index for hover/click hit testing.  The
        # previous implementation transformed every node on every mouse move,
        # which made dense trees increasingly sluggish.  A small data-space
        # grid keeps the candidate set local while preserving nearest-marker
        # semantics.
        self._hit_grid: Dict[Tuple[int, int], List[str]] = {}
        self._hit_grid_cell_size = 2.0
        # One precedence-resolved store snapshot per refresh.  Lookups outside
        # a refresh continue to read the store normally.
        self._render_store_animals: Optional[Dict[str, Dict[str, Any]]] = None
        self._render_core_animals: Optional[Dict[str, Dict[str, Any]]] = None
        self._render_backend_revision: int = 0
        self._render_pedigree_revision: str = "genesis"
        self._render_transaction_active = False
        self.no_selection_mode = True
        # ``layout_mode`` is the single Focused/Selection-overview decision
        # for a complete render transaction.  The canonical selection is
        # cached here so viewport/aspect helpers cannot reread a differently
        # shaped app list during the same refresh.
        self.layout_mode = LAYOUT_MODE_OVERVIEW
        self._canonical_selection_ids: Tuple[str, ...] = ()
        self._force_relayout = False
        self._active_position_cache_key: Optional[str] = None
        self._active_position_cache_user = "guest"
        self._active_position_cache_revision = ""
        self._active_position_cache_dependencies: Set[str] = set()
        self._position_cache_notice = ""

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
            except (AttributeError, KeyError, OSError, TypeError, ValueError):
                logging.getLogger(__name__).debug(
                    "Could not restore HeritageTrack generation limit",
                    exc_info=True,
                )
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
            except (AttributeError, KeyError, OSError, TypeError, ValueError):
                logging.getLogger(__name__).debug(
                    "Could not persist HeritageTrack generation limit",
                    exc_info=True,
                )
        # The generation limit is part of the selection/layout cache key.  A
        # new limit therefore checks its own entry instead of invalidating or
        # overwriting positions belonging to another display type.
        self._force_relayout = False


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

    def _animal_text_path_effects(self) -> List[Any]:
        """Return a DPI-correct three-pixel white halo for animal text."""
        dpi = max(1.0, float(self.figure.dpi))
        return [
            path_effects.withStroke(
                linewidth=3.0 * 72.0 / dpi,
                foreground="white",
            )
        ]

    def _route_pixel_scale(self) -> Tuple[float, float]:
        x0, y0 = self.ax.transData.transform((0.0, 0.0))
        x1, _ = self.ax.transData.transform((1.0, 0.0))
        _, y1 = self.ax.transData.transform((0.0, 1.0))
        return (
            max(1.0, abs(float(x1 - x0))),
            max(1.0, abs(float(y1 - y0))),
        )

    def _recompute_route_visual_gaps(self, route_plan: Optional[RoutePlan] = None) -> None:
        """Rebuild marker/crossing gaps for the final current pixel scale."""
        if route_plan is not None:
            self._route_plan = route_plan
        elif self._route_plan is None and self._render_cache_entry is not None:
            # Resize/zoom is a pixel-only view operation.  Start from a
            # detached copy of the accepted frame so the immutable cache
            # remains valid while matplotlib receives new gap geometry.
            self._route_plan = self._render_cache_entry.route_plan.to_mutable()
        if self._route_plan is None:
            return
        x_pixels_per_unit, y_pixels_per_unit = self._route_pixel_scale()
        self._route_gap_pixel_scale = (x_pixels_per_unit, y_pixels_per_unit)
        marker_radius_pixels = (
            (16.7 * self._rendered_artist_scale * float(self.figure.dpi) / 72.0)
            / 2.0
        ) + 1.5
        marker_obstacles = self._pedigree_router.marker_obstacles(
            self._route_plan.animal_positions,
            half_width=marker_radius_pixels / x_pixels_per_unit,
            half_height=marker_radius_pixels / y_pixels_per_unit,
        )
        junction_radius_pixels = (
            (8.4 * float(self.figure.dpi) / 72.0) / 2.0
        ) + 1.2
        raw_junction_obstacles = self._pedigree_router.marker_obstacles(
            self._route_plan.family_positions,
            half_width=junction_radius_pixels / x_pixels_per_unit,
            half_height=junction_radius_pixels / y_pixels_per_unit,
        )
        junction_obstacles = {
            f"@{family_id}": rect
            for family_id, rect in raw_junction_obstacles.items()
        }
        self._pedigree_router.recompute_line_gaps(
            self._route_plan,
            animal_gap_obstacles=marker_obstacles,
            junction_gap_obstacles=junction_obstacles,
            recompute_crossings=False,
        )

    @staticmethod
    def _render_revision(payload: Any) -> str:
        """Return a deterministic revision token for a render dependency."""
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _f_record(self, node: str, engine: PedigreeEngine) -> Dict[str, Any]:
        """Return the authoritative identity record used by F provenance."""
        record = engine.animals.get(node) if isinstance(engine.animals, dict) else None
        if isinstance(record, dict):
            return record
        record = engine.heritage_entries.get(node) if isinstance(engine.heritage_entries, dict) else None
        if isinstance(record, dict):
            return record
        return self._get_node_record(node)

    @staticmethod
    def _find_malformed_f_nodes(
        parent_map: Dict[str, Tuple[Optional[str], Optional[str]]],
        known_nodes: Set[str],
    ) -> Set[str]:
        """Find malformed genetic roots and propagate them to descendants."""
        calculator = InbreedingCalculator(parent_map)
        malformed = set(calculator.cycle_nodes)
        reverse: Dict[str, Set[str]] = defaultdict(set)
        for child, parents in parent_map.items():
            for parent in parents:
                if not parent:
                    continue
                reverse[str(parent)].add(str(child))
                if str(parent) not in known_nodes:
                    malformed.add(str(child))

        pending = list(malformed)
        while pending:
            ancestor = pending.pop()
            for child in reverse.get(ancestor, set()):
                if child in malformed:
                    continue
                malformed.add(child)
                pending.append(child)
        return malformed

    def _f_lineage_fingerprint(
        self,
        node: str,
        parent_map: Dict[str, Tuple[Optional[str], Optional[str]]],
        engine: PedigreeEngine,
        *,
        store_animals: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        """Fingerprint one complete reachable genetic lineage.

        The global pedigree token is intentionally not part of this fingerprint:
        an unrelated component may receive a new global revision without
        invalidating a numerically identical lineage.
        """
        if not isinstance(store_animals, dict):
            store_animals = self._render_store_animals
        if not isinstance(store_animals, dict):
            store_animals = self.plugin.store.get_all_entries()
        rows: List[Tuple[str, str, Tuple[str, str], str]] = []
        visited: Set[str] = set()
        stack: Set[str] = set()

        def visit(current: str) -> None:
            key = str(current or "").strip()
            if not key:
                return
            if key in stack:
                rows.append(("<cycle>", key, ("", ""), ""))
                return
            if key in visited:
                return
            visited.add(key)
            pair = parent_map.get(key, (None, None))
            normalized_pair = tuple(str(value or "").strip() for value in pair)
            record = self._f_record(key, engine)
            stable_id = str(record.get("ipid", "") or key).strip()
            stored = store_animals.get(key, {}) if isinstance(store_animals, dict) else {}
            genetic_revision = ""
            if isinstance(stored, dict):
                genetic_revision = str(stored.get("genetic_parentage_revision", "") or "").strip()
            rows.append((stable_id, key, normalized_pair, genetic_revision))
            stack.add(key)
            for parent in normalized_pair:
                visit(parent)
            stack.remove(key)

        visit(node)
        return self._render_revision({"schema": "heritage-f-lineage.v1", "rows": sorted(rows)})

    def _compute_inbreeding_state(
        self,
        engine: PedigreeEngine,
        display_nodes: Set[str],
        *,
        show_f: bool,
    ) -> Tuple[Dict[str, float], Dict[str, str]]:
        """Resolve F and malformed state before an immutable frame is painted."""
        f_values: Dict[str, float] = {}
        f_status: Dict[str, str] = {}
        parent_map = engine.get_genetic_parent_map()
        calculator = InbreedingCalculator(parent_map)
        self._cycle_nodes = calculator.cycle_nodes
        known_nodes = set(engine.animals) | set(engine.heritage_entries)
        self._malformed_f_nodes = self._find_malformed_f_nodes(parent_map, known_nodes)
        if not show_f:
            # Topology validity is independent of the selected numeric label.
            # Keep the per-node status available so a malformed lineage can be
            # shown below an ID/birth-date/empty label as a diagnostic without
            # inventing an F value.
            for node in display_nodes:
                if node in self._malformed_f_nodes:
                    f_status[node] = "unavailable"
            return f_values, f_status

        required_nodes = set(parent_map)
        required_nodes.update(
            str(parent)
            for pair in parent_map.values()
            for parent in pair
            if parent
        )
        render_transaction = bool(self.__dict__.get("_render_transaction_active", False))
        current_revision = ""
        cache_snapshot = getattr(self.plugin, "_active_store_snapshot", None)
        if not render_transaction:
            # Explicit cache maintenance must validate against the latest
            # backend revision, not a session-local engine/store snapshot.
            # Render transactions deliberately keep using their captured
            # immutable snapshot so one frame cannot mix revisions.
            try:
                latest_snapshot, _latest_backend_revision = (
                    self.plugin.store.load_latest_with_revision()
                )
                cache_snapshot = latest_snapshot
                # Keep this session's rebuildable, not-yet-flushed cache
                # entries available while authoritative records come from the
                # fresh backend snapshot.  A concurrent authoritative edit is
                # still detected by the lineage fingerprint below; only a
                # missing derived value is filled from the local pending view.
                local_snapshot = self.plugin.store.load()
                if isinstance(local_snapshot, dict) and isinstance(cache_snapshot, dict):
                    local_derived = local_snapshot.get("derived_inbreeding_cache", {})
                    latest_derived = cache_snapshot.setdefault("derived_inbreeding_cache", {})
                    if isinstance(local_derived, dict) and isinstance(latest_derived, dict):
                        for key, value in local_derived.items():
                            if key not in latest_derived:
                                latest_derived[key] = deepcopy(value)
                    local_animals = local_snapshot.get("animals", {})
                    latest_animals = cache_snapshot.get("animals", {})
                    if isinstance(local_animals, dict) and isinstance(latest_animals, dict):
                        for key, local_entry in local_animals.items():
                            latest_entry = latest_animals.get(key)
                            if (
                                isinstance(local_entry, dict)
                                and isinstance(latest_entry, dict)
                                and "inbreeding_f_cache" not in latest_entry
                                and "inbreeding_f_cache" in local_entry
                            ):
                                latest_entry["inbreeding_f_cache"] = deepcopy(
                                    local_entry["inbreeding_f_cache"]
                                )
            except Exception:
                cache_snapshot = getattr(self.plugin, "_active_store_snapshot", None)
            if isinstance(cache_snapshot, dict):
                current_revision = str(
                    cache_snapshot.get("pedigree_revision", "") or "genesis"
                ).strip() or "genesis"
            else:
                current_revision = str(
                    self.plugin.store.get_pedigree_revision() or "genesis"
                )
        updates: Dict[str, Dict[str, Any]] = {}
        cache_keys: Dict[str, str] = {}
        fingerprint_animals = (
            cache_snapshot.get("animals", {})
            if isinstance(cache_snapshot, dict)
            else None
        )
        for node in sorted(required_nodes, key=str.casefold):
            fingerprint = self._f_lineage_fingerprint(
                node,
                parent_map,
                engine,
                store_animals=fingerprint_animals,
            )
            record = self._f_record(node, engine)
            stable_id = str(record.get("ipid", "") or "").strip() if isinstance(record, dict) else ""
            # ``engine.animals`` is the Core projection; its F metadata must
            # use the stable IPID in the dedicated Heritage-derived cache.
            heritage_keys = set(fingerprint_animals) if isinstance(fingerprint_animals, dict) else set()
            if (
                stable_id
                and isinstance(engine.animals, dict)
                and node in engine.animals
                and node not in heritage_keys
            ):
                cache_keys[node] = stable_id
            cache_key = cache_keys.get(node)
            if cache_key:
                cached = self.plugin.store.get_inbreeding_cache(
                    node, snapshot=cache_snapshot, cache_key=cache_key
                )
            else:
                cached = self.plugin.store.get_inbreeding_cache(
                    node, snapshot=cache_snapshot
                )
            if node in self._malformed_f_nodes:
                status = "unavailable"
                value = None
                cache_revision = (
                    cached.get("pedigree_revision", "")
                    if cached
                    and cached.get("status") == "unavailable"
                    and cached.get("lineage_fingerprint") == fingerprint
                    else current_revision
                )
            else:
                if (
                    cached
                    and cached.get("status") == "valid"
                    and cached.get("lineage_fingerprint") == fingerprint
                ):
                    status = "cached"
                    value = float(cached["value"])
                    # The global token may advance for an unrelated lineage;
                    # keep the original lineage revision so a cache hit does
                    # not become a needless derived write.
                    cache_revision = str(cached.get("pedigree_revision", "") or current_revision)
                else:
                    status = "calculated"
                    value = float(calculator.self_inbreeding_F(node))
                    cache_revision = current_revision
            updates[node] = {
                "value": value,
                "pedigree_revision": cache_revision,
                "lineage_fingerprint": fingerprint,
                "status": "valid" if value is not None else "unavailable",
            }
            if node in display_nodes:
                if value is not None:
                    f_values[node] = value
                f_status[node] = status if value is not None else "unavailable"

        # Rendering is a read-only transaction.  Calculated values are carried
        # in the immutable render entry; persistence belongs to explicit data
        # mutation commands and must never be queued by a redraw.  Retain the
        # non-render call path for explicit F-cache maintenance and existing
        # callers that intentionally request derived persistence.
        if updates and not render_transaction:
            try:
                if cache_keys:
                    changed = self.plugin.store.set_inbreeding_cache_batch(
                        updates, persist=False, cache_keys=cache_keys
                    )
                else:
                    changed = self.plugin.store.set_inbreeding_cache_batch(
                        updates, persist=False
                    )
                if changed:
                    self.plugin.schedule_store_flush()
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not queue Heritage inbreeding cache update"
                )
        return f_values, f_status

    def _render_cache_key(
        self,
        selected_animals: List[str],
        chronological_mode: bool,
        display_mode: Optional[str] = None,
    ) -> RenderCacheKey:
        master_track = getattr(self.app, "master_track", None)
        user_id = getattr(master_track, "current_username", None) if master_track else None
        cached_selection = getattr(self, "_canonical_selection_ids", ())
        canonical_selection = (
            cached_selection
            if tuple(selected_animals) == cached_selection
            else self._canonicalize_selection(selected_animals)
        )
        semantic_mode = str(display_mode or self.layout_mode).strip() or self.layout_mode
        display_mode = f"{semantic_mode}:{'chronological' if chronological_mode else 'partner_normalized'}"
        return RenderCacheKey.create(
            user_id=user_id or "anonymous",
            selection=canonical_selection,
            selection_type="selected",
            display_mode=display_mode,
        )

    def _position_cache_user_id(self) -> str:
        master_track = getattr(self.app, "master_track", None)
        value = str(getattr(master_track, "current_username", "") or "").strip()
        return value or "guest"

    def _position_cache_key(
        self,
        selected_animals: List[str],
        *,
        display_mode: Optional[str] = None,
        chronological_mode: Optional[bool] = None,
    ) -> str:
        """Build a locale/DPI/viewport-neutral key for logical positions."""
        canonical = tuple(
            self._canonicalize_selection(selected_animals)
            if tuple(selected_animals) != self._canonical_selection_ids
            else self._canonical_selection_ids
        )
        chronological = (
            chronological_mode
            if chronological_mode is not None
            else self.settings.get("vertical_layout_mode") == VERTICAL_LAYOUT_CHRONOLOGICAL
        )
        semantic_mode = str(display_mode or self.layout_mode).strip() or self.layout_mode
        payload = {
            "schema": "heritage-position-cache.v1",
            "selection": canonical,
            "selection_type": "selected",
            "display_mode": semantic_mode,
            "vertical_layout_mode": "chronological" if chronological else "partner_normalized",
            "max_generations": int(self._max_generations),
            "show_heritage_only": bool(self.settings.get("show_heritage_only", True)),
            "exclude_archived": bool(self.settings.get("exclude_archived", False)),
        }
        return self._render_revision(payload)

    @staticmethod
    def _position_cache_dependencies(
        engine: PedigreeEngine,
        display_nodes: Set[str],
    ) -> Set[str]:
        dependencies = {str(node).strip() for node in display_nodes if str(node).strip()}
        parent_map = getattr(engine, "child_to_parents", {}) or {}
        for child in display_nodes:
            values = parent_map.get(child, {}) if isinstance(parent_map, dict) else {}
            if isinstance(values, dict):
                dependencies.update(
                    str(parent).strip()
                    for parent in values.values()
                    if str(parent or "").strip()
                )
        return dependencies

    def _position_cache_dependency_revision(
        self,
        engine: PedigreeEngine,
        dependency_ids: Set[str],
        *,
        core_snapshot: Optional[Dict[str, Dict[str, Any]]] = None,
        store_snapshot: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Return a revision scoped to the current layout dependencies.

        Backend writes carry one aggregate revision for the complete Heritage
        record.  Using that value for position validity would invalidate an
        unrelated selection whenever another lineage is edited.  Build the
        token from the exact dependency records and parent map captured for
        this render instead.
        """
        if core_snapshot is None:
            core_snapshot = getattr(self, "_render_core_animals", None)
        if not isinstance(core_snapshot, dict):
            core_snapshot = getattr(self.plugin, "_active_core_snapshot", None)
        if not isinstance(core_snapshot, dict):
            core_snapshot = self._copy_core_records(self.app)

        if store_snapshot is None:
            store_snapshot = getattr(self, "_render_store_animals", None)
        store_entries = (
            store_snapshot.get("animals", {})
            if isinstance(store_snapshot, dict)
            else {}
        )
        if not isinstance(store_entries, dict):
            store_entries = {}

        temporary = getattr(self.plugin, "_temporary_dummies", {})
        records: Dict[str, Dict[str, Any]] = {}
        for node in dependency_ids:
            key = str(node or "").strip()
            if not key:
                continue
            record = core_snapshot.get(key)
            if not isinstance(record, dict):
                record = store_entries.get(key)
            if not isinstance(record, dict) and isinstance(temporary, dict):
                record = temporary.get(key)
            records[key] = deepcopy(record) if isinstance(record, dict) else {}

        parent_map = getattr(engine, "child_to_parents", {}) or {}
        return self.plugin.store.build_position_dependency_revision(
            dependency_ids,
            parent_map,
            records,
        )

    def _save_position_cache(
        self,
        positions: Dict[str, Tuple[float, float]],
        *,
        confirm_delete_on_failure: bool = False,
    ) -> bool:
        """Persist one complete map, retrying exactly once on backend failure."""
        key = self._active_position_cache_key
        if not key or not positions:
            return True
        payload = {
            node: tuple(point)
            for node, point in positions.items()
            if node in self._active_position_cache_dependencies
            and not self._is_family_node(node)
        }
        if not payload:
            return True
        last_error: Optional[Exception] = None
        for _attempt in range(2):
            try:
                result = self.plugin.store.set_position_cache_entry(
                    self._active_position_cache_user,
                    key,
                    payload,
                    self._active_position_cache_revision or "genesis",
                    self._active_position_cache_dependencies,
                    selection_type=(
                        f"selected:{self.layout_mode}:"
                        f"{self.settings.get('vertical_layout_mode', VERTICAL_LAYOUT_PARTNER_NORMALIZED)}"
                    ),
                )
                evicted = str(result.get("evicted_key", "") or "").strip()
                if evicted:
                    self._position_cache_notice = self.messages.get(
                        "heritage_track.position_cache.full",
                        "Position cache limit reached; the oldest layout was replaced.",
                    )
                return True
            except Exception as exc:  # pragma: no cover - backend-specific failures
                last_error = exc
                logging.getLogger(__name__).exception(
                    "Could not persist Heritage selection position cache"
                )

        self._position_cache_notice = self.messages.get(
            "heritage_track.position_cache.write_failed",
            "The pedigree layout could not be saved; the previous saved layout was kept.",
        )
        if confirm_delete_on_failure and key:
            answer = QMessageBox.question(
                self,
                self.messages.get("heritage_track.position_cache.write_failed_title", "Layout not saved"),
                self.messages.get(
                    "heritage_track.position_cache.delete_failed_entry",
                    "Delete the saved layout for this selection?",
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                try:
                    self.plugin.store.remove_position_cache_entry(
                        self._active_position_cache_user, key
                    )
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not remove failed Heritage position cache entry"
                    )
        _ = last_error
        return False

    def _build_render_cache_entry(
        self,
        *,
        engine: PedigreeEngine,
        selected_animals: List[str],
        display_nodes: Set[str],
        ghost_nodes: Set[str],
        levels: Dict[str, int],
        families: Dict[str, Dict[str, Any]],
        positions: Dict[str, Tuple[float, float]],
        locked_positions: Dict[str, Tuple[float, float]],
        route_plan: RoutePlan,
        bounds: Tuple[Tuple[float, float], Tuple[float, float]],
        f_values: Dict[str, float],
        f_status: Dict[str, str],
        obstacle_labels: Dict[str, str],
        chronological_mode: bool,
        display_mode: str,
        source_revision: str = "",
        artist_scale: float = 1.0,
        chronological_undated_nodes: Optional[Set[str]] = None,
    ) -> RenderCacheEntry:
        """Freeze one complete render transaction before any artists paint."""
        record_index = {
            node: dict(self._get_node_record(node))
            for node in sorted(display_nodes, key=str.casefold)
        }
        node_metadata: Dict[str, Dict[str, Any]] = {}
        for node in sorted(display_nodes, key=str.casefold):
            record = record_index.get(node, {})
            is_heritage_only = self.plugin.is_heritage_only(node)
            role = canonical_role_value(record.get("rolle", ""))
            sex = self.plugin.get_effective_sex(node, record)
            visual = self.plugin.get_node_visual(
                node,
                fallback_genotype=str(record.get("genotype", "")),
                fallback_record=record,
            )
            fill_color_raw = visual.get("node_fill_color", "")
            fill_color = self._valid_fill(fill_color_raw)
            node_metadata[node] = {
                "display_label": self._get_node_display_label(node, record),
                "role": role,
                "sex": sex,
                "heritage_only": bool(is_heritage_only),
                "is_dead": bool(
                    record.get("death_date")
                    or record.get("sterbedatum")
                    or record.get("archived")
                ),
                "fill_color_raw": fill_color_raw,
                "fill_color": fill_color,
                "genotype": visual.get("genotype", ""),
                "shape": self._resolve_shape(
                    node,
                    role,
                    sex,
                    engine,
                    is_heritage_only=is_heritage_only,
                ),
                "detail_text": self._get_node_detail_text(
                    node,
                    record,
                    f_values.get(node),
                    inbreeding_unavailable=node in self._malformed_f_nodes,
                ),
                "animal_id": self._get_node_public_id(node, record),
                "birth_date": str(
                    record.get("birth_date", "") or record.get("geburtsdatum", "")
                ).strip(),
            }
        parent_map = {
            child: dict(values)
            for child, values in engine.child_to_parents.items()
            if child in display_nodes
        }
        dependencies: Set[str] = set(display_nodes)
        for values in parent_map.values():
            dependencies.update(value for value in values.values() if value)
        core_revision = self._render_revision(
            {
                "backend_revision": int(self._render_backend_revision or 0),
                "records": {
                    node: record_index.get(node, {})
                    for node in sorted(record_index, key=str.casefold)
                },
            }
        )
        pedigree_revision = self._render_revision(parent_map)
        engine_revision = str(
            getattr(engine, "resolution_revision", "")
            or self._render_revision(
                {"nodes": sorted(engine.all_nodes, key=str.casefold), "parents": parent_map}
            )
        )
        layout_revision = self._render_revision(
            {
                "levels": levels,
                "families": families,
                "positions": positions,
                "locked": locked_positions,
                "routes": route_plan.routes,
                "display_mode": display_mode,
                "chronological": chronological_mode,
            }
        )
        diagnostics = tuple(
            list(route_plan.unresolved)
            + list(getattr(route_plan, "layout_diagnostics", ()))
            + list(route_plan.line_crossing_problems)
            + list(route_plan.route_obstacle_hits)
        )
        cache_key = self._render_cache_key(
            selected_animals,
            chronological_mode,
            display_mode=display_mode,
        )
        fatal = self._render_geometry_fatal_diagnostics(
            route_plan,
            positions,
            locked_positions=locked_positions,
            bounds=bounds,
        )
        # Singleton parking and other widget-level post-routing adjustments
        # happen after the router's own placement pass.  Reuse the router's
        # obstacle model as the final hard collision boundary so no render
        # cache entry can certify overlapping labels or markers.
        fatal.extend(
            item
            for item in self._render_node_collision_diagnostics(
                route_plan.animal_positions,
                obstacle_labels,
                show_inbreeding=bool(
                    self.settings.get("animal_label_detail", "inbreeding_f")
                    != "nothing"
                    or self._malformed_f_nodes & set(route_plan.animal_positions)
                ),
            )
            if item not in fatal
        )
        # A topology diagnostic is deliberately fatal for cache publication:
        # an unresolved frame may be inspected locally, but must never replace
        # the last accepted complete pedigree as a valid render transaction.
        fatal.extend(
            item
            for item in getattr(route_plan, "layout_diagnostics", ())
            if item not in fatal
        )
        return RenderCacheEntry(
            cache_key=cache_key,
            core_projection_revision=core_revision,
            pedigree_f_revision=pedigree_revision,
            engine_resolution_revision=engine_revision,
            logical_layout_revision=layout_revision,
            dependencies=frozenset(dependencies),
            record_index=record_index,
            canonical_selection=cache_key.canonical_selection,
            selection_type="selected",
            display_mode=f"{display_mode}:{'chronological' if chronological_mode else 'partner_normalized'}",
            effective_parent_map=parent_map,
            display_nodes=frozenset(display_nodes),
            ghost_nodes=frozenset(ghost_nodes),
            levels=levels,
            family_nodes=families,
            family_members=route_plan.family_members,
            positions=positions,
            locked_positions=locked_positions,
            route_plan=route_plan,
            obstacles=obstacle_labels,
            bounds=bounds,
            f_values=f_values,
            f_status=f_status,
            diagnostics=diagnostics,
            fatal_diagnostics=tuple(fatal),
            backend_revision=int(self._render_backend_revision or 0),
            source_revision=str(source_revision or ""),
            node_metadata=node_metadata,
            artist_scale=float(artist_scale or 1.0),
            chronological_undated_nodes=frozenset(
                chronological_undated_nodes or set()
            ),
            position_cache_key=str(getattr(self, "_active_position_cache_key", "") or ""),
            position_cache_user=str(getattr(self, "_active_position_cache_user", "") or ""),
            position_cache_revision=str(getattr(self, "_active_position_cache_revision", "") or ""),
            position_cache_dependencies=frozenset(
                getattr(self, "_active_position_cache_dependencies", set()) or set()
            ),
        )

    @staticmethod
    def _render_geometry_fatal_diagnostics(
        route_plan: RoutePlan,
        positions: Dict[str, Tuple[float, float]],
        *,
        locked_positions: Optional[Dict[str, Tuple[float, float]]] = None,
        bounds: Optional[Tuple[Tuple[float, float], Tuple[float, float]]] = None,
    ) -> List[str]:
        """Reject non-finite geometry before a frame can replace the view."""
        fatal: List[str] = []
        for point in route_plan.all_points():
            try:
                finite = len(point) == 2 and all(math.isfinite(float(value)) for value in point)
            except (TypeError, ValueError):
                finite = False
            if not finite:
                fatal.append("non-finite route geometry")
                break
        for node, point in positions.items():
            try:
                finite = len(point) == 2 and all(math.isfinite(float(value)) for value in point)
            except (TypeError, ValueError):
                finite = False
            if not finite:
                fatal.append(f"{node}: non-finite node position")
        for node, point in (locked_positions or {}).items():
            try:
                finite = len(point) == 2 and all(math.isfinite(float(value)) for value in point)
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                fatal.append(f"{node}: non-finite locked position")
        if bounds is not None:
            try:
                finite = len(bounds) == 2 and all(
                    len(point) == 2
                    and all(math.isfinite(float(value)) for value in point)
                    for point in bounds
                )
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                fatal.append("non-finite render bounds")
        return fatal

    def _render_node_collision_diagnostics(
        self,
        positions: Mapping[str, Tuple[float, float]],
        labels: Mapping[str, str],
        *,
        show_inbreeding: bool,
    ) -> List[str]:
        """Return final label/marker collisions before a frame is published.

        The router performs collision avoidance during placement, but the
        widget can still add detached/singleton coordinates afterwards.  A
        final sweep over the same obstacle rectangles used by the router is a
        cheap publication boundary and prevents an accidentally overlapping
        frame from replacing the last accepted one.
        """
        obstacles = self._pedigree_router.node_obstacles(
            positions,
            labels,
            show_inbreeding,
        )
        problems: List[str] = []
        ordered = sorted(obstacles.items(), key=lambda item: item[1].left)
        for index, (first_node, first_rect) in enumerate(ordered):
            for second_node, second_rect in ordered[index + 1 :]:
                if second_rect.left >= first_rect.right:
                    break
                if (
                    first_rect.right > second_rect.left
                    and second_rect.right > first_rect.left
                    and first_rect.top > second_rect.bottom
                    and second_rect.top > first_rect.bottom
                ):
                    problems.append(
                        f"{first_node}/{second_node}: animal markers or labels overlap"
                    )
        return problems

    def _replace_route_collections(self) -> None:
        """Redraw only genealogy lines after masks or zoom scale change."""
        for collection in self._route_collections:
            if collection.axes is self.ax:
                collection.remove()
        self._route_collections = []
        if self._route_plan is None:
            return

        route_batches: Dict[
            Tuple[str, float, float],
            List[List[Tuple[float, float]]],
        ] = defaultdict(list)
        for family_id, family in self._rendered_families.items():
            if family_id not in self._route_plan.family_positions:
                continue
            mother = str(family.get("mother", "")).strip()
            father = str(family.get("father", "")).strip()
            for parent in (mother, father):
                if parent not in self._route_plan.animal_positions:
                    continue
                color = "#cccccc" if parent in self._ghost_nodes else "#666666"
                for first, second in self._route_plan.draw_segments(
                    family_id,
                    parent,
                    gap_radius_pixels=self._route_gap_radius_pixels,
                    pixel_scale=self._route_gap_pixel_scale,
                ):
                    route_batches[(color, 0.9, 1.1)].append([first, second])
            for child in family.get("children", []):
                if child not in self._route_plan.animal_positions:
                    continue
                color = "#cccccc" if child in self._ghost_nodes else "black"
                for first, second in self._route_plan.draw_segments(
                    family_id,
                    child,
                    gap_radius_pixels=self._route_gap_radius_pixels,
                    pixel_scale=self._route_gap_pixel_scale,
                ):
                    route_batches[(color, 1.0, 1.0)].append([first, second])

        for (color, linewidth, zorder), segments in route_batches.items():
            if not segments:
                continue
            collection = LineCollection(
                segments,
                colors=[color],
                linewidths=[linewidth],
                zorder=zorder,
            )
            self.ax.add_collection(collection)
            self._route_collections.append(collection)

    def _replace_relationship_highlights(self) -> None:
        """Redraw the selected relationship path with current pixel gaps."""
        for collection in self._relationship_highlight_collections:
            if collection.axes is self.ax:
                collection.remove()
        self._relationship_highlight_collections = []
        if (
            self._route_plan is None
            or len(self.selected_nodes) != 2
        ):
            return

        selected = sorted(self.selected_nodes, key=str.casefold)
        rendered_parent_map = None
        if self._rendered_engine is not None:
            rendered_parent_map = self._rendered_engine.get_genetic_parent_map()
        elif self._render_cache_entry is not None:
            rendered_parent_map = {
                child: (
                    values.get("egg_donor") or None,
                    values.get("sperm_donor") or None,
                )
                for child, values in self._render_cache_entry.effective_parent_map.items()
            }
        if rendered_parent_map is None:
            return
        calculator = InbreedingCalculator(rendered_parent_map)
        if calculator.kinship_phi(selected[0], selected[1]) <= 0:
            return
        segments = self._bfs_relationship_path(
            selected[0],
            selected[1],
            self._rendered_engine,
            self._route_plan.animal_positions,
            self._route_plan.family_positions,
            self._rendered_families,
        )
        if not segments:
            return
        collection = LineCollection(
            segments,
            colors=["#e67e22"],
            linewidths=[2.5],
            zorder=1.8,
        )
        collection.set_capstyle("round")
        self.ax.add_collection(collection)
        self._relationship_highlight_collections.append(collection)

    def _configure_subplot_geometry(self, chronological: bool) -> None:
        """Give the pedigree the full canvas except for real axis furniture.

        The genotype legend is an in-axes overlay.  It must never create a
        permanent blank column: large pedigrees need that horizontal space,
        while a small legend can be placed over whichever corner is clear in
        the current frame.
        """
        canvas_width, canvas_height = self.canvas.get_width_height()
        if canvas_width <= 0 or canvas_height <= 0:
            return
        left_px = 58.0 if chronological else 4.0
        bottom_px = 16.0 if chronological else 4.0
        right_px = 4.0
        top_px = 4.0
        left = min(0.22, left_px / canvas_width)
        right = max(left + 0.35, 1.0 - (right_px / canvas_width))
        bottom = min(0.18, bottom_px / canvas_height)
        top = max(bottom + 0.35, 1.0 - (top_px / canvas_height))
        self.figure.subplots_adjust(
            left=left,
            right=min(0.997, right),
            top=min(0.997, top),
            bottom=bottom,
        )

    def _effective_axes_pixels(self) -> Tuple[float, float]:
        """Return the drawable axes rectangle before equal-aspect fitting."""
        canvas_width, canvas_height = self.canvas.get_width_height()
        subplot = self.figure.subplotpars
        width = canvas_width * max(0.01, subplot.right - subplot.left)
        height = canvas_height * max(0.01, subplot.top - subplot.bottom)
        return width, height

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
        self._configure_subplot_geometry(True)

    def _place_legend_overlay(self, legend) -> None:
        """Place an in-axes legend in the least obstructed free rectangle.

        The legend is an overlay, never a layout column. Corner-only
        placement is insufficient for dense overview trees because a corner
        can contain a real node while the middle of the canvas is free. A
        small deterministic grid of lower-left anchors is therefore scored
        against valid rendered animal boxes; the chosen rectangle remains
        fully inside the Axes and cannot clip the plot.
        """
        try:
            renderer = self.canvas.get_renderer()
            content_boxes: List[Bbox] = []
            for meta in self.node_meta.values():
                if meta.get("kind") != "animal":
                    continue
                artists = (
                    meta.get("marker_artist"),
                    meta.get("label_artist"),
                    meta.get("f_artist"),
                    meta.get("undated_artist"),
                )
                for artist in artists:
                    if artist is None or not artist.get_visible():
                        continue
                    box = artist.get_window_extent(renderer)
                    # Matplotlib may expose a 1x1 placeholder before a real
                    # data transform is assigned; it is not an obstruction.
                    if box.width > 1.0 and box.height > 1.0:
                        content_boxes.append(box)

            axes_box = self.ax.get_window_extent(renderer)
            if axes_box.width <= 2.0 or axes_box.height <= 2.0:
                raise RuntimeError("invalid axes bounds")

            legend.set_loc("lower left")
            legend.set_bbox_to_anchor((0.0, 0.0), transform=self.ax.transAxes)
            legend_box = legend.get_window_extent(renderer)
            width_norm = min(0.98, max(0.02, legend_box.width / axes_box.width))
            height_norm = min(0.98, max(0.02, legend_box.height / axes_box.height))
            # Leave room for Matplotlib borderaxespad so the rendered box
            # remains inside the Axes rather than only its anchor point.
            edge_margin = 0.03
            max_x = max(0.0, 1.0 - width_norm - edge_margin)
            max_y = max(0.0, 1.0 - height_norm - edge_margin)

            stored_position = self.settings.get("legend_pos")
            if isinstance(stored_position, (list, tuple)) and len(stored_position) == 2:
                try:
                    x = float(stored_position[0])
                    y = float(stored_position[1])
                except (TypeError, ValueError):
                    x = y = 0.0
                x = min(max_x, max(0.0, x))
                y = min(max_y, max(0.0, y))
                legend.set_loc("lower left")
                legend.set_bbox_to_anchor((x, y), transform=self.ax.transAxes)
                self._legend_anchor_axes = (x, y)
                return

            grid = (0.0, 0.12, 0.25, 0.38, 0.52, 0.66, 0.80, 1.0)
            best = None
            for y_index, y_fraction in enumerate(grid):
                for x_index, x_fraction in enumerate(grid):
                    x = min(max_x, max(0.0, x_fraction * max_x))
                    y = min(max_y, max(0.0, y_fraction * max_y))
                    legend.set_loc("lower left")
                    legend.set_bbox_to_anchor((x, y), transform=self.ax.transAxes)
                    candidate_box = legend.get_window_extent(renderer)
                    overlap_count = 0
                    overlap_area = 0.0
                    for content_box in content_boxes:
                        overlap = Bbox.intersection(candidate_box, content_box)
                        if overlap is None or overlap.width <= 1.0 or overlap.height <= 1.0:
                            continue
                        overlap_count += 1
                        overlap_area += overlap.width * overlap.height
                    score = (
                        overlap_count,
                        round(overlap_area, 3),
                        -round(y, 4),
                        -round(x, 4),
                        y_index * len(grid) + x_index,
                    )
                    if best is None or score < best[0]:
                        best = (score, x, y)

            if best is None:
                best = ((0, 0.0, 0.0, 0.0, 0), 0.0, 0.0)
            _score, x, y = best
            legend.set_loc("lower left")
            legend.set_bbox_to_anchor((x, y), transform=self.ax.transAxes)
            self._legend_anchor_axes = (x, y)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            logging.getLogger(__name__).debug(
                "Could not place HeritageTrack legend using the overlap scorer",
                exc_info=True,
            )
            # Cosmetic fallback, still constrained to the plotting rectangle.
            legend.set_loc("lower left")
            legend.set_bbox_to_anchor((0.0, 0.0), transform=self.ax.transAxes)
            self._legend_anchor_axes = (0.0, 0.0)

    def _legend_hit(self, event: Any) -> bool:
        """Return whether a canvas event falls inside the visible legend."""
        legend = self._legend_artist
        if legend is None or not legend.get_visible() or event.inaxes != self.ax:
            return False
        try:
            renderer = self.canvas.get_renderer()
            bbox = legend.get_window_extent(renderer)
            return bool(bbox.contains(float(event.x), float(event.y)))
        except (AttributeError, TypeError, ValueError):
            return False

    def _legend_anchor_from_artist(self) -> Tuple[float, float]:
        """Recover the current lower-left anchor when no cached value exists."""
        if self._legend_anchor_axes is not None:
            return self._legend_anchor_axes
        legend = self._legend_artist
        try:
            renderer = self.canvas.get_renderer()
            axes_box = self.ax.get_window_extent(renderer)
            legend_box = legend.get_window_extent(renderer)
            if axes_box.width > 1.0 and axes_box.height > 1.0:
                return (
                    (legend_box.x0 - axes_box.x0) / axes_box.width,
                    (legend_box.y0 - axes_box.y0) / axes_box.height,
                )
        except (AttributeError, TypeError, ValueError):
            pass
        return (0.0, 0.0)

    def _finish_legend_drag(self, event: Any) -> None:
        """Persist a bounded axes-coordinate legend move and redraw."""
        try:
            if (
                self._legend_drag_start_px is None
                or self._legend_drag_start_anchor is None
                or self._legend_artist is None
            ):
                return
            renderer = self.canvas.get_renderer()
            axes_box = self.ax.get_window_extent(renderer)
            if axes_box.width <= 1.0 or axes_box.height <= 1.0:
                return
            dx = (float(event.x) - self._legend_drag_start_px[0]) / axes_box.width
            dy = (float(event.y) - self._legend_drag_start_px[1]) / axes_box.height
            x = self._legend_drag_start_anchor[0] + dx
            y = self._legend_drag_start_anchor[1] + dy

            # Measure the current legend footprint at the origin, then clamp
            # the lower-left anchor so the frame remains inside the Axes.
            legend = self._legend_artist
            legend.set_loc("lower left")
            legend.set_bbox_to_anchor((0.0, 0.0), transform=self.ax.transAxes)
            legend_box = legend.get_window_extent(renderer)
            width_norm = legend_box.width / axes_box.width
            height_norm = legend_box.height / axes_box.height
            edge_margin = 0.03
            max_x = max(0.0, 1.0 - width_norm - edge_margin)
            max_y = max(0.0, 1.0 - height_norm - edge_margin)
            x = min(max_x, max(0.0, x))
            y = min(max_y, max(0.0, y))
            self._legend_anchor_axes = (x, y)
            # Preserve the live widget settings (label detail, layout mode,
            # grid and visibility) while persisting only the moved legend.
            # Reloading the plugin record here could overwrite an in-memory
            # setting changed immediately before the drag.
            settings = dict(self.settings)
            settings["legend_pos"] = [x, y]
            self.settings = settings
            self.plugin.set_settings(settings)
            self.refresh_graph(keep_view=True)
        finally:
            self._legend_dragging = False
            self._legend_drag_start_px = None
            self._legend_drag_start_anchor = None

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
            # Chronological and partner-normalized layouts have independent
            # position-cache identities; let the selected mode restore its
            # own map (or create one on a miss).
            self._force_relayout = False
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
                logging.getLogger(__name__).exception(
                    "HeritageTrack selection callback failed while clearing selection"
                )

        # Selection identity is part of the persistent position-cache key.
        # Switching selection therefore checks that selection's own entry.
        self._force_relayout = False

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

        # Heritage-only animals are kept in their dedicated compatibility list;
        # the canonical helper merges that list with Core selection exactly
        # once.  Do not append the same key to both semantic registries.
        if is_heritage_only:
            if not hasattr(self.app, '_selected_heritage_only'):
                self.app._selected_heritage_only = []
            if animal_name not in self.app._selected_heritage_only:
                self.app._selected_heritage_only.append(animal_name)
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
                logging.getLogger(__name__).exception(
                    "HeritageTrack selection callback failed while adding animal"
                )

        # A ghost's coordinates belong to the previous display graph.  Once it
        # becomes an active selection, every visible branch can be re-ordered;
        # retaining the old temporary map would re-inject stale ghost positions
        # before the router sees the expanded graph.  Clear the transient cache
        # together with the relayout request. Persistent Overview positions are
        # still kept in the backend and are handled separately by refresh_graph.
        self.temp_positions.clear()
        self._force_relayout = False

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
        invalid_positions = self.plugin.store.get_invalid_node_positions()
        if invalid_positions:
            try:
                self.plugin.store.cleanup_invalid_node_positions()
            except Exception as exc:
                logging.getLogger(__name__).exception(
                    "Could not clean invalid Heritage node positions"
                )
                confirm = QMessageBox.warning(
                    self,
                    self.messages.get(
                        "heritage_track.error.geometry_cleanup_title",
                        "Invalid saved geometry",
                    ),
                    self.messages.get(
                        "heritage_track.error.geometry_cleanup_failed",
                        "Invalid saved geometry could not be cleaned. Remove the cached frame for this selection?",
                    )
                    + f"\n\n{exc}",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if confirm != QMessageBox.StandardButton.Yes:
                    return
                if self._render_cache_entry is not None:
                    self.plugin.remove_render_cache_entry(self._render_cache_entry.cache_key)
                    self._render_cache_entry = None

        # Refresh only concerns the current user's selection-scoped layout.
        # The historical global node_positions map is not part of this reset.
        selected_animals = list(self._canonicalize_selection())
        cache_key = self._position_cache_key(selected_animals)
        # Read all inputs from one current snapshot.  The aggregate backend
        # revision is intentionally not used for position validity: unrelated
        # pedigree edits must not evict this selection's coordinates.
        current_core = self.plugin._copy_core_records(self.app)
        current_store, current_backend_revision = self.plugin.store.load_latest_with_revision()
        current_engine = self.plugin.build_engine(
            sync=False,
            core_snapshot=current_core,
            store_snapshot=current_store,
            backend_revision=current_backend_revision,
        )
        current_nodes = (
            set(self._render_cache_entry.display_nodes)
            if self._render_cache_entry is not None
            and tuple(self._render_cache_entry.canonical_selection) == tuple(selected_animals)
            else set(current_engine.get_display_nodes(selected_animals))
        )
        cache_dependencies = self._position_cache_dependencies(
            current_engine, current_nodes
        )
        cache_revision = self._position_cache_dependency_revision(
            current_engine,
            cache_dependencies,
            core_snapshot=current_core,
            store_snapshot=current_store,
        )
        saved_entry = self.plugin.store.get_position_cache_entry(
            self._position_cache_user_id(),
            cache_key,
            pedigree_revision=cache_revision,
            dependency_ids=cache_dependencies,
        )

        if saved_entry:
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
        
        # Normal refresh without consuming the old entry.  refresh_graph()
        # replaces it only after a complete automatic frame is accepted.
        self._force_relayout = True
        self.selected_nodes.clear()
        self.temp_positions.clear()
        self.current_xlim = None
        self.current_ylim = None
        self._show_coefficients_dialog()
        self.refresh_graph()

    def _report_geometry_failure(self, error: object) -> None:
        """Keep the last accepted frame and expose a localized geometry error."""
        detail = str(error or "non-finite geometry")
        logging.getLogger(__name__).error("Rejected Heritage geometry: %s", detail)
        self.status_label.setToolTip(detail)
        self.status_label.setText(
            self.messages.get(
                "heritage_track.status.geometry_invalid",
                "Unable to render pedigree: invalid geometry",
            )
        )
        self._render_store_animals = None
        self._render_core_animals = None
        self.plugin._clear_active_projection_snapshot()
        self._drag_background = None
    
    def _cleanup_stale_positions(self, current_nodes: Set[str]) -> None:
        """Identify stale positions without mutating storage during a redraw.

        Position cleanup is deliberately deferred to an explicit reset or
        data mutation.  Refreshing a view must remain a read-only operation.
        """
        _ = current_nodes
    
    def _clear_all_saved_positions(self) -> None:
        """Clear all saved animal positions (for reset functionality)."""
        engine = self.plugin.build_engine()
        raw_store = self.plugin.store.load()
        raw_store_animals = raw_store.get("animals", {}) if isinstance(raw_store, dict) else {}
        self._render_store_animals = (
            raw_store_animals if isinstance(raw_store_animals, dict) else {}
        )
        selected_animals = list(self._canonicalize_selection())
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
        if not self._can('heritage.view'):
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
            # New Heritage-only dummies keep the normal editable sex control.
            sex_editable=True,
            allow_name_edit=True,
            parents_editable=self._can("heritage.edit_links"),
            parents_read_only_core=False,
            genotype_options=[],
            genotype_options_provider=self._genotype_options_for_species,
            color_editable=self._can("heritage.edit_genotype_colors"),
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
        *,
        with_status: bool = True,
    ) -> Tuple[List[str], List[str]]:
        # ``engine`` remains in the signature for compatibility with callers;
        # candidates deliberately come from actual records, not unresolved
        # graph labels or archived/dead animals.
        _ = engine
        return (
            self.plugin.parent_candidate_options("female", target_species, exclude_node, with_status=with_status),
            self.plugin.parent_candidate_options("male", target_species, exclude_node, with_status=with_status),
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

    def _view_data_aspect(self) -> float:
        """Return the display ratio of one vertical to one horizontal unit.

        Focused pedigrees may carry several generations of context around only
        a handful of selected animals.  Keeping the historical 1:1 data aspect
        while fitting that complete vertical context compressed names and
        sibling branches horizontally.  A reduced vertical/horizontal display
        ratio retains a full generation row at a readable height. Chronology
        receives a little more horizontal capacity because nearby dates may
        not be separated by moving their Y coordinates.
        """

        selected = set(self._canonical_selection_ids or self._canonicalize_selection())
        if 0 < len(selected) <= 8:
            # Chronological rows need extra horizontal capacity because dates
            # close in time cannot be separated vertically.  Normalized rows
            # already stagger those conflicts and retain the more compact
            # half-height aspect.
            return (
                0.60
                if self.settings.get("vertical_layout_mode")
                == VERTICAL_LAYOUT_CHRONOLOGICAL
                else 0.5
            )
        return 1.0

    def _apply_aspect_fill(
        self,
        xlim: Tuple[float, float],
        ylim: Tuple[float, float],
    ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        axes_width, axes_height = self._effective_axes_pixels()
        if axes_width <= 0 or axes_height <= 0:
            return xlim, ylim

        x_range = xlim[1] - xlim[0]
        y_range = ylim[1] - ylim[0]
        if x_range <= 0 or y_range <= 0:
            return xlim, ylim

        fig_ratio = axes_width / axes_height
        target_data_ratio = fig_ratio * self._view_data_aspect()
        data_ratio = x_range / y_range
        x_center = (xlim[0] + xlim[1]) / 2.0
        y_center = (ylim[0] + ylim[1]) / 2.0

        if data_ratio > target_data_ratio:
            new_y_range = x_range / target_data_ratio
            return xlim, (y_center - new_y_range / 2.0, y_center + new_y_range / 2.0)

        new_x_range = y_range * target_data_ratio
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
        core_collections = (
            (self._render_core_animals,)
            if isinstance(self._render_core_animals, dict)
            else (
                getattr(self.app, "animals", {}) or {},
                getattr(self.app, "archived", {}) or {},
            )
        )
        for collection in core_collections:
            if not isinstance(collection, dict):
                continue
            record = collection.get(node)
            if isinstance(record, dict):
                records.append(record)

        store_animals = self._render_store_animals
        if store_animals is None:
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

    def _canonicalize_selection(
        self,
        values: Optional[List[str]] = None,
        *,
        records: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[str, ...]:
        """Return one deterministic, duplicate-free selection of animal keys.

        The main list and Heritage-only interaction path historically kept
        separate selections and occasionally supplied display names/public IDs
        instead of the engine's identity key.  Build a temporary alias index
        from the current Core/archived/Heritage records, resolve each value to
        its stable key where possible, and sort it once for all render stages.
        Ambiguous or not-yet-loaded values are retained as trimmed keys so a
        refresh remains lossless; the engine will simply omit an unknown key.
        """
        if values is None:
            values = list(getattr(self.app, "selected_animals", []) or [])
            values.extend(list(getattr(self.app, "_selected_heritage_only", []) or []))

        if records is None:
            records = {}
            try:
                source = self.plugin._all_identity_records()
            except Exception:
                source = {}
            if isinstance(source, dict):
                records = {
                    str(key).strip(): value
                    for key, value in source.items()
                    if str(key).strip() and isinstance(value, dict)
                }
        else:
            records = {
                str(key).strip(): value
                for key, value in records.items()
                if str(key).strip() and isinstance(value, dict)
            }

        aliases: Dict[str, Optional[str]] = {}
        for key, record in records.items():
            candidates = {
                key,
                record.get("ipid"),
                record.get("id"),
                record.get("animal_id"),
                record.get("public_id"),
                animal_base_name(key, record),
                animal_identity_label(key, record),
            }
            for candidate in candidates:
                alias = str(candidate or "").strip()
                if not alias:
                    continue
                folded = alias.casefold()
                if folded not in aliases:
                    aliases[folded] = key
                elif aliases[folded] != key:
                    aliases[folded] = None

        canonical: Set[str] = set()
        for value in values:
            raw = str(value or "").strip()
            if not raw:
                continue
            resolved = aliases.get(raw.casefold())
            canonical.add(resolved if resolved else raw)
        return tuple(sorted(canonical, key=lambda item: (item.casefold(), item)))

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
        *,
        inbreeding_unavailable: bool = False,
    ) -> str:
        mode = str(self.settings.get("animal_label_detail", "inbreeding_f"))
        diagnostic = (
            self.messages.get(
                "heritage_track.node.inbreeding_unavailable",
                "F: unavailable (cyclic pedigree)",
            )
            if inbreeding_unavailable
            else ""
        )
        if mode == "nothing":
            return diagnostic
        if mode == "birth_date":
            detail = self._get_node_birth_date_text(node)
            return f"{detail}\n{diagnostic}" if diagnostic else detail
        if mode == "animal_id":
            detail = self._get_node_public_id(node, record)
            return f"{detail}\n{diagnostic}" if diagnostic else detail
        if diagnostic:
            return diagnostic
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
        detail = self._get_node_detail_text(
            node,
            source,
            None,
            inbreeding_unavailable=node in self._malformed_f_nodes,
        )
        if not detail:
            # The selected label mode may intentionally omit all secondary
            # text; keep the marker label itself as the routing obstacle.
            detail = ""
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
        return family_node_id(mother, father)

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
            if not family_id:
                # Incomplete or self-parented records remain visible as
                # singleton nodes, but never acquire a family junction or
                # parent route.
                continue
            families[family_id] = {
                "id": family_id,
                "mother": mother,
                "father": father,
                "children": children,
                "parent_level": max(levels.get(mother, 0), levels.get(father, 0)),
                "child_level": min(child_levels) if child_levels else 0,
            }

        return families



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
            route_known = False
            if self._route_plan is not None:
                if (
                    n1 in animal_positions
                    and n2 in family_positions
                    and n1 in self._route_plan.routes.get(n2, {})
                ):
                    route_known = True
                    drawn = self._route_plan.draw_segments(
                        n2,
                        n1,
                        gap_radius_pixels=self._route_gap_radius_pixels,
                        pixel_scale=self._route_gap_pixel_scale,
                    )
                    route_segments = [(second, first) for first, second in reversed(drawn)]
                elif (
                    n1 in family_positions
                    and n2 in animal_positions
                    and n2 in self._route_plan.routes.get(n1, {})
                ):
                    route_known = True
                    route_segments = self._route_plan.draw_segments(
                        n1,
                        n2,
                        gap_radius_pixels=self._route_gap_radius_pixels,
                        pixel_scale=self._route_gap_pixel_scale,
                    )
            if route_known:
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

        for point in points:
            try:
                finite = len(point) == 2 and all(math.isfinite(float(value)) for value in point)
            except (TypeError, ValueError, OverflowError):
                finite = False
            if not finite:
                raise GeometryValidationError("non-finite render bounds input")

        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        # Keep small graphs from floating in excessive whitespace while still
        # reserving a stable outer margin for markers, labels, and junctions.
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        label_half_widths = [
            self._pedigree_router._estimated_label_width(
                self._get_node_obstacle_label(node, self._get_node_record(node))
            ) / 2.0
            for node in positions
            if not self._is_family_node(node)
        ]
        label_margin_x = max(label_half_widths, default=0.48) + 0.45
        margin_x = max(1.15, min(2.4, max(span_x * 0.08, label_margin_x)))
        # Marker labels extend about 30 screen pixels below the data point.
        # The former 1.8-unit cap could clip the detail line at the bottom of
        # a focused view after its compressed vertical aspect was applied.
        margin_y = max(1.6, min(2.4, span_y * 0.08))
        full_xlim = (min(xs) - margin_x, max(xs) + margin_x)
        full_ylim = (min(ys) - margin_y, max(ys) + margin_y)

        # Matplotlib labels are point-sized, not data-sized.  Fitting an
        # arbitrarily large pedigree into one viewport therefore shrinks the
        # distance between nodes while the names stay equally wide.  Large
        # trees must open at a readable scale and remain pannable/zoomable.
        axes_width, axes_height = self._effective_axes_pixels()
        if axes_width <= 0 or axes_height <= 0:
            return full_xlim, full_ylim
        selected = [
            node
            for node in (self._canonical_selection_ids or self._canonicalize_selection())
            if node in positions and not self._is_family_node(node)
        ]
        min_pixels_per_unit = 36.0
        focused = bool(selected and len(selected) <= 8)
        horizontal_pixels_per_unit = (
            25.0
            if focused
            and self.settings.get("vertical_layout_mode")
            == VERTICAL_LAYOUT_CHRONOLOGICAL
            else min_pixels_per_unit
        )
        max_width = max(16.0, axes_width / horizontal_pixels_per_unit)
        # A focused pedigree has few semantic anchors but may include a deep
        # ancestry context. At 36 px/unit the viewport cut through complete
        # family routes, leaving Elladan/Elrohir with misleading short stubs.
        # Generation rows remain clearly separated at 16 px/unit (57.6 px per
        # standard 3.6-unit generation), so focused views may fit the complete
        # vertical context while large all-animal trees remain pannable.
        vertical_pixels_per_unit = (
            16.0 if focused else min_pixels_per_unit
        )
        max_height = max(11.0, axes_height / vertical_pixels_per_unit)
        full_width = full_xlim[1] - full_xlim[0]
        full_height = full_ylim[1] - full_ylim[0]
        if full_width <= max_width and full_height <= max_height:
            return full_xlim, full_ylim

        if selected and len(selected) <= 8:
            center_x = sum(positions[node][0] for node in selected) / len(selected)
            center_y = sum(positions[node][1] for node in selected) / len(selected)
        else:
            animal_points = sorted(
                (
                    point
                    for node, point in positions.items()
                    if not self._is_family_node(node)
                ),
                key=lambda point: (point[0], point[1]),
            ) or points
            ordered_x = sorted(point[0] for point in animal_points)
            ordered_y = sorted(point[1] for point in animal_points)
            center_x = ordered_x[len(ordered_x) // 2]
            center_y = ordered_y[len(ordered_y) // 2]

        visible_width = min(full_width, max_width)
        visible_height = min(full_height, max_height)
        center_x = min(
            max(center_x, full_xlim[0] + (visible_width / 2.0)),
            full_xlim[1] - (visible_width / 2.0),
        )
        center_y = min(
            max(center_y, full_ylim[0] + (visible_height / 2.0)),
            full_ylim[1] - (visible_height / 2.0),
        )
        return (
            center_x - (visible_width / 2.0),
            center_x + (visible_width / 2.0),
        ), (
            center_y - (visible_height / 2.0),
            center_y + (visible_height / 2.0),
        )

    @staticmethod
    def _layout_mode_for_selection(selected_animals: List[str]) -> str:
        """Return the visible layout mode for the current selection.

        A single or small selection stays in the focused relationship view.
        A large selection uses the Selection overview presentation even
        though its selected IDs still define the authorized display scope.
        The threshold is shared with the router plan.
        """
        unique_selection = {
            str(value).strip()
            for value in selected_animals
            if str(value).strip()
        }
        return (
            LAYOUT_MODE_FOCUSED
            if 0 < len(unique_selection) <= 8
            else LAYOUT_MODE_OVERVIEW
        )

    def _build_display_context(
        self,
        engine: PedigreeEngine,
        selected_animals: List[str],
        all_graph_families: Dict[str, Dict[str, Any]],
        display_mode: str,
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

        # The graph is always selection-driven.  An empty selection returns
        # through refresh_graph's splash state before this builder is called;
        # project/species filters never become an independent graph scope.
        display_strategy = SelectedAnimalsStrategy()

        # Offspring and siblings remain ghosts around explicitly selected
        # animals.
        ghost_strategies: list = []
        if selected_animals:
            ghost_strategies.append(
                OffspringAndSiblingsGhostStrategy(selected_animals=set(selected_animals))
            )
            ghost_strategies.append(
                VisibleFamilyCompletenessGhostStrategy(families=all_graph_families)
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
        )

        context = builder.build(
            selected_animals=selected_animals,
            archived_animals=archived_animals,
            display_mode=display_mode,
            selection_type="selected",
        )

        # Handle heritage-only filtering (needs plugin access)
        if not self.settings.get("show_heritage_only", True):
            display_nodes = {n for n in context.display_nodes if not self.plugin.is_heritage_only(n)}
            context = context.copy_with(display_nodes=display_nodes)

        return context

    def _paint_cached_render_entry(
        self,
        entry: RenderCacheEntry,
        *,
        keep_view: bool = False,
        recompute_gaps: bool = True,
    ) -> None:
        """Paint an already accepted frame without rebuilding its model.

        The cache entry is immutable and contains all semantic data needed by
        the painter.  A detached route-plan copy is used only for pixel-scale
        gap recalculation; the published entry is never mutated.
        """
        display_nodes = set(entry.display_nodes)
        ghost_nodes = set(entry.ghost_nodes)
        positions = dict(entry.positions)
        family_positions = dict(entry.route_plan.family_positions)
        family_members = {
            family_id: set(members)
            for family_id, members in entry.route_plan.family_members.items()
        }
        route_plan = entry.route_plan.to_mutable()
        families = {
            family_id: dict(family)
            for family_id, family in entry.family_nodes.items()
        }
        chronological_mode = str(entry.display_mode).endswith(":chronological")
        self._render_cache_entry = entry
        self._route_plan = route_plan
        self.family_routes = route_plan.routes
        self._ghost_nodes = ghost_nodes
        self._canonical_selection_ids = tuple(entry.canonical_selection)
        self.layout_mode = str(entry.display_mode).split(":", 1)[0] or LAYOUT_MODE_FOCUSED
        self.no_selection_mode = False
        self.family_positions = family_positions
        self.family_members = family_members
        self.node_positions = positions
        self._rendered_families = families
        self._rendered_engine = None
        self._rendered_artist_scale = float(entry.artist_scale or 1.0)
        self._chronological_undated_nodes = set(entry.chronological_undated_nodes)
        self._position_cache_hit = True
        # Restore the complete write context belonging to this frame.  Warm
        # painting can follow a different selection; deriving only the
        # display positions would otherwise leave drag persistence pointed at
        # the previous selection/user/revision.
        self._active_position_cache_key = str(entry.position_cache_key or "")
        self._active_position_cache_user = str(entry.position_cache_user or "") or "guest"
        self._active_position_cache_revision = str(entry.position_cache_revision or "")
        self._active_position_cache_dependencies = set(entry.position_cache_dependencies)
        if not self._active_position_cache_key:
            self._active_position_cache_key = self._position_cache_key(
                list(entry.canonical_selection),
                display_mode=self.layout_mode,
                chronological_mode=chronological_mode,
            )
        if not self._active_position_cache_dependencies:
            self._active_position_cache_dependencies = set(entry.dependencies)

        self.stack.setCurrentWidget(self.canvas)
        self._configure_subplot_geometry(chronological_mode)
        previous_xlim = self.current_xlim
        previous_ylim = self.current_ylim
        if keep_view and previous_xlim is not None and previous_ylim is not None:
            view_xlim, view_ylim = previous_xlim, previous_ylim
        else:
            view_xlim, view_ylim = entry.bounds
            view_xlim, view_ylim = self._apply_aspect_fill(view_xlim, view_ylim)
        if recompute_gaps:
            try:
                self._recompute_route_visual_gaps(route_plan)
            except GeometryValidationError as exc:
                self._report_geometry_failure(exc)
                return

        self.ax.clear()
        self._legend_artist = None
        self._legend_anchor_axes = None
        self.ax.set_aspect(self._view_data_aspect(), adjustable="box")
        self.ax.set_xlim(view_xlim)
        self.ax.set_ylim(view_ylim)
        self._route_collections = []
        self._relationship_highlight_collections = []
        if self.settings.get("show_grid", False):
            self._draw_grid()
        self._rebuild_hit_test_index()
        self.node_meta.clear()
        self._replace_route_collections()
        self._replace_relationship_highlights()

        collapsed_families = set(self.collapsed_families)
        for family_id, (fx, fy) in family_positions.items():
            family_fill = "black" if family_id in collapsed_families else "white"
            self.ax.plot(
                [fx], [fy], linestyle="None", marker="o", markersize=8.4,
                markeredgecolor="black", markerfacecolor=family_fill,
                markeredgewidth=1.1, zorder=2.5,
            )
            family = families.get(family_id, {})
            self.node_meta[family_id] = {
                "kind": "family", "genotype": "", "fill_color_raw": family_fill,
                "fill_color": family_fill, "role": "family", "sex": "",
                "heritage_only": False, "mother": family.get("mother", ""),
                "father": family.get("father", ""),
            }

        legend_entries: Dict[str, Dict[str, str]] = {}
        for node in sorted(display_nodes, key=str.casefold):
            x, y = positions.get(node, (0.0, 0.0))
            metadata = dict(entry.node_metadata.get(node, {}))
            display_label = str(metadata.get("display_label") or node)
            is_ghost = node in ghost_nodes
            is_heritage_only = bool(metadata.get("heritage_only", False))
            is_dead = bool(metadata.get("is_dead", False))
            fill_color = self._valid_fill(metadata.get("fill_color", ""))
            if is_ghost:
                node_edge_color, node_face_color, text_color, linewidth = (
                    "#aaaaaa", "#e8e8e8", "#999999", 1.0
                )
            else:
                node_edge_color, node_face_color, text_color, linewidth = (
                    "black", fill_color, "black", 3.0 if node in self.selected_nodes else 1.5
                )
            marker_artist, = self.ax.plot(
                [x], [y], linestyle="None", marker=str(metadata.get("shape") or "s"),
                markersize=16.7 * self._rendered_artist_scale,
                markeredgecolor=node_edge_color, markerfacecolor=node_face_color,
                markeredgewidth=linewidth, zorder=3,
            )
            label_artist = self.ax.annotate(
                display_label, xy=(x, y), xytext=(0, -10), textcoords="offset points",
                ha="center", va="top", fontsize=9 * self._rendered_artist_scale,
                color=text_color,
                fontstyle="italic" if is_heritage_only else "normal",
                fontweight="normal" if (is_dead or is_ghost) else "bold",
                zorder=4, path_effects=self._animal_text_path_effects(),
            )
            # Text is regenerated from immutable record/F data so a language
            # change can reuse the same geometry cache without showing stale
            # localized strings.
            detail_text = self._get_node_detail_text(
                node,
                dict(entry.record_index.get(node, {})),
                entry.f_values.get(node),
                inbreeding_unavailable=entry.f_status.get(node) == "unavailable",
            )
            f_artist = None
            if detail_text:
                f_artist = self.ax.annotate(
                    detail_text, xy=(x, y), xytext=(0, -22), textcoords="offset points",
                    ha="center", va="top", fontsize=7 * self._rendered_artist_scale,
                    color="#bbbbbb" if is_ghost else "#777777", zorder=4,
                    path_effects=self._animal_text_path_effects(),
                )
            undated_artist = None
            if chronological_mode and node in self._chronological_undated_nodes:
                undated_artist = self.ax.annotate(
                    self.messages.get("heritage_track.node.undated_marker", "//"),
                    xy=(x, y), xytext=(10, 7), textcoords="offset points", ha="left",
                    va="bottom", fontsize=7 * self._rendered_artist_scale,
                    color="#aaaaaa" if is_ghost else "#777777", zorder=5,
                    path_effects=self._animal_text_path_effects(),
                )
            genotype_value = str(metadata.get("genotype", "")).strip() or self.messages.get(
                "heritage_track.legend.no_genotype", "(no genotype)"
            )
            entry_key = genotype_value.casefold()
            legend_entries.setdefault(
                entry_key,
                {"genotype": genotype_value, "face_color": fill_color},
            )
            self.node_meta[node] = {
                "kind": "animal", "genotype": metadata.get("genotype", ""),
                "fill_color_raw": metadata.get("fill_color_raw", ""),
                "fill_color": fill_color, "role": metadata.get("role", ""),
                "sex": metadata.get("sex", ""), "heritage_only": is_heritage_only,
                "display_label": display_label, "ipid": node,
                "animal_id": metadata.get("animal_id", ""),
                "birth_date": metadata.get("birth_date", ""),
                "marker_artist": marker_artist, "label_artist": label_artist,
                "f_artist": f_artist, "undated_artist": undated_artist,
            }

        if chronological_mode:
            self._configure_chronological_axis()
        else:
            self.ax.axis("off")
            self._configure_subplot_geometry(False)
        if self.settings.get("show_legend", True) and legend_entries:
            handles = [
                Line2D(
                    [0], [0], marker="s", linestyle="None", markeredgecolor="black",
                    markerfacecolor=item["face_color"], color="black", markersize=7,
                    label=item["genotype"],
                )
                for item in sorted(
                    legend_entries.values(),
                    key=lambda value: str(value.get("genotype", "")).casefold(),
                )
            ]
            legend = self.ax.legend(
                handles=handles, loc="lower right", bbox_to_anchor=(0.0, 0.0, 1.0, 1.0),
                bbox_transform=self.ax.transAxes, borderaxespad=0.55,
                title=self.messages.get("heritage_track.legend.title", "Genotype legend"),
                fontsize=8, title_fontsize=8, frameon=True,
            )
            legend.set_zorder(12)
            legend.get_frame().set_alpha(0.86)
            self._legend_artist = legend
            self._place_legend_overlay(legend)

        self.current_xlim = self.ax.get_xlim()
        self.current_ylim = self.ax.get_ylim()
        mode_text = self.messages.get(
            "heritage_track.status.mode_overview", "Selection overview"
        ) if self.layout_mode == LAYOUT_MODE_OVERVIEW else self.messages.get(
            "heritage_track.status.mode_selected", "Selection mode"
        )
        selected_text = self.messages.get(
            "heritage_track.status.selected", "Selected in graph: {count}"
        ).format(count=len(self.selected_nodes))
        status_text = f"{mode_text} | {selected_text}"
        tooltip_lines: List[str] = []
        if route_plan.unresolved:
            status_text = f"{status_text} | " + self.messages.get(
                "heritage_track.status.routing_warning",
                "Warning: {count} pedigree route conflicts remain",
            ).format(count=len(route_plan.unresolved))
            tooltip_lines.extend(route_plan.unresolved)
        unavailable = sorted(
            node for node, status in entry.f_status.items() if status == "unavailable"
        )
        if unavailable:
            status_text = f"{status_text} | " + self.messages.get(
                "heritage_track.status.cycle_warning",
                "Warning: {count} cyclic or malformed pedigree records; inbreeding F is unavailable",
            ).format(count=len(unavailable))
            tooltip_lines.append(", ".join(unavailable))
        if self._position_cache_notice:
            status_text = f"{status_text} | {self._position_cache_notice}"
            tooltip_lines.append(self._position_cache_notice)
            self._position_cache_notice = ""
        self.status_label.setToolTip("\n".join(tooltip_lines))
        self.status_label.setText(status_text)
        self.temp_positions.clear()
        self._hover_annotation.set_visible(False)
        self.canvas.draw_idle()

    def _render_source_revision(
        self,
        *,
        core_snapshot: Dict[str, Dict[str, Any]],
        raw_store: Dict[str, Any],
        backend_revision: int,
        selected_animals: List[str],
        chronological_mode: bool,
    ) -> str:
        """Hash all semantic inputs that can invalidate a complete frame."""
        # Position-cache writes are a persistence concern, not a semantic
        # change to the pedigree that has already been laid out.  Excluding
        # those maps (and their bookkeeping timestamp) lets a frame remain a
        # valid warm hit after its own complete position map is saved, while
        # real Core/Heritage edits still change the content hash below.
        source_store = deepcopy(raw_store) if isinstance(raw_store, dict) else {}
        if isinstance(source_store, dict):
            source_store.pop("position_cache", None)
            source_store.pop("node_positions", None)
            source_store.pop("updated_at", None)
        return self._render_revision(
            {
                "schema": "heritage-render-input.v1",
                "core": core_snapshot,
                "store": source_store,
                "temporary_dummies": getattr(self.plugin, "_temporary_dummies", {}),
                "selection": selected_animals,
                "graph_selected_nodes": sorted(self.selected_nodes, key=str.casefold),
                "display_mode": self.layout_mode,
                "chronological": chronological_mode,
                "max_generations": int(self._max_generations),
                "collapsed_families": sorted(self.collapsed_families, key=str.casefold),
                "settings": {
                    key: self.settings.get(key)
                    for key in (
                        "exclude_archived",
                        "animal_label_detail",
                        "vertical_layout_mode",
                        "show_grid",
                        "show_legend",
                    )
                },
            }
        )

    def refresh_graph(self, keep_view: bool = False) -> None:
        # Rendering is one read-only transaction.  Capture Core and the latest
        # Heritage backend record/revision before building the engine so every
        # projection, resolver and position lookup uses the same snapshot.
        core_snapshot = self.plugin._copy_core_records(self.app)
        raw_store, backend_revision = self.plugin.store.load_latest_with_revision()
        store_animals = raw_store.get("animals", {}) if isinstance(raw_store, dict) else {}
        self._render_core_animals = core_snapshot
        self._render_store_animals = (
            deepcopy(store_animals) if isinstance(store_animals, dict) else {}
        )
        self._render_backend_revision = int(backend_revision or 0)
        self._render_pedigree_revision = str(
            raw_store.get("pedigree_revision", "") if isinstance(raw_store, dict) else ""
        ).strip() or "genesis"
        # Merge Core and Heritage-only selections once.  Every stage below
        # receives this same sorted identity tuple; the raw app lists are not
        # consulted again during the refresh.  Resolve aliases against the
        # snapshots captured above so a cache lookup cannot observe a stale
        # backend or Core record.
        identity_records = dict(core_snapshot)
        if isinstance(store_animals, dict):
            identity_records.update(
                {
                    str(key).strip(): value
                    for key, value in store_animals.items()
                    if str(key).strip() and isinstance(value, dict)
                }
            )
        selection_values = list(getattr(self.app, "selected_animals", []) or [])
        selection_values.extend(list(getattr(self.app, "_selected_heritage_only", []) or []))
        selected_animals = list(
            self._canonicalize_selection(selection_values, records=identity_records)
        )
        self._canonical_selection_ids = tuple(selected_animals)
        self.layout_mode = self._layout_mode_for_selection(selected_animals)
        self.no_selection_mode = not selected_animals

        chronological_mode = (
            self.settings.get("vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED)
            == VERTICAL_LAYOUT_CHRONOLOGICAL
        )
        source_revision = self._render_source_revision(
            core_snapshot=core_snapshot,
            raw_store=raw_store,
            backend_revision=backend_revision,
            selected_animals=selected_animals,
            chronological_mode=chronological_mode,
        )

        # A complete immutable frame is the fast path.  It is only reusable
        # when the full Core/Heritage source token matches; dependency and
        # revision checks remain available through the plugin registry.
        if selected_animals and not self._force_relayout:
            cache_key = self._render_cache_key(
                selected_animals,
                chronological_mode,
                display_mode=self.layout_mode,
            )
            cached_render_entry = self.plugin.get_render_entry(cache_key)
            if cached_render_entry is not None:
                if cached_render_entry.source_revision == source_revision and cached_render_entry.valid:
                    self._paint_cached_render_entry(cached_render_entry, keep_view=keep_view)
                    self._render_store_animals = None
                    self._render_core_animals = None
                    return
                self.plugin.remove_render_cache_entry(cache_key)

        # No-selection mode only shows the splash screen.  Avoid building the
        # complete pedigree, level map, families, layout, and routes merely to
        # discard them a few lines later.
        if self.no_selection_mode:
            self._show_splash_screen()
            self._render_store_animals = None
            self._render_core_animals = None
            self.plugin._clear_active_projection_snapshot()
            return

        engine = self.plugin.build_engine(
            sync=False,
            core_snapshot=core_snapshot,
            store_snapshot=raw_store,
            backend_revision=backend_revision,
        )

        all_graph_nodes = engine.get_display_nodes([])
        all_graph_levels = engine.compute_levels(all_graph_nodes)
        all_graph_families = self._build_family_units(all_graph_nodes, all_graph_levels, engine)

        # Use the new strategy-based architecture to build display context
        # This handles: display set computation, ghost detection, level computation
        context = self._build_display_context(
            engine,
            selected_animals,
            all_graph_families,
            self.layout_mode,
        )

        # Extract data from context
        display_nodes = context.display_nodes
        pre_collapse_levels = context.levels
        ghost_nodes = context.ghost_nodes
        # DisplayContext is immutable; interaction code needs its own mutable
        # membership set when a ghost is promoted to an active selection.
        self._ghost_nodes = set(ghost_nodes)

        # A changed display scope selects a different persistent map.  Do not
        # force a global relayout here: the canonical selection/layout key
        # below determines whether its own map is restored.
        if hasattr(self, '_prev_display_nodes') and self._prev_display_nodes != display_nodes:
            self.temp_positions.clear()
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
        # Read only the current user's complete selection map.  Legacy global
        # coordinates are deliberately ignored for selected views and are
        # never migrated into this cache.
        chronological_mode = (
            self.settings.get("vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED)
            == VERTICAL_LAYOUT_CHRONOLOGICAL
        )
        position_cache_key = self._position_cache_key(
            selected_animals,
            display_mode=self.layout_mode,
            chronological_mode=chronological_mode,
        )
        position_cache_user = self._position_cache_user_id()
        position_cache_dependencies = self._position_cache_dependencies(
            engine, set(display_nodes)
        )
        position_cache_revision = self._position_cache_dependency_revision(
            engine,
            position_cache_dependencies,
            core_snapshot=core_snapshot,
            store_snapshot=raw_store,
        )
        cached_entry = None
        if not self._force_relayout:
            cached_entry = self.plugin.store.get_position_cache_entry(
                position_cache_user,
                position_cache_key,
                pedigree_revision=position_cache_revision,
                dependency_ids=position_cache_dependencies,
            )
        cached_positions: Dict[str, Tuple[float, float]] = {}
        if isinstance(cached_entry, dict):
            raw_cached_positions = cached_entry.get("positions", {})
            if isinstance(raw_cached_positions, dict):
                cached_positions = {
                    node: (float(value["x"]), float(value["y"]))
                    for node, value in raw_cached_positions.items()
                    if node in display_nodes
                    and not self._is_family_node(node)
                    and isinstance(value, dict)
                    and "x" in value
                    and "y" in value
                }
            visible_animals = {
                node for node in display_nodes if not self._is_family_node(node)
            }
            if visible_animals and set(cached_positions) != visible_animals:
                # Partial records are not a valid cache hit; rebuild the
                # complete map instead of mixing maps from other selections.
                cached_entry = None
                cached_positions = {}

        self._active_position_cache_key = position_cache_key
        self._active_position_cache_user = position_cache_user
        self._active_position_cache_revision = position_cache_revision
        self._active_position_cache_dependencies = set(position_cache_dependencies)
        self._position_cache_hit = bool(cached_entry)

        locked_positions = cached_positions if cached_entry else {}

        try:
            auto_positions = self._compute_positions(
                display_nodes,
                levels,
                engine,
                families,
                locked_positions=locked_positions,
            )
        except GeometryValidationError as exc:
            self._report_geometry_failure(exc)
            return
        def _respect_vertical_mode(
            stored: Tuple[float, float],
            automatic: Tuple[float, float],
        ) -> Tuple[float, float]:
            if chronological_mode:
                return float(stored[0]), float(automatic[1])
            return float(stored[0]), float(stored[1])

        # Every non-family animal is either restored from the complete cache
        # or taken from one fresh automatic layout.  No previous selection or
        # legacy global map can leak into this scope.
        animal_positions = {}
        protected_nodes: Set[str] = set()
        for node, pos in auto_positions.items():
            if node in cached_positions:
                animal_positions[node] = _respect_vertical_mode(cached_positions[node], pos)
                protected_nodes.add(node)
            else:
                animal_positions[node] = pos

        # Partner order and ancestry locality are resolved together by the
        # router's row-block pass.  The former pre-route swap marked automatic
        # nodes as manually protected and therefore prevented the router from
        # keeping their complete partner row contiguous.

        # Prepare malformed-lineage state before the router estimates label
        # obstacles.  Descendants of an unresolved/cyclic pedigree need the
        # larger unavailable-F label footprint in this same frame.
        _layout_parent_map = engine.get_genetic_parent_map()
        self._cycle_nodes = InbreedingCalculator(_layout_parent_map).cycle_nodes
        self._malformed_f_nodes = self._find_malformed_f_nodes(
            _layout_parent_map,
            set(engine.animals) | set(engine.heritage_entries),
        )

        obstacle_labels = {
            node: self._get_node_obstacle_label(node, self._get_node_record(node))
            for node in animal_positions
        }
        # A diagnostic line is a real secondary label even when the user has
        # selected ``Nothing`` for ordinary details.  Reserve its footprint so
        # conditional warnings cannot create a fresh collision.
        has_secondary_label = (
            self.settings.get("animal_label_detail", "inbreeding_f") != "nothing"
            or bool(self._malformed_f_nodes & set(animal_positions))
        )
        # The legend is an in-axes overlay and does not narrow the geometry.
        # Keep the label estimate independent of whether the overlay is shown.
        focused_aspect = self._view_data_aspect()
        is_focused = focused_aspect < 1.0
        axes_pixel_width, _axes_pixel_height = self._effective_axes_pixels()
        # A typical 1360-wide application leaves roughly 1000 px for the
        # Heritage canvas after the animal sidebar. Chronological focus cannot
        # move close birth dates vertically, so use a modest responsive artist
        # scale below that threshold rather than merging neighbouring names.
        focused_artist_scale = (
            0.86
            if is_focused
            and chronological_mode
            and axes_pixel_width < 1050.0
            else 1.0
        )
        self._pedigree_router.label_width_scale = (
            1.08 if is_focused and chronological_mode else 1.0
        )
        # Focused views deliberately show substantially more vertical data per
        # pixel. Reserve the renderer's true marker + two-line point offsets
        # before routing, independent of the slightly wider chronological
        # aspect used to separate animals born close together.
        self._pedigree_router.label_height_scale = 3.0 if is_focused else 1.0
        try:
            route_plan = self._pedigree_router.plan(
                animal_positions,
                families,
                labels=obstacle_labels,
                protected_nodes=protected_nodes,
                focus_nodes=set(selected_animals),
                display_mode=self.layout_mode,
                show_inbreeding=has_secondary_label,
                vertical_layout_mode=self.settings.get(
                    "vertical_layout_mode", VERTICAL_LAYOUT_PARTNER_NORMALIZED
                ),
            )
        except GeometryValidationError as exc:
            self._report_geometry_failure(exc)
            return
        # The engine levels are the canonical hard generation assignment.
        # Validate the final visible scope after collapse filtering and carry
        # any cycle/order conflict into the plan and cache boundary.
        route_plan.layout_diagnostics = list(
            engine.generation_diagnostics(display_nodes, levels)
        )
        if route_plan.layout_diagnostics:
            route_plan.unresolved = sorted(
                set(route_plan.unresolved) | set(route_plan.layout_diagnostics),
                key=str.casefold,
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
            # A chronological singleton keeps its exact birth-date Y, so a
            # conventional row de-overlap must not move it vertically.  The
            # old allocator only separated *identical* Y values.  Dates a few
            # months (or one year) apart could therefore share the same
            # outside column while their two-line labels still overlapped.
            # Allocate the first column whose occupied vertical label bands
            # do not intersect instead.  This retains true dates and produces
            # a compact interval-colouring of the detached records.
            chronological_column_bands: Dict[int, List[Tuple[float, float]]] = defaultdict(list)
            singleton_column_spacing = max(
                4.2,
                max(
                    (
                        self._pedigree_router._estimated_label_width(
                            self._get_node_obstacle_label(
                                node, self._get_node_record(node)
                            )
                        )
                        for node in singletons
                    ),
                    default=3.5,
                )
                + 0.65,
            )
            ordered_singletons = (
                sorted(
                    singletons,
                    key=lambda node: (
                        -float(animal_positions[node][1]),
                        self._get_node_display_label(node).casefold(),
                    ),
                )
                if chronological and connected_points
                else singletons
            )
            for index, node in enumerate(ordered_singletons):
                if chronological and connected_points:
                    # Keep the birth-date Y coordinate, but reserve columns
                    # clearly outside the family-tree envelope.
                    y = animal_positions[node][1]
                    # Use the same vertical footprint as ``node_obstacles``.
                    # The focused renderer reserves a larger data-space band
                    # for the primary label, marker and optional detail/F
                    # line; a fixed 1.16-unit band let labels from nearby
                    # dates overlap in the outer singleton columns.
                    label_height_scale = max(
                        1.0, float(getattr(self._pedigree_router, "label_height_scale", 1.0))
                    )
                    bottom_offset = (
                        0.78 if has_secondary_label else 0.56
                    ) * label_height_scale
                    top_offset = 0.34 * label_height_scale
                    band = (
                        float(y) - bottom_offset,
                        float(y) + top_offset,
                    )
                    x_column = 0
                    while any(
                        min(band[1], occupied[1])
                        >= max(band[0], occupied[0]) - 1e-7
                        for occupied in chronological_column_bands[x_column]
                    ):
                        x_column += 1
                    chronological_column_bands[x_column].append(band)
                    animal_positions[node] = (
                        min_x - singleton_column_spacing
                        - x_column * singleton_column_spacing,
                        y,
                    )
                else:
                    animal_positions[node] = (
                        min_x + (index % columns) * 4.2,
                        min_y - 4.0 - (index // columns) * 3.2,
                    )
            # Singletons are parked after routing because they have no family
            # edges. Keep the route plan in sync with their final coordinates;
            # otherwise ``all_points()`` retains invisible stale points and
            # automatic fitting creates the large empty margins seen in the
            # all-species screenshot.
            route_plan.animal_positions = dict(animal_positions)
        positions: Dict[str, Tuple[float, float]] = dict(animal_positions)
        positions.update(family_positions)

        # Keep the force flag set until the automatic frame has been fully
        # validated and its complete animal map has been persisted below.

        # Compute the diagnostic/F state before accepting the render frame.
        # No values are written back during a view refresh; derived persistence
        # belongs to explicit data mutations and the dedicated F-cache issue.
        label_detail_mode = str(self.settings.get("animal_label_detail", "inbreeding_f"))
        show_f = label_detail_mode == "inbreeding_f"
        self._render_transaction_active = True
        try:
            f_values, f_status = self._compute_inbreeding_state(
                engine,
                display_nodes,
                show_f=show_f,
            )
        finally:
            self._render_transaction_active = False

        self._configure_subplot_geometry(chronological_mode)

        prev_xlim = self.current_xlim
        prev_ylim = self.current_ylim
        try:
            fit_xlim, fit_ylim = self._compute_view_bounds(positions, route_plan.all_points())
        except GeometryValidationError as exc:
            self._report_geometry_failure(exc)
            return

        if keep_view and prev_xlim is not None and prev_ylim is not None:
            view_xlim = prev_xlim
            view_ylim = prev_ylim
        else:
            view_xlim = fit_xlim
            view_ylim = fit_ylim

        view_xlim, view_ylim = self._apply_aspect_fill(view_xlim, view_ylim)

        fatal_geometry = self._render_geometry_fatal_diagnostics(
            route_plan,
            positions,
            locked_positions=locked_positions,
            bounds=(view_xlim, view_ylim),
        )
        if fatal_geometry:
            # Keep the last accepted artists/frame intact.  A malformed
            # transaction is reported, not painted as a plausible partial
            # pedigree.
            self._report_geometry_failure("\n".join(fatal_geometry))
            return

        # Prepare renderer state before the pixel-only gap pass.  The frame is
        # still invisible at this point, so a rejected render leaves the last
        # accepted artists intact.
        previous_route_plan = self._route_plan
        previous_family_routes = self.family_routes
        previous_rendered_engine = self._rendered_engine
        previous_rendered_families = self._rendered_families
        previous_artist_scale = self._rendered_artist_scale
        previous_render_cache_entry = self._render_cache_entry
        previous_position_context = (
            self._active_position_cache_key,
            self._active_position_cache_user,
            self._active_position_cache_revision,
            set(self._active_position_cache_dependencies),
            bool(self._position_cache_hit),
        )
        self._rendered_artist_scale = focused_artist_scale
        # Marker/crossing gaps are measured in data units derived from the
        # final pixel scale.  At this point the axes still carry the previous
        # frame's limits (or Matplotlib's defaults on the first render), so a
        # direct gap pass would use the wrong scale and can miss a marker that
        # the new route actually crosses.  Prime the transform with the
        # candidate bounds for the pass, then restore the old transform until
        # the complete frame is accepted and painted below.
        previous_axes_xlim = tuple(self.ax.get_xlim())
        previous_axes_ylim = tuple(self.ax.get_ylim())
        try:
            self.ax.set_xlim(view_xlim)
            self.ax.set_ylim(view_ylim)
            self._recompute_route_visual_gaps(route_plan)
        except GeometryValidationError as exc:
            self._route_plan = previous_route_plan
            self.family_routes = previous_family_routes
            self._rendered_engine = previous_rendered_engine
            self._rendered_families = previous_rendered_families
            self._rendered_artist_scale = previous_artist_scale
            self._report_geometry_failure(exc)
            return
        finally:
            self.ax.set_xlim(previous_axes_xlim)
            self.ax.set_ylim(previous_axes_ylim)

        # Publish the complete frame atomically before any visible artists are
        # created.  Every later view operation can derive its pixel-only route
        # copy from this accepted entry without rereading mutable backend data.
        render_entry: Optional[RenderCacheEntry] = None
        try:
            render_entry = self._build_render_cache_entry(
                engine=engine,
                selected_animals=selected_animals,
                display_nodes=display_nodes,
                ghost_nodes=ghost_nodes,
                levels=levels,
                families=families,
                positions=positions,
                locked_positions=locked_positions,
                route_plan=route_plan,
                bounds=(view_xlim, view_ylim),
                f_values=f_values,
                f_status=f_status,
                obstacle_labels=obstacle_labels,
                chronological_mode=chronological_mode,
                display_mode=self.layout_mode,
                source_revision=source_revision,
                artist_scale=focused_artist_scale,
                chronological_undated_nodes=self._chronological_undated_nodes,
            )
            if not render_entry.valid:
                logging.getLogger(__name__).error(
                    "Rejected Heritage render frame: %s",
                    "; ".join(render_entry.fatal_diagnostics),
                )
                self._route_plan = previous_route_plan
                self.family_routes = previous_family_routes
                self._rendered_engine = previous_rendered_engine
                self._rendered_families = previous_rendered_families
                self._rendered_artist_scale = previous_artist_scale
                self._report_geometry_failure("; ".join(render_entry.fatal_diagnostics))
                self._render_store_animals = None
                return
            # Publish exactly once, before any durable position write.  If
            # publication fails, no persistent coordinates have changed and
            # the previously accepted frame remains authoritative.
            self.plugin.cache_render_entry(render_entry)
        except Exception:
            # A failed cache publication must never paint a plausible partial
            # frame.  Keep the previous accepted entry and report the failure
            # through the normal status/diagnostic channel.
            if render_entry is not None:
                try:
                    self.plugin.remove_render_cache_entry(render_entry.cache_key)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Could not remove rejected Heritage render cache entry"
                    )
            self._route_plan = previous_route_plan
            self.family_routes = previous_family_routes
            self._rendered_engine = previous_rendered_engine
            self._rendered_families = previous_rendered_families
            self._rendered_artist_scale = previous_artist_scale
            self._render_cache_entry = previous_render_cache_entry
            (
                self._active_position_cache_key,
                self._active_position_cache_user,
                self._active_position_cache_revision,
                previous_dependencies,
                self._position_cache_hit,
            ) = previous_position_context
            self._active_position_cache_dependencies = set(previous_dependencies)
            logging.getLogger(__name__).exception("Could not publish Heritage render cache entry")
            self._render_store_animals = None
            return

        # A cache miss (or explicit Refresh) stores the complete final map,
        # including nodes that were not moved.  Publication was staged above;
        # a failed position write removes that staged entry so neither the
        # durable cache nor the last accepted frame is replaced.
        if not getattr(self, "_position_cache_hit", False):
            saved_position_cache = self._save_position_cache(
                animal_positions,
                confirm_delete_on_failure=self._force_relayout,
            )
            if saved_position_cache:
                self._force_relayout = False
            else:
                self.plugin.remove_render_cache_entry(render_entry.cache_key)
                self._route_plan = previous_route_plan
                self.family_routes = previous_family_routes
                self._rendered_engine = previous_rendered_engine
                self._rendered_families = previous_rendered_families
                self._rendered_artist_scale = previous_artist_scale
                self._render_cache_entry = previous_render_cache_entry
                (
                    self._active_position_cache_key,
                    self._active_position_cache_user,
                    self._active_position_cache_revision,
                    previous_dependencies,
                    self._position_cache_hit,
                ) = previous_position_context
                self._active_position_cache_dependencies = set(previous_dependencies)
                self._render_store_animals = None
                return

        self._render_cache_entry = render_entry

        # Paint through the same immutable-entry consumer used by warm
        # renders.  The legacy inline painter below is retained temporarily as
        # a compatibility reference, but is unreachable for accepted frames;
        # this keeps one authoritative paint path during the migration.
        self._paint_cached_render_entry(
            render_entry,
            keep_view=keep_view,
            recompute_gaps=False,
        )
        self._render_store_animals = None
        self._render_core_animals = None
        return

        self.ax.clear()
        self._legend_artist = None
        self._legend_anchor_axes = None
        self.ax.set_aspect(self._view_data_aspect(), adjustable="box")
        self.ax.set_xlim(view_xlim)
        self.ax.set_ylim(view_ylim)
        self._route_collections = []

        if self.settings.get("show_grid", False):
            self._draw_grid()

        self.family_positions = family_positions
        self.family_members = family_members
        self.node_positions = positions
        self._rebuild_hit_test_index()
        self.node_meta.clear()
        self._replace_route_collections()
        self._replace_relationship_highlights()

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

        # F values and cycle status were computed before the frame was
        # accepted, so painting consumes one coherent render transaction.

        for node in sorted(display_nodes, key=str.lower):
            x, y = animal_positions.get(node, (0.0, 0.0))
            is_ghost = node in ghost_nodes
            is_heritage_only = self.plugin.is_heritage_only(node)
            # Get record from main app data (active or archived) for proper sex/shape
            record = self._get_node_record(node)
            display_label = self._get_node_display_label(node, record)
            role = canonical_role_value(record.get("rolle", ""))
            sex = self.plugin.get_effective_sex(node, record)

            visual = self.plugin.get_node_visual(
                node,
                fallback_genotype=str(record.get("genotype", "")),
                fallback_record=record,
            )
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
                markersize=16.7 * focused_artist_scale,
                markeredgecolor=node_edge_color,
                markerfacecolor=node_face_color,
                markeredgewidth=lw,
                zorder=3,
            )
            label_artist = self.ax.annotate(
                display_label,
                xy=(x, y),
                xytext=(0, -10),
                textcoords="offset points",
                ha="center",
                va="top",
                fontsize=9 * focused_artist_scale,
                color=text_color,
                fontstyle="italic" if is_heritage_only else "normal",
                fontweight="normal" if (is_dead or is_ghost) else "bold",
                zorder=4,
                path_effects=self._animal_text_path_effects(),
            )

            detail_text = self._get_node_detail_text(
                node,
                record,
                f_values.get(node),
                inbreeding_unavailable=node in self._malformed_f_nodes,
            )
            f_artist = None
            if detail_text:
                f_artist = self.ax.annotate(
                    detail_text,
                    xy=(x, y),
                    xytext=(0, -22),
                    textcoords="offset points",
                    ha="center",
                    va="top",
                    fontsize=7 * focused_artist_scale,
                    color="#777777" if not is_ghost else "#bbbbbb",
                    zorder=4,
                    path_effects=self._animal_text_path_effects(),
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
                    fontsize=7 * focused_artist_scale,
                    color="#777777" if not is_ghost else "#aaaaaa",
                    zorder=5,
                    path_effects=self._animal_text_path_effects(),
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
            self._configure_subplot_geometry(False)

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
                bbox_to_anchor=(0.0, 0.0, 1.0, 1.0),
                bbox_transform=self.ax.transAxes,
                borderaxespad=0.55,
                title=self.messages.get("heritage_track.legend.title", "Genotype legend"),
                fontsize=8,
                title_fontsize=8,
                frameon=True,
            )
            legend.set_zorder(12)
            legend.get_frame().set_alpha(0.86)
            self._legend_artist = legend
            self._place_legend_overlay(legend)

        self.current_xlim = self.ax.get_xlim()
        self.current_ylim = self.ax.get_ylim()

        mode_text = (
            self.messages.get(
                "heritage_track.status.mode_overview", "Selection overview"
            )
            if self.layout_mode == LAYOUT_MODE_OVERVIEW
            else self.messages.get("heritage_track.status.mode_selected", "Selection mode")
        )
        selected_text = self.messages.get("heritage_track.status.selected", "Selected in graph: {count}").format(
            count=len(self.selected_nodes)
        )
        status_text = f"{mode_text} | {selected_text}"
        tooltip_lines: List[str] = []
        if route_plan.unresolved:
            routing_warning = self.messages.get(
                "heritage_track.status.routing_warning",
                "Warning: {count} pedigree route conflicts remain",
            ).format(count=len(route_plan.unresolved))
            status_text = f"{status_text} | {routing_warning}"
            for problem in route_plan.unresolved:
                marker = "shared animal endpoint "
                suffix = " has missing geometry"
                if marker in problem and problem.endswith(suffix):
                    endpoint = problem.split(marker, 1)[1][:-len(suffix)]
                    tooltip_lines.append(
                        self.messages.get(
                            "heritage_track.status.missing_geometry",
                            "Missing geometry for shared animal endpoint: {endpoint}",
                        ).format(endpoint=endpoint)
                    )
                else:
                    tooltip_lines.append(problem)
        visible_malformed_nodes = sorted(
            set(display_nodes) & set(self._malformed_f_nodes), key=str.casefold
        )
        if visible_malformed_nodes:
            cycle_warning = self.messages.get(
                "heritage_track.status.cycle_warning",
                "Warning: {count} cyclic or malformed pedigree records; inbreeding F is unavailable",
            ).format(count=len(visible_malformed_nodes))
            status_text = f"{status_text} | {cycle_warning}"
            tooltip_lines.append(
                ", ".join(visible_malformed_nodes)
                + " — "
                + self.messages.get(
                    "heritage_track.node.inbreeding_unavailable",
                    "F: unavailable (cyclic pedigree)",
                )
            )
        if self._position_cache_notice:
            status_text = f"{status_text} | {self._position_cache_notice}"
            tooltip_lines.append(self._position_cache_notice)
            self._position_cache_notice = ""
        self.status_label.setToolTip("\n".join(tooltip_lines))
        self.status_label.setText(status_text)

        # Do not persist collapsed-family or position cleanup as a side effect
        # of painting.  Explicit user actions own those writes.
        # Transient drag coordinates have now either been persisted as the
        # complete selection map or rejected; never carry them into another
        # selection's layout.
        self.temp_positions.clear()

        self._hover_annotation.set_visible(False)
        self.canvas.draw_idle()
        self._render_store_animals = None

    def closeEvent(self, event) -> None:
        '''Flush derived Heritage data before the window is closed.'''
        self.plugin.flush_pending_store()
        super().closeEvent(event)

    def _show_splash_screen(self) -> None:
        """Show the splash screen when no animals are selected.

        Similar to ProgTrack main window behavior when no animal is selected.
        Displays the splash.png image with legal disclaimer text.
        """
        # Switch to splash widget
        self.stack.setCurrentWidget(self.splash_widget)

        self.node_positions = {}
        self.family_positions = {}
        self.family_routes = {}
        self._route_plan = None
        self._render_cache_entry = None
        self._route_collections = []
        self._relationship_highlight_collections = []
        self._legend_artist = None
        self._legend_anchor_axes = None
        self._legend_dragging = False
        self._legend_drag_start_px = None
        self._legend_drag_start_anchor = None
        self._rendered_families = {}
        self._rendered_engine = None
        self.node_meta.clear()
        self._ghost_nodes = set()
        self._chronological_undated_nodes = set()
        self._active_position_cache_key = None
        self._active_position_cache_dependencies = set()
        self._active_position_cache_revision = ""
        self._position_cache_hit = False
        self.selected_nodes.clear()
        self.temp_positions.clear()
        self._hit_grid.clear()

        # Update status label
        mode_text = self.messages.get("heritage_track.status.mode_none", "No selection")
        instruction_status = self.messages.get("heritage_track.splash.status", "No scope selected")
        self.status_label.setText(f"{mode_text} | {instruction_status}")

        self._hover_annotation.set_visible(False)

    def _node_at_mouse(self, event, pixel_threshold: float = 14.0) -> Optional[str]:
        if event.x is None or event.y is None:
            return None

        # Hit testing reads the same accepted logical positions as painting.
        # During an active drag the transient positions intentionally take
        # precedence until the next frame is committed.
        hit_positions = (
            self.node_positions
            if self.drag_active or self._render_cache_entry is None
            else self._render_cache_entry.positions
        )

        # Use event data coordinates to select only nearby grid cells.  The
        # screen threshold is still evaluated exactly below, so this is an
        # optimization rather than a change to hit-test behavior.
        candidates: List[str]
        if event.xdata is None or event.ydata is None or not self._hit_grid:
            candidates = list(hit_positions)
        else:
            cell = self._hit_grid_cell_size
            cell_x = int(math.floor(float(event.xdata) / cell))
            cell_y = int(math.floor(float(event.ydata) / cell))
            sx0, sy0 = self.ax.transData.transform((float(event.xdata), float(event.ydata)))
            sx1, sy1 = self.ax.transData.transform(
                (float(event.xdata) + cell, float(event.ydata) + cell)
            )
            pixels_per_cell = max(abs(sx1 - sx0), abs(sy1 - sy0), 1e-6)
            cell_radius = max(1, int(math.ceil(pixel_threshold / pixels_per_cell)) + 1)
            if cell_radius > 8:
                candidates = list(hit_positions)
                cell_radius = 0
            else:
                candidates = []
            if cell_radius:
                for dx in range(-cell_radius, cell_radius + 1):
                    for dy in range(-cell_radius, cell_radius + 1):
                        candidates.extend(self._hit_grid.get((cell_x + dx, cell_y + dy), ()))

        nearest_name = None
        nearest_dist = float("inf")
        seen: Set[str] = set()
        for name in candidates:
            if name in seen:
                continue
            seen.add(name)
            point = hit_positions.get(name)
            if point is None:
                continue
            x, y = point
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

    def _rebuild_hit_test_index(self) -> None:
        """Build a cheap data-space index for the current node positions."""
        cell = self._hit_grid_cell_size
        self._hit_grid = defaultdict(list)
        for name, (x, y) in self.node_positions.items():
            key = (int(math.floor(float(x) / cell)), int(math.floor(float(y) / cell)))
            self._hit_grid[key].append(name)

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

        if event.button == 1 and self._legend_hit(event):
            self._legend_dragging = True
            self._legend_drag_start_px = (float(event.x), float(event.y))
            self._legend_drag_start_anchor = self._legend_anchor_from_artist()
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
        # Legend movement is committed on release, like CageTrack. Do not
        # let the same motion also pan the graph or move a node.
        if self._legend_dragging:
            return

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
        label.set_position((0, -10))
        if f_artist is not None:
            f_artist.xy = (x, y)
            f_artist.set_position((0, -22))
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
        if event.button == 1 and self._legend_dragging:
            self._finish_legend_drag(event)
            return

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
                        for member in sorted(self.drag_group_nodes, key=str.lower):
                            x, y = self.temp_positions.get(member, self.node_positions.get(member, (0.0, 0.0)))
                            sx, sy = self._snap_to_grid(x, y)
                            if chronological_mode:
                                sy = self.node_positions.get(member, (sx, sy))[1]
                            self.temp_positions[member] = (sx, sy)
                    elif not self._is_family_node(self.drag_node):
                        x, y = self.temp_positions.get(self.drag_node, self.node_positions.get(self.drag_node, (0.0, 0.0)))
                        sx, sy = self._snap_to_grid(x, y)
                        if chronological_mode:
                            sy = self.node_positions.get(self.drag_node, (sx, sy))[1]
                        self.temp_positions[self.drag_node] = (sx, sy)
                    # Replace the complete current selection map, not just
                    # the node/group that was dragged.  This is intentionally
                    # independent of the historical all-animals flag.
                    complete_positions = {
                        node: self.temp_positions.get(node, point)
                        for node, point in self.node_positions.items()
                        if not self._is_family_node(node)
                    }
                    self._save_position_cache(complete_positions)
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
            self._recompute_route_visual_gaps()
            self._replace_route_collections()
            self._replace_relationship_highlights()
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
        self._recompute_route_visual_gaps()
        self._replace_route_collections()
        self._replace_relationship_highlights()
        self.canvas.draw_idle()

    def _open_node_editor(self, node: str) -> None:
        # Viewing a node and editing Heritage-owned metadata are separate
        # capabilities.  Core animals are always a read-only projection;
        # only Heritage dummies may expose parent/sex/genotype editors.
        if not self._can('heritage.view'):
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
            with_status=is_heritage_only,
        )
        can_edit_links = self._can("heritage.edit_links")
        can_edit_colors = self._can("heritage.edit_genotype_colors")

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
            allow_remove=is_heritage_only and self.plugin.can_remove_heritage_only(node),
            animal_species=animal_species,
            sex_editable=is_heritage_only and can_edit_links,
            genotype_editable=is_heritage_only and can_edit_links,
            parents_editable=is_heritage_only and can_edit_links,
            parents_read_only_core=not is_heritage_only,
            genotype_options=(self._genotype_options_for_species(animal_species) if is_heritage_only else None),
            color_editable=can_edit_colors,
            genotype_options_provider=self._genotype_options_for_species,
            parent_options_provider=lambda required, species: self.plugin.parent_candidate_options(
                required,
                species,
                node,
                with_status=is_heritage_only,
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
        # Real Core records cannot be changed from Heritage.  Saving a real
        # node is still useful for an authorized genotype-display colour
        # overlay, but never writes parents, sex or genotype back to Core.
        if not is_heritage_only:
            if can_edit_colors:
                self.plugin.set_node_visual(node, None, values.get("fill_color", ""))
                self.refresh_graph(keep_view=True)
            return

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

        # One command validates the complete dummy edit and commits parentage,
        # sex, genotype and optional colour together.  If the actor only has
        # the colour permission, omit protected metadata rather than allowing
        # a forged disabled-widget value to reach the mutation boundary.
        metadata: Dict[str, Any] = {}
        if can_edit_links:
            metadata.update({
                "sex": values.get("sex", ""),
                "genotype": values.get("genotype", ""),
            })
        if can_edit_colors:
            metadata["node_fill_color"] = values.get("fill_color", "")
        try:
            self.plugin.set_dummy_parentage(
                actor=None,
                animal_id=node,
                expected_revision=getattr(self.plugin, "_active_backend_revision", None),
                values=updated_parentage if can_edit_links else None,
                source="plugin",
                allow_custom=False,
                target_metadata=metadata,
            )
        except ParentageCommandError as exc:
            QMessageBox.warning(self, self.messages.get("error.title", "Error"), exc.message)
            return
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
        except (OSError, UnicodeError):
            logging.getLogger(__name__).debug(
                "Could not load the HeritageTrack species catalog",
                exc_info=True,
            )
            return []
        return ordered_species_for_display(values)

    def _genotype_options_for_species(self, species: str) -> List[str]:
        """Return the registered genotype catalogue for one species."""
        species_text = str(species or "").strip()
        if not species_text:
            return []
        try:
            catalogue = read_genotypes(Path(__file__).resolve().parents[2])
        except Exception:
            return []
        for key, values in catalogue.items():
            if str(key).strip().casefold() == species_text.casefold():
                return list(values)
        return []

    def _open_create_animal_dialog(self) -> None:
        """Open dialog to create a new heritage-only (placeholder) animal."""
        if not self._can('heritage.view'):
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
            # New Heritage-only dummies keep the normal editable sex control.
            sex_editable=True,
            allow_name_edit=True,  # allow entering name for new animal
            allow_remove=False,    # can't remove an animal that doesn't exist yet
            animal_species="",
            species_options=species_options,
            parents_editable=self._can("heritage.edit_links"),
            parents_read_only_core=False,
            genotype_options=[],
            color_editable=self._can("heritage.edit_genotype_colors"),
            genotype_options_provider=self._genotype_options_for_species,
            parent_options_provider=lambda required, selected_species: self.plugin.parent_candidate_options(
                required,
                selected_species,
                with_status=True,
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
        self._render_cache = RenderCacheRegistry()
        self._store_flush_scheduled = False
        # Direct Heritage dummies are session-only unless the actor is
        # eligible to persist them through the Core-create/Unit policy.  They
        # intentionally never enter the Core animal dictionaries.
        self._temporary_dummies: Dict[str, Dict[str, Any]] = {}
        # A complete render/read transaction binds one immutable Core snapshot
        # to one immutable Heritage backend snapshot and revision.  The
        # active values are used by projection helpers while that frame is
        # being assembled and are replaced on the next read or explicit Core
        # invalidation.
        self._active_core_snapshot: Optional[Dict[str, Dict[str, Any]]] = None
        self._active_store_snapshot: Optional[Dict[str, Any]] = None
        self._active_backend_revision: Optional[int] = None
        self._active_core_projection_revision: str = ""
        self._engine_backend_revision: Optional[int] = None

        self.window: Optional[HeritageTrackWidget] = None
        app_instance = QApplication.instance()
        if app_instance is not None:
            app_instance.aboutToQuit.connect(self.flush_pending_store)

    def flush_pending_store(self) -> None:
        """Persist queued derived Heritage changes without raising into Qt."""
        try:
            self.store.flush_pending()
        except ConflictError:
            # A delayed derived write must never overwrite a newer session.
            # Surface a localized, non-modal notice on the next status update
            # while retaining the pending patch for an explicit retry.
            message = self.messages.get(
                "heritage_track.error.deferred_persistence_conflict",
                "Heritage data changed in another session; the derived update was not saved.",
            )
            if self.window is not None:
                self.window._position_cache_notice = message
            logging.getLogger(__name__).warning(
                "Deferred Heritage persistence conflict: %s", message
            )
        except Exception:
            logging.getLogger(__name__).exception("Failed to flush pending HeritageTrack data")

    def _flush_scheduled_store(self) -> None:
        self._store_flush_scheduled = False
        self.flush_pending_store()

    def schedule_store_flush(self) -> None:
        """Coalesce derived writes until the event loop is idle."""
        if not self.store.has_pending_changes() or self._store_flush_scheduled:
            return
        self._store_flush_scheduled = True
        QTimer.singleShot(0, self._flush_scheduled_store)

    def cache_render_entry(self, entry: RenderCacheEntry) -> None:
        """Atomically publish one complete, immutable Heritage frame."""
        self._render_cache.put(entry)

    def get_render_entry(
        self,
        key: RenderCacheKey,
        *,
        revisions: Optional[Tuple[str, str, str, str]] = None,
        dependencies: Optional[Set[str]] = None,
    ) -> Optional[RenderCacheEntry]:
        return self._render_cache.get_valid(
            key,
            revisions=revisions,
            dependencies=dependencies,
        )

    def invalidate_render_dependencies(self, dependencies: Set[str]) -> int:
        """Drop frames depending on changed stable animal/IPID keys."""
        normalized = {
            str(value).strip()
            for value in dependencies
            if str(value).strip()
        }
        removed = self._render_cache.invalidate(normalized)
        if normalized:
            try:
                self.store.invalidate_position_cache_dependencies(normalized)
            except Exception:
                # A derived position-cache cleanup must not turn a successful
                # Core/Heritage mutation into a failed command.  The revision
                # check on the next render still rejects stale entries.
                logging.getLogger(__name__).exception(
                    "Could not invalidate persisted Heritage position cache"
                )
        return removed

    def get_position_cache_entry(self, user_id: Any, cache_key: Any, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.store.get_position_cache_entry(user_id, cache_key, **kwargs)

    def set_position_cache_entry(self, user_id: Any, cache_key: Any, positions: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return self.store.set_position_cache_entry(user_id, cache_key, positions, **kwargs)

    def clear_render_cache(self) -> None:
        self._render_cache.clear()

    def remove_render_cache_entry(self, key: RenderCacheKey) -> bool:
        """Drop one malformed selection frame after explicit confirmation."""
        return self._render_cache.remove(key)

    def update_language(self, messages: Dict[str, Any]) -> None:
        self.messages = messages or {}
        if self.window is not None:
            try:
                self.window.update_language(self.messages)
            except RuntimeError:
                self.window = None

    def on_tab_hidden(self) -> None:
        """Drop non-persistent direct dummies when Heritage is left."""
        if self._temporary_dummies:
            dependencies = set(self._temporary_dummies)
            self._temporary_dummies.clear()
            self._engine_cache.invalidate()
            self.invalidate_render_dependencies(dependencies)

    def on_user_logout(self) -> None:
        """End the Heritage session and discard session-only dummies."""
        self.on_tab_hidden()

    @staticmethod
    def _copy_core_records(app: Any) -> Dict[str, Dict[str, Any]]:
        """Copy active and archived Core records for one render transaction."""
        records: Dict[str, Dict[str, Any]] = {}
        active = getattr(app, "animals", {})
        if isinstance(active, dict):
            records.update({str(key).strip(): deepcopy(value) for key, value in active.items() if str(key).strip() and isinstance(value, dict)})
        archived = getattr(app, "archived", {})
        if isinstance(archived, dict):
            records.update({str(key).strip(): deepcopy(value) for key, value in archived.items() if str(key).strip() and isinstance(value, dict)})
        return records

    def _store_snapshot_entries(self) -> Dict[str, Dict[str, Any]]:
        snapshot = self._active_store_snapshot
        if isinstance(snapshot, dict):
            entries = snapshot.get("animals", {})
            if isinstance(entries, dict):
                return entries
        return self.store.get_all_entries()

    def _clear_active_projection_snapshot(self) -> None:
        self._active_core_snapshot = None
        self._active_store_snapshot = None
        self._active_backend_revision = None
        self._active_core_projection_revision = ""

    def notify_core_records_changed(self, dependencies: Optional[Set[str]] = None) -> None:
        """Invalidate read-model state after an explicit Core mutation.

        The notification intentionally performs no Heritage write.  The next
        render reads a fresh Core snapshot and the latest Heritage revision.
        """
        self._clear_active_projection_snapshot()
        self._engine_backend_revision = None
        self._engine_cache.invalidate()
        self.clear_render_cache()
        if dependencies:
            self.invalidate_render_dependencies(set(dependencies))

    def get_parentage(self, animal_name: Optional[str], record: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        key = str(animal_name or "").strip()
        temporary = self._temporary_dummies.get(key)
        if isinstance(temporary, dict):
            return self.store._normalize_parents(temporary)
        core_record = record if isinstance(record, dict) else self._core_record(key)
        return self.store.get_parentage(
            key,
            core_record,
            core_authoritative=self._is_core_animal(key),
            snapshot=self._active_store_snapshot,
        )

    def _core_record(self, animal_name: Optional[str]) -> Optional[Dict[str, Any]]:
        key = str(animal_name or "").strip()
        if not key:
            return None
        animals = (
            self._active_core_snapshot
            if isinstance(self._active_core_snapshot, dict)
            else (self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {})
        )
        if key in animals and isinstance(animals[key], dict):
            return animals[key]
        # Core dictionaries normally use the IPID as their key, but callers
        # may pass a stable IPID while an integration keeps a display name as
        # the mapping key.  Resolve that alias before consulting Heritage
        # data so a forged Heritage mutation cannot target a real Core row.
        for candidate_key, candidate in animals.items():
            if (
                isinstance(candidate, dict)
                and str(candidate.get("ipid", "") or "").strip() == key
            ):
                return candidate
        archived = {} if isinstance(self._active_core_snapshot, dict) else (getattr(self.app, "archived", {}) or {})
        if isinstance(archived, dict) and key in archived and isinstance(archived[key], dict):
            return archived[key]
        if isinstance(archived, dict):
            for candidate in archived.values():
                if (
                    isinstance(candidate, dict)
                    and str(candidate.get("ipid", "") or "").strip() == key
                ):
                    return candidate
        return None

    def _parentage_message(self, key: str, fallback: str) -> str:
        return str(self.messages.get(key, fallback) or fallback)

    def _parentage_actor(self, actor: Any = None) -> str:
        value = str(actor or "").strip()
        if value:
            return value
        master = getattr(self.app, "master_track", None)
        return str(getattr(master, "current_username", "") or "guest").strip() or "guest"

    def _parentage_authorized(self) -> bool:
        """Check the write permission at the command boundary, not just in UI."""
        return self._action_authorized("heritage.edit_links")

    def _action_authorized(self, action: str) -> bool:
        """Resolve one permission at the mutation boundary.

        Heritage commands are callable independently of their Qt controls, so
        the command must not trust a disabled/enabled widget as authorization.
        A missing Master Track is the documented trusted-local mode; a
        configured managed service without its UI boundary fails closed.
        """
        checker = getattr(self.app, "_master_can", None)
        if callable(checker):
            try:
                return bool(checker(str(action or "").strip()))
            except Exception:
                logging.getLogger(__name__).warning(
                    "Heritage authorization check failed for %s", action,
                    exc_info=True,
                )
                return False
        authorization = getattr(self.app, "authorization", None)
        if authorization is None:
            return True
        try:
            return bool(authorization.can(str(action or "").strip()))
        except Exception:
            return bool(getattr(authorization, "trusted_local", False))

    @staticmethod
    def _dummy_kind(record: Optional[Dict[str, Any]]) -> str:
        """Return the explicit Heritage-owned dummy kind.

        ``heritage_only`` alone is deliberately insufficient: it does not
        identify whether a record is session-only, a directly persisted dummy
        or a former-Core snapshot.
        """
        if not isinstance(record, dict) or not bool(record.get("heritage_only", False)):
            return ""
        persistence = str(record.get("persistence_kind", "") or "").strip().casefold()
        if persistence == "temporary_dummy":
            return "temporary"
        if persistence == "direct_dummy":
            return "direct"
        if persistence == "former_core_dummy":
            return "former_core"
        kind = str(record.get("dummy_kind", "") or "").strip().casefold()
        if kind in {"direct", "former_core"}:
            return kind
        return ""

    def _dummy_owner_in_scope(self, record: Optional[Dict[str, Any]]) -> bool:
        """Require an explicit durable owner and the current Unit."""
        kind = self._dummy_kind(record)
        if kind == "temporary":
            return True
        if kind not in {"direct", "former_core"}:
            return False
        owner = str((record or {}).get("unit_id", "") or "").strip().casefold()
        current = self._current_unit_id().casefold()
        return bool(owner and current and owner == current)

    def _durable_dummy_delete_authorized(self, record: Dict[str, Any]) -> bool:
        """Authorize deletion of a persisted dummy, including Unit scope."""
        return (
            self._dummy_owner_in_scope(record)
            and self._action_authorized("heritage.delete_durable_dummy")
        )

    def _genotype_catalogue(self, species: str) -> List[str]:
        species_text = self.store._normalize_text(species)
        if not species_text:
            return []
        try:
            catalogue = read_genotypes(Path(__file__).resolve().parents[2])
        except Exception:
            return []
        for key, values in catalogue.items():
            if str(key).strip().casefold() == species_text.casefold():
                return [self.store._normalize_text(value) for value in values if self.store._normalize_text(value)]
        return []

    def _validate_dummy_metadata(
        self,
        target_record: Dict[str, Any],
        target_metadata: Optional[Dict[str, Any]],
        *,
        creating: bool = False,
        source: str = "plugin",
    ) -> Dict[str, Any]:
        """Validate and normalize mutable dummy metadata before one commit."""
        metadata = dict(target_metadata or {})
        if not metadata:
            return {}
        current_kind = self._dummy_kind(target_record)
        if creating:
            requested_kind = self._dummy_kind(metadata)
            if requested_kind != "temporary" and requested_kind != "direct":
                raise self._parentage_error(
                    "heritage_track.error.dummy_kind_required",
                    "A new Heritage dummy must declare a valid persistence kind.",
                )
            if not bool(metadata.get("heritage_only", False)):
                raise self._parentage_error(
                    "heritage_track.error.dummy_kind_required",
                    "A new Heritage dummy must be Heritage-owned.",
                )
        elif source != "former_core_dummy" and current_kind not in {"temporary", "direct", "former_core"}:
            raise self._parentage_error(
                "heritage_track.error.dummy_kind_required",
                "The selected record is not an identified Heritage dummy.",
            )

        normalized: Dict[str, Any] = {}
        if "sex" in metadata:
            raw_sex = self.store._normalize_text(metadata.get("sex", ""))
            normalized_sex = self.store._normalize_sex(raw_sex)
            if raw_sex and not normalized_sex:
                raise self._parentage_error(
                    "heritage_track.error.invalid_sex",
                    "Select a valid sex value.",
                )
            normalized["sex"] = normalized_sex
        if "genotype" in metadata:
            genotype = self.store._normalize_text(metadata.get("genotype", ""))
            if genotype:
                options = self._genotype_catalogue(
                    metadata.get("species", target_record.get("species", ""))
                )
                if not any(genotype.casefold() == option.casefold() for option in options):
                    raise self._parentage_error(
                        "heritage_track.error.invalid_genotype",
                        "Select a genotype registered for this species.",
                    )
            normalized["genotype"] = genotype
        if "node_fill_color" in metadata:
            color = self.store._normalize_text(metadata.get("node_fill_color", ""))
            if color and not QColor(color).isValid():
                raise self._parentage_error(
                    "heritage_track.error.invalid_color",
                    "Select a valid display colour.",
                )
            normalized["node_fill_color"] = color

        # Identity, ownership and lifecycle markers are immutable after a
        # dummy has been created.  A caller may provide them only for a new
        # record (or for the internal former-Core snapshot command).
        if not creating and source != "former_core_dummy":
            for field in (
                "name", "_base_name", "display_name", "species", "birth_date",
                "heritage_only", "identity_review_required", "identity_review_reason",
                "unit_id", "dummy_kind", "persistence_kind",
            ):
                if field in metadata and metadata[field] != target_record.get(field):
                    raise self._parentage_error(
                        "heritage_track.error.permission_denied",
                        "Heritage dummy identity and ownership fields are immutable.",
                    )
        for field in ("name", "_base_name", "display_name", "species", "birth_date", "heritage_only", "identity_review_required", "identity_review_reason", "unit_id", "dummy_kind", "persistence_kind"):
            if field in metadata and field not in normalized:
                normalized[field] = deepcopy(metadata[field])
        return normalized

    def _heritage_view_authorized(self) -> bool:
        checker = getattr(self.app, "_master_can", None)
        if callable(checker):
            try:
                return bool(checker("heritage.view"))
            except Exception:
                return False
        authorization = getattr(self.app, "authorization", None)
        if authorization is None:
            return True
        # Managed installations may expose only the authorization service,
        # without the Master Track UI helper.  Resolve the same action there
        # instead of accidentally denying every Heritage viewer (or treating
        # a service exception as trusted-local access).
        can = getattr(authorization, "can", None)
        if callable(can):
            try:
                return bool(can("heritage.view"))
            except Exception:
                logging.getLogger(__name__).warning(
                    "Heritage view authorization check failed", exc_info=True
                )
                return False
        return bool(getattr(authorization, "trusted_local", False))

    def _current_unit_id(self) -> str:
        master = getattr(self.app, "master_track", None)
        value = str(getattr(master, "current_unit_id", "") or "").strip()
        if value:
            return value
        user = getattr(master, "current_user", None)
        if isinstance(user, dict):
            return str(user.get("unit_id", "") or user.get("unit", "") or "").strip()
        return ""

    def _durable_dummy_allowed(self, unit_id: str = "") -> bool:
        """Check Core-create eligibility without granting Core mutation."""
        checker_for_unit = getattr(self.app, "_master_can_for_unit", None)
        checker = getattr(self.app, "_master_can", None)
        unit = str(unit_id or self._current_unit_id()).strip()
        if callable(checker_for_unit) and unit:
            try:
                return bool(checker_for_unit("core.create_animals", unit))
            except TypeError:
                try:
                    return bool(checker_for_unit("core.create_animals", owner_unit_id=unit))
                except Exception:
                    pass
            except Exception:
                return False
        if callable(checker):
            try:
                return bool(checker("core.create_animals"))
            except Exception:
                return False
        # A missing authorization service is trusted-local mode for protected
        # commands, but still does not provide a Core-create permission token.
        return False

    def _record_in_unit_scope(self, record: Dict[str, Any]) -> bool:
        """Hide explicit cross-Unit Heritage records from candidate queries."""
        current = self._current_unit_id().casefold()
        # ``unit_id`` on a Core animal is commonly a CageTrack housing unit,
        # not the Master organizational Unit.  Only Heritage-owned dummies
        # may use that field for ownership; Core records can opt into an
        # explicit organizational-unit key when the Core service supplies it.
        heritage_owned = bool(
            record.get("heritage_only")
            or record.get("dummy_kind")
            or record.get("persistence_kind")
        )
        owner_value = record.get("unit_id", "") if heritage_owned else ""
        if not owner_value:
            for field in ("organization_unit_id", "organizational_unit_id", "workgroup_id"):
                if record.get(field):
                    owner_value = record.get(field)
                    break
        owner = str(owner_value or "").strip().casefold()
        return not owner or not current or owner == current

    @staticmethod
    def _parentage_token(actor: str, sequence: int) -> Tuple[str, str]:
        now = datetime.now(timezone.utc)
        token = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z|{actor}|{sequence}"
        display = now.strftime("%Y-%m-%d %H.%M") + f" {actor}"
        return token, display

    def _parentage_records(
        self,
        core_record: Optional[Dict[str, Any]] = None,
        target_key: str = "",
        *,
        store_snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        records = {
            str(key): value
            for key, value in self._all_identity_records().items()
            if isinstance(value, dict)
        }
        if isinstance(store_snapshot, dict):
            store_animals = store_snapshot.get("animals", {})
            if isinstance(store_animals, dict):
                for key, value in store_animals.items():
                    if isinstance(value, dict):
                        records.setdefault(str(key), value)
        if core_record is not None and target_key:
            target_text = str(target_key).strip()
            if target_text in records:
                records[target_text] = core_record
            else:
                # Dialog callers normally pass the canonical identity key,
                # but resolving a display name here should not manufacture a
                # second identity that turns an otherwise valid target into an
                # ambiguity.  Keep the uncommitted record on the one matching
                # canonical key when the display name is unique.
                folded = target_text.casefold()
                matches = [
                    key for key, value in records.items()
                    if animal_base_name(key, value).casefold() == folded
                ]
                if len(matches) == 1:
                    records[matches[0]] = core_record
                else:
                    records[target_text] = core_record
        return records

    def _resolve_parentage_reference(
        self,
        value: Any,
        records: Dict[str, Dict[str, Any]],
        target_species: str = "",
    ) -> Tuple[str, Dict[str, Any], str]:
        """Resolve an IPID/display name without letting a legacy short key
        suppress an otherwise ambiguous display name.

        Canonical identity keys (``name | species | date | origin``) always
        win as exact references.  Short names are resolved only when unique,
        with the target-species narrowing rule applied before ambiguity is
        reported.
        """
        text = self.store._normalize_text(value)
        if not text:
            return "", {}, "missing"
        if split_animal_identity_key(text) is not None and text in records:
            return text, records.get(text, {}), "resolved"
        folded = text.casefold()
        matches = [
            (str(key), record)
            for key, record in records.items()
            if isinstance(record, dict) and animal_base_name(key, record).casefold() == folded
        ]
        if target_species:
            species_matches = [
                item for item in matches
                if self.store._normalize_text(item[1].get("species", "")).casefold()
                == self.store._normalize_text(target_species).casefold()
            ]
            if species_matches:
                matches = species_matches
        if len(matches) == 1:
            return matches[0][0], matches[0][1], "resolved"
        if len(matches) > 1:
            return "", {}, "ambiguous"
        if text in records:
            return text, records.get(text, {}), "resolved"
        return text, {}, "missing"

    def _parentage_error(self, code: str, fallback: str) -> ParentageCommandError:
        return ParentageCommandError(code, self._parentage_message(code, fallback))

    def _parentage_default_entry(self, key: str, *, name: str = "",
                                 species: str = "", sex: str = "",
                                 birth_date: str = "", heritage_only: bool = True) -> Dict[str, Any]:
        visible = str(name or animal_base_name(key)).strip()
        return {
            **{parent_key: "" for parent_key in ("egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father")},
            "ipid": key,
            "name": visible,
            "_base_name": visible,
            "display_name": visible,
            "genotype": "",
            "node_fill_color": "",
            "sex": sex,
            "species": species,
            "birth_date": birth_date,
            "heritage_only": heritage_only,
            "unit_id": "",
            "dummy_kind": "",
            "persistence_kind": "",
            "source": "plugin",
            "updated_at": "",
            "inbreeding_f": None,
            "parentage_revision": "",
            "parentage_revision_display": "",
            "genetic_parentage_revision": "",
            "inbreeding_f_cache": None,
        }

    def _parentage_date(self, value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text or text.casefold() in {"undated", "unknown"}:
            return None
        try:
            return datetime.strptime(normalize_birth_date(text, required=True), "%d.%m.%Y")
        except (TypeError, ValueError):
            return None

    def _parentage_lineage_map(self, records: Dict[str, Dict[str, Any]]) -> Dict[str, Tuple[str, str]]:
        result: Dict[str, Tuple[str, str]] = {}
        for key, record in records.items():
            if not isinstance(record, dict):
                continue
            values = self.store._normalize_parents(record)
            # Core records use the native German field names; Heritage-owned
            # entries already use the canonical graph names.
            if not values.get("egg_donor"):
                values["egg_donor"] = self.store._normalize_text(record.get("eizellspenderin", ""))
            if not values.get("sperm_donor"):
                values["sperm_donor"] = self.store._normalize_text(record.get("samenspender", ""))
            result[str(key)] = (
                str(values.get("egg_donor", "") or "").strip(),
                str(values.get("sperm_donor", "") or "").strip(),
            )
        return result

    @staticmethod
    def _parentage_has_cycle(parent_map: Dict[str, Tuple[str, str]]) -> bool:
        states: Dict[str, int] = {}

        def visit(node: str) -> bool:
            state = states.get(node, 0)
            if state == 1:
                return True
            if state == 2:
                return False
            states[node] = 1
            for parent in parent_map.get(node, ("", "")):
                if parent and visit(parent):
                    return True
            states[node] = 2
            return False

        return any(visit(node) for node in set(parent_map) | {
            parent for values in parent_map.values() for parent in values if parent
        })

    @staticmethod
    def _parentage_dependency_closure(parent_map: Dict[str, Tuple[str, str]], seeds: Set[str]) -> Set[str]:
        reverse: Dict[str, Set[str]] = defaultdict(set)
        for child, parents in parent_map.items():
            for parent in parents:
                if parent:
                    reverse[parent].add(child)
        result: Set[str] = set(str(seed).strip() for seed in seeds if str(seed).strip())
        stack = list(result)
        while stack:
            node = stack.pop()
            neighbours = set(parent_map.get(node, ("", ""))) | reverse.get(node, set())
            for neighbour in neighbours:
                if neighbour and neighbour not in result:
                    result.add(neighbour)
                    stack.append(neighbour)
        return result

    def set_dummy_parentage(
        self,
        actor: Any = None,
        animal_id: Any = None,
        expected_revision: Any = None,
        values: Optional[Dict[str, Any]] = None,
        *,
        source: str = "plugin",
        core_record: Optional[Dict[str, Any]] = None,
        allow_custom: bool = False,
        explicit_custom_parents: Optional[Set[str]] = None,
        target_metadata: Optional[Dict[str, Any]] = None,
        create: bool = False,
    ) -> bool:
        """Canonical mutation command for Heritage-owned dummy records.

        The command accepts display names but stores only canonical identity
        keys.  Validation and custom-ancestor materialization happen against a
        private snapshot.  Only Heritage-owned records are writable here;
        Core records are observed, never projected back into the application.
        """
        target_text = str(animal_id or "").strip()
        if not target_text:
            raise self._parentage_error("heritage_track.error.target_required", "An animal is required.")
        # Former-Core snapshots are an internal side effect of the Core
        # deletion transaction.  They must never be creatable or editable by
        # forging the public command's ``source`` argument; the guarded Core
        # deletion/facade path is the sole producer.
        if str(source or "").strip() == "former_core_dummy":
            raise self._parentage_error(
                "heritage_track.error.permission_denied",
                "Former-Core snapshots can only be created by the Core deletion workflow.",
            )
        # Core records are never writable through this command.  Former-Core
        # snapshots are produced by the backend's coordinated Core-deletion
        # transaction, never by this public Heritage mutation command.
        if core_record is not None and source != "former_core_dummy":
            raise self._parentage_error(
                "heritage_track.error.core_read_only",
                "Core animals are read-only in Heritage Track.",
            )
        if not self._heritage_view_authorized() and source != "former_core_dummy":
            raise self._parentage_error(
                "heritage_track.error.permission_denied",
                "You do not have permission to use Heritage Track.",
            )
        raw_values = values if isinstance(values, dict) else {}
        # ``values=None`` and an empty mapping are metadata-only commands.
        # A mapping that explicitly contains the parent slots (even when all
        # are empty, to clear existing links) is a parentage request and must
        # pass the edit-links permission check.
        parentage_requested = isinstance(values, dict) and any(
            key in values for key in PARENT_KEYS
        )
        requested_values = {
            self.store._normalize_text(raw_values.get(key, ""))
            for key in ("egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father")
        }
        if self._is_core_animal(target_text) and source != "former_core_dummy":
            raise self._parentage_error(
                "heritage_track.error.core_read_only",
                "Core animals are read-only in Heritage Track.",
            )
        explicit = {
            self.store._normalize_text(value)
            for value in (explicit_custom_parents or set())
            if self.store._normalize_text(value)
        }

        latest_snapshot, backend_revision = self.store.load_latest_with_revision()
        records = self._parentage_records(
            core_record,
            target_text,
            store_snapshot=latest_snapshot,
        )
        if create:
            # Creation is an explicit command, not an inferred update.  The
            # identity-absent check is repeated inside ``mutate`` below so a
            # stale client cannot overwrite a concurrently created dummy.
            if target_text in records or any(
                isinstance(record, dict)
                and str(record.get("ipid", "") or "").strip() == target_text
                for record in records.values()
            ):
                raise self._parentage_error(
                    "heritage_track.error.name_exists_heritage",
                    "This animal already exists in Heritage data.",
                )
            if not isinstance(target_metadata, dict):
                raise self._parentage_error(
                    "heritage_track.error.dummy_kind_required",
                    "A new Heritage dummy must declare a valid persistence kind.",
                )
            records[target_text] = deepcopy(target_metadata)
        elif (
            isinstance(target_metadata, dict)
            and target_text not in records
            and source != "former_core_dummy"
        ):
            # A newly-created durable or temporary dummy is not in either
            # backend snapshot yet; validate it against the proposed record.
            records[target_text] = deepcopy(target_metadata)
        target_key, target_record, target_status = self._resolve_parentage_reference(
            target_text, records, target_species=""
        )
        if target_status != "resolved" or not isinstance(target_record, dict):
            raise self._parentage_error(
                "heritage_track.error.target_missing",
                "The selected animal is no longer available.",
            )
        target_key = str(target_key).strip()
        if self._is_core_animal(target_key) and source != "former_core_dummy":
            raise self._parentage_error(
                "heritage_track.error.core_read_only",
                "Core animals are read-only in Heritage Track.",
            )
        if source != "former_core_dummy" and not self._record_in_unit_scope(target_record):
            raise self._parentage_error(
                "heritage_track.error.permission_denied",
                "The selected Heritage dummy is outside your authorized Unit scope.",
            )
        creating_dummy = bool(create)
        target_kind = self._dummy_kind(target_metadata if creating_dummy else target_record)
        if source != "former_core_dummy":
            if creating_dummy:
                if target_kind not in {"temporary", "direct"}:
                    raise self._parentage_error(
                        "heritage_track.error.dummy_kind_required",
                        "A new Heritage dummy must declare a valid persistence kind.",
                    )
                if target_kind == "direct":
                    unit_id = str((target_metadata or {}).get("unit_id", "") or "").strip()
                    if not unit_id or not self._durable_dummy_allowed(unit_id):
                        raise self._parentage_error(
                            "heritage_track.error.dummy_owner_scope",
                            "A persistent Heritage dummy requires a valid authorized Unit.",
                        )
                if any(requested_values) and not self._parentage_authorized():
                    raise self._parentage_error(
                        "heritage_track.error.permission_denied",
                        "You do not have permission to edit Heritage parentage.",
                    )
            else:
                if target_kind not in {"temporary", "direct", "former_core"}:
                    raise self._parentage_error(
                        "heritage_track.error.dummy_kind_required",
                        "The selected record is not an identified Heritage dummy.",
                    )
                if target_kind in {"direct", "former_core"} and not self._dummy_owner_in_scope(target_record):
                    raise self._parentage_error(
                        "heritage_track.error.dummy_owner_scope",
                        "This persistent Heritage dummy is outside your Unit scope.",
                    )
                if parentage_requested and not self._parentage_authorized():
                    raise self._parentage_error(
                        "heritage_track.error.permission_denied",
                        "You do not have permission to edit Heritage parentage.",
                    )
        normalized_metadata = self._validate_dummy_metadata(
            target_record,
            target_metadata,
            creating=creating_dummy,
            source=source,
        )
        if (
            any(field in normalized_metadata for field in ("sex", "genotype"))
            and not creating_dummy
            and source != "former_core_dummy"
            and not self._parentage_authorized()
        ):
            raise self._parentage_error(
                "heritage_track.error.permission_denied",
                "You do not have permission to edit Heritage dummy metadata.",
            )
        if (
            "node_fill_color" in normalized_metadata
            and source != "former_core_dummy"
            and normalized_metadata.get("node_fill_color")
            != self.store._normalize_text(target_record.get("node_fill_color", ""))
            and not self._action_authorized("heritage.edit_genotype_colors")
        ):
            raise self._parentage_error(
                "heritage_track.error.permission_denied",
                "You do not have permission to edit Heritage genotype display colours.",
            )
        target_species = self.store._normalize_text(target_record.get("species", ""))
        target_birth_text = self.store._normalize_text(target_record.get("birth_date", ""))
        target_birth = self._parentage_date(target_birth_text)
        if target_birth_text and target_birth is None and target_birth_text.casefold() not in {"undated", "unknown"}:
            raise self._parentage_error("heritage_track.error.invalid_date", "The animal birth date is invalid.")
        old_stored = latest_snapshot.get("animals", {}).get(target_key, {})
        if target_key in self._temporary_dummies:
            old_stored = self._temporary_dummies.get(target_key, {})
        if not isinstance(old_stored, dict):
            old_stored = {}
        old_revision_token = str((old_stored or {}).get("parentage_revision", "") or "").strip()
        if expected_revision is not None:
            expected_text = str(expected_revision).strip()
            if expected_text.isdigit() and int(expected_text) != backend_revision:
                raise self._parentage_error("heritage_track.error.parentage_conflict", "The parentage data changed. Reload it and try again.")
            if not expected_text.isdigit() and expected_text != old_revision_token:
                raise self._parentage_error("heritage_track.error.parentage_conflict", "The parentage data changed. Reload it and try again.")

        requested = (
            {key: self.store._normalize_text(raw_values.get(key, "")) for key in (
                "egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father"
            )}
            if parentage_requested
            else self.store._normalize_parents(target_record)
        )

        combined_records = dict(records)
        canonical: Dict[str, str] = {}
        custom_entries: Dict[str, Dict[str, Any]] = {}
        slot_sex = {
            "egg_donor": "female", "sperm_donor": "male",
            "surrogate_mother": "female", "surrogate_father": "male",
        }

        for field, raw_value in requested.items():
            if not raw_value:
                canonical[field] = ""
                continue
            resolved, record, status = self._resolve_parentage_reference(
                raw_value, combined_records, target_species=target_species
            )
            if status == "ambiguous":
                raise self._parentage_error("heritage_track.error.parent_ambiguous", "Parent name is ambiguous. Please select the full animal identity.")
            if status == "missing":
                if not allow_custom and raw_value not in explicit:
                    raise self._parentage_error("heritage_track.error.parent_not_selected", "Select an existing animal or explicitly add a Heritage-only ancestor.")
                base = animal_base_name(raw_value)
                custom_species = target_species or "Unknown species"
                parts = split_animal_identity_key(raw_value)
                if parts is not None:
                    base, custom_species, custom_birth, custom_origin = parts
                    birth_text = "" if str(custom_birth).casefold() == "undated" else custom_birth
                    custom_key = raw_value
                else:
                    birth_text = ""
                    custom_origin = "Heritage Track"
                    custom_key = f"{base} | {custom_species} | undated | {custom_origin}"
                if "|" in base:
                    raise self._parentage_error("heritage_track.error.parent_invalid", "Parent identity is invalid.")
                duplicate = any(
                    animal_base_name(key, rec).casefold() == base.casefold()
                    and self.store._normalize_text(rec.get("species", "")).casefold() == custom_species.casefold()
                    for key, rec in combined_records.items()
                    if isinstance(rec, dict)
                )
                if duplicate:
                    raise self._parentage_error("heritage_track.error.parent_duplicate_identity", "A Heritage animal with this name and species already exists.")
                custom_record = self._parentage_default_entry(
                    custom_key, name=base, species=custom_species,
                    sex=slot_sex[field], birth_date=birth_text,
                )
                custom_entries[custom_key] = custom_record
                combined_records[custom_key] = custom_record
                resolved = custom_key
                record = custom_record
            canonical[field] = str(resolved).strip()
            parent_record = record if isinstance(record, dict) else combined_records.get(canonical[field], {})
            if not isinstance(parent_record, dict):
                raise self._parentage_error("heritage_track.error.parent_missing", "The selected parent is no longer available.")
            if not self._record_in_unit_scope(parent_record):
                raise self._parentage_error(
                    "heritage_track.error.parent_not_selected",
                    "The selected parent is outside your authorized Unit scope.",
                )
            parent_sex = self.get_effective_sex(canonical[field], parent_record)
            if canonical[field] in custom_entries:
                parent_sex = slot_sex[field]
            if parent_sex == "unknown" or not parent_sex:
                raise self._parentage_error("heritage_track.error.parent_unknown_sex", "A parent must have a known sex.")
            if parent_sex != slot_sex[field]:
                key = "heritage_track.error.mother_not_female" if slot_sex[field] == "female" else "heritage_track.error.father_not_male"
                fallback = "Selected mother must be female." if slot_sex[field] == "female" else "Selected father must be male."
                raise self._parentage_error(key, fallback)
            parent_species = self.store._normalize_text(parent_record.get("species", ""))
            if target_species and parent_species != target_species:
                raise self._parentage_error("heritage_track.error.parent_wrong_species", "Selected parents must have the same species as the animal.")
            parent_birth_text = self.store._normalize_text(parent_record.get("birth_date", ""))
            parent_birth = self._parentage_date(parent_birth_text)
            if parent_birth_text and parent_birth is None and parent_birth_text.casefold() not in {"undated", "unknown"}:
                raise self._parentage_error("heritage_track.error.invalid_date", "The parent birth date is invalid.")
            if target_birth and parent_birth and parent_birth >= target_birth:
                raise self._parentage_error("heritage_track.error.parent_too_young", "A parent must be older than the animal.")
            if canonical[field] == target_key:
                raise self._parentage_error("heritage_track.error.parent_self", "An animal cannot be set as its own parent.")

        if canonical["egg_donor"] and canonical["egg_donor"] == canonical["sperm_donor"]:
            raise self._parentage_error("heritage_track.error.parent_same", "Mother and father must be different animals.")
        if canonical["surrogate_mother"] and canonical["surrogate_mother"] == canonical["surrogate_father"]:
            raise self._parentage_error("heritage_track.error.parent_same", "Surrogate parents must be different animals.")

        old_records = self._parentage_records(
            core_record,
            target_key,
            store_snapshot=latest_snapshot,
        )
        old_map = self._parentage_lineage_map(old_records)
        new_map = dict(old_map)
        new_map[target_key] = (canonical["egg_donor"], canonical["sperm_donor"])
        new_map.update({key: (str(record.get("egg_donor", "")), str(record.get("sperm_donor", ""))) for key, record in custom_entries.items()})
        if self._parentage_has_cycle(new_map):
            raise self._parentage_error("heritage_track.error.circular_parentage", "Invalid parent assignment: it would create a circular pedigree.")

        genetic_changed = old_map.get(target_key, ("", "")) != new_map.get(target_key, ("", ""))

        sequence = 0
        try:
            sequence = int(latest_snapshot.get("parentage_sequence", 0) or 0) + 1
        except (TypeError, ValueError):
            sequence = 1
        token, display_token = self._parentage_token(self._parentage_actor(actor), sequence)
        pedigree_sequence = 0
        pedigree_token = str(latest_snapshot.get("pedigree_revision", "") or "").strip()
        try:
            pedigree_sequence = int(latest_snapshot.get("pedigree_sequence", 0) or 0)
        except (TypeError, ValueError):
            pedigree_sequence = 0
        if genetic_changed:
            pedigree_sequence += 1
            # The command revision is also the pedigree revision for this
            # mutation.  A single comparable token avoids two timestamps for
            # one commit while the separate pedigree_sequence remains useful
            # for lineage diagnostics.
            pedigree_token = token

        target_is_temporary = (
            target_key in self._temporary_dummies
            or (
                isinstance(target_metadata, dict)
                and str(target_metadata.get("persistence_kind", "")).strip()
                == "temporary_dummy"
            )
        )

        def mutate(data: Dict[str, Any]) -> bool:
            animals = data.setdefault("animals", {})
            if not isinstance(animals, dict):
                raise self._parentage_error("heritage_track.error.parent_invalid", "Heritage data is invalid.")
            if creating_dummy and target_key in animals:
                raise self._parentage_error(
                    "heritage_track.error.name_exists_heritage",
                    "This animal already exists in Heritage data.",
                )
            for key, custom_record in custom_entries.items():
                if key in animals:
                    raise self._parentage_error(
                        "heritage_track.error.parent_duplicate_identity",
                        "A Heritage animal with this name and species already exists.",
                    )
                if not target_is_temporary:
                    # Custom ancestors materialized as part of a durable
                    # command inherit the owning Unit and explicit durable
                    # kind; they must not become unowned legacy records.
                    custom_record = deepcopy(custom_record)
                    custom_record["dummy_kind"] = "direct"
                    custom_record["persistence_kind"] = "direct_dummy"
                    custom_record["unit_id"] = str(
                        (target_metadata or {}).get("unit_id", "")
                        or target_record.get("unit_id", "")
                        or self._current_unit_id()
                    ).strip()
                animals[key] = deepcopy(custom_record)
            entry = animals.get(target_key)
            if not isinstance(entry, dict):
                entry = self._parentage_default_entry(
                    target_key, name=animal_base_name(target_key, target_record),
                    species=target_species,
                    sex=self.store._normalize_sex(target_record.get("sex", "")),
                    birth_date=self.store._normalize_text(target_record.get("birth_date", "")),
                    heritage_only=False,
                )
                animals[target_key] = entry
            for field, value in canonical.items():
                entry[field] = value
            entry["source"] = self.store._normalize_text(source) or "plugin"
            for metadata_key, metadata_value in normalized_metadata.items():
                entry[metadata_key] = deepcopy(metadata_value)
            entry["parentage_revision"] = token
            entry["parentage_revision_display"] = display_token
            if genetic_changed:
                entry["genetic_parentage_revision"] = pedigree_token
                entry.pop("inbreeding_f_cache", None)
            entry["updated_at"] = self.store._utc_now_iso()
            data["parentage_sequence"] = sequence
            if genetic_changed:
                data["pedigree_sequence"] = pedigree_sequence
                data["pedigree_revision"] = pedigree_token
                entry["inbreeding_f"] = None
            return True

        if target_is_temporary:
            # Temporary direct dummies never reach the backend or audit log.
            working = deepcopy(self._temporary_dummies.get(target_key, target_record))
            for key, custom_record in custom_entries.items():
                temporary_record = deepcopy(custom_record)
                temporary_record["dummy_kind"] = "direct"
                temporary_record["persistence_kind"] = "temporary_dummy"
                temporary_record["unit_id"] = self._current_unit_id()
                self._temporary_dummies[key] = temporary_record
            for field, value in canonical.items():
                working[field] = value
            for metadata_key, metadata_value in normalized_metadata.items():
                working[metadata_key] = deepcopy(metadata_value)
            working["parentage_revision"] = token
            working["parentage_revision_display"] = display_token
            working["updated_at"] = self.store._utc_now_iso()
            self._temporary_dummies[target_key] = working
        else:
            try:
                self.store.atomic_update(mutate, expected_revision=backend_revision)
            except ParentageCommandError:
                raise
            except Exception as exc:
                if isinstance(exc, ConflictError):
                    raise self._parentage_error("heritage_track.error.parentage_conflict", "The parentage data changed. Reload it and try again.") from exc
                raise

        dependencies = self._parentage_dependency_closure(old_map, {target_key, *canonical.values()})
        dependencies |= self._parentage_dependency_closure(new_map, {target_key, *canonical.values()})
        self._engine_cache.invalidate()
        self.invalidate_render_dependencies(dependencies)
        details = (
            f"animal={target_key}; previous={json.dumps(self.store._normalize_parents(old_stored), ensure_ascii=False, sort_keys=True)}; "
            f"new={json.dumps(canonical, ensure_ascii=False, sort_keys=True)}; revision={token}"
        )
        audit_fn = getattr(self.app, "_master_audit", None)
        if callable(audit_fn) and not target_is_temporary:
            audit_fn("heritage.parentage_update", target_key, details)
        return True

    def set_parentage(self, *args: Any, **kwargs: Any) -> bool:
        """Reject Core writes and route legacy dummy calls explicitly.

        Core dialog code no longer calls this method.  Keeping the narrow
        routing point avoids accidentally reintroducing a Heritage→Core write
        while existing Heritage-only callers transition to
        :meth:`set_dummy_parentage`.
        """
        if kwargs.get("core_record") is not None:
            raise self._parentage_error(
                "heritage_track.error.core_read_only",
                "Core animals are read-only in Heritage Track.",
            )
        return self.set_dummy_parentage(*args, **kwargs)

    def _is_core_animal(self, animal_name: Optional[str]) -> bool:
        key = str(animal_name or "").strip()
        if not key:
            return False
        animals = (
            self._active_core_snapshot
            if isinstance(self._active_core_snapshot, dict)
            else (self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {})
        )
        archived = {} if isinstance(self._active_core_snapshot, dict) else (getattr(self.app, "archived", {}) or {})
        if key in animals or (isinstance(archived, dict) and key in archived):
            return True
        for records in (animals, archived if isinstance(archived, dict) else {}):
            for record in records.values():
                if (
                    isinstance(record, dict)
                    and str(record.get("ipid", "") or "").strip() == key
                ):
                    return True
        return False

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
        *,
        with_status: bool = False,
    ) -> List[Any]:
        """Return canonical same-species candidates for a controlled parent picker.

        Archived and deceased records remain valid historical parents.  They
        are intentionally included here; the editor can still distinguish
        their inactive state through the candidate tooltip/annotation.
        """
        required = self.store._normalize_sex(required_sex)
        species = str(target_species or "").strip()
        excluded = str(exclude_animal or "").strip()
        candidates: List[str] = []
        inactive: Set[str] = set()

        active = self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {}
        for key, record in active.items():
            if key == excluded or not isinstance(record, dict):
                continue
            if not self._record_in_unit_scope(record):
                continue
            record_species = str(record.get("species", "") or "").strip()
            if species and record_species != species:
                continue
            if self.get_effective_sex(key, record) == required:
                candidates.append(key)
                if self._record_is_inactive(record):
                    inactive.add(key)

        archived = getattr(self.app, "archived", {}) or {}
        if isinstance(archived, dict):
            for key, record in archived.items():
                if key == excluded or key in active or not isinstance(record, dict):
                    continue
                if not self._record_in_unit_scope(record):
                    continue
                record_species = str(record.get("species", "") or "").strip()
                if species and record_species != species:
                    continue
                if self.get_effective_sex(key, record) == required:
                    candidates.append(key)
                    if self._record_is_inactive(record):
                        inactive.add(key)
        archived_keys = set(archived) if isinstance(archived, dict) else set()
        store_entries = self._store_snapshot_entries()
        for key, record in store_entries.items():
            if (
                key == excluded
                or key in active
                or key in archived_keys
                or not isinstance(record, dict)
            ):
                continue
            record_species = str(record.get("species", "") or "").strip()
            if species and record_species != species:
                continue
            if self.get_effective_sex(key, record) == required:
                candidates.append(key)
                if self._record_is_inactive(record):
                    inactive.add(key)

        # Session-only dummies participate in the same controlled picker as
        # durable Heritage entries, but never get copied into Core records.
        for key, record in self._temporary_dummies.items():
            if (
                key == excluded
                or key in active
                or key in archived_keys
                or key in store_entries
                or not isinstance(record, dict)
            ):
                continue
            if not self._record_in_unit_scope(record):
                continue
            record_species = str(record.get("species", "") or "").strip()
            if species and record_species != species:
                continue
            if self.get_effective_sex(key, record) == required:
                candidates.append(key)
                if self._record_is_inactive(record):
                    inactive.add(key)

        ordered = sorted(set(candidates), key=str.casefold)
        if not with_status:
            return ordered
        inactive_label = self._parentage_message(
            "heritage_track.parent.inactive", "inactive"
        )
        return [
            (key, f"{key} ({inactive_label})" if key in inactive else key)
            for key in ordered
        ]

    def create_parent_group(self, animal_name: Optional[str], record: Optional[Dict[str, Any]] = None):
        values = self.get_parentage(animal_name, record)
        source = record if isinstance(record, dict) else self._core_record(animal_name) or {}
        species = str(source.get("species", "") or "").strip()
        options = {
            "egg_donor": self.parent_candidate_options("female", species, str(animal_name or ""), with_status=True),
            "sperm_donor": self.parent_candidate_options("male", species, str(animal_name or ""), with_status=True),
            "surrogate_mother": self.parent_candidate_options("female", species, str(animal_name or ""), with_status=True),
            "surrogate_father": self.parent_candidate_options("male", species, str(animal_name or ""), with_status=True),
        }
        return build_parent_group(self.messages, values, options)

    def read_parent_group(self, parent_fields: Dict[str, Any]) -> Dict[str, str]:
        return extract_parent_values(parent_fields)

    def save_parentage(
        self,
        animal_name: str,
        parent_values: Dict[str, Any],
        source: str = "plugin",
        *,
        core_record: Optional[Dict[str, Any]] = None,
        allow_custom: bool = True,
    ) -> bool:
        """Compatibility shim; all callers now execute the canonical command."""
        return self.set_dummy_parentage(
            actor=None,
            animal_id=animal_name,
            expected_revision=None,
            values=parent_values,
            source=source,
            core_record=core_record,
            allow_custom=allow_custom,
        )

    def get_settings(self) -> Dict[str, Any]:
        return self.store.get_settings()

    def set_settings(self, settings: Dict[str, Any]) -> None:
        self.store.set_settings(settings)
        # Layout/display settings are part of the render identity.  Existing
        # entries must not be reused after a setting change.
        self.clear_render_cache()

    def sync_from_record(self, animal_name: str, record: Dict[str, Any], in_main_animals: bool = True) -> None:
        # Historical Core save hooks call this method.  Keep the hook but make
        # it a read-model invalidation only; no Core shadow is persisted.
        _ = (record, in_main_animals)
        self.notify_core_records_changed({str(animal_name or "").strip()})

    def is_heritage_only(self, animal_name: str) -> bool:
        key = str(animal_name or "").strip()
        if not key or self._is_core_animal(key):
            return False
        return key in self._temporary_dummies or key in self._store_snapshot_entries()

    def can_remove_heritage_only(self, animal_name: str) -> bool:
        """Return the same removal decision used by the command boundary."""
        key = str(animal_name or "").strip()
        if not key or not self._heritage_view_authorized() or self._is_core_animal(key):
            return False
        if key in self._temporary_dummies:
            return True
        snapshot, _revision = self.store.load_latest_with_revision()
        animals = snapshot.get("animals", {}) if isinstance(snapshot, dict) else {}
        entry = animals.get(key) if isinstance(animals, dict) else None
        return isinstance(entry, dict) and self._durable_dummy_delete_authorized(entry)

    def delete_heritage_only_animal(self, animal_name: str) -> bool:
        key = str(animal_name or "").strip()
        if not key or not self._heritage_view_authorized():
            return False
        if key in self._temporary_dummies:
            self._temporary_dummies.pop(key, None)
            self._engine_cache.invalidate()
            self.invalidate_render_dependencies({key})
            return True
        # Durable dummy removal uses one optimistic backend transaction so a
        # concurrent session cannot delete a newly-updated lineage snapshot.
        try:
            snapshot, revision = self.store.load_latest_with_revision()
            stored_animals = snapshot.get("animals", {}) if isinstance(snapshot, dict) else {}
            stored_entry = stored_animals.get(key) if isinstance(stored_animals, dict) else None
            if not isinstance(stored_entry, dict):
                return False
            if self._dummy_kind(stored_entry) not in {"direct", "former_core"}:
                return False
            if not self._durable_dummy_delete_authorized(stored_entry):
                return False

            def mutate(data: Dict[str, Any]) -> bool:
                animals = data.get("animals", {})
                if not isinstance(animals, dict) or key not in animals:
                    return False
                entry = animals.get(key)
                if (
                    not isinstance(entry, dict)
                    or self._dummy_kind(entry) not in {"direct", "former_core"}
                    or not self._durable_dummy_delete_authorized(entry)
                ):
                    return False
                animals.pop(key, None)
                positions = data.get("node_positions", {})
                if isinstance(positions, dict):
                    positions.pop(key, None)
                for entry in animals.values():
                    if not isinstance(entry, dict):
                        continue
                    for parent_key in (
                        "egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father"
                    ):
                        if self.store._normalize_text(entry.get(parent_key, "")) == key:
                            entry[parent_key] = ""
                            entry["updated_at"] = self.store._utc_now_iso()
                return True

            deleted = bool(self.store.atomic_update(
                mutate,
                expected_revision=revision,
            ))
        except (ConflictError, ParentageCommandError):
            return False
        except Exception:
            logging.getLogger(__name__).exception("Could not remove durable Heritage dummy")
            return False
        if deleted:
            self.invalidate_render_dependencies({key})
        return deleted

    def promote_core_to_former_dummy(
        self,
        animal_id: str,
        record: Dict[str, Any],
        *,
        authorized: bool = False,
    ) -> bool:
        """Retain a deleted Core parent as an editable Heritage dummy.

        The promotion is executed through the same canonical parentage
        command before the Core row is removed.  Identity and all existing
        lineage fields therefore survive deletion, and a later Core record
        with the same IPID is reconnected by ``sync_from_record``.
        """
        key = str(animal_id or "").strip()
        # This compatibility adapter is a write path and must never be
        # callable as a free-standing Heritage mutation.  Production uses the
        # backend-owned atomic Core-delete operation; the guarded fallback is
        # passed an explicit authorization token by the Core command caller.
        if (
            not authorized
            or not self._action_authorized("core.delete_animals")
            or not key
            or not isinstance(record, dict)
        ):
            return False
        referenced = False
        for child_key, child_record in self._all_identity_records().items():
            if str(child_key).strip() == key or not isinstance(child_record, dict):
                continue
            values = self.get_parentage(child_key, child_record)
            if key in {str(value or "").strip() for value in values.values()}:
                referenced = True
                break
        if not referenced:
            return True
        parentage = self.get_parentage(key, record)
        metadata = {
            field: record.get(field, "")
            for field in (
                "name", "_base_name", "display_name", "genotype", "node_fill_color",
                "sex", "species", "birth_date",
            )
        }
        metadata.update({
            "heritage_only": True,
            "identity_review_required": False,
            "identity_review_reason": "",
        })
        owner_unit = ""
        for field in ("organization_unit_id", "organizational_unit_id", "workgroup_id"):
            candidate = str(record.get(field, "") or "").strip()
            if candidate:
                owner_unit = candidate
                break
        metadata["unit_id"] = owner_unit or self._current_unit_id()
        # The Core delete command has already performed its authorization.
        # Publish the read-only deletion snapshot directly to the Heritage
        # store; it must remain durable even though the Core row is removed.
        metadata.update({
            "heritage_only": True,
            "dummy_kind": "former_core",
            "persistence_kind": "former_core_dummy",
        })
        latest_snapshot, backend_revision = self.store.load_latest_with_revision()
        target_record = self._parentage_default_entry(
            key,
            name=metadata.get("name", animal_base_name(key, record)),
            species=metadata.get("species", ""),
            sex=self.store._normalize_sex(metadata.get("sex", "")),
            birth_date=self.store._normalize_text(metadata.get("birth_date", "")),
            heritage_only=True,
        )
        target_record.update(metadata)
        target_record.update(parentage)
        sequence = int(latest_snapshot.get("parentage_sequence", 0) or 0) + 1
        token, display_token = self._parentage_token(self._parentage_actor(), sequence)
        target_record["parentage_revision"] = token
        target_record["parentage_revision_display"] = display_token
        target_record["source"] = "former_core_dummy"
        target_record["updated_at"] = self.store._utc_now_iso()

        def mutate(data: Dict[str, Any]) -> bool:
            animals = data.setdefault("animals", {})
            if not isinstance(animals, dict):
                raise ValueError("Heritage data is invalid")
            animals[key] = deepcopy(target_record)
            data["parentage_sequence"] = sequence
            return True

        try:
            self.store.atomic_update(
                mutate,
                expected_revision=backend_revision,
            )
        except Exception:
            logging.getLogger(__name__).exception("Could not retain deleted Core animal in Heritage")
            return False
        self._engine_cache.invalidate()
        self.invalidate_render_dependencies({key})
        return True

    def set_manual_sex(self, animal_name: str, sex: Optional[str]) -> bool:
        """Change dummy sex through the same validated atomic command."""
        if self._is_core_animal(animal_name):
            return False
        key = str(animal_name or "").strip()
        if not key:
            return False
        try:
            return bool(self.set_dummy_parentage(
                actor=None,
                animal_id=key,
                expected_revision=self._active_backend_revision,
                values=None,
                target_metadata={"sex": sex},
            ))
        except ParentageCommandError:
            return False

    def get_manual_sex(self, animal_name: str) -> str:
        if self._is_core_animal(animal_name):
            return ""
        key = str(animal_name or "").strip()
        if key in self._temporary_dummies:
            return self.store._normalize_sex(self._temporary_dummies[key].get("sex", ""))
        return self.store.get_manual_sex(animal_name)

    def set_node_visual(
        self,
        animal_name: str,
        genotype: Optional[str],
        fill_color: Optional[str],
    ) -> bool:
        """Store Heritage visual metadata without mutating a Core record."""
        key = str(animal_name or "").strip()
        if not key:
            return False
        if self._is_core_animal(key):
            # Core genotype is immutable here.  A permitted colour change is
            # a global Heritage visual overlay keyed by the authoritative
            # genotype, never a per-animal shadow record.
            record = self._core_record(key) or {}
            core_genotype = self.store._normalize_text(record.get("genotype", ""))
            if genotype is not None and self.store._normalize_text(genotype) != core_genotype:
                # A caller must not smuggle a genotype mutation through the
                # appearance command.  The node editor intentionally passes
                # ``None`` for this argument when the Core record is real.
                return False
            if fill_color is not None and not self._action_authorized("heritage.edit_genotype_colors"):
                return False
            if fill_color is not None and core_genotype:
                self.store.set_genotype_color(
                    core_genotype,
                    fill_color,
                    update_entries=False,
                )
            self.invalidate_render_dependencies({key})
            return True
        if key in self._temporary_dummies:
            metadata: Dict[str, Any] = {}
            if genotype is not None:
                metadata["genotype"] = genotype
            if fill_color is not None:
                metadata["node_fill_color"] = fill_color
            try:
                return bool(self.set_dummy_parentage(
                    actor=None,
                    animal_id=key,
                    expected_revision=None,
                    values=None,
                    target_metadata=metadata,
                ))
            except ParentageCommandError:
                return False
        # This is a plugin-owned visual overlay.  It deliberately does not
        # touch ``app.animals`` or call the Core persistence service.
        entry = self._store_snapshot_entries().get(key, {})
        if not isinstance(entry, dict) or self._dummy_kind(entry) not in {"direct", "former_core"}:
            return False
        metadata: Dict[str, Any] = {}
        if genotype is not None:
            metadata["genotype"] = genotype
        if fill_color is not None:
            metadata["node_fill_color"] = fill_color
        try:
            return bool(self.set_dummy_parentage(
                actor=None,
                animal_id=key,
                expected_revision=self._active_backend_revision,
                values=None,
                target_metadata=metadata,
            ))
        except ParentageCommandError:
            return False

    def get_effective_sex(self, animal_name: Optional[str], fallback_record: Optional[Dict[str, Any]] = None) -> str:
        key = str(animal_name or "").strip()
        temporary = self._temporary_dummies.get(key)
        if isinstance(temporary, dict):
            sex = self.store._normalize_sex(temporary.get("sex", ""))
            if sex:
                return sex
        core_record = (
            fallback_record
            if isinstance(fallback_record, dict) and self._is_core_animal(key)
            else self._core_record(key)
        )
        return self.store.get_effective_sex(
            key,
            core_record,
            core_authoritative=self._is_core_animal(key),
            snapshot=self._active_store_snapshot,
        )

    def get_node_visual(
        self,
        animal_name: str,
        *,
        fallback_genotype: str = "",
        fallback_record: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """Resolve visuals against the active immutable render snapshot."""
        return self.store.get_node_visual(
            animal_name,
            fallback_genotype=fallback_genotype,
            fallback_record=fallback_record,
            core_authoritative=self._is_core_animal(animal_name),
            snapshot=self._active_store_snapshot,
        )

    def _all_identity_records(self) -> Dict[str, Dict[str, Any]]:
        records: Dict[str, Dict[str, Any]] = {}
        animals = (
            self._active_core_snapshot
            if isinstance(self._active_core_snapshot, dict)
            else (self.app.animals if isinstance(getattr(self.app, "animals", {}), dict) else {})
        )
        archived = {} if isinstance(self._active_core_snapshot, dict) else (getattr(self.app, "archived", {}) or {})
        records.update(animals)
        if isinstance(archived, dict):
            records.update(archived)
        core_keys = {str(key).strip() for key in records}
        core_ipids = {
            str(entry.get("ipid", "")).strip()
            for entry in records.values()
            if isinstance(entry, dict) and str(entry.get("ipid", "")).strip()
        }
        for key, entry in self._store_snapshot_entries().items():
            if isinstance(entry, dict):
                if str(key).strip() in core_keys or str(entry.get("ipid", "")).strip() in core_ipids:
                    continue
                records.setdefault(key, entry)
        for key, entry in self._temporary_dummies.items():
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
        if not self._heritage_view_authorized():
            return False
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
                    key = animal_identity_key(base_name, species_value, birth_date, origin)
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

        if (
            self._is_core_animal(key)
            or key in self._store_snapshot_entries()
            or key in self._temporary_dummies
        ):
            return False
        unit_id = self._current_unit_id()
        # A durable dummy must have an explicit canonical organizational Unit;
        # without one the safe fallback is the documented session-only kind.
        durable = bool(unit_id and self._durable_dummy_allowed(unit_id))
        persistence_kind = "direct_dummy" if durable else "temporary_dummy"
        target_record = self._parentage_default_entry(
            key, name=base_name, species=species_value,
            sex=self.store._normalize_sex(sex), birth_date=birth_date,
            heritage_only=True,
        )
        target_record.update({
            "genotype": self.store._normalize_text(genotype),
            "node_fill_color": self.store._normalize_text(fill_color),
            "identity_review_required": review_required,
            "identity_review_reason": review_reason,
            "unit_id": unit_id,
            "dummy_kind": "direct",
            "persistence_kind": persistence_kind,
        })
        try:
            return self.set_dummy_parentage(
                actor=None,
                animal_id=key,
                expected_revision=None,
                values={
                    "egg_donor": mother,
                    "sperm_donor": father,
                    "surrogate_mother": "",
                    "surrogate_father": "",
                },
                source="plugin",
                allow_custom=False,
                explicit_custom_parents=explicit_custom_parents,
                target_metadata=target_record,
                create=True,
            )
        except ParentageCommandError:
            return False

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
        if name in self._temporary_dummies or self._store_snapshot_entries().get(name):
            return True
        return False

    def build_engine(
        self,
        *,
        sync: bool = True,
        core_snapshot: Optional[Dict[str, Dict[str, Any]]] = None,
        store_snapshot: Optional[Dict[str, Any]] = None,
        backend_revision: Optional[int] = None,
    ) -> PedigreeEngine:
        """Build from one immutable Core + Heritage read snapshot.

        ``sync`` remains accepted for callers from older plugin hooks, but it
        no longer authorizes a Core-to-Heritage mirror write.  When no snapshot
        is supplied, both sides are captured here before cache lookup.
        """
        _ = sync
        if store_snapshot is None:
            store_snapshot, backend_revision = self.store.load_latest_with_revision()
        elif backend_revision is None:
            backend_revision = self.store.get_backend_revision()
        if not isinstance(store_snapshot, dict):
            store_snapshot = self.store._default_data()
        store_snapshot = deepcopy(store_snapshot)
        backend_revision = int(backend_revision or 0)
        self.store.adopt_read_snapshot(store_snapshot, backend_revision)

        if core_snapshot is None:
            core_snapshot = self._copy_core_records(self.app)
        else:
            core_snapshot = {
                str(key).strip(): deepcopy(value)
                for key, value in core_snapshot.items()
                if str(key).strip() and isinstance(value, dict)
            }

        # A backend revision change invalidates a local engine even when the
        # newly read payload happens to hash to the same effective graph.
        if (
            self._engine_backend_revision is not None
            and self._engine_backend_revision != backend_revision
        ):
            self._engine_cache.invalidate()
            self.clear_render_cache()
        self._engine_backend_revision = backend_revision
        self._active_core_snapshot = core_snapshot
        self._active_store_snapshot = store_snapshot
        self._active_backend_revision = backend_revision
        self._active_core_projection_revision = hashlib.sha256(
            json.dumps(core_snapshot, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        animals = core_snapshot

        # Build base-name → [(key, species)] map for resolving same-name animals by species.
        _base_to_variants: Dict[str, List[tuple]] = {}
        all_store_entries = store_snapshot.get("animals", {})
        if not isinstance(all_store_entries, dict):
            all_store_entries = {}
        core_keys = set(animals)
        core_ipids = {
            str(entry.get("ipid", "")).strip()
            for entry in animals.values()
            if isinstance(entry, dict) and str(entry.get("ipid", "")).strip()
        }
        # The store's animal collection is reserved for Heritage-owned dummies.
        # Filter any stale legacy Core shadow by key or stable IPID at the read
        # boundary; no compatibility writer is needed and no render mutates it.
        _heritage_entries = {
            key: entry
            for key, entry in all_store_entries.items()
            if str(key).strip() not in core_keys
            and isinstance(entry, dict)
            and str(entry.get("ipid", "")).strip() not in core_ipids
        }
        for key, entry in self._temporary_dummies.items():
            if key not in animals and key not in _heritage_entries:
                _heritage_entries[key] = entry
        _identity_records = dict(_heritage_entries)
        _identity_records.update(animals)
        for _k, _r in _identity_records.items():
            if not isinstance(_r, dict):
                continue
            _base = animal_base_name(_k, _r).strip()
            _sp = (_r.get("species") or "").strip()
            _birth = (_r.get("birth_date") or "").strip()
            _base_to_variants.setdefault(_base.lower(), []).append((_k, _sp, _birth))

        def _species_aware_lookup(animal_name: str, record) -> Dict[str, str]:
            key = str(animal_name or "").strip()
            if key in animals and isinstance(record, dict):
                base_parentage = {
                    "egg_donor": str(record.get("eizellspenderin", "") or "").strip(),
                    "sperm_donor": str(record.get("samenspender", "") or "").strip(),
                    "surrogate_mother": str(record.get("ziehmutter", "") or "").strip(),
                    "surrogate_father": str(record.get("ziehvater", "") or "").strip(),
                }
            elif key in self._temporary_dummies:
                base_parentage = self.store._normalize_parents(self._temporary_dummies[key])
            else:
                base_parentage = self.store.get_parentage(
                    key,
                    record if isinstance(record, dict) else None,
                    snapshot=store_snapshot,
                )
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
