# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.1
# Required Launcher version: 0.1.1-log-menu or newer.
# Module: Main application entry point and core user interface.
# Minimum Python version: 3.9.

import sys
import os

# Fix Qt platform plugin path for direct Python execution
# CRITICAL: Set environment variables BEFORE importing any PyQt6 modules
try:
    from pathlib import Path
    import importlib.util
    
    # Find PyQt6 installation WITHOUT importing it
    pyqt6_spec = importlib.util.find_spec('PyQt6.QtCore')
    if pyqt6_spec and pyqt6_spec.origin:
        # Get PyQt6 path from the spec
        pyqt6_path = Path(pyqt6_spec.origin).parent.parent
        qt6_bin = pyqt6_path / "Qt6" / "bin"
        qt6_plugins = pyqt6_path / "Qt6" / "plugins"
        
        # Add Qt6 bin to PATH for DLL dependencies
        if qt6_bin.exists():
            os.environ['PATH'] = str(qt6_bin) + os.pathsep + os.environ.get('PATH', '')
        
        # Set Qt plugin paths BEFORE any Qt import
        if qt6_plugins.exists():
            os.environ['QT_PLUGIN_PATH'] = str(qt6_plugins)
            platforms_path = qt6_plugins / "platforms"
            if platforms_path.exists():
                os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(platforms_path)
except Exception:
    pass

# ================================================================ #
# Standard library imports
# ================================================================ #
import json
import numbers
import importlib
import functools
import inspect
import logging
import tempfile
import errno
import time
import warnings
import re
import platform
import calendar
import html
import shutil
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from Plugins.core.project_visibility import (
    animal_matches_name_filter,
    animal_visible_by_project_scope,
    is_unrestricted_project_role,
    visible_projects_for_user,
)
from Plugins.core.platform_helpers import default_export_directory, default_save_path
from Plugins.core.animal_identity import (
    animal_base_name,
    animal_identity_key,
    animal_identity_label,
    identity_conflict,
    normalize_birth_date,
    record_identity_tuple,
    split_animal_identity_key,
)
from Plugins.core.animal_reference_rewrite import (
    backfill_reference_display_names,
    move_medi_document_folder,
    replace_exact_animal_reference,
    rewrite_animal_reference_file,
)
from Plugins.core.animal_roles import (
    ALL_DIALOG_BLOCKS,
    AnimalRoleRegistry,
    REQUIRED_DIALOG_BLOCKS,
    canonical_role_value,
    clear_deleted_role_assignments,
    default_dialog_blocks,
    import_capabilities_for_blocks,
    normalize_animal_record_roles,
    normalize_block_list,
)
from Plugins.core.animal_status import (
    DECEASED_STATUS_SYMBOL,
    compact_status_with_death_priority,
    has_death_date,
    status_summary_with_death_priority,
)
from typing import List, Dict, Any, Optional, Tuple, Callable, Iterable, TYPE_CHECKING

APP_BASE_DIR = Path(__file__).resolve().parent
APP_RUNTIME_DIR = (
    APP_BASE_DIR / "_internal"
    if (APP_BASE_DIR / "_internal").exists()
    else APP_BASE_DIR
)
LOG_DIR = APP_RUNTIME_DIR / "logs"
APP_LOG_PATH = LOG_DIR / "progtrack.log"
MPL_CONFIG_DIR = APP_RUNTIME_DIR / "matplotlib_cache"

try:
    MPL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(MPL_CONFIG_DIR))
except OSError:
    pass


def _configure_logging() -> None:
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    handlers = [stream_handler]
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(APP_LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)


_configure_logging()
logger = logging.getLogger(__name__)

# Lazy loading system for heavy dependencies
class LazyLoader:
    _instances = {}
    
    def __new__(cls, module_name, package=None):
        key = (module_name, package)
        if key not in cls._instances:
            cls._instances[key] = super().__new__(cls)
            cls._instances[key]._module = None
            cls._instances[key]._module_name = module_name
            cls._instances[key]._package = package
        return cls._instances[key]
    
    def __getattr__(self, name):
        if self._module is None:
            try:
                self._module = importlib.import_module(self._module_name, self._package)
                # Initialize matplotlib backend only when needed
                if self._module_name == 'matplotlib':
                    self._module.use('Qt5Agg')
            except ImportError as e:
                logger.error(f"Failed to import {self._module_name}: {e}")
                raise
        return getattr(self._module, name)

# Lazy-loaded modules
np = LazyLoader('numpy')
pd = LazyLoader('pandas')
matplotlib = LazyLoader('matplotlib')
scipy = LazyLoader('scipy')
scipy_optimize = LazyLoader('scipy.optimize', 'scipy.optimize')

# Lazy load Qt modules
QtWidgets = LazyLoader('PyQt6.QtWidgets', 'PyQt6.QtWidgets')
QtCore = LazyLoader('PyQt6.QtCore', 'PyQt6.QtCore')
QtGui = LazyLoader('PyQt6.QtGui', 'PyQt6.QtGui')

# Lazy load matplotlib components
matplotlib_patches = LazyLoader('matplotlib.patches')
Rectangle = lambda: matplotlib_patches.Rectangle
matplotlib_transforms = LazyLoader('matplotlib.transforms')

# Platform-specific imports
if platform.system() == "Windows":
    import msvcrt
else:
    import fcntl

# # ================================================================ #
# # 2. Logging Setup                                                   #
# # ================================================================ #
# Disable noisy third-party debug logging.
logging.getLogger('matplotlib').setLevel(logging.WARNING)
logging.getLogger('PIL').setLevel(logging.WARNING)

# # ================================================================ #
# # 3. Constants and Enums                                             #
# # ================================================================ #
DATEN_DATEI = "progtrack_daten.json"
SETTINGS_FILE = "progtrack_settings.json"
LOCK_FILE = "progtrack_daten.lock"
DATE_FORMAT = "%d.%m.%Y"
PHASESCHWELLE = 10.0
MAX_SELECTED_ANIMALS = 5
DEFAULT_MAX_MESS = 100
DEFAULT_MAX_PGF = 12
DEFAULT_MAX_EMBRYO = 12
DEFAULT_MAX_PREGNANCIES = 6
DEFAULT_MAX_OP = 6
DEFAULT_MAX_FSH = 120
DEFAULT_MAX_GEBURTEN = 5
DEFAULT_REF_WEIGHT = 450
DEFAULT_RECOVERY_TIME = 60  # default days a donor remains in recovery after surgery

# Schema version for data persistence
SCHEMA_VERSION = "4.0"

# Canonical event type identifiers (internal representation)
# These are stored in the database and used in code logic
# Display names are localized via messages_*.json
EVENT_TYPES = [
    'surgery',           # Surgery/OP
    'embryo_transfer',   # Embryo transfer
    'pregnancy',         # Pregnancy
    'abortion',          # Abortion
    'birth',             # Birth
    'pgf',               # PGF administration
    'fsh',               # FSH administration
    'progesterone',      # Progesterone measurement event
    'special_measurement' # Special measurement (offspring-specific)
]

# Legacy event type mapping for backward compatibility
# Maps old German identifiers to new canonical English identifiers
LEGACY_EVENT_MAP = {
    'op': 'surgery',
    'embryoübertragung': 'embryo_transfer',
    'embryo': 'embryo_transfer',
    'trächtigkeit': 'pregnancy',
    'abort': 'abortion',
    'abbruch': 'abortion',
    'geburt': 'birth',
    'pgf': 'pgf',
    'fsh': 'fsh',
    'progesteron': 'progesterone',
    'sondermessung': 'special_measurement'
}

SELECT_PIXEL_THRESHOLD = 10  # Schwellwert in Pixeln für Hover-/Click-Detection
# Triangles are drawn just *inside* the axes so they obey y-limits & are clipped by the axes patch.
# Use a small positive axes-fraction and a downward marker so the tip touches the x-axis.
TRI_Y = 0.02  # axes-fraction above the x-axis (inside the axes)

class Role(Enum):
    """Enumeration of animal roles.

    The application distinguishes between different animal types based solely
    on their role. Each animal has exactly one role that determines its display,
    coloring, and processing throughout the application.
    """
    SPENDER   = "egg_cell_donor"     # Egg cell donor
    AMME      = "surrogate"          # Surrogate mother
    SAMENSP   = "sperm_donor"        # Male donor / sperm donor
    OFFSPRING = "offspring"          # Offspring
    PARTNER   = "partner_animal"     # Partner animal
    ZUCHTTIER    = "breeding_animal"     # Breeding animal (male or female)
    EXPERIMENTAL = "experimental_animal" # Experimental animal
    # Unknown/unassigned role; used for newly created animals where the
    # keeper has not yet classified the animal.
    UNKNOWN   = "unknown"

class Phase(Enum):
    ALLE = "alle"
    FOLLIKEL = "follikel"
    LUTEAL = "luteal"


# --- UI standardization for animal dialogs ---
# Adjust these two values to change ALL create/edit animal dialogs.
UI_STD_DIALOG_WIDTH: int = 520     # overall dialog width
UI_STD_FIELD_MIN_WIDTH: int = 180   # minimum width for input widgets

# # ================================================================ #
# # 4. Qt Import                                                       #
# # ================================================================ #
from PyQt6.QtCore import Qt, QDate, QTimer, QSize, QRect, QRectF
from PyQt6.QtGui import QIcon, QColor, QIntValidator, QPixmap, QAction, QActionGroup, QDoubleValidator, QFont, QPalette, QTextDocument, QAbstractTextDocumentLayout, QPainter
from PyQt6.QtWidgets import (
    QApplication, QDialog, QMainWindow, QLabel, QListWidget, QListWidgetItem,
    QVBoxLayout, QHBoxLayout, QPushButton, QFileDialog, QLineEdit, QGroupBox,
    QMessageBox, QComboBox, QScrollArea, QFormLayout, QFrame, QDateEdit,
    QCheckBox, QWidget, QRadioButton, QButtonGroup, QSizePolicy, QSpacerItem,
    QTextEdit, QTextBrowser, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QDialogButtonBox, QTabBar, QColorDialog, QMenuBar, QStyledItemDelegate, QStyleOptionViewItem,
    QSpinBox, QProgressBar, QProgressDialog
)
import math
from math import isnan

from pathlib import Path

# -------------------------------------------------------------------
# Global override for QMessageBox icons → use shared icons in root/icons
# first check the folder of the running script, then fall back to file location
# -------------------------------------------------------------------
def _find_icons_dir():
    # 1) check next to this code file
    base = Path(__file__).resolve().parent
    local = base / "icons"
    if local.is_dir():
        return local

    # 2) if not found, check the launched‐script folder
    exe_dir = Path(sys.argv[0]).resolve().parent
    candidate = exe_dir / "icons"
    if candidate.is_dir():
        return candidate

    # 3) walk upward from code file dir as a last resort
    for parent in base.parents:
        picons = parent / "icons"
        if picons.is_dir():
            return picons

    # 4) nothing found → default to local (so path is at least consistent)
    return local

ICON_DIR = _find_icons_dir()

def _set_shared_icon(box: QMessageBox, mtype: str):
    icon_file = ICON_DIR / f"{mtype}.png"
    pix = QPixmap(str(icon_file))
    if not pix.isNull():
        box.setIconPixmap(pix)
    else:
        # fallback to built-in Qt icon if custom PNG is missing
        fallback = {
            "warning":     QMessageBox.Icon.Warning,
            "information": QMessageBox.Icon.Information,
            "critical":    QMessageBox.Icon.Critical,
            "question":    QMessageBox.Icon.Question,
        }
        box.setIcon(fallback.get(mtype, QMessageBox.Icon.NoIcon))

# Monkey-patch all static QMessageBox methods
for _mt in ("warning", "information", "critical", "question"):
    _orig = getattr(QMessageBox, _mt)
    @staticmethod
    def _override(parent, title, text, buttons=QMessageBox.StandardButton.Ok, _mt=_mt, _orig=_orig):
        msg = QMessageBox(parent)
        msg.setWindowTitle(title)
        msg.setText(text)
        _set_shared_icon(msg, _mt)
        # preserve any custom buttons passed in
        if hasattr(msg, "setStandardButtons"):
            msg.setStandardButtons(buttons)
        return msg.exec()
    setattr(QMessageBox, _mt, _override)

# # ================================================================ #
# # 5. Matplotlib Backend Setup                                        #
# # ================================================================ #
matplotlib.rcParams['interactive'] = False
import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from matplotlib.collections import PathCollection
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
import matplotlib.dates as mdates
import matplotlib.ticker as mticker


# ---- Matplotlib date safety bounds & helpers --------------------
# Valid numeric range for Matplotlib dates (year 0001..9999)
MDATES_MIN_NUM = mdates.date2num(datetime(1, 1, 1))
MDATES_MAX_NUM = mdates.date2num(datetime(9999, 12, 31))

def _to_py_datetime(x):
    if isinstance(x, datetime):      return x
    if isinstance(x, pd.Timestamp):  return x.to_pydatetime()
    if isinstance(x, np.datetime64): return pd.to_datetime(x, errors='coerce').to_pydatetime()
    return parse_date(x)

def _safe_date2num(d: datetime) -> Optional[float]:
    try:
        if d is None: return None
        n = float(mdates.date2num(d))
        if not np.isfinite(n): return None
        if n < MDATES_MIN_NUM or n > MDATES_MAX_NUM: return None
        return n
    except Exception:
        return None

# # ================================================================ #
# # 6. Helper Functions                                                #
# # ================================================================ #
def phase_from_value(value: Optional[float]) -> Optional[str]:
    """Determine the phase based on progesterone value."""
    if value is None:
        return None
    return Phase.LUTEAL.value if value >= PHASESCHWELLE else Phase.FOLLIKEL.value

def parse_date(x: Any) -> Optional[datetime]:
    """Parse various date formats into a datetime object, handling Excel's 1900 leap-year bug."""
    if pd.isna(x):
        return None
    try:
        if isinstance(x, numbers.Number):
            # Excel treats 1900 as a leap year; serial date 60 is bogus Feb 29, so adjust for serials >= 60
            days = int(x)
            if days >= 60:
                days -= 1  # skip Excel's fake Feb 29, 1900
            base = datetime(1899, 12, 31)
            return base + timedelta(days=days)
        if isinstance(x, str):
            try:
                return datetime.strptime(x.strip(), DATE_FORMAT)
            except ValueError:
                try:
                    return datetime.fromisoformat(x)
                except ValueError:
                    dt = pd.to_datetime(x, errors='coerce')
                    return dt.to_pydatetime() if pd.notna(dt) else None
        if isinstance(x, pd.Timestamp):
            return x.to_pydatetime()
        return None
    except (ValueError, TypeError, OverflowError) as e:
        logging.warning(f"Failed to parse date '{x}': {e}")
        return None

# # ================================================================ #
# # 7. Main Application Class                                         #
def calculate_age(birth_date_str: str, death_date_str: str = "") -> str:
    """Calculate age from birth date string in DD.MM.YYYY format.
    If death_date_str is provided, calculates age at death.
    Returns formatted string like '2 Years, 3 Months, 15 Days' or error message."""
    if not birth_date_str or not birth_date_str.strip():
        return ""
    try:
        birth_date = datetime.strptime(birth_date_str.strip(), DATE_FORMAT)
        
        # Use death date if provided, otherwise use today
        if death_date_str and death_date_str.strip():
            try:
                end_date = datetime.strptime(death_date_str.strip(), DATE_FORMAT)
            except ValueError:
                return "Invalid death date"
        else:
            end_date = datetime.now()
        
        # Calculate difference
        years = end_date.year - birth_date.year
        months = end_date.month - birth_date.month
        days = end_date.day - birth_date.day
        
        # Adjust for negative days
        if days < 0:
            months -= 1
            # Get days in previous month
            prev_month = end_date.month - 1 if end_date.month > 1 else 12
            prev_year = end_date.year if end_date.month > 1 else end_date.year - 1
            days_in_prev_month = (datetime(prev_year, prev_month + 1, 1) - timedelta(days=1)).day if prev_month < 12 else 31
            days += days_in_prev_month
        
        # Adjust for negative months
        if months < 0:
            years -= 1
            months += 12
        
        # Handle future dates
        if years < 0:
            return "Invalid (future date)"
        
        # Format output
        parts = []
        if years > 0:
            parts.append(f"{years} Year{'s' if years != 1 else ''}")
        if months > 0:
            parts.append(f"{months} Month{'s' if months != 1 else ''}")
        if days > 0 or not parts:  # Always show days if no years/months
            parts.append(f"{days} Day{'s' if days != 1 else ''}")
        
        result = ", ".join(parts)
        # Add indicator if showing age at death
        if death_date_str and death_date_str.strip():
            result += " (at death)"
        return result

    except ValueError:
        return "Invalid date"

def calculate_age_localized(birth_date_str: str, reference_date: datetime, death_date_str: str = "", messages: dict = None) -> str:
    """Calculate age from birth date to reference date with localized abbreviated format.
    
    Args:
        birth_date_str: Birth date in DD.MM.YYYY format
        reference_date: Date to calculate age as of (datetime object)
        death_date_str: Optional death date in DD.MM.YYYY format
        messages: Localized messages dictionary
    
    Returns:
        Formatted string like '3 y., 9 m., 20 d.' or '- (age unknown)'
    """
    if not messages:
        messages = {}
    
    if not birth_date_str or not birth_date_str.strip():
        return messages.get('age.unknown', '(age unknown)')
    
    try:
        birth_date = datetime.strptime(birth_date_str.strip(), DATE_FORMAT)
        
        # Use death date if provided, otherwise use reference date
        if death_date_str and death_date_str.strip():
            try:
                end_date = datetime.strptime(death_date_str.strip(), DATE_FORMAT)
            except ValueError:
                return "Invalid death date"
        else:
            end_date = reference_date
        
        # Calculate difference
        years = end_date.year - birth_date.year
        months = end_date.month - birth_date.month
        days = end_date.day - birth_date.day
        
        # Adjust for negative days
        if days < 0:
            months -= 1
            # Get days in previous month
            prev_month = end_date.month - 1 if end_date.month > 1 else 12
            prev_year = end_date.year if end_date.month > 1 else end_date.year - 1
            days_in_prev_month = (datetime(prev_year, prev_month + 1, 1) - timedelta(days=1)).day if prev_month < 12 else 31
            days += days_in_prev_month
        
        # Adjust for negative months
        if months < 0:
            years -= 1
            months += 12
        
        # Handle future dates
        if years < 0:
            return "Invalid (future date)"
        
        # Get localized abbreviations
        year_abbrev = messages.get('age.year_abbrev', 'y.')
        month_abbrev = messages.get('age.month_abbrev', 'm.')
        day_abbrev = messages.get('age.day_abbrev', 'd.')
        
        # Format with abbreviated units (always include years even if 0)
        age_str = f"{years} {year_abbrev}, {months} {month_abbrev}, {days} {day_abbrev}"
        
        # Add death indicator if applicable
        if death_date_str and death_date_str.strip():
            at_death = messages.get('age.at_death', '(at death)')
            age_str += f" {at_death}"
        
        return age_str
        
    except ValueError:
        return "Invalid date"

# # ================================================================ #
# # HTML Delegate for Rich Text Display in Table Cells            #
# # ================================================================ #
class HTMLDelegate(QStyledItemDelegate):
    """Custom delegate to render HTML content in table cells."""
    
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        """Paint the cell with HTML rendering."""
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        
        painter.save()
        
        # Get background color from item data
        bg_color = index.data(Qt.ItemDataRole.BackgroundRole)
        
        # Draw background color if set
        if bg_color:
            painter.fillRect(option.rect, bg_color)
        
        # Create a QTextDocument to render HTML
        doc = QTextDocument()
        doc.setHtml(options.text)
        doc.setTextWidth(options.rect.width())
        
        # Clear the text from options so default painting doesn't draw it
        options.text = ""
        
        # Draw the border and selection state (but not background, we already drew it)
        if not bg_color:
            # Only use default background drawing if no custom background is set
            options.widget.style().drawControl(options.widget.style().ControlElement.CE_ItemViewItem, options, painter)
        
        # Translate painter to cell position and draw the HTML
        painter.translate(options.rect.left(), options.rect.top())
        clip = QRectF(0, 0, options.rect.width(), options.rect.height())
        doc.drawContents(painter, clip)
        
        painter.restore()
    
    def sizeHint(self, option: QStyleOptionViewItem, index):
        """Calculate size hint for HTML content."""
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        
        doc = QTextDocument()
        doc.setHtml(options.text)
        doc.setTextWidth(option.rect.width())
        
        return QSize(int(doc.idealWidth()), int(doc.size().height()))

# # ================================================================ #
# # File Locking Helper Functions                                     #
# # ================================================================ #
def try_acquire_lock(lock_file: str) -> Optional[object]:
    """
    Try to acquire an exclusive lock on the lock file.
    Returns a file handle if successful, None if lock is already held.
    """
    try:
        # Create lock file if it doesn't exist
        lock_handle = open(lock_file, 'w')
        
        if platform.system() == "Windows":
            # Try to lock the file (non-blocking)
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
                logger.info(f"Successfully acquired lock on {lock_file}")
                return lock_handle
            except (IOError, OSError) as e:
                logger.info(f"Could not acquire lock on {lock_file}: {e}")
                lock_handle.close()
                return None
        else:
            # Unix-like systems
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                logger.info(f"Successfully acquired lock on {lock_file}")
                return lock_handle
            except (IOError, OSError) as e:
                logger.info(f"Could not acquire lock on {lock_file}: {e}")
                lock_handle.close()
                return None
    except Exception as e:
        logger.error(f"Error trying to acquire lock: {e}")
        return None

def release_lock(lock_handle: object, lock_file: str) -> None:
    """
    Release the lock and clean up the lock file.
    """
    if lock_handle is None:
        return
    
    try:
        if platform.system() == "Windows":
            # Unlock the file
            try:
                lock_handle.seek(0, os.SEEK_SET)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except (IOError, OSError) as e:
                logger.warning(f"Error unlocking file: {e}")
        else:
            # Unix-like systems
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            except (IOError, OSError) as e:
                logger.warning(f"Error unlocking file: {e}")
        
        lock_handle.close()
        
        # Remove lock file
        try:
            if os.path.exists(lock_file):
                os.remove(lock_file)
                logger.info(f"Removed lock file {lock_file}")
        except Exception as e:
            logger.warning(f"Could not remove lock file {lock_file}: {e}")
    except Exception as e:
        logger.error(f"Error releasing lock: {e}")

# # ================================================================ #
# # Style Settings Dialog
# # ================================================================ #

class StyleSettingsDialog(QDialog):
    """Dialog for customizing plot colors, markers, and styles."""
    
    def __init__(self, parent, messages):
        super().__init__(parent)
        self.parent_app = parent
        self.messages = messages
        self.setWindowTitle(messages.get("style.dialog.title", "Style Settings"))
        self.setModal(True)
        self.resize(500, 600)
        
        # Store color buttons for easy access
        self.color_buttons = {}
        self.marker_combos = {}
        self.role_table = None
        self._role_definitions = self.parent_app._load_animal_role_definitions()
        self._role_setup_editable = self.parent_app._can_configure_animal_roles()
        self._accepted_role_definitions = None
        
        self._init_ui()
        self._load_current_settings()

    def _steroid_track_active(self) -> bool:
        """Return whether Steroid_track is currently active in the parent app."""
        checker = getattr(self.parent_app, '_is_steroid_track_active', None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False
    
    def _init_ui(self):
        """Initialize the dialog UI."""
        layout = QVBoxLayout(self)
        steroid_active = self._steroid_track_active()
        tabs = QTabWidget(self)
        visual_tab = QWidget()
        visual_layout = QVBoxLayout(visual_tab)
        
        # Create scroll area for settings
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        # ===== Color Groups Container (side by side) =====
        colors_container = QWidget()
        colors_container_layout = QHBoxLayout(colors_container)
        
        # ===== Main Colors Section =====
        colors_group = QGroupBox(self.messages.get("style.main_colors", "Main Colors"))
        colors_layout = QFormLayout()
        
        # Combined (Blood + Urine) color - conditional on plugin
        if self.parent_app.has_pdg_plugin and self._steroid_track_active():
            self.color_buttons['combined'] = self._create_color_button('#8B0000')
            colors_layout.addRow(
                self.messages.get("style.combined_color", "Combined (Blood + Urine):"),
                self.color_buttons['combined']
            )
        
        # Blood progesterone color
        if self._steroid_track_active():
            self.color_buttons['blood'] = self._create_color_button('#ff0000')
            colors_layout.addRow(
                self.messages.get("style.blood_color", "Blood (Pgr):"),
                self.color_buttons['blood']
            )
        
        # PdG colors - conditional on plugin presence
        if self.parent_app.has_pdg_plugin and self._steroid_track_active():
            # Urine PdG color
            self.color_buttons['urine'] = self._create_color_button('#FF8C00')
            colors_layout.addRow(
                self.messages.get("style.urine_color", "Urine (PdG):"),
                self.color_buttons['urine']
            )
            
            # PdG color
            self.color_buttons['pdg'] = self._create_color_button('#008000')
            colors_layout.addRow(
                self.messages.get("style.pdg_color", "PdG:"),
                self.color_buttons['pdg']
            )
        
        # Weight color (always shown)
        self.color_buttons['weight'] = self._create_color_button('#800080')
        colors_layout.addRow(
            self.messages.get("style.weight_color", "Weight:"),
            self.color_buttons['weight']
        )
        if self._steroid_track_active():
            self.color_buttons['sperm_total'] = self._create_color_button('#D55E00')
            colors_layout.addRow(
                self.messages.get("style.sperm_total_color", "Sperm Total:"),
                self.color_buttons['sperm_total']
            )
            
            self.color_buttons['sperm_motile'] = self._create_color_button('#0072B2')
            colors_layout.addRow(
                self.messages.get("style.sperm_motile_color", "Sperm Motile:"),
                self.color_buttons['sperm_motile']
            )
            
            self.color_buttons['sperm_progressive'] = self._create_color_button(
                getattr(self.parent_app, 'sperm_progressive_color', QColor('#009E73')).name()
            )
            colors_layout.addRow(
                self.messages.get("style.sperm_progressive_color", "Sperm Progressive:"),
                self.color_buttons['sperm_progressive']
            )
        
        # FSH injection color
        if self._steroid_track_active():
            self.color_buttons['fsh'] = self._create_color_button(
                getattr(self.parent_app, 'fsh_color', QColor('#000000')).name()
            )
            colors_layout.addRow(
                self.messages.get("style.fsh_color", "FSH Injection:"),
                self.color_buttons['fsh']
            )
        
        colors_group.setLayout(colors_layout)
        colors_container_layout.addWidget(colors_group)
        
        # ===== Event Colors Section =====
        events_group = QGroupBox(self.messages.get("style.event_colors", "Event Colors"))
        events_layout = QFormLayout()
        
        # PGF event color
        if self._steroid_track_active():
            self.color_buttons['pgf'] = self._create_color_button(
                getattr(self.parent_app, 'pgf_color', QColor('#FF0000')).name()
            )
            events_layout.addRow(
                self.messages.get("style.pgf_color", "PGF:"),
                self.color_buttons['pgf']
            )
        
        # Embryo transfer color
        if self._steroid_track_active():
            self.color_buttons['embryo'] = self._create_color_button(
                getattr(self.parent_app, 'embryo_color', QColor('#000000')).name()
            )
            events_layout.addRow(
                self.messages.get("style.embryo_color", "Embryo Transfer:"),
                self.color_buttons['embryo']
            )
        
        # OP color
        if self._steroid_track_active():
            self.color_buttons['op'] = self._create_color_button(
                getattr(self.parent_app, 'op_color', QColor('#0000FF')).name()
            )
            events_layout.addRow(
                self.messages.get("style.op_color", "OP:"),
                self.color_buttons['op']
            )
        
        # Pregnancy color
        if self._steroid_track_active():
            self.color_buttons['pregnancy'] = self._create_color_button(
                getattr(self.parent_app, 'pregnancy_color', QColor('#008000')).name()
            )
            events_layout.addRow(
                self.messages.get("style.pregnancy_color", "Pregnancy:"),
                self.color_buttons['pregnancy']
            )
        
        # Abort color
        if self._steroid_track_active():
            self.color_buttons['abort'] = self._create_color_button(
                getattr(self.parent_app, 'abort_color', QColor('#FF00FF')).name()
            )
            events_layout.addRow(
                self.messages.get("style.abort_color", "Abort:"),
                self.color_buttons['abort']
            )
        
        # Birth color
        if self._steroid_track_active():
            self.color_buttons['birth'] = self._create_color_button(
                getattr(self.parent_app, 'birth_color', QColor('#000000')).name()
            )
            events_layout.addRow(
                self.messages.get("style.birth_color", "Birth:"),
                self.color_buttons['birth']
            )
        
        # Special measurement color
        if self._steroid_track_active():
            self.color_buttons['special'] = self._create_color_button(
                getattr(self.parent_app, 'special_color', QColor('#FFA500')).name()
            )
            events_layout.addRow(
                self.messages.get("style.special_color", "Special Measurement:"),
                self.color_buttons['special']
            )
        
        events_group.setLayout(events_layout)
        colors_container_layout.addWidget(events_group)
        events_group.setVisible(steroid_active)
        
        # Add the colors container to the main layout
        scroll_layout.addWidget(colors_container)
        
        # ===== Markers & Styles Section =====
        markers_group = QGroupBox(self.messages.get("style.markers", "Markers & Styles"))
        markers_layout = QFormLayout()
        
        # Marker options
        marker_options = [
            ('o', '●'),  # circle
            ('^', '▲'),
            ('v', '▼'),
            ('s', '■'),
            ('D', '◆'),
            ('*', '★'),
            ('+', '✚'),
            ('x', '✖'),
        ]
        
        # Combined (converted) marker - conditional on plugin
        if self.parent_app.has_pdg_plugin and self._steroid_track_active():
            self.marker_combos['combined'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['combined'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.combined_marker", "Combined (Converted) Marker:"),
                self.marker_combos['combined']
            )
        
        # Blood progesterone marker
        if self._steroid_track_active():
            self.marker_combos['blood'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['blood'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.blood_marker", "Blood Progesterone Marker:"),
                self.marker_combos['blood']
            )
        
        # Urine PdG marker - conditional on plugin
        if self.parent_app.has_pdg_plugin and self._steroid_track_active():
            self.marker_combos['urine'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['urine'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.urine_marker", "Urine (PdG) Marker:"),
                self.marker_combos['urine']
            )
        
        # Weight marker
        self.marker_combos['weight'] = QComboBox()
        for value, label in marker_options:
            self.marker_combos['weight'].addItem(label, value)
        markers_layout.addRow(
            self.messages.get("style.weight_marker", "Weight Marker:"),
            self.marker_combos['weight']
        )
        
        # FSH injection marker
        if self._steroid_track_active():
            self.marker_combos['fsh'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['fsh'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.fsh_marker", "FSH Injection Marker:"),
                self.marker_combos['fsh']
            )
        
        if self._steroid_track_active():
            # Sperm Total marker
            self.marker_combos['sperm_total'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['sperm_total'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.sperm_total_marker", "Sperm Total Marker:"),
                self.marker_combos['sperm_total']
            )
            
            # Sperm Motile marker
            self.marker_combos['sperm_motile'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['sperm_motile'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.sperm_motile_marker", "Sperm Motile Marker:"),
                self.marker_combos['sperm_motile']
            )
            
            # Sperm Progressive marker
            self.marker_combos['sperm_progressive'] = QComboBox()
            for value, label in marker_options:
                self.marker_combos['sperm_progressive'].addItem(label, value)
            markers_layout.addRow(
                self.messages.get("style.sperm_progressive_marker", "Sperm Progressive Marker:"),
                self.marker_combos['sperm_progressive']
            )
        
        markers_group.setLayout(markers_layout)
        scroll_layout.addWidget(markers_group)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        visual_layout.addWidget(scroll)
        tabs.addTab(visual_tab, self.messages.get("settings.tab.visual_style", "Visual style"))
        tabs.addTab(self._create_role_setup_tab(), self.messages.get("settings.tab.role_setup", "Role setup"))
        layout.addWidget(tabs)
        
        # ===== Buttons =====
        button_layout = QHBoxLayout()
        
        reset_button = QPushButton(self.messages.get("style.reset_defaults", "Reset to Defaults"))
        reset_button.clicked.connect(self._reset_to_defaults)
        button_layout.addWidget(reset_button)
        
        button_layout.addStretch()
        
        button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        button_layout.addWidget(button_box)
        
        layout.addLayout(button_layout)

    def _create_role_setup_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        if not self._role_setup_editable:
            locked = QLabel(self.messages.get(
                "settings.role_setup.locked",
                "Only Lord, Master or Manager users can change animal role setup.",
            ))
            locked.setWordWrap(True)
            locked.setStyleSheet("color: #666;")
            layout.addWidget(locked)

        self.role_table = QTableWidget(0, 8, tab)
        self.role_table.setHorizontalHeaderLabels([
            self.messages.get("settings.role_setup.col.active", "Active"),
            self.messages.get("settings.role_setup.col.order", "Order"),
            self.messages.get("settings.role_setup.col.icon", "Emoji"),
            self.messages.get("settings.role_setup.col.label", "Label"),
            self.messages.get("settings.role_setup.col.value", "Internal ID"),
            self.messages.get("settings.role_setup.col.preset", "Preset"),
            self.messages.get("settings.role_setup.col.new_blocks", "New blocks"),
            self.messages.get("settings.role_setup.col.edit_blocks", "Edit blocks"),
        ])
        self.role_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.role_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.role_table.setColumnHidden(4, True)
        self.role_table.setColumnHidden(5, True)
        self.role_table.verticalHeader().setVisible(False)
        self.role_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.role_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        if not self._role_setup_editable:
            self.role_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self._rebuild_role_table(self._role_definitions)
        layout.addWidget(self.role_table, 1)

        button_row = QHBoxLayout()
        add_btn = QPushButton(self.messages.get("settings.role_setup.add_role", "Add role"))
        delete_btn = QPushButton(self.messages.get("settings.role_setup.delete_role", "Delete role"))
        up_btn = QPushButton(self.messages.get("settings.role_setup.move_up", "Move up"))
        down_btn = QPushButton(self.messages.get("settings.role_setup.move_down", "Move down"))
        add_btn.clicked.connect(self._add_custom_role_row)
        delete_btn.clicked.connect(self._delete_selected_role_row)
        up_btn.clicked.connect(lambda: self._move_selected_role_row(-1))
        down_btn.clicked.connect(lambda: self._move_selected_role_row(1))
        for btn in (add_btn, delete_btn, up_btn, down_btn):
            btn.setEnabled(self._role_setup_editable)
            button_row.addWidget(btn)
        button_row.addStretch()
        layout.addLayout(button_row)
        return tab

    def _rebuild_role_table(self, roles):
        if self.role_table is None:
            return
        self.role_table.setRowCount(0)
        for role in sorted(roles, key=lambda r: (int(r.get("order", 1000)), str(r.get("label", "")).casefold())):
            self._add_role_table_row(role)

    def _role_block_preset_options(self):
        m = self.messages
        return [
            ("basic", m.get("settings.role_setup.preset.basic", "Basic animal")),
            ("egg_cell_donor", m.get("settings.role_setup.preset.egg_cell_donor", "Egg cell donor")),
            ("surrogate", m.get("settings.role_setup.preset.surrogate", "Surrogate")),
            ("sperm_donor", m.get("settings.role_setup.preset.sperm_donor", "Sperm donor")),
            ("offspring", m.get("settings.role_setup.preset.offspring", "Offspring")),
            ("partner", m.get("settings.role_setup.preset.partner", "Partner animal")),
            ("breeding", m.get("settings.role_setup.preset.breeding", "Breeding animal")),
            ("experimental", m.get("settings.role_setup.preset.experimental", "Experimental animal")),
        ]

    def _blocks_for_preset(self, preset_id: str, mode: str):
        recipe = default_dialog_blocks(preset_id)
        return self.parent_app._normalize_role_dialog_blocks(recipe.get(mode, recipe.get("edit", [])))

    def _role_block_label(self, block_id: str) -> str:
        fallback = str(block_id or "").replace("_", " ").strip().capitalize()
        return self.messages.get(f"settings.role_setup.block.{block_id}.name", fallback)

    def _role_block_description(self, block_id: str) -> str:
        defaults = {
            "identity": "Core animal identity fields.",
            "cage_address": "Current housing and cage address.",
            "weight": "Weight entries and import support.",
            "parenting": "Parent and origin information.",
            "sperm_measurements": "Sperm value entry and import support.",
            "blood_progesterone": "Blood progesterone values and import support.",
            "urine_pdg": "Urine PdG values and import support.",
            "reproductive_events": "Reproductive event timeline.",
            "procedure_events": "Procedure and measurement event timeline.",
        }
        return self.messages.get(
            f"settings.role_setup.block.{block_id}.description",
            defaults.get(block_id, "Optional dialog block."),
        )

    def _make_role_block_preset_combo(self, blocks_text: str, mode: str, custom_name: str = ""):
        current_blocks = self.parent_app._normalize_role_dialog_blocks(blocks_text)
        combo = QComboBox()
        combo.setEnabled(self._role_setup_editable)
        combo.setProperty("roleSetupMode", mode)
        combo.setProperty("roleSetupPreviousIndex", 0)

        matched_index = -1
        for preset_id, label in self._role_block_preset_options():
            preset_blocks = self._blocks_for_preset(preset_id, mode)
            combo.addItem(label, {"preset": preset_id, "blocks": preset_blocks, "name": label})
            if preset_blocks == current_blocks:
                matched_index = combo.count() - 1

        custom_label = custom_name or self.messages.get("settings.role_setup.preset.custom", "Custom")
        combo.addItem(custom_label, {"preset": "custom", "blocks": current_blocks, "name": custom_label})
        combo.setCurrentIndex(matched_index if matched_index >= 0 else combo.count() - 1)
        combo.setProperty("roleSetupPreviousIndex", combo.currentIndex())
        combo.currentIndexChanged.connect(lambda _idx, c=combo: self._on_role_block_preset_changed(c))
        return combo

    def _on_role_block_preset_changed(self, combo: QComboBox) -> None:
        data = combo.currentData()
        if not isinstance(data, dict):
            return
        if data.get("preset") != "custom":
            combo.setProperty("roleSetupPreviousIndex", combo.currentIndex())
            return
        if not self._role_setup_editable:
            return

        result = self._exec_custom_role_blocks_dialog(
            data.get("blocks") or [],
            str(data.get("name") or self.messages.get("settings.role_setup.preset.custom", "Custom")),
        )
        if result is None:
            previous = combo.property("roleSetupPreviousIndex")
            combo.blockSignals(True)
            combo.setCurrentIndex(previous if isinstance(previous, int) else 0)
            combo.blockSignals(False)
            return

        preset_name, blocks = result
        insert_at = max(combo.count() - 1, 0)
        combo.insertItem(insert_at, preset_name, {"preset": "custom_saved", "blocks": blocks, "name": preset_name})
        combo.setItemData(combo.count() - 1, {
            "preset": "custom",
            "blocks": blocks,
            "name": self.messages.get("settings.role_setup.preset.custom", "Custom"),
        })
        combo.setCurrentIndex(insert_at)
        combo.setProperty("roleSetupPreviousIndex", insert_at)

    def _exec_custom_role_blocks_dialog(self, current_blocks, current_name: str):
        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get("settings.role_setup.custom_blocks.title", "Custom block preset"))
        layout = QVBoxLayout(dlg)

        name_le = QLineEdit(current_name if current_name != "Custom" else "")
        name_le.setPlaceholderText(self.messages.get("settings.role_setup.custom_blocks.name_placeholder", "Preset name"))
        layout.addWidget(QLabel(self.messages.get("settings.role_setup.custom_blocks.name", "Preset name:")))
        layout.addWidget(name_le)

        scroll = QScrollArea(dlg)
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        selected = set(self.parent_app._normalize_role_dialog_blocks(current_blocks))
        checkboxes = {}
        for block_id in ALL_DIALOG_BLOCKS:
            cb = QCheckBox(self._role_block_label(block_id))
            cb.setChecked(block_id in selected or block_id in REQUIRED_DIALOG_BLOCKS)
            if block_id in REQUIRED_DIALOG_BLOCKS:
                cb.setEnabled(False)
            desc = QLabel(self._role_block_description(block_id))
            desc.setWordWrap(True)
            desc.setStyleSheet("color: #666; margin-left: 18px;")
            body_layout.addWidget(cb)
            body_layout.addWidget(desc)
            checkboxes[block_id] = cb
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            dlg,
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None
        preset_name = name_le.text().strip() or self.messages.get("settings.role_setup.preset.custom", "Custom")
        blocks = [
            block_id
            for block_id in ALL_DIALOG_BLOCKS
            if checkboxes[block_id].isChecked() or block_id in REQUIRED_DIALOG_BLOCKS
        ]
        return preset_name, self.parent_app._normalize_role_dialog_blocks(blocks)

    def _role_blocks_from_table_cell(self, row: int, col: int, mode: str):
        widget = self.role_table.cellWidget(row, col) if self.role_table else None
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            if isinstance(data, dict):
                return self.parent_app._normalize_role_dialog_blocks(data.get("blocks") or [])
        return self.parent_app._normalize_role_dialog_blocks(
            self.role_table.item(row, col).text() if self.role_table.item(row, col) else "",
        )

    def _role_custom_preset_name_from_table_cell(self, row: int, col: int) -> str:
        widget = self.role_table.cellWidget(row, col) if self.role_table else None
        if isinstance(widget, QComboBox):
            data = widget.currentData()
            if isinstance(data, dict) and str(data.get("preset") or "").startswith("custom"):
                return str(data.get("name") or "").strip()
        return ""

    def _add_role_table_row(self, role):
        row = self.role_table.rowCount()
        self.role_table.insertRow(row)

        active_item = QTableWidgetItem("")
        active_item.setCheckState(
            Qt.CheckState.Checked if role.get("active", True) else Qt.CheckState.Unchecked
        )
        active_item.setFlags((active_item.flags() | Qt.ItemFlag.ItemIsUserCheckable) & ~Qt.ItemFlag.ItemIsEditable)
        if not self._role_setup_editable:
            active_item.setFlags(active_item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.role_table.setItem(row, 0, active_item)

        dialog_blocks = role.get("dialog_blocks", {}) if isinstance(role.get("dialog_blocks"), dict) else {}
        values = [
            str(role.get("order", (row + 1) * 10)),
            str(role.get("icon", "")),
            str(role.get("label", "")),
            str(role.get("value", "")),
            str(role.get("field_preset", "basic")),
            ", ".join(dialog_blocks.get("new", [])),
            ", ".join(dialog_blocks.get("edit", [])),
        ]
        custom_preset_names = role.get("custom_preset_names", {}) if isinstance(role.get("custom_preset_names"), dict) else {}
        for col, value in enumerate(values, start=1):
            item = QTableWidgetItem(value)
            item.setData(Qt.ItemDataRole.UserRole, dict(role))
            immutable = col in (4, 5)
            if immutable or not self._role_setup_editable:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.role_table.setItem(row, col, item)
            if col in (6, 7):
                mode = "new" if col == 6 else "edit"
                self.role_table.setCellWidget(
                    row,
                    col,
                    self._make_role_block_preset_combo(value, mode, str(custom_preset_names.get(mode, ""))),
                )

    def _current_role_values_in_table(self):
        values = set()
        if self.role_table is None:
            return values
        for row in range(self.role_table.rowCount()):
            item = self.role_table.item(row, 4)
            if item:
                values.add(item.text().strip())
        return values

    def _add_custom_role_row(self):
        role = self.parent_app._make_custom_animal_role_definition(
            self.messages.get("settings.role_setup.new_role_label", "New role"),
            "\u25cf",
            existing_values=self._current_role_values_in_table(),
        )
        self._add_role_table_row(role)
        self.role_table.selectRow(self.role_table.rowCount() - 1)

    def _move_selected_role_row(self, direction: int):
        if self.role_table is None:
            return
        selected = self.role_table.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()
        target = row + direction
        if target < 0 or target >= self.role_table.rowCount():
            return
        roles = self._role_rows_from_table(validate=False)
        roles[row], roles[target] = roles[target], roles[row]
        for index, role in enumerate(roles):
            role["order"] = (index + 1) * 10
        self._rebuild_role_table(roles)
        self.role_table.selectRow(target)

    def _delete_selected_role_row(self):
        if self.role_table is None or not self._role_setup_editable:
            return
        selected = self.role_table.selectionModel().selectedRows()
        if not selected:
            return
        self.role_table.removeRow(selected[0].row())

    def _role_rows_from_table(self, *, validate: bool):
        roles = []
        seen_values = set()
        if self.role_table is None:
            return roles
        for row in range(self.role_table.rowCount()):
            source_item = self.role_table.item(row, 4)
            original = source_item.data(Qt.ItemDataRole.UserRole) if source_item else {}
            original = dict(original) if isinstance(original, dict) else {}
            label = (self.role_table.item(row, 3).text() if self.role_table.item(row, 3) else "").strip()
            value = (self.role_table.item(row, 4).text() if self.role_table.item(row, 4) else "").strip()
            icon = (self.role_table.item(row, 2).text() if self.role_table.item(row, 2) else "").strip()
            order_text = (self.role_table.item(row, 1).text() if self.role_table.item(row, 1) else "").strip()
            active_item = self.role_table.item(row, 0)

            if validate and not label and not icon:
                raise ValueError(self.messages.get(
                    "settings.role_setup.error.label_or_icon_required",
                    "A role needs either a label or an icon.",
                ))
            if validate and not value:
                raise ValueError(self.messages.get("settings.role_setup.error.value_required", "Internal role ID is required."))
            if validate and value in seen_values:
                raise ValueError(self.messages.get("settings.role_setup.error.duplicate", "Internal role IDs must be unique."))
            seen_values.add(value)

            try:
                order = int(order_text)
            except ValueError:
                order = (row + 1) * 10

            role = dict(original)
            role.update({
                "value": value,
                "label": label,
                "icon": icon or "\u25cf",
                "order": order,
                "active": active_item.checkState() == Qt.CheckState.Checked if active_item else True,
                "dialog_blocks": {
                    "new": self._role_blocks_from_table_cell(row, 6, "new"),
                    "edit": self._role_blocks_from_table_cell(row, 7, "edit"),
                },
                "custom_preset_names": {
                    "new": self._role_custom_preset_name_from_table_cell(row, 6),
                    "edit": self._role_custom_preset_name_from_table_cell(row, 7),
                },
            })
            if not role.get("built_in"):
                role["label_key"] = role.get("label_key") or f"role.{value}"
                role.setdefault("base_editor", "basic")
                role.setdefault("field_preset", "basic")
            roles.append(role)
        return roles

    def accept(self):
        if self._role_setup_editable:
            try:
                self._accepted_role_definitions = self._role_rows_from_table(validate=True)
            except ValueError as exc:
                QMessageBox.warning(
                    self,
                    self.messages.get("title.warning", "Warning"),
                    str(exc),
                )
                return
        super().accept()
    
    def _create_color_button(self, default_color):
        """Create a color picker button."""
        button = QPushButton()
        button.setFixedSize(30, 30)  # Square button for color selection
        button.setStyleSheet(f"background-color: {default_color}; border: 1px solid #000;")
        button.clicked.connect(lambda: self._choose_color(button))
        button.setProperty('color', default_color)
        return button
    
    def _choose_color(self, button):
        """Open color picker dialog."""
        current_color = QColor(button.property('color'))
        color = QtWidgets.QColorDialog.getColor(current_color, self, "Choose Color")
        if color.isValid():
            button.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #000;")
            button.setProperty('color', color.name())
    
    def _load_current_settings(self):
        """Load current settings from parent application."""
        # Load colors
        if hasattr(self.parent_app, 'combined_color') and 'combined' in self.color_buttons:
            self.color_buttons['combined'].setStyleSheet(
                f"background-color: {self.parent_app.combined_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['combined'].setProperty('color', self.parent_app.combined_color.name())
        
        if hasattr(self.parent_app, 'blood_color') and 'blood' in self.color_buttons:
            self.color_buttons['blood'].setStyleSheet(
                f"background-color: {self.parent_app.blood_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['blood'].setProperty('color', self.parent_app.blood_color.name())
        
        if hasattr(self.parent_app, 'urine_color') and 'urine' in self.color_buttons:
            self.color_buttons['urine'].setStyleSheet(
                f"background-color: {self.parent_app.urine_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['urine'].setProperty('color', self.parent_app.urine_color.name())
        
        if hasattr(self.parent_app, 'weight_color'):
            self.color_buttons['weight'].setStyleSheet(
                f"background-color: {self.parent_app.weight_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['weight'].setProperty('color', self.parent_app.weight_color.name())
        
        if hasattr(self.parent_app, 'pdg_color') and 'pdg' in self.color_buttons:
            self.color_buttons['pdg'].setStyleSheet(
                f"background-color: {self.parent_app.pdg_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['pdg'].setProperty('color', self.parent_app.pdg_color.name())
        
        if hasattr(self.parent_app, 'sperm_total_color') and 'sperm_total' in self.color_buttons:
            self.color_buttons['sperm_total'].setStyleSheet(
                f"background-color: {self.parent_app.sperm_total_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['sperm_total'].setProperty('color', self.parent_app.sperm_total_color.name())
        
        if hasattr(self.parent_app, 'sperm_motile_color') and 'sperm_motile' in self.color_buttons:
            self.color_buttons['sperm_motile'].setStyleSheet(
                f"background-color: {self.parent_app.sperm_motile_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['sperm_motile'].setProperty('color', self.parent_app.sperm_motile_color.name())
        
        if hasattr(self.parent_app, 'sperm_progressive_color') and 'sperm_progressive' in self.color_buttons:
            self.color_buttons['sperm_progressive'].setStyleSheet(
                f"background-color: {self.parent_app.sperm_progressive_color.name()}; border: 1px solid #000;"
            )
            self.color_buttons['sperm_progressive'].setProperty('color', self.parent_app.sperm_progressive_color.name())
        
        # Load markers
        if hasattr(self.parent_app, 'combined_marker') and 'combined' in self.marker_combos:
            idx = self.marker_combos['combined'].findData(self.parent_app.combined_marker)
            if idx >= 0:
                self.marker_combos['combined'].setCurrentIndex(idx)
        
        if hasattr(self.parent_app, 'blood_marker') and 'blood' in self.marker_combos:
            idx = self.marker_combos['blood'].findData(self.parent_app.blood_marker)
            if idx >= 0:
                self.marker_combos['blood'].setCurrentIndex(idx)
        
        if self.parent_app.has_pdg_plugin and self._steroid_track_active() and hasattr(self.parent_app, 'urine_marker') and 'urine' in self.marker_combos:
            idx = self.marker_combos['urine'].findData(self.parent_app.urine_marker)
            if idx >= 0:
                self.marker_combos['urine'].setCurrentIndex(idx)
        
        if hasattr(self.parent_app, 'weight_marker'):
            idx = self.marker_combos['weight'].findData(self.parent_app.weight_marker)
            if idx >= 0:
                self.marker_combos['weight'].setCurrentIndex(idx)
        
        if hasattr(self.parent_app, 'fsh_marker') and 'fsh' in self.marker_combos:
            idx = self.marker_combos['fsh'].findData(self.parent_app.fsh_marker)
            if idx >= 0:
                self.marker_combos['fsh'].setCurrentIndex(idx)
        
        if hasattr(self.parent_app, 'sperm_total_marker') and 'sperm_total' in self.marker_combos:
            idx = self.marker_combos['sperm_total'].findData(self.parent_app.sperm_total_marker)
            if idx >= 0:
                self.marker_combos['sperm_total'].setCurrentIndex(idx)
        
        if hasattr(self.parent_app, 'sperm_motile_marker') and 'sperm_motile' in self.marker_combos:
            idx = self.marker_combos['sperm_motile'].findData(self.parent_app.sperm_motile_marker)
            if idx >= 0:
                self.marker_combos['sperm_motile'].setCurrentIndex(idx)
        
        if hasattr(self.parent_app, 'sperm_progressive_marker') and 'sperm_progressive' in self.marker_combos:
            idx = self.marker_combos['sperm_progressive'].findData(self.parent_app.sperm_progressive_marker)
            if idx >= 0:
                self.marker_combos['sperm_progressive'].setCurrentIndex(idx)
    
    def _reset_to_defaults(self):
        """Reset all settings to default values."""
        defaults = self.parent_app._get_default_style_settings()
        
        # Reset colors
        for key in ['combined', 'blood', 'urine', 'weight', 'pdg', 'sperm_total', 'sperm_motile', 'sperm_progressive', 'fsh', 
                    'pgf', 'embryo', 'op', 'pregnancy', 'abort', 'birth', 'special']:
            color_key = f'{key}_color'
            if color_key in defaults and key in self.color_buttons:
                self.color_buttons[key].setStyleSheet(
                    f"background-color: {defaults[color_key]}; border: 1px solid #000;"
                )
                self.color_buttons[key].setProperty('color', defaults[color_key])
        
        # Reset markers
        for key in ['combined', 'blood', 'urine', 'weight', 'fsh', 'sperm_total', 'sperm_motile', 'sperm_progressive']:
            marker_key = f'{key}_marker'
            if marker_key in defaults and key in self.marker_combos:
                idx = self.marker_combos[key].findData(defaults[marker_key])
                if idx >= 0:
                    self.marker_combos[key].setCurrentIndex(idx)
    
    def get_settings(self):
        """Get the current settings from the dialog."""
        def _color_or_parent(key: str, parent_attr: str, fallback: str) -> str:
            if key in self.color_buttons:
                return self.color_buttons[key].property('color')
            parent_val = getattr(self.parent_app, parent_attr, QColor(fallback))
            return parent_val.name() if hasattr(parent_val, 'name') else str(parent_val)

        def _marker_or_parent(key: str, parent_attr: str, fallback: str) -> str:
            if key in self.marker_combos:
                return self.marker_combos[key].currentData()
            return getattr(self.parent_app, parent_attr, fallback)

        return {
            'combined_color': _color_or_parent('combined', 'combined_color', '#8B0000'),
            'blood_color': _color_or_parent('blood', 'blood_color', '#ff0000'),
            'urine_color': _color_or_parent('urine', 'urine_color', '#FF8C00'),
            'weight_color': _color_or_parent('weight', 'weight_color', '#800080'),
            'pdg_color': _color_or_parent('pdg', 'pdg_color', '#008000'),
            'sperm_total_color': _color_or_parent('sperm_total', 'sperm_total_color', '#D55E00'),
            'sperm_motile_color': _color_or_parent('sperm_motile', 'sperm_motile_color', '#0072B2'),
            'sperm_progressive_color': _color_or_parent('sperm_progressive', 'sperm_progressive_color', '#009E73'),
            'fsh_color': _color_or_parent('fsh', 'fsh_color', '#000000'),
            'pgf_color': _color_or_parent('pgf', 'pgf_color', '#FF0000'),
            'embryo_color': _color_or_parent('embryo', 'embryo_color', '#000000'),
            'op_color': _color_or_parent('op', 'op_color', '#0000FF'),
            'pregnancy_color': _color_or_parent('pregnancy', 'pregnancy_color', '#008000'),
            'abort_color': _color_or_parent('abort', 'abort_color', '#FF00FF'),
            'birth_color': _color_or_parent('birth', 'birth_color', '#000000'),
            'special_color': _color_or_parent('special', 'special_color', '#FFA500'),
            'combined_marker': _marker_or_parent('combined', 'combined_marker', 'o'),
            'blood_marker': _marker_or_parent('blood', 'blood_marker', 'o'),
            'weight_marker': _marker_or_parent('weight', 'weight_marker', '^'),
            'fsh_marker': _marker_or_parent('fsh', 'fsh_marker', 'v'),
            'sperm_total_marker': _marker_or_parent('sperm_total', 'sperm_total_marker', 'o'),
            'sperm_motile_marker': _marker_or_parent('sperm_motile', 'sperm_motile_marker', 's'),
            'sperm_progressive_marker': _marker_or_parent('sperm_progressive', 'sperm_progressive_marker', '^')
        }

    def get_role_definitions(self):
        """Return accepted role definitions, or None when Role setup was read-only."""
        return self._accepted_role_definitions

# # ================================================================ #
# Helper classes for Master_Track integration
# # ================================================================ #

class _ClickableLabel(QLabel):
    """QLabel that emits a clicked signal on mouse press."""
    from PyQt6.QtCore import pyqtSignal
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)


class _IdleResetFilter(QtCore.QObject):
    """Event filter that resets Master_Track idle timer on user interaction."""

    _TRACKED = {
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QEvent.Type.KeyPress,
        QtCore.QEvent.Type.Wheel,
    }

    def __init__(self, master_track, parent=None):
        super().__init__(parent)
        self._mt = master_track

    def eventFilter(self, obj, event):
        if event.type() in self._TRACKED:
            self._mt.reset_idle_timer()
        return False


# # ================================================================ #
class ProgTrackApp(QtWidgets.QMainWindow):
    def __init__(self):
        try:
            # Initialize QMainWindow
            super().__init__()
            
            # Initialize data structures
            self.data = {}  # Main data storage
            self.animals = []  # List of animal names
            self.archived_animals = []  # List of archived animal names
            self.selected_animals = []  # Currently selected animals
            self.messages = {}  # UI messages
            self.lang = 'en'  # Default language
            self._plot_ctx = {}  # Plot context
            # ---- first-run / empty database dialog control ----
            self._no_data_pending = False   # set in _load_persistence() if DB is empty
            self._no_data_warned  = False   # ensure we show it only once
            
            # ---- File locking for multi-instance support ----
            self.lock_handle = None
            self.read_only_mode = False
            self.lock_retry_timer = None
            
            # Try to acquire lock on the data file
            self.lock_handle = try_acquire_lock(LOCK_FILE)
            if self.lock_handle is None:
                # Another instance has the lock - run in read-only mode
                self.read_only_mode = True
                logger.warning("Running in READ-ONLY mode - another instance is editing the data")
                self._start_read_only_lock_timer()
            
            # Set up basic window properties
            self.setWindowTitle(self.messages.get("app.initializing", "ProgTrack - Loading..."))
            self.setGeometry(100, 100, 1400, 800)
            
            # Initialize lazy-loaded modules that are needed immediately
            self._init_lazy_imports()
            
            # Show the window immediately
            self.show()
            
            # Process events to make sure the window is displayed
            QtWidgets.QApplication.processEvents()
            
            # Set up the status bar
            self.statusBar().showMessage(self.messages.get("app.initializing", "Initializing..."))
            
            # Initialize UI components first (synchronously)
            self._init_ui_components()
            
            # Load settings and data in background
            self._load_settings_async()

            # Load data in background
            self._load_data_async()
            
        except Exception as e:
            logger.critical(f"Error during initialization: {e}", exc_info=True)
            # Try to show an error message
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(None, 
                                   self.messages.get("error.initialization.title", "Initialization Error"), 
                                   self.messages.get("error.initialization.message", 
                                                   "Failed to initialize application: {error}\n\nCheck the log file for more details.").format(error=str(e)))
            except Exception as dialog_error:
                logger.error("Could not show initialization error dialog: %s", dialog_error)
            raise
        
    def _init_lazy_imports(self):
        """Initialize lazy-loaded modules that are needed immediately."""
        # Force load required modules
        _ = np.array  # Force numpy import
        _ = pd.DataFrame  # Force pandas import
        _ = plt  # Force matplotlib import
        
        # Expose LazyLoader modules as instance attributes for plugin access
        # This allows plugins to access Qt/matplotlib/numpy via parent_app.QtWidgets, etc.
        self.QtWidgets = QtWidgets
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.plt = plt
        self.matplotlib = matplotlib
        self.np = np
        self.pd = pd
        
        # Expose Role enum for plugin access
        self.Role = Role
        self.animal_role_registry = AnimalRoleRegistry(
            APP_BASE_DIR / "Plugins" / "core" / "animal_roles.json"
        )
        
        # Import Qt components that are needed immediately
        global QIcon, QMainWindow
        from PyQt6.QtGui import QIcon
        from PyQt6.QtWidgets import QMainWindow
        
        # Set window icon
        icon_path = Path("icons/progtrack_icon.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            logger.warning("Icon file 'progtrack_icon.ico' not found")
    
    @QtCore.pyqtSlot()
    def _load_settings_async(self):
        """Load settings in a background thread."""
        try:
            self._load_settings()
            self._load_messages(self.lang)
            # Initialize application state and build UI (only on first load)
            if not hasattr(self, '_ui_initialized'):
                self._init_application_state()
                self._ui_initialized = True
            self.setWindowTitle(self.messages.get("app.title", "ProgTrack"))
            self.statusBar().showMessage(self.messages.get("app.settings_loaded", "Settings loaded"), 3000)
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
            self.statusBar().showMessage(self.messages.get("error.settings.load", "Error loading settings"), 5000)
    
    @QtCore.pyqtSlot()
    def _load_data_async(self):
        """Load data in a background thread."""
        try:
            self.statusBar().showMessage(self.messages.get("app.loading_data", "Loading data..."))
            QtWidgets.QApplication.processEvents()
            
            try:
                # Load the data
                self._load_persistence()
                
                # Update the UI
                self._refresh_ui_after_data_load()

                # If first run / no animals, show the notice exactly once,
                # after the UI is ready (prevents splash/control-panel flicker).
                if getattr(self, "_no_data_pending", False) and not getattr(self, "_no_data_warned", False):
                    self._no_data_warned = True
                    self._no_data_pending = False
                    self._show_message("warning.load.json.no_data")
                
                # Show read-only mode warning if applicable
                if self.read_only_mode:
                    self._show_read_only_warning()

                self.statusBar().showMessage(self.messages.get("app.ready", "Ready"), 3000)
                
                # Log successful data load
                logger.info(f"Successfully loaded data for {len(self.animals)} animals")
                
            except Exception as e:
                error_msg = f"Error in data processing: {e}"
                logger.error(error_msg, exc_info=True)
                raise
                
        except Exception as e:
            error_msg = f"Error loading data: {e}"
            logger.error(error_msg, exc_info=True)
            # Show error in status bar and message box
            self.statusBar().showMessage(error_msg, 5000)
            
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(self, "Data Loading Error", 
                                   f"Failed to load data: {str(e)}\n\nCheck the log file for more details.")
            except Exception as e2:
                logger.error(f"Error showing message box: {e2}")
                
            # Re-raise the exception to ensure it's logged
            raise

    def _refresh_ui_after_data_load(self):
        """Refresh the UI after loading data."""
        try:
            logger.info("Refreshing UI...")
            
            # Update the main window title
            title_template = self.messages.get('app.window_title.animals_loaded', '{app_title} - {count} animals loaded')
            self.setWindowTitle(title_template.format(app_title=self.messages.get('app.title', 'ProgTrack'), count=len(self.animals)))
            
            # If we have a loading label, update it
            if hasattr(self, 'loading_label'):
                self.loading_label.setText(f"Loaded {len(self.animals)} animals and {len(self.archived_animals)} archived animals")
            
            # Process any pending events
            QtWidgets.QApplication.processEvents()
            
        except Exception as e:
            logger.error(f"Error refreshing UI: {e}", exc_info=True)
            raise

    def _init_ui_components(self):
        """Initialize the UI components."""
        try:
            logger.info("Initializing UI components...")
            
            # Initialize main window components first
            self._init_main_window()
            
            # Initialize menu bar
            self._init_menu_bar()
            
            # Initialize toolbars
            self._init_toolbars()
            
            # Add a central widget with a simple layout for now
            self.central_widget = QtWidgets.QWidget()
            self.setCentralWidget(self.central_widget)
            
            # Create a main layout
            self.main_layout = QtWidgets.QVBoxLayout(self.central_widget)
            # Remove the temporary banner/progress UI: we use only the window status bar.
            
            # Initialize status bar
            self.statusBar().showMessage(self.messages.get("app.ui_initialized", "UI initialized"))
            
            # Connect signals
            self._connect_signals()
            
            logger.info("UI components initialized")
            
            # Force update the UI
            self.central_widget.update()
            QtWidgets.QApplication.processEvents()
            
        except Exception as e:
            logger.error(f"Error initializing UI components: {e}", exc_info=True)
            # Try to show an error message in a message box
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.critical(None, "UI Initialization Error", 
                                   f"Failed to initialize UI: {str(e)}\n\nCheck the log file for more details.")
            except Exception as dialog_error:
                logger.error("Could not show UI initialization error dialog: %s", dialog_error)
            raise
    
    def _init_main_window(self):
        """Initialize the main window components."""
        # Set up central widget and main layout
        self.central_widget = QtWidgets.QWidget()
        self.setCentralWidget(self.central_widget)
        self.main_layout = QtWidgets.QVBoxLayout(self.central_widget)
        
        # Add other UI components here
        # ...
    
    # ------------------------------------------------------------------
    # Plugin enable/disable persistence
    # ------------------------------------------------------------------

    def _load_disabled_plugins(self) -> set:
        """Load set of disabled plugin keys from disabled_plugins.json."""
        path = os.path.join(os.path.dirname(__file__), "disabled_plugins.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()

    def _save_disabled_plugins(self) -> None:
        """Persist current disabled plugin keys to disabled_plugins.json."""
        path = os.path.join(os.path.dirname(__file__), "disabled_plugins.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(sorted(self._disabled_plugins), f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save disabled_plugins.json: {e}")

    _PLUGIN_ACTION_ATTRS = {
        "animal_reports": "animal_reports_action",
        "flow_track": "flow_track_action",
        "projects_track": "projects_track_action",
        "heritage_track": "heritage_track_action",
        "cage_track": "cage_track_action",
        "medi_track": "medi_track_action",
        "steroid_track": "steroid_track_action",
    }

    def _detect_steroid_track_plugin(self) -> bool:
        """Steroid_track is considered installed when its __init__.py exists."""
        try:
            plugin_init = Path(__file__).parent / "Plugins" / "Steroid_track" / "__init__.py"
            return plugin_init.exists()
        except Exception:
            return False

    def _is_steroid_track_active(self) -> bool:
        """Return whether Steroid_track is installed and currently enabled."""
        return bool(getattr(self, 'has_steroid_track_plugin', False)) and (
            "steroid_track" not in getattr(self, '_disabled_plugins', set())
        )

    def _is_projects_track_active(self) -> bool:
        """Return whether ProjectsTrack is installed and currently enabled."""
        return bool(getattr(self, 'has_projects_plugin', False)) and (
            "projects_track" not in getattr(self, '_disabled_plugins', set())
        )

    def _build_export_filter_panel(
        self,
        animal_cbs: dict,
        role_header_widgets: dict,
    ):
        """Build a collapsible Species/Projects filter sidebar for export dialogs.

        animal_cbs            : {name: QCheckBox}
        role_header_widgets   : {role_value: [QWidget, ...]}  (separator + label)
        Returns a QWidget or None if ProjectsTrack is not active.
        """
        if not self._is_projects_track_active():
            return None
        try:
            from Plugins.Projects_Track.ProjectsTrack_plugin import (
                ProjectTabButton, _SidebarToggleButton)
        except ImportError:
            return None
        proj_plugin = getattr(self, 'projects_plugin', None)
        if proj_plugin is None:
            return None

        state = {'project': None, 'species': None}

        def apply_filter():
            proj = state['project']
            spec = state['species']
            # Track computed visibility per role without relying on isVisible()
            # (isVisible() returns False before the dialog is shown, hiding all headers)
            role_has_visible: dict = {rv: False for rv in role_header_widgets}
            for aname, cb in animal_cbs.items():
                rec = self.animals.get(aname, {})
                vis = True
                if proj:
                    vis = vis and (rec.get('project') == proj)
                if spec:
                    vis = vis and (rec.get('species', '') == spec)
                cb.setVisible(vis)
                rv = rec.get('rolle')
                if vis and rv in role_has_visible:
                    role_has_visible[rv] = True
            for role_val, widgets in role_header_widgets.items():
                for w in widgets:
                    w.setVisible(role_has_visible.get(role_val, False))

        container = QWidget()
        outer = QHBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sp_lbl   = self.messages.get('projects.sidebar.toggle.species',  'Species')
        proj_lbl = self.messages.get('projects.sidebar.toggle.projects', 'Projects')
        toggle_btn = _SidebarToggleButton(f"{sp_lbl} / {proj_lbl}")
        toggle_btn.setChecked(True)
        outer.addWidget(toggle_btn)

        CONT_W = 35

        # ── Species column ──────────────────────────────────────────────────
        sp_content = QWidget()
        sp_content.setFixedWidth(CONT_W)
        sp_cl = QVBoxLayout(sp_content)
        sp_cl.setContentsMargins(2, 4, 2, 0)
        sp_cl.setSpacing(2)
        sp_inner = QWidget()
        sp_il = QVBoxLayout(sp_inner)
        sp_il.setContentsMargins(0, 0, 0, 0)
        sp_il.setSpacing(2)
        sp_il.setAlignment(Qt.AlignmentFlag.AlignTop)
        sp_bg = QButtonGroup(sp_inner)
        sp_bg.setExclusive(True)
        sp_btns: dict = {}

        def _add_sp_btn(sp_name: str) -> None:
            btn = ProjectTabButton(sp_name)
            sp_bg.addButton(btn)
            sp_il.addWidget(btn)
            sp_btns[sp_name] = btn
            def _on(checked, s=sp_name):
                if not checked:
                    btn.setActive(False)
                    return
                for n, b in sp_btns.items():
                    b.setActive(n == s)
                state['species'] = None if s == 'All' else s
                apply_filter()
            btn.toggled.connect(_on)

        _add_sp_btn('All')
        for sp in (proj_plugin.all_species or []):
            _add_sp_btn(sp)
        sp_il.addStretch()
        sp_sa = QScrollArea(sp_content)
        sp_sa.setWidgetResizable(True)
        sp_sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sp_sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        sp_sa.setFrameShape(QFrame.Shape.NoFrame)
        sp_sa.setWidget(sp_inner)
        sp_cl.addWidget(sp_sa, 1)
        if 'All' in sp_btns:
            sp_btns['All'].setChecked(True)
            sp_btns['All'].setActive(True)
        outer.addWidget(sp_content)

        # ── Project column ──────────────────────────────────────────────────
        proj_content = QWidget()
        proj_content.setFixedWidth(CONT_W)
        pr_cl = QVBoxLayout(proj_content)
        pr_cl.setContentsMargins(2, 4, 2, 0)
        pr_cl.setSpacing(2)
        pr_inner = QWidget()
        pr_il = QVBoxLayout(pr_inner)
        pr_il.setContentsMargins(0, 0, 0, 0)
        pr_il.setSpacing(2)
        pr_il.setAlignment(Qt.AlignmentFlag.AlignTop)
        pr_bg = QButtonGroup(pr_inner)
        pr_bg.setExclusive(True)
        pr_btns: dict = {}

        def _add_pr_btn(pr_name: str) -> None:
            btn = ProjectTabButton(pr_name)
            pr_bg.addButton(btn)
            pr_il.addWidget(btn)
            pr_btns[pr_name] = btn
            def _on(checked, p=pr_name):
                if not checked:
                    btn.setActive(False)
                    return
                for n, b in pr_btns.items():
                    b.setActive(n == p)
                state['project'] = None if p == 'All' else p
                apply_filter()
            btn.toggled.connect(_on)

        _add_pr_btn('All')
        for pr in (proj_plugin.all_projects or []):
            _add_pr_btn(pr)
        pr_il.addStretch()
        pr_sa = QScrollArea(proj_content)
        pr_sa.setWidgetResizable(True)
        pr_sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pr_sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        pr_sa.setFrameShape(QFrame.Shape.NoFrame)
        pr_sa.setWidget(pr_inner)
        pr_cl.addWidget(pr_sa, 1)
        if 'All' in pr_btns:
            pr_btns['All'].setChecked(True)
            pr_btns['All'].setActive(True)
        outer.addWidget(proj_content)

        # ── Toggle collapse ─────────────────────────────────────────────────
        def _on_toggle(checked: bool) -> None:
            sp_content.setVisible(checked)
            proj_content.setVisible(checked)
            w = _SidebarToggleButton.TOGGLE_W
            if checked:
                n_cols = (1 if proj_plugin.all_species else 0) + 1
                w += CONT_W * n_cols
            container.setFixedWidth(w)
            if not checked:
                state['project'] = None
                state['species'] = None
            apply_filter()

        toggle_btn.toggled.connect(_on_toggle)
        # Start collapsed; apply_filter() initialises all role-header visibility
        toggle_btn.setChecked(False)
        _on_toggle(False)
        return container

    def _style_plugin_action(self, plugin_key: str, enabled: bool) -> None:
        """Set italic font on a bottom-group action when disabled, normal when enabled."""
        attr = self._PLUGIN_ACTION_ATTRS.get(plugin_key)
        if attr:
            action = getattr(self, attr, None)
            if action:
                font = action.font()
                font.setItalic(not enabled)
                action.setFont(font)

    def _toggle_plugin_enabled(self, plugin_key: str, enabled: bool) -> None:
        """Enable or disable a bottom-group plugin via its menu checkbox."""
        was_disabled = plugin_key in self._disabled_plugins
        if enabled:
            self._disabled_plugins.discard(plugin_key)
        else:
            self._disabled_plugins.add(plugin_key)
        # Only persist and update UI when the state actually changed
        if was_disabled == enabled:
            mt = getattr(self, 'master_track', None)
            if mt and mt.is_logged_in:
                # Logged-in user: save to personal session
                mt.save_session({"disabled_plugins": sorted(self._disabled_plugins)})
            elif mt and not mt.is_logged_in:
                # Guest with Master Track active: do NOT persist changes
                pass
            else:
                # No Master Track: save to global file (backwards compatibility)
                self._save_disabled_plugins()
            self._style_plugin_action(plugin_key, enabled)
            self._apply_plugin_state(plugin_key, enabled)

    def _apply_plugin_state(self, plugin_key: str, enabled: bool) -> None:
        """Show/hide plugin tab or sidebar widget based on enabled state."""
        enabled = enabled and self._role_allows_plugin_key(plugin_key)
        tabs = getattr(self, 'main_tabs', None)
        if tabs is not None:
            tab_map = {
                "animal_reports": ("tab.reports", "Reports"),
                "flow_track": ("tab.flow_track", "Flow Track"),
                "heritage_track": ("tab.heritage_track", "Heritage Track"),
                "cage_track": ("tab.cage_track", "Cage Track"),
                "medi_track": ("tab.medi_track", "Medi Track"),
                "projects_track": ("tab.project_track", "Project Track"),
            }
            if plugin_key in tab_map:
                msg_key, fallback = tab_map[plugin_key]
                tab_text = self.messages.get(msg_key, fallback)
                for i in range(tabs.count()):
                    if tabs.tabText(i) == tab_text:
                        tabs.setTabVisible(i, enabled)
                        if not enabled and tabs.currentIndex() == i:
                            tabs.setCurrentIndex(0)
                        break
        if plugin_key == "projects_track":
            w = getattr(self, '_projects_sidebar_widget', None)
            if w:
                w.setVisible(enabled)
        if plugin_key == "steroid_track":
            # Rebuild menus so Steroid-gated tool entries (e.g. PdG converter)
            # appear/disappear immediately when the plugin is toggled.
            self._setup_menus()
            self._apply_steroid_track_state()
            self._apply_master_button_states()

    def _apply_steroid_track_state(self) -> None:
        """Apply Steroid_track-dependent visibility for hormone/repro controls."""
        steroid_active = self._is_steroid_track_active()

        current_idx = self.category_tab.currentIndex() if hasattr(self, 'category_tab') and self.category_tab is not None else 0
        is_sperm_tab = current_idx == 1
        self._set_prog_event_plot_controls_visible(
            steroid_active and (self._tab_shows_prog_event_controls(current_idx) or self._tab_shows_events_only(current_idx)),
            events_only=self._tab_shows_events_only(current_idx)
        )
        self._set_sperm_controls_visible(steroid_active and is_sperm_tab)
        if hasattr(self, 'box_rad') and self.box_rad is not None:
            if steroid_active and is_sperm_tab:
                self.box_rad.setTitle(self.messages.get('line_style.sperm.group', 'Spermawerte'))
            else:
                self.box_rad.setTitle(self.messages['group.line_style.title'])

        if hasattr(self, 'rb_sperm_on') and hasattr(self, 'rb_sperm_off') and not steroid_active:
            self.rb_sperm_on.blockSignals(True)
            self.rb_sperm_off.blockSignals(True)
            self.rb_sperm_on.setChecked(False)
            self.rb_sperm_off.setChecked(False)
            self.rb_sperm_on.setEnabled(False)
            self.rb_sperm_off.setEnabled(False)
            self.rb_sperm_on.blockSignals(False)
            self.rb_sperm_off.blockSignals(False)

        if hasattr(self, 'phase_widget') and hasattr(self, 'category_tab'):
            self.phase_widget.setVisible(steroid_active and self.category_tab.currentIndex() == 0)

        if hasattr(self, 'category_tab'):
            self._update_category_tab_visibility()

        if hasattr(self, 'category_tab') and hasattr(self, 'btn_load_blood'):
            self._on_category_selected(self.category_tab.currentIndex())
        elif hasattr(self, 'lst'):
            self._refresh_list()

        if hasattr(self, 'selected_animals') and hasattr(self, 'dlay'):
            try:
                self._on_select()
            except Exception:
                pass

    def _init_pdg_plugin(self):
        """Initialize PdG Converter plugin if available.
        
        Sets has_pdg_plugin flag and creates pdg_cap capability object.
        Called during _init_application_state().
        """
        try:
            from Plugins.PdG_converter.plugin import PdGConverterPlugin
            self.pdg_cap = PdGConverterPlugin.register(self)
            self.has_pdg_plugin = self.pdg_cap.has_pdg
            logger.info("PdG Converter plugin loaded successfully")
        except ImportError as e:
            self.pdg_cap = None
            self.has_pdg_plugin = False
            logger.info(f"PdG Converter plugin not available: {e}")
    
    def _init_projects_plugin(self):
        """Initialize ProjectsTrack plugin if available.
        
        Sets has_projects_plugin flag and creates projects_plugin object.
        Called during _init_application_state().
        """
        try:
            from Plugins.Projects_Track import initialize as init_projects
            self.projects_plugin = init_projects(self)
            self.has_projects_plugin = self.projects_plugin is not None
            if self.has_projects_plugin:
                logger.info("ProjectsTrack plugin loaded successfully")
        except ImportError as e:
            self.projects_plugin = None
            self.has_projects_plugin = False
            logger.info(f"ProjectsTrack plugin not available: {e}")
        except Exception as e:
            self.projects_plugin = None
            self.has_projects_plugin = False
            logger.error(f"ProjectsTrack plugin initialization failed: {e}")

    def _init_heritage_plugin(self):
        """Initialize Heritage_Track plugin if available."""
        try:
            from Plugins.Heritage_Track import initialize as init_heritage

            self.heritage_plugin = init_heritage(self)
            self.has_heritage_plugin = self.heritage_plugin is not None
            if self.has_heritage_plugin:
                logger.info("Heritage_Track plugin loaded successfully")
        except ImportError as e:
            self.heritage_plugin = None
            self.has_heritage_plugin = False
            logger.info(f"Heritage_Track plugin not available: {e}")
        except Exception as e:
            self.heritage_plugin = None
            self.has_heritage_plugin = False
            logger.error(f"Heritage_Track plugin initialization failed: {e}")

    def _init_cage_track_plugin(self):
        """Initialize Cage_Track plugin if available."""
        try:
            from Plugins.Cage__Track import initialize as init_cage_track

            self.cage_track_plugin = init_cage_track(self)
            self.has_cage_track_plugin = self.cage_track_plugin is not None
            if self.has_cage_track_plugin:
                logger.info("Cage_Track plugin loaded successfully")
        except ImportError as e:
            self.cage_track_plugin = None
            self.has_cage_track_plugin = False
            logger.info(f"Cage_Track plugin not available: {e}")
        except Exception as e:
            self.cage_track_plugin = None
            self.has_cage_track_plugin = False
            logger.error(f"Cage_Track plugin initialization failed: {e}")

    def _init_medi_track_plugin(self):
        """Initialize Medi_Track plugin if available."""
        try:
            from Plugins.Medi_Track import initialize as init_medi_track
            self.medi_track_plugin = init_medi_track(self)
            self.has_medi_track_plugin = self.medi_track_plugin is not None
            if self.has_medi_track_plugin:
                logger.info("Medi_Track plugin loaded successfully")
        except ImportError as e:
            self.medi_track_plugin = None
            self.has_medi_track_plugin = False
            logger.info(f"Medi_Track plugin not available: {e}")
        except Exception as e:
            self.medi_track_plugin = None
            self.has_medi_track_plugin = False
            logger.error(f"Medi_Track plugin initialization failed: {e}")

    def _setup_medi_track_ui_if_needed(self):
        """Add Medi Track tab and menu items if plugin loaded after initial UI setup."""
        if not getattr(self, 'has_medi_track_plugin', False):
            return
        if getattr(self, 'medi_track_tab_placeholder', None) is not None:
            return  # Already set up
        if not hasattr(self, 'main_tabs') or self.main_tabs is None:
            return  # UI not ready yet, will be added during normal tab setup

        # Add tab placeholder (actual widget loads on first selection)
        self.medi_track_tab = None
        self.medi_track_tab_placeholder = QWidget()
        self.main_tabs.addTab(self.medi_track_tab_placeholder,
                              self.messages.get("tab.medi_track", "Medi Track"))

        # Add to Tools menu if not already there
        mb = self.menuBar()
        if mb:
            for action in mb.actions():
                if action.text() == self.messages.get("menu.tools", "Tools"):
                    tools_menu = action.menu()
                    if tools_menu:
                        # Check if already added
                        already_added = any(
                            a.text() == self.messages.get("menu.tools.medi_track", "Medi Track")
                            for a in tools_menu.actions()
                        )
                        if not already_added:
                            self.medi_track_action = QAction(
                                self.messages.get("menu.tools.medi_track", "Medi Track"), self)
                            self.medi_track_action.setCheckable(True)
                            self.medi_track_action.setChecked(True)
                            self.medi_track_action.toggled.connect(
                                lambda c: self._toggle_plugin_enabled("medi_track", c))
                            self._style_plugin_action("medi_track", True)
                            tools_menu.addAction(self.medi_track_action)
                    break

        # Add File menu export option
        for action in mb.actions():
            if action.text() == self.messages.get("menu.file", "File"):
                file_menu = action.menu()
                if file_menu:
                    # Find where to insert (before Database section)
                    for i, a in enumerate(file_menu.actions()):
                        if a.isSeparator():
                            # Insert Medi Track section
                            file_menu.insertSection(
                                a,
                                self.messages.get("menu.file.section.medi_track", "Medi Track"))
                            medi_pdf_action = QAction(
                                self.messages.get("menu.file.export_medi_pdf", "Export Medi Track (.pdf)"), self)
                            medi_pdf_action.triggered.connect(self._dlg_export_medi_track_pdf)
                            file_menu.insertAction(a, medi_pdf_action)
                            break
                break

        # Apply disabled state if needed
        if "medi_track" in getattr(self, '_disabled_plugins', set()):
            self._apply_plugin_state("medi_track", False)

        logger.info("Medi Track UI added after plugin load")

    def _init_sample_track_plugin(self):
        """Initialize Sample_Track plugin if available."""
        try:
            from Plugins.Sample_Track import initialize as init_sample_track
            self.sample_track_plugin = init_sample_track(self)
            self.has_sample_track_plugin = self.sample_track_plugin is not None
            if self.has_sample_track_plugin:
                logger.info("Sample_Track plugin loaded successfully")
        except ImportError as e:
            self.sample_track_plugin = None
            self.has_sample_track_plugin = False
            logger.info(f"Sample_Track plugin not available: {e}")
        except Exception as e:
            self.sample_track_plugin = None
            self.has_sample_track_plugin = False
            logger.error(f"Sample_Track plugin initialization failed: {e}")

    def _init_master_track_plugin(self):
        """Initialize Master_Track plugin if available."""
        try:
            master_path = Path(__file__).parent / "Plugins" / "Master_Track" / "plugin.py"
            if not master_path.exists():
                self.master_track = None
                self.has_master_track = False
                return
            from Plugins.Master_Track import initialize as init_master

            self.master_track = init_master(self)
            self.has_master_track = self.master_track is not None
            if self.has_master_track:
                logger.info("Master_Track plugin loaded successfully")
        except ImportError as e:
            self.master_track = None
            self.has_master_track = False
            logger.info(f"Master_Track plugin not available: {e}")
        except Exception as e:
            self.master_track = None
            self.has_master_track = False
            logger.error(f"Master_Track plugin initialization failed: {e}")

    def _master_can(self, action: str) -> bool:
        """Central permission check delegating to Master_Track if active.
        Returns True (full access) when Master_Track is not installed
        or has been disabled by a Lord account."""
        mt = getattr(self, 'master_track', None)
        if mt is None:
            return True  # not installed → full access
        if "master_track" in getattr(self, '_disabled_plugins', set()):
            return True  # disabled by Lord → full access
        allowed = mt.can(action)
        if allowed and action in {
            "core.create_animals", "core.edit_animal_core", "core.edit_animal_immutable",
            "core.archive_animals", "core.delete_animals", "core.import",
            "reports.write",
        }:
            self._audit_data_operation(action)
        return allowed

    def _infer_audit_source(self) -> Tuple[str, str, Dict[str, Any]]:
        """Infer plugin/module + caller function/context for audit entries."""
        try:
            skip_functions = {
                "_infer_audit_source",
                "_audit_data_operation",
                "_master_can",
                "_master_audit",
                "_can",
                "can",
            }

            for frame_info in inspect.stack()[3:]:
                func_name = str(getattr(frame_info, "function", "") or "")
                if not func_name or func_name in skip_functions:
                    continue

                filename = str(getattr(frame_info, "filename", "") or "")
                normalized = filename.replace("\\", "/")
                plugin_name = "ProgTrack"

                marker = "/Plugins/"
                idx = normalized.lower().find(marker.lower())
                if idx >= 0:
                    rel = normalized[idx + len(marker):]
                    first = rel.split("/", 1)[0].strip()
                    if first:
                        plugin_name = first

                context: Dict[str, Any] = {}
                frame = getattr(frame_info, "frame", None)
                local_vars = getattr(frame, "f_locals", {}) if frame is not None else {}
                _missing = object()

                def _pick_first(keys: Tuple[str, ...]) -> Any:
                    for key in keys:
                        if key in local_vars:
                            return local_vars.get(key)
                    return _missing

                animal_value = _pick_first((
                    "animal_name", "new_name", "name", "selected_animal", "animal", "key"
                ))
                if isinstance(animal_value, str) and animal_value.strip():
                    context["animal"] = animal_value.strip()

                owner = local_vars.get("self") if isinstance(local_vars, dict) else None
                if "animal" not in context and owner is not None:
                    try:
                        report_animal = str(getattr(owner, "report_current_animal", "") or "").strip()
                        if report_animal:
                            context["animal"] = report_animal
                    except Exception:
                        pass
                if "animal" not in context and owner is not None:
                    try:
                        selected_animals = getattr(owner, "selected_animals", None)
                        if isinstance(selected_animals, list) and len(selected_animals) == 1:
                            one = str(selected_animals[0] or "").strip()
                            if one:
                                context["animal"] = one
                    except Exception:
                        pass

                parameter_value = _pick_first(("parameter", "field", "column", "typ", "event_type"))
                if isinstance(parameter_value, str) and parameter_value.strip():
                    context["parameter"] = parameter_value.strip()

                prev_value = _pick_first(("old_value", "previous_value", "before"))
                new_value = _pick_first(("new_value", "after", "value"))

                simple_types = (str, int, float, bool, list, dict, tuple, type(None))
                if prev_value is not _missing and isinstance(prev_value, simple_types):
                    context["previous"] = prev_value
                if new_value is not _missing and isinstance(new_value, simple_types):
                    context["new"] = new_value

                return plugin_name, func_name, context
        except Exception:
            pass

        return "ProgTrack", "", {}

    def _audit_data_operation(self, action: str) -> None:
        """Write standardized audit entry for successful data operations."""
        plugin_name, func_name, context = self._infer_audit_source()
        if func_name in {
            "_save_database",
            "_dlg_new_animal",
            "_dlg_edit_animal",
            "_import_excel",
            "_archive_current",
            "_restore_archived",
            "_delete_archived",
            "_report_cell_clicked",
            "_report_cell_changed",
            "_report_cell_double_clicked",
        }:
            return
        if (
            plugin_name == "ProgTrack"
            and not context.get("animal")
            and not context.get("parameter")
            and "previous" not in context
            and "new" not in context
        ):
            return

        detail_parts: List[str] = []
        if func_name:
            detail_parts.append(f"function={func_name}")
        detail_parts.append(f"animal={context.get('animal', '<unknown>')}")
        detail_parts.append(f"parameter={context.get('parameter', '<unknown>')}")
        if "previous" in context:
            detail_parts.append(f"previous={self._audit_value_to_string(context['previous'])}")
        else:
            detail_parts.append("previous=<unknown>")
        if "new" in context:
            detail_parts.append(f"new={self._audit_value_to_string(context['new'])}")
        else:
            detail_parts.append("new=<unknown>")

        details = "; ".join(detail_parts)
        self._master_audit(f"data_{action}", plugin_name, details)

    def _show_permission_denied(self) -> None:
        """Show a message box indicating the action is not permitted."""
        QMessageBox.warning(
            self,
            self.messages.get("master_track.error.title", "Error"),
            self.messages.get("master_track.error.permission_denied",
                              "You do not have permission to perform this action."),
        )

    def _master_audit(self, action: str, target: str = "", details: str = "") -> None:
        """Write an audit log entry if Master_Track is active."""
        mt = getattr(self, 'master_track', None)
        if mt:
            mt.audit(action, target, details)

    def _audit_value_to_string(self, value: Any, max_len: int = 600) -> str:
        """Render audit values as compact JSON-compatible strings."""
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = repr(value)
        if len(text) > max_len:
            return text[: max_len - 3] + "..."
        return text

    def _audit_data_snapshot_diff(self, before_data: Dict[str, Any], after_data: Dict[str, Any]) -> None:
        """Audit parameter-level before/after changes for core animal data."""
        if not isinstance(before_data, dict):
            before_data = {}
        if not isinstance(after_data, dict):
            after_data = {}

        for scope in ("animals", "archived_animals"):
            before_scope = before_data.get(scope, {})
            after_scope = after_data.get(scope, {})
            if not isinstance(before_scope, dict):
                before_scope = {}
            if not isinstance(after_scope, dict):
                after_scope = {}

            before_names = set(before_scope.keys())
            after_names = set(after_scope.keys())

            # Full-record creates
            for animal_name in sorted(after_names - before_names, key=str.lower):
                new_rec = after_scope.get(animal_name, {})
                details = (
                    f"scope={scope}; animal={animal_name}; parameter=<record>; "
                    f"previous={self._audit_value_to_string(None)}; "
                    f"new={self._audit_value_to_string(new_rec)}"
                )
                self._master_audit("data_create", "ProgTrack", details)

            # Full-record deletes
            for animal_name in sorted(before_names - after_names, key=str.lower):
                old_rec = before_scope.get(animal_name, {})
                details = (
                    f"scope={scope}; animal={animal_name}; parameter=<record>; "
                    f"previous={self._audit_value_to_string(old_rec)}; "
                    f"new={self._audit_value_to_string(None)}"
                )
                self._master_audit("data_delete", "ProgTrack", details)

            # Parameter-level edits
            for animal_name in sorted(before_names & after_names, key=str.lower):
                old_rec = before_scope.get(animal_name, {})
                new_rec = after_scope.get(animal_name, {})

                if old_rec == new_rec:
                    continue

                if not isinstance(old_rec, dict) or not isinstance(new_rec, dict):
                    details = (
                        f"scope={scope}; animal={animal_name}; parameter=<record>; "
                        f"previous={self._audit_value_to_string(old_rec)}; "
                        f"new={self._audit_value_to_string(new_rec)}"
                    )
                    self._master_audit("data_edit", "ProgTrack", details)
                    continue

                changed_params = sorted(set(old_rec.keys()) | set(new_rec.keys()), key=str.lower)
                for parameter in changed_params:
                    previous_value = old_rec.get(parameter)
                    new_value = new_rec.get(parameter)
                    if previous_value == new_value:
                        continue

                    details = (
                        f"scope={scope}; animal={animal_name}; parameter={parameter}; "
                        f"previous={self._audit_value_to_string(previous_value)}; "
                        f"new={self._audit_value_to_string(new_value)}"
                    )
                    self._master_audit("data_edit", "ProgTrack", details)

    def _init_menu_bar(self):
        """Initialize the menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        # Add file actions here
        
        # Edit menu
        edit_menu = menubar.addMenu("&Edit")
        # Add edit actions here
        
        # View menu
        view_menu = menubar.addMenu("&View")
        # Add view actions here
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        # Add help actions here
    
    def _init_toolbars(self):
        """Initialize toolbars."""
        # Main toolbar
        self.main_toolbar = self.addToolBar("Main Toolbar")
        self.main_toolbar.setVisible(False)
        # Add toolbar actions here
    
    def _connect_signals(self):
        """Connect signals to slots."""
        # Connect signals to slots here
        pass
    
    def _update_toggle_controls(self):
        """Enable or disable line style toggles depending on checkboxes."""
        steroid_active = self._is_steroid_track_active()
        weight_enabled = self.chk_weight.isChecked() and self.chk_weight.isEnabled()

        # Progesterone sub-checkboxes control their respective line style toggles
        if self.has_pdg_plugin and steroid_active and hasattr(self, 'chk_mode_combined'):
            combined_enabled = self.chk_mode_combined.isChecked() and self.chk_mode_combined.isEnabled()
            self.rb_combined_on.setEnabled(combined_enabled)
            self.rb_combined_off.setEnabled(combined_enabled)
        blood_enabled = steroid_active and self.chk_mode_blood.isChecked() and self.chk_mode_blood.isEnabled()
        self.rb_blood_on.setEnabled(blood_enabled)
        self.rb_blood_off.setEnabled(blood_enabled)
        
        # PdG widgets - conditional on plugin presence
        if self.has_pdg_plugin and steroid_active and hasattr(self, 'chk_mode_urin'):
            urine_enabled = self.chk_mode_urin.isChecked() and self.chk_mode_urin.isEnabled()
            self.rb_urine_on.setEnabled(urine_enabled)
            self.rb_urine_off.setEnabled(urine_enabled)
        
        self.rb_weight_on.setEnabled(weight_enabled)
        self.rb_weight_off.setEnabled(weight_enabled)

    # _update_urine_scale_enable method has been removed - always use urine scale

    # Keep the FSH/Prog triangle clip rect in sync whenever an axes xlim changes
    def _sync_tri_clip_rect(self, axes):
        try:
            rect = self._tri_clip_by_ax.get(axes)
            if rect is None:
                return
            x0, x1 = axes.get_xlim()
            rect.set_x(x0)
            rect.set_width(x1 - x0)
        except Exception:
            pass

    # Clamp x-limits to a safe Matplotlib date range to avoid DateLocator overflows.
    def _clamp_xlims(self, axes):
        try:
            xmin, xmax = axes.get_xlim()
            # Bail out if they are not finite
            if not np.isfinite(xmin) or not np.isfinite(xmax):
                return
            new_min = min(max(xmin, MDATES_MIN_NUM), MDATES_MAX_NUM)
            new_max = min(max(xmax, MDATES_MIN_NUM), MDATES_MAX_NUM)
            if (new_min, new_max) != (xmin, xmax):
                axes.set_xlim(new_min, new_max)
        except Exception:
            pass


    def _format_tooltip(self, data_type: str, date: datetime, value: Optional[float], animal_name: str, extra: Optional[dict] = None) -> str:
        """Return a human-friendly tooltip string for the given measurement.

        This helper centralizes tooltip formatting across all roles and measurement types.

        Parameters
        ----------
        data_type : str
            One of 'sperm_total', 'sperm_motility', 'sperm_progressive', 'fsh',
            'progesteron', 'progesterone', 'weight', 'pdg', 'pdg_conv'.
        date : datetime
            The date of the measurement.
        value : Optional[float]
            The primary numeric value associated with the measurement.
        animal_name : str
            Name of the animal the measurement belongs to.
        extra : dict, optional
            Additional values needed for certain measurements.  For sperm types this
            expects keys 'mot_pct' and 'prog_pct'; for 'pdg_conv' it expects key
            'orig_pdg'.  Missing keys are treated as None.
        """
        if extra is None:
            extra = {}
        # ensure we can lowercase the data_type
        try:
            dtype = data_type.lower()
        except Exception:
            dtype = str(data_type).lower()
        # format date once
        date_str = date.strftime('%d.%m.%Y') if hasattr(date, 'strftime') else str(date)
        date_label = self.messages.get('plot.tooltip.date_label', 'Date')
        sample_label = self.messages.get('plot.tooltip.sample_label', 'Sample')
        value_label = self.messages.get('plot.tooltip.value_label', 'Value')
        na_label = self.messages.get('plot.tooltip.na', 'n/a')
        vs_previous_label = self.messages.get('plot.tooltip.vs_previous', 'vs. previous')
        sperm_unit = self.messages.get('plot.tooltip.sperm_unit', 'Sperm/ml')
        weight_unit = self.messages.get('plot.unit.gram', 'g')
        prog_unit = self.messages.get('plot.unit.ng_per_ml', 'ng/ml')
        pdg_unit = self.messages.get('plot.unit.ug_per_mg_cr', 'µg/mg Cr')
        percent_unit = self.messages.get('plot.unit.percent', '%')
        total_label = self.messages.get('plot.tooltip.total_label', 'Total')
        motile_label = self.messages.get('plot.tooltip.motile_label', 'Motile')
        progressive_label = self.messages.get('plot.tooltip.progressive_label', 'Progressive')
        prog_label = self.messages.get('plot.series.progesterone', 'Progesterone')
        weight_label = self.messages.get('plot.series.weight', 'Weight')
        pdg_label = self.messages.get('plot.series.pdg', 'PdG')
        prog_computed_label = self.messages.get('plot.tooltip.progesterone_computed_label', 'Progesterone (computed)')
        pdg_orig_label = self.messages.get('plot.tooltip.pdg_original_label', 'orig. PdG')
        # Sperm measurement tooltips
        if dtype in ('sperm_total', 'sperm_motility', 'sperm_progressive'):
            mot_pct = extra.get('mot_pct')
            prog_pct = extra.get('prog_pct')
            if dtype == 'sperm_total':
                # total count per ml
                val_str = f"{value:.0f}" if value is not None else na_label
                return f"{date_label}: {date_str}\n{total_label}\n{val_str} {sperm_unit}"
            elif dtype == 'sperm_motility':
                cnt_text = f"{value:.0f} {sperm_unit}" if value is not None else na_label
                if mot_pct is not None:
                    return f"{date_label}: {date_str}\n{motile_label} ({mot_pct:.0f}{percent_unit})\n{cnt_text}"
                else:
                    return f"{date_label}: {date_str}\n{motile_label}\n{cnt_text}"
            else:  # sperm_progressive
                cnt_text = f"{value:.0f} {sperm_unit}" if value is not None else na_label
                if prog_pct is not None:
                    return f"{date_label}: {date_str}\n{progressive_label} ({prog_pct:.0f}{percent_unit})\n{cnt_text}"
                else:
                    return f"{date_label}: {date_str}\n{progressive_label}\n{cnt_text}"
        # FSH events
        if dtype == 'fsh':
            return f"{self.messages.get('plot.event.fsh', 'FSH')} – {date_str}"
        # Progesterone measurements
        if dtype in ('progesteron', 'progesterone'):
             # For triangle events (clicked below x-axis) we pass value=None,
             # so only the date is shown, analogous to FSH.
             if value is None:
                 return f"{prog_label} – {date_str}"
             # Check for sample ID (probennummer)
             probennummer = extra.get('probennummer')
             result = f"{date_label}: {date_str}\n{prog_label}: {value:.2f} {prog_unit}"
             if probennummer:
                 result += f"\n{sample_label}: {probennummer}"
             return result
        # Weight measurements
        if dtype == 'weight':
            rec = self.animals.get(animal_name, {})
            rolle = rec.get('rolle')
            # Normalize date for comparison
            dt_date = date.date() if isinstance(date, datetime) else date
            # allow "missing" weight entries to display a tooltip
            if value is None:
                return f"{date_label}: {date_str}\n{weight_label}: {na_label}"
            if rolle == Role.OFFSPRING.value:
                # For offspring compare to the last weight before this date
                prev_val = None
                try:
                    series = sorted(rec.get('gewicht', []), key=lambda t: t['datum'])
                    for entry in series:
                        # entry['datum'] is datetime
                        if entry['datum'].date() < dt_date:
                            prev_val = entry['wert']
                        elif entry['datum'].date() == dt_date:
                            break
                except Exception:
                    prev_val = None
                if prev_val not in (None, 0, False):
                    delta_pct = ((value - prev_val) / prev_val) * 100.0
                    sign = '+' if delta_pct >= 0 else ''
                    return f"{date_label}: {date_str}\n{weight_label}: {value:.0f} {weight_unit} ({sign}{delta_pct:.1f} {percent_unit} {vs_previous_label})"
                return f"{date_label}: {date_str}\n{weight_label}: {value:.0f} {weight_unit}"
            else:
                # For other animals compare to reference weight
                ref_w = rec.get('ref_weight', DEFAULT_REF_WEIGHT) or 0
                if ref_w > 0:
                    delta_pct = ((value - ref_w) / ref_w) * 100.0
                    sign = '+' if delta_pct >= 0 else ''
                    return f"{date_label}: {date_str}\n{weight_label}: {value:.0f} {weight_unit} ({sign}{delta_pct:.0f} {percent_unit})"
                return f"{date_label}: {date_str}\n{weight_label}: {value:.0f} {weight_unit}"
        # Raw PdG measurement
        if dtype == 'pdg':
            probennummer = extra.get('probennummer')
            result = f"{date_label}: {date_str}\n{pdg_label}: {value:.2f} {pdg_unit}"
            if probennummer:
                result += f"\n{sample_label}: {probennummer}"
            return result
        # Converted PdG → Progesteron
        if dtype == 'pdg_conv':
            orig_pdg = extra.get('orig_pdg')
            probennummer = extra.get('probennummer')
            conv_line = f"{prog_computed_label}: {value:.2f} {prog_unit}"
            if orig_pdg is None:
                result = f"{date_label}: {date_str}\n{conv_line}\n{pdg_orig_label}: {na_label}"
            else:
                result = f"{date_label}: {date_str}\n{conv_line}\n{pdg_orig_label}: {orig_pdg:.2f} {pdg_unit}"
            if probennummer:
                result += f"\n{sample_label}: {probennummer}"
            return result
        # Default fallback: show date and value if present
        return f"{date_label}: {date_str}\n{value_label}: {value}" if value is not None else f"{date_label}: {date_str}"

    # ——————————————————————————————————————————————
    # 7.0 Language persistence & bundle‐loading
    # ——————————————————————————————————————————————
    def _get_user_style_settings_file(self, username=None):
        """Get the path to the user-specific style settings file."""
        if username is None:
            # Use current logged-in user or 'guest' as fallback
            username = getattr(self, 'master_track', None)
            if username and username.is_logged_in:
                username = username.current_username
            else:
                username = 'guest'
        
        # Create user-specific settings directory if it doesn't exist
        user_settings_dir = os.path.join('Plugins', 'core', 'user_settings')
        os.makedirs(user_settings_dir, exist_ok=True)
        
        return os.path.join(user_settings_dir, f"{username}_style.json")

    def _load_user_style_settings(self, username=None):
        """Load style settings for a specific user.

        If Master Track is active and user is logged in, load from session.
        Otherwise fall back to file-based storage.
        """
        # Check if Master Track is active
        mt = getattr(self, 'master_track', None)
        if mt and mt.is_logged_in:
            # Load from Master Track session
            session = mt.load_session()
            style_settings = session.get("style_settings", {})
            if style_settings:
                return style_settings
            # If empty, return defaults (will be saved when user changes settings)
            return self._get_default_style_settings()

        # Fall back to file-based storage for non-Master Track setups
        user_style_file = self._get_user_style_settings_file(username)
        if os.path.exists(user_style_file):
            try:
                with open(user_style_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logging.error(f"Failed to load user style settings: {e}")

        # Return default settings if file doesn't exist or loading fails
        return self._get_default_style_settings()

    def _save_user_style_settings(self, settings, username=None):
        """Save style settings for a specific user.

        If Master Track is active and user is logged in, save to session.
        Otherwise fall back to file-based storage.
        """
        # Check if Master Track is active
        mt = getattr(self, 'master_track', None)
        if mt and mt.is_logged_in:
            # Save to Master Track session
            mt.save_session({"style_settings": settings})
            return
        elif mt and not mt.is_logged_in:
            # Don't save for guests when Master Track is active
            return

        # Fall back to file-based storage for non-Master Track setups
        # Get username if not provided
        if username is None:
            username = 'default_user'

        user_style_file = self._get_user_style_settings_file(username)
        try:
            with open(user_style_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save user style settings: {e}")

    def _load_settings(self):
        # Check if Master Track is active and user is logged in
        mt = getattr(self, 'master_track', None)
        if mt and mt.is_logged_in:
            # Load language from Master Track session
            session = mt.load_session()
            user_lang = session.get("language")
            if user_lang:
                self.lang = user_lang
            else:
                # Fallback to file or default
                self.lang = self._get_global_language_fallback()
            # Load user-specific style settings
            user_style_settings = self._load_user_style_settings()
            self._apply_style_settings(user_style_settings)
            return

        # Non-Master Track or guest mode: load from file
        self.lang = self._get_global_language_fallback()
        # Load user-specific style settings
        user_style_settings = self._load_user_style_settings()
        self._apply_style_settings(user_style_settings)

    def _get_global_language_fallback(self):
        """Get language from global settings file or default to 'en'."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("language", "en")
            except Exception:
                logging.error("Failed to read settings file; defaulting language to 'en'")
                return "en"
        return "en"

    def _save_settings(self):
        # Check if running in read-only mode
        if self.read_only_mode:
            logger.warning("Attempted to save settings in READ-ONLY mode - operation skipped")
            return

        # Check if Master Track is active
        mt = getattr(self, 'master_track', None)
        if mt and mt.is_logged_in:
            # Save language to Master Track session (per-user)
            mt.save_session({"language": self.lang})
            return
        elif mt and not mt.is_logged_in:
            # Guest mode: do NOT persist language changes between sessions
            return

        # Non-Master Track: save to global settings file
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "language": self.lang
                }, f, indent=2)
        except Exception as e:
            logging.error(f"Failed to save settings: {e}")

    def _load_messages(self, lang_code):
        """Load translation messages for the specified language code."""
        base_path = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_path, "lang", f"messages_{lang_code}.json")
        try:
            with open(path, encoding="utf-8") as f:
                self.messages = json.load(f)
        except Exception:
            # fallback to German
            fallback_path = os.path.join(base_path, "lang", "messages_de.json")
            with open(fallback_path, encoding="utf-8") as f:
                self.messages = json.load(f)
        self._merge_user_messages(lang_code)

    def _user_messages_path(self, lang_code: Optional[str] = None) -> Path:
        lang_code = lang_code or getattr(self, "lang", "de") or "de"
        return APP_BASE_DIR / "Plugins" / "core" / "user_lang" / f"messages_{lang_code}.json"

    def _merge_user_messages(self, lang_code: Optional[str] = None) -> None:
        path = self._user_messages_path(lang_code)
        if not path.is_file():
            return
        try:
            with path.open("r", encoding="utf-8") as handle:
                overrides = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            logging.warning("Failed to load user language overrides from %s: %s", path, exc)
            return
        if isinstance(overrides, dict):
            self.messages.update({str(key): str(value) for key, value in overrides.items()})

    def _save_role_label_overrides(self, roles: List[Dict[str, Any]]) -> None:
        path = self._user_messages_path()
        existing: Dict[str, str] = {}
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                    if isinstance(loaded, dict):
                        existing = {str(key): str(value) for key, value in loaded.items()}
            except (OSError, json.JSONDecodeError) as exc:
                logging.warning("Failed to read user language overrides from %s: %s", path, exc)

        changed = False
        for role in roles:
            if not isinstance(role, dict):
                continue
            value = canonical_role_value(role.get("value") or role.get("role_id") or "")
            label = str(role.get("label") or "").strip()
            if not value or not label:
                continue
            label_key = str(role.get("label_key") or "").strip() or f"role.{value}"
            if existing.get(label_key) != label:
                existing[label_key] = label
                changed = True
        if not changed:
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(existing, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        self.messages.update(existing)
    
    def _init_all_plugins_deferred(self, _step=0):
        """Initialize all plugins in background while login dialog is open.

        Called via QTimer.singleShot(...) before startup() blocks on login.
        By the time the user finishes typing credentials, plugins are loaded.

        Uses stepped loading with processEvents() to keep the login UI responsive.
        """
        # Prevent double-execution if plugins were already loaded synchronously
        if getattr(self, '_plugins_loaded', False):
            return

        if _step == 0:
            # ------------------------
            # 7.0 Check for Reports Plugin
            # ------------------------
            reports_plugin_path = Path(__file__).parent / "Plugins" / "Animal_Reports" / "animal_reports.py"
            self.reports_enabled = reports_plugin_path.exists()
            QtWidgets.QApplication.processEvents()

            # Check for Embryo Tracker Plugin
            embryo_tracker_plugin_path = Path(__file__).parent / "Plugins" / "Embryo_Track" / "embryo_track.py"
            self.embryo_tracker_enabled = embryo_tracker_plugin_path.exists()
            QtWidgets.QApplication.processEvents()

            # Check for Network Track Plugin (heavy - creates widget)
            network_track_plugin_path = Path(__file__).parent / "Plugins" / "Network_Track" / "network_track.py"
            self.network_track_enabled = network_track_plugin_path.exists()
            self.network_track_window = None
            if self.network_track_enabled:
                try:
                    from Plugins.Network_Track.network_track import NetworkTrackWidget
                    QtWidgets.QApplication.processEvents()
                    self.network_track_window = NetworkTrackWidget(self.messages, self, app=self)
                    QtWidgets.QApplication.processEvents()
                    self.network_track_window.hide()
                except Exception as e:
                    logging.error(f"Failed to initialize Network Track at startup: {e}", exc_info=True)
                    self.network_track_window = None

            QtWidgets.QApplication.processEvents()
            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=1))

        elif _step == 1:
            # Check for Flow Track Plugin
            flow_track_plugin_path = Path(__file__).parent / "Plugins" / "Flow_Track" / "flow_track_widget.py"
            self.flow_track_enabled = flow_track_plugin_path.exists()
            self.flow_track_window = None
            QtWidgets.QApplication.processEvents()

            # Check for Steroid_track gating plugin
            self.has_steroid_track_plugin = self._detect_steroid_track_plugin()
            QtWidgets.QApplication.processEvents()

            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=2))

        elif _step == 2:
            # ------------------------
            # 7.0a Check for PdG Converter Plugin
            # ------------------------
            self._init_pdg_plugin()
            QtWidgets.QApplication.processEvents()
            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=3))

        elif _step == 3:
            # ------------------------
            # 7.0b Check for ProjectsTrack Plugin (can be heavy)
            # ------------------------
            self._init_projects_plugin()
            QtWidgets.QApplication.processEvents()
            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=4))

        elif _step == 4:
            # ------------------------
            # 7.0c Check for Heritage_Track Plugin
            # ------------------------
            self._init_heritage_plugin()
            QtWidgets.QApplication.processEvents()
            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=5))

        elif _step == 5:
            # ------------------------
            # 7.0d Check for Cage_Track Plugin
            # ------------------------
            self._init_cage_track_plugin()
            QtWidgets.QApplication.processEvents()
            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=6))

        elif _step == 6:
            # ------------------------
            # 7.0e Check for Medi_Track Plugin
            # ------------------------
            self._init_medi_track_plugin()
            QtWidgets.QApplication.processEvents()
            QTimer.singleShot(0, lambda: self._init_all_plugins_deferred(_step=7))

        elif _step == 7:
            # ------------------------
            # 7.0f Check for Sample_Track Plugin
            # ------------------------
            self._init_sample_track_plugin()
            self._plugins_loaded = True
            logger.info("Deferred plugin loading completed")
            # Add Medi Track UI if plugin loaded after initial UI setup
            self._setup_medi_track_ui_if_needed()

    def _init_application_state(self):
        """Initialize application state and build the UI (called once during startup)."""
        # Load disabled-plugin set before menu/tab setup
        self._disabled_plugins = self._load_disabled_plugins()

        # ------------------------
        # 6.9 Master_Track — authentication & permissions
        # ------------------------
        self._init_master_track_plugin()
        if self.has_master_track:
            global_disabled = self._load_disabled_plugins()
            if "master_track" in global_disabled:
                # MT is globally disabled – run as guest, skip login dialog
                self.master_track._set_guest()
                self._disabled_plugins.add("master_track")
                # No login dialog → load plugins synchronously (all steps at once)
                for step in range(8):
                    self._init_all_plugins_deferred(step)
                    QtWidgets.QApplication.processEvents()
                self._plugins_loaded = True
            else:
                # ← Queue all plugin loading AFTER a short delay so login dialog
                #   can paint its UI first, then load plugins in the background
                QTimer.singleShot(50, self._init_all_plugins_deferred)

                self.master_track.startup()  # blocks here while user types

                # Apply per-user disabled plugins from session
                session = self.master_track.load_session()
                user_disabled = set(session.get("disabled_plugins", []))
                if self.master_track.is_logged_in:
                    self._disabled_plugins = user_disabled
                    # Apply user's saved language preference (load messages only, UI refresh happens later)
                    user_lang = session.get("language")
                    if user_lang:
                        self.lang = user_lang
                        self._load_messages(self.lang)
                else:
                    # User chose "Guest" - plugins may not have finished loading
                    # because the dialog closed quickly. Load synchronously.
                    logger.info("Guest mode selected, completing plugin loading...")
                    for step in range(8):
                        self._init_all_plugins_deferred(step)
                        QtWidgets.QApplication.processEvents()
                    self._plugins_loaded = True
        else:
            # No Master_Track → load plugins synchronously (all steps at once)
            for step in range(8):
                self._init_all_plugins_deferred(step)
                QtWidgets.QApplication.processEvents()
            self._plugins_loaded = True

        # ------------------------
        # 7.1 Menu Bar Setup
        # ------------------------
        menubar = QMenuBar(self)
        file_menu = menubar.addMenu(self.messages["menu.file"])

        # ── Reports section ──────────────────────────────────────────────────
        if self.reports_enabled:
            file_menu.addSection(self.messages.get("menu.file.section.reports", "Reports"))
            print_action = QAction(self.messages["menu.file.export"], self)
            print_action.triggered.connect(self._dlg_print_data)
            file_menu.addAction(print_action)

            pdf_export_action = QAction(self.messages.get("menu.file.export_pdf", "Export Reports (.pdf)"), self)
            pdf_export_action.triggered.connect(self._dlg_export_pdf)
            file_menu.addAction(pdf_export_action)

        # ── Medi Track section (only if plugin active) ───────────────────────
        if getattr(self, 'has_medi_track_plugin', False):
            file_menu.addSection(self.messages.get("menu.file.section.medi_track", "Medi Track"))
            medi_pdf_action = QAction(
                self.messages.get("menu.file.export_medi_pdf", "Export Medi Track (.pdf)"), self)
            medi_pdf_action.triggered.connect(self._dlg_export_medi_track_pdf)
            file_menu.addAction(medi_pdf_action)

        # ── Database section ─────────────────────────────────────────────────
        file_menu.addSection(self.messages.get("menu.file.section.database", "Database"))
        save_db_action = QAction(self.messages.get("menu.file.save_database", "Save Database"), self)
        save_db_action.triggered.connect(self._save_database)
        file_menu.addAction(save_db_action)

        # ------------------------
        # 7.2 Tools/Werkzeuge Menu
        # ------------------------
        tools_menu = menubar.addMenu(self.messages["menu.tools"])

        # --- Master_Track group (above everything) ---
        if getattr(self, 'has_master_track', False) and self.master_track:
            self._master_menu = tools_menu.addMenu(
                self.messages.get("menu.tools.master_track", "Master Track"))
            self._mt_manage_action = QAction(
                self.messages.get("master_track.menu.manage", "Manage Users"), self)
            self._mt_manage_action.triggered.connect(self.master_track.show_manage_users)
            self._mt_manage_action.setEnabled(self.master_track.can("master.view_users"))
            self._master_menu.addAction(self._mt_manage_action)

            self._mt_edit_jobs_action = QAction(
                self.messages.get("master_track.menu.edit_jobs", "Edit Jobs…"), self)
            self._mt_edit_jobs_action.triggered.connect(self.master_track.show_edit_jobs)
            self._mt_edit_jobs_action.setEnabled(self.master_track.can("master.manage_job_bundles"))
            self._master_menu.addAction(self._mt_edit_jobs_action)

            self._mt_logs_action = QAction(
                self.messages.get("master_track.menu.logs", "Logs"), self)
            self._mt_logs_action.triggered.connect(self.master_track.show_logs)
            self._mt_logs_action.setEnabled(self.master_track.can("master.view_audit"))
            self._master_menu.addAction(self._mt_logs_action)

            self._mt_open_logs_folder_action = QAction(
                self.messages.get("master_track.menu.open_logs_folder", "Open tech logs"), self)
            self._mt_open_logs_folder_action.triggered.connect(self.master_track.open_logs_folder)
            self._mt_open_logs_folder_action.setEnabled(self.master_track.can("master.view_audit"))
            self._master_menu.addAction(self._mt_open_logs_folder_action)

            self._mt_changepw_action = QAction(
                self.messages.get("master_track.menu.change_pw", "Change Password"), self)
            self._mt_changepw_action.triggered.connect(self.master_track.show_change_password)
            self._mt_changepw_action.setEnabled(self.master_track.is_logged_in)
            self._master_menu.addAction(self._mt_changepw_action)

            self._mt_logout_action = QAction(
                self.messages.get("master_track.menu.logout", "Logout"), self)
            self._mt_logout_action.triggered.connect(self._do_master_logout)
            self._mt_logout_action.setEnabled(self.master_track.is_logged_in)
            self._master_menu.addAction(self._mt_logout_action)

            self._mt_login_action = QAction(
                self.messages.get("master_track.menu.login", "Login"), self)
            self._mt_login_action.triggered.connect(self._do_master_login)
            self._mt_login_action.setEnabled(not self.master_track.is_logged_in)
            self._master_menu.addAction(self._mt_login_action)

            self._master_menu.addSeparator()
            _mt_enabled_now = "master_track" not in self._disabled_plugins
            _toggle_init_label = (
                self.messages.get("master_track.menu.disable", "Disable Master Track")
                if _mt_enabled_now else
                self.messages.get("master_track.menu.enable", "Enable Master Track")
            )
            self._mt_toggle_action = QAction(_toggle_init_label, self)
            self._mt_toggle_action.setEnabled(self.master_track.can("toggle_master_track"))
            self._mt_toggle_action.triggered.connect(self._toggle_master_track)
            self._master_menu.addAction(self._mt_toggle_action)

            tools_menu.addSeparator()

        # --- Middle group: utility / dialog plugins (no checkbox) ---
        if self.network_track_enabled:
            self.network_track_action = QAction(self.messages.get("menu.tools.network_track", "Network Track"), self)
            self.network_track_action.triggered.connect(self._launch_network_track)
            tools_menu.addAction(self.network_track_action)

        if self.has_pdg_plugin and self._is_steroid_track_active():
            self.pdg_cap.add_menu_items(tools_menu)

        if self.embryo_tracker_enabled:
            self.embryo_tracker_action = QAction(self.messages.get("menu.tools.embryo_tracker", "Embryo Track"), self)
            self.embryo_tracker_action.triggered.connect(self._launch_embryo_tracker)
            self.embryo_tracker_action.setEnabled(self._master_can('embryo_track.view'))
            tools_menu.addAction(self.embryo_tracker_action)

        self.op_planner_action = QAction(self.messages.get("menu.tools.op_planner", "OP Scheduler"), self)
        self.op_planner_action.triggered.connect(self._launch_op_planner)
        self.op_planner_action.setEnabled(
            self._op_planner_available() and self._master_can('op_scheduler.view'))
        tools_menu.addAction(self.op_planner_action)

        if getattr(self, 'has_sample_track_plugin', False):
            self.sample_track_action = QAction(
                self.messages.get("menu.tools.sample_track", "Sample Track"), self)
            self.sample_track_action.triggered.connect(self._launch_sample_track)
            self.sample_track_action.setEnabled(self._master_can('sample_track.use'))
            tools_menu.addAction(self.sample_track_action)

        # --- Separator ---
        tools_menu.addSeparator()

        # --- Bottom group: tab-based plugins with enable/disable toggle ---
        # Italic = disabled, normal = enabled.
        if self.reports_enabled:
            self.animal_reports_action = QAction(self.messages.get("menu.tools.animal_reports", "Animal Reports"), self)
            self.animal_reports_action.setCheckable(True)
            self.animal_reports_action.setChecked("animal_reports" not in self._disabled_plugins)
            self.animal_reports_action.toggled.connect(lambda c: self._toggle_plugin_enabled("animal_reports", c))
            self._style_plugin_action("animal_reports", "animal_reports" not in self._disabled_plugins)
            tools_menu.addAction(self.animal_reports_action)

        if self.flow_track_enabled:
            self.flow_track_action = QAction(self.messages.get("menu.tools.flow_track", "Flow Track"), self)
            self.flow_track_action.setCheckable(True)
            self.flow_track_action.setChecked("flow_track" not in self._disabled_plugins)
            self.flow_track_action.toggled.connect(lambda c: self._toggle_plugin_enabled("flow_track", c))
            self._style_plugin_action("flow_track", "flow_track" not in self._disabled_plugins)
            tools_menu.addAction(self.flow_track_action)

        if self.has_projects_plugin:
            self.projects_track_action = QAction(self.messages.get("menu.tools.projects_track", "Project Track"), self)
            self.projects_track_action.setCheckable(True)
            self.projects_track_action.setChecked("projects_track" not in self._disabled_plugins)
            self.projects_track_action.toggled.connect(lambda c: self._toggle_plugin_enabled("projects_track", c))
            self._style_plugin_action("projects_track", "projects_track" not in self._disabled_plugins)
            tools_menu.addAction(self.projects_track_action)

        if self.has_heritage_plugin:
            self.heritage_track_action = QAction(self.messages.get("menu.tools.heritage_track", "Heritage Track"), self)
            self.heritage_track_action.setCheckable(True)
            self.heritage_track_action.setChecked("heritage_track" not in self._disabled_plugins)
            self.heritage_track_action.toggled.connect(lambda c: self._toggle_plugin_enabled("heritage_track", c))
            self._style_plugin_action("heritage_track", "heritage_track" not in self._disabled_plugins)
            tools_menu.addAction(self.heritage_track_action)

        if getattr(self, 'has_cage_track_plugin', False):
            self.cage_track_action = QAction(self.messages.get("menu.tools.cage_track", "Cage Track"), self)
            self.cage_track_action.setCheckable(True)
            self.cage_track_action.setChecked("cage_track" not in self._disabled_plugins)
            self.cage_track_action.toggled.connect(lambda c: self._toggle_plugin_enabled("cage_track", c))
            self._style_plugin_action("cage_track", "cage_track" not in self._disabled_plugins)
            tools_menu.addAction(self.cage_track_action)

        if getattr(self, 'has_medi_track_plugin', False):
            self.medi_track_action = QAction(self.messages.get("menu.tools.medi_track", "Medi Track"), self)
            self.medi_track_action.setCheckable(True)
            self.medi_track_action.setChecked("medi_track" not in self._disabled_plugins)
            self.medi_track_action.toggled.connect(lambda c: self._toggle_plugin_enabled("medi_track", c))
            self._style_plugin_action("medi_track", "medi_track" not in self._disabled_plugins)
            tools_menu.addAction(self.medi_track_action)

        if getattr(self, 'has_steroid_track_plugin', False):
            self.steroid_track_action = QAction(self.messages.get("menu.tools.steroid_track", "Steroid Track"), self)
            self.steroid_track_action.setCheckable(True)
            self.steroid_track_action.setChecked("steroid_track" not in self._disabled_plugins)
            self.steroid_track_action.toggled.connect(lambda c: self._toggle_plugin_enabled("steroid_track", c))
            self._style_plugin_action("steroid_track", "steroid_track" not in self._disabled_plugins)
            tools_menu.addAction(self.steroid_track_action)

        self.setMenuBar(menubar)
        self._refresh_role_restricted_tool_states()
        # Add Program > Language Settings submenu
        self._build_language_menu()

        info_menu = menubar.addMenu(self.messages["menu.info"])
        about_action = QAction(self.messages["menu.info.about"], self)
        about_action.triggered.connect(self._dlg_about_programm)
        info_menu.addAction(about_action)

        # ------------------------
        # 7.3 Application State
        # ------------------------
        self.animals: Dict[str, Dict[str, Any]] = {}
        self.archived: Dict[str, Dict[str, Any]] = {}
        self.selected_animals: List[str] = []
        self.phase_filter: str = Phase.ALLE.value
        self.current_figure: Optional[plt.Figure] = None
        self.current_canvas: Optional[FigureCanvas] = None
        self.last_plotted_animals: List[str] = []
        # Stores filled-circle overlays for raw Prog points. In combined mode only
        # display overlays on converted Prog curves.  To properly manage overlays we
        # distinguish between raw overlays (one per animal) and combined overlays (one per
        # animal with PdG→Prog conversion).  See also `self.prog_overlay_names` below.
        self.prog_overlay_dots: List[Artist] = []
        # mapping of each combined overlay dot to its animal name.  This list has the same
        # length and order as `prog_overlay_dots`.  It is used in `_apply_mode` to
        # determine which overlays to display.
        self.prog_overlay_names: List[str] = []
        # raw Prog overlay dots (not shown) are stored separately to avoid index mismatches
        # when toggling combined overlays.  These are never toggled visible.
        self.prog_overlay_raw_dots: List[Artist] = []
        # Stores hollow-circle overlays for PdG-to-Prog-only points.
        self.pdg_hollow_dots: List[Artist] = []

        # NOTE: Do not reset the central widget here. It is created once in
        # _init_ui_components(). Replacing it here would delete the old QWidget
        # and invalidate self.central_widget.
        # ————————————————————————————————

        # Pre-create Samenspender "Spermawerte" radios so they exist when the UI is built
        # (visible only on the ♂ tab)
        self.rb_sperm_on  = QRadioButton(self.messages.get('label.on', 'On'))
        self.rb_sperm_off = QRadioButton(self.messages.get('label.off', 'Off'))
        sperm_group = QButtonGroup(self)
        sperm_group.setExclusive(True)
        sperm_group.addButton(self.rb_sperm_on,  0)
        sperm_group.addButton(self.rb_sperm_off, 1)
        self.rb_sperm_on.setChecked(True)
        # connect toggle: show/hide sperm lines
        self.rb_sperm_on.toggled.connect(
            lambda checked: (
                [ln.set_linestyle(ln._orig_linestyle if checked else 'None')
                     for ln in getattr(self, 'sperm_lines', [])],
                self.current_canvas.draw_idle() if self.current_canvas else None
            )
        )

        # 7.5 Build the rest of the UI, including radio groups and checkboxes
        self._build_ui()

        # ------------------------
        # 7.6 Connect Controls
        # ------------------------
        # Signal connections for mode checkboxes and line style toggles are now
        # handled in _build_main_content section 7.14.4
        
        # ------------------------
        # 7.6.1 Checkbox Toggles: Show/Hide Prog, Weight & Events
        # ------------------------
        self.chk_prog.toggled.connect(self._on_prog_checkbox_toggled)
        self.chk_weight.toggled.connect(
            lambda checked: (
                [ln.set_visible(checked) for ln in self.weight_lines],
                [band.set_visible(checked) for band in getattr(self, 'weight_ref_bands', [])],
                self.current_canvas.draw_idle() if self.current_canvas else None,
                self._update_toggle_controls()
            )
        )
        self.chk_events.toggled.connect(
            lambda checked: (
                [ln.set_visible(checked) for ln in self.ev_lines],
                [tx.set_visible(checked) for tx in self.ev_texts],
                self.current_canvas.draw_idle() if self.current_canvas else None
            )
        )

        # Per-role Events checkboxes share the same toggle handler
        for chk in (self.chk_events_offspring, self.chk_events_breeding, self.chk_events_experimental):
            chk.toggled.connect(
                lambda checked: (
                    [ln.set_visible(checked) for ln in self.ev_lines],
                    [tx.set_visible(checked) for tx in self.ev_texts],
                    self.current_canvas.draw_idle() if self.current_canvas else None
                )
            )

        # ------------------------
        # 7.7 Restore Persisted Settings (e.g. last mode, linestyles, etc.)
        # ------------------------
        self._load_persistence()

        # Re-discover species now that animals are loaded, then rebuild tabs.
        # (Species discovery at plugin-init time always sees an empty animals dict.)
        if getattr(self, 'has_projects_plugin', False) and self.projects_plugin is not None:
            self.projects_plugin._discover_species()
            self.projects_plugin._rebuild_species_tabs()

        # ------------------------
        # 7.8 Back-fill Data for All Loaded Animals
        # ------------------------
        for rec in list(self.animals.values()) + list(self.archived.values()):
            rec.setdefault('gewicht', [])
            rec.setdefault('ref_weight', 450)
            rec.setdefault('max_geburten', DEFAULT_MAX_GEBURTEN)  # neu
        QTimer.singleShot(0, self._plot_selected)
        if getattr(self, 'has_master_track', False):
            QTimer.singleShot(0, self._apply_startup_master_state)


    def _apply_startup_master_state(self) -> None:
        """Enforce Master_Track permissions and update UI after initial startup login.

        Called once via QTimer.singleShot(0, …) so all UI elements (buttons,
        menus, status bar) are guaranteed to exist before we touch them.
        """
        try:
            mt = getattr(self, 'master_track', None)
            if not mt:
                return
            self._refresh_master_menu_states()
            self._update_master_status_bar()
            self._apply_master_button_states()
            if mt.is_logged_in:
                session = mt.load_session()
                self._restore_session_ui(session)
            ntw = getattr(self, 'network_track_window', None)
            if ntw:
                ntw.refresh_master_name()
        except Exception as e:
            import logging as _log
            _log.error(f"_apply_startup_master_state failed: {e}", exc_info=True)


    # ------------------------
    # 7.8.x Helper: ensure defaults for newly seen names (used by all imports)
    # ------------------------
    def _ensure_defaults_for_new(
        self,
        name: str,
        *,
        base_name: str = "",
        species: str = "",
        birth_date: str = "",
    ):
        """
        Create a minimal default record if 'name' is not yet present.
        New animals start with rolle=Unbekannt and empty lists.
        """
        # If the animal doesn't exist in self.animals dict, create it
        if name not in self.animals:
            normalized_birth = normalize_birth_date(birth_date, required=False)
            visible_name = base_name or animal_base_name(name)
            self.animals[name] = {
                "ipid": name,
                "name": visible_name,
                "_base_name": visible_name,
                "display_name": visible_name,
                "rolle": Role.UNKNOWN.value,
                "events": [],
                "daten": [],
                "pdg": [],
                "gewicht": [],
                "species": self._normalize_species_value(species),
                "birth_date": normalized_birth,
                "chip_nr": "",
                "origin": "",
                "special_status": "",
                "in_experiment": False,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            
        return self.animals[name]

    @staticmethod
    def _apply_identity_fields_to_record(
        record: Dict[str, Any],
        animal_key: str,
        base_name: str,
        species: str,
        birth_date: str,
    ) -> None:
        visible_name = animal_base_name(base_name) or animal_base_name(animal_key)
        record['ipid'] = animal_key
        record['name'] = visible_name
        record['_base_name'] = visible_name
        record['display_name'] = visible_name
        record['species'] = species
        record['birth_date'] = birth_date

    @staticmethod
    def _replace_exact_animal_reference(value: Any, old_key: str, new_key: str) -> Any:
        return replace_exact_animal_reference(value, old_key, new_key)

    @staticmethod
    def _backfill_reference_display_names(value: Any, animal_key: str, base_name: str) -> None:
        backfill_reference_display_names(value, animal_key, base_name)

    def _rewrite_animal_reference_file(self, path: Path, old_key: str, new_key: str, base_name: str) -> None:
        rewrite_animal_reference_file(path, old_key, new_key, base_name)

    def _rewrite_animal_references_after_identity_change(
        self,
        old_key: str,
        new_key: str,
        base_name: str,
    ) -> None:
        if not old_key or old_key == new_key:
            return
        self.animals = self._replace_exact_animal_reference(self.animals, old_key, new_key)
        self.archived = self._replace_exact_animal_reference(self.archived, old_key, new_key)
        self._backfill_reference_display_names(self.animals, new_key, base_name)
        self._backfill_reference_display_names(self.archived, new_key, base_name)

        base = Path(__file__).resolve().parent
        for rel_path in (
            "Plugins/Medi_Track/medi_history.json",
            "Plugins/Cage__Track/cage.json",
            "Plugins/Heritage_Track/heritage_animals.json",
            "Plugins/Animal_Reports/animal_report_data.json",
            "Plugins/Flow_Track/flowtrack_daten.json",
            "Plugins/Flow_Track/flowtrack_config.json",
            "Plugins/Projects_Track/projects_history.json",
            "Plugins/Projects_Track/project_data.json",
            "Plugins/Sample_Track/organs.json",
            "Plugins/Sample_Track/other.json",
            "Plugins/Surgery_Planner/Surgery_Planner.schedule.json",
            "Plugins/Surgery_Planner/Surgery_Pre_Planner.schedule.json",
            "Plugins/PdG_converter/data/models.json",
        ):
            self._rewrite_animal_reference_file(base / rel_path, old_key, new_key, base_name)
        move_medi_document_folder(base, old_key, new_key)

    def _name_species_conflict(
        self,
        new_name: str,
        new_species: str,
        birth_date: str = "",
        *,
        exclude_key: Optional[str] = None,
    ) -> bool:
        """Return True if the complete animal identity is already taken."""
        return identity_conflict(
            new_name,
            new_species,
            birth_date,
            self.animals,
            getattr(self, 'archived', {}),
            exclude_key=exclude_key,
        )

    def _resolve_animal_key(self, base_name: str, species: str, birth_date: str) -> str:
        """Return the dict key to use for a new animal."""
        return animal_identity_key(base_name, species, birth_date)

    def _normalize_identity_birth_for_save(self, value: str, *, required: bool) -> Optional[str]:
        try:
            return normalize_birth_date(value, required=required)
        except ValueError as exc:
            self._show_message_raw(
                self.messages.get("error.title", "Error"),
                str(exc),
                "error",
            )
            return None

    def _validate_identity_species_for_save(self, species: str) -> bool:
        if species:
            return True
        self._show_message_raw(
            self.messages.get("error.title", "Error"),
            self.messages.get(
                "error.new_animal.species_required",
                "Species is required for animal identity.",
            ),
            "error",
        )
        return False

    def _validate_existing_identity_for_save(
        self,
        animal_key: str,
        base_name: str,
        species: str,
        birth_date: str,
    ) -> bool:
        try:
            target_key = self._resolve_animal_key(base_name, species, birth_date)
        except ValueError as exc:
            self._show_message_raw(
                self.messages.get("error.title", "Error"),
                str(exc),
                "error",
            )
            return False
        if target_key == animal_key:
            return True
        if not self._master_can('core.edit_animal_identity'):
            self._show_permission_denied()
            return False
        return True

    @staticmethod
    def _import_row_text(row: Any, candidates: Tuple[str, ...]) -> str:
        for column in candidates:
            try:
                if column not in row:
                    continue
                value = row[column]
            except Exception:
                continue
            text = "" if value is None else str(value).strip()
            if text and text.casefold() not in {"none", "null", "nan", "nat"}:
                return text
        return ""

    def _resolve_import_animal_key(self, row: Any, *, create_missing: bool = True) -> Optional[str]:
        raw_name = self._import_row_text(row, ("Name",))
        if not raw_name:
            return None

        if raw_name in self.animals:
            return raw_name

        species = self._normalize_species_value(
            self._import_row_text(row, ("Species", "species", "Spezies", "Art"))
        )
        birth_raw = self._import_row_text(
            row,
            (
                "Birth Date",
                "Birth date",
                "Birthdate",
                "birth_date",
                "Geburtsdatum",
                "Geburtsdatum (DD.MM.YYYY)",
            ),
        )

        parts = split_animal_identity_key(raw_name)
        base_name = raw_name
        if parts is not None:
            base_name = parts[0]
            species = species or parts[1]
            birth_raw = birth_raw or parts[2]

        birth = ""
        if birth_raw:
            try:
                birth = normalize_birth_date(birth_raw, required=True)
            except ValueError as exc:
                logging.warning(f"Import row skipped for {raw_name}: {exc}")
                return None

        if species and birth:
            try:
                normalized_key = animal_identity_key(base_name, species, birth)
            except ValueError as exc:
                logging.warning(f"Import row skipped for {raw_name}: {exc}")
                return None
            if normalized_key in self.animals:
                return normalized_key

        candidates = []
        wanted_name = animal_base_name(base_name).casefold()
        wanted_species = self._normalize_species_value(species).casefold()
        for key, rec in self.animals.items():
            rec_name, rec_species, rec_birth = record_identity_tuple(key, rec)
            if rec_name != wanted_name:
                continue
            if wanted_species and rec_species != wanted_species:
                continue
            if birth and rec_birth != birth:
                continue
            candidates.append(key)

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            logging.warning(
                "Ambiguous import row for animal %s; add Species and Birth Date.",
                raw_name,
            )
            return None

        if create_missing and species and birth:
            new_key = animal_identity_key(base_name, species, birth)
            self._ensure_defaults_for_new(
                new_key,
                base_name=base_name,
                species=species,
                birth_date=birth,
            )
            return new_key

        if create_missing:
            prompted = self._prompt_identity_for_import(base_name)
            if prompted is not None:
                species, birth = prompted
                new_key = animal_identity_key(base_name, species, birth)
                self._ensure_defaults_for_new(
                    new_key,
                    base_name=base_name,
                    species=species,
                    birth_date=birth,
                )
                return new_key

        logging.warning(
            "Import row skipped for %s: animal not found and identity columns missing.",
            raw_name,
        )
        return None

    def _reset_import_identity_prompt_cache(self) -> None:
        self._import_identity_prompt_cache = {}

    def _prompt_identity_for_import(self, base_name: str) -> Optional[Tuple[str, str]]:
        cache = getattr(self, '_import_identity_prompt_cache', None)
        if not isinstance(cache, dict):
            cache = {}
            self._import_identity_prompt_cache = cache
        cache_key = animal_base_name(base_name).casefold()
        if cache_key in cache:
            return cache[cache_key]

        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get(
            "dialog.import_identity.title",
            "Animal identity required",
        ))
        layout = QVBoxLayout(dlg)
        form = QFormLayout()

        name_le = QLineEdit(animal_base_name(base_name))
        name_le.setReadOnly(True)
        form.addRow(self.messages.get("dialog.field.name", "Name:"), name_le)

        species_cb = QComboBox()
        placeholder = self.messages.get("dialog.species.placeholder", "(Please select)")
        species_cb.addItem(placeholder, "")
        for species in self._load_species_options():
            species_cb.addItem(species, species)
        form.addRow(self.messages.get("dialog.field.species", "Species:"), species_cb)

        birth_le = QLineEdit()
        birth_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        form.addRow(self.messages.get("dialog.field.birth_date", "Birth date:"), birth_le)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        while True:
            if dlg.exec() != QDialog.DialogCode.Accepted:
                cache[cache_key] = None
                return None
            species = self._species_from_combo(species_cb)
            try:
                birth = normalize_birth_date(birth_le.text(), required=True)
                animal_identity_key(base_name, species, birth)
            except ValueError as exc:
                self._show_message_raw(
                    self.messages.get("error.title", "Error"),
                    str(exc),
                    "error",
                )
                continue
            cache[cache_key] = (species, birth)
            return cache[cache_key]


    def _display_name(self, key: str) -> str:
        """Return the user-visible name for an animal key.

        Full identity keys are stored internally, while the sidebar can
        keep showing the short animal name.
        """
        rec = self.animals.get(key)
        if isinstance(rec, dict):
            return animal_base_name(key, rec)
        rec = getattr(self, 'archived', {}).get(key)
        if isinstance(rec, dict):
            return animal_base_name(key, rec)
        return animal_base_name(key)

    # ------------------------
    # 7.9 Read JSON Data
    #   Load and parse the JSON configuration file, returning a dict.
    # ------------------------
    def _read_json(self) -> Dict[str, Any]:
        """Read persistence data from JSON file with size validation and corruption recovery."""
        # Default empty database structure
        default_data = {
            'version': SCHEMA_VERSION,
            'animals': {}, 
            'archived_animals': {}, 
            'settings': {
                'language': 'en',
                'urine_scale': 1.0,
                'show_prog': True,
                'show_weight': True,
                'show_events': True
            }
        }
        
        if not os.path.exists(DATEN_DATEI):
            return default_data
            
        if os.path.getsize(DATEN_DATEI) > 10 * 1024 * 1024:  # 10 MB limit
            logging.error(f"Data file {DATEN_DATEI} is too large (>10MB)")
            self._show_message("error.read_json.too_large")
            return default_data
            
        try:
            with open(DATEN_DATEI, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Validate basic structure
            if not isinstance(data, dict):
                raise ValueError("Root element must be a JSON object")
                
            # Ensure all required top-level keys exist
            if 'animals' not in data:
                data['animals'] = {}
            if 'archived_animals' not in data:
                # Handle legacy 'archived' key
                if 'archived' in data:
                    data['archived_animals'] = data['archived']
                    logging.info("Migrated legacy 'archived' key to 'archived_animals'")
                else:
                    data['archived_animals'] = {}
            if 'settings' not in data:
                data['settings'] = default_data['settings']
            if 'version' not in data:
                data['version'] = SCHEMA_VERSION
                
            return data
                
        except (json.JSONDecodeError, IOError, ValueError) as e:
            # Corruption detected - preserve the broken file and start fresh
            logging.error(f"Failed to read {DATEN_DATEI}: {e}")
            corrupt_path = f"{DATEN_DATEI}.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            try:
                import shutil
                shutil.copy2(DATEN_DATEI, corrupt_path)
                logging.warning(f"Corrupted database preserved as {corrupt_path}")
                # Show user-friendly warning (will be shown by _load_persistence)
            except Exception as copy_error:
                logging.error(f"Could not preserve corrupt file: {copy_error}")
            
            return default_data

    # ------------------------
    # 7.10 Write JSON Data
    #   Serialize and save the provided configuration data to disk.
    # ------------------------
    def _write_json(
        self,
        data: Dict[str, Any],
        audit_after_save: bool = True,
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Write persistence data (animals/archived_animals) safely to JSON file with atomic write."""
        self._save_trace(
            "write_json.enter",
            audit_after_save=audit_after_save,
            animal_count=len(data.get('animals', {})) if isinstance(data, dict) else None,
            archived_count=len(data.get('archived_animals', {})) if isinstance(data, dict) else None,
        )
        # Check if running in read-only mode
        if self.read_only_mode:
            self._save_trace("write_json.read_only_skip")
            logger.warning("Attempted to write data in READ-ONLY mode - operation skipped")
            return None

        try:
            self._save_trace("write_json.read_before_snapshot.before")
            before_snapshot = self._read_json()
            self._save_trace("write_json.read_before_snapshot.after")
        except Exception:
            before_snapshot = {}
            self._save_trace("write_json.read_before_snapshot.exception")
        
        # Build output structure with schema version
        out = {
            'version': SCHEMA_VERSION,
            'animals': {},
            'archived_animals': {},
            'settings': data.get('settings', {})
        }

        # Serialize animals and archived_animals
        for section in ('animals', 'archived_animals'):
            source_data = data.get(section, {})
            # Handle legacy 'archived' key for backward compatibility during transition
            if section == 'archived_animals' and not source_data:
                source_data = data.get('archived', {})
            
            for name, rec in source_data.items():
                rec_copy = rec.copy()
                rec_copy['rolle'] = canonical_role_value(rec_copy.get('rolle'), default=Role.UNKNOWN.value)
                visible_name = animal_base_name(name, rec_copy)
                rec_copy['ipid'] = name
                rec_copy['name'] = visible_name
                rec_copy['_base_name'] = visible_name
                rec_copy['display_name'] = visible_name
                parts = split_animal_identity_key(name)
                if parts is not None:
                    rec_copy['species'] = self._normalize_species_value(rec_copy.get('species')) or parts[1]
                    rec_copy['birth_date'] = (
                        normalize_birth_date(rec_copy.get('birth_date'), required=False)
                        or normalize_birth_date(parts[2], required=False)
                    )

                # --- progesterone (blood) ---
                rec_copy['daten'] = []
                for r in rec.get('daten', []):
                    d = r.get('datum'); v = r.get('wert')
                    if isinstance(d, datetime):
                        d = d.isoformat()
                    elif not isinstance(d, str):
                        logging.warning(f"Invalid date type {type(d)} for daten in {name}, skipping")
                        continue
                    entry = {'datum': d, 'wert': v}
                    probe = r.get('probennummer')
                    if probe is not None:
                        probe_text = str(probe).strip()
                        if probe_text:
                            entry['probennummer'] = probe_text
                    rec_copy['daten'].append(entry)

                # --- weights ---
                rec_copy['gewicht'] = []
                for r in rec.get('gewicht', []):
                    d = r.get('datum'); v = r.get('wert')
                    if isinstance(d, datetime):
                        d = d.isoformat()
                    elif not isinstance(d, str):
                        logging.warning(f"Invalid date type {type(d)} for weight in {name}, skipping")
                        continue
                    rec_copy['gewicht'].append({'datum': d, 'wert': v})

                # --- sperm measurements ---
                rec_copy['sperm'] = []
                for s in rec.get('sperm', []):
                    d = s.get('datum')
                    if isinstance(d, datetime):
                        d = d.isoformat()
                    elif not isinstance(d, str):
                        logging.warning(f"Invalid date type {type(d)} for sperm in {name}, skipping")
                        continue
                    rec_copy['sperm'].append({
                        'datum':       d,
                        'motility':    s.get('motility'),
                        'progressive': s.get('progressive'),
                        'count':       s.get('count'),
                    })

                # --- PdG series + formula ---
                rec_copy['pdg'] = []
                for r in rec.get('pdg', []):
                    d = r.get('datum'); v = r.get('wert')
                    if isinstance(d, datetime):
                        d = d.isoformat()
                    elif not isinstance(d, str):
                        logging.warning(f"Invalid date type {type(d)} for pdg in {name}, skipping")
                        continue
                    entry = {'datum': d, 'wert': v}
                    probe = r.get('probennummer')
                    if probe is not None:
                        probe_text = str(probe).strip()
                        if probe_text:
                            entry['probennummer'] = probe_text
                    rec_copy['pdg'].append(entry)

                # --- unified events (typ + datum) ---
                # Events are the canonical source of truth for all event data
                rec_copy['events'] = []
                for ev in rec.get('events', []):
                    if 'datum' in ev and 'typ' in ev:
                        d = ev['datum']
                        if isinstance(d, datetime):
                            d = d.isoformat()
                        # Normalize event type to canonical identifier
                        typ = ev['typ'].lower().strip()
                        canonical_typ = LEGACY_EVENT_MAP.get(typ, typ)
                        rec_copy['events'].append({'typ': canonical_typ, 'datum': d})
                    else:
                        logging.warning(f"Invalid event in {name}, skipping: {ev}")

                # --- legacy arrays (op, pgf, embryo) for backward compatibility ---
                # These are maintained for existing data but not created for new animals
                rec_copy['op'] = []
                for d in rec.get('op', []):
                    if isinstance(d, datetime):
                        rec_copy['op'].append(d.isoformat())
                    elif isinstance(d, str):
                        rec_copy['op'].append(d)
                
                rec_copy['pgf'] = []
                for d in rec.get('pgf', []):
                    if isinstance(d, datetime):
                        rec_copy['pgf'].append(d.isoformat())
                    elif isinstance(d, str):
                        rec_copy['pgf'].append(d)
                
                rec_copy['embryo'] = []
                for d in rec.get('embryo', []):
                    if isinstance(d, datetime):
                        rec_copy['embryo'].append(d.isoformat())
                    elif isinstance(d, str):
                        rec_copy['embryo'].append(d)

                out[section][name] = rec_copy
            self._save_trace(
                "write_json.section_serialized",
                section=section,
                count=len(out.get(section, {})),
            )

        # Atomic write: temp file → fsync → replace
        target_dir = os.path.dirname(DATEN_DATEI) or '.'
        os.makedirs(target_dir, exist_ok=True)
        self._save_trace("write_json.tempfile.before", target_dir=target_dir)
        fd, tmp_path = tempfile.mkstemp(prefix="progtrack_", suffix=".tmp", dir=target_dir)
        self._save_trace("write_json.tempfile.after", tmp_path=tmp_path)
        try:
            self._save_trace("write_json.dump.before", tmp_path=tmp_path)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(out, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            self._save_trace("write_json.dump.after", tmp_path=tmp_path)
            # Atomic replace (works on Windows and Unix)
            self._save_trace("write_json.replace.before", tmp_path=tmp_path, target=DATEN_DATEI)
            os.replace(tmp_path, DATEN_DATEI)
            self._save_trace("write_json.replace.after", target=DATEN_DATEI)
            logging.info(f"Successfully saved data to {DATEN_DATEI}")
            if audit_after_save:
                self._save_trace("write_json.audit_immediate.before")
                self._audit_data_snapshot_diff_when_safe(before_snapshot, out)
                self._save_trace("write_json.audit_immediate.after")
            else:
                self._save_trace("write_json.return_audit_snapshots")
                return before_snapshot, out
        except Exception as e:
            self._save_trace("write_json.primary.exception", error=e)
            # Clean up temp file on error
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                    self._save_trace("write_json.temp_cleanup.after", tmp_path=tmp_path)
            except Exception:
                self._save_trace("write_json.temp_cleanup.exception", tmp_path=tmp_path)
                pass
            # Try fallback save to user home directory
            fallback_path = os.path.expanduser(f"~/progtrack_daten_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
            try:
                self._save_trace("write_json.fallback.before", fallback_path=fallback_path)
                logging.warning(f"Primary save failed: {e}. Attempting fallback to {fallback_path}")
                with open(fallback_path, 'w', encoding='utf-8') as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                self._show_message("warning.write_json.fallback", target=DATEN_DATEI, fallback_path=fallback_path)
                logging.info(f"Fallback save successful to {fallback_path}")
                self._save_trace("write_json.fallback.after", fallback_path=fallback_path)
            except Exception as fallback_error:
                self._save_trace("write_json.fallback.exception", error=fallback_error)
                self._show_message("error.write_json.fallback_failure", error=fallback_error)
                logging.error(f"Failed to save to fallback {fallback_path}: {fallback_error}")
                raise  # Re-raise original error
        self._save_trace("write_json.exit")
        return None

    # ------------------------
    # 7.11 Show Message Dialog
    #     Display a message box with the specified title, text, and icon.
    # ------------------------
    def _show_message_raw(
        self, title: str, message: str, msg_type: str = "warning",
        buttons: Optional[QMessageBox.StandardButton] = None
    ) -> int:
        """Display a message box with specified title, message, and custom icon."""
        msg = QMessageBox(self)
        msg.setWindowTitle(title)
        msg.setText(message)
        msg.setIcon(QMessageBox.Icon.NoIcon)
        # core uses keys like "warning", "info", "question", "error", "deletion"
        # Map "info" to "information" to match icon filenames.
        file_type = {"info": "information"}.get(msg_type, msg_type)
        icon_path = ICON_DIR / f"{file_type}.png"
        pix = QPixmap(str(icon_path))
        if not pix.isNull():
            msg.setIconPixmap(pix)
        else:
            logging.warning(f"Custom icon '{icon_path}' not found, using default icon")
            default_icons = {
                "warning":     QMessageBox.Icon.Warning,
                "information": QMessageBox.Icon.Information,
                "question":    QMessageBox.Icon.Question,
                "error":       QMessageBox.Icon.Critical,
                "deletion":    QMessageBox.Icon.Critical
            }
            # also treat "info" as Information
            default_icons["info"] = QMessageBox.Icon.Information
            msg.setIcon(default_icons.get(msg_type, QMessageBox.Icon.NoIcon))
        if buttons:
            msg.setStandardButtons(buttons)
            yes_text = self.messages.get("button.yes", "Yes")
            no_text = self.messages.get("button.no", "No")
            yes_btn = msg.button(QMessageBox.StandardButton.Yes)
            no_btn = msg.button(QMessageBox.StandardButton.No)
            if yes_btn is not None:
                yes_btn.setText(yes_text)
            if no_btn is not None:
                no_btn.setText(no_text)
            msg.setDefaultButton(QMessageBox.StandardButton.No)
        msg.setMinimumSize(msg.sizeHint())
        return msg.exec()

    def _show_message(self, key: str, *args, **params) -> int:
        """Localized messages with backward-compatible positional usage.

        New style:
            self._show_message("error.print.no_selection")
            self._show_message("error.write_json.json_error", error=e)

        Legacy style (still supported):
            self._show_message(title, message, "error")
        """
        # Legacy positional form: (title, message, type)
        if args:
            title = key
            message = str(args[0]) if len(args) > 0 else ""
            msg_type = str(args[1]) if len(args) > 1 else "warning"
            return self._show_message_raw(title, message, msg_type)

        # New key-based localized form
        # Extract optional QMessageBox buttons (not used in text formatting)
        buttons = params.pop("buttons", None)
        kind, _ = key.split(".", 1)
        title = self.messages.get(f"title.{kind}", "")
        try:
            text = self.messages.get(key, "").format(**params)
        except (KeyError, IndexError, ValueError) as e:
            # If formatting fails, show a simple message with the raw params
            logging.warning(f"Message formatting failed for key '{key}': {e}")
            text = self.messages.get(key, "") + "\n" + ", ".join(f"{k}={v}" for k, v in params.items())
        return self._show_message_raw(title, text, kind, buttons=buttons)

    def _show_read_only_warning(self) -> None:
        """Show warning that the application is running in read-only mode."""
        title = self.messages.get("title.warning", "Warning")
        message = self.messages.get(
            "warning.read_only_mode",
            "ProgTrack is running in READ-ONLY mode.\n\n"
            "Another instance of ProgTrack is currently editing the data files.\n"
            "You can view all data, but any changes you make will NOT be saved.\n\n"
            "To edit data, please close the other instance first."
        )
        self._show_message_raw(title, message, "warning")
        
        # Update window title to indicate read-only mode
        current_title = self.windowTitle()
        if "READ-ONLY" not in current_title:
            self.setWindowTitle(f"{current_title} [READ-ONLY]")
    
    def _start_read_only_lock_timer(self) -> None:
        if getattr(self, "lock_retry_timer", None) is None:
            self.lock_retry_timer = QTimer(self)
            self.lock_retry_timer.setInterval(30000)
            self.lock_retry_timer.timeout.connect(self._check_lock_reacquire)
        if not self.lock_retry_timer.isActive():
            self.lock_retry_timer.start()

    def _check_lock_reacquire(self) -> None:
        if not self.read_only_mode:
            if getattr(self, "lock_retry_timer", None) is not None:
                self.lock_retry_timer.stop()
                self.lock_retry_timer = None
            return
        handle = try_acquire_lock(LOCK_FILE)
        if handle is None:
            return
        self.lock_handle = handle
        self.read_only_mode = False
        logger.info("Data lock acquired; switching from READ-ONLY to full mode")
        if getattr(self, "lock_retry_timer", None) is not None:
            self.lock_retry_timer.stop()
            self.lock_retry_timer = None
        current_title = self.windowTitle()
        if "READ-ONLY" in current_title:
            new_title = current_title.replace(" [READ-ONLY]", "").replace("[READ-ONLY]", "")
            self.setWindowTitle(new_title.strip())
        try:
            self.statusBar().showMessage(
                self.messages.get(
                    "app.read_only_unlocked",
                    "Data files unlocked - write access restored",
                ),
                5000,
            )
        except Exception:
            pass

    # ------------------------------------------------------------
    # Standard dialog/layout helpers (single source of truth)
    # ------------------------------------------------------------
    def _new_std_dialog(self, title: str) -> tuple[QDialog, QVBoxLayout, QFormLayout]:
        """
        Create a standard animal dialog shell:
          - modal QDialog with uniform title
          - VBox layout + standardized QFormLayout
          - uniform width from UI_STD_DIALOG_WIDTH
        Returns: (dialog, vbox_layout, form_layout)
        """
        dlg = QDialog(self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle(title)
        v = QVBoxLayout(dlg)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # apply standard width (hard minimum + resize using constants)
        dlg.setMinimumWidth(UI_STD_DIALOG_WIDTH)
        try:
            dlg.adjustSize()
            dlg.resize(max(UI_STD_DIALOG_WIDTH, dlg.sizeHint().width()),
                       dlg.sizeHint().height())
        except Exception:
            pass
        # enforce uniform minimum width for all common inputs in this dialog
        dlg.setStyleSheet(f"""
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit {{
                min-width: {UI_STD_FIELD_MIN_WIDTH}px;
            }}
        """)
        return dlg, v, form

    def _apply_dialog_width(self, dlg: QDialog, width: Optional[int] = None) -> None:
        """Finalize the dialog width *after* content has been added.
        This ensures edits to UI_STD_DIALOG_WIDTH actually reflect in window size.
        """
        w = int(width) if isinstance(width, int) and width > 0 else UI_STD_DIALOG_WIDTH
        try:
            dlg.setMinimumWidth(w)
            dlg.adjustSize()
            dlg.resize(max(w, dlg.sizeHint().width()), dlg.sizeHint().height())
        except Exception:
            pass


    def _std_widen(self, w: QWidget) -> None:
        """Enforce uniform field width/expansion for inputs."""
        w.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        w.setMinimumWidth(UI_STD_FIELD_MIN_WIDTH)

    @staticmethod
    def _normalize_species_value(value: Any) -> str:
        text = str(value or "").strip()
        if text.lower() in {"", "none", "null"}:
            return ""
        return text

    def _species_list_path(self) -> Path:
        return Path(__file__).resolve().parent / "Plugins" / "Resources" / "Species_List.txt"

    def _load_species_options(self) -> List[str]:
        species_path = self._species_list_path()
        if not species_path.is_file():
            logging.warning(f"Species list file not found: {species_path}")
            return []

        values: List[str] = []
        seen_lower: set[str] = set()
        try:
            with open(species_path, "r", encoding="utf-8") as handle:
                for raw_line in handle:
                    entry = raw_line.strip()
                    if not entry or entry.startswith("#"):
                        continue
                    entry_lower = entry.lower()
                    if entry_lower in seen_lower:
                        continue
                    seen_lower.add(entry_lower)
                    values.append(entry)
        except Exception as exc:
            logging.error(f"Failed to load species list from {species_path}: {exc}")
            return []

        return values

    def _build_name_species_inputs(
        self,
        form: QFormLayout,
        *,
        name_value: str,
        current_species: Any,
        editing: bool,
        name_label_key: str = "dialog.field.name",
    ) -> tuple[QLineEdit, QComboBox, str]:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        can_edit_identity = self._master_can('core.edit_animal_identity')
        display_name_value = animal_base_name(name_value) if editing else (name_value or "")
        name_le = QLineEdit(display_name_value)
        self._std_widen(name_le)
        name_le.setMaximumWidth(UI_STD_FIELD_MIN_WIDTH + 80)
        if editing and not can_edit_identity:
            name_le.setReadOnly(True)
        else:
            name_le.setPlaceholderText(self.messages.get("form.placeholder.name", "Name"))

        species_label = QLabel(self.messages.get("dialog.field.species", "Species:"))

        species_cb = QComboBox()
        self._std_widen(species_cb)
        placeholder = self.messages.get("dialog.species.placeholder", "(Please select)")
        species_cb.addItem(placeholder, "")
        for species in self._load_species_options():
            species_cb.addItem(species, species)

        normalized_species = self._normalize_species_value(current_species)
        if normalized_species and species_cb.findData(normalized_species) < 0:
            species_cb.addItem(normalized_species, normalized_species)

        italic_font = QFont(species_cb.font())
        italic_font.setItalic(True)
        default_font = QFont(species_cb.font())

        for idx in range(1, species_cb.count()):
            species_cb.setItemData(idx, italic_font, Qt.ItemDataRole.FontRole)

        if normalized_species:
            idx = species_cb.findData(normalized_species)
            species_cb.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            species_cb.setCurrentIndex(0)

        def _sync_species_font(index: int) -> None:
            species_cb.setFont(italic_font if index > 0 else default_font)

        species_cb.currentIndexChanged.connect(_sync_species_font)
        _sync_species_font(species_cb.currentIndex())

        if editing and normalized_species and not can_edit_identity:
            species_cb.setEnabled(False)

        row.addWidget(name_le, 0)
        row.addWidget(species_label, 0)
        row.addWidget(species_cb, 1)

        form.addRow(self.messages.get(name_label_key, "Name:"), row)
        return name_le, species_cb, normalized_species

    def _build_id_chip_origin_row(
        self,
        form: QFormLayout,
        rec: dict,
    ) -> tuple:
        """Build a combined ID / Chip Nr. / Origin row.

        Returns (id_le, chip_le, origin_le).
        Chip Nr. is gated by core.edit_animal_core; Origin by core.edit_animal_immutable.
        """
        id_w = QWidget()
        id_layout = QHBoxLayout(id_w)
        id_layout.setContentsMargins(0, 0, 0, 0)
        id_layout.setSpacing(4)

        id_le = QLineEdit(rec.get('id', ''))
        id_le.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        id_le.setMinimumWidth(50)
        id_le.setStyleSheet("min-width: 0;")

        chip_le = QLineEdit(rec.get('chip_nr', ''))
        chip_le.setPlaceholderText(self.messages.get("dialog.field.chip_nr", "Chip Nr.:"))
        chip_le.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        chip_le.setMinimumWidth(50)
        if not self._master_can('core.edit_animal_core'):
            chip_le.setReadOnly(True)
            chip_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            chip_le.setStyleSheet("min-width: 0;")

        origin_le = QLineEdit(rec.get('origin', ''))
        origin_le.setPlaceholderText(self.messages.get("dialog.field.origin", "Origin:"))
        origin_le.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        origin_le.setMinimumWidth(50)
        if not self._master_can('core.edit_animal_immutable'):
            origin_le.setReadOnly(True)
            origin_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            origin_le.setStyleSheet("min-width: 0;")

        id_layout.addWidget(id_le, 2)
        id_layout.addWidget(QLabel(self.messages.get("dialog.field.chip_nr", "Chip Nr.:")), 0)
        id_layout.addWidget(chip_le, 2)
        id_layout.addWidget(QLabel(self.messages.get("dialog.field.origin", "Origin:")), 0)
        id_layout.addWidget(origin_le, 2)

        id_w.setMinimumWidth(360)
        form.addRow(self.messages.get("dialog.field.id", "ID:"), id_w)
        return id_le, chip_le, origin_le

    def _species_from_combo(self, species_cb: QComboBox) -> str:
        selected = species_cb.currentData()
        if isinstance(selected, str):
            return self._normalize_species_value(selected)
        return self._normalize_species_value(species_cb.currentText())

    def _confirm_species_change_once(self, species_cb: QComboBox, previous_species: str, new_species: str) -> bool:
        previous = self._normalize_species_value(previous_species)
        new = self._normalize_species_value(new_species)

        # Existing persisted species is immutable in edit mode.
        if previous or previous == new:
            return True

        # No species selected: reset any in-dialog confirmation state.
        if not new:
            species_cb.setProperty("_confirmed_species_change", "")
            return True

        confirmed = self._normalize_species_value(species_cb.property("_confirmed_species_change"))
        if confirmed == new:
            return True

        reply = self._show_message(
            "question.species_change.confirm",
            species=self._format_species_text(new, rich_text=True),
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            species_cb.setProperty("_confirmed_species_change", new)
            return True

        # Revert unconfirmed selection so a new confirmation is only needed
        # after an actual new user change.
        prev_idx = species_cb.findData(previous)
        species_cb.setCurrentIndex(prev_idx if prev_idx >= 0 else 0)
        species_cb.setProperty("_confirmed_species_change", "")
        return False

    def _format_species_text(self, species: Any, rich_text: bool = False) -> str:
        species_text = self._normalize_species_value(species)
        if not species_text:
            return ""
        if not rich_text:
            return species_text
        return f"<i>{html.escape(species_text)}</i>"

    def _format_id_with_species(
        self,
        animal_data: Optional[Dict[str, Any]] = None,
        messages: Optional[Dict[str, Any]] = None,
        rich_text: bool = False,
        include_chip: bool = False,
    ) -> str:
        data = animal_data if isinstance(animal_data, dict) else {}
        id_text = str(data.get("id", "-") or "-").strip() or "-"
        chip_text = str(data.get("chip_nr", "") or "").strip()
        species_text = self._format_species_text((data or {}).get("species", ""), rich_text=rich_text)

        id_display = html.escape(id_text) if rich_text else id_text
        if include_chip and chip_text:
            chip_display = html.escape(chip_text) if rich_text else chip_text
            id_chip = f"{id_display} / {chip_display}" if id_text != "-" else chip_display
        else:
            id_chip = id_display

        if not species_text:
            return id_chip

        msg_map = messages if isinstance(messages, dict) else self.messages
        template = msg_map.get("report.id_with_species", "{id} {species}")
        if id_text != "-" or (include_chip and chip_text):
            try:
                return template.format(id=id_chip, species=species_text)
            except Exception:
                return f"{id_chip} {species_text}".strip()
        return species_text

    def _format_project_severity(self, rec: Dict[str, Any]) -> str:
        """Return project name with severity appended, e.g. 'ProjectA [SV0]' or 'ProjectA [SV3]'.

        Rules:
        - no severity saved (empty) → return project name only
        - severity saved (including SV0) → 'ProjectName [SV0]' / 'ProjectName [SV3]'
        - no project + severity → full severity label (e.g. 'SV0 - no severity')
        Legacy: stored '0' is normalized to 'SV0'.
        """
        if not isinstance(rec, dict):
            return ''
        project = (rec.get('project') or '').strip()
        severity = (rec.get('severity') or '').strip()
        if severity == '0':
            severity = 'SV0'
        if not severity:
            return project
        if project:
            return f"{project} [{severity}]"
        sev_map = {
            'SV0': self.messages.get('severity.0',   'SV0 - no severity'),
            'SV1': self.messages.get('severity.sv1', 'SV1 - non-recovery'),
            'SV2': self.messages.get('severity.sv2', 'SV2 - mild or very mild'),
            'SV3': self.messages.get('severity.sv3', 'SV3 - moderate'),
            'SV4': self.messages.get('severity.sv4', 'SV4 - severe'),
        }
        return sev_map.get(severity, severity)

    def _format_report_title_subject(
        self,
        animal_name: str,
        animal_data: Optional[Dict[str, Any]] = None,
        messages: Optional[Dict[str, Any]] = None,
        rich_text: bool = False,
    ) -> str:
        name_text = str(animal_name or "").strip()
        data = animal_data if isinstance(animal_data, dict) else self.animals.get(name_text, {})
        name_text = animal_base_name(name_text, data)
        name_display = html.escape(name_text) if rich_text else name_text
        id_species = self._format_id_with_species(data, messages=messages, rich_text=rich_text)
        if not id_species or id_species == "-":
            return name_display

        msg_map = messages if isinstance(messages, dict) else self.messages
        template = msg_map.get("report.title_subject", "{name} ({id_species})")
        try:
            return template.format(name=name_display, id_species=id_species)
        except Exception:
            return f"{name_display} ({id_species})"

    def _set_parent_mode(self, parent_group: QGroupBox, parent_fields: Dict[str, QLineEdit], mode: str) -> None:
        """Apply natural/embryo visibility and labels to a parent group."""
        layout = parent_group.layout()
        if not isinstance(layout, QFormLayout):
            return

        def _field_label(field_key: str) -> Optional[QLabel]:
            field_widget = parent_fields.get(field_key)
            if field_widget is None:
                return None
            lbl = layout.labelForField(field_widget)
            return lbl if isinstance(lbl, QLabel) else None

        def _set_field_visible(field_key: str, visible: bool) -> None:
            field_widget = parent_fields.get(field_key)
            if field_widget is not None:
                field_widget.setVisible(visible)
            lbl = _field_label(field_key)
            if lbl is not None:
                lbl.setVisible(visible)

        for key in ("egg_donor", "sperm_donor"):
            lbl = _field_label(key)
            if lbl is not None and lbl.property("_orig_parent_label") is None:
                lbl.setProperty("_orig_parent_label", lbl.text())

        mode_key = (mode or "natural").strip().lower()

        if mode_key == "natural":
            mother_label = self.messages.get("heritage_track.node.edit.mother", "Mother:")
            father_label = self.messages.get("heritage_track.node.edit.father", "Father:")
            egg_lbl = _field_label("egg_donor")
            sperm_lbl = _field_label("sperm_donor")
            if egg_lbl is not None:
                egg_lbl.setText(mother_label)
            if sperm_lbl is not None:
                sperm_lbl.setText(father_label)

            _set_field_visible("egg_donor", True)
            _set_field_visible("sperm_donor", True)
            _set_field_visible("surrogate_mother", False)
            _set_field_visible("surrogate_father", False)
            return

        # Embryo transfer mode (default): restore original labels and show all fields.
        for key in ("egg_donor", "sperm_donor"):
            lbl = _field_label(key)
            if lbl is None:
                continue
            original = lbl.property("_orig_parent_label")
            if isinstance(original, str) and original:
                lbl.setText(original)

        _set_field_visible("egg_donor", True)
        _set_field_visible("sperm_donor", True)
        _set_field_visible("surrogate_mother", True)
        _set_field_visible("surrogate_father", True)

    def _add_parent_mode_selector(
        self,
        form: QFormLayout,
        parent_group: QGroupBox,
        parent_fields: Dict[str, QLineEdit],
        default_mode: str,
    ) -> QButtonGroup:
        """Checkable 'Parents' group with natural/embryo radio toggle inside."""
        # Create outer checkable group (Address-style toggle)
        outer = QGroupBox(self.messages.get("dialog.offspring.parents", "Parents"))
        outer.setCheckable(True)

        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(4, 2, 4, 4)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)

        # Radio buttons row
        mode_row_widget = QWidget()
        mode_row = QHBoxLayout(mode_row_widget)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(12)

        rb_natural = QRadioButton(self.messages.get("dialog.parents.mode.natural", "Natural mating"))
        rb_embryo = QRadioButton(self.messages.get("dialog.parents.mode.embryo", "Embryo transfer"))

        mode_group = QButtonGroup(mode_row_widget)
        mode_group.setExclusive(True)
        mode_group.addButton(rb_natural, 1)
        mode_group.addButton(rb_embryo, 2)

        mode_row.addWidget(rb_natural)
        mode_row.addWidget(rb_embryo)
        mode_row.addStretch(1)

        content_layout.addWidget(mode_row_widget)

        # Nest parent_group (remove its own title since outer has it)
        parent_group.setTitle("")
        parent_group.setFlat(True)
        content_layout.addWidget(parent_group)

        outer_layout.addWidget(content)

        # Wire radio buttons
        mode_map = {1: "natural", 2: "embryo"}

        def _apply_mode(button_id: int) -> None:
            self._set_parent_mode(parent_group, parent_fields, mode_map.get(button_id, "natural"))

        mode_group.idClicked.connect(_apply_mode)

        # Toggle content visibility and apply current mode when toggled on
        def _on_toggle(checked: bool) -> None:
            content.setVisible(checked)
            if checked:
                _apply_mode(mode_group.checkedId())

        outer.toggled.connect(_on_toggle)

        normalized_default = (default_mode or "hide").strip().lower()
        if normalized_default == "embryo":
            outer.setChecked(True)
            rb_embryo.setChecked(True)
        elif normalized_default == "natural":
            outer.setChecked(True)
            rb_natural.setChecked(True)
        else:
            outer.setChecked(False)
            rb_natural.setChecked(True)

        content.setVisible(outer.isChecked())
        if outer.isChecked():
            _apply_mode(mode_group.checkedId())

        form.addRow(outer)
        return mode_group


    # ------------------------
    # 7.12 Clear Matplotlib Figures
    #     Remove existing Matplotlib figures and canvases from the UI.
    # ------------------------
    def _clear_matplotlib(self) -> None:
        """Clear Matplotlib figure and canvas to free resources."""
        if self.current_figure:
            self.current_figure.clf()
            plt.close(self.current_figure)
            self.current_figure = None
        if self.current_canvas:
            self.current_canvas.setParent(None)
            self.current_canvas.close()
            self.current_canvas.deleteLater()
            self.current_canvas = None

    # ------------------------
    # 7.13 Build Sidebar
    #     Create and configure the sidebar layout for animal selection and controls.
    # ------------------------
    def _category_tab_tooltips(self) -> List[str]:
        return [
            f"{self._get_localized_role(Role.SPENDER.value)} / {self._get_localized_role(Role.AMME.value)}",
            self._get_localized_role(Role.SAMENSP.value),
            self._get_localized_role(Role.OFFSPRING.value),
            self._get_localized_role(Role.PARTNER.value),
            self._get_localized_role(Role.ZUCHTTIER.value),
            self._get_localized_role(Role.EXPERIMENTAL.value),
            self.messages.get("sidebar.category.tooltip.all", "All animals"),
        ]

    def _build_sidebar(self) -> QVBoxLayout:
        """Build the sidebar layout."""
        sidebar = QVBoxLayout()
        # ── Category tabs ─────────────────────────────────────────────────────────
        # Use a QTabBar instead of buttons, with ♀ as the default
        self.category_tab = QTabBar()
        role_tooltips = self._category_tab_tooltips()
        categories = [
            ("♀", role_tooltips[0]),
            ("♂", role_tooltips[1]),
            ("👶", role_tooltips[2]),
            ("🐾", role_tooltips[3]),
            ("⚤", role_tooltips[4]),
            ("💡", role_tooltips[5]),
            (self.messages["sidebar.filter.all"], role_tooltips[6]),
        ]
        for idx, (label, tip) in enumerate(categories):
            self.category_tab.addTab(label)
            self.category_tab.setTabToolTip(idx, tip)
        # default to ♀ (index 0)
        self.category_tab.setCurrentIndex(0)
        self.category_tab.currentChanged.connect(self._on_category_selected)
        sidebar.addWidget(self.category_tab)

        # ── Phase filters (only for ♀) ───────────────────────────────────────────
        self.phase_widget = QWidget()
        phase_layout = QHBoxLayout(self.phase_widget)
        self.phase_buttons = {}
        phase_group = QButtonGroup(self.phase_widget)
        phase_group.setExclusive(True)
        phase_group.buttonClicked.connect(self._on_phase_selected)
        for label, phase_val in [
            (self.messages["sidebar.filter.all"], None),
            (self.messages["sidebar.filter.follicle_phase"], Phase.FOLLIKEL.value),
            (self.messages["sidebar.filter.luteal_phase"],  Phase.LUTEAL.value)
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.phase_val = phase_val
            phase_group.addButton(btn)
            phase_layout.addWidget(btn)
            # Use phase_val as key instead of translated label
            self.phase_buttons[phase_val] = btn
        # default to "Alle Phasen" (None = all phases)
        self.phase_buttons[None].setChecked(True)
        # hidden until ♀ selected; but show now if default tab is ♀
        self.phase_widget.setVisible(False)
        if self.category_tab.currentIndex() == 0 and self._is_steroid_track_active():
            self.phase_widget.setVisible(True)
        sidebar.addWidget(self.phase_widget)

        # Create horizontal container for project tabs + animal list
        content_container = QWidget()
        content_layout = QHBoxLayout(content_container)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)
        
        # Add project tabs if plugin available
        self._projects_sidebar_widget = None
        if self.has_projects_plugin and self.projects_plugin:
            tabs_widget = self.projects_plugin.create_sidebar_tabs(content_container)
            if tabs_widget:
                content_layout.addWidget(tabs_widget)
                self._projects_sidebar_widget = tabs_widget
                if "projects_track" in self._disabled_plugins:
                    tabs_widget.setVisible(False)
        
        # Animal list (narrower to accommodate project tabs)
        self.lst = QListWidget()
        self.lst.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.lst.itemSelectionChanged.connect(self._on_select)
        self.lst.setMinimumWidth(200)
        content_layout.addWidget(self.lst, 1)  # Stretch factor

        sidebar.addWidget(content_container, 1)  # Add to main sidebar with stretch

        self._animal_name_filter = ""
        self.animal_name_filter_edit = QLineEdit()
        self.animal_name_filter_edit.setPlaceholderText(
            self.messages.get("sidebar.animal_name_filter.placeholder", "Filter animals by name or IPID"))
        self.animal_name_filter_edit.setToolTip(
            self.messages.get("sidebar.animal_name_filter.tooltip", "Filter the visible animal list by short name or IPID"))
        self.animal_name_filter_edit.setClearButtonEnabled(True)
        self.animal_name_filter_edit.textChanged.connect(self._on_animal_name_filter_changed)
        sidebar.addWidget(self.animal_name_filter_edit)

        # Sidebar action buttons with dynamic visibility
        self.btn_new = QPushButton(self.messages["button.sidebar.new_animal"])
        self.btn_new.clicked.connect(self._dlg_new_animal)
        sidebar.addWidget(self.btn_new)

        self.btn_edit = QPushButton(self.messages["button.sidebar.edit_animal"])
        # Defer wiring to _on_category_selected to avoid duplicate receivers
        try:
            self.btn_edit.clicked.disconnect()
        except Exception:
            pass
        self.btn_edit.setEnabled(False)
        sidebar.addWidget(self.btn_edit)

        self.btn_edit_animal = QPushButton(self.messages.get(
            "button.sidebar.edit_animal", "\u270f\ufe0f    Edit"))
        self.btn_edit_animal.setEnabled(False)
        self.btn_edit_animal.setVisible(False)
        self.btn_edit_animal.clicked.connect(self._on_edit_animal_from_all_tab)
        sidebar.addWidget(self.btn_edit_animal)

        self.btn_load_blood = QPushButton(self.messages["button.sidebar.load_blood_values"])
        self.btn_load_blood.clicked.connect(self._import_excel)
        sidebar.addWidget(self.btn_load_blood)

        # Load urine values button - only if PdG plugin present
        if self.has_pdg_plugin:
            self.btn_load_urine = QPushButton(self.messages["button.sidebar.load_urine_values"])
            self.btn_load_urine.clicked.connect(self._import_pdg)
            sidebar.addWidget(self.btn_load_urine)

        self.btn_load_weights = QPushButton(self.messages["button.sidebar.load_weights"])
        self.btn_load_weights.clicked.connect(self._import_weights)
        sidebar.addWidget(self.btn_load_weights)

        # Samenspender-only import button (hidden until ♂ tab)
        self.btn_load_sperm = QPushButton(self.messages["button.sidebar.load_sperm_values"])
        self.btn_load_sperm.clicked.connect(self._import_sperm_values)
        self.btn_load_sperm.setVisible(False)
        sidebar.addWidget(self.btn_load_sperm)

        self.btn_archive = QPushButton(self.messages["button.sidebar.archive"])
        self.btn_archive.clicked.connect(self._archive_current)
        sidebar.addWidget(self.btn_archive)
        self.chk_show_archived = QCheckBox(self.messages.get("sidebar.show_archived", "Show Archived"))
        self.chk_show_archived.toggled.connect(self._refresh_list)
        sidebar.addWidget(self.chk_show_archived)
        self.cmb_arch = QComboBox()
        self.cmb_arch.setVisible(False)
        sidebar.addWidget(self.cmb_arch)
        arch_actions = QHBoxLayout(); arch_actions.setContentsMargins(0,0,0,3)
        self.btn_restore = QPushButton(self.messages["button.sidebar.restore"], clicked=self._restore_archived)
        self.btn_delete = QPushButton(self.messages["button.sidebar.delete"], clicked=self._delete_archived)
        arch_actions.addWidget(self.btn_restore)
        arch_actions.addWidget(self.btn_delete)
        sidebar.addLayout(arch_actions)
       
        return sidebar

    def _set_sperm_controls_visible(self, visible: bool) -> None:
        """Show/hide sperm line-style controls without leaving empty form rows."""
        if hasattr(self, 'sperm_label') and self.sperm_label is not None:
            self.sperm_label.setVisible(visible)
        if hasattr(self, 'sperm_widget') and self.sperm_widget is not None:
            self.sperm_widget.setVisible(visible)
        lay_rad = getattr(self, 'lay_rad', None)
        if lay_rad is not None and hasattr(self, 'sperm_label') and self.sperm_label is not None:
            try:
                lay_rad.setRowVisible(self.sperm_label, visible)
            except Exception:
                pass

    def _tab_shows_prog_event_controls(self, idx: int) -> bool:
        """Return whether progesterone/events display controls are applicable for a category tab."""
        # 0=♀, 1=♂, 2=👶, 3=🐾, 4=⚤, 5=💡, 6=All
        # Progesterone-related controls are only relevant for the female tab.
        return idx == 0

    def _tab_shows_events_only(self, idx: int) -> bool:
        """Return whether only events controls (no progesterone) are applicable for a category tab."""
        # 2=👶 Offspring, 4=⚤ Breeding, 5=💡 Experimental
        return idx in (2, 4, 5)

    def _get_current_events_checkbox(self):
        """Return the appropriate Events checkbox for the current tab."""
        current_idx = self.category_tab.currentIndex() if hasattr(self, 'category_tab') and self.category_tab is not None else 0
        # 2=👶 Offspring, 4=⚤ Breeding, 5=💡 Experimental
        if current_idx == 2 and hasattr(self, 'chk_events_offspring'):
            return self.chk_events_offspring
        elif current_idx == 4 and hasattr(self, 'chk_events_breeding'):
            return self.chk_events_breeding
        elif current_idx == 5 and hasattr(self, 'chk_events_experimental'):
            return self.chk_events_experimental
        else:
            return getattr(self, 'chk_events', None)

    def _set_prog_event_plot_controls_visible(self, visible: bool, events_only: bool = False) -> None:
        """Show/hide progesterone/events controls and their line-style rows."""
        has_pdg = bool(getattr(self, 'has_pdg_plugin', False))

        # Progesterone checkbox - only for female tab
        if hasattr(self, 'chk_prog') and self.chk_prog is not None:
            self.chk_prog.setVisible(visible and not events_only)
            if not visible:
                self.chk_prog.blockSignals(True)
                self.chk_prog.setChecked(False)
                self.chk_prog.blockSignals(False)

        # Events checkbox - show appropriate one based on tab
        if hasattr(self, 'chk_events') and self.chk_events is not None:
            self.chk_events.setVisible(visible and not events_only)
            if not visible:
                self.chk_events.blockSignals(True)
                self.chk_events.setChecked(False)
                self.chk_events.blockSignals(False)
                for ln in getattr(self, 'ev_lines', []):
                    ln.set_visible(False)
                for tx in getattr(self, 'ev_texts', []):
                    tx.set_visible(False)

        # Blood/combined/urine are progesterone-specific: hide when events_only
        prog_visible = visible and not events_only

        # Per-role Events checkboxes
        if events_only:
            current_idx = self.category_tab.currentIndex() if hasattr(self, 'category_tab') and self.category_tab is not None else 0
            # 2=👶 Offspring, 4=⚤ Breeding, 5=💡 Experimental
            if hasattr(self, 'chk_events_offspring'):
                self.chk_events_offspring.setVisible(visible and current_idx == 2)
            if hasattr(self, 'chk_events_breeding'):
                self.chk_events_breeding.setVisible(visible and current_idx == 4)
            if hasattr(self, 'chk_events_experimental'):
                self.chk_events_experimental.setVisible(visible and current_idx == 5)
        else:
            # Hide all per-role checkboxes when on female tab
            if hasattr(self, 'chk_events_offspring'):
                self.chk_events_offspring.setVisible(False)
            if hasattr(self, 'chk_events_breeding'):
                self.chk_events_breeding.setVisible(False)
            if hasattr(self, 'chk_events_experimental'):
                self.chk_events_experimental.setVisible(False)

        mode_widget = getattr(self, 'mode_widget', None)
        if mode_widget is not None:
            mode_widget.setVisible(prog_visible and has_pdg)

        if not visible:
            if has_pdg and hasattr(self, 'chk_mode_combined'):
                self.chk_mode_combined.blockSignals(True)
                self.chk_mode_combined.setChecked(False)
                self.chk_mode_combined.blockSignals(False)
            if hasattr(self, 'chk_mode_blood'):
                self.chk_mode_blood.blockSignals(True)
                self.chk_mode_blood.setChecked(False)
                self.chk_mode_blood.blockSignals(False)
            if has_pdg and hasattr(self, 'chk_mode_urin'):
                self.chk_mode_urin.blockSignals(True)
                self.chk_mode_urin.setChecked(False)
                self.chk_mode_urin.blockSignals(False)

        blood_widget = getattr(self, 'blood_widget', None)
        if blood_widget is not None:
            blood_widget.setVisible(prog_visible)
        if hasattr(self, 'blood_label') and self.blood_label is not None:
            self.blood_label.setVisible(prog_visible)

        combined_visible = prog_visible and has_pdg
        combined_widget = getattr(self, 'combined_widget', None)
        if combined_widget is not None:
            combined_widget.setVisible(combined_visible)
        if hasattr(self, 'combined_label') and self.combined_label is not None:
            self.combined_label.setVisible(combined_visible)

        urine_visible = prog_visible and has_pdg
        urine_widget = getattr(self, 'urine_widget', None)
        if urine_widget is not None:
            urine_widget.setVisible(urine_visible)
        if hasattr(self, 'urine_label') and self.urine_label is not None:
            self.urine_label.setVisible(urine_visible)

        lay_rad = getattr(self, 'lay_rad', None)
        if lay_rad is not None:
            try:
                if hasattr(self, 'blood_label') and self.blood_label is not None:
                    lay_rad.setRowVisible(self.blood_label, prog_visible)
                if hasattr(self, 'combined_label') and self.combined_label is not None:
                    lay_rad.setRowVisible(self.combined_label, combined_visible)
                if hasattr(self, 'urine_label') and self.urine_label is not None:
                    lay_rad.setRowVisible(self.urine_label, urine_visible)
            except Exception:
                pass

        if hasattr(self, 'prog_lines'):
            self._apply_mode()
        if self.current_canvas:
            self.current_canvas.draw_idle()

    def _on_category_selected(self, idx: int):
        """Handle category tab selection: show phase filters only for ♀ and refresh list."""
        # only ♀ (tab 0) shows the phase filters
        steroid_active = self._is_steroid_track_active()
        is_female = (idx == 0)
        self.phase_widget.setVisible(is_female and steroid_active)
        # reset phase back to "Alle" whenever you switch tabs
        self.phase_buttons[None].setChecked(True)
        # Always (re)wire the Edit button based on the active tab
        # Be explicit: remove known receivers to avoid stale connections.
        for slot in (getattr(self, "_dlg_edit_animal", None),
                     getattr(self, "_on_edit_in_all_tab", None)):
            if slot:
                try:
                    self.btn_edit.clicked.disconnect(slot)
                except Exception:
                    pass
        # Best-effort: also clear any remaining connections
        try:
            self.btn_edit.clicked.disconnect()
        except Exception:
            pass

        self._apply_sidebar_button_visibility_for_category(idx)

        # Show sperm controls only on Samenspender tab and only while Steroid_track is active.
        # Progesterone/events controls are shown for the ♀ category and events-only for other tabs.
        self._set_prog_event_plot_controls_visible(
            steroid_active and (self._tab_shows_prog_event_controls(idx) or self._tab_shows_events_only(idx)),
            events_only=self._tab_shows_events_only(idx)
        )
        is_sperm_tab = steroid_active and (idx == 1)
        self._set_sperm_controls_visible(is_sperm_tab)
        if hasattr(self, 'box_rad'):
            if is_sperm_tab:
                self.box_rad.setTitle(self.messages.get('line_style.sperm.group', 'Spermawerte'))
            else:
                self.box_rad.setTitle(self.messages['group.line_style.title'])
        self._refresh_list()
        self._apply_master_button_states()

    def _update_category_tab_visibility(self) -> None:
        """Hide category tabs that currently have no animals; keep All tab visible."""
        if not hasattr(self, 'category_tab') or self.category_tab is None:
            return

        counts = {
            0: 0,  # ♀ (Spenderin + Amme)
            1: 0,  # ♂ (Samenspender)
            2: 0,  # 👶 (Offspring)
            3: 0,  # 🐾 (Partner)
            4: 0,  # ⚤ (Zuchttier)
            5: 0,  # 💡 (Versuchstier)
        }

        for rec in self.animals.values():
            role = rec.get('rolle')
            if role in (Role.SPENDER.value, Role.AMME.value):
                counts[0] += 1
            elif role == Role.SAMENSP.value:
                counts[1] += 1
            elif role == Role.OFFSPRING.value:
                counts[2] += 1
            elif role == Role.PARTNER.value:
                counts[3] += 1
            elif role == Role.ZUCHTTIER.value:
                counts[4] += 1
            elif role == Role.EXPERIMENTAL.value:
                counts[5] += 1

        for tab_idx in range(6):
            self.category_tab.setTabVisible(tab_idx, counts.get(tab_idx, 0) > 0)
        self.category_tab.setTabVisible(6, True)

        current_idx = self.category_tab.currentIndex()
        if current_idx < 6 and counts.get(current_idx, 0) == 0:
            for tab_idx in range(self.category_tab.count()):
                if self.category_tab.isTabVisible(tab_idx):
                    self.category_tab.setCurrentIndex(tab_idx)
                    break

    def _on_phase_selected(self, button):
        """Handle phase button selection when ♀ is active."""
        # figure out which category tab is active
        idx = self.category_tab.currentIndex()
        steroid_active = self._is_steroid_track_active()
        # adjust line-style group title and sperm radios if Samenspender tab
        if idx == 1 and steroid_active:
            # Samenspender: rename style group and show sperm toggles
            self.box_rad.setTitle(self.messages.get('line_style.sperm.group', 'Spermawerte'))
            self._set_sperm_controls_visible(True)
        else:
            # other tabs: restore default title and hide sperm toggles
            self.box_rad.setTitle(self.messages['group.line_style.title'])
            self._set_sperm_controls_visible(False)
        self._refresh_list()

    # ------------------------------------------------------------
    # New: Edit in "Alle" tab → simple dialog to change only sort
    # ------------------------------------------------------------
    def _on_edit_animal_from_all_tab(self):
        """Open the correct full edit dialog for the selected animal based on its role."""
        if not self.selected_animals:
            return
        name = self.selected_animals[0]
        role = self.animals.get(name, {}).get('rolle', '')
        self._dialog_for_role_value(role)(name)

    def _on_edit_in_all_tab(self):
        """When on the 'Alle' tab, Bearbeiten opens a sort-only dialog."""
        selected = self.selected_animals[:1]
        if not selected:
            return
        self._dlg_change_sort(selected[0])

    # ------------------------------------------------------------
    # New: Minimal dialog to change animal's 'sort' (role/category)
    # ------------------------------------------------------------
    def _dlg_change_sort(self, animal_name: str):
        a = self.animals.get(animal_name, {})
        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get("dialog.select_category", "Select Category"))
        lay = QVBoxLayout(dlg)
        
        # Use localized animal label with name
        lay.addWidget(QLabel(self.messages.get("dialog.animal_label", "Animal: {name}").format(name=animal_name)))

        # Add category selection dropdown with localized options.
        # Use the same role-localization logic as reports: show the
        # translated role label, store the internal role code.
        cmb = QComboBox(dlg)
        role_order = [
            role.get("value", "")
            for role in self._active_animal_role_definitions()
            if role.get("value") and role.get("value") != Role.UNKNOWN.value
        ]
        if not self._is_steroid_track_active():
            role_order = [
                role_code for role_code in role_order
                if not self._is_steroid_role_value(role_code)
            ]

        current_role = a.get("rolle")
        if current_role and current_role not in role_order:
            # Keep currently assigned hidden roles selectable as current value so
            # opening the dialog while Steroid_track is inactive does not force
            # a silent role change on save.
            role_order.insert(0, current_role)
        current_index = 0
        for idx, role_code in enumerate(role_order):
            label = self._get_localized_role(role_code)
            cmb.addItem(label, role_code)
            if role_code == current_role:
                current_index = idx
        cmb.setCurrentIndex(current_index)

        lay.addWidget(cmb)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        lay.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        # finalize width so constants take effect
        self._apply_dialog_width(dlg)


        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Get the selected role code (stored as userData in the combobox)
        selected_role = cmb.currentData()
        if selected_role:
            a["rolle"] = selected_role

        self.animals[animal_name] = a
        self._write_json({"animals": self.animals, "archived": self.archived})
        
        # Jump to the appropriate tab based on rolle
        rolle = a.get("rolle")
        tab_idx = self._category_tab_index_for_role(rolle)
        self.category_tab.setCurrentIndex(tab_idx)
        self._refresh_list()
        self._on_select()


    # ------------------------
    # 7.14 Build Main Content
    #     Assemble the central scrollable area with controls and display widgets.
    # ------------------------
    def _build_main_content(self) -> QScrollArea:
        """Build the main content area."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.scroll = scroll

        self.detail_widget = QWidget()
        self.dlay = QVBoxLayout(self.detail_widget)

        # +---------------------------------------------------------------+
        # | 7.14.1 Visibility Controls Group                             |
        # +---------------------------------------------------------------+
        box_chk = QGroupBox(self.messages["group.visibility.title"])
        self.box_chk = box_chk
        lay_chk = QVBoxLayout(box_chk)
        lay_chk.setContentsMargins(8, 6, 8, 6)
        lay_chk.setSpacing(4)
        # ------------------------
        # 7.14.1.1 Row: Progesterone, Weight & Events visibility toggles
        # ------------------------
        h_chk = QHBoxLayout()
        h_chk.setContentsMargins(0, 0, 0, 0)
        h_chk.setSpacing(8)
        
        # Progesterone checkbox (color picker removed - use Settings → Style)
        self.chk_prog = QCheckBox(self.messages["checkbox.progesterone"])
        self.chk_prog.setChecked(True)
        h_chk.addWidget(self.chk_prog)
        
        # Weight checkbox (color picker removed - use Settings → Style)
        self.chk_weight = QCheckBox(self.messages["checkbox.weight"])
        self.chk_weight.setChecked(True)
        h_chk.addWidget(self.chk_weight)
        
        # Events checkbox (for female tab - used as default/fallback)
        self.chk_events = QCheckBox(self.messages["checkbox.events"])
        self.chk_events.setChecked(True)
        h_chk.addWidget(self.chk_events)

        # Per-role Events checkboxes (for tabs that need events but not progesterone)
        self.chk_events_offspring = QCheckBox(self.messages["checkbox.events"])
        self.chk_events_offspring.setChecked(True)
        self.chk_events_breeding = QCheckBox(self.messages["checkbox.events"])
        self.chk_events_breeding.setChecked(True)
        self.chk_events_experimental = QCheckBox(self.messages["checkbox.events"])
        self.chk_events_experimental.setChecked(True)
        # Add to layout but hide by default (will be shown for appropriate tabs)
        h_chk.addWidget(self.chk_events_offspring)
        h_chk.addWidget(self.chk_events_breeding)
        h_chk.addWidget(self.chk_events_experimental)
        self.chk_events_offspring.setVisible(False)
        self.chk_events_breeding.setVisible(False)
        self.chk_events_experimental.setVisible(False)

        h_chk.addStretch()
        lay_chk.addLayout(h_chk)
        
        # ------------------------
        # 7.14.1.2 Mode Selection Checkboxes - only when PdG plugin installed
        # ------------------------
        self.mode_widget = None
        if self.has_pdg_plugin:
            self.mode_widget = QWidget()
            h_mode = QHBoxLayout(self.mode_widget)
            h_mode.setContentsMargins(0, 0, 0, 0)
            h_mode.setSpacing(8)
            arrow = QLabel("\u21B3")  #     | 1.1.1 Arrow indicator showing data flow direction
            arrow.setFont(QFont("", 14))
            arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
            arrow.setStyleSheet("padding: 0px;")
            h_mode.addWidget(arrow)

            # Mode checkboxes (replacing radio buttons for independent selection)
            # Combined checkbox only relevant when PdG plugin present (shows converted PdG+Blood)
            self.chk_mode_combined = QCheckBox(self.messages.get("mode.combined", "Combined"))
            h_mode.addWidget(self.chk_mode_combined)
            
            self.chk_mode_blood    = QCheckBox(self.messages.get("mode.blood", "Blood (Pgr)"))
            h_mode.addWidget(self.chk_mode_blood)
            
            # PdG checkbox - conditional on plugin presence
            self.chk_mode_urin = QCheckBox(self.messages.get("mode.urine", "Urine (PdG)"))
            h_mode.addWidget(self.chk_mode_urin)
            h_mode.addStretch(1)
            
            self.chk_mode_combined.setChecked(True)
            lay_chk.addWidget(self.mode_widget)
        else:
            # No plugin: create hidden Blood checkbox, keep it checked
            self.chk_mode_blood = QCheckBox(self.messages.get("mode.blood", "Blood (Pgr)"))
            self.chk_mode_blood.setChecked(True)
            self.chk_mode_blood.setVisible(False)

        # ------------------------
        # 7.14.2 Line Style Controls Group
        # ------------------------
        box_rad = QGroupBox(self.messages["group.line_style.title"])
        self.box_rad = box_rad  
        lay_rad = QFormLayout()
        self.lay_rad = lay_rad
        lay_rad.setContentsMargins(8, 6, 8, 6)
        lay_rad.setHorizontalSpacing(10)
        lay_rad.setVerticalSpacing(2)
        box_rad.setLayout(lay_rad)

        def _mk_toggle_row(rb_on: QRadioButton, rb_off: QRadioButton) -> QWidget:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_layout.addWidget(rb_on)
            row_layout.addWidget(rb_off)
            row_layout.addStretch(1)
            return row_widget

        # ------------------------
        # 7.14.2.1 Combined (Blood + Urine) Toggle Controls
        # Only relevant when PdG plugin present
        # ------------------------
        self.combined_widget = None
        if self.has_pdg_plugin:
            combined_group = QButtonGroup(self)
            combined_group.setExclusive(True)
            self.rb_combined_on  = QRadioButton(self.messages.get("label.on", "On"))
            self.rb_combined_off = QRadioButton(self.messages.get("label.off", "Off"))
            combined_group.addButton(self.rb_combined_on,  0)
            combined_group.addButton(self.rb_combined_off, 1)
            self.rb_combined_on.setChecked(True)
            self.combined_label = QLabel(self.messages.get("line_style.combined.label", "Blood + Urine"))
            self.combined_widget = _mk_toggle_row(self.rb_combined_on, self.rb_combined_off)
            lay_rad.addRow(self.combined_label, self.combined_widget)

        # ------------------------
        # 7.14.2.2 Blood (Pgr) Toggle Controls
        # ------------------------
        blood_group = QButtonGroup(self)
        blood_group.setExclusive(True)
        self.rb_blood_on  = QRadioButton(self.messages.get("label.on", "On"))
        self.rb_blood_off = QRadioButton(self.messages.get("label.off", "Off"))
        blood_group.addButton(self.rb_blood_on,  0)
        blood_group.addButton(self.rb_blood_off, 1)
        self.rb_blood_on.setChecked(True)
        self.blood_label = QLabel(self.messages.get("line_style.blood.label", "Blood (Pgr)"))
        self.blood_widget = _mk_toggle_row(self.rb_blood_on, self.rb_blood_off)
        lay_rad.addRow(self.blood_label, self.blood_widget)

        # ------------------------
        # 7.14.2.3 Urine (PdG) Toggle Controls - conditional on plugin
        # ------------------------
        self.urine_widget = None
        if self.has_pdg_plugin:
            urine_group = QButtonGroup(self)
            urine_group.setExclusive(True)
            self.rb_urine_on = QRadioButton(self.messages.get("label.on", "On"))
            self.rb_urine_off = QRadioButton(self.messages.get("label.off", "Off"))
            urine_group.addButton(self.rb_urine_on, 0)
            urine_group.addButton(self.rb_urine_off, 1)
            self.rb_urine_on.setChecked(True)
            self.urine_label = QLabel(self.messages.get("line_style.urine.label", "Urine (PdG)"))
            self.urine_widget = _mk_toggle_row(self.rb_urine_on, self.rb_urine_off)
            lay_rad.addRow(self.urine_label, self.urine_widget)

        # ------------------------
        # 7.14.2.3 Sperm Toggle Controls (Spermawerte)
        # ------------------------
        # create a standalone QLabel so it lines up with the radios
        self.sperm_label = QLabel(self.messages.get('line_style.sperm.label', 'Spermawerte'))
        self.sperm_label.setVisible(False)

        # pack the two QRadioButtons into a QWidget for baseline alignment
        h_sperm = QHBoxLayout()
        h_sperm.setContentsMargins(0, 0, 0, 0)
        h_sperm.setSpacing(5)
        h_sperm.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h_sperm.addWidget(self.rb_sperm_on)
        h_sperm.addWidget(self.rb_sperm_off)
        h_sperm.addStretch(1)

        self.sperm_widget = QWidget()
        self.sperm_widget.setLayout(h_sperm)
        self.sperm_widget.setVisible(False)

        # now add them as one row in the form layout
        lay_rad.addRow(self.sperm_label, self.sperm_widget)
        self._set_sperm_controls_visible(False)

        # ------------------------
        # 7.14.2.2 Weight Toggle Controls
        # ------------------------
        weight_group = QButtonGroup(self)
        weight_group.setExclusive(True)
        self.rb_weight_on  = QRadioButton(self.messages["label.on"])
        self.rb_weight_off = QRadioButton(self.messages["label.off"])
        weight_group.addButton(self.rb_weight_on,  0)
        weight_group.addButton(self.rb_weight_off, 1)
        self.rb_weight_on.setChecked(True)
        self.weight_widget = _mk_toggle_row(self.rb_weight_on, self.rb_weight_off)
        # Store label reference for language switching
        self.weight_label = QLabel(self.messages["line_style.weight.label"])
        lay_rad.addRow(self.weight_label, self.weight_widget)

        # ------------------------
        # 7.14.3 Layout: Place control groups side by side
        # ------------------------
        ctrl_hbox = QHBoxLayout()
        ctrl_hbox.setContentsMargins(0, 0, 0, 0)
        ctrl_hbox.setSpacing(12)
        ctrl_hbox.addWidget(box_chk, 0, Qt.AlignmentFlag.AlignTop)
        ctrl_hbox.addWidget(box_rad, 0, Qt.AlignmentFlag.AlignTop)
        ctrl_hbox.addStretch(1)
        self.dlay.addLayout(ctrl_hbox)

        # ------------------------
        # 7.14.4 Connect Signals to Slots
        # ------------------------
        # Mode checkboxes - handle mutex logic and apply mode
        if self.has_pdg_plugin:
            self.chk_mode_combined.toggled.connect(self._on_mode_checkbox_toggled)
        self.chk_mode_blood.toggled.connect(self._on_mode_checkbox_toggled)
        if self.has_pdg_plugin:
            self.chk_mode_urin.toggled.connect(self._on_mode_checkbox_toggled)
        
        # Main progesterone checkbox controls sub-checkboxes
        self.chk_prog.toggled.connect(self._on_prog_checkbox_toggled)
        
        # Line style toggles trigger mode application
        if self.has_pdg_plugin:
            self.rb_combined_on.toggled.connect(self._apply_mode)
            self.rb_urine_on.toggled.connect(self._apply_mode)
        self.rb_blood_on.toggled.connect(self._apply_mode)
        
        # Weight line style toggle
        self.rb_weight_on.toggled.connect(self._toggle_weight_linestyle)

        scroll.setWidget(self.detail_widget)
        return scroll
    
    # ------------------------
    # 7.14.2 Build Reports Tab
    #     Create the animal reports interface with monthly view and rich text editing.
    # ------------------------
    def _build_reports_tab(self) -> QWidget:
        """Build the Reports tab for animal monthly reports."""
        # Create main container with stacked widget for splash/content
        reports_widget = QWidget()
        reports_main_layout = QVBoxLayout(reports_widget)
        reports_main_layout.setContentsMargins(0, 0, 0, 0)
        
        # Create stacked widget to hold splash and content
        from PyQt6.QtWidgets import QStackedWidget
        self.reports_stack = QStackedWidget()
        reports_main_layout.addWidget(self.reports_stack)
        
        # Splash widget (index 0)
        self.reports_splash_widget = QWidget()
        splash_layout = QVBoxLayout(self.reports_splash_widget)
        splash_layout.addStretch(1)
        
        # Add disclaimer/footer text above splash image
        disclaimer_label = QLabel(
            self.messages.get("footer.rights", "ProgTrack").format(year=datetime.now().year)
        )
        disclaimer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splash_layout.addWidget(disclaimer_label, alignment=Qt.AlignmentFlag.AlignCenter)
        splash_layout.addSpacing(20)
        
        img_label = QLabel()
        pix_path = Path("icons/Splash.png")
        if pix_path.exists():
            pix = QPixmap(str(pix_path))
            pix = pix.scaled(800, 800, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            img_label.setPixmap(pix)
        img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splash_layout.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)
        splash_layout.addStretch(1)
        self.reports_stack.addWidget(self.reports_splash_widget)
        
        # Content widget (index 1)
        self.reports_content_widget = QWidget()
        reports_layout = QVBoxLayout(self.reports_content_widget)
        reports_layout.setContentsMargins(10, 10, 10, 10)
        
        # Header section with animal info
        self.report_header_group = QGroupBox(self.messages.get("reports.header.title", "Animal Information"))
        header_layout = QFormLayout(self.report_header_group)
        
        self.report_name_label = QLabel("-")
        self.report_id_label = QLabel("-")
        self.report_id_label.setTextFormat(Qt.TextFormat.RichText)
        self.report_chip_nr_label = QLabel("-")
        self.report_origin_label = QLabel("-")
        self.report_role_label = QLabel("-")
        self.report_status_label = QLabel("-")
        self.report_birth_label = QLabel("-")
        self.report_genotype_label = QLabel("-")
        self.report_project_label = QLabel("-")
        self.report_statistics_label = QLabel("-")
        self.report_statistics_label.setWordWrap(True)
        
        header_layout.addRow(self.messages.get("reports.header.name", "Name:"), self.report_name_label)
        header_layout.addRow(self.messages.get("reports.header.id", "ID:"), self.report_id_label)
        header_layout.addRow(self.messages.get("reports.header.chip_nr", "Chip Nr.:"), self.report_chip_nr_label)
        header_layout.addRow(self.messages.get("reports.header.origin", "Origin:"), self.report_origin_label)
        header_layout.addRow(self.messages.get("reports.header.role", "Role:"), self.report_role_label)
        header_layout.addRow(self.messages.get("reports.header.status", "Status:"), self.report_status_label)
        header_layout.addRow(self.messages.get("reports.header.birth_date", "Birth Date:"), self.report_birth_label)
        header_layout.addRow(self.messages.get("reports.header.genotype", "Genotype:"), self.report_genotype_label)
        header_layout.addRow(self.messages.get("report.header.project", "Project:"), self.report_project_label)
        header_layout.addRow(self.messages.get("reports.header.statistics", "Statistics:"), self.report_statistics_label)
        
        reports_layout.addWidget(self.report_header_group)
        
        # Year/Month selectors with Update button
        selector_layout = QHBoxLayout()
        self.report_year_label = QLabel(self.messages.get("reports.selector.year", "Year:"))
        selector_layout.addWidget(self.report_year_label)
        
        self.report_year_combo = QComboBox()
        self.report_year_combo.setMinimumWidth(100)
        self.report_year_combo.currentTextChanged.connect(self._update_report_table)  # Auto-update
        selector_layout.addWidget(self.report_year_combo)
        
        self.report_month_label = QLabel(self.messages.get("reports.selector.month", "Month:"))
        selector_layout.addWidget(self.report_month_label)
        
        self.report_month_combo = QComboBox()
        self.report_month_combo.setMinimumWidth(150)
        # Populate months
        months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ]
        for i, month in enumerate(months, 1):
            self.report_month_combo.addItem(self.messages.get(f"month.{i}", month), i)
        self.report_month_combo.currentIndexChanged.connect(self._update_report_table)  # Auto-update
        selector_layout.addWidget(self.report_month_combo)
        
        self.btn_export_report_pdf = QPushButton(
            self.messages.get("button.export_pdf", "Export PDF"))
        self.btn_export_report_pdf.setToolTip(
            self.messages.get("tooltip.export_report_pdf_single",
                              "Export PDF for selected animal / month"))
        self.btn_export_report_pdf.clicked.connect(self._export_report_pdf_current_animal)
        selector_layout.addWidget(self.btn_export_report_pdf)

        selector_layout.addStretch()

        reports_layout.addLayout(selector_layout)
        
        # Report table
        self.report_table = QTableWidget()
        self.report_table.setColumnCount(4)
        self.report_table.setHorizontalHeaderLabels([
            self.messages.get("reports.column.date", "Date"),
            self.messages.get("reports.column.daily_data", "Daily Data"),
            self.messages.get("reports.column.scores", "Animal Scores"),
            self.messages.get("reports.column.signatures", "Signatures")
        ])
        
        # Set column widths
        header = self.report_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Date
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Daily Data (wide)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)  # Scores (medium)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Signatures (narrow)
        
        self.report_table.setAlternatingRowColors(True)
        self.report_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)  # Disable default editing
        self.report_table.cellClicked.connect(self._report_cell_clicked)
        self.report_table.cellDoubleClicked.connect(self._report_cell_double_clicked)
        self.report_table.itemChanged.connect(self._report_cell_changed)
        
        # Set HTML delegate for columns 1, 2, 3 to render rich text
        html_delegate = HTMLDelegate(self.report_table)
        self.report_table.setItemDelegateForColumn(1, html_delegate)  # Daily Data
        self.report_table.setItemDelegateForColumn(2, html_delegate)  # Scores
        self.report_table.setItemDelegateForColumn(3, html_delegate)  # Signatures
        
        # Enable word wrap for multi-line cells
        self.report_table.setWordWrap(True)
        self.report_table.verticalHeader().setDefaultSectionSize(60)  # Taller rows for wrapping
        self.report_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        
        # Hide row numbers
        self.report_table.verticalHeader().setVisible(False)
        
        reports_layout.addWidget(self.report_table, 1)
        
        # Initialize report data storage
        self.report_locked_dates = set()  # Set of locked date strings
        self.report_edits = {}  # Dict of date -> {daily_data, scores, signatures}
        self.report_current_animal = None  # Track current animal for reports
        self.report_save_timer = None  # Timer for debounced saving
        
        # Add content widget to stack
        self.reports_stack.addWidget(self.reports_content_widget)
        
        # Show splash by default (no animal selected initially)
        self.reports_stack.setCurrentWidget(self.reports_splash_widget)
        
        return reports_widget
    
    def _show_reports_splash(self) -> None:
        """Show the splash screen in the Reports tab when no animal is selected."""
        if hasattr(self, 'reports_stack') and self.reports_stack is not None:
            self.reports_stack.setCurrentWidget(self.reports_splash_widget)
    
    def _refresh_reports_ui(self) -> None:
        """Refresh all Reports tab UI texts after language change."""
        if not hasattr(self, 'report_header_group'):
            return
        
        # Update header group title
        self.report_header_group.setTitle(self.messages.get("reports.header.title", "Animal Information"))
        
        # Update form row labels
        # Get the form layout
        form_layout = self.report_header_group.layout()
        if isinstance(form_layout, QFormLayout):
            # Update each row label
            row_labels = [
                ("reports.header.name",       "Name:"),
                ("reports.header.id",         "ID:"),
                ("reports.header.chip_nr",    "Chip Nr.:"),
                ("reports.header.origin",     "Origin:"),
                ("reports.header.role",       "Role:"),
                ("reports.header.status",         "Status:"),
                ("reports.header.birth_date",      "Birth Date:"),
                ("reports.header.genotype",   "Genotype:"),
                ("report.header.project",     "Project:"),
                ("reports.header.statistics", "Statistics:"),
            ]
            for row_idx, (key, default) in enumerate(row_labels):
                label_item = form_layout.itemAt(row_idx, QFormLayout.ItemRole.LabelRole)
                if label_item and label_item.widget():
                    label_item.widget().setText(self.messages.get(key, default))
        
        # Update Year/Month selector labels
        if hasattr(self, 'report_year_label'):
            self.report_year_label.setText(self.messages.get("reports.selector.year", "Year:"))
        if hasattr(self, 'report_month_label'):
            self.report_month_label.setText(self.messages.get("reports.selector.month", "Month:"))
        
        # Update table headers
        if hasattr(self, 'report_table'):
            self.report_table.setHorizontalHeaderLabels([
                self.messages.get("reports.column.date", "Date"),
                self.messages.get("reports.column.daily_data", "Daily Data"),
                self.messages.get("reports.column.scores", "Animal Scores"),
                self.messages.get("reports.column.signatures", "Signatures")
            ])
        
        # Update month combo box items
        if hasattr(self, 'report_month_combo'):
            current_month = self.report_month_combo.currentData()
            self.report_month_combo.clear()
            months = [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December"
            ]
            for i, month in enumerate(months, 1):
                self.report_month_combo.addItem(self.messages.get(f"month.{i}", month), i)
            # Restore selection
            if current_month:
                idx = self.report_month_combo.findData(current_month)
                if idx >= 0:
                    self.report_month_combo.setCurrentIndex(idx)

    # ------------------------
    # 7.15 Build UI
    #     Initialize and assemble sidebar and main content areas.
    # ------------------------
    def _build_ui(self) -> None:
        """Build the main user interface."""
        content_layout = QHBoxLayout()
        self.main_layout.addLayout(content_layout)

        sidebar = self._build_sidebar()
        content_layout.addLayout(sidebar)

        # Create tab widget to hold Plots and Reports
        self.main_tabs = QTabWidget()
        self.main_tabs.setTabsClosable(False)
        
        # Build and add Plots tab (existing content)
        scroll = self._build_main_content()
        self.main_tabs.addTab(scroll, self.messages.get("tab.plots", "Plots"))
        
        # Add Reports tab if plugin is enabled (checked earlier in _init_application_state)
        if self.reports_enabled:
            # Add placeholder for Reports tab (lazy loading)
            self.reports_tab = None
            self.reports_tab_placeholder = QWidget()
            self.main_tabs.addTab(self.reports_tab_placeholder, self.messages.get("tab.reports", "Reports"))
        else:
            # Reports tab disabled - no plugin found
            self.reports_tab = None
            logging.info("Reports tab disabled - animal_reports.py plugin not found")
        
        # Add Flow Track tab if plugin is enabled
        if self.flow_track_enabled:
            # Add placeholder for Flow Track tab (lazy loading)
            self.flow_track_tab = None
            self.flow_track_tab_placeholder = QWidget()
            self.main_tabs.addTab(self.flow_track_tab_placeholder, self.messages.get("tab.flow_track", "Flow Track"))
        else:
            # Flow Track tab disabled - no plugin found
            self.flow_track_tab = None
            logging.info("Flow Track tab disabled - flow_track_widget.py plugin not found")

        # Add Heritage Track tab if plugin is enabled
        if getattr(self, 'has_heritage_plugin', False):
            # Add placeholder for Heritage Track tab (lazy loading)
            self.heritage_track_tab = None
            self.heritage_track_tab_placeholder = QWidget()
            self.main_tabs.addTab(self.heritage_track_tab_placeholder, self.messages.get("tab.heritage_track", "Heritage Track"))
        else:
            # Heritage Track tab disabled - no plugin found
            self.heritage_track_tab = None
            logging.info("Heritage Track tab disabled - plugin not found")

        # Add Cage Track tab if plugin is enabled
        if getattr(self, 'has_cage_track_plugin', False):
            self.cage_track_tab = None
            self.cage_track_tab_placeholder = QWidget()
            self.main_tabs.addTab(self.cage_track_tab_placeholder, self.messages.get("tab.cage_track", "Cage Track"))
        else:
            self.cage_track_tab = None
            logging.info("Cage Track tab disabled - plugin not found")

        # Add Medi Track tab if plugin is enabled
        if getattr(self, 'has_medi_track_plugin', False):
            self.medi_track_tab = None
            self.medi_track_tab_placeholder = QWidget()
            self.main_tabs.addTab(self.medi_track_tab_placeholder, self.messages.get("tab.medi_track", "Medi Track"))
        else:
            self.medi_track_tab = None
            logging.info("Medi Track tab disabled - plugin not found")

        # Add Project Track tab if both ProjectsTrack and Master_Track are active
        self._pt_tab_needed = (
            getattr(self, 'has_projects_plugin', False)
            and getattr(self, 'has_master_track', False)
        )
        if self._pt_tab_needed:
            self.project_track_tab = None
            self.project_track_tab_placeholder = QWidget()
            self.main_tabs.addTab(
                self.project_track_tab_placeholder,
                self.messages.get("tab.project_track", "Project Track"))
        else:
            self.project_track_tab = None

        # Connect tab change handler when at least one tab is lazy-loaded.
        if (self.reports_enabled or self.flow_track_enabled
                or getattr(self, 'has_heritage_plugin', False)
                or getattr(self, 'has_cage_track_plugin', False)
                or getattr(self, 'has_medi_track_plugin', False)
                or self._pt_tab_needed):
            self.main_tabs.currentChanged.connect(self._on_tab_changed)

        # Hide tabs for disabled plugins
        for pkey, msg_key, fallback in [
            ("animal_reports", "tab.reports", "Reports"),
            ("flow_track", "tab.flow_track", "Flow Track"),
            ("heritage_track", "tab.heritage_track", "Heritage Track"),
            ("cage_track", "tab.cage_track", "Cage Track"),
            ("medi_track", "tab.medi_track", "Medi Track"),
            ("projects_track", "tab.project_track", "Project Track"),
        ]:
            if pkey in self._disabled_plugins:
                txt = self.messages.get(msg_key, fallback)
                for i in range(self.main_tabs.count()):
                    if self.main_tabs.tabText(i) == txt:
                        self.main_tabs.setTabVisible(i, False)
                        break

        # Set Plots tab as default
        self.main_tabs.setCurrentIndex(0)
        
        content_layout.addWidget(self.main_tabs, stretch=2)

        # Now that both sidebar and main content exist, wire buttons for the active tab
        self._on_category_selected(self.category_tab.currentIndex())
        self._update_category_tab_visibility()
        self._refresh_list()
        self._apply_steroid_track_state()

        # ── Master_Track status bar & shortcut ──
        if getattr(self, 'has_master_track', False) and self.master_track:
            from PyQt6.QtWidgets import QStatusBar
            from PyQt6.QtGui import QShortcut, QKeySequence
            sb = QStatusBar(self)
            self.setStatusBar(sb)
            self._master_status_label = _ClickableLabel()
            self._master_status_label.setStyleSheet(
                "QLabel { padding: 0 8px; font-weight: bold; cursor: pointer; }")
            self._master_status_label.clicked.connect(self._show_master_quick_menu)
            sb.addPermanentWidget(self._master_status_label)
            self._update_master_status_bar()

            # Ctrl+L → login shortcut
            login_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
            login_shortcut.activated.connect(self._do_master_login)

            # Install event filter to reset idle timer on user interaction
            from PyQt6.QtCore import QEvent
            self._master_event_filter = _IdleResetFilter(self.master_track, self)
            self.installEventFilter(self._master_event_filter)

            # Restore session UI if logged in
            if self.master_track.is_logged_in:
                session = self.master_track.load_session()
                self._restore_session_ui(session)

            # Grey out sidebar buttons according to current role
            self._apply_master_button_states()

    # ------------------------
    # 7.15.0 Tab Management
    #     Handle lazy loading of Reports tab.
    # ------------------------
    def _replace_lazy_tab(self, index: int, widget: QWidget, label: str) -> None:
        """Replace a lazy placeholder without exposing intermediate tab states."""
        if index < 0 or index >= self.main_tabs.count():
            return
        tabs = self.main_tabs
        previous_signal_state = tabs.blockSignals(True)
        tabs.setUpdatesEnabled(False)
        self._lazy_tab_replacing = True
        try:
            tabs.removeTab(index)
            tabs.insertTab(index, widget, label)
            tabs.setCurrentIndex(index)
        finally:
            self._lazy_tab_replacing = False
            tabs.setUpdatesEnabled(True)
            tabs.blockSignals(previous_signal_state)
            tabs.update()

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab changes and lazy load Reports/Flow/Heritage tabs if needed."""
        if getattr(self, '_lazy_tab_replacing', False):
            return
        if index < 0:
            return
        # Determine which tab we're switching to
        tab_text = self.main_tabs.tabText(index)
        tab_widget = self.main_tabs.widget(index)

        reports_selected = self.reports_enabled and (
            tab_widget is getattr(self, 'reports_tab', None)
            or tab_widget is getattr(self, 'reports_tab_placeholder', None)
        )
        flow_selected = self.flow_track_enabled and (
            tab_widget is getattr(self, 'flow_track_tab', None)
            or tab_widget is getattr(self, 'flow_track_tab_placeholder', None)
        )
        _herit_tab = getattr(self, 'heritage_track_tab', None)
        _herit_ph = getattr(self, 'heritage_track_tab_placeholder', None)
        heritage_selected = getattr(self, 'has_heritage_plugin', False) and (
            tab_widget is _herit_tab
            or tab_widget is _herit_ph
        )
        cage_track_selected = getattr(self, 'has_cage_track_plugin', False) and (
            tab_widget is getattr(self, 'cage_track_tab', None)
            or tab_widget is getattr(self, 'cage_track_tab_placeholder', None)
        )
        medi_track_selected = getattr(self, 'has_medi_track_plugin', False) and (
            tab_widget is getattr(self, 'medi_track_tab', None)
            or tab_widget is getattr(self, 'medi_track_tab_placeholder', None)
        )
        pt_tab_selected = (
            getattr(self, '_pt_tab_needed', False) and (
                tab_widget is getattr(self, 'project_track_tab', None)
                or tab_widget is getattr(self, 'project_track_tab_placeholder', None)
            )
        )
        plots_selected = (index == 0)
        
        # Check if Reports tab is selected and not yet built
        if reports_selected and self.reports_tab is None:
            logging.info("Lazy loading Reports tab...")
            
            # Build the Reports tab
            self.reports_tab = self._build_reports_tab()
            
            self._replace_lazy_tab(
                index,
                self.reports_tab,
                self.messages.get("tab.reports", "Reports"),
            )
            
            # Update reports if an animal is selected (use last selected animal only)
            if self.selected_animals:
                self._update_reports_for_animal(self.selected_animals[-1])
            else:
                self._update_reports_for_animal(None)
            
            logging.info("Reports tab loaded successfully")
        
        # Check if Flow Track tab is selected and not yet built
        elif flow_selected and self.flow_track_tab is None:
            logging.info("Lazy loading Flow Track tab...")
            
            try:
                # Build the Flow Track tab
                from Plugins.Flow_Track.flow_track_widget import FlowTrackWidget
                self.flow_track_widget = FlowTrackWidget(self, self.messages)
                self.flow_track_tab = self.flow_track_widget.get_widget()
                
                self._replace_lazy_tab(
                    index,
                    self.flow_track_tab,
                    self.messages.get("tab.flow_track", "Flow Track"),
                )
                
                logging.info("Flow Track tab loaded successfully")
            except Exception as e:
                logging.error(f"Failed to load Flow Track tab: {e}", exc_info=True)
                QMessageBox.critical(
                    self,
                    self.messages.get("error.title", "Error"),
                    f"Failed to load Flow Track tab:\n{str(e)}"
                )

        # Check if Heritage Track tab is selected and not yet built
        elif heritage_selected and self.heritage_track_tab is None:
            logging.info("Lazy loading Heritage Track tab...")

            try:
                self.heritage_track_widget = self.heritage_plugin.get_tab_widget()
                self.heritage_track_tab = self.heritage_track_widget

                self._replace_lazy_tab(
                    index,
                    self.heritage_track_tab,
                    self.messages.get("tab.heritage_track", "Heritage Track"),
                )

                if hasattr(self.heritage_track_widget, 'refresh_graph'):
                    self.heritage_track_widget.refresh_graph()

                # Refresh animal list to show heritage-only animals
                self._refresh_list()

                logging.info("Heritage Track tab loaded successfully")
            except Exception as e:
                logging.error(f"Failed to load Heritage Track tab: {e}", exc_info=True)
                QMessageBox.critical(
                    self,
                    self.messages.get("error.title", "Error"),
                    f"Failed to load Heritage Track tab:\n{str(e)}"
                )
        
        # Check if Cage Track tab is selected and not yet built
        elif cage_track_selected and getattr(self, 'cage_track_tab', None) is None:
            logging.info("Lazy loading Cage Track tab...")

            try:
                self.cage_track_widget = self.cage_track_plugin.get_tab_widget()
                self.cage_track_tab = self.cage_track_widget

                self._replace_lazy_tab(
                    index,
                    self.cage_track_tab,
                    self.messages.get("tab.cage_track", "Cage Track"),
                )

                if hasattr(self.cage_track_widget, 'refresh_view'):
                    self.cage_track_widget.refresh_view()

                logging.info("Cage Track tab loaded successfully")
            except Exception as e:
                logging.error(f"Failed to load Cage Track tab: {e}", exc_info=True)
                QMessageBox.critical(
                    self,
                    self.messages.get("error.title", "Error"),
                    f"Failed to load Cage Track tab:\n{str(e)}"
                )

        # Check if Medi Track tab is selected and not yet built
        elif medi_track_selected and getattr(self, 'medi_track_tab', None) is None:
            logging.info("Lazy loading Medi Track tab...")

            try:
                self.medi_track_widget = self.medi_track_plugin.get_tab_widget()
                self.medi_track_tab = self.medi_track_widget

                self._replace_lazy_tab(
                    index,
                    self.medi_track_tab,
                    self.messages.get("tab.medi_track", "Medi Track"),
                )

                # Show currently selected animal if any (filter heritage-only animals)
                if self.selected_animals:
                    medi_animals = [n for n in self.selected_animals if n in self.animals]
                    self.medi_track_plugin.on_animal_selected(medi_animals)

                logging.info("Medi Track tab loaded successfully")
            except Exception as e:
                logging.error(f"Failed to load Medi Track tab: {e}", exc_info=True)
                QMessageBox.critical(
                    self,
                    self.messages.get("error.title", "Error"),
                    f"Failed to load Medi Track tab:\n{str(e)}"
                )

        # Check if Project Track tab is selected and not yet built
        elif pt_tab_selected and self.project_track_tab is None:
            logging.info("Lazy loading Project Track tab...")
            try:
                from Plugins.Projects_Track.project_track_tab import ProjectTrackTab
                self.project_track_widget = ProjectTrackTab(
                    self, self.messages,
                    history_store=self.projects_plugin._history)
                self.project_track_tab = self.project_track_widget
                self._replace_lazy_tab(
                    index,
                    self.project_track_tab,
                    self.messages.get("tab.project_track", "Project Track"),
                )
                if self.projects_plugin.current_project not in (None, 'All'):
                    self.project_track_widget.select_project(
                        self.projects_plugin.current_project)
                logging.info("Project Track tab loaded successfully")
            except Exception as e:
                logging.error(f"Failed to load Project Track tab: {e}", exc_info=True)
                self.project_track_tab = None

        # Reset Medi Track filter whenever we leave the Medi Track tab
        if not medi_track_selected and getattr(self, 'has_medi_track_plugin', False):
            medi_widget = getattr(self, 'medi_track_widget', None)
            if medi_widget is not None and hasattr(medi_widget, 'reset_filter'):
                if medi_widget.active_filter() != 'all':
                    medi_widget.reset_filter()
                    self._refresh_list()

        # Refresh list when switching to Heritage Track (runs regardless of Medi Track reset)
        if heritage_selected and getattr(self, 'heritage_track_tab', None) is not None:
            if hasattr(self, 'heritage_track_widget') and self.heritage_track_widget is not None:
                if hasattr(self.heritage_track_widget, 'refresh_graph'):
                    self.heritage_track_widget.refresh_graph()
            # Disable role tabs (0-5), enable only "All" tab (6), and auto-select it
            if hasattr(self, 'category_tab'):
                # Then disable tabs 0-5 (role tabs)
                for i in range(6):
                    self.category_tab.setTabEnabled(i, False)
                # Keep "All" tab (6) enabled
                self.category_tab.setTabEnabled(6, True)
                self.category_tab.setCurrentIndex(6)
                # Apply stylesheet for disabled tab styling with !important to override Windows defaults
                self.category_tab.setStyleSheet("""
                    QTabBar::tab:disabled {
                        color: grey !important;
                        background-color: #e0e0e0 !important;
                    }
                """)
                # Force complete refresh
                self.category_tab.style().unpolish(self.category_tab)
                self.category_tab.style().polish(self.category_tab)
                self.category_tab.repaint()
            self._refresh_list(force_heritage_visible=True)

        # Refresh content when switching to already-loaded tabs
        elif not medi_track_selected:
            if plots_selected:
                # Switching to Plots tab - refresh plot
                self._plot_selected()
            
            elif reports_selected and self.reports_tab is not None:
                # Switching to Reports tab - refresh reports
                # Filter out heritage-only animals - they don't have reports
                report_animals = [n for n in self.selected_animals if n in self.animals]
                if report_animals:
                    self._update_reports_for_animal(report_animals[-1])
                else:
                    self._update_reports_for_animal(None)
            
            elif flow_selected and self.flow_track_tab is not None:
                # Switching to Flow Track tab - refresh visualization
                if hasattr(self, 'flow_track_widget'):
                    self.flow_track_widget._redraw_canvas()

            elif cage_track_selected and getattr(self, 'cage_track_tab', None) is not None:
                # Switching to an already-loaded Cage Track tab should not
                # rebuild unless Cage Track knows assignments changed.
                cage_plugin = getattr(self, 'cage_track_plugin', None)
                if cage_plugin is not None and hasattr(cage_plugin, 'refresh_on_tab_activated'):
                    cage_plugin.refresh_on_tab_activated()

            elif medi_track_selected and getattr(self, 'medi_track_tab', None) is not None:
                # Switching to Medi Track tab - refresh view for current selection
                if hasattr(self, 'medi_track_plugin') and self.medi_track_plugin is not None:
                    # Filter heritage-only animals - they should stay in Heritage Track only
                    medi_animals = [n for n in self.selected_animals if n in self.animals]
                    self.medi_track_plugin.on_animal_selected(medi_animals)

        # When switching away from Heritage Track, refresh list to hide heritage-only animals
        # (they should only be visible in Heritage Track context)
        _prev_tab_was_heritage = getattr(self, '_prev_tab_was_heritage', False)
        if _prev_tab_was_heritage and not heritage_selected:
            # Unselect ALL animals when leaving Heritage Track (as if empty space was clicked)
            logging.info("Unselecting all animals when leaving Heritage Track")
            self._selected_heritage_only = []
            self.selected_animals = []
            # Update UI to reflect the selection change
            for i in range(self.lst.count()):
                item = self.lst.item(i)
                if item:
                    item.setSelected(False)
            # Re-enable all role tabs (0-6)
            if hasattr(self, 'category_tab'):
                self.category_tab.setStyleSheet("")  # Clear custom styling
                for i in range(7):  # Enable all tabs 0-6
                    self.category_tab.setTabEnabled(i, True)
                # Force style refresh to restore normal appearance
                self.category_tab.style().unpolish(self.category_tab)
                self.category_tab.style().polish(self.category_tab)
                self.category_tab.repaint()
            self._refresh_list()
        self._prev_tab_was_heritage = heritage_selected

    # ------------------------
    # 7.15.1 Reports Tab Methods
    #     Handle report table updates, formatting, and persistence.
    # ------------------------
    def _get_animal_name_from_item(self, item: QListWidgetItem) -> Optional[str]:
        """Extract animal name from a list widget item (handles custom widgets)."""
        user_data = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if isinstance(user_data, str) and user_data in self.animals:
            return user_data

        # Try to get from custom widget
        widget = self.lst.itemWidget(item)
        if widget:
            for child in widget.children():
                if isinstance(child, QLabel):
                    t = child.text()
                    if t and t in self.animals:
                        return t
        
        # Fallback to item.text()
        t = item.text()
        if t and t in self.animals:
            return t
        
        return None
    
    def _get_status_description(self, status: str) -> str:
        """Convert status symbols to human-readable descriptions."""
        status_map = {
            '☉': self.messages.get('status.pregnant', 'Pregnant'),
            '☉?': self.messages.get('status.possibly_pregnant', 'Possibly pregnant'),
            'Oo': self.messages.get('status.has_offspring', 'Has young offspring'),
            '+': self.messages.get('status.sick', 'Sick / In recovery'),
            '!': self.messages.get('status.abnormal', 'Abnormal'),
            DECEASED_STATUS_SYMBOL: self.messages.get('status.deceased', 'Deceased'),
            '': self.messages.get('status.normal', 'Normal')
        }
        
        if DECEASED_STATUS_SYMBOL in status and status != DECEASED_STATUS_SYMBOL:
            genotype = status.replace(DECEASED_STATUS_SYMBOL, '').strip()
            deceased_desc = status_map.get(DECEASED_STATUS_SYMBOL, 'Deceased')
            return f"{genotype} {deceased_desc}".strip()

        # Handle combined statuses (e.g., "☉+")
        if '+' in status and status != '+':
            base_status = status.replace('+', '').strip()
            base_desc = status_map.get(base_status, base_status)
            sick_desc = status_map.get('+', 'Sick')
            return f"{base_desc} + {sick_desc}"
        
        return status_map.get(status, status)
    
    def _load_project_names(self) -> list:
        """Return sorted list of project names from Projects_Track/project_data.json."""
        try:
            import json as _json
            from pathlib import Path as _Path
            p = _Path(__file__).parent / 'Plugins' / 'Projects_Track' / 'project_data.json'
            d = _json.loads(p.read_text(encoding='utf-8'))
            return sorted(d.get('projects', {}).keys())
        except Exception:
            return []

    def _load_project_records_for_visibility(self) -> Dict[str, Dict[str, Any]]:
        try:
            import json as _json
            p = Path(__file__).parent / 'Plugins' / 'Projects_Track' / 'project_data.json'
            if not p.exists():
                return {}
            data = _json.loads(p.read_text(encoding='utf-8'))
            projects = data.get('projects', {}) if isinstance(data, dict) else {}
            return {str(k): v for k, v in projects.items() if isinstance(v, dict)}
        except Exception as exc:
            logging.warning(f"Could not load project visibility records: {exc}")
            return {}

    def _project_visibility_scope(self) -> tuple[bool, set[str]]:
        mt = getattr(self, 'master_track', None)
        mt_disabled = "master_track" in getattr(self, '_disabled_plugins', set())
        if mt is None or mt_disabled:
            return True, set(self._load_project_names())

        username = getattr(mt, 'current_username', None)
        role = getattr(mt, 'current_role', None)
        can_view_all = mt.can("project.view_all") if hasattr(mt, 'can') else False
        if can_view_all or is_unrestricted_project_role(role):
            return True, set(self._load_project_names())

        cache = mt.get_project_visibility_cache() if hasattr(mt, 'get_project_visibility_cache') else {"dirty": True}
        cached_projects = set(cache.get("projects") or [])
        if username and not cache.get("dirty", True):
            return False, cached_projects

        unrestricted, visible = visible_projects_for_user(
            self._load_project_records_for_visibility(),
            username,
            role,
            can_view_all_projects=can_view_all,
        )
        if username and hasattr(mt, 'set_project_visibility_cache'):
            mt.set_project_visibility_cache(sorted(visible), dirty=False)
        return unrestricted, visible

    def _animal_visible_to_current_user(self, animal_data: Dict[str, Any]) -> bool:
        unrestricted, visible_projects = self._project_visibility_scope()
        return animal_visible_by_project_scope(animal_data, unrestricted, visible_projects)

    def _current_animal_name_filter_text(self) -> str:
        line_edit = getattr(self, 'animal_name_filter_edit', None)
        if line_edit is None:
            return getattr(self, '_animal_name_filter', '')
        return line_edit.text()

    def _on_animal_name_filter_changed(self, text: str) -> None:
        self._animal_name_filter = text
        self._refresh_list()

    def _can_configure_animal_roles(self) -> bool:
        mt = getattr(self, "master_track", None)
        if mt is None or "master_track" in getattr(self, "_disabled_plugins", set()):
            return True
        return getattr(mt, "_current_role", "") in {"lord", "master", "manager"}

    def _load_animal_role_definitions(self) -> List[Dict[str, Any]]:
        registry = getattr(self, "animal_role_registry", None)
        return registry.roles() if registry else []

    def _save_animal_role_definitions(self, roles: List[Dict[str, Any]]) -> bool:
        registry = getattr(self, "animal_role_registry", None)
        if registry is None:
            return False
        previous_values = {
            str(role.get("value") or "")
            for role in registry.roles()
            if str(role.get("value") or "")
        }
        roles_for_save = []
        for role in roles:
            if not isinstance(role, dict):
                continue
            role_for_save = dict(role)
            value = canonical_role_value(role_for_save.get("value") or role_for_save.get("role_id") or "")
            if value:
                role_for_save["value"] = value
                role_for_save["role_id"] = canonical_role_value(role_for_save.get("role_id") or value)
                role_for_save["label_key"] = str(role_for_save.get("label_key") or "").strip() or f"role.{value}"
            roles_for_save.append(role_for_save)
        next_values = {
            str(role.get("value") or "")
            for role in roles_for_save
            if isinstance(role, dict) and str(role.get("value") or "")
        }
        deleted_values = previous_values - next_values
        try:
            registry.save_roles(roles_for_save)
            self._save_role_label_overrides(registry.roles())
            if hasattr(self, "category_tab"):
                self._setup_sidebar_texts()
            changed_active = clear_deleted_role_assignments(self.animals, deleted_values)
            changed_archived = clear_deleted_role_assignments(self.archived, deleted_values)
            if changed_active or changed_archived:
                logging.info(
                    "Cleared deleted role assignments: active=%s archived=%s",
                    changed_active,
                    changed_archived,
                )
                self._save_persistence(defer_post_save_work=True)
                self._refresh_list()
            return True
        except Exception as exc:
            logging.error(f"Failed to save animal role setup: {exc}")
            self._show_message_raw(
                self.messages.get("error.title", "Error"),
                self.messages.get(
                    "settings.role_setup.save_failed",
                    "Animal role setup could not be saved.",
                ),
                "error",
            )
            return False

    def _make_custom_animal_role_definition(self, label: str, icon: str, existing_values=None) -> Dict[str, Any]:
        registry = getattr(self, "animal_role_registry", None)
        if registry is None:
            return {
                "role_id": label.casefold().replace(" ", "_"),
                "value": f"custom.{label.casefold().replace(' ', '_')}",
                "label": label,
                "label_key": f"role.custom.{label.casefold().replace(' ', '_')}",
                "icon": icon or "●",
                "order": 1000,
                "active": True,
                "built_in": False,
                "base_editor": "basic",
                "field_preset": "basic",
            }
        return registry.make_custom_role(label, icon, existing_values=existing_values)

    def _make_imported_animal_role_definition(self, original_label: str, source: str = "", existing_values=None) -> Dict[str, Any]:
        registry = getattr(self, "animal_role_registry", None)
        if registry is None:
            return {
                "role_id": str(original_label or "imported_role").strip().replace(" ", "_"),
                "value": f"imported.{str(original_label or 'role').strip().replace(' ', '_')}",
                "label": str(original_label or "Imported role").strip(),
                "label_key": f"role.imported.{str(original_label or 'role').strip().replace(' ', '_')}",
                "icon": "!",
                "order": 1000,
                "active": True,
                "built_in": False,
                "base_editor": "basic",
                "field_preset": "basic",
                "dialog_blocks": {
                    "new": list(REQUIRED_DIALOG_BLOCKS),
                    "edit": list(REQUIRED_DIALOG_BLOCKS),
                },
                "imported": True,
                "review_state": "confirmed",
                "original_label": str(original_label or "").strip(),
                "import_source": str(source or "").strip(),
            }
        return registry.make_imported_role(
            original_label,
            source=source,
            existing_values=existing_values,
        )

    def _active_animal_role_definitions(self) -> List[Dict[str, Any]]:
        roles = self._load_animal_role_definitions()
        return [role for role in roles if role.get("active")]

    def _normalize_role_dialog_blocks(self, blocks) -> List[str]:
        return normalize_block_list(blocks)

    def _role_definition_for_value(self, role_value: str) -> Dict[str, Any]:
        registry = getattr(self, "animal_role_registry", None)
        if registry is None:
            return {}
        return registry.get_by_value(role_value) or {}

    def _role_dialog_blocks(self, role_value: str, mode: str = "edit") -> List[str]:
        registry = getattr(self, "animal_role_registry", None)
        if registry is None:
            return list(REQUIRED_DIALOG_BLOCKS)
        return registry.dialog_blocks_for_value(role_value, mode)

    def _role_block_enabled(self, role_value: str, block_id: str, mode: str = "edit") -> bool:
        if block_id in REQUIRED_DIALOG_BLOCKS:
            return True
        return block_id in self._role_dialog_blocks(role_value, mode)

    def _role_import_capabilities(self, role_value: str) -> Dict[str, bool]:
        return import_capabilities_for_blocks(
            self._role_dialog_blocks(role_value, "edit"),
            steroid_active=self._is_steroid_track_active(),
            has_pdg_plugin=bool(getattr(self, "has_pdg_plugin", False)),
        )

    def _combine_role_import_capabilities(self, role_values: Iterable[str]) -> Dict[str, bool]:
        combined = {"blood": False, "urine": False, "weight": False, "sperm": False}
        for role_value in role_values:
            caps = self._role_import_capabilities(role_value)
            for key, enabled in caps.items():
                combined[key] = combined[key] or enabled
        return combined

    def _role_values_for_category_tab(self, idx: int) -> List[str]:
        if idx == 6:
            return [
                self.animals.get(name, {}).get("rolle") or Role.UNKNOWN.value
                for name in getattr(self, "selected_animals", [])
                if name in self.animals
            ]

        roles = []
        for role in self._active_animal_role_definitions():
            value = role.get("value", "")
            if value and self._category_tab_index_for_role(value) == idx:
                roles.append(value)
        return roles

    def _sidebar_import_capabilities_for_tab(self, idx: int) -> Dict[str, bool]:
        return self._combine_role_import_capabilities(self._role_values_for_category_tab(idx))

    def _apply_sidebar_button_visibility_for_category(self, idx: int) -> None:
        caps = self._sidebar_import_capabilities_for_tab(idx)
        is_all_tab = idx == 6

        for slot in (getattr(self, "_dlg_edit_animal", None),
                     getattr(self, "_on_edit_in_all_tab", None)):
            if slot:
                try:
                    self.btn_edit.clicked.disconnect(slot)
                except Exception:
                    pass

        self.btn_new.setVisible(True)
        self.btn_archive.setVisible(True)
        self.btn_load_blood.setVisible(caps["blood"])
        if self.has_pdg_plugin:
            self.btn_load_urine.setVisible(caps["urine"])
        self.btn_load_weights.setVisible(caps["weight"])
        self.btn_load_sperm.setVisible(caps["sperm"])

        self.btn_edit.setVisible(True)
        if is_all_tab:
            self.btn_edit.setText(self.messages.get(
                "button.sidebar.edit_role", "🫥    Edit Role"))
            self.btn_edit.clicked.connect(self._on_edit_in_all_tab)
            if hasattr(self, 'btn_edit_animal'):
                self.btn_edit_animal.setVisible(True)
                self.btn_edit_animal.setText(self.messages.get(
                    "button.sidebar.edit_animal", "✏️    Edit"))
        else:
            self.btn_edit.setText(self.messages.get(
                "button.sidebar.edit_animal", "✏️    Edit"))
            self.btn_edit.clicked.connect(self._dlg_edit_animal)
            if hasattr(self, 'btn_edit_animal'):
                self.btn_edit_animal.setVisible(False)

    def _role_label_with_icon(self, rolle: str) -> str:
        registry = getattr(self, "animal_role_registry", None)
        if registry:
            return registry.display_for_value(rolle, self.messages)
        return self._get_localized_role(rolle)

    def _build_export_role_groups(self, *, steroid_active: bool, visible_only: bool = False):
        role_order = [
            role.get("value", "")
            for role in self._active_animal_role_definitions()
            if role.get("value") and role.get("value") != Role.UNKNOWN.value
        ]
        if Role.UNKNOWN.value not in role_order:
            role_order.append(Role.UNKNOWN.value)
        role_groups = {role: [] for role in role_order}

        for name, data in sorted(self.animals.items()):
            if visible_only and not self._animal_visible_to_current_user(data):
                continue
            role = canonical_role_value(data.get("rolle"), default=Role.UNKNOWN.value)
            if role == Role.SAMENSP.value and not steroid_active:
                continue
            if role not in role_groups:
                role_groups[role] = []
                role_order.append(role)
            role_groups[role].append(name)

        role_labels = {role: self._role_label_with_icon(role) for role in role_order}
        return role_groups, role_order, role_labels

    def _is_steroid_role_value(self, role_value: str) -> bool:
        role_value = canonical_role_value(role_value, default=Role.UNKNOWN.value)
        return role_value in (Role.SPENDER.value, Role.AMME.value, Role.SAMENSP.value)

    def _dialog_for_role_value(self, role_value: str):
        role_value = canonical_role_value(role_value, default=Role.UNKNOWN.value)
        if role_value in (Role.SPENDER.value, Role.AMME.value):
            return lambda name, read_only=False: self._dlg_female_animal(
                name,
                read_only=read_only,
                default_role=role_value,
            )
        if role_value == Role.SAMENSP.value:
            return self._dlg_samenspender
        if role_value == Role.OFFSPRING.value:
            return self._dlg_offspring
        if role_value == Role.PARTNER.value:
            return self._dlg_partner
        if role_value == Role.ZUCHTTIER.value:
            return self._dlg_zuchttier
        if role_value == Role.EXPERIMENTAL.value:
            return self._dlg_versuchstier
        return lambda name, read_only=False: self._dlg_basic_animal_role(
            name,
            role_value=role_value,
            read_only=read_only,
        )

    def _category_tab_index_for_role(self, role_value: str) -> int:
        role_value = canonical_role_value(role_value, default=Role.UNKNOWN.value)
        if role_value in (Role.SPENDER.value, Role.AMME.value):
            return 0
        if role_value == Role.SAMENSP.value:
            return 1
        if role_value == Role.OFFSPRING.value:
            return 2
        if role_value == Role.PARTNER.value:
            return 3
        if role_value == Role.ZUCHTTIER.value:
            return 4
        if role_value == Role.EXPERIMENTAL.value:
            return 5
        return 6

    def _get_localized_role(self, rolle: str, messages: Optional[Dict[str, str]] = None) -> str:
        """Return a localized, human-readable role name for display."""
        m = messages or self.messages

        if not rolle:
            return m.get("role.unknown", "Unknown")
        rolle = canonical_role_value(rolle, default=Role.UNKNOWN.value)

        registry = getattr(self, "animal_role_registry", None)
        if registry:
            label = registry.label_for_value(rolle, m)
            if label:
                return label

        role_map = {
            Role.SPENDER.value:   m.get("role.egg_cell_donor", m.get("role.spenderin", "Egg cell donor")),
            Role.AMME.value:      m.get("role.surrogate", m.get("role.amme", "Surrogate")),
            Role.SAMENSP.value:   m.get("role.sperm_donor", m.get("role.samenspender", "Sperm donor")),
            Role.OFFSPRING.value: m.get("role.offspring", "Offspring"),
            Role.PARTNER.value:      m.get("role.partner_animal", m.get("role.partnertier", "Partner animal")),
            Role.ZUCHTTIER.value:    m.get("role.breeding_animal", m.get("role.zuchttier", "Breeding animal")),
            Role.EXPERIMENTAL.value: m.get("role.experimental_animal", m.get("role.experimental", "Experimental animal")),
            Role.UNKNOWN.value:      m.get("role.unknown",      "Unknown"),
        }
        return role_map.get(rolle, rolle)
    
    def _load_reference_weights(self, species: str = "") -> List[Tuple[float, float, float]]:
        """Load reference weight data for offspring from Resources folder.

        Looks for a species-specific file first by deriving a prefix from the
        first letter of each word in the species name (e.g. "Callithrix jacchus"
        → "cj" → "Reference _Weight_Infants_cj.txt").  Falls back to the generic
        "Reference _Weight_Infants.txt" if the species-specific file is missing.
        Returns an empty list if no file is found or parsing fails.

        Returns:
            List of tuples (age_weeks, min_weight, max_weight)
            The last entry has age_weeks = float('inf') for adult animals.
        """
        resources_dir = Path(__file__).parent / 'Plugins' / 'Resources'

        def _candidate_paths() -> List[Path]:
            paths: List[Path] = []
            if species and species.strip():
                prefix = "".join(w[0].lower() for w in species.strip().split() if w)
                if prefix:
                    paths.append(resources_dir / f"Reference_Weight_Infants_{prefix}.txt")
            paths.append(resources_dir / 'Reference_Weight_Infants.txt')
            return paths

        ref_file: Optional[Path] = None
        for candidate in _candidate_paths():
            if candidate.exists():
                ref_file = candidate
                break

        if ref_file is None:
            logger.debug(
                f"No reference weight file found for species '{species}'. "
                f"Tried: {[str(p) for p in _candidate_paths()]}")
            return []
        logger.debug(f"Loading reference weights from: {ref_file}")

        try:
            reference_data = []
            with open(ref_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                # Skip header line
                for line in lines[1:]:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split('\t')
                    if len(parts) < 5:
                        continue

                    age_str = parts[0].strip()
                    min_weight_str = parts[2].strip().replace(',', '.')
                    max_weight_str = parts[3].strip().replace(',', '.')

                    try:
                        if age_str.lower() == 'above':
                            age_weeks = float('inf')
                        else:
                            age_weeks = float(age_str)
                        min_weight = float(min_weight_str)
                        max_weight = float(max_weight_str)
                        reference_data.append((age_weeks, min_weight, max_weight))
                    except ValueError:
                        continue

            return reference_data
        except Exception as e:
            logger.warning(f"Failed to load reference weights from {ref_file}: {e}")
            return []
    
    def _export_report_pdf_current_animal(self) -> None:
        """Export a PDF report for the currently selected animal in the selected month/year."""
        from datetime import date as _date
        animal_name = getattr(self, 'report_current_animal', None)
        if not animal_name or animal_name not in self.animals:
            self._show_message("error.print.no_selection")
            return
        year_text = self.report_year_combo.currentText()
        month_idx = self.report_month_combo.currentData()
        try:
            year = int(year_text)
            month = int(month_idx)
        except (ValueError, TypeError):
            return
        last_day = calendar.monthrange(year, month)[1]
        von = _date(year, month, 1)
        bis = _date(year, month, last_day)
        safe_name = ''.join(
            c for c in animal_name if c.isalnum() or c in ('_', '-', ' ')
        ).strip().replace(' ', '_')
        default_filename = f"{safe_name}_{year}_{month:02d}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.messages.get("dialog.save_pdf.title", "Save PDF Report"),
            default_save_path(default_filename),
            "PDF files (*.pdf)",
        )
        if not path:
            return
        if not path.lower().endswith('.pdf'):
            path += '.pdf'
        try:
            self._load_report_data(animal_name)
            self._create_single_pdf_report(
                animal_name, self.animals[animal_name],
                year, month, path, von, bis, self.lang)
            QMessageBox.information(
                self,
                self.messages.get("info.title", "Success"),
                self.messages.get("info.animal_reports_exported", "Animal reports exported to PDF."))
        except Exception as e:
            error_msg = str(e).replace('{', '{{').replace('}', '}}')
            self._show_message("error.pdf_export.failed", error=error_msg)

    def _update_report_table(self) -> None:
        """Update the report table with data for the selected month."""
        # Check if UI is initialized
        if not hasattr(self, 'lst') or not hasattr(self, 'report_table'):
            logging.debug("Report table UI not initialized")
            return
        
        # Block signals to prevent infinite loop during table population
        self.report_table.blockSignals(True)
        
        # Use the report's tracked animal as source of truth. The sidebar
        # selection can be rebuilt by filters/tab changes while Reports stays open.
        animal_name = getattr(self, 'report_current_animal', None)
        if not animal_name:
            selected = self.lst.selectedItems()
            if selected:
                animal_name = self._get_animal_name_from_item(selected[0])
        if not animal_name:
            logging.debug("No animal selected for report")
            self.report_table.setRowCount(0)
            self.report_table.blockSignals(False)
            return
        
        logging.info(f"Updating report table for animal: {animal_name}")
        animal_data = self.animals.get(animal_name)
        if not animal_data:
            logging.warning(f"No data found for animal: {animal_name}")
            self.report_table.setRowCount(0)
            self.report_table.blockSignals(False)
            return
        
        # Update header info
        self.report_name_label.setText(self._display_name(animal_name))
        self.report_id_label.setText(self._format_id_with_species(animal_data, rich_text=True))
        if hasattr(self, 'report_chip_nr_label'):
            self.report_chip_nr_label.setText(animal_data.get('chip_nr', '') or '-')
        if hasattr(self, 'report_origin_label'):
            self.report_origin_label.setText(animal_data.get('origin', '') or '-')
        rolle = animal_data.get('rolle')
        self.report_role_label.setText(self._get_localized_role(rolle) if rolle is not None else '-')
        
        self.report_status_label.setText(
            status_summary_with_death_priority(
                animal_data,
                self.messages,
                projects_track_active=self._is_projects_track_active(),
            )
        )
        
        self.report_birth_label.setText(animal_data.get('birth_date', '-'))
        self.report_genotype_label.setText(animal_data.get('genotype', '-'))
        if hasattr(self, 'report_project_label'):
            self.report_project_label.setText(
                self._format_project_severity(animal_data) or '-')
        
        # Update statistics in header
        stats = self._get_event_statistics(animal_data)
        self.report_statistics_label.setText(stats if stats else '-')
        
        # Get selected year and month
        year = int(self.report_year_combo.currentText()) if self.report_year_combo.currentText() else datetime.now().year
        month = self.report_month_combo.currentData()
        
        # Generate days for the month
        num_days = calendar.monthrange(year, month)[1]
        
        self.report_table.setRowCount(num_days)
        
        for day in range(1, num_days + 1):
            date = datetime(year, month, day).date()
            date_str = date.strftime(DATE_FORMAT)
            
            # Column 0: Date (clickable for lock/unlock)
            date_item = QTableWidgetItem(str(day))
            date_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            date_item.setFlags(date_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Date column not editable
            
            is_locked = date_str in self.report_locked_dates
            if is_locked:
                date_item.setBackground(QColor(200, 255, 200))  # Light green for locked
                date_item.setData(Qt.ItemDataRole.UserRole, 'locked')
            else:
                date_item.setData(Qt.ItemDataRole.UserRole, 'unlocked')
            self.report_table.setItem(day - 1, 0, date_item)
            
            # Column 1: Daily Data (auto-populated if not locked)
            # Since we filter out unlocked dates from report_edits, locked dates will have stored data
            # and unlocked dates will always regenerate from progtrack_daten
            if is_locked and date_str in self.report_edits and 'daily_data' in self.report_edits[date_str]:
                daily_data = self.report_edits[date_str]['daily_data']
            else:
                # Generate fresh data for unlocked dates or locked dates without stored data
                daily_data = self._generate_daily_data(animal_name, animal_data, date, self.messages)
            
            daily_item = QTableWidgetItem(daily_data)
            daily_item.setFlags(daily_item.flags() & ~Qt.ItemFlag.ItemIsEditable)  # Daily data not editable
            if is_locked:
                daily_item.setBackground(QColor(200, 255, 200))
            self.report_table.setItem(day - 1, 1, daily_item)
            
            # Column 2: Scores (manual entry, preserved only for locked dates)
            # Unlocked dates will have empty string since they're filtered from report_edits
            scores = self.report_edits.get(date_str, {}).get('scores', '')
            scores_item = QTableWidgetItem(scores)
            # Disable inline editing - use double-click dialog instead
            scores_item.setFlags(scores_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if is_locked:
                scores_item.setBackground(QColor(200, 255, 200))
            self.report_table.setItem(day - 1, 2, scores_item)
            
            # Column 3: Signatures (manual entry, preserved only for locked dates)
            # Unlocked dates will have empty string since they're filtered from report_edits
            signatures = self.report_edits.get(date_str, {}).get('signatures', '')
            sig_item = QTableWidgetItem(signatures)
            # Disable inline editing - use double-click dialog instead
            sig_item.setFlags(sig_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if is_locked:
                sig_item.setBackground(QColor(200, 255, 200))
            self.report_table.setItem(day - 1, 3, sig_item)
            
            # Set row background color for locked rows (affects entire row including empty space)
            if is_locked:
                for col in range(self.report_table.columnCount()):
                    item = self.report_table.item(day - 1, col)
                    if item:
                        item.setBackground(QColor(200, 255, 200))
        
        # Re-enable signals after table is populated
        self.report_table.blockSignals(False)
    
    def _is_in_recovery_period(self, animal_data: Dict[str, Any], date: datetime.date) -> bool:
        """Check if a specific date falls within an animal's recovery period."""
        role = animal_data.get('rolle')
        
        # For Spenderin and Samenspender: check recovery after OP or sperm samples
        if role in (Role.SPENDER.value, Role.SAMENSP.value):
            recovery_days = animal_data.get('recovery_time', DEFAULT_RECOVERY_TIME)
            
            # Collect all relevant event dates: ops + sperm samples
            op_dates = animal_data.get('op', []) or []
            sperm_dates = [s.get('datum') for s in animal_data.get('sperm', []) if s.get('datum')]
            all_dates = op_dates + sperm_dates
            
            # Check if any event date puts this date in recovery
            for event_date in all_dates:
                if isinstance(event_date, datetime):
                    event_date = event_date.date()
                    # Check if date is within recovery_days after event_date
                    if event_date <= date <= event_date + timedelta(days=recovery_days):
                        return True
        
        # For Amme: check recovery after embryo transfer
        elif role == Role.AMME.value:
            embryo_dates = [ev['datum'] for ev in animal_data.get('events', []) 
                           if ev.get('typ') == 'embryo_transfer' and isinstance(ev.get('datum'), datetime)]
            recovery_days = animal_data.get('recovery_time', DEFAULT_RECOVERY_TIME)
            
            for embryo_date in embryo_dates:
                embryo_date = embryo_date.date()
                # Check if date is within recovery_days after embryo transfer
                if embryo_date <= date <= embryo_date + timedelta(days=recovery_days):
                    return True
        
        return False
    
    def _generate_daily_data(self, animal_name: str, animal_data: Dict[str, Any], date: datetime.date, messages: Dict[str, str] = None) -> str:
        """Generate daily data text for a specific date.
        
        Args:
            animal_name: Name of the animal
            animal_data: Animal data dictionary
            date: Date for which to generate daily data
            messages: Optional localization messages dict. If None, uses self.messages
        """
        # Use provided messages or fall back to self.messages
        if messages is None:
            messages = self.messages
        
        lines = []
        
        # Check if animal is in recovery period on this date
        if self._is_in_recovery_period(animal_data, date):
            status_label = messages.get('daily.status', 'Status')
            recovery_text = messages.get('status.recovery_period', 'Recovery period')
            lines.append(f"{status_label}: {recovery_text}")
        
        # Check if animal was sick on this specific date
        # New approach: Check sick period (start_date to end_date)
        date_is_sick = False
        
        sick_start = animal_data.get('sick_start_date')
        sick_end = animal_data.get('sick_end_date')
        
        logging.debug(f"[SICK CHECK] Checking sick status for {animal_name} on {date}: sick_start={sick_start}, sick_end={sick_end}")
        
        if sick_start:
            try:
                start_dt = datetime.fromisoformat(sick_start.replace('Z', '+00:00')).date()
                logging.debug(f"[SICK CHECK] Parsed sick_start_date: {start_dt}, checking date: {date}")
                
                # Check if date is after or on start date
                if date >= start_dt:
                    if sick_end:
                        # Had a recovery date, check if on or before last sick day (inclusive)
                        end_dt = datetime.fromisoformat(sick_end.replace('Z', '+00:00')).date()
                        date_is_sick = (date <= end_dt)
                        logging.debug(f"[SICK CHECK] Sick with end date {end_dt}: date_is_sick={date_is_sick}")
                    else:
                        # No end date means still sick
                        date_is_sick = True
                        logging.debug("[SICK CHECK] Sick with no end date: date_is_sick=True")
            except Exception as e:
                logging.warning(f"Error parsing sick dates: {e}")
        
        # Fallback: Check old sick_times array for historical data (backward compatibility)
        if not date_is_sick:
            sick_times = animal_data.get('sick_times', [])
            for sick_date in sick_times:
                if isinstance(sick_date, datetime):
                    if sick_date.date() == date:
                        date_is_sick = True
                        break
                elif isinstance(sick_date, str):
                    try:
                        sick_dt = datetime.fromisoformat(sick_date.replace('Z', '+00:00'))
                        if sick_dt.date() == date:
                            date_is_sick = True
                            break
                    except (TypeError, ValueError):
                        logging.debug("Skipping invalid legacy sick date: %r", sick_date)
        
        if date_is_sick:
            health_label = messages.get('daily.health_status', 'Health Status')
            sick_text = messages.get('status.sick', 'Sick')
            sick_line = f"{health_label}: {sick_text}"
            lines.append(sick_line)
            logging.debug(f"[SICK CHECK] Added sick status to output: '{sick_line}'")

        # Check if animal was abnormal on this specific date
        date_is_abnormal = False
        abnormal_start = animal_data.get('abnormal_start_date')
        abnormal_end   = animal_data.get('abnormal_end_date')
        if abnormal_start:
            try:
                ab_start_dt = datetime.fromisoformat(abnormal_start.replace('Z', '+00:00')).date()
                if date >= ab_start_dt:
                    if abnormal_end:
                        ab_end_dt = datetime.fromisoformat(abnormal_end.replace('Z', '+00:00')).date()
                        date_is_abnormal = (date <= ab_end_dt)
                    else:
                        date_is_abnormal = True
            except Exception as _e:
                logging.warning(f"Error parsing abnormal dates: {_e}")
        if date_is_abnormal:
            abnormal_label = messages.get('daily.abnormal_status', 'Abnormal Status')
            abnormal_text  = messages.get('status.abnormal', 'Abnormal')
            lines.append(f"{abnormal_label}: {abnormal_text}")
        
        # Check for measurements on this date
        # Count total progesterone measurements up to this date for blood sample tracking
        prog_up_to_date = 0
        for meas in animal_data.get('daten', []):
            if isinstance(meas.get('datum'), datetime) and meas['datum'].date() <= date:
                prog_up_to_date += 1
        
        for measurement in animal_data.get('daten', []):
            if isinstance(measurement.get('datum'), datetime):
                meas_date = measurement['datum'].date()
                if meas_date == date:
                    value = measurement.get('wert', '?')
                    # Round to 2 decimal places
                    if isinstance(value, (int, float)):
                        value = round(float(value), 2)
                    probe = measurement.get('probennummer', '')
                    max_samples = animal_data.get('max_messungen', '?')
                    # Include blood sample count
                    blood_sample_label = messages.get('daily.blood_sample', 'Blood Sample')
                    sample_info = f" [{blood_sample_label}: {prog_up_to_date}/{max_samples}]"
                    prog_short = messages.get('plot.event.progesterone_short', 'Prog.')
                    sample_id_label = messages.get('table.header.sample_id', messages.get('plot.tooltip.sample_label', 'Sample'))
                    lines.append(f"{prog_short}: {value} ng/ml" + (f" ({sample_id_label}: {probe})" if probe else "") + sample_info)
        
        # Check for PdG measurements
        for pdg in animal_data.get('pdg', []):
            if isinstance(pdg.get('datum'), datetime):
                pdg_date = pdg['datum'].date()
                if pdg_date == date:
                    value = pdg.get('wert', '?')
                    # Round to 2 decimal places
                    if isinstance(value, (int, float)):
                        value = round(float(value), 2)
                    probe = pdg.get('probennummer', '')
                    pdg_label = messages.get('plot.series.pdg', 'PdG')
                    sample_id_label = messages.get('table.header.sample_id', messages.get('plot.tooltip.sample_label', 'Sample'))
                    lines.append(f"{pdg_label}: {value} µg/mg Cr" + (f" ({sample_id_label}: {probe})" if probe else ""))
        
        # Check for weight measurements
        role = animal_data.get('rolle')
        for weight in animal_data.get('gewicht', []):
            if isinstance(weight.get('datum'), datetime):
                w_date = weight['datum'].date()
                if w_date == date:
                    value = weight.get('wert', '?')
                    # Round to 0 decimal places (integer)
                    if isinstance(value, (int, float)):
                        current_weight = int(round(float(value)))
                        
                        # Calculate percentage change
                        reference_weight = None
                        if role == Role.OFFSPRING.value:
                            # For offspring: compare to last measurement before this date
                            previous_weights = [w for w in animal_data.get('gewicht', [])
                                              if isinstance(w.get('datum'), datetime) 
                                              and w['datum'].date() < w_date]
                            if previous_weights:
                                # Sort by date and get the most recent
                                previous_weights.sort(key=lambda x: x['datum'])
                                last_weight = previous_weights[-1].get('wert')
                                if isinstance(last_weight, (int, float)):
                                    reference_weight = float(last_weight)
                        else:
                            # For non-offspring: compare to reference weight
                            ref_w = animal_data.get('ref_weight')
                            if isinstance(ref_w, (int, float)):
                                reference_weight = float(ref_w)
                        
                        # Format weight with percentage
                        weight_label = messages.get('daily.weight', 'Weight')
                        if reference_weight and reference_weight > 0:
                            percent_change = ((current_weight - reference_weight) / reference_weight) * 100
                            sign = '+' if percent_change >= 0 else ''
                            color = 'green' if percent_change >= 0 else 'red'
                            weight_text = f"{weight_label}: {current_weight}g (<span style='color:{color}'>{sign}{percent_change:.1f}%</span>)"
                        else:
                            weight_text = f"{weight_label}: {current_weight}g"
                        
                        lines.append(weight_text)
                    else:
                        weight_label = messages.get('daily.weight', 'Weight')
                        lines.append(f"{weight_label}: {value} g")
        
        # Check for sperm measurements (for males)
        if self._is_steroid_track_active():
            for sperm in animal_data.get('sperm', []):
                if isinstance(sperm.get('datum'), datetime):
                    s_date = sperm['datum'].date()
                    if s_date == date:
                        mot = sperm.get('motility', '?')
                        prog = sperm.get('progressive', '?')
                        count = sperm.get('count', '?')
                        # Round percentages to 2 decimals
                        if isinstance(mot, (int, float)):
                            mot = round(float(mot), 2)
                        if isinstance(prog, (int, float)):
                            prog = round(float(prog), 2)
                        if isinstance(count, (int, float)):
                            count = round(float(count), 2)
                        sperm_label = messages.get('daily.sperm', 'Sperm')
                        motile_label = messages.get('plot.tooltip.motile_label', 'Motile')
                        progressive_label = messages.get('plot.tooltip.progressive_label', 'Progressive')
                        sperm_unit = messages.get('plot.tooltip.sperm_unit', 'Sperm/ml')
                        lines.append(f"{sperm_label}: {motile_label} {mot}%, {progressive_label} {prog}%, {count} {sperm_unit}")
        
        # Count events for this animal (normalized) and render events for this date
        event_counts = self._count_events_up_to_date(animal_data, date)

        # Collect events from both the unified events list and legacy arrays.
        # Dedupe by (event_type, date) and prefer a non-empty note.
        ordered_keys: List[Tuple[str, datetime.date]] = []
        occ_notes: Dict[Tuple[str, datetime.date], str] = {}

        # 1) Unified events list (has typ + optional notiz)
        for ev in animal_data.get('events', []) or []:
            if not (isinstance(ev, dict) and isinstance(ev.get('datum'), datetime)):
                continue
            if ev['datum'].date() != date:
                continue
            typ_lower = self._normalize_report_event_type(ev.get('typ', ''))
            key = (typ_lower, ev['datum'].date())
            note = str(ev.get('notiz', '') or '').strip()
            if key not in occ_notes:
                ordered_keys.append(key)
                occ_notes[key] = note
            elif not occ_notes.get(key) and note:
                occ_notes[key] = note

        # 2) Legacy arrays (date-only) — do not override an existing unified entry
        for ev_type in ['op', 'pgf', 'embryo', 'abort', 'geburt', 'trächtigkeit', 'fsh', 'progesterone']:
            typ_lower = self._normalize_report_event_type(ev_type)
            for ev_date in animal_data.get(ev_type, []) or []:
                if not isinstance(ev_date, datetime):
                    continue
                if ev_date.date() != date:
                    continue
                key = (typ_lower, ev_date.date())
                if key not in occ_notes:
                    ordered_keys.append(key)
                    occ_notes[key] = ''

        for typ_lower, _d in ordered_keys:
            label = self._get_report_event_label(typ_lower, messages)
            max_allowed = self._get_report_event_max(typ_lower, animal_data)
            cur = event_counts.get(typ_lower, (None, None))[0]

            suffix = ''
            if typ_lower == 'abort':
                suffix = f" ({cur})" if cur is not None else " (?)"
            elif max_allowed not in (None, 0, ''):
                suffix = f" ({cur}/{max_allowed})" if cur is not None else f" (?/{max_allowed})"

            line = f"{label}{suffix}".strip()
            note = occ_notes.get((typ_lower, _d), '')
            if note:
                line += f": {note}"
            lines.append(line)
        
        # Add reproduction status - calculate based on the specific date for historical accuracy
        status = self._get_status_at_date(animal_name, date)
        
        # Convert pregnancy symbols to readable text
        reproductive_status = ''
        if '☉?' in status:
            reproductive_status = messages.get('status.possibly_pregnant', 'Possibly pregnant')
        elif '☉' in status:
            reproductive_status = messages.get('status.pregnant', 'Pregnant')
        elif 'Oo' in status:
            reproductive_status = messages.get('status.has_offspring', 'Has offspring')
        # Don't show "not pregnant" (empty) - that's the default state
        
        if reproductive_status:
            repro_label = messages.get('daily.reproduction_status', 'Reproduction Status')
            lines.append(f"{repro_label}: {reproductive_status}")

        # Append project-assignment and severity-change notes stored by _log_project_change
        # and _log_severity_change (keyed by ISO date string in animal_data['edits'])
        iso_str = date.strftime('%Y-%m-%d')
        day_edits = animal_data.get('edits', {}).get(iso_str, {})
        if isinstance(day_edits, dict):
            proj_note = day_edits.get('project_note', '')
            sev_note  = day_edits.get('severity_note', '')
            experiment_note = day_edits.get('experiment_note', '')
            if proj_note:
                lines.append(proj_note)
            if sev_note:
                lines.append(sev_note)
            if experiment_note:
                lines.append(experiment_note)

        return ', '.join(lines) if lines else ''
    
    # ── Status checkbox wiring ──────────────────────────────────────────────

    def _wire_status_checkboxes(
        self,
        chk_sick: 'QCheckBox',
        chk_abnormal: 'QCheckBox',
        animal_name: Optional[str],
        rec: Dict[str, Any],
        parent_dlg: 'QDialog',
    ) -> None:
        """Wire sick/abnormal checkboxes to the permission-aware status system.

        If animal_name is None (creating new animal), no wiring is done —
        the checkboxes act as plain toggles whose value is saved on dialog accept.
        """
        if not animal_name:
            return

        medi = getattr(self, 'medi_track_plugin', None)
        has_enable  = self._master_can('medi_track.status_enable')
        has_manage  = self._master_can('medi_track.status_manage')
        master_active = getattr(self, 'has_master_track', False)

        # Scenario 6 (medi active, no permissions) OR
        # Scenario 1 no-perm (no medi, no enable perm) → disable both
        if medi is not None and master_active and not has_enable and not has_manage:
            chk_sick.setEnabled(False)
            chk_abnormal.setEnabled(False)
            return
        if medi is None and master_active and not has_enable:
            chk_sick.setEnabled(False)
            chk_abnormal.setEnabled(False)
            return

        orig_sick     = bool(rec.get('sick', False))
        orig_abnormal = bool(rec.get('abnormal_current', False))

        chk_sick.clicked.connect(
            lambda: self._on_status_chk_click(
                parent_dlg, chk_sick, 'sick', animal_name, orig_sick,
                medi, has_enable, has_manage, master_active))
        chk_abnormal.clicked.connect(
            lambda: self._on_status_chk_click(
                parent_dlg, chk_abnormal, 'abnormal', animal_name, orig_abnormal,
                medi, has_enable, has_manage, master_active))

    def _on_status_chk_click(
        self,
        parent_dlg: 'QDialog',
        checkbox: 'QCheckBox',
        status_type: str,
        animal_name: str,
        original_value: bool,
        medi: Any,
        has_enable: bool,
        has_manage: bool,
        master_active: bool,
    ) -> None:
        """Handle a sick or abnormal checkbox click according to the 6 scenarios."""
        new_value = checkbox.isChecked()
        mode = 'add' if new_value else 'resolve'

        if medi is None:
            # Scenario 1: plugin not active — just let the toggle stand;
            # permissions already checked at wire time (disabled if no enable).
            return

        # Determine prefill signature using display_name when available
        prefill_sig = ''
        mt = getattr(self, 'master_track', None)
        if master_active and mt:
            dname = getattr(mt, 'current_display_name', None)
            prefill_sig = str(dname or getattr(mt, 'current_username', '') or '')

        def _set_checked(state: bool) -> None:
            """Set checkbox state without emitting any signals."""
            checkbox.blockSignals(True)
            checkbox.setChecked(state)
            checkbox.blockSignals(False)

        if original_value:
            # Box was already active. Silently keep it checked (Qt already
            # unchecked it on click — restore before any repaint happens).
            _set_checked(True)
            accepted = medi.open_status_dialog(
                parent_dlg, animal_name, status_type, 'manage', prefill_sig,
                can_add=has_enable or has_manage, can_resolve=has_manage)
            if accepted:
                # Only uncheck when: resolve was chosen, save was pressed,
                # AND no unresolved issues remain.
                remaining = medi.store.get_active_issues(animal_name, status_type)
                _set_checked(len(remaining) > 0)
            # Cancelled → box stays checked (already set above).
            return

        # Scenario 3: status_enable only + trying to uncheck → warning + revert
        if master_active and not new_value and has_enable and not has_manage:
            QMessageBox.warning(
                parent_dlg,
                self.messages.get('error.title', 'Warning'),
                self.messages.get(
                    'medi_track.error.no_manage_rights',
                    'You need status management rights to resolve a health issue.'))
            _set_checked(True)
            return

        accepted = medi.open_status_dialog(
            parent_dlg, animal_name, status_type, mode, prefill_sig)
        if not accepted:
            _set_checked(original_value)

    # ────────────────────────────────────────────────────────────────────────

    def _update_sick_times(self, animal_data: Dict[str, Any], is_sick: bool) -> None:
        """Update sick status using persistent periods for new data.
        
        New behavior: Sick status persists across dates until unchecked.
        - Checking sick: Sets sick_start_date to today (if not already sick)
        - Unchecking sick: Sets sick_end_date to today (if was sick)
        
        Old sick_times array is kept for backward compatibility with historical data.
        
        Args:
            animal_data: The animal's data dictionary
            is_sick: True if checkbox is checked, False if unchecked
        """
        # Get today's date at midnight
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get current sick status (before update)
        was_sick = animal_data.get('sick', False)
        
        logging.debug(f"[SICK UPDATE] _update_sick_times called: is_sick={is_sick}, was_sick={was_sick}, current sick_start_date={animal_data.get('sick_start_date')}, sick_end_date={animal_data.get('sick_end_date')}")
        
        if is_sick:
            # Only set start date if transitioning from healthy to sick
            if not was_sick:
                animal_data['sick_start_date'] = today.isoformat()
                animal_data['sick_end_date'] = None  # Clear end date (ongoing illness)
                logging.debug(f"[SICK UPDATE] Animal became sick on {today.date()}, sick_start_date set to {animal_data['sick_start_date']}")
            else:
                # Animal was already sick, but check if we need to set start date (legacy data migration)
                if not animal_data.get('sick_start_date'):
                    animal_data['sick_start_date'] = today.isoformat()
                    animal_data['sick_end_date'] = None
                    logging.debug(f"[SICK UPDATE] Migrating legacy sick data: setting sick_start_date to {today.date()}")
                else:
                    logging.debug(f"[SICK UPDATE] Animal already sick, keeping existing sick_start_date={animal_data.get('sick_start_date')}")
        else:
            # Only set end date if transitioning from sick to healthy
            if was_sick:
                # sick_end_date represents the LAST day the animal was sick (yesterday)
                yesterday = today - timedelta(days=1)
                animal_data['sick_end_date'] = yesterday.isoformat()
                logging.debug(f"[SICK UPDATE] Animal recovered on {today.date()}, sick_end_date set to {animal_data['sick_end_date']} (last sick day)")
            else:
                logging.debug("[SICK UPDATE] Animal already healthy, no changes")
        
        # Update current sick status
        animal_data['sick'] = is_sick
        logging.debug(f"[SICK UPDATE] After _update_sick_times: sick={animal_data['sick']}, sick_start_date={animal_data.get('sick_start_date')}, sick_end_date={animal_data.get('sick_end_date')}")
        
        # Initialize old sick_times array for backward compatibility (not used for new data)
        if 'sick_times' not in animal_data:
            animal_data['sick_times'] = []

    def _update_abnormal_times(self, animal_data: Dict[str, Any], is_abnormal: bool) -> None:
        """Update abnormal status using persistent periods, mirroring _update_sick_times."""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        was_abnormal = animal_data.get('abnormal_current', False)
        if is_abnormal:
            if not was_abnormal:
                animal_data['abnormal_start_date'] = today.isoformat()
                animal_data['abnormal_end_date'] = None
            else:
                if not animal_data.get('abnormal_start_date'):
                    animal_data['abnormal_start_date'] = today.isoformat()
                    animal_data['abnormal_end_date'] = None
        else:
            if was_abnormal:
                yesterday = today - timedelta(days=1)
                animal_data['abnormal_end_date'] = yesterday.isoformat()
        animal_data['abnormal_current'] = is_abnormal
        if is_abnormal:
            animal_data['abnormal_ever'] = True

    def _auto_fill_status_signature(self, animal_data: Dict[str, Any], status_changed: bool) -> None:
        """When a sick/abnormal status transition occurs, write the current user's name
        into today's report Signatures cell so the change is attributed automatically."""
        if not status_changed:
            return
        mt = getattr(self, 'master_track', None)
        if not (mt and getattr(mt, 'is_logged_in', False)):
            return
        username = str(getattr(mt, 'current_username', '') or '').strip()
        if not username:
            return
        today_str = datetime.now().strftime('%Y-%m-%d')
        if 'edits' not in animal_data:
            animal_data['edits'] = {}
        if today_str not in animal_data['edits']:
            animal_data['edits'][today_str] = {}
        existing = animal_data['edits'][today_str].get('signatures', '')
        if username not in existing:
            animal_data['edits'][today_str]['signatures'] = (
                f"{existing}, {username}".strip(', ') if existing else username)

    def _log_project_change(
        self,
        animal_name: str,
        old_project: str,
        new_project: str,
        old_severity: str = "",
        new_severity: str = "",
    ) -> None:
        """Write a project assignment change to Medi Track and Reports daily data."""
        if old_project == new_project:
            return
        mt = getattr(self, 'master_track', None)
        dname = getattr(mt, 'current_display_name', None) if mt else None
        sig = str(dname or getattr(mt, 'current_username', '') or '').strip() if mt else ''
        # Normalise severity keys and build display labels
        def _norm_sv(s: str) -> str:
            return 'SV0' if s == '0' else (s or '')
        old_sv = _norm_sv(old_severity)
        new_sv = _norm_sv(new_severity)
        sev_map = {
            'SV0': self.messages.get('severity.0',   'SV0 - no severity'),
            'SV1': self.messages.get('severity.sv1', 'SV1 - non-recovery'),
            'SV2': self.messages.get('severity.sv2', 'SV2 - mild or very mild'),
            'SV3': self.messages.get('severity.sv3', 'SV3 - moderate'),
            'SV4': self.messages.get('severity.sv4', 'SV4 - severe'),
        }
        old_sv_lbl = sev_map.get(old_sv, old_sv) if old_sv else ''
        new_sv_lbl = sev_map.get(new_sv, new_sv) if new_sv else ''

        def _proj_sev(proj: str, sv_lbl: str) -> str:
            return f"{proj} ({sv_lbl})" if sv_lbl else proj

        # Build human-readable note for Reports daily data
        left_lbl = self.messages.get('medi_track.report.event.project_left', 'Project — left')
        asgn_lbl = self.messages.get('medi_track.report.event.project_assigned', 'Project — assigned')
        if new_project and not old_project:
            note = f"{asgn_lbl}: {_proj_sev(new_project, new_sv_lbl)}"
        elif old_project and not new_project:
            note = f"{left_lbl}: {_proj_sev(old_project, old_sv_lbl)}"
        else:
            note = (f"{left_lbl}: {_proj_sev(old_project, old_sv_lbl)}; "
                    f"{asgn_lbl}: {_proj_sev(new_project, new_sv_lbl)}")

        animal_data = self.animals.get(animal_name, {})
        today_iso = datetime.now().strftime('%Y-%m-%d')
        today_fmt = datetime.now().strftime('%d.%m.%Y')
        animal_data.setdefault('edits', {}).setdefault(today_iso, {})
        existing = animal_data['edits'][today_iso].get('project_note', '')
        animal_data['edits'][today_iso]['project_note'] = (
            f"{existing}; {note}" if existing else note)

        # Project history tracking
        if old_project:
            old_entry_date = animal_data.get('project_entry_date', '')
            hist = animal_data.setdefault('project_history', [])
            hist.append({
                'project':    old_project,
                'entry_date': old_entry_date,
                'leave_date': today_fmt,
                'severity':   old_sv or '',
            })
        if new_project:
            animal_data['project_entry_date'] = today_fmt
            animal_data['project_severity']   = new_sv or ''

        medi = getattr(self, 'medi_track_plugin', None)
        if getattr(self, 'has_medi_track_plugin', False) and medi:
            try:
                medi.log_project_change(
                    animal_name, old_project, new_project, sig,
                    old_sv_lbl, new_sv_lbl)
            except Exception as exc:
                logging.error(f"_log_project_change: {exc}")

    def _connect_project_severity_reset(self, project_combo, severity_combo):
        """Reset project severity after a completed project selection/edit."""
        def _reset_severity():
            try:
                QTimer.singleShot(0, lambda: severity_combo.setCurrentIndex(0))
            except RuntimeError:
                pass
        try:
            project_combo.activated.connect(lambda _idx: _reset_severity())
            line_edit = project_combo.lineEdit()
            if line_edit is not None:
                line_edit.editingFinished.connect(_reset_severity)
        except RuntimeError:
            pass

    def _coerce_in_experiment_for_project(self, requested: bool, project: str) -> bool:
        """Do not allow experimental status without a project association."""
        if requested and not (project or '').strip():
            self._show_message("warning.in_experiment_requires_project")
            return False
        return bool(requested)

    def _notify_project_track_assignment_change(
        self,
        animal_name: str,
        old_project: str,
        new_project: str,
        old_severity: str = '',
        old_in_experiment: bool = False,
    ) -> None:
        pt = getattr(self, 'projects_plugin', None)
        if not pt:
            return
        old_proj = (old_project or '').strip()
        new_proj = (new_project or '').strip()
        try:
            if old_proj and old_proj != new_proj:
                pt.on_animal_project_removed(
                    animal_name, old_proj, old_severity or '', old_in_experiment)
            if new_proj and new_proj != old_proj:
                pt.on_animal_added(animal_name)
        except Exception:
            logging.exception(
                "Project Track update failed for %s while changing project from %s to %s",
                animal_name, old_proj, new_proj)

    def _mark_cage_assignments_dirty_for_project_change(
        self,
        animal_name: str,
        old_project: str,
        new_project: str,
    ) -> None:
        old_proj = (old_project or '').strip()
        new_proj = (new_project or '').strip()
        if old_proj == new_proj:
            return
        cage = getattr(self, 'cage_track_plugin', None)
        if not (getattr(self, 'has_cage_track_plugin', False) and cage):
            return
        mark_dirty = getattr(cage, 'mark_cage_assignments_dirty', None)
        if not callable(mark_dirty):
            return
        try:
            mark_dirty()
            self._save_trace(
                "post_animal_project_updates.cage_dirty_marked",
                animal_name=animal_name,
                old_project=old_proj,
                new_project=new_proj,
            )
        except Exception:
            logging.exception(
                "Failed to mark Cage Track assignments dirty for project change: %s %r -> %r",
                animal_name, old_proj, new_proj)

    def _schedule_post_animal_save_project_updates(
        self,
        animal_name: str,
        old_project: str,
        new_project: str,
        old_severity: str = '',
        new_severity: str = '',
        old_in_experiment: bool = False,
        new_in_experiment: bool = False,
        creating: bool = False,
    ) -> None:
        self._save_trace(
            "post_animal_project_updates.schedule.enter",
            animal_name=animal_name,
            old_project=old_project,
            new_project=new_project,
            old_severity=old_severity,
            new_severity=new_severity,
            old_in_experiment=old_in_experiment,
            new_in_experiment=new_in_experiment,
            creating=creating,
        )
        if old_project == new_project and old_severity == new_severity and old_in_experiment == new_in_experiment:
            self._save_trace("post_animal_project_updates.schedule.no_change", animal_name=animal_name)
            return

        def _run_post_save_updates():
            if QApplication.activeModalWidget() is not None:
                self._save_trace("post_animal_project_updates.run.modal_wait", animal_name=animal_name)
                QTimer.singleShot(250, _run_post_save_updates)
                return
            try:
                self._save_trace("post_animal_project_updates.run.enter", animal_name=animal_name)
                logging.info(
                    "Post-save project updates begin: animal=%s old_project=%r new_project=%r old_severity=%r new_severity=%r",
                    animal_name, old_project, new_project, old_severity, new_severity)
                self._mark_cage_assignments_dirty_for_project_change(
                    animal_name, old_project, new_project)
                self._log_project_change(
                    animal_name, old_project, new_project, old_severity, new_severity)
                self._save_trace("post_animal_project_updates.run.project_log.after", animal_name=animal_name)
                self._log_severity_change(
                    animal_name, old_project, new_project, old_severity, new_severity, creating)
                self._save_trace("post_animal_project_updates.run.severity_log.after", animal_name=animal_name)
                if new_in_experiment != old_in_experiment:
                    self._save_trace("post_animal_project_updates.run.experiment_log.before", animal_name=animal_name)
                    self._log_experiment_change(animal_name, new_in_experiment)
                    self._save_trace("post_animal_project_updates.run.experiment_log.after", animal_name=animal_name)
                self._notify_project_track_assignment_change(
                    animal_name, old_project, new_project, old_severity, old_in_experiment)
                self._save_trace("post_animal_project_updates.run.project_track_notify.after", animal_name=animal_name)
                pt = getattr(self, 'projects_plugin', None)
                if pt and hasattr(pt, 'on_animal_experiment_status_changed'):
                    active_project = (new_project or '').strip()
                    had_in_experiment = bool(old_in_experiment or new_in_experiment)
                    if active_project and had_in_experiment:
                        pt.on_animal_experiment_status_changed(
                            animal_name, active_project, had_in_experiment,
                            new_severity or old_severity or '')
                        self._save_trace(
                            "post_animal_project_updates.run.project_track_experiment_history.after",
                            animal_name=animal_name)
                if pt and hasattr(pt, '_schedule_project_ui_refresh'):
                    pt._schedule_project_ui_refresh()
                    self._save_trace("post_animal_project_updates.run.project_track_refresh.after", animal_name=animal_name)
                self._save_persistence()
                self._save_trace("post_animal_project_updates.run.persistence.after", animal_name=animal_name)
                logging.info("Post-save project updates completed: animal=%s", animal_name)
                self._save_trace("post_animal_project_updates.run.exit", animal_name=animal_name)
            except Exception:
                self._save_trace("post_animal_project_updates.run.exception", animal_name=animal_name)
                logging.exception("Post-save project updates failed for %s", animal_name)

        self._save_trace("post_animal_project_updates.timer.before", animal_name=animal_name)
        QTimer.singleShot(250, _run_post_save_updates)
        self._save_trace("post_animal_project_updates.timer.after", animal_name=animal_name)

    def _save_trace(self, step: str, **fields: Any) -> None:
        """Write crash-resistant save diagnostics with immediate flush/fsync."""
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            safe_fields = []
            for key, value in fields.items():
                try:
                    text = repr(value)
                except Exception:
                    text = f"<unreprable {type(value).__name__}>"
                if len(text) > 300:
                    text = text[:297] + "..."
                safe_fields.append(f"{key}={text}")
            line = (
                f"{datetime.now().isoformat(timespec='milliseconds')} "
                f"{step}"
            )
            if safe_fields:
                line += " | " + " | ".join(safe_fields)
            path = LOG_DIR / "save_trace.log"
            with open(path, "a", encoding="utf-8") as trace_file:
                trace_file.write(line + "\n")
                trace_file.flush()
                os.fsync(trace_file.fileno())
            logging.info("SAVE_TRACE %s %s", step, " ".join(safe_fields))
        except Exception:
            pass

    def _save_trace_record_summary(
        self,
        rec: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(rec, dict):
            return {"type": type(rec).__name__}
        summary: Dict[str, Any] = {}
        for key in (
            "rolle", "project", "severity", "species", "in_experiment",
            "sick", "abnormal_current", "birth_date", "death_date",
        ):
            summary[key] = rec.get(key)
        for key in ("daten", "pdg", "gewicht", "events", "sperm"):
            value = rec.get(key)
            summary[f"{key}_len"] = len(value) if isinstance(value, list) else None
        return summary

    def _log_severity_change(
        self,
        animal_name: str,
        old_project: str,
        new_project: str,
        old_severity: str,
        new_severity: str,
        creating: bool = False,
    ) -> None:
        """Log a severity change to animal report edits and Medi Track (other type)."""
        # Normalize legacy '0' value
        if old_severity == '0':
            old_severity = 'SV0'
        if new_severity == '0':
            new_severity = 'SV0'
        # No change or user left (Please select) — nothing to log
        if old_severity == new_severity or not new_severity:
            return
        # When switching between two real projects, project-change log already covers it
        if old_project and new_project and old_project != new_project:
            return
        proj = new_project or old_project
        if not proj:
            return
        mt = getattr(self, 'master_track', None)
        dname = getattr(mt, 'current_display_name', None) if mt else None
        sig = str(dname or getattr(mt, 'current_username', '') or '').strip() if mt else ''
        sev_map = {
            'SV0': self.messages.get('severity.0',   'SV0 - no severity'),
            'SV1': self.messages.get('severity.sv1', 'SV1 - non-recovery'),
            'SV2': self.messages.get('severity.sv2', 'SV2 - mild or very mild'),
            'SV3': self.messages.get('severity.sv3', 'SV3 - moderate'),
            'SV4': self.messages.get('severity.sv4', 'SV4 - severe'),
        }
        old_lbl = (self.messages.get('severity.undefined', 'undefined')
                   if not old_severity else sev_map.get(old_severity, old_severity))
        new_lbl = sev_map.get(new_severity, new_severity)
        template = self.messages.get(
            'medi_track.entry.severity_changed',
            'Severity in {project} changed from {old} to {new}')
        note = template.replace('{project}', proj).replace('{old}', old_lbl).replace('{new}', new_lbl)
        animal_data = self.animals.get(animal_name, {})
        today_str = datetime.now().strftime('%Y-%m-%d')
        animal_data.setdefault('edits', {}).setdefault(today_str, {})
        animal_data['edits'][today_str]['severity_note'] = note
        # Keep project_severity in sync when only the severity changes
        def _norm_sv2(s: str) -> str:
            return 'SV0' if s == '0' else (s or '')
        animal_data['project_severity'] = _norm_sv2(new_severity)
        medi = getattr(self, 'medi_track_plugin', None)
        if getattr(self, 'has_medi_track_plugin', False) and medi:
            try:
                medi.log_severity_change(animal_name, proj, old_lbl, new_lbl, sig)
            except Exception as exc:
                logging.error(f"_log_severity_change: {exc}")

    def _log_experiment_change(self, animal_name: str, started: bool) -> None:
        """Write 'Experiment started/ended' to Reports daily edits and Medi_Track."""
        mt = getattr(self, 'master_track', None)
        sig = ''
        if mt:
            dname = getattr(mt, 'current_display_name', None)
            sig = str(dname or getattr(mt, 'current_username', '') or '').strip()

        if started:
            key      = 'medi_track.entry.experiment_started'
            fallback = 'Experiment started'
        else:
            key      = 'medi_track.entry.experiment_ended'
            fallback = 'Experiment ended'
        note = self.messages.get(key, fallback)

        animal_data = self.animals.get(animal_name, {})
        today_str = datetime.now().strftime('%Y-%m-%d')
        animal_data.setdefault('edits', {}).setdefault(today_str, {})
        animal_data['edits'][today_str]['experiment_note'] = note

        medi = getattr(self, 'medi_track_plugin', None)
        if getattr(self, 'has_medi_track_plugin', False) and medi:
            try:
                entry_type = 'experiment_started' if started else 'experiment_ended'
                medi.log_experiment_change(animal_name, entry_type, sig)
            except Exception as exc:
                logging.error(f'_log_experiment_change: {exc}')

    def _count_events_up_to_date(self, animal_data: Dict[str, Any], date: datetime.date) -> Dict[str, Tuple[int, int]]:
        """Count how many times each event type has occurred up to the given date.
        Returns dict of event_type -> (current_count, total_count)
        """
        event_counts = {}

        # Build a lookup of event dates already present in the unified events list so we can
        # ignore legacy-array duplicates.
        unified_dates_by_type = {}
        for ev in animal_data.get('events', []) or []:
            if isinstance(ev, dict) and isinstance(ev.get('datum'), datetime):
                t = self._normalize_report_event_type(ev.get('typ', ''))
                d = ev['datum'].date()
                unified_dates_by_type.setdefault(t, set()).add(d)
        
        # Count from events array
        for event in animal_data.get('events', []):
            if isinstance(event.get('datum'), datetime):
                typ = self._normalize_report_event_type(event.get('typ', '').lower())
                if typ:
                    if typ not in event_counts:
                        event_counts[typ] = {'up_to': 0, 'total': 0}
                    event_counts[typ]['total'] += 1
                    if event['datum'].date() <= date:
                        event_counts[typ]['up_to'] += 1
        
        # Count from legacy event arrays
        for ev_type in ['op', 'pgf', 'embryo', 'abort', 'geburt', 'trächtigkeit', 'fsh', 'progesterone']:
            events = animal_data.get(ev_type, [])
            if events:
                typ = self._normalize_report_event_type(ev_type)
                if typ not in event_counts:
                    event_counts[typ] = {'up_to': 0, 'total': 0}

                # Only count legacy entries that are not already represented in the unified events list.
                legacy_dates = [d.date() for d in events if isinstance(d, datetime)]
                filtered_dates = [d for d in legacy_dates if d not in unified_dates_by_type.get(typ, set())]

                event_counts[typ]['total'] += len(filtered_dates)
                for ev_date in events:
                    if isinstance(ev_date, datetime):
                        d = ev_date.date()
                        if d in unified_dates_by_type.get(typ, set()):
                            continue
                        if d <= date:
                            event_counts[typ]['up_to'] += 1
        
        # Convert to (current, total) tuples
        result = {}
        for ev_type, counts in event_counts.items():
            result[ev_type] = (counts['up_to'], counts['total'])
        
        return result

    def _normalize_report_event_type(self, typ: str) -> str:
        try:
            t = (typ or '').strip().lower()
        except Exception:
            t = str(typ).strip().lower()

        # normalize aliases / legacy keys using LEGACY_EVENT_MAP
        return LEGACY_EVENT_MAP.get(t, t)

    def _get_report_event_label(self, typ_lower: str, messages: Dict[str, str]) -> str:
        labels = {
            'pgf': messages.get('plot.event.pgf', 'PGF'),
            'embryo_transfer': messages.get('plot.event.embryo_transfer', 'Embryo'),
            'surgery': messages.get('plot.event.operation', messages.get('daily.surgery', 'Surgery')),
            'pregnancy': messages.get('plot.event.pregnancy', 'Pregnancy'),
            'abortion': messages.get('plot.event.abort', 'Abort'),
            'birth': messages.get('plot.event.birth', 'Birth'),
            'fsh': messages.get('plot.event.fsh', 'FSH'),
            'progesterone': messages.get('plot.series.progesterone', 'Progesterone'),
            'special_measurement': messages.get('plot.event.special_measurement', 'Special measurement'),
            'measurement': messages.get('stats.measurement', 'Measurement'),
        }
        return labels.get(typ_lower, typ_lower)

    def _get_report_event_max(self, typ_lower: str, animal_data: Dict[str, Any]):
        if typ_lower == 'fsh':
            return animal_data.get('max_fsh', '?')
        if typ_lower == 'surgery':
            return animal_data.get('max_op', '?')
        if typ_lower == 'measurement':
            return animal_data.get('max_measurements', '?')
        if typ_lower == 'embryo_transfer':
            return animal_data.get('max_embryo', '?')
        if typ_lower == 'pregnancy':
            return animal_data.get('max_pregnancies', '?')
        if typ_lower == 'birth':
            return animal_data.get('max_geburten', '?')
        if typ_lower == 'pgf':
            return animal_data.get('max_pgf', '?')
        if typ_lower == 'special_measurement':
            return animal_data.get('max_special', '?')
        if typ_lower == 'progesterone':
            return animal_data.get('max_messungen', '?')
        return None
    
    def _get_reproduction_status(self, animal_data: Dict[str, Any], date: datetime.date) -> str:
        """Get reproduction status symbols for a specific date."""
        role = animal_data.get('rolle')
        if role not in [Role.AMME.value, Role.ZUCHTTIER.value]:
            return ''
        
        # Check if female
        if role == Role.ZUCHTTIER.value and animal_data.get('sex') != 'female':
            return ''
        
        preg_dates = [ev['datum'].date() for ev in animal_data.get('events', []) 
                     if ev.get('typ') == 'pregnancy' and isinstance(ev.get('datum'), datetime)]
        birth_dates = [ev['datum'].date() for ev in animal_data.get('events', []) 
                      if ev.get('typ') == 'birth' and isinstance(ev.get('datum'), datetime)]
        abort_dates = [ev['datum'].date() for ev in animal_data.get('events', []) 
                      if ev.get('typ') == 'abortion' and isinstance(ev.get('datum'), datetime)]
        
        # Check if has children
        if birth_dates:
            return 'Oo'  # Has children
        
        # Check pregnancy status relative to this date
        recent_preg = [d for d in preg_dates if d <= date]
        recent_abort = [d for d in abort_dates if d <= date]
        
        if recent_preg:
            last_preg = max(recent_preg)
            if recent_abort and max(recent_abort) > last_preg:
                return 'O'  # Not pregnant (aborted after last pregnancy confirmation)
            # Check if pregnancy is recent (within ~21 days)
            if (date - last_preg).days <= 21:
                return '☉'  # Pregnant
            else:
                return '☉?'  # Uncertain
        
        return 'O'  # Not pregnant
    
    def _get_event_statistics(self, animal_data: Dict[str, Any]) -> str:
        """Get event statistics showing all items with defined maximums for the role."""
        role = animal_data.get('rolle')
        steroid_active = self._is_steroid_track_active()
        stats = []
        
        if role == Role.SAMENSP.value and steroid_active:
            # Sperm donor: show sperm samples
            sperm_count = len(set(s['datum'].date() for s in animal_data.get('sperm', []) 
                                if isinstance(s.get('datum'), datetime)))
            max_sperm = animal_data.get('max_spermaproben', '?')
            label_sperm = self.messages.get('stats.sperm', 'Sperm')
            stats.append(f"{label_sperm}: {sperm_count}/{max_sperm}")
        
        elif role == Role.OFFSPRING.value:
            # Offspring: show special measurements and OPs
            events = animal_data.get('events', [])
            sonder_count = sum(1 for ev in events if ev.get('typ') == 'special_measurement')
            op_count = sum(1 for ev in events if ev.get('typ') == 'surgery')
            max_special = animal_data.get('max_special', 0)
            max_op = animal_data.get('max_op', 0)
            if max_special > 0:
                label_special = self.messages.get('stats.special', 'Special')
                stats.append(f"{label_special}: {sonder_count}/{max_special}")
            if max_op > 0:
                label_op = self.messages.get('stats.surgery', 'OP')
                stats.append(f"{label_op}: {op_count}/{max_op}")

        elif role == Role.EXPERIMENTAL.value:
            # Experimental animal: show surgeries and measurements
            events = animal_data.get('events', [])
            op_count   = sum(1 for ev in events if ev.get('typ') == 'surgery')
            meas_count = sum(1 for ev in events if ev.get('typ') == 'measurement')
            max_op   = animal_data.get('max_op', 0)
            max_meas = animal_data.get('max_measurements', 0)
            label_op   = self.messages.get('stats.surgery',     'Surgery')
            label_meas = self.messages.get('stats.measurement', 'Measurement')
            if max_op > 0:
                stats.append(f"{label_op}: {op_count}/{max_op}")
            if max_meas > 0:
                stats.append(f"{label_meas}: {meas_count}/{max_meas}")
        
        elif role == Role.SPENDER.value:
            # Female donor: show all relevant maximums
            # Each progesterone measurement ('daten') represents one blood sample
            prog_count = len(animal_data.get('daten', []))
            pgf_count = len(animal_data.get('pgf', []))
            op_count = len(animal_data.get('op', []))
            fsh_count = sum(1 for ev in animal_data.get('events', []) if ev.get('typ') == 'fsh')
            
            max_messungen = animal_data.get('max_messungen', 0)
            max_pgf = animal_data.get('max_pgf', 0)
            max_op = animal_data.get('max_op', 0)
            max_fsh = animal_data.get('max_fsh', 0)

            label_blood   = self.messages.get('stats.blood_samples', 'Blood Samples')
            label_pgf     = self.messages.get('stats.pgf', 'PGF')
            label_op      = self.messages.get('stats.surgery', 'OP')
            label_fsh     = self.messages.get('stats.fsh', 'FSH')

            if max_messungen > 0:
                stats.append(f"{label_blood}: {prog_count}/{max_messungen}")
            if max_pgf > 0:
                stats.append(f"{label_pgf}: {pgf_count}/{max_pgf}")
            if max_op > 0:
                stats.append(f"{label_op}: {op_count}/{max_op}")
            if max_fsh > 0:
                stats.append(f"{label_fsh}: {fsh_count}/{max_fsh}")
        
        elif role == Role.AMME.value:
            # Surrogate: show blood samples (progesterone measurements), PGF, embryo transfers, pregnancies, births
            # Each progesterone measurement ('daten') represents one blood sample
            prog_count = len(animal_data.get('daten', []))
            pgf_count = len(animal_data.get('pgf', []))
            embryo_count = sum(1 for ev in animal_data.get('events', []) 
                             if ev.get('typ') == 'embryo_transfer')
            pregnancy_count = sum(1 for ev in animal_data.get('events', []) 
                                if ev.get('typ') == 'pregnancy')
            birth_count = sum(1 for ev in animal_data.get('events', []) 
                            if ev.get('typ') == 'birth')
            
            max_messungen = animal_data.get('max_messungen', 0)
            max_pgf = animal_data.get('max_pgf', 0)
            max_embryo = animal_data.get('max_embryo', 0)
            max_pregnancies = animal_data.get('max_pregnancies', 0)
            max_geburten = animal_data.get('max_geburten', 0)

            label_blood     = self.messages.get('stats.blood_samples', 'Blood Samples')
            label_pgf       = self.messages.get('stats.pgf', 'PGF')
            label_embryo    = self.messages.get('stats.embryo', 'Embryo')
            label_pregnancy = self.messages.get('stats.pregnancy', 'Pregnancy')
            label_birth     = self.messages.get('stats.birth', 'Birth')

            if max_messungen > 0:
                stats.append(f"{label_blood}: {prog_count}/{max_messungen}")
            if max_pgf > 0:
                stats.append(f"{label_pgf}: {pgf_count}/{max_pgf}")
            if max_embryo > 0:
                stats.append(f"{label_embryo}: {embryo_count}/{max_embryo}")
            if max_pregnancies > 0:
                stats.append(f"{label_pregnancy}: {pregnancy_count}/{max_pregnancies}")
            if max_geburten > 0:
                stats.append(f"{label_birth}: {birth_count}/{max_geburten}")
        
        elif role == Role.ZUCHTTIER.value:
            # Breeding animals: show based on what's defined
            events = animal_data.get('events', [])
            pregnancy_count = sum(1 for ev in events if ev.get('typ') == 'pregnancy')
            birth_count = sum(1 for ev in events if ev.get('typ') == 'birth')
            
            max_pregnancies = animal_data.get('max_pregnancies', 0)
            max_geburten = animal_data.get('max_geburten', 0)

            label_pregnancy = self.messages.get('stats.pregnancy', 'Pregnancy')
            label_birth     = self.messages.get('stats.birth', 'Birth')

            if max_pregnancies > 0:
                stats.append(f"{label_pregnancy}: {pregnancy_count}/{max_pregnancies}")
            if max_geburten > 0:
                stats.append(f"{label_birth}: {birth_count}/{max_geburten}")
        
        return ', '.join(stats) if stats else '-'
    
    def _get_event_statistics_localized(self, animal_data: Dict[str, Any], messages: dict) -> str:
        """Get event statistics with localized labels."""
        role = animal_data.get('rolle')
        steroid_active = self._is_steroid_track_active()
        stats = []
        
        if role == Role.SAMENSP.value and steroid_active:
            # Sperm donor: show sperm samples
            sperm_count = len(set(s['datum'].date() for s in animal_data.get('sperm', []) 
                                if isinstance(s.get('datum'), datetime)))
            max_sperm = animal_data.get('max_spermaproben', '?')
            stats.append(f"{messages.get('stats.sperm', 'Sperm')}: {sperm_count}/{max_sperm}")
        
        elif role == Role.OFFSPRING.value:
            # Offspring: show special measurements and OPs
            events = animal_data.get('events', [])
            sonder_count = sum(1 for ev in events if ev.get('typ') == 'special_measurement')
            op_count = sum(1 for ev in events if ev.get('typ') == 'surgery')
            max_special = animal_data.get('max_special', 0)
            max_op = animal_data.get('max_op', 0)
            if max_special > 0:
                stats.append(f"{messages.get('plot.event.special_measurement', messages.get('stats.special', 'Special measurement'))}: {sonder_count}/{max_special}")
            if max_op > 0:
                stats.append(f"{messages.get('stats.surgery', 'Surgery')}: {op_count}/{max_op}")

        elif role == Role.EXPERIMENTAL.value:
            # Experimental animal: show surgeries and measurements
            events = animal_data.get('events', [])
            op_count   = sum(1 for ev in events if ev.get('typ') == 'surgery')
            meas_count = sum(1 for ev in events if ev.get('typ') == 'measurement')
            max_op   = animal_data.get('max_op', 0)
            max_meas = animal_data.get('max_measurements', 0)
            if max_op > 0:
                stats.append(f"{messages.get('stats.surgery', 'Surgery')}: {op_count}/{max_op}")
            if max_meas > 0:
                stats.append(f"{messages.get('stats.measurement', 'Measurement')}: {meas_count}/{max_meas}")
        
        elif role == Role.SPENDER.value:
            # Female donor: show all relevant maximums
            prog_count = len(animal_data.get('daten', []))
            pgf_count = len(animal_data.get('pgf', []))
            op_count = len(animal_data.get('op', []))
            fsh_count = sum(1 for ev in animal_data.get('events', []) if ev.get('typ') == 'fsh')
            
            max_messungen = animal_data.get('max_messungen', 0)
            max_pgf = animal_data.get('max_pgf', 0)
            max_op = animal_data.get('max_op', 0)
            max_fsh = animal_data.get('max_fsh', 0)
            
            if max_messungen > 0:
                stats.append(f"{messages.get('stats.blood_samples', 'Blood Samples')}: {prog_count}/{max_messungen}")
            if max_pgf > 0:
                stats.append(f"{messages.get('stats.pgf', 'PGF')}: {pgf_count}/{max_pgf}")
            if max_op > 0:
                stats.append(f"{messages.get('stats.surgery', 'Surgery')}: {op_count}/{max_op}")
            if max_fsh > 0:
                stats.append(f"{messages.get('stats.fsh', 'FSH')}: {fsh_count}/{max_fsh}")
        
        elif role == Role.AMME.value:
            # Surrogate: show blood samples, PGF, embryo transfers, pregnancies, births
            prog_count = len(animal_data.get('daten', []))
            pgf_count = len(animal_data.get('pgf', []))
            embryo_count = sum(1 for ev in animal_data.get('events', []) 
                             if ev.get('typ') == 'embryo_transfer')
            pregnancy_count = sum(1 for ev in animal_data.get('events', []) 
                                if ev.get('typ') == 'pregnancy')
            birth_count = sum(1 for ev in animal_data.get('events', []) 
                            if ev.get('typ') == 'birth')
            
            max_messungen = animal_data.get('max_messungen', 0)
            max_pgf = animal_data.get('max_pgf', 0)
            max_embryo = animal_data.get('max_embryo', 0)
            max_pregnancies = animal_data.get('max_pregnancies', 0)
            max_geburten = animal_data.get('max_geburten', 0)
            
            if max_messungen > 0:
                stats.append(f"{messages.get('stats.blood_samples', 'Blood Samples')}: {prog_count}/{max_messungen}")
            if max_pgf > 0:
                stats.append(f"{messages.get('stats.pgf', 'PGF')}: {pgf_count}/{max_pgf}")
            if max_embryo > 0:
                stats.append(f"{messages.get('stats.embryo', 'Embryo')}: {embryo_count}/{max_embryo}")
            if max_pregnancies > 0:
                stats.append(f"{messages.get('stats.pregnancy', 'Pregnancy')}: {pregnancy_count}/{max_pregnancies}")
            if max_geburten > 0:
                stats.append(f"{messages.get('stats.birth', 'Birth')}: {birth_count}/{max_geburten}")
        
        elif role == Role.ZUCHTTIER.value:
            # Breeding animals: show based on what's defined
            events = animal_data.get('events', [])
            pregnancy_count = sum(1 for ev in events if ev.get('typ') == 'pregnancy')
            birth_count = sum(1 for ev in events if ev.get('typ') == 'birth')
            
            max_pregnancies = animal_data.get('max_pregnancies', 0)
            max_geburten = animal_data.get('max_geburten', 0)
            
            if max_pregnancies > 0:
                stats.append(f"{messages.get('stats.pregnancy', 'Pregnancy')}: {pregnancy_count}/{max_pregnancies}")
            if max_geburten > 0:
                stats.append(f"{messages.get('stats.birth', 'Birth')}: {birth_count}/{max_geburten}")
        
        return ', '.join(stats) if stats else '-'
    
    def _get_status_localized(self, animal_name: str, messages: Dict[str, str]) -> str:
        """
        Get localized status string for an animal for report generation.
        
        Args:
            animal_name: Name of the animal
            messages: Dictionary of localized messages
            
        Returns:
            Localized status string
        """
        a = self.animals.get(animal_name, {})
        if has_death_date(a):
            return messages.get('status.deceased', 'Deceased')
        role = a.get('rolle')
        now = datetime.now()
        
        # Offspring: only show sick status
        if role == Role.OFFSPRING.value:
            sick = a.get('sick', False)
            return messages.get('status.sick', 'Sick') if sick else ''
        
        # Partners: build status with reproduction field and partner name
        if role == Role.PARTNER.value:
            partner_name = (a.get('partner_von') or '').strip()
            repro_field = (a.get('reproduktionsfeld') or '').strip()
            parts = []
            if a.get('sick', False):
                parts.append(messages.get('status.sick', 'Sick'))
            if repro_field:
                parts.append(repro_field)
            if partner_name:
                paired = messages.get('status.paired_with', 'Paired with')
                parts.append(f"{paired} {partner_name}")
            return " ".join(parts).strip()
        
        # Donor logic (including Samenspender): recovery after OP or Spermaprobe
        if role in (Role.SPENDER.value, Role.SAMENSP.value):
            op_dates = a.get('op', []) or []
            sperm_dates = [s.get('datum') for s in a.get('sperm', []) if s.get('datum')]
            all_dates = op_dates + sperm_dates
            try:
                last_evt = max(all_dates) if all_dates else None
            except Exception:
                last_evt = None
            recovery_days = a.get('recovery_time', DEFAULT_RECOVERY_TIME)
            in_recovery = False
            if last_evt:
                try:
                    in_recovery = (now - last_evt).days <= recovery_days
                except Exception:
                    in_recovery = False
            # during recovery window
            if in_recovery:
                status = messages.get('status.recovery_period', 'Recovery')
                if a.get('sick', False):
                    status += ' + ' + messages.get('status.sick', 'Sick')
            else:
                status = messages.get('status.sick', 'Sick') if a.get('sick', False) else ''
            return status
        
        # Surrogate-specific status logic
        elif role == Role.AMME.value:
            preg_dates   = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'pregnancy']
            birth_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'birth']
            abort_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'abortion']
            embryo_dates = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'embryo_transfer']
            
            last_preg   = max(preg_dates)   if preg_dates   else None
            last_birth  = max(birth_dates)  if birth_dates  else None
            last_abort  = max(abort_dates)  if abort_dates  else None
            last_embryo = max(embryo_dates) if embryo_dates else None
            
            term_dates = [d for d in (last_birth, last_abort) if d]
            last_term = max(term_dates) if term_dates else None
            
            # Determine base status
            if last_preg and (not last_term or last_preg > last_term):
                status = messages.get('status.pregnant', 'Pregnant')
            elif last_abort and (not last_preg or last_abort > last_preg) \
                 and (not last_embryo or last_abort > last_embryo):
                status = ''
            elif last_embryo:
                days_since_transfer = (now - last_embryo).days
                if days_since_transfer <= 30:
                    status = messages.get('status.possibly_pregnant', 'Possibly Pregnant')
                else:
                    status = ''
            elif last_birth and (now - last_birth).days < 90 \
                 and (not last_abort or last_birth > last_abort):
                status = messages.get('status.recent_birth', 'Recent Birth')
            else:
                status = ''
            
            # append sick status if applicable
            if a.get('sick', False):
                if status:
                    status += ' + ' + messages.get('status.sick', 'Sick')
                else:
                    status = messages.get('status.sick', 'Sick')
            return status
        
        # Zuchttiere status
        elif role == Role.ZUCHTTIER.value:
            sex = a.get('sex', '').lower()
            is_female = 'female' in sex or 'weiblich' in sex
            
            if is_female:
                preg_dates   = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'pregnancy']
                birth_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'birth']
                abort_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'abortion']
                
                last_preg  = max(preg_dates)  if preg_dates  else None
                last_birth = max(birth_dates) if birth_dates else None
                last_abort = max(abort_dates) if abort_dates else None
                
                term_dates = [d for d in (last_birth, last_abort) if d]
                last_term = max(term_dates) if term_dates else None
                
                # Determine base status
                if last_preg and (not last_term or last_preg > last_term):
                    status = messages.get('status.pregnant', 'Pregnant')
                elif last_birth and (now - last_birth).days < 90:
                    status = messages.get('status.recent_birth', 'Recent Birth')
                else:
                    status = ''
                
                # Append sick status
                if a.get('sick', False):
                    if status:
                        status += ' + ' + messages.get('status.sick', 'Sick')
                    else:
                        status = messages.get('status.sick', 'Sick')
                return status
            else:
                # Male Zuchttiere
                return messages.get('status.sick', 'Sick') if a.get('sick', False) else ''
        
        # Unknown or unspecified roles
        return ''

    def _audit_report_cell_edit(
        self,
        animal_name: str,
        date_str: str,
        column_name: str,
        previous_value: Any,
        new_value: Any,
        source_function: str,
    ) -> None:
        """Write detailed audit entry for report-cell edits."""
        if previous_value == new_value:
            return
        details = (
            f"function={source_function}; "
            f"animal={animal_name or '<unknown>'}; "
            f"parameter=report.{column_name}[{date_str}]; "
            f"previous={self._audit_value_to_string(previous_value)}; "
            f"new={self._audit_value_to_string(new_value)}"
        )
        self._master_audit("data_edit", "ProgTrack", details)

    def _report_cell_clicked(self, row: int, column: int) -> None:
        """Handle cell clicks in the report table."""
        if column == 0:  # Date column - toggle lock
            if self.read_only_mode:
                self._show_read_only_warning()
                return
            if not self._master_can('reports.write'):
                self._show_permission_denied()
                return
            item = self.report_table.item(row, column)
            if not item:
                return
            
            # Get the date
            year = int(self.report_year_combo.currentText()) if self.report_year_combo.currentText() else datetime.now().year
            month = self.report_month_combo.currentData()
            day = row + 1
            date_str = datetime(year, month, day).strftime(DATE_FORMAT)
            was_locked = date_str in self.report_locked_dates
            
            # Toggle lock state
            if date_str in self.report_locked_dates:
                # Show confirmation dialog before unlocking
                reply = self._show_message_raw(
                    self.messages.get("dialog.unlock_date.title", "Unlock Date"),
                    self.messages.get("dialog.unlock_date.message", "Do you wish to unlock the date?"),
                    "question",
                    buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )

                if reply != QMessageBox.StandardButton.Yes:
                    return  # User cancelled, don't unlock
                
                self.report_locked_dates.remove(date_str)
                
                # Remove ALL data for this date to force complete regeneration
                if date_str in self.report_edits:
                    del self.report_edits[date_str]
                
                # Remove thick border from all cells in row
                for col in range(4):
                    cell_item = self.report_table.item(row, col)
                    if cell_item:
                        cell_item.setData(Qt.ItemDataRole.UserRole, 'unlocked')
                        # Reset to default border
                        cell_item.setBackground(QColor(255, 255, 255))
                
                # Enable editing for columns 2 and 3
                for col in [2, 3]:
                    cell_item = self.report_table.item(row, col)
                    if cell_item:
                        cell_item.setFlags(cell_item.flags() | Qt.ItemFlag.ItemIsEditable)
                
                # Refresh ALL cells with current data from progtrack_daten
                if self.report_current_animal and self.report_current_animal in self.animals:
                    animal_data = self.animals[self.report_current_animal]
                    date = datetime(year, month, day).date()
                    new_daily_data = self._generate_daily_data(self.report_current_animal, animal_data, date)
                    daily_cell = self.report_table.item(row, 1)
                    if daily_cell:
                        daily_cell.setText(new_daily_data)
                    
                    # Clear scores and signatures when unlocking
                    scores_cell = self.report_table.item(row, 2)
                    if scores_cell:
                        scores_cell.setText('')
                    sig_cell = self.report_table.item(row, 3)
                    if sig_cell:
                        sig_cell.setText('')
            else:
                self.report_locked_dates.add(date_str)
                # Add thick border to all cells in row
                for col in range(4):
                    cell_item = self.report_table.item(row, col)
                    if cell_item:
                        cell_item.setData(Qt.ItemDataRole.UserRole, 'locked')
                        # Use background color to simulate thick border
                        cell_item.setBackground(QColor(200, 255, 200))  # Light green
                
                # Disable editing for columns 2 and 3 when locked
                for col in [2, 3]:
                    cell_item = self.report_table.item(row, col)
                    if cell_item:
                        cell_item.setFlags(cell_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                
                # Save current cell contents
                if date_str not in self.report_edits:
                    self.report_edits[date_str] = {}
                for col in range(1, 4):
                    cell_item = self.report_table.item(row, col)
                    if cell_item:
                        col_name = ['daily_data', 'scores', 'signatures'][col - 1]
                        self.report_edits[date_str][col_name] = cell_item.text()

            now_locked = date_str in self.report_locked_dates
            self._audit_report_cell_edit(
                self.report_current_animal or "",
                date_str,
                "locked",
                was_locked,
                now_locked,
                "_report_cell_clicked",
            )
            
            # Debounce save for locking too
            if self.report_save_timer:
                self.report_save_timer.stop()
            
            from PyQt6.QtCore import QTimer
            if not self.report_save_timer:
                self.report_save_timer = QTimer()
                self.report_save_timer.setSingleShot(True)
                self.report_save_timer.timeout.connect(self._save_report_data)
            self.report_save_timer.start(500)  # 500ms delay
    
    def _report_cell_changed(self, item: QTableWidgetItem) -> None:
        """Handle cell content changes and save to JSON with debouncing."""
        if not item or not self.report_current_animal:
            return
        if self.read_only_mode:
            return
        if not self._master_can('reports.write'):
            return
        
        row = item.row()
        column = item.column()
        
        # Only save for editable columns (1, 2, 3)
        if column < 1:
            return
        
        # Get the date for this row
        year = int(self.report_year_combo.currentText()) if self.report_year_combo.currentText() else datetime.now().year
        month = self.report_month_combo.currentData()
        day = row + 1
        date_str = datetime(year, month, day).strftime(DATE_FORMAT)
        
        # Save the edit to memory
        if date_str not in self.report_edits:
            self.report_edits[date_str] = {}

        col_name = ['daily_data', 'scores', 'signatures'][column - 1]
        previous_value = self.report_edits[date_str].get(col_name)
        new_value = item.text()
        self.report_edits[date_str][col_name] = new_value
        self._audit_report_cell_edit(
            self.report_current_animal or "",
            date_str,
            col_name,
            previous_value,
            new_value,
            "_report_cell_changed",
        )
        
        # Debounce save - only save after 500ms of no changes
        if self.report_save_timer:
            self.report_save_timer.stop()
        else:
            from PyQt6.QtCore import QTimer
            self.report_save_timer = QTimer()
            self.report_save_timer.setSingleShot(True)
            self.report_save_timer.timeout.connect(self._save_report_data)
        
        self.report_save_timer.start(500)  # 500ms delay
    
    def _report_cell_double_clicked(self, row: int, column: int) -> None:
        """Handle double-click on a cell to open rich text editor."""
        if self.read_only_mode:
            self._show_read_only_warning()
            return
        if not self._master_can('reports.write'):
            self._show_permission_denied()
            return
        logging.info(f"Double-click detected: row={row}, column={column}")
        
        # Only allow editing columns 1, 2 and 3 (Daily Data, Scores and Signatures)
        if column not in [1, 2, 3]:
            logging.info(f"Column {column} not editable (only 1-3 allowed)")
            return

        item = self.report_table.item(row, column)
        if not item:
            logging.warning(f"No item at row={row}, column={column}")
            return
        
        # Check if cell is locked
        date_item = self.report_table.item(row, 0)
        if date_item and date_item.data(Qt.ItemDataRole.UserRole) == 'locked':
            logging.info(f"Cell at row={row} is locked")
            return  # Don't allow editing locked cells
        
        logging.info(f"Opening rich text editor for row={row}, column={column}")
        # Open rich text editor dialog
        self._open_rich_text_editor(row, column, item)
    
    def _open_rich_text_editor(self, row: int, column: int, item: QTableWidgetItem) -> None:
        """Open a rich text editor dialog for the cell."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QHBoxLayout, QLabel, QToolBar
        from PyQt6.QtGui import QTextCharFormat, QColor, QFont, QAction
        from PyQt6.QtCore import Qt

        previous_html = item.text()
        
        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(self.messages.get("dialog.edit_cell.title", "Edit Cell Content"))
        dialog.setMinimumSize(600, 400)
        layout = QVBoxLayout(dialog)
        
        # Toolbar for formatting
        toolbar = QToolBar()
        
        # Bold button
        bold_action = QAction(self.messages.get("dialog.edit_cell.action.bold", "Bold"), dialog)
        bold_action.setCheckable(True)
        bold_action.triggered.connect(lambda: self._toggle_format(text_edit, 'bold'))
        toolbar.addAction(bold_action)
        
        # Italic button
        italic_action = QAction(self.messages.get("dialog.edit_cell.action.italic", "Italic"), dialog)
        italic_action.setCheckable(True)
        italic_action.triggered.connect(lambda: self._toggle_format(text_edit, 'italic'))
        toolbar.addAction(italic_action)
        
        # Underline button
        underline_action = QAction(self.messages.get("dialog.edit_cell.action.underline", "Underline"), dialog)
        underline_action.setCheckable(True)
        underline_action.triggered.connect(lambda: self._toggle_format(text_edit, 'underline'))
        toolbar.addAction(underline_action)
        
        toolbar.addSeparator()
        
        # Color button
        color_action = QAction(self.messages.get("dialog.edit_cell.action.text_color", "Text Color"), dialog)
        color_action.triggered.connect(lambda: self._apply_text_color(text_edit))
        toolbar.addAction(color_action)
        
        layout.addWidget(toolbar)
        
        # Text edit with rich text support
        text_edit = QTextEdit()
        text_edit.setHtml(item.text())  # Load HTML content
        layout.addWidget(text_edit)
        
        # Buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton(self.messages.get("button.ok", "OK"))
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn = QPushButton(self.messages.get("button.cancel", "Cancel"))
        cancel_btn.clicked.connect(dialog.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.read_only_mode:
                self._show_read_only_warning()
                return
            if not self._master_can('reports.write'):
                self._show_permission_denied()
                return

            # Block signals to prevent triggering itemChanged
            self.report_table.blockSignals(True)
            
            # Save HTML back to the cell to preserve formatting
            # Extract just the body content to avoid DOCTYPE and reduce file size
            full_html = text_edit.toHtml()
            
            # Extract content between <body> tags, or use full HTML if no body found
            import re
            body_match = re.search(r'<body[^>]*>(.*?)</body>', full_html, re.DOTALL | re.IGNORECASE)
            if body_match:
                new_html = body_match.group(1).strip()
            else:
                # Fallback to full HTML if body not found
                new_html = full_html
            
            item.setText(new_html)
            
            # Re-enable signals
            self.report_table.blockSignals(False)
            
            # Manually save the change
            year = int(self.report_year_combo.currentText()) if self.report_year_combo.currentText() else datetime.now().year
            month = self.report_month_combo.currentData()
            day = row + 1
            date_str = datetime(year, month, day).strftime(DATE_FORMAT)
            
            if date_str not in self.report_edits:
                self.report_edits[date_str] = {}
            
            col_name = ['daily_data', 'scores', 'signatures'][column - 1]
            self.report_edits[date_str][col_name] = new_html
            self._audit_report_cell_edit(
                self.report_current_animal or "",
                date_str,
                col_name,
                previous_html,
                new_html,
                "_open_rich_text_editor",
            )
            
            # Trigger save
            if self.report_save_timer:
                self.report_save_timer.stop()
            else:
                from PyQt6.QtCore import QTimer
                self.report_save_timer = QTimer()
                self.report_save_timer.setSingleShot(True)
                self.report_save_timer.timeout.connect(self._save_report_data)
            
            self.report_save_timer.start(500)
    
    def _toggle_format(self, text_edit: 'QTextEdit', format_type: str) -> None:
        """Toggle formatting on selected text."""
        from PyQt6.QtGui import QTextCharFormat
        
        cursor = text_edit.textCursor()
        if not cursor.hasSelection():
            return
        
        char_format = cursor.charFormat()
        
        if format_type == 'bold':
            weight = 400 if char_format.fontWeight() == 700 else 700
            char_format.setFontWeight(weight)
        elif format_type == 'italic':
            char_format.setFontItalic(not char_format.fontItalic())
        elif format_type == 'underline':
            char_format.setFontUnderline(not char_format.fontUnderline())
        
        cursor.setCharFormat(char_format)
    
    def _apply_text_color(self, text_edit: 'QTextEdit') -> None:
        """Apply color to selected text."""
        from PyQt6.QtWidgets import QColorDialog
        from PyQt6.QtGui import QTextCharFormat
        
        cursor = text_edit.textCursor()
        if not cursor.hasSelection():
            return
        
        color = QColorDialog.getColor(parent=text_edit,
                                      title=self.messages.get("dialog.color_picker.title", "Choose Color"))
        if color.isValid():
            char_format = cursor.charFormat()
            char_format.setForeground(color)
            cursor.setCharFormat(char_format)
    
    def _save_report_data(self) -> None:
        """Save report data (locked dates and edits) to JSON file."""
        if self.read_only_mode:
            logging.info("Skipping report data save in READ-ONLY mode")
            return

        # Handle delayed-save races (e.g., user lost edit rights before timer fired)
        mt = getattr(self, 'master_track', None)
        mt_disabled = "master_track" in getattr(self, '_disabled_plugins', set())
        if mt is not None and not mt_disabled and not mt.can("reports.write"):
            logging.info("Skipping report data save without edit permission")
            return

        # Use the current animal being displayed in reports
        if not self.report_current_animal:
            return
        
        animal_name = self.report_current_animal
        
        # Load existing data
        report_file = Path(__file__).parent / "Plugins" / "Animal_Reports" / "animal_report_data.json"
        if report_file.exists():
            try:
                with open(report_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if not content:
                        all_data = {}
                    else:
                        all_data = json.loads(content)
            except (json.JSONDecodeError, ValueError) as e:
                logging.warning(f"Could not parse report data file during save: {e}. Creating new file.")
                all_data = {}
        else:
            all_data = {}
        
        # Update data for this animal
        all_data[animal_name] = {
            'locked_dates': list(self.report_locked_dates),
            'edits': self.report_edits
        }
        
        # Save
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, indent=2, ensure_ascii=False)
    
    def _load_report_data(self, animal_name: str) -> None:
        """Load report data for a specific animal."""
        report_file = Path(__file__).parent / "Plugins" / "Animal_Reports" / "animal_report_data.json"
        if not report_file.exists():
            self.report_locked_dates = set()
            self.report_edits = {}
            return
        
        try:
            with open(report_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                if not content:
                    # Empty file
                    all_data = {}
                else:
                    all_data = json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            logging.warning(f"Could not parse report data file: {e}. Starting with empty data.")
            all_data = {}
        
        animal_data = all_data.get(animal_name, {})
        self.report_locked_dates = set(animal_data.get('locked_dates', []))
        all_edits = animal_data.get('edits', {})
        
        # Only keep edits for locked dates; clear all others to force regeneration
        self.report_edits = {
            date_str: edits 
            for date_str, edits in all_edits.items() 
            if date_str in self.report_locked_dates
        }
    
    def _update_reports_for_animal(self, animal_name: str = None) -> None:
        """Update the reports tab when a new animal is selected, or show splash if none selected."""
        # Only update if reports are enabled and Reports tab has been loaded
        if not self.reports_enabled or self.reports_tab is None:
            return
        
        # If no animal selected, show splash screen
        if not animal_name:
            self._show_reports_splash()
            return
        
        animal_data = self.animals.get(animal_name)
        if not animal_data:
            self._show_reports_splash()
            return
        if not self._animal_visible_to_current_user(animal_data):
            self._show_reports_splash()
            return
        
        # Show content widget (hide splash)
        if hasattr(self, 'reports_stack') and self.reports_stack is not None:
            self.reports_stack.setCurrentWidget(self.reports_content_widget)
        
        # Track current animal
        self.report_current_animal = animal_name
        
        # Load report data for this animal
        self._load_report_data(animal_name)
        
        # Populate year combo with years that have data
        years = set()
        current_year = datetime.now().year
        years.add(current_year)
        
        # Add years from measurements
        for measurement in animal_data.get('daten', []):
            if isinstance(measurement.get('datum'), datetime):
                years.add(measurement['datum'].year)
        
        for pdg in animal_data.get('pdg', []):
            if isinstance(pdg.get('datum'), datetime):
                years.add(pdg['datum'].year)
        
        for weight in animal_data.get('gewicht', []):
            if isinstance(weight.get('datum'), datetime):
                years.add(weight['datum'].year)
        
        for sperm in animal_data.get('sperm', []):
            if isinstance(sperm.get('datum'), datetime):
                years.add(sperm['datum'].year)
        
        for event in animal_data.get('events', []):
            if isinstance(event.get('datum'), datetime):
                years.add(event['datum'].year)
        
        # Add birth year if available
        birth_date_str = animal_data.get('birth_date', '')
        if birth_date_str:
            try:
                birth_date = datetime.strptime(birth_date_str, DATE_FORMAT)
                years.add(birth_date.year)
            except (TypeError, ValueError):
                logging.debug("Skipping invalid birth date while collecting report years: %r", birth_date_str)
        
        # Populate year combo
        self.report_year_combo.clear()
        for year in sorted(years, reverse=True):
            self.report_year_combo.addItem(str(year))
        
        # Set current month as default
        current_month = datetime.now().month
        self.report_month_combo.setCurrentIndex(current_month - 1)
        
        # Update the table
        self._update_report_table()

    # ------------------------
    # 7.16 Refresh Animal List
    #     Update the sidebar list based on current animal selection and filters.
    # ------------------------
    def _refresh_list(self, update_tab_visibility: bool = False, force_heritage_visible: bool = False) -> None:
        """Refresh the animal list based on current filter and selections.
        
        Args:
            update_tab_visibility: Whether to update category tab visibility.
            force_heritage_visible: If True, force show heritage-only animals regardless of current tab.
        """
        # Check if UI is initialized
        if not hasattr(self, 'lst') or self.lst is None:
            return

        if hasattr(self, 'category_tab') and self.category_tab is not None:
            # Only update tab visibility when explicitly requested (e.g. after add/edit/delete)
            # This is expensive as it iterates over all animals
            if update_tab_visibility:
                before_idx = self.category_tab.currentIndex()
                self._update_category_tab_visibility()
                if self.category_tab.currentIndex() != before_idx:
                    return
        
        # remember what was selected (use stored key, not display text)
        sel = [item.data(Qt.ItemDataRole.UserRole) for item in self.lst.selectedItems()]
        if sel:
            self.selected_animals = [n for n in sel if n and n in self.animals]
        # Track selected heritage-only animals separately
        self._selected_heritage_only = []
        if getattr(self, 'has_heritage_plugin', False):
            heritage_plugin = getattr(self, 'heritage_plugin', None)
            if heritage_plugin is not None:
                self._selected_heritage_only = [
                    n for n in sel if n and n not in self.animals and heritage_plugin.store.is_heritage_only(n)
                ]

        # rebuild list based on the current tab index
        idx = self.category_tab.currentIndex()
        labels = ["♀", "♂", "👶", "🐾", "⚤", "💡", self.messages["sidebar.filter.all"]]
        cat = labels[idx] if idx < len(labels) else self.messages["sidebar.filter.all"]
        # Cache steroid_active result to avoid repeated calls
        steroid_active = self._is_steroid_track_active()
        # Cache phase filter value to avoid recomputing inside loop
        phase_filter_val = None
        if cat == "♀" and steroid_active:
            phase_filter_val = next((phase_val for phase_val, btn in self.phase_buttons.items() if btn.isChecked()), None)
        # Filter by rolle only
        if cat == "♀":
            cat_pred = lambda d: d.get("rolle") in (Role.SPENDER.value, Role.AMME.value)
        elif cat == "♂":
            cat_pred = lambda d: d.get("rolle") == Role.SAMENSP.value
        elif cat == "👶":
            cat_pred = lambda d: d.get("rolle") == Role.OFFSPRING.value
        elif cat == "🐾":
            cat_pred = lambda d: d.get("rolle") == Role.PARTNER.value
        elif cat == "⚤":
            cat_pred = lambda d: d.get("rolle") == Role.ZUCHTTIER.value
        elif cat == "💡":
            cat_pred = lambda d: d.get("rolle") == Role.EXPERIMENTAL.value
        elif cat == self.messages["sidebar.filter.all"]:
            cat_pred = (lambda d: True) if steroid_active else (lambda d: d.get("rolle") != Role.SAMENSP.value)
        else:
            cat_pred = lambda d: True

        self.lst.clear()
        visible_count = 0
        # Cache plugin states outside the loop for performance
        project_filter = getattr(self, '_current_project_filter', 'All')
        has_projects_plugin = self.has_projects_plugin and self.projects_plugin is not None
        active_species = getattr(self.projects_plugin, 'active_species', None) if has_projects_plugin else None
        name_filter = self._current_animal_name_filter_text()
        unrestricted_projects, visible_projects = self._project_visibility_scope()
        show_all_animals_tab = cat == self.messages["sidebar.filter.all"]

        def _visible_in_animal_sidebar(data: Dict[str, Any]) -> bool:
            if show_all_animals_tab:
                return True
            return animal_visible_by_project_scope(data, unrestricted_projects, visible_projects)

        has_medi_track = getattr(self, 'has_medi_track_plugin', False)
        medi_tab_open = False
        medi_plugin = None
        medi_widget = None
        active_medi_filter = 'all'
        if has_medi_track:
            _medi_disabled = "medi_track" in getattr(self, '_disabled_plugins', set())
            if not _medi_disabled:
                _cur_tab = self.main_tabs.currentWidget() if hasattr(self, 'main_tabs') else None
                medi_tab_open = (
                    _cur_tab is not None and (
                        _cur_tab is getattr(self, 'medi_track_tab', None) or
                        _cur_tab is getattr(self, 'medi_track_tab_placeholder', None)
                    )
                )
                if medi_tab_open:
                    medi_plugin = getattr(self, 'medi_track_plugin', None)
                    medi_widget = getattr(self, 'medi_track_widget', None)
                    if medi_widget is not None and hasattr(medi_widget, 'active_filter'):
                        active_medi_filter = medi_widget.active_filter()
        
        for name, data in sorted(self.animals.items()):
            if not _visible_in_animal_sidebar(data):
                continue
            # Filter by rolle
            if not cat_pred(data):
                continue
            # if female and Steroid_track active, apply phase filter
            if cat == "♀" and steroid_active and phase_filter_val is not None:
                vals = data.get('daten', [])
                if vals and isinstance(vals[-1], dict) and 'datum' in vals[-1]:
                    phase = self.phase_from_combined_or_blood(name, vals[-1]['datum'])
                    if phase != phase_filter_val:
                        continue
            
            # Apply project filter (if ProjectsTrack plugin is active)
            if project_filter != 'All':
                animal_project = data.get('project', '')
                if animal_project != project_filter:
                    continue

            # Apply Medi Track filter only when: installed, not disabled, AND tab is open
            if medi_tab_open and medi_plugin is not None:
                if not medi_plugin.matches_filter(name, active_medi_filter):
                    continue

            # Apply species filter (if ProjectsTrack plugin has a species selected)
            if active_species:
                if data.get('species', '') != active_species:
                    continue

            if not animal_matches_name_filter(name, data, name_filter):
                continue

            # build item + status
            status = self._get_status(name)
            display_name = self._display_name(name)
            identity_label = animal_identity_label(name, data)

            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, name)
            row_widget = QWidget()
            h = QHBoxLayout(row_widget)
            
            # Add the animal name label
            name_label = QLabel(display_name)
            name_label.setToolTip(identity_label)
            h.addWidget(name_label)
            
            # Add the status label
            status_lbl = QLabel(status or "")
            status_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            h.addStretch()
            h.addWidget(status_lbl)
            h.setContentsMargins(4, 2, 4, 2)
            row_widget.setLayout(h)
            
            # Set text color based on rolle only
            role = data.get('rolle')
            text_color = QColor('black')  # default color
            
            if role is None or role == Role.UNKNOWN.value:
                # unclassified animals are rendered grey until a role is assigned
                text_color = QColor('lightgray')
            elif role == Role.SPENDER.value:
                text_color = QColor('deeppink')
            elif role == Role.AMME.value:
                text_color = QColor('mediumpurple')
            elif role == Role.SAMENSP.value:
                text_color = QColor('black')
            elif role == Role.OFFSPRING.value:
                # Offspring color based on sex
                sex = data.get('sex', '').lower()
                # Include English, German, and Russian indicators.
                # Check "female" first to avoid matching "male" substring in "female".
                is_female = (
                    'female' in sex or
                    'weiblich' in sex or
                    'жен' in sex  # e.g. "женский"
                )
                is_male = (
                    'male' in sex or
                    'männlich' in sex or
                    'муж' in sex  # e.g. "мужской"
                )

                if is_female:
                    text_color = QColor('hotpink')  # rosa/pink color
                elif is_male:
                    text_color = QColor('blue')
                else:
                    text_color = QColor('gray')  # Unknown sex
            elif role == Role.PARTNER.value:
                # Partner color based on sex: darkorange for male, darker chocolate for female
                sex = data.get('sex', '').lower()
                is_female = (
                    'female' in sex or
                    'weiblich' in sex or
                    'жен' in sex
                )
                is_male = (
                    'male' in sex or
                    'männlich' in sex or
                    'муж' in sex
                )

                if is_female:
                    text_color = QColor('#D2691E')  # chocolate (darker orange)
                elif is_male:
                    text_color = QColor('darkorange')
                else:
                    text_color = QColor('gray')  # Unknown sex
            elif role == Role.ZUCHTTIER.value:
                # Zuchttiere color based on sex: dark blue for male, dark rosa for female
                sex = data.get('sex', '').lower()
                # Include English, German, and Russian indicators.
                is_female = (
                    'female' in sex or
                    'weiblich' in sex or
                    'жен' in sex
                )
                is_male = (
                    'male' in sex or
                    'männlich' in sex or
                    'муж' in sex
                )

                if is_female:
                    text_color = QColor('#C71585')  # mediumvioletred (dark rosa)
                elif is_male:
                    text_color = QColor('#00008B')  # darkblue
                else:
                    text_color = QColor('gray')  # Unknown sex
            elif role == Role.EXPERIMENTAL.value:
                sex = data.get('sex', '').lower()
                is_female = 'female' in sex or 'weiblich' in sex or 'жен' in sex
                is_male   = 'male' in sex or 'männlich' in sex or 'муж' in sex
                if is_female:
                    text_color = QColor('#FF7788')
                elif is_male:
                    text_color = QColor('#00CCAA')
                else:
                    text_color = QColor('#00AAAA')
            
            # Apply the color to the name label
            name_label.setStyleSheet(f'color: {text_color.name()};')
            
            # Set the widget as the item's widget
            item.setSizeHint(row_widget.sizeHint())
            item.setToolTip(identity_label)
            self.lst.addItem(item)
            self.lst.setItemWidget(item, row_widget)
            
            # Selection match must use the base name (without appended status)
            if name in self.selected_animals:
                item.setSelected(True)
            visible_count += 1

        # --- Show heritage-only animals below separator when Heritage Track tab is active and in All tab ---
        has_heritage_track = getattr(self, 'has_heritage_plugin', False)
        if has_heritage_track:
            _heritage_disabled = "heritage_track" in getattr(self, '_disabled_plugins', set())
            if not _heritage_disabled:
                _cur_tab = self.main_tabs.currentWidget() if hasattr(self, 'main_tabs') else None
                _heritage_tab = getattr(self, 'heritage_track_tab', None)
                _heritage_placeholder = getattr(self, 'heritage_track_tab_placeholder', None)
                heritage_tab_open = (
                    _cur_tab is not None and
                    (_cur_tab is _heritage_tab or _cur_tab is _heritage_placeholder)
                )
                # Show heritage-only animals when Heritage Track is active or forced
                # (regardless of which category tab is selected)
                if (heritage_tab_open or force_heritage_visible) and cat == self.messages["sidebar.filter.all"]:
                    heritage_plugin = getattr(self, 'heritage_plugin', None)
                    if heritage_plugin is not None:
                        all_heritage_entries = heritage_plugin.store.get_all_entries()
                        heritage_only_animals = {
                            name: data for name, data in all_heritage_entries.items()
                            if heritage_plugin.store.is_heritage_only(name) and name not in self.animals
                        }
                        if active_species:
                            heritage_only_animals = {
                                name: data for name, data in heritage_only_animals.items()
                                if heritage_plugin.store.get_species(name) == active_species
                            }
                        if heritage_only_animals:
                            # Add separator
                            hsep_item = QListWidgetItem('\u2500' * 24)
                            hsep_item.setFlags(Qt.ItemFlag.NoItemFlags)
                            hsep_item.setData(Qt.ItemDataRole.UserRole, '__heritage_sep__')
                            self.lst.addItem(hsep_item)
                            # Add heritage-only animals
                            for h_name in sorted(heritage_only_animals.keys()):
                                h_data = heritage_only_animals[h_name]
                                # Simple text item with heritage indicator - no custom widget to block events
                                display_text = f"{h_name} (H)"
                                h_item = QListWidgetItem(display_text)
                                h_item.setData(Qt.ItemDataRole.UserRole, h_name)
                                # Enable selection for the item
                                h_item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                                # Dark grey italic styling via item foreground and font
                                h_item.setForeground(QColor('#444444'))
                                h_font = QFont()
                                h_font.setItalic(True)
                                h_item.setFont(h_font)
                                self.lst.addItem(h_item)
                                # Select if previously selected (check both current sel and tracked heritage selections)
                                if h_name in sel or h_name in getattr(self, '_selected_heritage_only', []):
                                    h_item.setSelected(True)

        # --- Update hidden cmb_arch for backward compat ---
        self.cmb_arch.clear()
        if not self.archived:
            self.cmb_arch.addItem(self.messages.get("archived.no_animals", "No archived animals"))
        else:
            self.cmb_arch.addItems(sorted(self.archived.keys()))

        # --- Show archived animals below separator when checkbox is checked ---
        if getattr(self, 'chk_show_archived', None) and self.chk_show_archived.isChecked() and self.archived:
            def _arch_filter(name: str, data: dict) -> bool:
                if not _visible_in_animal_sidebar(data):
                    return False
                if not cat_pred(data):
                    return False
                # Apply project filter
                if project_filter != 'All':
                    if data.get('project', '') != project_filter:
                        return False
                # Apply Medi Track filter when tab is open
                if getattr(self, 'has_medi_track_plugin', False):
                    _medi_disabled = "medi_track" in getattr(self, '_disabled_plugins', set())
                    if not _medi_disabled:
                        _cur_tab = self.main_tabs.currentWidget() if hasattr(self, 'main_tabs') else None
                        _medi_tab_open = (
                            _cur_tab is not None and (
                                _cur_tab is getattr(self, 'medi_track_tab', None) or
                                _cur_tab is getattr(self, 'medi_track_tab_placeholder', None)
                            )
                        )
                        if _medi_tab_open:
                            medi_plugin = getattr(self, 'medi_track_plugin', None)
                            medi_widget = getattr(self, 'medi_track_widget', None)
                            if medi_plugin is not None and medi_widget is not None:
                                active_medi_filter = medi_widget.active_filter() if hasattr(medi_widget, 'active_filter') else 'all'
                                if not medi_plugin.matches_filter(name, active_medi_filter):
                                    return False
                # Apply species filter
                if self.has_projects_plugin and self.projects_plugin is not None:
                    active_species = getattr(self.projects_plugin, 'active_species', None)
                    if active_species:
                        if data.get('species', '') != active_species:
                            return False
                if not animal_matches_name_filter(name, data, name_filter):
                    return False
                return True
            arch_to_show = {k: v for k, v in self.archived.items() if _arch_filter(k, v)}
            if arch_to_show:
                sep_item = QListWidgetItem('\u2500' * 24)
                sep_item.setFlags(Qt.ItemFlag.NoItemFlags)
                sep_item.setData(Qt.ItemDataRole.UserRole, '__archived_sep__')
                self.lst.addItem(sep_item)
                for arch_name in sorted(arch_to_show.keys()):
                    arch_identity_label = animal_identity_label(arch_name, arch_to_show[arch_name])
                    arch_item = QListWidgetItem()
                    arch_row = QWidget()
                    arch_h = QHBoxLayout(arch_row)
                    arch_name_lbl = QLabel(self._display_name(arch_name))
                    arch_name_lbl.setToolTip(arch_identity_label)
                    arch_name_lbl.setStyleSheet('color: black;')
                    arch_h.addWidget(arch_name_lbl)
                    arch_h.addStretch()
                    arch_h.setContentsMargins(4, 2, 4, 2)
                    arch_row.setLayout(arch_h)
                    arch_item.setSizeHint(arch_row.sizeHint())
                    arch_item.setData(Qt.ItemDataRole.UserRole, '__archived__' + arch_name)
                    arch_item.setToolTip(arch_identity_label)
                    self.lst.addItem(arch_item)
                    self.lst.setItemWidget(arch_item, arch_row)

        can_archive = self._master_can('core.archive_animals')
        can_delete  = self._master_can('core.delete_animals')
        can_edit    = self._master_can('core.edit_animal_core')
        selected_arch = getattr(self, '_selected_archived', [])
        self.btn_restore.setEnabled(bool(selected_arch) and can_archive)
        self.btn_delete.setEnabled(bool(selected_arch) and can_delete)
        self.btn_edit.setEnabled(bool(self.selected_animals) and can_edit)
        if hasattr(self, 'btn_edit_animal'):
            self.btn_edit_animal.setEnabled(bool(self.selected_animals) and can_edit)

        logging.info(f"Refreshed list with {visible_count} visible animals")

    def _apply_project_filter(self, project_name):
        """Filter animal list by project (called by ProjectsTrack plugin).
        
        Args:
            project_name: "All" or specific project name to filter by
        """
        # Store current filter
        self._current_project_filter = project_name
        
        # Refresh the animal list display
        self._refresh_list()

        pt_w = getattr(self, 'project_track_widget', None)
        if pt_w and project_name and project_name != 'All':
            pt_w.select_project(project_name)
        
        logging.info(f"Project filter applied: {project_name}")

    def _medi_filter_changed(self, filter_key: str) -> None:
        """Called by MediTrackWidget when a medical status filter button is clicked.

        Args:
            filter_key: one of 'all', 'sick', 'ever_sick', 'abnormal', 'ever_abnormal'
        """
        self._refresh_list()
        logging.info(f"Medi Track filter changed: {filter_key}")

    def _on_select(self) -> None:
        """Handle list selection changes: update selection and replot."""
        # Check if UI is initialized
        if not hasattr(self, 'lst') or self.lst is None:
            return

        # Read real animal key from UserRole (display text may show _base_name)
        selected_names = []
        selected_heritage_only = []
        for item in self.lst.selectedItems():
            key = item.data(Qt.ItemDataRole.UserRole)
            if key and key in self.animals:
                selected_names.append(key)
            elif key:
                # Check if this is a heritage-only animal
                if getattr(self, 'has_heritage_plugin', False):
                    heritage_plugin = getattr(self, 'heritage_plugin', None)
                    if heritage_plugin is not None and heritage_plugin.store.is_heritage_only(key):
                        selected_heritage_only.append(key)

        # Store heritage-only selections separately
        self._selected_heritage_only = selected_heritage_only

        # Enforce selection limit based on active tab
        # Reports tab: only allow single selection
        # Non-Heritage contexts: allow multiple selections up to MAX_SELECTED_ANIMALS
        current_tab_widget = None
        if hasattr(self, 'main_tabs') and self.main_tabs is not None and self.main_tabs.count() > 0:
            current_tab_widget = self.main_tabs.currentWidget()
        is_reports_tab = (
            hasattr(self, 'reports_enabled')
            and self.reports_enabled
            and (
                current_tab_widget is getattr(self, 'reports_tab', None)
                or current_tab_widget is getattr(self, 'reports_tab_placeholder', None)
            )
        )
        _heritage_tab_widget = getattr(self, 'heritage_track_tab', None)
        _heritage_placeholder_widget = getattr(self, 'heritage_track_tab_placeholder', None)
        is_heritage_tab = (
            getattr(self, 'has_heritage_plugin', False)
            and current_tab_widget is not None
            and (current_tab_widget is _heritage_tab_widget or current_tab_widget is _heritage_placeholder_widget)
        )
        is_heritage_window_visible = False
        if getattr(self, 'has_heritage_plugin', False):
            try:
                heritage_plugin = getattr(self, 'heritage_plugin', None)
                heritage_window = getattr(heritage_plugin, 'window', None) if heritage_plugin is not None else None
                is_heritage_window_visible = bool(heritage_window is not None and heritage_window.isVisible())
            except Exception:
                is_heritage_window_visible = False
        is_heritage_context = is_heritage_tab or is_heritage_window_visible
        
        if is_reports_tab and len(selected_names) > 1:
            # In Reports tab, keep only the last selected animal
            selected_names = selected_names[-1:]
            # Deselect all others in UI
            for i in range(self.lst.count()):
                it = self.lst.item(i)
                widget = self.lst.itemWidget(it)
                if widget:
                    for child in widget.children():
                        if isinstance(child, QLabel):
                            t = child.text()
                            if t and t in self.animals:
                                it.setSelected(t in selected_names)
                                break
                else:
                    txt = it.text()
                    base = txt.rsplit(" (", 1)[0] if " (" in txt and txt.endswith(")") else txt
                    it.setSelected(base in selected_names)
        elif not is_heritage_context and len(selected_names) > MAX_SELECTED_ANIMALS:
            # Enforce MAX_SELECTED_ANIMALS outside Heritage Track context.
            selected_names = selected_names[:MAX_SELECTED_ANIMALS]
            # Keep UI in sync: deselect extras
            still_ok = set(selected_names)
            for i in range(self.lst.count()):
                it = self.lst.item(i)
                txt = it.text()
                base = txt.rsplit(" (", 1)[0] if " (" in txt and txt.endswith(")") else txt
                it.setSelected(base in still_ok)

        self.selected_animals = selected_names
        logging.info(f"Selected animals: {self.selected_animals}")

        if hasattr(self, 'category_tab') and hasattr(self, 'btn_load_sperm'):
            self._apply_sidebar_button_visibility_for_category(self.category_tab.currentIndex())

        # Detect archived animal selections and update restore/delete button states
        archived_selected = []
        for item in self.lst.selectedItems():
            user_data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(user_data, str) and user_data.startswith('__archived__'):
                archived_selected.append(user_data[len('__archived__'):])
        self._selected_archived = archived_selected
        if hasattr(self, 'btn_restore') and hasattr(self, 'btn_delete'):
            can_arch = self._master_can('core.archive_animals')
            can_del  = self._master_can('core.delete_animals')
            self.btn_restore.setEnabled(bool(archived_selected) and can_arch)
            self.btn_delete.setEnabled(bool(archived_selected) and can_del)

        # Enable/disable Edit button based on selection AND permissions
        if hasattr(self, "btn_edit"):
            can_edit = self._master_can('core.edit_animal_core')
            self.btn_edit.setEnabled(bool(self.selected_animals) and can_edit)
        if hasattr(self, 'btn_edit_animal'):
            can_edit = self._master_can('core.edit_animal_core')
            self.btn_edit_animal.setEnabled(bool(self.selected_animals) and can_edit)

        # Determine which tab is currently active
        current_tab_index = self.main_tabs.currentIndex() if hasattr(self, 'main_tabs') else 0
        active_tab_widget = self.main_tabs.currentWidget() if hasattr(self, 'main_tabs') and self.main_tabs is not None else None

        # Update only the active tab to avoid unnecessary background processing
        if current_tab_index == 0:
            # Plots tab is active - update plot
            self._plot_selected()

        elif (
            active_tab_widget is getattr(self, 'reports_tab', None)
            and hasattr(self, 'reports_tab')
            and self.reports_tab is not None
        ):
            # Reports tab is active and loaded - update reports
            if self.selected_animals:
                self._update_reports_for_animal(self.selected_animals[-1])
            else:
                self._update_reports_for_animal(None)

        elif (
            active_tab_widget is getattr(self, 'flow_track_tab', None)
            and hasattr(self, 'flow_track_tab')
            and self.flow_track_tab is not None
        ):
            # Flow Track tab is active - refresh visualization
            if hasattr(self, 'flow_track_widget'):
                self.flow_track_widget._redraw_canvas()

        elif (
            getattr(self, 'has_heritage_plugin', False)
            and active_tab_widget is not None
            and (
                active_tab_widget is getattr(self, 'heritage_track_tab', None)
                or active_tab_widget is getattr(self, 'heritage_track_tab_placeholder', None)
            )
        ):
            # Heritage Track tab is active - refresh visualization
            if hasattr(self, 'heritage_track_widget') and self.heritage_track_widget is not None:
                if hasattr(self.heritage_track_widget, 'refresh_graph'):
                    self.heritage_track_widget.refresh_graph()

        # Refresh Heritage_Track graph if plugin window is visible.
        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None) is not None:
            if hasattr(self.heritage_plugin, 'refresh_if_visible'):
                self.heritage_plugin.refresh_if_visible()

        # Refresh Cage_Track if tab is active
        if getattr(self, 'has_cage_track_plugin', False):
            active_tab = self.main_tabs.currentWidget()
            if (
                active_tab is getattr(self, 'cage_track_tab', None)
                and getattr(self, 'cage_track_widget', None) is not None
            ):
                if hasattr(self.cage_track_widget, 'on_animal_selected'):
                    # Filter out heritage-only animals - they should stay in Heritage Track only
                    cage_animals = [n for n in self.selected_animals if n in self.animals]
                    self.cage_track_widget.on_animal_selected(cage_animals)

        # Refresh Cage_Track plugin if visible.
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None) is not None:
            if hasattr(self.cage_track_plugin, 'refresh_if_visible'):
                self.cage_track_plugin.refresh_if_visible()

        # Notify Medi_Track plugin of current selection.
        if getattr(self, 'has_medi_track_plugin', False) and getattr(self, 'medi_track_plugin', None) is not None:
            if getattr(self, 'medi_track_tab', None) is not None:
                # Filter out heritage-only animals - they should stay in Heritage Track only
                medi_animals = [n for n in self.selected_animals if n in self.animals]
                self.medi_track_plugin.on_animal_selected(medi_animals)

    # ------------------------
    # 7.16.1 Pregnancy status
    #     Update the sidebar list based on current animal status.
    # ------------------------
    def _get_status_at_date(self, name: str, at_date: datetime.date) -> str:
        """
        Compute and return a status string for the given animal name at a specific date.
        This is used for historical report generation.
        """
        a = self.animals.get(name, {})
        if has_death_date(a):
            return compact_status_with_death_priority(a)
        role = a.get('rolle')
        
        # Convert date to datetime for comparison
        check_datetime = datetime.combine(at_date, datetime.min.time())
        
        # Offspring: only show sick status at that date
        if role == Role.OFFSPRING.value:
            # We don't have historical sick data, so skip for historical dates
            return ''
        
        # Partners: build status with reproduction field
        if role == Role.PARTNER.value:
            repro_field = (a.get('reproduktionsfeld') or '').strip()
            return repro_field if repro_field else ''
        
        # For donor animals
        if role in (Role.SPENDER.value, Role.SAMENSP.value):
            return ''  # No historical status for donors
        
        # For surrogate animals - calculate historical status
        elif role == Role.AMME.value:
            preg_dates   = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'pregnancy' and isinstance(ev.get('datum'), datetime)]
            birth_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'birth' and isinstance(ev.get('datum'), datetime)]
            abort_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'abortion' and isinstance(ev.get('datum'), datetime)]
            embryo_dates = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'embryo_transfer' and isinstance(ev.get('datum'), datetime)]
            
            # Filter events to only those that occurred on or before the check date
            preg_dates_before   = [d for d in preg_dates if d <= check_datetime]
            birth_dates_before  = [d for d in birth_dates if d <= check_datetime]
            abort_dates_before  = [d for d in abort_dates if d <= check_datetime]
            embryo_dates_before = [d for d in embryo_dates if d <= check_datetime]
            
            last_preg   = max(preg_dates_before)   if preg_dates_before   else None
            last_birth  = max(birth_dates_before)  if birth_dates_before  else None
            last_abort  = max(abort_dates_before)  if abort_dates_before  else None
            last_embryo = max(embryo_dates_before) if embryo_dates_before else None
            
            # Compute most recent termination (birth or abort)
            term_dates = [d for d in (last_birth, last_abort) if d]
            last_term = max(term_dates) if term_dates else None
            
            # Determine status at this specific date (priority order: pregnant > recent birth > embryo transfer > abort)
            if last_preg and (not last_term or last_preg > last_term):
                status = '☉'  # pregnant
            elif last_birth and (check_datetime - last_birth).days < 90 \
                 and (not last_abort or last_birth > last_abort):
                status = 'Oo'  # recent birth (within 90 days)
            elif last_embryo and (not last_term or last_embryo > last_term):
                # Embryo transfer logic for historical date (only if no pregnancy/birth/abort after transfer)
                days_since_transfer = (check_datetime - last_embryo).days
                if days_since_transfer <= 30:
                    status = '☉?'  # possibly pregnant after embryo transfer
                else:
                    status = ''  # not pregnant (transfer failed)
            elif last_abort and (not last_preg or last_abort > last_preg) \
                 and (not last_embryo or last_abort > last_embryo) \
                 and (not last_birth or last_abort > last_birth):
                status = ''  # abort (most recent event)
            else:
                status = ''  # fallback
                
            return status
        
        elif role == Role.ZUCHTTIER.value:
            sex = a.get('sex', '').lower()
            is_female = 'female' in sex or 'weiblich' in sex
            
            if is_female:
                preg_dates   = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'pregnancy' and isinstance(ev.get('datum'), datetime)]
                birth_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'birth' and isinstance(ev.get('datum'), datetime)]
                abort_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'abortion' and isinstance(ev.get('datum'), datetime)]
                
                # Filter to events before the check date
                preg_dates_before  = [d for d in preg_dates if d <= check_datetime]
                birth_dates_before = [d for d in birth_dates if d <= check_datetime]
                abort_dates_before = [d for d in abort_dates if d <= check_datetime]
                
                last_preg  = max(preg_dates_before)  if preg_dates_before  else None
                last_birth = max(birth_dates_before) if birth_dates_before else None
                last_abort = max(abort_dates_before) if abort_dates_before else None
                
                term_dates = [d for d in (last_birth, last_abort) if d]
                last_term = max(term_dates) if term_dates else None
                
                # Determine base status
                if last_preg and (not last_term or last_preg > last_term):
                    status = '☉'  # pregnant
                elif last_birth and (check_datetime - last_birth).days < 90:
                    status = 'Oo'  # recent birth
                else:
                    status = ''
            else:
                status = ''
                
            return status
        else:
            return ''
    
    def _get_status(self, name: str) -> str:
        """
        Compute and return a status string for the given animal name.

        For donor animals (Role.SPENDER), the status is "+" when
        within the configured recovery period following the most recent
        operation or when manually marked sick. Outside the recovery
        period, the manual flag governs whether the plus sign is
        displayed. Donors have no other status symbols.

        For surrogate animals (Role.AMME), the original status logic
        applies (☉, ☉?, Oo, O). If the surrogate is manually marked
        sick, a "+" is appended to whatever base symbol is returned.

        Animals with other or unspecified roles return an empty status.
        """
        a = self.animals.get(name, {})
        role = a.get('rolle')
        # define now early so partners can also use the injection-tag block later
        now = datetime.now()


        # Offspring: only show sick status, genotype is shown in separate field
        if role == Role.OFFSPRING.value:
            sick = a.get('sick', False)
            abnormal = a.get('abnormal_current', False)
            # Genotype is excluded from status - it's shown in a separate field in the UI
            markers = ('!' if abnormal else '') + ('+' if sick else '')
            if a.get('in_experiment', False) and self._is_projects_track_active():
                markers += ' ■'
            return markers
        # Partners: build status with reproduction field and partner name
        if role == Role.PARTNER.value:
            partner_name = (a.get('partner_von') or '').strip()
            repro_field = (a.get('reproduktionsfeld') or '').strip()
            parts = []
            if a.get('abnormal_current', False):
                parts.append('!')
            if a.get('sick', False):
                parts.append("+")
            if repro_field:
                parts.append(repro_field)
            if partner_name:
                parts.append(f"♥ {partner_name}")
            status = " ".join(parts).strip()
        else:
            # Donor logic (including Samenspender): recovery after OP or Spermaprobe
            if role in (Role.SPENDER.value, Role.SAMENSP.value):
                # collect all relevant event dates: ops + sperm samples
                op_dates = a.get('op', []) or []
                sperm_dates = [s.get('datum') for s in a.get('sperm', []) if s.get('datum')]
                all_dates = op_dates + sperm_dates
                try:
                    last_evt = max(all_dates) if all_dates else None
                except Exception:
                    last_evt = None
                recovery_days = a.get('recovery_time', DEFAULT_RECOVERY_TIME)
                in_recovery = False
                if last_evt:
                    try:
                        in_recovery = (now - last_evt).days <= recovery_days
                    except Exception:
                        in_recovery = False
                # during recovery window, show special text
                if in_recovery:
                    status = self.messages.get('status.recovery_period', 'Recovery')
                    # Append abnormal/sick markers during recovery
                    if a.get('abnormal_current', False):
                        status += '!'
                    if a.get('sick', False):
                        status += '+'
                else:
                    # outside recovery period, sick/abnormal status applies
                    markers = ('!' if a.get('abnormal_current', False) else '') + ('+' if a.get('sick', False) else '')
                    status = markers
            elif role == Role.AMME.value:
                # Surrogate-specific status logic (☉, ☉?, Oo, O)
                preg_dates   = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'pregnancy']
                birth_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'birth']
                abort_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'abortion']
                embryo_dates = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'embryo_transfer']

                last_preg   = max(preg_dates)   if preg_dates   else None
                last_birth  = max(birth_dates)  if birth_dates  else None
                last_abort  = max(abort_dates)  if abort_dates  else None
                last_embryo = max(embryo_dates) if embryo_dates else None

                # compute most recent termination (birth or abort)
                term_dates = [d for d in (last_birth, last_abort) if d]
                last_term = max(term_dates) if term_dates else None

                # compute current phase (based on last blood/combined date)
                vals = a.get('daten', [])
                if vals and isinstance(vals[-1], dict) and 'datum' in vals[-1]:
                    current_phase = self.phase_from_combined_or_blood(name, vals[-1]['datum'])
                else:
                    current_phase = None

                # Determine base status (priority order: pregnant > recent birth > embryo transfer > abort)
                if last_preg and (not last_term or last_preg > last_term):
                    status = '☉'              # pregnant
                elif last_birth and (now - last_birth).days < 90 \
                     and (not last_abort or last_birth > last_abort):
                    status = 'Oo'             # recent birth (within 90 days)
                elif last_embryo and (not last_term or last_embryo > last_term):
                    # Embryo transfer logic (only if no pregnancy/birth/abort after transfer):
                    # - If pregnancy confirmed after transfer -> handled by first condition above
                    # - If birth after transfer -> handled by second condition above
                    # - If within 30 days and no pregnancy yet -> possibly pregnant
                    # - If more than 30 days and no pregnancy -> resolved, not pregnant
                    days_since_transfer = (now - last_embryo).days
                    if days_since_transfer <= 30:
                        # Still within 30-day window, no pregnancy confirmed yet
                        status = '☉?'             # possibly pregnant after embryo transfer
                    else:
                        # More than 30 days since transfer, no pregnancy confirmed
                        # Status resolved: embryo transfer failed
                        status = ''               # not pregnant (transfer failed)
                elif last_abort and (not last_preg or last_abort > last_preg) \
                     and (not last_embryo or last_abort > last_embryo) \
                     and (not last_birth or last_abort > last_birth):
                    status = ''               # abort (most recent event)
                else:
                    status = ''               # fallback

                # append abnormal/sick markers for surrogates
                if a.get('abnormal_current', False):
                    status += '!'
                if a.get('sick', False):
                    status += '+'
            elif role == Role.ZUCHTTIER.value:
                # Zuchttiere status (same as surrogates for females, simple for males)
                sex = a.get('sex', '').lower()
                is_female = 'female' in sex or 'weiblich' in sex
                
                if is_female:
                    # Female Zuchttiere: same pregnancy logic as surrogates
                    preg_dates   = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'pregnancy']
                    birth_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'birth']
                    abort_dates  = [ev['datum'] for ev in a.get('events', []) if ev['typ'] == 'abortion']
                    
                    last_preg  = max(preg_dates)  if preg_dates  else None
                    last_birth = max(birth_dates) if birth_dates else None
                    last_abort = max(abort_dates) if abort_dates else None
                    
                    term_dates = [d for d in (last_birth, last_abort) if d]
                    last_term = max(term_dates) if term_dates else None
                    
                    # Determine base status
                    if last_preg and (not last_term or last_preg > last_term):
                        status = '☉'  # pregnant
                    elif last_birth and (now - last_birth).days < 90:  # 90-day recovery
                        status = 'Oo'  # recent birth
                    else:
                        status = ''
                    
                    # Append abnormal/sick markers
                    if a.get('abnormal_current', False):
                        status += '!'
                    if a.get('sick', False):
                        status += '+'
                else:
                    # Male Zuchttiere: abnormal + sick markers
                    markers = ('!' if a.get('abnormal_current', False) else '') + ('+' if a.get('sick', False) else '')
                    status = markers
                
                # Genotype is excluded from status - it's shown in a separate field in the UI
                
                # Append partner name (like partnertiere)
                partner = a.get('verpaart_mit', '').strip()
                if partner:
                    status = f"{status} | ♥ {partner}" if status else f"♥ {partner}"
            else:
                # Unknown or unspecified roles: no status
                status = ''

        # --- inject same‐day FSH/Prog. into the status string when Steroid_track is active ---
        if self._is_steroid_track_active():
            today = now.date()
            injections = []
            for ev in a.get('events', []):
                if ev.get('datum') and ev['datum'].date() == today:
                    if ev.get('typ') == 'fsh':
                        injections.append('FSH')
                    elif ev.get('typ') == 'progesterone':
                        injections.append('Prog.')
            if injections:
                status = (status + ' ' + ' '.join(injections)).strip()

        if a.get('in_experiment', False) and self._is_projects_track_active():
            status = (status + ' ■').strip()

        return compact_status_with_death_priority(a, status)

    # ------------------------
    # 7.17 Apply Phase Filter
    #     Set the reproductive phase filter and refresh the animal list.
    # ------------------------
    
    def _set_filter(self) -> None:
        """Set the phase filter and refresh the list."""
        s = self.sender()
        if s == self.btn_alle:
            self.phase_filter = Phase.ALLE.value
        elif s == self.btn_follikel:
            self.phase_filter = Phase.FOLLIKEL.value
        elif s == self.btn_luteal:
            self.phase_filter = Phase.LUTEAL.value
        for b in (self.btn_alle, self.btn_follikel, self.btn_luteal):
            b.setChecked(b == s)
        self._refresh_list()
        self._on_select()

    def phase_from_combined_or_blood(self, name: str, date: datetime) -> Optional[str]:
        """Determine the phase using combined values if available, else use blood progesterone."""
        a = self.animals.get(name, {})
        # Direct lookup instead of building dict - O(n) search but avoids dict allocation
        daten = a.get('daten', [])
        value = None
        for r in daten:
            if r.get('datum') == date:
                value = r.get('wert')
                break
        if value is None:
            return None
        return Phase.LUTEAL.value if value >= PHASESCHWELLE else Phase.FOLLIKEL.value

    # ------------------------
    # 7.18 Plot Selected Animals
    #     Render the selected animals' data on the Matplotlib canvas.
    # ------------------------
    def _plot_selected(self) -> None:

        # Initialize storage for sperm hover overlay dots
        self.sperm_overlay_dots = []

        """Plot data for selected animals (mit Click-Tooltips, Hover-Highlight und Statistiken)."""
        logging.info(f"_plot_selected called with {len(self.selected_animals)} selected animals")
        
        # Check if UI components exist
        if not hasattr(self, 'detail_widget') or not hasattr(self, 'dlay'):
            logging.warning("detail_widget or dlay not initialized, skipping plot")
            return
        
        self.detail_widget.setMinimumSize(0, 0)
        self._clear_matplotlib()

        has_selection = bool(self.selected_animals)
        # Show / hide “Anzeigen” and “Linien-Stil” boxes
        if hasattr(self, "box_chk") and hasattr(self, "box_rad"):
            self.box_chk.setVisible(has_selection)
            self.box_rad.setVisible(has_selection)

        # ------------------------
        # 7.18.1 Check data availability
        # ------------------------
        steroid_active = self._is_steroid_track_active()
        has_blood, has_urine, has_combined, has_weight, has_events = False, False, False, False, False
        # Filter to only main-list animals (heritage-only are not in self.animals)
        main_animals = [n for n in self.selected_animals if n in self.animals]
        for name in main_animals:
            animal = self.animals[name]
            has_blood |= steroid_active and bool(animal.get('daten'))
            has_urine |= steroid_active and bool(animal.get('pdg'))
            has_weight |= bool(animal.get('gewicht'))
            has_events |= steroid_active and (
                bool(animal.get('sperm')) or bool(
                    animal.get('events') or
                    animal.get('pgf')    or
                    animal.get('op')
                )
            )

            # Check if plugin has fitted model for combined data availability
            has_combined = False
            if steroid_active and self.has_pdg_plugin and hasattr(self, 'pdg_cap') and self.pdg_cap:
                for name in self.selected_animals:
                    params = self.pdg_cap._plugin.get_parameters(name)
                    if params and params.get('n_pairs', 0) > 0:
                        has_combined = True
                        break

        # ------------------------
        # 7.18.2 Enable/disable UI controls based on available data
        # ------------------------
        if self.has_pdg_plugin:
            self.chk_mode_combined.setEnabled(has_combined)
        self.chk_mode_blood.setEnabled(has_blood)
        if self.has_pdg_plugin:
            self.chk_mode_urin.setEnabled(has_urine)
        self.chk_prog.setEnabled(has_blood or has_urine or has_combined)
        self.chk_weight.setEnabled(has_weight)
        self.chk_events.setEnabled(has_events)

        # ------------------------
        # 7.18.3 Auto-select the appropriate display mode
        # ------------------------
        # Default priority: Combined > Blood > Urine
        # Only auto-select sub-modes if progesterone checkbox is enabled and checked
        if self.chk_prog.isEnabled() and self.chk_prog.isChecked():
            if has_combined and self.has_pdg_plugin:
                self.chk_mode_combined.setChecked(True)
            elif has_blood:
                self.chk_mode_blood.setChecked(True)
            elif has_urine and self.has_pdg_plugin:
                self.chk_mode_urin.setChecked(True)
        # Note: We don't auto-uncheck chk_prog here - user preference is preserved

        # ------------------------
        # 7.18.4 Enable/disable line style toggles
        # ------------------------
        # Combined line-style radios - conditional on plugin
        if self.has_pdg_plugin:
            self.rb_combined_on.setEnabled(has_selection and has_combined)
            self.rb_combined_off.setEnabled(has_selection and has_combined)
        
        # Blood line-style radios
        self.rb_blood_on.setEnabled(has_selection and has_blood)
        self.rb_blood_off.setEnabled(has_selection and has_blood)
        
        # Urine line-style radios - conditional on plugin
        if self.has_pdg_plugin:
            self.rb_urine_on.setEnabled(has_selection and has_urine)
            self.rb_urine_off.setEnabled(has_selection and has_urine)

        # Weight line-style radios
        self.rb_weight_on.setEnabled(has_selection and has_weight)
        self.rb_weight_off.setEnabled(has_selection and has_weight)

        # ——— Sperm line-style radios ———
        # Only Samenspender can have sperm data; check if any selected Samenspender have entries
        has_sperm = steroid_active and any(
            self.animals.get(name, {}).get('rolle') == Role.SAMENSP.value and
            bool(self.animals.get(name, {}).get('sperm'))
            for name in self.selected_animals
        )
        # Enable/disable and auto-check the “On/Off” radios accordingly
        self.rb_sperm_on.setEnabled(has_selection and has_sperm)
        self.rb_sperm_off.setEnabled(has_selection and has_sperm)
        if has_selection and has_sperm:
            # default to “On”
            self.rb_sperm_on.blockSignals(True)
            self.rb_sperm_off.blockSignals(True)
            self.rb_sperm_on.setChecked(True)
            self.rb_sperm_off.setChecked(False)
            self.rb_sperm_on.blockSignals(False)
            self.rb_sperm_off.blockSignals(False)
        else:
            self.rb_sperm_on.setChecked(False)
            self.rb_sperm_off.setChecked(False)

        if has_selection:
            # Set all line style toggles to "on" by default
            radio_buttons = [self.rb_weight_on, self.rb_blood_on]
            if self.has_pdg_plugin:
                radio_buttons.append(self.rb_combined_on)
                radio_buttons.append(self.rb_urine_on)
            for rb in radio_buttons:
                rb.blockSignals(True)
                rb.setChecked(True)
                rb.blockSignals(False)

            # Preserve user checkbox states - only update enabled state, not checked state
            # This allows per-user display preferences to persist across animal switches
            for cb in (self.chk_prog, self.chk_weight, self.chk_events):
                cb.blockSignals(True)
                # Only uncheck if the checkbox is disabled (no data available)
                # Keep user's checked preference if data is available
                if not cb.isEnabled() and cb.isChecked():
                    cb.setChecked(False)
                cb.blockSignals(False)

        # ------------------------
        # 7.18.5 Initialize Matplotlib artist lists
        # ------------------------
        self.prog_lines = []
        self.prog_overlay_dots = []
        # clear names and raw overlays at the start of every plot
        self.prog_overlay_names = []
        self.prog_overlay_raw_dots = []
        self.pdg_hollow_dots = []
        self.weight_lines = []
        self.weight_axes = []
        self.weight_ref_bands = []  # Reference range bands for offspring
        self.ev_lines = []
        self.ev_texts = []
        self.pdg_lines = []
        self.pdg_conv_lines = []
        self.sperm_lines = []
        # plotting context per animal for later urine scaling
        self._plot_ctx = {}

        # ------------------------
        # 7.18.6 Remove old widgets from layout
        # ------------------------
        # Keep only the first item (control boxes), remove all dynamic content
        while self.dlay.count() > 1:
            item = self.dlay.takeAt(1)
            if item is None:
                break
            widget = item.widget()
            if widget:
                widget.setParent(None)

        # ------------------------
        # 7.18.7 Handle empty selection: show splash image
        # ------------------------
        if not self.selected_animals:
            self.detail_widget.setMinimumSize(0, 0)
            self.dlay.addStretch(1)
            
            # Add disclaimer/footer text above splash image
            disclaimer_label = QLabel(
                self.messages.get("footer.rights", "ProgTrack").format(year=datetime.now().year)
            )
            disclaimer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.dlay.addWidget(disclaimer_label, alignment=Qt.AlignmentFlag.AlignCenter)
            
            # Add spacing between disclaimer and image
            spacer = QWidget()
            spacer.setFixedHeight(20)
            self.dlay.addWidget(spacer)
            
            img_label = QLabel()
            pix_path = Path("icons/Splash.png")
            pix = QPixmap(str(pix_path)) if pix_path.exists() else QPixmap()
            viewport = self.scroll.viewport().size()
            max_size = QSize(800, 800)
            pix = pix.scaled(
                min(viewport.width(), max_size.width()),
                min(viewport.height(), max_size.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            img_label.setPixmap(pix)
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.dlay.addWidget(img_label, alignment=Qt.AlignmentFlag.AlignCenter)
            self.dlay.addStretch(1)
            self.last_plotted_animals = []
            return

        # ------------------------
        # 7.18.8 Create Matplotlib figure and axes
        # ------------------------
        n = len(self.selected_animals)
        fig, axes = plt.subplots(n, 1, figsize=(10, 3 * n), sharex=True)
        fig.subplots_adjust(hspace=0.7, bottom=0.2)
        self.current_figure = fig
        if n == 1:
            axes = [axes]

        # ------------------------
        # 7.18.9 Initialize hover_data and conversion flag
        # ------------------------
        self.hover_data = []

        pdg_already_plotted = False

        # Filter to only main-list animals (heritage-only are not in self.animals)
        main_selected = [n for n in self.selected_animals if n in self.animals]
        for idx, name in enumerate(main_selected):
            a = self.animals[name]
            _dname = self._display_name(name)
            # Determine role label based on rolle
            rolle = a.get('rolle')
            if rolle == Role.OFFSPRING.value:
                # show genotype as "role" for offspring, fallback to localized role name
                role_label = a.get('genotype', '') or self._get_localized_role(rolle)
            elif rolle == Role.ZUCHTTIER.value:
                # show genotype as "role" for breeding animals, fallback to localized role name
                role_label = a.get('genotype', '') or self._get_localized_role(rolle)
            else:
                role_label = self._get_localized_role(rolle) if rolle is not None else ''
            weights = a.get('gewicht', [])
            recs = [
                r for r in a.get('daten', [])
                if isinstance(r.get('datum'), datetime)
                and isinstance(r.get('wert', None), (int, float))
            ]
            if not steroid_active:
                recs = []
            ax = axes[idx]
            prog_ax = ax
            # track per-animal values and axes
            prog_vals_for_ctx = []
            pdg_vals_for_ctx: list = []
            pdg_ax = None

 
            # --- If absolutely no data: show an empty graph with no axes/labels ---
            has_pdg = steroid_active and bool(a.get('pdg'))
            has_weight = bool(a.get('gewicht'))
            has_sperm_data = steroid_active and bool(a.get('sperm'))
            if not recs and not has_pdg and not has_weight and not has_sperm_data:
                ax.set_title(f"{role_label} – {_dname}")
                # remove ticks/labels and frame for a clean empty panel
                ax.set_xticks([])
                ax.set_yticks([])
                ax.set_xlabel("")
                ax.set_ylabel("")
                ax.grid(False)
                for spine in ax.spines.values():
                    spine.set_visible(False)
                continue

            # tag axis with animal name (for autoscale/label in _apply_mode)
            ax._animal_name = name
            # Do not skip animals lacking weights; still process progesterone or PdG
            # Only plot PdG data if plugin is installed
            pdg_recs = a.get('pdg', []) if steroid_active else []
            if self.has_pdg_plugin and steroid_active and not recs and pdg_recs:
                pdg_data = [(r['datum'], r['wert']) for r in pdg_recs]
                pdg_data.sort()
                dates = [d for d, v in pdg_data]
                vals  = [v for d, v in pdg_data]
                pdg_line, = ax.plot(
                    dates, vals,
                    marker=getattr(self, 'pdg_marker', 's'), linestyle='-', label=self.messages.get('plot.series.pdg', 'PdG'),
                    color=getattr(self, 'prog_color', QColor('crimson')).name(), picker=5
                )
                pdg_line._orig_linestyle = pdg_line.get_linestyle()
                # Respect urine mode checkbox state (requires main progesterone checkbox)
                pdg_line.set_visible(
                    getattr(self, 'chk_prog', None) and self.chk_prog.isChecked() and
                    getattr(self, 'chk_mode_urin', None) and self.chk_mode_urin.isChecked()
                )
                self.pdg_lines.append(pdg_line)
                ax.set_ylabel(self.messages.get('plot.ylabel.pdg', 'PdG (µg/mg Cr)'))
                ax.set_ylim(min(vals)*0.9, max(vals)*1.1)
                for i, (d, v) in enumerate(zip(dates, vals)):
                    probennummer = pdg_recs[i].get('probennummer') if i < len(pdg_recs) else None
                    self.hover_data.append((d, v, ax, 'pdg', name, pdg_line, probennummer))
                ax.set_title(f"{role_label} – {_dname}")
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%Y"))
                ax.grid(True)
                pdg_already_plotted = True
                pdg_vals_for_ctx = list(vals)
            elif recs:
                dates = np.array([r['datum'] for r in recs])
                vals = np.array([r['wert'] for r in recs])
                idx_sort = np.argsort(dates)
                sorted_dates = dates[idx_sort]
                sorted_vals = vals[idx_sort]
                prog_line, = ax.plot(
                    sorted_dates, sorted_vals,
                    marker=getattr(self, 'blood_marker', 'o'), linestyle='-', label=self.messages.get('plot.series.progesterone', 'Progesterone'),
                    color=getattr(self, 'prog_color', QColor('crimson')).name(), picker=5
                )
                prog_line._orig_linestyle = prog_line.get_linestyle()
                # Respect main progesterone checkbox - start hidden if unchecked
                prog_line.set_visible(getattr(self, 'chk_prog', None) and self.chk_prog.isChecked())
                self.prog_lines.append(prog_line)
                dot = ax.scatter(
                    sorted_dates, sorted_vals,
                    s=(prog_line.get_markersize() or 6)**2,
                    marker=prog_line.get_marker(),
                    facecolors=prog_line.get_color(),
                    edgecolors=prog_line.get_color(),
                    label='_nolegend_',
                    visible=False,
                    zorder=prog_line.get_zorder() + 1
                )
                # store raw overlay dots separately; they are not shown directly but kept
                # for potential future extensions
                self.prog_overlay_raw_dots.append(dot)
                # Create sorted records to match sorted dates/vals
                sorted_recs = [recs[i] for i in idx_sort]
                for i, (d, v) in enumerate(zip(sorted_dates, sorted_vals)):
                    probennummer = sorted_recs[i].get('probennummer') if i < len(sorted_recs) else None
                    self.hover_data.append((d, v, prog_ax, 'progesterone', name, prog_line, probennummer))
                prog_vals_for_ctx = list(sorted_vals)

            ax.set_title(f"{role_label} – {_dname}")

            if weights:
                # Parse dates to datetime objects so sorting is chronological
                # and the reference band shares the same axis scale
                def _parse_w_date(d):
                    if isinstance(d, datetime):
                        return d
                    s = str(d).strip()
                    for fmt in ('%d.%m.%Y', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
                        try:
                            return datetime.strptime(s, fmt)
                        except ValueError:
                            continue
                    return None
                _w_pairs = sorted(
                    [(dt, float(r['wert']))
                     for r in weights
                     for dt in (_parse_w_date(r.get('datum', '')),)
                     if dt is not None],
                    key=lambda x: x[0]
                )
                sorted_weight_dates = [p[0] for p in _w_pairs]   # list[datetime]
                sorted_weight_vals = np.array([p[1] for p in _w_pairs])
                weight_ax = ax.twinx()
                weight_ax.set_ylabel(self.messages.get('plot.ylabel.weight', 'Weight (g)'), 
                                     color=getattr(self, 'weight_color', QColor('purple')).name())
                
                # Add reference band for offspring animals (only if still offspring role)
                if rolle == Role.OFFSPRING.value and sorted_weight_dates:
                    reference_data = self._load_reference_weights(species=a.get('species', ''))
                    if reference_data:
                        # Determine age-0 anchor: birth date if set, else first measurement
                        birth_date_raw = a.get('birth_date', '')
                        age_reference = None
                        if birth_date_raw:
                            try:
                                age_reference = datetime.strptime(
                                    str(birth_date_raw).strip(), '%d.%m.%Y')
                            except ValueError:
                                pass
                        if age_reference is None:
                            age_reference = sorted_weight_dates[0]

                        ref_dates = []
                        ref_mins  = []
                        ref_maxs  = []

                        last_date     = sorted_weight_dates[-1]
                        max_age_days  = (last_date - age_reference).days
                        max_age_weeks = max_age_days / 7.0
                        max_ref_age   = max(
                            (age for age, _, _ in reference_data if age != float('inf')),
                            default=0)

                        for age_weeks, min_w, max_w in reference_data:
                            if age_weeks == float('inf'):
                                if max_age_weeks > max_ref_age:
                                    ref_dates.append(age_reference + timedelta(days=max_ref_age * 7))
                                    ref_mins.append(min_w)
                                    ref_maxs.append(max_w)
                                    ref_dates.append(last_date)
                                    ref_mins.append(min_w)
                                    ref_maxs.append(max_w)
                            else:
                                ref_dates.append(age_reference + timedelta(days=age_weeks * 7))
                                ref_mins.append(min_w)
                                ref_maxs.append(max_w)

                        if len(ref_dates) >= 2:
                            ref_band = weight_ax.fill_between(
                                ref_dates, ref_mins, ref_maxs,
                                color='grey', alpha=0.2, zorder=0,
                                label='Reference range'
                            )
                            # Respect user's display checkbox preference
                            ref_band.set_visible(getattr(self, 'chk_weight', None) and self.chk_weight.isChecked())
                            self.weight_ref_bands.append(ref_band)
                
                weight_line, = weight_ax.plot(
                    sorted_weight_dates, sorted_weight_vals,
                    marker=getattr(self, 'weight_marker', '^'), linestyle='--',
                    color=getattr(self, 'weight_color', QColor('purple')).name(),
                    label=self.messages.get('plot.series.weight', 'Weight'), picker=5
                )
                weight_line._orig_linestyle = weight_line.get_linestyle()
                # Respect user's display checkbox preference
                weight_line.set_visible(getattr(self, 'chk_weight', None) and self.chk_weight.isChecked())
                self.weight_lines.append(weight_line)
                self.weight_axes.append(weight_ax)
                # Store full weight series per animal for later % change lookup
                if not hasattr(self, '_weights_by_animal'):
                    self._weights_by_animal = {}
                self._weights_by_animal[name] = list(zip(sorted_weight_dates, sorted_weight_vals))

                for d, v in zip(sorted_weight_dates, sorted_weight_vals):
                    self.hover_data.append((d, v, weight_ax, 'weight', name, weight_line))
                # Y-limits: autoscale weights with sensible padding (based only on actual measurements)
                if sorted_weight_vals.size > 0:
                    wmin = float(np.nanmin(sorted_weight_vals))
                    wmax = float(np.nanmax(sorted_weight_vals))
                    # Guard against NaNs / single-point series
                    if not np.isfinite(wmin) or not np.isfinite(wmax):
                        wmin, wmax = 0.0, 1.0
                    if wmin == wmax:
                        # constant series: pad by at least 10 units or 10% of |wmin|
                        pad = max(10.0, max(1.0, abs(wmin)) * 0.1)
                    else:
                        # variable series: pad by 10% of the range (min 10 g)
                        pad = max(10.0, 0.1 * (wmax - wmin))
                    weight_ax.set_ylim(wmin - pad, wmax + pad)
 
                weight_ax.tick_params(axis='y', labelcolor='purple')
                # precompute midpoint of weight axis for offspring events
                if rolle == Role.OFFSPRING.value:
                    w_ymin, w_ymax = weight_ax.get_ylim()
                    weight_ymid = (w_ymin + w_ymax) / 2

            # y-axis labeling moved to _apply_mode
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m.%Y"))
            ax.grid(True)

            pdg_recs = a.get('pdg', []) if steroid_active else []
            pdg_data = [(r['datum'], r['wert']) for r in pdg_recs]
            pdg_ax = pdg_ax
            # Only plot PdG data if plugin is installed
            if self.has_pdg_plugin and steroid_active and pdg_data and not pdg_already_plotted:
                pdg_ax = ax.twinx()
                # Position PdG axis on left side with outward offset for dual left-axis display
                pdg_ax.spines['left'].set_position(('outward', 60))
                pdg_ax.spines['right'].set_visible(False)
                pdg_ax.yaxis.set_label_position('left')
                pdg_ax.yaxis.set_ticks_position('left')
                pdg_ax.set_ylabel(self.messages.get('plot.ylabel.pdg', 'PdG (µg/mg Cr)'), 
                                  color=self.urine_color.name())
                pdg_dates = np.array([d for d, v in pdg_data])
                pdg_vals  = np.array([v for d, v in pdg_data])
                order = np.argsort(pdg_dates)
                pdg_dates = pdg_dates[order]
                pdg_vals  = pdg_vals[order]
                pdg_line, = pdg_ax.plot(
                    pdg_dates, pdg_vals,
                    marker=getattr(self, 'pdg_marker', 's'), linestyle='-', label=self.messages.get('plot.series.pdg', 'PdG'),
                    color=self.urine_color.name(), picker=5
                )
                pdg_line._orig_linestyle = pdg_line.get_linestyle()
                # Respect urine mode checkbox state (requires main progesterone checkbox)
                pdg_line.set_visible(
                    getattr(self, 'chk_prog', None) and self.chk_prog.isChecked() and
                    getattr(self, 'chk_mode_urin', None) and self.chk_mode_urin.isChecked()
                )
                self.pdg_lines.append(pdg_line)
                # Create sorted pdg_recs to match sorted dates/vals
                sorted_pdg_recs = [pdg_recs[i] for i in order]
                for i, (d, v) in enumerate(zip(pdg_dates, pdg_vals)):
                    probennummer = sorted_pdg_recs[i].get('probennummer') if i < len(sorted_pdg_recs) else None
                    self.hover_data.append((d, v, pdg_ax, 'pdg', name, pdg_line, probennummer))
                pdg_ax.tick_params(axis='y', labelcolor=self.urine_color.name())
                pdg_vals_for_ctx = list(pdg_vals)

            if pdg_ax is not None:
                # Always autoscale PdG axis to urine values with padding
                if pdg_vals_for_ctx:
                    pmin = float(np.nanmin(pdg_vals_for_ctx))
                    pmax = float(np.nanmax(pdg_vals_for_ctx))
                    if pmin == pmax:
                        pad = max(0.1, abs(pmin) * 0.1)
                    else:
                        pad = max(0.1, 0.1 * (pmax - pmin))
                    pdg_ax.set_ylim(pmin - pad, pmax + pad)
                else:
                    pdg_ax.relim()
                    pdg_ax.autoscale_view()

            # store per-animal context for urine scaling
            try:
                self._plot_ctx[name] = {
                    'ax': ax,
                    'pdg_ax': pdg_ax,
                    'prog_vals': prog_vals_for_ctx,
                    'pdg_vals': pdg_vals_for_ctx,
                }
            except Exception:
                pass
                    
            # Unified Prog-Werte (Messung + PdG→Prog) *only* in Kombiniert
            # Check per-animal if formula exists - if not, fall back to blood progesterone
            has_animal_formula = False
            if steroid_active and self.has_pdg_plugin and hasattr(self, 'pdg_cap') and self.pdg_cap:
                params = self.pdg_cap._plugin.get_parameters(name)
                has_animal_formula = params and params.get('n_pairs', 0) > 0
            
            if self.has_pdg_plugin and steroid_active and self.chk_mode_combined.isChecked() and pdg_data and has_animal_formula:
                # Call plugin's extend_plot to get converted data
                plot_context = type('PlotContext', (), {
                    'animal_data': a,
                    'animal_name': name,
                    'urine_color': getattr(self, 'urine_color', QColor('#FF8C00')),
                    'pdg_color': getattr(self, 'pdg_color', QColor('#008000')),
                    'urine_marker': getattr(self, 'urine_marker', 's'),
                })()
                
                plugin_result = self.pdg_cap.hooks.extend_plot(plot_context)
                
                # Build unified map: blood progesterone + converted PdG
                raw_map = {r['datum']: r['wert'] for r in recs}
                unified_map = raw_map.copy()
                
                # Add converted PdG values if plugin returned data
                if plugin_result and plugin_result.get('converted_series'):
                    for dt, conv_val in plugin_result['converted_series']:
                        if dt not in unified_map and not math.isnan(conv_val):
                            unified_map[dt] = conv_val
                
                # 3) sort and split
                all_dates = sorted(unified_map.keys())
                all_vals = [unified_map[d] for d in all_dates]
                
                # Precompute maps for tooltips
                pdg_map = {d: v for d, v in pdg_data}
                pdg_probe_map = {r['datum']: r.get('probennummer') for r in pdg_recs}
                raw_probe_map = {r['datum']: r.get('probennummer') for r in recs}


                # 4) plot the single continuous line
                unified_line, = ax.plot(
                    all_dates, all_vals,
                    linestyle='-', marker=None,
                    label=self.messages.get('plot.series.progesterone_combined', 'Progesterone (combined)'), picker=5,
                    color=getattr(self, 'prog_color', QColor('crimson')).name()
                )
                unified_line._orig_linestyle = unified_line.get_linestyle()
                # Respect combined mode checkbox state (requires main progesterone checkbox)
                unified_line.set_visible(
                    getattr(self, 'chk_prog', None) and self.chk_prog.isChecked() and
                    getattr(self, 'chk_mode_combined', None) and self.chk_mode_combined.isChecked()
                )
                self.pdg_conv_lines.append(unified_line)

                # 5) overlay solid dots on the *original* Pgr dates (blood measurements)
                overlay = ax.scatter(
                    list(raw_map.keys()), list(raw_map.values()),
                    s=(unified_line.get_markersize() or 6)**2,
                    marker=getattr(self, 'blood_marker', 'o'),
                    facecolors=unified_line.get_color(),
                    edgecolors=unified_line.get_color(),
                    label='_nolegend_',
                    zorder=unified_line.get_zorder()+1
                )
                # Respect combined mode checkbox state (requires main progesterone checkbox)
                overlay.set_visible(
                    getattr(self, 'chk_prog', None) and self.chk_prog.isChecked() and
                    getattr(self, 'chk_mode_combined', None) and self.chk_mode_combined.isChecked()
                )
                # append overlay for combined conversion and record the animal name for mapping
                self.prog_overlay_dots.append(overlay)
                self.prog_overlay_names.append(name)
                
                # 6) overlay hollow markers on the PdG→Pgr‐only dates (converted measurements)
                conv_only_dates = [d for d in all_dates if d not in raw_map]
                if conv_only_dates:
                    conv_only_vals = [unified_map[d] for d in conv_only_dates]
                    hollow = ax.scatter(
                        conv_only_dates, conv_only_vals,
                        s=(unified_line.get_markersize() or 6)**2,
                        marker=getattr(self, 'combined_marker', 'o'),
                        facecolors='none',
                        edgecolors=unified_line.get_color(),
                        label='_nolegend_',
                        zorder=unified_line.get_zorder()+1
                    )
                    # Respect combined mode checkbox state (requires main progesterone checkbox)
                    hollow.set_visible(
                        getattr(self, 'chk_prog', None) and self.chk_prog.isChecked() and
                        getattr(self, 'chk_mode_combined', None) and self.chk_mode_combined.isChecked()
                    )
                    # keep reference if you need it later (e.g. for legend or pick)
                    self.pdg_hollow_dots.append(hollow)

                # 7) rebuild hover_data for this unified line using the PdG map
                for d in all_dates:
                    v = unified_map[d]
                    orig_pdg = pdg_map.get(d)
                    # Get probennummer from blood if available, otherwise from urine
                    probennummer = raw_probe_map.get(d) or pdg_probe_map.get(d)
                    self.hover_data.append((d, v, prog_ax, 'pdg_conv', name, unified_line, orig_pdg, probennummer))
                
                # 8) Store combined values separately for proper y-axis scaling in _apply_mode
                # Keep prog_vals as native progesterone (for blood mode)
                # Add combined_vals for combined mode
                try:
                    self._plot_ctx[name]['combined_vals'] = all_vals
                except Exception:
                    pass
                
                # 9) Force axis to rescale to include all combined values
                ax.relim()
                ax.autoscale_view()

            ymin, ymax = ax.get_ylim()
            if ymin == ymax:
                # Single value: add padding like PdG does
                pad = max(1.0, abs(ymax) * 0.1)
                ymin, ymax = max(0, ymax - pad), ymax + pad
                ax.set_ylim(ymin, ymax)
            elif weights:
                ax.set_ylim(0, max(ymax, PHASESCHWELLE * 1.2))
            ymid = (ymax + ymin) / 2

            # Freeze limits before event markers so they cannot alter autoscale.
            x0, x1 = ax.get_xlim()
            y0, y1 = ax.get_ylim()
            ax.set_autoscale_on(False)
            ax.set_xlim(x0, x1)
            ax.set_ylim(y0, y1)
            if pdg_ax is not None:
                py0, py1 = pdg_ax.get_ylim()
                pdg_ax.set_autoscale_on(False)
                pdg_ax.set_ylim(py0, py1)

            # after PdG plots (raw or combined) and before event annotations, plot sperm lines
            # ------------------------------------------------------------------------------
            # add Sondermessung to labels
            # how many of each type we have now
            counts = {}
            # ------------------------------------------------------------------------------
            # Render sperm parameters as separate pickable lines (Samenspender only)
            if steroid_active and rolle == Role.SAMENSP.value:
                # Collect and sort raw sperm data
                sperm_data = [(
                    s['datum'],
                    s.get('count', 0) or 0,
                    s.get('motility', 0) or 0,
                    s.get('progressive', 0) or 0
                ) for s in a.get('sperm', [])]
                sperm_data.sort(key=lambda x: x[0])
                if sperm_data:
                    dates, counts, motility_pct, prog_pct = zip(*sperm_data)
                    # Draw bars: total = 100% (full count), motile/progressive = percentage of total
                    # Y-axis shows absolute sperm/ml, but bar heights represent percentages
                    bars_total = ax.bar(
                        dates, counts,  # Total = 100% height (full count value)
                        width=1.0, align='center', zorder=1, 
                        color=getattr(self, 'sperm_total_color', QColor('#D55E00')).name()
                    )
                    bars_motile = ax.bar(
                        dates, [c * (m/100.0) for c, m in zip(counts, motility_pct)],  # Motile = motility% of total height
                        width=0.8, align='center', zorder=2, 
                        color=getattr(self, 'sperm_motile_color', QColor('#0072B2')).name()
                    )
                    bars_progress = ax.bar(
                        dates, [c * (p/100.0) for c, p in zip(counts, prog_pct)],  # Progressive = progressive% of total height
                        width=0.6, align='center', zorder=3, 
                        color=getattr(self, 'sperm_progressive_color', QColor('#009E73')).name()
                    )
                    for rect in bars_total + bars_motile + bars_progress:
                        self.ev_lines.append(rect)
                    # Total count, motile count, progressive count
                    vals_total     = list(counts)
                    vals_motile    = [c * (m / 100.0) for c, m in zip(counts, motility_pct)]
                    vals_progress = [c * (p / 100.0) for c, p in zip(counts, prog_pct)]
                    # Plot as three pickable lines above the bars with distinct markers
                    total_line,  = ax.plot(
                        dates, vals_total,
                        marker=getattr(self, 'sperm_total_marker', 'o'), linestyle='--', markersize=8,
                        color=getattr(self, 'sperm_total_color', QColor('#D55E00')).name(), 
                        label=self.messages.get('plot.series.sperm_total', 'Sperm total'), picker=5
                    )
                    motile_line, = ax.plot(
                        dates, vals_motile,
                        marker=getattr(self, 'sperm_motile_marker', 's'), linestyle='--', markersize=7,
                        color=getattr(self, 'sperm_motile_color', QColor('#0072B2')).name(), 
                        label=self.messages.get('plot.series.sperm_motile', 'Motile'), picker=5
                    )
                    prog_line,   = ax.plot(
                        dates, vals_progress,
                        marker=getattr(self, 'sperm_progressive_marker', '^'), linestyle='--', markersize=7,
                        color=getattr(self, 'sperm_progressive_color', QColor('#009E73')).name(), 
                        label=self.messages.get('plot.series.sperm_progressive', 'Progressive'), picker=5
                    )

                    # collect sperm lines for toggling
                    self.sperm_lines = [total_line, motile_line, prog_line]
                    # store original linestyles
                    for ln in self.sperm_lines:
                        ln._orig_linestyle = ln.get_linestyle()

                    # Register sperm hover‐data, storing count+motility%+progressive%
                    for d, raw_count, mot_pct, prog_pct in sperm_data:
                        # total
                        self.ev_lines.append(total_line)
                        self.hover_data.append((
                            d, raw_count, mot_pct, prog_pct,
                            ax, 'sperm_total', name, total_line
                        ))
                        # motile
                        mot_count = raw_count * mot_pct / 100.0
                        self.ev_lines.append(motile_line)
                        self.hover_data.append((
                            d, mot_count, mot_pct, prog_pct,
                            ax, 'sperm_motility', name, motile_line
                        ))
                        # progressive
                        prog_count = raw_count * prog_pct / 100.0  # progressive % of all sperms
                        self.ev_lines.append(prog_line)
                        self.hover_data.append((
                            d, prog_count, mot_pct, prog_pct,
                            ax, 'sperm_progressive', name, prog_line
                        ))



            if steroid_active:
                # Plot all reproductive events (PGF as line; FSH & Progesterone as triangles; Surgery, embryo, etc. as before)
                evs = []
                evs += [('pgf',       dt) for dt in a.get('pgf', [])]
                # special case: offspring should plot all its surgery/special_measurement events
                if rolle == Role.OFFSPRING.value:
                    evs += [(ev['typ'], ev['datum']) for ev in a.get('events', [])]
                # surrogates show all their events
                elif rolle == Role.AMME.value:
                    # surrogates: all events (including 'progesterone')
                    evs += [(ev['typ'], ev['datum']) for ev in a.get('events', [])]
                # female Zuchttiere show pregnancy events (same as surrogates)
                elif rolle == Role.ZUCHTTIER.value and a.get('sex', '').lower() in ('female', 'weiblich'):
                    # female Zuchttiere: pregnancy-related events
                    evs += [(ev['typ'], ev['datum']) for ev in a.get('events', [])]
                # male Zuchttiere (breeding animals) show all events
                elif rolle == Role.ZUCHTTIER.value:
                    evs += [(ev['typ'], ev['datum']) for ev in a.get('events', [])]
                # experimental animals show all events
                elif rolle == Role.EXPERIMENTAL.value:
                    evs += [(ev['typ'], ev['datum']) for ev in a.get('events', [])]
                else:
                    # donors: Surgery, PGF, and FSH (from both legacy arrays and events)
                    evs += [('surgery',      dt) for dt in a.get('op', [])]
                    evs += [(ev['typ'], ev['datum'])
                            for ev in a.get('events', [])
                            if ev['typ'] in ('fsh', 'pgf', 'surgery')]

                # styling maps
                colors = {
                    'pgf': getattr(self, 'pgf_color', QColor('#FF0000')).name(), 
                    'embryo_transfer': getattr(self, 'embryo_color', QColor('#000000')).name(), 
                    'surgery': getattr(self, 'op_color', QColor('#0000FF')).name(),
                    'pregnancy': getattr(self, 'pregnancy_color', QColor('#008000')).name(), 
                    'abortion': getattr(self, 'abort_color', QColor('#FF00FF')).name(), 
                    'birth': getattr(self, 'birth_color', QColor('#000000')).name(),
                    'fsh': getattr(self, 'fsh_color', QColor('#000000')).name(), 
                    'progesterone': 'green',
                    'special_measurement': getattr(self, 'special_color', QColor('#FFA500')).name()
                }
                labels = {
                    'pgf': self.messages.get('plot.event.pgf', 'PGF'),
                    'embryo_transfer': self.messages.get('plot.event.embryo_transfer', 'Embryo'),
                    'surgery': self.messages.get('plot.event.operation', 'OP'),
                    'pregnancy': self.messages.get('plot.event.pregnancy', 'Pregnancy'),
                    'abortion': self.messages.get('plot.event.abort', 'Abort'),
                    'birth': self.messages.get('plot.event.birth', 'Birth'),
                    'fsh': self.messages.get('plot.event.fsh', 'FSH'),
                    'progesterone': self.messages.get('plot.event.progesterone_short', 'Prog.'),
                    'special_measurement': self.messages.get('plot.event.special_measurement', 'Special measurement')
                }

                # how many of each type we have now
                counts = {}
                for typ, _ in evs:
                    counts[typ] = counts.get(typ, 0) + 1
                idxs = {typ: 0 for typ in counts}

                # Configured maximum event counts.
                max_allowed = {
                    'pgf': a.get('max_pgf', '?'),
                    'embryo_transfer': a.get('max_embryo', '?'),
                    'surgery': a.get('max_op', '?'),
                    'birth': a.get('max_geburten', '?'),
                    'pregnancy': a.get('max_pregnancies', '?'),
                    'abortion': a.get('max_geburten', '?'),
                    'special_measurement': a.get('max_special', '?'),
                    'fsh': a.get('max_fsh', '?'),
                }

                # constant y‐offset for all event triangles so only the tip touches the axis

                for typ, dt_raw in evs:
                    idxs[typ] += 1
                    col = colors.get(typ, 'black')
                    # FSH and Progesterone: tiny triangles inside the axes, clipped by y-limits
                    if typ in ('fsh', 'progesterone'):
                        # strict normalization to avoid invalid ordinals
                        dt = _to_py_datetime(dt_raw)
                        dt_num = _safe_date2num(dt)
                        if dt_num is None:
                            logging.warning(f"Skipping invalid {typ} event date: {dt_raw!r}")
                            continue
                        # Use custom marker for FSH, default 'v' for progesteron
                        marker = getattr(self, 'fsh_marker', 'v') if typ == 'fsh' else 'v'
                        tri = ax.scatter(
                            dt_num, TRI_Y,
                            marker=marker, s=30, color=col,
                            transform=ax.get_xaxis_transform(),  # x=data, y=axes-fraction
                            clip_on=True, picker=10, zorder=3
                        )
                        # Respect events checkbox state (use appropriate checkbox for current tab)
                        events_chk = self._get_current_events_checkbox()
                        tri.set_visible(events_chk is not None and events_chk.isChecked())
                        self.ev_lines.append(tri)
                        self.hover_data.append((dt, TRI_Y, ax, typ, name, tri))
                    else:
                        # All line event labels use weight axis for placement
                        if 'weight_ax' in locals():
                            axis_plot = weight_ax
                            y_min, y_max = weight_ax.get_ylim()
                            y_pos = (y_min + y_max) / 2
                        else:
                            axis_plot = ax
                            # Calculate midpoint dynamically to ensure proper centering
                            y_min, y_max = ax.get_ylim()
                            y_pos = (y_min + y_max) / 2

                        # Normalize date for vertical line as well
                        dt = _to_py_datetime(dt_raw)
                        if dt is None:
                            logging.warning(f"Skipping invalid event date for {typ}: {dt_raw!r}")
                            continue
                        lab = labels.get(typ, typ)
                        line = axis_plot.axvline(dt, linestyle='dashed', color=col)
                        # Respect events checkbox state (use appropriate checkbox for current tab)
                        events_chk = self._get_current_events_checkbox()
                        line.set_visible(events_chk is not None and events_chk.isChecked())
                        self.ev_lines.append(line)
                        denom = max_allowed.get(typ, counts.get(typ, '?'))
                        if typ.lower() == 'abortion':
                            txt = f"{lab} ({idxs[typ]})"
                        else:
                            txt = f"{lab} ({idxs[typ]}/{denom})"
                        ev_text = axis_plot.text(
                            dt + timedelta(days=0.1),
                            y_pos,
                            txt,
                            rotation=90,
                            fontsize=8,
                            va='center',
                            clip_on=True
                        )
                        # Respect events checkbox state (use appropriate checkbox for current tab)
                        events_chk = self._get_current_events_checkbox()
                        ev_text.set_visible(events_chk is not None and events_chk.isChecked())
                        self.ev_texts.append(ev_text)
        # After plotting events, sanity-clamp x-limits once
        try:
            self._clamp_xlims(ax)
        except Exception:
            pass
        # enforce Anzeige-mode on the freshly plotted lines
        if has_selection:
            self._apply_mode()
            # and apply urine scaling (if in urine mode)
            self._apply_urine_scale()
        self.last_plotted_animals = list(self.selected_animals)

        # ------------------------
        # 7.18.10 Pan state variables
        # ------------------------
        _pan_active = False
        _pan_start_x = None
        _pan_axis = None

        # ------------------------
        # 7.18.11 Scroll zoom handler
        # ------------------------
        def on_scroll(event):
            if event.inaxes:
                ax = event.inaxes
                x_min, x_max = ax.get_xlim()
                x_range = (x_max - x_min)
                # Newer Matplotlib uses event.step; older uses button 'up'/'down'
                try:
                    direction = event.step
                    factor = (1/1.2) if direction > 0 else 1.2
                except AttributeError:
                    factor = 1.2 if getattr(event, "button", "") == "up" else 0.8
                xdata = event.xdata
                if xdata is None:
                    return
                new_min = xdata - (xdata - x_min) * factor
                new_max = xdata + (x_max - xdata) * factor
                ax.set_xlim(new_min, new_max)
                # Clamp x-limits to a safe date range to avoid DateLocator overflow
                self._clamp_xlims(ax)
                event.canvas.draw_idle()
                # guard: end scroll handler here; prevent stray code from running
                return

        # ------------------------
        # 7.18.11 Hover highlight handler
        # ------------------------
            # scroll_event → MouseEvent (no .artist). Do nothing here for scrolls.
            if not hasattr(event, "artist"):
                return
            artist = event.artist
            # skip immediate hover tooltips on weight lines to avoid flashing
            if artist in self.weight_lines:
                return
            # include event triangles in pickable artists
            if artist not in (self.prog_lines + self.weight_lines + self.ev_lines):
                return
            # find matching entry for this artist in hover_data
            for entry in self.hover_data:
                # each entry is (d, v, ax, dtype, name, line)
                if entry[5] is artist:
                    d, v, ax, dtype, name, line = entry
                    break
            else:
                return
            # format date
            date_str = d.strftime('%d.%m.%Y')
            # choose annotation text
            if dtype == 'fsh':
                text = f"{self.messages.get('plot.event.fsh', 'FSH')} – {date_str}"
            elif dtype == 'progesterone':
                text = f"{self.messages.get('plot.series.progesterone', 'Progesterone')} – {date_str}"
            elif dtype == 'sperm':
                text = f"{self.messages.get('plot.tooltip.sperm_sample', 'Sperm sample')} – {date_str}"
            elif dtype == 'weight':
                # normalize date
                if isinstance(d, np.datetime64):
                    d = d.astype('datetime64[ms]').tolist()
                elif isinstance(d, (float, np.floating)):
                    d = mdates.num2date(d)

                # use unified tooltip formatter (handles v=None)
                text = self._format_tooltip('weight', d, v, name, extra={})

                # render tooltip (use 0 for y‐coord if v is None)
                y = v if v is not None else 0
                disp = ax.transData.transform((mdates.date2num(d), y))
                fx, fy = ax.figure.transFigure.inverted().transform(disp)
                self._annotation = ax.figure.text(
                    fx, fy, text,
                    transform=ax.figure.transFigure,
                    bbox=dict(boxstyle="round,pad=0.5", fc="yellow", alpha=0.9),
                    zorder=9999, clip_on=False
                )
                for od in getattr(self, 'sperm_overlay_dots', []):
                    od.remove()
                self.sperm_overlay_dots.clear()
                event.canvas.draw_idle()
                return
            else:
                text = f"{dtype.capitalize()} – {date_str}"
            # remove old annotation
            if self._annotation:
                self._annotation.remove()
                self._annotation = None
            # compute display coords using the artist's own transform for triangles
            xnum = mdates.date2num(d)
            if dtype in ('fsh', 'progesterone', 'sperm_total', 'sperm_motility', 'sperm_progressive'):
            # re-compute exactly where the triangle lives, in numeric coords
                x_num = mdates.date2num(d)
            # TRI_Y is the constant axes-fraction offset under the x-axis
                x_px, y_px = line.get_transform().transform((x_num, TRI_Y))
            else:
                disp_x, disp_y = ax.transData.transform((xnum, v))
            # annotate
            fig = ax.figure
            xnum = mdates.date2num(d)
            if dtype in ('fsh', 'progesterone'):
                 disp_x, disp_y = ax.transData.transform((xnum, 0))
            else:
                disp_x, disp_y = ax.transData.transform((xnum, v))
            fig_x, fig_y = fig.transFigure.inverted().transform((disp_x, disp_y))
            self._annotation = fig.text(
                fig_x, fig_y,
                text,
                transform=fig.transFigure,
                bbox=dict(boxstyle='round,pad=0.3', fc='yellow', alpha=0.9),
                zorder=20000,
                clip_on=False
            )
            event.canvas.draw_idle()

        # ------------------------
        # 7.18.12 Hover tooltip handler (visible lines only)
        # ------------------------
        def on_move(event):
            # Handle horizontal panning if active
            nonlocal _pan_active, _pan_start_x, _pan_axis
            if _pan_active and _pan_axis and event.xdata is not None:
                if event.inaxes == _pan_axis:
                    # Calculate horizontal delta
                    delta_x = _pan_start_x - event.xdata
                    x_min, x_max = _pan_axis.get_xlim()
                    # Apply horizontal pan
                    _pan_axis.set_xlim(x_min + delta_x, x_max + delta_x)
                    # Clamp to safe date range
                    self._clamp_xlims(_pan_axis)
                    event.canvas.draw_idle()
                    # Update start position for smooth continuous panning
                    _pan_start_x = event.xdata
                return
            
            if event.inaxes is None:
                return

            closest = None
            min_px = float('inf')
            for entry in self.hover_data:
                # Support multiple entry formats with optional sample number.
                #  - 6‐tuple: (d,v,ax,dtype,name,line)
                #  - 7-tuple with sample number: (d,v,ax,dtype,name,line,probennummer)
                #  - 7‐tuple pdg_conv: (d,v,ax,'pdg_conv',name,line,orig)
                #  - 8‐tuple pdg_conv with probe: (d,v,ax,'pdg_conv',name,line,orig,probennummer)
                #  - 8‐tuple sperm:  (d,count,mot_pct,prog_pct,ax,dtype,name,line)
                if len(entry) == 6:
                    d, v, ax_h, dtype, name_h, line = entry
                elif len(entry) == 7 and entry[3] == 'pdg_conv':
                    d, v, ax_h, dtype, name_h, line, orig = entry
                elif len(entry) == 7:
                    d, v, ax_h, dtype, name_h, line, probennummer = entry
                elif len(entry) == 8 and entry[3] == 'pdg_conv':
                    d, v, ax_h, dtype, name_h, line, orig, probennummer = entry
                elif len(entry) == 8 and entry[5] in ('sperm_total','sperm_motility','sperm_progressive'):
                    # sperm_data entries: (d, count, mot_pct, prog_pct, ax, dtype, name, line)
                    d, count, mot_pct, prog_pct, ax_h, dtype, name_h, line = entry
                    # Get actual plotted y-value from the line data
                    line_xdata = line.get_xdata()
                    line_ydata = line.get_ydata()
                    # Find matching date in line data
                    d_num = mdates.date2num(d)
                    idx = np.argmin(np.abs(np.array([mdates.date2num(x) for x in line_xdata]) - d_num))
                    v = line_ydata[idx] if idx < len(line_ydata) else count
                else:
                    continue
                if not line.get_visible():
                    continue
                # Compute display coords robustly for hover, too
                # Only use axis transform for actual triangle events (fsh, progesterone injections at TRI_Y)
                is_triangle_event = (dtype in ('fsh', 'progesterone') 
                                    and isinstance(v, (int, float)) 
                                    and abs(v - TRI_Y) < 1e-6)
                if is_triangle_event:
                    x_num = mdates.date2num(d)
                    x_px, y_px = ax_h.get_xaxis_transform().transform((x_num, TRI_Y))
                else:
                    if hasattr(line, "get_offsets"):
                        offs = line.get_offsets()
                        if len(offs) == 0:
                            continue
                        x_off, y_off = offs[0]
                        x_px, y_px = line.get_transform().transform((x_off, y_off))
                    else:
                        x_px, y_px = line.get_transform().transform((mdates.date2num(d), v))

                dx, dy = event.x - x_px, event.y - y_px
                dist = np.hypot(dx, dy)
                if dist < min_px and dist <= SELECT_PIXEL_THRESHOLD:
                    min_px = dist
                    closest = (d, v, ax_h, dtype, name_h, line)
            if self._highlight_scatter and (not closest or self._highlight_point != closest):
                self._highlight_scatter.remove()
                self._highlight_point = None
                self._highlight_scatter = None
                event.canvas.draw_idle()
            if closest and self._highlight_point != closest:
                d, v, ax_h, dtype, name_h, line = closest
                self._highlight_point = closest
                # draw hover-highlight using the artist's own coordinate system
                if hasattr(line, "get_offsets"):
                    x_off, y_off = line.get_offsets()[0]
                    trans = line.get_transform()
                    mark = 'v' if dtype in ('fsh','progesterone') else 'o'
                    size = 150 if dtype in ('fsh','progesterone') else 80
                    self._highlight_scatter = ax_h.scatter(
                        [x_off], [y_off],
                        marker=mark, s=size,
                        facecolors='yellow', edgecolors='black',
                        alpha=0.9, zorder=9999, transform=trans
                    )
                else:
                    # Use the actual marker from the line being hovered over
                    marker = line.get_marker() if hasattr(line, 'get_marker') else 'o'
                    # Handle pdg_conv which has marker=None on the line but uses scatter overlays
                    # Also handle cases where marker might be 'None' string or None
                    if marker == 'None' or marker is None:
                        if dtype == 'pdg_conv':
                            marker = getattr(self, 'combined_marker', 'o')
                        elif dtype == 'pdg':
                            marker = getattr(self, 'pdg_marker', 's')
                        elif dtype == 'progesterone':
                            marker = getattr(self, 'blood_marker', 'o')
                        else:
                            marker = 'o'
                    self._highlight_scatter = ax_h.scatter(
                        [d], [v],
                        marker=marker, s=100,
                        facecolors='yellow', edgecolors='black',
                        alpha=0.9, zorder=9999
                    )

                event.canvas.draw_idle()
        # ------------------------
        # 7.18.13 Click handler for tooltips/annotations
        # ------------------------
        def on_click(event):
            # Accept clicks even when they fall just outside the axes.
            # FSH/Prog triangles are drawn with ax.get_xaxis_transform() at TRI_Y<0,
            # so event.inaxes can be None when clicking them.

            # clear any existing hover‐highlight scatter
            if getattr(self, '_highlight_scatter', None):
                try:
                    self._highlight_scatter.remove()
                except Exception:
                    pass
                self._highlight_scatter = None
                self._highlight_point = None
            # remove previous annotation
            if self._annotation:
                self._annotation.remove()
                self._annotation = None
                event.canvas.draw_idle()
            # remove any sperm overlay dots lingering from hover
            for dot in getattr(self, 'sperm_overlay_dots', []):
                try:
                    dot.remove()
                except Exception as exc:
                    logging.debug("Failed to remove sperm overlay dot: %s", exc)
            self.sperm_overlay_dots.clear()

            closest = None
            min_px = float('inf')
            for entry in self.hover_data:
                # support multiple formats with optional probennummer:
                #  - sperm lines:    8-tuple (d, count, mot_pct, prog_pct, ax, dtype, name, line)
                #  - pdg_conv:       8-tuple (d, v, ax, 'pdg_conv', name, line, orig_pdg, probennummer)
                #  - pdg_conv old:   7-tuple (d, v, ax, 'pdg_conv', name, line, orig_pdg)
                #  - with probe:     7-tuple (d, v, ax, dtype, name, line, probennummer)
                #  - without probe:  6-tuple (d, v, ax, dtype, name, line)
                if len(entry) == 8 and entry[5] in ('sperm_total','sperm_motility','sperm_progressive'):
                    d, count, mot_pct, prog_pct, ax, data_type, name, line = entry
                    # Get actual plotted y-value from the line data
                    line_xdata = line.get_xdata()
                    line_ydata = line.get_ydata()
                    # Find matching date in line data
                    d_num = mdates.date2num(d)
                    idx = np.argmin(np.abs(np.array([mdates.date2num(x) for x in line_xdata]) - d_num))
                    v = line_ydata[idx] if idx < len(line_ydata) else count
                elif len(entry) == 8 and entry[3] == 'pdg_conv':
                    d, v, ax, data_type, name, line, orig_pdg, probennummer = entry
                elif len(entry) == 7 and entry[3] == 'pdg_conv':
                    d, v, ax, data_type, name, line, orig_pdg = entry
                elif len(entry) == 7:
                    d, v, ax, data_type, name, line, probennummer = entry
                else:
                    d, v, ax, data_type, name, line = entry
                # Compute display coords robustly
                # Only use axis transform for actual triangle events (fsh, progesterone injections at TRI_Y)
                is_triangle_event = (data_type in ('fsh', 'progesterone') 
                                    and isinstance(v, (int, float)) 
                                    and abs(v - TRI_Y) < 1e-6)
                if is_triangle_event:
                    x_num = mdates.date2num(d)
                    x_px, y_px = ax.get_xaxis_transform().transform((x_num, TRI_Y))
                    local_thresh = max(SELECT_PIXEL_THRESHOLD, 18)
                else:
                    if hasattr(line, "get_offsets"):
                        offs = line.get_offsets()
                        if len(offs) == 0:
                            continue
                        x_off, y_off = offs[0]
                        x_px, y_px = line.get_transform().transform((x_off, y_off))
                    else:
                        x_px, y_px = line.get_transform().transform((mdates.date2num(d), v))
                    local_thresh = SELECT_PIXEL_THRESHOLD

                dx, dy = event.x - x_px, event.y - y_px
                dist_px = np.hypot(dx, dy)
                if dist_px < min_px and dist_px <= local_thresh:
                    min_px = dist_px
                    closest = entry
            
            # Handle middle button click in empty space for panning
            if not closest and event.button == 2 and event.inaxes:
                nonlocal _pan_active, _pan_start_x, _pan_axis
                _pan_active = True
                _pan_start_x = event.xdata
                _pan_axis = event.inaxes
                return
            
            if not closest:
                return
            # unpack the chosen entry the same way
            probennummer = None
            orig_pdg = None
            if len(closest) == 8 and closest[5] in ('sperm_total','sperm_motility','sperm_progressive'):
                d, count, mot_pct, prog_pct, ax, data_type, name, line = closest
                v = count
            elif len(closest) == 8 and closest[3] == 'pdg_conv':
                d, v, ax, data_type, name, line, orig_pdg, probennummer = closest
            elif len(closest) == 7 and closest[3] == 'pdg_conv':
                # pdg_conv entries are 7-tuples: (d, v, ax, 'pdg_conv', name, line, orig_pdg)
                d, v, ax, data_type, name, line, orig_pdg = closest
            elif len(closest) == 7:
                d, v, ax, data_type, name, line, probennummer = closest
            else:
                d, v, ax, data_type, name, line = closest
            # Build tooltip text via unified helper
            extra_data = {}
            # For sperm entries include motility and progressive percentages
            if data_type in ('sperm_total', 'sperm_motility', 'sperm_progressive'):
                # mot_pct and prog_pct may be undefined in this scope for non-applicable types
                extra_data['mot_pct'] = locals().get('mot_pct')
                extra_data['prog_pct'] = locals().get('prog_pct')
            elif data_type == 'pdg_conv':
                # include original PdG value if available (only for pdg_conv, not for regular progesterone)
                if orig_pdg is not None:
                    extra_data['orig_pdg'] = orig_pdg
            # Add probennummer if available (for progesterone, pdg, and pdg_conv)
            if probennummer:
                extra_data['probennummer'] = probennummer
            # Compose text
            # Only triangles (drawn under the x-axis) should show a date-only label.
            # We detect them by their sentinel y = TRI_Y in hover_data.
            is_axis_triangle = (data_type in ('fsh', 'progesterone')
                                and isinstance(v, (int, float))
                                and abs(v - TRI_Y) < 1e-6)
            v_for_text = None if is_axis_triangle else v
            text = self._format_tooltip(data_type, d, v_for_text, name, extra=extra_data)

            # (above we have reset all sperm dots → now highlight the one under the cursor)
            if data_type in ('sperm_total', 'sperm_motility', 'sperm_progressive'):
                # Use the actual marker from the line being clicked
                marker = line.get_marker() if hasattr(line, 'get_marker') else 'o'
                dot = ax.scatter(
                    [d], [v],
                    marker=marker, s=100,
                    facecolors='yellow', edgecolors='black',
                    alpha=0.9,
                    zorder=9999
                )
                self.sperm_overlay_dots.append(dot)
            # compute screen‐coords for the annotation
            # Only use axis transform for actual triangle events (fsh, progesterone injections at TRI_Y)
            is_triangle_event = (data_type in ('fsh', 'progesterone') 
                                and isinstance(v, (int, float)) 
                                and abs(v - TRI_Y) < 1e-6)
            if is_triangle_event:
                x_num = mdates.date2num(d)
                disp_x, disp_y = ax.get_xaxis_transform().transform((x_num, TRI_Y))
            else:
                x_num = mdates.date2num(d)
                disp_x, disp_y = ax.transData.transform((x_num, v))
            fig = ax.figure
            fig_x, fig_y = fig.transFigure.inverted().transform((disp_x, disp_y))

            # remove any old annotation
            if self._annotation:
                self._annotation.remove()

            self._annotation = fig.text(
                fig_x, fig_y,
                text,
                transform=fig.transFigure,
                bbox=dict(boxstyle="round,pad=0.5", fc="yellow", alpha=0.9),
                verticalalignment='center',
                zorder=9999,
                clip_on=False
            )

            # remove the overlay dot immediately after annotation spawns
            for od in self.sperm_overlay_dots:
                od.remove()
            self.sperm_overlay_dots.clear()
            # trigger redraw to clear overlay
            event.canvas.draw_idle()

        # ------------------------
        # 7.18.14 Button release handler to end panning
        # ------------------------
        def on_release(event):
            nonlocal _pan_active, _pan_start_x, _pan_axis
            if event.button == 2:
                _pan_active = False
                _pan_start_x = None
                _pan_axis = None

        # ------------------------
        # 7.18.15 Create and configure FigureCanvas
        # ------------------------
        canvas = FigureCanvas(fig)

        # ------------------------
        # 7.18.15 Ensure Urine-mode overlays reapply on resize
        # ------------------------
        def _on_resize(event):
            if self.current_canvas:
                self._apply_mode()
        canvas.mpl_connect('resize_event', _on_resize)
        #disconnect old mpl callbacks if present
        for cid in getattr(self, '_mpl_cids', []):
            try:
                fig.canvas.mpl_disconnect(cid)
            except Exception:
                pass
        self._mpl_cids = []

        # ------------------------
        # 7.18.16 Register Matplotlib event handlers
        # ------------------------
        self._mpl_cids.append(fig.canvas.mpl_connect('scroll_event', on_scroll))
        self._mpl_cids.append(fig.canvas.mpl_connect('button_press_event', on_click))
        self._mpl_cids.append(fig.canvas.mpl_connect('motion_notify_event', on_move))
        self._mpl_cids.append(fig.canvas.mpl_connect('button_release_event', on_release))
        # self._mpl_cids.append(fig.canvas.mpl_connect('pick_event', on_pick)) 

        # ------------------------
        # 7.18.17 Finalize canvas integration
        # ------------------------
        self.current_canvas = canvas


        # ------------------------
        # 7.18.18 Adjust layout stretch based on selection count
        # ------------------------
        self.dlay.addWidget(canvas)

        count = len(self.selected_animals)
        if count in (1, 2):
            self.dlay.addStretch(7)
        elif 3 <= count <= 5:
            self.dlay.addStretch(1)

        # ------------------------
        # 7.18.19 Format X-axis tick labels
        # ------------------------
        for ax in axes:
            for label in ax.get_xticklabels():
                label.set_rotation(45)
                label.set_ha('right')
                label.set_fontsize(8)
        for ax in axes:
            for label in ax.get_xticklabels():
                label.set_rotation(45)
                label.set_ha('right')
                label.set_fontsize(8)

        # ------------------------
        # 7.18.20 Update last plotted state
        # ------------------------
        self.last_plotted_animals = self.selected_animals.copy()

        # ------------------------
        # 7.18.21 Reset highlight state
        # ------------------------
        self._highlight_scatter = None
        self._highlight_point = None
        self._annotation = None

        # ------------------------
        # 7.18.22 Display summary statistics below plots
        # ------------------------
        if steroid_active:
            # Filter out heritage-only animals that aren't in main animals list
            plot_animals = [n for n in self.selected_animals if n in self.animals]
            for name in plot_animals:
                a = self.animals[name]
                rolle = a.get('rolle')
                
                # Skip statistics for partner animals
                if rolle == Role.PARTNER.value:
                    continue
                    
                # Use centralized statistics function for consistency
                stats_text = self._get_event_statistics(a)
                if stats_text and stats_text != '-':
                    stats = f"{name}: {stats_text}"
                    self.dlay.addWidget(QLabel(stats))
    # ------------------------
    # 7.19 Build Editable List
    #     Construct the editable animal selection list with controls.
    # ------------------------
    def _build_editable_list(self, title: str, items: List[Any],
                             format_item: Callable[[Any], Tuple[str, Optional[str], Optional[str]]],
                             add_default: Callable[[List[Any]], Tuple[str, Optional[str], Optional[str]]],
                             col_headers: Optional[Tuple[str, str, str]] = None
                             ) -> Tuple[QScrollArea, List[Tuple[QLineEdit, Optional[QLineEdit], Optional[QLineEdit]]]]:
        """Build a scrollable list for editing items with date validation and optional sample ID."""
        frame = QFrame()
        frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        widgets: List[Tuple[QLineEdit, Optional[QLineEdit], Optional[QLineEdit]]] = []
        
        # Add column headers if provided
        if col_headers:
            header_layout = QHBoxLayout()
            header_layout.setContentsMargins(0, 0, 0, 5)
            header_layout.setSpacing(5)
            # Add headers with proper stretch factors matching the rows exactly
            for i, header_text in enumerate(col_headers):
                # Skip empty headers (like sample ID for weight entries)
                if not header_text:
                    continue
                header_label = QLabel(f"<b>{header_text}</b>")
                header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if i == 2:  # Sample ID column - use fixed width to match input field
                    header_label.setFixedWidth(120)
                    header_layout.addWidget(header_label, 0)  # No stretch
                else:
                    # Date and Value columns - use stretch factor 1 to match input fields
                    header_layout.addWidget(header_label, 1)  # Stretch factor 1
            # Add space for delete button
            del_header = QLabel(f"<b>{self.messages.get('table.header.delete', 'Delete')}</b>")
            del_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_header.setFixedWidth(50)
            header_layout.addWidget(del_header, 0)  # No stretch
            layout.addLayout(header_layout)
        
        # Localized "New" button with the list title
        add_btn = QPushButton(self.messages["button.new_item"].format(title=title))
        layout.addWidget(add_btn)

        # ------------------------
        # 7.19.1 Add Row to Editable List
        #     Insert or update a row in the editable animal list widget.
        # ------------------------
        def add_row(item_data: Tuple[str, Optional[str], Optional[str]]) -> None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            d_edit = QLineEdit(item_data[0])
            d_edit.setPlaceholderText(self.messages["form.placeholder.date"])
            
            # Add real-time date validation styling
            def validate_date_field():
                date_text = d_edit.text().strip()
                if date_text:
                    try:
                        datetime.strptime(date_text, DATE_FORMAT)
                        d_edit.setStyleSheet("")  # Valid - clear any error styling
                    except ValueError:
                        d_edit.setStyleSheet("border: 2px solid red;")  # Invalid - red border
                else:
                    d_edit.setStyleSheet("")  # Empty - no styling
            
            d_edit.textChanged.connect(validate_date_field)
            
            w_edit = QLineEdit(item_data[1]) if item_data[1] is not None else None
            # Add sample ID field only if col_headers indicates it should exist (non-empty header)
            has_sample_id = col_headers and len(col_headers) > 2 and col_headers[2]
            probe_edit = None
            if has_sample_id:
                probe_edit = QLineEdit(item_data[2] if len(item_data) > 2 and item_data[2] else "")
                probe_edit.setPlaceholderText(self.messages.get("form.placeholder.sample_id", "Sample ID"))
                probe_edit.setFixedWidth(120)  # Fixed width to match header
            del_btn = QPushButton('×')
            del_btn.setFixedWidth(50)  # Fixed width for delete button

        # ------------------------
        # 7.19.1.1 Delete Row from Editable List
        #     Remove the specified entry from the editable animal list widget.
        # ------------------------
            def delete_row() -> None:
                # Show confirmation dialog
                reply = self._show_message_raw(
                    self.messages.get("dialog.confirm_delete.title", "Confirm Deletion"),
                    self.messages.get("dialog.confirm_delete.message", "Do you really wish to delete this entry?"),
                    "question",
                    buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                # Proceed with deletion
                for i in range(layout.count()):
                    it = layout.itemAt(i)
                    if it.layout() is row:
                        while row.count():
                            item = row.takeAt(0)
                            widget = item.widget()
                            if widget:
                                widget.deleteLater()
                        layout.takeAt(i)
                        break
                if (d_edit, w_edit, probe_edit) in widgets:
                    widgets.remove((d_edit, w_edit, probe_edit))

            del_btn.clicked.connect(delete_row)
            row.addWidget(d_edit, 1)  # Stretch factor 1
            if w_edit:
                row.addWidget(w_edit, 1)  # Stretch factor 1
            if probe_edit:
                row.addWidget(probe_edit, 0)  # No stretch for sample ID
            row.addWidget(del_btn, 0)  # No stretch for delete button

            idx = layout.indexOf(add_btn)
            layout.insertLayout(idx, row)
            widgets.append((d_edit, w_edit, probe_edit))

        add_btn.clicked.connect(lambda: add_row(add_default(widgets)))
        for item in items:
            add_row(format_item(item))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(frame)
        return scroll, widgets

    # ------------------------
    # 7.20 Build Reproduction Event List
    #     Create and configure the list of reproduction events (PGF, embryo transfer, etc.).
    # ------------------------
    def _build_repro_event_list(
            self,
            title: str,
            items: List[Dict[str, Any]],
            format_item: Callable[[Dict[str, Any]], Tuple[str, str]],
            add_default: Callable[[List[Any]], Tuple[str, str]],
            role_cb: Optional[QComboBox] = None
    ) -> Tuple[QScrollArea, List[Tuple[QLineEdit, QComboBox]]]:
        """Build a scrollable list for editing reproductive events with date and type."""
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        widgets: List[Tuple[QLineEdit, QComboBox]] = []
        
        # Add column headers
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 5)
        header_layout.setSpacing(5)
        date_header = QLabel(f"<b>{self.messages.get('table.header.date', 'Date')}</b>")
        date_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        type_header = QLabel(f"<b>{self.messages.get('table.header.event_type', 'Event Type')}</b>")
        type_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        del_header = QLabel(f"<b>{self.messages.get('table.header.delete', 'Delete')}</b>")
        del_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        del_header.setFixedWidth(50)
        header_layout.addWidget(date_header, 1)  # Stretch factor 1
        header_layout.addWidget(type_header, 1)  # Stretch factor 1
        header_layout.addWidget(del_header, 0)  # No stretch
        layout.addLayout(header_layout)
        
        # Localized "New" button with the list title
        add_btn = QPushButton(self.messages["button.new_item"].format(title=title))
        layout.addWidget(add_btn)

        # ------------------------
        # 7.20.1 Add Row to Reproduction Event List
        #     Insert a new event row (date & type) into the scrollable list.
        # ------------------------
        def add_row(item_data: Tuple[str, str]) -> None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            d_edit = QLineEdit(item_data[0])
            d_edit.setPlaceholderText(self.messages["form.placeholder.date"])
            
            # Add real-time date validation styling
            def validate_date_field():
                date_text = d_edit.text().strip()
                if date_text:
                    try:
                        datetime.strptime(date_text, DATE_FORMAT)
                        d_edit.setStyleSheet("")  # Valid - clear any error styling
                    except ValueError:
                        d_edit.setStyleSheet("border: 2px solid red;")  # Invalid - red border
                else:
                    d_edit.setStyleSheet("")  # Empty - no styling
            
            d_edit.textChanged.connect(validate_date_field)
            
            combo = QComboBox()
            
            # Filter events based on role if role_cb is provided
            # Use the underlying role code from userData so this stays
            # correct even when the role combobox shows localized labels.
            current_role = role_cb.currentData() if role_cb else None
            
            # Define all blocks using English identifiers
            all_blocks = [
                ['surgery'],
                ['embryo_transfer', 'pregnancy', 'abortion', 'birth'],
                ['pgf', 'fsh', 'progesterone']
            ]
            
            # Filter blocks based on role
            if current_role == Role.SPENDER.value:
                # For Spenderin: hide embryo_transfer, pregnancy, abortion, birth
                blocks = [
                    ['surgery'],
                    ['pgf', 'fsh', 'progesterone']
                ]
            elif current_role == Role.AMME.value:
                # For Amme: hide fsh and surgery
                blocks = [
                    ['embryo_transfer', 'pregnancy', 'abortion', 'birth'],
                    ['pgf', 'progesterone']
                ]
            else:
                # Default: all events
                blocks = all_blocks
            
            # Populate combo box with filtered blocks using localized names
            event_labels = {
                'surgery': self.messages.get('event.surgery', 'Surgery'),
                'embryo_transfer': self.messages.get('event.embryo_transfer', 'Embryo Transfer'),
                'pregnancy': self.messages.get('event.pregnancy', 'Pregnancy'),
                'abortion': self.messages.get('event.abort', 'Abort'),
                'birth': self.messages.get('event.birth', 'Birth'),
                'pgf': self.messages.get('event.pgf', 'PGF'),
                'fsh': self.messages.get('event.fsh', 'FSH'),
                'progesterone': self.messages.get('event.progesterone', 'Progesterone')
            }
            
            for idx, block in enumerate(blocks):
                for typ in block:
                    label = event_labels.get(typ, typ.capitalize())
                    combo.addItem(label, typ)
                # insert a visual separator after each block except the last
                if idx < len(blocks) - 1:
                    combo.insertSeparator(combo.count())
            
            # set current selection by matching the stored type
            stored_type = item_data[1].lower()
            for i in range(combo.count()):
                if combo.itemData(i) == stored_type:
                    combo.setCurrentIndex(i)
                    break
            del_btn = QPushButton('×')
            del_btn.setFixedWidth(50)  # Fixed width for delete button

        # ------------------------
        # 7.20.1.1 Delete Row from Reproduction Event List
        #     Remove the specified event row widget from the reproduction event list.
        # ------------------------
            def delete_row() -> None:
                # Show confirmation dialog
                reply = self._show_message_raw(
                    self.messages.get("dialog.confirm_delete.title", "Confirm Deletion"),
                    self.messages.get("dialog.confirm_delete.message", "Do you really wish to delete this entry?"),
                    "question",
                    buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                # Proceed with deletion
                for i in range(layout.count()):
                    if layout.itemAt(i).layout() is row:
                        while row.count():
                            item = row.takeAt(0)
                            widget = item.widget()
                            if widget:
                                widget.deleteLater()
                        layout.takeAt(i)
                        break
                if (d_edit, combo) in widgets:
                    widgets.remove((d_edit, combo))

            del_btn.clicked.connect(delete_row)
            row.addWidget(d_edit, 1)  # Stretch factor 1
            row.addWidget(combo, 1)  # Stretch factor 1
            row.addWidget(del_btn, 0)  # No stretch

            idx = layout.indexOf(add_btn)
            layout.insertLayout(idx, row)
            widgets.append((d_edit, combo))

        add_btn.clicked.connect(lambda: add_row(add_default(widgets)))
        for item in items:
            add_row(format_item(item))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(frame)
        return scroll, widgets

    # ------------------------
    # 7.21 New Animal Dialog
    #     Display a dialog allowing the user to add a new animal record.
    # ------------------------
    def _dlg_new_animal(self) -> None:
        """
        Unified dialog for creating a new female animal (donor or surrogate).  This
        function now delegates to the common editor used for both new and existing
        animals, ensuring a consistent user experience across creation and
        modification.  The active category tab determines which type of dialog
        to open.
        """
        if not self._master_can('core.create_animals'):
            self._show_permission_denied()
            return
        idx = self.category_tab.currentIndex()
        # ♂ tab → Samenspender
        if idx == 1:
            return self._dlg_samenspender(None)
        # 👶 tab → Offspring
        if idx == 2:
            return self._dlg_offspring(None)
        # 🐾 tab → Partner
        if idx == 3:
            return self._dlg_partner(None)
        # ⚤ tab → Zuchttier
        if idx == 4:
            return self._dlg_zuchttier(None)
        # 💡 tab → Versuchstier
        if idx == 5:
            return self._dlg_versuchstier(None)
        # All tab (idx 6) → role-selector helper
        if idx == 6:
            return self._dlg_new_animal_with_role_selector()
        # Default (♀ or other) → female donor/surrogate dialog
        return self._dlg_female_animal(None)

    def _dlg_new_animal_with_role_selector(self):
        """r11: Ask which role to create, then open the matching dialog."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QDialogButtonBox
        dlg = QDialog(self)
        dlg.setWindowTitle(self.messages.get('dialog.new_animal.select_role', 'Select Role'))
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(self.messages.get('dialog.new_animal.select_role_prompt',
                                             'Choose a role for the new animal:')))
        lw = QListWidget()
        role_options = []
        steroid_active = self._is_steroid_track_active()
        for role in self._active_animal_role_definitions():
            value = role.get("value", "")
            if value == Role.UNKNOWN.value:
                continue
            if not steroid_active and self._is_steroid_role_value(value):
                continue
            role_options.append((self._role_label_with_icon(value), value))
        if not role_options:
            role_options.append((self._role_label_with_icon(Role.OFFSPRING.value), Role.OFFSPRING.value))

        for label, _role_value in role_options:
            lw.addItem(label)
        lw.setCurrentRow(0)
        v.addWidget(lw)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted: return
        chosen = lw.currentRow()
        if 0 <= chosen < len(role_options):
            role_value = role_options[chosen][1]
            self._dialog_for_role_value(role_value)(None)


    # --- Moved from top-level into class ProgTrackApp ---
    def _dlg_print_data(self) -> None:
        """Open dialog to select animals and date range for printing to Excel."""
        dlg = QDialog(self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle(self.messages["dialog.print_data.title"])
        layout = QVBoxLayout(dlg)

        select_all_cb = QCheckBox(self.messages["checkbox.select_all"])
        layout.addWidget(select_all_cb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(1)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        animal_cbs = {}
        role_header_widgets = {}
        steroid_active = self._is_steroid_track_active()

        role_groups, role_order, role_labels = self._build_export_role_groups(
            steroid_active=steroid_active,
            visible_only=True,
        )
        
        # Add animals grouped by role with separators
        first_group = True

        for role in role_order:
            animals_in_role = role_groups[role]
            if not animals_in_role:
                continue
            
            rh_widgets = []
            # Add separator line before each group (except first)
            if not first_group:
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                scroll_layout.addWidget(separator)
                rh_widgets.append(separator)
            first_group = False
            
            # Add role label
            role_label = QLabel(role_labels[role])
            role_label.setStyleSheet("font-weight: bold; color: #555; padding-top: 4px;")
            scroll_layout.addWidget(role_label)
            rh_widgets.append(role_label)
            role_header_widgets[role] = rh_widgets
            
            # Add animal checkboxes for this role
            for name in sorted(animals_in_role):
                cb = QCheckBox(self._display_name(name))
                animal_cbs[name] = cb
                scroll_layout.addWidget(cb)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)

        # Content row: filter sidebar (if ProjectsTrack active) + scroll area
        content_row = QHBoxLayout()
        filter_panel = self._build_export_filter_panel(animal_cbs, role_header_widgets)
        if filter_panel:
            content_row.addWidget(filter_panel)
        content_row.addWidget(scroll, 1)
        layout.addLayout(content_row)

        form = QFormLayout()
        von_date = QDateEdit()
        von_date.setCalendarPopup(True)
        von_date.setDate(QDate.currentDate().addMonths(-1))
        bis_date = QDateEdit()
        bis_date.setCalendarPopup(True)
        bis_date.setDate(QDate.currentDate())
        form.addRow(self.messages["form.label.from"], von_date)
        form.addRow(self.messages["form.label.to"], bis_date)
        layout.addLayout(form)

        def toggle_all(checked):
            for cb in animal_cbs.values():
                if cb.isVisible():
                    cb.setChecked(checked)
        select_all_cb.toggled.connect(toggle_all)

        print_btn = QPushButton(self.messages["button.export_xlsx"])
        def do_print():
            selected_animals = [name for name, cb in animal_cbs.items() if cb.isChecked()]
            if not selected_animals:
                self._show_message("error.print.no_selection")
                return

            von = von_date.date().toPyDate()
            bis = bis_date.date().toPyDate()
            if von > bis:
                self._show_message("error.print.date_order")
                return
            if von == bis:
                self._show_message("warning.print.same_date")

            path, _ = QFileDialog.getSaveFileName(
                self,
                self.messages["dialog.save_excel.title"],
                str(default_export_directory()),
                self.messages["dialog.save_excel.filter"]
            )
            if not path:
                return

            try:
                import openpyxl
                with pd.ExcelWriter(path, engine='openpyxl') as writer:
                    used_sheet_names = set()
                    for name in selected_animals:
                        animal = self.animals[name]
                        animal_display = self._display_name(name)
                        role = animal.get('rolle')
                        is_sperm_role = steroid_active and role == Role.SAMENSP.value
                        if role == Role.SAMENSP.value and not steroid_active:
                            continue
                        data = []
                        
                        # Only include progesterone data for Spenderin and Amme
                        if role in (Role.SPENDER.value, Role.AMME.value):
                            for rec in animal.get('daten', []):
                                dt = rec.get('datum')
                                if isinstance(dt, datetime) and von <= dt.date() <= bis:
                                    data.append({
                                        'IPID': name,
                                        'Name': animal_display,
                                        'Datum': dt.strftime(DATE_FORMAT),
                                        self.messages.get('export.header.progesterone', 'Progesteron (ng/ml)'): rec['wert'],
                                        'F': '',
                                        self.messages.get('export.header.pdg', 'PdG (µg/mg Cr)'): ''
                                    })
                        
                        # For Samenspender, include sperm data instead
                        if is_sperm_role:
                            for sperm in animal.get('sperm', []):
                                dt = sperm.get('datum')
                                if isinstance(dt, datetime) and von <= dt.date() <= bis:
                                    mot = sperm.get('motility', '')
                                    prog = sperm.get('progressive', '')
                                    count = sperm.get('count', '')
                                    data.append({
                                        'IPID': name,
                                        'Name': animal_display,
                                        'Datum': dt.strftime(DATE_FORMAT),
                                        self.messages.get('export.header.motility', 'Motility (%)'): mot,
                                        self.messages.get('export.header.progressive', 'Progressive (%)'): prog,
                                        self.messages.get('export.header.count', 'Count (/ml)'): count,
                                        'F': ''
                                    })
                        # ------------------------
                        # 7.24.1 Merge reproduction events for Excel export
                        # ------------------------
                        repro_events = []
                        # Only include reproductive events for Spenderin and Amme
                        if role in (Role.SPENDER.value, Role.AMME.value):
                            repro_events += [('pgf', dt) for dt in animal.get('pgf', [])]
                            if role == Role.AMME.value:
                                repro_events += [('embryo', dt) for dt in animal.get('embryo', [])]
                            else:
                                repro_events += [('op', dt) for dt in animal.get('op', [])]
                            repro_events += [(ev['typ'], ev['datum']) for ev in animal.get('events', [])]

                            for typ, dt in repro_events:
                                if isinstance(dt, datetime) and von <= dt.date() <= bis:
                                    data.append({
                                        'IPID': name,
                                        'Name': animal_display,
                                        'Datum': dt.strftime(DATE_FORMAT),
                                        self.messages.get('export.header.progesterone', 'Progesteron (ng/ml)'): '',
                                        'F': typ,
                                        self.messages.get('export.header.pdg', 'PdG (µg/mg Cr)'): ''
                                    })
                        elif is_sperm_role:
                            # For Samenspender, include events with sperm columns
                            repro_events += [(ev['typ'], ev['datum']) for ev in animal.get('events', [])]
                            for typ, dt in repro_events:
                                if isinstance(dt, datetime) and von <= dt.date() <= bis:
                                    data.append({
                                        'IPID': name,
                                        'Name': animal_display,
                                        'Datum': dt.strftime(DATE_FORMAT),
                                        'Motility (%)': '',
                                        'Progressive (%)': '',
                                        'Count (/ml)': '',
                                        'F': typ
                                    })

                        for w in animal.get('gewicht', []):
                            dt = w.get('datum')
                            if isinstance(dt, datetime) and von <= dt.date() <= bis:
                                dt_str = dt.strftime(DATE_FORMAT)
                                match = next((entry for entry in data
                                              if entry['Datum'] == dt_str and entry.get('IPID') == name),
                                             None)
                                if match:
                                    match[self.messages.get('export.header.weight', 'Weight (g)')] = w.get('wert')
                                else:
                                    # Create appropriate structure based on role
                                    if is_sperm_role:
                                        data.append({
                                            'IPID': name,
                                            'Name': animal_display,
                                            'Datum': dt_str,
                                            self.messages.get('export.header.motility', 'Motility (%)'): '',
                                            self.messages.get('export.header.progressive', 'Progressive (%)'): '',
                                            self.messages.get('export.header.count', 'Count (/ml)'): '',
                                            'F': '',
                                            self.messages.get('export.header.weight', 'Weight (g)'): w.get('wert')
                                        })
                                    elif role in (Role.SPENDER.value, Role.AMME.value):
                                        data.append({
                                            'IPID': name,
                                            'Name': animal_display,
                                            'Datum': dt_str,
                                            self.messages.get('export.header.progesterone', 'Progesteron (ng/ml)'): '',
                                            'F': '',
                                            self.messages.get('export.header.weight', 'Weight (g)'): w.get('wert'),
                                            self.messages.get('export.header.pdg', 'PdG (µg/mg Cr)'): ''
                                        })
                                    else:
                                        # For Offspring, Partners, Zuchttiere: minimal columns
                                        data.append({
                                            'IPID': name,
                                            'Name': animal_display,
                                            'Datum': dt_str,
                                            'F': '',
                                            self.messages.get('export.header.weight', 'Weight (g)'): w.get('wert')
                                        })

                        # ------------------------
                        # 7.24.2 Merge PdG raw data into existing rows or append new rows
                        # Only for Spenderin and Amme
                        # ------------------------
                        if role in (Role.SPENDER.value, Role.AMME.value):
                            for p in animal.get('pdg', []):
                                dt = p.get('datum')
                                if not (isinstance(dt, datetime) and von <= dt.date() <= bis):
                                    continue
                                date_str = dt.strftime(DATE_FORMAT)
                                # look for an existing row with the same Datum
                                for entry in data:
                                    if entry['Datum'] == date_str:
                                        entry[self.messages.get('export.header.pdg', 'PdG (µg/mg Cr)')] = p.get('wert')
                                        break
                                else:
                                    # no existing row → append new one
                                    data.append({
                                        'IPID': name,
                                        'Name': animal_display,
                                        'Datum': date_str,
                                        self.messages.get('export.header.progesterone', 'Progesteron (ng/ml)'): '',
                                        'F': '',
                                        self.messages.get('export.header.pdg', 'PdG (µg/mg Cr)'): p.get('wert')
                                    })

                        # ------------------------
                        # 7.24.3 Sort entries by date and write to Excel sheets
                        # ------------------------
                        data.sort(key=lambda entry: datetime.strptime(entry['Datum'], DATE_FORMAT))
                        if data:
                            df = pd.DataFrame(data)
                            df['Datum'] = pd.to_datetime(df['Datum'], format=DATE_FORMAT, dayfirst=True)
                            df.sort_values('Datum', inplace=True)
                            df['Datum'] = df['Datum'].dt.strftime(DATE_FORMAT)
                            base_sheet = re.sub(r'[\\/\\:*?"<>|]', '_', animal_display)[:31] or "Animal"
                            sheet_name = base_sheet
                            counter = 2
                            while sheet_name in used_sheet_names:
                                suffix = f"_{counter}"
                                sheet_name = f"{base_sheet[:31-len(suffix)]}{suffix}"
                                counter += 1
                            used_sheet_names.add(sheet_name)
                            df.to_excel(writer, sheet_name=sheet_name, index=False, startrow=1)
                            
                            # Add header row with Project and Date Range
                            worksheet = writer.sheets[sheet_name]
                            project = animal.get('project', '')
                            date_range_text = f"{von.strftime(DATE_FORMAT)} - {bis.strftime(DATE_FORMAT)}"
                            if project:
                                header_text = f"Animal: {animal_display}  |  IPID: {name}  |  Project: {project}  |  Date Range: {date_range_text}"
                            else:
                                header_text = f"Animal: {animal_display}  |  IPID: {name}  |  Date Range: {date_range_text}"
                            worksheet.cell(row=1, column=1, value=header_text)

                # ------------------------
                # 7.24.4 Show success message and log export completion
                # ------------------------
                self._show_message("info.print.saved", path=path)
                logging.info(f"Printed data for {len(selected_animals)} animals to {path}")
                dlg.accept()

            except ImportError as e:
                # ------------------------
                # 7.24.5 Handle missing openpyxl dependency
                # ------------------------
                self._show_message("error.print.openpyxl_not_installed")
                logging.error(f"Openpyxl not installed: {e}")

            except Exception as e:
                # ------------------------
                # 7.24.6 Handle unexpected errors during export
                # ------------------------
                self._show_message("error.print.save_failed", error=e)
                logging.error(f"Failed to print data to Excel: {e}")
                
        print_btn.clicked.connect(do_print)
        layout.addWidget(print_btn)

        dlg.exec()

    def _dlg_export_pdf(self) -> None:
        """Open dialog to select animals and date range for exporting to PDF reports."""
        dlg = QDialog(self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle(self.messages.get("dialog.export_pdf.title", "Export PDF Reports"))
        layout = QVBoxLayout(dlg)

        select_all_cb = QCheckBox(self.messages["checkbox.select_all"])
        layout.addWidget(select_all_cb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(1)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        animal_cbs = {}
        role_header_widgets = {}
        steroid_active = self._is_steroid_track_active()

        role_groups, role_order, role_labels = self._build_export_role_groups(
            steroid_active=steroid_active,
            visible_only=False,
        )
        
        # Add animals grouped by role with separators
        first_group = True

        for role in role_order:
            animals_in_role = role_groups[role]
            if not animals_in_role:
                continue
            
            rh_widgets = []
            # Add separator line before each group (except first)
            if not first_group:
                separator = QFrame()
                separator.setFrameShape(QFrame.Shape.HLine)
                separator.setFrameShadow(QFrame.Shadow.Sunken)
                scroll_layout.addWidget(separator)
                rh_widgets.append(separator)
            first_group = False
            
            # Add role label
            role_label = QLabel(role_labels[role])
            role_label.setStyleSheet("font-weight: bold; color: #555; padding-top: 4px;")
            scroll_layout.addWidget(role_label)
            rh_widgets.append(role_label)
            role_header_widgets[role] = rh_widgets
            
            # Add animal checkboxes for this role
            for name in sorted(animals_in_role):
                cb = QCheckBox(self._display_name(name))
                animal_cbs[name] = cb
                scroll_layout.addWidget(cb)
        
        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)

        # Content row: filter sidebar (if ProjectsTrack active) + scroll area
        content_row = QHBoxLayout()
        filter_panel = self._build_export_filter_panel(animal_cbs, role_header_widgets)
        if filter_panel:
            content_row.addWidget(filter_panel)
        content_row.addWidget(scroll, 1)
        layout.addLayout(content_row)

        form = QFormLayout()
        von_date = QDateEdit()
        von_date.setCalendarPopup(True)
        von_date.setDate(QDate.currentDate().addMonths(-1))
        bis_date = QDateEdit()
        bis_date.setCalendarPopup(True)
        bis_date.setDate(QDate.currentDate())
        form.addRow(self.messages["form.label.from"], von_date)
        form.addRow(self.messages["form.label.to"], bis_date)
        layout.addLayout(form)
        
        # Language selection group
        lang_group = QGroupBox(self.messages.get("dialog.export_pdf.language", "Report Language"))
        lang_layout = QHBoxLayout(lang_group)
        
        # Detect available languages from lang folder
        lang_dir = os.path.join(os.path.dirname(__file__), 'lang')
        available_languages = []
        language_names = {
            'en': self.messages.get("menu.program.language_settings.english", "English"),
            'de': self.messages.get("menu.program.language_settings.german", "German"),
            'ru': self.messages.get("menu.program.language_settings.russian", "Russian"),
            'it': self.messages.get("menu.program.language_settings.italian", "Italiano"),
        }
        
        if os.path.exists(lang_dir):
            for file in os.listdir(lang_dir):
                if file.startswith('messages_') and file.endswith('.json'):
                    lang_code = file[9:-5]  # Extract language code from filename
                    if lang_code in language_names:
                        available_languages.append(lang_code)
        
        # Sort to ensure consistent order: en, de, ru (if all present)
        available_languages.sort()
        
        # Create radiobuttons for each available language
        lang_radio_buttons = {}
        for lang_code in available_languages:
            rb = QRadioButton(language_names[lang_code])
            lang_radio_buttons[lang_code] = rb
            lang_layout.addWidget(rb)
            # Select current language by default
            if lang_code == self.lang:
                rb.setChecked(True)
        
        # If current language not in available languages, select first one
        if self.lang not in lang_radio_buttons and lang_radio_buttons:
            list(lang_radio_buttons.values())[0].setChecked(True)
        
        lang_layout.addStretch()
        layout.addWidget(lang_group)

        def toggle_all(checked):
            for cb in animal_cbs.values():
                if cb.isVisible():
                    cb.setChecked(checked)
        select_all_cb.toggled.connect(toggle_all)

        export_btn = QPushButton(self.messages.get("button.export_pdf", "Export PDF"))
        def do_export():
            selected_animals = [name for name, cb in animal_cbs.items() if cb.isChecked()]
            if not steroid_active:
                selected_animals = [
                    name for name in selected_animals
                    if self.animals.get(name, {}).get('rolle') != Role.SAMENSP.value
                ]
            if not selected_animals:
                self._show_message("error.print.no_selection")
                return

            von = von_date.date().toPyDate()
            bis = bis_date.date().toPyDate()
            if von > bis:
                self._show_message("error.print.date_order")
                return
            
            # Get selected language
            selected_lang = self.lang  # Default to current language
            for lang_code, rb in lang_radio_buttons.items():
                if rb.isChecked():
                    selected_lang = lang_code
                    break

            # Select output directory
            output_dir = QFileDialog.getExistingDirectory(
                self,
                self.messages.get("dialog.select_output_directory", "Select Output Directory"),
                str(default_export_directory()),
                QFileDialog.Option.ShowDirsOnly
            )
            if not output_dir:
                return

            try:
                # Generate PDFs with progress dialog
                self._generate_pdf_reports(selected_animals, von, bis, output_dir, selected_lang)
                dlg.accept()

            except Exception as e:
                # Escape curly braces in error message to prevent format string issues
                error_msg = str(e).replace('{', '{{').replace('}', '}}')
                self._show_message("error.pdf_export.failed", error=error_msg)
                logging.error(f"Failed to export PDF reports: {e}")
                
        export_btn.clicked.connect(do_export)
        layout.addWidget(export_btn)

        dlg.exec()

    def _dlg_export_medi_track_pdf(self) -> None:
        """Open dialog to select animals for Medi Track PDF export (no date range)."""
        if not getattr(self, 'has_medi_track_plugin', False):
            return

        dlg = QDialog(self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle(self.messages.get("dialog.export_medi_pdf.title", "Export Medi Track PDFs"))
        layout = QVBoxLayout(dlg)

        select_all_cb = QCheckBox(self.messages["checkbox.select_all"])
        layout.addWidget(select_all_cb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(1)
        scroll_layout.setContentsMargins(4, 4, 4, 4)
        animal_cbs = {}
        role_header_widgets = {}
        steroid_active = self._is_steroid_track_active()

        role_groups, role_order, role_labels = self._build_export_role_groups(
            steroid_active=steroid_active,
            visible_only=False,
        )

        first_group = True
        for role in role_order:
            animals_in_role = role_groups.get(role, [])
            if not animals_in_role:
                continue
            rh_widgets = []
            if not first_group:
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setFrameShadow(QFrame.Shadow.Sunken)
                scroll_layout.addWidget(sep)
                rh_widgets.append(sep)
            first_group = False
            lbl = QLabel(role_labels.get(role, role))
            lbl.setStyleSheet("font-weight: bold; color: #555; padding-top: 4px;")
            scroll_layout.addWidget(lbl)
            rh_widgets.append(lbl)
            role_header_widgets[role] = rh_widgets
            for name in sorted(animals_in_role):
                cb = QCheckBox(self._display_name(name))
                animal_cbs[name] = cb
                scroll_layout.addWidget(cb)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)

        content_row = QHBoxLayout()
        filter_panel = self._build_export_filter_panel(animal_cbs, role_header_widgets)
        if filter_panel:
            content_row.addWidget(filter_panel)
        content_row.addWidget(scroll, 1)
        layout.addLayout(content_row)

        def toggle_all(checked):
            for cb in animal_cbs.values():
                if cb.isVisible():
                    cb.setChecked(checked)
        select_all_cb.toggled.connect(toggle_all)

        # Language selection (reuse same pattern as Report PDF export)
        lang_dir = os.path.join(os.path.dirname(__file__), "lang")
        available_languages = []
        language_names = {'de': 'Deutsch', 'en': 'English', 'it': 'Italiano', 'ru': 'Русский'}
        if os.path.exists(lang_dir):
            for _f in os.listdir(lang_dir):
                if _f.startswith('messages_') and _f.endswith('.json'):
                    _lc = _f[9:-5]
                    if _lc in language_names:
                        available_languages.append(_lc)
        available_languages.sort()

        medi_lang_radio_buttons = {}
        if available_languages:
            medi_lang_group = QGroupBox(
                self.messages.get("dialog.export_pdf.language", "Report Language"))
            medi_lang_layout = QHBoxLayout(medi_lang_group)
            medi_lang_bg = QButtonGroup(dlg)
            for lc in available_languages:
                rb = QRadioButton(language_names.get(lc, lc))
                medi_lang_layout.addWidget(rb)
                medi_lang_bg.addButton(rb)
                medi_lang_radio_buttons[lc] = rb
                if lc == self.lang:
                    rb.setChecked(True)
            if self.lang not in medi_lang_radio_buttons and medi_lang_radio_buttons:
                list(medi_lang_radio_buttons.values())[0].setChecked(True)
            medi_lang_layout.addStretch()
            layout.addWidget(medi_lang_group)

        export_btn = QPushButton(self.messages.get("button.export_pdf", "Export PDF"))

        def do_export():
            selected_animals = [name for name, cb in animal_cbs.items() if cb.isChecked()]
            if not selected_animals:
                self._show_message("error.print.no_selection")
                return
            output_dir = QFileDialog.getExistingDirectory(
                self,
                self.messages.get("dialog.select_output_directory", "Select Output Directory"),
                str(default_export_directory()),
                QFileDialog.Option.ShowDirsOnly,
            )
            if not output_dir:
                return
            selected_lang = self.lang
            for lc, rb in medi_lang_radio_buttons.items():
                if rb.isChecked():
                    selected_lang = lc
                    break
            try:
                import os as _os
                medi_widget = self.medi_track_plugin.get_tab_widget()
                errors = []
                for animal_name in selected_animals:
                    safe = ''.join(
                        c for c in animal_name if c.isalnum() or c in ('_', '-', ' ')
                    ).strip().replace(' ', '_')
                    out_path = _os.path.join(
                        output_dir, f"{safe}_medical_history.pdf")
                    try:
                        medi_widget._export_animal_to_pdf(
                            animal_name, out_path, lang=selected_lang)
                    except Exception as exc:
                        errors.append(f"{animal_name}: {exc}")
                if errors:
                    err_text = "\n".join(errors)
                    self._show_message("error.pdf_export.failed", error=err_text)
                else:
                    self._show_message("info.print.saved", path=output_dir)
                dlg.accept()
            except Exception as e:
                self._show_message("error.pdf_export.failed", error=str(e))

        export_btn.clicked.connect(do_export)
        layout.addWidget(export_btn)
        dlg.resize(420, 580)
        dlg.exec()

    def _generate_pdf_reports(self, selected_animals: list, von_date: datetime.date, bis_date: datetime.date, output_dir: str, report_lang: str = None) -> None:
        """Generate PDF reports for selected animals with progress dialog."""
        
        # Use provided language or fall back to current language
        if report_lang is None:
            report_lang = self.lang
        
        # Ensure report data structures exist
        if not hasattr(self, 'report_locked_dates'):
            self.report_locked_dates = set()
        if not hasattr(self, 'report_edits'):
            self.report_edits = {}
        
        # Calculate total number of PDFs to generate (one per animal per month)
        total_pdfs = 0
        animal_months = []
        
        for animal_name in selected_animals:
            current_date = datetime(von_date.year, von_date.month, 1)
            end_date = datetime(bis_date.year, bis_date.month, 1)
            
            while current_date <= end_date:
                animal_months.append((animal_name, current_date.year, current_date.month))
                total_pdfs += 1
                # Move to next month
                if current_date.month == 12:
                    current_date = datetime(current_date.year + 1, 1, 1)
                else:
                    current_date = datetime(current_date.year, current_date.month + 1, 1)
        
        # Create progress dialog
        progress_dlg = QDialog(self)
        progress_dlg.setWindowTitle(self.messages.get("dialog.export_progress.title", "Exporting PDFs"))
        progress_dlg.setWindowModality(Qt.WindowModality.WindowModal)
        progress_dlg.setMinimumWidth(400)
        progress_layout = QVBoxLayout(progress_dlg)
        
        status_label = QLabel()
        progress_layout.addWidget(status_label)
        
        progress_bar = QProgressBar()
        progress_bar.setRange(0, total_pdfs)
        progress_bar.setValue(0)
        progress_layout.addWidget(progress_bar)
        
        cancel_btn = QPushButton(self.messages.get("button.cancel", "Cancel"))
        progress_layout.addWidget(cancel_btn)
        
        cancelled = [False]  # Use list to allow modification in nested function
        
        def cancel_export():
            cancelled[0] = True
            cancel_btn.setEnabled(False)
            status_label.setText(self.messages.get("export.cancelling", "Cancelling..."))
        
        cancel_btn.clicked.connect(cancel_export)
        
        progress_dlg.show()
        QApplication.processEvents()
        
        pdfs_created = 0
        current_loaded_animal = None
        
        try:
            for animal_name, year, month in animal_months:
                if cancelled[0]:
                    break
                
                # Load report data for this animal if we haven't already
                if animal_name != current_loaded_animal:
                    try:
                        self._load_report_data(animal_name)
                        current_loaded_animal = animal_name
                    except Exception as e:
                        logging.warning(f"Could not load report data for {animal_name}: {e}")
                        # Continue with empty report data
                
                # Update progress
                status_label.setText(self.messages.get(
                    "export.status", 
                    "Exporting {animal} - {month}/{year}..."
                ).format(animal=self._display_name(animal_name), month=month, year=year))
                QApplication.processEvents()
                
                # Generate PDF for this animal/month
                animal_data = self.animals[animal_name]
                safe_subject = re.sub(
                    r'[\\/\\:*?"<>|]',
                    '_',
                    animal_identity_label(animal_name, animal_data),
                ).strip().replace(' ', '_')
                pdf_filename = f"{safe_subject}_{year}_{month:02d}.pdf"
                pdf_path = os.path.join(output_dir, pdf_filename)
                
                self._create_single_pdf_report(animal_name, animal_data, year, month, pdf_path, von_date, bis_date, report_lang)
                
                pdfs_created += 1
                progress_bar.setValue(pdfs_created)
                QApplication.processEvents()
            
            progress_dlg.close()
            
            if cancelled[0]:
                QMessageBox.information(
                    self,
                    self.messages.get("info.title", "Information"),
                    self.messages.get("info.pdf_export.cancelled", 
                                    "Export abgebrochen. {pdfs_created} von {total_pdfs} PDFs erstellt.").format(pdfs_created=pdfs_created, total_pdfs=total_pdfs)
                )
            else:
                # Show simple success message
                QMessageBox.information(
                    self,
                    self.messages.get("info.title", "Success"),
                    "All report(s) successfully exported!"
                )
                
                logging.info(f"Created {pdfs_created} PDF reports in {output_dir}")
                
        except Exception as e:
            progress_dlg.close()
            raise e
    
    def _create_single_pdf_report(self, animal_name: str, animal_data: dict, year: int, month: int, output_path: str, von_date: datetime.date = None, bis_date: datetime.date = None, report_lang: str = None) -> None:
        """Create a single PDF report for one animal for one month."""
        try:
            # Use provided language or fall back to current language
            if report_lang is None:
                report_lang = self.lang
            
            # Load messages for the report language
            lang_path = os.path.join(os.path.dirname(__file__), "lang", f"messages_{report_lang}.json")
            try:
                with open(lang_path, encoding="utf-8") as f:
                    report_messages = json.load(f)
            except Exception:
                # Fall back to current language messages if loading fails
                report_messages = self.messages
            
            # Import the Animal_Reports plugin
            import sys
            from pathlib import Path
            plugin_path = Path(__file__).parent / "Plugins" / "Animal_Reports"
            if str(plugin_path) not in sys.path:
                sys.path.insert(0, str(plugin_path))
            
            from animal_reports import create_monthly_report
            
            # Collect animal header information
            # Use localized status function for report
            status_text = self._get_status_localized(animal_name, report_messages) or report_messages.get('status.normal', 'Normal')
            
            # Generate localized statistics
            localized_stats = self._get_event_statistics_localized(animal_data, report_messages)
            
            rolle = animal_data.get('rolle')
            
            # Calculate age as of last day of report month
            import calendar
            last_day = calendar.monthrange(year, month)[1]
            reference_date = datetime(year, month, last_day)
            
            birth_date_str = animal_data.get('birth_date', '')
            death_date_str = animal_data.get('death_date', '')
            
            if birth_date_str and birth_date_str.strip() and birth_date_str != '-':
                age_str = calculate_age_localized(birth_date_str, reference_date, death_date_str, report_messages)
                birth_date_display = f"{birth_date_str} ({age_str})"
            else:
                # No birth date provided
                age_unknown = report_messages.get('age.unknown', '(age unknown)')
                birth_date_display = f"- {age_unknown}"

            formatted_id = self._format_id_with_species(animal_data, messages=report_messages, rich_text=True, include_chip=True)
            report_title_subject = self._format_report_title_subject(
                animal_name,
                animal_data,
                messages=report_messages,
                rich_text=True,
            )
            
            header_info = {
                'Name': self._display_name(animal_name),
                'IPID': animal_name,
                'ID': formatted_id,
                'Chip Nr.': animal_data.get('chip_nr', '') or '-',
                'Origin': animal_data.get('origin', '') or '-',
                'Title Subject': report_title_subject,
                'Project': self._format_project_severity(animal_data) or '-',
                'Role': self._get_localized_role(rolle, report_messages) if rolle is not None else '-',
                'Status': status_summary_with_death_priority(
                    animal_data,
                    report_messages,
                    projects_track_active=self._is_projects_track_active(),
                ),
                'Birth Date': birth_date_display,
                'Genotype': animal_data.get('genotype', '-'),
                'Statistics': localized_stats
            }
            
            # Report data should already be loaded by the calling function
            # Just ensure the structures exist as a safety check
            if not hasattr(self, 'report_locked_dates'):
                self.report_locked_dates = set()
            if not hasattr(self, 'report_edits'):
                self.report_edits = {}
            
            # Collect daily data for dates in the specified range
            import calendar
            num_days = calendar.monthrange(year, month)[1]
            daily_data = []
            
            for day in range(1, num_days + 1):
                date = datetime(year, month, day).date()
                
                # Skip dates outside the specified range
                if von_date and date < von_date:
                    continue
                if bis_date and date > bis_date:
                    continue
                date_str = date.strftime(DATE_FORMAT)
                
                # Check if this date is locked
                is_locked = date_str in self.report_locked_dates
                
                # Get or generate daily data
                if is_locked and date_str in self.report_edits and 'daily_data' in self.report_edits[date_str]:
                    # Preserve locked data exactly as saved
                    daily_text = self.report_edits[date_str]['daily_data']
                else:
                    # Generate new data with proper translation
                    daily_text = self._generate_daily_data(animal_name, animal_data, date, report_messages)
                
                # Get scores and signatures (only if locked)
                scores = self.report_edits.get(date_str, {}).get('scores', '') if is_locked else ''
                signatures = self.report_edits.get(date_str, {}).get('signatures', '') if is_locked else ''
                
                daily_data.append({
                    'date': day,
                    'daily_data': daily_text,
                    'scores': scores,
                    'signatures': signatures,
                    'is_locked': is_locked
                })
            
            # Call the plugin function to create the PDF
            create_monthly_report(
                header_info=header_info,
                daily_data=daily_data,
                month=month,
                year=year,
                output_path=output_path,
                von_date=von_date,
                bis_date=bis_date,
                messages=report_messages
            )
            
        except ImportError as e:
            logging.error(f"Animal_Reports plugin not found: {e}")
            # Escape curly braces in error message
            error_msg = str(e).replace('{', '{{').replace('}', '}}')
            raise Exception(f"Animal_Reports plugin is not properly installed: {error_msg}")
        except Exception as e:
            logging.error(f"Error creating PDF for {animal_name} ({year}-{month}): {e}")
            raise

    def _save_database(self) -> None:
        """Create a dated backup bundle for ProgTrack + all installed plugins."""
        if not self._master_can('core.export'):
            self._show_permission_denied()
            return
        try:
            app_root = Path(os.path.dirname(__file__)).resolve()
            current_db_path = app_root / DATEN_DATEI

            if not current_db_path.exists():
                self._show_message("error.save_database.file_not_found")
                return

            target_root = QFileDialog.getExistingDirectory(
                self,
                self.messages.get("file_dialog.save_database.title", "Save Database"),
                "",
                QFileDialog.Option.ShowDirsOnly,
            )
            if not target_root:
                return

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_root = Path(target_root).resolve() / "Backup" / timestamp
            backup_root.mkdir(parents=True, exist_ok=True)

            copied_files = 0

            def _copy_within_scope(scope_name: str, src_path: Path, rel_path: Optional[Path] = None) -> None:
                nonlocal copied_files
                base = backup_root / scope_name
                dst_path = base / (rel_path if rel_path is not None else Path(src_path.name))
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_path), str(dst_path))
                copied_files += 1

            # Main ProgTrack database backup
            _copy_within_scope("ProgTrack", current_db_path)

            # Backup all installed plugins (enabled or disabled)
            for plugin_dir in self._iter_installed_plugin_dirs():
                manifest = self._load_plugin_manifest(plugin_dir / "manifest.json")
                plugin_scope = str(manifest.get("name", "")).strip() or plugin_dir.name
                for src in self._collect_plugin_backup_files(plugin_dir, app_root):
                    try:
                        rel = src.relative_to(plugin_dir)
                    except ValueError:
                        rel = Path(src.name)
                    _copy_within_scope(plugin_scope, src, rel)

            self._show_message("info.save_database.success", path=str(backup_root))
            logging.info("Created database backup bundle at %s (%d files)", backup_root, copied_files)
            self._master_audit("backup", "ProgTrack", f"path={backup_root}; files={copied_files}")

        except Exception as e:
            # Handle errors
            self._show_message("error.save_database.failed", error=str(e))
            logging.error(f"Failed to save database: {e}")

    def _iter_installed_plugin_dirs(self) -> List[Path]:
        """Return all installed plugin directories (manifest.json at top level)."""
        plugins_root = Path(os.path.dirname(__file__)).resolve() / "Plugins"
        if not plugins_root.is_dir():
            return []

        plugin_dirs: List[Path] = []
        for child in sorted(plugins_root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            if child.name.startswith("__"):
                continue
            if (child / "manifest.json").is_file():
                plugin_dirs.append(child)
        return plugin_dirs

    def _load_plugin_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        """Load plugin manifest JSON safely."""
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def _collect_plugin_backup_files(self, plugin_dir: Path, app_root: Path) -> List[Path]:
        """Collect plugin-associated data files for backup.

        Sources:
        1) manifest.json -> data_files entries (supports glob patterns)
        2) plugin-local data-like files (json/db/sqlite/txt/enc/csv)
        3) app-root data-like files heuristically matching plugin name
        """
        manifest = self._load_plugin_manifest(plugin_dir / "manifest.json")
        files = set()

        plugin_key = re.sub(r"[^a-z0-9]+", "", plugin_dir.name.lower())
        if plugin_key in {"networktrack", "networtrack"}:
            chat_log = (plugin_dir / "chat_log.txt").resolve()
            return [chat_log] if chat_log.is_file() else []
        if plugin_key == "embryotrack":
            craniometry = (plugin_dir / "cranimetry_reference.json").resolve()
            return [craniometry] if craniometry.is_file() else []

        def _normalize_token(value: str) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

        # 1) Manifest-declared files
        raw_data_files = manifest.get("data_files", [])
        if isinstance(raw_data_files, list):
            for entry in raw_data_files:
                pattern = str(entry or "").strip()
                if not pattern:
                    continue
                try:
                    matches = list(plugin_dir.glob(pattern))
                except Exception:
                    matches = []

                for match in matches:
                    if match.is_file():
                        files.add(match.resolve())
                    elif match.is_dir():
                        for nested in match.rglob("*"):
                            if nested.is_file():
                                files.add(nested.resolve())

        allowed_exts = {".json", ".db", ".sqlite", ".sqlite3", ".txt", ".enc", ".csv"}

        # 2) Plugin-local fallback files
        for local_file in plugin_dir.rglob("*"):
            if not local_file.is_file():
                continue
            rel_parts = [part.lower() for part in local_file.relative_to(plugin_dir).parts]
            if rel_parts:
                head = rel_parts[0]
                if head == "__pycache__" or head.startswith("arch"):
                    continue
            if local_file.suffix.lower() not in allowed_exts:
                continue
            if local_file.name.lower() == "manifest.json":
                continue
            files.add(local_file.resolve())

        # 3) App-root fallback files
        name_tokens = set()
        for raw_name in [manifest.get("name", ""), plugin_dir.name]:
            token = _normalize_token(str(raw_name))
            if token:
                name_tokens.add(token)
                if token.endswith("s") and len(token) > 1:
                    name_tokens.add(token[:-1])

        excluded_root_files = {
            DATEN_DATEI.lower(),
            SETTINGS_FILE.lower(),
            LOCK_FILE.lower(),
            "disabled_plugins.json",
            "progtrack_config.json",
        }
        for root_file in app_root.iterdir():
            if not root_file.is_file():
                continue
            if root_file.name.lower() in excluded_root_files:
                continue
            if root_file.suffix.lower() not in allowed_exts:
                continue

            normalized_stem = _normalize_token(root_file.stem)
            if any(token and token in normalized_stem for token in name_tokens):
                files.add(root_file.resolve())

        return sorted(files, key=lambda p: str(p).lower())

    def _build_language_menu(self):
        menubar = self.menuBar()
        program_menu = menubar.addMenu(self.messages["menu.program"])
        lang_menu = program_menu.addMenu(self.messages["menu.program.language_settings"])

        group = QActionGroup(self)
        group.setExclusive(True)

        items = [
            ("en", self.messages.get("menu.program.language_settings.english", "English")),
            ("de", self.messages.get("menu.program.language_settings.german", "Deutsch")),
            ("ru", self.messages.get("menu.program.language_settings.russian", "Русский")),
            ("it", self.messages.get("menu.program.language_settings.italian", "Italiano")),
        ]
        for code, label in items:
            act = QAction(label, self, checkable=True)
            act.setData(code)                               # <-- critical
            act.setChecked(code == getattr(self, "lang", "en"))
            lang_menu.addAction(act)
            group.addAction(act)

        group.triggered.connect(self._on_language_changed)   # <-- uses handler above
        
        # Add Style settings menu item
        self.style_settings_action = QAction(self.messages.get("menu.program.style_settings", "Style"), self)
        self.style_settings_action.triggered.connect(self._show_style_settings)
        program_menu.addAction(self.style_settings_action)


    def _on_language_changed(self, action):
        """Handle click on Program → Language Settings (immediate reload)."""
        # Prefer QAction.data()
        try:
            new_lang = action.data()
        except Exception:
            new_lang = None

        # Fallback from label if .data() is missing
        if not new_lang and hasattr(action, "text"):
            t = action.text().lower()
            if "deutsch" in t or "german" in t:
                new_lang = "de"
            elif "english" in t or "englisch" in t:
                new_lang = "en"

        if not new_lang or new_lang == self.lang:
            return

        self.lang = new_lang
        self._save_settings()
        self._load_messages(self.lang)
        self._refresh_ui()  # keep instant UI refresh

    def _show_style_settings(self):
        """Show the Style Settings dialog."""
        if not self._master_can('core.style_settings'):
            QMessageBox.warning(
                self,
                self.messages.get("title.warning", "Warning"),
                self.messages.get("master_track.warn.no_permission", "You don't have permission to access this feature.")
            )
            return

        dialog = StyleSettingsDialog(self, self.messages)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Apply the new style settings
            new_settings = dialog.get_settings()
            new_roles = dialog.get_role_definitions()
            self._apply_style_settings(new_settings)
            # Save per-user style settings
            self._save_user_style_settings(new_settings)
            if new_roles is not None and self._save_animal_role_definitions(new_roles):
                self._refresh_list(update_tab_visibility=True)
            # Refresh the plot to show new colors/styles
            if hasattr(self, 'selected_animals') and self.selected_animals:
                self._plot_selected()

    def _reload_user_style_settings(self):
        """Reload style settings for the current user."""
        user_style_settings = self._load_user_style_settings()
        self._apply_style_settings(user_style_settings)
        # Refresh the plot to show new colors/styles if applicable
        if hasattr(self, 'selected_animals') and self.selected_animals:
            self._plot_selected()

    def _apply_style_settings(self, settings):
        """Apply style settings to the application."""
        # Colors
        self.prog_color = QColor(settings.get('prog_color', '#DC143C'))
        self.blood_color = QColor(settings.get('blood_color', '#ff0000'))  # red
        self.urine_color = QColor(settings.get('urine_color', '#FF8C00'))  # darkorange
        self.combined_color = QColor(settings.get('combined_color', '#8B0000'))  # darkred
        self.weight_color = QColor(settings.get('weight_color', '#800080'))
        self.pdg_color = QColor(settings.get('pdg_color', '#008000'))
        self.sperm_total_color = QColor(settings.get('sperm_total_color', '#D55E00'))
        self.sperm_motile_color = QColor(settings.get('sperm_motile_color', '#0072B2'))
        self.sperm_progressive_color = QColor(settings.get('sperm_progressive_color', '#009E73'))
        self.fsh_color = QColor(settings.get('fsh_color', '#000000'))
        
        # Event colors
        self.pgf_color = QColor(settings.get('pgf_color', '#FF0000'))
        self.embryo_color = QColor(settings.get('embryo_color', '#000000'))
        self.op_color = QColor(settings.get('op_color', '#0000FF'))
        self.pregnancy_color = QColor(settings.get('pregnancy_color', '#008000'))
        self.abort_color = QColor(settings.get('abort_color', '#FF00FF'))
        self.birth_color = QColor(settings.get('birth_color', '#000000'))
        self.special_color = QColor(settings.get('special_color', '#FFA500'))
        
        # Markers
        self.combined_marker = settings.get('combined_marker', 'o')
        self.blood_marker = settings.get('blood_marker', 'o')
        self.urine_marker = settings.get('urine_marker', 's')
        self.prog_marker = settings.get('prog_marker', 'o')
        self.weight_marker = settings.get('weight_marker', '^')
        self.pdg_marker = settings.get('pdg_marker', 's')
        self.fsh_marker = settings.get('fsh_marker', 'v')
        self.sperm_total_marker = settings.get('sperm_total_marker', 'o')
        self.sperm_motile_marker = settings.get('sperm_motile_marker', 's')
        self.sperm_progressive_marker = settings.get('sperm_progressive_marker', '^')

    def _get_default_style_settings(self):
        """Get default style settings."""
        return {
            'prog_color': '#DC143C',  # crimson
            'blood_color': '#ff0000',  # red
            'urine_color': '#FF8C00',  # darkorange
            'combined_color': '#8B0000',  # darkred
            'weight_color': '#800080',  # purple
            'pdg_color': '#008000',  # green
            'sperm_total_color': '#D55E00',
            'sperm_motile_color': '#0072B2',
            'sperm_progressive_color': '#009E73',
            'fsh_color': '#000000',  # black
            'pgf_color': '#FF0000',  # red
            'embryo_color': '#000000',  # black
            'op_color': '#0000FF',  # blue
            'pregnancy_color': '#008000',  # green
            'abort_color': '#FF00FF',  # magenta
            'birth_color': '#000000',  # black
            'special_color': '#FFA500',  # orange
            'combined_marker': 'o',  # circle for combined/converted values (rendered empty by plotting code)
            'blood_marker': 'o',  # filled circle for blood progesterone
            'urine_marker': 's',  # square for urine PdG
            'prog_marker': 'o',
            'weight_marker': '^',
            'pdg_marker': 's',
            'fsh_marker': 'v',  # triangle down
            'sperm_total_marker': 'o',  # circle
            'sperm_motile_marker': 's',  # square
            'sperm_progressive_marker': '^'  # triangle up
        }

    def _refresh_ui(self):
        """Refresh all visible UI texts after a language change."""
        self.setWindowTitle(self.messages.get("app.title", "ProgTrack"))
        mb = self.menuBar()
        if mb:
            mb.clear()
        self._setup_menus()
        self._setup_sidebar_texts()
        self._refresh_list()
        
        # Update tab labels
        if hasattr(self, 'main_tabs') and self.main_tabs is not None:
            if self.main_tabs.count() > 0:
                self.main_tabs.setTabText(0, self.messages.get("tab.plots", "Plots"))

            for i in range(self.main_tabs.count()):
                tab_widget = self.main_tabs.widget(i)

                if self.reports_enabled:
                    if tab_widget is getattr(self, 'reports_tab', None) or tab_widget is getattr(self, 'reports_tab_placeholder', None):
                        self.main_tabs.setTabText(i, self.messages.get("tab.reports", "Reports"))

                if self.flow_track_enabled:
                    flow_widget = getattr(getattr(self, 'flow_track_widget', None), 'widget', None)
                    if tab_widget is flow_widget or tab_widget is getattr(self, 'flow_track_tab_placeholder', None):
                        self.main_tabs.setTabText(i, self.messages.get("tab.flow_track", "Flow Track"))

                if getattr(self, 'has_heritage_plugin', False):
                    if tab_widget is getattr(self, 'heritage_track_tab', None) or tab_widget is getattr(self, 'heritage_track_tab_placeholder', None):
                        self.main_tabs.setTabText(i, self.messages.get("tab.heritage_track", "Heritage Track"))

                if getattr(self, 'has_cage_track_plugin', False):
                    if tab_widget is getattr(self, 'cage_track_tab', None) or tab_widget is getattr(self, 'cage_track_tab_placeholder', None):
                        self.main_tabs.setTabText(i, self.messages.get("tab.cage_track", "Cage Track"))

                if getattr(self, 'has_medi_track_plugin', False):
                    if tab_widget is getattr(self, 'medi_track_tab', None) or tab_widget is getattr(self, 'medi_track_tab_placeholder', None):
                        self.main_tabs.setTabText(i, self.messages.get("tab.medi_track", "Medi Track"))

        # Re-apply visibility state for all plugins (after text update to match correctly)
        all_plugins = {"animal_reports", "flow_track", "heritage_track", "cage_track", "medi_track", "projects_track"}
        disabled = getattr(self, '_disabled_plugins', set())
        for pkey in all_plugins:
            self._apply_plugin_state(pkey, pkey not in disabled)

        # Update Reports tab UI
        if self.reports_enabled:
            self._refresh_reports_ui()
        
        # Update Flow Track widget UI
        if self.flow_track_enabled and hasattr(self, 'flow_track_widget') and self.flow_track_widget is not None:
            if hasattr(self.flow_track_widget, 'update_language'):
                self.flow_track_widget.update_language(self.messages)
        
        # Update ProjectsTrack plugin UI
        if self.has_projects_plugin and self.projects_plugin is not None:
            if hasattr(self.projects_plugin, 'update_language'):
                self.projects_plugin.update_language(self.messages)

        # Update Heritage_Track plugin UI
        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None) is not None:
            if hasattr(self.heritage_plugin, 'update_language'):
                self.heritage_plugin.update_language(self.messages)

        # Update Cage_Track plugin UI
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None) is not None:
            if hasattr(self.cage_track_plugin, 'update_language'):
                self.cage_track_plugin.update_language(self.messages)

        # Update Medi_Track plugin UI
        if getattr(self, 'has_medi_track_plugin', False) and getattr(self, 'medi_track_plugin', None) is not None:
            if hasattr(self.medi_track_plugin, 'update_language'):
                self.medi_track_plugin.update_language(self.messages)

        # Update Project Track tab UI
        if getattr(self, '_pt_tab_needed', False) and getattr(self, 'main_tabs', None):
            for i in range(self.main_tabs.count()):
                w = self.main_tabs.widget(i)
                if (w is getattr(self, 'project_track_tab', None)
                        or w is getattr(self, 'project_track_tab_placeholder', None)):
                    self.main_tabs.setTabText(
                        i, self.messages.get('tab.project_track', 'Project Track'))
        pt_w = getattr(self, 'project_track_widget', None)
        if pt_w and hasattr(pt_w, 'update_language'):
            pt_w.update_language(self.messages)

        if getattr(self, "current_canvas", None):
            self.current_canvas.draw_idle()

    def _setup_menus(self):
        """(Re)build the entire menubar with localized text."""
        menubar = self.menuBar()
        if not menubar:
            return
        menubar.clear()

        # File
        file_menu = menubar.addMenu(self.messages["menu.file"])

        if self.reports_enabled:
            file_menu.addSection(self.messages.get("menu.file.section.reports", "Reports"))
            print_action = QAction(self.messages["menu.file.export"], self)
            print_action.triggered.connect(self._dlg_print_data)
            file_menu.addAction(print_action)

            pdf_export_action = QAction(self.messages.get("menu.file.export_pdf", "Export Reports (.pdf)"), self)
            pdf_export_action.triggered.connect(self._dlg_export_pdf)
            file_menu.addAction(pdf_export_action)

        if getattr(self, 'has_medi_track_plugin', False):
            file_menu.addSection(self.messages.get("menu.file.section.medi_track", "Medi Track"))
            medi_pdf_action = QAction(
                self.messages.get("menu.file.export_medi_pdf", "Export Medi Track (.pdf)"), self)
            medi_pdf_action.triggered.connect(self._dlg_export_medi_track_pdf)
            file_menu.addAction(medi_pdf_action)

        file_menu.addSection(self.messages.get("menu.file.section.database", "Database"))
        save_db_action = QAction(self.messages.get("menu.file.save_database", "Save Database"), self)
        save_db_action.triggered.connect(self._save_database)
        file_menu.addAction(save_db_action)

        # Tools
        tools_menu = menubar.addMenu(self.messages["menu.tools"])

        # --- Master_Track group (above everything) ---
        if getattr(self, 'has_master_track', False) and self.master_track:
            self._master_menu = tools_menu.addMenu(
                self.messages.get("menu.tools.master_track", "Master Track"))

            if hasattr(self, '_mt_manage_action'):
                self._mt_manage_action.setText(
                    self.messages.get("master_track.menu.manage", "Manage Users"))
            else:
                self._mt_manage_action = QAction(
                    self.messages.get("master_track.menu.manage", "Manage Users"), self)
                self._mt_manage_action.triggered.connect(self.master_track.show_manage_users)
            self._mt_manage_action.setEnabled(self.master_track.can("master.view_users"))
            self._master_menu.addAction(self._mt_manage_action)

            if hasattr(self, '_mt_edit_jobs_action'):
                self._mt_edit_jobs_action.setText(
                    self.messages.get("master_track.menu.edit_jobs", "Edit Jobs…"))
            else:
                self._mt_edit_jobs_action = QAction(
                    self.messages.get("master_track.menu.edit_jobs", "Edit Jobs…"), self)
                self._mt_edit_jobs_action.triggered.connect(self.master_track.show_edit_jobs)
            self._mt_edit_jobs_action.setEnabled(self.master_track.can("master.manage_job_bundles"))
            self._master_menu.addAction(self._mt_edit_jobs_action)

            if hasattr(self, '_mt_logs_action'):
                self._mt_logs_action.setText(
                    self.messages.get("master_track.menu.logs", "Logs"))
            else:
                self._mt_logs_action = QAction(
                    self.messages.get("master_track.menu.logs", "Logs"), self)
                self._mt_logs_action.triggered.connect(self.master_track.show_logs)
            self._mt_logs_action.setEnabled(self.master_track.can("master.view_audit"))
            self._master_menu.addAction(self._mt_logs_action)

            if hasattr(self, '_mt_open_logs_folder_action'):
                self._mt_open_logs_folder_action.setText(
                    self.messages.get("master_track.menu.open_logs_folder", "Open tech logs"))
            else:
                self._mt_open_logs_folder_action = QAction(
                    self.messages.get("master_track.menu.open_logs_folder", "Open tech logs"), self)
                self._mt_open_logs_folder_action.triggered.connect(self.master_track.open_logs_folder)
            self._mt_open_logs_folder_action.setEnabled(self.master_track.can("master.view_audit"))
            self._master_menu.addAction(self._mt_open_logs_folder_action)

            if hasattr(self, '_mt_changepw_action'):
                self._mt_changepw_action.setText(
                    self.messages.get("master_track.menu.change_pw", "Change Password"))
            else:
                self._mt_changepw_action = QAction(
                    self.messages.get("master_track.menu.change_pw", "Change Password"), self)
                self._mt_changepw_action.triggered.connect(self.master_track.show_change_password)
            self._mt_changepw_action.setEnabled(self.master_track.is_logged_in)
            self._master_menu.addAction(self._mt_changepw_action)

            if hasattr(self, '_mt_logout_action'):
                self._mt_logout_action.setText(
                    self.messages.get("master_track.menu.logout", "Logout"))
            else:
                self._mt_logout_action = QAction(
                    self.messages.get("master_track.menu.logout", "Logout"), self)
                self._mt_logout_action.triggered.connect(self._do_master_logout)
            self._mt_logout_action.setEnabled(self.master_track.is_logged_in)
            self._master_menu.addAction(self._mt_logout_action)

            if hasattr(self, '_mt_login_action'):
                self._mt_login_action.setText(
                    self.messages.get("master_track.menu.login", "Login"))
            else:
                self._mt_login_action = QAction(
                    self.messages.get("master_track.menu.login", "Login"), self)
                self._mt_login_action.triggered.connect(self._do_master_login)
            self._mt_login_action.setEnabled(not self.master_track.is_logged_in)
            self._master_menu.addAction(self._mt_login_action)

            self._master_menu.addSeparator()
            _mt_enabled_now2 = "master_track" not in getattr(self, '_disabled_plugins', set())
            _toggle_label2 = (
                self.messages.get("master_track.menu.disable", "Disable Master Track")
                if _mt_enabled_now2 else
                self.messages.get("master_track.menu.enable", "Enable Master Track")
            )
            if hasattr(self, '_mt_toggle_action'):
                self._mt_toggle_action.setText(_toggle_label2)
            else:
                self._mt_toggle_action = QAction(_toggle_label2, self)
                self._mt_toggle_action.triggered.connect(self._toggle_master_track)
            self._mt_toggle_action.setEnabled(
                self.master_track.can("toggle_master_track"))
            self._master_menu.addAction(self._mt_toggle_action)

            tools_menu.addSeparator()

        # --- Middle group: utility / dialog plugins (no checkbox) ---
        if self.network_track_enabled:
            if hasattr(self, "network_track_action"):
                action = self.network_track_action
                action.setText(self.messages.get("menu.tools.network_track", "Network Track"))
            else:
                action = QAction(self.messages.get("menu.tools.network_track", "Network Track"), self)
                action.triggered.connect(self._launch_network_track)
                self.network_track_action = action
            tools_menu.addAction(action)

        if self.has_pdg_plugin and self._is_steroid_track_active():
            self.pdg_cap.add_menu_items(tools_menu)

        if self.embryo_tracker_enabled:
            if hasattr(self, "embryo_tracker_action"):
                action = self.embryo_tracker_action
                action.setText(self.messages.get("menu.tools.embryo_tracker", "Embryo Track"))
            else:
                action = QAction(self.messages.get("menu.tools.embryo_tracker", "Embryo Track"), self)
                action.triggered.connect(self._launch_embryo_tracker)
                self.embryo_tracker_action = action
            tools_menu.addAction(action)

        if hasattr(self, "op_planner_action"):
            action = self.op_planner_action
            action.setText(self.messages.get("menu.tools.op_planner", "OP Scheduler"))
        else:
            action = QAction(self.messages.get("menu.tools.op_planner", "OP Scheduler"), self)
            action.triggered.connect(self._launch_op_planner)
            self.op_planner_action = action
        action.setEnabled(
            self._op_planner_available() and self._master_can('op_scheduler.view'))
        tools_menu.addAction(action)

        if getattr(self, 'has_sample_track_plugin', False):
            if hasattr(self, "sample_track_action"):
                action = self.sample_track_action
                action.setText(self.messages.get("menu.tools.sample_track", "Sample Track"))
            else:
                action = QAction(self.messages.get("menu.tools.sample_track", "Sample Track"), self)
                action.triggered.connect(self._launch_sample_track)
                self.sample_track_action = action
            tools_menu.addAction(action)

        # --- Separator ---
        tools_menu.addSeparator()

        # --- Bottom group: tab-based plugins with enable/disable toggle ---
        # Italic = disabled, normal = enabled.
        if self.reports_enabled:
            if hasattr(self, "animal_reports_action"):
                action = self.animal_reports_action
                action.setText(self.messages.get("menu.tools.animal_reports", "Animal Reports"))
            else:
                action = QAction(self.messages.get("menu.tools.animal_reports", "Animal Reports"), self)
                action.setCheckable(True)
                action.setChecked("animal_reports" not in self._disabled_plugins)
                action.toggled.connect(lambda c: self._toggle_plugin_enabled("animal_reports", c))
                self.animal_reports_action = action
            self._style_plugin_action("animal_reports", "animal_reports" not in self._disabled_plugins)
            tools_menu.addAction(action)

        if self.flow_track_enabled:
            if hasattr(self, "flow_track_action"):
                action = self.flow_track_action
                action.setText(self.messages.get("menu.tools.flow_track", "Flow Track"))
            else:
                action = QAction(self.messages.get("menu.tools.flow_track", "Flow Track"), self)
                action.setCheckable(True)
                action.setChecked("flow_track" not in self._disabled_plugins)
                action.toggled.connect(lambda c: self._toggle_plugin_enabled("flow_track", c))
                self.flow_track_action = action
            self._style_plugin_action("flow_track", "flow_track" not in self._disabled_plugins)
            tools_menu.addAction(action)

        if self.has_projects_plugin:
            if hasattr(self, "projects_track_action"):
                action = self.projects_track_action
                action.setText(self.messages.get("menu.tools.projects_track", "Project Track"))
            else:
                action = QAction(self.messages.get("menu.tools.projects_track", "Project Track"), self)
                action.setCheckable(True)
                action.setChecked("projects_track" not in self._disabled_plugins)
                action.toggled.connect(lambda c: self._toggle_plugin_enabled("projects_track", c))
                self.projects_track_action = action
            self._style_plugin_action("projects_track", "projects_track" not in self._disabled_plugins)
            tools_menu.addAction(action)

        if self.has_heritage_plugin:
            if hasattr(self, "heritage_track_action"):
                action = self.heritage_track_action
                action.setText(self.messages.get("menu.tools.heritage_track", "Heritage Track"))
            else:
                action = QAction(self.messages.get("menu.tools.heritage_track", "Heritage Track"), self)
                action.setCheckable(True)
                action.setChecked("heritage_track" not in self._disabled_plugins)
                action.toggled.connect(lambda c: self._toggle_plugin_enabled("heritage_track", c))
                self.heritage_track_action = action
            self._style_plugin_action("heritage_track", "heritage_track" not in self._disabled_plugins)
            tools_menu.addAction(action)

        if getattr(self, 'has_cage_track_plugin', False):
            if hasattr(self, "cage_track_action"):
                action = self.cage_track_action
                action.setText(self.messages.get("menu.tools.cage_track", "Cage Track"))
            else:
                action = QAction(self.messages.get("menu.tools.cage_track", "Cage Track"), self)
                action.setCheckable(True)
                action.setChecked("cage_track" not in self._disabled_plugins)
                action.toggled.connect(lambda c: self._toggle_plugin_enabled("cage_track", c))
                self.cage_track_action = action
            self._style_plugin_action("cage_track", "cage_track" not in self._disabled_plugins)
            tools_menu.addAction(action)

        if getattr(self, 'has_steroid_track_plugin', False):
            if hasattr(self, "steroid_track_action"):
                action = self.steroid_track_action
                action.setText(self.messages.get("menu.tools.steroid_track", "Steroid Track"))
            else:
                action = QAction(self.messages.get("menu.tools.steroid_track", "Steroid Track"), self)
                action.setCheckable(True)
                action.setChecked("steroid_track" not in self._disabled_plugins)
                action.toggled.connect(lambda c: self._toggle_plugin_enabled("steroid_track", c))
                self.steroid_track_action = action
            self._style_plugin_action("steroid_track", "steroid_track" not in self._disabled_plugins)
            tools_menu.addAction(action)

        self._refresh_role_restricted_tool_states()

        # Program → Language
        self._build_language_menu()

        # Info
        info_menu = menubar.addMenu(self.messages["menu.info"])
        about_action = QAction(self.messages["menu.info.about"], self)
        about_action.triggered.connect(self._dlg_about_programm)
        info_menu.addAction(about_action)

    def _setup_sidebar_texts(self):
        """Refresh labels/buttons in the sidebar after language change."""
        steroid_active = self._is_steroid_track_active()
        # Update category tab tooltips and label for the "All" tab.
        if hasattr(self, 'category_tab') and self.category_tab is not None:
            tooltips = self._category_tab_tooltips()
            for idx, tip in enumerate(tooltips):
                if idx < self.category_tab.count():
                    self.category_tab.setTabToolTip(idx, tip)
            # Also refresh the text label of the "All" tab (last tab, index 6)
            if self.category_tab.count() > 6:
                self.category_tab.setTabText(6, self.messages["sidebar.filter.all"])
        
        # Visibility/line-style groups
        self.box_chk.setTitle(self.messages["group.visibility.title"])
        self.chk_prog.setText(self.messages["checkbox.progesterone"])
        self.chk_weight.setText(self.messages["checkbox.weight"])
        self.chk_events.setText(self.messages["checkbox.events"])
        if self.has_pdg_plugin and hasattr(self, 'chk_mode_combined'):
            self.chk_mode_combined.setText(self.messages.get("mode.combined", "Combined"))
        self.chk_mode_blood.setText(self.messages.get("mode.blood", "Blood (Pgr)"))
        if self.has_pdg_plugin and hasattr(self, 'chk_mode_urin'):
            self.chk_mode_urin.setText(self.messages.get("mode.urine", "Urine (PdG)"))
        self.box_rad.setTitle(self.messages["group.line_style.title"])
        
        # Combined toggle - conditional on plugin
        if self.has_pdg_plugin and hasattr(self, 'rb_combined_on'):
            self.rb_combined_on.setText(self.messages.get("label.on", "On"))
            self.rb_combined_off.setText(self.messages.get("label.off", "Off"))
        
        # Blood toggle - conditional on plugin
        if hasattr(self, 'rb_blood_on'):
            self.rb_blood_on.setText(self.messages.get("label.on", "On"))
            self.rb_blood_off.setText(self.messages.get("label.off", "Off"))
        
        # Urine toggle - conditional on plugin
        if self.has_pdg_plugin and hasattr(self, 'rb_urine_on'):
            self.rb_urine_on.setText(self.messages.get("label.on", "On"))
            self.rb_urine_off.setText(self.messages.get("label.off", "Off"))
        
        # Weight toggle
        self.rb_weight_on.setText(self.messages["label.on"])
        self.rb_weight_off.setText(self.messages["label.off"])
        
        # Sperm toggle
        if hasattr(self, 'rb_sperm_on'):
            self.rb_sperm_on.setText(self.messages["label.on"])
        if hasattr(self, 'rb_sperm_off'):
            self.rb_sperm_off.setText(self.messages["label.off"])
        
        # Update row labels for combined, blood, urine, and weight
        if hasattr(self, 'combined_label'):
            self.combined_label.setText(self.messages.get("line_style.combined.label", "Blood + Urine"))
        if hasattr(self, 'blood_label'):
            self.blood_label.setText(self.messages.get("line_style.blood.label", "Blood (Pgr)"))
        if hasattr(self, 'urine_label'):
            self.urine_label.setText(self.messages.get("line_style.urine.label", "Urine (PdG)"))
        if hasattr(self, 'weight_label'):
            self.weight_label.setText(self.messages["line_style.weight.label"])
        if hasattr(self, 'sperm_label'):
            self.sperm_label.setText(self.messages.get('line_style.sperm.label', 'Spermawerte'))

        # Sidebar main buttons
        self.btn_new.setText(self.messages["button.sidebar.new_animal"])
        # btn_edit text depends on active category tab (All = Edit Role, others = Edit)
        _cat_idx = self.category_tab.currentIndex() if hasattr(self, 'category_tab') else -1
        if _cat_idx == 6:
            self.btn_edit.setText(self.messages.get("button.sidebar.edit_role", "\U0001fae5    Edit Role"))
        else:
            self.btn_edit.setText(self.messages["button.sidebar.edit_animal"])
        if hasattr(self, 'btn_edit_animal'):
            self.btn_edit_animal.setText(self.messages.get(
                "button.sidebar.edit_animal", "\u270f\ufe0f    Edit"))
        self.btn_load_blood.setText(self.messages["button.sidebar.load_blood_values"])
        if self.has_pdg_plugin and hasattr(self, 'btn_load_urine'):
            self.btn_load_urine.setText(self.messages["button.sidebar.load_urine_values"])
        self.btn_load_weights.setText(self.messages["button.sidebar.load_weights"])
        self.btn_archive.setText(self.messages["button.sidebar.archive"])
        # If present, also localize the sperm import button
        if hasattr(self, "btn_load_sperm") and "button.sidebar.load_sperm_values" in self.messages:
            self.btn_load_sperm.setText(self.messages["button.sidebar.load_sperm_values"])

        # Archive section
        if hasattr(self, 'sidebar_label'):
            self.sidebar_label.setText(self.messages["sidebar.available_animals"])
        if hasattr(self, 'chk_show_archived'):
            self.chk_show_archived.setText(self.messages.get("sidebar.show_archived", "Show Archived"))
        elif hasattr(self, 'archived_label'):
            self.archived_label.setText(self.messages.get("sidebar.archived_animals",
                                                          "Archived Animals:"))
        if hasattr(self, 'animal_name_filter_edit'):
            self.animal_name_filter_edit.setPlaceholderText(
                self.messages.get("sidebar.animal_name_filter.placeholder", "Filter animals by name or IPID"))
            self.animal_name_filter_edit.setToolTip(
                self.messages.get("sidebar.animal_name_filter.tooltip", "Filter the visible animal list by short name or IPID"))
        self.btn_restore.setText(self.messages["button.sidebar.restore"])
        self.btn_delete.setText(self.messages["button.sidebar.delete"])

        # Phase filter labels (update button text based on phase_val)
        phase_labels = {
            None:                         self.messages["sidebar.filter.all"],
            Phase.FOLLIKEL.value:         self.messages["sidebar.filter.follicle_phase"],
            Phase.LUTEAL.value:           self.messages["sidebar.filter.luteal_phase"],
        }
        for phase_val, btn in self.phase_buttons.items():
            btn.setText(phase_labels.get(phase_val, self.messages["sidebar.filter.all"]))

        if hasattr(self, 'phase_widget') and hasattr(self, 'category_tab'):
            self.phase_widget.setVisible(steroid_active and self.category_tab.currentIndex() == 0)

 
    def _op_planner_available(self) -> bool:
        """
        Determine whether the surgery planner plugin is present.  The
        expected location is ``Plugins/Surgery_Planner/__init__.py``.  If
        that file exists, the planner is considered available.  This
        method caches no state and merely checks the filesystem each time.
        """
        try:
            plugin_path = os.path.join('Plugins', 'Surgery_Planner', '__init__.py')
            return os.path.exists(plugin_path)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Master_Track login / logout actions
    # ------------------------------------------------------------------

    def _toggle_master_track(self):
        """Lord-only toggle to enable/disable the Master_Track plugin itself.
        Shows a confirmation dialog with a message that differs depending on
        whether Master Track is currently enabled or disabled."""
        mt = getattr(self, 'master_track', None)
        if not mt or not mt.can("toggle_master_track"):
            self._show_permission_denied()
            return
        currently_enabled = "master_track" not in self._disabled_plugins
        if currently_enabled:
            # Ask to DISABLE
            title = self.messages.get("master_track.toggle.disable_title",
                                      "Disable Master Track")
            msg = self.messages.get("master_track.toggle.disable_msg",
                                    "This will disable Master Track globally for all users. "
                                    "All permission checks and login requirements will be "
                                    "suspended.\n\nDo you want to disable Master Track?")
        else:
            # Ask to ENABLE
            title = self.messages.get("master_track.toggle.enable_title",
                                      "Enable Master Track")
            msg = self.messages.get("master_track.toggle.enable_msg",
                                    "This will re-enable Master Track globally for all users. "
                                    "Permission checks and login requirements will be enforced "
                                    "again.\n\nDo you want to enable Master Track?")
        reply = self._show_message_raw(
            title, msg, "warning",
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Apply the change
        if currently_enabled:
            self._disabled_plugins.add("master_track")
        else:
            self._disabled_plugins.discard("master_track")
        # Persist – master_track toggle is global, always save to global file
        self._save_disabled_plugins()
        if mt.is_logged_in:
            mt.save_session({"disabled_plugins": sorted(self._disabled_plugins)})
        # Refresh UI
        self._apply_master_button_states()
        self._update_master_status_bar()
        self._refresh_master_menu_states()
        mt.audit("toggle_master_track",
                 "disabled" if currently_enabled else "enabled")

    def _do_master_logout(self):
        """Log out, save session, revert to Guest mode, refresh UI."""
        mt = getattr(self, 'master_track', None)
        if not mt:
            return
        # Save session state before logout
        self._save_master_session()
        mt.logout()
        # Revert to global disabled_plugins (Guest defaults)
        self._disabled_plugins = self._load_disabled_plugins()
        # Reload language from global settings (guest mode doesn't persist changes)
        self.lang = self._get_global_language_fallback()
        self._load_messages(self.lang)
        self._refresh_ui()
        # Reset display checkboxes to defaults for guest mode
        if hasattr(self, 'chk_prog'):
            self.chk_prog.setChecked(True)
        if hasattr(self, 'chk_weight'):
            self.chk_weight.setChecked(True)
        if hasattr(self, 'chk_events'):
            self.chk_events.setChecked(True)
        # Reset per-role Events checkboxes to defaults for guest mode
        if hasattr(self, 'chk_events_offspring'):
            self.chk_events_offspring.setChecked(True)
        if hasattr(self, 'chk_events_breeding'):
            self.chk_events_breeding.setChecked(True)
        if hasattr(self, 'chk_events_experimental'):
            self.chk_events_experimental.setChecked(True)
        self._apply_all_plugin_states()
        self._refresh_master_menu_states()
        self._update_master_status_bar()
        self._apply_master_button_states()
        pt = getattr(self, 'projects_track_plugin', None)
        if pt and callable(getattr(pt, 'on_user_login', None)):
            pt.on_user_login()
        ptw = getattr(self, 'project_track_widget', None)
        if ptw and callable(getattr(ptw, 'on_user_login', None)):
            ptw.on_user_login()
        ntw = getattr(self, 'network_track_window', None)
        if ntw:
            ntw.refresh_master_name()

    def _do_master_login(self):
        """Show login dialog, apply user session on success."""
        mt = getattr(self, 'master_track', None)
        if not mt:
            return
        if mt.login_interactive():
            session = mt.load_session()
            self._disabled_plugins = set(session.get("disabled_plugins", []))
            # Merge global master_track toggle (it is a global setting, not per-user)
            global_disabled = self._load_disabled_plugins()
            if "master_track" in global_disabled:
                self._disabled_plugins.add("master_track")
            else:
                self._disabled_plugins.discard("master_track")
            self._apply_all_plugin_states()
            self._restore_session_ui(session)
            # Reload language from user session if set
            user_lang = session.get("language")
            if user_lang:
                self.lang = user_lang
                self._load_messages(self.lang)
                self._refresh_ui()
            self._refresh_master_menu_states()
            self._update_master_status_bar()
            self._apply_master_button_states()
            pt = getattr(self, 'projects_track_plugin', None)
            if pt and callable(getattr(pt, 'on_user_login', None)):
                pt.on_user_login()
            ptw = getattr(self, 'project_track_widget', None)
            if ptw and callable(getattr(ptw, 'on_user_login', None)):
                ptw.on_user_login()
            ntw = getattr(self, 'network_track_window', None)
            if ntw:
                ntw.refresh_master_name()
            # Warn if another instance holds the file lock
            if getattr(self, 'read_only_mode', False):
                self._show_message_raw(
                    self.messages.get("master_track.login.read_only_title",
                                     "Read-only Mode"),
                    self.messages.get("master_track.login.read_only_msg",
                                     "You are now logged in, but another instance of "
                                     "ProgTrack is currently editing the data files.\n\n"
                                     "You are in read-only mode. Changes will NOT be "
                                     "saved until the other instance is closed."),
                    "warning")

    def _show_master_quick_menu(self):
        """Show a quick context menu when the status bar label is clicked."""
        from PyQt6.QtWidgets import QMenu
        mt = getattr(self, 'master_track', None)
        if not mt:
            return
        
        # If in guest mode, directly open login dialog instead of showing menu
        if not mt.is_logged_in:
            self._do_master_login()
            return
        
        # For logged-in users, show the context menu
        menu = QMenu(self)
        
        # Add logout action
        menu.addAction(
            self.messages.get("master_track.menu.logout", "Logout"),
            self._do_master_logout)
        
        # Add change password action
        menu.addAction(
            self.messages.get("master_track.menu.change_pw", "Change Password"),
            mt.show_change_password)
        
        label = getattr(self, '_master_status_label', None)
        if label:
            menu.exec(label.mapToGlobal(label.rect().bottomLeft()))

    def _save_master_session(self):
        """Persist current UI state to the logged-in user's session file."""
        mt = getattr(self, 'master_track', None)
        if not mt or not mt.is_logged_in:
            return
        extra = {
            "disabled_plugins": sorted(self._disabled_plugins),
        }
        # Display checkbox states (Progesterone, Weight, Events)
        if hasattr(self, 'chk_prog'):
            extra["display_chk_prog"] = self.chk_prog.isChecked()
        if hasattr(self, 'chk_weight'):
            extra["display_chk_weight"] = self.chk_weight.isChecked()
        if hasattr(self, 'chk_events'):
            extra["display_chk_events"] = self.chk_events.isChecked()
        # Per-role Events checkbox states
        if hasattr(self, 'chk_events_offspring'):
            extra["display_chk_events_offspring"] = self.chk_events_offspring.isChecked()
        if hasattr(self, 'chk_events_breeding'):
            extra["display_chk_events_breeding"] = self.chk_events_breeding.isChecked()
        if hasattr(self, 'chk_events_experimental'):
            extra["display_chk_events_experimental"] = self.chk_events_experimental.isChecked()
        # Current tab
        tabs = getattr(self, 'main_tabs', None)
        if tabs is not None:
            tab_map = {
                self.messages.get("tab.plots", "Plots"): "tab.plots",
                self.messages.get("tab.reports", "Reports"): "tab.reports",
                self.messages.get("tab.flow_track", "Flow Track"): "tab.flow_track",
                self.messages.get("tab.heritage_track", "Heritage Track"): "tab.heritage_track",
                self.messages.get("tab.cage_track", "Cage Track"): "tab.cage_track",
            }
            current_text = tabs.tabText(tabs.currentIndex())
            extra["last_active_tab"] = tab_map.get(current_text, "tab.plots")
        # Current category
        cat_tab = getattr(self, 'category_tab', None)
        if cat_tab is not None:
            extra["last_category_index"] = cat_tab.currentIndex()
        # Window geometry
        geo = self.geometry()
        extra["window_geometry"] = {
            "x": geo.x(), "y": geo.y(), "w": geo.width(), "h": geo.height()
        }
        mt.save_session(extra)

    def _restore_session_ui(self, session):
        """Apply UI state from a session dict (tab, category, geometry)."""
        # Restore tab
        tab_key = session.get("last_active_tab", "tab.plots")
        tabs = getattr(self, 'main_tabs', None)
        if tabs is not None:
            key_to_label = {
                "tab.plots": self.messages.get("tab.plots", "Plots"),
                "tab.reports": self.messages.get("tab.reports", "Reports"),
                "tab.flow_track": self.messages.get("tab.flow_track", "Flow Track"),
                "tab.heritage_track": self.messages.get("tab.heritage_track", "Heritage Track"),
                "tab.cage_track": self.messages.get("tab.cage_track", "Cage Track"),
            }
            target_label = key_to_label.get(tab_key)
            if target_label:
                for i in range(tabs.count()):
                    if tabs.tabText(i) == target_label and tabs.isTabVisible(i):
                        tabs.setCurrentIndex(i)
                        break
        # Restore category
        cat_idx = session.get("last_category_index", 0)
        cat_tab = getattr(self, 'category_tab', None)
        if cat_tab is not None and 0 <= cat_idx < cat_tab.count():
            cat_tab.setCurrentIndex(cat_idx)
        # Restore geometry
        geo = session.get("window_geometry")
        if geo and isinstance(geo, dict):
            from PyQt6.QtCore import QRect
            from PyQt6.QtWidgets import QApplication
            x = geo.get("x", 100)
            y = geo.get("y", 50)
            w = geo.get("w", 1400)
            h = geo.get("h", 900)
            rect = QRect(x, y, w, h)
            screens = QApplication.screens()
            visible = any(s.availableGeometry().intersects(rect) for s in screens)
            if not visible:
                primary = QApplication.primaryScreen().availableGeometry()
                w = min(w, primary.width())
                h = min(h, primary.height())
                x = primary.x() + (primary.width() - w) // 2
                y = primary.y() + (primary.height() - h) // 2
                rect = QRect(x, y, w, h)
            self.setGeometry(rect)
        # Restore display checkbox states (Progesterone, Weight, Events)
        # For guests, defaults (all True) are used implicitly
        if hasattr(self, 'chk_prog'):
            self.chk_prog.setChecked(session.get("display_chk_prog", True))
        if hasattr(self, 'chk_weight'):
            self.chk_weight.setChecked(session.get("display_chk_weight", True))
        if hasattr(self, 'chk_events'):
            self.chk_events.setChecked(session.get("display_chk_events", True))
        # Restore per-role Events checkbox states
        if hasattr(self, 'chk_events_offspring'):
            self.chk_events_offspring.setChecked(session.get("display_chk_events_offspring", True))
        if hasattr(self, 'chk_events_breeding'):
            self.chk_events_breeding.setChecked(session.get("display_chk_events_breeding", True))
        if hasattr(self, 'chk_events_experimental'):
            self.chk_events_experimental.setChecked(session.get("display_chk_events_experimental", True))

    def _apply_all_plugin_states(self):
        """Re-apply visibility for all toggle-able plugin tabs and sidebar."""
        for pkey in ("animal_reports", "flow_track", "heritage_track",
                     "cage_track", "projects_track", "steroid_track"):
            enabled = pkey not in self._disabled_plugins
            self._apply_plugin_state(pkey, enabled)
            self._style_plugin_action(pkey, enabled)
            # Also update the checkable action's checked state
            attr = self._PLUGIN_ACTION_ATTRS.get(pkey)
            if attr:
                action = getattr(self, attr, None)
                if action and action.isCheckable():
                    action.blockSignals(True)
                    action.setChecked(enabled)
                    action.blockSignals(False)
        self._refresh_role_restricted_tool_states()

    def _refresh_master_menu_states(self):
        """Enable/disable Master_Track submenu items based on login state."""
        mt = getattr(self, 'master_track', None)
        if not mt:
            return
        if hasattr(self, '_mt_manage_action'):
            self._mt_manage_action.setEnabled(mt.can("master.view_users"))
        if hasattr(self, '_mt_edit_jobs_action'):
            self._mt_edit_jobs_action.setEnabled(mt.can("master.manage_job_bundles"))
        if hasattr(self, '_mt_logs_action'):
            self._mt_logs_action.setEnabled(mt.can("master.view_audit"))
        if hasattr(self, '_mt_open_logs_folder_action'):
            self._mt_open_logs_folder_action.setEnabled(mt.can("master.view_audit"))
        if hasattr(self, '_mt_changepw_action'):
            self._mt_changepw_action.setEnabled(mt.is_logged_in)
        if hasattr(self, '_mt_logout_action'):
            self._mt_logout_action.setEnabled(mt.is_logged_in)
        if hasattr(self, '_mt_login_action'):
            self._mt_login_action.setEnabled(not mt.is_logged_in)
        if hasattr(self, '_mt_toggle_action'):
            _currently_on = "master_track" not in getattr(self, '_disabled_plugins', set())
            self._mt_toggle_action.setText(
                self.messages.get("master_track.menu.disable", "Disable Master Track")
                if _currently_on else
                self.messages.get("master_track.menu.enable", "Enable Master Track")
            )
            self._mt_toggle_action.setEnabled(mt.can("toggle_master_track"))
        self._refresh_role_restricted_tool_states()

    def _tool_permission_for_action(self, action_attr: str) -> Optional[str]:
        return {
            'network_track_action': 'network.view',
            'embryo_tracker_action': 'embryo_track.view',
            'op_planner_action': 'op_scheduler.view',
            'sample_track_action': 'sample_track.use',
            'animal_reports_action': 'reports.view',
            'flow_track_action': 'flow_track.open',
            'projects_track_action': 'project.view',
            'heritage_track_action': 'heritage.view',
            'cage_track_action': 'cage.view',
            'medi_track_action': 'medi_track.view',
            'steroid_track_action': 'core.view',
        }.get(action_attr)

    def _plugin_permission_for_key(self, plugin_key: str) -> Optional[str]:
        return {
            'animal_reports': 'reports.view',
            'flow_track': 'flow_track.open',
            'projects_track': 'project.view',
            'heritage_track': 'heritage.view',
            'cage_track': 'cage.view',
            'medi_track': 'medi_track.view',
            'steroid_track': 'core.view',
        }.get(plugin_key)

    def _role_allows_tool_action(self, action_attr: str) -> bool:
        if action_attr == 'op_planner_action' and not self._op_planner_available():
            return False
        perm = self._tool_permission_for_action(action_attr)
        return True if not perm else self._master_can(perm)

    def _role_allows_plugin_key(self, plugin_key: str) -> bool:
        perm = self._plugin_permission_for_key(plugin_key)
        return True if not perm else self._master_can(perm)

    def _refresh_role_restricted_tool_states(self) -> None:
        for action_attr in (
            'network_track_action',
            'embryo_tracker_action',
            'op_planner_action',
            'sample_track_action',
            'animal_reports_action',
            'flow_track_action',
            'projects_track_action',
            'heritage_track_action',
            'cage_track_action',
            'medi_track_action',
            'steroid_track_action',
        ):
            action = getattr(self, action_attr, None)
            if action is not None:
                action.setEnabled(self._role_allows_tool_action(action_attr))

    def _update_master_status_bar(self):
        """Update the status bar with the current user/role."""
        mt = getattr(self, 'master_track', None)
        if not mt:
            return
        lbl = getattr(self, '_master_status_label', None)
        if lbl is None:
            return
        if mt.is_logged_in:
            user = mt.user_db.get_user(mt.current_username)
            display = user.get("display_name", mt.current_username) if user else mt.current_username
            role_icon = "👑" if mt.current_role == "lord" else "👤"
            jobs = user.get("jobs", []) if user else []
            jobs_str = f" [{', '.join(jobs)}]" if jobs else ""
            lbl.setText(f" {role_icon} {display}{jobs_str} ")
        else:
            lbl.setText(f" 🔒 {self.messages.get('master_track.status.guest', 'Guest')} ")
        self._update_master_window_title()

    def _update_master_window_title(self):
        """Append or remove '[Read only]' from the window title based on
        Master_Track guest state and file-lock read-only mode."""
        mt = getattr(self, 'master_track', None)
        mt_disabled = "master_track" in getattr(self, '_disabled_plugins', set())
        is_guest = mt and not mt_disabled and not mt.is_logged_in
        is_file_locked = getattr(self, 'read_only_mode', False)
        read_only = is_guest or is_file_locked

        current = self.windowTitle()
        clean = current.replace(" [Read only]", "").replace(" [READ-ONLY]", "").strip()
        if read_only:
            self.setWindowTitle(f"{clean} [Read only]")
        else:
            self.setWindowTitle(clean)

    def _apply_master_button_states(self):
        """Grey out sidebar buttons based on current Master_Track permissions.
        When Master_Track is absent or disabled, all buttons stay enabled."""
        mt = getattr(self, 'master_track', None)
        mt_disabled = "master_track" in getattr(self, '_disabled_plugins', set())
        steroid_active = self._is_steroid_track_active()
        if not mt or mt_disabled:
            # No Master_Track or disabled → enable everything
            for attr in ('btn_new', 'btn_edit', 'btn_edit_animal', 'btn_load_blood',
                         'btn_load_urine', 'btn_load_weights', 'btn_load_sperm',
                         'btn_archive', 'btn_restore', 'btn_delete'):
                btn = getattr(self, attr, None)
                if btn:
                    btn.setEnabled(True)
            if hasattr(self, 'btn_load_sperm'):
                self.btn_load_sperm.setEnabled(steroid_active)
            return
        can_create = mt.can("core.create_animals")
        can_import = mt.can("core.import")
        can_edit = mt.can("core.edit_animal_core")
        can_archive = mt.can("core.archive_animals")
        can_delete = mt.can("core.delete_animals")

        if hasattr(self, 'btn_new'):
            self.btn_new.setEnabled(can_create)
        if hasattr(self, 'btn_edit'):
            # Edit button also needs a selection — only enable if both permission and selection
            self.btn_edit.setEnabled(can_edit and bool(self.selected_animals))
        if hasattr(self, 'btn_edit_animal'):
            self.btn_edit_animal.setEnabled(can_edit and bool(self.selected_animals))
        if hasattr(self, 'btn_load_blood'):
            self.btn_load_blood.setEnabled(can_import)
        if hasattr(self, 'btn_load_urine'):
            self.btn_load_urine.setEnabled(can_import)
        if hasattr(self, 'btn_load_weights'):
            self.btn_load_weights.setEnabled(can_import)
        if hasattr(self, 'btn_load_sperm'):
            self.btn_load_sperm.setEnabled(can_create and steroid_active)
        if hasattr(self, 'btn_archive'):
            self.btn_archive.setEnabled(can_archive)
        if hasattr(self, 'btn_restore'):
            _sel_arch = getattr(self, '_selected_archived', [])
            self.btn_restore.setEnabled(can_archive and bool(_sel_arch))
        if hasattr(self, 'btn_delete'):
            _sel_arch = getattr(self, '_selected_archived', [])
            self.btn_delete.setEnabled(can_delete and bool(_sel_arch))
        # Grey out Style settings menu for guests when Master Track is active
        if hasattr(self, 'style_settings_action'):
            can_style = mt.can("core.style_settings") if mt else True
            self.style_settings_action.setEnabled(can_style)

    def _freeze_dialog_inputs(self, root) -> None:
        """Disable all interactive input widgets inside *root* (read-only mode).

        QPushButton texts matching 'cancel / close / schließen / abbrechen'
        are left enabled so the user can still dismiss the dialog.
        """
        from PyQt6.QtWidgets import (
            QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox,
            QComboBox, QCheckBox, QRadioButton, QDateEdit, QSlider,
            QPushButton,
        )
        _KEEP = {"cancel", "close", "schließen", "abbrechen", "ok"}
        for w in root.findChildren(QWidget):
            if isinstance(w, (QLineEdit, QTextEdit, QPlainTextEdit,
                               QSpinBox, QDoubleSpinBox, QComboBox,
                               QCheckBox, QRadioButton, QDateEdit, QSlider)):
                w.setEnabled(False)
            elif isinstance(w, QPushButton):
                if w.text().strip().lower() not in _KEEP:
                    w.setEnabled(False)

    def _apply_dialog_field_permissions(self, groups: dict) -> None:
        """Disable widget groups for which the current user lacks the required permission.

        groups: dict mapping permission_key → widget, or list/tuple of widgets.
        None entries are silently skipped.
        Disabling a container (QGroupBox, QWidget) greys out all its children.
        """
        for perm, widgets in groups.items():
            if not self._master_can(perm):
                if not isinstance(widgets, (list, tuple)):
                    widgets = [widgets]
                for w in widgets:
                    if w is not None:
                        try:
                            w.setEnabled(False)
                        except Exception:
                            pass

    def _launch_op_planner(self):
        """
        Show the surgical planning dialog from the plugin.  This method
        dynamically imports the plugin to avoid a hard dependency at startup.
        If the planner has already been opened, it will be brought to the
        foreground rather than re‑created.  Any errors are logged and
        displayed to the user.
        """
        if not self._master_can('op_scheduler.view'):
            self._show_permission_denied()
            return
        try:
            # Determine if the planner is available.  This checks for the plugin
            # package being present in the expected location.  If not,
            # disable the action and bail out early.
            if not self._op_planner_available():
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.op_planner_not_available", "OP‑Planner plugin is not installed.")
                )
                return

            # Lazy import to avoid slowing down application startup.  Import the
            # entire module so that we can adjust its global variables (e.g. the
            # data and settings file paths) before constructing the widget.
            import importlib
            from matplotlib import rcParams, rcdefaults, rc_context

            module_name = 'Plugins.Surgery_Planner.surgery_planner'
            # 1) snapshot ProgTrack’s default label sizes (currently unused)
            _pt_default_sizes = {
                'xtick.labelsize': rcParams['xtick.labelsize'],
                'axes.labelsize':  rcParams['axes.labelsize'],
            }
            # 2) import plugin (its rcParams tweaks apply here)
            surgery_planner = importlib.import_module(module_name)
            # 3) capture plugin small-label settings
            _plugin_sizes = {
                'xtick.labelsize': rcParams['xtick.labelsize'],
                'axes.labelsize':  rcParams['axes.labelsize'],
            }
            # 4) restore ProgTrack’s defaults for all subsequent plotting
            rcdefaults()

            # Pass the current ProgTrack data file and settings file to the
            # plugin so it reads the same data as the main application.
            try:
                # The plugin defines DATA_FILE and SETTINGS_FILE at module level.
                # Point plugin module variables to the active data file and
                # settings file.  Absolute paths are robust against
                # working‑directory changes.
                surgery_planner.DATA_FILE = os.path.abspath(DATEN_DATEI)
                surgery_planner.SETTINGS_FILE = os.path.abspath(SETTINGS_FILE)
            except Exception:
                # Even if updating fails, the plugin will fall back to its own
                # relative paths.  We log but do not crash in this case.
                logging.warning('Could not override surgery planner data paths')

            GanttWidget = getattr(surgery_planner, 'GanttWidget', None)
            if GanttWidget is None:
                raise ImportError('GanttWidget class not found in surgery planner plugin')

            # Build the list of ALL donor/surrogate animals from the current ProgTrack state.
            # Include ALL animals regardless of pregnancy/health status - the surgery planner
            # will handle exclusions via checkboxes.
            # Use ROLE-BASED filtering (no 'category' anymore): pass only Spenderin/Amme.
            # For each eligible animal, construct a record with required fields plus status info.
            animals_for_planner: list[dict] = []
            try:
                for name, rec in self.animals.items():
                    # Role-based inclusion: only female roles are schedulable here
                    if rec.get('rolle') not in (Role.SPENDER.value, Role.AMME.value):
                        continue
                    # Compute current status to pass to planner for auto-exclusion
                    status = self._get_status(name)
                    # Build a new record for the planner
                    # Extract embryo transfer dates from events list
                    embryo_transfer_dates = [ev['datum'] for ev in rec.get('events', []) 
                                            if ev.get('typ') == 'embryo_transfer' and ev.get('datum')]
                    
                    new_rec = {
                        'name': name,
                        'rolle': rec.get('rolle'),
                        # Normalise maximum values; default to zero if missing
                        'OP_max':         rec.get('max_op',     rec.get('OP_max',     0)),
                        'FSH_max':        rec.get('max_fsh',    rec.get('FSH_max',    0)),
                        'Embryo_max':     rec.get('max_embryo', rec.get('Embryo_max', 0)),
                        # include already-performed raw history
                        'op':              rec.get('op', []),
                        'embryoübertragung': embryo_transfer_dates,
                        # preserve any other event types
                        'events':         rec.get('events', []),
                        # Pass status info for auto-exclusion in planner
                        'status': status,
                    }
                    animals_for_planner.append(new_rec)
            except Exception as ex:
                logging.error(f"Failed to prepare animal list for planner: {ex}")
                animals_for_planner = []

            # Create or reuse the planner dialog, injecting the animals list
            # create or reuse the planner dialog *inside* the plugin’s tiny-label context
            if not hasattr(self, '_op_planner_dialog') or self._op_planner_dialog is None or not self._op_planner_dialog.isVisible():
                with rc_context(_plugin_sizes):
                    self._op_planner_dialog = GanttWidget(animals=animals_for_planner, messages=self.messages, parent=self)
            else:
                try:
                    with rc_context(_plugin_sizes):
                        self._op_planner_dialog.animals = animals_for_planner
                        if hasattr(self._op_planner_dialog, 'messages'):
                            self._op_planner_dialog.messages = self.messages
                        self._op_planner_dialog.update_animal_table()
                except Exception as e:
                    logging.error(f"Error updating OP Planner dialog: {e}")

            # run the plugin dialog modally inside its small‐label context
            dialog = self._op_planner_dialog
            with rc_context(_plugin_sizes):
                # In PyQt6, exec() runs the dialog application-modally by default:
                dialog.exec()

        except Exception as e:
            logging.error(f"Failed to launch OP planner: {e}")
            QMessageBox.critical(self, self.messages.get("error.title", "Error"), str(e))

    def _launch_sample_track(self):
        """Show the Sample Track window."""
        if not self._master_can('sample_track.use'):
            self._show_permission_denied()
            return
        if not getattr(self, 'has_sample_track_plugin', False) or self.sample_track_plugin is None:
            QMessageBox.information(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("error.sample_track_not_available",
                                  "Sample Track plugin is not installed."))
            return
        self.sample_track_plugin.show_window()

    def _launch_animal_reports(self):
        """
        Toggle the visibility of the Reports tab.
        If the tab is hidden, show it and switch to it.
        If the tab is visible, hide it and switch to Plots tab.
        """
        try:
            # Check if plugin is available
            if not hasattr(self, 'reports_enabled') or not self.reports_enabled:
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.animal_reports_not_available", 
                                    "Animal Reports plugin is not installed.")
                )
                return
            
            # Find the Reports tab index
            reports_tab_index = -1
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == self.messages.get("tab.reports", "Reports"):
                    reports_tab_index = i
                    break
            
            # Toggle visibility
            if reports_tab_index >= 0:
                # Tab exists - check if it's visible
                if self.main_tabs.isTabVisible(reports_tab_index):
                    # Hide the tab and switch to Plots
                    self.main_tabs.setTabVisible(reports_tab_index, False)
                    self.main_tabs.setCurrentIndex(0)  # Switch to Plots tab
                    logging.info("Reports tab hidden")
                else:
                    # Show the tab and switch to it
                    self.main_tabs.setTabVisible(reports_tab_index, True)
                    self.main_tabs.setCurrentIndex(reports_tab_index)
                    
                    # Update reports if an animal is selected
                    if self.selected_animals and self.reports_tab is not None:
                        self._update_reports_for_animal(self.selected_animals[-1])
                    
                    logging.info("Reports tab shown")
            else:
                logging.warning("Reports tab not found")
            
        except Exception as e:
            logging.error(f"Error toggling Animal Reports tab: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to toggle Reports tab:\n{str(e)}"
            )
    
    def _launch_embryo_tracker(self):
        """Launch the Embryo Tracker plugin."""
        if not self._master_can('embryo_track.view'):
            self._show_permission_denied()
            return
        try:
            from Plugins.Embryo_Track.embryo_track import show_embryo_tracker
            show_embryo_tracker(self.messages, self)
        except ImportError as e:
            logging.error(f"Failed to import Embryo Tracker plugin: {e}")
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("error.embryo_tracker_not_available", 
                                "Embryo Tracker plugin is not installed.")
            )
        except Exception as e:
            logging.error(f"Error launching Embryo Tracker: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to launch Embryo Tracker:\n{str(e)}"
            )
    
    def _launch_network_track(self):
        """
        Toggle the Network Track chat window.
        If the window is closed or doesn't exist, create and show it.
        If the window exists and is visible, bring it to front.
        If the window exists but is hidden, show it.
        """
        try:
            # Check if plugin is available
            if not hasattr(self, 'network_track_enabled') or not self.network_track_enabled:
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.network_track_not_available", 
                                    "Network Track plugin is not installed.")
                )
                return
            
            # Check if window exists and is still valid
            if self.network_track_window is not None:
                try:
                    # Try to check if window is still valid
                    if self.network_track_window.isVisible():
                        # Window exists and is visible - bring to front
                        self.network_track_window.activateWindow()
                        self.network_track_window.raise_()
                        logging.info("Network Track window brought to front")
                        return
                    else:
                        # Window exists but is hidden - show it
                        self.network_track_window.show()
                        self.network_track_window.activateWindow()
                        self.network_track_window.raise_()
                        logging.info("Network Track window shown")
                        return
                except RuntimeError:
                    # Window was deleted - create new one
                    self.network_track_window = None
            
            # Create new window
            from Plugins.Network_Track.network_track import NetworkTrackWidget
            self.network_track_window = NetworkTrackWidget(self.messages, self, app=self)
            self.network_track_window.show()
            logging.info("Network Track window created and shown")
            
        except ImportError as e:
            logging.error(f"Failed to import Network Track plugin: {e}")
            QMessageBox.warning(
                self,
                self.messages.get("error.title", "Error"),
                self.messages.get("error.network_track_not_available", 
                                "Network Track plugin is not installed.")
            )
        except Exception as e:
            logging.error(f"Error launching Network Track: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to launch Network Track:\n{str(e)}"
            )
    
    def _launch_flow_track(self):
        """
        Toggle the visibility of the Flow Track tab.
        If the tab is hidden, show it and switch to it.
        If the tab is visible, hide it and switch to Plots tab.
        """
        try:
            # Check if plugin is available
            if not hasattr(self, 'flow_track_enabled') or not self.flow_track_enabled:
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.flow_track_not_available", 
                                    "Flow Track plugin is not installed.")
                )
                return
            
            # Find the Flow Track tab index
            flow_track_tab_index = -1
            for i in range(self.main_tabs.count()):
                if self.main_tabs.tabText(i) == self.messages.get("tab.flow_track", "Flow Track"):
                    flow_track_tab_index = i
                    break
            
            # Toggle visibility
            if flow_track_tab_index >= 0:
                # Tab exists - check if it's visible
                if self.main_tabs.isTabVisible(flow_track_tab_index):
                    # Hide the tab and switch to Plots
                    self.main_tabs.setTabVisible(flow_track_tab_index, False)
                    self.main_tabs.setCurrentIndex(0)  # Switch to Plots tab
                    logging.info("Flow Track tab hidden")
                else:
                    # Show the tab and switch to it
                    self.main_tabs.setTabVisible(flow_track_tab_index, True)
                    self.main_tabs.setCurrentIndex(flow_track_tab_index)
                    logging.info("Flow Track tab shown")
            else:
                logging.error("Flow Track tab not found")
                QMessageBox.warning(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.flow_track_not_available", 
                                    "Flow Track tab is not available.")
                )
            
        except Exception as e:
            logging.error(f"Error switching to Flow Track tab: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to switch to Flow Track:\n{str(e)}"
            )
    
    def _launch_projects_track(self):
        """
        Refresh the ProjectsTrack plugin tabs.
        This allows users to manually trigger a refresh of the project list.
        """
        try:
            # Check if plugin is available
            if not hasattr(self, 'has_projects_plugin') or not self.has_projects_plugin:
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.projects_track_not_available", 
                                    "ProjectsTrack plugin is not installed.")
                )
                return
            
            # Trigger refresh of project tabs
            if self.projects_plugin:
                self.projects_plugin._on_refresh_clicked()
                QMessageBox.information(
                    self,
                    self.messages.get("info.title", "Info"),
                    self.messages.get("projects.refreshed", "Project list refreshed.")
                )
                logging.info("ProjectsTrack: Manual refresh triggered from menu")
            
        except Exception as e:
            logging.error(f"Error refreshing ProjectsTrack: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to refresh ProjectsTrack:\n{str(e)}"
            )

    def _launch_heritage_track(self):
        """Toggle the visibility of the Heritage Track tab."""
        try:
            if not getattr(self, 'has_heritage_plugin', False):
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get(
                        "error.heritage_track_not_available",
                        "Heritage_Track plugin is not installed.",
                    ),
                )
                return

            # Find the Heritage Track tab index
            heritage_track_tab_index = -1
            candidate_widgets = []
            if getattr(self, 'heritage_track_tab', None) is not None:
                candidate_widgets.append(self.heritage_track_tab)
            if getattr(self, 'heritage_track_tab_placeholder', None) is not None:
                candidate_widgets.append(self.heritage_track_tab_placeholder)

            for i in range(self.main_tabs.count()):
                if self.main_tabs.widget(i) in candidate_widgets:
                    heritage_track_tab_index = i
                    break

            if heritage_track_tab_index < 0:
                for i in range(self.main_tabs.count()):
                    if self.main_tabs.tabText(i) == self.messages.get("tab.heritage_track", "Heritage Track"):
                        heritage_track_tab_index = i
                        break

            # Toggle visibility
            if heritage_track_tab_index >= 0:
                if self.main_tabs.isTabVisible(heritage_track_tab_index):
                    # Hide the tab and switch to Plots
                    self.main_tabs.setTabVisible(heritage_track_tab_index, False)
                    self.main_tabs.setCurrentIndex(0)
                    logging.info("Heritage Track tab hidden")
                else:
                    # Show the tab and switch to it (lazy-load on demand)
                    self.main_tabs.setTabVisible(heritage_track_tab_index, True)
                    self.main_tabs.setCurrentIndex(heritage_track_tab_index)
                    logging.info("Heritage Track tab shown")
            else:
                logging.error("Heritage Track tab not found")
                QMessageBox.warning(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.heritage_track_not_available", "Heritage_Track plugin is not installed."),
                )
        except Exception as e:
            logging.error(f"Error launching Heritage_Track: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to launch Heritage_Track:\n{str(e)}",
            )

    def _launch_cage_track(self):
        """Toggle the visibility of the Cage Track tab."""
        try:
            if not getattr(self, 'has_cage_track_plugin', False):
                QMessageBox.information(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get(
                        "error.cage_track_not_available",
                        "Cage_Track plugin is not installed.",
                    ),
                )
                return

            # Find the Cage Track tab index
            cage_track_tab_index = -1
            candidate_widgets = []
            if getattr(self, 'cage_track_tab', None) is not None:
                candidate_widgets.append(self.cage_track_tab)
            if getattr(self, 'cage_track_tab_placeholder', None) is not None:
                candidate_widgets.append(self.cage_track_tab_placeholder)

            for i in range(self.main_tabs.count()):
                if self.main_tabs.widget(i) in candidate_widgets:
                    cage_track_tab_index = i
                    break

            if cage_track_tab_index < 0:
                for i in range(self.main_tabs.count()):
                    if self.main_tabs.tabText(i) == self.messages.get("tab.cage_track", "Cage Track"):
                        cage_track_tab_index = i
                        break

            # Toggle visibility
            if cage_track_tab_index >= 0:
                if self.main_tabs.isTabVisible(cage_track_tab_index):
                    self.main_tabs.setTabVisible(cage_track_tab_index, False)
                    self.main_tabs.setCurrentIndex(0)
                    logging.info("Cage Track tab hidden")
                else:
                    self.main_tabs.setTabVisible(cage_track_tab_index, True)
                    self.main_tabs.setCurrentIndex(cage_track_tab_index)
                    logging.info("Cage Track tab shown")
            else:
                logging.error("Cage Track tab not found")
                QMessageBox.warning(
                    self,
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.cage_track_not_available", "Cage_Track plugin is not installed."),
                )
        except Exception as e:
            logging.error(f"Error launching Cage_Track: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                self.messages.get("error.title", "Error"),
                f"Failed to launch Cage_Track:\n{str(e)}",
            )

    def _dlg_about_programm(self) -> None:
        """Open dialog to display program information from info.json (as pure HTML)."""
        dlg = QDialog(self)
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setWindowTitle(self.messages["dialog.about.title"])
        layout = QVBoxLayout(dlg)

        base_dir = os.path.dirname(__file__)
        lang = getattr(self, 'lang', None) or 'en'

        def _load_about_html(path: str) -> str:
            with open(path, 'r', encoding='utf-8') as f:
                raw = f.read()

            # Prefer JSON format (silences editor JSON lint and allows structured content)
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get('html'), str) and parsed.get('html'):
                        return parsed['html']
                    if isinstance(parsed.get('html_lines'), list):
                        return "\n".join(str(x) for x in parsed['html_lines'])
                # If parsed JSON does not match the expected schema, fall through.
            except Exception:
                pass

            # Legacy format: file contains raw HTML
            return raw

        candidates = [
            os.path.join(base_dir, f"info_{lang}.json"),
            os.path.join(base_dir, "info_en.json"),
            os.path.join(base_dir, "info.json"),
        ]

        html_content = ""
        last_error = None
        for info_path in candidates:
            try:
                html_content = _load_about_html(info_path)
                break
            except Exception as e:
                last_error = e
                continue

        if not html_content:
            html_content = (
                f"<p><strong>{self.messages.get('dialog.about.error_load', 'Error loading about text:')}</strong> "
                f"{last_error}</p>"
            )

        browser = QTextBrowser()
        browser.setHtml(html_content)
        layout.addWidget(browser)
        dlg.adjustSize()
        dlg.exec()

    def _dlg_partner(self, name: Optional[str], read_only: bool = False) -> None:
        """
        Create or edit a Partnertier (⚤). Creation requires: Name, Referenzgewicht,
        free text "Reproduktionsfeld" (appended in parentheses after the name in the list),
        free text "Partner von" (becomes status line), and Gesundheitstatus [krank].
        Editing: allow adding/changing weights and modifying all free fields except Name.
        """
        editing = bool(name)
        rec: Dict[str, Any] = self.animals.get(name, {}) if editing else {}

        # Standardized dialog shell (uniform width/label alignment)
        dlg_title = self.messages.get(
            "dialog.partner.title_edit", "Edit Partner: {name}"
        ).format(name=self._display_name(name)) if editing else self.messages.get("dialog.partner.title_new", "New Partner")
        dlg, vbox, form = self._new_std_dialog(dlg_title)
        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value=name or "",
            current_species=rec.get('species', ''),
            editing=editing,
            name_label_key="dialog.partner.field.name",
        )

        # ID / Chip Nr. / Origin
        id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)

        # Project
        _old_project = rec.get('project', '')
        _old_severity = rec.get('severity', '')
        project_le = QComboBox()
        project_le.setEditable(True)
        project_le.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        for _pn in self._load_project_names():
            project_le.addItem(_pn)
        _pidx = project_le.findText(_old_project)
        if _pidx >= 0:
            project_le.setCurrentIndex(_pidx)
        elif _old_project:
            project_le.insertItem(0, _old_project)
            project_le.setCurrentIndex(0)
        else:
            project_le.lineEdit().clear()
        self._std_widen(project_le)
        if not self._master_can('project.project_assign'):
            project_le.setEnabled(False)
            project_le.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        _has_medi = getattr(self, 'has_medi_track_plugin', False)
        _sev_items = [
            ('', self.messages.get('dialog.severity.please_select', '(Please select)')),
            ('SV0', self.messages.get('severity.0',   'SV0 - no severity')),
            ('SV1', self.messages.get('severity.sv1', 'SV1 - non-recovery')),
            ('SV2', self.messages.get('severity.sv2', 'SV2 - mild or very mild')),
            ('SV3', self.messages.get('severity.sv3', 'SV3 - moderate')),
            ('SV4', self.messages.get('severity.sv4', 'SV4 - severe')),
        ]
        severity_cb = QComboBox()
        severity_cb.setToolTip(self.messages.get('dialog.severity.tooltip', 'Project severity level'))
        for _sv_d, _sv_l in _sev_items:
            severity_cb.addItem(_sv_l, _sv_d)
        _old_severity_n = 'SV0' if _old_severity == '0' else _old_severity
        _sev_idx = next((i for i, (_d, _l) in enumerate(_sev_items) if _d == _old_severity_n), 0)
        severity_cb.setCurrentIndex(_sev_idx)
        if not self._master_can('project.manage_severity'):
            severity_cb.setEnabled(False)
            severity_cb.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        self._connect_project_severity_reset(project_le, severity_cb)
        _proj_sev_w = QWidget()
        _proj_sev_l = QHBoxLayout(_proj_sev_w)
        _proj_sev_l.setContentsMargins(0, 0, 0, 0)
        _proj_sev_l.setSpacing(4)
        _proj_sev_l.addWidget(project_le, 1)
        if _has_medi:
            _proj_sev_l.addWidget(severity_cb)
        form.addRow(self.messages.get("dialog.field.project", "Project:"), _proj_sev_w)

        # Birth Date and Death Date on the same line with Age calculation
        dates_layout = QHBoxLayout()
        birth_date_le = QLineEdit(rec.get('birth_date', ''))
        birth_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        birth_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        death_date_le = QLineEdit(rec.get('death_date', ''))
        death_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        death_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        special_status_le = QLineEdit(rec.get('special_status', ''))
        if not self._master_can('core.edit_animal_core'):
            special_status_le.setReadOnly(True)
            special_status_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            special_status_le.setStyleSheet('min-width: 0;')
        age_label = QLabel(calculate_age(rec.get('birth_date', ''), rec.get('death_date', '')))
        age_label.setStyleSheet("color: gray; font-style: italic;")
        
        def update_age():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))
        
        birth_date_le.textChanged.connect(update_age)
        death_date_le.textChanged.connect(update_age)
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel("/"))
        dates_layout.addWidget(death_date_le)
        dates_layout.addWidget(age_label)
        dates_layout.addWidget(QLabel(self.messages.get("dialog.field.special_status", "Special Status:")))
        dates_layout.addWidget(special_status_le)
        form.addRow(self.messages.get("dialog.field.birth_death_date", "Birth / Death Date:"), dates_layout)

        # Separation line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator)

        _cage_addr_group = None
        cage_address_fields = None
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values
                current_addr = self.cage_track_plugin.get_current_address(name if editing else "")
                structs = self.cage_track_plugin.get_structures_for_address()
                _cage_addr_group, cage_address_fields = build_address_group(
                    self.messages, current_addr,
                    structs["buildings"], structs["rooms"], structs["cages"],
                )
                form.addRow(_cage_addr_group)
            except Exception as e:
                logging.error(f"Cage_Track address fields failed: {e}")
                cage_address_fields = None

        ref_w_le = QLineEdit(str(rec.get('ref_weight', DEFAULT_REF_WEIGHT))); self._std_widen(ref_w_le)
        ref_w_le.setValidator(QDoubleValidator(0.0, 10000.0, 2))
        form.addRow(self.messages.get("dialog.partner.field.reference_weight", "Reference Weight (g):"), ref_w_le)

        # Sex (same pattern as offspring dialog, guarded by core.edit_animal_identity)
        sex_cb = QComboBox()
        sex_cb.addItem(self.messages.get("sex.male", "Male"), "Male")
        sex_cb.addItem(self.messages.get("sex.female", "Female"), "Female")
        sex_now = rec.get("sex", "Male")
        if sex_now == self.messages.get("sex.male", "Male"):
            sex_now = "Male"
        elif sex_now == self.messages.get("sex.female", "Female"):
            sex_now = "Female"
        sex_cb.setCurrentIndex(0 if sex_now == "Male" else 1)
        form.addRow(self.messages.get("dialog.offspring.sex", "Sex:"), sex_cb)

        rep_le = QLineEdit(rec.get('reproduktionsfeld', '')); self._std_widen(rep_le)
        form.addRow(self.messages.get("dialog.partner.field.reproduction_field", "Reproduction Field:"), rep_le)

        partner_von_le = QLineEdit(rec.get('partner_von', '')); self._std_widen(partner_von_le)
        form.addRow(self.messages.get("dialog.partner.field.partner_of", "Partner of:"), partner_von_le)

        _heritage_group = None
        heritage_parent_fields = None
        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
            _heritage_group, heritage_parent_fields = self.heritage_plugin.create_parent_group(name if editing else None, rec)
            for parent_widget in heritage_parent_fields.values():
                self._std_widen(parent_widget)
            self._add_parent_mode_selector(form, _heritage_group, heritage_parent_fields, default_mode="hide")

        _health_w_p = QWidget()
        _health_hl_p = QHBoxLayout(_health_w_p)
        _health_hl_p.setContentsMargins(0, 0, 0, 0)
        _health_hl_p.setSpacing(14)
        chk_sick = QCheckBox(self.messages.get("dialog.partner.checkbox.sick", "Sick"))
        chk_sick.setChecked(rec.get('sick', False))
        chk_abnormal = QCheckBox(self.messages.get("dialog.partner.checkbox.abnormal", "Abnormal"))
        chk_abnormal.setChecked(rec.get('abnormal_current', False))
        _health_hl_p.addWidget(chk_sick)
        _health_hl_p.addWidget(chk_abnormal)
        if self._is_projects_track_active():
            chk_in_exp = QCheckBox(self.messages.get("checkbox.in_experiment", "In Experiment"))
            chk_in_exp.setChecked(bool(rec.get('in_experiment', False)))
            currently_on = rec.get('in_experiment', False)
            perm = ('project.unset_in_experiment' if currently_on else 'project.set_in_experiment')
            chk_in_exp.setEnabled(self._master_can(perm))
            chk_in_exp.setToolTip(self.messages.get('tooltip.in_experiment', 'Mark this animal as currently in experiment'))
            _health_hl_p.addWidget(chk_in_exp)
        else:
            chk_in_exp = None
        _health_hl_p.addStretch()
        form.addRow(self.messages.get("dialog.partner.health_status", "Health Status:"), _health_w_p)
        self._wire_status_checkboxes(chk_sick, chk_abnormal, name, rec, dlg)

        vbox.addLayout(form)

        # --- Weights tab ---
        tabs = QTabWidget(dlg)
        # Hide tabs in the create-new dialog; only show when editing
        tabs.setVisible(editing)
        weights_tab = QWidget()
        tabs.addTab(weights_tab, self.messages.get("dialog.partner.tab.weights", "Weight"))
        
        # PdG plugin hook for partner dialog
        _pdg_tabs = None
        if self.has_pdg_plugin and hasattr(self, 'pdg_cap') and self.pdg_cap and hasattr(self.pdg_cap, 'hooks'):
            _pdg_tabs = self.pdg_cap.hooks.on_partner_dialog_tabs(tabs, rec, editing, self, name)
        
        vbox.addWidget(tabs, 1)

        wt_layout = QVBoxLayout(weights_tab)

        # existing weights
        existing_w = rec.get('gewicht', [])
        # Normalize to list of dicts {'datum': datetime, 'wert': float}
        norm_w = []
        for w in existing_w:
            try:
                d = w.get('datum')
                if isinstance(d, str):
                    # try parse common formats
                    for fmt in (DATE_FORMAT, "%Y-%m-%d", "%d.%m.%y"):
                        try:
                            d = datetime.strptime(d, fmt)
                            break
                        except Exception:
                            pass
                v = float(w.get('wert'))
                norm_w.append({'datum': d, 'wert': v})
            except Exception:
                continue

        # Add column headers
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 5)
        header_layout.setSpacing(5)
        date_header = QLabel(f"<b>{self.messages.get('table.header.date', 'Date')}</b>")
        date_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        weight_header = QLabel(f"<b>{self.messages.get('table.header.weight', 'Weight (g)')}</b>")
        weight_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        del_header = QLabel(f"<b>{self.messages.get('table.header.delete', 'Delete')}</b>")
        del_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        del_header.setFixedWidth(50)
        header_layout.addWidget(date_header, 1)  # Stretch factor 1
        header_layout.addWidget(weight_header, 1)  # Stretch factor 1
        header_layout.addWidget(del_header, 0)  # No stretch
        wt_layout.addLayout(header_layout)
        
        # Build a small table-like editor
        wt_rows: List[Tuple[QLineEdit, QLineEdit]] = []  # (date, value)

        def add_w_row(init: Optional[Tuple[str, str]] = None) -> None:
            row = QHBoxLayout()
            row.setSpacing(5)
            # Default date: today
            if init:
                default_date = init[0]
            else:
                default_date = datetime.now().date().strftime(DATE_FORMAT)
            d_le = QLineEdit(default_date)
            v_le = QLineEdit(init[1] if init else "")
            d_le.setPlaceholderText(DATE_FORMAT)
            
            # Add real-time date validation styling
            def validate_weight_date():
                date_text = d_le.text().strip()
                if date_text:
                    try:
                        datetime.strptime(date_text, DATE_FORMAT)
                        d_le.setStyleSheet("")  # Valid - clear any error styling
                    except ValueError:
                        d_le.setStyleSheet("border: 2px solid red;")  # Invalid - red border
                else:
                    d_le.setStyleSheet("")  # Empty - no styling
            
            d_le.textChanged.connect(validate_weight_date)
            
            v_le.setValidator(QDoubleValidator(0.0, 100000.0, 3))
            add_btn = QPushButton("×")
            add_btn.setFixedWidth(50)

            def rm():
                # Show confirmation dialog
                reply = self._show_message_raw(
                    self.messages.get("dialog.confirm_delete.title", "Confirm Deletion"),
                    self.messages.get("dialog.confirm_delete.message", "Do you really wish to delete this entry?"),
                    "question",
                    buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                for i, (d, v) in enumerate(wt_rows):
                    if d is d_le:
                        wt_rows.pop(i)
                        # remove all widgets from the row layout
                        while row.count():
                            itm = row.takeAt(0).widget()
                            if itm:
                                itm.setParent(None)
                        break

            add_btn.clicked.connect(rm)
            row.addWidget(d_le, 1)
            row.addWidget(v_le, 1)
            row.addWidget(add_btn, 0)
            # Insert new rows above the "Neu Gewicht" button if present
            try:
                idx_btn = wt_layout.indexOf(btn_add_w)  # -1 if button not added yet
            except NameError:
                idx_btn = -1
            if idx_btn >= 0:
                wt_layout.insertLayout(idx_btn, row)
            else:
                wt_layout.addLayout(row)
            wt_rows.append((d_le, v_le))

        # seed with existing rows
        for w in sorted(norm_w, key=lambda t: t['datum'] or datetime.now()):
            d = w['datum'].strftime(DATE_FORMAT) if isinstance(w['datum'], datetime) else str(w['datum'])
            add_w_row((d, str(w['wert'])))

        btn_add_w = QPushButton(self.messages.get("dialog.partner.button.new_weight", "New Weight"))
        btn_add_w.clicked.connect(lambda: add_w_row(None))
        wt_layout.addWidget(btn_add_w)
        wt_layout.addStretch(1)

        # --- Save button (standard single center button) ---
        save_btn = QPushButton(self.messages.get("dialog.partner.button.save", "Save"))
        vbox.addWidget(save_btn)

        def on_save() -> None:
            self._save_trace("partner.save.enter", editing=editing, original_name=name)
            # Basic validation
            new_name = name_le.text().strip()
            self._save_trace("partner.save.name_read", new_name=new_name)
            if not new_name:
                self._show_message("error.empty_name", self.messages.get("dialog.partner.error.empty_name", "Name cannot be empty."))
                return
            selected_species = self._species_from_combo(species_cb)
            birth_date = self._normalize_identity_birth_for_save(
                birth_date_le.text(), required=not editing)
            if birth_date is None:
                return
            if not editing and not self._validate_identity_species_for_save(selected_species):
                return
            if editing and not self._validate_existing_identity_for_save(
                    name, new_name, selected_species, birth_date):
                return
            if self._name_species_conflict(
                    new_name, selected_species, birth_date,
                    exclude_key=name if editing else None):
                self._show_message("error.name_exists", self.messages.get("dialog.partner.error.name_exists", "Name already exists."))
                return

            if editing and not self._confirm_species_change_once(species_cb, initial_species, selected_species):
                return

            _orig_name = new_name
            new_name = self._resolve_animal_key(new_name, selected_species, birth_date)
            self._save_trace(
                "partner.save.identity_resolved",
                new_name=new_name,
                selected_species=selected_species,
            )

            # parse ref weight
            try:
                ref_w = float(ref_w_le.text().strip().replace(',', '.'))
            except Exception:
                ref_w = DEFAULT_REF_WEIGHT

            # parse weights
            new_weights = []
            for d_le, v_le in wt_rows:
                ds = d_le.text().strip()
                vs = v_le.text().strip().replace(',', '.')
                if not ds or not vs:
                    continue
                try:
                    d = datetime.strptime(ds, DATE_FORMAT)
                except Exception:
                    try:
                        d = datetime.strptime(ds, "%Y-%m-%d")
                    except Exception:
                        self._show_message("error.invalid_date", self.messages.get("dialog.partner.error.invalid_date", "Invalid date: {date}").format(date=ds))
                        return
                try:
                    val = float(vs)
                except Exception:
                    self._show_message_raw("Fehler", f"Ungültiges Gewicht: {vs}")
                    return
                new_weights.append({'datum': d, 'wert': val})

            # write record
            rec_obj = dict(self.animals.get(name, {})) if editing else {}
            rec_obj['rolle']             = Role.PARTNER.value
            rec_obj['id']                = id_le.text().strip()
            rec_obj['chip_nr']           = chip_le.text().strip()
            rec_obj['origin']            = origin_le.text().strip()
            rec_obj['project']           = project_le.currentText().strip()
            rec_obj['severity']          = severity_cb.currentData()
            rec_obj['death_date']        = death_date_le.text().strip()
            rec_obj['special_status']    = special_status_le.text().strip()
            rec_obj['ref_weight']        = ref_w
            self._apply_identity_fields_to_record(
                rec_obj, new_name, _orig_name, selected_species, birth_date)
            rec_obj['sex']               = sex_cb.currentData() or sex_cb.currentText()
            rec_obj['reproduktionsfeld'] = rep_le.text().strip()
            rec_obj['partner_von']       = partner_von_le.text().strip()
            _was_sick_p     = bool(rec_obj.get('sick', False))
            _was_abnormal_p = bool(rec_obj.get('abnormal_current', False))
            is_sick = bool(chk_sick.isChecked())
            is_abnormal_p = bool(chk_abnormal.isChecked())
            self._update_sick_times(rec_obj, is_sick)
            self._update_abnormal_times(rec_obj, is_abnormal_p)
            self._auto_fill_status_signature(
                rec_obj, is_sick != _was_sick_p or is_abnormal_p != _was_abnormal_p)
            old_in_exp_p = rec_obj.get('in_experiment', False)
            new_in_exp_p = chk_in_exp.isChecked() if chk_in_exp is not None else old_in_exp_p
            if new_in_exp_p != old_in_exp_p:
                _perm_p = ('project.unset_in_experiment' if old_in_exp_p else 'project.set_in_experiment')
                if not self._master_can(_perm_p):
                    new_in_exp_p = old_in_exp_p
            new_in_exp_p = self._coerce_in_experiment_for_project(
                new_in_exp_p, rec_obj.get('project', ''))
            rec_obj['in_experiment'] = new_in_exp_p
            # lists expected elsewhere in code
            rec_obj['daten']    = rec_obj.get('daten', [])
            rec_obj['events']   = rec_obj.get('events', [])
            rec_obj['gewicht']  = new_weights
            self._save_trace(
                "partner.save.record_built",
                new_name=new_name,
                record=self._save_trace_record_summary(rec_obj),
                old_project=_old_project,
                old_severity=_old_severity,
            )

            if (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
                and heritage_parent_fields is not None
            ):
                try:
                    self._save_trace("partner.save.heritage_parent.before", new_name=new_name)
                    parent_values = self.heritage_plugin.read_parent_group(heritage_parent_fields)
                    self.heritage_plugin.save_parentage(new_name, parent_values, source="plugin")
                    # Create heritage-only placeholders for non-existing parents
                    mother = parent_values.get("egg_donor", "")
                    father = parent_values.get("sperm_donor", "")
                    species = rec_obj.get("species", "")
                    self.heritage_plugin._ensure_parent_placeholders(mother, father, species)
                    self._save_trace("partner.save.heritage_parent.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("partner.save.heritage_parent.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track parent save failed for {new_name}: {e}")

            if (
                getattr(self, 'has_cage_track_plugin', False)
                and getattr(self, 'cage_track_plugin', None)
                and cage_address_fields is not None
            ):
                try:
                    self._save_trace("partner.save.cage_address.before", new_name=new_name)
                    from Plugins.Cage__Track.ui_address_fields import extract_address_values
                    addr_values = extract_address_values(cage_address_fields)
                    self.cage_track_plugin.save_address_from_dialog(new_name, addr_values)
                    self._save_trace("partner.save.cage_address.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("partner.save.cage_address.exception", new_name=new_name, error=e)
                    logging.error(f"Cage_Track address save failed for {new_name}: {e}")

            self._save_trace("partner.save.commit.before", new_name=new_name)
            self.animals[new_name] = rec_obj
            if editing and new_name != name:
                self.animals.pop(name, None)
                self._rewrite_animal_references_after_identity_change(name, new_name, _orig_name)
            self._save_trace("partner.save.commit.after", new_name=new_name, animal_count=len(self.animals))
            # Sync to Heritage Track (including sex from dialog)
            if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
                try:
                    self._save_trace("partner.save.heritage_sync.before", new_name=new_name)
                    self.heritage_plugin.sync_from_record(new_name, rec_obj, in_main_animals=True)
                    self._save_trace("partner.save.heritage_sync.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("partner.save.heritage_sync.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track sync failed for partner {new_name}: {e}")
            self._save_trace("partner.save.project_updates.schedule.before", new_name=new_name)
            self._schedule_post_animal_save_project_updates(
                new_name, _old_project, rec_obj.get('project', ''),
                _old_severity, rec_obj.get('severity', ''),
                old_in_exp_p, new_in_exp_p)
            self._save_trace("partner.save.project_updates.schedule.after", new_name=new_name)
            self._save_trace("partner.save.persistence.before", new_name=new_name)
            self._save_persistence(defer_post_save_work=True)
            self._save_trace("partner.save.persistence.after", new_name=new_name)
            self._save_trace("partner.save.dialog_accept.before", new_name=new_name)
            dlg.accept()
            self._save_trace("partner.save.dialog_accept.after", new_name=new_name)
            # Force heritage visible to show newly created parent placeholders
            _heritage_fields_present = (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
                and heritage_parent_fields is not None
            )
            self._refresh_list(update_tab_visibility=True, force_heritage_visible=_heritage_fields_present)
            # Select new/edited item
            items = self.lst.findItems(new_name, Qt.MatchFlag.MatchStartsWith)
            if items:
                self.lst.setCurrentItem(items[0])
            # Refresh report table if Reports tab is active
            if self.reports_enabled and hasattr(self, 'report_current_animal'):
                if self.report_current_animal == new_name:
                    self._update_report_table()

        save_btn.clicked.connect(on_save)
        
        # Adjust dialog width based on tab content
        def adjust_dialog_width():
            if tabs.isVisible():
                current_widget = tabs.currentWidget()
                if current_widget:
                    # Force layout update to get accurate size
                    current_widget.updateGeometry()
                    QApplication.processEvents()
                    
                    # Get the actual content width including all widgets
                    content_width = current_widget.sizeHint().width()
                    # Add extra padding for margins, scrollbars, and fixed-width elements
                    dialog_width = max(700, content_width + 150)
                    dlg.setMinimumWidth(dialog_width)
                    dlg.resize(dialog_width, dlg.height())
        
        tabs.currentChanged.connect(lambda: adjust_dialog_width())
        
        self._apply_dialog_width(dlg)
        QTimer.singleShot(100, adjust_dialog_width)
        # ── Field-level permissions ───────────────────────────────────────────
        _pdg_extra = list(_pdg_tabs) if isinstance(_pdg_tabs, (list, tuple)) else []
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                project_le, severity_cb,
                birth_date_le, death_date_le, special_status_le, sex_cb,
            ],
            'core.edit_animal_housing': [_cage_addr_group, _heritage_group, rep_le, partner_von_le],
            'core.edit_animal_measurements': [weights_tab],
            'core.edit_animal_research_data': [ref_w_le] + _pdg_extra,
        })
        if read_only:
            self._freeze_dialog_inputs(dlg)
        dlg.exec()


    def _dlg_samenspender(self, name: Optional[str], read_only: bool = False) -> None:
        """
        Create or edit a male donor (Samenspender).  When `name` is `None` a
        new Samenspender is created; otherwise the existing Samenspender is
        edited.  The dialog presents fields for name, reference weight,
        maximum number of sperm samples, recovery time and health status.
        It also includes two tabs for entering sperm measurements and
        weight values.
        """
        editing = bool(name)
        # retrieve existing record or use defaults for a new entry
        rec: Dict[str, Any] = self.animals.get(name, {}) if editing else {}
        steroid_active = self._is_steroid_track_active()
        
        # Seed defaults
        rec.setdefault('sick', False)
        rec.setdefault('ref_weight', DEFAULT_REF_WEIGHT)
        rec.setdefault('recovery_time', DEFAULT_RECOVERY_TIME)

        # Standardized dialog shell (uniform width/label alignment)
        dlg_title = self.messages.get("dialog.sperm_donor.edit_title", "Edit Sperm Donor: {name}").format(name=self._display_name(name)) if editing else \
                   self.messages.get("dialog.sperm_donor.new_title", "New Sperm Donor")
        dlg, v, form = self._new_std_dialog(dlg_title)
        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value=name or "",
            current_species=rec.get('species', ''),
            editing=editing,
            name_label_key="dialog.sperm_donor.label.name",
        )

        # ID / Chip Nr. / Origin
        id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)

        # Project
        _old_project = rec.get('project', '')
        _old_severity = rec.get('severity', '')
        project_le = QComboBox()
        project_le.setEditable(True)
        project_le.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        for _pn in self._load_project_names():
            project_le.addItem(_pn)
        _pidx = project_le.findText(_old_project)
        if _pidx >= 0:
            project_le.setCurrentIndex(_pidx)
        elif _old_project:
            project_le.insertItem(0, _old_project)
            project_le.setCurrentIndex(0)
        else:
            project_le.lineEdit().clear()
        self._std_widen(project_le)
        if not self._master_can('project.project_assign'):
            project_le.setEnabled(False)
            project_le.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        _has_medi = getattr(self, 'has_medi_track_plugin', False)
        _sev_items = [
            ('', self.messages.get('dialog.severity.please_select', '(Please select)')),
            ('SV0', self.messages.get('severity.0',   'SV0 - no severity')),
            ('SV1', self.messages.get('severity.sv1', 'SV1 - non-recovery')),
            ('SV2', self.messages.get('severity.sv2', 'SV2 - mild or very mild')),
            ('SV3', self.messages.get('severity.sv3', 'SV3 - moderate')),
            ('SV4', self.messages.get('severity.sv4', 'SV4 - severe')),
        ]
        severity_cb = QComboBox()
        severity_cb.setToolTip(self.messages.get('dialog.severity.tooltip', 'Project severity level'))
        for _sv_d, _sv_l in _sev_items:
            severity_cb.addItem(_sv_l, _sv_d)
        _old_severity_n = 'SV0' if _old_severity == '0' else _old_severity
        _sev_idx = next((i for i, (_d, _l) in enumerate(_sev_items) if _d == _old_severity_n), 0)
        severity_cb.setCurrentIndex(_sev_idx)
        if not self._master_can('project.manage_severity'):
            severity_cb.setEnabled(False)
            severity_cb.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        self._connect_project_severity_reset(project_le, severity_cb)
        _proj_sev_w = QWidget()
        _proj_sev_l = QHBoxLayout(_proj_sev_w)
        _proj_sev_l.setContentsMargins(0, 0, 0, 0)
        _proj_sev_l.setSpacing(4)
        _proj_sev_l.addWidget(project_le, 1)
        if _has_medi:
            _proj_sev_l.addWidget(severity_cb)
        form.addRow(self.messages.get("dialog.field.project", "Project:"), _proj_sev_w)

        # Birth Date and Death Date on the same line with Age calculation
        dates_layout = QHBoxLayout()
        birth_date_le = QLineEdit(rec.get('birth_date', ''))
        birth_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        birth_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        death_date_le = QLineEdit(rec.get('death_date', ''))
        death_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        death_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        special_status_le = QLineEdit(rec.get('special_status', ''))
        if not self._master_can('core.edit_animal_core'):
            special_status_le.setReadOnly(True)
            special_status_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            special_status_le.setStyleSheet('min-width: 0;')
        age_label = QLabel(calculate_age(rec.get('birth_date', ''), rec.get('death_date', '')))
        age_label.setStyleSheet("color: gray; font-style: italic;")
        
        def update_age():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))
        
        birth_date_le.textChanged.connect(update_age)
        death_date_le.textChanged.connect(update_age)
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel("/"))
        dates_layout.addWidget(death_date_le)
        dates_layout.addWidget(age_label)
        dates_layout.addWidget(QLabel(self.messages.get("dialog.field.special_status", "Special Status:")))
        dates_layout.addWidget(special_status_le)
        form.addRow(self.messages.get("dialog.field.birth_death_date", "Birth / Death Date:"), dates_layout)

        # Separation line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator)

        _cage_addr_group = None
        cage_address_fields = None
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values
                current_addr = self.cage_track_plugin.get_current_address(name if editing else "")
                structs = self.cage_track_plugin.get_structures_for_address()
                _cage_addr_group, cage_address_fields = build_address_group(
                    self.messages, current_addr,
                    structs["buildings"], structs["rooms"], structs["cages"],
                )
                form.addRow(_cage_addr_group)
            except Exception as e:
                logging.error(f"Cage_Track address fields failed: {e}")
                cage_address_fields = None
            
        # Reference weight
        ref_w_le = QLineEdit(str(rec.get('ref_weight', DEFAULT_REF_WEIGHT))); self._std_widen(ref_w_le)
        ref_w_le.setValidator(QDoubleValidator(0.0, 10000.0, 2))
        form.addRow(self.messages.get("dialog.sperm_donor.label.ref_weight", "Reference Weight (g):"), ref_w_le)
        
        # Separator after reference weight
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator2)
        
        # Max sperm samples
        lbl_max_sperm = QLabel(self.messages.get("dialog.sperm_donor.label.max_sperm_samples", "Max Sperm Samples:"))
        max_sperm_le = QLineEdit(str(rec.get('max_spermaproben', 100))); self._std_widen(max_sperm_le)
        max_sperm_le.setValidator(QIntValidator(1, 10000))
        form.addRow(lbl_max_sperm, max_sperm_le)
        try:
            form.setRowVisible(lbl_max_sperm, steroid_active)
        except Exception:
            lbl_max_sperm.setVisible(steroid_active)
            max_sperm_le.setVisible(steroid_active)
        
        # Recovery time (days)
        lbl_recovery = QLabel(self.messages.get('form.label.recovery_time', 'Recovery Time (days):'))
        rec_le = QLineEdit(str(rec.get('recovery_time', DEFAULT_RECOVERY_TIME))); self._std_widen(rec_le)
        rec_le.setValidator(QIntValidator(1, 365))
        form.addRow(lbl_recovery, rec_le)
        try:
            form.setRowVisible(lbl_recovery, steroid_active)
        except Exception:
            lbl_recovery.setVisible(steroid_active)
            rec_le.setVisible(steroid_active)
        
        # Health status
        _health_w_s = QWidget()
        _health_hl_s = QHBoxLayout(_health_w_s)
        _health_hl_s.setContentsMargins(0, 0, 0, 0)
        _health_hl_s.setSpacing(14)
        chk_sick = QCheckBox(self.messages.get("status.sick", "Sick"))
        chk_sick.setChecked(rec.get('sick', False))
        chk_abnormal = QCheckBox(self.messages.get("status.abnormal", "Abnormal"))
        chk_abnormal.setChecked(rec.get('abnormal_current', False))
        _health_hl_s.addWidget(chk_sick)
        _health_hl_s.addWidget(chk_abnormal)
        if self._is_projects_track_active():
            chk_in_exp = QCheckBox(self.messages.get("checkbox.in_experiment", "In Experiment"))
            chk_in_exp.setChecked(bool(rec.get('in_experiment', False)))
            currently_on = rec.get('in_experiment', False)
            perm = ('project.unset_in_experiment' if currently_on else 'project.set_in_experiment')
            chk_in_exp.setEnabled(self._master_can(perm))
            chk_in_exp.setToolTip(self.messages.get('tooltip.in_experiment', 'Mark this animal as currently in experiment'))
            _health_hl_s.addWidget(chk_in_exp)
        else:
            chk_in_exp = None
        _health_hl_s.addStretch()
        form.addRow(self.messages.get("form.label.health_status", "Health Status:"), _health_w_s)
        self._wire_status_checkboxes(chk_sick, chk_abnormal, name, rec, dlg)

        _heritage_group = None
        heritage_parent_fields = None
        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
            _heritage_group, heritage_parent_fields = self.heritage_plugin.create_parent_group(name if editing else None, rec)
            for parent_widget in heritage_parent_fields.values():
                self._std_widen(parent_widget)
            self._add_parent_mode_selector(form, _heritage_group, heritage_parent_fields, default_mode="hide")

        v.addLayout(form)

        # --- Tabbed editors for sperm and weight ---
        tabs = QTabWidget()
        # Hide tabs in the create-new dialog; only show when editing
        tabs.setVisible(editing)
        # Sperm tab
        sperm_tab = QWidget()
        sperm_layout = QVBoxLayout(sperm_tab)
        # keep outer container margins at zero so headers line up with rows
        sperm_layout.setContentsMargins(0, 0, 0, 0)
        
        # Scroll area for rows
        frame = QFrame()
        # remove default frame to avoid width offset vs. header
        frame.setFrameShape(QFrame.Shape.NoFrame)
        frame.setLineWidth(0)
        frame.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        row_layout = QVBoxLayout(frame)
        # zero margins so row content starts flush with header
        row_layout.setContentsMargins(0, 0, 0, 0)
        
        # Column headers - add INSIDE the frame so they scroll with content
        hdr = QHBoxLayout()
        hdr.setContentsMargins(0, 0, 0, 5)
        hdr.setSpacing(5)
        def _hdr_label(text: str, bold: bool = True) -> QLabel:
            lbl = QLabel(f"<b>{text}</b>" if bold else text)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            return lbl
        hdr.addWidget(_hdr_label(self.messages.get("table.header.date", "Date")), 1)
        hdr.addWidget(_hdr_label(self.messages.get("table.header.motility_percent", "% Motile")), 1)
        hdr.addWidget(_hdr_label(self.messages.get("table.header.progressive_percent", "% Progressive")), 1)
        hdr.addWidget(_hdr_label(self.messages.get("table.header.sperm_per_ml", "Sperm/ml")), 1)
        # Delete header with fixed width to match delete buttons
        del_hdr = _hdr_label(self.messages.get("table.header.delete", "Delete"))
        del_hdr.setFixedWidth(50)
        hdr.addWidget(del_hdr, 0)  # No stretch for delete column
        row_layout.addLayout(hdr)
        sperm_widgets: List[Tuple[QLineEdit, QLineEdit, QLineEdit, QLineEdit]] = []
        # function to add a row
        def add_sperm_row(data: Optional[Dict[str, Any]] = None) -> None:
            d = data or {
                'date': datetime.now().strftime(DATE_FORMAT),
                'motility': '', 'progressive': '', 'count': ''
            }
            # layout per row
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)
            date_le = QLineEdit(d['date'])
            date_le.setPlaceholderText(DATE_FORMAT)
            
            # Add real-time date validation styling
            def validate_sperm_date():
                date_text = date_le.text().strip()
                if date_text:
                    try:
                        datetime.strptime(date_text, DATE_FORMAT)
                        date_le.setStyleSheet("")  # Valid - clear any error styling
                    except ValueError:
                        date_le.setStyleSheet("border: 2px solid red;")  # Invalid - red border
                else:
                    date_le.setStyleSheet("")  # Empty - no styling
            
            date_le.textChanged.connect(validate_sperm_date)
            
            mot_le  = QLineEdit(str(d['motility']))
            prog_le = QLineEdit(str(d['progressive']))
            cnt_le  = QLineEdit(str(d['count']))
            del_btn = QPushButton("×")
            del_btn.setFixedWidth(50)

            def rm():
                # Show confirmation dialog
                reply = self._show_message_raw(
                    self.messages.get("dialog.confirm_delete.title", "Confirm Deletion"),
                    self.messages.get("dialog.confirm_delete.message", "Do you really wish to delete this entry?"),
                    "question",
                    buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                
                # remove this row from the layout
                for i in range(row_layout.count()):
                    item = row_layout.itemAt(i)
                    if item and item.layout() is row:
                        while row.count():
                            itm = row.takeAt(0).widget()
                            if itm:
                                itm.deleteLater()
                        row_layout.takeAt(i)
                        break
                try:
                    sperm_widgets.remove((date_le, mot_le, prog_le, cnt_le))
                except ValueError:
                    pass
            del_btn.clicked.connect(rm)
            # add widgets with proper stretch factors
            row.addWidget(date_le, 1)
            row.addWidget(mot_le,   1)
            row.addWidget(prog_le,  1)
            row.addWidget(cnt_le,   1)
            row.addWidget(del_btn,  0)  # No stretch for delete button
            # Insert new rows above the add-button if present
            try:
                idx_btn = row_layout.indexOf(btn_new_sperm)  # -1 if not yet added
            except NameError:
                idx_btn = -1
            if idx_btn >= 0:
                row_layout.insertLayout(idx_btn, row)
            else:
                row_layout.addLayout(row)
            sperm_widgets.append((date_le, mot_le, prog_le, cnt_le))

        # Button to add new sperm row
        sperm_title = self.messages.get("dialog.sperm_donor.measurement_title", "Sperm measurement")
        btn_new_sperm = QPushButton(self.messages.get("button.new_item", "New {title}").format(title=sperm_title))
        btn_new_sperm.clicked.connect(lambda: add_sperm_row())

        # Populate existing sperm values for edit (sorted chronologically)
        sorted_sperm = sorted(rec.get('sperm', []), key=lambda x: x['datum'])
        for ent in sorted_sperm:
            add_sperm_row({
                'date': ent['datum'].strftime(DATE_FORMAT),
                'motility': '' if ent.get('motility')   is None else str(ent['motility']),
                'progressive': '' if ent.get('progressive') is None else str(ent['progressive']),
                'count': '' if ent.get('count')       is None else str(ent['count'])
            })
        # put the add-row button *inside* the scroll area at the bottom
        row_layout.addWidget(btn_new_sperm)
        # scroll area to contain the sperm rows
        scroll = QScrollArea()
        # remove frame so visible width matches header exactly
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setWidget(frame)
        sperm_layout.addWidget(scroll, 1)
        if steroid_active:
            tabs.addTab(sperm_tab, self.messages.get("dialog.tab.sperm_measurements", "Sperm Measurements"))

        # Weight tab
        weight_tab = QWidget()
        wlay = QVBoxLayout(weight_tab)
        # build editable weight list using the helper
        def fmt_weight(item):
            return (item['datum'].strftime(DATE_FORMAT), str(int(item['wert'])), '')
        def def_weight(ws):
            return (datetime.now().date().strftime(DATE_FORMAT), '0', '')
        
        sorted_gewicht = sorted(rec.get('gewicht', []), key=lambda x: x['datum'])
        weight_sc, weight_widgets = self._build_editable_list(
            self.messages.get("dialog.tab.weights", "Weights"), 
            sorted_gewicht, 
            fmt_weight, 
            def_weight,
            col_headers=(
                self.messages.get("table.header.date", "Date"),
                self.messages.get("table.header.weight", "Weight (g)"),
                ""  # Empty for unused sample ID column
            )
        )
        wlay.addWidget(weight_sc, 1)
        wlay.addStretch()
        tabs.addTab(weight_tab, self.messages.get("dialog.tab.weights", "Weight"))
        v.addWidget(tabs, 1)

        # --- Save button ---
        save_btn = QPushButton(self.messages.get("button.save", "Save"))
        def on_save() -> None:
            self._save_trace("sperm_donor.save.enter", editing=editing, original_name=name)
            # gather name
            new_name = name_le.text().strip()
            self._save_trace("sperm_donor.save.name_read", new_name=new_name)
            if not editing:
                if not new_name:
                    self._show_message(
                        self.messages.get("error.title", "Error"),
                        self.messages.get("error.new_animal.name_empty", "Name cannot be empty."),
                        'error'
                    )
                    return

            selected_species = self._species_from_combo(species_cb)
            birth_date = self._normalize_identity_birth_for_save(
                birth_date_le.text(), required=not editing)
            if birth_date is None:
                return
            if not editing and not self._validate_identity_species_for_save(selected_species):
                return
            if editing and not self._validate_existing_identity_for_save(
                    name, new_name, selected_species, birth_date):
                return
            if self._name_species_conflict(
                    new_name, selected_species, birth_date,
                    exclude_key=name if editing else None):
                self._show_message(
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.new_animal.name_exists", "Name already exists."),
                    'error'
                )
                return

            if editing and not self._confirm_species_change_once(species_cb, initial_species, selected_species):
                return

            _orig_name = new_name
            new_name = self._resolve_animal_key(new_name, selected_species, birth_date)
            self._save_trace(
                "sperm_donor.save.identity_resolved",
                new_name=new_name,
                selected_species=selected_species,
            )

            # parse numeric fields
            try:
                ref_w = float(ref_w_le.text())
                max_sp = int(max_sperm_le.text()) if steroid_active else int(rec.get('max_spermaproben', 100))
                recov  = int(rec_le.text()) if steroid_active else int(rec.get('recovery_time', DEFAULT_RECOVERY_TIME))
            except ValueError:
                self._show_message(
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.invalid_input", "Invalid input values."),
                    'error'
                )
                return
            # parse weight entries
            new_weights: List[Dict[str, Any]] = []
            for d_edit, w_edit, probe_edit in weight_widgets:
                try:
                    dt = datetime.strptime(d_edit.text(), DATE_FORMAT)
                    txt = w_edit.text().strip()
                    val = float(txt) if txt else None
                    new_weights.append({'datum': dt, 'wert': val})
                except Exception:
                    continue
            # parse sperm entries
            new_sperm: List[Dict[str, Any]] = []
            if steroid_active:
                for d_le, mot_le, prog_le, cnt_le in sperm_widgets:
                    try:
                        dt = datetime.strptime(d_le.text(), DATE_FORMAT)
                    except Exception:
                        continue
                    m_text = mot_le.text().strip()
                    p_text = prog_le.text().strip()
                    c_text = cnt_le.text().strip()
                    new_sperm.append({
                        'datum':       dt,
                        'motility':    float(m_text) if m_text else None,
                        'progressive': float(p_text) if p_text else None,
                        'count':       float(c_text) if c_text else None
                    })
            else:
                new_sperm = list(rec.get('sperm', []))
            # construct or update record
            if editing:
                rec_obj = dict(self.animals.get(name, {}))
            else:
                rec_obj = {}
            rec_obj['rolle'] = Role.SAMENSP.value
            rec_obj['id'] = id_le.text().strip()
            rec_obj['chip_nr'] = chip_le.text().strip()
            rec_obj['origin'] = origin_le.text().strip()
            rec_obj['project'] = project_le.currentText().strip()
            rec_obj['severity'] = severity_cb.currentData()
            rec_obj['death_date'] = death_date_le.text().strip()
            rec_obj['special_status'] = special_status_le.text().strip()
            self._apply_identity_fields_to_record(
                rec_obj, new_name, _orig_name, selected_species, birth_date)
            rec_obj['ref_weight'] = ref_w
            rec_obj['max_spermaproben'] = max_sp
            rec_obj['recovery_time'] = recov
            _was_sick_s     = bool(rec_obj.get('sick', False))
            _was_abnormal_s = bool(rec_obj.get('abnormal_current', False))
            is_sick = bool(chk_sick.isChecked())
            is_abnormal_s = bool(chk_abnormal.isChecked())
            self._update_sick_times(rec_obj, is_sick)
            self._update_abnormal_times(rec_obj, is_abnormal_s)
            self._auto_fill_status_signature(
                rec_obj, is_sick != _was_sick_s or is_abnormal_s != _was_abnormal_s)
            old_in_exp_s = rec_obj.get('in_experiment', False)
            new_in_exp_s = chk_in_exp.isChecked() if chk_in_exp is not None else old_in_exp_s
            if new_in_exp_s != old_in_exp_s:
                _perm_s = ('project.unset_in_experiment' if old_in_exp_s else 'project.set_in_experiment')
                if not self._master_can(_perm_s):
                    new_in_exp_s = old_in_exp_s
            new_in_exp_s = self._coerce_in_experiment_for_project(
                new_in_exp_s, rec_obj.get('project', ''))
            rec_obj['in_experiment'] = new_in_exp_s
            # update or set lists
            rec_obj['sperm']          = new_sperm
            rec_obj['gewicht']        = new_weights
            # Ensure only necessary keys exist (no deprecated arrays for new animals)
            rec_obj.setdefault('daten', [])
            rec_obj.setdefault('events', [])
            rec_obj.setdefault('pdg', [])
            self._save_trace(
                "sperm_donor.save.record_built",
                new_name=new_name,
                record=self._save_trace_record_summary(rec_obj),
                old_project=_old_project,
                old_severity=_old_severity,
            )

            if (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
                and heritage_parent_fields is not None
            ):
                try:
                    self._save_trace("sperm_donor.save.heritage_parent.before", new_name=new_name)
                    parent_values = self.heritage_plugin.read_parent_group(heritage_parent_fields)
                    self.heritage_plugin.save_parentage(new_name, parent_values, source="plugin")
                    # Create heritage-only placeholders for non-existing parents
                    mother = parent_values.get("egg_donor", "")
                    father = parent_values.get("sperm_donor", "")
                    species = rec_obj.get("species", "")
                    self.heritage_plugin._ensure_parent_placeholders(mother, father, species)
                    self._save_trace("sperm_donor.save.heritage_parent.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("sperm_donor.save.heritage_parent.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track parent save failed for {new_name}: {e}")

            if (
                getattr(self, 'has_cage_track_plugin', False)
                and getattr(self, 'cage_track_plugin', None)
                and cage_address_fields is not None
            ):
                try:
                    self._save_trace("sperm_donor.save.cage_address.before", new_name=new_name)
                    from Plugins.Cage__Track.ui_address_fields import extract_address_values
                    addr_values = extract_address_values(cage_address_fields)
                    self.cage_track_plugin.save_address_from_dialog(new_name, addr_values)
                    self._save_trace("sperm_donor.save.cage_address.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("sperm_donor.save.cage_address.exception", new_name=new_name, error=e)
                    logging.error(f"Cage_Track address save failed for {new_name}: {e}")

            # update animals mapping
            self._save_trace("sperm_donor.save.commit.before", new_name=new_name)
            self.animals[new_name] = rec_obj
            # if the name was changed on edit, remove old entry
            if editing and new_name != name:
                self.animals.pop(name, None)
                self._rewrite_animal_references_after_identity_change(name, new_name, _orig_name)
            self._save_trace("sperm_donor.save.commit.after", new_name=new_name, animal_count=len(self.animals))
            self._save_trace("sperm_donor.save.project_updates.schedule.before", new_name=new_name)
            self._schedule_post_animal_save_project_updates(
                new_name, _old_project, rec_obj.get('project', ''),
                _old_severity, rec_obj.get('severity', ''),
                old_in_exp_s, new_in_exp_s)
            self._save_trace("sperm_donor.save.project_updates.schedule.after", new_name=new_name)
            self._save_trace("sperm_donor.save.persistence.before", new_name=new_name)
            self._save_persistence(defer_post_save_work=True)
            self._save_trace("sperm_donor.save.persistence.after", new_name=new_name)
            # Sync to Heritage Track (including role-determined sex)
            if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
                try:
                    self._save_trace("sperm_donor.save.heritage_sync.before", new_name=new_name)
                    self.heritage_plugin.sync_from_record(new_name, rec_obj, in_main_animals=True)
                    self._save_trace("sperm_donor.save.heritage_sync.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("sperm_donor.save.heritage_sync.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track sync failed for samenspender {new_name}: {e}")
            # Force heritage visible to show newly created parent placeholders
            _heritage_fields_present = (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
                and heritage_parent_fields is not None
            )
            self._refresh_list(update_tab_visibility=True, force_heritage_visible=_heritage_fields_present)
            dlg.accept()
            # Refresh report table if Reports tab is active
            if self.reports_enabled and hasattr(self, 'report_current_animal'):
                if self.report_current_animal == new_name:
                    self._update_report_table()

        save_btn.clicked.connect(on_save)
        v.addWidget(save_btn)

        # Adjust dialog width based on tab content
        def adjust_dialog_width():
            if tabs.isVisible():
                current_widget = tabs.currentWidget()
                if current_widget:
                    # Force layout update to get accurate size
                    current_widget.updateGeometry()
                    QApplication.processEvents()

                    # Get the actual content width including all widgets
                    content_width = current_widget.sizeHint().width()
                    # Add extra padding for margins, scrollbars, and fixed-width elements
                    dialog_width = max(700, content_width + 200)
                    dlg.setMinimumWidth(dialog_width)
                    dlg.resize(dialog_width, dlg.height())

        tabs.currentChanged.connect(lambda: adjust_dialog_width())

        # finalize width so constants take effect
        self._apply_dialog_width(dlg)
        QTimer.singleShot(100, adjust_dialog_width)
        # ── Field-level permissions ───────────────────────────────────────────
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                project_le, severity_cb,
                birth_date_le, death_date_le, special_status_le,
            ],
            'core.edit_animal_housing': [_cage_addr_group, _heritage_group],
            'core.edit_animal_measurements': [weight_tab],
            'core.edit_animal_research_data': [
                sperm_tab if steroid_active else None,
                ref_w_le, max_sperm_le, rec_le,
            ],
        })
        if read_only:
            self._freeze_dialog_inputs(dlg)
        dlg.exec()

    def _dlg_offspring(self, name: Optional[str], read_only: bool = False) -> None:
        """
        Create or edit an offspring (Nachkomme).  When `name` is `None` a new
        offspring record is created; otherwise the existing offspring is
        edited.  The form captures basic demographics (name, sex,
        genotype), maximum counts for special measurements and surgeries,
        parentage information and health status.  Tabs allow editing of
        weight measurements and events (special measurements and
        operations).
        """
        editing = bool(name)
        rec: Dict[str, Any] = self.animals.get(name, {}) if editing else {}
        # Standardized dialog shell (uniform width/label alignment)
        dlg_title = self.messages.get(
            "dialog.offspring.title_edit", "Edit Offspring: {name}"
        ).format(name=self._display_name(name)) if editing else self.messages.get("dialog.offspring.title_new", "New Offspring")
        dlg, layout, form = self._new_std_dialog(dlg_title)
        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value=name or "",
            current_species=rec.get('species', ''),
            editing=editing,
            name_label_key="dialog.field.name",
        )

        # ID / Chip Nr. / Origin
        id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)

        # Project
        _old_project = rec.get('project', '')
        _old_severity = rec.get('severity', '')
        project_le = QComboBox()
        project_le.setEditable(True)
        project_le.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        for _pn in self._load_project_names():
            project_le.addItem(_pn)
        _pidx = project_le.findText(_old_project)
        if _pidx >= 0:
            project_le.setCurrentIndex(_pidx)
        elif _old_project:
            project_le.insertItem(0, _old_project)
            project_le.setCurrentIndex(0)
        else:
            project_le.lineEdit().clear()
        self._std_widen(project_le)
        if not self._master_can('project.project_assign'):
            project_le.setEnabled(False)
            project_le.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        _has_medi = getattr(self, 'has_medi_track_plugin', False)
        _sev_items = [
            ('', self.messages.get('dialog.severity.please_select', '(Please select)')),
            ('SV0', self.messages.get('severity.0',   'SV0 - no severity')),
            ('SV1', self.messages.get('severity.sv1', 'SV1 - non-recovery')),
            ('SV2', self.messages.get('severity.sv2', 'SV2 - mild or very mild')),
            ('SV3', self.messages.get('severity.sv3', 'SV3 - moderate')),
            ('SV4', self.messages.get('severity.sv4', 'SV4 - severe')),
        ]
        severity_cb = QComboBox()
        severity_cb.setToolTip(self.messages.get('dialog.severity.tooltip', 'Project severity level'))
        for _sv_d, _sv_l in _sev_items:
            severity_cb.addItem(_sv_l, _sv_d)
        _old_severity_n = 'SV0' if _old_severity == '0' else _old_severity
        _sev_idx = next((i for i, (_d, _l) in enumerate(_sev_items) if _d == _old_severity_n), 0)
        severity_cb.setCurrentIndex(_sev_idx)
        if not self._master_can('project.manage_severity'):
            severity_cb.setEnabled(False)
            severity_cb.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        self._connect_project_severity_reset(project_le, severity_cb)
        _proj_sev_w = QWidget()
        _proj_sev_l = QHBoxLayout(_proj_sev_w)
        _proj_sev_l.setContentsMargins(0, 0, 0, 0)
        _proj_sev_l.setSpacing(4)
        _proj_sev_l.addWidget(project_le, 1)
        if _has_medi:
            _proj_sev_l.addWidget(severity_cb)
        form.addRow(self.messages.get("dialog.field.project", "Project:"), _proj_sev_w)

        # Birth Date and Death Date on the same line with Age calculation
        dates_layout = QHBoxLayout()
        birth_date_le = QLineEdit(rec.get('birth_date', ''))
        birth_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        birth_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        death_date_le = QLineEdit(rec.get('death_date', ''))
        death_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        death_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        special_status_le = QLineEdit(rec.get('special_status', ''))
        if not self._master_can('core.edit_animal_core'):
            special_status_le.setReadOnly(True)
            special_status_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            special_status_le.setStyleSheet('min-width: 0;')
        age_label = QLabel(calculate_age(rec.get('birth_date', ''), rec.get('death_date', '')))
        age_label.setStyleSheet("color: gray; font-style: italic;")
        
        def update_age():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))
        
        birth_date_le.textChanged.connect(update_age)
        death_date_le.textChanged.connect(update_age)
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel("/"))
        dates_layout.addWidget(death_date_le)
        dates_layout.addWidget(age_label)
        dates_layout.addWidget(QLabel(self.messages.get("dialog.field.special_status", "Special Status:")))
        dates_layout.addWidget(special_status_le)
        form.addRow(self.messages.get("dialog.field.birth_death_date", "Birth / Death Date:"), dates_layout)
            
        # Sex
        sex_cb = QComboBox()
        # Show localized labels but store canonical values as userData
        sex_cb.addItem(self.messages.get("sex.male", "Male"), "Male")
        sex_cb.addItem(self.messages.get("sex.female", "Female"), "Female")
        sex_now = rec.get("sex", "Male")
        # Backward compatibility: some older records may have stored localized labels
        if sex_now == self.messages.get("sex.male", "Male"):
            sex_now = "Male"
        elif sex_now == self.messages.get("sex.female", "Female"):
            sex_now = "Female"
        sex_cb.setCurrentIndex(0 if sex_now == "Male" else 1)
        form.addRow(self.messages.get("dialog.offspring.sex", "Sex:"), sex_cb)

        # Genotype
        genotype_le = QLineEdit(rec.get("genotype", ""))
        form.addRow(self.messages.get("dialog.offspring.genotype", "Genotype:"), genotype_le)

        # Max special measurements
        max_special_sb = QSpinBox()
        max_special_sb.setRange(0, 1000)
        max_special_sb.setValue(rec.get("max_special", 6))
        form.addRow(
            self.messages.get("dialog.offspring.field.max_special_measurements", "Max Special Measurements:"), 
            max_special_sb
        )
        
        # Max surgeries
        max_ops_sb = QSpinBox()
        max_ops_sb.setRange(0, 1000)
        max_ops_sb.setValue(rec.get('max_op', 2))
        self._std_widen(max_ops_sb)
        form.addRow(
            self.messages.get("dialog.offspring.field.max_ops", "Max OPs:"), 
            max_ops_sb
        )
        
        _cage_addr_group = None
        cage_address_fields = None
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values
                current_addr = self.cage_track_plugin.get_current_address(name if editing else "")
                structs = self.cage_track_plugin.get_structures_for_address()
                _cage_addr_group, cage_address_fields = build_address_group(
                    self.messages, current_addr,
                    structs["buildings"], structs["rooms"], structs["cages"],
                )
                form.addRow(_cage_addr_group)
            except Exception as e:
                logging.error(f"Cage_Track address fields failed: {e}")
                cage_address_fields = None

        # Parentage
        parents_group = QGroupBox(self.messages.get("dialog.offspring.parents", "Parents"))
        parents_layout = QFormLayout(parents_group)
        
        eizell_le = QLineEdit(rec.get('eizellspenderin', '')); self._std_widen(eizell_le)
        parents_layout.addRow(
            self.messages.get("dialog.offspring.field.egg_donor", "Egg Donor:"), 
            eizell_le
        )
        
        sperm_le = QLineEdit(rec.get('samenspender', '')); self._std_widen(sperm_le)
        parents_layout.addRow(
            self.messages.get("dialog.offspring.field.sperm_donor", "Sperm Donor:"), 
            sperm_le
        )
        
        ziehmutter_le = QLineEdit(rec.get('ziehmutter', '')); self._std_widen(ziehmutter_le)
        parents_layout.addRow(
            self.messages.get("dialog.offspring.field.surrogate_mother", "Surrogate Mother:"), 
            ziehmutter_le
        )
        
        ziehvater_le = QLineEdit(rec.get('ziehvater', '')); self._std_widen(ziehvater_le)
        parents_layout.addRow(
            self.messages.get("dialog.offspring.field.surrogate_father", "Surrogate Father:"), 
            ziehvater_le
        )

        offspring_parent_fields = {
            "egg_donor": eizell_le,
            "sperm_donor": sperm_le,
            "surrogate_mother": ziehmutter_le,
            "surrogate_father": ziehvater_le,
        }
        self._add_parent_mode_selector(form, parents_group, offspring_parent_fields, default_mode="embryo")
        
        # Health status
        _health_w_o = QWidget()
        _health_hl_o = QHBoxLayout(_health_w_o)
        _health_hl_o.setContentsMargins(0, 0, 0, 0)
        _health_hl_o.setSpacing(14)
        sick_chk = QCheckBox(self.messages.get("dialog.offspring.checkbox.sick", "Sick"))
        sick_chk.setChecked(rec.get('sick', False))
        chk_abnormal_o = QCheckBox(self.messages.get("dialog.offspring.checkbox.abnormal", "Abnormal"))
        chk_abnormal_o.setChecked(rec.get('abnormal_current', False))
        _health_hl_o.addWidget(sick_chk)
        _health_hl_o.addWidget(chk_abnormal_o)
        if self._is_projects_track_active():
            chk_in_exp = QCheckBox(self.messages.get("checkbox.in_experiment", "In Experiment"))
            chk_in_exp.setChecked(bool(rec.get('in_experiment', False)))
            currently_on = rec.get('in_experiment', False)
            perm = ('project.unset_in_experiment' if currently_on else 'project.set_in_experiment')
            chk_in_exp.setEnabled(self._master_can(perm))
            chk_in_exp.setToolTip(self.messages.get('tooltip.in_experiment', 'Mark this animal as currently in experiment'))
            _health_hl_o.addWidget(chk_in_exp)
        else:
            chk_in_exp = None
        _health_hl_o.addStretch()
        form.addRow(
            self.messages.get("dialog.offspring.health_status", "Health Status:"),
            _health_w_o
        )
        self._wire_status_checkboxes(sick_chk, chk_abnormal_o, name, rec, dlg)

        layout.addLayout(form)
        
        # Tabs for weight and events
        tabs = QTabWidget()
        # Hide tabs in the create-new dialog; only show when editing
        tabs.setVisible(editing)
        steroid_active = self._is_steroid_track_active()
        
        # Weight tab
        weight_tab = QWidget()
        wlay = QVBoxLayout(weight_tab)
        
        def fmt_wg(item):
            return (item['datum'].strftime(DATE_FORMAT), str(int(item['wert'])), '')
            
        def def_wg(ws):
            return (datetime.now().date().strftime(DATE_FORMAT), '0', '')
        
        sorted_gewicht = sorted(rec.get('gewicht', []), key=lambda x: x['datum'])
        wg_sc, wg_widgets = self._build_editable_list(
            self.messages.get("dialog.offspring.tab.weights", "Weights"), 
            sorted_gewicht, 
            fmt_wg, 
            def_wg,
            col_headers=(
                self.messages.get("table.header.date", "Date"),
                self.messages.get("table.header.weight", "Weight (g)"),
                ""  # Empty for unused sample ID column
            )
        )
        
        wlay.addWidget(wg_sc, 1)
        wlay.addStretch()
        tabs.addTab(weight_tab, self.messages.get("dialog.offspring.tab.weights", "Weights"))
        
        # Events tab (special measurement or OP)
        _events_tab = None
        ev_widgets: List[Tuple[QLineEdit, QComboBox]] = []
        if steroid_active:
            events_tab = QWidget()
            _events_tab = events_tab
            elay = QVBoxLayout(events_tab)
            elay.setContentsMargins(0, 0, 0, 0)
            frame = QFrame()
            flayout = QVBoxLayout(frame)
            flayout.setContentsMargins(0, 0, 0, 0)

            # Add column headers
            header_layout = QHBoxLayout()
            header_layout.setContentsMargins(0, 0, 0, 5)
            header_layout.setSpacing(5)
            date_header = QLabel(f"<b>{self.messages.get('table.header.date', 'Date')}</b>")
            date_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            type_header = QLabel(f"<b>{self.messages.get('table.header.event_type', 'Event Type')}</b>")
            type_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_header = QLabel(f"<b>{self.messages.get('table.header.delete', 'Delete')}</b>")
            del_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_header.setFixedWidth(50)
            header_layout.addWidget(date_header, 1)  # Stretch factor 1
            header_layout.addWidget(type_header, 1)  # Stretch factor 1
            header_layout.addWidget(del_header, 0)  # No stretch
            flayout.addLayout(header_layout)

            add_ev_btn = QPushButton(self.messages.get("dialog.offspring.button.new_event", "New Event"))
            flayout.addWidget(add_ev_btn)

            # function to add event row
            def add_ev_row(data: Optional[Tuple[str, str]] = None) -> None:
                row = QHBoxLayout()
                row.setSpacing(5)
                # Default date: today
                if data:
                    default_date = data[0]
                else:
                    default_date = datetime.now().date().strftime(DATE_FORMAT)
                date_le = QLineEdit(default_date)
                date_le.setPlaceholderText(self.messages.get("form.placeholder.date", "DD.MM.YYYY"))

                # Add real-time date validation styling
                def validate_event_date():
                    date_text = date_le.text().strip()
                    if date_text:
                        try:
                            datetime.strptime(date_text, DATE_FORMAT)
                            date_le.setStyleSheet("")  # Valid - clear any error styling
                        except ValueError:
                            date_le.setStyleSheet("border: 2px solid red;")  # Invalid - red border
                    else:
                        date_le.setStyleSheet("")  # Empty - no styling

                date_le.textChanged.connect(validate_event_date)

                combo = QComboBox()
                # Store canonical event types as data for proper persistence
                combo.addItem(
                    self.messages.get("dialog.offspring.combo.special_measurement", "Special Measurement"),
                    "special_measurement"
                )
                combo.addItem(
                    self.messages.get("dialog.offspring.combo.operation", "Operation"),
                    "surgery"
                )

                if data:
                    # Map the stored values to the combo data (canonical event types)
                    stored_type = data[1].lower()
                    for i in range(combo.count()):
                        if combo.itemData(i) == stored_type:
                            combo.setCurrentIndex(i)
                            break

                del_btn = QPushButton("×")
                del_btn.setFixedWidth(50)

                def del_row() -> None:
                    # Show confirmation dialog
                    reply = self._show_message_raw(
                        self.messages.get("dialog.confirm_delete.title", "Confirm Deletion"),
                        self.messages.get("dialog.confirm_delete.message", "Do you really wish to delete this entry?"),
                        "question",
                        buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return

                    while row.count():
                        item = row.takeAt(0)
                        w = item.widget()
                        if w:
                            w.deleteLater()
                    flayout.removeItem(row)
                    try:
                        ev_widgets.remove((date_le, combo))
                    except ValueError:
                        pass

                del_btn.clicked.connect(del_row)
                row.addWidget(date_le, 1)  # Stretch factor 1
                row.addWidget(combo, 1)  # Stretch factor 1
                row.addWidget(del_btn, 0)  # No stretch
                # Insert before the add button to keep button at bottom
                idx = flayout.indexOf(add_ev_btn)
                flayout.insertLayout(idx, row)
                ev_widgets.append((date_le, combo))

            add_ev_btn.clicked.connect(lambda: add_ev_row())

            # populate existing events (sorted chronologically)
            sorted_events = sorted(rec.get('events', []), key=lambda x: x['datum'])
            for ev in sorted_events:
                add_ev_row((ev['datum'].strftime(DATE_FORMAT), ev['typ']))

            ev_sc = QScrollArea()
            ev_sc.setWidgetResizable(True)
            ev_sc.setWidget(frame)
            elay.addWidget(ev_sc, 1)
            tabs.addTab(events_tab, self.messages.get("dialog.offspring.tab.events", "Events"))
        layout.addWidget(tabs)
        
        # Save button
        save_btn2 = QPushButton(self.messages.get("dialog.offspring.button.save", "Save"))
        def on_save_offspring() -> None:
            self._save_trace("offspring.save.enter", editing=editing, original_name=name)
            # determine name
            new_name = name_le.text().strip()
            self._save_trace("offspring.save.name_read", new_name=new_name)
            if not editing:
                if not new_name:
                    QMessageBox.critical(
                        self, 
                        self.messages.get("title.error", "Error"),
                        self.messages.get("dialog.offspring.error.invalid_name", "Invalid name or name already exists.")
                    )
                    return

            selected_species = self._species_from_combo(species_cb)
            birth_date = self._normalize_identity_birth_for_save(
                birth_date_le.text(), required=not editing)
            if birth_date is None:
                return
            if not editing and not self._validate_identity_species_for_save(selected_species):
                return
            if editing and not self._validate_existing_identity_for_save(
                    name, new_name, selected_species, birth_date):
                return
            if self._name_species_conflict(
                    new_name, selected_species, birth_date,
                    exclude_key=name if editing else None):
                QMessageBox.critical(
                    self,
                    self.messages.get("title.error", "Error"),
                    self.messages.get("dialog.offspring.error.invalid_name", "Invalid name or name already exists.")
                )
                return

            if editing and not self._confirm_species_change_once(species_cb, initial_species, selected_species):
                return

            _orig_name = new_name
            new_name = self._resolve_animal_key(new_name, selected_species, birth_date)
            self._save_trace("offspring.save.identity_resolved", new_name=new_name, selected_species=selected_species)

            # collect fields
            rec_obj = dict(self.animals.get(name, {})) if editing else {}
            rec_obj['rolle']       = Role.OFFSPRING.value
            rec_obj['id']          = id_le.text().strip()
            rec_obj['chip_nr']     = chip_le.text().strip()
            rec_obj['origin']      = origin_le.text().strip()
            rec_obj['project']     = project_le.currentText().strip()
            rec_obj['severity']    = severity_cb.currentData()
            rec_obj['death_date']       = death_date_le.text().strip()
            rec_obj['special_status']   = special_status_le.text().strip()
            self._apply_identity_fields_to_record(
                rec_obj, new_name, _orig_name, selected_species, birth_date)
            rec_obj['sex']              = sex_cb.currentData() or sex_cb.currentText()
            rec_obj['genotype']         = genotype_le.text().strip()
            rec_obj['max_special'] = max_special_sb.value()
            rec_obj['max_op']      = max_ops_sb.value()
            rec_obj['eizellspenderin'] = eizell_le.text().strip()
            rec_obj['samenspender']    = sperm_le.text().strip()
            rec_obj['ziehmutter']      = ziehmutter_le.text().strip()
            rec_obj['ziehvater']       = ziehvater_le.text().strip()
            _was_sick_o     = bool(rec_obj.get('sick', False))
            _was_abnormal_o = bool(rec_obj.get('abnormal_current', False))
            is_sick = bool(sick_chk.isChecked())
            is_abnormal_o = bool(chk_abnormal_o.isChecked())
            self._update_sick_times(rec_obj, is_sick)
            self._update_abnormal_times(rec_obj, is_abnormal_o)
            self._auto_fill_status_signature(
                rec_obj, is_sick != _was_sick_o or is_abnormal_o != _was_abnormal_o)
            old_in_exp_o = rec_obj.get('in_experiment', False)
            new_in_exp_o = chk_in_exp.isChecked() if chk_in_exp is not None else old_in_exp_o
            if new_in_exp_o != old_in_exp_o:
                _perm_o = ('project.unset_in_experiment' if old_in_exp_o else 'project.set_in_experiment')
                if not self._master_can(_perm_o):
                    new_in_exp_o = old_in_exp_o
            new_in_exp_o = self._coerce_in_experiment_for_project(
                new_in_exp_o, rec_obj.get('project', ''))
            rec_obj['in_experiment'] = new_in_exp_o
            # weights
            weights_list = []
            for d_edit, w_edit, probe_edit in wg_widgets:
                try:
                    dt = datetime.strptime(d_edit.text(), DATE_FORMAT)
                    val = float(w_edit.text())
                    weights_list.append({'datum': dt, 'wert': val})
                except Exception:
                    pass
            rec_obj['gewicht'] = weights_list
            rec_obj['project'] = project_le.currentText().strip()
            rec_obj['severity'] = severity_cb.currentData()
            # events
            if steroid_active:
                events_list = []
                for date_le, combo in ev_widgets:
                    try:
                        dt = datetime.strptime(date_le.text(), DATE_FORMAT)
                        # Use currentData() to get canonical event type, not translated display text
                        typ = combo.currentData()
                        if typ:  # Ensure we have valid data
                            events_list.append({'datum': dt, 'typ': typ})
                    except Exception:
                        pass
                rec_obj['events'] = events_list
            else:
                rec_obj['events'] = rec_obj.get('events', [])
            # Ensure only necessary keys exist (no deprecated arrays for new animals)
            rec_obj.setdefault('daten', [])
            rec_obj.setdefault('pdg', [])
            self._save_trace(
                "offspring.save.record_built",
                new_name=new_name,
                record=self._save_trace_record_summary(rec_obj),
                old_project=_old_project,
                old_severity=_old_severity,
            )

            if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
                try:
                    self._save_trace("offspring.save.heritage.before", new_name=new_name)
                    # Offspring is in main animals list, so in_main_animals=True
                    self.heritage_plugin.sync_from_record(new_name, rec_obj, in_main_animals=True)
                    # Save parentage to heritage store
                    parent_values = {
                        "egg_donor": eizell_le.text().strip(),
                        "sperm_donor": sperm_le.text().strip(),
                        "surrogate_mother": ziehmutter_le.text().strip(),
                        "surrogate_father": ziehvater_le.text().strip(),
                    }
                    self.heritage_plugin.save_parentage(new_name, parent_values, source="plugin")
                    # Create heritage-only placeholders for non-existing parents
                    mother = rec_obj.get('eizellspenderin', '')
                    father = rec_obj.get('samenspender', '')
                    species = rec_obj.get('species', '')
                    self.heritage_plugin._ensure_parent_placeholders(mother, father, species)
                    self._save_trace("offspring.save.heritage.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("offspring.save.heritage.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track sync failed for offspring {new_name}: {e}")

            if (
                getattr(self, 'has_cage_track_plugin', False)
                and getattr(self, 'cage_track_plugin', None)
                and cage_address_fields is not None
            ):
                try:
                    self._save_trace("offspring.save.cage.before", new_name=new_name)
                    from Plugins.Cage__Track.ui_address_fields import extract_address_values
                    addr_vals = extract_address_values(cage_address_fields)
                    self.cage_track_plugin.save_address_from_dialog(new_name, addr_vals)
                    self._save_trace("offspring.save.cage.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("offspring.save.cage.exception", new_name=new_name, error=e)
                    logging.error(f"Cage_Track address save failed for offspring {new_name}: {e}")

            # update mapping
            self._save_trace("offspring.save.commit.before", new_name=new_name)
            self.animals[new_name] = rec_obj
            if editing and new_name != name:
                self.animals.pop(name, None)
                self._rewrite_animal_references_after_identity_change(name, new_name, _orig_name)
            self._save_trace("offspring.save.commit.after", new_name=new_name, animal_count=len(self.animals))
            self._save_trace("offspring.save.project_updates.schedule.before", new_name=new_name)
            self._schedule_post_animal_save_project_updates(
                new_name, _old_project, rec_obj.get('project', ''),
                _old_severity, rec_obj.get('severity', ''),
                old_in_exp_o, new_in_exp_o)
            self._save_trace("offspring.save.project_updates.schedule.after", new_name=new_name)
            self._save_trace("offspring.save.persistence.before", new_name=new_name)
            self._save_persistence(defer_post_save_work=True)
            self._save_trace("offspring.save.persistence.after", new_name=new_name)
            # Force heritage visible to show newly created parent placeholders
            _heritage_fields_present = (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
            )
            self._save_trace("offspring.save.refresh_list.before", new_name=new_name)
            self._refresh_list(update_tab_visibility=True, force_heritage_visible=_heritage_fields_present)
            self._save_trace("offspring.save.refresh_list.after", new_name=new_name)
            self._save_trace("offspring.save.dialog_accept.before", new_name=new_name)
            dlg.accept()
            self._save_trace("offspring.save.dialog_accept.after", new_name=new_name)
            # Refresh report table if Reports tab is active
            if self.reports_enabled and hasattr(self, 'report_current_animal'):
                if self.report_current_animal == new_name:
                    self._update_report_table()
        save_btn2.clicked.connect(on_save_offspring)
        layout.addWidget(save_btn2)

        # Adjust dialog width based on tab content
        def adjust_dialog_width():
            if tabs.isVisible():
                current_widget = tabs.currentWidget()
                if current_widget:
                    # Force layout update to get accurate size
                    current_widget.updateGeometry()
                    QApplication.processEvents()
                    
                    # Get the actual content width including all widgets
                    content_width = current_widget.sizeHint().width()
                    # Add extra padding for margins, scrollbars, and fixed-width elements
                    dialog_width = max(700, content_width + 150)
                    dlg.setMinimumWidth(dialog_width)
                    dlg.resize(dialog_width, dlg.height())
        
        tabs.currentChanged.connect(lambda: adjust_dialog_width())

        # finalize width so constants take effect
        self._apply_dialog_width(dlg)
        QTimer.singleShot(100, adjust_dialog_width)
        # ── Field-level permissions ───────────────────────────────────────────
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                project_le, severity_cb,
                birth_date_le, death_date_le, special_status_le, sex_cb,
            ],
            'core.edit_animal_housing': [_cage_addr_group, parents_group],
            'core.edit_animal_measurements': [weight_tab, _events_tab],
            'core.edit_animal_research_data': [genotype_le, max_special_sb, max_ops_sb],
        })
        if read_only:
            self._freeze_dialog_inputs(dlg)
        dlg.exec()

    def _dlg_zuchttier(self, name: Optional[str], read_only: bool = False) -> None:
        """
        Dialog for creating/editing Zuchttiere (breeding animals).
        - Create (name=None): tabs are hidden until the record exists.
        - Edit (name!=None): tabs are visible.
        Zuchttiere can be male or female, have genotype, parent fields,
        and females can track pregnancies and births.
        """
        editing = (name is not None)
        rec: Dict[str, Any] = {} if not editing else dict(self.animals.get(name, {}))
        
        # Seed defaults
        rec.setdefault('rolle', Role.ZUCHTTIER.value)
        rec.setdefault('sex', 'Female')
        rec.setdefault('genotype', '')
        rec.setdefault('ref_weight', DEFAULT_REF_WEIGHT)
        rec.setdefault('max_pregnancies', 0)
        rec.setdefault('max_geburten', 0)
        rec.setdefault('eizellspenderin', '')
        rec.setdefault('samenspender', '')
        rec.setdefault('ziehmutter', '')
        rec.setdefault('ziehvater', '')
        rec.setdefault('gewicht', [])
        rec.setdefault('events', [])
        rec.setdefault('daten', [])
        rec.setdefault('pdg', [])

        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
            try:
                parent_defaults = self.heritage_plugin.get_parentage(name if editing else None, rec)
                rec['eizellspenderin'] = parent_defaults.get('egg_donor', rec.get('eizellspenderin', ''))
                rec['samenspender'] = parent_defaults.get('sperm_donor', rec.get('samenspender', ''))
                rec['ziehmutter'] = parent_defaults.get('surrogate_mother', rec.get('ziehmutter', ''))
                rec['ziehvater'] = parent_defaults.get('surrogate_father', rec.get('ziehvater', ''))
            except Exception as e:
                logging.error(f"Heritage_Track parent preload failed for zuchttier {name}: {e}")
        
        # Create dialog
        if not editing:
            dlg_title = self.messages.get("dialog.zuchttier.title_new", "New Zuchttier")
        else:
            dlg_title = self.messages.get("dialog.zuchttier.title_edit", "Edit Zuchttier: {name}").format(name=self._display_name(name))
        dlg, layout, form = self._new_std_dialog(dlg_title)
        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value=name or "",
            current_species=rec.get('species', ''),
            editing=editing,
            name_label_key="dialog.field.name",
        )
        
        # ID / Chip Nr. / Origin
        id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)
        
        # Project
        _old_project = rec.get('project', '')
        _old_severity = rec.get('severity', '')
        project_le = QComboBox()
        project_le.setEditable(True)
        project_le.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        for _pn in self._load_project_names():
            project_le.addItem(_pn)
        _pidx = project_le.findText(_old_project)
        if _pidx >= 0:
            project_le.setCurrentIndex(_pidx)
        elif _old_project:
            project_le.insertItem(0, _old_project)
            project_le.setCurrentIndex(0)
        else:
            project_le.lineEdit().clear()
        self._std_widen(project_le)
        if not self._master_can('project.project_assign'):
            project_le.setEnabled(False)
            project_le.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        _has_medi = getattr(self, 'has_medi_track_plugin', False)
        _sev_items = [
            ('', self.messages.get('dialog.severity.please_select', '(Please select)')),
            ('SV0', self.messages.get('severity.0',   'SV0 - no severity')),
            ('SV1', self.messages.get('severity.sv1', 'SV1 - non-recovery')),
            ('SV2', self.messages.get('severity.sv2', 'SV2 - mild or very mild')),
            ('SV3', self.messages.get('severity.sv3', 'SV3 - moderate')),
            ('SV4', self.messages.get('severity.sv4', 'SV4 - severe')),
        ]
        severity_cb = QComboBox()
        severity_cb.setToolTip(self.messages.get('dialog.severity.tooltip', 'Project severity level'))
        for _sv_d, _sv_l in _sev_items:
            severity_cb.addItem(_sv_l, _sv_d)
        _old_severity_n = 'SV0' if _old_severity == '0' else _old_severity
        _sev_idx = next((i for i, (_d, _l) in enumerate(_sev_items) if _d == _old_severity_n), 0)
        severity_cb.setCurrentIndex(_sev_idx)
        if not self._master_can('project.manage_severity'):
            severity_cb.setEnabled(False)
            severity_cb.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        self._connect_project_severity_reset(project_le, severity_cb)
        _proj_sev_w = QWidget()
        _proj_sev_l = QHBoxLayout(_proj_sev_w)
        _proj_sev_l.setContentsMargins(0, 0, 0, 0)
        _proj_sev_l.setSpacing(4)
        _proj_sev_l.addWidget(project_le, 1)
        if _has_medi:
            _proj_sev_l.addWidget(severity_cb)
        form.addRow(self.messages.get("dialog.field.project", "Project:"), _proj_sev_w)
        
        # Birth Date and Death Date on the same line with Age calculation
        dates_layout = QHBoxLayout()
        birth_date_le = QLineEdit(rec.get('birth_date', ''))
        birth_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        birth_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        death_date_le = QLineEdit(rec.get('death_date', ''))
        death_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        death_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        special_status_le = QLineEdit(rec.get('special_status', ''))
        if not self._master_can('core.edit_animal_core'):
            special_status_le.setReadOnly(True)
            special_status_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            special_status_le.setStyleSheet('min-width: 0;')
        age_label = QLabel(calculate_age(rec.get('birth_date', ''), rec.get('death_date', '')))
        age_label.setStyleSheet("color: gray; font-style: italic;")
        
        def update_age():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))
        
        birth_date_le.textChanged.connect(update_age)
        death_date_le.textChanged.connect(update_age)
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel("/"))
        dates_layout.addWidget(death_date_le)
        dates_layout.addWidget(age_label)
        dates_layout.addWidget(QLabel(self.messages.get("dialog.field.special_status", "Special Status:")))
        dates_layout.addWidget(special_status_le)
        form.addRow(self.messages.get("dialog.field.birth_death_date", "Birth / Death Date:"), dates_layout)
        
        # Separation line
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator)

        _cage_addr_group = None
        cage_address_fields = None
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values
                current_addr = self.cage_track_plugin.get_current_address(name if editing else "")
                structs = self.cage_track_plugin.get_structures_for_address()
                _cage_addr_group, cage_address_fields = build_address_group(
                    self.messages, current_addr,
                    structs["buildings"], structs["rooms"], structs["cages"],
                )
                form.addRow(_cage_addr_group)
            except Exception as e:
                logging.error(f"Cage_Track address fields failed: {e}")
                cage_address_fields = None
        
        # Reference weight
        ref_w_le = QLineEdit(str(rec.get('ref_weight', DEFAULT_REF_WEIGHT)))
        ref_w_le.setValidator(QDoubleValidator(0.0, 10000.0, 2))
        self._std_widen(ref_w_le)
        form.addRow(self.messages.get("dialog.zuchttier.ref_weight", "Reference Weight (g):"), ref_w_le)
        
        # Sex (Male/Female) - REQUIRED
        sex_cb = QComboBox()
        # Show localized labels but store canonical values as userData
        sex_cb.addItem(self.messages.get("sex.male", "Male"), "Male")
        sex_cb.addItem(self.messages.get("sex.female", "Female"), "Female")
        sex_now = rec.get('sex', 'Female')
        # Backward compatibility: some older records may have stored localized labels
        if sex_now == self.messages.get("sex.male", "Male"):
            sex_now = "Male"
        elif sex_now == self.messages.get("sex.female", "Female"):
            sex_now = "Female"
        sex_cb.setCurrentIndex(0 if sex_now == "Male" else 1)
        self._std_widen(sex_cb)
        form.addRow(self.messages.get("dialog.zuchttier.sex", "Sex:"), sex_cb)
        
        # Genotype (free text)
        genotype_le = QLineEdit(rec.get('genotype', ''))
        self._std_widen(genotype_le)
        form.addRow(self.messages.get("dialog.zuchttier.genotype", "Genotype:"), genotype_le)
        
        # Verpaart mit (Mated with) - free text field
        verpaart_le = QLineEdit(rec.get('verpaart_mit', ''))
        self._std_widen(verpaart_le)
        form.addRow(self.messages.get("dialog.zuchttier.verpaart_mit", "Mated with:"), verpaart_le)
        
        # Health status checkbox
        _health_w_z = QWidget()
        _health_hl_z = QHBoxLayout(_health_w_z)
        _health_hl_z.setContentsMargins(0, 0, 0, 0)
        _health_hl_z.setSpacing(14)
        chk_plus = QCheckBox(self.messages.get('dialog.zuchttier.checkbox.sick', 'Sick'))
        chk_plus.setChecked(rec.get('sick', False))
        chk_abnormal_z = QCheckBox(self.messages.get('dialog.zuchttier.checkbox.abnormal', 'Abnormal'))
        chk_abnormal_z.setChecked(rec.get('abnormal_current', False))
        _health_hl_z.addWidget(chk_plus)
        _health_hl_z.addWidget(chk_abnormal_z)
        if self._is_projects_track_active():
            chk_in_exp = QCheckBox(self.messages.get("checkbox.in_experiment", "In Experiment"))
            chk_in_exp.setChecked(bool(rec.get('in_experiment', False)))
            currently_on = rec.get('in_experiment', False)
            perm = ('project.unset_in_experiment' if currently_on else 'project.set_in_experiment')
            chk_in_exp.setEnabled(self._master_can(perm))
            chk_in_exp.setToolTip(self.messages.get('tooltip.in_experiment', 'Mark this animal as currently in experiment'))
            _health_hl_z.addWidget(chk_in_exp)
        else:
            chk_in_exp = None
        _health_hl_z.addStretch()
        form.addRow(self.messages.get("dialog.zuchttier.health_status", "Health Status:"), _health_w_z)
        self._wire_status_checkboxes(chk_plus, chk_abnormal_z, name, rec, dlg)

        # Female-only fields: Max Pregnancies and Max Births
        lbl_maxpr = QLabel(self.messages.get("dialog.zuchttier.max_pregnancies", "Max Pregnancies:"))
        maxpr_sb = QSpinBox()
        maxpr_sb.setRange(0, 999)
        maxpr_sb.setValue(rec.get('max_pregnancies', 0))
        self._std_widen(maxpr_sb)
        form.addRow(lbl_maxpr, maxpr_sb)
        
        lbl_maxb = QLabel(self.messages.get("dialog.zuchttier.max_geburten", "Max Births:"))
        maxb_sb = QSpinBox()
        maxb_sb.setRange(0, 999)
        maxb_sb.setValue(rec.get('max_geburten', 0))
        self._std_widen(maxb_sb)
        form.addRow(lbl_maxb, maxb_sb)
        
        # Parentage fields
        parents_group = QGroupBox(self.messages.get("dialog.zuchttier.parents", "Parents"))
        parents_layout = QFormLayout(parents_group)
        
        eizell_le = QLineEdit(rec.get('eizellspenderin', ''))
        self._std_widen(eizell_le)
        parents_layout.addRow(
            self.messages.get("dialog.zuchttier.field.egg_donor", "Egg Donor:"),
            eizell_le
        )
        
        sperm_le = QLineEdit(rec.get('samenspender', ''))
        self._std_widen(sperm_le)
        parents_layout.addRow(
            self.messages.get("dialog.zuchttier.field.sperm_donor", "Sperm Donor:"),
            sperm_le
        )
        
        ziehmutter_le = QLineEdit(rec.get('ziehmutter', ''))
        self._std_widen(ziehmutter_le)
        parents_layout.addRow(
            self.messages.get("dialog.zuchttier.field.surrogate_mother", "Surrogate Mother:"),
            ziehmutter_le
        )
        
        ziehvater_le = QLineEdit(rec.get('ziehvater', ''))
        self._std_widen(ziehvater_le)
        parents_layout.addRow(
            self.messages.get("dialog.zuchttier.field.surrogate_father", "Surrogate Father:"),
            ziehvater_le
        )

        zuchttier_parent_fields = {
            "egg_donor": eizell_le,
            "sperm_donor": sperm_le,
            "surrogate_mother": ziehmutter_le,
            "surrogate_father": ziehvater_le,
        }
        self._add_parent_mode_selector(form, parents_group, zuchttier_parent_fields, default_mode="hide")
        
        layout.addLayout(form)
        
        # Tabs for weight and events (hidden while creating)
        tabs = QTabWidget()
        tabs.setVisible(editing)
        steroid_active = self._is_steroid_track_active()
        
        # Weight tab
        weight_tab = QWidget()
        wlay = QVBoxLayout(weight_tab)
        
        def fmt_wg(item):
            return (item['datum'].strftime(DATE_FORMAT), str(int(item['wert'])), '')
        
        def def_wg(ws):
            return (datetime.now().date().strftime(DATE_FORMAT), '0', '')
        
        sorted_gewicht = sorted(rec.get('gewicht', []), key=lambda x: x['datum'])
        wg_sc, wg_widgets = self._build_editable_list(
            self.messages.get("dialog.zuchttier.tab.weights", "Weights"),
            sorted_gewicht,
            fmt_wg,
            def_wg,
            col_headers=(
                self.messages.get("table.header.date", "Date"),
                self.messages.get("table.header.weight", "Weight (g)"),
                ""  # Empty for unused sample ID column
            )
        )
        
        wlay.addWidget(wg_sc, 1)
        wlay.addStretch()
        tabs.addTab(weight_tab, self.messages.get("dialog.zuchttier.tab.weights", "Weights"))
        
        # Events tab
        _events_tab = None
        ev_widgets: List[Tuple[QLineEdit, QComboBox]] = []
        if steroid_active:
            events_tab = QWidget()
            _events_tab = events_tab
            elay = QVBoxLayout(events_tab)
            elay.setContentsMargins(0, 0, 0, 0)
            frame = QFrame()
            flayout = QVBoxLayout(frame)
            flayout.setContentsMargins(0, 0, 0, 0)

            # Add column headers
            header_layout = QHBoxLayout()
            header_layout.setContentsMargins(0, 0, 0, 5)
            header_layout.setSpacing(5)
            date_header = QLabel(f"<b>{self.messages.get('table.header.date', 'Date')}</b>")
            date_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            type_header = QLabel(f"<b>{self.messages.get('table.header.event_type', 'Event Type')}</b>")
            type_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_header = QLabel(f"<b>{self.messages.get('table.header.delete', 'Delete')}</b>")
            del_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            del_header.setFixedWidth(50)
            header_layout.addWidget(date_header, 1)  # Stretch factor 1
            header_layout.addWidget(type_header, 1)  # Stretch factor 1
            header_layout.addWidget(del_header, 0)  # No stretch
            flayout.addLayout(header_layout)

            add_ev_btn = QPushButton(self.messages.get("dialog.zuchttier.button.new_event", "New Event"))
            flayout.addWidget(add_ev_btn)

            # Function to add event row
            def add_ev_row(data: Optional[Tuple[str, str]] = None) -> None:
                row = QHBoxLayout()
                row.setSpacing(5)
                # Default date: today
                if data:
                    default_date = data[0]
                else:
                    default_date = datetime.now().date().strftime(DATE_FORMAT)
                date_le = QLineEdit(default_date)
                date_le.setPlaceholderText(self.messages.get("form.placeholder.date", "DD.MM.YYYY"))

                # Add real-time date validation styling
                def validate_event_date():
                    date_text = date_le.text().strip()
                    if date_text:
                        try:
                            datetime.strptime(date_text, DATE_FORMAT)
                            date_le.setStyleSheet("")  # Valid - clear any error styling
                        except ValueError:
                            date_le.setStyleSheet("border: 2px solid red;")  # Invalid - red border
                    else:
                        date_le.setStyleSheet("")  # Empty - no styling

                date_le.textChanged.connect(validate_event_date)

                combo = QComboBox()
                # Event types for Zuchttiere (females can have pregnancy events)
                combo.addItem(
                    self.messages.get("dialog.zuchttier.event.pregnant", "Pregnant"),
                    "pregnancy"
                )
                combo.addItem(
                    self.messages.get("dialog.zuchttier.event.abort", "Abort"),
                    "abortion"
                )
                combo.addItem(
                    self.messages.get("dialog.zuchttier.event.birth", "Birth"),
                    "birth"
                )

                if data:
                    # Map the stored values to the combo data
                    stored_type = data[1].lower()
                    for i in range(combo.count()):
                        if combo.itemData(i) == stored_type:
                            combo.setCurrentIndex(i)
                            break

                del_btn = QPushButton("×")
                del_btn.setFixedWidth(50)

                def del_row() -> None:
                    # Show confirmation dialog
                    reply = self._show_message_raw(
                        self.messages.get("dialog.confirm_delete.title", "Confirm Deletion"),
                        self.messages.get("dialog.confirm_delete.message", "Do you really wish to delete this entry?"),
                        "question",
                        buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return

                    while row.count():
                        item = row.takeAt(0)
                        w = item.widget()
                        if w:
                            w.deleteLater()
                    flayout.removeItem(row)
                    try:
                        ev_widgets.remove((date_le, combo))
                    except ValueError:
                        pass

                del_btn.clicked.connect(del_row)
                row.addWidget(date_le, 1)  # Stretch factor 1
                row.addWidget(combo, 1)  # Stretch factor 1
                row.addWidget(del_btn, 0)  # No stretch
                # Insert before the add button to keep button at bottom
                idx = flayout.indexOf(add_ev_btn)
                flayout.insertLayout(idx, row)
                ev_widgets.append((date_le, combo))

            add_ev_btn.clicked.connect(lambda: add_ev_row())

            # Populate existing events (sorted chronologically)
            sorted_events = sorted(rec.get('events', []), key=lambda x: x['datum'])
            for ev in sorted_events:
                add_ev_row((ev['datum'].strftime(DATE_FORMAT), ev['typ']))

            ev_sc = QScrollArea()
            ev_sc.setWidgetResizable(True)
            ev_sc.setWidget(frame)
            elay.addWidget(ev_sc, 1)
            tabs.addTab(events_tab, self.messages.get("dialog.zuchttier.tab.events", "Events"))
        layout.addWidget(tabs)
        
        # Sex-dependent visibility for female-only fields
        def _update_sex_fields(sex_text: str) -> None:
            is_female = (sex_text == 'Female')
            lbl_maxpr.setVisible(is_female)
            maxpr_sb.setVisible(is_female)
            lbl_maxb.setVisible(is_female)
            maxb_sb.setVisible(is_female)
            # tabs are only hidden during creation; always visible in edit
            tabs.setVisible(editing)
        
        sex_cb.currentTextChanged.connect(lambda _t: _update_sex_fields(sex_cb.currentData() or "Female"))
        _update_sex_fields(sex_cb.currentData() or "Female")
        
        # Save button
        save_btn = QPushButton(self.messages.get("dialog.zuchttier.button.save", "Save"))
        
        def on_save_zuchttier() -> None:
            self._save_trace("zuchttier.save.enter", editing=editing, original_name=name)
            # Determine name
            new_name = name_le.text().strip()
            self._save_trace("zuchttier.save.name_read", new_name=new_name)
            if not editing:
                if not new_name:
                    QMessageBox.critical(
                        self,
                        self.messages.get("title.error", "Error"),
                        self.messages.get("dialog.zuchttier.error.invalid_name", "Invalid name or name already exists.")
                    )
                    return

            selected_species = self._species_from_combo(species_cb)
            birth_date = self._normalize_identity_birth_for_save(
                birth_date_le.text(), required=not editing)
            if birth_date is None:
                return
            if not editing and not self._validate_identity_species_for_save(selected_species):
                return
            if editing and not self._validate_existing_identity_for_save(
                    name, new_name, selected_species, birth_date):
                return
            if self._name_species_conflict(
                    new_name, selected_species, birth_date,
                    exclude_key=name if editing else None):
                QMessageBox.critical(
                    self,
                    self.messages.get("title.error", "Error"),
                    self.messages.get("dialog.zuchttier.error.invalid_name", "Invalid name or name already exists.")
                )
                return

            if editing and not self._confirm_species_change_once(species_cb, initial_species, selected_species):
                return

            _orig_name = new_name
            new_name = self._resolve_animal_key(new_name, selected_species, birth_date)
            self._save_trace("zuchttier.save.identity_resolved", new_name=new_name, selected_species=selected_species)
            
            # Collect fields
            rec_obj = dict(self.animals.get(name, {})) if editing else {}
            rec_obj['rolle'] = Role.ZUCHTTIER.value
            rec_obj['id'] = id_le.text().strip()
            rec_obj['chip_nr'] = chip_le.text().strip()
            rec_obj['origin'] = origin_le.text().strip()
            rec_obj['project'] = project_le.currentText().strip()
            rec_obj['severity'] = severity_cb.currentData()
            self._apply_identity_fields_to_record(
                rec_obj, new_name, _orig_name, selected_species, birth_date)
            rec_obj['ref_weight'] = float(ref_w_le.text()) if ref_w_le.text() else DEFAULT_REF_WEIGHT
            rec_obj['death_date'] = death_date_le.text().strip()
            rec_obj['special_status'] = special_status_le.text().strip()
            rec_obj['sex'] = sex_cb.currentData() or sex_cb.currentText()
            rec_obj['genotype'] = genotype_le.text().strip()
            rec_obj['verpaart_mit'] = verpaart_le.text().strip()
            _was_sick_z     = bool(rec_obj.get('sick', False))
            _was_abnormal_z = bool(rec_obj.get('abnormal_current', False))
            is_sick = bool(chk_plus.isChecked())
            is_abnormal_z = bool(chk_abnormal_z.isChecked())
            self._update_sick_times(rec_obj, is_sick)
            self._update_abnormal_times(rec_obj, is_abnormal_z)
            self._auto_fill_status_signature(
                rec_obj, is_sick != _was_sick_z or is_abnormal_z != _was_abnormal_z)
            old_in_exp_z = rec_obj.get('in_experiment', False)
            new_in_exp_z = chk_in_exp.isChecked() if chk_in_exp is not None else old_in_exp_z
            if new_in_exp_z != old_in_exp_z:
                _perm_z = ('project.unset_in_experiment' if old_in_exp_z else 'project.set_in_experiment')
                if not self._master_can(_perm_z):
                    new_in_exp_z = old_in_exp_z
            new_in_exp_z = self._coerce_in_experiment_for_project(
                new_in_exp_z, rec_obj.get('project', ''))
            rec_obj['in_experiment'] = new_in_exp_z
            rec_obj['max_pregnancies'] = maxpr_sb.value()
            rec_obj['max_geburten'] = maxb_sb.value()
            rec_obj['eizellspenderin'] = eizell_le.text().strip()
            rec_obj['samenspender'] = sperm_le.text().strip()
            rec_obj['ziehmutter'] = ziehmutter_le.text().strip()
            rec_obj['ziehvater'] = ziehvater_le.text().strip()
            
            # Weights
            weights_list = []
            for d_edit, w_edit, probe_edit in wg_widgets:
                try:
                    dt = datetime.strptime(d_edit.text(), DATE_FORMAT)
                    val = float(w_edit.text())
                    weights_list.append({'datum': dt, 'wert': val})
                except Exception:
                    pass
            rec_obj['gewicht'] = weights_list
            
            # Events
            if steroid_active:
                events_list = []
                for date_le, combo in ev_widgets:
                    try:
                        dt = datetime.strptime(date_le.text(), DATE_FORMAT)
                        typ = combo.currentData()
                        if typ:
                            events_list.append({'datum': dt, 'typ': typ})
                    except Exception:
                        pass
                rec_obj['events'] = events_list
            else:
                rec_obj['events'] = rec_obj.get('events', [])
            
            # Ensure necessary keys exist
            rec_obj.setdefault('daten', [])
            rec_obj.setdefault('pdg', [])
            self._save_trace(
                "zuchttier.save.record_built",
                new_name=new_name,
                record=self._save_trace_record_summary(rec_obj),
                old_project=_old_project,
                old_severity=_old_severity,
            )

            if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
                try:
                    self._save_trace("zuchttier.save.heritage.before", new_name=new_name)
                    # Zuchttier is in main animals list, so in_main_animals=True
                    self.heritage_plugin.sync_from_record(new_name, rec_obj, in_main_animals=True)
                    # Save parentage to heritage store
                    parent_values = {
                        "egg_donor": eizell_le.text().strip(),
                        "sperm_donor": sperm_le.text().strip(),
                        "surrogate_mother": ziehmutter_le.text().strip(),
                        "surrogate_father": ziehvater_le.text().strip(),
                    }
                    self.heritage_plugin.save_parentage(new_name, parent_values, source="plugin")
                    # Create heritage-only placeholders for non-existing parents
                    mother = rec_obj.get('eizellspenderin', '')
                    father = rec_obj.get('samenspender', '')
                    species = rec_obj.get('species', '')
                    self.heritage_plugin._ensure_parent_placeholders(mother, father, species)
                    self._save_trace("zuchttier.save.heritage.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("zuchttier.save.heritage.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track sync failed for zuchttier {new_name}: {e}")

            if (
                getattr(self, 'has_cage_track_plugin', False)
                and getattr(self, 'cage_track_plugin', None)
                and cage_address_fields is not None
            ):
                try:
                    self._save_trace("zuchttier.save.cage.before", new_name=new_name)
                    from Plugins.Cage__Track.ui_address_fields import extract_address_values
                    addr_vals = extract_address_values(cage_address_fields)
                    self.cage_track_plugin.save_address_from_dialog(new_name, addr_vals)
                    self._save_trace("zuchttier.save.cage.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("zuchttier.save.cage.exception", new_name=new_name, error=e)
                    logging.error(f"Cage_Track address save failed for zuchttier {new_name}: {e}")

            # Update mapping
            self._save_trace("zuchttier.save.commit.before", new_name=new_name)
            self.animals[new_name] = rec_obj
            if editing and new_name != name:
                self.animals.pop(name, None)
                self._rewrite_animal_references_after_identity_change(name, new_name, _orig_name)
            self._save_trace("zuchttier.save.commit.after", new_name=new_name, animal_count=len(self.animals))
            self._save_trace("zuchttier.save.project_updates.schedule.before", new_name=new_name)
            self._schedule_post_animal_save_project_updates(
                new_name, _old_project, rec_obj.get('project', ''),
                _old_severity, rec_obj.get('severity', ''),
                old_in_exp_z, new_in_exp_z)
            self._save_trace("zuchttier.save.project_updates.schedule.after", new_name=new_name)
            self._save_trace("zuchttier.save.persistence.before", new_name=new_name)
            self._save_persistence(defer_post_save_work=True)
            self._save_trace("zuchttier.save.persistence.after", new_name=new_name)
            # Force heritage visible to show newly created parent placeholders
            _heritage_fields_present = (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
            )
            self._save_trace("zuchttier.save.refresh_list.before", new_name=new_name)
            self._refresh_list(update_tab_visibility=True, force_heritage_visible=_heritage_fields_present)
            self._save_trace("zuchttier.save.refresh_list.after", new_name=new_name)
            self._save_trace("zuchttier.save.dialog_accept.before", new_name=new_name)
            dlg.accept()
            self._save_trace("zuchttier.save.dialog_accept.after", new_name=new_name)
            # Refresh report table if Reports tab is active
            if self.reports_enabled and hasattr(self, 'report_current_animal'):
                if self.report_current_animal == new_name:
                    self._update_report_table()
        
        save_btn.clicked.connect(on_save_zuchttier)
        layout.addWidget(save_btn)
        
        # Adjust dialog width based on tab content
        def adjust_dialog_width():
            if tabs.isVisible():
                current_widget = tabs.currentWidget()
                if current_widget:
                    # Force layout update to get accurate size
                    current_widget.updateGeometry()
                    QApplication.processEvents()
                    
                    # Get the actual content width including all widgets
                    content_width = current_widget.sizeHint().width()
                    # Add extra padding for margins, scrollbars, and fixed-width elements
                    dialog_width = max(700, content_width + 150)
                    dlg.setMinimumWidth(dialog_width)
                    dlg.resize(dialog_width, dlg.height())
        
        tabs.currentChanged.connect(lambda: adjust_dialog_width())
        
        # Finalize width
        self._apply_dialog_width(dlg)
        QTimer.singleShot(100, adjust_dialog_width)
        # ── Field-level permissions ───────────────────────────────────────────
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                project_le, severity_cb,
                birth_date_le, death_date_le, special_status_le, sex_cb,
            ],
            'core.edit_animal_housing': [_cage_addr_group, parents_group, verpaart_le],
            'core.edit_animal_measurements': [weight_tab, _events_tab],
            'core.edit_animal_research_data': [ref_w_le, genotype_le, maxpr_sb, maxb_sb],
        })
        if read_only:
            self._freeze_dialog_inputs(dlg)
        dlg.exec()

    def _dlg_versuchstier(self, name: Optional[str], read_only: bool = False) -> None:
        """Dialog for creating/editing Versuchstiere (experimental animals)."""
        editing = (name is not None)
        rec: Dict[str, Any] = {} if not editing else dict(self.animals.get(name, {}))
        rec.setdefault('rolle',          Role.EXPERIMENTAL.value)
        rec.setdefault('sex',            'Female')
        rec.setdefault('genotype',       '')
        rec.setdefault('ref_weight',     DEFAULT_REF_WEIGHT)
        rec.setdefault('gewicht',        [])
        rec.setdefault('events',         [])
        rec.setdefault('special_status', '')
        rec.setdefault('max_op',         0)
        rec.setdefault('max_measurements', 0)

        dlg_title = (
            self.messages.get('dialog.versuchstier.title_new', 'New Experimental Animal')
            if not editing else
            self.messages.get('dialog.versuchstier.title_edit',
                              'Edit Experimental Animal: {name}').format(name=self._display_name(name))
        )
        dlg, layout, form = self._new_std_dialog(dlg_title)

        # ── Name + Species ───────────────────────────────────────────────────
        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value=name or '',
            current_species=rec.get('species', ''),
            editing=editing,
            name_label_key='dialog.field.name',
        )

        # ── ID / Chip / Origin ───────────────────────────────────────────────
        id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)

        # ── Project + Severity ───────────────────────────────────────────────
        _old_project  = rec.get('project', '')
        _old_severity = rec.get('severity', '')
        project_le = QComboBox()
        project_le.setEditable(True)
        project_le.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        for _pn in self._load_project_names():
            project_le.addItem(_pn)
        _pidx = project_le.findText(_old_project)
        if _pidx >= 0:
            project_le.setCurrentIndex(_pidx)
        elif _old_project:
            project_le.insertItem(0, _old_project)
            project_le.setCurrentIndex(0)
        else:
            project_le.lineEdit().clear()
        self._std_widen(project_le)
        if not self._master_can('project.project_assign'):
            project_le.setEnabled(False)
            project_le.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        _has_medi = getattr(self, 'has_medi_track_plugin', False)
        _sev_items = [
            ('', self.messages.get('dialog.severity.please_select', '(Please select)')),
            ('SV0', self.messages.get('severity.0',   'SV0 - no severity')),
            ('SV1', self.messages.get('severity.sv1', 'SV1 - non-recovery')),
            ('SV2', self.messages.get('severity.sv2', 'SV2 - mild or very mild')),
            ('SV3', self.messages.get('severity.sv3', 'SV3 - moderate')),
            ('SV4', self.messages.get('severity.sv4', 'SV4 - severe')),
        ]
        severity_cb = QComboBox()
        severity_cb.setToolTip(self.messages.get('dialog.severity.tooltip', 'Project severity level'))
        for _sv_d, _sv_l in _sev_items:
            severity_cb.addItem(_sv_l, _sv_d)
        _old_severity_n = 'SV0' if _old_severity == '0' else _old_severity
        _sev_idx = next((i for i, (_d, _l) in enumerate(_sev_items) if _d == _old_severity_n), 0)
        severity_cb.setCurrentIndex(_sev_idx)
        if not self._master_can('project.manage_severity'):
            severity_cb.setEnabled(False)
            severity_cb.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        self._connect_project_severity_reset(project_le, severity_cb)
        _proj_sev_w = QWidget()
        _proj_sev_l = QHBoxLayout(_proj_sev_w)
        _proj_sev_l.setContentsMargins(0, 0, 0, 0)
        _proj_sev_l.setSpacing(4)
        _proj_sev_l.addWidget(project_le, 1)
        if _has_medi:
            _proj_sev_l.addWidget(severity_cb)
        form.addRow(self.messages.get('dialog.field.project', 'Project:'), _proj_sev_w)

        # ── Birth / Death + Age + Special Status (combined row) ──────────────
        birth_date_le = QLineEdit(rec.get('birth_date', ''))
        birth_date_le.setPlaceholderText(
            self.messages.get('form.placeholder.date_short', '(DD.MM.YYYY)'))
        birth_date_le.setStyleSheet('min-width: 0; max-width: 110px;')
        death_date_le = QLineEdit(rec.get('death_date', ''))
        death_date_le.setPlaceholderText(
            self.messages.get('form.placeholder.date_short', '(DD.MM.YYYY)'))
        death_date_le.setStyleSheet('min-width: 0; max-width: 110px;')
        special_status_le = QLineEdit(rec.get('special_status', ''))
        if not self._master_can('core.edit_animal_core'):
            special_status_le.setReadOnly(True)
            special_status_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            special_status_le.setStyleSheet('min-width: 0;')
        age_label = QLabel(calculate_age(rec.get('birth_date', ''), rec.get('death_date', '')))
        age_label.setStyleSheet('color: gray; font-style: italic;')

        def _update_age_vt():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))
        birth_date_le.textChanged.connect(_update_age_vt)
        death_date_le.textChanged.connect(_update_age_vt)

        dates_layout = QHBoxLayout()
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel('/'))
        dates_layout.addWidget(death_date_le)
        dates_layout.addWidget(age_label)
        dates_layout.addWidget(QLabel(
            self.messages.get('dialog.field.special_status', 'Special Status:')))
        dates_layout.addWidget(special_status_le)
        form.addRow(
            self.messages.get('dialog.field.birth_death_date', 'Birth / Death Date:'),
            dates_layout)

        # ── Cage address (build_address_group style) ──────────────────────────
        _cage_addr_group = None
        cage_address_fields = None
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values
                current_addr = self.cage_track_plugin.get_current_address(name if editing else '')
                structs = self.cage_track_plugin.get_structures_for_address()
                _cage_addr_group, cage_address_fields = build_address_group(
                    self.messages, current_addr,
                    structs['buildings'], structs['rooms'], structs['cages'],
                )
                form.addRow(_cage_addr_group)
            except Exception as e:
                logging.warning(f'Cage_Track address fields failed: {e}')

        # ── Sex ──────────────────────────────────────────────────────────────
        sex_cb = QComboBox()
        sex_cb.addItem(self.messages.get('sex.male',    'Male'),    'Male')
        sex_cb.addItem(self.messages.get('sex.female',  'Female'),  'Female')
        sex_cb.addItem(self.messages.get('sex.unknown', 'Unknown'), 'Unknown')
        _sx = rec.get('sex', 'Female')
        _sx_idx = next((i for i in range(sex_cb.count()) if sex_cb.itemData(i) == _sx), 1)
        sex_cb.setCurrentIndex(_sx_idx)
        form.addRow(self.messages.get('dialog.offspring.sex', 'Sex:'), sex_cb)

        # ── Genotype ─────────────────────────────────────────────────────────
        genotype_le = QLineEdit(rec.get('genotype', ''))
        self._std_widen(genotype_le)
        form.addRow(self.messages.get('dialog.offspring.genotype', 'Genotype:'), genotype_le)

        # ── Ref. weight ──────────────────────────────────────────────────────
        ref_w_le = QLineEdit(str(rec.get('ref_weight', DEFAULT_REF_WEIGHT)))
        form.addRow(self.messages.get('dialog.field.reference_weight', 'Reference weight (g):'), ref_w_le)

        # ── Max Surgeries + Max Measurements ─────────────────────────────────
        max_op_sb = QSpinBox()
        max_op_sb.setRange(0, 1000)
        max_op_sb.setValue(rec.get('max_op', 0))
        self._std_widen(max_op_sb)
        form.addRow(
            self.messages.get('dialog.versuchstier.field.max_op', 'Max Surgeries:'),
            max_op_sb)

        max_meas_sb = QSpinBox()
        max_meas_sb.setRange(0, 1000)
        max_meas_sb.setValue(rec.get('max_measurements', 0))
        self._std_widen(max_meas_sb)
        form.addRow(
            self.messages.get('dialog.versuchstier.field.max_measurements', 'Max Measurements:'),
            max_meas_sb)

        # ── Parents (collapsible, natural/embryo toggle) ──────────────────────
        parents_group = QGroupBox(self.messages.get('dialog.offspring.parents', 'Parents'))
        parents_layout = QFormLayout(parents_group)
        eizell_le = QLineEdit(rec.get('eizellspenderin', ''))
        self._std_widen(eizell_le)
        parents_layout.addRow(
            self.messages.get('dialog.offspring.field.egg_donor', 'Egg Donor:'), eizell_le)
        sperm_le = QLineEdit(rec.get('samenspender', ''))
        self._std_widen(sperm_le)
        parents_layout.addRow(
            self.messages.get('dialog.offspring.field.sperm_donor', 'Sperm Donor:'), sperm_le)
        ziehmutter_le = QLineEdit(rec.get('ziehmutter', ''))
        self._std_widen(ziehmutter_le)
        parents_layout.addRow(
            self.messages.get('dialog.offspring.field.surrogate_mother', 'Surrogate Mother:'), ziehmutter_le)
        ziehvater_le = QLineEdit(rec.get('ziehvater', ''))
        self._std_widen(ziehvater_le)
        parents_layout.addRow(
            self.messages.get('dialog.offspring.field.surrogate_father', 'Surrogate Father:'), ziehvater_le)
        vt_parent_fields = {
            'egg_donor':       eizell_le,
            'sperm_donor':     sperm_le,
            'surrogate_mother': ziehmutter_le,
            'surrogate_father': ziehvater_le,
        }
        self._add_parent_mode_selector(form, parents_group, vt_parent_fields, default_mode='hide')

        # ── Health status checkboxes ──────────────────────────────────────────
        _health_w_vt = QWidget()
        _health_hl_vt = QHBoxLayout(_health_w_vt)
        _health_hl_vt.setContentsMargins(0, 0, 0, 0)
        _health_hl_vt.setSpacing(14)
        sick_chk_vt = QCheckBox(self.messages.get('dialog.offspring.checkbox.sick', 'Sick'))
        sick_chk_vt.setChecked(rec.get('sick', False))
        chk_abnormal_vt = QCheckBox(self.messages.get('dialog.offspring.checkbox.abnormal', 'Abnormal'))
        chk_abnormal_vt.setChecked(rec.get('abnormal_current', False))
        _health_hl_vt.addWidget(sick_chk_vt)
        _health_hl_vt.addWidget(chk_abnormal_vt)
        if self._is_projects_track_active():
            chk_in_exp_vt = QCheckBox(self.messages.get('checkbox.in_experiment', 'In Experiment'))
            chk_in_exp_vt.setChecked(bool(rec.get('in_experiment', False)))
            _curr_on_vt = rec.get('in_experiment', False)
            _perm_vt = ('project.unset_in_experiment' if _curr_on_vt else 'project.set_in_experiment')
            chk_in_exp_vt.setEnabled(self._master_can(_perm_vt))
            chk_in_exp_vt.setToolTip(
                self.messages.get('tooltip.in_experiment', 'Mark this animal as currently in experiment'))
            _health_hl_vt.addWidget(chk_in_exp_vt)
        else:
            chk_in_exp_vt = None
        _health_hl_vt.addStretch()
        form.addRow(
            self.messages.get('dialog.offspring.health_status', 'Health Status:'),
            _health_w_vt)
        self._wire_status_checkboxes(sick_chk_vt, chk_abnormal_vt, name, rec, dlg)

        layout.addLayout(form)

        # ── Tabs: Weights + Events ─────────────────────────────────────────────
        tabs = QTabWidget()
        tabs.setVisible(editing)

        # Weight tab
        weight_tab = QWidget()
        wlay = QVBoxLayout(weight_tab)
        sorted_gewicht = sorted(rec.get('gewicht', []), key=lambda x: x['datum'])
        wg_sc, wg_widgets = self._build_editable_list(
            self.messages.get('dialog.versuchstier.tab.weights', 'Weights'),
            sorted_gewicht,
            lambda item: (item['datum'].strftime(DATE_FORMAT), str(int(item['wert'])), ''),
            lambda ws:   (datetime.now().date().strftime(DATE_FORMAT), '0', ''),
            col_headers=(
                self.messages.get('table.header.date',   'Date'),
                self.messages.get('table.header.weight', 'Weight (g)'),
                '',
            )
        )
        wlay.addWidget(wg_sc, 1)
        wlay.addStretch()
        tabs.addTab(weight_tab,
                    self.messages.get('dialog.versuchstier.tab.weights', 'Weights'))

        # Events tab (Surgery / Measurement – always shown, not gated on steroid_active)
        events_tab = QWidget()
        elay = QVBoxLayout(events_tab)
        elay.setContentsMargins(0, 0, 0, 0)
        ev_frame = QFrame()
        ev_flayout = QVBoxLayout(ev_frame)
        ev_flayout.setContentsMargins(0, 0, 0, 0)

        hdr_ev = QHBoxLayout()
        hdr_ev.setContentsMargins(0, 0, 0, 5)
        hdr_ev.setSpacing(5)
        _dh = QLabel(f"<b>{self.messages.get('table.header.date', 'Date')}</b>")
        _dh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _th = QLabel(f"<b>{self.messages.get('table.header.event_type', 'Event Type')}</b>")
        _th.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _xh = QLabel(f"<b>{self.messages.get('table.header.delete', 'Delete')}</b>")
        _xh.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _xh.setFixedWidth(50)
        hdr_ev.addWidget(_dh, 1)
        hdr_ev.addWidget(_th, 1)
        hdr_ev.addWidget(_xh, 0)
        ev_flayout.addLayout(hdr_ev)

        add_ev_btn_vt = QPushButton(
            self.messages.get('dialog.versuchstier.button.new_event', 'New Event'))
        ev_flayout.addWidget(add_ev_btn_vt)

        ev_widgets: List[Tuple[QLineEdit, QComboBox]] = []

        def add_ev_row_vt(data: Optional[Tuple[str, str]] = None) -> None:
            row = QHBoxLayout()
            row.setSpacing(5)
            default_date = data[0] if data else datetime.now().date().strftime(DATE_FORMAT)
            ev_date_le = QLineEdit(default_date)
            ev_date_le.setPlaceholderText(
                self.messages.get('form.placeholder.date', 'DD.MM.YYYY'))

            def _val_ev_date():
                txt = ev_date_le.text().strip()
                if txt:
                    try:
                        datetime.strptime(txt, DATE_FORMAT)
                        ev_date_le.setStyleSheet('')
                    except ValueError:
                        ev_date_le.setStyleSheet('border: 2px solid red;')
                else:
                    ev_date_le.setStyleSheet('')
            ev_date_le.textChanged.connect(_val_ev_date)

            ev_combo = QComboBox()
            ev_combo.addItem(
                self.messages.get('dialog.versuchstier.event.surgery',     'Surgery'),
                'surgery')
            ev_combo.addItem(
                self.messages.get('dialog.versuchstier.event.measurement', 'Measurement'),
                'measurement')
            if data:
                stored = data[1].lower()
                for i in range(ev_combo.count()):
                    if ev_combo.itemData(i) == stored:
                        ev_combo.setCurrentIndex(i)
                        break

            del_btn_ev = QPushButton('×')
            del_btn_ev.setFixedWidth(50)

            def _del_ev_row() -> None:
                reply = self._show_message_raw(
                    self.messages.get('dialog.confirm_delete.title',   'Confirm Deletion'),
                    self.messages.get('dialog.confirm_delete.message', 'Do you really wish to delete this entry?'),
                    'question',
                    buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return
                while row.count():
                    item = row.takeAt(0)
                    w = item.widget()
                    if w:
                        w.deleteLater()
                ev_flayout.removeItem(row)
                try:
                    ev_widgets.remove((ev_date_le, ev_combo))
                except ValueError:
                    pass

            del_btn_ev.clicked.connect(_del_ev_row)
            row.addWidget(ev_date_le, 1)
            row.addWidget(ev_combo,   1)
            row.addWidget(del_btn_ev, 0)
            idx = ev_flayout.indexOf(add_ev_btn_vt)
            ev_flayout.insertLayout(idx, row)
            ev_widgets.append((ev_date_le, ev_combo))

        add_ev_btn_vt.clicked.connect(lambda: add_ev_row_vt())

        sorted_events = sorted(rec.get('events', []), key=lambda x: x['datum'])
        for ev in sorted_events:
            add_ev_row_vt((ev['datum'].strftime(DATE_FORMAT), ev['typ']))

        ev_sc = QScrollArea()
        ev_sc.setWidgetResizable(True)
        ev_sc.setWidget(ev_frame)
        elay.addWidget(ev_sc, 1)
        tabs.addTab(events_tab,
                    self.messages.get('dialog.versuchstier.tab.events', 'Events'))

        layout.addWidget(tabs)

        # ── Save button ───────────────────────────────────────────────────────
        save_btn = QPushButton(self.messages.get('button.save', 'Save'))
        save_btn.setEnabled(self._master_can('core.create_animals') if not editing
                            else self._master_can('core.edit_animal_core'))

        def on_save_versuchstier():
            self._save_trace("versuchstier.save.enter", editing=editing, original_name=name)
            new_name = name_le.text().strip()
            self._save_trace("versuchstier.save.name_read", new_name=new_name)
            if not new_name:
                self._show_message_raw(
                    self.messages.get('error.title', 'Error'),
                    self.messages.get('error.name_required', 'Name is required.'))
                return
            selected_species = self._species_from_combo(species_cb)
            birth_date = self._normalize_identity_birth_for_save(
                birth_date_le.text(), required=not editing)
            if birth_date is None:
                return
            if not editing and not self._validate_identity_species_for_save(selected_species):
                return
            if editing and not self._validate_existing_identity_for_save(
                    name, new_name, selected_species, birth_date):
                return
            if self._name_species_conflict(
                    new_name, selected_species, birth_date,
                    exclude_key=name if editing else None):
                self._show_message_raw(
                    self.messages.get('error.title', 'Error'),
                    self.messages.get('error.name_exists', 'An animal with this name already exists.'))
                return
            if editing and not self._confirm_species_change_once(
                    species_cb, initial_species, selected_species):
                return
            _orig_name = new_name
            new_name = self._resolve_animal_key(new_name, selected_species, birth_date)
            self._save_trace("versuchstier.save.identity_resolved", new_name=new_name, selected_species=selected_species)
            try:
                ref_w = float(ref_w_le.text()) if ref_w_le.text() else DEFAULT_REF_WEIGHT
            except ValueError:
                ref_w = DEFAULT_REF_WEIGHT

            weights_list = []
            for d_edit, w_edit, _probe in wg_widgets:
                try:
                    dt  = datetime.strptime(d_edit.text(), DATE_FORMAT)
                    val = float(w_edit.text())
                    weights_list.append({'datum': dt, 'wert': val})
                except Exception:
                    pass

            events_list = []
            for ev_d, ev_c in ev_widgets:
                try:
                    dt  = datetime.strptime(ev_d.text(), DATE_FORMAT)
                    typ = ev_c.currentData()
                    if typ:
                        events_list.append({'datum': dt, 'typ': typ})
                except Exception:
                    pass

            rec_obj = dict(self.animals.get(name, {})) if editing else {}
            rec_obj['rolle']            = Role.EXPERIMENTAL.value
            rec_obj['id']               = id_le.text().strip()
            rec_obj['chip_nr']          = chip_le.text().strip()
            rec_obj['origin']           = origin_le.text().strip()
            rec_obj['project']          = project_le.currentText().strip()
            rec_obj['severity']         = severity_cb.currentData()
            self._apply_identity_fields_to_record(
                rec_obj, new_name, _orig_name, selected_species, birth_date)
            rec_obj['sex']              = sex_cb.currentData() or sex_cb.currentText()
            rec_obj['genotype']         = genotype_le.text().strip()
            rec_obj['special_status']   = special_status_le.text().strip()
            rec_obj['ref_weight']       = ref_w
            rec_obj['death_date']       = death_date_le.text().strip()
            rec_obj['max_op']           = max_op_sb.value()
            rec_obj['max_measurements'] = max_meas_sb.value()
            rec_obj['eizellspenderin']  = eizell_le.text().strip()
            rec_obj['samenspender']     = sperm_le.text().strip()
            rec_obj['ziehmutter']       = ziehmutter_le.text().strip()
            rec_obj['ziehvater']        = ziehvater_le.text().strip()
            rec_obj['gewicht']          = weights_list
            rec_obj['events']           = events_list
            rec_obj.setdefault('daten', [])
            rec_obj.setdefault('pdg',   [])

            _was_sick_vt     = bool(rec_obj.get('sick', False))
            _was_abnormal_vt = bool(rec_obj.get('abnormal_current', False))
            is_sick_vt       = bool(sick_chk_vt.isChecked())
            is_abnormal_vt   = bool(chk_abnormal_vt.isChecked())
            self._update_sick_times(rec_obj, is_sick_vt)
            self._update_abnormal_times(rec_obj, is_abnormal_vt)
            self._auto_fill_status_signature(
                rec_obj,
                is_sick_vt != _was_sick_vt or is_abnormal_vt != _was_abnormal_vt)
            old_in_exp_vt = rec_obj.get('in_experiment', False)
            new_in_exp_vt = (chk_in_exp_vt.isChecked()
                             if chk_in_exp_vt is not None else old_in_exp_vt)
            if new_in_exp_vt != old_in_exp_vt:
                _p = ('project.unset_in_experiment' if old_in_exp_vt
                      else 'project.set_in_experiment')
                if not self._master_can(_p):
                    new_in_exp_vt = old_in_exp_vt
            new_in_exp_vt = self._coerce_in_experiment_for_project(
                new_in_exp_vt, rec_obj.get('project', ''))
            rec_obj['in_experiment'] = new_in_exp_vt
            self._save_trace(
                "versuchstier.save.record_built",
                new_name=new_name,
                record=self._save_trace_record_summary(rec_obj),
                old_project=_old_project,
                old_severity=_old_severity,
            )

            if cage_address_fields is not None:
                try:
                    self._save_trace("versuchstier.save.cage.before", new_name=new_name)
                    from Plugins.Cage__Track.ui_address_fields import extract_address_values
                    addr_vals = extract_address_values(cage_address_fields)
                    self.cage_track_plugin.save_address_from_dialog(new_name, addr_vals)
                    self._save_trace("versuchstier.save.cage.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("versuchstier.save.cage.exception", new_name=new_name, error=e)
                    logging.error(f'Cage_Track address save failed for {new_name}: {e}')

            self._save_trace("versuchstier.save.commit.before", new_name=new_name)
            self.animals[new_name] = rec_obj
            if editing and new_name != name:
                self.animals.pop(name, None)
                self._rewrite_animal_references_after_identity_change(name, new_name, _orig_name)
            self._save_trace("versuchstier.save.commit.after", new_name=new_name, animal_count=len(self.animals))
            self._save_trace("versuchstier.save.project_updates.schedule.before", new_name=new_name)
            self._schedule_post_animal_save_project_updates(
                new_name, _old_project, rec_obj.get('project', ''),
                _old_severity, rec_obj.get('severity', ''),
                old_in_exp_vt, new_in_exp_vt)
            self._save_trace("versuchstier.save.project_updates.schedule.after", new_name=new_name)
            self._save_trace("versuchstier.save.persistence.before", new_name=new_name)
            self._save_persistence(defer_post_save_work=True)
            self._save_trace("versuchstier.save.persistence.after", new_name=new_name)
            # Sync to Heritage Track (including sex from dialog)
            if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
                try:
                    self._save_trace("versuchstier.save.heritage.before", new_name=new_name)
                    self.heritage_plugin.sync_from_record(new_name, rec_obj, in_main_animals=True)
                    # Save parentage to heritage store
                    parent_values = {
                        "egg_donor": eizell_le.text().strip(),
                        "sperm_donor": sperm_le.text().strip(),
                        "surrogate_mother": ziehmutter_le.text().strip(),
                        "surrogate_father": ziehvater_le.text().strip(),
                    }
                    self.heritage_plugin.save_parentage(new_name, parent_values, source="plugin")
                    # Create heritage-only placeholders for non-existing parents
                    mother = rec_obj.get('eizellspenderin', '')
                    father = rec_obj.get('samenspender', '')
                    species = rec_obj.get('species', '')
                    self.heritage_plugin._ensure_parent_placeholders(mother, father, species)
                    self._save_trace("versuchstier.save.heritage.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("versuchstier.save.heritage.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track sync failed for versuchstier {new_name}: {e}")
            # Force heritage visible to show newly created parent placeholders
            _heritage_fields_present = (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
            )
            self._save_trace("versuchstier.save.refresh_list.before", new_name=new_name)
            self._refresh_list(update_tab_visibility=True, force_heritage_visible=_heritage_fields_present)
            self._save_trace("versuchstier.save.refresh_list.after", new_name=new_name)
            self._save_trace("versuchstier.save.dialog_accept.before", new_name=new_name)
            dlg.accept()
            self._save_trace("versuchstier.save.dialog_accept.after", new_name=new_name)

        save_btn.clicked.connect(on_save_versuchstier)
        layout.addWidget(save_btn)
        self._apply_dialog_width(dlg)
        # ── Field-level permissions ───────────────────────────────────────────
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                project_le, severity_cb,
                birth_date_le, death_date_le, special_status_le,
            ],
            'core.edit_animal_housing': [_cage_addr_group, sex_cb, parents_group],
            'core.edit_animal_measurements': [weight_tab, events_tab],
            'core.edit_animal_research_data': [genotype_le, ref_w_le, max_op_sb, max_meas_sb],
        })
        if read_only:
            self._freeze_dialog_inputs(dlg)
        dlg.exec()

    def _dlg_basic_animal_role(
        self,
        name: Optional[str],
        role_value: Optional[str] = None,
        read_only: bool = False,
    ) -> None:
        creating = name is None
        if creating and not self._master_can('core.create_animals'):
            self._show_permission_denied()
            return
        if not creating and not (self._master_can('core.edit_animal_core') or self._master_can('core.open_readonly_dialogs')):
            self._show_permission_denied()
            return

        rec: Dict[str, Any] = {} if creating else dict(self.animals.get(name, {}))
        role_value = role_value or rec.get("rolle") or Role.UNKNOWN.value
        rec.setdefault("rolle", role_value)
        rec.setdefault("daten", [])
        rec.setdefault("pdg", [])
        rec.setdefault("gewicht", [])
        rec.setdefault("events", [])
        rec.setdefault("sperm", [])
        rec.setdefault("ref_weight", DEFAULT_REF_WEIGHT)
        rec.setdefault("sick", False)
        rec.setdefault("abnormal_current", False)
        rec.setdefault("in_experiment", False)
        dialog_mode = "new" if creating else "edit"
        enabled_blocks = set(self._role_dialog_blocks(role_value, dialog_mode))

        role_label = self._role_label_with_icon(role_value)
        title = (
            self.messages.get("dialog.basic_role.title_new", "New animal: {role}").format(role=role_label)
            if creating else
            self.messages.get("dialog.basic_role.title_edit", "Edit animal: {name}").format(name=self._display_name(name))
        )
        dlg, v, form = self._new_std_dialog(title)

        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value="" if creating else (name or ""),
            current_species=rec.get("species", ""),
            editing=not creating,
            name_label_key="dialog.field.name",
        )
        if "id_chip_origin" in enabled_blocks:
            id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)
        else:
            id_le = QLineEdit(str(rec.get("id", "")))
            chip_le = QLineEdit(str(rec.get("chip_nr", "")))
            origin_le = QLineEdit(str(rec.get("origin", "")))

        project_cb = None
        current_project = rec.get("project", "")
        if "project_severity" in enabled_blocks:
            project_cb = QComboBox()
            project_cb.setEditable(True)
            project_cb.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
            for project_name in self._load_project_names():
                project_cb.addItem(project_name)
            project_idx = project_cb.findText(current_project)
            if project_idx >= 0:
                project_cb.setCurrentIndex(project_idx)
            elif current_project:
                project_cb.insertItem(0, current_project)
                project_cb.setCurrentIndex(0)
            elif project_cb.lineEdit():
                project_cb.lineEdit().clear()
            self._std_widen(project_cb)
            if not self._master_can('project.project_assign'):
                project_cb.setEnabled(False)
            form.addRow(self.messages.get("dialog.field.project", "Project:"), project_cb)

        dates_layout = QHBoxLayout()
        birth_date_le = QLineEdit(rec.get("birth_date", ""))
        birth_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        birth_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        death_date_le = QLineEdit(rec.get("death_date", ""))
        death_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        death_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        special_status_le = QLineEdit(rec.get("special_status", ""))
        special_status_le.setStyleSheet("min-width: 0;")
        age_label = QLabel(calculate_age(rec.get("birth_date", ""), rec.get("death_date", "")))
        age_label.setStyleSheet("color: gray; font-style: italic;")

        def update_age():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))

        birth_date_le.textChanged.connect(update_age)
        death_date_le.textChanged.connect(update_age)
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel("/"))
        if "lifecycle" in enabled_blocks:
            dates_layout.addWidget(death_date_le)
            dates_layout.addWidget(age_label)
            dates_layout.addWidget(QLabel(self.messages.get("dialog.field.special_status", "Special Status:")))
            dates_layout.addWidget(special_status_le)
        else:
            dates_layout.addWidget(age_label)
        form.addRow(self.messages.get("dialog.field.birth_death_date", "Birth / Death Date:"), dates_layout)

        sex_cb = QComboBox()
        sex_cb.addItem(self.messages.get("sex.unknown", "Unknown"), "Unknown")
        sex_cb.addItem(self.messages.get("sex.male", "Male"), "Male")
        sex_cb.addItem(self.messages.get("sex.female", "Female"), "Female")
        current_sex = str(rec.get("sex", "Unknown"))
        sex_idx = sex_cb.findData(current_sex)
        sex_cb.setCurrentIndex(sex_idx if sex_idx >= 0 else 0)
        self._std_widen(sex_cb)
        form.addRow(self.messages.get("dialog.offspring.sex", "Sex:"), sex_cb)

        genotype_le = QLineEdit(str(rec.get("genotype", "")))
        form.addRow(self.messages.get("dialog.offspring.genotype", "Genotype:"), genotype_le)

        cage_address_fields = None
        extract_address_values = None
        _cage_addr_group = None
        if "cage_address" in enabled_blocks and getattr(self, 'has_cage_track_plugin', False):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values as _extract_address_values
                extract_address_values = _extract_address_values
                _cage_addr_group, cage_address_fields = build_address_group(
                    self, self.cage_track_plugin, name if not creating else None
                )
                form.addRow(_cage_addr_group)
            except Exception as exc:
                logging.warning(f"Could not build basic-role cage address block: {exc}")

        parent_fields: Dict[str, QLineEdit] = {}
        parents_group = QGroupBox(self.messages.get("dialog.offspring.parents", "Parents"))
        parents_layout = QFormLayout(parents_group)
        parent_specs = [
            ("eizellspenderin", "dialog.offspring.field.egg_donor", "Egg Donor:"),
            ("samenspender", "dialog.offspring.field.sperm_donor", "Sperm Donor:"),
            ("ziehmutter", "dialog.offspring.field.surrogate_mother", "Surrogate Mother:"),
            ("ziehvater", "dialog.offspring.field.surrogate_father", "Surrogate Father:"),
        ]
        for field_name, label_key, default_label in parent_specs:
            parent_fields[field_name] = QLineEdit(str(rec.get(field_name, "")))
            parents_layout.addRow(self.messages.get(label_key, default_label), parent_fields[field_name])
        self._add_parent_mode_selector(form, parents_group, parent_fields, default_mode="hide")

        ref_w_le = None
        if "reference_weight" in enabled_blocks:
            ref_w_le = QLineEdit(str(rec.get("ref_weight", "")))
            ref_w_le.setValidator(QDoubleValidator(0.0, 100000.0, 3, ref_w_le))
            form.addRow(self.messages.get("dialog.field.reference_weight", "Reference weight (g):"), ref_w_le)

        new_weight_le = None
        if "weight" in enabled_blocks:
            new_weight_le = QLineEdit("")
            new_weight_le.setValidator(QDoubleValidator(0.0, 100000.0, 3, new_weight_le))
            weight_label = (
                self.messages.get("dialog.field.initial_weight", "Initial weight (g):")
                if creating else
                self.messages.get("dialog.field.new_weight", "New weight (g):")
            )
            form.addRow(weight_label, new_weight_le)

        sick_chk = None
        abnormal_chk = None
        in_exp_chk = None
        if "health_flags" in enabled_blocks:
            health_w = QWidget()
            health_l = QHBoxLayout(health_w)
            health_l.setContentsMargins(0, 0, 0, 0)
            sick_chk = QCheckBox(self.messages.get("dialog.offspring.checkbox.sick", "Sick"))
            abnormal_chk = QCheckBox(self.messages.get("dialog.offspring.checkbox.abnormal", "Abnormal"))
            in_exp_chk = QCheckBox(self.messages.get("checkbox.in_experiment", "In Experiment"))
            sick_chk.setChecked(bool(rec.get("sick", False)))
            abnormal_chk.setChecked(bool(rec.get("abnormal_current", False)))
            in_exp_chk.setChecked(bool(rec.get("in_experiment", False)))
            in_exp_chk.setVisible(self._is_projects_track_active())
            for widget in (sick_chk, abnormal_chk, in_exp_chk):
                health_l.addWidget(widget)
            health_l.addStretch()
            form.addRow(self.messages.get("dialog.offspring.health_status", "Health Status:"), health_w)

        v.addLayout(form)
        save_btn = QPushButton(self.messages.get("button.save", "Save"))
        save_btn.setEnabled(not read_only and (
            self._master_can('core.create_animals') if creating else self._master_can('core.edit_animal_core')
        ))
        v.addWidget(save_btn)
        self._apply_dialog_width(dlg)

        if read_only:
            self._freeze_dialog_inputs(dlg)

        def on_save_basic_role():
            base_name = name_le.text().strip()
            if not base_name:
                self._show_message_raw(
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.name_required", "Name is required."),
                    "error",
                )
                return
            selected_species = self._species_from_combo(species_cb)
            if not self._validate_identity_species_for_save(selected_species):
                return
            birth_date = self._normalize_identity_birth_for_save(birth_date_le.text(), required=True)
            if birth_date is None:
                return
            death_date = self._normalize_identity_birth_for_save(death_date_le.text(), required=False)
            if death_date is None:
                return
            if not creating and not self._validate_existing_identity_for_save(
                name, base_name, selected_species, birth_date
            ):
                return
            if self._name_species_conflict(
                base_name,
                selected_species,
                birth_date,
                exclude_key=name if not creating else None,
            ):
                self._show_message_raw(
                    self.messages.get("error.title", "Error"),
                    self.messages.get("error.name_exists", "An animal with this name already exists."),
                    "error",
                )
                return
            if not creating and not self._confirm_species_change_once(
                species_cb, initial_species, selected_species
            ):
                return

            try:
                new_key = self._resolve_animal_key(base_name, selected_species, birth_date)
            except ValueError as exc:
                self._show_message_raw(self.messages.get("error.title", "Error"), str(exc), "error")
                return

            if ref_w_le is not None:
                try:
                    ref_weight = float(ref_w_le.text()) if ref_w_le.text().strip() else DEFAULT_REF_WEIGHT
                except ValueError:
                    ref_weight = DEFAULT_REF_WEIGHT
            else:
                ref_weight = rec.get("ref_weight", DEFAULT_REF_WEIGHT)

            rec_obj = dict(rec)
            rec_obj.update({
                "rolle": role_value,
                "id": id_le.text().strip(),
                "chip_nr": chip_le.text().strip(),
                "origin": origin_le.text().strip(),
                "project": project_cb.currentText().strip() if project_cb is not None else current_project,
                "birth_date": birth_date,
                "death_date": death_date if "lifecycle" in enabled_blocks else rec.get("death_date", ""),
                "special_status": special_status_le.text().strip() if "lifecycle" in enabled_blocks else rec.get("special_status", ""),
                "sex": sex_cb.currentData() or "Unknown",
                "genotype": genotype_le.text().strip(),
                "ref_weight": ref_weight,
                "sick": sick_chk.isChecked() if sick_chk is not None else bool(rec.get("sick", False)),
                "abnormal_current": abnormal_chk.isChecked() if abnormal_chk is not None else bool(rec.get("abnormal_current", False)),
                "in_experiment": (
                    in_exp_chk.isChecked()
                    if in_exp_chk is not None and in_exp_chk.isVisible()
                    else bool(rec.get("in_experiment", False))
                ),
                "species": selected_species,
                "ipid": new_key,
                "name": base_name,
                "_base_name": base_name,
                "display_name": base_name,
            })
            for field_name, widget in parent_fields.items():
                rec_obj[field_name] = widget.text().strip()

            if new_weight_le is not None and new_weight_le.text().strip():
                try:
                    weight_value = float(new_weight_le.text().strip())
                except ValueError:
                    weight_value = None
                if weight_value is not None:
                    weights = list(rec_obj.get("gewicht", []))
                    weights.append({"datum": datetime.now(), "wert": weight_value})
                    rec_obj["gewicht"] = weights

            if not creating and new_key != name:
                self.animals.pop(name, None)
            self.animals[new_key] = rec_obj
            self._write_json({"animals": self.animals, "archived": self.archived})
            if cage_address_fields and extract_address_values and getattr(self, 'cage_track_plugin', None) is not None:
                try:
                    self.cage_track_plugin.save_address_from_dialog(
                        new_key,
                        extract_address_values(cage_address_fields),
                    )
                except Exception as exc:
                    logging.warning(f"Could not save basic-role cage address block: {exc}")
            if getattr(self, 'heritage_plugin', None) is not None:
                try:
                    self.heritage_plugin.save_parentage(
                        new_key,
                        {field: widget.text().strip() for field, widget in parent_fields.items()},
                        source="plugin",
                    )
                except Exception as exc:
                    logging.warning(f"Could not save basic-role parentage block: {exc}")
            self.selected_animals = [new_key]
            self._refresh_list(update_tab_visibility=True)
            self._on_select()
            dlg.accept()

        save_btn.clicked.connect(on_save_basic_role)
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                birth_date_le, death_date_le, special_status_le, sex_cb, genotype_le,
            ],
            'core.edit_animal_housing': [_cage_addr_group, parents_group],
            'core.edit_animal_measurements': [new_weight_le],
            'core.edit_animal_research_data': [ref_w_le],
        })
        dlg.exec()

    def _dlg_female_animal(
        self,
        name: Optional[str],
        read_only: bool = False,
        default_role: Optional[str] = None,
    ) -> None:
        """
        Full editor for Spenderin/Amme.
        - Create (name=None): tabs are hidden until the record exists.
        - Edit (name!=None): tabs are visible.
        """
        creating = (name is None)
        rec: Dict[str, Any] = {} if creating else dict(self.animals.get(name, {}))
        # Normalize/seed fields
        role_now = rec.get('rolle', default_role or Role.SPENDER.value)
        if creating and default_role in (Role.SPENDER.value, Role.AMME.value):
            role_now = default_role
        if not creating and role_now not in (Role.SPENDER.value, Role.AMME.value):
            role_now = Role.SPENDER.value
        rec.setdefault('rolle', role_now)
        rec.setdefault('ref_weight', DEFAULT_REF_WEIGHT)
        rec.setdefault('max_messungen', DEFAULT_MAX_MESS)
        rec.setdefault('max_pgf', DEFAULT_MAX_PGF)
        rec.setdefault('max_embryo', rec.get('max_embryo', 0))
        rec.setdefault('max_op',     rec.get('max_op', 0))
        rec.setdefault('max_fsh',    rec.get('max_fsh', 0))
        rec.setdefault('max_pregnancies', rec.get('max_pregnancies', 0))
        rec.setdefault('max_geburten',    rec.get('max_geburten', 0))
        rec.setdefault('recovery_time', rec.get('recovery_time', DEFAULT_RECOVERY_TIME))
        rec.setdefault('daten', [])
        rec.setdefault('pdg', [])
        rec.setdefault('gewicht', [])
        rec.setdefault('events', [])
        # Note: pgf and op arrays are deprecated and maintained only for backward compatibility
        # They are NOT created for new animals but preserved for existing ones

        # Standardized shell
        if creating:
            dlg_title = self.messages.get("dialog.female_animal.title_new", "New Female Animal")
        else:
            dlg_title = self.messages.get("dialog.female_animal.title_edit", "Edit Female Animal: {name}").format(name=self._display_name(name))
        dlg, v, form = self._new_std_dialog(dlg_title)
        name_le, species_cb, initial_species = self._build_name_species_inputs(
            form,
            name_value="" if creating else (name or ""),
            current_species=rec.get('species', ''),
            editing=not creating,
            name_label_key="dialog.field.name",
        )

        # ID / Chip Nr. / Origin
        id_le, chip_le, origin_le = self._build_id_chip_origin_row(form, rec)

        # Project
        _old_project = rec.get('project', '')
        _old_severity = rec.get('severity', '')
        project_le = QComboBox()
        project_le.setEditable(True)
        project_le.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        for _pn in self._load_project_names():
            project_le.addItem(_pn)
        _pidx = project_le.findText(_old_project)
        if _pidx >= 0:
            project_le.setCurrentIndex(_pidx)
        elif _old_project:
            project_le.insertItem(0, _old_project)
            project_le.setCurrentIndex(0)
        else:
            project_le.lineEdit().clear()
        self._std_widen(project_le)
        if not self._master_can('project.project_assign'):
            project_le.setEnabled(False)
            project_le.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        _has_medi = getattr(self, 'has_medi_track_plugin', False)
        _sev_items = [
            ('', self.messages.get('dialog.severity.please_select', '(Please select)')),
            ('SV0', self.messages.get('severity.0',   'SV0 - no severity')),
            ('SV1', self.messages.get('severity.sv1', 'SV1 - non-recovery')),
            ('SV2', self.messages.get('severity.sv2', 'SV2 - mild or very mild')),
            ('SV3', self.messages.get('severity.sv3', 'SV3 - moderate')),
            ('SV4', self.messages.get('severity.sv4', 'SV4 - severe')),
        ]
        severity_cb = QComboBox()
        severity_cb.setToolTip(self.messages.get('dialog.severity.tooltip', 'Project severity level'))
        for _sv_d, _sv_l in _sev_items:
            severity_cb.addItem(_sv_l, _sv_d)
        _old_severity_n = 'SV0' if _old_severity == '0' else _old_severity
        _sev_idx = next((i for i, (_d, _l) in enumerate(_sev_items) if _d == _old_severity_n), 0)
        severity_cb.setCurrentIndex(_sev_idx)
        if not self._master_can('project.manage_severity'):
            severity_cb.setEnabled(False)
            severity_cb.setStyleSheet('QComboBox { background: #f0f0f0; color: #666; }')
        self._connect_project_severity_reset(project_le, severity_cb)
        _proj_sev_w = QWidget()
        _proj_sev_l = QHBoxLayout(_proj_sev_w)
        _proj_sev_l.setContentsMargins(0, 0, 0, 0)
        _proj_sev_l.setSpacing(4)
        _proj_sev_l.addWidget(project_le, 1)
        if _has_medi:
            _proj_sev_l.addWidget(severity_cb)
        form.addRow(self.messages.get("dialog.field.project", "Project:"), _proj_sev_w)
        logging.info(f"Female dialog: Created project_le widget with initial value: '{rec.get('project', '')}'")

        # Birth Date and Death Date on the same line with Age calculation
        dates_layout = QHBoxLayout()
        birth_date_le = QLineEdit(rec.get('birth_date', ''))
        birth_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        birth_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        death_date_le = QLineEdit(rec.get('death_date', ''))
        death_date_le.setPlaceholderText(self.messages.get("form.placeholder.date_short", "(DD.MM.YYYY)"))
        death_date_le.setStyleSheet("min-width: 0; max-width: 110px;")
        special_status_le = QLineEdit(rec.get('special_status', ''))
        if not self._master_can('core.edit_animal_core'):
            special_status_le.setReadOnly(True)
            special_status_le.setStyleSheet('min-width: 0; background: #f0f0f0; color: #666;')
        else:
            special_status_le.setStyleSheet('min-width: 0;')
        age_label = QLabel(calculate_age(rec.get('birth_date', ''), rec.get('death_date', '')))
        age_label.setStyleSheet("color: gray; font-style: italic;")
        
        def update_age():
            age_label.setText(calculate_age(birth_date_le.text(), death_date_le.text()))
        
        birth_date_le.textChanged.connect(update_age)
        death_date_le.textChanged.connect(update_age)
        dates_layout.addWidget(birth_date_le)
        dates_layout.addWidget(QLabel("/"))
        dates_layout.addWidget(death_date_le)
        dates_layout.addWidget(age_label)
        dates_layout.addWidget(QLabel(self.messages.get("dialog.field.special_status", "Special Status:")))
        dates_layout.addWidget(special_status_le)
        form.addRow(self.messages.get("dialog.field.birth_death_date", "Birth / Death Date:"), dates_layout)

        # Role: only the two valid female roles
        role_cb = QComboBox()
        # Show localized labels but store the internal role code as userData
        female_roles = [Role.SPENDER.value, Role.AMME.value]
        current_index = 0
        for idx, role_code in enumerate(female_roles):
            label = self._get_localized_role(role_code)
            role_cb.addItem(label, role_code)
            if role_now == role_code:
                current_index = idx
        role_cb.setCurrentIndex(current_index)
        self._std_widen(role_cb)
        form.addRow(self.messages.get("dialog.female_animal.role", "Role:"), role_cb)

        # First separation line
        separator1 = QFrame()
        separator1.setFrameShape(QFrame.Shape.HLine)
        separator1.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator1)

        _cage_addr_group = None
        cage_address_fields = None
        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                from Plugins.Cage__Track.ui_address_fields import build_address_group, extract_address_values
                current_addr = self.cage_track_plugin.get_current_address(name if not creating else "")
                structs = self.cage_track_plugin.get_structures_for_address()
                _cage_addr_group, cage_address_fields = build_address_group(
                    self.messages, current_addr,
                    structs["buildings"], structs["rooms"], structs["cages"],
                )
                form.addRow(_cage_addr_group)
            except Exception as e:
                logging.error(f"Cage_Track address fields failed: {e}")
                cage_address_fields = None

        # Reference weight
        ref_w_le = QLineEdit(str(rec.get('ref_weight', DEFAULT_REF_WEIGHT)))
        ref_w_le.setValidator(QDoubleValidator(0.0, 10000.0, 2))
        self._std_widen(ref_w_le)
        form.addRow(self.messages.get("dialog.female_animal.ref_weight", "Reference Weight (g):"), ref_w_le)

        # Separator after reference weight
        separator2 = QFrame()
        separator2.setFrameShape(QFrame.Shape.HLine)
        separator2.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator2)

        steroid_active = self._is_steroid_track_active()

        # Create all role-specific widgets (will be shown/hidden based on role)
        # Spenderin-only fields
        lbl_maxop = QLabel(self.messages.get("dialog.female_animal.max_op", "Max Surgeries:"))
        maxop_le = QLineEdit(str(rec.get('max_op', 0)))
        maxop_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxop_le)

        # Amme-only fields
        lbl_maxe = QLabel(self.messages.get("dialog.female_animal.max_embryo", "Max Embryo Transfers:"))
        maxe_le = QLineEdit(str(rec.get('max_embryo', 0)))
        maxe_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxe_le)
        
        # Recovery time (both)
        lbl_rec = QLabel(self.messages.get('dialog.female_animal.recovery_time', 'Recovery Time (days):'))
        rec_le = QLineEdit(str(rec.get('recovery_time', DEFAULT_RECOVERY_TIME)))
        rec_le.setValidator(QIntValidator(1, 365))
        self._std_widen(rec_le)

        # Amme-only fields continued
        lbl_maxpr = QLabel(self.messages.get("dialog.female_animal.max_pregnancies", "Max Pregnancies:"))
        maxpr_le = QLineEdit(str(rec.get('max_pregnancies', 0)))
        maxpr_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxpr_le)
        
        lbl_maxb = QLabel(self.messages.get("dialog.female_animal.max_births", "Max Births:"))
        maxb_le = QLineEdit(str(rec.get('max_geburten', 0)))
        maxb_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxb_le)

        # Store row positions for dynamic reordering
        row_maxop = form.rowCount()
        form.addRow(lbl_maxop, maxop_le)
        row_maxe = form.rowCount()
        form.addRow(lbl_maxe, maxe_le)
        row_rec = form.rowCount()
        form.addRow(lbl_rec, rec_le)
        row_maxpr = form.rowCount()
        form.addRow(lbl_maxpr, maxpr_le)
        row_maxb = form.rowCount()
        form.addRow(lbl_maxb, maxb_le)

        # Second separation line
        separator_limits = QFrame()
        separator_limits.setFrameShape(QFrame.Shape.HLine)
        separator_limits.setFrameShadow(QFrame.Shadow.Sunken)
        form.addRow(separator_limits)

        # Max Blood Samples (renamed from Max Measurements)
        lbl_maxm = QLabel(self.messages.get("dialog.field.max_blood_samples", "Max Blood Samples:"))
        maxm_le = QLineEdit(str(rec.get('max_messungen', DEFAULT_MAX_MESS)))
        maxm_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxm_le)
        form.addRow(lbl_maxm, maxm_le)

        # Max PGF
        lbl_maxp = QLabel(self.messages.get("dialog.field.max_pgf", "Max PGF:"))
        maxp_le = QLineEdit(str(rec.get('max_pgf', DEFAULT_MAX_PGF)))
        maxp_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxp_le)
        form.addRow(lbl_maxp, maxp_le)

        # Max FSH (Spenderin only)
        lbl_maxfsh = QLabel(self.messages.get("dialog.female_animal.max_fsh", "Max FSH:"))
        maxfsh_le = QLineEdit(str(rec.get('max_fsh', 0)))
        maxfsh_le.setValidator(QIntValidator(0, 9999))
        self._std_widen(maxfsh_le)
        form.addRow(lbl_maxfsh, maxfsh_le)
        
        # Health Status
        _health_w_f = QWidget()
        _health_hl_f = QHBoxLayout(_health_w_f)
        _health_hl_f.setContentsMargins(0, 0, 0, 0)
        _health_hl_f.setSpacing(14)
        chk_plus = QCheckBox(self.messages.get('dialog.female_animal.checkbox.sick', 'Sick'))
        chk_plus.setChecked(rec.get('sick', False))
        chk_abnormal_f = QCheckBox(self.messages.get('dialog.female_animal.checkbox.abnormal', 'Abnormal'))
        chk_abnormal_f.setChecked(rec.get('abnormal_current', False))
        _health_hl_f.addWidget(chk_plus)
        _health_hl_f.addWidget(chk_abnormal_f)
        if self._is_projects_track_active():
            chk_in_exp = QCheckBox(self.messages.get("checkbox.in_experiment", "In Experiment"))
            chk_in_exp.setChecked(bool(rec.get('in_experiment', False)))
            currently_on = rec.get('in_experiment', False)
            perm = ('project.unset_in_experiment' if currently_on else 'project.set_in_experiment')
            chk_in_exp.setEnabled(self._master_can(perm))
            chk_in_exp.setToolTip(self.messages.get('tooltip.in_experiment', 'Mark this animal as currently in experiment'))
            _health_hl_f.addWidget(chk_in_exp)
        else:
            chk_in_exp = None
        _health_hl_f.addStretch()
        form.addRow(self.messages.get('dialog.female_animal.health_status', 'Health Status:'), _health_w_f)
        self._wire_status_checkboxes(chk_plus, chk_abnormal_f, name, rec, dlg)

        _heritage_group = None
        heritage_parent_fields = None
        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
            _heritage_group, heritage_parent_fields = self.heritage_plugin.create_parent_group(name if not creating else None, rec)
            for parent_widget in heritage_parent_fields.values():
                self._std_widen(parent_widget)
            self._add_parent_mode_selector(form, _heritage_group, heritage_parent_fields, default_mode="hide")

        v.addLayout(form)

        # Tabs (hidden while creating)
        tabs = QTabWidget()
        tabs.setVisible(not creating)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # PdG plugin hook for female animal dialog
        _pdg_tabs = None
        if steroid_active and self.has_pdg_plugin and hasattr(self, 'pdg_cap') and self.pdg_cap and hasattr(self.pdg_cap, 'hooks'):
            _pdg_tabs = self.pdg_cap.hooks.on_female_dialog_tabs(tabs, rec, not creating, self, name)
        
        v.addWidget(tabs, 1)

        # Role-dependent visibility
        # Spenderin: Ref weight, Max Surgeries, Recovery time, sep, Max Blood Samples, Max PGF, Max FSH
        # Amme: Ref weight, Max Embryo Transfers, Recovery time, Max Pregnancies, Max Births, sep, Max Blood Samples, Max PGF
        def _update_role_fields(_ignored: str = "") -> None:
            # Always derive the role from the combobox userData so this
            # remains correct when labels are localized.
            role_code = role_cb.currentData() or Role.SPENDER.value
            is_amme = (role_code == Role.AMME.value)

            def _set_row_visible(anchor_widget: QWidget, visible: bool) -> None:
                try:
                    form.setRowVisible(anchor_widget, visible)
                except Exception:
                    anchor_widget.setVisible(visible)

            if steroid_active:
                # Spenderin only
                _set_row_visible(lbl_maxop, not is_amme)
                maxop_le.setVisible(not is_amme)
                _set_row_visible(lbl_maxfsh, not is_amme)
                maxfsh_le.setVisible(not is_amme)
                # Amme only
                _set_row_visible(lbl_maxe, is_amme)
                maxe_le.setVisible(is_amme)
                _set_row_visible(lbl_maxpr, is_amme)
                maxpr_le.setVisible(is_amme)
                _set_row_visible(lbl_maxb, is_amme)
                maxb_le.setVisible(is_amme)

                _set_row_visible(separator_limits, True)
                _set_row_visible(lbl_maxm, True)
                maxm_le.setVisible(True)
                _set_row_visible(lbl_maxp, True)
                maxp_le.setVisible(True)
            else:
                # Hide all steroid-related max-value inputs while Steroid_track is inactive.
                _set_row_visible(lbl_maxop, False)
                maxop_le.setVisible(False)
                _set_row_visible(lbl_maxfsh, False)
                maxfsh_le.setVisible(False)
                _set_row_visible(lbl_maxe, False)
                maxe_le.setVisible(False)
                _set_row_visible(lbl_maxpr, False)
                maxpr_le.setVisible(False)
                _set_row_visible(lbl_maxb, False)
                maxb_le.setVisible(False)
                _set_row_visible(separator_limits, False)
                _set_row_visible(lbl_maxm, False)
                maxm_le.setVisible(False)
                _set_row_visible(lbl_maxp, False)
                maxp_le.setVisible(False)

            # Recovery time is steroid-related: hide when Steroid_track is inactive.
            _set_row_visible(lbl_rec, steroid_active)
            rec_le.setVisible(steroid_active)
            # tabs are only hidden during creation; always visible in edit
            tabs.setVisible(not creating)

        role_cb.currentTextChanged.connect(_update_role_fields)
        _update_role_fields()

        # ---------------- Progesterone tab ----------------
        _prog_tab = None
        dp_w = []
        if steroid_active:
            def fmt_dp(item): return (item['datum'].strftime(DATE_FORMAT), str(item['wert']), item.get('probennummer', ''))
            def def_dp(widgets):
                return (datetime.now().date().strftime(DATE_FORMAT), '0', '')

            sorted_daten = sorted(rec.get('daten', []), key=lambda x: x['datum'])
            dp_sc, dp_w = self._build_editable_list(
                self.messages.get("dialog.female_animal.tab.measurements", "Progesterone Values"), 
                sorted_daten, 
                fmt_dp, 
                def_dp,
                col_headers=(
                    self.messages.get("table.header.date", "Date"),
                    self.messages.get("table.header.progesterone", "Progesterone (ng/ml)"),
                    self.messages.get("table.header.sample_id", "Sample ID")
                )
            )
            prog_tab = QWidget()
            _prog_tab = prog_tab
            prog_lay = QVBoxLayout(prog_tab)
            prog_lay.addWidget(dp_sc, 1)
            prog_lay.addStretch()
            tabs.addTab(prog_tab, self.messages.get("dialog.tab.progesterone", "Progesterone"))

        # ---------------- PdG and Unified Prog tabs ----------------
        # These are added by the PdG plugin via extend_animal_dialog() hook
        # when has_pdg_plugin is True

        # ---------------- Weight tab ----------------
        def fmt_gewicht(item):
            return (item['datum'].strftime(DATE_FORMAT), str(int(item['wert'])), '')
        
        def def_gewicht(widgets):
            return (datetime.now().date().strftime(DATE_FORMAT), '0', '')
        
        sorted_gewicht = sorted(rec.get('gewicht', []), key=lambda x: x['datum'])
        gew_sc, gew_w = self._build_editable_list(
            self.messages.get("dialog.female_animal.tab.weights", "Weights"), 
            sorted_gewicht, 
            fmt_gewicht, 
            def_gewicht,
            col_headers=(
                self.messages.get("table.header.date", "Date"),
                self.messages.get("table.header.weight", "Weight (g)"),
                ""  # Empty for unused sample ID column
            )
        )
        gewicht_tab = QWidget()
        gewicht_lay = QVBoxLayout(gewicht_tab)
        gewicht_lay.addWidget(gew_sc, 1)
        gewicht_lay.addStretch()
        tabs.addTab(gewicht_tab, self.messages.get("dialog.tab.weights", "Weight"))

        # ---------------- Events tab ----------------
        _events_tab = None
        events_w = []
        if steroid_active:
            if rec.get('rolle') == Role.SPENDER.value:
                ev_items = (
                    [{'typ': LEGACY_EVENT_MAP.get('op', 'surgery'),  'datum': dt} for dt in rec.get('op', [])] +
                    [{'typ': 'pgf', 'datum': dt} for dt in rec.get('pgf', [])] +
                    [{'typ': LEGACY_EVENT_MAP.get(ev.get('typ'), ev.get('typ')), 'datum': ev.get('datum')} 
                     for ev in rec.get('events', []) if ev.get('typ') in ('fsh', 'progesterone')]
                )
            else:
                # Normalize legacy event types to English
                ev_items = [{'typ': LEGACY_EVENT_MAP.get(ev.get('typ'), ev.get('typ')), 'datum': ev.get('datum')} 
                           for ev in rec.get('events', []) if LEGACY_EVENT_MAP.get(ev.get('typ'), ev.get('typ')) in EVENT_TYPES]
                ev_items += [{'typ': 'pgf', 'datum': dt} for dt in rec.get('pgf', [])]

            def fmt_ev(ev): 
                return (ev['datum'].strftime(DATE_FORMAT), ev['typ'])

            def def_ev(widgets):
                # Use the underlying role code to decide the default event
                role_code = role_cb.currentData() or Role.SPENDER.value
                default_event = 'surgery' if role_code == Role.SPENDER.value else 'embryo_transfer'
                return (datetime.now().date().strftime(DATE_FORMAT), default_event)

            sorted_events = sorted(ev_items, key=lambda x: x['datum'])
            events_sc, events_w = self._build_repro_event_list(
                self.messages.get("dialog.female_animal.tab.events", "Events"), 
                sorted_events, 
                fmt_ev, 
                def_ev,
                role_cb
            )

            events_tab = QWidget()
            _events_tab = events_tab
            events_lay = QVBoxLayout(events_tab)
            events_lay.addWidget(events_sc, 1)
            events_lay.addStretch()
            tabs.addTab(events_tab, self.messages.get("dialog.tab.events", "Events"))

        # ---------------- Save (single standard button) ----------------
        save_btn = QPushButton(self.messages.get("button.save", "Save"))
        def on_save() -> None:
            self._save_trace(
                "female_like.save.enter",
                editing=not creating,
                creating=creating,
                original_name=name,
            )
            new_name = name_le.text().strip()
            self._save_trace("female_like.save.name_read", new_name=new_name)
            if not new_name:
                self._show_message(
                    self.messages.get('error.title', 'Error'), 
                    self.messages.get('error.name_required', 'Name cannot be empty.'), 
                    'error'
                )
                return

            selected_species = self._species_from_combo(species_cb)
            birth_date = self._normalize_identity_birth_for_save(
                birth_date_le.text(), required=creating)
            if birth_date is None:
                return
            if creating and not self._validate_identity_species_for_save(selected_species):
                return
            if not creating and not self._validate_existing_identity_for_save(
                    name, new_name, selected_species, birth_date):
                return
            if self._name_species_conflict(
                    new_name, selected_species, birth_date,
                    exclude_key=None if creating else name):
                self._show_message(
                    self.messages.get('error.title', 'Error'),
                    self.messages.get('error.name_exists', 'Name already exists.'),
                    'error'
                )
                return

            if not creating and not self._confirm_species_change_once(species_cb, initial_species, selected_species):
                return

            _orig_name = new_name
            new_name = self._resolve_animal_key(new_name, selected_species, birth_date)
            self._save_trace(
                "female_like.save.identity_resolved",
                new_name=new_name,
                selected_species=selected_species,
            )

            # Get PdG widgets from plugin-created tab if available
            pdg_w = []
            if steroid_active and self.has_pdg_plugin:
                # Find the PdG tab and get its widgets
                for i in range(tabs.count()):
                    tab_widget = tabs.widget(i)
                    if hasattr(tab_widget, '_pdg_widgets'):
                        pdg_w = tab_widget._pdg_widgets
                        break

            # Read the internal role code from the combobox userData so
            # storage and logic stay independent of the localized label.
            role_code = role_cb.currentData() or Role.SPENDER.value
            self._save_trace("female_like.save.role_read", new_name=new_name, role_code=role_code)
            # role change cleanups
            if not creating and rec.get('rolle') != role_code:
                if role_code == Role.AMME.value and rec.get('op'):
                    self._show_message(
                        self.messages.get('warning.title', 'Warning'),
                        self.messages.get('warning.role_change_surrogate', 'Switching to Surrogate role will remove surgery events.'),
                        'warning'
                    )
                    rec['op'] = []
                if role_code == Role.SPENDER.value:
                    # remove surrogate-only events
                    rec['events'] = [ev for ev in rec.get('events', []) if ev.get('typ') != 'embryoübertragung']

            # Scalars
            try:
                self._apply_identity_fields_to_record(
                    rec, new_name, _orig_name, selected_species, birth_date)
                rec['rolle'] = role_code
                id_text = id_le.text().strip()
                project_text = project_le.currentText().strip()
                logging.info(f"Saving animal: id='{id_text}', project='{project_text}'")
                rec['id'] = id_text
                rec['chip_nr'] = chip_le.text().strip()
                rec['origin'] = origin_le.text().strip()
                rec['project'] = project_text
                rec['severity'] = severity_cb.currentData()
                rec['death_date'] = death_date_le.text().strip()
                rec['special_status'] = special_status_le.text().strip()
                rec['ref_weight'] = float(ref_w_le.text() or DEFAULT_REF_WEIGHT)
                if steroid_active:
                    rec['max_messungen']   = int(maxm_le.text() or DEFAULT_MAX_MESS)
                    rec['max_pgf']         = int(maxp_le.text() or DEFAULT_MAX_PGF)
                    rec['max_embryo']      = int(maxe_le.text() or 0) if role_code == Role.AMME.value else 0
                    rec['max_pregnancies'] = int(maxpr_le.text() or 0) if role_code == Role.AMME.value else 0
                    rec['max_geburten']    = int(maxb_le.text() or 0)  if role_code == Role.AMME.value else 0
                    rec['max_op']          = int(maxop_le.text() or 0) if role_code == Role.SPENDER.value else 0
                    rec['max_fsh']         = int(maxfsh_le.text() or 0) if role_code == Role.SPENDER.value else 0
                else:
                    rec['max_messungen']   = rec.get('max_messungen', DEFAULT_MAX_MESS)
                    rec['max_pgf']         = rec.get('max_pgf', DEFAULT_MAX_PGF)
                    rec['max_embryo']      = rec.get('max_embryo', 0)
                    rec['max_pregnancies'] = rec.get('max_pregnancies', 0)
                    rec['max_geburten']    = rec.get('max_geburten', 0)
                    rec['max_op']          = rec.get('max_op', 0)
                    rec['max_fsh']         = rec.get('max_fsh', 0)
                if role_code in (Role.SPENDER.value, Role.AMME.value):
                    if steroid_active:
                        rec['recovery_time'] = int(rec_le.text() or DEFAULT_RECOVERY_TIME)
                    else:
                        rec['recovery_time'] = int(rec.get('recovery_time', DEFAULT_RECOVERY_TIME))
                else:
                    rec['recovery_time'] = 0
                _was_sick_f     = bool(rec.get('sick', False))
                _was_abnormal_f = bool(rec.get('abnormal_current', False))
                is_sick = bool(chk_plus.isChecked())
                is_abnormal_f = bool(chk_abnormal_f.isChecked())
                self._update_sick_times(rec, is_sick)
                self._update_abnormal_times(rec, is_abnormal_f)
                self._auto_fill_status_signature(
                    rec, is_sick != _was_sick_f or is_abnormal_f != _was_abnormal_f)
                old_in_exp_f = rec.get('in_experiment', False)
                new_in_exp_f = chk_in_exp.isChecked() if chk_in_exp is not None else old_in_exp_f
                if new_in_exp_f != old_in_exp_f:
                    _perm_f = ('project.unset_in_experiment' if old_in_exp_f else 'project.set_in_experiment')
                    if not self._master_can(_perm_f):
                        new_in_exp_f = old_in_exp_f
                new_in_exp_f = self._coerce_in_experiment_for_project(
                    new_in_exp_f, rec.get('project', ''))
                rec['in_experiment'] = new_in_exp_f
                self._save_trace(
                    "female_like.save.scalars_written",
                    new_name=new_name,
                    record=self._save_trace_record_summary(rec),
                    old_project=_old_project,
                    old_severity=_old_severity,
                    new_in_experiment=new_in_exp_f,
                )
            except ValueError:
                self._save_trace("female_like.save.scalars.value_error", new_name=new_name)
                self._show_message(
                    self.messages.get('error.title', 'Error'),
                    self.messages.get('error.invalid_numbers', 'Invalid numbers.'),
                    'error'
                )
                return

            # Sample_Track: snapshot existing measurement dates before mutation
            _st_old_daten = {e['datum'] for e in rec.get('daten', [])}
            _st_old_pdg   = {e['datum'] for e in rec.get('pdg', [])}
            self._save_trace(
                "female_like.save.sample_snapshot_done",
                new_name=new_name,
                old_daten_count=len(_st_old_daten),
                old_pdg_count=len(_st_old_pdg),
            )

            # Progesteron
            if steroid_active:
                new_daten, seen_dates = [], set()
                for d_edit, w_edit, probe_edit in dp_w:
                    # Skip deleted widgets or empty rows
                    try:
                        date_text = d_edit.text().strip() if d_edit else ''
                        value_text = w_edit.text().strip() if w_edit else ''
                        if not date_text or not value_text:
                            continue
                    except (RuntimeError, AttributeError):
                        # Widget has been deleted or is invalid
                        continue
                    try:
                        dt = datetime.strptime(d_edit.text(), DATE_FORMAT).date()
                        if dt in seen_dates: raise ValueError('Doppeltes Datum')
                        seen_dates.add(dt)
                        val = float(w_edit.text());  assert val >= 0
                        entry = {'datum': datetime.combine(dt, datetime.min.time()), 'wert': val}
                        # Add probennummer if provided
                        probe_text = probe_edit.text().strip()
                        if probe_text:
                            entry['probennummer'] = probe_text
                        new_daten.append(entry)
                    except Exception as e:
                        self._show_message(
                            self.messages.get('error.title', 'Error'),
                            self.messages.get('error.invalid_prog_values', 'Invalid progesterone values: {}').format(str(e)),
                            'error'
                        )
                        return
                max_mess = rec.get('max_messungen', DEFAULT_MAX_MESS)
                if len(new_daten) > max_mess:
                    self._show_message(
                        self.messages.get('error.title', 'Error'),
                        self.messages.get('error.max_measurements', 'Maximum of {} measurements allowed.').format(max_mess),
                        'information'
                    )
                    return
                rec['daten'] = new_daten
            else:
                rec['daten'] = rec.get('daten', [])

            # PdG
            if steroid_active:
                new_pdg = []
                for d_edit, w_edit, probe_edit in pdg_w:
                    # Skip deleted widgets or empty rows
                    try:
                        date_text = d_edit.text().strip() if d_edit else ''
                        value_text = w_edit.text().strip() if w_edit else ''
                        if not date_text or not value_text:
                            continue
                    except (RuntimeError, AttributeError):
                        # Widget has been deleted or is invalid
                        continue
                    try:
                        dt = datetime.strptime(d_edit.text(), DATE_FORMAT).date()
                        val = float((w_edit.text() or '').strip());  assert val >= 0
                        entry = {'datum': datetime.combine(dt, datetime.min.time()), 'wert': val}
                        # Add probennummer if provided
                        probe_text = probe_edit.text().strip()
                        if probe_text:
                            entry['probennummer'] = probe_text
                        new_pdg.append(entry)
                    except Exception as e:
                        self._show_message(
                            self.messages.get('error.title', 'Error'),
                            self.messages.get('error.invalid_pdg_values', 'Invalid PdG values: {}').format(str(e)),
                            'error'
                        )
                        return
                rec['pdg'] = new_pdg
            else:
                rec['pdg'] = rec.get('pdg', [])

            # Gewicht
            new_gew, seen_w = [], set()
            for d_edit, w_edit, probe_edit in gew_w:
                # Skip deleted widgets or empty rows
                try:
                    date_text = d_edit.text().strip() if d_edit else ''
                    value_text = w_edit.text().strip() if w_edit else ''
                    if not date_text or not value_text:
                        continue
                except (RuntimeError, AttributeError):
                    # Widget has been deleted or is invalid
                    continue
                try:
                    dt = datetime.strptime(d_edit.text(), DATE_FORMAT).date()
                    if dt in seen_w:
                        raise ValueError(self.messages.get('error.duplicate_weight_date', 'Duplicate weight date: {}').format(dt.strftime(DATE_FORMAT)))
                    seen_w.add(dt)
                    val = float(w_edit.text()); assert val >= 0
                    new_gew.append({'datum': datetime.combine(dt, datetime.min.time()), 'wert': val})
                except Exception as e:
                    logging.error(f"Weight validation error: {e}, date={d_edit.text() if d_edit else 'N/A'}, value={w_edit.text() if w_edit else 'N/A'}")
                    self._show_message(
                        self.messages.get('error.title', 'Error'),
                        self.messages.get('error.invalid_weight_values', 'Invalid weight values: {}').format(str(e)),
                        'error'
                    )
                    return
            rec['gewicht'] = new_gew

            # Events
            if steroid_active:
                new_op, seen_op = [], set()
                new_pgf, seen_pgf = [], set()
                new_fsh, seen_fsh = [], set()
                new_ev, seen_ev   = [], set()
                for d_edit, combo in events_w:
                    # Skip deleted widgets or empty rows
                    try:
                        date_text = d_edit.text().strip() if d_edit else ''
                        if not date_text:
                            continue
                    except (RuntimeError, AttributeError):
                        # Widget has been deleted or is invalid
                        continue
                    try:
                        dt = datetime.strptime(d_edit.text(), DATE_FORMAT).date()
                        # Use currentData() to get canonical event type, not translated display text
                        typ = combo.currentData()
                        if not typ or typ not in EVENT_TYPES: raise ValueError
                        if typ == 'surgery':
                            if dt not in seen_op:  seen_op.add(dt);  new_op.append(datetime.combine(dt, datetime.min.time()))
                        elif typ == 'pgf':
                            if dt not in seen_pgf: seen_pgf.add(dt); new_pgf.append(datetime.combine(dt, datetime.min.time()))
                        elif typ == 'fsh':
                            if dt not in seen_fsh:
                                seen_fsh.add(dt)
                                new_fsh.append({'typ':'fsh','datum': datetime.combine(dt, datetime.min.time())})
                        else:
                            if dt not in seen_ev:
                                seen_ev.add(dt)
                                new_ev.append({'typ': typ, 'datum': datetime.combine(dt, datetime.min.time())})
                    except Exception:
                        self._show_message(
                            self.messages.get('error.title', 'Error'),
                            self.messages.get('error.invalid_events', 'Invalid events.'),
                            'error'
                        )
                        return
                rec['op']  = new_op
                rec['pgf'] = new_pgf
                rec['events'] = (new_fsh if role_code == Role.SPENDER.value else new_ev)
            else:
                rec['op'] = rec.get('op', [])
                rec['pgf'] = rec.get('pgf', [])
                rec['events'] = rec.get('events', [])
            self._save_trace(
                "female_like.save.measurements_events_done",
                new_name=new_name,
                record=self._save_trace_record_summary(rec),
            )

            if (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
                and heritage_parent_fields is not None
            ):
                try:
                    self._save_trace("female_like.save.heritage_parent.before", new_name=new_name)
                    parent_values = self.heritage_plugin.read_parent_group(heritage_parent_fields)
                    self.heritage_plugin.save_parentage(new_name, parent_values, source="plugin")
                    # Create heritage-only placeholders for non-existing parents
                    mother = parent_values.get("egg_donor", "")
                    father = parent_values.get("sperm_donor", "")
                    species = rec.get("species", "")
                    self.heritage_plugin._ensure_parent_placeholders(mother, father, species)
                    self._save_trace("female_like.save.heritage_parent.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("female_like.save.heritage_parent.exception", new_name=new_name, error=e)
                    logging.error(f"Heritage_Track parent save failed for {new_name}: {e}")

            if (
                getattr(self, 'has_cage_track_plugin', False)
                and getattr(self, 'cage_track_plugin', None)
                and cage_address_fields is not None
            ):
                try:
                    self._save_trace("female_like.save.cage_address.before", new_name=new_name)
                    from Plugins.Cage__Track.ui_address_fields import extract_address_values
                    addr_values = extract_address_values(cage_address_fields)
                    self.cage_track_plugin.save_address_from_dialog(new_name, addr_values)
                    self._save_trace("female_like.save.cage_address.after", new_name=new_name)
                except Exception as e:
                    self._save_trace("female_like.save.cage_address.exception", new_name=new_name, error=e)
                    logging.error(f"Cage_Track address save failed for {new_name}: {e}")

            # Commit
            key = new_name
            self._save_trace("female_like.save.commit.before", key=key, new_name=new_name)
            self.animals[key] = rec
            if not creating and new_name != name:
                self.animals.pop(name, None)
                self._rewrite_animal_references_after_identity_change(name, new_name, _orig_name)
            elif creating and key != name:
                # ensure no leftover incomplete entry
                self.animals.pop(name, None)
            self._save_trace("female_like.save.commit.after", key=key, animal_count=len(self.animals))
            self._save_trace("female_like.save.project_updates.schedule.before", key=key)
            self._schedule_post_animal_save_project_updates(
                key, _old_project, rec.get('project', ''),
                _old_severity, rec.get('severity', ''),
                old_in_exp_f, new_in_exp_f, creating)
            self._save_trace("female_like.save.project_updates.schedule.after", key=key)
            self._save_trace("female_like.save.persistence.before", key=key)
            self._save_persistence(defer_post_save_work=True)
            self._save_trace("female_like.save.persistence.after", key=key)
            # Sync to Heritage Track (including role-determined sex)
            if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
                try:
                    self._save_trace("female_like.save.heritage_sync.before", key=key)
                    self.heritage_plugin.sync_from_record(key, rec, in_main_animals=True)
                    self._save_trace("female_like.save.heritage_sync.after", key=key)
                except Exception as e:
                    self._save_trace("female_like.save.heritage_sync.exception", key=key, error=e)
                    logging.error(f"Heritage_Track sync failed for female animal {key}: {e}")
            # Sample_Track: notify for newly added blood/urine measurements
            if getattr(self, 'has_sample_track_plugin', False) and self.sample_track_plugin:
                _st_anim = key
                for _e in rec.get('daten', []):
                    _d = _e.get('datum')
                    if _d and _d not in _st_old_daten:
                        try:
                            self._save_trace("female_like.save.sample_blood.before", key=key, date=_d)
                            self.sample_track_plugin.notify_blood_sample(
                                _st_anim, _d.strftime(DATE_FORMAT))
                            self._save_trace("female_like.save.sample_blood.after", key=key, date=_d)
                        except Exception:
                            self._save_trace("female_like.save.sample_blood.exception", key=key, date=_d)
                            pass
                for _e in rec.get('pdg', []):
                    _d = _e.get('datum')
                    if _d and _d not in _st_old_pdg:
                        try:
                            self._save_trace("female_like.save.sample_urine.before", key=key, date=_d)
                            self.sample_track_plugin.notify_urine_sample(
                                _st_anim, _d.strftime(DATE_FORMAT))
                            self._save_trace("female_like.save.sample_urine.after", key=key, date=_d)
                        except Exception:
                            self._save_trace("female_like.save.sample_urine.exception", key=key, date=_d)
                            pass
            # Force heritage visible to show newly created parent placeholders
            _heritage_fields_present = (
                getattr(self, 'has_heritage_plugin', False)
                and getattr(self, 'heritage_plugin', None)
                and heritage_parent_fields is not None
            )
            self._save_trace("female_like.save.refresh_list.before", key=key)
            self._refresh_list(update_tab_visibility=True, force_heritage_visible=_heritage_fields_present)
            self._save_trace("female_like.save.refresh_list.after", key=key)
            
            self.selected_animals = [key]
            self._save_trace("female_like.save.dialog_accept.before", key=key)
            dlg.accept()
            self._save_trace("female_like.save.dialog_accept.after", key=key)
            # Refresh report table if Reports tab is active
            if self.reports_enabled and hasattr(self, 'report_current_animal'):
                if self.report_current_animal == key:
                    self._save_trace("female_like.save.report_update.before", key=key)
                    self._update_report_table()
                    self._save_trace("female_like.save.report_update.after", key=key)

        save_btn.clicked.connect(on_save)
        v.addWidget(save_btn)
        
        # Adjust dialog width based on tab content
        def adjust_dialog_width():
            if tabs.isVisible():
                current_widget = tabs.currentWidget()
                if current_widget:
                    # Force layout update to get accurate size
                    current_widget.updateGeometry()
                    QApplication.processEvents()
                    
                    # Get the actual content width including all widgets
                    content_width = current_widget.sizeHint().width()
                    # Add extra padding for margins, scrollbars, and fixed-width elements
                    dialog_width = max(700, content_width + 150)
                    dlg.setMinimumWidth(dialog_width)
                    dlg.resize(dialog_width, dlg.height())
        
        # Connect to tab changes
        tabs.currentChanged.connect(lambda: adjust_dialog_width())
        
        self._apply_dialog_width(dlg)
        QTimer.singleShot(100, adjust_dialog_width)
        # ── Field-level permissions ───────────────────────────────────────────
        _pdg_extra = list(_pdg_tabs) if isinstance(_pdg_tabs, (list, tuple)) else []
        self._apply_dialog_field_permissions({
            'core.edit_animal_identity': [
                name_le, species_cb, id_le, chip_le, origin_le,
                project_le, severity_cb,
                birth_date_le, death_date_le, special_status_le,
            ],
            'core.edit_animal_housing': [_cage_addr_group, _heritage_group],
            'core.edit_animal_measurements': [gewicht_tab, _events_tab],
            'core.edit_animal_research_data': [
                _prog_tab, role_cb, ref_w_le,
                maxop_le, maxe_le, rec_le, maxpr_le, maxb_le,
                maxm_le, maxp_le, maxfsh_le,
            ] + _pdg_extra,
        })
        if read_only:
            self._freeze_dialog_inputs(dlg)
        dlg.exec()

    # ------------------------
    # 7.22 Edit Animal Dialog
    #     Display a dialog for editing an existing animal’s details.
    # ------------------------

    def _dlg_edit_animal(self, name: Optional[str] = None) -> None:
        """Dispatch to the correct editor based on the active category tab.

        Tab index map:
          0 = Female (Spenderin/Amme)
          1 = Samenspender
          2 = Nachkomme
          3 = Partnertier (🐾)
          4 = Zuchttier (⚤)
          5 = Versuchstier (💡)
          6 = Alle (Sortierung ändern)
        """
        can_edit     = self._master_can('core.edit_animal_core')
        can_readonly = self._master_can('core.open_readonly_dialogs')
        if not can_edit and not can_readonly:
            self._show_permission_denied()
            return
        read_only = not can_edit

        if not self.selected_animals:
            self._show_message("error.edit_animal.no_selection")
            return

        name = self.selected_animals[0]
        idx = self.category_tab.currentIndex()

        if idx == 1:
            self._dlg_samenspender(name, read_only=read_only)
            return

        if idx == 2:
            self._dlg_offspring(name, read_only=read_only)
            return

        if idx == 3:
            self._dlg_partner(name, read_only=read_only)
            return

        if idx == 4:
            self._dlg_zuchttier(name, read_only=read_only)
            return

        if idx == 5:
            self._dlg_versuchstier(name, read_only=read_only)
            return

        if idx == 6:
            self._dlg_change_sort(name)
            return

        # idx == 0 → female editor
        self._dlg_female_animal(name, read_only=read_only)

    # ------------------------
    # 7.24 Print Data Dialog
    #     Display and handle exporting of current data views.
    # ------------------------

    # ------------------------
    # 7.25 About Program Dialog
    #     Display application version and credits information.
    # ------------------------

    # ------------------------
    # 7.26 Import Excel Data
    #     Load and validate data from an Excel file into the application.
    # ------------------------
    def _import_excel(self) -> None:
        """Import data from an Excel file with progress feedback."""
        if not self._master_can('core.import'):
            self._show_permission_denied()
            return
        self._reset_import_identity_prompt_cache()
        path, _ = QFileDialog.getOpenFileName(
            self, "Excel laden", "", "Excel-Dateien (*.xlsx *.xls)"
        )
        if not path:
            return
        try:
            df = pd.read_excel(path, dtype={'Name': str, 'F': str})
        except Exception as e:
            logging.error(f"Failed to load Excel: {e}")
            self._show_message('Fehler', f"Excel-Laden fehlgeschlagen: {e}", 'error')
            return

        # Look for sample ID column (probennummer) - try common column names
        sample_id_col = None
        possible_names = ['Probennummer', 'Sample ID', 'Sample', 'Probe', 'ID', 'probennummer', 'sample_id', 'probe']
        for col_name in df.columns:
            if col_name in possible_names or col_name.lower() in [n.lower() for n in possible_names]:
                sample_id_col = col_name
                break
        
        required = ['Name', 'Datum', 'Progesteron (ng/ml)', 'F']
        if not all(col in df.columns for col in required):
            self._show_message(
                'Fehler',
                "Excel benötigt Spalten 'Name', 'Datum', 'Progesteron (ng/ml)' und 'F'.",
                'information'
            )
            return

        # ------------------------
        # 7.26.2 Prepare date and progesterone columns
        # ------------------------
        df['Datum'] = df['Datum'].apply(parse_date)
        df['Progesteron (ng/ml)'] = pd.to_numeric(df['Progesteron (ng/ml)'], errors='coerce')

        # ------------------------
        # 7.26.3 Count invalid rows for dates and names
        # ------------------------
        invalid_dates = df['Datum'].isna().sum()
        invalid_names = df['Name'].isna().sum() + df['Name'].eq('').sum()
        total_invalid = invalid_dates + invalid_names
        if total_invalid > 0:
            self._show_message('Warnung', f"{total_invalid} ungültige Zeilen ignoriert.", 'information')

        # ------------------------
        # 7.26.4 Split into measurement and event DataFrames
        # ------------------------
        df_measurements = df[
            df['Progesteron (ng/ml)'].notna() &
            (df['F'].isna() | (df['F'].str.strip() == ''))
        ].dropna(subset=['Datum', 'Name'])
        df_events = df[
            df['F'].notna() &
            (df['F'].str.strip() != '')
        ].dropna(subset=['Datum', 'Name'])

        # ------------------------
        # 7.26.5 Filter out blank Name entries
        # ------------------------
        df_measurements = df_measurements[df_measurements['Name'].str.strip() != '']
        df_events = df_events[df_events['Name'].str.strip() != '']

        # ------------------------
        # 7.26.6 Validate event types against allowed list
        # ------------------------
        valid_events = {'pgf', 'embryo', 'op', 'abort', 'geburt', 'trächtigkeit'}
        f_values = df_events['F'].astype(str).str.strip().str.lower()
        unrec = set(f_values) - valid_events
        if unrec:
            self._show_message(
                'Warnung',
                f"Unbekannte Ereignisse ignoriert: {', '.join(sorted(unrec))}",
                'warning'
            )

        # ------------------------
        # 7.26.7 Initialize Counters and Optional Progress Dialog
        #     Track added, skipped rows and show progress dialog if large file.
        # ------------------------
        meas_added = evt_added = skipped = 0
        _st_blood_pairs: list = []
        rows = len(df)
        progress = None
        if rows > 1000:
            progress = QProgressDialog(
                self.messages["dialog.import_excel"],
                self.messages["button.cancel"],
                0,
                rows,
                self
            )
            progress.setWindowModality(Qt.WindowModal)

        try:
            # ------------------------
            # 7.26.7.1 Process Measurements from Excel
            # ------------------------
            for i, (_, row) in enumerate(df_measurements.iterrows()):
                if progress:
                    progress.setValue(i)
                    if progress.wasCanceled():
                        break

                raw_name = row['Name'].strip()
                if any(c in raw_name for c in r'\/:*?"<>'):
                    logging.warning(f"Invalid name skipped: {raw_name}")
                    skipped += 1
                    continue
                name = self._resolve_import_animal_key(row, create_missing=True)
                if not name:
                    skipped += 1
                    continue

                datum = row['Datum']
                if datum is None:
                    logging.warning(f"Invalid date skipped for {name}: {row['Datum']}")
                    skipped += 1
                    continue

                wert = row['Progesteron (ng/ml)']
                if wert < 0:
                    logging.warning(f"Negative progesterone value skipped for {name}: {wert}")
                    skipped += 1
                    continue

                # Extract sample ID from sample ID column if available
                probennummer = None
                if sample_id_col and sample_id_col in row:
                    probennummer = str(row[sample_id_col]) if pd.notna(row[sample_id_col]) else None

                a = self._ensure_defaults_for_new(name)

                # ------------------------
                # 7.26.7.3 Check max measurements limit
                # ------------------------
                if len(a.get('daten', [])) >= a.get('max_messungen', DEFAULT_MAX_MESS):
                    logging.warning(f"Max measurements reached for {name}, skipping")
                    skipped += 1
                    continue

                # ------------------------
                # 7.26.7.4 Avoid duplicates
                # ------------------------
                existing = a['daten']
                is_new = True
                for ex in existing:
                    if isinstance(ex['datum'], datetime) and ex['datum'] == datum and abs(ex['wert'] - wert) < 1e-3:
                        is_new = False
                        break

                if is_new:
                    entry = {'datum': datum, 'wert': wert}
                    if probennummer:
                        entry['probennummer'] = probennummer
                    a['daten'].append(entry)
                    meas_added += 1
                    _d_str = datum.strftime(DATE_FORMAT) if hasattr(datum, 'strftime') else ''
                    if _d_str:
                        _st_blood_pairs.append((name, _d_str))

            # ------------------------
            # 7.26.7.2 Process events from Excel
            # ------------------------
            for _, row in df_events.iterrows():
                if progress:
                    progress.setValue(progress.value() + 1)
                    if progress.wasCanceled():
                        break

                raw_name = row['Name'].strip()
                if any(c in raw_name for c in r'\/:*?"<>'):
                    logging.warning(f"Invalid name skipped: {raw_name}")
                    skipped += 1
                    continue
                name = self._resolve_import_animal_key(row, create_missing=True)
                if not name:
                    skipped += 1
                    continue

                datum = row['Datum']
                if datum is None:
                    logging.warning(f"Invalid date skipped for {name}: {row['Datum']}")
                    skipped += 1
                    continue

                evt = row['F'].strip().lower()
                if evt not in valid_events:
                    logging.warning(f"Invalid event skipped for {name}: {evt}")
                    skipped += 1
                    continue

                a = self._ensure_defaults_for_new(name)

                # ------------------------
                # 7.26.7.2.1 Add events to animal record
                # ------------------------
                if evt == 'pgf':
                    a.setdefault('pgf', [])
                    if datum not in a['pgf'] and len(a['pgf']) < a.get('max_pgf', DEFAULT_MAX_PGF):
                        a['pgf'].append(datum)
                        evt_added += 1
                    else:
                        logging.warning(f"PGF event skipped for {name}: max reached or duplicate")
                        skipped += 1
                elif evt == 'embryo':
                    a.setdefault('embryo', [])
                    if datum not in a['embryo'] and len(a['embryo']) < a.get('max_embryo', DEFAULT_MAX_EMBRYO):
                        a['embryo'].append(datum)
                        evt_added += 1
                    else:
                        logging.warning(f"Embryo event skipped for {name}: invalid role or max reached")
                        skipped += 1
                elif evt == 'op':
                    a.setdefault('op', [])
                    if datum not in a['op'] and len(a['op']) < a.get('max_op', DEFAULT_MAX_OP):
                        a['op'].append(datum)
                        evt_added += 1
                    else:
                        logging.warning(f"OP event skipped for {name}: duplicate or max reached")
                        skipped += 1

                elif evt in ('abort', 'geburt', 'trächtigkeit'):
                    a.setdefault('events', [])
                    evs = a['events']
                    if not any(
                        isinstance(e.get('datum'), datetime) and e.get('datum') == datum and e.get('typ') == evt
                        for e in evs
                    ):
                        evs.append({'typ': evt, 'datum': datum})
                        evt_added += 1
                    else:
                        logging.warning(f"Lifecycle event skipped for {name}: duplicate")
                        skipped += 1

        finally:
            if progress:
                progress.setValue(rows)
                progress.deleteLater()

        # ------------------------
        # 7.26.8 Final message and persistence
        # ------------------------
        total = meas_added + evt_added
        if total > 0:
            self._show_message(
                "info.import_excel.added",
                measurements=meas_added,
                events=evt_added,
                total=total
            )
            self._save_persistence()
            # Sample_Track: notify for each newly imported blood measurement
            if getattr(self, 'has_sample_track_plugin', False) and self.sample_track_plugin:
                for _stn, _std in _st_blood_pairs:
                    try:
                        self.sample_track_plugin.notify_blood_sample(_stn, _std)
                    except Exception:
                        pass
            self._refresh_list(update_tab_visibility=True)
            self._on_select()
            logging.info(f"Imported {meas_added} measurements and {evt_added} events from {path}, skipped {skipped} rows")
        else:
            self._show_message(
                "info.import_excel.none_added",
                skipped=skipped
            )
            logging.info(f"No new data imported from {path}, skipped {skipped} rows")

    # ------------------------
    # 7.27.1 Load persisted data from storage
    # ------------------------
    def _load_persistence(self) -> None:
        """Load data from JSON with accurate robust datetime parsing."""
        data = self._read_json()
        self.animals = data.get('animals', {})
        self.archived = data.get('archived_animals', {})
        migrated_active_roles = normalize_animal_record_roles(self.animals)
        migrated_archived_roles = normalize_animal_record_roles(self.archived)
        if migrated_active_roles or migrated_archived_roles:
            logging.info(
                "Normalized animal role IDs on load: active=%s archived=%s",
                len(migrated_active_roles),
                len(migrated_archived_roles),
            )
        total_skipped = 0
        for d in (self.animals, self.archived):
            for animal_key, rec in d.items():
                identity_parts = split_animal_identity_key(animal_key)
                if identity_parts is not None:
                    rec['ipid'] = animal_key
                    rec['_base_name'] = rec.get('_base_name') or rec.get('display_name') or rec.get('name') or identity_parts[0]
                    rec['display_name'] = rec.get('display_name') or rec['_base_name']
                    rec['name'] = rec.get('name') or rec['_base_name']
                    if not rec.get('species'):
                        rec['species'] = identity_parts[1]
                    if not rec.get('birth_date'):
                        rec['birth_date'] = normalize_birth_date(identity_parts[2], required=False)
                else:
                    logging.error(
                        "Animal key is not an IPID identity key and is unsupported: %s",
                        animal_key,
                    )
                rec.setdefault('species', '')
                try:
                    rec['birth_date'] = normalize_birth_date(rec.get('birth_date'), required=False)
                except ValueError:
                    logging.warning(f"Invalid birth date for animal {animal_key}: {rec.get('birth_date')}")
                    rec['birth_date'] = ''
                rec.setdefault('chip_nr', '')
                rec.setdefault('origin', '')
                rec.setdefault('special_status', '')
                rec.setdefault('in_experiment', False)
                orig_daten = rec.get('daten', [])
                rec['daten'] = []
                skipped = 0
                for r in orig_daten:
                    try:
                        datum = parse_date(r['datum'])
                        if datum is None:
                            raise ValueError("Invalid date")
                        entry = {'datum': datum, 'wert': r['wert']}
                        probe = r.get('probennummer')
                        if probe is not None:
                            probe_text = str(probe).strip()
                            if probe_text:
                                entry['probennummer'] = probe_text
                        rec['daten'].append(entry)
                    except (ValueError, TypeError):
                        logging.warning(f"Skipped invalid measurement: {r}")
                        skipped += 1
                        continue
                orig_pgf = rec.get('pgf', [])
                rec['pgf'] = []
                for x in orig_pgf:
                    try:
                        dt = parse_date(x)
                        if dt is None:
                            raise ValueError("Invalid date")
                        rec['pgf'].append(dt)
                    except (ValueError, TypeError):
                        logging.warning(f"Skipped invalid PGF date: {x}")
                        skipped += 1
                        continue
                orig_embryo = rec.get('embryo', [])
                rec['embryo'] = []
                for x in orig_embryo:
                    try:
                        dt = parse_date(x)
                        if dt is None:
                            raise ValueError("Invalid date")
                        rec['embryo'].append(dt)
                    except (ValueError, TypeError):
                        logging.warning(f"Skipped invalid embryo date: {x}")
                        skipped += 1
                        continue
                # ------------------------
                # 7.27.1.1 Read reproductive events
                # ------------------------
                orig_events = rec.get('events', [])
                rec['events'] = []
                for ev in orig_events:
                    try:
                        datum = parse_date(ev.get('datum'))
                        typ = ev.get('typ', '').strip().lower()
                        # Normalize legacy German identifiers to English
                        normalized_typ = LEGACY_EVENT_MAP.get(typ, typ)
                        # Only include valid event types
                        if datum and normalized_typ in EVENT_TYPES:
                            rec['events'].append({"typ": normalized_typ, "datum": datum})
                    except Exception:
                        logging.warning(f"Skipped invalid event: {ev}")
                        skipped += 1

                # ------------------------
                # 7.27.1.2 Read OP data
                # ------------------------
                orig_op = rec.get('op', [])
                rec['op'] = []
                for x in orig_op:
                    try:
                        dt = parse_date(x)
                        if dt is None:
                            raise ValueError("Invalid date")
                        rec['op'].append(dt)
                    except (ValueError, TypeError):
                        logging.warning(f"Skipped invalid OP date: {x}")
                        skipped += 1

                # ------------------------
                # 7.27.1.3 Read weight data
                # ------------------------
                orig_w = rec.get('gewicht', [])
                rec['gewicht'] = []
                for x in orig_w:
                    try:
                        dt = parse_date(x['datum'])
                        if dt is None:
                            raise ValueError
                        rec['gewicht'].append({'datum': dt, 'wert': x['wert']})
                    except Exception:
                        logging.warning(f"Skipped invalid weight: {x}")
                        skipped += 1

                # ------------------------
                # 7.27.1.4 Read PdG data (unfiltered)
                # ------------------------
                orig_pdg = rec.get('pdg', [])
                rec['pdg'] = []
                for p in orig_pdg:
                    try:
                        dt = parse_date(p.get('datum'))
                        if dt is None:
                            raise ValueError("Invalid date")
                        entry = {'datum': dt, 'wert': p.get('wert')}
                        probe = p.get('probennummer')
                        if probe is not None:
                            probe_text = str(probe).strip()
                            if probe_text:
                                entry['probennummer'] = probe_text
                        rec['pdg'].append(entry)
                    except Exception:
                        logging.warning(f"Skipped invalid pdg entry: {p}")
                        skipped += 1

                # ------------------------
                # 7.27.1.5 Read sperm data
                # ------------------------
                orig_sperm = rec.get('sperm', [])
                rec['sperm'] = []
                for s in orig_sperm:
                    try:
                        dt = parse_date(s.get('datum'))
                        if dt is None:
                            raise ValueError("Invalid date")
                        rec['sperm'].append({
                            'datum':       dt,
                            'motility':    s.get('motility'),
                            'progressive': s.get('progressive'),
                            'count':       s.get('count'),
                        })
                    except Exception:
                        logging.warning(f"Skipped invalid sperm entry: {s}")
                        skipped += 1

                # ------------------------
                # 7.27.1.5 Add default fields for recovery and manual '+'
                # If these keys are missing (e.g. older persisted data),
                # assign sensible defaults. Donors, surrogates, and sperm donors get
                # the default recovery period; others get 0. All animals start
                # without a manual plus flag.
                role = rec.get('rolle', Role.SPENDER.value)
                if 'recovery_time' not in rec:
                    rec['recovery_time'] = DEFAULT_RECOVERY_TIME if role in (Role.SPENDER.value, Role.AMME.value, Role.SAMENSP.value) else 0
                # Migrate manual_plus to sick for backward compatibility
                if 'manual_plus' in rec and 'sick' not in rec:
                    rec['sick'] = rec['manual_plus']
                    del rec['manual_plus']
                if 'sick' not in rec:
                    rec['sick'] = False
                
                # Initialize sick_times array if missing and normalize to ISO strings
                if 'sick_times' not in rec:
                    rec['sick_times'] = []
                else:
                    # Normalize all sick_times entries to ISO strings for consistency
                    normalized_times = []
                    for st in rec.get('sick_times', []):
                        if isinstance(st, datetime):
                            normalized_times.append(st.isoformat())
                        elif isinstance(st, str):
                            normalized_times.append(st)
                    rec['sick_times'] = normalized_times
                
                # Initialize new sick period fields (for new persistent sick status feature)
                if 'sick_start_date' not in rec:
                    rec['sick_start_date'] = None
                if 'sick_end_date' not in rec:
                    rec['sick_end_date'] = None

                # ------------------------
                # 7.27.1.6 After processing all entries:
                # ------------------------
                total_skipped += skipped
                if skipped > 0:
                    logging.info(f"Skipped {skipped} invalid entries for animal")
        # ------------------------
        # End of for-rec loop; continue with overall statistics...
        # ------------------------
        if total_skipped > 5:
            self._show_message("warning.load.json.invalid_records", count=total_skipped)
        # Do not show dialogs during data-load; just set a flag.
        if not self.animals and not self.archived:
            self._no_data_pending = True
        logging.info(f"Loaded {len(self.animals)} animals and {len(self.archived)} archived animals")
        self._refresh_list(update_tab_visibility=True)

    # ------------------------
    # 7.27.2 Import PdG data from Excel
    # ------------------------
    def _import_pdg(self) -> None:
        """Import PdG data from a separate Excel file."""
        self._reset_import_identity_prompt_cache()
        path, _ = QFileDialog.getOpenFileName(
            self, "PdG-Daten laden", "", "Excel-Dateien (*.xlsx *.xls)"
        )
        if not path:
            return
        try:
            df = pd.read_excel(path, dtype={'Name': str})
        except Exception as e:
            logging.error(f"Failed to load PdG Excel: {e}")
            self._show_message('Fehler', f"PdG-Excel-Laden fehlgeschlagen: {e}", 'error')
            return
        
        # Look for sample ID column (probennummer) - try common column names
        sample_id_col = None
        possible_names = ['Probennummer', 'Sample ID', 'Sample', 'Probe', 'ID', 'probennummer', 'sample_id', 'probe']
        for col_name in df.columns:
            if col_name in possible_names or col_name.lower() in [n.lower() for n in possible_names]:
                sample_id_col = col_name
                break
        
        required = ['Name', 'Datum', 'PdG (µg/mg Cr)']
        if not all(col in df.columns for col in required):
            self._show_message(
                'Fehler',
                "PdG-Datei benötigt Spalten 'Name', 'Datum' und 'PdG (µg/mg Cr)'.",
                'information'
            )
            return
        df['Datum'] = df['Datum'].apply(parse_date)
        df['PdG (µg/mg Cr)'] = pd.to_numeric(df['PdG (µg/mg Cr)'], errors='coerce')
        invalid = df['Datum'].isna().sum() + df['Name'].isna().sum() + df['Name'].eq('').sum()
        if invalid > 0:
            self._show_message('Warnung', f"{invalid} ungültige Zeilen ignoriert.", 'information')
        added = 0
        _st_urine_pairs: list = []
        for _, r in df.dropna(subset=['Name','Datum','PdG (µg/mg Cr)']).iterrows():
            name = self._resolve_import_animal_key(r, create_missing=True)
            if not name:
                continue
            datum = r['Datum']
            val = r['PdG (µg/mg Cr)']
            
            # Extract sample ID from sample ID column if available
            probennummer = None
            if sample_id_col and sample_id_col in r:
                probennummer = str(r[sample_id_col]) if pd.notna(r[sample_id_col]) else None
            
            a = self._ensure_defaults_for_new(name)
            a.setdefault('pdg', [])
            entry = {'datum': datum, 'wert': val}
            if probennummer:
                entry['probennummer'] = probennummer
            a['pdg'].append(entry)
            added += 1
            _d_str = datum.strftime(DATE_FORMAT) if hasattr(datum, 'strftime') else ''
            if _d_str:
                _st_urine_pairs.append((name, _d_str))
        if added > 0:
            self._show_message(
                "info.import_pdg.added",
                count=added
            )
            self._save_persistence()
            # Sample_Track: notify for each newly imported urine measurement
            if getattr(self, 'has_sample_track_plugin', False) and self.sample_track_plugin:
                for _stn, _std in _st_urine_pairs:
                    try:
                        self.sample_track_plugin.notify_urine_sample(_stn, _std)
                    except Exception:
                        pass
            self._refresh_list(update_tab_visibility=True)
            self._on_select()
            logging.info(f"Imported {added} PdG entries from {path}")
        else:
            self._show_message("info.import_pdg.none_added")
            logging.info(f"No new PdG entries imported from {path}")

    # ------------------------
    # 7.27.3 Import weight data from Excel
    # ------------------------
    def _import_weights(self) -> None:
        """Import weights from an Excel file with columns 'Name', 'Datum', and 'Gewicht'."""
        self._reset_import_identity_prompt_cache()
        path, _ = QFileDialog.getOpenFileName(
            self, "Gewichte laden", "", "Excel-Dateien (*.xlsx *.xls)"
        )
        if not path:
            return

        try:
            df = pd.read_excel(path, dtype={'Name': str})
        except Exception as e:
            self._show_message('Fehler', f"Excel-Laden fehlgeschlagen: {e}", 'error')
            return

        required = ['Name', 'Datum', 'Gewicht']
        if not all(col in df.columns for col in required):
            self._show_message(
                'Fehler',
                "Excel benötigt Spalten 'Name','Datum' und 'Gewicht'.",
                'information'
            )
            return

        df['Datum'] = df['Datum'].apply(parse_date)
        df['Gewicht'] = pd.to_numeric(df['Gewicht'], errors='coerce')
        df = df.dropna(subset=['Name','Datum','Gewicht'])
        df['Name'] = df['Name'].str.strip()
        df = df.drop_duplicates(subset=['Name', 'Datum', 'Gewicht'])
        added = 0
        for _, row in df.iterrows():
            name = self._resolve_import_animal_key(row, create_missing=True)
            if not name:
                continue
            dt   = row['Datum']
            wt   = float(row['Gewicht'])

            a = self._ensure_defaults_for_new(name)
            a.setdefault('gewicht', [])

            existing = a['gewicht']
            if not any(e['datum'] == dt and e['wert'] == wt for e in existing):
                a['gewicht'].append({'datum': dt, 'wert': wt})
                added += 1

        if added:
            self._show_message(
                "info.import_weights.added",
                count=added
            )
            self._save_persistence()
            self._refresh_list(update_tab_visibility=True)
            self._on_select()
        else:
             self._show_message("info.import_weights.none_added")


    def _import_sperm_values(self) -> None:
        """Import sperm data from an Excel file."""
        if not self._is_steroid_track_active():
            return
        self._reset_import_identity_prompt_cache()
        path, _ = QFileDialog.getOpenFileName(
            self, "Spermawerte laden", "", "Excel-Dateien (*.xlsx *.xls)"
        )
        if not path:
            return

        try:
            df = pd.read_excel(path)
        except Exception as e:
            logging.error(f"Failed to load Sperm Excel: {e}")
            self._show_message_raw("Fehler", f"Sperma-Laden fehlgeschlagen: {e}", "error")
            return

        # Expected header columns
        required = ["Datum", "Name", "% Motility", "% Progressive", "Sperms/ml"]
        if not all(col in df.columns for col in required):
            self._show_message_raw(
                "Fehler",
                "Excel benötigt Spalten 'Datum','Name','% Motility','% Progressive','Sperms/ml'.",
                "information"
            )
            return

        # Parse date column → Python datetime (no Pandas Timestamps)
        df["Datum"] = df["Datum"].apply(parse_date)

        added = 0
        for _, row in df.iterrows():
            if pd.isna(row["Name"]) or pd.isna(row["Datum"]):
                continue  # skip invalid rows

            name = self._resolve_import_animal_key(row, create_missing=True)
            if not name:
                continue
            date_val = row["Datum"]

            # Create or get existing animal record
            a = self._ensure_defaults_for_new(name)

            try:
                mot = float(row["% Motility"]) if pd.notna(row["% Motility"]) else None
                prog = float(row["% Progressive"]) if pd.notna(row["% Progressive"]) else None
                count = float(row["Sperms/ml"]) if pd.notna(row["Sperms/ml"]) else None
            except (ValueError, TypeError):
                continue  # skip invalid numeric values

            # Ensure 'sperm' list exists (persisted schema)
            a.pop("sperm_values", None)  # drop legacy key to avoid JSON errors
            a.setdefault("sperm", [])

            # Append new sperm measurement (schema: 'datum', 'motility', 'progressive', 'count')
            a["sperm"].append({
                "datum": date_val,
                "motility": mot,
                "progressive": prog,
                "count": count,
            })
            added += 1

        if added:
            self._show_message("info.import_sperm.added", count=added)
            self._save_persistence()
        else:
            self._show_message("info.import_sperm.none_added")

    # ------------------------
    # 7.27.4 Save all animal data to persistent storage
    # ------------------------
    def _audit_data_snapshot_diff_when_safe(
        self,
        before_snapshot: Dict[str, Any],
        after_snapshot: Dict[str, Any],
    ) -> None:
        """Run data-save audit after modal dialogs have closed."""
        self._save_trace("audit_snapshot_diff.safe.enter")
        if QApplication.activeModalWidget() is not None:
            self._save_trace("audit_snapshot_diff.safe.modal_wait")
            QTimer.singleShot(
                250,
                lambda before=before_snapshot, after=after_snapshot:
                    self._audit_data_snapshot_diff_when_safe(before, after)
            )
            return
        try:
            self._save_trace("audit_snapshot_diff.before")
            self._audit_data_snapshot_diff(before_snapshot, after_snapshot)
            self._save_trace("audit_snapshot_diff.after")
        except Exception as audit_error:
            self._save_trace("audit_snapshot_diff.exception", error=audit_error)
            logging.error(f"Failed to audit data diff after save: {audit_error}")

    def _run_post_persistence_sync(
        self,
        audit_snapshots: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None,
    ) -> None:
        """Run plugin sync work after modal dialogs have closed."""
        self._save_trace(
            "post_persistence_sync.enter",
            has_audit_snapshots=audit_snapshots is not None,
        )
        if QApplication.activeModalWidget() is not None:
            self._save_trace("post_persistence_sync.modal_wait")
            QTimer.singleShot(
                250,
                lambda snapshots=audit_snapshots:
                    self._run_post_persistence_sync(snapshots)
            )
            return

        logging.info("Post-persistence plugin sync begin")
        if audit_snapshots is not None:
            self._save_trace("post_persistence_sync.audit.before")
            before_snapshot, after_snapshot = audit_snapshots
            self._audit_data_snapshot_diff_when_safe(before_snapshot, after_snapshot)
            self._save_trace("post_persistence_sync.audit.after")

        if getattr(self, 'has_heritage_plugin', False) and getattr(self, 'heritage_plugin', None):
            try:
                self._save_trace("post_persistence_sync.heritage.before")
                self.heritage_plugin.store.sync_from_animals(self.animals)
                self._save_trace("post_persistence_sync.heritage.after")
            except Exception as e:
                self._save_trace("post_persistence_sync.heritage.exception", error=e)
                logging.error(f"Heritage_Track sync during persistence save failed: {e}")

        if getattr(self, 'has_cage_track_plugin', False) and getattr(self, 'cage_track_plugin', None):
            try:
                self._save_trace("post_persistence_sync.cage.before")
                self.cage_track_plugin.sync_animal_data(self.animals)
                self._save_trace("post_persistence_sync.cage.after")
            except Exception as e:
                self._save_trace("post_persistence_sync.cage.exception", error=e)
                logging.error(f"Cage_Track sync during persistence save failed: {e}")
        logging.info("Post-persistence plugin sync completed")
        self._save_trace("post_persistence_sync.exit")

    def _save_persistence(self, defer_post_save_work: bool = False):
        """Save current data to JSON."""
        self._save_trace("save_persistence.enter", defer_post_save_work=defer_post_save_work)
        logging.info(
            "Persistence save begin; defer_post_save_work=%s",
            defer_post_save_work,
        )
        data = {'animals': self.animals, 'archived_animals': self.archived}
        audit_snapshots = self._write_json(
            data,
            audit_after_save=not defer_post_save_work,
        )
        self._save_trace(
            "save_persistence.write_json.after",
            defer_post_save_work=defer_post_save_work,
            has_audit_snapshots=audit_snapshots is not None,
        )
        logging.info(
            "Persistence JSON save completed; defer_post_save_work=%s",
            defer_post_save_work,
        )
        if defer_post_save_work:
            self._save_trace("save_persistence.defer_timer.before")
            QTimer.singleShot(
                250,
                lambda snapshots=audit_snapshots:
                    self._run_post_persistence_sync(snapshots)
            )
            self._save_trace("save_persistence.defer_timer.after")
        else:
            self._save_trace("save_persistence.sync_now.before")
            self._run_post_persistence_sync()
            self._save_trace("save_persistence.sync_now.after")

    # ------------------------
    # 7.27.5 Archive the currently selected animal
    # ------------------------
    def _archive_current(self):
        """Archive the animals."""
        if not self._master_can('core.archive_animals'):
            self._show_permission_denied()
            return
        if not self.selected_animals:
            self._show_message("error.archive.no_selection")
            return
        data = self._read_json()
        archived_names = [
            name for name in list(self.selected_animals)
            if name in data.get('animals', {})
        ]
        if not archived_names:
            self._show_message("error.archive.no_selection")
            return
        for name in archived_names:
            data['archived_animals'][name] = data['animals'].pop(name, {})
        self._write_json(data)
        self._load_persistence()
        self._on_select()
        details = (
            f"animals={self._audit_value_to_string(archived_names)}; "
            f"count={len(archived_names)}; "
            "from_scope=animals; to_scope=archived_animals"
        )
        self._master_audit("archive", "ProgTrack", details)
        logging.info(f"Archived animals: {', '.join(archived_names)}")

    # ------------------------
    # 7.27.6 Restore an archived animal to active list
    # ------------------------
    def _restore_archived(self):
        """Restore an archived animal."""
        if not self._master_can('core.archive_animals'):
            self._show_permission_denied()
            return
        selected = getattr(self, '_selected_archived', [])
        name = selected[0] if selected else self.cmb_arch.currentText()
        no_archived_msg = self.messages.get("archived.no_animals", "No archived animals")
        if not name or name == no_archived_msg:
            return
        data = self._read_json()
        data['animals'][name] = data['archived_animals'].pop(name, {})
        self._write_json(data)
        self._load_persistence()
        self._on_select()
        details = (
            f"animal={name}; "
            "from_scope=archived_animals; to_scope=animals"
        )
        self._master_audit("restore", "ProgTrack", details)
        logging.info(f"Restored animal: {name}")

    # ------------------------
    # 7.27.7 Delete an animal permanently from archive
    # ------------------------
    def _delete_archived(self):
        """Delete an archived animal after confirmation."""
        if not self._master_can('core.delete_animals'):
            self._show_permission_denied()
            return
        selected = getattr(self, '_selected_archived', [])
        name = selected[0] if selected else self.cmb_arch.currentText()
        no_archived_msg = self.messages.get("archived.no_animals", "No archived animals")
        if not name or name == no_archived_msg:
            return
        reply = self._show_message(
            "question.delete_archived.confirm",
            name=name,
            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        data = self._read_json()
        data['archived_animals'].pop(name, None)
        self._write_json(data)
        self._load_persistence()
        self._on_select()
        details = f"animal={name}; scope=archived_animals"
        self._master_audit("delete", "ProgTrack", details)
        logging.info(f"Deleted archived animal: {name}")
        self._show_message("deletion.delete_archived.success", name=name)

    # ------------------------
    # 7.27.8 Open dialog to edit PdG conversion formula
    # ------------------------

    # ------------------------
    # 7.27.9 Apply current display mode to all plotted lines
    # ------------------------
    def _apply_mode(self):
        """Show/hide Prog curves and adjust controls based on availability."""
        if not hasattr(self, 'prog_lines'):
            return

        steroid_active = self._is_steroid_track_active()

        # Get checkbox states for modes
        # Main progesterone checkbox must be checked for any sub-mode to show
        prog_enabled = steroid_active and getattr(self, 'chk_prog', None) and self.chk_prog.isChecked()
        show_combined = prog_enabled and self.chk_mode_combined.isChecked() if self.has_pdg_plugin and hasattr(self, 'chk_mode_combined') else False
        show_blood = prog_enabled and self.chk_mode_blood.isChecked()
        show_urine = prog_enabled and self.chk_mode_urin.isChecked() if self.has_pdg_plugin and hasattr(self, 'chk_mode_urin') else False
        
        # Get line style toggle states
        blood_on = self.rb_blood_on.isChecked() if steroid_active else False
        urine_on = self.rb_urine_on.isChecked() if self.has_pdg_plugin and steroid_active and hasattr(self, 'rb_urine_on') else False
        combined_on = self.rb_combined_on.isChecked() if self.has_pdg_plugin and steroid_active and hasattr(self, 'rb_combined_on') else False

        # 1) Blood progesterone lines: checkbox controls visibility, radio controls line style
        for ln in self.prog_lines:
            ln.set_visible(show_blood)  # Visible when checkbox is checked
            ln.set_color(self.blood_color.name())
            # Radio button controls line style: On = solid line, Off = markers only
            ln.set_linestyle(ln._orig_linestyle if blood_on else 'None')
        
        # 2) Urine PdG lines: checkbox controls visibility, radio controls line style
        if self.has_pdg_plugin and steroid_active:
            for ln in self.pdg_lines:
                ln.set_visible(show_urine)  # Visible when checkbox is checked
                ln.set_color(self.urine_color.name())
                # Radio button controls line style: On = solid line, Off = markers only
                ln.set_linestyle(ln._orig_linestyle if urine_on else 'None')

            # 3) Combined lines: checkbox controls visibility, radio controls line style
            for ln in self.pdg_conv_lines:
                ln.set_visible(show_combined)  # Visible when checkbox is checked
                ln.set_color(self.combined_color.name())
                # Radio button controls line style: On = solid line, Off = markers only
                ln.set_linestyle(ln._orig_linestyle if combined_on else 'None')

            # 4) Toggle visibility of hollow PdG→Prog dots only in Combined mode
            for dot in getattr(self, 'pdg_hollow_dots', []):
                dot.set_visible(show_combined and combined_on)

        # 5) Toggle visibility of overlay solid dots (raw-Prog) only in Combined mode
        if show_combined:
            # compute which animals have PdG data available for combined display
            # The plugin handles conversion; we just check for PdG data presence
            combined_map = {}
            for name in self.selected_animals:
                a = self.animals.get(name, {})
                # Combined display available if PdG data exists and plugin is active
                has_combined = steroid_active and self.has_pdg_plugin and bool(a.get('pdg'))
                combined_map[name] = has_combined
            # show dots only if combined mode is active and line style is on
            for dot, name in zip(self.prog_overlay_dots, self.prog_overlay_names):
                show = combined_on and combined_map.get(name, False)
                dot.set_visible(show)
        else:
            # Hide all combined overlay dots when not in combined mode
            for dot in self.prog_overlay_dots:
                dot.set_visible(False)

        # 6) Update y-axis labels and scales based on active modes
        # Blood/Combined use left main axis (ax), Urine uses left offset axis (pdg_ax)
        for name in self.selected_animals:
            ctx = self._plot_ctx.get(name, {})
            ax = ctx.get('ax')
            pdg_ax = ctx.get('pdg_ax')
            animal = self.animals.get(name, {})
            rolle = animal.get('rolle')
            hide_label = rolle in [Role.OFFSPRING.value, Role.PARTNER.value]
            
            if ax:
                # Handle main left axis (blood or combined)
                if show_blood or show_combined:
                    # Determine which values to use for scaling
                    if show_combined and ctx.get('combined_vals'):
                        prog_vals = ctx.get('combined_vals', [])
                        label_text = self.messages.get('plot.ylabel.progesterone', 'Pgr (ng/ml)')
                        label_color = self.combined_color.name()
                    elif show_blood:
                        prog_vals = ctx.get('prog_vals', [])
                        label_text = self.messages.get('plot.ylabel.progesterone', 'Pgr (ng/ml)')  
                        label_color = self.blood_color.name()
                    else:
                        prog_vals = []
                        label_text = ''
                        label_color = 'black'
                    
                    # Scale main axis to blood/combined values
                    if prog_vals:
                        pmin = float(np.nanmin(prog_vals))
                        pmax = float(np.nanmax(prog_vals))
                        if pmin == pmax:
                            pad = max(1.0, abs(pmax) * 0.1)
                            ax.set_ylim(max(0, pmax - pad), pmax + pad)
                        else:
                            ax.set_ylim(0, max(pmax * 1.1, PHASESCHWELLE * 1.2))
                    
                    # Set label and ticks
                    ylabel = ax.yaxis.get_label()
                    if hide_label:
                        ylabel.set_text('')
                        ax.tick_params(axis='y', labelleft=False, left=False)
                    else:
                        ylabel.set_text(label_text)
                        ylabel.set_color(label_color)
                        ylabel.set_bbox({
                            'boxstyle': 'square,pad=0.3',
                            'facecolor': 'white',
                            'edgecolor': 'none'
                        })
                        ax.tick_params(axis='y', labelcolor=label_color, labelleft=True, left=True)
                else:
                    # No blood or combined - hide main axis labels
                    ylabel = ax.yaxis.get_label()
                    ylabel.set_text('')
                    ax.tick_params(axis='y', labelleft=False, left=False)
            
            # Handle PdG twin axis (urine - left offset)
            if pdg_ax:
                if show_urine:
                    # Show and scale PdG axis for urine
                    pdg_vals = ctx.get('pdg_vals', [])
                    if pdg_vals:
                        pmin_pdg = float(np.nanmin(pdg_vals))
                        pmax_pdg = float(np.nanmax(pdg_vals))
                        if pmin_pdg == pmax_pdg:
                            pad = max(0.1, abs(pmin_pdg) * 0.1)
                        else:
                            pad = max(0.1, 0.1 * (pmax_pdg - pmin_pdg))
                        pdg_ax.set_ylim(pmin_pdg - pad, pmax_pdg + pad)
                    
                    # Show the PdG axis spine
                    pdg_ax.spines['left'].set_visible(True)
                    
                    ylabel = pdg_ax.yaxis.get_label()
                    if hide_label:
                        ylabel.set_text('')
                        pdg_ax.tick_params(axis='y', labelleft=False, left=False)
                    else:
                        ylabel.set_text(self.messages.get('plot.ylabel.pdg', 'PdG (µg/mg Cr)'))
                        ylabel.set_color(self.urine_color.name())
                        ylabel.set_bbox({
                            'boxstyle': 'square,pad=0.3',
                            'facecolor': 'white',
                            'edgecolor': 'none'
                        })
                        pdg_ax.tick_params(axis='y', labelcolor=self.urine_color.name(), labelleft=True, left=True)
                else:
                    # Hide entire PdG axis when urine not selected
                    ylabel = pdg_ax.yaxis.get_label()
                    ylabel.set_text('')
                    pdg_ax.tick_params(axis='y', labelleft=False, left=False)
                    # Hide the PdG axis spine completely
                    pdg_ax.spines['left'].set_visible(False)

        # 7) Enable/disable weight radiobuttons based on checkbox state
        weight_enabled = self.chk_weight.isChecked() and self.chk_weight.isEnabled()
        self.rb_weight_on.setEnabled(weight_enabled)
        self.rb_weight_off.setEnabled(weight_enabled)

        # Redraw canvas if it exists
        if self.current_canvas:
            self.current_canvas.draw_idle()


       # 7) Samenspender: set y-label and autoscale to max sperm count
        if hasattr(self, 'current_figure') and self.current_figure:
            for ax in self.current_figure.axes:
                name = getattr(ax, '_animal_name', None)
                if name and self.animals.get(name, {}).get('rolle') == Role.SAMENSP.value:
                    ax.set_ylabel("Spermien/ml")
                    counts = [s.get('count', 0) or 0 for s in self.animals[name].get('sperm', [])]
                    maxc = max(counts) if counts else 1
                    ax.set_ylim(0, maxc * 1.1)



    # -------------------------------------------------------------------
    # 7.X Plugin Availability Check
    # -------------------------------------------------------------------



    
    # ------------------------
    # 7.27.11 Color picker for plot elements
    # ------------------------
    def _choose_color(self, element_type: str):
        """Open color picker dialog and apply selected color to plot elements.
        
        Parameters
        ----------
        element_type : str
            One of 'prog', 'weight', or 'events'
        """
        # Get current color
        if element_type == 'prog':
            current_color = self.prog_color
        elif element_type == 'weight':
            current_color = self.weight_color
        elif element_type == 'events':
            current_color = self.events_color
        else:
            return
        
        # Open color dialog
        color = QColorDialog.getColor(current_color, self, 
                                      self.messages.get("dialog.color_picker.title", "Choose Color"))
        
        if not color.isValid():
            return
        
        # Store the new color
        if element_type == 'prog':
            self.prog_color = color
            self.btn_prog_color.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #888;")
            # Update all progesterone lines
            for ln in self.prog_lines + self.pdg_lines + self.pdg_conv_lines:
                ln.set_color(color.name())
            # Update overlay dots
            for dot in self.prog_overlay_dots:
                dot.set_facecolors(color.name())
                dot.set_edgecolors(color.name())
            for dot in self.pdg_hollow_dots:
                dot.set_edgecolors(color.name())
                
        elif element_type == 'weight':
            self.weight_color = color
            self.btn_weight_color.setStyleSheet(f"background-color: {color.name()}; border: 1px solid #888;")
            # Update all weight lines
            for ln in self.weight_lines:
                ln.set_color(color.name())
            # Update weight axes labels
            for ax in self.weight_axes:
                ax.set_ylabel(self.messages.get('plot.ylabel.weight', 'Weight (g)'), color=color.name())
                ax.tick_params(axis='y', labelcolor=color.name())
                
        # Events color picker removed - no longer supported
        elif element_type == 'events':
            return
        
        # Redraw canvas
        if self.current_canvas:
            self.current_canvas.draw_idle()

    # ------------------------
    # 7.27.11 Handle main Progesterone checkbox toggle
    # ------------------------
    def _on_prog_checkbox_toggled(self, checked):
        """Master progesterone checkbox controls all three sub-checkboxes."""
        if not self._is_steroid_track_active():
            if checked:
                self.chk_prog.blockSignals(True)
                self.chk_prog.setChecked(False)
                self.chk_prog.blockSignals(False)
            if self.has_pdg_plugin and hasattr(self, 'chk_mode_combined'):
                self.chk_mode_combined.setChecked(False)
            self.chk_mode_blood.setChecked(False)
            if self.has_pdg_plugin and hasattr(self, 'chk_mode_urin'):
                self.chk_mode_urin.setChecked(False)
            self._apply_mode()
            return

        if not checked:
            # Uncheck all three sub-checkboxes
            if self.has_pdg_plugin and hasattr(self, 'chk_mode_combined'):
                self.chk_mode_combined.setChecked(False)
            self.chk_mode_blood.setChecked(False)
            if self.has_pdg_plugin and hasattr(self, 'chk_mode_urin'):
                self.chk_mode_urin.setChecked(False)
        else:
            # Apply default state: Combined > Blood > Urine > Nothing
            self._apply_default_prog_mode()
        
        # Trigger mode application
        self._apply_mode()
    
    def _apply_default_prog_mode(self):
        """Set default progesterone mode based on data availability.
        
        Priority: Combined > Blood > Urine > Nothing
        """
        if not self._is_steroid_track_active():
            return
        if self.has_pdg_plugin and hasattr(self, 'chk_mode_combined') and self.chk_mode_combined.isEnabled():
            self.chk_mode_combined.setChecked(True)
        elif self.chk_mode_blood.isEnabled():
            self.chk_mode_blood.setChecked(True)
        elif self.has_pdg_plugin and hasattr(self, 'chk_mode_urin') and self.chk_mode_urin.isEnabled():
            self.chk_mode_urin.setChecked(True)
    
    def _on_mode_checkbox_toggled(self, checked):
        """Handle mode checkbox toggles - all three can be active simultaneously."""
        if not self._is_steroid_track_active():
            self._apply_mode()
            return
        # No mutex logic - Combined, Blood, and Urine can all be checked at the same time
        # This allows displaying multiple data types simultaneously on the plot
        
        # Trigger mode application to update plot
        self._apply_mode()
    
    def _toggle_weight_linestyle(self, checked):
        """Toggle weight line style between solid line and markers only.
        
        Args:
            checked: True = show line with markers, False = markers only
        """
        if self.current_canvas is None:
            return
        
        # Weight lines should show markers when visible
        # Toggle controls whether connecting line is shown
        for ln in self.weight_lines:
            if ln.get_visible():  # Only affect visible weight lines
                ln.set_linestyle(ln._orig_linestyle if checked else 'None')

        if self.current_canvas:
            self.current_canvas.draw_idle()

    # ------------------------
    # 7.27.12 Toggle visibility of all progesterone-related controls
    # ------------------------
    def _toggle_all_prog_related(self, checked):
        """Deprecated: Line style toggling is now handled separately for each mode.
        
        This method is kept for backward compatibility but does nothing.
        Line style toggling is now handled by individual mode toggles:
        - rb_combined_on/off for combined mode
        - rb_blood_on/off for blood mode
        - rb_urine_on/off for urine mode
        """
        pass

    # ------------------------
    # 7.27.13 Apply urine y-axis scaling (removed - now handled in plotting)
    # ------------------------
    def _apply_urine_scale(self):
        """Placeholder method for backward compatibility.
        
        Urine scaling is now handled directly during plotting in _plot_selected()
        and mode changes in _apply_mode().
        """
        pass
    
    # ------------------------
    # 7.28 Application Cleanup
    # ------------------------
    def closeEvent(self, event):
        """Handle application close event - save session and release file lock."""
        # Save Master_Track session before closing
        self._save_master_session()

        logger.info("Application closing - releasing file lock")
        if getattr(self, "lock_retry_timer", None) is not None:
            self.lock_retry_timer.stop()
            self.lock_retry_timer = None
        release_lock(self.lock_handle, LOCK_FILE)
        event.accept()

# ------------------------
# 8.0 Application entry point
#     Initialize and start the main Qt event loop.
# ------------------------
if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = ProgTrackApp()
    win.show()
    sys.exit(app.exec())
