"""Audit maintained first-party Python source for compile-time SyntaxWarnings."""

from pathlib import Path
import os
import tokenize
import warnings


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (ROOT / "Plugins", ROOT / "source", ROOT / "tests")
ENTRYPOINTS = (ROOT / "ProgTrack.v.0.3.0.py",)
EXCLUDED_COMPONENTS = {
    ".git",
    ".build_env_py312",
    ".build_env_py313",
    ".build_tmp",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "_internal",
    "archive",
    "build",
    "dist",
    "node_modules",
    "payload",
    "release",
    "staging",
    "site-packages",
    "third_party",
    "vendor",
    "venv",
    ".venv",
}


def _maintained_python_files() -> list[Path]:
    files = {path for path in ENTRYPOINTS if path.is_file()}
    for source_root in SOURCE_ROOTS:
        if not source_root.is_dir():
            continue
        for directory, child_directories, filenames in os.walk(source_root):
            child_directories[:] = sorted(
                name for name in child_directories
                if name.casefold() not in EXCLUDED_COMPONENTS
            )
            files.update(
                Path(directory) / name
                for name in filenames
                if name.endswith(".py")
            )
    return sorted(files, key=lambda path: path.as_posix().casefold())


def test_maintained_first_party_sources_compile_without_syntax_warnings():
    files = _maintained_python_files()
    findings: list[str] = []
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", SyntaxWarning)
        for path in files:
            try:
                with tokenize.open(path) as stream:
                    source = stream.read()
                compile(source, str(path), "exec", dont_inherit=True)
            except SyntaxWarning as warning:
                findings.append(f"{path}: {warning}")
        findings.extend(
            f"{item.filename}:{item.lineno}: {item.message}"
            for item in recorded
            if issubclass(item.category, SyntaxWarning)
        )

    print(
        f"[SYNTAX AUDIT] {len(files)} maintained first-party Python files; "
        f"{len(findings)} SyntaxWarning(s).",
        flush=True,
    )
    assert not findings, "SyntaxWarning audit findings:\n" + "\n".join(findings)
