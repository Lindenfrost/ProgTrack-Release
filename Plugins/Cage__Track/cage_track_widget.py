# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Cage Track main visualization widget.

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, QDate, QPointF, QTimer
from PyQt6.QtGui import QColor, QAction
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.text import Text
import matplotlib.colors as mcolors

from .cage_store import CageStore, UNASSIGNED_CAGE_ID
from .cage_engine import CageEngine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Visual constants
# ---------------------------------------------------------------------------

# Structural colours
BLD_BG = "#F5F7FA"
BLD_BORDER = "#90A4AE"
BLD_TITLE_BG = "#CFD8DC"
ROOM_BG = "#F7F9F7"
ROOM_BORDER = "#A5D6A7"
ROOM_TITLE_BG = "#C8E6C9"
CAGE_BG = "#FFFFFF"
CAGE_BORDER = "#BDBDBD"
CAGE_TITLE_BG = "#F5F5F5"
UNASSIGNED_BG = "#FFF8E1"
UNASSIGNED_BORDER = "#FFD54F"

SELECTED_BORDER = "#FF9800"
DROP_HIGHLIGHT = "#4CAF50"
ARCHIVED_COLOR = "#757575"

# Sizing (in data-coordinate units, 1 unit ≈ 1 px at zoom 1)
BLD_PAD = 10
ROOM_PAD = 8
CAGE_PAD = 5
CAGE_MIN_W = 90
CAGE_MIN_H = 50
OCCUPANT_LINE_H = 14
TITLE_H = 20
SPACING = 10

FONT_BLD = {"fontsize": 11, "fontweight": "bold"}
FONT_ROOM = {"fontsize": 9.5, "fontweight": "bold"}
FONT_CAGE = {"fontsize": 8.5, "fontweight": "bold"}
FONT_OCC = {"fontsize": 7.5}

DEFAULT_PROJECT_PALETTE = [
    "#1976D2", "#D32F2F", "#388E3C", "#7B1FA2", "#F57C00",
    "#0097A7", "#C2185B", "#455A64", "#FBC02D", "#512DA8",
]


# ======================================================================
# Movement History Dialog
# ======================================================================

class MovementHistoryDialog(QDialog):
    """Show movement history for a single occupant."""

    def __init__(self, parent: QWidget, messages: Dict[str, Any], occupant_id: str,
                 engine: CageEngine, store: CageStore):
        super().__init__(parent)
        self.messages = messages
        self.occupant_id = occupant_id
        self.setWindowTitle(
            messages.get("cage_track.history.title", "Movement History: {occupant}").replace("{occupant}", occupant_id)
        )
        self.setModal(True)
        self.resize(620, 400)

        layout = QVBoxLayout(self)

        occ = store.get_occupant(occupant_id)
        layout.addWidget(QLabel(f"<b>{occupant_id}</b>"))

        current_cage_id = occ.get("cage_id", UNASSIGNED_CAGE_ID) if occ else UNASSIGNED_CAGE_ID
        ua_label = messages.get("cage_track.unassigned", "Unassigned")
        current_path = engine.resolve_cage_path(current_cage_id, ua_label)
        layout.addWidget(QLabel(
            f"{messages.get('cage_track.history.current_cage', 'Current Cage')}: {current_path}"
        ))

        self._store = store
        self._table = None
        history = store.get_movement_history(occupant_id)
        if not history:
            layout.addWidget(QLabel(messages.get("cage_track.history.no_history", "No movement history available")))
        else:
            table = QTableWidget(len(history), 5)
            table.setHorizontalHeaderLabels([
                messages.get("cage_track.history.cage_path", "Cage"),
                messages.get("cage_track.history.moved_in", "Moved In"),
                messages.get("cage_track.history.moved_out", "Moved Out"),
                messages.get("cage_track.history.duration", "Duration"),
                messages.get("cage_track.history.cage_mates", "Cage Mates"),
            ])
            table.horizontalHeader().setStretchLastSection(True)
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)

            no_edit = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled

            for row, entry in enumerate(history):
                cage_path = engine.resolve_cage_path(entry.get("cage_id", ""), ua_label)
                item0 = QTableWidgetItem(cage_path)
                item0.setFlags(no_edit)
                table.setItem(row, 0, item0)

                mi = entry.get("moved_in", "")
                table.setItem(row, 1, QTableWidgetItem(mi[:10] if mi else ""))

                moved_out = entry.get("moved_out")
                mo_display = moved_out[:10] if moved_out else messages.get("cage_track.history.current", "Current")
                table.setItem(row, 2, QTableWidgetItem(mo_display))

                days = CageStore.calculate_duration(entry.get("moved_in"), moved_out)
                dur_text = messages.get("cage_track.history.days", "{count} days").replace(
                    "{count}", str(days)
                ) if days is not None else ""
                item3 = QTableWidgetItem(dur_text)
                item3.setFlags(no_edit)
                table.setItem(row, 3, item3)

                mates = ", ".join(entry.get("cage_mates_snapshot", []))
                item4 = QTableWidgetItem(mates)
                item4.setFlags(no_edit)
                table.setItem(row, 4, item4)

            table.cellChanged.connect(self._on_cell_changed)
            layout.addWidget(table)
            self._table = table

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_btn = QPushButton(messages.get("cage_track.history.export_pdf", "Export PDF"))
        export_btn.clicked.connect(self._export_pdf)
        export_btn.setEnabled(self._table is not None)
        btn_row.addWidget(export_btn)
        close_btn = QPushButton(messages.get("cage_track.history.close", "Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _export_pdf(self) -> None:
        if self._table is None:
            return
        now_str = datetime.now().strftime("%Y-%m-%d")
        default_name = f"Movement_History_{self.occupant_id}_{now_str}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.messages.get("cage_track.history.export_pdf", "Export PDF"),
            default_name,
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        try:
            from matplotlib.backends.backend_pdf import PdfPages as _PdfPages
            from matplotlib.figure import Figure as _Figure
            fig = _Figure(figsize=(11, max(4, self._table.rowCount() * 0.4 + 2)))
            ax = fig.add_subplot(111)
            ax.axis("off")
            ax.set_title(
                self.messages.get("cage_track.history.title", "Movement History: {occupant}").replace("{occupant}", self.occupant_id),
                fontsize=12, fontweight="bold", loc="left",
            )
            cols = self._table.columnCount()
            rows = self._table.rowCount()
            headers = [self._table.horizontalHeaderItem(c).text() for c in range(cols)]
            cell_text = []
            for r in range(rows):
                row_data = []
                for c in range(cols):
                    item = self._table.item(r, c)
                    row_data.append(item.text() if item else "")
                cell_text.append(row_data)
            tbl = ax.table(cellText=cell_text, colLabels=headers, loc="center", cellLoc="left")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1.0, 1.4)
            for (row_idx, col_idx), cell in tbl.get_celld().items():
                if row_idx == 0:
                    cell.set_facecolor("#CFD8DC")
                    cell.set_text_props(fontweight="bold")
            fig.tight_layout()
            with _PdfPages(path) as pdf:
                pdf.savefig(fig)
            QMessageBox.information(
                self,
                self.messages.get("cage_track.history.export_pdf", "Export PDF"),
                self.messages.get("cage_track.history.export_success", "Movement history exported successfully"),
            )
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _on_cell_changed(self, row: int, col: int) -> None:
        """Handle edits to the moved_in / moved_out date cells."""
        if col not in (1, 2):
            return
        item = self._table.item(row, col)
        if item is None:
            return
        text = item.text().strip()
        field = "moved_in" if col == 1 else "moved_out"
        current_label = self.messages.get("cage_track.history.current", "Current")

        # Allow clearing moved_out back to "Current"
        if col == 2 and text.lower() in (current_label.lower(), ""):
            self._store.update_history_date(self.occupant_id, row, field, None)
            self._table.blockSignals(True)
            item.setText(current_label)
            self._table.blockSignals(False)
            self._refresh_duration(row)
            return

        # Validate YYYY-MM-DD
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            history = self._store.get_movement_history(self.occupant_id)
            if row < len(history):
                old_val = history[row].get(field)
                display = old_val[:10] if old_val else current_label
            else:
                display = ""
            self._table.blockSignals(True)
            item.setText(display)
            self._table.blockSignals(False)
            return

        self._store.update_history_date(self.occupant_id, row, field, text)
        self._refresh_duration(row)

    def _refresh_duration(self, row: int) -> None:
        """Recalculate and display the duration for a history row."""
        history = self._store.get_movement_history(self.occupant_id)
        if row >= len(history):
            return
        entry = history[row]
        days = CageStore.calculate_duration(entry.get("moved_in"), entry.get("moved_out"))
        dur_text = self.messages.get("cage_track.history.days", "{count} days").replace(
            "{count}", str(days)
        ) if days is not None else ""
        dur_item = self._table.item(row, 3)
        if dur_item:
            self._table.blockSignals(True)
            dur_item.setText(dur_text)
            self._table.blockSignals(False)


# ======================================================================
# Project Color Editor Dialog
# ======================================================================

class ProjectColorDialog(QDialog):
    """Edit project → colour mapping."""

    def __init__(self, parent: QWidget, messages: Dict[str, Any], store: CageStore,
                 project_names: List[str]):
        super().__init__(parent)
        self.messages = messages
        self.store = store
        self.setWindowTitle(messages.get("cage_track.colors.title", "Edit Project Colors"))
        self.setModal(True)
        self.resize(400, 300)

        self._colors: Dict[str, str] = dict(store.get_all_project_colors())
        self._project_names = sorted(set(project_names) | set(self._colors.keys()))

        layout = QVBoxLayout(self)

        self.table = QTableWidget(len(self._project_names), 2)
        self.table.setHorizontalHeaderLabels([
            messages.get("cage_track.colors.project", "Project"),
            messages.get("cage_track.colors.color", "Color"),
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, pname in enumerate(self._project_names):
            self.table.setItem(row, 0, QTableWidgetItem(pname))
            color = self._colors.get(pname, "#CCCCCC")
            item = QTableWidgetItem(color)
            item.setBackground(QColor(color))
            self.table.setItem(row, 1, item)

        self.table.cellDoubleClicked.connect(self._pick_color)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        apply_btn = QPushButton(messages.get("cage_track.colors.apply", "Apply"))
        apply_btn.clicked.connect(self._apply)
        cancel_btn = QPushButton(messages.get("cage_track.colors.reset", "Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

    def _pick_color(self, row: int, col: int) -> None:
        if col != 1:
            return
        pname = self._project_names[row]
        current = QColor(self._colors.get(pname, "#CCCCCC"))
        chosen = QColorDialog.getColor(current, self, self.messages.get("cage_track.colors.color", "Color"))
        if chosen.isValid():
            hex_color = chosen.name()
            self._colors[pname] = hex_color
            item = self.table.item(row, 1)
            item.setText(hex_color)
            item.setBackground(chosen)

    def _apply(self) -> None:
        for pname, color in self._colors.items():
            self.store.set_project_color(pname, color)
        self.accept()


# ======================================================================
# Add Structure Dialog
# ======================================================================

class AddStructureDialog(QDialog):
    """Create building / room / cage."""

    def __init__(self, parent: QWidget, messages: Dict[str, Any], engine: CageEngine, store: CageStore):
        super().__init__(parent)
        self.messages = messages
        self.engine = engine
        self.store = store
        self.setWindowTitle(messages.get("cage_track.add.title", "Add New Structure"))
        self.setModal(True)
        self.resize(350, 250)

        layout = QFormLayout(self)

        self.type_combo = QComboBox()
        self.type_combo.setEditable(False)
        self.type_combo.addItem(messages.get("cage_track.add.type.building", "Building"), "building")
        self.type_combo.addItem(messages.get("cage_track.add.type.room", "Room"), "room")
        self.type_combo.addItem(messages.get("cage_track.add.type.cage", "Cage"), "cage")
        layout.addRow(messages.get("cage_track.add.type", "Type:"), self.type_combo)

        self.name_edit = QLineEdit()
        layout.addRow(messages.get("cage_track.add.name", "Name:"), self.name_edit)

        self.building_combo = QComboBox()
        self.building_combo.setEditable(False)
        self.building_label = QLabel(messages.get("cage_track.add.parent_building", "Parent Building:"))
        layout.addRow(self.building_label, self.building_combo)

        self.room_combo = QComboBox()
        self.room_combo.setEditable(False)
        self.room_label = QLabel(messages.get("cage_track.add.parent_room", "Parent Room:"))
        layout.addRow(self.room_label, self.room_combo)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

        self.type_combo.currentIndexChanged.connect(self._update_visibility)
        self.building_combo.currentIndexChanged.connect(self._populate_rooms)

        self._populate_buildings()
        self._update_visibility()

    def _populate_buildings(self) -> None:
        self.building_combo.clear()
        self.building_combo.addItem("—", None)
        for b in self.engine.get_all_buildings():
            self.building_combo.addItem(b.get("display_name", b["id"]), b["id"])

    def _populate_rooms(self) -> None:
        self.room_combo.clear()
        self.room_combo.addItem("\u2014", None)
        bid = self.building_combo.currentData()
        if bid:
            for r in self.engine.get_rooms_in_building(bid):
                self.room_combo.addItem(r.get("display_name", r["id"]), r["id"])

    def _update_visibility(self) -> None:
        t = self.type_combo.currentData()
        self.building_label.setVisible(t in ("room", "cage"))
        self.building_combo.setVisible(t in ("room", "cage"))
        self.room_label.setVisible(t == "cage")
        self.room_combo.setVisible(t == "cage")

    def _on_accept(self) -> None:
        t = self.type_combo.currentData()
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, self.messages.get("error.title", "Error"),
                                self.messages.get("cage_track.add.name", "Name is required"))
            return

        if t == "building":
            self.store.create_building(name)
        elif t == "room":
            bid = self.building_combo.currentData()
            if not bid:
                QMessageBox.warning(self, self.messages.get("error.title", "Error"),
                                    self.messages.get("cage_track.error.no_building", "Please select a building"))
                return
            self.store.create_room(bid, name)
        elif t == "cage":
            rid = self.room_combo.currentData()
            if not rid:
                QMessageBox.warning(self, self.messages.get("error.title", "Error"),
                                    self.messages.get("cage_track.error.no_room", "Please select a room"))
                return
            self.store.create_cage(rid, name)
        self.accept()


# ======================================================================
# Settings Dialog
# ======================================================================

class CageSettingsDialog(QDialog):
    """Plugin-level settings for Cage_Track – also hosts PDF export and color editor."""

    def __init__(self, parent: "CageTrackWidget", messages: Dict[str, Any], store: CageStore):
        super().__init__(parent)
        self.messages = messages
        self.store = store
        self._parent_widget = parent
        self.setWindowTitle(messages.get("cage_track.toolbar.settings", "Settings"))
        self.setModal(True)
        self.resize(320, 200)

        layout = QVBoxLayout(self)

        # --- Action buttons (vertical) ---
        self.colors_btn = QPushButton(messages.get("cage_track.toolbar.edit_colors", "Edit Colors"))
        self.colors_btn.clicked.connect(self._on_edit_colors)
        layout.addWidget(self.colors_btn)

        self.pdf_btn = QPushButton(messages.get("cage_track.toolbar.export_pdf", "Export PDF"))
        self.pdf_btn.clicked.connect(self._on_export_pdf)
        self.pdf_btn.setEnabled(parent._can('cage.export_pdf'))
        layout.addWidget(self.pdf_btn)

        # --- Checkbox below ---
        ui_state = store.get_ui_state()
        self.show_legend_cb = QCheckBox(messages.get("cage_track.settings.show_legend", "Show legend"))
        self.show_legend_cb.setChecked(ui_state.get("show_legend", True))
        layout.addWidget(self.show_legend_cb)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self) -> None:
        self.store.set_ui_state({"show_legend": self.show_legend_cb.isChecked()})
        self.accept()

    def _on_edit_colors(self) -> None:
        pw = self._parent_widget
        animals = pw._get_animals_dict()
        projects = set()
        for rec in animals.values():
            p = rec.get("project", "") if isinstance(rec, dict) else ""
            if p:
                projects.add(p)
        dlg = ProjectColorDialog(self, self.messages, self.store, list(projects))
        dlg.exec()

    def _on_export_pdf(self) -> None:
        pw = self._parent_widget
        now_str = datetime.now().strftime("%Y-%m-%d")
        default_name = self.messages.get(
            "cage_track.export.filename", "Cage Layout {date}"
        ).replace("{date}", now_str) + ".pdf"

        path, _ = QFileDialog.getSaveFileName(
            self,
            self.messages.get("cage_track.export.title", "Export Cage Layout"),
            default_name,
            "PDF Files (*.pdf)",
        )
        if not path:
            return

        try:
            with PdfPages(path) as pdf:
                pdf.savefig(pw.figure)
            QMessageBox.information(
                self,
                self.messages.get("cage_track.export.title", "Export Cage Layout"),
                self.messages.get("cage_track.export.success", "Cage layout exported successfully"),
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("cage_track.export.error", "Failed to export: {error}").replace("{error}", str(e)),
            )


# ======================================================================
# Inspection Dialog
# ======================================================================

class InspectionDialog(QDialog):
    """Non-editable viewer for cage inspection records."""

    def __init__(self, parent, messages: Dict[str, Any],
                 records: List[Dict[str, Any]], on_export):
        super().__init__(parent)
        self.messages = messages
        self.setWindowTitle(messages.get(
            "cage_track.inspection.title", "Inspection Log"))
        self.setModal(True)
        self.resize(720, 460)

        layout = QVBoxLayout(self)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels([
            messages.get("cage_track.inspection.col_unit", "Unit"),
            messages.get("cage_track.inspection.col_date", "Date"),
            messages.get("cage_track.inspection.col_cages", "Cages"),
            messages.get("cage_track.inspection.col_inspector", "Inspector"),
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        sorted_recs = sorted(
            records, key=lambda r: r.get("date_sort", ""), reverse=True)
        self._table.setRowCount(len(sorted_recs))
        for row, rec in enumerate(sorted_recs):
            self._table.setItem(
                row, 0, QTableWidgetItem(rec.get("unit_name", "")))
            self._table.setItem(
                row, 1, QTableWidgetItem(rec.get("date", "")))
            self._table.setItem(
                row, 2, QTableWidgetItem(rec.get("cages", "")))
            self._table.setItem(
                row, 3, QTableWidgetItem(rec.get("user", "")))
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_btn = QPushButton(messages.get(
            "cage_track.inspection.export_pdf", "Export PDF"))
        export_btn.clicked.connect(lambda: on_export())
        btn_row.addWidget(export_btn)
        close_btn = QPushButton(messages.get(
            "cage_track.inspection.close", "Close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


class InspectionPDFExportDialog(QDialog):
    """Export inspection records to PDF with unit and date-range filters.

    Layout follows the ProgTrack 'Export PDF Reports' dialog style.
    """

    def __init__(self, parent, messages: Dict[str, Any],
                 records: List[Dict[str, Any]]):
        super().__init__(parent)
        self.messages = messages
        self._records = records
        self.setWindowTitle(messages.get(
            "cage_track.inspection.export_title",
            "Export Inspection Report"))
        self.setModal(True)
        self.resize(460, 420)

        layout = QVBoxLayout(self)

        # --- Unit selection ---
        layout.addWidget(QLabel(
            f"<b>{messages.get('cage_track.inspection.select_units', 'Select Units:')}</b>"))

        select_all_cb = QCheckBox(
            messages.get("cage_track.inspection.select_all", "Select All"))
        layout.addWidget(select_all_cb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)

        units: Dict[str, str] = {}
        for rec in records:
            uid = rec.get("unit_id", "")
            uname = rec.get("unit_name", "")
            if uid and uid not in units:
                units[uid] = uname

        self._unit_cbs: Dict[str, QCheckBox] = {}
        for uid, uname in sorted(units.items(), key=lambda x: x[1]):
            cb = QCheckBox(uname)
            cb.setChecked(True)
            self._unit_cbs[uid] = cb
            scroll_layout.addWidget(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        def toggle_all(checked):
            for cb in self._unit_cbs.values():
                cb.setChecked(checked)
        select_all_cb.toggled.connect(toggle_all)

        # --- Date range ---
        form = QFormLayout()
        self._from_date = QDateEdit()
        self._from_date.setCalendarPopup(True)
        self._from_date.setDate(QDate.currentDate().addMonths(-1))
        self._to_date = QDateEdit()
        self._to_date.setCalendarPopup(True)
        self._to_date.setDate(QDate.currentDate())
        form.addRow(
            messages.get("cage_track.inspection.from", "From:"),
            self._from_date)
        form.addRow(
            messages.get("cage_track.inspection.to", "To:"),
            self._to_date)
        layout.addLayout(form)

        # --- Export button ---
        export_btn = QPushButton(messages.get(
            "cage_track.inspection.export_pdf", "Export PDF"))
        export_btn.clicked.connect(self._do_export)
        layout.addWidget(export_btn)

    # ------------------------------------------------------------------ #

    def _do_export(self) -> None:
        selected_units = {
            uid for uid, cb in self._unit_cbs.items() if cb.isChecked()}
        from_date = self._from_date.date().toPyDate()
        to_date = self._to_date.date().toPyDate()

        filtered: List[Dict[str, Any]] = []
        for rec in self._records:
            if rec.get("unit_id") not in selected_units:
                continue
            try:
                rec_date = datetime.strptime(
                    rec.get("date", ""), "%d/%m/%Y").date()
                if from_date <= rec_date <= to_date:
                    filtered.append(rec)
            except ValueError:
                continue

        if not filtered:
            QMessageBox.information(
                self,
                self.messages.get("cage_track.inspection.export_title",
                                  "Export Inspection Report"),
                self.messages.get("cage_track.inspection.no_records",
                                  "No records match the selected filters."))
            return

        filtered.sort(
            key=lambda r: (r.get("unit_name", ""), r.get("date_sort", "")))

        now_str = datetime.now().strftime("%Y-%m-%d")
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        default_path = os.path.join(desktop, f"Inspection_Report_{now_str}.pdf")
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.messages.get(
                "cage_track.inspection.export_pdf", "Export PDF"),
            default_path,
            "PDF Files (*.pdf)")
        if not path:
            return

        try:
            fig = Figure(figsize=(11, max(4, len(filtered) * 0.4 + 2)))
            ax = fig.add_subplot(111)
            ax.axis("off")
            ax.set_title(
                self.messages.get("cage_track.inspection.report_title",
                                  "Cage Inspection Report"),
                fontsize=14, fontweight="bold", loc="left")

            headers = [
                self.messages.get("cage_track.inspection.col_unit", "Unit"),
                self.messages.get("cage_track.inspection.col_date", "Date"),
                self.messages.get("cage_track.inspection.col_cages", "Cages"),
                self.messages.get(
                    "cage_track.inspection.col_inspector", "Inspector"),
            ]
            cell_text = [
                [rec.get("unit_name", ""), rec.get("date", ""),
                 rec.get("cages", ""), rec.get("user", "")]
                for rec in filtered
            ]

            tbl = ax.table(cellText=cell_text, colLabels=headers,
                           loc="center", cellLoc="left")
            tbl.auto_set_font_size(False)
            tbl.set_fontsize(8)
            tbl.scale(1.0, 1.4)
            col_widths = [0.15, 0.12, 0.53, 0.20]
            for ci, w in enumerate(col_widths):
                for ri in range(len(filtered) + 1):
                    tbl[ri, ci].set_width(w)
            for (ri, ci), cell in tbl.get_celld().items():
                if ri == 0:
                    cell.set_facecolor("#CFD8DC")
                    cell.set_text_props(fontweight="bold")

            fig.tight_layout()
            with PdfPages(path) as pdf:
                pdf.savefig(fig)

            QMessageBox.information(
                self,
                self.messages.get(
                    "cage_track.inspection.export_pdf", "Export PDF"),
                self.messages.get(
                    "cage_track.inspection.export_success",
                    "Inspection report exported successfully."))
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))


# ======================================================================
# Main Widget
# ======================================================================

class CageTrackWidget(QWidget):
    """Main Cage_Track tab widget with matplotlib canvas."""

    def __init__(self, plugin: "CageTrackPlugin"):
        super().__init__()
        self.plugin = plugin
        self.store = plugin.store
        self.engine = CageEngine(self.store)
        self.messages = plugin.messages

        self.selected_cage_id: Optional[str] = None
        self._highlight_occupant: Optional[str] = None
        self._hit_map: List[Tuple[Tuple[float, float, float, float], str, str]] = []
        # (x0, y0, x1, y1), entity_id, entity_type

        self._project_color_cache: Dict[str, str] = {}
        self._active_project_cache: Set[str] = set()
        self._role_color_cache: Dict[str, str] = {}
        self._animal_project_color_cache: Dict[str, str] = {}
        self._project_rgba_cache: Dict[str, List[float]] = {}
        self._hierarchy_cache: Optional[List[Dict[str, Any]]] = None
        self._unassigned_cache: Optional[List[Dict[str, Any]]] = None

        # Minimum pixel widths for cages (cage_id -> min pixel width)
        self._cage_min_pixel_widths: Dict[str, float] = {}
        # Actual data widths of cages (cage_id -> data coordinate width)
        self._cage_data_widths: Dict[str, float] = {}

        # Legend drag state
        self._legend_dragging = False
        self._legend_drag_offset: Optional[Tuple[float, float]] = None

        self._build_ui()

    # ------------------------------------------------------------------
    # Permission helpers (delegate to Master_Track via main app)
    # ------------------------------------------------------------------

    def _can(self, action: str) -> bool:
        app = getattr(self.plugin, 'app', None)
        if app is None:
            return True
        fn = getattr(app, '_master_can', None)
        return fn(action) if fn else True

    def _deny(self) -> None:
        app = getattr(self.plugin, 'app', None)
        if app and hasattr(app, '_show_permission_denied'):
            app._show_permission_denied()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        # Toolbar
        toolbar = QHBoxLayout()
        self.add_btn = QPushButton(self.messages.get("cage_track.toolbar.add", "Add"))
        self.add_btn.setToolTip(self.messages.get("cage_track.toolbar.add", "Add"))
        self.add_btn.clicked.connect(self._on_add)

        self.refresh_assignments_btn = QPushButton("🔄")
        self.refresh_assignments_btn.setToolTip(
            self.messages.get(
                "cage_track.toolbar.refresh_assignments",
                "Refresh cage assignments"))
        self.refresh_assignments_btn.setFixedSize(28, 28)
        self.refresh_assignments_btn.clicked.connect(self._on_refresh_assignments)

        self.inspection_btn = QPushButton(
            self.messages.get("cage_track.toolbar.inspection", "Inspection"))
        self.inspection_btn.setToolTip(
            self.messages.get("cage_track.toolbar.inspection", "Inspection"))
        self.inspection_btn.clicked.connect(self._on_inspection)

        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setToolTip(self.messages.get("cage_track.toolbar.settings", "Settings"))
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.clicked.connect(self._on_settings)

        toolbar.addStretch()
        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.refresh_assignments_btn)
        toolbar.addWidget(self.inspection_btn)
        toolbar.addWidget(self.settings_btn)
        root.addLayout(toolbar)

        # Matplotlib canvas
        self.figure = Figure(figsize=(10, 6))
        self.figure.patch.set_facecolor("#FAFAFA")
        self.ax = self.figure.add_subplot(111)
        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        root.addWidget(self.canvas, stretch=1)

        # Connect canvas events
        self.canvas.mpl_connect("button_press_event", self._on_canvas_press)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_canvas_motion)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("resize_event", self._on_resize)

        # Pan state
        self._pan_active = False
        self._pan_start: Optional[Tuple[float, float]] = None
        self._pan_xlim: Optional[Tuple[float, float]] = None
        self._pan_ylim: Optional[Tuple[float, float]] = None

        # Stored view limits for resize
        self._current_xlim: Optional[Tuple[float, float]] = None
        self._current_ylim: Optional[Tuple[float, float]] = None

        # Legend placeholder
        self.legend_widget: Optional[QWidget] = None

    # ------------------------------------------------------------------
    # Language update
    # ------------------------------------------------------------------

    def update_language(self, messages: Dict[str, Any]) -> None:
        self.messages = messages
        self.add_btn.setText(messages.get("cage_track.toolbar.add", "Add"))
        self.add_btn.setToolTip(messages.get("cage_track.toolbar.add", "Add"))
        self.refresh_assignments_btn.setToolTip(
            messages.get(
                "cage_track.toolbar.refresh_assignments",
                "Refresh cage assignments"))
        self.inspection_btn.setText(
            messages.get("cage_track.toolbar.inspection", "Inspection"))
        self.inspection_btn.setToolTip(
            messages.get("cage_track.toolbar.inspection", "Inspection"))
        self.settings_btn.setToolTip(messages.get("cage_track.toolbar.settings", "Settings"))
        self.refresh_view(sync_animals=False)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def refresh_view(self, sync_animals: bool = True) -> None:
        """Redraw the cage hierarchy.

        Keep animal synchronization for full refreshes, but skip it for
        UI-only redraws such as expand/collapse or selection highlighting.
        """
        # Auto-sync ProgTrack animals so unassigned ones appear
        animals_dict = self._get_animals_dict()
        archived_dict = self._get_archived_dict()
        if sync_animals and animals_dict:
            self.store.sync_from_progtrack(animals_dict, archived_dict)
            if hasattr(self.plugin, "mark_cage_assignments_clean"):
                self.plugin.mark_cage_assignments_clean()

        self.ax.clear()
        self.ax.set_aspect("auto")
        self.ax.axis("off")
        self._hit_map.clear()

        if sync_animals or self._hierarchy_cache is None or self._unassigned_cache is None:
            hierarchy = self.engine.build_hierarchy()
            self._hierarchy_cache = hierarchy
            self._unassigned_cache = self.store.get_unassigned_occupants()
        else:
            hierarchy = self._hierarchy_cache
        ui_state = self.store.get_ui_state()
        expanded_buildings = set(ui_state.get("expanded_buildings", []))
        expanded_rooms = set(ui_state.get("expanded_rooms", []))

        if sync_animals or not self._project_color_cache:
            project_colors = self._build_project_color_map(animals_dict)
            self._project_color_cache = dict(project_colors)
            self._active_project_cache = self._get_active_projects(animals_dict)
        else:
            project_colors = dict(self._project_color_cache)

        self._role_color_cache.clear()
        self._animal_project_color_cache.clear()
        self._project_rgba_cache.clear()

        x_cursor = SPACING
        max_y = 0

        # Draw unassigned section at top if occupants exist
        unassigned = [
            occ for occ in (self._unassigned_cache or [])
            if not (
                (animals_dict.get(occ.get('occupant_id', ''), {}) or {}).get('death_date') or
                (archived_dict.get(occ.get('occupant_id', ''), {}) or {}).get('death_date')
            )
        ]
        unassigned_height = 0
        show_unassigned = ui_state.get("show_unassigned", True)
        if unassigned:
            unassigned_height = self._draw_unassigned(
                unassigned, x_cursor, 0, animals_dict, project_colors, show_unassigned)
            max_y = unassigned_height + SPACING

        y_start = max_y

        # Draw buildings
        for bld in hierarchy:
            bld_id = bld["id"]
            is_expanded = bld_id in expanded_buildings

            if is_expanded:
                bld_w, bld_h = self._measure_building(bld, expanded_rooms, animals_dict)
            else:
                bld_w = 160
                bld_h = TITLE_H + 6

            self._draw_building(bld, x_cursor, y_start, bld_w, bld_h,
                                is_expanded, expanded_rooms, animals_dict, project_colors)
            x_cursor += bld_w + SPACING
            max_y = max(max_y, y_start + bld_h)

        # Fit view
        total_w = max(x_cursor + SPACING, 400)
        total_h = max(max_y + SPACING, 300)
        self.ax.set_xlim(-SPACING, total_w)
        self.ax.set_ylim(total_h, -SPACING)  # y inverted so top-to-bottom
        self._current_xlim = self.ax.get_xlim()
        self._current_ylim = self.ax.get_ylim()

        legend_project_colors = {
            project: project_colors[project]
            for project in sorted(self._active_project_cache, key=str.lower)
            if project in project_colors
        }

        # Legend – only show projects that currently have animals.
        if ui_state.get("show_legend", True) and legend_project_colors:
            legend_pos = ui_state.get("legend_pos", None)
            self._draw_legend(legend_project_colors, total_w, total_h, legend_pos)

        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.canvas.draw_idle()

    def _get_animals_dict(self) -> Dict[str, Any]:
        app = getattr(self.plugin, "app", None)
        if app and hasattr(app, "animals") and isinstance(app.animals, dict):
            return app.animals
        return {}

    def _get_archived_dict(self) -> Dict[str, Any]:
        app = getattr(self.plugin, "app", None)
        if app and hasattr(app, "archived") and isinstance(app.archived, dict):
            return app.archived
        return {}

    @staticmethod
    def _get_active_projects(animals_dict: Dict[str, Any]) -> Set[str]:
        projects: Set[str] = set()
        for rec in animals_dict.values():
            if not isinstance(rec, dict):
                continue
            project = str(rec.get("project", "")).strip()
            if project:
                projects.add(project)
        return projects

    def _build_project_color_map(self, animals_dict: Dict[str, Any]) -> Dict[str, str]:
        """Build complete project -> colour map, assigning defaults for unassigned projects."""
        stored = self.store.get_all_project_colors()
        projects: set = set()
        for rec in animals_dict.values():
            if isinstance(rec, dict):
                p = rec.get("project", "")
                if p:
                    projects.add(p)
        projects |= set(stored.keys())

        result: Dict[str, str] = {}
        palette_idx = 0
        for p in sorted(projects):
            if p in stored and stored[p]:
                result[p] = stored[p]
            else:
                result[p] = DEFAULT_PROJECT_PALETTE[palette_idx % len(DEFAULT_PROJECT_PALETTE)]
                palette_idx += 1
        return result

    def _get_animal_project_color(self, animal_name: str, animals_dict: Dict[str, Any],
                                   project_colors: Dict[str, str]) -> str:
        """Get project colour for an animal (used for cage backgrounds / legend)."""
        rec = animals_dict.get(animal_name, {})
        if not isinstance(rec, dict):
            return "#000000"
        project = rec.get("project", "")
        if project and project in project_colors:
            return project_colors[project]
        return "#000000"

    def _cached_animal_project_color(self, animal_name: str, animals_dict: Dict[str, Any],
                                     project_colors: Dict[str, str]) -> str:
        if animal_name not in self._animal_project_color_cache:
            self._animal_project_color_cache[animal_name] = self._get_animal_project_color(
                animal_name, animals_dict, project_colors)
        return self._animal_project_color_cache[animal_name]

    def _cached_project_rgba(self, project_color: str) -> List[float]:
        if project_color not in self._project_rgba_cache:
            try:
                rgba = list(mcolors.to_rgba(project_color))
                rgba[3] = 0.25
            except Exception:
                rgba = [0.8, 0.8, 0.8, 0.25]
            self._project_rgba_cache[project_color] = rgba
        return self._project_rgba_cache[project_color]

    def _cached_animal_role_color(self, animal_name: str, animals_dict: Dict[str, Any]) -> str:
        if animal_name not in self._role_color_cache:
            self._role_color_cache[animal_name] = self._get_animal_role_color(animal_name, animals_dict)
        return self._role_color_cache[animal_name]

    @staticmethod
    def _get_animal_role_color(animal_name: str, animals_dict: Dict[str, Any]) -> str:
        """Get role colour for an animal matching the ProgTrack list colours."""
        rec = animals_dict.get(animal_name, {})
        if not isinstance(rec, dict):
            return "#000000"
        role = rec.get("rolle", "")
        sex = (rec.get("sex", "") or "").lower()

        is_female = ("female" in sex or "weiblich" in sex or "жен" in sex)
        is_male = ("male" in sex or "männlich" in sex or "муж" in sex)

        if not role or role == "Unbekannt":
            return "#D3D3D3"  # lightgray
        if role == "Spenderin":
            return "#FF1493"  # deeppink
        if role == "Amme":
            return "#9370DB"  # mediumpurple
        if role == "Samenspender":
            return "#000000"  # black
        if role == "Nachkomme":
            if is_female:
                return "#FF69B4"  # hotpink
            if is_male:
                return "#0000FF"  # blue
            return "#808080"  # gray
        if role == "Partnertier":
            return "#FF8C00"  # darkorange
        if role == "Zuchttier":
            if is_female:
                return "#C71585"  # mediumvioletred
            if is_male:
                return "#00008B"  # darkblue
            return "#808080"  # gray
        if role == "Versuchstier":
            if is_female:
                return "#FF7788"
            if is_male:
                return "#00CCAA"
            return "#00AAAA"
        return "#000000"

    # ------------------------------------------------------------------
    # Measurement helpers
    # ------------------------------------------------------------------

    def _measure_cage(self, cage: Dict[str, Any]) -> Tuple[float, float]:
        occupants = cage.get("occupants", [])
        h = TITLE_H + max(len(occupants), 1) * OCCUPANT_LINE_H + CAGE_PAD * 2
        h = max(h, CAGE_MIN_H)

        # Calculate width based on longest occupant name
        # Text: 7.5pt font ~ 7px per char for average characters, add safety buffer
        max_text_width = 0
        for occ in occupants:
            occ_id = occ.get("occupant_id", "")
            text_width = len(occ_id) * 7.5  # ~7.5px per char at 7.5pt for mixed chars
            max_text_width = max(max_text_width, text_width)

        # Minimum width: circle area (18px) + text + padding on both sides + safety buffer
        min_pixel_width = CAGE_PAD + 18 + max_text_width + CAGE_PAD + 10  # +10px safety buffer
        w = max(CAGE_MIN_W, min_pixel_width)

        # Store minimum pixel width and data width for resize enforcement
        cage_id = cage.get("id", "")
        if cage_id:
            self._cage_min_pixel_widths[cage_id] = min_pixel_width
            self._cage_data_widths[cage_id] = w

        return w, h

    def _measure_room(self, room: Dict[str, Any], animals_dict: Dict[str, Any]) -> Tuple[float, float]:
        cages = room.get("cages", [])
        if not cages:
            return 140, TITLE_H + 30

        max_per_row = room.get("max_per_row", 4)
        rows_of_cages: List[List[Dict[str, Any]]] = []
        for i in range(0, len(cages), max_per_row):
            rows_of_cages.append(cages[i:i + max_per_row])

        max_row_w = 0
        total_h = TITLE_H + ROOM_PAD
        for row_cages in rows_of_cages:
            row_w = ROOM_PAD
            max_cage_h = 0
            for cage in row_cages:
                cw, ch = self._measure_cage(cage)
                row_w += cw + SPACING
                max_cage_h = max(max_cage_h, ch)
            row_w = row_w - SPACING + ROOM_PAD
            max_row_w = max(max_row_w, row_w)
            total_h += max_cage_h + SPACING

        total_w = max(max_row_w, 140)
        total_h = max(total_h - SPACING + ROOM_PAD, TITLE_H + 30)
        return total_w, total_h

    def _measure_building(self, bld: Dict[str, Any], expanded_rooms: Set[str],
                          animals_dict: Dict[str, Any]) -> Tuple[float, float]:
        rooms = bld.get("rooms", [])
        if not rooms:
            return 180, TITLE_H + 30

        max_per_row = bld.get("max_per_row", 4)
        rows_of_rooms: List[List[Dict[str, Any]]] = []
        for i in range(0, len(rooms), max_per_row):
            rows_of_rooms.append(rooms[i:i + max_per_row])

        max_row_w = 0
        total_h = TITLE_H + BLD_PAD
        for row_rooms in rows_of_rooms:
            row_w = BLD_PAD
            max_room_h = 0
            for room in row_rooms:
                if room["id"] in expanded_rooms:
                    rw, rh = self._measure_room(room, animals_dict)
                else:
                    rw, rh = 140, TITLE_H + 6
                row_w += rw + SPACING
                max_room_h = max(max_room_h, rh)
            row_w = row_w - SPACING + BLD_PAD
            max_row_w = max(max_row_w, row_w)
            total_h += max_room_h + SPACING

        total_w = max(max_row_w, 180)
        total_h = max(total_h - SPACING + BLD_PAD, TITLE_H + 30)
        return total_w, total_h

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_rect(self, x: float, y: float, w: float, h: float,
                   facecolor: str, edgecolor: str, linewidth: float = 1.0,
                   zorder: int = 1) -> None:
        rect = FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=2",
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            zorder=zorder,
        )
        self.ax.add_patch(rect)

    def _draw_flat_rect(self, x: float, y: float, w: float, h: float,
                        facecolor: str, edgecolor: str, linewidth: float = 1.0,
                        zorder: int = 1) -> None:
        rect = Rectangle(
            (x, y), w, h,
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            zorder=zorder,
        )
        self.ax.add_patch(rect)

    def _draw_unassigned(self, occupants: List[Dict[str, Any]], x: float, y: float,
                         animals_dict: Dict[str, Any],
                         project_colors: Dict[str, str],
                         expanded: bool = True) -> float:
        """Draw the unassigned section at top. Returns total height."""
        n = len(occupants)
        ua_label = self.messages.get("cage_track.unassigned", "Unassigned")

        if not expanded:
            # Collapsed – just title bar
            w = 300
            h = TITLE_H + 6
            self._draw_flat_rect(x, y, w, h, UNASSIGNED_BG, UNASSIGNED_BORDER, linewidth=1.5, zorder=2)
            arrow = "▶"
            self.ax.text(x + 6, y + TITLE_H * 0.6, f"{arrow} {ua_label} ({n})",
                         **FONT_CAGE, color="#F57F17", zorder=3)
            self._hit_map.append(((x, y, x + w, y + h), UNASSIGNED_CAGE_ID, "cage"))
            self._hit_map.append(((x, y, x + w, y + TITLE_H), UNASSIGNED_CAGE_ID, "unassigned_title"))
            return h

        cols = min(n, 4)
        rows = max(1, (n + 3) // 4)
        w = max(300, cols * 110 + CAGE_PAD * 2)
        h = TITLE_H + OCCUPANT_LINE_H * rows + CAGE_PAD * 2

        self._draw_flat_rect(x, y, w, h, UNASSIGNED_BG, UNASSIGNED_BORDER, linewidth=1.5, zorder=2)
        arrow = "▼"
        self.ax.text(x + 6, y + TITLE_H * 0.6, f"{arrow} {ua_label} ({n})",
                     **FONT_CAGE, color="#F57F17", zorder=3)

        # Keep circles batched for speed, but draw names per row so they stay
        # aligned with the corresponding circle.
        ox = x + CAGE_PAD
        oy = y + TITLE_H + CAGE_PAD
        scatter_x: List[float] = []
        scatter_y: List[float] = []
        face_colors: List[str] = []
        edge_colors: List[str] = []
        text_rows: List[Tuple[float, float, str]] = []
        for i, occ in enumerate(occupants):
            col_idx = i % 4
            row_idx = i // 4
            tx = ox + col_idx * 110
            ty = oy + row_idx * OCCUPANT_LINE_H
            occ_id = occ["occupant_id"]
            is_archived = occ.get("archived", False)
            if is_archived:
                color = ARCHIVED_COLOR
                project_color = ARCHIVED_COLOR
            else:
                color = self._cached_animal_role_color(occ_id, animals_dict)
                project_color = self._cached_animal_project_color(occ_id, animals_dict, project_colors)
            scatter_x.append(tx + 5)
            scatter_y.append(ty)
            face_colors.append(color)
            edge_colors.append(project_color if project_color != "#000000" else "#424242")
            text_rows.append((tx + 13, ty, occ_id))
            self._hit_map.append(((tx - 2, ty - 6, tx + 100, ty + 8), occ_id, "occupant"))

        if scatter_x:
            self.ax.scatter(scatter_x, scatter_y, s=42, marker="o",
                            facecolors=face_colors, edgecolors=edge_colors,
                            linewidths=0.8, zorder=4, clip_on=True)
        for tx, ty, occ_id in text_rows:
            self.ax.text(tx, ty, occ_id, fontsize=7.5, color="#212121",
                         zorder=3, clip_on=True, verticalalignment="center")

        self._hit_map.append(((x, y, x + w, y + h), UNASSIGNED_CAGE_ID, "cage"))
        self._hit_map.append(((x, y, x + w, y + TITLE_H), UNASSIGNED_CAGE_ID, "unassigned_title"))
        return h

    def _draw_building(self, bld: Dict[str, Any], x: float, y: float, w: float, h: float,
                       expanded: bool, expanded_rooms: Set[str],
                       animals_dict: Dict[str, Any], project_colors: Dict[str, str]) -> None:
        bld_id = bld["id"]
        border = SELECTED_BORDER if self.selected_cage_id == bld_id else BLD_BORDER
        lw = 2.5 if self.selected_cage_id == bld_id else 1.0

        self._draw_rect(x, y, w, h, BLD_BG, border, linewidth=lw, zorder=1)
        # Title bar
        self._draw_flat_rect(x + 1, y + 1, w - 2, TITLE_H - 2, BLD_TITLE_BG, "none", zorder=2)
        arrow = "▼" if expanded else "▶"
        self.ax.text(x + 6, y + TITLE_H * 0.65, f"{arrow} {bld.get('display_name', bld_id)}",
                     **FONT_BLD, color="#37474F", zorder=3, clip_on=True)

        self._hit_map.append(((x, y, x + w, y + h), bld_id, "building"))
        self._hit_map.append(((x, y, x + w, y + TITLE_H), bld_id, "building_title"))

        if not expanded:
            return

        # Draw rooms in rows respecting max_per_row
        max_per_row = bld.get("max_per_row", 4)
        all_rooms = bld.get("rooms", [])
        ry = y + TITLE_H + BLD_PAD
        for row_start in range(0, len(all_rooms), max_per_row):
            row_rooms = all_rooms[row_start:row_start + max_per_row]
            rx = x + BLD_PAD
            max_room_h = 0
            for room in row_rooms:
                room_id = room["id"]
                room_expanded = room_id in expanded_rooms
                if room_expanded:
                    rw, rh = self._measure_room(room, animals_dict)
                else:
                    rw, rh = 140, TITLE_H + 6
                self._draw_room(room, rx, ry, rw, rh, room_expanded,
                                animals_dict, project_colors)
                rx += rw + SPACING
                max_room_h = max(max_room_h, rh)
            ry += max_room_h + SPACING

    def _draw_room(self, room: Dict[str, Any], x: float, y: float, w: float, h: float,
                   expanded: bool, animals_dict: Dict[str, Any],
                   project_colors: Dict[str, str]) -> None:
        room_id = room["id"]
        border = SELECTED_BORDER if self.selected_cage_id == room_id else ROOM_BORDER
        lw = 2.5 if self.selected_cage_id == room_id else 1.0

        self._draw_rect(x, y, w, h, ROOM_BG, border, linewidth=lw, zorder=2)
        self._draw_flat_rect(x + 1, y + 1, w - 2, TITLE_H - 2, ROOM_TITLE_BG, "none", zorder=3)
        arrow = "▼" if expanded else "▶"
        self.ax.text(x + 6, y + TITLE_H * 0.65, f"{arrow} {room.get('display_name', room_id)}",
                     **FONT_ROOM, color="#2E7D32", zorder=4, clip_on=True)

        self._hit_map.append(((x, y, x + w, y + h), room_id, "room"))
        self._hit_map.append(((x, y, x + w, y + TITLE_H), room_id, "room_title"))

        if not expanded:
            return

        # Draw cages in rows respecting max_per_row
        max_per_row = room.get("max_per_row", 4)
        all_cages = room.get("cages", [])
        cy = y + TITLE_H + ROOM_PAD
        for row_start in range(0, len(all_cages), max_per_row):
            row_cages = all_cages[row_start:row_start + max_per_row]
            cx = x + ROOM_PAD
            max_cage_h = 0
            for cage in row_cages:
                cw, ch = self._measure_cage(cage)
                self._draw_cage(cage, cx, cy, cw, ch, animals_dict, project_colors)
                cx += cw + SPACING
                max_cage_h = max(max_cage_h, ch)
            cy += max_cage_h + SPACING

    def _draw_cage(self, cage: Dict[str, Any], x: float, y: float, w: float, h: float,
                   animals_dict: Dict[str, Any], project_colors: Dict[str, str]) -> None:
        cage_id = cage["id"]
        occupants = cage.get("occupants", [])

        border = SELECTED_BORDER if self.selected_cage_id == cage_id else CAGE_BORDER
        lw = 2.5 if self.selected_cage_id == cage_id else 1.0

        # Plain cage background
        self._draw_flat_rect(x, y, w, h, CAGE_BG, border, linewidth=lw, zorder=3)

        self._draw_flat_rect(x, y, w, TITLE_H, CAGE_TITLE_BG, "none", zorder=4)
        self.ax.text(x + 4, y + TITLE_H * 0.65, cage.get("display_name", cage_id),
                     **FONT_CAGE, color="#424242", zorder=5, clip_on=True)

        self._hit_map.append(((x, y, x + w, y + h), cage_id, "cage"))
        self._hit_map.append(((x, y, x + w, y + TITLE_H), cage_id, "cage_title"))

        # Keep circles batched for speed, but draw names per row so they stay
        # aligned with the corresponding circle.
        oy = y + TITLE_H + CAGE_PAD
        scatter_x: List[float] = []
        scatter_y: List[float] = []
        face_colors: List[str] = []
        edge_colors: List[str] = []
        text_rows: List[Tuple[float, float, str]] = []
        for occ in occupants:
            occ_id = occ["occupant_id"]
            circle_color = self._cached_animal_role_color(occ_id, animals_dict)
            project_color = self._cached_animal_project_color(occ_id, animals_dict, project_colors)
            scatter_x.append(x + CAGE_PAD + 5)
            scatter_y.append(oy)
            face_colors.append(circle_color)
            edge_colors.append(project_color if project_color != "#000000" else "#424242")
            text_rows.append((x + CAGE_PAD + 13, oy, occ_id))
            self._hit_map.append(((x + CAGE_PAD - 2, oy - 6, x + w - CAGE_PAD, oy + 8),
                                  occ_id, "occupant"))
            oy += OCCUPANT_LINE_H
        if scatter_x:
            self.ax.scatter(scatter_x, scatter_y, s=42, marker="o",
                            facecolors=face_colors, edgecolors=edge_colors,
                            linewidths=0.8, zorder=6, clip_on=True)
        for tx, ty, occ_id in text_rows:
            self.ax.text(tx, ty, occ_id, fontsize=7.5, color="#212121",
                         zorder=5, clip_on=True, verticalalignment="center")

    def _draw_legend(self, project_colors: Dict[str, str], total_w: float,
                     total_h: float, stored_pos: Optional[List[float]] = None) -> None:
        """Draw project colour legend. Position is draggable and persisted."""
        n = len(project_colors)
        if n == 0:
            return

        # Calculate dynamic width based on longest project name
        # Text: 7pt font ~ 6.5px per char, plus margins for circle (~20px) and padding
        max_name_width = 0
        for pname in project_colors.keys():
            name_width = len(pname) * 6.5
            max_name_width = max(max_name_width, name_width)
        # min width: padding (6) + circle area (20) + text + padding (10) + safety (10)
        lw = max(130, 6 + 20 + max_name_width + 10 + 10)
        lh = 18 + n * 16 + 20

        if stored_pos and len(stored_pos) == 2:
            lx, ly = stored_pos
        else:
            lx = total_w - lw - SPACING
            ly = total_h - lh - SPACING

        self._draw_flat_rect(lx, ly, lw, lh, "#FFFFFF", "#BDBDBD", linewidth=0.8, zorder=8)
        self.ax.text(lx + 6, ly + 14, "Projects", fontsize=8, fontweight="bold", zorder=9)

        row_y = ly + 28
        for pname, pcolor in sorted(project_colors.items()):
            self.ax.scatter([lx + 11], [row_y + 1], s=50, marker="o",
                            facecolors=pcolor, edgecolors="#424242",
                            linewidths=0.6, zorder=9, clip_on=True)
            self.ax.text(lx + 22, row_y + 3, pname, fontsize=7, zorder=9, clip_on=True)
            row_y += 16

        # Hit map entry for dragging (must come last so it wins over building hits)
        self._hit_map.append(((lx, ly, lx + lw, ly + lh), "__legend__", "legend"))

    # ------------------------------------------------------------------
    # Canvas interaction
    # ------------------------------------------------------------------

    def _hit_test(self, x: float, y: float) -> Optional[Tuple[str, str]]:
        """Return (entity_id, entity_type) for the topmost hit, or None."""
        # Walk in reverse so top-drawn items win
        for (x0, y0, x1, y1), eid, etype in reversed(self._hit_map):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return eid, etype
        return None

    def _on_canvas_press(self, event) -> None:
        if event.inaxes is not self.ax:
            return
        if event.xdata is None or event.ydata is None:
            return

        # Middle-button or ctrl+left → pan
        if event.button == 2 or (event.button == 1 and event.key == "control"):
            self._pan_active = True
            self._pan_start = (event.x, event.y)
            self._pan_xlim = self.ax.get_xlim()
            self._pan_ylim = self.ax.get_ylim()
            return

        if event.button == 1:
            hit = self._hit_test(event.xdata, event.ydata)
            if hit:
                eid, etype = hit

                # Legend drag
                if etype == "legend":
                    self._legend_dragging = True
                    self._legend_drag_offset = (event.xdata, event.ydata)
                    self._pan_start = (event.x, event.y)
                    return

                # Double-click on occupant → movement history
                if getattr(event, "dblclick", False) and etype == "occupant":
                    self._show_movement_history(eid)
                    return

                if etype == "occupant":
                    self._highlight_occupant = eid
                    self.refresh_view(sync_animals=False)
                    return
                elif etype in ("building_title", "room_title", "cage_title", "unassigned_title"):
                    self._toggle_expand(eid, etype)
                    return
                elif etype in ("building", "room", "cage"):
                    self.selected_cage_id = eid
                    self.refresh_view(sync_animals=False)
                    return

        # Right-click → context menu
        if event.button == 3:
            hit = self._hit_test(event.xdata, event.ydata)
            if hit:
                self._show_context_menu(hit[0], hit[1], event)

    def _on_canvas_release(self, event) -> None:
        if self._pan_active and not self._legend_dragging:
            self._pan_active = False
            return

        if self._legend_dragging:
            self._legend_dragging = False
            if event.xdata is not None and event.ydata is not None and self._legend_drag_offset:
                dx = event.xdata - self._legend_drag_offset[0]
                dy = event.ydata - self._legend_drag_offset[1]
                ui_state = self.store.get_ui_state()
                old_pos = ui_state.get("legend_pos", None)
                if old_pos and len(old_pos) == 2:
                    new_pos = [old_pos[0] + dx, old_pos[1] + dy]
                else:
                    # Estimate current legend position from hit_map
                    for (x0, y0, x1, y1), eid, etype in reversed(self._hit_map):
                        if etype == "legend":
                            new_pos = [x0 + dx, y0 + dy]
                            break
                    else:
                        new_pos = [event.xdata, event.ydata]
                self.store.set_ui_state({"legend_pos": new_pos})
                self.refresh_view()
            self._legend_drag_offset = None
            return

    def _on_canvas_motion(self, event) -> None:
        if self._pan_active and self._pan_start and event.x is not None:
            if self._legend_dragging:
                return
            inv = self.ax.transData.inverted()
            start_data = inv.transform((self._pan_start[0], self._pan_start[1]))
            end_data = inv.transform((event.x, event.y))
            ddx = end_data[0] - start_data[0]
            ddy = end_data[1] - start_data[1]
            self.ax.set_xlim(self._pan_xlim[0] - ddx, self._pan_xlim[1] - ddx)
            self.ax.set_ylim(self._pan_ylim[0] - ddy, self._pan_ylim[1] - ddy)
            self.canvas.draw_idle()
            return

    def _on_scroll(self, event) -> None:
        """Zoom via scroll wheel."""
        if event.inaxes is not self.ax:
            return
        factor = 0.9 if event.button == "up" else 1.1
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        xdata = event.xdata or (xlim[0] + xlim[1]) / 2
        ydata = event.ydata or (ylim[0] + ylim[1]) / 2

        new_xlim = [xdata + (x - xdata) * factor for x in xlim]
        new_ylim = [ydata + (y - ydata) * factor for y in ylim]
        self.ax.set_xlim(new_xlim)
        self.ax.set_ylim(new_ylim)
        self._current_xlim = tuple(new_xlim)
        self._current_ylim = tuple(new_ylim)
        # Enforce minimum content widths after zoom
        self._enforce_min_content_widths()
        self.canvas.draw_idle()

    def _on_resize(self, _event) -> None:
        """Re-apply subplot margins and enforce minimum cage/legend pixel widths."""
        self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
        if self._current_xlim and self._current_ylim:
            self.ax.set_xlim(self._current_xlim)
            self.ax.set_ylim(self._current_ylim)
            self._enforce_min_content_widths()
        self.canvas.draw_idle()

    def _enforce_min_content_widths(self) -> None:
        """Ensure cages and legend don't shrink below their content widths when window narrows.

        Calculates required pixels-per-data-unit ratio based on stored minimum pixel widths
        and expands the x-axis range if necessary to maintain those widths.
        """
        if not self._cage_min_pixel_widths:
            return

        # Get current display metrics
        bbox = self.ax.get_window_extent()
        if bbox.width <= 0 or bbox.height <= 0:
            return

        fig_w = self.figure.get_figwidth() * self.figure.dpi
        xlim = self.ax.get_xlim()
        data_width = xlim[1] - xlim[0]
        if data_width <= 0:
            return

        # Current pixels per data unit
        px_per_unit = bbox.width / data_width

        # Find the maximum required pixels-per-unit ratio
        # For each cage: we need its min_pixel_width to occupy its data_width
        # So required px_per_unit = min_pixel_width / data_width
        max_required_ratio = 0
        for cage_id, min_px_width in self._cage_min_pixel_widths.items():
            # Get actual data width for this cage
            cage_data_width = self._cage_data_widths.get(cage_id, CAGE_MIN_W)
            if cage_data_width > 0:
                required_ratio = min_px_width / cage_data_width
                max_required_ratio = max(max_required_ratio, required_ratio)

        # If current ratio is insufficient, we need to zoom out (increase data_width)
        if px_per_unit < max_required_ratio and max_required_ratio > 0:
            # Calculate new data width needed to maintain min pixel widths
            new_data_width = (bbox.width / max_required_ratio) * 1.02  # 2% safety margin
            center = (xlim[0] + xlim[1]) / 2
            new_xlim = (center - new_data_width / 2, center + new_data_width / 2)
            self.ax.set_xlim(new_xlim)
            self._current_xlim = new_xlim

    def _show_movement_history(self, occupant_id: str) -> None:
        dlg = MovementHistoryDialog(self, self.messages, occupant_id, self.engine, self.store)
        dlg.exec()

    # ------------------------------------------------------------------
    # Expand / collapse
    # ------------------------------------------------------------------

    def _toggle_expand(self, entity_id: str, entity_type: str) -> None:
        ui_state = self.store.get_ui_state()

        if entity_type == "building_title":
            expanded = list(ui_state.get("expanded_buildings", []))
            if entity_id in expanded:
                expanded.remove(entity_id)
            else:
                expanded.append(entity_id)
            self.store.set_ui_state({"expanded_buildings": expanded})

        elif entity_type == "room_title":
            expanded = list(ui_state.get("expanded_rooms", []))
            if entity_id in expanded:
                expanded.remove(entity_id)
            else:
                expanded.append(entity_id)
            self.store.set_ui_state({"expanded_rooms": expanded})

        elif entity_type == "unassigned_title":
            current = ui_state.get("show_unassigned", True)
            self.store.set_ui_state({"show_unassigned": not current})

        self.refresh_view(sync_animals=False)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, entity_id: str, entity_type: str, event) -> None:
        menu = QMenu(self)
        _eid = entity_id  # capture for lambdas

        if entity_type in ("building", "building_title"):
            edit_action = menu.addAction(self.messages.get("cage_track.context.edit", "Edit"))
            edit_action.triggered.connect(lambda checked=False, sid=_eid: self._edit_structure(sid, "building"))

            add_room = menu.addAction(self.messages.get("cage_track.context.add_room", "Add Room"))
            add_room.triggered.connect(lambda checked=False, sid=_eid: self._quick_add_child(sid, "room"))

            delete_action = menu.addAction(self.messages.get("cage_track.context.delete", "Delete"))
            delete_action.triggered.connect(lambda checked=False, sid=_eid: self._delete_structure(sid, "building"))

        elif entity_type in ("room", "room_title"):
            edit_action = menu.addAction(self.messages.get("cage_track.context.edit", "Edit"))
            edit_action.triggered.connect(lambda checked=False, sid=_eid: self._edit_structure(sid, "room"))

            add_cage = menu.addAction(self.messages.get("cage_track.context.add_cage", "Add Cage"))
            add_cage.triggered.connect(lambda checked=False, sid=_eid: self._quick_add_child(sid, "cage"))

            delete_action = menu.addAction(self.messages.get("cage_track.context.delete", "Delete"))
            delete_action.triggered.connect(lambda checked=False, sid=_eid: self._delete_structure(sid, "room"))

        elif entity_type in ("cage", "cage_title"):
            if entity_id == UNASSIGNED_CAGE_ID:
                return
            edit_action = menu.addAction(self.messages.get("cage_track.context.edit", "Edit"))
            edit_action.triggered.connect(lambda checked=False, sid=_eid: self._edit_structure(sid, "cage"))

            delete_action = menu.addAction(self.messages.get("cage_track.context.delete", "Delete"))
            delete_action.triggered.connect(lambda checked=False, sid=_eid: self._delete_structure(sid, "cage"))

        elif entity_type == "occupant":
            hist_action = menu.addAction(self.messages.get("cage_track.context.properties", "Properties"))
            hist_action.triggered.connect(lambda checked=False, sid=_eid: self._show_movement_history(sid))

        if menu.actions():
            from PyQt6.QtGui import QCursor
            menu.exec(QCursor.pos())

    # ------------------------------------------------------------------
    # Quick actions
    # ------------------------------------------------------------------

    def _edit_structure(self, struct_id: str, struct_type: str) -> None:
        if not self._can('cage.edit'):
            self._deny()
            return
        current = self.store.get_structure_by_id(struct_id)
        if not current:
            return
        old_name = current.get("display_name", "")

        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get("cage_track.context.edit", "Edit"))
        dlg.setModal(True)
        layout = QFormLayout(dlg)

        name_edit = QLineEdit(old_name)
        layout.addRow(self.messages.get("cage_track.add.name", "Name:"), name_edit)

        from PyQt6.QtWidgets import QSpinBox
        count_spin = None
        max_per_row_spin = None

        if struct_type == "building":
            # Current room count
            existing_rooms = self.engine.get_rooms_in_building(struct_id)
            count_spin = QSpinBox()
            count_spin.setMinimum(0)
            count_spin.setMaximum(200)
            count_spin.setValue(len(existing_rooms))
            layout.addRow(self.messages.get("cage_track.edit.room_count", "Number of rooms:"), count_spin)

            max_per_row_spin = QSpinBox()
            max_per_row_spin.setMinimum(1)
            max_per_row_spin.setMaximum(20)
            max_per_row_spin.setValue(current.get("max_per_row", 4))
            layout.addRow(self.messages.get("cage_track.edit.max_per_row", "Max rooms per row:"), max_per_row_spin)

        elif struct_type == "room":
            # Current cage count
            existing_cages = self.engine.get_cages_in_room(struct_id)
            count_spin = QSpinBox()
            count_spin.setMinimum(0)
            count_spin.setMaximum(200)
            count_spin.setValue(len(existing_cages))
            layout.addRow(self.messages.get("cage_track.edit.cage_count", "Number of cages:"), count_spin)

            max_per_row_spin = QSpinBox()
            max_per_row_spin.setMinimum(1)
            max_per_row_spin.setMaximum(20)
            max_per_row_spin.setValue(current.get("max_per_row", 4))
            layout.addRow(self.messages.get("cage_track.edit.max_per_row", "Max cages per row:"), max_per_row_spin)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addRow(btn_box)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_name = name_edit.text().strip()
            if new_name and new_name != old_name:
                self.store.rename_structure(struct_id, struct_type, new_name)

            if max_per_row_spin is not None:
                self._set_structure_max_per_row(struct_id, struct_type, max_per_row_spin.value())

            if count_spin is not None:
                desired = count_spin.value()
                if struct_type == "building":
                    existing = self.engine.get_rooms_in_building(struct_id)
                    current_count = len(existing)
                    if desired > current_count:
                        base_name = new_name or old_name
                        for i in range(current_count + 1, desired + 1):
                            self.store.create_room(struct_id, f"{base_name} R{i}")
                elif struct_type == "room":
                    existing = self.engine.get_cages_in_room(struct_id)
                    current_count = len(existing)
                    if desired > current_count:
                        base_name = new_name or old_name
                        for i in range(current_count + 1, desired + 1):
                            self.store.create_cage(struct_id, f"{base_name} C{i}")

            self.refresh_view()

    def _set_structure_max_per_row(self, struct_id: str, struct_type: str, value: int) -> None:
        """Persist max_per_row setting on a building or room."""
        data = self.store.load_data()
        type_map = {"building": "buildings", "room": "rooms"}
        container = data["structures"].get(type_map.get(struct_type, ""), {})
        if struct_id in container:
            container[struct_id]["max_per_row"] = value
            self.store.save_data()

    def _delete_structure(self, struct_id: str, struct_type: str) -> None:
        if not self._can('cage.edit'):
            self._deny()
            return
        reply = QMessageBox.question(
            self,
            self.messages.get("cage_track.confirm.delete", "Delete this structure?"),
            self.messages.get("cage_track.confirm.delete.cascade",
                              "Delete this structure and all its contents? Orphaned animals will be moved to Unassigned."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.store.delete_structure(struct_id, struct_type)
            self.refresh_view()

    def _quick_add_child(self, parent_id: str, child_type: str) -> None:
        """Quick-add a room or cage under a parent."""
        if not self._can('cage.manage_rooms_buildings'):
            self._deny()
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get("cage_track.add.title", "Add New Structure"))
        dlg.setModal(True)
        layout = QFormLayout(dlg)
        name_edit = QLineEdit()
        layout.addRow(self.messages.get("cage_track.add.name", "Name:"), name_edit)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addRow(btn_box)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = name_edit.text().strip()
            if name:
                if child_type == "room":
                    self.store.create_room(parent_id, name)
                elif child_type == "cage":
                    self.store.create_cage(parent_id, name)
                self.refresh_view()

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def _get_inspection_path(self) -> str:
        return os.path.join(self.store.plugin_dir, "inspection.json")

    def _load_inspections(self) -> List[Dict[str, Any]]:
        path = self._get_inspection_path()
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("records", [])
        except Exception:
            return []

    def _save_inspections(self, records: List[Dict[str, Any]]) -> None:
        path = self._get_inspection_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"records": records}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save inspections: {e}")

    def _get_current_username(self) -> str:
        app = getattr(self.plugin, 'app', None)
        if app:
            mt = getattr(app, 'master_track', None)
            if mt and getattr(mt, 'is_logged_in', False):
                user = mt.user_db.get_user(mt.current_username)
                if user:
                    return user.get("display_name", mt.current_username)
                return mt.current_username
        import getpass
        return getpass.getuser()

    def _resolve_entity_type(self, entity_id: str) -> Optional[str]:
        data = self.store.load_data()
        s = data["structures"]
        if entity_id in s["buildings"]:
            return "building"
        if entity_id in s["rooms"]:
            return "room"
        if entity_id in s["cages"]:
            return "cage"
        return None

    def _resolve_inspection_data(self) -> Optional[Dict[str, Any]]:
        """From current selection, resolve building (unit) and cage addresses."""
        eid = self.selected_cage_id
        if not eid or eid == UNASSIGNED_CAGE_ID:
            return None

        etype = self._resolve_entity_type(eid)
        if not etype:
            return None

        data = self.store.load_data()
        s = data["structures"]

        if etype == "building":
            bld_id = eid
            bld_name = s["buildings"][eid].get("display_name", eid)
            rooms = sorted(
                [r for r in s["rooms"].values()
                 if r.get("parent_building_id") == bld_id],
                key=lambda r: r.get("order", 0))
            cage_names: List[str] = []
            for room in rooms:
                r_name = room.get("display_name", room["id"])
                cages = sorted(
                    [c for c in s["cages"].values()
                     if c.get("parent_room_id") == room["id"]
                     and not c.get("is_virtual")],
                    key=lambda c: c.get("order", 0))
                cage_names.extend(
                    f"{r_name}/{c.get('display_name', c['id'])}" for c in cages)

        elif etype == "room":
            room = s["rooms"][eid]
            bld_id = room.get("parent_building_id")
            bld = s["buildings"].get(bld_id) if bld_id else None
            if not bld:
                return None
            bld_name = bld.get("display_name", bld_id)
            cages = sorted(
                [c for c in s["cages"].values()
                 if c.get("parent_room_id") == eid
                 and not c.get("is_virtual")],
                key=lambda c: c.get("order", 0))
            r_name = room.get("display_name", eid)
            cage_names = [
                f"{r_name}/{c.get('display_name', c['id'])}" for c in cages]

        elif etype == "cage":
            cage = s["cages"][eid]
            if cage.get("is_virtual"):
                return None
            room_id = cage.get("parent_room_id")
            room = s["rooms"].get(room_id) if room_id else None
            bld_id = room.get("parent_building_id") if room else None
            bld = s["buildings"].get(bld_id) if bld_id else None
            if not bld:
                return None
            bld_name = bld.get("display_name", bld_id)
            r_name = room.get("display_name", room_id) if room else ""
            cage_names = [f"{r_name}/{cage.get('display_name', eid)}"]
        else:
            return None

        if not cage_names:
            return None

        return {
            "unit_id": bld_id if etype != "building" else eid,
            "unit_name": bld_name,
            "cages": ", ".join(cage_names),
        }

    def _on_inspection(self) -> None:
        """Record inspection for current selection and show inspection log."""
        inspection_data = self._resolve_inspection_data()

        if inspection_data and self._can('cage.record_inspection'):
            today = datetime.now().strftime("%d/%m/%Y")
            today_sort = datetime.now().strftime("%Y-%m-%d")
            user = self._get_current_username()

            records = self._load_inspections()
            # Same unit + same date → overwrite
            records = [
                r for r in records
                if not (r.get("unit_id") == inspection_data["unit_id"]
                        and r.get("date") == today)]
            records.append({
                "unit_id": inspection_data["unit_id"],
                "unit_name": inspection_data["unit_name"],
                "date": today,
                "date_sort": today_sort,
                "cages": inspection_data["cages"],
                "user": user,
            })
            self._save_inspections(records)

        all_records = self._load_inspections()

        def on_export():
            export_dlg = InspectionPDFExportDialog(
                self, self.messages, self._load_inspections())
            export_dlg.exec()

        dlg = InspectionDialog(self, self.messages, all_records, on_export)
        dlg.exec()

    # ------------------------------------------------------------------
    # Toolbar actions
    # ------------------------------------------------------------------

    def _on_add(self) -> None:
        if not self._can('cage.manage_rooms_buildings'):
            self._deny()
            return
        dlg = AddStructureDialog(self, self.messages, self.engine, self.store)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh_view()

    def _on_settings(self) -> None:
        dlg = CageSettingsDialog(self, self.messages, self.store)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.refresh_view()

    def _on_refresh_assignments(self) -> None:
        if hasattr(self.plugin, "mark_cage_assignments_dirty"):
            self.plugin.mark_cage_assignments_dirty()
        self.refresh_view(sync_animals=True)

    # ------------------------------------------------------------------
    # External integration
    # ------------------------------------------------------------------

    def on_animal_selected(self, animal_names: List[str]) -> None:
        """Called by ProgTrack when an animal is selected in main list."""
        if not animal_names:
            self._highlight_occupant = None
            self.selected_cage_id = None
            return

        name = animal_names[0]
        self._highlight_occupant = name
        cage_id = self.store.get_occupant_cage(name)
        if cage_id and cage_id != UNASSIGNED_CAGE_ID:
            self.selected_cage_id = cage_id
        else:
            self.selected_cage_id = None


# ======================================================================
# Plugin class
# ======================================================================

class CageTrackPlugin:
    """Main plugin object used by ProgTrack integration hooks."""

    def __init__(self, app):
        self.app = app
        self.messages: Dict[str, Any] = getattr(app, "messages", {}) or {}
        self.plugin_dir = os.path.dirname(os.path.abspath(__file__))
        self.store = CageStore(self.plugin_dir)
        self.store.load_data()

        self.widget: Optional[CageTrackWidget] = None
        self._cage_assignments_dirty = True
        self.settings: Dict[str, Any] = {
            "show_unassigned": True,
            "show_legend": True,
            "auto_expand_on_select": True,
        }

    # ------------------------------------------------------------------
    # Plugin interface
    # ------------------------------------------------------------------

    def get_name(self) -> str:
        return "Cage_Track"

    def get_tab_widget(self) -> CageTrackWidget:
        if self.widget is not None:
            try:
                return self.widget
            except RuntimeError:
                self.widget = None
        self.widget = CageTrackWidget(self)
        return self.widget

    def update_language(self, messages: Dict[str, Any]) -> None:
        self.messages = messages or {}
        if self.widget is not None:
            try:
                self.widget.update_language(self.messages)
            except RuntimeError:
                self.widget = None

    def refresh_if_visible(self) -> None:
        # Keep this hook deliberately light. ProgTrack calls it after selection
        # and persistence changes; doing a visible full sync here makes active
        # Cage Track interactions slow and shows address changes before the
        # user presses the explicit refresh button.
        return

    def refresh_on_tab_activated(self) -> None:
        if self.widget is None:
            return
        if not self._cage_assignments_dirty:
            return
        try:
            self.widget.refresh_view(sync_animals=True)
        except RuntimeError:
            self.widget = None

    def mark_cage_assignments_dirty(self) -> None:
        self._cage_assignments_dirty = True

    def mark_cage_assignments_clean(self) -> None:
        self._cage_assignments_dirty = False

    # ------------------------------------------------------------------
    # Address integration for ProgTrack dialog
    # ------------------------------------------------------------------

    def get_address_fields_builder(self):
        from .ui_address_fields import build_address_group
        return build_address_group

    def get_current_address(self, animal_name: str) -> Dict[str, Optional[str]]:
        return self.store.get_address_for_dialog(animal_name)

    def save_address_from_dialog(self, animal_name: str, address_values: Dict[str, Optional[str]]) -> None:
        before_address = self.store.get_address_for_dialog(animal_name)
        self.store.set_address_from_dialog(
            animal_name,
            address_values.get("building_id"),
            address_values.get("room_id"),
            address_values.get("cage_id"),
        )

        after_address = self.store.get_address_for_dialog(animal_name)
        if before_address == after_address:
            return
        self.mark_cage_assignments_dirty()

        app = getattr(self, "app", None)
        audit_fn = getattr(app, "_master_audit", None) if app is not None else None
        if callable(audit_fn):
            details = (
                f"function=save_address_from_dialog; "
                f"animal={animal_name}; "
                f"parameter=address; "
                f"previous={json.dumps(before_address, ensure_ascii=False, sort_keys=True)}; "
                f"new={json.dumps(after_address, ensure_ascii=False, sort_keys=True)}"
            )
            audit_fn("data_edit", "Cage__Track", details)

    # ------------------------------------------------------------------
    # Data sync
    # ------------------------------------------------------------------

    def sync_animal_data(self, animals: Dict[str, Any]) -> None:
        self.mark_cage_assignments_dirty()

    def get_structures_for_address(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return all structures for populating address dropdowns."""
        return {
            "buildings": self.store.get_all_buildings(),
            "rooms": self.store.get_all_rooms(),
            "cages": self.store.get_all_cages(),
        }
