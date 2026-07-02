import json
import tempfile
import unittest
from pathlib import Path

from Plugins.Flow_Track.flow_track_widget import FlowTrackWidget
from Plugins.core.animal_reference_rewrite import (
    move_medi_document_folder,
    rewrite_animal_reference_files,
    safe_medi_document_folder_name,
)


OLD_KEY = "Luna | Macaca mulatta | 01.01.2020"
NEW_KEY = "Luna | Macaca mulatta | 02.01.2020"


class AnimalReferenceRewriteTest(unittest.TestCase):
    def test_rewrite_covers_flow_and_sample_runtime_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            flow_dir = base / "Plugins" / "Flow_Track"
            sample_dir = base / "Plugins" / "Sample_Track"
            flow_dir.mkdir(parents=True)
            sample_dir.mkdir(parents=True)

            (flow_dir / "flowtrack_daten.json").write_text(
                json.dumps(
                    {
                        "manual_data": {
                            "sperm_donors": {OLD_KEY: {"donations": {}}},
                            "egg_donors": {OLD_KEY: {"surgeries": {}}},
                            OLD_KEY: {"transfers": {"t1": {}}},
                            "transfers_by_id": {
                                "t1": {
                                    "surrogate_name": OLD_KEY,
                                    "embryos": [
                                        {
                                            "egg_donor_name": OLD_KEY,
                                            "sperm_donor_name": OLD_KEY,
                                        }
                                    ],
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (flow_dir / "flowtrack_config.json").write_text(
                json.dumps(
                    {
                        "settings": {
                            "embryo_visibility": {OLD_KEY: True},
                            "egg_donor_surgery_visibility": {OLD_KEY: {"s1": False}},
                            "sperm_donor_donation_visibility": {OLD_KEY: {"d1": True}},
                            "surrogate_transfer_visibility": {OLD_KEY: {"t1": True}},
                        }
                    }
                ),
                encoding="utf-8",
            )
            (sample_dir / "organs.json").write_text(
                json.dumps([{"animal_name": OLD_KEY, "species": "Macaca mulatta"}]),
                encoding="utf-8",
            )
            (sample_dir / "other.json").write_text(
                json.dumps([{"animal_name": OLD_KEY, "sample_number": "S-1"}]),
                encoding="utf-8",
            )

            changed = rewrite_animal_reference_files(
                base,
                (
                    "Plugins/Flow_Track/flowtrack_daten.json",
                    "Plugins/Flow_Track/flowtrack_config.json",
                    "Plugins/Sample_Track/organs.json",
                    "Plugins/Sample_Track/other.json",
                ),
                OLD_KEY,
                NEW_KEY,
                "Luna",
            )

            self.assertEqual(changed, 4)
            for path in (
                flow_dir / "flowtrack_daten.json",
                flow_dir / "flowtrack_config.json",
                sample_dir / "organs.json",
                sample_dir / "other.json",
            ):
                text = path.read_text(encoding="utf-8")
                self.assertIn(NEW_KEY, text)
                self.assertNotIn(OLD_KEY, text)

    def test_medi_document_folder_moves_from_old_ipid_to_new_ipid(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            docs_root = base / "Plugins" / "Medi_Track" / "medi_track"
            old_folder = docs_root / safe_medi_document_folder_name(OLD_KEY)
            old_folder.mkdir(parents=True)
            (old_folder / "report.pdf").write_text("doc", encoding="utf-8")

            self.assertTrue(move_medi_document_folder(base, OLD_KEY, NEW_KEY))

            new_folder = docs_root / safe_medi_document_folder_name(NEW_KEY)
            self.assertFalse(old_folder.exists())
            self.assertTrue((new_folder / "report.pdf").is_file())

    def test_flow_embryo_id_token_uses_short_sanitized_name_not_raw_ipid(self):
        token = FlowTrackWidget._safe_identity_token("Luna | Macaca mulatta | 01.01.2020")

        self.assertEqual(token, "Luna")
        self.assertNotIn("|", token)


if __name__ == "__main__":
    unittest.main()
