"""Offscreen and filesystem regression tests for Medi Track batch export."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from openpyxl import load_workbook
from pypdf import PdfReader
from PyQt6.QtWidgets import QApplication

from Plugins.Medi_Track.medi_track_widget import (
    BatchExportFailure,
    BatchExportOutcome,
    BatchExportProgressDialog,
    MediTrackWidget,
    batch_export_summary,
)


ROOT = Path(__file__).resolve().parents[1]


class _Store:
    backend = None

    def __init__(self, document: Path | None = None):
        self.document = document

    def get_documents(self, _animal_id):
        return [{"path": str(self.document)}] if self.document else []

    def get_entries(self, _animal_id):
        return []

    def get_active_issues(self, _animal_id, _status_type):
        return []


class _Harness:
    def __init__(self, animals, messages, document=None):
        self.app = SimpleNamespace(
            animals={animal: {"name": animal.split(" | ", 1)[0]} for animal in animals}
        )
        self.messages = messages
        self._lang = "en"
        self.store = _Store(document)
        self.fail = set()
        self.real_pdf = False
        self.allowed = set(animals)

    def _permission_scope_export_ids(self, requested):
        return [item for item in requested if item in self.allowed]

    def _export_animal_to_pdf(self, animal_id, output_path, **_kwargs):
        if self.real_pdf:
            return MediTrackWidget._export_animal_to_pdf(
                self, animal_id, output_path, **_kwargs
            )
        Path(output_path).write_bytes(b"%PDF-1.4\nvalid\n%%EOF\n")
        if animal_id in self.fail:
            raise RuntimeError("injected item failure")

    def _entry_type_label(self, entry_type, status_type=""):
        return entry_type or status_type

    def _export_animal_to_xlsx(self, *args, **kwargs):
        return MediTrackWidget._export_animal_to_xlsx(self, *args, **kwargs)

    def _atomic_export_animal(self, *args, **kwargs):
        return MediTrackWidget._atomic_export_animal(self, *args, **kwargs)

    def export_batch(self, *args, **kwargs):
        return MediTrackWidget._export_animals_to_directory_batch(
            self, *args, **kwargs
        )

    def _export_animals_to_directory_batch(self, *args, **kwargs):
        return self.export_batch(*args, **kwargs)

    def export_lightweight(self, *args, **kwargs):
        return MediTrackWidget._export_animals_to_directory(
            self, *args, **kwargs
        )


class _CancelOnThird:
    def __init__(self):
        self.cancelled = False
        self.started = []
        self.finished = []

    def was_cancelled(self):
        return self.cancelled

    def begin_item(self, label, number):
        self.started.append((label, number))
        if number == 3:
            self.cancelled = True

    def finish_item(self, number, *, success=True):
        self.finished.append((number, success))


class MediBatchExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.messages = json.loads(
            (ROOT / "lang" / "messages_en.json").read_text(encoding="utf-8")
        )

    def test_progress_is_determinate_names_current_item_and_cancels(self):
        dialog = BatchExportProgressDialog(None, total=4, messages=self.messages)
        self.assertEqual((dialog.progress_bar.minimum(), dialog.progress_bar.maximum()), (0, 4))
        dialog.begin_item("Arwen", 2)
        self.assertEqual(dialog.progress_bar.value(), 1)
        self.assertIn("Arwen", dialog.status_label.text())
        self.assertIn("2/4", dialog.status_label.text())
        dialog.finish_item(2)
        self.assertEqual(dialog.progress_bar.value(), 2)
        self.assertEqual(dialog.completed_count, 1)
        dialog.request_cancel()
        self.assertTrue(dialog.was_cancelled())
        self.assertFalse(dialog.cancel_button.isEnabled())
        self.assertEqual(dialog.status_label.text(), self.messages["export.cancelling"])
        dialog.close()

    def test_failure_then_cancel_keeps_only_complete_valid_files(self):
        animals = [
            "Arwen | Callitrix jacchus | 23.02.2015 | DPZ",
            "Elrond | Callitrix jacchus | 04.02.2012 | DPZ",
            "Eldarion | Callitrix jacchus | 01.03.2026 | DPZ",
            "Taylor | Callitrix jacchus | 01.01.2018 | DPZ",
        ]
        harness = _Harness(animals, self.messages)
        harness.fail.add(animals[1])
        progress = _CancelOnThird()
        with tempfile.TemporaryDirectory() as folder:
            outcome = harness.export_batch(
                animals,
                folder,
                export_format="PDF",
                include_signatures=True,
                include_documents=False,
                progress=progress,
            )
            self.assertTrue(outcome.cancelled)
            self.assertEqual(outcome.total, 4)
            self.assertEqual(outcome.completed_count, 1)
            self.assertEqual(outcome.failed_count, 1)
            self.assertEqual(progress.finished, [(1, True), (2, False)])
            outputs = list(Path(folder).glob("*.pdf"))
            self.assertEqual(len(outputs), 1)
            self.assertTrue(outputs[0].read_bytes().startswith(b"%PDF-"))
            self.assertTrue(outputs[0].read_bytes().rstrip().endswith(b"%%EOF"))
            self.assertEqual(list(Path(folder).glob(".progtrack-export-*")), [])

            severity, _title, body = batch_export_summary(
                self.messages, outcome, folder
            )
            self.assertEqual(severity, "information")
            self.assertIn("1 of 4", body)
            self.assertIn(Path(outcome.completed_paths[0]).name, body)
            self.assertIn("Elrond", body)
            self.assertIn("injected item failure", body)

    def test_xlsx_and_document_sidecar_are_finalized_with_valid_links(self):
        animal = "Arwen | Callitrix jacchus | 23.02.2015 | DPZ"
        with tempfile.TemporaryDirectory() as source_folder, tempfile.TemporaryDirectory() as output:
            document = Path(source_folder) / "clinical-note.txt"
            document.write_text("valid document", encoding="utf-8")
            harness = _Harness([animal], self.messages, document=document)
            outcome = harness.export_batch(
                [animal],
                output,
                export_format="XLSX",
                include_signatures=False,
                include_documents=True,
            )
            self.assertFalse(outcome.cancelled)
            self.assertEqual((outcome.completed_count, outcome.failed_count), (1, 0))
            workbook_path = Path(outcome.completed_paths[0])
            workbook = load_workbook(workbook_path)
            self.assertEqual(workbook.sheetnames, ["Medical history"])
            document_dir = workbook_path.parent / f"{workbook_path.stem}_documents"
            copied = document_dir / document.name
            self.assertTrue(copied.is_file())
            self.assertEqual(copied.read_text(encoding="utf-8"), "valid document")
            linked_cells = [
                cell
                for row in workbook["Medical history"].iter_rows()
                for cell in row
                if cell.hyperlink is not None
            ]
            self.assertEqual(len(linked_cells), 1)
            self.assertEqual(
                Path(linked_cells[0].hyperlink.target),
                Path(document_dir.name) / document.name,
            )

            # Re-export keeps the existing sidecar document and selects the
            # same collision-safe suffix the direct exporter used before the
            # atomic staging layer was added.
            second = harness.export_batch(
                [animal],
                output,
                export_format="XLSX",
                include_signatures=False,
                include_documents=True,
            )
            workbook = load_workbook(second.completed_paths[0])
            linked_cells = [
                cell
                for row in workbook["Medical history"].iter_rows()
                for cell in row
                if cell.hyperlink is not None
            ]
            self.assertEqual(
                Path(linked_cells[0].hyperlink.target),
                Path(document_dir.name) / "clinical-note_1.txt",
            )
            self.assertTrue((document_dir / "clinical-note_1.txt").is_file())
            self.assertEqual(list(Path(output).glob(".progtrack-export-*")), [])

    def test_real_offscreen_medi_pdfs_are_readable_after_atomic_publish(self):
        animals = [
            "Arwen | Callitrix jacchus | 23.02.2015 | DPZ",
            "Elrond | Callitrix jacchus | 04.02.2012 | DPZ",
        ]
        harness = _Harness(animals, self.messages)
        harness.real_pdf = True
        with tempfile.TemporaryDirectory() as output:
            outcome = harness.export_batch(
                animals,
                output,
                export_format="PDF",
                include_signatures=True,
                include_documents=False,
            )
            self.assertEqual((outcome.completed_count, outcome.failed_count), (2, 0))
            for path in outcome.completed_paths:
                reader = PdfReader(path)
                self.assertGreaterEqual(len(reader.pages), 1)
            self.assertEqual(list(Path(output).glob(".progtrack-export-*")), [])

    def test_single_export_is_lightweight_and_permission_and_destination_fail_closed(self):
        animal = "Arwen | Callitrix jacchus | 23.02.2015 | DPZ"
        harness = _Harness([animal], self.messages)
        with tempfile.TemporaryDirectory() as output:
            paths = harness.export_lightweight(
                [animal],
                output,
                export_format="PDF",
                include_signatures=True,
                include_documents=False,
            )
            self.assertEqual(len(paths), 1)
            self.assertTrue(Path(paths[0]).is_file())

            harness.allowed.clear()
            with self.assertRaises(PermissionError):
                harness.export_batch(
                    [animal],
                    output,
                    export_format="PDF",
                    include_signatures=True,
                    include_documents=False,
                )

        harness.allowed.add(animal)
        with tempfile.NamedTemporaryFile() as not_a_directory:
            with self.assertRaises(NotADirectoryError):
                harness.export_batch(
                    [animal],
                    not_a_directory.name,
                    export_format="PDF",
                    include_signatures=True,
                    include_documents=False,
                )

    def test_all_locales_define_and_format_every_batch_summary(self):
        outcomes = (
            BatchExportOutcome(2, ("one", "two"), (), False),
            BatchExportOutcome(2, ("one",), (), True),
            BatchExportOutcome(
                2,
                ("one",),
                (BatchExportFailure("two", "Elrond", "injected"),),
                False,
            ),
            BatchExportOutcome(
                1,
                (),
                (BatchExportFailure("one", "Arwen", "injected"),),
                False,
            ),
        )
        for language in ("en", "de", "it", "ru"):
            messages = json.loads(
                (ROOT / "lang" / f"messages_{language}.json").read_text(encoding="utf-8")
            )
            for outcome in outcomes:
                severity, title, body = batch_export_summary(messages, outcome, "C:/Export")
                self.assertIn(severity, {"information", "warning", "critical"})
                self.assertTrue(title)
                if outcome.completed_paths or outcome.cancelled:
                    self.assertIn("C:/Export", body)


if __name__ == "__main__":
    unittest.main()
