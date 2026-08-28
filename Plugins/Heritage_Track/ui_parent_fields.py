# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track parent-field UI helpers.

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QWidget,
)


class ParentSelector(QComboBox):
    """Searchable, closed-set parent picker with an explicit custom path.

    Typing is used only to search existing entries.  Text that does not match
    an item can therefore never leak into stored pedigree data.  A user who
    intentionally needs a not-yet-existing ancestor must use the dedicated
    action at the bottom of the list.
    """

    _KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    _ACTION_KIND = "add_custom"

    def __init__(
        self,
        messages: Dict[str, Any],
        options: Iterable[str] = (),
        selected: str = "",
        *,
        allow_custom: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.messages = messages or {}
        self._none_label = self.messages.get("heritage_track.value.none", "none")
        self._custom_label = self.messages.get(
            "heritage_track.parent.add_custom",
            "Add Heritage-only ancestor…",
        )
        self._allow_custom = bool(allow_custom)
        self._last_valid_value = ""

        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._replace_items(options, selected)

        self.activated.connect(self._on_activated)
        if self.lineEdit() is not None:
            self.lineEdit().editingFinished.connect(self._restore_invalid_text)

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    def _append_value(self, label: str, value: str, kind: str) -> int:
        self.addItem(label, value)
        index = self.count() - 1
        self.setItemData(index, kind, self._KIND_ROLE)
        return index

    def _append_action(self) -> None:
        if self._allow_custom:
            self._append_value(self._custom_label, "", self._ACTION_KIND)

    def _configure_completer(self) -> None:
        searchable = [
            self.itemText(index)
            for index in range(self.count())
            if self.itemData(index, self._KIND_ROLE) != self._ACTION_KIND
        ]
        completer = QCompleter(searchable, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.setCompleter(completer)

    def _replace_items(self, options: Iterable[str], selected: str = "") -> None:
        clean_selected = self._clean(selected)
        previous_block = self.blockSignals(True)
        self.clear()
        self._append_value(self._none_label, "", "none")

        seen = {""}
        for raw_name in sorted(
            {self._clean(value) for value in options if self._clean(value)},
            key=str.casefold,
        ):
            folded = raw_name.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            self._append_value(raw_name, raw_name, "candidate")

        selected_index = self._find_value(clean_selected)
        if clean_selected and selected_index < 0:
            selected_index = self._append_value(clean_selected, clean_selected, "current")

        self._append_action()
        self.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self._last_valid_value = clean_selected if selected_index >= 0 else ""
        self.blockSignals(previous_block)
        self._configure_completer()

    def set_options(self, options: Iterable[str]) -> None:
        """Refresh candidates while retaining the current valid selection."""
        self._replace_items(options, self.selected_value())

    def _find_value(self, value: str) -> int:
        clean_value = self._clean(value)
        for index in range(self.count()):
            if self.itemData(index, self._KIND_ROLE) == self._ACTION_KIND:
                continue
            candidate = self._clean(self.itemData(index))
            if candidate.casefold() == clean_value.casefold():
                return index
        return -1

    def _matching_index_for_text(self, text: str) -> int:
        clean_text = self._clean(text)
        if not clean_text or clean_text.casefold() == self._none_label.casefold():
            return self._find_value("")
        for index in range(self.count()):
            if self.itemData(index, self._KIND_ROLE) == self._ACTION_KIND:
                continue
            if self.itemText(index).strip().casefold() == clean_text.casefold():
                return index
        return -1

    def _restore_invalid_text(self) -> None:
        index = self._matching_index_for_text(self.currentText())
        if index >= 0:
            self.setCurrentIndex(index)
            self._last_valid_value = self._clean(self.itemData(index))
            return
        fallback = self._find_value(self._last_valid_value)
        self.setCurrentIndex(fallback if fallback >= 0 else 0)

    def _on_activated(self, index: int) -> None:
        if self.itemData(index, self._KIND_ROLE) != self._ACTION_KIND:
            self._last_valid_value = self._clean(self.itemData(index))
            return

        text, accepted = QInputDialog.getText(
            self,
            self.messages.get("heritage_track.parent.custom.title", "Heritage-only ancestor"),
            self.messages.get("heritage_track.parent.custom.name", "Ancestor name:"),
        )
        if accepted and self._clean(text):
            self.add_custom_ancestor(text)
            return
        fallback = self._find_value(self._last_valid_value)
        self.setCurrentIndex(fallback if fallback >= 0 else 0)

    def add_custom_ancestor(self, name: str) -> bool:
        """Add and select an explicitly requested custom ancestor."""
        clean_name = self._clean(name)
        if not clean_name or clean_name.casefold() == self._none_label.casefold():
            return False
        existing = self._find_value(clean_name)
        if existing >= 0:
            self.setCurrentIndex(existing)
            self._last_valid_value = self._clean(self.itemData(existing))
            return True

        action_index = next(
            (
                index
                for index in range(self.count())
                if self.itemData(index, self._KIND_ROLE) == self._ACTION_KIND
            ),
            self.count(),
        )
        self.insertItem(action_index, clean_name, clean_name)
        self.setItemData(action_index, "custom", self._KIND_ROLE)
        self.setCurrentIndex(action_index)
        self._last_valid_value = clean_name
        self._configure_completer()
        return True

    def has_invalid_input(self) -> bool:
        return self._matching_index_for_text(self.currentText()) < 0

    def selected_value(self) -> str:
        index = self._matching_index_for_text(self.currentText())
        if index >= 0:
            value = self._clean(self.itemData(index))
            self._last_valid_value = value
            return value
        return self._last_valid_value

    def selection_kind(self) -> str:
        index = self._matching_index_for_text(self.currentText())
        if index < 0:
            index = self._find_value(self._last_valid_value)
        return self._clean(self.itemData(index, self._KIND_ROLE)) if index >= 0 else "none"

    def allows_missing_value(self) -> bool:
        return self.selection_kind() in {"custom", "current"}

    def text(self) -> str:
        """QLineEdit-compatible accessor used by the core dialog hook."""
        return self.selected_value()


def build_parent_group(
    messages: Dict[str, Any],
    values: Dict[str, str],
    options: Optional[Mapping[str, Iterable[str]]] = None,
    *,
    allow_custom: bool = True,
) -> Tuple[QGroupBox, Dict[str, ParentSelector]]:
    """Create safe parent selectors and return both group and widgets."""
    group = QGroupBox(messages.get("heritage_track.parents.group", "Parents"))
    layout = QFormLayout(group)
    candidate_options = options or {}

    fields = {
        key: ParentSelector(
            messages,
            candidate_options.get(key, ()),
            values.get(key, ""),
            allow_custom=allow_custom,
        )
        for key in (
            "egg_donor",
            "sperm_donor",
            "surrogate_mother",
            "surrogate_father",
        )
    }

    layout.addRow(messages.get("heritage_track.field.egg_donor", "Egg Donor:"), fields["egg_donor"])
    layout.addRow(messages.get("heritage_track.field.sperm_donor", "Sperm Donor:"), fields["sperm_donor"])
    layout.addRow(messages.get("heritage_track.field.surrogate_mother", "Surrogate Mother:"), fields["surrogate_mother"])
    layout.addRow(messages.get("heritage_track.field.surrogate_father", "Surrogate Father:"), fields["surrogate_father"])

    return group, fields


def extract_parent_values(fields: Mapping[str, Any]) -> Dict[str, str]:
    """Extract only a valid selection; never return arbitrary editor text."""
    result: Dict[str, str] = {}
    for key in (
        "egg_donor",
        "sperm_donor",
        "surrogate_mother",
        "surrogate_father",
    ):
        field = fields.get(key)
        if isinstance(field, ParentSelector):
            result[key] = field.selected_value()
        elif field is not None and hasattr(field, "text"):
            result[key] = str(field.text() or "").strip()
        else:
            result[key] = ""
    return result
