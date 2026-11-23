"""Focused contracts for centralized institution-branding placement."""

from __future__ import annotations

import copy
import tempfile
import unittest
import uuid
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ContentStream
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from Plugins.core.institution_branding import (
    InstitutionBrandingService,
    POSITION_TOP_LEFT,
    POSITION_TOP_RIGHT,
)
from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths


class _Records:
    def __init__(self) -> None:
        self.values = {}

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, value, **_kwargs):
        self.values[(namespace, record_id)] = copy.deepcopy(value)
        return 1


class _Documents:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.records = {}

    def add(self, source, **_kwargs):
        document_id = str(uuid.uuid4())
        target = self.root / f"{document_id}{Path(source).suffix.lower()}"
        target.write_bytes(Path(source).read_bytes())
        record = {"document_id": document_id, "path": str(target)}
        self.records[document_id] = record
        return dict(record)

    def get(self, document_id):
        return dict(self.records[document_id])

    @staticmethod
    def payload_path(record):
        return Path(record["path"])


class _Audit:
    def __init__(self) -> None:
        self.entries = []

    def append(self, **entry):
        self.entries.append(entry)


def _base_pdf(path: Path, *, pages: int = 1) -> None:
    canvas = Canvas(str(path), pagesize=A4)
    canvas.setTitle("Source PDF metadata retained")
    for page_number in range(1, pages + 1):
        canvas.drawString(
            36, A4[1] - 100, f"Representative report body page {page_number}"
        )
        canvas.showPage()
    canvas.save()


def _logo_x_bounds(path: Path) -> tuple[float, float, float]:
    reader = PdfReader(str(path))
    page = reader.pages[0]
    stream = ContentStream(page.get_contents(), reader)
    last_matrix = None
    image_matrix = None
    for operands, operator in stream.operations:
        if operator == b"cm" and len(operands) == 6:
            last_matrix = tuple(float(value) for value in operands)
        elif operator == b"Do" and last_matrix is not None:
            image_matrix = last_matrix
    if image_matrix is None:
        raise AssertionError("branding logo drawing operation not found")
    width, x = image_matrix[0], image_matrix[4]
    return x, x + width, float(page.mediabox.width)


def _image_count(page) -> int:
    resources = page.get("/Resources")
    if not resources or "/XObject" not in resources:
        return 0
    return sum(
        reference.get_object().get("/Subtype") == "/Image"
        for reference in resources["/XObject"].values()
    )


class BrandingPositionTest(unittest.TestCase):
    def test_position_is_persisted_and_applied_at_both_page_edges(self):
        with tempfile.TemporaryDirectory(prefix="progtrack-brand-position-") as temporary:
            root = Path(temporary)
            logo = root / "logo.png"
            Image.new("RGB", (600, 180), "#25a6a6").save(logo)
            backend = ProgTrackBackend(
                runtime_paths(root),
                acquire_process_lock=False,
                choose_demo_seed=lambda: False,
            )
            self.addCleanup(backend.close)
            service = backend.branding

            for position in (POSITION_TOP_LEFT, POSITION_TOP_RIGHT):
                service.save(
                    enabled=True,
                    facility_name="Example Research Facility",
                    logo_source=(
                        str(logo)
                        if not service.load().get("logo_document_id")
                        else ""
                    ),
                    position=position,
                    actor="Admin",
                    authorized=True,
                )
                pdf = root / f"{position}.pdf"
                _base_pdf(pdf)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", DeprecationWarning)
                    self.assertTrue(service.apply_to_pdf(pdf))
                left, right, page_width = _logo_x_bounds(pdf)
                if position == POSITION_TOP_LEFT:
                    self.assertLessEqual(left, 20.0)
                else:
                    self.assertGreaterEqual(right, page_width - 20.0)
                self.assertIn(
                    "Example Research Facility",
                    PdfReader(str(pdf)).pages[0].extract_text(),
                )

            self.assertEqual(service.load()["position"], POSITION_TOP_RIGHT)
            branding_events = [
                event for event in backend.audit.list_events()
                if event["action"] == "institution_branding_update"
            ]
            self.assertTrue(branding_events)
            self.assertEqual(branding_events[0]["payload"]["position"], POSITION_TOP_RIGHT)

    def test_multipage_clone_preserves_order_metadata_and_brands_every_page(self):
        with tempfile.TemporaryDirectory(prefix="progtrack-brand-multipage-") as temporary:
            root = Path(temporary)
            logo = root / "logo.png"
            Image.new("RGB", (600, 180), "#25a6a6").save(logo)
            backend = ProgTrackBackend(
                runtime_paths(root),
                acquire_process_lock=False,
                choose_demo_seed=lambda: False,
            )
            self.addCleanup(backend.close)
            service = backend.branding
            service.save(
                enabled=True,
                facility_name="Example Research Facility",
                logo_source=str(logo),
                position=POSITION_TOP_RIGHT,
                actor="Admin",
                authorized=True,
            )
            pdf = root / "multipage.pdf"
            _base_pdf(pdf, pages=3)

            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                self.assertTrue(service.apply_to_pdf(pdf))

            reader = PdfReader(str(pdf))
            self.assertEqual(len(reader.pages), 3)
            self.assertEqual(reader.metadata.title, "Source PDF metadata retained")
            for page_number, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                self.assertIn(f"Representative report body page {page_number}", text)
                self.assertIn("Example Research Facility", text)
                self.assertGreaterEqual(_image_count(page), 1)

    def test_failed_write_keeps_original_pdf_and_removes_temporary_output(self):
        with tempfile.TemporaryDirectory(prefix="progtrack-brand-write-failure-") as temporary:
            root = Path(temporary)
            logo = root / "logo.png"
            Image.new("RGB", (600, 180), "#25a6a6").save(logo)
            backend = ProgTrackBackend(
                runtime_paths(root),
                acquire_process_lock=False,
                choose_demo_seed=lambda: False,
            )
            self.addCleanup(backend.close)
            backend.branding.save(
                enabled=True,
                facility_name="Example Research Facility",
                logo_source=str(logo),
                actor="Admin",
                authorized=True,
            )
            pdf = root / "original.pdf"
            _base_pdf(pdf)
            original_bytes = pdf.read_bytes()

            with patch.object(PdfWriter, "write", side_effect=OSError("simulated write failure")):
                with self.assertRaisesRegex(OSError, "simulated write failure"):
                    backend.branding.apply_to_pdf(pdf)

            self.assertEqual(pdf.read_bytes(), original_bytes)
            self.assertEqual(list(root.glob(".original-branding-*.pdf")), [])

    def test_missing_or_invalid_position_retains_right_edge_compatibility(self):
        backend = SimpleNamespace(
            records=_Records(), documents=SimpleNamespace(), audit=SimpleNamespace()
        )
        service = InstitutionBrandingService(backend)
        self.assertEqual(service.load()["position"], POSITION_TOP_RIGHT)
        backend.records.put(
            "configuration",
            "institution-branding",
            {"enabled": False, "position": "somewhere"},
        )
        self.assertEqual(service.load()["position"], POSITION_TOP_RIGHT)


if __name__ == "__main__":
    unittest.main()
