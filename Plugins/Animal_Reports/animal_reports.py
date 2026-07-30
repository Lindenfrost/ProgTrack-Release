# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Animal Reports plugin implementation.

import os
import sys
import json
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, field

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QMessageBox,
    QLineEdit, QComboBox, QFormLayout,
    QGroupBox, QTextEdit,
    QMainWindow, QListWidget, QListWidgetItem,
    QFrame
)
from PyQt6.QtGui import QIcon, QAction, QKeySequence, QFont, QDoubleValidator

try:
    from Plugins.core.animal_identity import animal_base_name
except Exception:  # pragma: no cover - standalone plugin fallback
    def animal_base_name(key: Any, record: Dict[str, Any] | None = None) -> str:
        if isinstance(record, dict):
            for field in ("_base_name", "display_name", "name"):
                value = str(record.get(field, "") or "").strip()
                if value:
                    return value
        return str(key or "").split(" - ")[0].strip()

@dataclass
class LockedEntry:
    """Class to represent a locked timeline entry."""
    date: str
    entry_type: str
    details: str
    locked: bool = True
    data: dict = field(default_factory=dict)  # Original data for reference
    
    def to_dict(self) -> dict:
        """Convert to a dictionary for JSON serialization."""
        return {
            'date': self.date,
            'type': self.entry_type,
            'details': self.details,
            'locked': self.locked,
            'data': self.data
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> 'LockedEntry':
        """Create a LockedEntry from a dictionary."""
        return cls(
            date=data.get('date', ''),
            entry_type=data.get('type', ''),
            details=data.get('details', ''),
            locked=data.get('locked', True),
            data=data.get('data', {})
        )

# Set up logging
logger = logging.getLogger(__name__)

class AnimalReportsWidget(QMainWindow):
    """
    Main widget for the Animal Reports plugin.
    Displays detailed information about a single animal and allows editing.
    """
    
    # Class variables
    _instances = {}
    DATA_FILE = None  # Will be set by the main application or standalone mode
    LOCKED_ENTRIES_FILE = 'animal_reports_locked.json'  # File to store locked entries
    
    def __init__(self, animal_name: str = None, parent=None, messages: Dict = None, data_file: str = None):
        """
        Initialize the Animal Reports widget.
        
        Args:
            animal_name: Name of the animal to display. If None, shows the first animal.
            parent: Parent widget
            messages: Dictionary of UI messages for localization
            data_file: Path to the data file. If None, uses the class variable
        """
        super().__init__(parent)
        
        # Set the data file if provided
        if data_file:
            AnimalReportsWidget.DATA_FILE = data_file
        
        # Initialize instance variables
        self.animal_name = animal_name
        self.messages = messages or {}
        from Plugins.core.backend_store import BackendJsonStore
        backend = getattr(parent, "backend", None)
        self._backend = backend
        self._report_store = BackendJsonStore(
            backend, "animal-reports", "report-data"
        )
        self._locked_store = BackendJsonStore(
            backend, "animal-reports", "locked-entries"
        )
        self.data = {}  # Initialize as empty dict to avoid None checks
        self.current_animal_data = None
        self._data_loaded = False
        self.locked_entries = {}  # animal_name -> list of LockedEntry
        self._initializing = True  # Track initialization state
        self.animal_list = None  # Will be initialized in _init_ui
        
        try:
            # Set up the UI first
            self._init_ui()
            
            # Initialize data structures
            if not hasattr(self, 'data') or not isinstance(self.data, dict):
                self.data = {'animals': {}}
            
            # Load locked entries
            self._load_locked_entries()
            
            # Load data - this will also populate the animal list
            self._load_data()
            
            # If an animal was specified, select it after a short delay to ensure UI is ready
            if animal_name:
                QTimer.singleShot(100, lambda: self._select_animal_after_init(animal_name))
            
            # Register this instance
            self._register_instance()
            
        except Exception as e:
            logger.error(f"Error initializing AnimalReportsWidget: {str(e)}", exc_info=True)
            QMessageBox.critical(
                self,
                self._get_message('error.initialization_failed', 'Initialization Failed'),
                self._get_message('error.initialization_failed_details', 
                               'Failed to initialize Animal Reports. See log for details.')
            )
            raise
        finally:
            self._initializing = False
    
    def _select_animal_after_init(self, animal_name: str):
        """Select an animal after the UI has been fully initialized."""
        try:
            if not hasattr(self, 'animal_list') or not self.animal_list:
                logger.warning("Animal list not available for selection")
                return
                
            animals_dict = self.data.get('animals', {}) or self.data.get('tiere', {})
            if animal_name in animals_dict:
                item = self._find_animal_list_item_by_key(animal_name)
                if item:
                    self.animal_list.setCurrentItem(item)
                    logger.debug(f"Selected animal after init: {animal_name}")
                else:
                    logger.warning(f"Animal {animal_name} not found in animal list")
            else:
                logger.warning(f"Animal {animal_name} not found in data")
                # Select first animal if available
                if self.animal_list.count() > 0:
                    self.animal_list.setCurrentRow(0)
        except Exception as e:
            logger.error(f"Error selecting animal after init: {str(e)}", exc_info=True)
    
    def _init_ui(self):
        """Initialize the user interface with the new layout."""
        try:
            # Set window properties
            self.setWindowTitle(self._get_message('plugin.animal_reports.title', 'Animal Report'))
            self.setMinimumSize(1200, 800)
            
            # Create main widget and layout
            main_widget = QWidget()
            self.setCentralWidget(main_widget)
            self.main_layout = QHBoxLayout(main_widget)  # Store as instance variable
            self.main_layout.setContentsMargins(5, 5, 5, 5)
            self.main_layout.setSpacing(5)
            
            # Create left panel for animal list
            self.left_panel = QWidget()
            self.left_panel.setMaximumWidth(300)
            self.left_layout = QVBoxLayout(self.left_panel)
            self.left_layout.setContentsMargins(0, 0, 5, 0)
            
            # Animal list label
            self.left_layout.addWidget(QLabel(self._get_message('label.animal_list', 'Animals:')))
            
            # Search box
            self.search_box = QLineEdit()
            self.search_box.setPlaceholderText(self._get_message('label.search', 'Search...'))
            self.search_box.textChanged.connect(self._filter_animals)
            self.left_layout.addWidget(self.search_box)
            
            # Animal list widget
            self.animal_list = QListWidget()
            self.animal_list.setAlternatingRowColors(True)
            self.animal_list.itemSelectionChanged.connect(self._on_animal_selected)
            self.left_layout.addWidget(self.animal_list, 1)  # Allow the list to expand
            
            # Add left panel to main layout
            self.main_layout.addWidget(self.left_panel)
            
            # Create right panel for animal details
            self.right_panel = QWidget()
            self.right_layout = QVBoxLayout(self.right_panel)
            self.right_layout.setContentsMargins(5, 0, 0, 0)
            
            # Animal details group
            details_group = QGroupBox(self._get_message('label.animal_details', 'Animal Details'))
            details_layout = QVBoxLayout(details_group)
            
            # Animal details table
            self.details_table = QTableWidget()
            self.details_table.setColumnCount(2)
            self.details_table.setHorizontalHeaderLabels([
                self._get_message('header.property', 'Property'),
                self._get_message('header.value', 'Value')
            ])
            self.details_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.details_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self.details_table.verticalHeader().setVisible(False)
            self.details_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            details_layout.addWidget(self.details_table)
            
            self.right_layout.addWidget(details_group)
            
            # Timeline group
            timeline_group = QGroupBox(self._get_message('label.timeline', 'Timeline'))
            timeline_layout = QVBoxLayout(timeline_group)
            
            # Timeline table
            self.timeline_table = QTableWidget()
            self.timeline_table.setColumnCount(3)
            self.timeline_table.setHorizontalHeaderLabels([
                self._get_message('header.date', 'Date'),
                self._get_message('header.type', 'Type'),
                self._get_message('header.details', 'Details')
            ])
            self.timeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self.timeline_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            self.timeline_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self.timeline_table.verticalHeader().setVisible(False)
            self.timeline_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            timeline_layout.addWidget(self.timeline_table)
            
            self.right_layout.addWidget(timeline_group, 1)  # Give timeline more space
            
            # Add right panel to main layout
            self.main_layout.addWidget(self.right_panel, 1)  # Give right panel more space
            
            # Status bar
            self.statusBar().showMessage(self._get_message('status.ready', 'Ready'))
            
            logger.debug("UI initialization complete")
            
        except Exception as e:
            logger.error(f"Error initializing UI: {str(e)}", exc_info=True)
            raise
    
    def _filter_animals(self):
        """Filter the animal list to show only animals whose names start with the search text."""
        try:
            if not hasattr(self, 'animal_list') or not self._is_widget_valid(self.animal_list):
                logger.warning("Animal list not available for filtering")
                return
                
            search_text = self.search_box.text().strip().lower()
            
            for i in range(self.animal_list.count()):
                item = self.animal_list.item(i)
                if not item:
                    continue
                    
                animal_data = item.data(Qt.ItemDataRole.UserRole)
                
                if isinstance(animal_data, dict):
                    animal_name = animal_data.get('name', '').lower()
                else:
                    animal_name = item.text().lower()
                    
                # Show if search text matches start of name (case-insensitive)
                item.setHidden(not animal_name.startswith(search_text))
                
        except RuntimeError as e:
            if 'wrapped C/C++' in str(e):
                logger.warning("Widget was deleted during filtering")
                return
            raise
        except Exception as e:
            logger.error(f"Error filtering animals: {str(e)}", exc_info=True)
    
    def _is_widget_valid(self, widget):
        """Check if a Qt widget is still valid (not deleted)."""
        try:
            # Try to access a property to check if widget is still alive
            return widget is not None and widget.isWidgetType()
        except RuntimeError:
            return False

    def _display_animal_name(self, animal_key: str, animal_data: Any = None) -> str:
        record = animal_data if isinstance(animal_data, dict) else None
        display_name = animal_base_name(animal_key, record)
        fallback_name = animal_base_name(animal_key)
        if display_name == str(animal_key or '').strip() or " | " in display_name:
            return fallback_name
        return display_name or fallback_name

    def _animal_key_from_item(self, item) -> str:
        item_data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(item_data, dict):
            return str(item_data.get('key') or item_data.get('ipid') or item_data.get('name') or item.text()).strip()
        return item.text().strip() if item else ''

    def _find_animal_list_item_by_key(self, animal_key: str):
        if not self.animal_list:
            return None
        wanted = str(animal_key or '').strip()
        for index in range(self.animal_list.count()):
            item = self.animal_list.item(index)
            if self._animal_key_from_item(item) == wanted or item.text().strip() == wanted:
                return item
        return None

    def _is_valid_date(self, date_str):
        """Check if a date string is in a valid format."""
        if not date_str:
            return False
        try:
            from datetime import datetime
            date_str = str(date_str).split('T')[0]  # Remove time component if present
            datetime.strptime(date_str, '%Y-%m-%d')
            return True
        except (ValueError, TypeError):
            return False

    def _load_progtrack_data(self):
        """Load and validate data from progtrack_daten.json."""
        try:
            # Try to find the data file in the parent directory
            data_file = os.path.abspath(os.path.join(
                os.path.dirname(__file__), 
                '..', '..', 'progtrack_daten.json'
            ))
            
            if not os.path.exists(data_file):
                # Try alternative location for standalone execution
                data_file = os.path.abspath(os.path.join(
                    os.path.dirname(__file__), 
                    'progtrack_daten.json'
                ))
                if not os.path.exists(data_file):
                    logger.error(f"Data file not found at: {data_file}")
                    QMessageBox.warning(
                        self,
                        self._get_message('error.data_file_not_found', 'Data File Not Found'),
                        self._get_message('error.data_file_not_found_details',
                                       f'Could not find data file at: {data_file}')
                    )
                    return {'animals': {}, 'archived': {}}
            
            logger.info(f"Loading data from: {data_file}")
            
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
                # Ensure the data has the expected structure
                if not isinstance(data, dict):
                    raise ValueError("Invalid data format: expected a dictionary")
                    
                # Ensure we have the required top-level keys
                if 'animals' not in data:
                    data['animals'] = {}
                if 'archived' not in data:
                    data['archived'] = {}
                    
                logger.info(f"Loaded data for {len(data.get('animals', {}))} active and {len(data.get('archived', {}))} archived animals")
                return data
                
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in data file: {str(e)}"
            logger.error(error_msg, exc_info=True)
            QMessageBox.critical(
                self,
                self._get_message('error.invalid_data', 'Invalid Data'),
                self._get_message('error.invalid_data_details',
                               f'Error in data file: {str(e)}')
            )
            return {'animals': {}, 'archived': {}}
            
        except Exception as e:
            error_msg = f"Error loading progtrack data: {str(e)}"
            logger.error(error_msg, exc_info=True)
            QMessageBox.critical(
                self,
                self._get_message('error.load_failed', 'Load Failed'),
                self._get_message('error.load_failed_details',
                               f'Failed to load data: {str(e)}')
            )
            return {'animals': {}, 'archived': {}}

    def _format_date(self, date_str):
        """Convert date from YYYY-MM-DD to DD.MM.YYYY format."""
        if not date_str or not self._is_valid_date(date_str):
            logger.warning(f"Invalid date format: {date_str}")
            return str(date_str) if date_str else ""
            
        try:
            from datetime import datetime
            date_str = str(date_str).split('T')[0]  # Remove time component
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            return date_obj.strftime('%d.%m.%Y')
        except Exception as e:
            logger.warning(f"Error formatting date '{date_str}': {str(e)}")
            return str(date_str)
    
    def _get_all_dates(self, animal_name, progtrack_data):
        """Extract all unique dates for an animal from progtrack data.
        
        Args:
            animal_name: Name of the animal to get dates for
            progtrack_data: Dictionary containing the loaded progtrack data
            
        Returns:
            List of unique dates (strings in YYYY-MM-DD format), sorted newest first
        """
        if not isinstance(progtrack_data, dict) or not animal_name:
            logger.warning("Invalid arguments to _get_all_dates")
            return []
            
        dates = set()
        
        # Check both active and archived animals
        for section in ['animals', 'archived']:
            section_data = progtrack_data.get(section, {})
            if not isinstance(section_data, dict):
                logger.debug(f"No {section} data found or invalid format in progtrack_data")
                continue
            
            animal_data = section_data.get(animal_name)
            if not isinstance(animal_data, dict):
                logger.debug(f"Animal '{animal_name}' not found or invalid in {section}")
                continue
            
            logger.debug(f"Processing {section} data for {animal_name}")
            
            # List of all possible data types to check
            data_types = [
                # Main application event types
                'op', 'embryoübertragung', 'embryo', 'trächtigkeit', 'abort', 
                'geburt', 'pgf', 'fsh', 'progesteron',
                
                # Weight measurements (common in both)
                'gewicht', 'weight',
                
                # Blood/urine tests (common in both)
                'blut', 'urin', 'labor', 'daten',
                
                # Other common medical events
                'operation', 'untersuchung', 'medikation', 'impfung', 'behandlung'
            ]
            
            # Also include any other keys that might exist in the data
            additional_types = [
                k for k in animal_data.keys() 
                if k not in data_types 
                and k not in ['id', 'name', 'status', 'notes', 'reference_weight']
            ]
            data_types.extend(additional_types)
            
            # Process each data type
            for data_type in data_types:
                if data_type not in animal_data:
                    continue
                
                try:
                    items = animal_data[data_type]
                    if not isinstance(items, list):
                        logger.debug(f"{data_type} is not a list in {animal_name}'s {section} data")
                        continue
                    
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                            
                        # Handle different possible date field names
                        date_val = item.get('datum') or item.get('date') or item.get('Datum') or item.get('Date')
                        if not date_val:
                            continue
                            
                        # Convert to string and extract just the date part if it's a datetime
                        if hasattr(date_val, 'strftime'):
                            date_str = date_val.strftime('%Y-%m-%d')
                        else:
                            date_str = str(date_val).split('T')[0].split(' ')[0]
                            
                        # Validate date format (YYYY-MM-DD)
                        if self._is_valid_date(date_str):
                            dates.add(date_str)
                            
                except Exception as e:
                    logger.error(f"Error processing {data_type} in {section} for {animal_name}: {str(e)}", 
                               exc_info=True)
                    continue
        
        # Convert to list and sort newest first
        try:
            from datetime import datetime
            return sorted(dates, 
                         key=lambda x: datetime.strptime(x, '%Y-%m-%d'), 
                         reverse=True)
        except Exception as e:
            logger.error(f"Error sorting dates: {str(e)}", exc_info=True)
            return sorted(dates, reverse=True) if dates else []
    
    def _get_events_for_date(self, animal_name, date_str, progtrack_data):
        """Get all events for a specific animal on a specific date.
        
        Args:
            animal_name: Name of the animal
            date_str: Date string in YYYY-MM-DD format
            progtrack_data: Dictionary containing the loaded progtrack data
            
        Returns:
            List of event strings for the specified date
        """
        if not animal_name or not date_str or not progtrack_data:
            return []
            
        events = []
        
        # Check both active and archived animals
        for section in ['animals', 'archived']:
            section_data = progtrack_data.get(section, {})
            if not isinstance(section_data, dict):
                continue
                
            animal_data = section_data.get(animal_name)
            if not isinstance(animal_data, dict):
                continue
            
            # First check for weight data (special handling)
            for weight_key in ['gewicht', 'weight']:
                if weight_key in animal_data and isinstance(animal_data[weight_key], list):
                    for weight_item in animal_data[weight_key]:
                        if not isinstance(weight_item, dict):
                            continue
                            
                        weight_date = str(weight_item.get('datum', '') or weight_item.get('date', '')).split('T')[0]
                        if weight_date == date_str:
                            weight_value = weight_item.get('wert') or weight_item.get('value', '')
                            weight_unit = weight_item.get('einheit') or weight_item.get('unit', 'kg')
                            weight_note = weight_item.get('bemerkung') or weight_item.get('note', '')
                            
                            weight_str = f"Gewicht: {weight_value} {weight_unit}"
                            if weight_note:
                                weight_str += f" ({weight_note})"
                            events.append(weight_str)
            
            # Process other data types
            for data_type in ['op', 'embryoübertragung', 'embryo', 'trächtigkeit', 'abort', 'geburt', 'pgf', 'fsh', 'progesteron']:
                if data_type not in animal_data or not isinstance(animal_data[data_type], list):
                    continue
                    
                for item in animal_data[data_type]:
                    if not isinstance(item, dict):
                        continue
                        
                    item_date = str(item.get('datum', '') or item.get('date', '')).split('T')[0]
                    if item_date != date_str:
                        continue
                        
                    # Process the event
                    value = item.get('wert') or item.get('value', '')
                    unit = item.get('einheit') or item.get('unit', '')
                    note = item.get('bemerkung') or item.get('note', '')
                    
                    # Build event string
                    event_str = data_type.capitalize()
                    details = []
                    if value:
                        details.append(f"{value} {unit}".strip())
                    if note:
                        details.append(note)
                        
                    if details:
                        event_str += ": {}".format(', '.join(details))
                        
                    events.append(event_str)
        
        return events

    def _update_timeline(self, animal_data):
        """Update the timeline with all events from the animal's data."""
        logger.debug("Starting timeline update")
        
        if not isinstance(animal_data, dict) or 'name' not in animal_data:
            logger.warning("Invalid or missing animal data")
            self._show_timeline_message(
                self._get_message('error.invalid_animal_data', 'Invalid animal data')
            )
            return
            
        animal_name = animal_data['name']
        logger.info(f"Updating timeline for animal: {animal_name}")
        
        # Show loading indicator
        self._show_timeline_message(
            self._get_message('status.loading', 'Loading...')
        )
        
        try:
            # Get timeline entries from the animal's data
            timeline_entries = animal_data.get('timeline_entries', {})
            if not timeline_entries:
                logger.info(f"No timeline entries found for {animal_name}")
                self._show_timeline_message(
                    self._get_message('info.no_timeline_data', 'No timeline data available')
                )
                return
            
            # Sort dates in descending order (newest first)
            sorted_dates = sorted(timeline_entries.keys(), reverse=True)
            
            # Format the timeline entries
            formatted_entries = []
            events_count = 0
            
            for date_str in sorted_dates:
                date_entry = timeline_entries[date_str]
                if not date_entry or 'entries' not in date_entry or not date_entry['entries']:
                    continue
                
                # Format the date
                formatted_date = self._format_date(date_str)
                formatted_entries.append(f"\n[{formatted_date}]")
                
                # Add each entry for this date
                for entry in date_entry['entries']:
                    if not isinstance(entry, dict):
                        continue
                        
                    entry_type = entry.get('type', '')
                    details = []
                    
                    # Format based on entry type
                    if entry_type == 'weight':
                        value = entry.get('value', '')
                        unit = entry.get('unit', 'g')
                        note = entry.get('notes', '')
                        details.append(f"Weight: {value} {unit}")
                        if note:
                            details[-1] += f" ({note})"
                            
                    elif entry_type == 'progesterone':
                        value = entry.get('value', '')
                        unit = entry.get('unit', 'ng/ml')
                        note = entry.get('notes', '')
                        details.append(f"Progesterone: {value} {unit}")
                        if note:
                            details[-1] += f" ({note})"
                            
                    elif entry_type == 'pdg':
                        value = entry.get('value', '')
                        unit = entry.get('unit', 'ng/ml')
                        note = entry.get('notes', '')
                        details.append(f"PDG: {value} {unit}")
                        if note:
                            details[-1] += f" ({note})"
                            
                    elif entry_type == 'sperm':
                        total = entry.get('total', '')
                        motility = entry.get('motility', '')
                        progressive = entry.get('progressive', '')
                        note = entry.get('notes', '')
                        details.append(f"Sperm Analysis - Total: {total}, Motility: {motility}%, Progressive: {progressive}%")
                        if note:
                            details[-1] += f" ({note})"
                            
                    elif entry_type == 'event':
                        event_type = entry.get('event_type', 'Event')
                        note = entry.get('notes', '')
                        details.append(f"{event_type}")
                        if note:
                            details[-1] += f": {note}"
                    
                    # Add any additional fields
                    for key, value in entry.items():
                        if key not in ['type', 'value', 'unit', 'notes', 'event_type', 'total', 'motility', 'progressive'] and value:
                            details.append(f"{key}: {value}")
                    
                    # Add the formatted entry
                    if details:
                        formatted_entries.extend(details)
                        events_count += 1
            
            logger.info(f"Processed {len(sorted_dates)} dates with {events_count} total events")
            
            if not formatted_entries:
                self._show_timeline_message(
                    self._get_message('info.no_events_found', 'No events found in the selected period')
                )
                return
            
            # Update the display
            self._update_timeline_display(formatted_entries, events_count, animal_name)
            
        except Exception as e:
            error_msg = self._get_message(
                'error.update_failed', 
                'Failed to update timeline: {error}'
            ).format(error=str(e))
            
            logger.error(error_msg, exc_info=True)
            self._show_timeline_message(error_msg)
    
    def _show_timeline_message(self, message):
        """Show a message in the timeline table."""
        try:
            if not hasattr(self, 'timeline_table') or not self._is_widget_valid(self.timeline_table):
                logger.warning("Timeline table not available for showing message")
                return
                
            # Clear existing content
            self.timeline_table.clear()
            self.timeline_table.setRowCount(1)
            self.timeline_table.setColumnCount(1)
            
            # Create and configure label
            label = QLabel(message)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            
            # Set font
            font = label.font()
            font.setPointSize(10)
            label.setFont(font)
            
            # Add to table
            self.timeline_table.setCellWidget(0, 0, label)
            self.timeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            
        except Exception as e:
            logger.error(f"Error showing timeline message: {str(e)}", exc_info=True)
    
    def _update_timeline_display(self, timeline_entries, events_count, animal_name):
        """Update the timeline display with the provided entries."""
        try:
            if not hasattr(self, 'timeline_table') or not self._is_widget_valid(self.timeline_table):
                logger.error("Timeline table not available")
                return
            
            # Clear existing widget if any
            if hasattr(self, '_timeline_text_edit'):
                try:
                    self.timeline_table.removeCellWidget(0, 0)
                    self._timeline_text_edit.deleteLater()
                    del self._timeline_text_edit
                except Exception as e:
                    logger.warning(f"Error cleaning up previous timeline widget: {str(e)}")
            
            # Combine entries with newlines
            text = "\n".join(timeline_entries).strip()
            if not text:
                logger.warning("No text to display in timeline")
                return
            
            # Create and configure text edit
            self._timeline_text_edit = QTextEdit()
            self._timeline_text_edit.setPlainText(text)
            self._timeline_text_edit.setReadOnly(True)
            self._timeline_text_edit.setFrameShape(QFrame.Shape.NoFrame)
            self._timeline_text_edit.setFrameStyle(QFrame.Shape.NoFrame)
            
            # Configure font
            font = QFont('Courier New')
            font.setStyleHint(QFont.StyleHint.Monospace)
            font.setPointSize(9)
            self._timeline_text_edit.setFont(font)
            
            # Set up table
            self.timeline_table.clear()
            self.timeline_table.setRowCount(1)
            self.timeline_table.setColumnCount(1)
            self.timeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.timeline_table.verticalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            self.timeline_table.setCellWidget(0, 0, self._timeline_text_edit)
            
            # Set minimum height based on content
            doc_height = self._timeline_text_edit.document().size().height()
            row_height = min(max(int(doc_height) + 20, 200), 800)  # Clamp between 200 and 800px
            self.timeline_table.setRowHeight(0, row_height)
            
            logger.info(f"Updated timeline display with {events_count} events for {animal_name}")
            
        except Exception as e:
            logger.error(f"Error updating timeline display: {str(e)}", exc_info=True)
            self._show_timeline_message(
                self._get_message('error.display_failed', 'Failed to update display')
            )
    
    def _get_animal_status(self, animal_data):
        """Get the status text for an animal."""
        if not isinstance(animal_data, dict):
            logger.warning(f"Expected animal_data to be a dict, got {type(animal_data)}")
            return self._get_message('status.unknown', 'Unknown')
            
        status = str(animal_data.get('status', 'active')).lower()
        
        if 'archiv' in status:
            return self._get_message('status.archived', 'Archived')
        elif any(x in status for x in ['deceased', 'dead', 'gestorben', 'tot']):
            return self._get_message('status.deceased', 'Deceased')
        elif 'active' in status or 'aktiv' in status:
            return self._get_message('status.active', 'Active')
        else:
            # Default to active for any unrecognized status
            return self._get_message('status.active', 'Active')
    
    def _create_toolbar(self):
        """Create the main toolbar."""
        toolbar = self.addToolBar('Main Toolbar')
        
        # Save action
        self.save_action = QAction(
            QIcon.fromTheme('document-save'),
            self._get_message('action.save', '&Save'),
            self
        )
        self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self._save_data)
        toolbar.addAction(self.save_action)
        
        # Export action
        export_action = QAction(
            QIcon.fromTheme('document-export'),
            self._get_message('action.export', '&Export Report'),
            self
        )
        export_action.setEnabled(False)
        export_action.setToolTip(
            self._get_message(
                'action.export.use_main_reports',
                'Use the main Reports tab PDF export.'
            )
        )
        toolbar.addAction(export_action)
        
        toolbar.addSeparator()
        
        # Refresh action
        refresh_action = QAction(
            QIcon.fromTheme('view-refresh'),
            self._get_message('action.refresh', '&Refresh'),
            self
        )
        refresh_action.triggered.connect(self._load_data)
        toolbar.addAction(refresh_action)
        
        # Close action
        close_action = QAction(
            QIcon.fromTheme('window-close'),
            self._get_message('action.close', '&Close'),
            self
        )
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        toolbar.addAction(close_action)
    
    def _create_general_tab(self):
        """Create the General Information tab."""
        tab = QWidget()
        layout = QFormLayout(tab)
        
        # Basic information
        self.name_edit = QLineEdit()
        self.name_edit.textChanged.connect(self._on_data_changed)
        layout.addRow(self._get_message('label.name', 'Name:'), self.name_edit)
        
        self.reference_weight_edit = QLineEdit()
        self.reference_weight_edit.setValidator(QDoubleValidator(0, 999, 2, self))
        self.reference_weight_edit.textChanged.connect(self._on_data_changed)
        layout.addRow(self._get_message('label.reference_weight', 'Reference Weight (kg):'), 
                     self.reference_weight_edit)
        
        # Status
        self.status_combo = QComboBox()
        self.status_combo.addItems([
            self._get_message('status.active', 'Active'),
            self._get_message('status.archived', 'Archived'),
            self._get_message('status.deceased', 'Deceased')
        ])
        self.status_combo.currentTextChanged.connect(self._on_data_changed)
        layout.addRow(self._get_message('label.status', 'Status:'), self.status_combo)
        
        # Add stretch to push everything to the top
        layout.addRow(QWidget(), QWidget())
        
        self.tabs.addTab(tab, self._get_message('tab.general', 'General'))
    
    def _create_measurements_tab(self):
        """Create the Measurements tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Progesterone measurements table
        self.prog_table = QTableWidget()
        self.prog_table.setColumnCount(3)
        self.prog_table.setHorizontalHeaderLabels([
            self._get_message('header.date', 'Date'),
            self._get_message('header.value', 'Value'),
            self._get_message('header.comment', 'Comment')
        ])
        self.prog_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        
        # Add buttons for adding/removing measurements
        btn_layout = QHBoxLayout()
        add_btn = QPushButton(self._get_message('button.add', 'Add'))
        add_btn.setEnabled(False)
        add_btn.setToolTip(
            self._get_message(
                'animal_reports.measurements.add_disabled',
                'Add measurements in the animal editor or import workflow.'
            )
        )
        remove_btn = QPushButton(self._get_message('button.remove', 'Remove'))
        remove_btn.clicked.connect(self._remove_measurement)
        
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
        
        layout.addWidget(QLabel(self._get_message('label.progesterone_measurements', 'Progesterone Measurements:')))
        layout.addWidget(self.prog_table)
        layout.addLayout(btn_layout)
        
        self.tabs.addTab(tab, self._get_message('tab.measurements', 'Measurements'))
    
    def _create_procedures_tab(self):
        """Create the Procedures tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Procedures table
        self.procedures_table = QTableWidget()
        self.procedures_table.setColumnCount(4)
        self.procedures_table.setHorizontalHeaderLabels([
            self._get_message('header.date', 'Date'),
            self._get_message('header.type', 'Type'),
            self._get_message('header.details', 'Details'),
            self._get_message('header.veterinarian', 'Veterinarian')
        ])
        self.procedures_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        
        # Add buttons for adding/removing procedures
        btn_layout = QHBoxLayout()
        add_btn = QPushButton(self._get_message('button.add', 'Add'))
        add_btn.setEnabled(False)
        add_btn.setToolTip(
            self._get_message(
                'animal_reports.procedures.add_disabled',
                'Add procedures in the animal editor or surgery workflow.'
            )
        )
        remove_btn = QPushButton(self._get_message('button.remove', 'Remove'))
        remove_btn.clicked.connect(self._remove_procedure)
        
        btn_layout.addWidget(add_btn)
        btn_layout.addWidget(remove_btn)
        btn_layout.addStretch()
        
        layout.addWidget(QLabel(self._get_message('label.procedures', 'Procedures:')))
        layout.addWidget(self.procedures_table)
        layout.addLayout(btn_layout)
        
        self.tabs.addTab(tab, self._get_message('tab.procedures', 'Procedures'))
    
    def _create_notes_tab(self):
        """Create the Notes tab."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # Notes editor
        self.notes_edit = QTextEdit()
        self.notes_edit.textChanged.connect(self._on_data_changed)
        
        layout.addWidget(QLabel(self._get_message('label.notes', 'Notes:')))
        layout.addWidget(self.notes_edit)
        
        self.tabs.addTab(tab, self._get_message('tab.notes', 'Notes'))
    
    def _get_animals_dict(self, data: Dict) -> Dict:
        """Get the animals dictionary from data, handling both 'animals' and 'tiere' keys.
        
        Args:
            data: The data dictionary containing animal information
            
        Returns:
            Dictionary of animals, or empty dict if no valid data found
        """
        if not isinstance(data, dict):
            logger.warning("Invalid data format: expected dictionary")
            return {}
            
        # Try 'animals' first, then 'tiere' for backward compatibility
        animals = data.get('animals')
        if isinstance(animals, dict):
            return animals
            
        tiere = data.get('tiere')
        if isinstance(tiere, dict):
            logger.debug("Using 'tiere' key for animal data (legacy format)")
            return tiere
            
        logger.warning("No valid animal data found in 'animals' or 'tiere' keys")
        return {}

    def _load_locked_entries(self):
        """Load locked entries from the shared backend."""
        self.locked_entries = {}
        try:
            data = self._locked_store.load({})
            for animal_name, entries in data.items():
                self.locked_entries[animal_name] = [
                    LockedEntry.from_dict(entry) for entry in entries
                ]
            logger.info(f"Loaded {sum(len(entries) for entries in self.locked_entries.values())} locked entries")
        except Exception as e:
            logger.error(f"Error loading locked entries: {str(e)}", exc_info=True)
    
    def _save_locked_entries(self):
        """Save locked entries to the shared backend."""
        try:
            # Create a serializable dictionary
            data = {}
            for animal_name, entries in self.locked_entries.items():
                data[animal_name] = [entry.to_dict() for entry in entries]
            
            self._locked_store.save(data)
            logger.info(f"Saved {sum(len(entries) for entries in self.locked_entries.values())} locked entries to backend")
            
        except Exception as e:
            logger.error(f"Error saving locked entries: {str(e)}", exc_info=True)
    
    def _aggregate_animal_data(self, animal_name: str, animal_data: dict) -> dict:
        """
        Aggregate data for a single animal from progtrack_daten.json format.
        
        Args:
            animal_name: Name of the animal
            animal_data: Raw animal data from progtrack_daten.json
            
        Returns:
            Dictionary with aggregated data in the format for animal_report_data.json
        """
        logger.debug(f"Aggregating data for animal: {animal_name}")
        
        # Initialize the result structure
        display_name = self._display_animal_name(animal_name, animal_data)
        result = {
            'ipid': animal_name,
            'name': display_name,
            'id': animal_data.get('id', animal_name),
            'reference_weight': animal_data.get('referenz_gewicht', 
                                             animal_data.get('referenzgewicht', 0)),
            'species': animal_data.get('species', ''),
            'project': animal_data.get('project', ''),
            'birth_date': animal_data.get('birth_date', ''),
            'notes': '',
            'measurements': [],
            'procedures': [],
            'timeline_entries': {}
        }
        
        # Process all date-based data
        date_entries = {}
        
        # Process weight measurements
        for weight in animal_data.get('gewicht', []):
            date_str = weight.get('datum', '')
            if not date_str:
                continue
                
            # Convert to YYYY-MM-DD format if needed
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
                
            if date_str not in date_entries:
                date_entries[date_str] = []
                
            date_entries[date_str].append({
                'type': 'weight',
                'value': weight.get('wert'),
                'unit': 'g',
                'notes': ''
            })
        
        # Process progesterone measurements
        for prog in animal_data.get('progesteron', []):
            date_str = prog.get('datum', '')
            if not date_str:
                continue
                
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
                
            if date_str not in date_entries:
                date_entries[date_str] = []
                
            date_entries[date_str].append({
                'type': 'progesterone',
                'value': prog.get('wert'),
                'unit': 'ng/ml',
                'notes': ''
            })
        
        # Process PDG measurements
        for pdg in animal_data.get('pdg', []):
            date_str = pdg.get('datum', '')
            if not date_str:
                continue
                
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
                
            if date_str not in date_entries:
                date_entries[date_str] = []
                
            date_entries[date_str].append({
                'type': 'pdg',
                'value': pdg.get('wert'),
                'unit': 'ng/ml',
                'notes': ''
            })
        
        # Process events
        for event in animal_data.get('events', []):
            date_str = event.get('datum', '')
            if not date_str:
                continue
                
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
                
            if date_str not in date_entries:
                date_entries[date_str] = []
                
            date_entries[date_str].append({
                'type': 'event',
                'event_type': event.get('typ', ''),
                'notes': event.get('notiz', '')
            })
        
        # Process sperm data
        for sperm in animal_data.get('sperm', []):
            date_str = sperm.get('datum', '')
            if not date_str:
                continue
                
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
                
            if date_str not in date_entries:
                date_entries[date_str] = []
                
            date_entries[date_str].append({
                'type': 'sperm',
                'total': sperm.get('gesamt'),
                'motility': sperm.get('motilitaet'),
                'progressive': sperm.get('progressiv'),
                'notes': sperm.get('notiz', '')
            })
        
        # Convert date_entries to timeline_entries format
        for date_str, entries in date_entries.items():
            result['timeline_entries'][date_str] = {
                'locked': False,  # New entries are not locked by default
                'entries': entries,
                'notes': ''
            }
        
        return result
    
    def _load_data(self):
        """Aggregate current animal data while preserving report-owned edits."""
        if self._data_loaded:
            return
            
        try:
            logger.info("Starting data loading and aggregation")
            
            try:
                progtrack_data = self._backend.load_core_data()
                logger.info("Successfully loaded ProgTrack data from backend")
            except Exception as e:
                logger.error(f"Error loading progtrack data: {str(e)}", exc_info=True)
                QMessageBox.critical(
                    self,
                    self._get_message('error.load_failed', 'Load Failed'),
                    self._get_message('error.json_parse_error', 'Failed to parse {file}').format(
                        file='backend')
                )
                return
            
            # Initialize report data structure
            report_data = {'animals': {}}
            
            report_data = self._report_store.load({'animals': {}})
            report_data.setdefault('animals', {})
            logger.info(f"Loaded existing report data with {len(report_data['animals'])} animals")
            
            # Process animals from progtrack data
            animals_dict = self._get_animals_dict(progtrack_data)
            logger.info(f"Processing {len(animals_dict)} animals from progtrack data")
            
            # Track which animals we've processed
            processed_animals = set()
            
            # Process each animal in the progtrack data
            for animal_name, animal_data in animals_dict.items():
                try:
                    if not isinstance(animal_data, dict):
                        logger.warning(f"Skipping non-dict animal data for: {animal_name}")
                        continue
                    
                    # Get or create animal entry in report data
                    if animal_name not in report_data['animals']:
                        report_data['animals'][animal_name] = {}
                    
                    # Get the existing entry to preserve locked data
                    existing_entry = report_data['animals'][animal_name]
                    
                    # Get the aggregated data
                    aggregated_data = self._aggregate_animal_data(animal_name, animal_data)
                    
                    # Preserve existing notes if they exist
                    if 'notes' in existing_entry:
                        aggregated_data['notes'] = existing_entry['notes']
                    
                    # Process timeline entries to preserve locked ones
                    if 'timeline_entries' in existing_entry:
                        for date_str, date_data in existing_entry['timeline_entries'].items():
                            if date_data.get('locked', False) and date_str in aggregated_data['timeline_entries']:
                                # Keep the locked entry
                                aggregated_data['timeline_entries'][date_str] = date_data
                    
                    # Update the report data
                    report_data['animals'][animal_name] = aggregated_data
                    processed_animals.add(animal_name)
                    
                except Exception as e:
                    logger.error(f"Error processing animal {animal_name}: {str(e)}", exc_info=True)
            
            try:
                self._report_store.save(report_data)
                logger.info("Successfully saved report data to backend")
                
            except Exception as e:
                error_msg = f"Error saving report data: {str(e)}"
                logger.error(error_msg, exc_info=True)
                QMessageBox.critical(
                    self,
                    self._get_message('error.save_failed', 'Save Failed'),
                    self._get_message('error.save_failed_details', 
                                   'Failed to save report data. See log for details.')
                )
                return
            
            # Set the loaded data
            self.data = report_data
            self._data_loaded = True
            
            # Log the number of loaded animals
            logger.info(f"Successfully loaded data for {len(self.data.get('animals', {}))} animals")
            
            # Update the animal list
            self._load_animal_list()
            
            # If we have animals but none selected, select the first one
            if self.animal_list and not self.animal_name and self.animal_list.count() > 0:
                first_item = self.animal_list.item(0)
                if first_item:
                    self.animal_name = self._animal_key_from_item(first_item)
                    self._select_animal(self.animal_name)
            
        except Exception as e:
            error_msg = f"Critical error in _load_data: {str(e)}"
            logger.critical(error_msg, exc_info=True)
            
            QMessageBox.critical(
                self,
                self._get_message('error.title', 'Error'),
                self._get_message('error.load_data', 'Failed to load data: {error}').format(
                    error=str(e))
            )
            
            # Initialize with empty data to prevent further errors
            self.data = {'animals': {}}
            self._data_loaded = False
    
    def _load_animal_list(self):
        """
        Load the list of animals from the data, handling both 'animals' and 'tiere' keys.
        Returns True if successful, False otherwise.
        """
        try:
            # Check if widget exists and is valid
            if not hasattr(self, 'animal_list') or not self._is_widget_valid(self.animal_list):
                logger.warning("Animal list widget not available or deleted")
                return False
                
            # Block signals during update
            was_blocked = self.animal_list.signalsBlocked()
            self.animal_list.blockSignals(True)
            
            try:
                # Store current selection
                current_items = self.animal_list.selectedItems()
                current_selection = current_items[0].text() if current_items else None
                
                # Clear existing items
                self.animal_list.clear()
                
                if not self.data:
                    logger.warning("No data available to load animal list")
                    return False
                
                # Get animals from data, using only the 'animals' key
                animals_dict = self._get_animals_dict(self.data)
                
                if not isinstance(animals_dict, dict):
                    logger.warning(f"Unexpected animals_dict type: {type(animals_dict)}")
                    return False
                
                if not animals_dict:
                    logger.warning("No animal data found to load")
                    return False
                    
                # Get sorted list of animal items
                animal_items = sorted(
                    animals_dict.items(),
                    key=lambda item: self._display_animal_name(str(item[0]), item[1]).lower(),
                )
                logger.info("Loading %d animals into the list", len(animal_items))
                
                # Add animals to the list
                added_count = 0
                for animal_name, animal_data in animal_items:
                    try:
                        if not self._is_widget_valid(self.animal_list):
                            logger.warning("Widget was deleted during list population")
                            return False

                        if (
                            self.parent()
                            and hasattr(self.parent(), '_animal_visible_to_current_user')
                            and not self.parent()._animal_visible_to_current_user(animal_data)
                        ):
                            continue

                        animal_name = str(animal_name).strip()
                        if not animal_name:
                            logger.warning("Skipping animal with empty name")
                            continue
                            
                        display_name = self._display_animal_name(animal_name, animal_data)
                        item = QListWidgetItem(display_name)
                        
                        # Store both the display name and the original data for reference
                        item_data = {
                            'key': animal_name,
                            'ipid': animal_name,
                            'name': display_name,
                            'data': animal_data
                        }
                        item.setData(Qt.ItemDataRole.UserRole, item_data)
                        
                        # Build tooltip with available information
                        tooltip_parts = []
                        
                        # Add ID if available and different from name
                        animal_id = animal_data.get('id')
                        if animal_id and str(animal_id) != animal_name:
                            tooltip_parts.append(f"ID: {animal_id}")
                        
                        # Add status if available
                        status = animal_data.get('status')
                        if status:
                            tooltip_parts.append(f"Status: {status}")
                            
                        # Add species if available
                        species = animal_data.get('species')
                        if species:
                            tooltip_parts.append(f"Species: {species}")
                            
                        # Add reference weight if available
                        weight = animal_data.get('reference_weight')
                        if weight:
                            tooltip_parts.append(f"Ref. Weight: {weight}")
                        
                        if tooltip_parts:
                            item.setToolTip("\n".join(tooltip_parts))
                        
                        self.animal_list.addItem(item)
                        added_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error adding animal {animal_name}: {str(e)}", exc_info=True)
                        continue
                
                # Only try to restore selection if we still have a valid widget
                if self._is_widget_valid(self.animal_list):
                    if current_selection:
                        item = self._find_animal_list_item_by_key(current_selection)
                        if item:
                            self.animal_list.setCurrentItem(item)
                            logger.debug(f"Restored selection to: {current_selection}")
                    elif self.animal_list.count() > 0:
                        self.animal_list.setCurrentRow(0)
                        logger.debug("Selected first animal in list")
                
                logger.info("Successfully loaded %d/%d animals into the list", 
                          added_count, len(animal_items))
                return added_count > 0
                
            finally:
                # Always restore signal blocking state
                if hasattr(self, 'animal_list') and self._is_widget_valid(self.animal_list):
                    self.animal_list.blockSignals(was_blocked)
            
        except RuntimeError as e:
            if 'wrapped C/C++' in str(e):
                logger.warning("Widget was deleted during operation")
                return False
            logger.error(f"Runtime error in _load_animal_list: {str(e)}", exc_info=True)
            return False
            
        except Exception as e:
            logger.error(f"Unexpected error in _load_animal_list: {str(e)}", exc_info=True)
            return False
    
    def _on_animal_selected(self):
        """Handle selection of an animal from the list."""
        selected_items = self.animal_list.selectedItems()
        if not selected_items:
            return
        
        animal_name = self._animal_key_from_item(selected_items[0])
        self.animal_name = animal_name
        self._update_animal_details(animal_name)
    
    def _validate_animal_data(self, animal_data, animal_name):
        """Validate that the animal data has the required fields."""
        if not isinstance(animal_data, dict):
            logger.warning(f"Invalid animal data for {animal_name}: expected dict, got {type(animal_data)}")
            return False
            
        required_fields = ['name']
        for field in required_fields:
            if field not in animal_data:
                logger.warning(f"Missing required field '{field}' in data for {animal_name}")
                return False
                
        return True
    
    def _update_animal_details(self, animal_name):
        """Update the UI to display the selected animal's details.
        
        Args:
            animal_name: Name of the animal to display
        """
        if not animal_name:
            logger.warning("No animal name provided to _update_animal_details")
            return
            
        try:
            logger.debug(f"Updating details for animal: {animal_name}")
            
            # Update the window title to show the current animal
            self.setWindowTitle(f"{self._get_message('plugin.animal_reports.title', 'Animal Report')} - {animal_name}")
            
            # Get the animal data
            animals_dict = self._get_animals_dict(self.data)
            if not animals_dict:
                logger.warning("No animals found in data")
                return
                
            if animal_name not in animals_dict:
                logger.warning(f"Animal '{animal_name}' not found in data. Available animals: {list(animals_dict.keys())}")
                return
                
            animal_data = animals_dict[animal_name]
            
            # Validate the animal data
            if not self._validate_animal_data(animal_data, animal_name):
                logger.warning(f"Invalid data for animal: {animal_name}")
                return
                
            self.current_animal_data = animal_data
            self.animal_name = animal_name
            
            # Ensure the display name is set in the animal data
            if 'name' not in self.current_animal_data:
                self.current_animal_data['name'] = self._display_animal_name(animal_name, animal_data)
            
            # Update the UI with the animal's data
            self._update_ui()
            
            # Update the timeline if the method exists
            if hasattr(self, '_update_timeline') and callable(self._update_timeline):
                self._update_timeline(animal_data)
                
            logger.info(f"Successfully updated UI for animal: {animal_name}")
            
        except Exception as e:
            logger.error(f"Error updating animal details for {animal_name}: {str(e)}", exc_info=True)
            QMessageBox.critical(
                self,
                self._get_message('error.update_failed', 'Update Failed'),
                self._get_message('error.update_failed_details', 
                               f'Failed to update details for {animal_name}. See log for details.')
            )
    
    def _select_animal(self, animal_name):
        """Select and display the specified animal's data."""
        try:
            if not animal_name:
                logger.warning("No animal name provided to _select_animal")
                return
                
            logger.debug(f"Selecting animal: {animal_name}")
            
            # Get the animals dictionary
            animals_dict = self._get_animals_dict(self.data)
            if not animals_dict:
                logger.warning("No animals found in data")
                return
                
            if animal_name not in animals_dict:
                logger.warning(f"Animal '{animal_name}' not found in data. Available animals: {list(animals_dict.keys())}")
                return
            
            animal_data = animals_dict[animal_name]
            
            # Validate the animal data
            if not self._validate_animal_data(animal_data, animal_name):
                logger.warning(f"Invalid data for animal: {animal_name}")
                return
            
            self.current_animal_data = animal_data
            self.animal_name = animal_name
            
            # Ensure the display name is set in the animal data
            if 'name' not in self.current_animal_data:
                self.current_animal_data['name'] = self._display_animal_name(animal_name, animal_data)
            
            # Update the timeline with the selected animal's data
            if hasattr(self, '_update_timeline') and callable(self._update_timeline):
                self._update_timeline(self.current_animal_data)
            
            # Update the rest of the UI
            self._update_ui()
            
            logger.info(f"Successfully selected animal: {animal_name}")
            
        except Exception as e:
            logger.error(f"Error selecting animal {animal_name}: {str(e)}", exc_info=True)
            QMessageBox.critical(
                self,
                self._get_message('error.selection_failed', 'Selection Failed'),
                self._get_message('error.selection_failed_details',
                               f'Failed to select {animal_name}. See log for details.')
            )
    
    def _calculate_age(self, birth_date_str):
        """Calculate age from birth date string (YYYY-MM-DD format)."""
        if not birth_date_str:
            return ""
            
        try:
            from datetime import datetime
            birth_date = datetime.strptime(birth_date_str, '%Y-%m-%d').date()
            today = datetime.now().date()
            
            years = today.year - birth_date.year
            months = today.month - birth_date.month
            
            if months < 0:
                years -= 1
                months += 12
                
            if years > 0:
                return f"{years} years, {months} months"
            return f"{months} months"
            
        except Exception as e:
            logger.warning(f"Error calculating age: {str(e)}")
            return ""
            
    def _update_ui(self):
        """Update the UI to display the current animal's data."""
        if not self.current_animal_data:
            return
        
        try:
            # Update window title
            display_name = self._display_animal_name(self.animal_name, self.current_animal_data)
            self.setWindowTitle(f"{self._get_message('plugin.animal_reports.title', 'Animal Report')} - {display_name}")
            
            # Update details table
            if hasattr(self, 'details_table') and self._is_widget_valid(self.details_table):
                birth_date = self.current_animal_data.get('geburt', '')
                current_age = self._calculate_age(birth_date)
                
                details = [
                    (self._get_message('label.name', 'Name'), display_name),
                    (self._get_message('label.id', 'ID'), self.current_animal_data.get('id', '')),
                    (self._get_message('label.species', 'Species'), self.current_animal_data.get('species', '')),
                    (self._get_message('label.status', 'Status'), self._get_animal_status(self.current_animal_data)),
                    (self._get_message('label.birth_date', 'Birth Date'), birth_date),
                    (self._get_message('label.current_age', 'Current Age'), current_age),
                ]
                
                self.details_table.setRowCount(len(details))
                for row, (key, value) in enumerate(details):
                    self.details_table.setItem(row, 0, QTableWidgetItem(key))
                    self.details_table.setItem(row, 1, QTableWidgetItem(str(value) if value is not None else ''))
            
            # Update timeline with debug info
            if hasattr(self, 'timeline_table') and self._is_widget_valid(self.timeline_table):
                logger.debug(f"Updating timeline for {self.animal_name}")
                logger.debug(f"Animal data: {self.current_animal_data}")
                self._update_timeline(self.current_animal_data)
                logger.debug(f"Timeline table rows: {self.timeline_table.rowCount()}")
            
            # Update status bar
            if hasattr(self, 'statusBar'):
                self.statusBar().showMessage(
                    self._get_message('status.animal_loaded', 'Loaded data for {name}').format(name=display_name),
                    5000
                )
                
        except Exception as e:
            logger.error(f"Error updating UI: {str(e)}", exc_info=True)
            if hasattr(self, 'statusBar'):
                self.statusBar().showMessage(
                    self._get_message('error.update_failed', 'Failed to update UI'), 
                    5000
                )
    
    def _update_measurements_table(self):
        """Update the measurements table with the current animal's data."""
        measurements = self.current_animal_data.get('messungen', [])
        self.prog_table.setRowCount(len(measurements))
        
        for i, m in enumerate(measurements):
            self.prog_table.setItem(i, 0, QTableWidgetItem(m.get('datum', '')))
            self.prog_table.setItem(i, 1, QTableWidgetItem(str(m.get('wert', ''))))
            self.prog_table.setItem(i, 2, QTableWidgetItem(m.get('bemerkung', '')))
    
    def _update_procedures_table(self):
        """Update the procedures table with the current animal's data."""
        procedures = self.current_animal_data.get('prozeduren', [])
        self.procedures_table.setRowCount(len(procedures))
        
        for i, p in enumerate(procedures):
            self.procedures_table.setItem(i, 0, QTableWidgetItem(p.get('datum', '')))
            self.procedures_table.setItem(i, 1, QTableWidgetItem(p.get('typ', '')))
            self.procedures_table.setItem(i, 2, QTableWidgetItem(p.get('details', '')))
            self.procedures_table.setItem(i, 3, QTableWidgetItem(p.get('tierarzt', '')))
    
    def _remove_measurement(self):
        """Remove the selected measurement."""
        selected = self.prog_table.selectedItems()
        if not selected:
            return
        
        # Get unique row indices
        rows = {item.row() for item in selected}
        
        # Remove from data
        if 'messungen' in self.current_animal_data:
            for row in sorted(rows, reverse=True):
                if 0 <= row < len(self.current_animal_data['messungen']):
                    del self.current_animal_data['messungen'][row]
        
        # Update UI
        self._update_measurements_table()
        self._on_data_changed()
    
    def _remove_procedure(self):
        """Remove the selected procedure."""
        selected = self.procedures_table.selectedItems()
        if not selected:
            return
        
        # Get unique row indices
        rows = {item.row() for item in selected}
        
        # Remove from data
        if 'prozeduren' in self.current_animal_data:
            for row in sorted(rows, reverse=True):
                if 0 <= row < len(self.current_animal_data['prozeduren']):
                    del self.current_animal_data['prozeduren'][row]
        
        # Update UI
        self._update_procedures_table()
        self._on_data_changed()
    
    def _update_current_animal_data(self):
        """Update the current animal's data from the UI."""
        if not self.current_animal_data:
            return
        
        # Update basic info
        self.current_animal_data['name'] = self.name_edit.text()
        
        try:
            self.current_animal_data['referenz_gewicht'] = float(self.reference_weight_edit.text())
        except ValueError:
            pass
        
        # Update status
        status_text = self.status_combo.currentText()
        if status_text == self._get_message('status.archived', 'Archived'):
            self.current_animal_data['status'] = 'archived'
        elif status_text == self._get_message('status.deceased', 'Deceased'):
            self.current_animal_data['status'] = 'deceased'
        else:
            self.current_animal_data['status'] = 'active'
        
        # Update notes
        self.current_animal_data['notes'] = self.notes_edit.toPlainText()
    
    def _on_data_changed(self):
        """Handle changes to the data."""
        if self._initializing:
            return
        
        logger.debug("Data changed, updating UI...")
        self.save_action.setEnabled(True)
        
        # Update the current animal data from UI
        self._update_current_animal_data()
        
        # Save the changes
        self._save_data()
    
    def _save_data(self):
        """Save the current report-owned data to the backend."""
        if not hasattr(self, 'data') or not self.data:
            logger.warning("No data to save")
            return False
        
        try:
            self._report_store.save(self.data)
            logger.info("Successfully saved report data")
            return True
        
        except Exception as e:
            error_msg = f"Error saving report data: {str(e)}"
            logger.error(error_msg, exc_info=True)
            
            # Inform the user
            QMessageBox.critical(
                self,
                self._get_message('error.save_failed', 'Save Failed'),
                self._get_message('error.save_failed_details', 'Failed to save data: {error}').format(error=str(e))
            )
            return False
    
    def _get_message(self, key, default):
        """Get a localized message from the messages dictionary."""
        return self.messages.get(key, default)
    
    def _register_instance(self):
        """Register this instance in the class variable."""
        if self.animal_name:
            # If there's already a window for this animal, close it
            if self.animal_name in AnimalReportsWidget._instances:
                old_instance = AnimalReportsWidget._instances[self.animal_name]
                if old_instance != self:
                    old_instance.close()
            
            # Register this instance
            AnimalReportsWidget._instances[self.animal_name] = self
    
    def closeEvent(self, event):
        """Handle window close event."""
        try:
            # Save any pending changes
            if hasattr(self, '_save_data'):
                self._save_data()
            # Save locked entries if available
            if hasattr(self, '_save_locked_entries'):
                self._save_locked_entries()
        except Exception as e:
            logger.error(f"Error during close: {str(e)}", exc_info=True)
        finally:
            # Unregister this instance
            if hasattr(self, 'animal_name') and self.animal_name in AnimalReportsWidget._instances and \
               AnimalReportsWidget._instances[self.animal_name] == self:
                del AnimalReportsWidget._instances[self.animal_name]
            
            # Call parent's closeEvent
            super().closeEvent(event)


def launch_animal_reports(animal_name=None, parent=None, messages=None, data_file=None):
    """
    Launch the Animal Reports plugin.
    
    Args:
        animal_name: Name of the animal to display. If None, shows the first animal.
        parent: Parent widget
        messages: Dictionary of UI messages for localization
        data_file: Path to the data file. If None, will try to find it automatically
    """
    # Check if we already have a window for this animal
    if animal_name and animal_name in AnimalReportsWidget._instances:
        # Bring existing window to front
        instance = AnimalReportsWidget._instances[animal_name]
        instance.show()
        instance.activateWindow()
        instance.raise_()
        return instance
    
    # Create a new instance
    widget = AnimalReportsWidget(
        animal_name=animal_name, 
        parent=parent, 
        messages=messages,
        data_file=data_file
    )
    widget.show()
    return widget


def main():
    """Main entry point when run as a standalone application."""
    import sys
    from PyQt6.QtWidgets import QApplication
    
    # Set up logging
    logging.basicConfig(level=logging.INFO)
    
    # Create application
    app = QApplication(sys.argv)
    
    # Try to find the data file in common locations
    data_file = None
    possible_paths = [
        'progtrack_daten.json',
        os.path.expanduser('~/.progtrack/progtrack_daten.json'),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'progtrack_daten.json')
    ]
    
    for path in possible_paths:
        if os.path.exists(path):
            data_file = os.path.abspath(path)
            break
    
    # If no data file found, show error and exit
    if not data_file:
        error_msg = (
            "Could not find ProgTrack data file.\n\n"
            "Please ensure you have a valid 'progtrack_daten.json' file in one of these locations:\n"
            f"- {os.path.abspath('.')}\n"
            f"- {os.path.expanduser('~/.progtrack/')}\n"
            f"- {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}"
        )
        QMessageBox.critical(
            None,
            "Error - ProgTrack Animal Reports",
            error_msg
        )
        return 1
    
    # Set the data file as a class variable so it's available to all instances
    AnimalReportsWidget.DATA_FILE = data_file
    
    # Load test messages (in a real standalone version, these would be loaded from language files)
    messages = {
        # General
        'app.title': 'ProgTrack',
        'app.initializing': 'Initializing...',
        'app.loading_data': 'Loading data...',
        'app.ready': 'Ready',
        'app.settings_loaded': 'Settings loaded',
        'app.data_loaded': 'Data loaded',
        'app.ui_initialized': 'UI initialized',
        'status.ready': 'Ready',
        'status.loading': 'Loading...',
        'status.saving': 'Saving...',
        'status.saved': 'Changes saved',
        'status.exported': 'Report exported to {path}',
        'status.data_loaded': 'Data loaded',
        
        # Plugin
        'plugin.animal_reports.title': 'Animal Report',
        
        # Labels
        'label.animal': 'Animal:',
        'label.name': 'Name:',
        'label.reference_weight': 'Reference Weight (kg):',
        'label.status': 'Status:',
        'label.progesterone_measurements': 'Progesterone Measurements:',
        'label.procedures': 'Procedures:',
        'label.notes': 'Notes:',
        
        # Status values
        'status.active': 'Active',
        'status.archived': 'Archived',
        'status.deceased': 'Deceased',
        
        # Headers
        'header.date': 'Date',
        'header.value': 'Value',
        'header.comment': 'Comment',
        'header.type': 'Type',
        'header.details': 'Details',
        'header.veterinarian': 'Veterinarian',
        
        # Buttons
        'button.add': 'Add',
        'button.remove': 'Remove',
        'button.save': 'Save',
        'button.cancel': 'Cancel',
        'button.close': 'Close',
        'button.export': 'Export...',
        
        # Tabs
        'tab.general': 'General',
        'tab.measurements': 'Measurements',
        'tab.procedures': 'Procedures',
        'tab.notes': 'Notes',
        
        # Actions
        'action.save': '&Save',
        'action.export': '&Export...',
        'action.close': '&Close',
        
        # Dialogs
        'dialog.export.title': 'Export Report',
        'dialog.export.filter': 'PDF Files (*.pdf);;All Files (*)',
        
        # Errors
        'error.title': 'Error',
        'error.load_data': 'Failed to load data: {error}',
        'error.save_data': 'Failed to save data: {error}',
        'error.export_failed': 'Failed to export report: {error}',
        'error.data_file_not_found': 'Could not find data file: {path}'
    }
    
    try:
        # Launch the plugin with the found data file
        launch_animal_reports(
            animal_name=sys.argv[1] if len(sys.argv) > 1 else None,
            messages=messages,
            data_file=data_file
        )
        
        # Set application style to match the system
        app.setStyle('Fusion')
        
        # Run the application
        return app.exec()
    except Exception as e:
        QMessageBox.critical(
            None,
            "Fatal Error - ProgTrack Animal Reports",
            f"An unexpected error occurred:\n\n{str(e)}\n\nPlease check the logs for more details."
        )
        logging.exception("Fatal error in Animal Reports")
        return 1


def _clean_html_for_reportlab(html_text: str) -> str:
    """
    Clean HTML text to be compatible with ReportLab's Paragraph.
    Converts HTML formatting tags to ReportLab-compatible format.
    ReportLab supports: <b>, <i>, <u>, <strike>, <sub>, <super>, <br/>, etc.
    """
    import re
    from html import unescape
    
    if not html_text:
        return html_text
    
    text = html_text
    
    # First, handle span tags with color styles - convert to ReportLab font color format
    # Example: <span style='color:red'>text</span> -> <font color="red">text</font>
    def convert_color_span(match):
        color = match.group(1)
        content = match.group(2)
        return f'<font color="{color}">{content}</font>'
    
    text = re.sub(r'<span\s+style=["\']color:\s*(\w+)["\']>(.*?)</span>', convert_color_span, text, flags=re.IGNORECASE | re.DOTALL)
    
    # Convert <strong> to <b>
    text = re.sub(r'<strong(?:\s+[^>]*)?>',  '<b>', text, flags=re.IGNORECASE)
    text = re.sub(r'</strong>', '</b>', text, flags=re.IGNORECASE)
    
    # Convert <em> to <i>
    text = re.sub(r'<em(?:\s+[^>]*)?>',  '<i>', text, flags=re.IGNORECASE)
    text = re.sub(r'</em>', '</i>', text, flags=re.IGNORECASE)
    
    # Remove any attributes from supported tags (b, i, u, etc.) but keep the tags
    # This handles both single and double quotes
    text = re.sub(r'<(b|i|u|strike|sub|super)(?:\s+[^>]+)>', r'<\1>', text, flags=re.IGNORECASE)
    
    # Remove unsupported tags but keep their content (except already converted spans)
    text = re.sub(r'</?(?:div|span|p)[^>]*>', '', text, flags=re.IGNORECASE)
    
    # Convert line breaks
    text = text.replace('<br>', '<br/>')
    text = text.replace('<BR>', '<br/>')
    
    # Decode HTML entities BEFORE escaping ampersands
    text = unescape(text)
    
    # Handle special characters
    text = text.replace('\\xb5', 'μ')
    text = text.replace('\xb5', 'μ')
    
    # Escape special XML characters that aren't part of our allowed tags
    # ReportLab needs proper XML escaping
    # But first protect our valid tags
    valid_tags = []
    tag_pattern = r'<(/?)(?:b|i|u|strike|sub|super|br/?|font[^>]*?)>'
    
    def save_tag(match):
        valid_tags.append(match.group(0))
        return f'___TAG_{len(valid_tags)-1}___'
    
    text = re.sub(tag_pattern, save_tag, text, flags=re.IGNORECASE)
    
    # Now escape special characters in the remaining text
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    
    # CRITICAL: Escape curly braces to prevent ReportLab from interpreting them as format strings
    # This prevents "Replacement index 0 out of range" errors
    text = text.replace('{', '{{')
    text = text.replace('}', '}}')
    
    # Restore valid tags
    for idx, tag in enumerate(valid_tags):
        text = text.replace(f'___TAG_{idx}___', tag)
    
    # Clean up excessive whitespace but preserve line breaks
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Max 2 consecutive newlines
    text = text.strip()
    
    return text


def daily_report_table_spec(include_signatures: bool = True):
    """Return PDF daily-table column keys and widths in centimetres."""
    if include_signatures:
        return (
            ("date", "daily_data", "scores", "signatures", "pi"),
            (1.5, 15.0, 2.0, 4.0, 1.5),
        )
    return (
        ("date", "daily_data", "scores", "pi"),
        (1.5, 19.0, 2.0, 1.5),
    )


def create_monthly_report(
    header_info: dict,
    daily_data: list,
    month: int,
    year: int,
    output_path: str,
    von_date=None,
    bis_date=None,
    messages: dict = None,
    *,
    include_signatures: bool = True,
) -> None:
    """
    Create a PDF report for one animal for one month.
    
    Args:
        header_info (dict): {'Name', 'ID', 'Role', 'Status', 'Birth Date', 'Genotype', 'Statistics'}
        daily_data (list): [{'date': int, 'daily_data': str, 'scores': str, 'signatures': str, 'is_locked': bool}, ...]
        month (int): 1-12
        year (int): YYYY
        output_path (str): Full path to output PDF file
        von_date: Start date of the report range
        bis_date: End date of the report range
        messages (dict): Localization messages dictionary
        include_signatures (bool): Include the complete Signatures table column.
    """
    # Use default empty dict if no messages provided
    if messages is None:
        messages = {}
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Table, TableStyle, Paragraph
        from reportlab.lib.enums import TA_CENTER
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from datetime import datetime
        import os
        
        # Register Unicode font family (regular/bold/italic) so ReportLab
        # can actually render inline <i>/<b> tags in Paragraph content.
        base_font = 'Helvetica'  # default fallback
        try:
            def _first_existing(paths):
                for candidate in paths:
                    if candidate and os.path.exists(candidate):
                        return candidate
                return None

            def _register_font_if_needed(alias: str, path: Optional[str]) -> bool:
                if not path:
                    return False
                if alias not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont(alias, path))
                return True

            plugin_dir = os.path.dirname(__file__)
            families = [
                {
                    'name': 'Arial',
                    'regular': [
                        'C:\\Windows\\Fonts\\arial.ttf',
                        'C:\\Windows\\Fonts\\ARIAL.TTF',
                        'C:\\Windows\\Fonts\\Arial.ttf',
                    ],
                    'bold': [
                        'C:\\Windows\\Fonts\\arialbd.ttf',
                        'C:\\Windows\\Fonts\\ARIALBD.TTF',
                    ],
                    'italic': [
                        'C:\\Windows\\Fonts\\ariali.ttf',
                        'C:\\Windows\\Fonts\\ARIALI.TTF',
                    ],
                    'bold_italic': [
                        'C:\\Windows\\Fonts\\arialbi.ttf',
                        'C:\\Windows\\Fonts\\ARIALBI.TTF',
                    ],
                },
                {
                    'name': 'Calibri',
                    'regular': [
                        'C:\\Windows\\Fonts\\calibri.ttf',
                        'C:\\Windows\\Fonts\\CALIBRI.TTF',
                        'C:\\Windows\\Fonts\\Calibri.ttf',
                    ],
                    'bold': [
                        'C:\\Windows\\Fonts\\calibrib.ttf',
                        'C:\\Windows\\Fonts\\CALIBRIB.TTF',
                    ],
                    'italic': [
                        'C:\\Windows\\Fonts\\calibrii.ttf',
                        'C:\\Windows\\Fonts\\CALIBRII.TTF',
                    ],
                    'bold_italic': [
                        'C:\\Windows\\Fonts\\calibriz.ttf',
                        'C:\\Windows\\Fonts\\CALIBRIZ.TTF',
                    ],
                },
                {
                    'name': 'Verdana',
                    'regular': [
                        'C:\\Windows\\Fonts\\verdana.ttf',
                        'C:\\Windows\\Fonts\\VERDANA.TTF',
                        'C:\\Windows\\Fonts\\Verdana.ttf',
                    ],
                    'bold': [
                        'C:\\Windows\\Fonts\\verdanab.ttf',
                        'C:\\Windows\\Fonts\\VERDANAB.TTF',
                    ],
                    'italic': [
                        'C:\\Windows\\Fonts\\verdanai.ttf',
                        'C:\\Windows\\Fonts\\VERDANAI.TTF',
                    ],
                    'bold_italic': [
                        'C:\\Windows\\Fonts\\verdanaz.ttf',
                        'C:\\Windows\\Fonts\\VERDANAZ.TTF',
                    ],
                },
                {
                    'name': 'DejaVu Sans',
                    'regular': [
                        os.path.join(plugin_dir, 'DejaVuSans.ttf'),
                        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                    ],
                    'bold': [
                        os.path.join(plugin_dir, 'DejaVuSans-Bold.ttf'),
                        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
                    ],
                    'italic': [
                        os.path.join(plugin_dir, 'DejaVuSans-Oblique.ttf'),
                        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf',
                    ],
                    'bold_italic': [
                        os.path.join(plugin_dir, 'DejaVuSans-BoldOblique.ttf'),
                        '/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf',
                    ],
                },
                {
                    'name': 'Liberation Sans',
                    'regular': [
                        '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
                    ],
                    'bold': [
                        '/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf',
                    ],
                    'italic': [
                        '/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf',
                    ],
                    'bold_italic': [
                        '/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf',
                    ],
                },
            ]

            selected = None
            for family in families:
                regular_path = _first_existing(family['regular'])
                if not regular_path:
                    continue
                selected = {
                    'name': family['name'],
                    'regular': regular_path,
                    'bold': _first_existing(family['bold']),
                    'italic': _first_existing(family['italic']),
                    'bold_italic': _first_existing(family['bold_italic']),
                }
                break

            if selected:
                _register_font_if_needed('UnicodeFont', selected['regular'])
                has_bold = _register_font_if_needed('UnicodeFont-Bold', selected['bold'])
                has_italic = _register_font_if_needed('UnicodeFont-Italic', selected['italic'])
                has_bold_italic = _register_font_if_needed('UnicodeFont-BoldItalic', selected['bold_italic'])

                pdfmetrics.registerFontFamily(
                    'UnicodeFont',
                    normal='UnicodeFont',
                    bold='UnicodeFont-Bold' if has_bold else 'UnicodeFont',
                    italic='UnicodeFont-Italic' if has_italic else 'UnicodeFont',
                    boldItalic=(
                        'UnicodeFont-BoldItalic' if has_bold_italic
                        else ('UnicodeFont-Italic' if has_italic else ('UnicodeFont-Bold' if has_bold else 'UnicodeFont'))
                    ),
                )

                base_font = 'UnicodeFont'
                logger.info(
                    "Successfully registered Unicode font family '%s' (regular=%s, italic=%s)",
                    selected['name'],
                    selected['regular'],
                    bool(selected['italic']),
                )
                print(f"[Animal Reports] Using font family: {selected['name']}")
            else:
                # Fall back to Helvetica (won't display Cyrillic properly)
                logger.warning("No Unicode font found in standard paths, using Helvetica (Cyrillic may not display)")
                print("[Animal Reports] WARNING: No Unicode font found! Cyrillic will not display correctly.")
        except Exception as e:
            logger.error(f"Could not register Unicode font: {e}, using Helvetica")
            import traceback
            logger.error(traceback.format_exc())
            base_font = 'Helvetica'
        
        # Month names - use localized if available
        month_names = [''] + [
            messages.get(f"month.{i}", default_month)
            for i, default_month in enumerate([
                'January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November', 'December'
            ], 1)
        ]
        
        # Format date range for display, including project if available
        project = header_info.get('Project', '-')
        if von_date and bis_date:
            date_range_str = f"{von_date.strftime('%d.%m.%Y')} - {bis_date.strftime('%d.%m.%Y')}"
        else:
            date_range_str = f"{month_names[month]} {year}"
        
        # Add project to date range if it's not empty or '-'
        if project and project != '-':
            project_label = messages.get('report.header.project', 'Project')
            date_range_str = f"{project_label}: {project}  |  {date_range_str}"
        
        elements = []
        styles = getSampleStyleSheet()
        
        # Create custom Normal style with Unicode font for Cyrillic support
        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontName=base_font
        )
        
        # Title style - more compact
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Heading1'],
            fontName=base_font,
            fontSize=14,
            textColor=colors.HexColor('#2C3E50'),
            spaceAfter=1,
            alignment=TA_CENTER
        )
        
        # Subtitle style for date range - black text with more space below
        subtitle_style = ParagraphStyle(
            'CustomSubtitle',
            parent=normal_style,
            fontName=base_font,
            fontSize=9,
            textColor=colors.black,
            spaceAfter=10,
            alignment=TA_CENTER
        )
        
        # Create header function that will be called on each page
        def header(canvas, doc):
            canvas.saveState()
            page_width, page_height = doc.pagesize
            # Leave a practical hole-punch margin above the complete header.
            header_top = page_height - 1.5*cm
            # Title subject can be passed explicitly (e.g. "Name (ID + Species)").
            # Fallback to Name and optionally append ID.
            title_subject = header_info.get('Title Subject')
            if title_subject is None or str(title_subject).strip() in ('', '-'):
                base_name = str(header_info.get('Name', 'Unknown'))
                id_text = str(header_info.get('ID', '')).strip()
                title_subject = f"{base_name} ({id_text})" if id_text and id_text != '-' else base_name
            title_subject = str(title_subject).replace('{', '{{').replace('}', '}}')
            report_title = messages.get('report.title', 'Animal Report')
            title = Paragraph(f"<b>{report_title}: {title_subject}</b>", title_style)
            subtitle = Paragraph(date_range_str, subtitle_style)
            w, h = title.wrap(doc.width, doc.topMargin)
            title.drawOn(canvas, doc.leftMargin, header_top - h)
            
            w2, h2 = subtitle.wrap(doc.width, doc.topMargin)
            subtitle.drawOn(canvas, doc.leftMargin, header_top - h - h2 - 0.1*cm)
            
            # Animal information table
            # Wrap all values in Paragraph objects and escape curly braces to prevent format string issues
            def safe_str(value):
                """Convert value to string and escape curly braces."""
                return str(value).replace('{', '{{').replace('}', '}}')
            
            # Localized header labels
            header_data = [
                [Paragraph('<b>' + messages.get('report.header.name', 'Name') + ':</b>', normal_style), 
                 Paragraph(safe_str(header_info.get('Name', '-')), normal_style), 
                 Paragraph('<b>' + messages.get('report.header.role', 'Role') + ':</b>', normal_style), 
                 Paragraph(safe_str(header_info.get('Role', '-')), normal_style)],
                [Paragraph('<b>' + messages.get('report.header.ipid', 'IPID') + ':</b>', normal_style),
                 Paragraph(safe_str(header_info.get('IPID', '-')), normal_style),
                 Paragraph('<b>' + messages.get('report.header.id', 'ID') + ':</b>', normal_style),
                 Paragraph(safe_str(header_info.get('ID', '-')), normal_style)],
                [Paragraph('<b>' + messages.get('report.header.birth_date', 'Birth Date') + ':</b>', normal_style),
                 Paragraph(safe_str(header_info.get('Birth Date', '-')), normal_style),
                 Paragraph('<b>' + messages.get('report.header.status', 'Status') + ':</b>', normal_style),
                 Paragraph(safe_str(header_info.get('Status', '-')), normal_style)],
                [Paragraph('<b>' + messages.get('report.header.genotype', 'Genotype') + ':</b>', normal_style),
                 Paragraph(safe_str(header_info.get('Genotype', '-')), normal_style),
                 '', ''],
                [Paragraph('<b>' + messages.get('report.header.statistics', 'Statistics') + ':</b>', normal_style), 
                 Paragraph(safe_str(header_info.get('Statistics', '-')), normal_style), '', '']
            ]
            
            header_table = Table(header_data, colWidths=[3*cm, 6*cm, 3*cm, 6*cm])
            header_table.setStyle(TableStyle([
                ('FONT', (0, 0), (-1, -1), base_font, 7),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#ECF0F1')),
                ('BOX', (0, 0), (-1, -1), 0.5, colors.grey),
                ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.grey),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 2),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                ('SPAN', (0, 4), (0, 4)),
                ('SPAN', (1, 4), (3, 4)),
            ]))
            
            w3, h3 = header_table.wrap(doc.width, doc.topMargin)
            # Center the table horizontally
            table_x = doc.leftMargin + (doc.width - w3) / 2
            header_table.drawOn(canvas, table_x, header_top - h - h2 - h3 - 0.15*cm)
            
            canvas.restoreState()
        
        # Create PDF document with custom page template - minimal margins for wider table
        doc = BaseDocTemplate(output_path, pagesize=landscape(A4),
                             leftMargin=0.5*cm, rightMargin=0.5*cm,
                             topMargin=7.7*cm, bottomMargin=1*cm)
        
        # Define frame for content (below header)
        frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id='normal')
        template = PageTemplate(id='main', frames=frame, onPage=header)
        doc.addPageTemplates([template])
        
        # Add daily data table
        # Use Paragraph objects for column headers with HTML formatting - localized
        column_keys, column_widths_cm = daily_report_table_spec(include_signatures)
        column_labels = {
            'date': messages.get('report.column.date', 'Date'),
            'daily_data': messages.get('report.column.daily_data', 'Daily Data'),
            'scores': messages.get('report.column.scores', 'Scores'),
            'signatures': messages.get('report.column.signatures', 'Signatures'),
            'pi': messages.get('report.column.pi', 'PI'),
        }
        table_header = [
            Paragraph('<b>' + column_labels[key] + '</b>', normal_style)
            for key in column_keys
        ]
        table_data = [table_header]
        
        for day_entry in daily_data:
            # Convert date from day number to dd/mm/yy format
            day_num = day_entry['date']
            date_obj = datetime(year, month, day_num)
            date_val = date_obj.strftime('%d/%m/%y')
            
            daily_text = day_entry['daily_data'] or ''
            scores = day_entry['scores'] or ''
            # Clean HTML: Convert inline styles to ReportLab format (includes curly brace escaping)
            daily_text = _clean_html_for_reportlab(daily_text)
            scores = _clean_html_for_reportlab(scores)
            
            # Create paragraphs for wrapping text
            daily_para = Paragraph(daily_text if daily_text else '-', normal_style)
            scores_para = Paragraph(scores if scores else '-', normal_style)
            pi_para = Paragraph('-', normal_style)  # Empty PI column
            
            row_values = {
                'date': date_val,
                'daily_data': daily_para,
                'scores': scores_para,
                'pi': pi_para,
            }
            if include_signatures:
                signatures = _clean_html_for_reportlab(
                    day_entry.get('signatures') or ''
                )
                row_values['signatures'] = Paragraph(
                    signatures if signatures else '-', normal_style
                )
            row = [row_values[key] for key in column_keys]
            table_data.append(row)
        
        # Create the table with appropriate column widths
        # Without signatures, Daily Data receives the released 4 cm.
        # splitByRow=1 allows rows to split across pages if needed
        col_widths = [width * cm for width in column_widths_cm]
        data_table = Table(table_data, colWidths=col_widths, repeatRows=1, splitByRow=1)
        
        # Build table style - smaller font for better fitting
        table_style_commands = [
            ('FONT', (0, 0), (-1, 0), base_font, 9),  # Header row
            ('FONT', (0, 1), (-1, -1), base_font, 7),      # All data rows
            ('FONT', (0, 1), (0, -1), base_font, 9),  # Date column larger font
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#3498DB')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('TEXTCOLOR', (0, 1), (0, -1), colors.black),  # Date column black text
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),  # Center all column headers
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # Center date column data
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOX', (0, 0), (-1, -1), 1, colors.black),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 1), (0, -1), 6),  # More padding below dates
        ]
        
        # Add green background for locked rows
        for idx, day_entry in enumerate(daily_data, start=1):
            if day_entry.get('is_locked', False):
                table_style_commands.append(
                    ('BACKGROUND', (0, idx), (-1, idx), colors.HexColor('#C8FFC8'))
                )
        
        data_table.setStyle(TableStyle(table_style_commands))
        elements.append(data_table)
        
        # Build PDF
        doc.build(elements)
        from Plugins.core.institution_branding import brand_generated_pdf
        brand_generated_pdf(self, output_path)
        logger.info(f"Successfully created PDF report: {output_path}")
        
    except ImportError as e:
        logger.error(f"ReportLab not installed: {e}")
        raise ImportError("ReportLab library is required for PDF export. Install with: pip install reportlab")
    except Exception as e:
        logger.error(f"Error creating PDF report: {e}", exc_info=True)
        raise


if __name__ == '__main__':
    sys.exit(main())
