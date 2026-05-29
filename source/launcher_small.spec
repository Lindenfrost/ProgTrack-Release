# -*- mode: python ; coding: utf-8 -*-
"""
Smaller OneDir PyInstaller spec for ProgTrack.

This keeps the ProgTrack script and plugins external, but includes the Python
runtime and the libraries needed by the discovered ProgTrack.v.*.py files.
"""

import importlib.util
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


project_dir = Path(SPECPATH).resolve()
launcher_script = project_dir / "launcher.py"
payload_candidates = [
    project_dir / "payload",
    project_dir / "dist" / "ProgTrack",
]
payload_dir = next((path for path in payload_candidates if path.exists()), None)
icon_candidates = [
    project_dir / "progtrack_icon.ico",
    project_dir / "icons" / "progtrack_icon.ico",
]
icon_path = next((path for path in icon_candidates if path.exists()), None)


def package_dir(module_name):
    spec = importlib.util.find_spec(module_name)
    if spec and spec.origin:
        return Path(spec.origin).resolve().parent
    return None


pyqt6_dir = package_dir("PyQt6")
qt6_dir = pyqt6_dir / "Qt6" if pyqt6_dir else None
qt6_bin_dir = qt6_dir / "bin" if qt6_dir else None
qt6_plugins_dir = qt6_dir / "plugins" if qt6_dir else None


def collect_tree_files(source_dir, target_dir):
    source_dir = Path(source_dir)
    if not source_dir.exists():
        return []
    return [
        (str(path), str(Path(target_dir) / path.relative_to(source_dir).parent))
        for path in source_dir.rglob("*")
        if path.is_file()
    ]


analysis_scripts = [str(launcher_script)]
if payload_dir:
    analysis_scripts.extend(str(path) for path in sorted(payload_dir.glob("ProgTrack.v.*.py")))
    plugins_dir = payload_dir / "Plugins"
    if plugins_dir.exists():
        analysis_scripts.extend(
            str(path)
            for path in sorted(plugins_dir.rglob("*.py"))
            if "__pycache__" not in path.parts
            and not any("archive" in part.lower() for part in path.parts)
            and not path.name.lower().startswith("working_")
            and "working" not in path.name.lower()
        )


hiddenimports_file = project_dir / "hiddenimports.txt"
hiddenimports = [
    line.strip()
    for line in hiddenimports_file.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.strip().startswith("#")
]


datas = []
datas += collect_data_files("matplotlib", includes=["mpl-data/**"])
datas += collect_data_files("openpyxl")
datas += collect_data_files("reportlab")


runtime_dll_names = [
    # Copy the Qt runtime DLLs needed by the launcher and the ProgTrack plugins
    # once into _internal, where launcher.py adds them to PATH.
    "Qt6Core.dll",
    "Qt6Gui.dll",
    "Qt6Widgets.dll",
    "Qt6PrintSupport.dll",
    "Qt6Network.dll",
    "Qt6Svg.dll",
    "Qt6Multimedia.dll",
    "Qt6MultimediaWidgets.dll",
    "Qt6OpenGL.dll",
    "Qt6OpenGLWidgets.dll",
    "freetype.dll",
    "libpng16.dll",
    # Pillow image codecs used indirectly by matplotlib.
    "libwebp.dll",
    "libsharpyuv.dll",
    "libwebpmux.dll",
    "libwebpdemux.dll",
    "lcms2.dll",
    "tiff.dll",
    "openjp2.dll",
    "libjpeg.dll",
    "zlib.dll",
    "liblzma.dll",
    "zstd.dll",
    "Lerc.dll",
    "deflate.dll",
]

runtime_dll_patterns = [
    "icu*.dll",
    "avcodec*.dll",
    "avformat*.dll",
    "avutil*.dll",
    "swresample*.dll",
    "swscale*.dll",
]

binaries = []
if qt6_bin_dir and qt6_bin_dir.exists():
    binaries.extend(
        (str(qt6_bin_dir / name), ".")
        for name in runtime_dll_names
        if (qt6_bin_dir / name).exists()
    )
    for pattern in runtime_dll_patterns:
        binaries.extend((str(path), ".") for path in qt6_bin_dir.glob(pattern))

if qt6_plugins_dir and qt6_plugins_dir.exists():
    binaries += collect_tree_files(
        qt6_plugins_dir / "multimedia",
        "PyQt6/Qt6/plugins/multimedia",
    )

seen_runtime_dlls = set()
deduped_binaries = []
for source, target in binaries:
    key = Path(source).name.lower()
    if key not in seen_runtime_dlls:
        seen_runtime_dlls.add(key)
        deduped_binaries.append((source, target))
binaries = deduped_binaries


excludes = [
    "tkinter", "_tkinter",
    "PyQt5", "PySide2", "PySide6",
    "IPython", "jupyter", "notebook",
    "pytest", "doctest",
    "matplotlib.testing",
    "numba", "llvmlite",
    "torch", "jax", "cupy", "dask",
    "sklearn", "statsmodels",
    "distutils", "setuptools", "pip",
]


a = Analysis(
    analysis_scripts,
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)


excluded_binary_fragments = (
    "mkl_blacs",
    "mkl_scalapack",
    "mkl_core",
    "mkl_def",
    "mkl_intel_thread",
    "mkl_pgi_thread",
    "mkl_rt",
    "mkl_tbb_thread",
    "mkl_sequential",
    "mkl_vml",
    "mkl_avx",
    "mkl_mc",
    "omptarget",
    "sycl",
    "tcl86t.dll",
    "tk86t.dll",
    "\\tcl\\",
    "/tcl/",
    "\\tk\\",
    "/tk/",
    "msmpi",
    "impi",
    "pgmath",
    "pgf90",
    "pgc",
    "reportlab\\graphics\\samples",
    "reportlab/graphics/samples",
    "reportlab\\graphics\\test",
    "reportlab/graphics/test",
)


def keep_entry(entry):
    text = " ".join(str(part).lower() for part in entry)
    return not any(fragment in text for fragment in excluded_binary_fragments)


a.binaries = [entry for entry in a.binaries if keep_entry(entry)]
a.datas = [entry for entry in a.datas if keep_entry(entry)]

pyz = PYZ(a.pure, compress=True)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Launcher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(icon_path) if icon_path else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="ProgTrack_small",
)
