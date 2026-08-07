"""Facility branding configuration and repeatable compact PDF headers."""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)


CONFIG_NAMESPACE = "configuration"
CONFIG_RECORD = "institution-branding"
ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_LOGO_BYTES = 10 * 1024 * 1024


class InstitutionBrandingService:
    def __init__(self, backend: Any):
        self.backend = backend

    def load(self) -> dict[str, Any]:
        value = self.backend.records.get(
            CONFIG_NAMESPACE,
            CONFIG_RECORD,
            default={"enabled": False, "facility_name": "", "logo_document_id": ""},
        )
        return value if isinstance(value, dict) else {}

    def save(
        self,
        *,
        enabled: bool,
        facility_name: str,
        logo_source: str = "",
        actor: str,
        authorized: bool = False,
        remove_logo: bool = False,
    ) -> dict[str, Any]:
        if not authorized:
            raise PermissionError(
                "Institution branding requires Lord, Master, or Manager authority."
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
        """Overlay a compact, right-aligned name/logo header on every page.

        The exported document remains the primary content.  Branding occupies
        only a bounded top-right header region: the logo is the rightmost
        element and the facility name is right-aligned immediately to its
        left.  The available logo width is reduced before the font is reduced
        so long facility names remain readable without clipping or overlap.
        """
        config = self.load()
        if not config.get("enabled"):
            return False
        facility = str(config.get("facility_name", "")).strip()
        logo = self.logo_path(config)
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
            right_edge = width - margin
            center_y = height - margin - header_h / 2.0
            logo_size: tuple[float, float] | None = None
            if logo is not None:
                with Image.open(logo) as image:
                    iw, ih = image.size
                max_w, max_h = 216.0, header_h - 6.0
                scale = min(max_w / max(iw, 1), max_h / max(ih, 1))
                logo_size = (iw * scale, ih * scale)
            if facility:
                # Fit the name and logo into the bounded right header.  The
                # normal 8pt font is retained whenever possible; only very
                # long names reduce the logo width/font size, never the page
                # content area or the right-edge alignment.
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
                logo_left = right_edge - (logo_size[0] if logo_size else 0.0)
                text_right = logo_left - gap if logo_size else right_edge
                layer.setFont(font_name, font_size)
                layer.drawRightString(
                    text_right,
                    center_y - font_size * 0.35,
                    facility,
                )
            if logo_size:
                draw_w, draw_h = logo_size
                layer.drawImage(
                    ImageReader(str(logo)),
                    right_edge - draw_w,
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


class InstitutionBrandingDialog(QDialog):
    def __init__(
        self,
        service: InstitutionBrandingService,
        actor: str,
        *,
        authorized: bool = False,
        messages: dict[str, Any] | None = None,
        embedded: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.actor = actor
        self.authorized = authorized
        self.messages = messages or {}
        self.embedded = bool(embedded)
        self._remove_logo = False
        self.setWindowTitle(self._text("branding.title", "Institution branding"))
        self.setMinimumWidth(560)
        if self.embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        config = service.load()
        self._initial_config = dict(config)

        outer = QVBoxLayout(self)
        form = QFormLayout()
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
        self.choose_button = QPushButton(
            self._text("branding.choose", "Choose PNG/JPEG…")
        )
        self.choose_button.clicked.connect(self._choose_logo)
        self.remove_button = QPushButton(
            self._text("branding.remove", "Remove")
        )
        self.remove_button.clicked.connect(self._remove_selected_logo)
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo, 1)
        logo_row.addWidget(self.choose_button)
        logo_row.addWidget(self.remove_button)
        form.addRow("", self.enabled)
        form.addRow(
            self._text("branding.institution_name", "Institution name:"),
            self.name,
        )
        form.addRow(self._text("branding.logo", "Logo:"), logo_row)
        outer.addLayout(form)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        outer.addWidget(self.preview)
        self.preview_logo = QLabel()
        self.preview_logo.setMinimumHeight(132)
        self.preview_logo.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        outer.addWidget(self.preview_logo)
        self.name.textChanged.connect(self._refresh_preview)
        self.enabled.toggled.connect(self._refresh_preview)
        self._refresh_preview()
        for widget in (
            self.enabled,
            self.name,
            self.logo,
            self.choose_button,
            self.remove_button,
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
            self.logo.setText(filename)
            self._remove_logo = False
            self._refresh_preview()

    def _remove_selected_logo(self) -> None:
        self.logo.clear()
        self._remove_logo = True
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        state = (
            self._text("branding.state.enabled", "Enabled")
            if self.enabled.isChecked()
            else self._text("branding.state.disabled", "Disabled")
        )
        name = self.name.text().strip() or self._text(
            "branding.no_name", "(no institution name)"
        )
        self.preview.setText(
            self._text(
                "branding.preview",
                "Preview — {state}: {name}\n"
                "The logo keeps its aspect ratio inside a bounded PDF header.",
            ).format(state=state, name=name)
        )
        self.preview_logo.clear()
        logo_path = Path(self.logo.text()) if self.logo.text() else self.service.logo_path()
        if not self._remove_logo and logo_path and logo_path.is_file():
            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                self.preview_logo.setPixmap(
                    pixmap.scaled(
                        288, 126,
                        aspectRatioMode=Qt.AspectRatioMode.KeepAspectRatio,
                        transformMode=Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def save_embedded(self) -> bool:
        unchanged = (
            not self.logo.text()
            and not self._remove_logo
            and bool(self._initial_config.get("enabled"))
            == self.enabled.isChecked()
            and str(self._initial_config.get("facility_name", "")).strip()
            == self.name.text().strip()
        )
        if unchanged:
            return True
        try:
            saved = self.service.save(
                enabled=self.enabled.isChecked(),
                facility_name=self.name.text(),
                logo_source=self.logo.text(),
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
        self.logo.clear()
        self._remove_logo = False
        return True

    def _save(self) -> None:
        if self.save_embedded():
            self.accept()
