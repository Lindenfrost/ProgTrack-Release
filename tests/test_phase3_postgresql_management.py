from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from Plugins.core.backend.errors import BackendConfigurationError

from Plugins.core.backend.postgresql_admin import (
    PostgreSQLAdministrationService,
)
from Plugins.core.backend_configuration import (
    BackendConfigurationService,
    BackendConfigurationValidationError,
    PostgreSQLSettings,
)
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths


class MemoryCredentials:
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


def paths(root: Path) -> RuntimePaths:
    result = RuntimePaths(
        application_root=root,
        profile=BackendProfile.STANDALONE_SQLITE,
        data_root=root / "data",
        config_root=root / "config",
        cache_root=root / "cache",
        state_root=root / "state",
        database_path=root / "data" / "database" / "progtrack.sqlite3",
        managed_root=root / "managed",
        managed_documents=root / "managed" / "documents",
        managed_config_assets=root / "managed" / "config-assets",
        logs=root / "logs",
        runtime=root / "runtime",
        exports=root / "exports",
        preferences=root / "preferences",
        profile_file=root / "config" / "backend.json",
    )
    result.create_mutable_roots()
    return result


class PostgreSQLManagementTests(unittest.TestCase):
    def test_pg_tools_hide_console_and_preserve_failure_reporting(self):
        module = 'Plugins.core.backend.postgresql_admin'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BackendConfigurationService(paths(root), credential_store=MemoryCredentials())
            settings = PostgreSQLSettings(host='db', database='fixture', user='app',
                                          managed_root=str(root / 'managed'))
            admin = PostgreSQLAdministrationService(service, settings, authorized=True,
                                                      validate_initiator=lambda: "Lord")
            backup = root / 'fixture.ptbackup'
            with zipfile.ZipFile(backup, 'w') as archive:
                archive.writestr('manifest.json', json.dumps({
                    'schema': 'progtrack-postgresql-backup/1', 'managed_files': []}))
                archive.writestr('database.dump', b'fixture')
            def authenticate(grant):
                grant.server_login = "dba"
                grant.row = (10, True, False, True)
            for platform in ('win32', 'linux'):
                for operation in ('backup', 'restore'):
                    with self.subTest(platform=platform, operation=operation):
                        with patch(module + '.sys.platform', platform), \
                             patch.object(admin, '_authenticate', side_effect=authenticate), \
                             patch.object(admin, '_connect', return_value=MagicMock()), \
                             patch.object(admin, '_check_current_target'), \
                             patch.object(admin, '_catalog_action', return_value=nullcontext()), \
                             patch.object(admin, '_backup_snapshot', return_value=nullcontext((4, [], 'snapshot'))), \
                             patch.object(admin, '_restore_guard', return_value=nullcontext(MagicMock())), \
                             patch.object(admin, '_target_storage_client', return_value=SimpleNamespace(check=lambda **_: root / 'managed')), \
                             patch.object(admin, '_restore_scripts', return_value=(root / 'restore.sql', root / 'verify.sql')), \
                             patch(module + '.shutil.which', side_effect=lambda tool: tool), \
                             patch(module + '.subprocess.CREATE_NO_WINDOW', 0x08000000, create=True), \
                             patch(module + '.subprocess.run', return_value=SimpleNamespace(
                                 returncode=1, stderr='fixture tool failed')) as run:
                            with self.assertRaisesRegex(BackendConfigurationError, r'(pg_dump|psql) failed \(exit code 1\)'):
                                with admin.authorize(operation, 'fixture', login='dba', password='fixture-secret') as grant:
                                    if operation == 'backup':
                                        admin.backup_database(root / 'output.ptbackup', database='fixture', operation=grant)
                                    else:
                                        admin.restore_backup(backup, database='fixture', confirmed=True, operation=grant)
                            self.assertEqual(run.call_args.kwargs['creationflags'],
                                             0x08000000 if platform == 'win32' else 0)
                            self.assertTrue(run.call_args.kwargs['capture_output'])
                            self.assertTrue(run.call_args.kwargs['text'])
                            self.assertNotIn('fixture-secret', ' '.join(run.call_args.args[0]))
                            self.assertEqual(run.call_args.kwargs['env']['PGPASSWORD'], 'fixture-secret')

    def test_verify_full_requires_ca_and_mutual_tls_requires_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BackendConfigurationService(paths(root), credential_store=MemoryCredentials())
            base = dict(host="db.example", database="progtrack", user="app", managed_root=str(root / "managed"))
            with self.assertRaises(BackendConfigurationValidationError):
                service.validate_postgresql(PostgreSQLSettings(**base, sslmode="verify-full"))
            ca = root / "ca.pem"
            cert = root / "client.crt"
            key = root / "client.key"
            for item in (ca, cert, key):
                item.write_text("fixture", encoding="utf-8")
            with self.assertRaises(BackendConfigurationValidationError):
                service.validate_postgresql(PostgreSQLSettings(**base, sslmode="verify-full", ca_file=str(ca), client_cert_file=str(cert)))
            valid = PostgreSQLSettings(
                **base,
                sslmode="verify-full",
                server_name="db.example",
                ca_file=str(ca),
                client_cert_file=str(cert),
                client_key_file=str(key),
            )
            service.validate_postgresql(valid)
            dsn = service.connection_dsn(valid, password="secret", client_key_passphrase="key-secret", allow_environment=False)
            self.assertIn("sslrootcert", dsn)
            self.assertIn("sslcert", dsn)
            self.assertIn("sslkey", dsn)
            self.assertIn("sslpassword", dsn)
            self.assertNotIn("secret", service.paths.profile_file.read_text(encoding="utf-8") if service.paths.profile_file.exists() else "")

    def test_database_name_and_destructive_guards(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = BackendConfigurationService(paths(root), credential_store=MemoryCredentials())
            settings = PostgreSQLSettings(host="db", database="active", user="app", managed_root=str(root / "managed"))
            admin = PostgreSQLAdministrationService(service, settings, authorized=True)
            self.assertEqual(admin.validate_database_name("progtrack_01"), "progtrack_01")
            with self.assertRaises(Exception):
                admin.validate_database_name("bad name;")
            with self.assertRaises(Exception):
                admin.delete_database("other")
            with self.assertRaises(Exception):
                admin.delete_database("active", typed_confirmation="active")

    def test_backup_manifest_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "backup.ptbackup"
            payload = b"pg dump fixture"
            file_data = b"document"
            manifest = {
                "schema": "progtrack-postgresql-backup/1",
                "database": "progtrack",
                "managed_files": [{
                    "path": "documents/x.txt",
                    "size": len(file_data),
                    "sha256": hashlib.sha256(file_data).hexdigest(),
                }],
            }
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("database.dump", payload)
                archive.writestr("managed/documents/x.txt", file_data)
            result = PostgreSQLAdministrationService.verify_backup(path)
            self.assertEqual(result["database"], "progtrack")


if __name__ == "__main__":
    unittest.main()
