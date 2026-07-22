# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.2
# Module: small cross-platform desktop/path helpers.

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def _qt_writable_location(location_name: str) -> Optional[Path]:
    try:
        from PyQt6.QtCore import QStandardPaths
    except Exception:
        return None
    try:
        location = getattr(QStandardPaths.StandardLocation, location_name)
        raw_path = QStandardPaths.writableLocation(location)
    except Exception:
        return None
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    return path if path.exists() else None


def default_export_directory(home: Optional[Path] = None) -> Path:
    """Return a valid cross-platform default folder for export dialogs."""
    desktop = _qt_writable_location("DesktopLocation")
    if desktop:
        return desktop
    documents = _qt_writable_location("DocumentsLocation")
    if documents:
        return documents

    home_path = Path.home() if home is None else Path(home)
    desktop_path = home_path / "Desktop"
    if desktop_path.exists():
        return desktop_path
    documents_path = home_path / "Documents"
    if documents_path.exists():
        return documents_path
    return home_path


def default_save_path(filename: str, home: Optional[Path] = None) -> str:
    return str(default_export_directory(home=home) / filename)


def open_local_path(path: os.PathLike[str] | str) -> bool:
    """Open a local file/folder with the platform desktop service if available."""
    target = Path(path)
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        if QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            return True
    except Exception:
        pass

    system = platform.system()
    try:
        if system == "Windows" and hasattr(os, "startfile"):
            os.startfile(str(target))  # type: ignore[attr-defined]
            return True
        if system == "Darwin" and shutil.which("open"):
            subprocess.Popen(["open", str(target)])
            return True
        if shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", str(target)])
            return True
    except Exception:
        return False
    return False


def exact_case_path_exists(path: os.PathLike[str] | str) -> bool:
    """Return True only when every path component matches filesystem casing."""
    target = Path(path)
    if not target.exists():
        return False
    parts = target.resolve().parts
    if not parts:
        return False
    current = Path(parts[0])
    for part in parts[1:]:
        try:
            names = {child.name for child in current.iterdir()}
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return True
