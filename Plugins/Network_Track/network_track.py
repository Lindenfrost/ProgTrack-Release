# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Network Track file-based team chat.

import os
import sys
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict

from PyQt6.QtCore import Qt, QTimer, QSize, QUrl
from PyQt6.QtGui import QIcon, QFont, QPixmap
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QLineEdit, QPushButton,
    QLabel, QHeaderView, QMessageBox, QDialog, QFormLayout,
    QCheckBox, QSpinBox, QGroupBox, QDialogButtonBox, QMenu,
    QGridLayout, QToolButton, QTextEdit
)

# QtMultimedia is optional - plugin works without sound
try:
    from PyQt6.QtMultimedia import QSoundEffect
    SOUND_AVAILABLE = True
except ImportError:
    SOUND_AVAILABLE = False
    logging.info("PyQt6.QtMultimedia not installed - sound notifications will be disabled")

# Set up paths
PLUGIN_DIR = Path(__file__).resolve().parent
ROOT_DIR = PLUGIN_DIR.parent.parent
ICON_DIR = ROOT_DIR / "icons"
CHAT_LOG_FILE = PLUGIN_DIR / "chat_log.txt"
SETTINGS_FILE = PLUGIN_DIR / "network_track_settings.json"
NOTIFICATION_SOUND = PLUGIN_DIR / "notification.wav"

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('NetworkTrack')

# Icon discovery function
def discover_icons() -> Dict[str, Path]:
    """Discover available PNG icons from icons folder.
    
    Returns:
        Dictionary mapping icon names (without extension) to file paths.
        Excludes splash.png and *.ico files.
    """
    icons = {}
    if not ICON_DIR.exists():
        logger.warning(f"Icons directory not found: {ICON_DIR}")
        return icons
    
    for icon_file in ICON_DIR.glob("*.png"):
        # Exclude splash.png (case-insensitive)
        if icon_file.name.lower() == 'splash.png':
            continue
        
        # Get icon name without extension
        icon_name = icon_file.stem.lower()
        icons[icon_name] = icon_file
        logger.info(f"Discovered icon: {icon_name} -> {icon_file.name}")
    
    return icons

# Icon helper functions (same as main ProgTrack)
def _set_shared_icon(box: QMessageBox, mtype: str):
    """Set shared icon for message box."""
    icon_file = ICON_DIR / f"{mtype}.png"
    if icon_file.exists():
        pix = QPixmap(str(icon_file))
        if not pix.isNull():
            box.setIconPixmap(pix)
    else:
        # Fallback to built-in Qt icons
        fallback = {
            "warning": QMessageBox.Icon.Warning,
            "information": QMessageBox.Icon.Information,
            "critical": QMessageBox.Icon.Critical,
            "question": QMessageBox.Icon.Question,
            "error": QMessageBox.Icon.Critical,
        }
        if mtype in fallback:
            box.setIcon(fallback[mtype])

def show_message(parent, msg_type: str, title: str, text: str, 
                buttons=QMessageBox.StandardButton.Ok):
    """Show message box with custom icons (same as main ProgTrack)."""
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(text)
    _set_shared_icon(msg, msg_type)
    msg.setStandardButtons(buttons)
    return msg.exec()


class SettingsDialog(QDialog):
    """Settings dialog for Network Track."""
    
    def __init__(self, settings: Dict, parent=None, master_locked: bool = False):
        super().__init__(parent)
        self.settings = settings.copy()
        self.master_locked = master_locked
        self.setWindowTitle("Network Track Settings")
        self.setModal(True)
        self._init_ui()
        
    def _init_ui(self):
        """Initialize settings dialog UI."""
        layout = QVBoxLayout(self)
        
        # Notification group
        notif_group = QGroupBox("Notifications")
        notif_layout = QFormLayout()
        
        self.bring_to_front_cb = QCheckBox()
        self.bring_to_front_cb.setChecked(self.settings.get('bring_to_front', True))
        notif_layout.addRow("Bring window to front on new message:", self.bring_to_front_cb)
        
        self.play_sound_cb = QCheckBox()
        if SOUND_AVAILABLE:
            self.play_sound_cb.setChecked(self.settings.get('play_sound', True))
        else:
            self.play_sound_cb.setChecked(False)
            self.play_sound_cb.setEnabled(False)
            self.play_sound_cb.setToolTip("Sound unavailable: Install PyQt6-Multimedia to enable")
        notif_layout.addRow("Play sound on new message:", self.play_sound_cb)
        
        notif_group.setLayout(notif_layout)
        layout.addWidget(notif_group)
        
        # Polling group
        poll_group = QGroupBox("File Monitoring")
        poll_layout = QFormLayout()
        
        self.polling_interval = QSpinBox()
        self.polling_interval.setMinimum(2)
        self.polling_interval.setMaximum(30)
        self.polling_interval.setValue(self.settings.get('polling_interval', 10))
        self.polling_interval.setSuffix(" seconds")
        poll_layout.addRow("Polling interval:", self.polling_interval)
        
        poll_group.setLayout(poll_layout)
        layout.addWidget(poll_group)
        
        # User group
        user_group = QGroupBox("User Settings")
        user_layout = QFormLayout()
        
        self.default_name = QLineEdit()
        self.default_name.setText(self.settings.get('default_name', ''))
        self.default_name.setPlaceholderText("Enter your name")
        user_layout.addRow("Default name:", self.default_name)
        
        user_group.setLayout(user_layout)
        layout.addWidget(user_group)
        # Hide entire user settings section when Master_Track manages the name
        if self.master_locked:
            user_group.setVisible(False)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | 
            QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
    def get_settings(self) -> Dict:
        """Get the updated settings."""
        return {
            'bring_to_front': self.bring_to_front_cb.isChecked(),
            'play_sound': self.play_sound_cb.isChecked(),
            'polling_interval': self.polling_interval.value(),
            'default_name': self.default_name.text().strip()
        }


class NetworkTrackWidget(QMainWindow):
    """Main widget for the Network Track plugin - multi-user chat window."""
    
    def __init__(self, messages: Optional[Dict] = None, parent=None, app=None):
        super().__init__(parent)
        self.messages = messages or {}
        self.app = app
        self.settings = self._load_settings()
        self.last_line_count = 0
        self.sound_effect = None
        
        # Discover available icons
        self.available_icons = discover_icons()
        logger.info(f"Discovered {len(self.available_icons)} icons: {list(self.available_icons.keys())}")
        
        # Set window properties
        self.setWindowTitle("Network Track - Chat")
        self.setMinimumSize(800, 600)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Set window icon
        icon_path = ICON_DIR / 'progtrack_icon.ico'
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        
        # Initialize chat log file
        self._init_chat_log()
        
        # Initialize UI
        self._init_ui()
        
        # Initialize sound effect
        self._init_sound()
        
        # Load initial chat content
        self._load_chat_log()
        
        # Autofill name from Master_Track if available
        self._apply_master_name_state()
        
        # Start file monitoring timer
        self._start_monitoring()
        
    def _load_settings(self) -> Dict:
        """Load settings from JSON file."""
        default_settings = {
            'bring_to_front': True,
            'play_sound': True,
            'polling_interval': 10,
            'default_name': ''
        }
        
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    default_settings.update(loaded)
            except Exception as e:
                logger.error(f"Error loading settings: {e}")
        
        return default_settings
    
    def _save_settings(self):
        """Save settings to JSON file."""
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
    
    def _init_chat_log(self):
        """Initialize chat log file if it doesn't exist."""
        if not CHAT_LOG_FILE.exists():
            try:
                CHAT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
                CHAT_LOG_FILE.touch()
                logger.info(f"Created chat log file: {CHAT_LOG_FILE}")
            except Exception as e:
                logger.error(f"Error creating chat log file: {e}")
    
    def _init_ui(self):
        """Initialize the user interface."""
        # Create central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Chat display table
        self.chat_table = QTableWidget()
        self.chat_table.setColumnCount(4)
        self.chat_table.setHorizontalHeaderLabels(["Timestamp", "Name", "Message", ""])
        
        # Configure column widths
        header = self.chat_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.chat_table.setColumnWidth(3, 28)
        
        # Configure row heights — Interactive mode so setRowHeight() is not overridden
        vertical_header = self.chat_table.verticalHeader()
        vertical_header.setVisible(False)
        vertical_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        
        self.chat_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.chat_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.chat_table.setAlternatingRowColors(True)
        
        layout.addWidget(self.chat_table, 1)
        
        # Input area
        input_layout = QHBoxLayout()
        
        # Name input
        name_label = QLabel("Name:")
        input_layout.addWidget(name_label)
        
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Your name")
        self.name_input.setText(self.settings.get('default_name', ''))
        self.name_input.setMaximumWidth(150)
        input_layout.addWidget(self.name_input)
        
        # Message input
        message_label = QLabel("Message:")
        input_layout.addWidget(message_label)
        
        self.message_input = QLineEdit()
        self.message_input.setPlaceholderText("Type your message here...")
        self.message_input.returnPressed.connect(self._send_message)
        input_layout.addWidget(self.message_input, 1)
        
        # Icon picker button
        self.icon_button = QToolButton()
        self.icon_button.setText("☺")
        self.icon_button.setToolTip("Insert icon")
        self.icon_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.icon_button.setMenu(self._create_icon_menu())
        input_layout.addWidget(self.icon_button)
        
        # Send button
        send_button = QPushButton("Send")
        send_button.clicked.connect(self._send_message)
        send_button.setDefault(True)
        input_layout.addWidget(send_button)
        
        # Settings button
        settings_button = QPushButton("Settings")
        settings_button.clicked.connect(self._show_settings)
        input_layout.addWidget(settings_button)
        
        layout.addLayout(input_layout)
        
        # Status bar
        self.statusBar().showMessage("Ready")

        # Edit-button state
        self._last_own_row: int = -1
    
    def _create_icon_menu(self) -> QMenu:
        """Create icon picker menu with 2-column grid layout."""
        menu = QMenu(self)
        
        # Create widget with grid layout for the menu
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setSpacing(5)
        grid.setContentsMargins(5, 5, 5, 5)
        
        # Sort icons alphabetically
        sorted_icons = sorted(self.available_icons.items())
        
        # Add icons to grid (2 columns)
        row, col = 0, 0
        for icon_name, icon_path in sorted_icons:
            btn = QPushButton()
            
            # Load and scale icon to 40x40
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                scaled_pixmap = pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, 
                                              Qt.TransformationMode.SmoothTransformation)
                btn.setIcon(QIcon(scaled_pixmap))
                btn.setIconSize(QSize(40, 40))
            
            # Set tooltip with icon name
            btn.setToolTip(icon_name)
            btn.setFixedSize(50, 50)
            # Center icon by removing padding
            btn.setStyleSheet("QPushButton { padding: 0px; }")
            
            # Connect to insertion handler
            btn.clicked.connect(lambda checked, name=icon_name: self._insert_icon(name))
            
            grid.addWidget(btn, row, col)
            
            # Move to next position
            col += 1
            if col >= 3:  # 3 columns
                col = 0
                row += 1
        
        # Add widget to menu using QWidgetAction
        from PyQt6.QtWidgets import QWidgetAction
        widget_action = QWidgetAction(menu)
        widget_action.setDefaultWidget(widget)
        menu.addAction(widget_action)
        
        return menu
    
    def _get_master_user(self) -> Optional[str]:
        """Return the display name of the currently logged-in Master_Track user, or None."""
        mt = getattr(self.app, 'master_track', None)
        if mt is None or not mt.is_logged_in:
            return None
        user = mt._current_user_record()
        if user:
            return user.get('display_name') or user.get('username') or None
        return mt.current_username

    def _apply_master_name_state(self) -> None:
        """Autofill and optionally lock the name field from Master_Track."""
        master_name = self._get_master_user()
        if master_name:
            self.name_input.setText(master_name)
            self.name_input.setReadOnly(True)
            self.name_input.setStyleSheet("QLineEdit { color: grey; }")
            self.name_input.setToolTip("Name set by Master Track login")
        else:
            # Guest: use the configured default name (read-only so it cannot be changed)
            default_name = self.settings.get('default_name', '') or 'Guest'
            self.name_input.setText(default_name)
            self.name_input.setReadOnly(True)
            self.name_input.setStyleSheet("QLineEdit { color: grey; }")
            self.name_input.setToolTip("Guest user – name is set in Settings")

    def _can_send(self) -> bool:
        """Return True if the current user has permission to send messages."""
        mt = getattr(self.app, 'master_track', None)
        if mt is None:
            return True
        disabled = getattr(self.app, '_disabled_plugins', set())
        if 'master_track' in disabled:
            return True
        # Guests (not logged in) can always send under the default name
        if not getattr(mt, 'is_logged_in', False):
            return True
        return mt.can('network.create_entry')

    def _can_edit(self) -> bool:
        """Return True if the current user has permission to edit messages."""
        mt = getattr(self.app, 'master_track', None)
        if mt is None:
            return True
        disabled = getattr(self.app, '_disabled_plugins', set())
        if 'master_track' in disabled:
            return True
        return mt.can('network.edit_entry')

    def _insert_icon(self, icon_name: str):
        """Insert icon pattern at cursor position in message input."""
        cursor_pos = self.message_input.cursorPosition()
        current_text = self.message_input.text()
        
        # Insert :iconname: pattern at cursor position
        pattern = f":{icon_name}:"
        new_text = current_text[:cursor_pos] + pattern + current_text[cursor_pos:]
        
        self.message_input.setText(new_text)
        # Move cursor after inserted pattern
        self.message_input.setCursorPosition(cursor_pos + len(pattern))
        
        # Focus back on message input
        self.message_input.setFocus()
        
        logger.info(f"Inserted icon pattern: {pattern}")
    
    def _parse_message_with_icons(self, message: str) -> str:
        """Parse message text and replace :iconname: patterns with HTML img tags.
        
        Args:
            message: Raw message text with :iconname: patterns
            
        Returns:
            HTML string with icon patterns replaced by <img> tags
        """
        # Escape HTML special characters first
        import html
        message = html.escape(message)
        
        # Find all :iconname: patterns (case-insensitive)
        pattern = r':([a-zA-Z_]+):'
        
        def replace_icon(match):
            icon_name = match.group(1).lower()
            
            # Check if icon exists
            if icon_name in self.available_icons:
                icon_path = self.available_icons[icon_name]
                # Convert to file:// URL for HTML
                icon_url = icon_path.as_uri()
                return f'<img src="{icon_url}" style="vertical-align: middle;"/>'
            else:
                # Icon not found - return original pattern
                return match.group(0)
        
        # Replace all icon patterns
        html_message = re.sub(pattern, replace_icon, message)
        
        return html_message
    
    def _init_sound(self):
        """Initialize the notification sound effect."""
        self.sound_effect = None
        
        if not SOUND_AVAILABLE:
            logger.info("Sound disabled - PyQt6.QtMultimedia not installed")
            return
        
        try:
            if NOTIFICATION_SOUND.exists():
                self.sound_effect = QSoundEffect()
                self.sound_effect.setSource(QUrl.fromLocalFile(str(NOTIFICATION_SOUND)))
                self.sound_effect.setVolume(0.5)
                logger.info("Notification sound loaded")
            else:
                logger.warning(f"Notification sound not found: {NOTIFICATION_SOUND}")
        except Exception as e:
            logger.error(f"Error initializing sound: {e}")
            self.sound_effect = None
    
    def _start_monitoring(self):
        """Start monitoring the chat log file for changes."""
        self.monitor_timer = QTimer(self)
        self.monitor_timer.timeout.connect(self._check_for_updates)
        interval_ms = self.settings.get('polling_interval', 10) * 1000
        self.monitor_timer.start(interval_ms)
        logger.info(f"File monitoring started (interval: {interval_ms}ms)")
    
    def _load_chat_log(self):
        """Load and display the last 100 lines of the chat log."""
        try:
            if not CHAT_LOG_FILE.exists():
                self.chat_table.setRowCount(0)
                return
            
            with open(CHAT_LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Get last 100 lines
            lines = lines[-100:]
            self.last_line_count = len(lines)
            
            # Clear and populate table
            self.chat_table.setRowCount(0)
            for line in lines:
                self._add_chat_line(line.strip())
            
            # Scroll to bottom
            self.chat_table.scrollToBottom()
            self._refresh_edit_button()
            
            self.statusBar().showMessage(f"Loaded {len(lines)} messages")
            
        except Exception as e:
            logger.error(f"Error loading chat log: {e}")
            show_message(self, "error", "Error", f"Failed to load chat log: {e}")
    
    def _add_chat_line(self, line: str):
        """Add a single chat line to the table."""
        if not line:
            return
        
        # Parse line format: timestamp\tname\tmessage
        parts = line.split('\t', 2)
        if len(parts) != 3:
            logger.warning(f"Invalid chat line format: {line}")
            return
        
        timestamp, name, message = parts
        
        # Add row to table
        row = self.chat_table.rowCount()
        self.chat_table.insertRow(row)
        
        # Set timestamp and name as regular table items
        self.chat_table.setItem(row, 0, QTableWidgetItem(timestamp))
        self.chat_table.setItem(row, 1, QTableWidgetItem(name))
        
        # Parse message for icons and create QLabel with HTML
        html_message = self._parse_message_with_icons(message)
        message_label = QLabel(html_message)
        message_label.setTextFormat(Qt.TextFormat.RichText)
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        message_label.setContentsMargins(5, 2, 5, 2)
        
        # Store raw name/message for edit comparison
        item_ts = self.chat_table.item(row, 0)
        if item_ts:
            item_ts.setData(Qt.ItemDataRole.UserRole, name)

        # Set the label as cell widget
        self.chat_table.setCellWidget(row, 2, message_label)
        if '<img' in html_message:
            # Load each icon's actual pixmap to get the true rendered height,
            # then set the row to that height + padding.  This is more reliable
            # than HTML height= attributes, which Qt may or may not honour.
            max_icon_h = 0
            for m in re.finditer(r':([a-zA-Z_]+):', message):
                iname = m.group(1).lower()
                if iname in self.available_icons:
                    pix = QPixmap(str(self.available_icons[iname]))
                    if not pix.isNull():
                        max_icon_h = max(max_icon_h, pix.height())
            row_h = max(max_icon_h + 12, 40) if max_icon_h > 0 else 76
            self.chat_table.setRowHeight(row, row_h)
        else:
            self.chat_table.resizeRowToContents(row)
    
    def _current_user_name(self) -> Optional[str]:
        """Resolve display name for the current user (MT login or name field)."""
        name = self._get_master_user()
        if not name:
            mt = getattr(self.app, 'master_track', None)
            disabled = getattr(self.app, '_disabled_plugins', set())
            if mt is None or 'master_track' in disabled:
                name = self.name_input.text().strip() or None
        return name

    def _refresh_edit_button(self) -> None:
        """Place the \u270f edit button on the last own message row; remove it from all others."""
        current_name = self._current_user_name()
        # Clear previous button
        if 0 <= self._last_own_row < self.chat_table.rowCount():
            self.chat_table.removeCellWidget(self._last_own_row, 3)
        self._last_own_row = -1
        if not current_name or not self._can_edit():
            return
        # Scan from the bottom to find the last row belonging to current user
        for r in range(self.chat_table.rowCount() - 1, -1, -1):
            name_item = self.chat_table.item(r, 1)
            if name_item and name_item.text() == current_name:
                btn = QPushButton("Edit")
                btn.setFlat(True)
                btn.setFixedSize(34, 22)
                btn.setToolTip("Edit this message")
                btn.clicked.connect(lambda _checked, row=r: self._edit_message_at_row(row))
                self.chat_table.setCellWidget(r, 3, btn)
                self._last_own_row = r
                break

    def _edit_message_at_row(self, row: int) -> None:
        """Open a report-style QTextEdit dialog to edit the message at *row*."""
        ts_item = self.chat_table.item(row, 0)
        name_item = self.chat_table.item(row, 1)
        cell_widget = self.chat_table.cellWidget(row, 2)
        if not ts_item or not name_item:
            return
        ts = ts_item.text()
        author = name_item.text()
        current_text = cell_widget.text() if cell_widget else ""
        import html as _html
        current_text = _html.unescape(re.sub(r'<[^>]+>', '', current_text)).strip()

        dlg = QDialog(self)
        dlg.setWindowTitle("Edit Message")
        dlg.setModal(True)
        dlg.setMinimumSize(580, 220)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(f"Editing message from <b>{author}</b> at {ts}:"))
        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlainText(current_text)
        layout.addWidget(text_edit)
        btn_layout = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.setDefault(True)
        cancel_btn = QPushButton("Cancel")
        save_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new_text = text_edit.toPlainText().strip()
        if not new_text or new_text == current_text:
            return
        edit_ts = datetime.now().strftime("%H:%M")
        self._replace_message_in_log(ts, author, current_text, new_text, edit_ts)
        html_message = self._parse_message_with_icons(
            new_text + f' [edited {edit_ts}]'
        )
        cell_widget.setText(html_message)

    def _replace_message_in_log(self, ts: str, author: str,
                                 old_text: str, new_text: str,
                                 edit_ts: str = "") -> None:
        """Rewrite the chat log file replacing one matching line."""
        try:
            if not CHAT_LOG_FILE.exists():
                return
            with open(CHAT_LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            suffix = f" [edited {edit_ts}]" if edit_ts else " [edited]"
            new_line = f"{ts}\t{author}\t{new_text}{suffix}\n"
            for i, line in enumerate(lines):
                stored = line.rstrip('\n')
                if stored == f"{ts}\t{author}\t{old_text}":
                    lines[i] = new_line
                    break
            with open(CHAT_LOG_FILE, 'w', encoding='utf-8') as f:
                f.writelines(lines)
        except Exception as exc:
            logger.error("Failed to rewrite chat log for edit: %s", exc)

    def _check_for_updates(self):
        """Check if the chat log file has been updated."""
        try:
            if not CHAT_LOG_FILE.exists():
                return
            
            with open(CHAT_LOG_FILE, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            current_count = len(lines)
            
            # Check if there are new messages
            if current_count > self.last_line_count:
                # Get only the new lines
                new_lines = lines[self.last_line_count:]
                
                # Add new lines to display
                for line in new_lines:
                    self._add_chat_line(line.strip())
                
                # Update line count
                self.last_line_count = current_count

                # Trim table to last 100 entries
                while self.chat_table.rowCount() > 100:
                    self.chat_table.removeRow(0)

                # Scroll to bottom
                self.chat_table.scrollToBottom()
                self._refresh_edit_button()

                # Play notification sound
                if self.settings.get('play_sound', True) and self.sound_effect:
                    self.sound_effect.play()

                # Bring window to front or show it (only if not already active)
                if self.settings.get('bring_to_front', True):
                    if not self.isVisible():
                        self.show()
                    if self.isMinimized():
                        self.showNormal()
                    # Only activate/raise if not already the active window
                    if not self.isActiveWindow():
                        self.activateWindow()
                        self.raise_()

                self.statusBar().showMessage("New message(s) received", 3000)
                
        except Exception as e:
            logger.error(f"Error checking for updates: {e}")
    
    def _send_message(self):
        """Send a new message to the chat."""
        if not self._can_send():
            show_message(self, "warning", "Warning", "You do not have permission to send messages.")
            return

        name = self.name_input.text().strip()
        message = self.message_input.text().strip()
        
        if not name:
            show_message(self, "warning", "Warning", "Please enter your name")
            self.name_input.setFocus()
            return
        
        if not message:
            show_message(self, "warning", "Warning", "Please enter a message")
            self.message_input.setFocus()
            return
        
        try:
            # Create timestamp in dd/mm/yy hh:mm format
            timestamp = datetime.now().strftime("%d/%m/%y %H:%M")
            
            # Format: timestamp\tname\tmessage
            chat_line = f"{timestamp}\t{name}\t{message}\n"
            
            # Append to chat log file
            with open(CHAT_LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(chat_line)
            
            # Clear message input
            self.message_input.clear()
            self.message_input.setFocus()
            
            # Reload to show new message
            # Note: The monitoring timer will pick this up, but we can
            # manually add it for immediate feedback
            self._add_chat_line(chat_line.strip())
            self.last_line_count += 1
            
            # Trim to last 100 entries
            while self.chat_table.rowCount() > 100:
                self.chat_table.removeRow(0)
            
            # Scroll to bottom
            self.chat_table.scrollToBottom()
            self._refresh_edit_button()

            self.statusBar().showMessage("Message sent", 2000)
            
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            show_message(self, "error", "Error", f"Failed to send message: {e}")
    
    def refresh_master_name(self) -> None:
        """Re-apply Master_Track name state (call after login/logout)."""
        self._apply_master_name_state()

    def _show_settings(self):
        """Show settings dialog."""
        mt = getattr(self.app, 'master_track', None)
        disabled = getattr(self.app, '_disabled_plugins', set()) if self.app else set()
        master_active = mt is not None and 'master_track' not in disabled
        dialog = SettingsDialog(self.settings, self, master_locked=master_active)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings = dialog.get_settings()
            self._save_settings()
            
            # Update monitoring interval
            interval_ms = self.settings.get('polling_interval', 10) * 1000
            self.monitor_timer.setInterval(interval_ms)
            
            # Update default name (unless locked by Master_Track)
            if self.settings.get('default_name', '') and not self.name_input.isReadOnly():
                self.name_input.setText(self.settings['default_name'])
            
            show_message(self, "information", "Settings", "Settings saved successfully")
    
    def closeEvent(self, event):
        """Handle window close event - hide instead of closing to keep monitoring."""
        # Hide the window instead of closing it to keep monitoring active
        event.ignore()
        self.hide()
        logger.info("Network Track window hidden (monitoring continues)")


# Standalone mode for testing
if __name__ == '__main__':
    from PyQt6.QtWidgets import QApplication
    import sys
    
    app = QApplication(sys.argv)
    window = NetworkTrackWidget()
    window.show()
    sys.exit(app.exec())
