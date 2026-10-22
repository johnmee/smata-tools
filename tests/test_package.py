"""Offline tests for the package itself: layout, imports and version placement."""

import unittest
from importlib import resources
from pathlib import Path

import smata
from smata import errors


class LayerTests(unittest.TestCase):
    """Each module imports only from below it, so no import cycle can form."""

    def test_config_does_not_import_the_client(self):
        self.assertNotIn("smata.client", _imports_of("smata.config"))

    def test_client_does_not_import_config_or_archive(self):
        imported = _imports_of("smata.client")
        self.assertNotIn("smata.config", imported)
        self.assertNotIn("smata.archive", imported)

    def test_archive_does_not_import_config(self):
        self.assertNotIn("smata.config", _imports_of("smata.archive"))

    def test_storage_imports_nothing_from_the_package(self):
        self.assertEqual([m for m in _imports_of("smata.storage") if m.startswith("smata")], [])


class PackageSurfaceTests(unittest.TestCase):
    def test_every_public_name_is_importable(self):
        for name in smata.__all__:
            with self.subTest(name=name):
                self.assertTrue(hasattr(smata, name), name)

    def test_errors_are_the_same_objects_at_both_paths(self):
        from smata.client import ApiError as ClientApiError
        from smata.config import ConfigError as ConfigConfigError

        self.assertIs(ClientApiError, errors.ApiError)
        self.assertIs(ConfigConfigError, errors.ConfigError)

    def test_client_is_exported_under_both_names(self):
        from smata import Client
        from smata.client import SmataClient

        self.assertIs(SmataClient, Client)


class SchemaTests(unittest.TestCase):
    def test_schema_sql_reads_the_packaged_copy(self):
        from smata.storage import schema_sql

        sql = schema_sql()
        self.assertIn("CREATE TABLE documents", sql)

    def test_schema_is_reachable_as_package_data(self):
        text = resources.files("smata").joinpath("schema.sql").read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE documents", text)

    def test_create_database_works_from_an_installed_copy(self):
        """A zip import has no real file for schema.sql; the package data read must still work."""
        from smata.storage import create_database

        conn = create_database(":memory:")
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            self.assertEqual(tables, ["documents"])
        finally:
            conn.close()


class VersionTests(unittest.TestCase):
    def test_version_comes_from_smata_version(self):
        from smata.version import __version__

        self.assertEqual(smata.__version__, __version__)

    def test_version_is_a_pep440_string(self):
        self.assertRegex(smata.__version__, r"^\d+\.\d+\.\d+$")

    def test_user_agent_carries_the_version(self):
        from smata.client import USER_AGENT

        self.assertIn(smata.__version__, USER_AGENT)


def _imports_of(module_name: str) -> set[str]:
    """The names a module's source text imports, for the layering checks."""
    import ast

    parts = module_name.split(".")
    path = Path(__file__).resolve().parent.parent.joinpath(*parts).with_suffix(".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


if __name__ == "__main__":
    unittest.main()
