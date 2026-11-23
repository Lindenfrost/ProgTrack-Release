import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SaveBackupLabelTest(unittest.TestCase):
    CATALOGS = {
        "messages_en.json": {
            "menu": "Save Backup",
            "title": "Save Backup",
            "success": "Backup successfully saved to: {path}",
            "failure": "Failed to save backup: {error}",
        },
        "messages_de.json": {
            "menu": "Backup speichern",
            "title": "Backup speichern",
            "success": "Backup erfolgreich gespeichert unter: {path}",
            "failure": "Fehler beim Speichern des Backups: {error}",
        },
        "messages_it.json": {
            "menu": "Salva backup",
            "title": "Salva backup",
            "success": "Backup salvato con successo in: {path}",
            "failure": "Salvataggio del backup non riuscito: {error}",
        },
        "messages_ru.json": {
            "menu": "Сохранить резервную копию",
            "title": "Сохранить резервную копию",
            "success": "Резервная копия успешно сохранена в: {path}",
            "failure": "Ошибка при сохранении резервной копии: {error}",
        },
    }

    def test_all_supported_languages_use_backup_terminology(self):
        for filename, expected in self.CATALOGS.items():
            with self.subTest(language=filename):
                catalog = json.loads(
                    (ROOT / "lang" / filename).read_text(encoding="utf-8")
                )
                self.assertEqual(catalog["menu.file.save_database"], expected["menu"])
                self.assertEqual(
                    catalog["file_dialog.save_database.title"], expected["title"]
                )
                self.assertEqual(
                    catalog["info.save_database.success"], expected["success"]
                )
                self.assertEqual(
                    catalog["error.save_database.failed"], expected["failure"]
                )

    def test_core_fallbacks_use_backup_wording_without_renaming_compatibility_keys(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertEqual(source.count('"menu.file.save_database", "Save Backup"'), 2)
        self.assertIn(
            '"file_dialog.save_database.title", "Save Backup"', source
        )
        self.assertIn('logging.error(f"Failed to save backup: {e}")', source)
        self.assertNotIn('"menu.file.save_database", "Save Database"', source)
        self.assertNotIn(
            '"file_dialog.save_database.title", "Save Database"', source
        )
        self.assertIn('"info.save_database.success"', source)
        self.assertIn('"error.save_database.failed"', source)

    def test_only_operation_labels_changed_not_postgresql_backup_labels(self):
        expected_titles = {
            "messages_en.json": "Save PostgreSQL backup",
            "messages_de.json": "PostgreSQL-Sicherung speichern",
            "messages_it.json": "Salva backup PostgreSQL",
            "messages_ru.json": "Сохранить резервную копию PostgreSQL",
        }
        for filename, expected in expected_titles.items():
            with self.subTest(language=filename):
                catalog = json.loads(
                    (ROOT / "lang" / filename).read_text(encoding="utf-8")
                )
                self.assertEqual(
                    catalog["backend.postgresql.backup_database.title"], expected
                )


if __name__ == "__main__":
    unittest.main()
