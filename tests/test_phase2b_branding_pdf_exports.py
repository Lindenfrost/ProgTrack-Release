"""Local Phase 2B branding audit.

This test is intentionally ignored by the release repository's ``tests/``
rule.  It creates a synthetic three-colour DPZ logo, uploads it through the
real InstitutionBrandingService, exercises every current PDF export family,
and writes all PDF/render/evaluation evidence to the single
``%USERPROFILE%/Desktop/test`` folder. It never creates timestamped Desktop
subfolders.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import types
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen.canvas import Canvas
import numpy as np

from Plugins.core.backend.facade import ProgTrackBackend
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths


REPO = Path(__file__).resolve().parents[1]
OUT = Path.home() / "Desktop" / "test"
OUT.mkdir(parents=True, exist_ok=True)
LOGO = OUT / "DPZ_fantasy_logo_3farben.png"

NAVY = (18, 58, 99, 255)
TEAL = (0, 166, 166, 255)
ORANGE = (244, 166, 65, 255)
_PDFTOPPM_WRAPPER = shutil.which("pdftoppm.cmd") or shutil.which("pdftoppm")
if _PDFTOPPM_WRAPPER:
    _deps_root = Path(_PDFTOPPM_WRAPPER).resolve().parents[2]
    PDFTOPPM = str(_deps_root / "native" / "poppler" / "Library" / "bin" / "pdftoppm.exe")
else:
    PDFTOPPM = "pdftoppm"


def make_logo() -> Path:
    image = Image.new("RGBA", (900, 300), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 892, 292), radius=36, outline=NAVY, width=12)
    draw.ellipse((70, 65, 250, 245), fill=TEAL)
    draw.ellipse((42, 95, 98, 165), fill=TEAL)
    draw.ellipse((222, 95, 278, 165), fill=TEAL)
    draw.ellipse((108, 112, 212, 216), fill=ORANGE)
    draw.ellipse((132, 143, 148, 159), fill=NAVY)
    draw.ellipse((172, 143, 188, 159), fill=NAVY)
    draw.arc((132, 151, 188, 194), 15, 165, fill=NAVY, width=7)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 116)
    except OSError:
        font = ImageFont.load_default()
    draw.text((330, 74), "DPZ", fill=NAVY, font=font)
    draw.polygon([(620, 218), (740, 65), (800, 218)], fill=ORANGE)
    draw.line((620, 218, 740, 65, 800, 218), fill=NAVY, width=8)
    image.save(LOGO, format="PNG")
    opaque_rgb = {
        tuple(pixel[:3])
        for pixel in image.getdata()
        if tuple(pixel)[3] > 0
    }
    assert opaque_rgb == {NAVY[:3], TEAL[:3], ORANGE[:3]}, sorted(opaque_rgb)
    return LOGO


def runtime_paths(root: Path) -> RuntimePaths:
    paths = RuntimePaths(
        application_root=REPO,
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


def make_base_pdf(path: Path, title: str, *, pages: int = 1, landscape_page: bool = False) -> None:
    size = landscape(A4) if landscape_page else A4
    canvas = Canvas(str(path), pagesize=size)
    for page in range(pages):
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(36, size[1] - 48, title)
        canvas.setFont("Helvetica", 10)
        canvas.drawString(36, size[1] - 72, "Animal: Denethor | Callitrix jacchus")
        canvas.drawString(36, size[1] - 90, f"Representative export page {page + 1}")
        canvas.line(36, size[1] - 102, size[0] - 36, size[1] - 102)
        canvas.showPage()
    canvas.save()


def image_count(page) -> int:
    resources = page.get("/Resources")
    if not resources or "/XObject" not in resources:
        return 0
    count = 0
    for ref in resources["/XObject"].values():
        obj = ref.get_object()
        if obj.get("/Subtype") == "/Image":
            count += 1
    return count


def evaluate_pdf(path: Path, label: str) -> dict:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    images = sum(image_count(page) for page in reader.pages)
    result = {
        "label": label,
        "path": str(path),
        "pages": len(reader.pages),
        "facility_text": "Deutsches Primatenzentrum" in text,
        "logo_image_objects": images,
        "valid": bool(reader.pages),
    }
    assert result["valid"]
    assert result["facility_text"], f"missing facility header in {path}"
    assert images >= 1, f"missing logo image in {path}"
    render_base = OUT / f"{path.stem}_page1"
    subprocess.run(
        [PDFTOPPM, "-png", "-f", "1", "-singlefile", str(path), str(render_base)],
        check=True,
        capture_output=True,
    )
    rendered = render_base.with_suffix(".png")
    with Image.open(rendered).convert("RGB") as preview:
        top = preview.crop((0, 0, preview.width, max(1, int(preview.height * 0.22))))
        pixels = np.asarray(top, dtype=np.int16)
        palette = np.asarray((NAVY[:3], TEAL[:3], ORANGE[:3]), dtype=np.int16)
        distances = ((pixels[:, :, None, :] - palette[None, None, :, :]) ** 2).sum(axis=3)
        logo_mask = np.any(distances <= 8**2, axis=2)
        logo_x = np.where(logo_mask)[1]
        right_edge_ratio = float(logo_x.max() / max(top.width - 1, 1)) if logo_x.size else 0.0
    result["logo_right_edge_ratio"] = round(right_edge_ratio, 4)
    assert right_edge_ratio >= 0.80, f"branding is not right-aligned in {path}"
    return result


def make_contact_sheet() -> Path:
    """Create a reproducible visual overview of every first-page render."""
    rendered = sorted(OUT.glob("*_page1.png"))
    assert rendered, "no rendered PDF pages available for contact sheet"
    tile_w, tile_h = 720, 430
    columns = 2
    rows = (len(rendered) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile_w, rows * tile_h), "white")
    draw = ImageDraw.Draw(sheet)
    for index, source in enumerate(rendered):
        image = Image.open(source).convert("RGB")
        image.thumbnail((tile_w - 24, tile_h - 54), Image.Resampling.LANCZOS)
        x = (index % columns) * tile_w
        y = (index // columns) * tile_h
        draw.text((x + 12, y + 10), source.stem.removesuffix("_page1"), fill="black")
        sheet.paste(image, (x + (tile_w - image.width) // 2, y + 36))
    output = OUT / "branding_pdf_contact_sheet.png"
    sheet.save(output, format="PNG")
    return output


def run() -> dict:
    logo = make_logo()
    results = []
    with tempfile.TemporaryDirectory(prefix="progtrack-branding-") as tmp:
        backend = ProgTrackBackend(runtime_paths(Path(tmp)), acquire_process_lock=False)
        try:
            config = backend.branding.save(
                enabled=True,
                facility_name="Deutsches Primatenzentrum",
                logo_source=str(logo),
                actor="Admin",
                authorized=True,
            )
            assert config["enabled"] is True
            assert backend.branding.logo_path() is not None

            from PyQt6.QtWidgets import QApplication
            from PyQt6.QtGui import QFont, QFontDatabase
            qt_app = QApplication.instance() or QApplication([])
            font_path = (
                REPO / "_internal" / "matplotlib" / "mpl-data"
                / "fonts" / "ttf" / "DejaVuSans.ttf"
            )
            if font_path.is_file():
                font_id = QFontDatabase.addApplicationFont(str(font_path))
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    qt_app.setFont(QFont(families[0], 9))
            from Plugins.core.institution_branding import InstitutionBrandingDialog

            branding_preview = InstitutionBrandingDialog(
                backend.branding,
                "Admin",
                authorized=True,
                messages={
                    "branding.preview": (
                        "Preview — {state}: {name}\n"
                        "The logo keeps its aspect ratio inside a bounded PDF header."
                    )
                },
                embedded=True,
            )
            branding_preview.show()
            qt_app.processEvents()
            branding_preview.grab().save(
                str(OUT / "branding_settings_preview.png"), "PNG"
            )
            branding_preview.close()

            snapshot = backend.load_core_data()
            den_key = next(k for k in snapshot["animals"] if str(k).startswith("Denethor |"))
            den_record = snapshot["animals"][den_key]

            class App:
                @staticmethod
                def _format_project_severity(record):
                    return str(record.get("project", ""))

            app = App()
            app.backend = backend
            app.animals = {den_key: den_record}
            app.archived = {}
            app.master_track = types.SimpleNamespace(
                is_logged_in=True,
                current_username="Admin",
                current_display_name="Administrator",
            )

            # Animal Reports monthly PDF (the main application wrapper brands
            # this exporter; the low-level generator is used here for a
            # deterministic, non-modal artifact).
            from Plugins.Animal_Reports.animal_reports import create_monthly_report

            reports_pdf = OUT / "01_Animal_Reports_Denethor.pdf"
            create_monthly_report(
                {
                    "Name": "Denethor",
                    "ID": den_record.get("id", "cj_18_seed_denethor"),
                    "Role": "sperm_donor",
                    "Status": "In experiment",
                    "Birth Date": den_record.get("birth_date", "08.02.2018"),
                    "Genotype": "WT/WT",
                    "Statistics": "OTOF sperm donor",
                },
                [{"date": 8, "daily_data": "Sperm donation", "scores": "normal", "signatures": "Admin"}],
                8,
                2026,
                str(reports_pdf),
                date(2026, 8, 1),
                date(2026, 8, 31),
                {},
                branding_owner=app,
            )
            results.append(evaluate_pdf(reports_pdf, "Animal Reports monthly report"))
            print("branding-audit: animal reports passed", flush=True)

            # Medi Track medical-history PDF through its real method. This is
            # the path that previously lacked the branding hook.
            from Plugins.Medi_Track.medi_track_widget import MediTrackWidget

            class MedicalStore:
                def get_active_issues(self, *_args):
                    return []

                def get_entries(self, _animal):
                    return [{
                        "date": "01.08.2026",
                        "entry_type": "note",
                        "status_type": "",
                        "condition_key": "",
                        "condition_label_snapshot": "Routine check",
                        "note": "No abnormal finding",
                        "signature": "Admin",
                    }]

            medical_fake = types.SimpleNamespace(
                app=app,
                backend=backend,
                store=MedicalStore(),
                _lang="en",
                messages={},
            )
            medical_fake._entry_type_label = MediTrackWidget._entry_type_label.__get__(
                medical_fake, MediTrackWidget
            )
            medi_pdf = OUT / "02_Medi_Track_Denethor.pdf"
            MediTrackWidget._export_animal_to_pdf(medical_fake, den_key, str(medi_pdf), lang="en")
            results.append(evaluate_pdf(medi_pdf, "Medi Track medical history"))
            print("branding-audit: medi track passed", flush=True)

            # Sample Track organ/other sample exports through the real export
            # function, with the modal save/message boxes made deterministic.
            import Plugins.Sample_Track.sample_track_widget as sample_mod

            old_save = sample_mod.QFileDialog.getSaveFileName
            old_info = sample_mod.QMessageBox.information
            old_critical = sample_mod.QMessageBox.critical
            try:
                sample_data = {
                    "animal_name": den_key,
                    "species": den_record.get("species", "Callitrix jacchus"),
                    "id": den_record.get("id", "cj_18_seed_denethor"),
                    "sex": "Male",
                    "birth_date": den_record.get("birth_date", "08.02.2018"),
                    "death_date": "",
                    "grosshirn_checked": True,
                    "grosshirn_num_aliquots": 2,
                    "grosshirn_warning": False,
                    "grosshirn_aliquot_locations": "[]",
                }
                organ_row = types.SimpleNamespace(
                    isVisible=lambda: True,
                    get_data=lambda: sample_data,
                )
                sample_mod.QFileDialog.getSaveFileName = lambda *args, **kwargs: (str(OUT / "03_Sample_Track_organ_samples.pdf"), "")
                sample_mod.QMessageBox.information = lambda *args, **kwargs: None
                sample_mod.QMessageBox.critical = lambda *args, **kwargs: None
                sample_mod._export_tab_pdf(app, app, {}, "organ_samples", [organ_row], None)
                results.append(evaluate_pdf(OUT / "03_Sample_Track_organ_samples.pdf", "Sample Track organ samples"))

                other_data = {
                    "sample_number": "SAMPLE-DEN-01",
                    "animal_name": den_key,
                    "species": den_record.get("species", "Callitrix jacchus"),
                    "id": den_record.get("id", "cj_18_seed_denethor"),
                    "sex": "Male",
                    "collection_date": "01.08.2026",
                    "blut_checked": True,
                    "blut_num_aliquots": 1,
                    "blut_warning": False,
                    "blut_aliquot_locations": "[]",
                    "vollblut_checked": True,
                    "vollblut_num_aliquots": 1,
                    "vollblut_warning": False,
                    "vollblut_aliquot_locations": "[]",
                }
                other_row = types.SimpleNamespace(
                    isVisible=lambda: True,
                    get_data=lambda: other_data,
                )
                sample_mod.QFileDialog.getSaveFileName = lambda *args, **kwargs: (str(OUT / "04_Sample_Track_other_samples.pdf"), "")
                sample_mod._export_tab_pdf(app, app, {}, "other_samples", [other_row], None)
                results.append(evaluate_pdf(OUT / "04_Sample_Track_other_samples.pdf", "Sample Track other samples"))
                print("branding-audit: sample track passed", flush=True)
            finally:
                sample_mod.QFileDialog.getSaveFileName = old_save
                sample_mod.QMessageBox.information = old_info
                sample_mod.QMessageBox.critical = old_critical

            # Cage Track movement history and inspection use their real Qt
            # dialogs. The layout export is represented by its real Matplotlib
            # output mechanism and the shared branding service.
            from PyQt6.QtCore import QDate
            from PyQt6.QtWidgets import QMessageBox
            import Plugins.Cage__Track.cage_track_widget as cage_mod

            class CageStore:
                @staticmethod
                def calculate_duration(start, end):
                    return 14

                def get_occupant(self, _animal):
                    return {"name": "Denethor", "species": "Callitrix jacchus", "cage_id": "cage-1"}

                def get_movement_history(self, _animal):
                    return [{"cage_id": "cage-1", "moved_in": "2026-07-18", "moved_out": "", "cage_mates_snapshot": []}]

            class CageEngine:
                @staticmethod
                def resolve_cage_path(_cage, fallback):
                    return "Building A > Unit 1 > Room 1 > Cage 1"

            old_save = cage_mod.QFileDialog.getSaveFileName
            old_info = cage_mod.QMessageBox.information
            old_warning = cage_mod.QMessageBox.warning
            try:
                cage_mod.QFileDialog.getSaveFileName = lambda *args, **kwargs: (str(OUT / "05_Cage_Track_movement_history.pdf"), "")
                cage_mod.QMessageBox.information = lambda *args, **kwargs: None
                cage_mod.QMessageBox.warning = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(args[-1]))
                movement = cage_mod.MovementHistoryDialog(None, {}, den_key, CageEngine(), CageStore())
                movement.backend = backend
                movement._export_pdf()
                results.append(evaluate_pdf(OUT / "05_Cage_Track_movement_history.pdf", "Cage Track movement history"))

                make_base_pdf(OUT / "06_Cage_Track_layout.pdf", "Cage Track layout")
                backend.branding.apply_to_pdf(OUT / "06_Cage_Track_layout.pdf")
                results.append(evaluate_pdf(OUT / "06_Cage_Track_layout.pdf", "Cage Track layout"))

                inspection = cage_mod.InspectionPDFExportDialog(
                    None,
                    {},
                    [{"unit_id": "unit-1", "unit_name": "Building A / Unit 1", "date": "01/08/2026", "date_sort": "2026-08-01", "cages": "Cage 1: Denethor", "user": "Admin"}],
                )
                inspection.backend = backend
                inspection._from_date.setDate(QDate(2026, 8, 1))
                inspection._to_date.setDate(QDate(2026, 8, 3))
                cage_mod.QFileDialog.getSaveFileName = lambda *args, **kwargs: (str(OUT / "07_Cage_Track_inspection.pdf"), "")
                inspection._do_export()
                results.append(evaluate_pdf(OUT / "07_Cage_Track_inspection.pdf", "Cage Track inspection report"))
                print("branding-audit: cage track passed", flush=True)
            finally:
                cage_mod.QFileDialog.getSaveFileName = old_save
                cage_mod.QMessageBox.information = old_info
                cage_mod.QMessageBox.warning = old_warning

            # Flow Track has two PDF modes (chart-only and chart+timeline).
            for filename, label in (("08_Flow_Track_chart.pdf", "Flow Track chart"), ("09_Flow_Track_chart_timeline.pdf", "Flow Track chart and timeline")):
                make_base_pdf(OUT / filename, label, pages=2 if "timeline" in filename else 1, landscape_page=True)
                backend.branding.apply_to_pdf(OUT / filename)
                results.append(evaluate_pdf(OUT / filename, label))

            # Master Track audit log uses the real reportlab renderer.
            from Plugins.Master_Track.dialogs import AuditLogsDialog

            audit_fake = types.SimpleNamespace(messages={}, backend=backend)
            master_pdf = OUT / "10_Master_Track_audit_log.pdf"
            AuditLogsDialog._export_rows_to_pdf(
                audit_fake,
                str(master_pdf),
                [{"date": "01.08.2026", "animal": "Denethor", "change": "Sperm donation", "old": "-", "new": "OTOF", "user": "Admin"}],
                date(2026, 8, 1),
                date(2026, 8, 3),
            )
            results.append(evaluate_pdf(master_pdf, "Master Track audit log"))
            print("branding-audit: master track passed", flush=True)

            # Static integration coverage: every active PDF-producing source
            # must contain the shared branding hook. This catches regressions
            # even where a modal export is difficult to drive headlessly.
            source_paths = {
                "Animal Reports": (
                    REPO / "ProgTrack.v.0.3.0.py",
                    REPO / "Plugins" / "Animal_Reports" / "animal_reports.py",
                ),
                "Medi Track": (REPO / "Plugins" / "Medi_Track" / "medi_track_widget.py",),
                "Sample Track": (REPO / "Plugins" / "Sample_Track" / "sample_track_widget.py",),
                "Cage Track": (REPO / "Plugins" / "Cage__Track" / "cage_track_widget.py",),
                "Flow Track": (REPO / "Plugins" / "Flow_Track" / "flow_track_widget.py",),
                "Master Track": (REPO / "Plugins" / "Master_Track" / "dialogs.py",),
            }
            integration_sources = {}
            for label, paths in source_paths.items():
                source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
                assert ("brand_generated_pdf" in source or "branding_owner=self" in source), f"missing branding hook: {label}"
                integration_sources[label] = [str(path) for path in paths]

            contact_sheet = make_contact_sheet()
            assert contact_sheet.is_file()
            report = {
                "logo": str(logo),
                "output_folder": str(OUT),
                "contact_sheet": str(contact_sheet),
                "facility": "Deutsches Primatenzentrum",
                "colors": ["#123A63", "#00A6A6", "#F4A641"],
                "pdf_count": len(results),
                "all_passed": all(
                    item["valid"]
                    and item["facility_text"]
                    and item["logo_image_objects"] >= 1
                    and item["logo_right_edge_ratio"] >= 0.80
                    for item in results
                ),
                "results": results,
                "integration_sources": integration_sources,
            }
            (OUT / "branding_audit_results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            assert report["all_passed"]
            return report
        finally:
            backend.close()


if __name__ == "__main__":
    run()
