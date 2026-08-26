# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.1
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.2.1 or newer.
# Module: Surgery Planner scheduling and Gantt-chart tools.

import os
import sys
import json
import logging
import shutil
import time
import csv
import re
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set, Union

# Set up paths
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))  # Go up two levels to get to the root

# Date format for display
DATE_FORMAT = '%Y-%m-%d'

# Qt6-compatible Matplotlib canvas. The plugin never changes the process-global backend.
import matplotlib as mpl
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from matplotlib.ticker import NullFormatter  # For hiding axis labels
import numpy as np  # Used by matplotlib

# PyQt6 imports
from PyQt6 import QtWidgets, QtCore, QtGui
from PyQt6.QtCore import QLocale, Qt, QDate, QSize, QPointF, QRectF
from PyQt6.QtGui import QColor, QAction, QTextCharFormat, QBrush, QFont, QIcon, QPainter, QPen, QColorConstants
from PyQt6.QtWidgets import (
    QWidget, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QLineEdit, QListWidget, QListWidgetItem, QAbstractItemView,
    QGroupBox, QFormLayout, QSplitter, QScrollArea, QCalendarWidget, QTabWidget,
    QSpinBox, QApplication, QCheckBox, QFrame, QStatusBar, QSizePolicy, QComboBox,
    QDialogButtonBox, QTableWidget, QTableWidgetItem, QHeaderView, QToolButton,
    QMenu, QFileDialog
)

from Plugins.core.animal_identity import animal_base_name
from Plugins.core.animal_roles import ROLE_VALUE_AMME, ROLE_VALUE_SPENDER, canonical_role_value
from Plugins.core.backend_store import BackendJsonStore
from .surgery_engine import PlannerSnapshot, stable_schedule_id


def _backend_load(backend, record_id: str, default):
    if backend is None:
        return default
    return BackendJsonStore(backend, "surgery-planner", record_id).load(default)


def _backend_save(backend, record_id: str, payload) -> None:
    if backend is None:
        raise RuntimeError("Surgery Planner requires the ProgTrack backend.")
    BackendJsonStore(backend, "surgery-planner", record_id).save(payload)


def _animal_role_value(animal: Dict[str, Any]) -> str:
    return canonical_role_value((animal or {}).get('rolle', ''))


def _canonical_event_count(animal: Dict[str, Any], event_type: str) -> int:
    return sum(
        1 for event in animal.get("events", []) or []
        if isinstance(event, dict) and event.get("typ") == event_type
    )


def _canonical_event_dates(animal: Dict[str, Any], event_type: str) -> list[Any]:
    return [
        event.get("datum")
        for event in animal.get("events", []) or []
        if isinstance(event, dict)
        and event.get("typ") == event_type
        and event.get("datum") is not None
    ]

def _as_planner_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (DATE_FORMAT, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None

# Translation helper function
def tr(messages: Dict[str, Any], key: str, default: str = '', **kwargs) -> str:
    """
    Get a translated message from the messages dictionary.
    Supports both flat structure ("general.weekday.monday") and nested structure.
    
    Args:
        messages: Dictionary containing translations
        key: Dot-separated key path (e.g., 'surgery_planner.title')
        default: Default text if key is not found
        **kwargs: Format arguments for the message
        
    Returns:
        str: The translated and formatted message
    """
    if not messages or not key:
        return default.format(**kwargs) if kwargs else default
    
    # First try direct lookup (flat structure)
    if key in messages and isinstance(messages[key], str):
        result = messages[key]
        return result.format(**kwargs) if kwargs else result
    
    # Then try nested structure
    parts = key.split('.')
    value = messages
    
    try:
        for part in parts:
            if not isinstance(value, dict):
                return default.format(**kwargs) if kwargs else default
            value = value.get(part, {})
        
        # If we have a string, format it with kwargs if provided
        if isinstance(value, str):
            return value.format(**kwargs) if kwargs else value
            
        # If we have a dict but no string, return default
        return default.format(**kwargs) if kwargs else default
        
    except (AttributeError, KeyError, ValueError):
        return default.format(**kwargs) if kwargs else default

# ─── Logging Setup ──────────────────────────────────────────────────
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('SurgeryPlanner')


# ─── Data Structures ──────────────────────────────────────────────────

class BlockDay:
    def __init__(self, date_obj: date, name: str):
        self.date = date_obj
        self.name = name

class ScheduleEntry:
    def __init__(self, animal: str, event_type: str, date_obj: date, override: bool = False):
        self.animal = animal
        self.event_type = event_type
        self.date = date_obj
        self.override_weekday = override
        self.created_by = "surgery_planner_plugin_v1.0.0"
        self.timestamp = datetime.utcnow().isoformat()
        # by default, new entries are unfixed (can be regenerated)
        self.fixed = False
        self.entry_id = stable_schedule_id(animal, event_type, date_obj)
        self.override_reason = ""


def schedule_entry_to_dict(entry: ScheduleEntry, *, date_format: str = "iso") -> Dict[str, Any]:
    date_value = entry.date.isoformat() if date_format == "iso" else entry.date.strftime(date_format)
    return {
        'animal': entry.animal,
        'ipid': entry.animal,
        'name': animal_base_name(entry.animal),
        'event_type': entry.event_type,
        'date': date_value,
        'override_weekday': entry.override_weekday,
        'created_by': entry.created_by,
        'timestamp': entry.timestamp,
        'fixed': entry.fixed,
        'id': getattr(entry, 'entry_id', stable_schedule_id(entry.animal, entry.event_type, entry.date)),
        'override_reason': getattr(entry, 'override_reason', ''),
    }

# ─── JSON I/O ──────────────────────────────────────────────

# Determine the root directory relative to this plugin's location. The plugin lives
# in Plugins/Surgery_Planner/surgery_planner.py, so the root is two levels up.
PLUGIN_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(PLUGIN_DIR, '..', '..'))
# Store plugin‐specific data files alongside this plugin.  The block days
# and schedule definitions are written into the same folder as this file
# (Plugins/Surgery_Planner) rather than the root.  Exported PNG schedules
# continue to be written into ROOT_DIR.
DATE_FORMAT = '%Y-%m-%d'

def load_animals() -> list[dict]:
    """Return planner animals supplied by the application backend.

    The standalone helper is retained for plugin API compatibility, but it
    deliberately never reads a legacy JSON file.  The main application
    injects the backend records into :class:`SurgeryPlannerWidget`.
    """
    return []

def load_block_days(backend=None) -> list[BlockDay]:
    """
    Load the list of block days from the plugin's block day file.  The
    expected format is a JSON object with a ``block_days`` key pointing to
    a list of objects with ``date`` and ``name``.  If the file contains a
    list directly or any other unexpected structure, the function
    gracefully handles it by interpreting it as a list of block day
    dictionaries.  Any malformed entries are skipped with a logged
    warning.
    """
    try:
        data = _backend_load(backend, "block-days", {"block_days": []})
        items: list = []
        if isinstance(data, dict):
            raw = data.get('block_days', [])
            items = raw if isinstance(raw, list) else []
        elif isinstance(data, list):
            items = data
        else:
            logger.warning(f"Unexpected format in backend block-days record: {type(data)}")
            items = []
        days: list[BlockDay] = []
        for item in items:
            try:
                if not isinstance(item, dict):
                    logger.warning(f"Ignoring malformed block day entry: {item}")
                    continue
                date_str = item['date']
                # Try the plugin’s chosen format first, then fall back to ISO
                try:
                    dt = datetime.strptime(date_str, DATE_FORMAT).date()
                except ValueError:
                    dt = datetime.strptime(date_str, '%Y-%m-%d').date()
                # Use the name from the JSON, or default to 'Holiday' if missing
                name = item.get('name', 'Holiday')
                days.append(BlockDay(dt, name))
            except Exception as ex:
                logger.warning(f"Skipping invalid block day record {item}: {ex}")
        logger.debug(f"Loaded {len(days)} block days")
        return days
    except Exception as e:
        logger.error(f"Failed to load block days: {e}")
        return []

def save_block_days(days: list[BlockDay], backend=None) -> None:
    try:
        data = _backend_load(backend, "block-days", {"block_days": []})
        raw = data.get('block_days', []) if isinstance(data, dict) else []
        # build a map date→name from disk
        on_disk = {item['date']: item.get('name', '') for item in raw if 'date' in item}
        # update map with our in-memory days (add or override), using DATE_FORMAT
        for d in days:
            key = d.date.strftime(DATE_FORMAT)
            on_disk[key] = d.name
        # only keep those still blocked
        keep = [bd.date.strftime(DATE_FORMAT) for bd in days]
        cleaned = [
            {'date': dt, 'name': on_disk[dt]}
            for dt in on_disk
            if dt in keep
        ]
        _backend_save(backend, "block-days", {'block_days': cleaned})
        logger.debug(f"Persisted {len(cleaned)} block days to backend")
    except Exception as e:
        logger.error(f"Failed to save block days: {e}")

def save_schedule_to_plugin(entries: list[ScheduleEntry], backend=None) -> None:
    """Publish the schedule through the shared backend."""
    try:
        # Convert entries to dict format for JSON serialization
        schedule_data = []
        for entry in entries:
            schedule_data.append(schedule_entry_to_dict(entry, date_format="iso"))
        
        _backend_save(backend, "schedule", {'schedule': schedule_data})
        logger.info("Schedule saved to backend")
    except Exception as e:
        logger.error(f"Failed to save schedule: {e}")

def load_plugin_settings(backend=None) -> dict:
    """Load plugin settings from the shared backend."""
    try:
        settings = _backend_load(backend, "settings", {})
        if settings:
            logger.info("Settings loaded from backend")
            return settings
        default_settings = {
            'donors_per_surgery': 2,
            'surrogates_per_transfer': 2,
            'transfer_offset_days': 6,
            'recovery_op': 60,
            'recovery_transfer': 30,
            'cycle_active_days': 60,
            'cycle_break_days': 30,
            'surgery_weekdays': [0, 1, 2, 3, 4],
            'transfer_weekdays': [0, 1, 2, 3, 4],
        }
        logger.info("Using default plugin settings")
        return default_settings
    except Exception as e:
        logger.error(f"Failed to load settings: {e}")
        return {}

def save_plugin_settings(settings: dict, backend=None) -> None:
    """Save plugin settings to the shared backend."""
    try:
        # Convert sets to lists for JSON serialization
        serializable_settings = {}
        for key, value in settings.items():
            if isinstance(value, set):
                serializable_settings[key] = list(value)
            else:
                serializable_settings[key] = value
        
        _backend_save(backend, "settings", serializable_settings)
        logger.info("Settings saved to backend")
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")

def export_schedule_to_csv(entries: list[ScheduleEntry], filename: str) -> None:
    try:
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['IPID', 'Animal', 'Event Type', 'Date', 'Override Weekday', 'Created By', 'Timestamp'])
            for entry in entries:
                writer.writerow([
                    entry.animal,
                    animal_base_name(entry.animal),
                    entry.event_type,
                    entry.date.isoformat(),
                    entry.override_weekday,
                    entry.created_by,
                    entry.timestamp,
                ])
        logger.debug(f"Exported schedule to {filename}")
    except Exception as e:
        logger.error(f"Failed to export CSV: {e}")

# ─── Scheduler ─────────────────────────────────────────

class GanttWidget(QDialog):

    def _backend_load(self, record_id: str, default):
        return _backend_load(self.backend, record_id, default)

    def _backend_save(self, record_id: str, payload) -> None:
        _backend_save(self.backend, record_id, payload)

    def __init__(self, animals: Optional[list[dict]] = None, messages: Optional[dict] = None, parent=None):
        """
        Create a new GanttWidget.  The optional ``animals`` parameter allows
        the caller to specify a pre‑filtered list of animal records (each
        containing at least a ``name``, ``rolle``, ``OP_max`` and
        ``Embryo_max``).  The optional ``messages`` parameter provides
        localized strings for the UI.
        """
        super().__init__(parent)
        self.backend = getattr(parent, "backend", None)
        
        # Initialize instance variables
        self.planned = []  # List of scheduled events
        self.fixed_events = set()  # Set of fixed event IDs
        self.animal_pools = {}  # Tracks animal availability
        self.messages = messages or {}
        self.animals = animals or []
        self._parent = parent  # Store parent for permission checks
        self.animal_checkboxes = {}  # Tracks checkboxes for each animal {name: QCheckBox}
        self.checked_animals = set()  # Set of animal names that are checked for scheduling
        
        # Debug: Check what language and messages we have
        logger.debug(f"Surgery Planner initialized with language: {self.messages.get('_language', 'unknown')}")
        logger.debug(f"Messages keys: {list(self.messages.keys())[:10]}...")  # Show first 10 keys
        logger.debug(f"Sample weekday translation: {self.messages.get('general.weekday.monday', 'NOT FOUND')}")
        self.block_days = []
        self.calendar_events = {}
        self._formatted_dates = set()
        self._dots_data = []  # For plot interaction
        self.day_width = 86400  # Seconds in a day
        
        # Initialize pan state and limits so _on_motion never crashes
        self._pan_active = False
        self._pan_start_x = 0
        self._pan_start_y = 0
        self._pan_xlim = (0, 0)
        self._pan_ylim = (0, 0)
        
        # Initialize settings with default values
        self.settings = {
            'recovery_op': 60,
            'recovery_transfer': 30,
            'cycle_active_days': 60,
            'cycle_break_days': 30,
            'transfer_offset_days': 6,
            'donors_per_surgery': 2,
            'surrogates_per_transfer': 2,
            'surgery_weekdays': {0, 1, 2, 3, 4},  # Monday-Friday by default
            'transfer_weekdays': {0, 1, 2, 3, 4},  # Monday-Friday by default
        }
        
        # Set window properties
        self.setWindowTitle(tr(messages, 'surgery_planner.window_title', 'Surgery Planner'))
        self.resize(1200, 800)
        
        # Set window icon if available
        try:
            icon_path = os.path.join(ROOT_DIR, 'icons', 'progtrack_icon.ico')
            if os.path.exists(icon_path):
                self.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            logger.warning(f"Could not set window icon: {e}")
            
        # Set window flags
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Initialize UI first
        self._init_ui()
        
        # Load data after UI is initialized
        self._load_data()
        
        # Initialize schedule and calendar after everything else is ready
        self._initialize_schedule_and_calendar()

    def _can(self, perm):
        """Check if current user has permission. Returns True if no parent/master_track."""
        parent = getattr(self, '_parent', None)
        if parent is None:
            return True
        can_fn = getattr(parent, '_master_can', None)
        if callable(can_fn):
            return bool(can_fn(perm))
        mt = getattr(parent, 'master_track', None)
        if mt is None:
            return True
        if "master_track" in getattr(parent, '_disabled_plugins', set()):
            return True
        return bool(getattr(mt, 'can', lambda _: False)(perm))

    def _can_edit_schedule(self) -> bool:
        return self._can('op_scheduler.use')

    def _apply_permission_state(self) -> None:
        """Keep view-only users inside the planner while disabling write controls."""
        editable = self._can_edit_schedule()
        for attr in (
            'block_name',
            'add_block_btn',
            'horizon_start',
            'horizon_end',
            'donors_per_surgery',
            'surrogates_per_transfer',
            'transfer_offset',
            'surgery_days',
            'transfer_days',
            'recovery_op',
            'recovery_transfer',
            'cycle_active_days',
            'cycle_break_days',
            'generate_btn',
            'optimize_btn',
            'save_btn',
            'invert_fixed_btn',
            'fix_all_btn',
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                try:
                    widget.setEnabled(editable)
                except Exception:
                    pass
        # Export does not modify ProgTrack data; keep it available to view-only users.
        export_btn = getattr(self, 'export_menu_btn', None)
        if export_btn is not None:
            export_btn.setEnabled(self._can('op_scheduler.view'))
        for checkbox in getattr(self, 'animal_checkboxes', {}).values():
            try:
                checkbox.setEnabled(editable)
            except Exception:
                pass

    def _initialize_schedule_and_calendar(self):
        """Initialize schedule and calendar after UI is fully set up."""
        # Load schedule using the new method (main file only)
        self.load_schedule_on_startup()
        
        # Render the loaded schedule if it exists
        if hasattr(self, 'planned') and self.planned:
            self._render_schedule()
        
        # Load and highlight all saved schedule dates in the calendar
        self._load_schedule_events()
        self._update_calendar_format()
        
        # Ensure the figure is drawn on startup
        try:
            self.canvas.draw()
        except Exception as e:
            logger.error(f"Error drawing initial canvas: {e}")

    def _format_xaxis(self):
        """Re-apply date locator, formatter, and 90° rotation."""
        self.ax.xaxis_date()
        self.ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter(DATE_FORMAT))
        # no-op: now handled by _update_xaxis

    def _update_xaxis(self):
        """Enhanced dynamic x-axis with intelligent date spacing."""
        self.ax.xaxis_date()
        x_min, x_max = self.ax.get_xlim()
        span_days = x_max - x_min
        
        # Calculate optimal interval based on span and figure width
        fig_width_px = self.fig.get_figwidth() * self.fig.dpi
        min_label_spacing_px = 60  # Minimum pixels between labels
        
        if span_days <= 7:
            # Daily view: show all days
            self.ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        elif span_days <= 31:
            # Monthly view: show every 3 days
            self.ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        elif span_days <= 180:
            # 6-month view: weekly
            self.ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m'))
        else:
            # Long-term view: monthly
            self.ax.xaxis.set_major_locator(mdates.MonthLocator())
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%m.%Y'))
        
        # Dynamic rotation based on label density
        labels = self.ax.get_xticklabels()
        if len(labels) > 15:
            for lbl in labels:
                lbl.set_rotation(45)
        else:
            for lbl in labels:
                lbl.set_rotation(0)
        
        self.ax.tick_params(axis='x', which='major', labelsize=7)


    def _draw_month_background(self, start_date: date, end_date: date):
        """
        Shade every second calendar month (Jan, Mar, May, …) in light gray
        across the full vertical span.
        """
        m = date(start_date.year, start_date.month, 1)
        last = date(end_date.year, end_date.month, 1)
        while m <= last:
            if m.month % 2 == 1:
                # compute first day of next month
                if m.month == 12:
                    nm = date(m.year + 1, 1, 1)
                else:
                    nm = date(m.year, m.month + 1, 1)
                self.ax.axvspan(
                    mdates.date2num(m), mdates.date2num(nm),
                    facecolor='gray', alpha=0.1, zorder=0
                )
            # advance to next month
            if m.month == 12:
                m = date(m.year + 1, 1, 1)
            else:
                m = date(m.year, m.month + 1, 1)

    def _load_data(self) -> None:
        """
        Load data required by the planner using READ-ONLY ProgTrack access.
        Load plugin settings from plugin config file.
        """
        # Load plugin settings first
        plugin_settings = load_plugin_settings(self.backend)
        if plugin_settings:
            # Merge plugin settings with current settings (plugin settings take precedence)
            self.settings.update(plugin_settings)
        
        # Animals are injected from the configured backend by ProgTrack.
        # An empty list is valid (for example, before the first import); do
        # not fall back to a legacy JSON file.
        if not self.animals:
            logger.info("No planner animals were supplied by the configured backend")
        self.block_days = load_block_days(self.backend)
        self.update_animal_table()

        # Refresh calendar formatting now that block days have been loaded.
        try:
            self._update_calendar_format()
        except Exception as _:
            pass

    def _init_ui(self):
        # Set window title using the translation function
        self.setWindowTitle(tr(self.messages, 'surgery_planner.window_title', 'Surgery Planner'))
        # Use the same program icon as the main ProgTrack application if available
        try:
            icon_path = os.path.join(ROOT_DIR, 'icons', 'progtrack_icon.ico')
            if os.path.exists(icon_path):
                from PyQt6.QtGui import QIcon
                self.setWindowIcon(QIcon(icon_path))
        except Exception:
            pass
        # define the “normal” window size
        self.resize(1366, 768)
        # allow minimize, maximize (restore) and close in the title bar
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        # Optionally: start maximized (fills the screen but keeps title bar)
        #self.showMaximized()
        # ── Main container: a horizontal splitter between left (calendar+settings) and right (Gantt) ──
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left pane: calendar (top) + settings (scrollable) ─────────────────────
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Calendar + block-day controls
        self.calendar = QCalendarWidget()
        
        # Set initial calendar locale based on language setting
        self._update_calendar_locale()
        
        self.block_name = QLineEdit(tr(self.messages, 'surgery_planner.block_day_name', 'Holiday'))
        self.add_block_btn = QPushButton(tr(self.messages, 'surgery_planner.button.block_unblock_date', 'Block/Unblock Date'))
        self.add_block_btn.clicked.connect(self._add_block_day)
        self.add_block_btn.setEnabled(self._can('op_scheduler.use'))
        left_layout.addWidget(self.calendar)
        left_layout.addWidget(self.block_name)
        left_layout.addWidget(self.add_block_btn)
        # refresh calendar formatting on page change
        self.calendar.currentPageChanged.connect(lambda y, m: self._update_calendar_format())
        # clicking a date shows its scheduled events
        self.calendar.clicked.connect(self._on_calendar_date_clicked)
        # …and do it once right now so existing block_days render immediately
        self._update_calendar_format()

        # Create a scroll area for the settings
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        
        # Create a widget to contain the settings
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        
        # Date range section
        date_group = QGroupBox(tr(self.messages, 'surgery_planner.group.date_range', 'Date Range'))
        date_layout = QFormLayout()
        
        self.horizon_start = QLineEdit(datetime.today().strftime(DATE_FORMAT))
        self.horizon_end = QLineEdit((datetime.today() + timedelta(days=365)).strftime(DATE_FORMAT))
        
        date_layout.addRow(tr(self.messages, 'surgery_planner.label.start_date', 'Start Date:'), self.horizon_start)
        date_layout.addRow(tr(self.messages, 'surgery_planner.label.end_date', 'End Date:'), self.horizon_end)
        date_group.setLayout(date_layout)
        settings_layout.addWidget(date_group)
        
        # Animal Numbers configuration group
        animal_numbers_group = QGroupBox(tr(self.messages, 'surgery_planner.group.animal_numbers', 'Animal Numbers'))
        animal_numbers_layout = QFormLayout()

        self.donors_per_surgery = QSpinBox()
        self.donors_per_surgery.setRange(1, 10)  # Up to 10 is sufficient
        self.donors_per_surgery.setValue(2)
        animal_numbers_layout.addRow(
            tr(self.messages, 'surgery_planner.label.donors_per_surgery', 'Donors per Surgery:'),
            self.donors_per_surgery
        )

        self.surrogates_per_transfer = QSpinBox()
        self.surrogates_per_transfer.setRange(1, 10)  # Up to 10 is sufficient
        self.surrogates_per_transfer.setValue(2)
        animal_numbers_layout.addRow(
            tr(self.messages, 'surgery_planner.label.surrogates_per_transfer', 'Surrogates per Transfer:'),
            self.surrogates_per_transfer
        )

        # Transfer offset days configuration
        self.transfer_offset = QSpinBox()
        self.transfer_offset.setRange(0, 365)
        self.transfer_offset.setValue(self.settings.get('transfer_offset_days', 6))
        animal_numbers_layout.addRow(
            tr(self.messages, 'surgery_planner.label.transfer_offset_days', 'Transfer offset (days):'),
            self.transfer_offset
        )

        animal_numbers_group.setLayout(animal_numbers_layout)
        settings_layout.addWidget(animal_numbers_group)

        # Connect signals to update settings
        def update_donors(val):
            self.settings['donors_per_surgery'] = val
            save_plugin_settings(self.settings, self.backend)

        def update_surrogates(val):
            self.settings['surrogates_per_transfer'] = val
            save_plugin_settings(self.settings, self.backend)

        def update_transfer_offset(val):
            self.settings['transfer_offset_days'] = val
            save_plugin_settings(self.settings, self.backend)

        self.donors_per_surgery.valueChanged.connect(update_donors)
        self.surrogates_per_transfer.valueChanged.connect(update_surrogates)
        self.transfer_offset.valueChanged.connect(update_transfer_offset)
        
        # Surgery weekdays selector with scroll area
        surgery_group = QGroupBox(tr(self.messages, 'surgery_planner.group.surgery_days', 'Surgery Days'))
        surgery_layout = QVBoxLayout()
        
        # Create a container widget for the list with fixed height
        surgery_container = QWidget()
        surgery_container_layout = QVBoxLayout(surgery_container)
        surgery_container_layout.setContentsMargins(0, 0, 0, 0)
        
        self.surgery_days = QListWidget()
        self.surgery_days.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Add translated weekday names
        weekdays = [
            tr(self.messages, 'general.weekday.monday', 'Monday'),
            tr(self.messages, 'general.weekday.tuesday', 'Tuesday'),
            tr(self.messages, 'general.weekday.wednesday', 'Wednesday'),
            tr(self.messages, 'general.weekday.thursday', 'Thursday'),
            tr(self.messages, 'general.weekday.friday', 'Friday'),
            tr(self.messages, 'general.weekday.saturday', 'Saturday'),
            tr(self.messages, 'general.weekday.sunday', 'Sunday')
        ]
        self.surgery_days.addItems(weekdays)
        self.surgery_days.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        
        # Pre-select Wednesday by default
        for i, day in enumerate(weekdays):
            if day == tr(self.messages, 'general.weekday.wednesday', 'Wednesday'):
                self.surgery_days.item(i).setSelected(True)
        
        # Set fixed height to show about 4 items at a time with scrollbar
        row_h = self.surgery_days.sizeHintForRow(0)
        self.surgery_days.setFixedHeight(row_h * 4 + 2)  # 4 rows + a bit of padding
        
        surgery_container_layout.addWidget(self.surgery_days)
        surgery_layout.addWidget(surgery_container)
        surgery_group.setLayout(surgery_layout)
        settings_layout.addWidget(surgery_group)
        
        # Keep settings in sync when surgery weekdays change
        def update_surgery():
            names = [item.text() for item in self.surgery_days.selectedItems()]
            self.settings['surgery_weekdays'] = self._parse_weekdays(','.join(names))
        
        self.surgery_days.itemSelectionChanged.connect(update_surgery)
        
        # Transfer weekdays selector with scroll area
        transfer_group = QGroupBox(tr(self.messages, 'surgery_planner.group.transfer_days', 'Embryo Transfer Days'))
        transfer_layout = QVBoxLayout()
        
        # Create a container widget for the list with fixed height
        transfer_container = QWidget()
        transfer_container_layout = QVBoxLayout(transfer_container)
        transfer_container_layout.setContentsMargins(0, 0, 0, 0)
        
        self.transfer_days = QListWidget()
        self.transfer_days.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        # Use the same translated weekdays
        self.transfer_days.addItems(weekdays)
        self.transfer_days.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        
        # Pre-select Tuesday by default
        for i, day in enumerate(weekdays):
            if day == tr(self.messages, 'general.weekday.tuesday', 'Tuesday'):
                self.transfer_days.item(i).setSelected(True)
        
        # Set fixed height to show about 4 items at a time with scrollbar
        row_h = self.transfer_days.sizeHintForRow(0)
        self.transfer_days.setFixedHeight(row_h * 4 + 2)  # 4 rows + a bit of padding
        rows2 = self.transfer_days.count()
        if rows2 > 0:
            row_h2 = self.transfer_days.sizeHintForRow(0)
            frame2 = self.transfer_days.frameWidth() * 2
            self.transfer_days.setFixedHeight(rows2 * row_h2 + frame2)
        
        transfer_layout.addWidget(self.transfer_days)
        transfer_group.setLayout(transfer_layout)
        
        # Keep settings in sync when transfer weekdays change
        def update_transfer():
            names = [item.text() for item in self.transfer_days.selectedItems()]
            self.settings['transfer_weekdays'] = self._parse_weekdays(','.join(names))

        self.transfer_days.itemSelectionChanged.connect(update_transfer)

        # Add the groups to the layout
        settings_layout.addWidget(surgery_group)
        settings_layout.addWidget(transfer_group)
        
        # Recovery settings group
        recovery_group = QGroupBox(tr(self.messages, 'surgery_planner.group.recovery_settings', 'Recovery Settings'))
        recovery_layout = QFormLayout()
        
        # Recovery days after surgery
        self.recovery_op = QSpinBox()
        self.recovery_op.setRange(0, 365)
        self.recovery_op.setValue(self.settings.get('recovery_op', 60))
        recovery_layout.addRow(
            tr(self.messages, 'surgery_planner.label.recovery_after_surgery', 'Recovery days after surgery:'),
            self.recovery_op
        )
        
        # Recovery days after transfer
        self.recovery_transfer = QSpinBox()
        self.recovery_transfer.setRange(0, 365)
        self.recovery_transfer.setValue(self.settings.get('recovery_transfer', 30))
        recovery_layout.addRow(
            tr(self.messages, 'surgery_planner.label.recovery_after_transfer', 'Recovery days after transfer:'),
            self.recovery_transfer
        )
        
        # Cycle settings
        self.cycle_active_days = QSpinBox()
        self.cycle_active_days.setRange(1, 365)
        self.cycle_active_days.setValue(self.settings.get('cycle_active_days', 60))
        recovery_layout.addRow(
            tr(self.messages, 'surgery_planner.label.active_cycle_days', 'Active cycle days:'),
            self.cycle_active_days
        )
        
        self.cycle_break_days = QSpinBox()
        self.cycle_break_days.setRange(1, 365)
        self.cycle_break_days.setValue(self.settings.get('cycle_break_days', 30))
        recovery_layout.addRow(
            tr(self.messages, 'surgery_planner.label.break_cycle_days', 'Break cycle days:'),
            self.cycle_break_days
        )
        
        recovery_group.setLayout(recovery_layout)
        settings_layout.addWidget(recovery_group)
        
        # Note: Main action buttons moved to bottom button bar with other buttons
        
        # –– Animal Pools section
        animal_group = QGroupBox(tr(self.messages, 'surgery_planner.group.available_animals', 'Available Animals'))
        animal_layout = QVBoxLayout()
        self.animal_table = QFormLayout()
        
        animal_layout.addLayout(self.animal_table)
        animal_group.setLayout(animal_layout)
        
        # Add to the main settings layout
        settings_layout.addWidget(animal_group)
        
        # Populate the animal pools
        self._populate_animal_pools()
        
        # Transfer group already added above, no need to duplicate
        
        # Add stretch to push everything to the top
        settings_layout.addStretch()
        
        # Set the scroll area widget
        scroll.setWidget(settings_widget)
        
        # Add the scroll area to the left layout
        left_layout.addWidget(scroll, stretch=1)  # Use stretch=1 to take remaining space
        
        # Add the left pane to the splitter
        splitter.addWidget(left_widget)
        
        # Right pane: Gantt chart
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        
        # Create matplotlib figure and canvas for Gantt chart
        self.fig = Figure(figsize=(12, 8), dpi=100, facecolor='white')
        self.canvas = FigureCanvas(self.fig)
        self.ax = self.fig.add_subplot(111)
        
        # Initial axis formatting
        self._update_xaxis()
        self.ax.xaxis.set_major_formatter(NullFormatter())
        self.ax.yaxis.set_major_formatter(NullFormatter())
        self.ax.xaxis.set_minor_locator(mdates.DayLocator(interval=1))
        self.ax.xaxis.set_minor_formatter(NullFormatter())
        
        # Add canvas to the right layout
        right_layout.addWidget(self.canvas, stretch=1)
        
        # Add the right pane to the splitter
        splitter.addWidget(right_widget)
        
        # Set initial splitter sizes (left: 1/3, right: 2/3)
        splitter.setSizes([self.width() // 3, 2 * self.width() // 3])
        
        # Add the splitter to the main layout
        main_layout.addWidget(splitter)
        
        # Connect events
        self.canvas.mpl_connect('scroll_event', self._on_scroll)
        self.canvas.mpl_connect('button_press_event', self._on_button_press)
        self.canvas.mpl_connect('button_release_event', self._on_button_release)
        self.canvas.mpl_connect('motion_notify_event', self._on_motion)
        
        # Set the layout
        self.setLayout(main_layout)
        
        # Connect signals for settings updates
        def update_recovery_op(value):
            self.settings['recovery_op'] = value
            
        def update_recovery_transfer(value):
            self.settings['recovery_transfer'] = value
            
        def update_cycle_active_days(value):
            self.settings['cycle_active_days'] = value
            
        def update_cycle_break_days(value):
            self.settings['cycle_break_days'] = value
            
        def update_transfer_offset(value):
            self.settings['transfer_offset_days'] = value
            
        # Connect signals
        self.recovery_op.valueChanged.connect(update_recovery_op)
        self.recovery_transfer.valueChanged.connect(update_recovery_transfer)
        self.cycle_active_days.valueChanged.connect(update_cycle_active_days)
        self.cycle_break_days.valueChanged.connect(update_cycle_break_days)
        self.transfer_offset.valueChanged.connect(update_transfer_offset)

        # manual click-to-edit is now handled in _on_button_press; disable pick_event
        # self.canvas.mpl_connect('pick_event', self._on_dot_clicked)

        # ── Action buttons under chart ───────────────────────────────────
        # Create all buttons including main action buttons
        self.generate_btn = QPushButton(self.messages.get('op_planner.button.generate', 'Generate Schedule'))
        self.generate_btn.clicked.connect(self._generate)
        self.generate_btn.setEnabled(self._can('op_scheduler.use'))
        
        self.optimize_btn = QPushButton(tr(self.messages, 'surgery_planner.button.create_event', 'Create Event'))
        self.optimize_btn.clicked.connect(self._create_manual_event)
        self.optimize_btn.setEnabled(self._can('op_scheduler.use'))
        
        self.save_btn = QPushButton(self.messages.get('op_planner.button.save', 'Save Schedule'))
        self.save_btn.clicked.connect(self._save_schedule)
        self.save_btn.setEnabled(self._can('op_scheduler.use'))
        
        self.invert_fixed_btn = QPushButton(self.messages.get('op_planner.button.invert_fixed', 'Invert Fixed'))
        self.invert_fixed_btn.clicked.connect(self._invert_fixed_events)
        self.invert_fixed_btn.setEnabled(self._can('op_scheduler.use'))
        
        self.fix_all_btn = QPushButton(self.messages.get('op_planner.button.fix_all', 'Fix All'))
        self.fix_all_btn.clicked.connect(self._fix_all_events)
        self.fix_all_btn.setEnabled(self._can('op_scheduler.use'))

        # Export menu system
        self.export_menu_btn = QPushButton(tr(self.messages, 'surgery_planner.button.export_schedule', 'Export Schedule...'))
        self.export_menu_btn.setEnabled(self._can('op_scheduler.use'))
        self.export_menu = QMenu()
        self.export_menu.addAction(tr(self.messages, 'surgery_planner.option.export_png', 'Export as PNG'), self._export_png)
        self.export_menu.addAction(tr(self.messages, 'surgery_planner.option.export_csv', 'Export as CSV'), self._export_csv)
        self.export_menu.addAction(tr(self.messages, 'surgery_planner.option.save_json', 'Save Schedule JSON'), self._save_schedule_as)
        self.export_menu_btn.setMenu(self.export_menu)

        # Layout for all buttons with separators between groups
        btn_layout = QHBoxLayout()
        
        # Main action buttons
        btn_layout.addWidget(self.generate_btn)
        btn_layout.addWidget(self.optimize_btn)
        btn_layout.addWidget(self.save_btn)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        sep1.setLineWidth(1)
        btn_layout.addWidget(sep1)
        
        # Event manipulation buttons
        btn_layout.addWidget(self.invert_fixed_btn)
        btn_layout.addWidget(self.fix_all_btn)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        sep2.setLineWidth(1)
        btn_layout.addWidget(sep2)

        # Export buttons
        btn_layout.addWidget(self.export_menu_btn)
        right_layout.addLayout(btn_layout)

        # add the right pane to the splitter
        splitter.addWidget(right_widget)

        # preserve your original stretch ratios
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)

        # finally add the splitter into the dialog
        main_layout.addWidget(splitter)
        self._apply_permission_state()

    def _on_scroll(self, event):
        """Zoom in/out on wheel scroll, centered under the cursor (x-axis only)."""
        ax = self.ax
        x_min, x_max = ax.get_xlim()
        x_range = x_max - x_min
        # choose zoom factor
        base_scale = 1.2
        if event.button == 'up':
            scale_factor = 1 / base_scale
        else:
            scale_factor = base_scale

        # compute new limits, keeping mouse position fixed
        xdata = event.xdata if event.xdata is not None else (x_min + x_range/2)
        left  = xdata - (xdata - x_min) * scale_factor
        right = xdata + (x_max - xdata) * scale_factor

        ax.set_xlim(left, right)
        # After zoom, re-apply our dynamic ticks + rotation
        self._update_xaxis()
        self.canvas.draw_idle()

    def _on_button_press(self, event):
        """
        Handle mouse press with middle-button panning.
          - Left-click on a dot opens the edit dialog.
          - Middle-click and drag pans the chart.
          - Right-click shows context menu.
        """
        # LEFT-CLICK: manual hit-testing for dots (so zones update with zoom)
        if event.button == 1 and event.inaxes == self.ax and hasattr(self, '_dots_data'):
            threshold_px = 5  # radius in screen pixels
            for idx, meta in enumerate(self._dots_data):
                # convert stored data coords to display coords
                mp_x = mdates.date2num(datetime.fromtimestamp(meta['x']))
                mp_y = meta['y']
                disp_x, disp_y = self.ax.transData.transform((mp_x, mp_y))
                dx = event.x - disp_x
                dy = event.y - disp_y
                if dx*dx + dy*dy <= threshold_px * threshold_px:
                    # simulate a pick_event for our existing handler
                    dummy = type('Event', (), {})()
                    dummy.ind = [idx]
                    self._on_dot_clicked(dummy)
                    return
        
        # MIDDLE-CLICK: start panning
        if event.button == 2 and event.inaxes == self.ax:
            self._pan_active = True
            self._pan_start_x = event.x
            self._pan_start_y = event.y
            self._pan_xlim = self.ax.get_xlim()
            self._pan_ylim = self.ax.get_ylim()
            self.canvas.setCursor(Qt.CursorShape.ClosedHandCursor)
        
        # RIGHT-CLICK: context menu (no pan)
        if event.button == 3 and event.inaxes == self.ax:
            self._show_context_menu(event)

    def _on_button_release(self, event):
        """End pan on middle-button release."""
        if event.button == 2:
            self._pan_active = False
            self.canvas.setCursor(Qt.CursorShape.ArrowCursor)

    def _show_context_menu(self, event):
        """Show context menu on right-click."""
        context_menu = QMenu(self)
        
        # Add context menu actions
        export_action = context_menu.addAction(tr(self.messages, 'surgery_planner.context.export', 'Export Schedule'))
        export_action.triggered.connect(lambda: self.export_menu_btn.showMenu())
        
        refresh_action = context_menu.addAction(tr(self.messages, 'surgery_planner.context.refresh', 'Refresh Chart'))
        refresh_action.triggered.connect(self._render_schedule)
        
        reset_zoom_action = context_menu.addAction(tr(self.messages, 'surgery_planner.context.reset_zoom', 'Reset Zoom'))
        reset_zoom_action.triggered.connect(self._reset_zoom)
        
        # Show the context menu
        context_menu.exec(event.guiEvent.globalPos())

    def _reset_zoom(self):
        """Reset the chart zoom to show all data."""
        if hasattr(self, 'planned') and self.planned:
            # Calculate date range from schedule data
            dates = [entry.date for entry in self.planned]
            if dates:
                min_date = min(dates)
                max_date = max(dates)
                # Add some padding
                padding = timedelta(days=7)
                self.ax.set_xlim(min_date - padding, max_date + padding)
                self._update_xaxis()
                self.canvas.draw_idle()

    def _on_motion(self, event):
        """Handle dragging for pan."""
        if not getattr(self, '_pan_active', False) or event.inaxes != self.ax:
            return
        dx = event.x - self._pan_start_x
        dy = event.y - self._pan_start_y
        w_px, h_px = self.canvas.get_width_height()
        # map pixel delta to data delta
        dx_data = -dx * (self._pan_xlim[1] - self._pan_xlim[0]) / w_px
        # invert vertical input so dragging up moves the chart down (and vice versa)
        dy_data = -dy * (self._pan_ylim[1] - self._pan_ylim[0]) / h_px
        self.ax.set_xlim(self._pan_xlim[0] + dx_data,
                         self._pan_xlim[1] + dx_data)
        self.ax.set_ylim(self._pan_ylim[0] + dy_data,
                         self._pan_ylim[1] + dy_data)
        self.canvas.draw_idle()

    def update_animal_table(self):
        # Populate available animals with performed/allowed counts
        # This replaces the old name-only list and avoids removeRow errors.
        self._populate_animal_pools()

    def _add_block_day(self):
        """
        Toggle the selected date in the calendar as a block day.  If the day
        is already blocked, it will be removed; otherwise it will be added.
        After updating the list, the calendar formatting is refreshed
        to indicate the blocked status using a red text colour.
        """
        try:
            selected_date = self.calendar.selectedDate().toPyDate()
            name = self.block_name.text() or tr(self.messages, 'surgery_planner.default_holiday_name', 'Holiday')
            # Check if the selected date is already blocked
            existing = next((bd for bd in self.block_days if bd.date == selected_date), None)
            if existing:
                # Remove the block day
                self.block_days.remove(existing)
                message = f"Unblocked day: {selected_date}"
                logger.debug(f"Removed block day: {selected_date} - {existing.name}")
            else:
                # Add a new block day entry
                self.block_days.append(BlockDay(selected_date, name))
                message = f"Blocked day: {selected_date}"
                logger.debug(f"Added block day: {selected_date} - {name}")
            # Persist the updated list
            save_block_days(self.block_days, self.backend)
            QMessageBox.information(
                self, 
                self.messages.get('op_planner.info.block_day_updated', 'Block Day Updated'), 
                message
            )
            # Refresh calendar formatting to apply red text on blocked days
            try:
                self._update_calendar_format()
            except Exception:
                pass
        except Exception as e:
            QMessageBox.critical(
                self, 
                self.messages.get('op_planner.error.validation', 'Validation Error'), 
                str(e)
            )
            logger.error(f"Validation error: {e}")
            return False

    def load_schedule_on_startup(self):
        """Load the published schedule, or the current draft, from the backend."""
        data = self._backend_load("schedule", None)
        if data is None:
            data = self._backend_load("draft-schedule", None)
        if data is not None:
            try:
                # Handle both formats: direct list [...] or wrapped {"schedule": [...]}
                if isinstance(data, dict):
                    items = data.get('schedule', [])
                elif isinstance(data, list):
                    items = data
                else:
                    logger.error(f"Unexpected schedule data format: {type(data)}")
                    self.planned = []
                    return
                
                saved = []
                for item in items:
                    # parse date in ISO format
                    try:
                        dt = datetime.fromisoformat(item.get('date', '')).date()
                    except ValueError:
                        try:
                            # Fallback to old format
                            dt = datetime.strptime(item.get('date', ''), DATE_FORMAT).date()
                        except ValueError:
                            continue
                    # Create schedule entry
                    entry = ScheduleEntry(
                        animal=item.get('animal', ''),
                        event_type=item.get('event_type', ''),
                        date_obj=dt,
                        override=item.get('override_weekday', False)
                    )
                    entry.fixed = item.get('fixed', False)
                    entry.entry_id = item.get('id', stable_schedule_id(entry.animal, entry.event_type, entry.date))
                    entry.override_reason = item.get('override_reason', '')
                    entry.created_by = item.get('created_by', 'surgery_planner_plugin_v1.0.0')
                    entry.timestamp = item.get('timestamp', datetime.utcnow().isoformat())
                    saved.append(entry)
                if saved:
                    self.planned = saved
                    logger.info(f"Loaded {len(saved)} schedule entries from backend")
            except Exception as e:
                logger.error(f"Failed to load schedule: {e}")
                self.planned = []
        else:
            logger.info("No existing backend schedule found - starting fresh")
            self.planned = []

    def generate_new_schedule_workflow(self):
        """Generate a preview without writing backend records."""
        self._generate_schedule_to_memory()
        return bool(self.planned)

    def _generate_schedule_to_memory(self):
        """Generate schedule data in memory before writing the staging schedule."""
        self.planned = []
        
        # Get flexible ratio requirements
        donors_per_surgery = self.settings.get('donors_per_surgery', 2)
        surrogates_per_transfer = self.settings.get('surrogates_per_transfer', 2)
        
        # Validate the ratio
        if donors_per_surgery == 0 and surrogates_per_transfer == 0:
            logger.warning("Both donor and surrogate counts are zero - no events scheduled")
            return
        
        # Reuse the central generator so all schedule paths share the same allocation rules.
        self._generate()  # Use existing generation method
        
        logger.info(f"Generated schedule with {len(self.planned)} entries")

    def _load_saved_schedule(self) -> None:
        """Load and render an existing backend schedule, if present."""
        try:
            data = self._backend_load(
                "draft-schedule", self._backend_load("schedule", None)
            )
            if data is None:
                return
            if isinstance(data, list):
                data = {"schedule": data}
            if isinstance(data, dict):
                saved = []
                for item in data.get('schedule', []):
                    # parse date in dd.mm.yyyy
                    try:
                        dt = datetime.strptime(item.get('date', ''), DATE_FORMAT).date()
                    except ValueError:
                        continue
                    # only pass the four supported args
                    entry = ScheduleEntry(
                        animal     = item.get('animal', ''),
                        event_type = item.get('event_type', ''),
                        date_obj   = dt,
                        override   = item.get('override_weekday', False)
                    )
                    entry.fixed = item.get('fixed', True)
                    entry.entry_id = item.get('id', stable_schedule_id(entry.animal, entry.event_type, entry.date))
                    entry.override_reason = item.get('override_reason', '')
                    saved.append(entry)
                if saved:
                    self.planned = saved
                    self._render_schedule()
        except Exception as e:
            logger.error(f"Failed to load saved schedule: {e}")

    def _render_schedule(self) -> None:
        """Draw self.planned onto the Gantt chart."""
        logger.debug(f"_render_schedule called. Has planned: {hasattr(self, 'planned')}, Length: {len(getattr(self, 'planned', []))}")
        
        # Clear the current figure to ensure a clean slate
        self.ax.clear()
        
        # If there are no events to render, clear chart and notify
        if not hasattr(self, 'planned') or not self.planned:
            logger.debug("No planned events to render")
            self.canvas.draw()
            # Don't show the message box during normal operation
            return
        # draw each entry, fixed events get black outlines
        # determine horizon
        dates = [e.date for e in self.planned]
        start, end = min(dates), max(dates)
        self.ax.set_xlim(mdates.date2num(start), mdates.date2num(end))
        self._update_xaxis()
        self._draw_month_background(start, end)

        # layout animals - sort by role (donors first, then surrogates)
        all_animals = {e.animal for e in self.planned}
        animal_roles = {a['name']: _animal_role_value(a) for a in self.animals}
        donors_names = sorted([n for n in all_animals if animal_roles.get(n) == ROLE_VALUE_SPENDER])
        surrogates_names = sorted([n for n in all_animals if animal_roles.get(n) == ROLE_VALUE_AMME])
        names = donors_names + surrogates_names  # Donors at top, surrogates below
        y_spacing = 1.0 / max(len(names), 1)
        y_positions = {n:(i+0.5)*y_spacing for i,n in enumerate(names)}

        # Clear any existing dots and bars
        if hasattr(self, '_dots_data'):
            self._dots_data.clear()
        else:
            self._dots_data = []
            
        # Prepare dot metadata so loaded schedules become pickable
        for e in self.planned:
            y = y_positions[e.animal]
            # compute a true timestamp (seconds since epoch) for this date
            ts   = time.mktime(datetime.combine(e.date, datetime.min.time()).timetuple())
            md_x = mdates.date2num(datetime.fromtimestamp(ts))
            # match the same colour scheme as _generate
            if e.event_type == 'op':
                dot_color = 'red'
                bar_color = 'firebrick'
                rec       = self.settings.get('recovery_op', 0)
            else:
                dot_color = 'orange'
                bar_color = 'green'
                rec       = self.settings.get('recovery_transfer', 0)
            # fixed events get a black edge on the bar
            if getattr(e, 'fixed', False):
                self.ax.broken_barh(
                    [(md_x, rec)],
                    (y - 0.0125, 0.025),
                    facecolors=bar_color,
                    edgecolors='black',
                    linewidth=1
                )
            else:
                self.ax.broken_barh(
                    [(md_x, rec)],
                    (y - 0.0125, 0.025),
                    facecolors=bar_color
                )
            # stash metadata with ts so hit-testing works the same as _generate
            self._dots_data.append({'x': ts, 'y': y, 'color': dot_color, 'entry': e})
            # plot the dot; fixed events get black outline
            dot_kwargs = {'c': dot_color, 's': 10, 'picker': True}
            if getattr(e, 'fixed', False):
                dot_kwargs.update({'edgecolors': 'black', 'linewidths': 1})
            self.ax.scatter([md_x], [y], **dot_kwargs)

        # y-axis labels
        # Set y-ticks and labels
        if names:  # Only set ticks if we have names to display
            self.ax.set_yticks([y_positions[n] for n in names])
            self.ax.set_yticklabels([animal_base_name(n) for n in names])
        
        # Force a full redraw of the canvas
        try:
            logger.debug(f"Drawing canvas with {len(self._dots_data)} dots")
            self.canvas.draw()
            logger.debug("Canvas drawn successfully")
        except Exception as e:
            logger.error(f"Error drawing canvas: {e}")

    def _populate_animal_pools(self):
        """Fill the 'Verfügbare Tiere' form with checkboxes and performed/allowed counts.
        Shows ALL animals with allowed > 0, sorted by role (donors first, then surrogates).
        Auto-unchecks animals with any status (pregnant, sick, recovery, etc.)."""
        # Clear any existing rows
        while self.animal_table.rowCount():
            self.animal_table.removeRow(0)
        
        self.animal_checkboxes.clear()
        self.checked_animals.clear()
        
        # Sort animals by role: donors first, then surrogates
        donors = [a for a in self.animals if _animal_role_value(a) == ROLE_VALUE_SPENDER]
        surrogates = [a for a in self.animals if _animal_role_value(a) == ROLE_VALUE_AMME]
        sorted_animals = sorted(donors, key=lambda x: x.get('name', '')) + sorted(surrogates, key=lambda x: x.get('name', ''))

        # For each animal, compute performed vs. allowed
        for a in sorted_animals:
            name = a.get('name')
            role = _animal_role_value(a)
            status = a.get('status', '')
            
            if role == ROLE_VALUE_SPENDER:
                performed = sum(
                    1 for event in a.get('events', []) or []
                    if isinstance(event, dict) and event.get('typ') == 'surgery'
                )
                allowed   = int(a.get('OP_max', 0))
                label_txt = f"{performed}/{allowed} {tr(self.messages, 'surgery_planner.label.operations_short', 'OPs')}"
                role_label = tr(self.messages, 'surgery_planner.role.donor', 'Donor')
            else:
                performed = sum(
                    1 for event in a.get('events', []) or []
                    if isinstance(event, dict) and event.get('typ') == 'embryo_transfer'
                )
                allowed   = int(a.get('Embryo_max', 0))
                label_txt = f"{performed}/{allowed} {tr(self.messages, 'surgery_planner.label.embryo_transfers_short', 'ETs')}"
                role_label = tr(self.messages, 'surgery_planner.role.surrogate', 'Surrogate')

            # Show all animals with allowed > 0
            if allowed > 0:
                # Create checkbox with animal name
                checkbox = QCheckBox(f"{name} ({role_label})")
                checkbox.setObjectName(name)
                
                # Determine checked state - auto-check if status is empty (normal/available)
                if not status:  # Empty status means normal/available
                    checkbox.setChecked(True)
                    self.checked_animals.add(name)
                else:
                    checkbox.setChecked(False)  # Uncheck if any status (pregnant, sick, recovery, etc.)
                
                # Add status indicator if excluded (has any status)
                if status:
                    checkbox.setStyleSheet("QCheckBox { color: gray; }")
                    checkbox.setToolTip(tr(self.messages, 'surgery_planner.tooltip.auto_excluded', 
                                          'Auto-excluded: {status}').format(status=status))
                
                # Connect checkbox to update checked_animals
                checkbox.stateChanged.connect(lambda state, n=name: self._on_animal_checkbox_changed(n, state))
                checkbox.setEnabled(self._can_edit_schedule())
                
                self.animal_checkboxes[name] = checkbox
                
                # Status label
                lbl_status = QLabel(label_txt)
                lbl_status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if status:
                    lbl_status.setStyleSheet("QLabel { color: gray; }")
                
                self.animal_table.addRow(checkbox, lbl_status)
    
    def _on_animal_checkbox_changed(self, animal_name: str, state: int):
        """Handle checkbox state change for an animal."""
        if state == 2:  # Qt.CheckState.Checked
            self.checked_animals.add(animal_name)
        else:
            self.checked_animals.discard(animal_name)
        # State updated for current session only (not persisted)

    # ─── Bulk invert-fixed handler ─────────────────────────────────────────
    def _invert_fixed_events(self):
        """Invert fixed status on all events and update the temp JSON."""
        for e in getattr(self, 'planned', []):
            e.fixed = not e.fixed
        self._persist_temp_schedule()
        self._render_schedule()
        QMessageBox.information(
            self, 
            self.messages.get('op_planner.info.invert_fixed', 'Invert Fixed'), 
            self.messages.get('op_planner.info.fixed_flags_inverted', 'Fixed flags inverted.')
        )

    def _fix_all_events(self):
        """Mark every scheduled event as fixed, persist, and redraw."""
        for e in getattr(self, 'planned', []):
            e.fixed = True
        self._persist_temp_schedule()
        self._render_schedule()
        QMessageBox.information(
            self, 
            self.messages.get('op_planner.info.fix_all', 'Fix All'), 
            self.messages.get('op_planner.info.all_events_fixed', 'All events have been fixed.')
        )

    def _persist_temp_schedule(self):
        """Persist the editable schedule draft in the backend."""
        try:
            temp_out = {'schedule': []}
            for entry in getattr(self, 'planned', []):
                temp_out['schedule'].append(schedule_entry_to_dict(entry, date_format=DATE_FORMAT))
            self._backend_save("draft-schedule", temp_out)
            logger.debug(f"Persisted {len(temp_out['schedule'])} events to temp schedule")
        except Exception as ex:
            logger.error(f"Failed to persist temp schedule: {ex}")

       
    def _validate_event(self, entry: ScheduleEntry, animals: list[dict]) -> bool:
        try:
            animal_data = next((a for a in animals if a['name'] == entry.animal), {})
            events = animal_data.get('events', [])
            recovery_days = self.settings['recovery_transfer'] if entry.event_type == 'embryoübertragung' else self.settings['recovery_op']
            
            # Check recovery period against existing events in animal data
            for evt in events:
                # Check if date field exists and is valid
                if 'date' not in evt:
                    continue
                try:
                    evt_date = datetime.strptime(evt['date'], DATE_FORMAT).date()
                except (ValueError, TypeError) as e:
                    logger.warning(f"Skipping invalid date format in event: {evt.get('date', 'missing')} - {e}")
                    continue
                
                # Check if new event falls within recovery period of existing event
                recovery_end = evt_date + timedelta(days=recovery_days)
                if evt_date <= entry.date < recovery_end:
                    QMessageBox.warning(
                        self, 
                        tr(self.messages, 'surgery_planner.warning.recovery_conflict_title', 'Recovery Period Conflict'), 
                        tr(self.messages, 'surgery_planner.warning.recovery_conflict', 
                           '{animal} is in recovery until {recovery_end}. Events cannot be scheduled during recovery periods.').format(
                            animal=entry.animal, recovery_end=recovery_end.strftime(DATE_FORMAT))
                    )
                    logger.warning(f"Validation failed: {entry.animal} in recovery until {recovery_end}")
                    return False
            
            # Also check recovery period against planned events
            for planned_entry in self.planned:
                if planned_entry.animal != entry.animal:
                    continue
                if planned_entry == entry:  # Skip self
                    continue
                
                planned_recovery_days = self.settings['recovery_transfer'] if planned_entry.event_type == 'embryoübertragung' else self.settings['recovery_op']
                planned_recovery_end = planned_entry.date + timedelta(days=planned_recovery_days)
                
                # Check if new event falls within recovery period of planned event
                if planned_entry.date <= entry.date < planned_recovery_end:
                    QMessageBox.warning(
                        self, 
                        tr(self.messages, 'surgery_planner.warning.recovery_conflict_title', 'Recovery Period Conflict'), 
                        tr(self.messages, 'surgery_planner.warning.recovery_conflict', 
                           '{animal} is in recovery until {recovery_end}. Events cannot be scheduled during recovery periods.').format(
                            animal=entry.animal, recovery_end=planned_recovery_end.strftime(DATE_FORMAT))
                    )
                    logger.warning(f"Validation failed: {entry.animal} in planned recovery until {planned_recovery_end}")
                    return False
                
                # Check if new event's recovery would overlap with existing planned event
                new_recovery_end = entry.date + timedelta(days=recovery_days)
                if entry.date <= planned_entry.date < new_recovery_end:
                    QMessageBox.warning(
                        self, 
                        tr(self.messages, 'surgery_planner.warning.recovery_conflict_title', 'Recovery Period Conflict'), 
                        tr(self.messages, 'surgery_planner.warning.recovery_conflict_planned', 
                           '{animal} has a scheduled event on {planned_date} which falls within the recovery period of this event (until {recovery_end}).').format(
                            animal=entry.animal, planned_date=planned_entry.date.strftime(DATE_FORMAT), recovery_end=new_recovery_end.strftime(DATE_FORMAT))
                    )
                    logger.warning(f"Validation failed: planned event on {planned_entry.date} conflicts with recovery until {new_recovery_end}")
                    return False
            # Block-day check: only enforce if "Ignore Blocked Dates" is unchecked
            block_days_set = {bd.date for bd in self.block_days}
            is_block_day = entry.date in block_days_set
            
            if is_block_day and not entry.override_weekday:
                msg = f"{entry.date} is a block day. Check 'Ignore Blocked Dates' to override."
                QMessageBox.warning(
                    self, 
                        self.messages.get('op_planner.warning.conflict', 'Conflict'), 
                        msg
                    )
                logger.warning(f"Block day validation failed: {entry.date}, override={entry.override_weekday}")
                return False
            elif is_block_day and entry.override_weekday:
                logger.info(f"Block day override allowed: {entry.date}")
            
            # Weekday check: only enforce if "Ignore Blocked Dates" is unchecked
            if not entry.override_weekday:
                valid_days = (
                    self.settings['transfer_weekdays']
                    if entry.event_type == 'embryoübertragung'
                    else self.settings['surgery_weekdays']
                )
                if entry.date.weekday() not in valid_days:
                    msg = self.messages.get('op_planner.warning.not_surgery_weekday', 'Not a surgery Weekday!') if entry.event_type=='op' \
                          else self.messages.get('op_planner.warning.not_transfer_weekday', 'Not an allowed transfer weekday!')
                    msg += "\nCheck 'Ignore Blocked Dates' to override."
                    QMessageBox.warning(
                        self, 
                        self.messages.get('op_planner.warning.conflict', 'Conflict'), 
                        msg
                    )
                    logger.warning(f"Weekday validation failed: {entry.date}, weekday={entry.date.weekday()}, valid_days={valid_days}, override={entry.override_weekday}")
                    return False
            else:
                logger.info(f"Weekday override allowed: {entry.date} (weekday {entry.date.weekday()})")

            # all validations passed
            return True

        except Exception as e:
            logger.error(f"Unexpected error validating {entry}: {e}")
            return False

    def _find_best_completion_for_fixed_group(self, dt: date, fixed_animals: set, 
                                              needed_donors: int, donors: list,
                                              end_date: date, surgery_days: set, 
                                              blocks: set, fixed_op_windows: dict) -> Optional[list]:
        """
        Find additional donors that complete the fixed group while minimizing 
        disruption to future complete sets.
        
        IMPORTANT: When completing a fixed event group, this function IGNORES 
        block days and weekday constraints, but RESPECTS recovery periods.
        This is the special rule for fixed event completion only.
        
        Args:
            dt: Target date for the fixed group
            fixed_animals: Set of animal names already in fixed surgeries on this date
            needed_donors: Number of additional donors needed
            donors: List of donor dictionaries with 'animal', 'remaining', 'performed', 'next'
            end_date: End of scheduling horizon
            surgery_days: Valid weekdays for surgeries (NOT USED - ignored for fixed groups)
            blocks: Set of blocked dates (NOT USED - ignored for fixed groups)
            fixed_op_windows: Recovery windows for fixed operations
            
        Returns:
            List of selected donor dictionaries, or None if no valid selection exists
        """
        from itertools import combinations
        
        donors_per_surgery = self.settings.get('donors_per_surgery', 2)
        
        # Get all available donors for this date
        # NOTE: Block days and weekday constraints are INTENTIONALLY IGNORED here
        # This is the special rule for completing fixed event groups
        candidates = [
            d for d in donors
            if d['remaining'] > 0
            and d['animal']['name'] not in fixed_animals
            and dt >= d['next']  # Respect recovery periods
            and not any(st <= dt < en for st, en in fixed_op_windows.get(d['animal']['name'], []))  # Respect recovery windows
            # NO checks for: dt in blocks, dt.weekday() in surgery_days
            # Block/weekday constraints are ignored when completing fixed groups
        ]
        
        if len(candidates) < needed_donors:
            return None  # Cannot complete the group
        
        # Limit candidates to top 10 by workload to prevent combinatorial explosion
        candidates = sorted(candidates, key=lambda x: x['performed'], reverse=True)[:10]
        
        if len(candidates) < needed_donors:
            return None  # Still not enough after limiting
        
        # Score each possible combination
        best_score = float('-inf')
        best_selection = None
        
        for combo in combinations(candidates, needed_donors):
            # Calculate remaining donors after this selection
            remaining_donors = [d for d in candidates if d not in combo]
            
            # Count how many complete sets can be formed from remaining donors
            future_complete_sets = len(remaining_donors) // donors_per_surgery
            
            # Count orphaned animals (cannot form complete sets)
            orphaned_count = len(remaining_donors) % donors_per_surgery
            
            # Prefer animals with more procedures already done (balance workload)
            workload_balance = sum(d['performed'] for d in combo)
            
            # Check if remaining animals can be rescheduled to form complete sets
            can_reschedule_orphans = self._can_reschedule_orphans(
                remaining_donors[-orphaned_count:] if orphaned_count > 0 else [],
                dt, end_date, surgery_days, blocks, donors_per_surgery
            )
            
            # Calculate score
            score = (
                future_complete_sets * 100 +  # Prioritize preserving future complete sets
                (50 if can_reschedule_orphans else 0) -  # Bonus if orphans can be rescheduled
                (orphaned_count * 30) +  # Penalize creating orphans
                workload_balance  # Slight preference for balancing workload
            )
            
            if score > best_score:
                best_score = score
                best_selection = combo
        
        return list(best_selection) if best_selection else None

    def _can_reschedule_orphans(self, orphaned_animals: list, current_date: date,
                                end_date: date, surgery_days: set, blocks: set,
                                donors_per_surgery: int) -> bool:
        """
        Check if orphaned animals can potentially be rescheduled to form complete sets.
        
        Args:
            orphaned_animals: List of donor dictionaries that would be orphaned
            current_date: Current scheduling date
            end_date: End of scheduling horizon
            surgery_days: Valid weekdays for surgeries
            blocks: Set of blocked dates
            donors_per_surgery: Number of donors needed per surgery
            
        Returns:
            True if orphans can potentially be rescheduled, False otherwise
        """
        if not orphaned_animals:
            return True
        
        # Look ahead up to 30 days to see if these animals could join a complete set
        for days_offset in range(1, 31):
            candidate_date = current_date + timedelta(days=days_offset)
            
            if (candidate_date > end_date or
                candidate_date.weekday() not in surgery_days or
                candidate_date in blocks):
                continue
            
            # Check if all orphaned animals are available on this date
            all_available = all(
                candidate_date >= d['next']
                for d in orphaned_animals
            )
            
            if all_available:
                # If the number of orphans matches the group size, they can form a complete set
                if len(orphaned_animals) == donors_per_surgery:
                    return True
                # If orphans are less than group size, they might join other animals
                elif len(orphaned_animals) < donors_per_surgery:
                    return True  # Optimistic assumption
        
        return False

    def _validate_schedule_completeness(self, planned: list) -> list:
        """
        Check if the generated schedule has incomplete sets and return warnings.
        
        Args:
            planned: List of ScheduleEntry objects
            
        Returns:
            List of warning strings for incomplete sets
        """
        warnings = []
        
        # Group surgeries by date
        surgeries_by_date = {}
        transfers_by_date = {}
        
        for entry in planned:
            if entry.event_type == 'op':
                surgeries_by_date.setdefault(entry.date, []).append(entry)
            elif entry.event_type == 'embryoübertragung':
                transfers_by_date.setdefault(entry.date, []).append(entry)
        
        # Check surgery dates
        donors_per_surgery = self.settings.get('donors_per_surgery', 2)
        for dt, surgeries in surgeries_by_date.items():
            if len(surgeries) % donors_per_surgery != 0:
                warnings.append({
                    'date': dt,
                    'type': 'surgery',
                    'count': len(surgeries),
                    'expected': donors_per_surgery,
                    'animals': [s.animal for s in surgeries]
                })
        
        # Check transfer dates
        surrogates_per_transfer = self.settings.get('surrogates_per_transfer', 2)
        for dt, transfers in transfers_by_date.items():
            if len(transfers) % surrogates_per_transfer != 0:
                warnings.append({
                    'date': dt,
                    'type': 'transfer',
                    'count': len(transfers),
                    'expected': surrogates_per_transfer,
                    'animals': [t.animal for t in transfers]
                })
        
        return warnings

    def _find_alternative_date_for_animal(self, animal_data: dict, original_date: date,
                                          end_date: date, surgery_days: set, blocks: set,
                                          donors: list, fixed_op_windows: dict,
                                          event_type: str = 'op') -> Optional[date]:
        """
        Attempt to find an alternative date where this animal can join a complete set.
        
        Args:
            animal_data: Dictionary with animal information
            original_date: Original proposed date
            end_date: End of scheduling horizon
            surgery_days: Valid weekdays for surgeries
            blocks: Set of blocked dates
            donors: List of all donor dictionaries
            fixed_op_windows: Recovery windows for fixed operations
            event_type: 'op' or 'embryoübertragung'
            
        Returns:
            Alternative date if found, None otherwise
        """
        animal_name = animal_data['animal']['name']
        group_size = (self.settings.get('donors_per_surgery', 2) 
                      if event_type == 'op' 
                      else self.settings.get('surrogates_per_transfer', 2))
        
        # Look ahead for dates where adding this animal completes a set
        for days_offset in range(1, 45):  # Look 45 days ahead
            candidate_date = original_date + timedelta(days=days_offset)
            
            if (candidate_date > end_date or
                candidate_date.weekday() not in surgery_days or
                candidate_date in blocks):
                continue
            
            # Check if animal is available on this date
            if candidate_date < animal_data['next']:
                continue
            
            # Check recovery windows
            if any(st <= candidate_date < en 
                   for st, en in fixed_op_windows.get(animal_name, [])):
                continue
            
            # Count how many animals are available on this date
            available = [
                d for d in donors
                if d['remaining'] > 0
                and d['animal']['name'] != animal_name
                and candidate_date >= d['next']
                and not any(st <= candidate_date < en 
                           for st, en in fixed_op_windows.get(d['animal']['name'], []))
            ]
            
            # If adding this animal completes a set, use this date
            if (len(available) + 1) % group_size == 0:
                logger.info(f"Found alternative date {candidate_date} for {animal_name} "
                           f"(completes set with {len(available)} other animals)")
                return candidate_date
        
        return None

    def _generate(self):
        try:
            # Clear previous Gantt chart and re-apply tick formatting
            self.ax.clear()
            self._update_xaxis()

            # Validate animal data
            if not self.animals:
                QMessageBox.warning(
                    self, 
                    self.messages.get('op_planner.warning.no_animals', 'No Animals'), 
                    self.messages.get('op_planner.warning.no_animals_found', 'No animals found')
                )
                logger.warning("No animals found for scheduling")
                return

            # Parse weekday selections
            surgery_names = [item.text() for item in self.surgery_days.selectedItems()]
            transfer_names = [item.text() for item in self.transfer_days.selectedItems()]
            surgery_days = self._parse_weekdays(','.join(surgery_names))
            transfer_days = self._parse_weekdays(','.join(transfer_names))
            if not surgery_days:
                QMessageBox.warning(
                    self, 
                    self.messages.get('op_planner.warning.no_surgery_weekdays', 'No Surgery Weekdays Selected'),
                    self.messages.get('op_planner.warning.select_surgery_weekday', 'Please select at least one surgery weekday.')
                )
                return
            if not transfer_days:
                QMessageBox.warning(
                    self, 
                    self.messages.get('op_planner.warning.no_transfer_weekdays', 'No Transfer Weekdays Selected'),
                    self.messages.get('op_planner.warning.select_transfer_weekday', 'Please select at least one transfer weekday.')
                )
                return
            self.settings['surgery_weekdays'] = surgery_days
            self.settings['transfer_weekdays'] = transfer_days
            self.settings['recovery_op'] = self.recovery_op.value()
            self.settings['recovery_transfer'] = self.recovery_transfer.value()
            self.settings['cycle_active_days'] = self.cycle_active_days.value()
            self.settings['cycle_break_days'] = self.cycle_break_days.value()
            self.settings['transfer_offset_days'] = self.transfer_offset.value()
            # Read animal group sizes from spinboxes
            self.settings['donors_per_surgery'] = self.donors_per_surgery.value()
            self.settings['surrogates_per_transfer'] = self.surrogates_per_transfer.value()

            # Parse planning horizon
            try:
                start_date = datetime.strptime(self.horizon_start.text(), DATE_FORMAT).date()
            except Exception:
                start_date = date.today()
            try:
                end_date = datetime.strptime(self.horizon_end.text(), DATE_FORMAT).date()
            except Exception:
                end_date = start_date + timedelta(days=365)
            if start_date > end_date:
                QMessageBox.critical(
                    self, 
                    self.messages.get('op_planner.error.invalid_date_range', 'Invalid Date Range'),
                    self.messages.get('op_planner.error.end_after_start', 'End date must be after start date')
                )
                logger.error("Invalid date range: start_date > end_date")
                return

            self.planner_snapshot = PlannerSnapshot.from_inputs(self.animals, self.settings, start_date, end_date, (bd.date for bd in self.block_days), getattr(self, 'planned', ()))

            # Initialize fixed events and planned list
            blocks = {bd.date for bd in self.block_days}
            fixed_events = [e for e in getattr(self, 'planned', []) if getattr(e, 'fixed', False)]
            planned: list[ScheduleEntry] = fixed_events.copy()

            # Build recovery windows for fixed events
            fixed_op_windows = {}
            fixed_tr_windows = {}
            for e in fixed_events:
                if e.event_type == 'op':
                    rec_days = self.settings['recovery_op']
                    start = e.date
                    end = e.date + timedelta(days=rec_days)
                    fixed_op_windows.setdefault(e.animal, []).append((start, end))
                elif e.event_type == 'embryoübertragung':
                    rec_days = self.settings['recovery_transfer']
                    start = e.date
                    end = e.date + timedelta(days=rec_days)
                    fixed_tr_windows.setdefault(e.animal, []).append((start, end))

            # Build capacity lists with performed counts - only include CHECKED animals
            donors, surrogates = [], []
            for a in self.animals:
                name = a.get('name')
                # Skip animals that are not checked
                if name not in self.checked_animals:
                    continue
                    
                role = _animal_role_value(a)
                if role == ROLE_VALUE_SPENDER:
                    total_allowed = int(a.get('OP_max', 0))
                    performed = _canonical_event_count(a, 'surgery')
                    used_fixed = sum(1 for e in fixed_events
                                     if e.event_type == 'op' and e.animal == name)
                    remaining = max(total_allowed - performed - used_fixed, 0)
                    donors.append({
                        'animal': a,
                        'remaining': remaining,
                        'performed': performed,
                        'next': start_date
                    })
                elif role == ROLE_VALUE_AMME:
                    total_allowed = int(a.get('Embryo_max', 0))
                    performed = _canonical_event_count(a, 'embryo_transfer')
                    used_fixed = sum(1 for e in fixed_events
                                     if e.event_type == 'embryoübertragung' and e.animal == name)
                    remaining = max(total_allowed - performed - used_fixed, 0)
                    surrogates.append({
                        'animal': a,
                        'remaining': remaining,
                        'performed': performed,
                        'next': start_date
                    })

            # Validate that enough animals are available for the selected group sizes
            donors_per_surgery = self.settings.get('donors_per_surgery', 2)
            surrogates_per_transfer = self.settings.get('surrogates_per_transfer', 2)
            available_donors = len([d for d in donors if d['remaining'] > 0])
            available_surrogates = len([s for s in surrogates if s['remaining'] > 0])
            
            if donors_per_surgery > 0 and available_donors < donors_per_surgery:
                QMessageBox.warning(
                    self,
                    tr(self.messages, 'surgery_planner.warning.insufficient_animals_title', 'Insufficient Animals'),
                    tr(self.messages, 'surgery_planner.warning.insufficient_donors', 
                       'Cannot generate schedule: {required} donors required per surgery, but only {available} donors with remaining capacity are available.').format(
                        required=donors_per_surgery, available=available_donors)
                )
                logger.warning(f"Insufficient donors: need {donors_per_surgery}, have {available_donors}")
                return
            
            if surrogates_per_transfer > 0 and available_surrogates < surrogates_per_transfer:
                QMessageBox.warning(
                    self,
                    tr(self.messages, 'surgery_planner.warning.insufficient_animals_title', 'Insufficient Animals'),
                    tr(self.messages, 'surgery_planner.warning.insufficient_surrogates', 
                       'Cannot generate schedule: {required} surrogates required per transfer, but only {available} surrogates with remaining capacity are available.').format(
                        required=surrogates_per_transfer, available=available_surrogates)
                )
                logger.warning(f"Insufficient surrogates: need {surrogates_per_transfer}, have {available_surrogates}")
                return

            # Prepare y positions for Gantt chart - sort by role (donors first, then surrogates)
            donors_names = sorted([a['name'] for a in self.animals if _animal_role_value(a) == ROLE_VALUE_SPENDER])
            surrogates_names = sorted([a['name'] for a in self.animals if _animal_role_value(a) == ROLE_VALUE_AMME])
            names = donors_names + surrogates_names  # Donors at top, surrogates below
            y_spacing = 1.0 / max(len(names), 1)
            y_positions = {name: (i + 0.5) * y_spacing for i, name in enumerate(names)}

            # Initialize plotting lists
            dots: list[dict] = []
            bars: list[tuple] = []
            for e in fixed_events:
                y = y_positions[e.animal]
                ts = time.mktime(datetime.combine(e.date, datetime.min.time()).timetuple())
                rec_days = (self.settings['recovery_op']
                            if e.event_type == 'op'
                            else self.settings['recovery_transfer'])
                dot_color = QColor('red') if e.event_type == 'op' else QColor('orange')
                bar_color = QColor('firebrick') if e.event_type == 'op' else QColor('green')
                bars.append((ts, y, rec_days * self.day_width, 0.025, bar_color))
                dots.append({'x': ts, 'y': y, 'color': dot_color, 'entry': e})
            self.dot_items = []

            # Simple per-day scheduler with ultra-flexible ratios
            offset_days = self.settings.get('transfer_offset_days', 6)
            donors_per_surgery = self.settings.get('donors_per_surgery', 2)
            surrogates_per_transfer = self.settings.get('surrogates_per_transfer', 2)
            
            # Cycle settings
            cycle_active = self.settings.get('cycle_active_days', 60)
            cycle_break = self.settings.get('cycle_break_days', 30)
            
            def is_in_active_cycle(animal_data, target_date):
                """Check if the target date falls within an active cycle for the animal."""
                # Get the animal's previous operations/transfers
                animal = animal_data['animal']
                name = animal.get('name')
                
                # Get all previous events for this animal (from data + planned)
                previous_events = []
                
                # Add events from animal data
                if _animal_role_value(animal_data.get('animal', {})) == ROLE_VALUE_SPENDER:
                    ops = _canonical_event_dates(animal, 'surgery')
                    for op_date_str in ops:
                        op_date = _as_planner_date(op_date_str)
                        if op_date is not None:
                            previous_events.append(op_date)
                else:
                    transfers = _canonical_event_dates(animal, 'embryo_transfer')
                    for tr_date_str in transfers:
                        tr_date = _as_planner_date(tr_date_str)
                        if tr_date is not None:
                            previous_events.append(tr_date)
                
                # Add already planned events for this animal
                for entry in planned:
                    if entry.animal == name:
                        previous_events.append(entry.date)
                
                if not previous_events:
                    return True  # No previous events, so we're in active cycle
                
                # Find the most recent event
                last_event = max(previous_events)
                days_since_last = (target_date - last_event).days
                
                # Check if we're in active cycle or break period
                cycle_length = cycle_active + cycle_break
                days_in_current_cycle = days_since_last % cycle_length
                
                is_active = days_in_current_cycle < cycle_active
                logger.debug(f"Animal {name}: {days_since_last} days since last event, "
                           f"cycle position: {days_in_current_cycle}/{cycle_length}, "
                           f"active: {is_active}")
                return is_active
            
            # Determine operation mode
            linked_mode = donors_per_surgery > 0 and surrogates_per_transfer > 0
            
            # First pass: Process all fixed events and complete their groups
            fixed_event_groups = []
            for dt in (start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)):
                fixed_surgeries_today = [e for e in fixed_events if e.date == dt and e.event_type == 'op']
                
                if fixed_surgeries_today:
                    logger.info(f"Found {len(fixed_surgeries_today)} fixed surgeries on {dt} - forming complete groups")
                    
                    # Form complete groups around fixed surgeries (override block/weekday constraints but respect recovery)
                    total_fixed_surgeries = len(fixed_surgeries_today)
                    
                    # Check if fixed surgeries alone can form complete groups
                    if total_fixed_surgeries >= donors_per_surgery:
                        # Fixed surgeries alone can form one or more complete groups
                        num_complete_groups = total_fixed_surgeries // donors_per_surgery
                        remaining_fixed = total_fixed_surgeries % donors_per_surgery
                        
                        if remaining_fixed == 0:
                            # Perfect fit - fixed surgeries form complete groups
                            logger.info(f"Fixed surgery group: {total_fixed_surgeries} fixed surgeries form {num_complete_groups} complete groups on {dt}")
                            additional_donors = []
                        else:
                            # Fixed surgeries exceed group size or don't divide evenly
                            logger.warning(f"Fixed surgery group: {total_fixed_surgeries} fixed surgeries don't divide evenly into groups of {donors_per_surgery} on {dt} - SKIPPING")
                            continue
                    else:
                        # Need additional donors to complete the group
                        needed_donors = donors_per_surgery - total_fixed_surgeries
                        logger.info(f"Fixed surgery group: Need {needed_donors} additional donors on {dt}")
                        
                        # Get animals already used in fixed surgeries to avoid duplicates
                        fixed_animals = {e.animal for e in fixed_surgeries_today}
                        
                        # Filter donors by cycle settings before passing to completion function
                        donors_in_active_cycle = [d for d in donors if is_in_active_cycle(d, dt)]
                        
                        # Use set-aware selection instead of greedy selection
                        additional_donors = self._find_best_completion_for_fixed_group(
                            dt=dt,
                            fixed_animals=fixed_animals,
                            needed_donors=needed_donors,
                            donors=donors_in_active_cycle,
                            end_date=end_date,
                            surgery_days=surgery_days,
                            blocks=blocks,
                            fixed_op_windows=fixed_op_windows
                        )
                        
                        if additional_donors and len(additional_donors) >= needed_donors:
                            logger.info(f"Fixed surgery group: Found optimal {len(additional_donors)} donors "
                                       f"using set-aware selection: {[d['animal']['name'] for d in additional_donors]}")
                        else:
                            logger.warning(
                                "Fixed surgery group: Could not find optimal donor selection "
                                "that preserves future complete sets - SKIPPING this fixed group"
                            )
                            continue
                    
                    # Only store the fixed group if it can be completed
                    fixed_group = {
                        'date': dt,
                        'fixed_surgeries': fixed_surgeries_today,
                        'additional_donors': additional_donors
                    }
                    fixed_event_groups.append(fixed_group)
                    
                    # CRITICAL: Immediately update recovery tracking for additional donors
                    # This prevents them from being scheduled again during recovery period
                    rec_days = self.settings['recovery_op']
                    for d in additional_donors:
                        donor_name = d['animal']['name']
                        d['next'] = dt + timedelta(days=rec_days)  # Update next available date
                        d['remaining'] -= 1  # Decrease remaining capacity
                        # Add to recovery windows tracking
                        recovery_start = dt
                        recovery_end = dt + timedelta(days=rec_days)
                        fixed_op_windows.setdefault(donor_name, []).append((recovery_start, recovery_end))
                        logger.debug(f"Fixed group completion: Updated {donor_name} recovery window to {recovery_start} - {recovery_end}")
                    
                    # Enhanced logging for fixed group
                    logger.info(f"Fixed group on {dt}: "
                               f"Fixed surgeries: {[s.animal for s in fixed_surgeries_today]}, "
                               f"Additional donors: {[d['animal']['name'] for d in additional_donors]}, "
                               f"Total: {len(fixed_surgeries_today) + len(additional_donors)} animals")
                    
                    # Schedule transfers exactly offset_days after the fixed surgery date (override constraints)
                    if linked_mode and surrogates_per_transfer > 0:
                        transfer_dt = dt + timedelta(days=offset_days)
                        
                        # Calculate how many surgery groups we have (each needs surrogates_per_transfer surrogates)
                        total_surgeries = len(fixed_surgeries_today) + len(additional_donors)
                        num_surgery_groups = total_surgeries // donors_per_surgery if donors_per_surgery > 0 else 0
                        surrogates_needed = num_surgery_groups * surrogates_per_transfer
                        
                        logger.info(f"Fixed surgery group: Scheduling {surrogates_needed} surrogates for {num_surgery_groups} surgery groups on {transfer_dt} (offset={offset_days} days from {dt})")
                        
                        # Find available surrogates (ignore weekday/block constraints but respect recovery)
                        avail_s = sorted(
                            [s for s in surrogates
                             if s['remaining'] > 0
                             and transfer_dt >= s['next']  # Respect recovery constraint
                             and is_in_active_cycle(s, transfer_dt)
                             and not any(st <= transfer_dt < en for st, en in fixed_tr_windows.get(s['animal']['name'], []))],
                            key=lambda x: x['performed'], reverse=True  # Prioritize animals with most transfers performed
                        )[:surrogates_needed]
                        
                        if surrogates_needed > 0 and len(avail_s) >= surrogates_needed:
                            logger.info(f"Fixed surgery group: Found {len(avail_s)} surrogates, scheduling transfers on {transfer_dt}")
                            fixed_group['transfers'] = avail_s
                            
                            # CRITICAL: Immediately update recovery tracking for surrogates
                            rec_days_tr = self.settings['recovery_transfer']
                            for s in avail_s:
                                surrogate_name = s['animal']['name']
                                s['next'] = transfer_dt + timedelta(days=rec_days_tr)
                                s['remaining'] -= 1
                                recovery_start = transfer_dt
                                recovery_end = transfer_dt + timedelta(days=rec_days_tr)
                                fixed_tr_windows.setdefault(surrogate_name, []).append((recovery_start, recovery_end))
                                logger.debug(f"Fixed group completion: Updated {surrogate_name} recovery window to {recovery_start} - {recovery_end}")
                        else:
                            logger.warning(f"Fixed surgery group: Only found {len(avail_s)} surrogates, need {surrogates_per_transfer} for transfers - scheduling transfers without complete group")
                            fixed_group['transfers'] = []  # Still add the group but with no transfers
            
            # Log fixed groups summary
            fixed_dates = sorted({group['date'] for group in fixed_event_groups})
            logger.info(f"Fixed groups processed: {len(fixed_event_groups)} groups on dates: {fixed_dates}")
            
            # Second pass: Process regular (non-fixed) surgeries, avoiding conflicts with fixed groups
            for dt in (start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)):
                surgeries_scheduled_today = []
                
                # Process regular (non-fixed) surgeries - allow on fixed group dates but with different animals
                if dt.weekday() in surgery_days and dt not in blocks:
                    if donors_per_surgery > 0:
                        # Get animals used in fixed groups on this specific date
                        fixed_animals_today = set()
                        for group in fixed_event_groups:
                            if group['date'] == dt:
                                for surgery in group['fixed_surgeries']:
                                    fixed_animals_today.add(surgery.animal)
                                for donor in group['additional_donors']:
                                    fixed_animals_today.add(donor['animal']['name'])
                        
                        # Get ALL available donors (excluding animals used in fixed groups on this specific date only)
                        all_avail_d = sorted(
                            [d for d in donors
                             if d['remaining'] > 0
                             and d['animal']['name'] not in fixed_animals_today  # Only avoid animals used in fixed groups on THIS date
                             and dt >= d['next']
                             and is_in_active_cycle(d, dt)
                             and not any(st <= dt < en for st, en in fixed_op_windows.get(d['animal']['name'], []))],
                            key=lambda x: x['performed'], reverse=True
                        )
                        
                        # Only schedule if we have enough donors for a complete group
                        if len(all_avail_d) >= donors_per_surgery:
                            # In linked mode, check surrogate availability BEFORE scheduling surgeries
                            if linked_mode and surrogates_per_transfer > 0:
                                transfer_dt = dt + timedelta(days=offset_days)
                                
                                # Check if transfer date is valid
                                if not (transfer_dt.weekday() in transfer_days and transfer_dt not in blocks and transfer_dt <= end_date):
                                    logger.info(f"LINKED mode: Skipping surgery on {dt} - transfer date {transfer_dt} is not valid (blocked or wrong weekday)")
                                    continue
                                
                                # Get animals used in fixed groups on this transfer date
                                fixed_animals_on_transfer_date = set()
                                for group in fixed_event_groups:
                                    if group['date'] + timedelta(days=offset_days) == transfer_dt:
                                        if 'transfers' in group:
                                            for surrogate in group['transfers']:
                                                fixed_animals_on_transfer_date.add(surrogate['animal']['name'])
                                
                                # Check if enough surrogates will be available on transfer date
                                avail_surrogates_for_check = [
                                    s for s in surrogates
                                    if s['remaining'] > 0
                                    and s['animal']['name'] not in fixed_animals_on_transfer_date
                                    and transfer_dt >= s['next']
                                    and is_in_active_cycle(s, transfer_dt)
                                    and not any(st <= transfer_dt < en for st, en in fixed_tr_windows.get(s['animal']['name'], []))
                                ]
                                
                                surrogates_needed = surrogates_per_transfer  # 1 group = surrogates_per_transfer surrogates
                                if len(avail_surrogates_for_check) < surrogates_needed:
                                    logger.info(f"LINKED mode: Skipping surgery on {dt} - not enough surrogates on {transfer_dt} (have {len(avail_surrogates_for_check)}, need {surrogates_needed})")
                                    continue
                            
                            # Limit to 1 surgery group per day
                            num_groups = 1
                            
                            logger.info(f"Regular scheduling on {dt}: Found {len(all_avail_d)} available donors: {[d['animal']['name'] for d in all_avail_d]}")
                            logger.info(f"Regular scheduling on {dt}: Fixed animals today: {list(fixed_animals_today)}")
                            logger.info(f"Regular scheduling on {dt}: Scheduling {num_groups} group (1 per day limit, need {donors_per_surgery} donors per group)")
                            
                            for group_idx in range(num_groups):
                                # Get donors for this specific group
                                group_donors = all_avail_d[group_idx * donors_per_surgery : (group_idx + 1) * donors_per_surgery]
                                
                                logger.info(f"Regular scheduling on {dt}: Group {group_idx + 1} donors: {[d['animal']['name'] for d in group_donors]}")
                                
                                # Schedule the surgeries for this group
                                for d in group_donors:
                                    entry = ScheduleEntry(d['animal']['name'], 'op', dt)
                                    ts = time.mktime(datetime.combine(dt, datetime.min.time()).timetuple())
                                    y = y_positions[d['animal']['name']]
                                    rec = self.settings['recovery_op']
                                    dots.append({'x': ts, 'y': y, 'color': QColor('red'), 'entry': entry})
                                    bars.append((ts, y, rec * self.day_width, 0.025, QColor('firebrick')))
                                    planned.append(entry)
                                    d['remaining'] -= 1
                                    d['next'] = dt + timedelta(days=rec)
                                    surgeries_scheduled_today.append(entry)
                            
                            logger.info(f"Regular scheduling summary for {dt}: "
                                       f"Formed {num_groups} complete groups, "
                                       f"Used {num_groups * donors_per_surgery} donors, "
                                       f"Remaining available: {len(all_avail_d) - (num_groups * donors_per_surgery)}")
                        else:
                            logger.info(f"Regular scheduling on {dt}: Not enough donors for complete group (have {len(all_avail_d)}, need {donors_per_surgery})")
                            logger.info(f"Regular scheduling on {dt}: Available donors: {[d['animal']['name'] for d in all_avail_d]}")
                            logger.info(f"Regular scheduling on {dt}: Fixed animals today: {list(fixed_animals_today)}")
                
                # Process transfers ONLY for regular (non-fixed) surgeries
                if surgeries_scheduled_today:
                    transfer_dt = dt + timedelta(days=offset_days) if linked_mode else dt
                    
                    if linked_mode:
                        # In linked mode, only process transfers if we actually had surgeries on dt
                        # and transfer date is valid
                        if (transfer_dt.weekday() in transfer_days and transfer_dt not in blocks and
                            transfer_dt <= end_date and surrogates_per_transfer > 0):
                            
                            logger.info(f"LINKED mode: Scheduling {len(surgeries_scheduled_today)} surgeries on {dt}, transfers on {transfer_dt}")
                            
                            # Get animals used in fixed groups on this transfer date
                            fixed_animals_on_transfer_date = set()
                            for group in fixed_event_groups:
                                if group['date'] + timedelta(days=offset_days) == transfer_dt:
                                    if 'transfers' in group:
                                        for surrogate in group['transfers']:
                                            fixed_animals_on_transfer_date.add(surrogate['animal']['name'])
                            
                            # Get ALL available surrogates (excluding animals used in fixed groups on this transfer date only)
                            all_avail_s = sorted(
                                [s for s in surrogates
                                 if s['remaining'] > 0
                                 and s['animal']['name'] not in fixed_animals_on_transfer_date  # Only avoid animals used in fixed groups on THIS transfer date
                                 and transfer_dt >= s['next']
                                 and is_in_active_cycle(s, transfer_dt)
                                 and not any(st <= transfer_dt < en for st, en in fixed_tr_windows.get(s['animal']['name'], []))],
                                key=lambda x: x['performed'], reverse=True
                            )
                            
                            # Calculate how many surgery groups we scheduled (1 surgery group = donors_per_surgery donors)
                            # Each surgery group needs surrogates_per_transfer surrogates
                            num_surgery_groups = len(surgeries_scheduled_today) // donors_per_surgery if donors_per_surgery > 0 else 0
                            
                            # Only schedule transfers if we have enough surrogates for the surgery groups
                            surrogates_needed = num_surgery_groups * surrogates_per_transfer
                            if len(all_avail_s) >= surrogates_needed:
                                # In linked mode, transfer groups should match surgery groups
                                num_transfer_groups = num_surgery_groups
                                
                                logger.info(f"LINKED mode transfers on {transfer_dt}: Found {len(all_avail_s)} available surrogates: {[s['animal']['name'] for s in all_avail_s]}")
                                logger.info(f"LINKED mode transfers on {transfer_dt}: Fixed transfer animals today: {list(fixed_animals_on_transfer_date)}")
                                logger.info(f"LINKED mode transfers on {transfer_dt}: Scheduling {num_transfer_groups} transfer groups to match {num_surgery_groups} surgery groups (need {surrogates_per_transfer} surrogates per group)")
                                
                                for group_idx in range(num_transfer_groups):
                                    # Get surrogates for this specific group
                                    group_surrogates = all_avail_s[group_idx * surrogates_per_transfer : (group_idx + 1) * surrogates_per_transfer]
                                    
                                    logger.info(f"LINKED mode transfers on {transfer_dt}: Transfer group {group_idx + 1} surrogates: {[s['animal']['name'] for s in group_surrogates]}")
                                    
                                    # Schedule the transfers for this group
                                    for s in group_surrogates:
                                        entry = ScheduleEntry(s['animal']['name'], 'embryoübertragung', transfer_dt)
                                        ts = time.mktime(datetime.combine(transfer_dt, datetime.min.time()).timetuple())
                                        y = y_positions[s['animal']['name']]
                                        rec = self.settings['recovery_transfer']
                                        dots.append({'x': ts, 'y': y, 'color': QColor('orange'), 'entry': entry})
                                        bars.append((ts, y, rec * self.day_width, 0.025, QColor('green')))
                                        planned.append(entry)
                                        s['remaining'] -= 1
                                        s['next'] = transfer_dt + timedelta(days=rec)
                                
                                logger.info(f"LINKED mode: Formed {num_transfer_groups} complete transfer groups with {num_transfer_groups * surrogates_per_transfer} surrogates on {transfer_dt}")
                            else:
                                logger.warning(f"LINKED mode: Not enough surrogates for {num_surgery_groups} surgery groups on {transfer_dt} (have {len(all_avail_s)}, need {surrogates_needed})")
                                logger.info(f"LINKED mode transfers on {transfer_dt}: Available surrogates: {[s['animal']['name'] for s in all_avail_s]}")
                                logger.info(f"LINKED mode transfers on {transfer_dt}: Fixed transfer animals today: {list(fixed_animals_on_transfer_date)}")
                        elif surgeries_scheduled_today:
                            logger.warning(f"LINKED mode: Surgeries scheduled on {dt} but transfer date {transfer_dt} conflicts with constraints")
                    else:
                        # INDEPENDENT mode: process transfers on their own schedule
                        if surrogates_per_transfer > 0 and dt.weekday() in transfer_days and dt not in blocks:
                            logger.info(f"INDEPENDENT mode: Scheduling transfers on {dt}")
                            
                            # Get animals used in fixed groups on this transfer date
                            fixed_animals_today = set()
                            for group in fixed_event_groups:
                                if group['date'] == dt:
                                    if 'transfers' in group:
                                        for surrogate in group['transfers']:
                                            fixed_animals_today.add(surrogate['animal']['name'])
                            
                            # Get ALL available surrogates (excluding animals used in fixed groups on this specific date only)
                            all_avail_s = sorted(
                                [s for s in surrogates
                                 if s['remaining'] > 0
                                 and s['animal']['name'] not in fixed_animals_today  # Only avoid animals used in fixed groups on THIS date
                                 and dt >= s['next']
                                 and is_in_active_cycle(s, dt)
                                 and not any(st <= dt < en for st, en in fixed_tr_windows.get(s['animal']['name'], []))],
                                key=lambda x: x['performed'], reverse=True
                            )
                            
                            # Only schedule transfers if we have enough surrogates for a complete group
                            if len(all_avail_s) >= surrogates_per_transfer:
                                # Limit to 1 transfer group per day (matches surgery group limit)
                                num_transfer_groups = 1
                                
                                logger.info(f"INDEPENDENT mode transfers on {dt}: Found {len(all_avail_s)} available surrogates: {[s['animal']['name'] for s in all_avail_s]}")
                                logger.info(f"INDEPENDENT mode transfers on {dt}: Fixed transfer animals today: {list(fixed_animals_today)}")
                                logger.info(f"INDEPENDENT mode transfers on {dt}: Scheduling {num_transfer_groups} group (1 per day limit, need {surrogates_per_transfer} per group)")
                                
                                for group_idx in range(num_transfer_groups):
                                    # Get surrogates for this specific group
                                    group_surrogates = all_avail_s[group_idx * surrogates_per_transfer : (group_idx + 1) * surrogates_per_transfer]
                                    
                                    logger.info(f"INDEPENDENT mode transfers on {dt}: Transfer group {group_idx + 1} surrogates: {[s['animal']['name'] for s in group_surrogates]}")
                                    
                                    # Schedule the transfers for this group
                                    for s in group_surrogates:
                                        entry = ScheduleEntry(s['animal']['name'], 'embryoübertragung', dt)
                                        ts = time.mktime(datetime.combine(dt, datetime.min.time()).timetuple())
                                        y = y_positions[s['animal']['name']]
                                        rec = self.settings['recovery_transfer']
                                        dots.append({'x': ts, 'y': y, 'color': QColor('orange'), 'entry': entry})
                                        bars.append((ts, y, rec * self.day_width, 0.025, QColor('green')))
                                        planned.append(entry)
                                        s['remaining'] -= 1
                                        s['next'] = dt + timedelta(days=rec)
                                
                                logger.info(f"INDEPENDENT mode: Formed {num_transfer_groups} complete transfer groups with {len(all_avail_s)} surrogates on {dt}")
                            else:
                                logger.warning(f"INDEPENDENT mode: Not enough surrogates for complete transfer group on {dt} (have {len(all_avail_s)}, need {surrogates_per_transfer})")
                                logger.info(f"INDEPENDENT mode transfers on {dt}: Available surrogates: {[s['animal']['name'] for s in all_avail_s]}")
                                logger.info(f"INDEPENDENT mode transfers on {dt}: Fixed transfer animals today: {list(fixed_animals_today)}")
            
            # Finally, render all the fixed groups
            for group in fixed_event_groups:
                dt = group['date']
                
                # Render fixed surgeries
                for surgery in group['fixed_surgeries']:
                    ts = time.mktime(datetime.combine(dt, datetime.min.time()).timetuple())
                    y = y_positions[surgery.animal]
                    rec = self.settings['recovery_op']
                    dots.append({'x': ts, 'y': y, 'color': QColor('red'), 'entry': surgery})
                    bars.append((ts, y, rec * self.day_width, 0.025, QColor('darkred')))  # Darker red for fixed
                
                # Render additional donors (tracking already updated in first pass)
                for d in group['additional_donors']:
                    entry = ScheduleEntry(d['animal']['name'], 'op', dt)
                    ts = time.mktime(datetime.combine(dt, datetime.min.time()).timetuple())
                    y = y_positions[d['animal']['name']]
                    rec = self.settings['recovery_op']
                    dots.append({'x': ts, 'y': y, 'color': QColor('red'), 'entry': entry})
                    bars.append((ts, y, rec * self.day_width, 0.025, QColor('firebrick')))
                    planned.append(entry)
                    # Note: d['remaining'] and d['next'] already updated in first pass
                
                # Render transfers (tracking already updated in first pass)
                if 'transfers' in group:
                    transfer_dt = dt + timedelta(days=offset_days)
                    for s in group['transfers']:
                        entry = ScheduleEntry(s['animal']['name'], 'embryoübertragung', transfer_dt)
                        ts = time.mktime(datetime.combine(transfer_dt, datetime.min.time()).timetuple())
                        y = y_positions[s['animal']['name']]
                        rec = self.settings['recovery_transfer']
                        dots.append({'x': ts, 'y': y, 'color': QColor('orange'), 'entry': entry})
                        bars.append((ts, y, rec * self.day_width, 0.025, QColor('green')))
                        planned.append(entry)
                        # Note: s['remaining'] and s['next'] already updated in first pass

            # Update internal schedule
            self.planned = planned
            
            # Validate schedule completeness and warn user if incomplete sets exist
            validation_warnings = self._validate_schedule_completeness(planned)
            if validation_warnings:
                warning_msg = tr(self.messages, 'surgery_planner.warning.incomplete_sets_header',
                                 'Warning: Incomplete sets detected in schedule:\n\n')
                
                for warning in validation_warnings:
                    if warning['type'] == 'surgery':
                        warning_msg += tr(
                            self.messages,
                            'surgery_planner.warning.incomplete_surgery_set',
                            '• {date}: {count} surgeries (expected multiple of {expected})\n  Animals: {animals}\n'
                        ).format(
                            date=warning['date'].strftime(DATE_FORMAT),
                            count=warning['count'],
                            expected=warning['expected'],
                            animals=', '.join(warning['animals'])
                        )
                    else:
                        warning_msg += tr(
                            self.messages,
                            'surgery_planner.warning.incomplete_transfer_set',
                            '• {date}: {count} transfers (expected multiple of {expected})\n  Animals: {animals}\n'
                        ).format(
                            date=warning['date'].strftime(DATE_FORMAT),
                            count=warning['count'],
                            expected=warning['expected'],
                            animals=', '.join(warning['animals'])
                        )
                
                QMessageBox.warning(
                    self,
                    tr(self.messages, 'surgery_planner.warning.incomplete_sets_title', 'Incomplete Sets'),
                    warning_msg
                )
                logger.warning(f"Schedule validation found {len(validation_warnings)} incomplete sets")
            
            self._render_schedule()
            logger.info(f"Generated schedule with {len(planned)} entries")

        except Exception as e:
            logger.error(f"Error generating schedule: {e}")
            QMessageBox.critical(
                self,
                self.messages.get('op_planner.error.title', 'Error'),
                f"{self.messages.get('op_planner.error.generating_schedule', 'Error generating schedule')}: {str(e)}"
            )

    def _delete_event(self, entry: ScheduleEntry) -> bool:
        """
        Delete an event from the schedule and return it to the possibility pool.
        
        Args:
            entry: The ScheduleEntry to delete
            
        Returns:
            True if deletion was successful, False otherwise
        """
        try:
            # Find and remove the entry from planned list
            if entry in self.planned:
                self.planned.remove(entry)
                logger.info(f"Deleted event: {entry.event_type} for {entry.animal} on {entry.date}")
                
                # The animal's capacity is automatically returned to the pool
                # because _generate() recalculates from animal data each time
                # No need to manually update animal records
                
                # Persist the updated schedule to the staging file.
                self._persist_temp_schedule()
                
                # Redraw the schedule
                self._render_schedule()
                
                return True
            else:
                logger.warning(f"Event not found in planned list: {entry}")
                return False
                
        except Exception as e:
            logger.error(f"Error deleting event: {e}")
            return False

    def _on_dot_clicked(self, event):
        """
        Handle a click on a dot: pop up an edit dialog for that event.
        """
        try:
            if not hasattr(self, '_dots_data'):
                return
            # only handle the first picked point
            if not event.ind:
                return
            idx  = event.ind[0]
            meta = self._dots_data[idx]
            entry = meta['entry']
            # always pull from entry.date so it's up-to-date
            new_date = entry.date
            
            # Draw visual indicator on the Gantt chart
            indicator_line = None
            try:
                # Convert date to matplotlib date number
                date_num = mdates.date2num(new_date)
                
                # Draw red dotted vertical line at the event's date
                indicator_line = self.ax.axvline(
                    x=date_num,
                    color='red',
                    linestyle='--',
                    linewidth=2,
                    alpha=0.7,
                    zorder=1000  # High z-order to draw on top
                )
                
                # Redraw canvas to show the indicator
                self.canvas.draw()
                logger.debug(f"Drew editing indicator line at {new_date}")
            except Exception as e:
                logger.warning(f"Failed to draw editing indicator: {e}")
            
            dialog = QDialog(self)
            dialog.setWindowTitle(self.messages.get('op_planner.dialog.edit_event', 'Edit Event'))
            layout = QVBoxLayout(dialog)
            date_input = QLineEdit(new_date.strftime(DATE_FORMAT))
            override_cb = QCheckBox(self.messages.get('op_planner.checkbox.ignore_blocked', 'Ignore All Date Constraints'))
            # initialize from the entry so override actually works
            override_cb.setChecked(entry.override_weekday)
            fixed_cb = QCheckBox(self.messages.get('op_planner.checkbox.fix_event', 'Fix Event'))
            fixed_cb.setChecked(entry.fixed)
            
            # Create button layout with Save and Delete buttons
            button_layout = QHBoxLayout()
            save_btn = QPushButton(self.messages.get('button.save', 'Save'))
            save_btn.setEnabled(self._can('op_scheduler.use'))
            delete_btn = QPushButton(tr(self.messages, 'surgery_planner.button.delete_event', 'Delete Event'))
            delete_btn.setStyleSheet("QPushButton { background-color: #d32f2f; color: white; }")
            delete_btn.setEnabled(self._can('op_scheduler.use'))
            button_layout.addWidget(save_btn)
            button_layout.addWidget(delete_btn)
            
            layout.addWidget(QLabel(f"Editing {entry.event_type} for {entry.animal}"))
            layout.addWidget(QLabel(f'New Date ({DATE_FORMAT}):'))
            layout.addWidget(date_input)
            layout.addWidget(override_cb)
            layout.addWidget(fixed_cb)
            layout.addLayout(button_layout)

            def save_changes():
                text = date_input.text().strip()
                # parse the user input
                try:
                    dt = datetime.strptime(text, DATE_FORMAT).date()
                except ValueError:
                    QMessageBox.critical(
                        dialog,
                        self.messages.get('op_planner.error.title', 'Error'),
                        self.messages.get('op_planner.error.invalid_date_format', 'Invalid date format')
                    )
                    return

                # preserve original in case validation fails
                orig_date     = entry.date
                orig_override = entry.override_weekday
                # tentatively apply override & fixed
                entry.date             = dt
                entry.override_weekday = override_cb.isChecked()
                entry.fixed            = fixed_cb.isChecked()

                # validate against existing events and block days
                if not self._validate_event(entry, self.animals):
                    # revert on failure
                    entry.date             = orig_date
                    entry.override_weekday = orig_override
                    return

                # Persist the updated plan to the staging JSON.
                try:
                    temp_out = {'schedule': []}
                    for e in self.planned:
                        temp_out['schedule'].append(schedule_entry_to_dict(e, date_format=DATE_FORMAT))
                    self._backend_save("draft-schedule", temp_out)
                except Exception as ex:
                    logger.error(f"Failed to save temp schedule after edit: {ex}")

                # Redraw from self.planned and close dialog
                self._render_schedule()
                dialog.accept()
            
            def delete_event():
                # Show confirmation dialog
                confirm_msg = tr(
                    self.messages,
                    'surgery_planner.confirm.delete_event',
                    'Are you sure you want to delete this event?\n\nAnimal: {animal}\nType: {event_type}\nDate: {date}\n\nThe animal will be returned to the possibility pool for future scheduling.'
                ).format(
                    animal=entry.animal,
                    event_type=entry.event_type,
                    date=entry.date.strftime(DATE_FORMAT)
                )
                
                reply = QMessageBox.question(
                    dialog,
                    tr(self.messages, 'surgery_planner.confirm.delete_event_title', 'Confirm Deletion'),
                    confirm_msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                
                if reply == QMessageBox.StandardButton.Yes:
                    # Delete the event
                    if self._delete_event(entry):
                        QMessageBox.information(
                            dialog,
                            tr(self.messages, 'surgery_planner.info.event_deleted_title', 'Event Deleted'),
                            tr(self.messages, 'surgery_planner.info.event_deleted', 'Event deleted successfully. The animal is now available for future scheduling.')
                        )
                        dialog.accept()
                    else:
                        QMessageBox.critical(
                            dialog,
                            tr(self.messages, 'surgery_planner.error.delete_failed_title', 'Deletion Failed'),
                            tr(self.messages, 'surgery_planner.error.delete_failed', 'Failed to delete the event. Please try again.')
                        )
            
            # Remove the indicator line when dialog closes
            def cleanup_indicator():
                try:
                    if indicator_line is not None:
                        indicator_line.remove()
                        self.canvas.draw()
                        logger.debug("Removed editing indicator line")
                except Exception as e:
                    logger.warning(f"Failed to remove editing indicator: {e}")
            
            dialog.finished.connect(cleanup_indicator)
            delete_btn.clicked.connect(delete_event)
            save_btn.clicked.connect(save_changes)
            dialog.exec()
            # always exit pan mode when the dialog closes
            self._pan_active = False
        except Exception as e:
            QMessageBox.critical(
                self, 
                self.messages.get('op_planner.error.edit_error', 'Edit Error'), 
                str(e)
            )
            logger.error(f"Error editing event: {e}")

    def _create_manual_event(self):
        """
        Open a dialog to manually create a new event (surgery or transfer).
        Allows selection of animal, event type, date, and constraints.
        """
        try:
            dialog = QDialog(self)
            dialog.setWindowTitle(tr(self.messages, 'surgery_planner.dialog.create_event', 'Create New Event'))
            dialog.setMinimumWidth(400)
            layout = QVBoxLayout(dialog)
            
            # Animal selection dropdown
            animal_label = QLabel(tr(self.messages, 'surgery_planner.label.select_animal', 'Select Animal:'))
            animal_combo = QComboBox()
            
            # Populate with available animals (donors and surrogates)
            available_animals = []
            for animal in self.animals:
                name = animal.get('name', '')
                role = _animal_role_value(animal)
                
                if role == ROLE_VALUE_SPENDER:
                    # Check if donor has remaining capacity
                    total_allowed = int(animal.get('OP_max', 0))
                    performed = _canonical_event_count(animal, 'surgery')
                    if performed < total_allowed:
                        available_animals.append({
                            'name': name,
                            'role': 'donor',
                            'display': f"{name} (Donor: {performed}/{total_allowed})"
                        })
                elif role == ROLE_VALUE_AMME:
                    # Check if surrogate has remaining capacity
                    total_allowed = int(animal.get('Embryo_max', 0))
                    performed = _canonical_event_count(animal, 'embryo_transfer')
                    if performed < total_allowed:
                        available_animals.append({
                            'name': name,
                            'role': 'surrogate',
                            'display': f"{name} (Surrogate: {performed}/{total_allowed})"
                        })
            
            if not available_animals:
                QMessageBox.warning(
                    self,
                    tr(self.messages, 'surgery_planner.warning.no_available_animals', 'No Available Animals'),
                    tr(self.messages, 'surgery_planner.warning.no_animals_with_capacity', 'No animals have remaining capacity for surgeries or transfers.')
                )
                return
            
            # Add animals to combo box
            for animal in available_animals:
                animal_combo.addItem(animal['display'], animal)
            
            # Event type display - determined by animal role (read-only)
            event_type_label = QLabel(tr(self.messages, 'surgery_planner.label.event_type', 'Event Type:'))
            event_type_display = QLabel()  # Non-editable text instead of dropdown
            event_type_display.setStyleSheet("QLabel { padding: 5px; background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 3px; }")
            
            # Store the actual event type value
            event_type_value = {'type': 'op'}  # Default, will be updated
            
            # Update event type display based on selected animal
            def update_event_type():
                current_animal = animal_combo.currentData()
                if current_animal:
                    # Set the appropriate event type for this animal's role
                    if current_animal['role'] == 'donor':
                        event_type_display.setText(
                            tr(self.messages, 'surgery_planner.event_type.surgery', 'Surgery (OP)')
                        )
                        event_type_value['type'] = 'op'
                    else:  # surrogate
                        event_type_display.setText(
                            tr(self.messages, 'surgery_planner.event_type.transfer', 'Embryo Transfer')
                        )
                        event_type_value['type'] = 'embryoübertragung'
            
            animal_combo.currentIndexChanged.connect(update_event_type)
            update_event_type()  # Set initial value
            
            # Date input
            date_label = QLabel(tr(self.messages, 'surgery_planner.label.event_date', f'Event Date ({DATE_FORMAT}):', format=DATE_FORMAT))
            date_input = QLineEdit(date.today().strftime(DATE_FORMAT))
            
            # Checkboxes (same as edit event dialog)
            override_cb = QCheckBox(tr(self.messages, 'op_planner.checkbox.ignore_blocked', 'Ignore All Date Constraints'))
            override_cb.setChecked(False)
            
            fixed_cb = QCheckBox(tr(self.messages, 'op_planner.checkbox.fix_event', 'Fix Event'))
            fixed_cb.setChecked(False)
            fixed_cb.setToolTip(tr(self.messages, 'surgery_planner.tooltip.fix_event', 'Fixed events are preserved when regenerating the schedule'))
            
            # Buttons
            button_layout = QHBoxLayout()
            create_btn = QPushButton(tr(self.messages, 'surgery_planner.button.create', 'Create'))
            create_btn.setEnabled(self._can('op_scheduler.use'))
            cancel_btn = QPushButton(tr(self.messages, 'button.cancel', 'Cancel'))
            button_layout.addWidget(create_btn)
            button_layout.addWidget(cancel_btn)
            
            # Add widgets to layout
            layout.addWidget(animal_label)
            layout.addWidget(animal_combo)
            layout.addWidget(event_type_label)
            layout.addWidget(event_type_display)
            layout.addWidget(date_label)
            layout.addWidget(date_input)
            layout.addWidget(override_cb)
            layout.addWidget(fixed_cb)
            layout.addLayout(button_layout)
            
            # Button handlers
            def create_event():
                # Get selected animal
                animal_data = animal_combo.currentData()
                if not animal_data:
                    QMessageBox.warning(
                        dialog,
                        tr(self.messages, 'surgery_planner.error.no_animal_selected', 'No Animal Selected'),
                        tr(self.messages, 'surgery_planner.error.please_select_animal', 'Please select an animal.')
                    )
                    return
                
                # Parse date
                try:
                    event_date = datetime.strptime(date_input.text().strip(), DATE_FORMAT).date()
                except ValueError:
                    QMessageBox.critical(
                        dialog,
                        tr(self.messages, 'op_planner.error.title', 'Error'),
                        tr(self.messages, 'op_planner.error.invalid_date_format', 'Invalid date format')
                    )
                    return
                
                # Get event type (automatically determined by animal role)
                event_type = event_type_value['type']
                
                # Create new schedule entry
                entry = ScheduleEntry(
                    animal=animal_data['name'],
                    event_type=event_type,
                    date_obj=event_date,
                    override=override_cb.isChecked()
                )
                entry.fixed = fixed_cb.isChecked()
                
                # Validate the event
                if not self._validate_event(entry, self.animals):
                    return
                
                # Add to planned list
                self.planned.append(entry)
                logger.info(f"Manually created event: {event_type} for {animal_data['name']} on {event_date}")
                
                # Persist to the staging schedule.
                self._persist_temp_schedule()
                
                # Redraw schedule
                self._render_schedule()
                
                # Show success message
                QMessageBox.information(
                    dialog,
                    tr(self.messages, 'surgery_planner.info.event_created_title', 'Event Created'),
                    tr(self.messages, 'surgery_planner.info.event_created', 'Event created successfully.')
                )
                
                dialog.accept()
            
            cancel_btn.clicked.connect(dialog.reject)
            create_btn.clicked.connect(create_event)
            
            dialog.exec()
            
        except Exception as e:
            logger.error(f"Error creating manual event: {e}")
            QMessageBox.critical(
                self,
                tr(self.messages, 'surgery_planner.error.create_failed', 'Creation Failed'),
                tr(self.messages, 'surgery_planner.error.failed_to_create_event', f'Failed to create event: {str(e)}')
            )

    def _parse_weekdays(self, text: str) -> set[int]:
        """
        Convert a comma or whitespace‑separated list of weekday names into
        a set of weekday indices (Monday=0 .. Sunday=6).  Supports English,
        German, and Russian weekday names. Unrecognized tokens are ignored.
        Names may be full or abbreviated. The comparison is case‑insensitive.
        """
        # Create mapping with translated weekday names
        mapping = {
            # English
            'monday': 0, 'mon': 0,
            'tuesday': 1, 'tue': 1, 'tues': 1,
            'wednesday': 2, 'wed': 2,
            'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
            'friday': 4, 'fri': 4,
            'saturday': 5, 'sat': 5,
            'sunday': 6, 'sun': 6,
            # German
            'montag': 0, 'mo': 0,
            'dienstag': 1, 'di': 1,
            'mittwoch': 2, 'mi': 2,
            'donnerstag': 3, 'do': 3,
            'freitag': 4, 'fr': 4,
            'samstag': 5, 'sa': 5,
            'sonntag': 6, 'so': 6,
            # Russian
            'понедельник': 0, 'пн': 0,
            'вторник': 1, 'вт': 1,
            'среда': 2, 'ср': 2,
            'четверг': 3, 'чт': 3,
            'пятница': 4, 'пт': 4,
            'суббота': 5, 'сб': 5,
            'воскресенье': 6, 'вс': 6,
        }
        
        # Also add the current translated weekday names from messages
        try:
            translated_weekdays = [
                tr(self.messages, 'general.weekday.monday', 'Monday').lower(),
                tr(self.messages, 'general.weekday.tuesday', 'Tuesday').lower(),
                tr(self.messages, 'general.weekday.wednesday', 'Wednesday').lower(),
                tr(self.messages, 'general.weekday.thursday', 'Thursday').lower(),
                tr(self.messages, 'general.weekday.friday', 'Friday').lower(),
                tr(self.messages, 'general.weekday.saturday', 'Saturday').lower(),
                tr(self.messages, 'general.weekday.sunday', 'Sunday').lower()
            ]
            for i, day_name in enumerate(translated_weekdays):
                if day_name and day_name not in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                    mapping[day_name] = i
                    logger.debug(f"Added translated weekday mapping: '{day_name}' -> {i}")
        except Exception as e:
            logger.warning(f"Could not add translated weekday names to mapping: {e}")
        
        days = set()
        for token in [t.strip().lower() for t in text.replace(',', ' ').split() if t.strip()]:
            if token.isdigit():
                try:
                    idx = int(token)
                    if 0 <= idx <= 6:
                        days.add(idx)
                except ValueError:
                    pass
            elif token in mapping:
                days.add(mapping[token])
        return days

    def _update_calendar_locale(self):
        """Update the calendar's locale based on the current language setting."""
        try:
            # Get language from messages or default to German
            language = self.messages.get('_language', 'de')
            logger.debug(f"Setting calendar locale to: {language}")
            
            # Set the appropriate locale
            if language == 'de':
                locale = QLocale(QLocale.Language.German, QLocale.Country.Germany)
            elif language == 'ru':
                locale = QLocale(QLocale.Language.Russian, QLocale.Country.Russia)
            else:
                locale = QLocale(QLocale.Language.English, QLocale.Country.UnitedStates)
                
            # Apply the locale to the calendar
            self.calendar.setLocale(locale)
            
            # Set first day of week based on locale
            if language in ['de', 'ru']:  # Both German and Russian start week on Monday
                self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
            else:
                self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Sunday)
            
            # Force a complete refresh of the calendar display
            current_date = self.calendar.selectedDate()
            current_month = self.calendar.monthShown()
            current_year = self.calendar.yearShown()
            
            # Navigate away and back to force locale refresh
            self.calendar.showNextMonth()
            self.calendar.showPreviousMonth()
            
            # Ensure we're back to the correct month/year
            self.calendar.setCurrentPage(current_year, current_month)
            self.calendar.setSelectedDate(current_date)
            
            # Force widget update
            self.calendar.update()
            
            logger.debug(f"Calendar locale updated successfully to {language}")
            
        except Exception as e:
            logger.error(f"Error updating calendar locale: {e}")

    def _update_calendar_format(self) -> None:
        """
        Apply custom formatting to the calendar.  All days in the currently
        displayed month are set to bold.  Blocked days are highlighted in
        red.  Any formatting from a previous month is cleared before
        applying new formats.

        This method is called whenever block days are loaded or when the
        calendar page changes.  It maintains a set of dates that have
        custom formatting so that formatting can be cleared on update.
        """
        try:
            # Clear previous formatting
            for qd in list(self._formatted_dates):
                try:
                    self.calendar.setDateTextFormat(qd, QTextCharFormat())
                except Exception:
                    pass
            self._formatted_dates.clear()

            # Bold all days in the current month
            year = self.calendar.yearShown()
            month = self.calendar.monthShown()
            fmt_bold = QTextCharFormat()
            fmt_bold.setFontWeight(QFont.Weight.Bold)
            # Iterate through possible days (1..31).  Only valid dates are
            # formatted.  We avoid using calendar.monthDaysInMonth() because
            # QCalendarWidget does not expose this directly.
            for day in range(1, 32):
                qdate = QDate(year, month, day)
                if qdate.isValid():
                    self.calendar.setDateTextFormat(qdate, fmt_bold)
                    self._formatted_dates.add(qdate)

            # Highlight blocked days in red AND bold
            for bd in self.block_days:
                qd = QDate(bd.date.year, bd.date.month, bd.date.day)
                if bd.date.year == year and bd.date.month == month:
                    fmt = QTextCharFormat()
                    fmt.setForeground(QBrush(QColor('red')))
                    fmt.setFontWeight(QFont.Weight.Bold)
                    self.calendar.setDateTextFormat(qd, fmt)
                    self._formatted_dates.add(qd)
            # Highlight scheduled events with colored background
            for dt, events in self.calendar_events.items():
                qd = QDate(dt.year, dt.month, dt.day)
                # build or get existing format
                fmt = self.calendar.dateTextFormat(qd)
                # choose base color
                base = QColor('red') if any(e[0] == 'op' for e in events) else QColor('green')
                # if outside current month, use 30% alpha
                if dt.year != year or dt.month != month:
                    # keep text at default grey by not touching foreground
                    base.setAlphaF(0.3)
                # apply background brush
                fmt.setBackground(QBrush(base))
                self.calendar.setDateTextFormat(qd, fmt)
                self._formatted_dates.add(qd)
        except Exception as e:
            logger.error(f"Failed to update calendar formatting: {e}")

    def _export_csv(self):
        """Export the current schedule as CSV to a user-selected location."""
        suggested_name = f"schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        filename, _ = QFileDialog.getSaveFileName(
            self, 
            tr(self.messages, 'surgery_planner.dialog.export_csv_title', 'Export Schedule as CSV'),
            suggested_name,
            "CSV Files (*.csv)"
        )
        if filename:
            try:
                export_schedule_to_csv(self.planned, filename)
                QMessageBox.information(self, 
                    tr(self.messages, 'surgery_planner.info.export_success', 'Export Success'),
                    tr(self.messages, 'surgery_planner.message.export_success', 'Schedule exported successfully to {filename}').format(filename=filename)
                )
                logger.info(f"Schedule exported to CSV: {filename}")
            except Exception as e:
                QMessageBox.critical(self, 
                    tr(self.messages, 'surgery_planner.error.export_failed', 'Export Failed'),
                    tr(self.messages, 'surgery_planner.error.export_failed', 'Failed to export schedule: {error}').format(error=str(e))
                )
                logger.error(f"CSV export failed: {e}")

    def _export_png(self):
        """Export the current schedule figure as PNG to a user-selected location."""
        suggested_name = f"schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        filename, _ = QFileDialog.getSaveFileName(
            self, 
            tr(self.messages, 'surgery_planner.dialog.export_png_title', 'Export Schedule as PNG'),
            suggested_name,
            "PNG Files (*.png)"
        )
        if filename:
            try:
                self.fig.savefig(filename, dpi=300, bbox_inches='tight')
                QMessageBox.information(self, 
                    tr(self.messages, 'surgery_planner.info.export_success', 'Export Success'),
                    tr(self.messages, 'surgery_planner.message.export_success', 'Schedule exported successfully to {filename}').format(filename=filename)
                )
                logger.info(f"Schedule exported to PNG: {filename}")
            except Exception as e:
                QMessageBox.critical(self, 
                    tr(self.messages, 'surgery_planner.error.export_failed', 'Export Failed'),
                    tr(self.messages, 'surgery_planner.error.export_failed', 'Failed to export schedule: {error}').format(error=str(e))
                )
                logger.error(f"PNG export failed: {e}")

    def _save_schedule_as(self):
        """Save the current schedule JSON to a user-selected location."""
        if not hasattr(self, 'planned') or not self.planned:
            QMessageBox.warning(
                self,
                tr(self.messages, 'surgery_planner.warning.no_schedule', 'No Schedule'),
                tr(self.messages, 'surgery_planner.message.generate_schedule_first', 'Please generate a schedule first before exporting.')
            )
            return
            
        suggested_name = f"schedule_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filename, _ = QFileDialog.getSaveFileName(
            self, 
            tr(self.messages, 'surgery_planner.dialog.save_json_title', 'Save Schedule as JSON'),
            suggested_name,
            "JSON Files (*.json)"
        )
        if filename:
            try:
                out = {
                    "schedule": [
                        schedule_entry_to_dict(entry, date_format="iso")
                        for entry in self.planned
                    ]
                }
                with open(filename, "w", encoding="utf-8") as handle:
                    json.dump(out, handle, ensure_ascii=False, indent=2)
                QMessageBox.information(
                    self,
                    tr(self.messages, 'surgery_planner.info.export_success', 'Export Success'),
                    tr(self.messages, 'surgery_planner.message.export_success', 'Schedule exported successfully to {filename}').format(filename=filename)
                )
                logger.info(f"Schedule exported to JSON: {filename}")
            except Exception as e:
                QMessageBox.critical(self, 
                    tr(self.messages, 'surgery_planner.error.export_failed', 'Export Failed'),
                    tr(self.messages, 'surgery_planner.error.export_failed', 'Failed to export schedule: {error}').format(error=str(e))
                )
                logger.error(f"JSON export failed: {e}")

    def _audit_publish(self, revision, count):
        parent=getattr(self, "_parent", None)
        master=getattr(parent, "master_track", None)
        audit=getattr(master, "audit", None)
        if callable(audit):
            try:
                audit("surgery_planner.publish", f"revision={revision}; entries={count}")
            except Exception:
                logger.exception("Surgery Planner audit failed")

    def _save_schedule(self):
        """
        Persist the currently generated schedule entries to
        the selected ProgTrack backend.
        """
        if not self._can("op_scheduler.use"):
            QMessageBox.warning(self, self.messages.get("op_planner.warning.permission", "Permission denied"), self.messages.get("op_planner.warning.publish_permission", "You are not allowed to publish a schedule."))
            return

        try:
            # Ensure we've generated a schedule
            if not hasattr(self, 'planned') or not self.planned:
                QMessageBox.warning(
                    self, 
                    self.messages.get('op_planner.warning.save_schedule', 'Save Schedule'),
                    self.messages.get('op_planner.warning.no_schedule_to_save', 'No schedule to save. Please generate first.')
                )
                return

            # Serialize entries
            out = {'schedule': [], 'snapshot_revision': getattr(getattr(self, 'planner_snapshot', None), 'revision', 'unknown'), 'published_at': datetime.utcnow().isoformat()}
            for entry in self.planned:
                out['schedule'].append(schedule_entry_to_dict(entry, date_format=DATE_FORMAT))

            self._backend_save("schedule", out)
            self._audit_publish(out.get("snapshot_revision"), len(self.planned))

            QMessageBox.information(
                self, 
                self.messages.get('op_planner.info.save_schedule', 'Save Schedule'),
                f"{self.messages.get('op_planner.info.schedule_saved_to', 'Schedule saved to')} backend"
            )
            logger.debug(f"Saved schedule ({len(self.planned)} entries) to backend")
            # reload & re-highlight calendar events whenever user saves a new schedule
            self._load_schedule_events()
            self._update_calendar_format()
        except Exception as e:
            QMessageBox.critical(
                self, 
                self.messages.get('op_planner.error.save_schedule_failed', 'Save Schedule Failed'), 
                str(e)
            )
            logger.error(f"Failed to save schedule: {e}")

    def _load_schedule_events(self):
        """Load schedule events from both JSON and in-memory planned list."""
        try:
            self.calendar_events.clear()
            
            # First load from in-memory planned events if available
            if hasattr(self, 'planned') and self.planned:
                for entry in self.planned:
                    if hasattr(entry, 'date') and hasattr(entry, 'event_type') and hasattr(entry, 'animal'):
                        self.calendar_events.setdefault(entry.date, []).append((entry.event_type, entry.animal))
                # Force calendar refresh
                self._update_calendar_format()
                return
                
            # Fall back to the published backend schedule.
            data = self._backend_load("schedule", None)
            if data is not None:
                if isinstance(data, list):
                    data = {"schedule": data}
                for item in data.get('schedule', []):
                    try:
                        dt = datetime.strptime(item.get('date', ''), DATE_FORMAT).date()
                        etype = item.get('event_type', '')
                        animal = item.get('animal', '')
                        if dt and etype and animal:  # Only add if all required fields are present
                            self.calendar_events.setdefault(dt, []).append((etype, animal))
                    except (ValueError, KeyError) as e:
                        logger.warning(f"Skipping invalid schedule entry: {item}")
                # Force calendar refresh
                self._update_calendar_format()
        except Exception as e:
            logger.error(f"Failed to load calendar events: {e}")
            # Try to continue with empty calendar_events if there was an error

    def _on_calendar_date_clicked(self, qdate):
        """Pop up a dialog listing all events on the clicked date."""
        dt = qdate.toPyDate()
        # If this date is in the block_days list, show its label and return
        for bd in self.block_days:
            if bd.date == dt:
                QMessageBox.information(
                    self,
                    self.messages.get('op_planner.dialog.blocked', 'Blocked'),
                    bd.name
                )
                return
        events = self.calendar_events.get(dt, [])
        if not events:
            return
        # Determine dialog title based on event types present
        types = {etype for etype, animal in events}
        if types == {'op'}:
            dlg_title = 'Surgery'
        elif types == {'embryoübertragung'}:
            dlg_title = 'Transfer'
        else:
            dlg_title = 'Events'

        dlg = QDialog(self)
        dlg.setWindowTitle(dlg_title)
        layout = QVBoxLayout(dlg)
        # For each event on that date, show animal, type, and current/max counts
        for etype, animal in events:
            label = 'Surgery' if etype == 'op' else 'Transfer'
            # build sorted list of dates for this animal & event type
            all_dates = sorted(
                e.date for e in getattr(self, 'planned', [])
                if e.animal == animal and e.event_type == etype
            )
            # determine 1-based index of this date among *new* scheduled events
            sched_index = all_dates.index(dt) + 1 if dt in all_dates else len(all_dates)
            # count past performed events from the animal record
            past = 0
            for rec in self.animals:
                if rec.get('name') == animal:
                    # use 'op' list for surgeries, 'embryoübertragung' list for transfers
                    key = 'op' if etype == 'op' else 'embryoübertragung'
                    past = len(rec.get(key, []))
                    break
            # total = past performed + this scheduled one
            current = past + sched_index
            # lookup the max‐allowed for that animal
            max_allowed = 0
            for rec in self.animals:
                if rec.get('name') == animal:
                    if etype == 'op':
                        max_allowed = int(rec.get('OP_max', 0))
                    else:
                        max_allowed = int(rec.get('Embryo_max', 0))
                    break
            layout.addWidget(QLabel(f"{animal}: {label} {current}/{max_allowed}"))
        dlg.exec()


class Plugin:
    def __init__(self, progtrack_api):
        self.api = progtrack_api
        self.logger = logging.getLogger('SurgeryPlanner')
        self.messages = {}
        self.dialog = None

    def on_load(self):
        """
        Add a localized menu entry for the surgical planner into the Tools
        menu. The label is taken from the messages dictionary passed by the
        main application.
        """
        try:
            tools_menu = self.api.get_menu('Tools')
            self._tools_menu = tools_menu
            
            # Get messages from the API if available
            if hasattr(self.api, 'get_messages'):
                self.messages = self.api.get_messages()
                
            # Create the action with localized text
            action = QAction(self.messages.get('menu.tools.op_planner', 'OP Scheduler'))
            self._action_ref = action
            action.triggered.connect(self.on_activate)
            self._tools_menu.addAction(action)
            self.logger.info('Surgery Planner menu added')
        except Exception as e:
            self.logger.error(f"Failed to load plugin: {e}")

    def on_activate(self):
        try:
            self.logger.debug("Starting Surgery Planner activation...")
            
            # Get the latest messages from the API if available
            if hasattr(self.api, 'get_messages'):
                try:
                    self.messages = self.api.get_messages()
                    self.logger.debug("Successfully loaded messages from API")
                    self.logger.debug(f"Plugin messages language: {self.messages.get('_language', 'unknown')}")
                    self.logger.debug(f"Plugin messages has general.weekday.monday: {'general.weekday.monday' in str(self.messages)}")
                    # Check if we have the nested structure
                    if 'general' in self.messages and 'weekday' in self.messages['general']:
                        self.logger.debug(f"Found general.weekday structure with keys: {list(self.messages['general']['weekday'].keys())}")
                    else:
                        self.logger.debug("No general.weekday structure found in messages")
                except Exception as e:
                    self.logger.error(f"Failed to get messages from API: {e}")
                    QMessageBox.critical(
                        None,
                        self.messages.get('error.title', 'Error'),
                        self.messages.get('op_planner.error.load_messages', 
                                        'Failed to load interface text. Some text may appear in English.') + f"\n\n{str(e)}"
                    )
                    
            # Check if we need to create a new dialog
            if not hasattr(self, 'dialog') or not self.dialog or not self.dialog.isVisible():
                self.logger.debug("Creating new GanttWidget dialog...")
                
                # Get animals from the API if available
                animals = []
                if hasattr(self.api, 'get_animals'):
                    try:
                        animals = self.api.get_animals()
                        self.logger.debug(f"Loaded {len(animals)} animals from API")
                    except Exception as e:
                        error_msg = f"Failed to get animals from API: {e}"
                        self.logger.error(error_msg)
                        QMessageBox.warning(
                            None,
                            self.messages.get('warning.title', 'Warning'),
                            self.messages.get('op_planner.warning.no_animals', 
                                           'Could not load animal data. Some features may be limited.') + 
                            f"\n\n{str(e)}"
                        )
                
                # Create the dialog with error handling
                try:
                    # Get the main window as parent
                    parent = self.api.get_main_window() if hasattr(self.api, 'get_main_window') else None
                    self.logger.debug(f"Creating GanttWidget with messages language: {self.messages.get('_language', 'unknown')}")
                    self.dialog = GanttWidget(animals=animals, messages=self.messages, parent=parent)
                    
                    # Ensure the calendar uses the correct locale
                    if hasattr(self.dialog, '_update_calendar_locale'):
                        self.dialog._update_calendar_locale()
                        
                    self.logger.debug("Successfully created GanttWidget with locale: " + 
                                   self.messages.get('_language', 'en'))
                except Exception as e:
                    error_msg = f"Failed to create Surgery Planner window: {e}"
                    self.logger.error(error_msg, exc_info=True)
                    QMessageBox.critical(
                        None,
                        self.messages.get('error.title', 'Error'),
                        self.messages.get('op_planner.error.create_window', 
                                        'Failed to create Surgery Planner window.') + 
                        f"\n\n{str(e)}"
                    )
                    return
            else:
                # Update existing dialog with latest messages and locale
                self.logger.debug("Updating existing GanttWidget dialog...")
                try:
                    self.dialog.messages = self.messages
                    # Update calendar locale for existing dialog
                    if hasattr(self.dialog, '_update_calendar_locale'):
                        self.dialog._update_calendar_locale()
                    self.logger.debug("Updated existing GanttWidget with locale: " + 
                                   self.messages.get('_language', 'en'))
                except Exception as e:
                    self.logger.error(f"Failed to update existing dialog: {e}")
            
            # Show the dialog
            try:
                self.dialog.show()
                self.dialog.raise_()
                self.dialog.activateWindow()
                self.logger.debug("Surgery Planner activated successfully")
            except Exception as e:
                error_msg = f"Failed to show Surgery Planner window: {e}"
                self.logger.error(error_msg, exc_info=True)
                QMessageBox.critical(
                    None,
                    self.messages.get('error.title', 'Error'),
                    self.messages.get('op_planner.error.show_window', 
                                    'Failed to show Surgery Planner window.') + 
                    f"\n\n{str(e)}"
                )
                
        except Exception as e:
            error_msg = f"Unexpected error in Surgery Planner: {e}"
            self.logger.error(error_msg, exc_info=True)
            QMessageBox.critical(
                None,
                self.messages.get('error.title', 'Error'),
                self.messages.get('op_planner.error.unexpected', 
                                'An unexpected error occurred while starting the Surgery Planner.') + 
                                f"\n\n{str(e)}"
            )
