import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Plugins.core.backend.errors import BackendSchemaError
from Plugins.core.backend.migration import _ordered_plugins, require_shared_transaction


def test_registry_orders_dependencies_and_rejects_cycles():
    revision = {1: {"postgresql": "SELECT 1"}}
    registry = {
        "z_reports": (("a_core_projection",), revision, None),
        "b_independent": (("core",), revision, None),
        "a_core_projection": (("core",), revision, None),
    }
    assert [name for name, *_ in _ordered_plugins(registry)] == [
        "a_core_projection", "b_independent", "z_reports"
    ]
    registry["a_core_projection"] = (("z_reports",), revision, None)
    with pytest.raises(BackendSchemaError, match="cyclic"):
        list(_ordered_plugins(registry))


def test_busy_transaction_rejects_before_ordinary_queries():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {"acquired": False}
    with pytest.raises(BackendSchemaError) as busy:
        require_shared_transaction(connection, SimpleNamespace(dialect="postgresql"))
    assert busy.value.reason == "maintenance"
    connection.execute.assert_called_once()


@pytest.mark.parametrize("reason", ["unknown", "maintenance", "newer"])
def test_startup_schema_ui_only_prompts_for_authoritative_eligible_state(monkeypatch, reason):
    from PyQt6.QtWidgets import QMessageBox

    source = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py"
    tree = ast.parse(source.read_text(encoding="utf8"))
    helper = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_offer_startup_schema_maintenance")
    namespace = {
        "BackendSchemaError": BackendSchemaError,
        "ProgTrackApp": SimpleNamespace(_startup_messages_for_system_locale=lambda: {}),
    }
    exec(compile(ast.Module(body=[helper], type_ignores=[]), str(source), "exec"), namespace)
    warning = Mock()
    question = Mock(side_effect=AssertionError("should not request credentials"))
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr(QMessageBox, "question", question)
    assert namespace[helper.name](BackendSchemaError("expected detail", reason=reason)) is False
    warning.assert_called_once()
    question.assert_not_called()
