import unittest
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = ROOT / "Resources" / "ExampleFiles"


class Phase1ExampleImportFilesTest(unittest.TestCase):
    def _headers(self, filename):
        workbook = load_workbook(EXAMPLE_DIR / filename, read_only=True, data_only=True)
        try:
            sheet = workbook.active
            return [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
        finally:
            workbook.close()

    def test_example_import_files_match_importer_headers(self):
        expected = {
            "ProgTrack_example_blood_progesterone_import.xlsx": [
                "Name", "Animal ID", "Datum", "Progesteron (ng/ml)", "F", "Sample ID",
            ],
            "ProgTrack_example_urine_pdg_import.xlsx": [
                "Name", "Animal ID", "Datum", "PdG (µg/mg Cr)", "Sample ID",
            ],
            "ProgTrack_example_weight_import.xlsx": [
                "Name", "Animal ID", "Datum", "Gewicht", "Sample ID",
            ],
            "ProgTrack_example_sperm_import.xlsx": [
                "Datum", "Name", "Animal ID", "% Motility", "% Progressive", "Sperms/ml", "Sample ID",
            ],
        }

        for filename, headers in expected.items():
            with self.subTest(filename=filename):
                self.assertTrue((EXAMPLE_DIR / filename).exists())
                self.assertEqual(headers, self._headers(filename))


if __name__ == "__main__":
    unittest.main()
