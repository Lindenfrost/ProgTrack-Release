"""Facility branding configuration and repeatable compact PDF headers."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image
from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from Plugins.core.ui_icons import apply_icon


CONFIG_NAMESPACE = "configuration"
CONFIG_RECORD = "institution-branding"
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_LOGO_BYTES = 10 * 1024 * 1024
POSITION_TOP_LEFT = "top_left"
POSITION_TOP_RIGHT = "top_right"
BRANDING_POSITIONS = (POSITION_TOP_LEFT, POSITION_TOP_RIGHT)


def normalize_branding_position(value: object) -> str:
    position = str(value or "").strip().casefold().replace("-", "_")
    return position if position in BRANDING_POSITIONS else POSITION_TOP_RIGHT


class InstitutionBrandingService:
    def __init__(self, backend: Any, authorization: Any = None):
        self.backend = backend
        self.authorization = authorization

    def load(self) -> dict[str, Any]:
        value = self.backend.records.get(
            CONFIG_NAMESPACE,
            CONFIG_RECORD,
            default={
                "enabled": False,
                "facility_name": "",
                "logo_document_id": "",
                "position": POSITION_TOP_RIGHT,
            },
        )
        if not isinstance(value, dict):
            value = {}
        normalized = dict(value)
        normalized.setdefault("enabled", False)
        normalized.setdefault("facility_name", "")
        normalized.setdefault("logo_document_id", "")
        normalized["position"] = normalize_branding_position(
            normalized.get("position")
        )
        return normalized

    def save(
        self,
        *,
        enabled: bool,
        facility_name: str,
        logo_source: str = "",
        position: str = POSITION_TOP_RIGHT,
        actor: str,
        authorized: bool = False,
        remove_logo: bool = False,
    ) -> dict[str, Any]:
        policy = self.authorization
        if policy is not None:
            authorized = bool(
                policy.can("core.manage_institution_settings", write=True)
            )
        if not authorized:
            raise PermissionError(
                "Institution branding requires institution-settings permission."
            )
        current = self.load()
        logo_id = str(current.get("logo_document_id", ""))
        if remove_logo:
            logo_id = ""
        if logo_source:
            source = Path(logo_source)
            if source.suffix.lower() not in ALLOWED_SUFFIXES:
                raise ValueError("Institution logos must be PNG or JPEG files.")
            if source.stat().st_size > MAX_LOGO_BYTES:
                raise ValueError("Institution logos must not exceed 10 MiB.")
            with Image.open(source) as image:
                image.verify()
            record = self.backend.documents.add(
                source,
                owner_type="installation",
                owner_id="branding",
                category="config-asset",
                actor=actor,
            )
            logo_id = str(record["document_id"])
        value = {
            "enabled": bool(enabled),
            "facility_name": str(facility_name or "").strip(),
            "logo_document_id": logo_id,
            "position": normalize_branding_position(position),
        }
        self.backend.records.put(CONFIG_NAMESPACE, CONFIG_RECORD, value)
        self.backend.audit.append(
            actor_login=actor,
            category="configuration",
            action="institution_branding_update",
            entity_type="installation",
            entity_id="branding",
            payload={
                "enabled": value["enabled"],
                "facility_name": value["facility_name"],
                "logo_document_id": value["logo_document_id"],
                "position": value["position"],
            },
        )
        return value

    def logo_path(self, config: dict[str, Any] | None = None) -> Path | None:
        config = config or self.load()
        document_id = str(config.get("logo_document_id", ""))
        if not document_id:
            return None
        try:
            record = self.backend.documents.get(document_id)
            path = self.backend.documents.payload_path(record)
            return path if path.is_file() else None
        except (KeyError, OSError):
            return None

    def apply_to_pdf(self, pdf_path: str | Path) -> bool:
        """Overlay the configured compact name/logo header on every page.

        The complete block is anchored to the selected top-left or top-right
        page edge.  Every exporter reaches this one method, so the convention
        cannot drift between report types.  Logo proportions and the bounded
        header height remain identical in both orientations.
        """
        config = self.load()
        if not config.get("enabled"):
            return False
        facility = str(config.get("facility_name", "")).strip()
        logo = self.logo_path(config)
        position = normalize_branding_position(config.get("position"))
        if not facility and logo is None:
            return False

        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas

        path = Path(pdf_path)
        reader = PdfReader(str(path))
        writer = PdfWriter()
        for page in reader.pages:
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            overlay_bytes = io.BytesIO()
            layer = canvas.Canvas(overlay_bytes, pagesize=(width, height))
            margin = 18.0
            header_h = min(82.0, max(64.0, height * 0.095))
            gap = 10.0
            left_edge = margin
            right_edge = width - margin
            center_y = height - margin - header_h / 2.0
            logo_size: tuple[float, float] | None = None
            if logo is not None:
                with Image.open(logo) as image:
                    iw, ih = image.size
                max_w, max_h = 216.0, header_h - 6.0
                scale = min(max_w / max(iw, 1), max_h / max(ih, 1))
                logo_size = (iw * scale, ih * scale)
            logo_x = (
                right_edge - (logo_size[0] if logo_size else 0.0)
                if position == POSITION_TOP_RIGHT
                else left_edge
            )
            if facility:
                # Fit the complete block into the bounded page header.  The
                # same sizing rules apply at either edge.
                font_name = "Helvetica-Bold"
                font_size = 10.0
                available = max(36.0, width - 2.0 * margin)
                for candidate in (10.0, 9.0, 8.0, 7.0, 6.0, 5.5):
                    text_width = stringWidth(facility, font_name, candidate)
                    logo_width = logo_size[0] if logo_size else 0.0
                    if text_width + (gap if logo_size else 0.0) + logo_width <= available:
                        font_size = candidate
                        break
                    font_size = candidate
                text_width = stringWidth(facility, font_name, font_size)
                if logo_size:
                    allowed_logo_width = max(
                        24.0,
                        available - text_width - gap,
                    )
                    if logo_size[0] > allowed_logo_width:
                        logo_w, logo_h = logo_size
                        logo_size = (
                            allowed_logo_width,
                            logo_h * allowed_logo_width / max(logo_w, 1.0),
                        )
                logo_x = (
                    right_edge - (logo_size[0] if logo_size else 0.0)
                    if position == POSITION_TOP_RIGHT
                    else left_edge
                )
                layer.setFont(font_name, font_size)
                if position == POSITION_TOP_RIGHT:
                    text_right = logo_x - gap if logo_size else right_edge
                    layer.drawRightString(
                        text_right,
                        center_y - font_size * 0.35,
                        facility,
                    )
                else:
                    text_left = (
                        logo_x + logo_size[0] + gap
                        if logo_size else left_edge
                    )
                    layer.drawString(
                        text_left,
                        center_y - font_size * 0.35,
                        facility,
                    )
            if logo_size:
                draw_w, draw_h = logo_size
                layer.drawImage(
                    ImageReader(str(logo)),
                    logo_x,
                    center_y - draw_h / 2.0,
                    width=draw_w,
                    height=draw_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
            layer.save()
            overlay_bytes.seek(0)
            page.merge_page(PdfReader(overlay_bytes).pages[0])
            writer.add_page(page)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.stem}-branding-", suffix=".pdf", dir=path.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with temporary.open("wb") as handle:
                writer.write(handle)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return True


def brand_generated_pdf(owner: Any, pdf_path: str | Path) -> bool:
    """Locate the application backend from a widget/plugin and brand a PDF."""
    current = owner
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        backend = getattr(current, "backend", None)
        if backend is not None and hasattr(backend, "branding"):
            return bool(backend.branding.apply_to_pdf(pdf_path))
        app = getattr(current, "app", None) or getattr(current, "_app", None)
        if app is not None and app is not current:
            current = app
            continue
        plugin = getattr(current, "plugin", None)
        if plugin is not None and plugin is not current:
            current = plugin
            continue
        parent = getattr(current, "parent", None)
        current = parent() if callable(parent) else None
    return False


class _BrandingPreview(QWidget):
    """Small grey page mock-up sharing the PDF block orientation."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("institutionBrandingPreview")
        self.setMinimumSize(420, 176)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._enabled = False
        self._facility_name = ""
        self._logo_path: Path | None = None
        self._position = POSITION_TOP_RIGHT

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API name
        return QSize(520, 210)

    def set_preview(
        self,
        *,
        enabled: bool,
        facility_name: str,
        logo_path: Path | None,
        position: str,
    ) -> None:
        self._enabled = bool(enabled)
        self._facility_name = str(facility_name or "").strip()
        self._logo_path = logo_path if logo_path and logo_path.is_file() else None
        self._position = normalize_branding_position(position)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        page = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        painter.setPen(QColor("#9ca3aa"))
        painter.setBrush(QColor("#e5e7e9"))
        painter.drawRoundedRect(page, 3, 3)

        # Muted body lines make the header position legible without pretending
        # to preview a specific exporter or covering its content.
        painter.setPen(QColor("#c1c6ca"))
        body_top = page.top() + 76
        for fraction, offset in ((0.84, 0), (0.66, 18), (0.78, 36), (0.52, 54)):
            painter.drawLine(
                int(page.left() + 18),
                int(body_top + offset),
                int(page.left() + 18 + (page.width() - 36) * fraction),
                int(body_top + offset),
            )

        painter.save()
        painter.setOpacity(1.0 if self._enabled else 0.38)
        margin = 18.0
        gap = 9.0
        header = QRectF(page.left() + margin, page.top() + 14, page.width() - 2 * margin, 48)
        pixmap = QPixmap(str(self._logo_path)) if self._logo_path is not None else QPixmap()
        logo_w = logo_h = 0.0
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                132,
                46,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            logo_w = float(scaled.width())
            logo_h = float(scaled.height())
            logo_x = (
                header.right() - logo_w
                if self._position == POSITION_TOP_RIGHT
                else header.left()
            )
            logo_y = header.center().y() - logo_h / 2.0
            painter.drawPixmap(int(logo_x), int(logo_y), scaled)
        else:
            logo_x = header.right() if self._position == POSITION_TOP_RIGHT else header.left()

        if self._facility_name:
            font = QFont(self.font())
            font.setBold(True)
            font.setPointSizeF(max(8.0, font.pointSizeF()))
            painter.setFont(font)
            painter.setPen(QColor("#343a40"))
            if self._position == POSITION_TOP_RIGHT:
                right = logo_x - gap if logo_w else header.right()
                text_rect = QRectF(header.left(), header.top(), max(1.0, right - header.left()), header.height())
                alignment = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            else:
                left = logo_x + logo_w + gap if logo_w else header.left()
                text_rect = QRectF(left, header.top(), max(1.0, header.right() - left), header.height())
                alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            elided = painter.fontMetrics().elidedText(
                self._facility_name,
                Qt.TextElideMode.ElideRight,
                max(1, int(text_rect.width())),
            )
            painter.drawText(text_rect, alignment, elided)
        painter.restore()
        painter.end()


class _OrganizationalUnitsEditor(QWidget):
    """Backend-backed editor embedded below the institution preview."""

    def __init__(self, service: Any, actor: str, *, authorized: bool,
                 messages: dict[str, Any], user_db: Any = None, parent=None):
        super().__init__(parent)
        self.service = service
        self.actor = str(actor or "")
        self.authorized = bool(authorized)
        self.messages = messages
        self.user_db = user_db
        self._selected_id = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.setSpacing(4)
        self.toggle = QPushButton(self._text("units.title", "Organizational Units"), self)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setFlat(True)
        self.toggle.setFixedHeight(30)
        self.toggle.setIconSize(QSize(18, 18))
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; border: none; padding: 4px; }"
        )
        self.toggle.toggled.connect(self._set_expanded)
        outer.addWidget(self.toggle)

        self.content = QWidget(self)
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(12, 4, 4, 6)
        content_layout.setSpacing(5)
        self.table = QTableWidget(0, 3, self.content)
        self.table.setHorizontalHeaderLabels([
            self._text("units.id", "ID"),
            self._text("units.display_name", "Display name"),
            self._text("units.status", "Status"),
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._select_current)
        content_layout.addWidget(self.table)

        form = QFormLayout()
        self.id_edit = QLineEdit(self.content)
        self.id_edit.setPlaceholderText("unit-id")
        self.name_edit = QLineEdit(self.content)
        form.addRow(self._text("units.id", "ID:"), self.id_edit)
        form.addRow(self._text("units.display_name", "Display name:"), self.name_edit)
        content_layout.addLayout(form)

        buttons = QHBoxLayout()
        self.new_button = QPushButton(self._text("units.new", "New"), self.content)
        self.save_button = QPushButton(self._text("units.save", "Save"), self.content)
        self.archive_button = QPushButton(self._text("units.archive", "Archive"), self.content)
        self.delete_button = QPushButton(self._text("units.delete", "Delete"), self.content)
        for button in (self.new_button, self.save_button, self.archive_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch()
        content_layout.addLayout(buttons)
        outer.addWidget(self.content)
        self.content.setVisible(False)

        self.new_button.clicked.connect(self._new)
        self.save_button.clicked.connect(self._save)
        self.archive_button.clicked.connect(self._toggle_archive)
        self.delete_button.clicked.connect(self._delete)
        for button in (self.new_button, self.save_button, self.archive_button, self.delete_button):
            button.setEnabled(self.authorized)
        self._update_icon(False)
        self.refresh()

    def _text(self, key: str, fallback: str) -> str:
        return str(self.messages.get(key, fallback))

    def _update_icon(self, expanded: bool) -> None:
        apply_icon(
            self.toggle,
            "toggle.collapse" if expanded else "toggle.expand",
            fallback=self.toggle.text(),
        )

    def _set_expanded(self, expanded: bool) -> None:
        self.content.setVisible(bool(expanded))
        self._update_icon(bool(expanded))
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()

    def refresh(self) -> None:
        try:
            units = self.service.load()
        except Exception:
            units = {}
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for unit_id, unit in sorted(units.items(), key=lambda item: item[0]):
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(unit_id))
            self.table.setItem(row, 1, QTableWidgetItem(unit.display_name))
            status = self._text("units.archived", "Archived") if unit.archived else self._text("units.active", "Active")
            self.table.setItem(row, 2, QTableWidgetItem(status))
            if unit_id == self._selected_id:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        if not self._selected_id:
            self._new()

    def _select_current(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        self._selected_id = str(self.table.item(row, 0).text())
        self.id_edit.setText(self._selected_id)
        self.id_edit.setReadOnly(True)
        self.name_edit.setText(str(self.table.item(row, 1).text()))
        unit = self.service.get(self._selected_id)
        self.archive_button.setText(
            self._text("units.restore", "Restore") if unit and unit.archived
            else self._text("units.archive", "Archive")
        )

    def _new(self) -> None:
        self._selected_id = ""
        self.table.clearSelection()
        self.id_edit.clear()
        self.id_edit.setReadOnly(False)
        self.name_edit.clear()
        self.archive_button.setText(self._text("units.archive", "Archive"))

    def _save(self) -> None:
        try:
            if self._selected_id:
                self.service.update(
                    self._selected_id,
                    self.name_edit.text(),
                    actor=self.actor,
                    authorized=self.authorized,
                )
            else:
                created = self.service.create(
                    self.id_edit.text(),
                    self.name_edit.text(),
                    actor=self.actor,
                    authorized=self.authorized,
                )
                self._selected_id = created.unit_id
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, self._text("units.save_failed.title", "Cannot save organizational unit"), str(exc))
            return
        if self.user_db is not None:
            self.user_db.load()
        self.refresh()

    def _toggle_archive(self) -> None:
        if not self._selected_id:
            return
        current = self.service.get(self._selected_id)
        if current is None:
            return
        try:
            self.service.set_archived(
                self._selected_id,
                not current.archived,
                actor=self.actor,
                authorized=self.authorized,
            )
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, self._text("units.save_failed.title", "Cannot save organizational unit"), str(exc))
            return
        if self.user_db is not None:
            self.user_db.load()
        self.refresh()

    def _delete(self) -> None:
        if not self._selected_id:
            return
        answer = QMessageBox.question(
            self,
            self._text("units.delete.title", "Delete organizational unit"),
            self._text("units.delete.confirm", "Delete this unit and unassign its users?"),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.service.delete(
                self._selected_id,
                actor=self.actor,
                authorized=self.authorized,
            )
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, self._text("units.save_failed.title", "Cannot save organizational unit"), str(exc))
            return
        if self.user_db is not None:
            self.user_db.load()
        self._new()
        self.refresh()


class InstitutionBrandingDialog(QDialog):
    def __init__(
        self,
        service: InstitutionBrandingService,
        actor: str,
        *,
        authorized: bool = False,
        messages: dict[str, Any] | None = None,
        embedded: bool = False,
        units_service: Any = None,
        units_authorized: bool | None = None,
        user_db: Any = None,
        authorization: Any = None,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.actor = actor
        self.authorized = authorized
        self.messages = messages or {}
        self.embedded = bool(embedded)
        self.units_service = units_service
        self.units_authorized = self.authorized if units_authorized is None else bool(units_authorized)
        self.user_db = user_db
        if authorization is not None:
            self.service.authorization = authorization
        self._remove_logo = False
        self._logo_source_path: Path | None = None
        self._logo_controls_compact: bool | None = None
        self.setWindowTitle(self._text("branding.title", "Institution"))
        if self.embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        else:
            self.setMinimumSize(480, 360)
        config = service.load()
        self._initial_config = dict(config)

        outer = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.enabled = QCheckBox(
            self._text(
                "branding.enabled",
                "Include institution header in generated PDFs",
            )
        )
        self.enabled.setChecked(bool(config.get("enabled")))
        self.name = QLineEdit(str(config.get("facility_name", "")))
        self.logo = QLineEdit()
        self.logo.setReadOnly(True)
        existing_logo = service.logo_path(config)
        if existing_logo is not None:
            self.logo.setText(existing_logo.name)
        self.choose_button = QPushButton(
            self._text("branding.choose", "Choose PNG/JPEG…")
        )
        self.choose_button.clicked.connect(self._choose_logo)
        self.remove_button = QPushButton(
            self._text("branding.remove", "Remove")
        )
        self.remove_button.clicked.connect(self._remove_selected_logo)
        for button in (self.choose_button, self.remove_button):
            button.setMinimumWidth(button.sizeHint().width())
        self._logo_controls = QWidget(self)
        self._logo_controls.setObjectName("institutionBrandingLogoControls")
        self._logo_controls_layout = QGridLayout(self._logo_controls)
        self._logo_controls_layout.setContentsMargins(0, 0, 0, 0)
        self._logo_controls_layout.setSpacing(5)

        self.position_left = QRadioButton(
            self._text("branding.position.top_left", "Top left"), self
        )
        self.position_left.setObjectName("institutionBrandingPositionTopLeft")
        self.position_right = QRadioButton(
            self._text("branding.position.top_right", "Top right"), self
        )
        self.position_right.setObjectName("institutionBrandingPositionTopRight")
        self._position_group = QButtonGroup(self)
        self._position_group.setExclusive(True)
        self._position_group.addButton(self.position_left)
        self._position_group.addButton(self.position_right)
        if normalize_branding_position(config.get("position")) == POSITION_TOP_LEFT:
            self.position_left.setChecked(True)
        else:
            self.position_right.setChecked(True)
        position_row = QWidget(self)
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.addWidget(self.position_left)
        position_layout.addWidget(self.position_right)
        position_layout.addStretch()

        form.addRow("", self.enabled)
        form.addRow(
            self._text("branding.institution_name", "Institution name:"),
            self.name,
        )
        form.addRow(self._text("branding.logo", "Logo:"), self._logo_controls)
        form.addRow(
            self._text("branding.position", "Header position:"),
            position_row,
        )
        outer.addLayout(form)
        self.preview = _BrandingPreview(self)
        outer.addWidget(self.preview, 1)
        if self.units_service is not None:
            self.units_editor = _OrganizationalUnitsEditor(
                self.units_service,
                self.actor,
                authorized=self.units_authorized,
                messages=self.messages,
                user_db=self.user_db,
                parent=self,
            )
            outer.addWidget(self.units_editor)
        self.name.textChanged.connect(self._refresh_preview)
        self.enabled.toggled.connect(self._refresh_preview)
        self.position_left.toggled.connect(self._refresh_preview)
        self.position_right.toggled.connect(self._refresh_preview)
        self._set_logo_controls_compact(False)
        self._refresh_preview()
        for widget in (
            self.enabled,
            self.name,
            self.logo,
            self.choose_button,
            self.remove_button,
            self.position_left,
            self.position_right,
        ):
            widget.setEnabled(self.authorized)
        if not self.embedded:
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Save
                | QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self._save)
            buttons.rejected.connect(self.reject)
            outer.addWidget(buttons)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self._set_logo_controls_compact(self.width() < 560)

    def _set_logo_controls_compact(self, compact: bool) -> None:
        compact = bool(compact)
        if self._logo_controls_compact == compact:
            return
        layout = self._logo_controls_layout
        for widget in (self.logo, self.choose_button, self.remove_button):
            layout.removeWidget(widget)
        if compact:
            layout.addWidget(self.logo, 0, 0, 1, 2)
            layout.addWidget(self.choose_button, 1, 0)
            layout.addWidget(self.remove_button, 1, 1)
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 1)
        else:
            layout.addWidget(self.logo, 0, 0)
            layout.addWidget(self.choose_button, 0, 1)
            layout.addWidget(self.remove_button, 0, 2)
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 0)
            layout.setColumnStretch(2, 0)
        self._logo_controls_compact = compact
        self._logo_controls.updateGeometry()

    def _text(self, key: str, fallback: str) -> str:
        return str(self.messages.get(key, fallback))

    def _choose_logo(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            self._text("branding.choose_title", "Choose institution logo"),
            "",
            self._text("branding.image_filter", "Images (*.png *.jpg *.jpeg)"),
        )
        if filename:
            self._logo_source_path = Path(filename)
            self.logo.setText(self._logo_source_path.name)
            self._remove_logo = False
            self._refresh_preview()

    def _remove_selected_logo(self) -> None:
        self.logo.clear()
        self._logo_source_path = None
        self._remove_logo = True
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        logo_path = self._logo_source_path or self.service.logo_path()
        if self._remove_logo:
            logo_path = None
        self.preview.set_preview(
            enabled=self.enabled.isChecked(),
            facility_name=self.name.text(),
            logo_path=logo_path,
            position=(
                POSITION_TOP_LEFT
                if self.position_left.isChecked()
                else POSITION_TOP_RIGHT
            ),
        )

    def save_embedded(self) -> bool:
        unchanged = (
            self._logo_source_path is None
            and not self._remove_logo
            and bool(self._initial_config.get("enabled"))
            == self.enabled.isChecked()
            and str(self._initial_config.get("facility_name", "")).strip()
            == self.name.text().strip()
            and normalize_branding_position(self._initial_config.get("position"))
            == (
                POSITION_TOP_LEFT
                if self.position_left.isChecked()
                else POSITION_TOP_RIGHT
            )
        )
        if unchanged:
            return True
        try:
            saved = self.service.save(
                enabled=self.enabled.isChecked(),
                facility_name=self.name.text(),
                logo_source=str(self._logo_source_path or ""),
                position=(
                    POSITION_TOP_LEFT
                    if self.position_left.isChecked()
                    else POSITION_TOP_RIGHT
                ),
                actor=self.actor,
                authorized=self.authorized,
                remove_logo=self._remove_logo,
            )
        except (OSError, PermissionError, ValueError) as exc:
            QMessageBox.warning(
                self,
                self._text(
                    "branding.save_failed.title", "Cannot save branding"
                ),
                str(exc),
            )
            return False
        self._initial_config = dict(saved)
        self._logo_source_path = None
        saved_logo = self.service.logo_path(saved)
        self.logo.setText(saved_logo.name if saved_logo is not None else "")
        self._remove_logo = False
        self._refresh_preview()
        return True

    def _save(self) -> None:
        if self.save_embedded():
            self.accept()
