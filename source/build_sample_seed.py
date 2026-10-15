"""Compile the repository-owned fictional ProgTrack seed.

Inputs: source/seed/manifest.json. Outputs: Resources/Seed.
No active database, archive, or previous package is an authoring input.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Importable authoring helpers for the existing focused local test modules.
from source.seed.common import *
from source.seed.config import *
from source.seed.generators import *
from source.seed.projections import *
from source.seed.reproduction import *
from source.seed.validation import validate, validate_shire_colony
from source.seed.compiler import main

if __name__ == "__main__":
    raise SystemExit(main())
