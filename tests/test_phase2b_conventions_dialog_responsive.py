"""Focused offscreen checks for the responsive Conventions dialog (#61)."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QLayout, QMainWindow, QPushButton

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Plugins.core.animal_roles import (
    DEFAULT_ROLE_DEFINITIONS,
    normalize_block_list,
    normalize_role_definition,
)
from Plugins.core.backend.core_configuration import (
    IDENTITY_CONVENTIONS,
    read_seed_configuration,
)
from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths


VIRTUAL_WORK_AREA = QRect(0, 0, 1366, 768)


def _install_offscreen_font(app: QApplication) -> None:
    """Give the headless platform a real font instead of tofu glyph boxes."""
    font_path = ROOT / "_internal" / "matplotlib" / "mpl-data" / "fonts" / "ttf" / "DejaVuSans.ttf"
    family = "DejaVu Sans"
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            family = families[0]
    app.setFont(QFont(family, 9))


class _MemoryRecords:
    def __init__(self) -> None:
        self.values = {}
        self.revisions = {}
        self.audit_events = []

    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.values.get((namespace, key), default))

    def get_with_revision(self, namespace, key, default=None):
        return self.get(namespace, key, default), self.revisions.get((namespace, key), 0)

    def put(self, namespace, key, value, **_kwargs):
        current = self.revisions.get((namespace, key), 0)
        if _kwargs.get('expected_revision') not in (None, current):
            raise RuntimeError('stale record revision')
        self.values[(namespace, key)] = copy.deepcopy(value)
        self.revisions[(namespace, key)] = current + 1
        return current + 1

    def put_many_audited(self, records, *, expected_revisions, audit_events):
        records = list(records)
        for ns, key, _value in records:
            if self.revisions.get((ns, key), 0) != expected_revisions[(ns, key)]:
                raise RuntimeError('stale record revision')
        result = {(ns, key): self.put(ns, key, value, expected_revision=expected_revisions[(ns, key)])
                  for ns, key, value in records}
        self.audit_events.extend(copy.deepcopy(audit_events))
        return result


class _Branding:
    def edit_state(self):
        return self.load(), 0, None

    def load(self):
        return {
            "enabled": True,
            "facility_name": "Example Primate Research Centre",
            "logo_document_id": "",
            "position": "top_right",
        }

    def logo_path(self, *_args):
        return None

    def logo_name(self, *_args):
        return ""

    def logo_contents(self, *_args):
        return None


class _Parent(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self._temporary = tempfile.TemporaryDirectory(
            prefix="progtrack-conventions-responsive-"
        )
        self.backend = ProgTrackBackend(
            runtime_paths(Path(self._temporary.name)),
            acquire_process_lock=False,
            choose_demo_seed=lambda: False,
        )
        session = self.backend.authenticated_sessions.create_first_account(
            "responsive-admin", "Responsive-test!"
        )
        self.backend.core_configuration.setup(session)
        self.master_track = SimpleNamespace(
            current_username="responsive-admin",
            session_context=session,
        )
        self.has_pdg_plugin = True
        colors = {
            "combined": "#8b0000",
            "blood": "#ff0000",
            "urine": "#ff8c00",
            "weight": "#800080",
            "pdg": "#008000",
            "sperm_total": "#d55e00",
            "sperm_motile": "#0072b2",
            "sperm_progressive": "#009e73",
        }
        for key, value in colors.items():
            setattr(self, f"{key}_color", QColor(value))
        for key in (
            "combined",
            "blood",
            "urine",
            "weight",
            "fsh",
            "sperm_total",
            "sperm_motile",
            "sperm_progressive",
        ):
            setattr(self, f"{key}_marker", "o")

    def _load_animal_role_definitions(self):
        return [
            normalize_role_definition(copy.deepcopy(role))
            for role in DEFAULT_ROLE_DEFINITIONS
        ]

    def _can_configure_animal_roles(self):
        return True

    def _normalize_role_dialog_blocks(self, blocks):
        return normalize_block_list(blocks)

    def _load_identity_conventions(self):
        seeded = read_seed_configuration(ROOT)
        return copy.deepcopy(seeded[IDENTITY_CONVENTIONS])

    def _can_manage_institution_branding(self):
        return True

    def _is_steroid_track_active(self):
        return True


class ConventionsDialogResponsiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        _install_offscreen_font(cls.app)
        spec = importlib.util.spec_from_file_location(
            "progtrack_conventions_responsive_test",
            ROOT / "ProgTrack.v.0.3.0.py",
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)
        cls.messages = json.loads(
            (ROOT / "lang" / "messages_en.json").read_text(encoding="utf-8")
        )

    def setUp(self):
        self.parent = _Parent()
        self.addCleanup(self.parent._temporary.cleanup)
        self.addCleanup(self.parent.backend.close)
        self.dialog = self.module.StyleSettingsDialog(self.parent, self.messages)

    def tearDown(self):
        self.dialog.close()
        self.parent.close()
        self.dialog.deleteLater()
        self.parent.deleteLater()
        self.app.processEvents()

    def _fit_tab(self, index: int, area: QRect = VIRTUAL_WORK_AREA) -> QSize:
        self.dialog.tabs.setCurrentIndex(index)
        self.app.processEvents()
        target = self.dialog._fit_current_tab(area, apply_guard=False)
        self.app.processEvents()
        return target

    def test_each_english_tab_uses_natural_size_within_1366_work_area(self):
        maximum = QSize(
            int(VIRTUAL_WORK_AREA.width() * 0.92),
            int(VIRTUAL_WORK_AREA.height() * 0.92),
        )
        sizes = [self._fit_tab(index) for index in range(self.dialog.tabs.count())]

        self.assertEqual(len(sizes), 4)
        for size in sizes:
            self.assertLessEqual(size.width(), maximum.width())
            self.assertLessEqual(size.height(), maximum.height())
            self.assertGreaterEqual(size.width(), 560)
            self.assertGreaterEqual(size.height(), 360)

        visual, role, identity, branding = sizes
        self.assertGreater(role.width(), visual.width())
        self.assertGreater(identity.width(), visual.width())
        self.assertGreater(visual.width(), branding.width() - 140)

    def test_role_branding_role_switch_shrinks_and_restores_natural_size(self):
        role_first = self._fit_tab(1)
        branding = self._fit_tab(3)
        role_second = self._fit_tab(1)

        self.assertEqual(role_first, role_second)
        self.assertLess(branding.width(), role_first.width())

    def test_expanding_institution_units_grows_and_collapse_restores_dialog_height(self):
        editor = self.dialog._branding_editor.units_editor
        self.assertIsNotNone(editor)
        for index in range(3):
            editor.service.create(
                f"resize_test_{index:02d}",
                f"Resize Test Unit {index:02d}",
                actor="Admin",
                authorized=True,
            )
        self.assertTrue(editor.refresh())

        self._fit_tab(3)
        self.dialog.show()
        self.app.processEvents()
        collapsed_size = self.dialog.size()
        self.assertFalse(editor.content.isVisible())

        with patch.object(
            self.dialog,
            "_available_screen_geometry",
            return_value=VIRTUAL_WORK_AREA,
        ):
            editor.toggle.setChecked(True)
            self.app.processEvents()
            self.app.processEvents()

            expanded_size = self.dialog.size()
            self.assertGreater(expanded_size.height(), collapsed_size.height())
            self.assertTrue(editor.content.isVisibleTo(self.dialog))
            required_table_height = (
                editor.table.horizontalHeader().height()
                + sum(editor.table.rowHeight(row) for row in range(3))
                + 2 * editor.table.frameWidth()
            )
            self.assertGreaterEqual(editor.table.height(), required_table_height)

            controls = (
                editor.table,
                editor.id_edit,
                editor.name_edit,
                editor.new_button,
                editor.save_button,
                editor.archive_button,
                editor.delete_button,
            )
            for widget in controls:
                self.assertTrue(widget.isVisibleTo(self.dialog))
                top_left = widget.mapTo(self.dialog, QPoint(0, 0))
                bounds = QRect(top_left, widget.size())
                self.assertTrue(self.dialog.rect().contains(bounds))

            editor.toggle.setChecked(False)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(self.dialog.height(), collapsed_size.height())

            self.dialog.resize(collapsed_size.width(), expanded_size.height())
            self.app.processEvents()
            already_fits_height = self.dialog.height()
            editor.toggle.setChecked(True)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(self.dialog.height(), already_fits_height)
            editor.toggle.setChecked(False)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(self.dialog.height(), already_fits_height)

            # Restore the original compact height before checking another
            # expand/collapse cycle. A user-selected taller window must stay
            # tall when the section is collapsed.
            self.dialog.resize(collapsed_size)
            self.app.processEvents()
            compact_cycle_size = self.dialog.size()

            editor.toggle.setChecked(True)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(self.dialog.height(), expanded_size.height())
            editor.toggle.setChecked(False)
            self.app.processEvents()
            self.app.processEvents()
            self.assertEqual(self.dialog.size(), compact_cycle_size)

    def test_narrow_work_area_reflows_identity_and_keeps_actions_reachable(self):
        narrow = QRect(0, 0, 800, 600)
        size = self._fit_tab(2, narrow)

        self.assertLessEqual(size.width(), int(narrow.width() * 0.92))
        self.assertLessEqual(size.height(), int(narrow.height() * 0.92))
        self.assertEqual(self.dialog._identity_component_columns, 3)
        self.assertTrue(self.dialog._identity_scroll.widgetResizable())
        self.assertTrue(self.dialog._dialog_button_box.isVisibleTo(self.dialog))

    def test_role_setup_is_compact_and_hides_stored_icon_values(self):
        self._fit_tab(1)
        self.assertEqual(self.dialog.role_table.columnWidth(2), 230)
        self.assertEqual(self.dialog.role_table.columnWidth(7), 165)
        self.assertEqual(self.dialog.role_table.columnWidth(8), 165)
        for row in range(self.dialog.role_table.rowCount()):
            self.assertEqual(self.dialog.role_table.item(row, 2).text(), "")
            reset = self.dialog.role_table.cellWidget(row, 3).findChild(
                QPushButton, "roleColorResetButton"
            )
            self.assertIsNotNone(reset)
            self.assertEqual(reset.text(), "")
            self.assertFalse(reset.icon().isNull())
        unknown_row = next(
            row for row in range(self.dialog.role_table.rowCount())
            if self.dialog.role_table.item(row, 5).text() == "unknown"
        )
        unknown_cell = self.dialog.role_table.cellWidget(unknown_row, 2)
        self.assertEqual(
            unknown_cell.findChild(QPushButton, "roleIconPickerButton").text(),
            "",
        )
        self.assertEqual(
            unknown_cell.findChild(
                self.module.QLineEdit, "roleRepresentationTextEdit"
            ).text(),
            "●",
        )

        # Egg-cell donor and surrogate currently share the same block recipe.
        # Their own field preset must still disambiguate the visible selection.
        role_rows = {
            self.dialog.role_table.item(row, 5).text(): row
            for row in range(self.dialog.role_table.rowCount())
        }
        for role_value, expected_preset in (
            ("egg_cell_donor", "egg_cell_donor"),
            ("surrogate", "surrogate"),
        ):
            row = role_rows[role_value]
            for column in (7, 8):
                selected = self.dialog.role_table.cellWidget(row, column).currentData()
                self.assertEqual(selected["preset"], expected_preset)

    def test_identity_sections_are_full_width_collapsible_and_id_row_is_equal(self):
        # Select the small-screen witness explicitly. Previously this test
        # fitted 1366px and relied on a redundant queued callback resetting it
        # to the real 800px offscreen screen, an accidental scheduling order.
        small_area = QRect(0, 0, 800, 600)
        with patch.object(self.dialog, "_available_screen_geometry", return_value=small_area):
            self._fit_tab(2, area=small_area)
        sections = self.dialog._identity_sections
        self.assertEqual(
            set(sections),
            {
                "id_pattern", "species", "animal_origins", "genotypes",
                "experiment_exit_reasons", "death_causes",
                "departure_reasons", "handover_recipients",
            },
        )
        self.assertTrue(sections["id_pattern"].is_expanded())
        self.assertTrue(all(
            not section.is_expanded()
            for key, section in sections.items() if key != "id_pattern"
        ))
        # Both supported reflows remain mandatory, independently of queue order.
        self.assertEqual(self.dialog._identity_component_columns, 3)
        widths = [combo.width() for combo in self.dialog._id_component_combos]
        self.assertLessEqual(max(widths) - min(widths), 1)
        sections["species"].set_expanded(True)
        self.app.processEvents()
        self.assertTrue(sections["species"].is_expanded())
        self.assertFalse(sections["species"]._button.icon().isNull())
        with patch.object(self.dialog, "_available_screen_geometry", return_value=VIRTUAL_WORK_AREA):
            self._fit_tab(2, area=VIRTUAL_WORK_AREA)
        self.assertEqual(self.dialog._identity_component_columns, 5)
        widths = [combo.width() for combo in self.dialog._id_component_combos]
        self.assertLessEqual(max(widths) - min(widths), 1)

    def test_branding_reflows_remove_button_and_previews_both_edges(self):
        self._fit_tab(3)
        editor = self.dialog._branding_editor
        editor._set_logo_controls_compact(False)
        self.assertFalse(editor._logo_controls_compact)
        self.assertGreaterEqual(editor.remove_button.width(), editor.remove_button.sizeHint().width())
        editor._set_logo_controls_compact(True)
        self.assertTrue(editor._logo_controls_compact)
        editor.position_left.setChecked(True)
        self.app.processEvents()
        self.assertEqual(editor.preview._position, "top_left")
        editor.position_right.setChecked(True)
        self.app.processEvents()
        self.assertEqual(editor.preview._position, "top_right")

    def test_installed_geometry_guard_clamps_complete_frame_to_real_screen(self):
        self.dialog.tabs.setCurrentIndex(0)
        self.dialog.setGeometry(-500, -500, 1800, 1200)
        self.dialog.show()
        for _ in range(4):
            self.app.processEvents()
        guard = getattr(self.dialog, "_progtrack_geometry_guard", None)
        self.assertIsNotNone(guard)
        guard.clamp()
        available = self.app.primaryScreen().availableGeometry()
        frame = self.dialog.frameGeometry()
        self.assertGreaterEqual(frame.left(), available.left())
        self.assertGreaterEqual(frame.top(), available.top())
        self.assertLessEqual(frame.right(), available.right())
        self.assertLessEqual(frame.bottom(), available.bottom())


def _activate_layouts(widget) -> None:
    # Hidden offscreen widgets do not receive the normal native show/layout
    # passes.  Iterate from the outer shell through every child layout so the
    # rendered geometry matches a shown dialog rather than constructor-time
    # placeholder rectangles.
    for _pass in range(3):
        if widget.layout() is not None:
            widget.layout().activate()
        for layout in widget.findChildren(QLayout):
            layout.activate()


def _dialog_image(dialog, target: QSize) -> QImage:
    dialog.resize(target)
    dialog.ensurePolished()
    _activate_layouts(dialog)
    QApplication.processEvents()
    if dialog.isVisible():
        return dialog.grab().toImage()
    image = QImage(target, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#f3f5f7"))
    painter = QPainter(image)
    dialog.render(painter, QPoint(0, 0))
    painter.end()
    return image


def render_evidence(output: Path) -> dict:
    """Render deterministic EN evidence on a virtual 1366x768 work area."""
    app = QApplication.instance() or QApplication([])
    _install_offscreen_font(app)
    spec = importlib.util.spec_from_file_location(
        "progtrack_conventions_evidence", ROOT / "ProgTrack.v.0.3.0.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    messages = json.loads(
        (ROOT / "lang" / "messages_en.json").read_text(encoding="utf-8")
    )
    parent = _Parent()
    dialog = module.StyleSettingsDialog(parent, messages)
    # Make Qt perform the same recursive layout pass as a native window while
    # keeping the evidence headless.  Hold the real-screen guard during the
    # virtual 1366px captures; its behavior is covered separately against the
    # actual offscreen work area.
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    dialog.show()
    for _pass in range(3):
        app.processEvents()
    guard = dialog._progtrack_geometry_guard
    guard._busy = True
    dialog._tab_fit_pending = True
    output.mkdir(parents=True, exist_ok=True)
    report = {"virtual_work_area": [1366, 768], "tabs": {}}
    captures = {}
    slugs = ("visual_style", "role_setup", "identity_lifecycle", "branding")
    for index, slug in enumerate(slugs):
        dialog.tabs.setCurrentIndex(index)
        app.processEvents()
        target = dialog._fit_current_tab(VIRTUAL_WORK_AREA, apply_guard=False)
        app.processEvents()
        capture = _dialog_image(dialog, target)
        captures[slug] = capture
        canvas = QImage(1366, 768, QImage.Format.Format_ARGB32_Premultiplied)
        canvas.fill(QColor("#dfe4e8"))
        painter = QPainter(canvas)
        x = (canvas.width() - capture.width()) // 2
        y = (canvas.height() - capture.height()) // 2
        painter.drawImage(x, y, capture)
        painter.end()
        filename = f"{index + 1:02d}_{slug}_en_1366x768.png"
        canvas.save(str(output / filename))
        report["tabs"][slug] = {
            "target": [target.width(), target.height()],
            "file": filename,
        }

    sequence = []
    for index, name in ((1, "role_setup"), (3, "branding"), (1, "role_setup")):
        dialog.tabs.setCurrentIndex(index)
        app.processEvents()
        target = dialog._fit_current_tab(VIRTUAL_WORK_AREA, apply_guard=False)
        app.processEvents()
        sequence.append((name, QSize(target), _dialog_image(dialog, target)))
    comparison = QImage(1366, 768, QImage.Format.Format_ARGB32_Premultiplied)
    comparison.fill(QColor("#dfe4e8"))
    painter = QPainter(comparison)
    painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
    column_width = comparison.width() // 3
    for column, (name, size, image) in enumerate(sequence):
        label = f"{name.replace('_', ' ').title()}  {size.width()} x {size.height()}"
        painter.drawText(
            QRect(column * column_width, 12, column_width, 32),
            Qt.AlignmentFlag.AlignCenter,
            label,
        )
        scaled = image.scaled(
            column_width - 24,
            comparison.height() - 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = column * column_width + (column_width - scaled.width()) // 2
        y = 52 + (comparison.height() - 64 - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
    painter.end()
    comparison_name = "05_role_branding_role_size_change_en_1366x768.png"
    comparison.save(str(output / comparison_name))
    report["role_branding_role"] = [
        {"tab": name, "target": [size.width(), size.height()]}
        for name, size, _image in sequence
    ]
    report["comparison_file"] = comparison_name

    # Show one expanded catalogue and both centralized branding positions;
    # these states are otherwise hidden by the compact default presentation.
    dialog.tabs.setCurrentIndex(2)
    dialog._identity_sections["species"].set_expanded(True)
    app.processEvents()
    target = dialog._fit_current_tab(VIRTUAL_WORK_AREA, apply_guard=False)
    identity_expanded = _dialog_image(dialog, target)
    identity_name = "06_identity_species_expanded_en_1366x768.png"
    identity_canvas = QImage(1366, 768, QImage.Format.Format_ARGB32_Premultiplied)
    identity_canvas.fill(QColor("#dfe4e8"))
    painter = QPainter(identity_canvas)
    painter.drawImage(
        (identity_canvas.width() - identity_expanded.width()) // 2,
        (identity_canvas.height() - identity_expanded.height()) // 2,
        identity_expanded,
    )
    painter.end()
    identity_canvas.save(str(output / identity_name))
    report["identity_expanded_file"] = identity_name

    dialog.tabs.setCurrentIndex(3)
    target = dialog._fit_current_tab(VIRTUAL_WORK_AREA, apply_guard=False)
    brand_images = []
    for position, radio in (
        ("top_left", dialog._branding_editor.position_left),
        ("top_right", dialog._branding_editor.position_right),
    ):
        radio.setChecked(True)
        app.processEvents()
        brand_images.append((position, _dialog_image(dialog, target)))
    brand_compare = QImage(1366, 768, QImage.Format.Format_ARGB32_Premultiplied)
    brand_compare.fill(QColor("#dfe4e8"))
    painter = QPainter(brand_compare)
    half = brand_compare.width() // 2
    painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
    for index, (position, capture) in enumerate(brand_images):
        painter.drawText(
            QRect(index * half, 12, half, 32),
            Qt.AlignmentFlag.AlignCenter,
            position.replace("_", " ").title(),
        )
        scaled = capture.scaled(
            half - 30,
            brand_compare.height() - 64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        painter.drawImage(
            index * half + (half - scaled.width()) // 2,
            52 + (brand_compare.height() - 64 - scaled.height()) // 2,
            scaled,
        )
    painter.end()
    branding_positions_name = "07_branding_positions_en_1366x768.png"
    brand_compare.save(str(output / branding_positions_name))
    report["branding_positions_file"] = branding_positions_name
    (output / "geometry_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    dialog.close()
    guard._busy = False
    parent.close()
    dialog.deleteLater()
    parent.deleteLater()
    app.processEvents()
    return report


if __name__ == "__main__":
    if "--render-evidence" in sys.argv:
        evidence_root = (
            ROOT
            / "tests"
            / "_phase2b_closure_evidence_2026-08-09"
            / "issue61_conventions_responsive"
        )
        print(json.dumps(render_evidence(evidence_root), indent=2))
    else:
        unittest.main()
