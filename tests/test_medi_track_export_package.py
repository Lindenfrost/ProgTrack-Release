import inspect
import tempfile
import unittest
from pathlib import Path

from Plugins.Medi_Track.medi_track_widget import (
    MediTrackWidget,
    _copy_document_files_to_directory,
    _document_paths_for_animal,
    _safe_name,
)


class _Store:
    def __init__(self, docs):
        self._docs = docs

    def get_documents(self, _animal_name):
        return list(self._docs)


class MediTrackExportPackageTest(unittest.TestCase):
    def test_document_paths_include_folder_and_json_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            animal = "Thranduil | Callitrix jacchus | 11.02.2021"
            folder = root / _safe_name(animal)
            folder.mkdir()
            folder_doc = folder / "lab.pdf"
            folder_doc.write_text("folder", encoding="utf-8")
            json_doc = root / "external_scan.pdf"
            json_doc.write_text("json", encoding="utf-8")
            store = _Store([
                {"path": str(json_doc), "title": "External scan"},
                {"path": str(folder_doc), "title": "Duplicate folder doc"},
            ])

            paths = _document_paths_for_animal(animal, store, root)

            self.assertEqual({folder_doc, json_doc}, set(paths))

    def test_document_copy_uses_destination_directory_without_overwriting(self):
        with tempfile.TemporaryDirectory() as src_tmp, tempfile.TemporaryDirectory() as dst_tmp:
            src = Path(src_tmp)
            dst = Path(dst_tmp)
            first = src / "scan.pdf"
            first.write_text("first", encoding="utf-8")
            second = src / "note.txt"
            second.write_text("second", encoding="utf-8")
            existing = dst / "scan.pdf"
            existing.write_text("existing", encoding="utf-8")

            copied = _copy_document_files_to_directory([first, second], dst)

            self.assertEqual(2, copied)
            self.assertEqual("existing", existing.read_text(encoding="utf-8"))
            self.assertEqual("first", (dst / "scan_1.pdf").read_text(encoding="utf-8"))
            self.assertEqual("second", (dst / "note.txt").read_text(encoding="utf-8"))

    def test_medi_pdf_export_does_not_include_ipid_header_row(self):
        source = inspect.getsource(MediTrackWidget._export_animal_to_pdf)

        self.assertNotIn("report.header.ipid", source)
        self.assertNotIn("_h_ipid", source)
        self.assertNotIn("_v_ipid", source)


if __name__ == "__main__":
    unittest.main()
