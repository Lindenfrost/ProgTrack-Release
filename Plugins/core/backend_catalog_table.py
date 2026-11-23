# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Group database rows for display while keeping their typed identity."""

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtWidgets import QFrame, QTableWidget, QTableWidgetItem

from .backend.postgresql_admin import PostgreSQLDatabaseInfo


def selected_database_row(table):
    item = table.item(table.currentRow(), 0)
    row = item.data(Qt.ItemDataRole.UserRole) if item else None
    return row if isinstance(row, PostgreSQLDatabaseInfo) and item.isSelected() else None


class DatabaseCatalogTable(QTableWidget):
    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        item = self.item(index.row(), 0) if index.isValid() else None
        if item is None or not isinstance(item.data(Qt.ItemDataRole.UserRole), PostgreSQLDatabaseInfo):
            self.clearSelection()
            self.setCurrentItem(None)
            event.accept()
            return
        super().mousePressEvent(event)


def render_database_table(table, rows, text):
    selected = selected_database_row(table)
    with QSignalBlocker(table):
        table.setSortingEnabled(False)
        table.clearSpans()
        table.setRowCount(0)
        columns = table.columnCount()
        for flag, key, title in (
            (True, "yes", "ProgTrack compatible"),
            (False, "no", "Not ProgTrack compatible"),
            (None, "unknown", "Compatibility unknown"),
        ):
            group = sorted((row for row in rows if row.compatible is flag),
                           key=lambda row: (row.name.casefold(), row.name))
            if not group:
                continue
            if table.rowCount():
                index = table.rowCount()
                table.insertRow(index)
                table.setSpan(index, 0, 1, columns)
                divider = QFrame(table)
                divider.setFrameShape(QFrame.Shape.HLine)
                divider.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                empty = QTableWidgetItem()
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                table.setItem(index, 0, empty)
                table.setCellWidget(index, 0, divider)
                table.setRowHeight(index, 5)
            index = table.rowCount()
            table.insertRow(index)
            table.setSpan(index, 0, 1, columns)
            label = QTableWidgetItem(text(f"backend.catalog.group.{key}", title))
            font = label.font()
            font.setBold(True)
            label.setFont(font)
            label.setFlags(Qt.ItemFlag.ItemIsEnabled)
            table.setItem(index, 0, label)
            for row in group:
                index = table.rowCount()
                table.insertRow(index)
                values = (
                    row.name, row.owner, str(row.size_bytes),
                    text("backend.catalog.archived", "Archived") if row.archived else text("backend.catalog.available", "Available"),
                    text(f"backend.catalog.compatibility.{key}", key),
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    table.setItem(index, column, item)
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, row)
                if selected is not None and row.name == selected.name:
                    table.selectRow(index)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().hide()
