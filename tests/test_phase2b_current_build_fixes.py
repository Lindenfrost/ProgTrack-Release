import importlib.util
import json
import os
import re
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Plugins.Heritage_Track.pedigree_router import PedigreeRouter
from Plugins.core.backend_configuration import (
    BackendConfigurationPermissionError,
    BackendConfigurationService,
    BackendConfigurationValidationError,
    PostgreSQLSettings,
)
from Plugins.core.backend.facade import ProgTrackBackend
from Plugins.core.animal_identity import animal_base_name
from Plugins.core.animal_roles import canonical_role_value
from Plugins.core.experimental_limits import (
    KNOWN_LIMIT_FIELDS,
    default_experimental_limits,
    synchronize_experimental_limits,
)
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths
from Plugins.Projects_Track.project_track_tab import _project_role_specs


ROOT = Path(__file__).resolve().parents[1]


def runtime_paths(root: Path) -> RuntimePaths:
    paths = RuntimePaths(
        application_root=ROOT,
        profile=BackendProfile.STANDALONE_SQLITE,
        data_root=root / "data",
        config_root=root / "config",
        cache_root=root / "cache",
        state_root=root / "state",
        database_path=root / "data" / "database" / "progtrack.sqlite3",
        managed_root=root / "managed",
        managed_documents=root / "managed" / "documents",
        managed_config_assets=root / "managed" / "config-assets",
        logs=root / "state" / "logs",
        runtime=root / "state" / "runtime",
        exports=root / "data" / "exports",
        preferences=root / "config" / "preferences",
        profile_file=root / "config" / "backend.json",
    )
    paths.create_mutable_roots()
    return paths


class MemoryCredentialStore:
    def __init__(self):
        self.values = {}

    def available(self):
        return True

    def read(self, target):
        return self.values.get(target, "")

    def write(self, target, secret):
        self.values[target] = secret

    def delete(self, target):
        self.values.pop(target, None)


class BackendConfigurationTest(unittest.TestCase):
    def test_sqlite_save_ignores_blank_postgresql_fields_and_persists_local_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            service = BackendConfigurationService(
                paths, environ={}, credential_store=MemoryCredentialStore()
            )
            custom_folder = Path(directory) / "local-database"
            document = service.save(
                profile=BackendProfile.STANDALONE_SQLITE,
                sqlite_filename="facility.sqlite3",
                sqlite_folder=custom_folder,
                postgresql=PostgreSQLSettings(),
                password="",
                authorized=True,
            )
            self.assertEqual(
                Path(document["sqlite_path"]),
                (custom_folder / "facility.sqlite3").resolve(),
            )
            self.assertEqual(document["profile"], "standalone_sqlite")
            self.assertNotIn("password", paths.profile_file.read_text().casefold())

    def test_backend_dialog_resizes_by_profile_and_accepts_a_local_folder_choice(self):
        from PyQt6.QtWidgets import QApplication

        from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            service = BackendConfigurationService(
                paths, environ={}, credential_store=MemoryCredentialStore()
            )
            dialog = BackendConfigurationDialog(service, {}, authorized=True)
            dialog.sqlite_radio.setChecked(True)
            dialog._resize_for_profile()
            sqlite_width = dialog.width()
            dialog.postgres_radio.setChecked(True)
            dialog._resize_for_profile()
            postgres_width = dialog.width()
            self.assertGreater(postgres_width, sqlite_width)
            dialog.sqlite_radio.setChecked(True)
            dialog._resize_for_profile()
            self.assertEqual(dialog.width(), sqlite_width)

            chosen = str((Path(directory) / "chosen").resolve())
            with patch(
                "Plugins.core.backend_configuration_dialog.QFileDialog.getExistingDirectory",
                return_value=chosen,
            ):
                dialog._change_sqlite_folder()
            self.assertEqual(dialog.sqlite_folder.text(), chosen)
            dialog.reject()
            app.processEvents()

    def test_backend_dialog_has_no_transfer_note_and_profile_geometry_is_bounded(self):
        from PyQt6.QtWidgets import QApplication, QLabel

        from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            service = BackendConfigurationService(
                paths, environ={}, credential_store=MemoryCredentialStore()
            )
            dialog = BackendConfigurationDialog(service, {}, authorized=True)
            dialog.show()
            app.processEvents()
            sqlite_size = None
            postgres_size = None
            sqlite_hint_width = None
            postgres_hint_width = None
            for _ in range(2):
                dialog.sqlite_radio.setChecked(True)
                dialog._resize_for_profile()
                app.processEvents()
                sqlite_size = (dialog.width(), dialog.height())
                sqlite_hint_width = dialog.pages.sizeHint().width()
                dialog.postgres_radio.setChecked(True)
                dialog._resize_for_profile()
                app.processEvents()
                postgres_size = (dialog.width(), dialog.height())
                postgres_hint_width = dialog.pages.sizeHint().width()
            self.assertIsNotNone(sqlite_size)
            self.assertIsNotNone(postgres_size)
            self.assertIsNotNone(sqlite_hint_width)
            self.assertIsNotNone(postgres_hint_width)
            self.assertGreater(postgres_hint_width, sqlite_hint_width)
            self.assertLess(sqlite_size[1], postgres_size[1])
            self.assertFalse(
                any(
                    "separate canonical" in label.text().casefold()
                    or "no data" in label.text().casefold()
                    for label in dialog.findChildren(QLabel)
                )
            )
            dialog.close()
            app.processEvents()

    def test_backend_dialog_localized_save_messages_are_clear(self):
        for path in sorted((ROOT / "lang").glob("messages_*.json")):
            catalog = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("backend.dialog.transfer_note", catalog)
            self.assertNotIn("transfer", catalog["backend.dialog.intro"].casefold())
            self.assertNotIn("übertragen", catalog["backend.dialog.intro"].casefold())
            self.assertNotIn("trasfer", catalog["backend.dialog.intro"].casefold())
            self.assertNotIn("перед", catalog["backend.dialog.intro"].casefold())
            self.assertIn("backend.save.sqlite_created", catalog)
            self.assertNotIn("no data", catalog["backend.save.success"].casefold())
            self.assertNotIn("keine daten", catalog["backend.save.success"].casefold())

    def test_missing_seed_keeps_new_backend_empty_without_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = replace(
                runtime_paths(Path(directory)),
                application_root=Path(directory) / "application-without-seed",
            )
            backend = ProgTrackBackend(
                paths,
                acquire_process_lock=False,
                bootstrap_seed=True,
            )
            try:
                snapshot = backend.load_core_data()
                self.assertFalse(snapshot.get("animals"))
                self.assertFalse(snapshot.get("archived_animals"))
                self.assertEqual(backend.records.namespace_names(), [])
            finally:
                backend.close()

    def test_backend_dialog_reports_new_sqlite_target_clearly(self):
        from PyQt6.QtWidgets import QApplication

        from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            service = BackendConfigurationService(
                paths, environ={}, credential_store=MemoryCredentialStore()
            )
            dialog = BackendConfigurationDialog(service, {}, authorized=True)
            dialog.sqlite_folder.setText(directory)
            dialog.sqlite_filename.setText("new.sqlite3")
            with patch(
                "Plugins.core.backend_configuration_dialog.QMessageBox.information"
            ) as information:
                dialog._save()
            self.assertTrue(information.called)
            message = str(information.call_args.args[2]).casefold()
            self.assertIn("new empty database has been created", message)
            self.assertNotIn("no data", message)
            dialog.reject()
            app.processEvents()


class ExperimentalLimitDefaultsTest(unittest.TestCase):
    def test_role_defaults_are_normalized_and_seeded_for_every_record(self):
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as package:
            animals = [
                json.loads(line)["payload"]
                for line in package.read("records/animals.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
            domains = [
                json.loads(line)
                for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        self.assertTrue(animals)
        for animal in animals:
            role = canonical_role_value(animal.get("rolle"), default="unknown")
            expected = default_experimental_limits(role)
            actual = animal.get("experimental_limits")
            self.assertIsInstance(actual, dict)
            self.assertEqual(set(expected), set(actual) & set(expected))
            for key in expected:
                self.assertEqual(actual[key], animal.get(key))
            self.assertFalse(set(animal) & (KNOWN_LIMIT_FIELDS - set(expected)))
        role_payload = next(
            row["payload"]
            for row in domains
            if row.get("namespace") == "configuration"
            and row.get("record_id") == "animal-roles"
        )
        configured = {
            canonical_role_value(role.get("value"), default=""): role
            for role in role_payload["roles"]
        }
        for role in {canonical_role_value(animal.get("rolle"), default="unknown") for animal in animals}:
            self.assertEqual(
                configured[role]["experimental_limit_defaults"],
                default_experimental_limits(role),
            )

    def test_limit_synchronizer_promotes_flat_values_and_removes_inapplicable_fields(self):
        record = {
            "rolle": "experimental_animal",
            "max_op": 3,
            "max_measurements": 8,
            "max_pgf": 99,
        }
        synchronize_experimental_limits(
            record,
            "experimental_animal",
            prefer_flat=True,
            prune_unsupported=True,
        )
        self.assertEqual(record["experimental_limits"]["max_op"], 3)
        self.assertEqual(record["experimental_limits"]["max_measurements"], 8)
        self.assertNotIn("max_pgf", record)

    def test_statistics_read_canonical_sperm_limit_without_question_mark(self):
        spec = importlib.util.spec_from_file_location(
            "progtrack_main_limits", ROOT / "ProgTrack.v.0.3.0.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        app = module.ProgTrackApp.__new__(module.ProgTrackApp)
        app.messages = {"stats.sperm": "Sperm"}
        app._is_steroid_track_active = lambda: False
        app._animal_role_value = lambda record, default=None: record.get("rolle", default or "")
        app._custom_event_definitions = lambda include_retired=True: []
        app._normalize_report_event_type = lambda value: str(value or "").strip().casefold()
        animal = {
            "rolle": "sperm_donor",
            "sperm": [{"datum": datetime.now()}],
            "experimental_limits": {"max_spermaproben": 7, "ref_weight": 480, "recovery_time": 60},
        }
        self.assertEqual(app._get_event_statistics(animal), "Sperm: 1/7")


class AnimalParentsLabelLocalizationTest(unittest.TestCase):
    def test_parents_section_is_punctuation_free_in_all_catalogs_and_fallback(self):
        expected = {
            "messages_en.json": "Parents",
            "messages_de.json": "Eltern",
            "messages_it.json": "Genitori",
            "messages_ru.json": None,
        }
        for path in sorted((ROOT / "lang").glob("messages_*.json")):
            catalog = json.loads(path.read_text(encoding="utf-8"))
            value = str(catalog["dialog.offspring.parents"])
            self.assertFalse(value.endswith(":"), path.name)
            if expected.get(path.name):
                self.assertEqual(value, expected[path.name])
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn('self.messages.get("dialog.offspring.parents", "Parents")', source)
        self.assertNotIn('dialog.offspring.parents", "Parents:"', source)

    def test_lord_only_atomic_configuration_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            credentials = MemoryCredentialStore()
            service = BackendConfigurationService(
                paths, environ={}, credential_store=credentials
            )
            ca_file = Path(directory) / "ca.pem"
            ca_file.write_text("test-ca", encoding="utf-8")
            pg = PostgreSQLSettings(
                host="db.example.invalid",
                database="progtrack",
                user="progtrack",
                sslmode="verify-full",
                ca_file=str(ca_file),
                managed_root=str(paths.managed_root),
                pool_min=1,
                pool_max=4,
            )
            with self.assertRaises(BackendConfigurationPermissionError):
                service.save(
                    profile=BackendProfile.SHARED_POSTGRESQL,
                    sqlite_filename="progtrack.sqlite3",
                    postgresql=pg,
                    password="secret",
                    authorized=False,
                )
            document = service.save(
                profile=BackendProfile.SHARED_POSTGRESQL,
                sqlite_filename="facility.sqlite3",
                postgresql=pg,
                password="secret",
                authorized=True,
            )
            raw = paths.profile_file.read_text(encoding="utf-8")
            self.assertNotIn("secret", raw)
            self.assertNotIn("password", raw.casefold())
            self.assertEqual(credentials.values[service.credential_target], "secret")
            self.assertEqual(document["profile"], "shared_postgresql")
            self.assertEqual(document["postgresql"]["pool_max"], 4)

    def test_validation_and_environment_override_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            service = BackendConfigurationService(
                paths,
                environ={
                    "PROGTRACK_POSTGRES_DSN": "host=override dbname=override",
                    "PROGTRACK_POSTGRES_POOL_MIN": "2",
                    "PROGTRACK_POSTGRES_POOL_MAX": "7",
                },
                credential_store=MemoryCredentialStore(),
            )
            with self.assertRaises(BackendConfigurationValidationError):
                service.validate_sqlite_filename("../network.sqlite3")
            with self.assertRaises(BackendConfigurationValidationError):
                service.validate_postgresql(
                    PostgreSQLSettings(
                        host="server",
                        database="db",
                        user="user",
                        managed_root=str(paths.managed_root),
                        pool_min=5,
                        pool_max=2,
                    )
                )
            self.assertEqual(
                service.connection_dsn(PostgreSQLSettings(), allow_environment=True),
                "host=override dbname=override",
            )
            self.assertEqual(service.effective_pool_sizes(), (2, 7))


class LoginWindowPresentationTest(unittest.TestCase):
    def test_login_is_top_level_taskbar_window_and_only_temporarily_topmost(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QApplication

        from Plugins.Master_Track.dialogs import LoginDialog

        app = QApplication.instance() or QApplication([])

        class StubUsers:
            def authenticate(self, username, password):
                return None

            def record_login(self, username):
                return None

        dialog = LoginDialog(None, {}, StubUsers())
        flags = dialog.windowFlags()
        self.assertTrue(flags & Qt.WindowType.Window)
        self.assertTrue(flags & Qt.WindowType.WindowStaysOnTopHint)
        self.assertNotEqual(
            flags & Qt.WindowType.WindowType_Mask, Qt.WindowType.Tool
        )
        self.assertFalse(dialog.windowIcon().isNull())
        self.assertEqual(
            dialog.windowModality(), Qt.WindowModality.ApplicationModal
        )
        dialog.done(0)
        self.assertFalse(dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        dialog.deleteLater()
        app.processEvents()


class StandaloneLockPresentationTest(unittest.TestCase):
    def test_active_writer_uses_localized_warning_instead_of_generic_critical(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn("isinstance(e, StandaloneLockError)", source)
        self.assertIn("QMessageBox.warning(", source)
        self.assertIn('"backend.lock.active.message"', source)
        for language in ("en", "de", "it", "ru"):
            messages = json.loads(
                (ROOT / "lang" / f"messages_{language}.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("{owner}", messages["backend.lock.active.message"])
            self.assertIn("{lock_path}", messages["backend.lock.active.message"])


class LanguageFlagAssetTest(unittest.TestCase):
    def test_language_menu_uses_ui_flag_assets(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        manifest = json.loads(
            (ROOT / "icons" / "ui" / "manifest.json").read_text(encoding="utf-8")
        )["icons"]
        expected = {
            "language.en": "flag_gb.svg",
            "language.de": "flag_de.svg",
            "language.it": "flag_it.svg",
            "language.ru": "flag_ru.svg",
        }
        self.assertIn("QAction(ui_icon(semantic_id)", source)
        for semantic_id, filename in expected.items():
            self.assertEqual(manifest[semantic_id]["file"], filename)
            self.assertTrue((ROOT / "icons" / "ui" / filename).is_file())
            asset = filename.removeprefix("flag_").removesuffix(".svg")
            self.assertFalse((ROOT / "icons" / f"flag_{asset}.svg").exists())
        self.assertNotIn("emojiFlag", source)


class SharedUiIconAssetTest(unittest.TestCase):
    def test_manifest_reuses_canonical_files_and_has_no_duplicate_svg_bytes(self):
        manifest = json.loads(
            (ROOT / "icons" / "ui" / "manifest.json").read_text(encoding="utf-8")
        )
        icons = manifest["icons"]
        expected_shared_files = {
            "role.breeding": "pedigree_symbol.svg",
            "status.partner": "role_partner.svg",
            "medi_track.filter.current_sick": "status_sick.svg",
            "medi_track.filter.current_abnormal": "status_abnormal.svg",
            "status.warning": "status_abnormal.svg",
        }
        for semantic_id, filename in expected_shared_files.items():
            self.assertEqual(icons[semantic_id]["file"], filename)
            self.assertTrue((ROOT / "icons" / "ui" / filename).is_file())

        removed_aliases = {
            "role_breeding.svg",
            "status_partner.svg",
            "medi_current_sick.svg",
            "medi_current_abnormal.svg",
            "status_warning.svg",
        }
        self.assertTrue(
            removed_aliases.isdisjoint(
                {path.name for path in (ROOT / "icons" / "ui").glob("*.svg")}
            )
        )

        hashes = {}
        for path in (ROOT / "icons" / "ui").glob("*.svg"):
            digest = path.read_bytes()
            hashes.setdefault(digest, []).append(path.name)
        self.assertTrue(
            all(len(filenames) == 1 for filenames in hashes.values()),
            msg=f"Duplicate UI SVG artwork remains: {hashes}",
        )


class HeritageConstraintTest(unittest.TestCase):
    def test_single_child_axes_and_label_rectangles_are_both_preserved(self):
        positions = {
            "hub": (0.0, 3.6),
            "mate-left": (-2.0, 3.6),
            "mate-right": (2.0, 3.6),
            "child-left": (-0.2, 7.2),
            "child-right": (0.2, 7.2),
            "outside-left": (-1.0, 7.2),
            "outside-right": (1.0, 7.2),
        }
        families = {
            "left": {
                "mother": "mate-left",
                "father": "hub",
                "children": ["child-left"],
            },
            "right": {
                "mother": "hub",
                "father": "mate-right",
                "children": ["child-right"],
            },
            "outside": {
                "mother": "outside-left",
                "father": "outside-right",
                "children": [],
            },
        }
        labels = {
            node: f"{node} long display label" for node in positions
        }
        router = PedigreeRouter()
        plan = router.plan(positions, families, labels=labels)
        self.assertEqual(
            router.validate_plan(plan, families, labels=labels), []
        )
        for family_id in ("left", "right"):
            family = families[family_id]
            child = family["children"][0]
            parent_midpoint = (
                plan.animal_positions[family["mother"]][0]
                + plan.animal_positions[family["father"]][0]
            ) / 2.0
            self.assertLessEqual(
                abs(plan.animal_positions[child][0] - parent_midpoint),
                0.15,
                "a sole child should remain close to the family axis while "
                "retaining collision-clearing freedom",
            )


class ProjectTrackRegressionTest(unittest.TestCase):
    def test_legacy_string_role_config_is_safe_for_project_detail(self):
        self.assertEqual(
            _project_role_specs(
                {"approved_count": 4, "roles": ["experimental_animal"]}
            ),
            [{"role": "experimental_animal", "count": 4}],
        )
        self.assertEqual(
            _project_role_specs(
                {"approved_count": 4, "roles": [{"role": "experimental_animal", "count": 4}]}
            ),
            [{"role": "experimental_animal", "count": 4}],
        )


class PartnerStatusDisplayTest(unittest.TestCase):
    def test_full_partner_ipid_is_reduced_to_base_name_for_display(self):
        ipid = "Eglantine | Mus musculus | 03.10.2021 | DPZ"
        self.assertEqual(animal_base_name(ipid), "Eglantine")
        self.assertEqual(animal_base_name("Eglantine"), "Eglantine")

    def test_main_status_paths_normalize_partner_references(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        start = source.index("    def _get_status(self, name: str) -> str:")
        end = source.index("        # --- inject same", start)
        status_source = source[start:end]
        self.assertNotIn("partner_name = animal_base_name", status_source)
        self.assertNotIn('f"♥ {partner_name}"', status_source)
        self.assertNotIn("partner = animal_base_name(a.get('verpaart_mit', ''))", status_source)
        self.assertIn('parts.append("♥")', status_source)
        self.assertIn('status = f"{status} | ♥"', status_source)


class SeedAndIconTest(unittest.TestCase):
    def _load_seed_mice(self):
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as package:
            animals = [
                json.loads(line)["payload"]
                for line in package.read("records/animals.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
            domains = [
                json.loads(line)
                for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        named_animals = next(
            row["payload"]["named_animals"]
            for row in domains
            if row.get("namespace") == "seed" and row.get("record_id") == "shire-colony"
        )
        mice_by_ipid = {
            animal["ipid"]: animal
            for animal in animals
            if animal.get("species") == "Mus musculus"
        }
        named_mice = {
            alias: mice_by_ipid[ipid]
            for alias, ipid in named_animals.items()
        }
        return animals, domains, named_mice

    def test_seed_contains_canonical_users_and_complete_reproductive_links(self):
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as package:
            domain = [
                json.loads(line)
                for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
            animals = [
                json.loads(line)["payload"]
                for line in package.read("records/animals.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        user_payload = next(
            item["payload"]
            for item in domain
            if item["namespace"] == "security" and item["record_id"] == "users"
        )
        user_records = {item["username"]: item for item in user_payload}
        self.assertEqual(
            set(user_records),
            {"Admin", "Researcher", "Vet", "Manager", "Keeper", "Tester", "Veti"},
        )
        for animal in animals:
            scientist_dates = []
            for field in ("daten", "pdg", "gewicht", "sperm", "events", "pgf", "op", "embryo"):
                for entry in animal.get(field, []) if isinstance(animal.get(field, []), list) else []:
                    value = entry.get("datum") if isinstance(entry, dict) else entry
                    if value:
                        try:
                            scientist_dates.append(datetime.fromisoformat(str(value)))
                        except ValueError:
                            pass
            if scientist_dates:
                weight_dates = [
                    datetime.fromisoformat(str(entry["datum"]))
                    for entry in animal.get("gewicht", [])
                    if isinstance(entry, dict) and entry.get("datum")
                ]
                self.assertTrue(weight_dates)
                self.assertGreaterEqual(max(weight_dates), max(scientist_dates))
            donations = {
                str(event.get("sample_id") or "")
                for event in animal.get("events", [])
                if event.get("event_type") == "sperm_donation"
            }
            samples = {
                str(sample.get("sample_id") or sample.get("probennummer") or "")
                for sample in animal.get("sperm", [])
            }
            self.assertEqual(donations, samples)

    def test_status_and_medi_filter_icons_are_distinct_and_packaged(self):
        manifest = json.loads(
            (ROOT / "icons" / "ui" / "manifest.json").read_text(encoding="utf-8")
        )
        ids = (
            "medi_track.filter.current_sick",
            "medi_track.filter.ever_sick",
            "medi_track.filter.current_abnormal",
            "medi_track.filter.ever_abnormal",
            "medi_track.filter.current_experiment",
            "medi_track.filter.ever_experiment",
        )
        files = [manifest["icons"][item]["file"] for item in ids]
        self.assertEqual(len(set(files)), 6)
        for filename in files:
            self.assertTrue((ROOT / "icons" / "ui" / filename).is_file())
        experiment = manifest["icons"]["status.in_experiment"]
        self.assertEqual(experiment["file"], "status_in_experiment.svg")
        self.assertTrue((ROOT / "icons" / "ui" / experiment["file"]).is_file())
        self.assertTrue(
            (ROOT.parent / "Graphics" / "SVG" / "UI" / experiment["file"]).is_file()
        )
        self.assertEqual(
            manifest["icons"]["medi_track.filter.current_experiment"]["file"],
            "role_experimental.svg",
        )
        self.assertEqual(
            manifest["icons"]["medi_track.filter.ever_experiment"]["file"],
            "medi_ever_experiment.svg",
        )


    def test_mouse_ringbearer_colony_and_dedicated_housing(self):
        _animals, domains, named_mice = self._load_seed_mice()
        mice = list(named_mice.values())
        self.assertGreaterEqual(len(mice), 20)
        for alias in ("Frodo", "Sam", "Merry", "Pippin"):
            self.assertEqual(named_mice[alias]["rolle"], "experimental_animal")
            self.assertEqual(named_mice[alias]["project"], "Ringbearer")
            self.assertTrue(named_mice[alias]["in_experiment"])
        self.assertEqual(named_mice["Fredegar"]["rolle"], "breeding_animal")
        housing = next(
            item["payload"] for item in domains
            if item["namespace"] == "housing" and item["record_id"] == "cage"
        )
        structures = housing["structures"]
        self.assertEqual(
            structures["buildings"]["bld_mus"]["display_name"],
            "Building 05 - Mouse House",
        )
        occupants = housing["occupants"]
        for alias in ("Frodo", "Sam", "Merry", "Pippin"):
            expected_cage = (
                "cage_mus_experimental_frodo_sam"
                if alias in ("Frodo", "Sam")
                else "cage_mus_experimental_merry_pippin"
            )
            self.assertEqual(occupants[named_mice[alias]["ipid"]]["cage_id"], expected_cage)
        self.assertEqual(occupants[named_mice["Bilbo"]["ipid"]]["cage_id"], "cage_mus_bilbo")

    def test_mouse_hobbit_genealogy_uses_canonical_parentage(self):
        animals, _domains, mice = self._load_seed_mice()
        alias_by_ipid = {animal["ipid"]: alias for alias, animal in mice.items()}

        def parent(alias, field):
            reference = mice[alias].get(field)
            self.assertTrue(reference, f"{alias} has no {field}")
            return alias_by_ipid.get(reference, reference.split(" | ", 1)[0])

        expected_parents = {
            "Frodo": ("Drogo", "Primula"),
            "Bilbo": ("Bungo", "Belladonna"),
            "Sam": ("Hamfast", "Bell"),
            "Merry": ("Saradoc", "Esmeralda"),
            "Pippin": ("Paladin", "Eglantine"),
            "Fredegar": ("Odovacar", "Rosamunda"),
            "Elanor": ("Sam", "Rosie"),
        }
        for alias, expected in expected_parents.items():
            with self.subTest(animal=alias):
                self.assertEqual(
                    (parent(alias, "samenspender"), parent(alias, "eizellspenderin")),
                    expected,
                )
        self.assertNotIn("Bilbo", (parent("Frodo", "samenspender"), parent("Frodo", "eizellspenderin")))
        for field in ("verpaart_mit", "samenspender", "eizellspenderin"):
            self.assertFalse(mice["Diamond"].get(field))

        # Every declared biological parent predates the child, which prevents
        # an accidentally inverted generation in Heritage Track.
        by_ipid = {animal["ipid"]: animal for animal in animals}
        for child in (animal for animal in animals if animal.get("species") == "Mus musculus"):
            child_date = datetime.strptime(child["birth_date"], "%d.%m.%Y")
            for field in ("samenspender", "eizellspenderin"):
                reference = child.get(field)
                if not reference:
                    continue
                parent_record = by_ipid[reference]
                parent_date = datetime.strptime(parent_record["birth_date"], "%d.%m.%Y")
                self.assertLess(parent_date, child_date, f"{child['name']} has a later-born {field}")

    def test_mouse_public_ids_follow_callitrix_convention_with_sex_and_sequence(self):
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as package:
            animals = [
                json.loads(line)["payload"]
                for line in package.read("records/animals.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        mice = [animal for animal in animals if animal.get("species") == "Mus musculus"]
        pattern = re.compile(r"^mm_\d{2}_\d{4}_[MFU]_[A-Za-z0-9_]+$")
        self.assertTrue(mice)
        self.assertEqual(len({animal.get("id") for animal in mice}), len(mice))
        for animal in mice:
            public_id = str(animal.get("id") or "")
            self.assertRegex(public_id, pattern)
            sex = str(animal.get("sex") or "").strip().casefold()
            expected = "M" if sex in {"male", "m"} else "F" if sex in {"female", "f"} else "U"
            self.assertIn(f"_{expected}_", public_id)

    def test_project_catalog_is_complete_and_assigns_available_staff_roles(self):
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as package:
            domains = {
                (row := json.loads(line))["record_id"]: row["payload"]
                for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip() and (row := json.loads(line)).get("namespace") == "projects"
            }
            security = [
                json.loads(line)
                for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        users = next(
            row["payload"]
            for row in security
            if row.get("namespace") == "security" and row.get("record_id") == "users"
        )
        user_by_login = {str(user.get("username")): user for user in users}
        projects = domains["catalog"]["projects"]
        self.assertEqual(
            set(projects),
            {"Backcrossing", "OTOF-", "Oakshield", "Ringbearer", "Shire"},
        )
        for name, project in projects.items():
            self.assertTrue(project["summary"]["title"])
            self.assertTrue(project["summary"]["comment"])
            self.assertEqual(len(project["arrive"]), 20)
            self.assertTrue(all(str(value).strip() for value in project["arrive"].values()))
            for login in (
                project["iacuc"]["pi_login"],
                project["iacuc"]["di_login"],
                project["iacuc"]["welfare_login"],
                project["assoc_users"]["applicant_login"],
                project["assoc_users"]["planning_login"],
                *project["assoc_users"]["staff_logins"],
            ):
                self.assertIn(login, user_by_login, (name, login))
            self.assertEqual(
                user_by_login[project["iacuc"]["welfare_login"]]["role"],
                "user",
            )
            self.assertIn(
                "animal_welfare_officer",
                user_by_login[project["iacuc"]["welfare_login"]].get("jobs", []),
            )
            self.assertGreater(project["animals_config"]["approved_count"], 0)
            self.assertTrue(project["animals_config"]["roles"])

    def test_current_build_ui_contracts(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        flow_source = (ROOT / "Plugins" / "Flow_Track" / "flow_track_widget.py").read_text(encoding="utf-8")
        geometry_source = (ROOT / "Plugins" / "core" / "dialog_geometry.py").read_text(encoding="utf-8")
        self.assertIn('icon_label.setPixmap(status_icon.pixmap(27, 27))', source)
        self.assertIn('self.archive_maintenance_widget.setVisible(False)', source)
        self.assertIn('def _toggle_archive_controls', source)
        self.assertIn('QtCore = self.parent_app.QtCore', flow_source)
        self.assertIn('def _partner_generated_id', source)
        self.assertIn('self.btn_plot_unselect_all = QPushButton(', source)
        self.assertIn('self.btn_plot_unselect_all.clicked.connect(self._unselect_all_plots)', source)
        self.assertIn('def _unselect_all_plots(self)', source)
        self.assertIn('plot_action_vbox = QVBoxLayout(self.plot_action_widget)', source)
        self.assertIn('ctrl_hbox.addStretch(1)', source)
        self.assertIn('Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight', source)
        self.assertIn('def _sync_plot_action_button_widths(self)', source)
        self.assertIn('frame_left = max(0, client.left() - frame.left())', geometry_source)
        self.assertIn('self._apply_dialog_width(dlg)', source)

    def test_select_role_dialog_shows_titles_without_internal_ids(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        start = source.index("    def _dlg_new_animal_with_role_selector(self):")
        end = source.index("    # --- Moved from top-level into class ProgTrackApp ---", start)
        selector = source[start:end]
        self.assertIn(
            "role_options.append((self._role_selector_title(value), value))",
            selector,
        )
        self.assertNotIn(
            "role_options.append((self._role_label_with_icon(value), value))",
            selector,
        )
        self.assertNotIn("display_for_value(value", selector)


class StartupStatusRenderTest(unittest.TestCase):
    def test_core_data_load_reenters_canonical_list_status_renderer(self):
        """Loaded Core rows must be rendered before the first visible frame."""
        from PyQt6.QtWidgets import QApplication

        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_198_startup", ROOT / "ProgTrack.v.0.3.0.py"
        )
        loaded = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(loaded)
        qt_app = QApplication.instance() or QApplication([])

        class LoadingLabel:
            def __init__(self):
                self.value = ""

            def setText(self, value):
                self.value = value

        class Harness:
            def __init__(self):
                self.animals = {"Experiment animal": {"in_experiment": True}}
                self.archived_animals = {}
                # Match the production app contract used by the startup
                # refresh path.  The regression test must reach the canonical
                # list renderer instead of failing because its minimal
                # harness omitted the localized title catalog.
                self.messages = {
                    "app.window_title.animals_loaded": "{app_title} - {count} animals loaded",
                    "app.title": "ProgTrack",
                }
                self.loading_label = LoadingLabel()
                self.refresh_calls = 0
                self.startup_event_calls = 0
                self.window_title = ""

            def setWindowTitle(self, value):
                self.window_title = value

            def _refresh_list(self):
                self.refresh_calls += 1

            def _process_startup_events(self):
                self.startup_event_calls += 1

        harness = Harness()
        loaded.ProgTrackApp._refresh_ui_after_data_load(harness)
        qt_app.processEvents()

        self.assertEqual(harness.refresh_calls, 1)
        self.assertEqual(harness.startup_event_calls, 1)
        self.assertEqual(harness.loading_label.value, "Loaded 1 animals and 0 archived animals")


class PlotWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "progtrack_phase2b_test", ROOT / "ProgTrack.v.0.3.0.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def test_recent_window_uses_first_selected_real_record_only(self):
        fake = type("FakePlot", (), {})()
        fake.animals = {
            "primary": {
                "gewicht": [{"datum": "2024-02-15", "wert": 100}],
                "birth_date": "2026-01-01",
            },
            "later": {
                "events": [{"datum": "2026-07-01", "event_type": "birth"}],
            },
        }
        fake.selected_animals = ["primary", "later"]
        fake._plot_record_datetime = self.module.ProgTrackApp._plot_record_datetime
        fake._actual_plot_dates = (
            lambda name: self.module.ProgTrackApp._actual_plot_dates(fake, name)
        )
        viewport = self.module.ProgTrackApp._recent_plot_window(fake)
        start = self.module.mdates.num2date(viewport[0]).replace(tzinfo=None)
        end = self.module.mdates.num2date(viewport[1]).replace(tzinfo=None)
        self.assertEqual(end.date().isoformat(), "2024-02-15")
        self.assertEqual((end - start).days, 30)

    def test_recent_window_does_not_substitute_a_later_animal_for_no_data_primary(self):
        fake = type("FakePlot", (), {})()
        fake.animals = {
            "primary": {"birth_date": "2020-01-01"},
            "later": {
                "gewicht": [{"datum": "2024-05-01", "wert": 100}],
            },
        }
        fake.selected_animals = ["primary", "later"]
        fake._plot_record_datetime = self.module.ProgTrackApp._plot_record_datetime
        fake._actual_plot_dates = (
            lambda name: self.module.ProgTrackApp._actual_plot_dates(fake, name)
        )
        self.assertIsNone(self.module.ProgTrackApp._recent_plot_window(fake))

    def test_unselect_all_resets_every_plot_selection_state_once(self):
        class FakeList:
            def __init__(self):
                self.blocked = []
                self.cleared = 0

            def blockSignals(self, value):
                self.blocked.append(value)

            def clearSelection(self):
                self.cleared += 1

        fake = type("FakePlot", (), {})()
        fake.lst = FakeList()
        fake._selected_heritage_only = ["heritage"]
        fake._selected_archived = ["archived"]
        fake._plot_selection_order = ["first", "second"]
        fake.selected_animals = ["first", "second"]
        fake._plot_x_viewport = (1.0, 2.0)
        fake.select_updates = 0
        fake._on_select = lambda: setattr(
            fake, "select_updates", fake.select_updates + 1
        )

        self.module.ProgTrackApp._unselect_all_plots(fake)

        self.assertEqual(fake.lst.blocked, [True, False])
        self.assertEqual(fake.lst.cleared, 1)
        self.assertEqual(fake._selected_heritage_only, [])
        self.assertEqual(fake._selected_archived, [])
        self.assertEqual(fake._plot_selection_order, [])
        self.assertEqual(fake.selected_animals, [])
        self.assertIsNone(fake._plot_x_viewport)
        self.assertEqual(fake.select_updates, 1)

    def test_partner_tooltip_uses_generated_id_not_ipid(self):
        partner_ipid = "Eglantine | Mus musculus | 03.10.2021 | DPZ"
        fake = type("FakePartner", (), {})()
        fake.animals = {
            partner_ipid: {
                "name": "Eglantine",
                "_base_name": "Eglantine",
                "id": "MUS-0007",
            }
        }
        generated_id = self.module.ProgTrackApp._partner_generated_id(
            fake, {"partner_von": partner_ipid}
        )
        self.assertEqual(generated_id, "MUS-0007")
        self.assertNotIn("Mus musculus", generated_id)

    def test_in_experiment_tooltip_includes_visible_canonical_project(self):
        fake = type("FakeExperimentTooltip", (), {})()
        fake.messages = {
            "status.in_experiment": "In experiment",
            "status.in_experiment_project": "In experiment, in the {project} Project",
        }
        fake._status_icon_tooltip = lambda semantic_id: self.module.ProgTrackApp._status_icon_tooltip(
            fake, semantic_id
        )
        fake._load_project_records_for_visibility = lambda: {"Ringbearer": {}}
        fake._project_visibility_scope = lambda: (True, {"Ringbearer"})

        tooltip = self.module.ProgTrackApp._in_experiment_icon_tooltip(
            fake,
            {"in_experiment": True, "project": "Ringbearer"},
        )

        self.assertEqual(tooltip, "In experiment, in the Ringbearer Project")

    def test_in_experiment_tooltip_falls_back_for_missing_or_hidden_project(self):
        fake = type("FakeExperimentTooltip", (), {})()
        fake.messages = {
            "status.in_experiment": "In experiment",
            "status.in_experiment_project": "In experiment, in the {project} Project",
        }
        fake._status_icon_tooltip = lambda semantic_id: self.module.ProgTrackApp._status_icon_tooltip(
            fake, semantic_id
        )
        fake._load_project_records_for_visibility = lambda: {
            "Ringbearer": {},
            "Other": {},
        }
        fake._project_visibility_scope = lambda: (False, {"Other"})

        self.assertEqual(
            self.module.ProgTrackApp._in_experiment_icon_tooltip(
                fake, {"in_experiment": True, "project": "Ringbearer"}
            ),
            "In experiment",
        )
        self.assertEqual(
            self.module.ProgTrackApp._in_experiment_icon_tooltip(
                fake, {"in_experiment": True, "project": "Missing"}
            ),
            "In experiment",
        )

    def test_all_localized_experiment_project_templates_have_placeholder(self):
        for path in sorted((ROOT / "lang").glob("messages_*.json")):
            catalog = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(path=path.name):
                self.assertIn("status.in_experiment_project", catalog)
                self.assertIn("{project}", catalog["status.in_experiment_project"])

    def test_relationship_icon_tooltip_uses_name_and_public_id(self):
        subject_key = "Subject | Callithrix jacchus | 01.01.2020 | DPZ"
        partner_key = "Eglantine | Callithrix jacchus | 03.10.2021 | DPZ"
        archived_key = "Archived | Callithrix jacchus | 04.10.2021 | DPZ"
        fake = type("FakeRelationshipTooltip", (), {})()
        fake.animals = {
            subject_key: {
                "name": "Subject",
                "partner_von": partner_key,
                "verpaart_mit": archived_key,
            },
            partner_key: {"name": "Eglantine", "id": "CJ-0007"},
        }
        fake.archived_animals = {
            archived_key: {"name": "Archived", "id": "CJ-0008"},
        }
        # The production loader exposes the archive as ``archived``.  Keep a
        # legacy mapping too so the test catches regressions in either
        # session-transition path.
        fake.archived = dict(fake.archived_animals)
        fake._relationship_records = lambda: self.module.ProgTrackApp._relationship_records(fake)

        tooltip = self.module.ProgTrackApp._relationship_icon_tooltip(
            fake,
            subject_key,
            fake.animals[subject_key],
            "status.partner",
        )

        self.assertEqual(tooltip, "Eglantine (CJ-0007)\nArchived (CJ-0008)")
        self.assertNotIn("Callithrix jacchus", tooltip)
        self.assertNotIn("03.10.2021", tooltip)

    def test_relationship_icon_tooltip_reads_persisted_archive_mapping(self):
        subject_key = "Subject | Callithrix jacchus | 01.01.2020 | DPZ"
        archived_key = "Archived | Callithrix jacchus | 04.10.2021 | DPZ"
        fake = type("FakePersistedArchiveTooltip", (), {})()
        fake.animals = {
            subject_key: {"name": "Subject", "verpaart_mit": archived_key},
        }
        fake.archived = {
            archived_key: {"name": "Archived", "id": "CJ-0010"},
        }
        fake.archived_animals = {}
        fake._relationship_records = lambda: self.module.ProgTrackApp._relationship_records(fake)

        tooltip = self.module.ProgTrackApp._relationship_icon_tooltip(
            fake,
            subject_key,
            fake.animals[subject_key],
            "status.partner",
        )
        self.assertEqual(tooltip, "Archived (CJ-0010)")

    def test_relationship_icon_tooltip_omits_unresolved_or_idless_records(self):
        subject_key = "Subject | Callithrix jacchus | 01.01.2020 | DPZ"
        known_key = "Known | Callithrix jacchus | 03.10.2021 | DPZ"
        missing_id_key = "No ID | Callithrix jacchus | 04.10.2021 | DPZ"
        fake = type("FakeRelationshipTooltip", (), {})()
        fake.animals = {
            subject_key: {
                "partner_von": "missing-reference | Callithrix jacchus | 02.10.2021 | DPZ",
                "verpaart_mit": known_key,
            },
            known_key: {"name": "Known", "id": "CJ-0009"},
            missing_id_key: {"name": "No ID"},
        }
        fake.archived_animals = {}
        fake._relationship_records = lambda: self.module.ProgTrackApp._relationship_records(fake)

        tooltip = self.module.ProgTrackApp._relationship_icon_tooltip(
            fake,
            subject_key,
            fake.animals[subject_key],
            "role.breeding",
        )

        self.assertEqual(tooltip, "Known (CJ-0009)")
        self.assertNotIn("missing-reference", tooltip)

    def test_partner_and_breeding_status_keep_only_semantic_marker(self):
        fake = type("FakeStatus", (), {})()
        fake._animal_role_value = lambda record: record.get("rolle", "")
        fake._localized_reproduction_status = lambda value: ""
        fake._is_steroid_track_active = lambda: False

        partner = "Partner | Callithrix jacchus | 02.02.2020 | DPZ"
        fake.animals = {
            "Subject": {
                "rolle": "partner_animal",
                "partner_von": partner,
            }
        }
        self.assertEqual(
            self.module.ProgTrackApp._get_status(fake, "Subject"),
            "♥",
        )

        fake.animals["Subject"] = {
            "rolle": "breeding_animal",
            "verpaart_mit": partner,
        }
        self.assertEqual(
            self.module.ProgTrackApp._get_status(fake, "Subject"),
            "♥",
        )

    def test_role_selector_uses_user_label_for_non_localized_role(self):
        class Registry:
            def get_by_value(self, value):
                return {
                    "value": value,
                    "label": "Ringbearer assistant",
                    "built_in": False,
                }

        fake = type("FakeRoleSelector", (), {})()
        fake.animal_role_registry = Registry()
        fake._role_definition_for_value = (
            lambda value: self.module.ProgTrackApp._role_definition_for_value(fake, value)
        )
        fake._get_localized_role = lambda value: "SHOULD NOT BE USED"
        title = self.module.ProgTrackApp._role_selector_title(fake, "custom.ringbearer_assistant")
        self.assertEqual(title, "Ringbearer assistant")

    def test_role_builder_svg_picker_covers_registered_and_unregistered_assets(self):
        fake = type("FakeRoleBuilder", (), {})()
        entries = self.module.StyleSettingsDialog._role_icon_entries(fake)
        svg_files = {
            path.name
            for path in (ROOT / "icons" / "ui").glob("*.svg")
        }
        self.assertEqual({path.name for _value, path, _tip in entries}, svg_files)
        values = {value for value, _path, _tip in entries}
        self.assertIn("role.female", values)
        manifest = json.loads(
            (ROOT / "icons" / "ui" / "manifest.json").read_text(encoding="utf-8")
        ).get("icons", {})
        registered_files = {
            str(definition.get("file") or "")
            for definition in manifest.values()
            if isinstance(definition, dict)
        }
        for value, path, _tip in entries:
            if path.name not in registered_files:
                self.assertEqual(value, f"svg:{path.name}")
        resolved = self.module.StyleSettingsDialog._role_icon_path(fake, "role.female")
        self.assertEqual(resolved.name, "role_female.svg")
        # The explicit filename namespace remains compatible even when every
        # bundled asset currently has a semantic manifest ID.
        explicit_path = self.module.StyleSettingsDialog._role_icon_path(
            fake, f"svg:{next(iter(svg_files))}"
        )
        self.assertTrue(explicit_path.is_file())


if __name__ == "__main__":
    unittest.main()
