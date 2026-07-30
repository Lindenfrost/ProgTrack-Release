#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Smoke-test native modules through the frozen ProgTrack launcher."""

from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import sqlite3
import ssl
import tempfile
from contextlib import closing
from pathlib import Path

import matplotlib
import numexpr
import numpy
import openpyxl
import pandas
import pyqtgraph
import reportlab
import scipy
from PIL import Image
from PyQt6 import QtCore, QtGui, QtWidgets
from scipy import stats as scipy_stats
from scipy.interpolate import interp1d
from scipy.optimize import minimize


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="progtrack-frozen-smoke-") as temp_dir:
        database_path = Path(temp_dir) / "smoke.sqlite3"
        with closing(sqlite3.connect(database_path)) as connection:
            with connection:
                connection.execute("CREATE TABLE smoke (value TEXT NOT NULL)")
                connection.execute("INSERT INTO smoke (value) VALUES (?)", ("ok",))
        with closing(sqlite3.connect(database_path)) as connection:
            value = connection.execute("SELECT value FROM smoke").fetchone()[0]
        if value != "ok":
            raise RuntimeError("SQLite persistence smoke test failed")

        interpolated = float(interp1d([0.0, 1.0], [0.0, 2.0])(0.5))
        optimized = float(minimize(lambda x: (x[0] - 1.0) ** 2, [0.0]).x[0])
        f_cdf = float(scipy_stats.f.cdf(1.0, 2, 10))
        if not (abs(interpolated - 1.0) < 1e-9 and abs(optimized - 1.0) < 1e-4):
            raise RuntimeError("SciPy numerical smoke test failed")

    result = {
        "status": "ok",
        "ctypes": ctypes.__name__,
        "multiprocessing": multiprocessing.__name__,
        "sqlite_version": sqlite3.sqlite_version,
        "openssl_version": ssl.OPENSSL_VERSION,
        "qt_version": QtCore.QT_VERSION_STR,
        "numpy_version": numpy.__version__,
        "pandas_version": pandas.__version__,
        "scipy_version": scipy.__version__,
        "scipy_f_cdf": f_cdf,
        "matplotlib_version": matplotlib.__version__,
        "openpyxl_version": openpyxl.__version__,
        "reportlab_version": reportlab.Version,
        "numexpr_version": numexpr.__version__,
        "pyqtgraph_version": pyqtgraph.__version__,
        "pillow_module": Image.__name__,
        "qt_modules": [QtGui.__name__, QtWidgets.__name__],
    }
    result_json = json.dumps(result, ensure_ascii=False, sort_keys=True)
    result_path = os.environ.get("PROGTRACK_SMOKE_RESULT")
    if result_path:
        Path(result_path).write_text(result_json + "\n", encoding="utf-8")
    print(result_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
