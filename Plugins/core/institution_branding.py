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
        """Overlay a compact name/logo header on every existing PDF page."""
        config = self.load()
        if not config.get("enabled"):
            return False
        facility = str(config.get("facility_name", "")).strip()
        logo = self.logo_path(config)
        if not facility and logo is None:
            return False

        from pypdf import PdfReader, PdfWriter
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
            header_h = min(34.0, max(24.0, height * 0.04))
            x = margin
            if logo is not None:
                with Image.open(logo) as image:
                    iw, ih = image.size
                max_w, max_h = 72.0, header_h - 4.0
                scale = min(max_w / max(iw, 1), max_h / max(ih, 1))
                draw_w, draw_h = iw * scale, ih * scale
                layer.drawImage(
                    ImageReader(str(logo)),
                    x,
                    height - margin - draw_h,
                    width=draw_w,
                    height=draw_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
                x += draw_w + 8.0
            if facility:
                layer.setFont("Helvetica-Bold", 8)
                layer.drawString(x, height - margin - header_h / 2.0, facility)
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
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.actor = actor
        self.authorized = authorized
        self._remove_logo = False
        self.setWindowTitle("Institution branding")
        self.setMinimumWidth(480)
        config = service.load()

        outer = QVBoxLayout(self)
        form = QFormLayout()
        self.enabled = QCheckBox("Include institution header in generated PDFs")
        self.enabled.setChecked(bool(config.get("enabled")))
        self.name = QLineEdit(str(config.get("facility_name", "")))
        self.logo = QLineEdit()
        self.logo.setReadOnly(True)
        choose = QPushButton("Choose PNG/JPEG…")
        choose.clicked.connect(self._choose_logo)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected_logo)
        logo_row = QHBoxLayout()
        logo_row.addWidget(self.logo, 1)
        logo_row.addWidget(choose)
        logo_row.addWidget(remove)
        form.addRow("", self.enabled)
        form.addRow("Institution name:", self.name)
        form.addRow("Logo:", logo_row)
        outer.addLayout(form)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        outer.addWidget(self.preview)
        self.preview_logo = QLabel()
        self.preview_logo.setMinimumHeight(44)
        self.preview_logo.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self.preview_logo)
        self.name.textChanged.connect(self._refresh_preview)
        self.enabled.toggled.connect(self._refresh_preview)
        self._refresh_preview()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _choose_logo(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Choose institution logo", "", "Images (*.png *.jpg *.jpeg)"
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
        state = "Enabled" if self.enabled.isChecked() else "Disabled"
        name = self.name.text().strip() or "(no institution name)"
        self.preview.setText(
            f"Preview — {state}: {name}\n"
            "The logo keeps its aspect ratio inside a compact PDF header."
        )
        self.preview_logo.clear()
        logo_path = Path(self.logo.text()) if self.logo.text() else self.service.logo_path()
        if not self._remove_logo and logo_path and logo_path.is_file():
            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                self.preview_logo.setPixmap(
                    pixmap.scaled(
                        96, 42,
                        aspectRatioMode=Qt.AspectRatioMode.KeepAspectRatio,
                        transformMode=Qt.TransformationMode.SmoothTransformation,
                    )
                )

    def _save(self) -> None:
        self.service.save(
            enabled=self.enabled.isChecked(),
            facility_name=self.name.text(),
            logo_source=self.logo.text(),
            actor=self.actor,
            authorized=self.authorized,
            remove_logo=self._remove_logo,
        )
        self.accept()
