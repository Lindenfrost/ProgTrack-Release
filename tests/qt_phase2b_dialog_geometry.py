"""Phase 2B focused smoke tests (local tests are intentionally git-ignored)."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QMainWindow,
    QLineEdit,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from Plugins.Cage__Track.cage_track_widget import (
    InspectionDialog,
    InspectionPDFExportDialog,
)
from Plugins.core.dialog_geometry import install_dialog_geometry_guard
from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths


class Phase2BDialogGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_guard_rejects_too_small_resize(self):
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("header"))
        install_dialog_geometry_guard(dialog)
        dialog.show()
        self.app.processEvents()
        dialog.resize(1, 1)
        self.app.processEvents()
        self.assertGreaterEqual(dialog.width(), dialog.minimumWidth())
        self.assertGreaterEqual(dialog.height(), dialog.minimumHeight())
        dialog.close()

    def test_guard_keeps_native_frame_and_header_inside_work_area(self):
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("New Animal / Edit Animal header"))
        install_dialog_geometry_guard(dialog)
        dialog.setGeometry(-500, -500, 1500, 1200)
        dialog.show()
        self.app.processEvents()
        self.app.processEvents()
        available = self.app.primaryScreen().availableGeometry()
        frame = dialog.frameGeometry()
        self.assertGreaterEqual(frame.left(), available.left())
        self.assertGreaterEqual(frame.top(), available.top())
        self.assertLessEqual(frame.right(), available.right())
        self.assertLessEqual(frame.bottom(), available.bottom())
        dialog.close()

    def test_standard_new_edit_shell_and_all_tab_selector_use_guard(self):
        # Exercise the shared shell used by every role-specific New/Edit Animal
        # dialog, and keep the small All-tab selector covered by a source
        # contract because opening it is modal.
        fake_parent = QMainWindow()
        module_path = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        source = module_path.read_text(encoding="utf-8")
        self.assertIn("install_dialog_geometry_guard(dlg, minimum=QSize(w, 220))", source)
        self.assertIn("def _dlg_new_animal_with_role_selector", source)
        dialog = QDialog(fake_parent)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Edit Animal"))
        install_dialog_geometry_guard(dialog, minimum=QSize(620, 260))
        dialog.show()
        self.app.processEvents()
        dialog.resize(1, 1)
        self.app.processEvents()
        self.assertGreaterEqual(dialog.width(), dialog.minimumWidth())
        self.assertGreaterEqual(dialog.height(), dialog.minimumHeight())
        dialog.close()
        fake_parent.close()

    def test_every_role_specific_new_edit_dialog_uses_the_shared_guard(self):
        source = (
            Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        ).read_text(encoding="utf-8")
        functions = (
            "_dlg_partner",
            "_dlg_samenspender",
            "_dlg_offspring",
            "_dlg_zuchttier",
            "_dlg_versuchstier",
            "_dlg_basic_animal_role",
            "_dlg_female_animal",
        )
        for index, name in enumerate(functions):
            with self.subTest(dialog=name):
                start = source.index(f"    def {name}(")
                next_def = source.find("\n    def ", start + 8)
                block = source[start: next_def if next_def >= 0 else len(source)]
                self.assertIn("self._apply_dialog_width(dlg)", block)

    def test_measurement_file_dialog_titles_are_localized(self):
        source = (
            Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        ).read_text(encoding="utf-8")
        for kind in ("blood", "urine", "weight", "sperm"):
            self.assertIn(f'"import.file.{kind}.title"', source)
        self.assertIn('"file.filter.excel"', source)

    def test_cage_dialogs_keep_primary_controls_reachable(self):
        dialogs = (
            InspectionDialog(None, {}, [], lambda: None),
            InspectionPDFExportDialog(None, {}, []),
        )
        for dialog in dialogs:
            with self.subTest(dialog=type(dialog).__name__):
                dialog.show()
                self.app.processEvents()
                dialog.resize(1, 1)
                self.app.processEvents()
                self.assertGreaterEqual(dialog.width(), dialog.minimumWidth())
                self.assertGreaterEqual(dialog.height(), dialog.minimumHeight())
                dialog.close()

    def test_lazy_plugin_modules_import_without_missing_qt_symbols(self):
        for module_name in (
            "Plugins.Cage__Track.cage_track_widget",
            "Plugins.Flow_Track.flow_track_widget",
        ):
            with self.subTest(module=module_name):
                self.assertIsNotNone(importlib.import_module(module_name))

    def test_animal_dialog_shared_fit_expands_and_compacts_for_active_tab(self):
        module_path = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        spec = importlib.util.spec_from_file_location("progtrack_dialog_fit", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        host = module.ProgTrackApp.__new__(module.ProgTrackApp)

        dialog = QDialog()
        outer = QVBoxLayout(dialog)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.addWidget(QLabel("Animal identity"))
        form_scroll = QScrollArea()
        form_scroll.setObjectName("animalDialogFormScroll")
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(body)
        outer.addWidget(form_scroll)

        tabs = QTabWidget()
        compact = QWidget()
        compact.setLayout(QVBoxLayout())
        compact.layout().addWidget(QLabel("Five compact history rows"))
        wide = QWidget()
        wide.setLayout(QVBoxLayout())
        wide_label = QLabel("W" * 110)
        wide_label.setMinimumWidth(850)
        wide.layout().addWidget(wide_label)
        tabs.addTab(compact, "Weights")
        tabs.addTab(wide, "Events")
        outer.addWidget(tabs)
        host._register_animal_dialog_tabs(dialog, tabs)
        dialog.show()
        self.app.processEvents()
        compact_width = dialog.width()
        tabs.setCurrentIndex(1)
        self.app.processEvents()
        expanded_width = dialog.width()
        tabs.setCurrentIndex(0)
        self.app.processEvents()
        recompacted_width = dialog.width()
        self.assertGreater(expanded_width, compact_width)
        self.assertLess(recompacted_width, expanded_width)
        evidence = Path(r"Q:\GitHub\Issue Tracker\Screenshots\issue-84-focused-dialog-resize.png")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(evidence))
        dialog.close()

    def test_animal_dialog_fit_uses_active_page_on_open_and_switch(self):
        module_path = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        spec = importlib.util.spec_from_file_location("progtrack_active_tab_fit", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        host = module.ProgTrackApp.__new__(module.ProgTrackApp)

        dialog = QDialog()
        outer = QVBoxLayout(dialog)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.addWidget(QLabel("Address / limits / parents"))
        form_scroll = QScrollArea()
        form_scroll.setObjectName("animalDialogFormScroll")
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(body)
        outer.addWidget(form_scroll, 1)

        tabs = QTabWidget()
        short_page = QWidget()
        short_layout = QVBoxLayout(short_page)
        short_layout.addWidget(QLabel("One sperm measurement"))
        long_page = QWidget()
        long_layout = QVBoxLayout(long_page)
        for index in range(12):
            long_layout.addWidget(QLineEdit(f"Weight {index}"))
        # Add the short page first to reproduce the Sperm Donor cold-start
        # order while keeping the longer inactive page in the tab hint.
        tabs.addTab(short_page, "Sperm Measurements")
        tabs.addTab(long_page, "Weights")
        outer.addWidget(tabs, 1)
        outer.addWidget(QLabel("Save"))

        host._register_animal_dialog_tabs(dialog, tabs)
        dialog.show()
        self.app.processEvents()
        self.app.processEvents()
        short_dialog_height = dialog.height()
        short_form_height = form_scroll.height()
        short_tabs_height = tabs.height()
        self.assertEqual(tabs.property("_progtrack_active_tab_height"), tabs.height())
        self.assertLess(short_tabs_height, tabs.sizeHint().height())

        tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.app.processEvents()
        long_dialog_height = dialog.height()
        long_tabs_height = tabs.height()
        self.assertGreater(long_tabs_height, short_tabs_height)
        self.assertGreater(long_dialog_height, short_dialog_height)

        tabs.setCurrentIndex(0)
        self.app.processEvents()
        self.app.processEvents()
        self.assertLessEqual(abs(dialog.height() - short_dialog_height), 4)
        self.assertLessEqual(abs(form_scroll.height() - short_form_height), 4)
        self.assertLessEqual(abs(tabs.height() - short_tabs_height), 4)
        dialog.close()

    def test_animal_dialog_fit_reserves_upper_form_before_history_tabs(self):
        """Expanded identity/section content must get a viewport before tabs.

        This reproduces the reported case with a populated upper form.  The
        body deliberately needs more than the old fixed viewport, while the
        history tab remains bounded.  The shared fit must reserve the upper
        form's natural height when the screen allows it and must keep the two
        vertical areas disjoint when the screen does not.
        """
        module_path = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        spec = importlib.util.spec_from_file_location("progtrack_dialog_fit_upper", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        host = module.ProgTrackApp.__new__(module.ProgTrackApp)

        dialog = QDialog()
        outer = QVBoxLayout(dialog)
        body = QWidget()
        body.setMinimumHeight(420)
        body_layout = QVBoxLayout(body)
        body_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(QLabel("Address / Parents / Health status"))
        form_scroll = QScrollArea()
        form_scroll.setObjectName("animalDialogFormScroll")
        form_scroll.setWidgetResizable(True)
        form_scroll.setWidget(body)
        outer.addWidget(form_scroll, 1)

        tabs = QTabWidget()
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(QLabel("Five bounded history rows"))
        page.setMinimumHeight(120)
        tabs.addTab(page, "Weights")
        outer.addWidget(tabs, 1)
        outer.addWidget(QLabel("Save"))

        host._register_animal_dialog_tabs(dialog, tabs)
        # Role builders finalize their shell with this helper before the
        # deferred content-fit callback runs; keep that ordering covered.
        host._apply_dialog_width(dialog)
        dialog.show()
        self.app.processEvents()
        self.app.processEvents()

        # The upper form must not be squeezed back to the old default height
        # while the tab is visible.  On a short screen it may be capped, but
        # the VBox geometry must still remain non-overlapping and scrollable.
        self.assertGreaterEqual(
            form_scroll.height(), min(420, form_scroll.maximumHeight()),
            f"form={form_scroll.height()} min={form_scroll.minimumHeight()} "
            f"body={body.sizeHint().height()} dlg={dialog.size()} "
            f"tabs={tabs.geometry()} prop={dialog.property('_progtrack_content_height')}",
        )
        self.assertGreaterEqual(tabs.geometry().top(), form_scroll.geometry().bottom() + 1)
        self.assertFalse(form_scroll.geometry().intersects(tabs.geometry()))
        self.assertGreaterEqual(dialog.property("_progtrack_content_height"), dialog.minimumHeight())
        evidence = Path(r"Q:\GitHub\Issue Tracker\Screenshots\issue-84-content-aware-height.png")
        evidence.parent.mkdir(parents=True, exist_ok=True)
        dialog.grab().save(str(evidence))
        dialog.close()

    def test_new_animal_id_preview_updates_components_and_respects_override(self):
        module_path = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        spec = importlib.util.spec_from_file_location("progtrack_id_preview", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        host = module.ProgTrackApp.__new__(module.ProgTrackApp)
        module.QMainWindow.__init__(host)
        host.animals = {}
        host.archived = {}
        host._master_can = lambda _permission: True
        temporary = tempfile.TemporaryDirectory(prefix="progtrack-id-preview-")
        self.addCleanup(temporary.cleanup)
        host.backend = ProgTrackBackend(
            runtime_paths(Path(temporary.name)),
            acquire_process_lock=False,
            choose_demo_seed=lambda: False,
        )
        self.addCleanup(host.backend.close)
        session = host.backend.authenticated_sessions.create_first_account(
            "id-preview-admin", "Id-preview-test!"
        )
        host.master_track = type("MasterTrack", (), {"session_context": session})()
        host.backend.core_configuration.setup(session)
        host._core_configuration_missing = False
        host._core_configuration_generation = 0
        host._publish_core_configuration(host.backend.core_configuration.snapshot(), redraw=False)
        host._load_identity_conventions = lambda: host.backend.records.get(
            "configuration", "identity-lifecycle-conventions", default={}
        )

        id_widget = QLineEdit()
        name_widget = QLineEdit()
        species_widget = QComboBox()
        species_widget.addItem("Callitrix jacchus", "Callitrix jacchus")
        birth_widget = QLineEdit()
        sex_widget = QComboBox()
        sex_widget.addItem("Unknown", "Unknown")
        sex_widget.addItem("Male", "Male")
        origin_widget = QComboBox()
        origin_widget.addItem("DPZ", "DPZ")

        host._install_new_animal_id_preview(
            id_widget,
            name_widget=name_widget,
            species_widget=species_widget,
            birth_widget=birth_widget,
            sex_widget=sex_widget,
            origin_widget=origin_widget,
            editing=False,
        )
        self.assertIn("XX", id_widget.text())
        name_widget.setText("Luna")
        birth_widget.setText("01.01.2025")
        sex_widget.setCurrentIndex(1)
        self.assertIn("cj_25_", id_widget.text())
        self.assertIn("_M_Luna_DPZ", id_widget.text())

        generated, metadata = host._id_for_animal_dialog(
            id_widget,
            name="Luna",
            species="Callitrix jacchus",
            birth_date="01.01.2025",
            sex="Male",
            origin="DPZ",
            editing=False,
        )
        self.assertEqual(generated, id_widget.text())
        self.assertIsNotNone(metadata)

        id_widget.setText("manual-id")
        id_widget.textEdited.emit("manual-id")
        name_widget.setText("Changed")
        self.assertEqual(id_widget.text(), "manual-id")

    def test_new_animal_id_preview_is_wired_into_every_role_builder(self):
        source = (
            Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
        ).read_text(encoding="utf-8")
        for function in (
            "_dlg_partner", "_dlg_samenspender", "_dlg_offspring",
            "_dlg_zuchttier", "_dlg_versuchstier", "_dlg_female_animal",
            "_dlg_basic_animal_role",
        ):
            with self.subTest(function=function):
                start = source.index(f"    def {function}(")
                next_def = source.find("\n    def ", start + 8)
                block = source[start: next_def if next_def >= 0 else len(source)]
                self.assertIn("_install_new_animal_id_preview(", block)


if __name__ == "__main__":
    unittest.main()
