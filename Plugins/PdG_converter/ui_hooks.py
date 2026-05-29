# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: PdG Converter UI hook helpers.

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, 
    QPushButton, QLineEdit, QLabel, QTableWidget, 
    QTableWidgetItem, QComboBox, QMessageBox, QScrollArea,
    QWidget, QFrame, QTabWidget
)
from PyQt6.QtCore import Qt
from datetime import datetime, date


class ConverterDialog(QDialog):
    """PdG Converter dialog for fitting and editing conversion formulas.
    
    Phase 2: Full implementation with new bounded least squares fitting
    """
    
    def __init__(self, parent_app, plugin, animal_name=None):
        super().__init__(parent_app)
        self.parent_app = parent_app
        self.plugin = plugin
        self.animal_name = animal_name
        
        self.setWindowTitle(self.parent_app.messages.get("pdg_converter.dialog.title", "PdG → Progesterone Converter"))
        self.setMinimumSize(700, 500)
        
        self._build_ui()
        self._load_data()
    
    def _build_ui(self):
        """Build the dialog UI."""
        layout = QVBoxLayout(self)
        
        # Animal selector if no animal specified
        if not self.animal_name:
            selector_layout = QHBoxLayout()
            selector_layout.addWidget(QLabel(self.parent_app.messages.get("pdg_converter.label.animal", "Animal:")))
            self.animal_combo = QComboBox()
            # Add 'Select animal' as first option
            self.animal_combo.addItem(self.parent_app.messages.get("pdg_converter.select.animal", "-- Select animal --"))
            self.animal_combo.addItems(sorted(self.parent_app.animals.keys()))
            self.animal_combo.setCurrentIndex(0)  # Select the 'Select animal' option
            self.animal_combo.currentTextChanged.connect(self._on_animal_changed)
            selector_layout.addWidget(self.animal_combo)
            selector_layout.addStretch()
            layout.addLayout(selector_layout)
        
        # Model info section
        info_frame = QFrame()
        info_frame.setFrameStyle(QFrame.Shape.StyledPanel)
        info_layout = QFormLayout(info_frame)
        
        self.model_type_label = QLabel(self.parent_app.messages.get("pdg_converter.status.no_model", "No model fitted"))
        self.n_pairs_label = QLabel("0")
        self.mse_label = QLabel(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
        self.fitted_at_label = QLabel(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
        
        # Model parameters
        self.a0_label = QLabel(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
        self.b1_label = QLabel(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
        self.b2_label = QLabel(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
        self.knot_label = QLabel(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
        
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.model_type", "Model Type:"), self.model_type_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.n_pairs", "Number of Pairs:"), self.n_pairs_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.mse", "MSE:"), self.mse_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.fitted_at", "Fitted At:"), self.fitted_at_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.intercept", "Intercept (a0):"), self.a0_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.slope", "Slope (b1):"), self.b1_label)
        
        # Kink-specific parameters (hidden for linear)
        self.b2_row_widget = QWidget()
        b2_row_layout = QHBoxLayout(self.b2_row_widget)
        b2_row_layout.setContentsMargins(0, 0, 0, 0)
        b2_row_layout.addWidget(self.b2_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.slope_change", "Slope Change (b2):"), self.b2_row_widget)
        
        self.knot_row_widget = QWidget()
        knot_row_layout = QHBoxLayout(self.knot_row_widget)
        knot_row_layout.setContentsMargins(0, 0, 0, 0)
        knot_row_layout.addWidget(self.knot_label)
        info_layout.addRow(self.parent_app.messages.get("pdg_converter.label.knot", "Knot:"), self.knot_row_widget)
        
        layout.addWidget(info_frame)
        
        # Formula display
        formula_layout = QHBoxLayout()
        formula_layout.addWidget(QLabel(self.parent_app.messages.get("pdg_converter.label.formula", "Formula:")))
        self.formula_label = QLabel(self.parent_app.messages.get("pdg_converter.status.no_formula", "No formula available"))
        self.formula_label.setWordWrap(True)
        formula_layout.addWidget(self.formula_label, 1)
        layout.addLayout(formula_layout)
        
        # Paired data table
        layout.addWidget(QLabel(self.parent_app.messages.get("pdg_converter.label.paired_data", "Paired Data (same-day PdG & Progesterone):")))
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels([
            self.parent_app.messages.get("pdg_converter.table.date", "Date"),
            self.parent_app.messages.get("pdg_converter.table.pdg", "PdG (µg/mg Cr)"),
            self.parent_app.messages.get("pdg_converter.table.prog", "Progesterone (ng/ml)")
        ])
        layout.addWidget(self.table)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.fit_btn = QPushButton(self.parent_app.messages.get("pdg_converter.button.fit_model", "Fit Model"))
        self.fit_btn.clicked.connect(self._fit_model)
        button_layout.addWidget(self.fit_btn)
        
        self.clear_btn = QPushButton(self.parent_app.messages.get("pdg_converter.button.clear_model", "Clear Model"))
        self.clear_btn.clicked.connect(self._clear_model)
        button_layout.addWidget(self.clear_btn)
        
        button_layout.addStretch()
        
        close_btn = QPushButton(self.parent_app.messages.get("pdg_converter.button.close", "Close"))
        close_btn.clicked.connect(self.accept)
        button_layout.addWidget(close_btn)
        
        layout.addLayout(button_layout)
    
    def _load_data(self):
        """Load data for the current animal."""
        if not self.animal_name:
            return
        
        animal = self.parent_app.animals.get(self.animal_name)
        if not animal:
            return
        
        # Get paired data
        from .converter import get_paired_data
        paired_dates, paired_pdg, paired_prog = get_paired_data(animal)
        
        # Set header for date column
        self.table.setHorizontalHeaderLabels([
            self.parent_app.messages.get("pdg_converter.table.date", "Date"),
            self.parent_app.messages.get("pdg_converter.table.pdg_short", "PdG"),
            self.parent_app.messages.get("pdg_converter.table.prog_short", "Prog")
        ])
        
        # Populate table
        self.table.setRowCount(len(paired_pdg))
        for i, (date_val, pdg, prog) in enumerate(zip(paired_dates, paired_pdg, paired_prog)):
            # Format date as dd.mm.yyyy
            if isinstance(date_val, (datetime, date)):
                date_str = date_val.strftime('%d.%m.%Y')
            else:
                date_str = str(date_val)
            self.table.setItem(i, 0, QTableWidgetItem(date_str))
            self.table.setItem(i, 1, QTableWidgetItem(f"{pdg:.2f}"))
            self.table.setItem(i, 2, QTableWidgetItem(f"{prog:.2f}"))
        
        # Adjust column widths to fit content
        self.table.resizeColumnsToContents()
        
        # Load existing model if any
        params = self.plugin.get_parameters(self.animal_name)
        self._update_display(params)
    
    def _update_display(self, params):
        """Update UI with model parameters."""
        if params is None:
            self.model_type_label.setText(self.parent_app.messages.get("pdg_converter.status.no_model", "No model fitted"))
            self.n_pairs_label.setText("0")
            self.mse_label.setText(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
            self.fitted_at_label.setText(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
            self.a0_label.setText(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
            self.b1_label.setText(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
            self.b2_label.setText(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
            self.knot_label.setText(self.parent_app.messages.get("pdg_converter.status.na", "N/A"))
            self.formula_label.setText(self.parent_app.messages.get("pdg_converter.status.no_formula", "No formula available"))
            
            # Hide kink-specific rows
            self._set_row_visible(self.b2_row_widget, False)
            self._set_row_visible(self.knot_row_widget, False)
            return
        
        # Update model info
        self.model_type_label.setText(params.get('model_type', 'Unknown').capitalize())
        self.n_pairs_label.setText(str(params.get('n_pairs', 0)))
        self.mse_label.setText(f"{params.get('mse', 0):.4f}")
        self.fitted_at_label.setText(params.get('fitted_at', self.parent_app.messages.get("pdg_converter.status.unknown", "Unknown"))[:19])  # Trim to seconds
        
        # Update parameters
        self.a0_label.setText(f"{params.get('a0', 0):.4f}")
        self.b1_label.setText(f"{params.get('b1', 0):.4f}")
        
        # Show kink-specific parameters if applicable
        if params.get('model_type') == 'kink':
            self.b2_label.setText(f"{params.get('b2', 0):.4f}")
            self.knot_label.setText(f"{params.get('knot', 0):.2f}")
            self._set_row_visible(self.b2_row_widget, True)
            self._set_row_visible(self.knot_row_widget, True)
            
            # Show both slopes for kink
            slope_high = params.get('b1', 0) + params.get('b2', 0)
            self.b1_label.setText(f"{params.get('b1', 0):.4f} (low), {slope_high:.4f} (high)")
        else:
            self._set_row_visible(self.b2_row_widget, False)
            self._set_row_visible(self.knot_row_widget, False)
        
        # Update formula
        formula = self.plugin.converter.generate_formula_string(params)
        self.formula_label.setText(formula)
    
    def _set_row_visible(self, row_widget, visible):
        """Set visibility of a form layout row."""
        row_widget.setVisible(visible)
    
    def _fit_model(self):
        """Fit the conversion model."""
        if not self.animal_name:
            return
        
        try:
            params, paired_pdg, paired_prog = self.plugin.fit_animal_model(self.animal_name)
        except Exception as e:
            import traceback
            error_msg = str(e)
            detail = traceback.format_exc()
            print(f"Fit error: {error_msg}\n{detail}")  # Log to console
            QMessageBox.critical(
                self,
                self.parent_app.messages.get("pdg_converter.error.fitting_error", "Fitting Error"),
                self.parent_app.messages.get("pdg_converter.error.fitting_failed",
                    "Could not fit model:\n{error}\n\nIf scipy is missing, install it: pip install scipy\nOtherwise, check that you have paired PdG/Prog measurements on the same dates."
                ).format(error=error_msg)
            )
            return
        
        if params is None:
            print(f"DEBUG: paired_pdg count = {len(paired_pdg)}, paired_prog count = {len(paired_prog)}")
            if len(paired_pdg) < 3:
                QMessageBox.warning(
                    self,
                    self.parent_app.messages.get("pdg_converter.error.insufficient_data_title", "Insufficient Data"),
                    self.parent_app.messages.get("pdg_converter.error.insufficient_data",
                        "Need at least 3 paired measurements for fitting.\nCurrently have {n_pairs} pairs."
                    ).format(n_pairs=len(paired_pdg))
                )
            else:
                QMessageBox.warning(
                    self,
                    self.parent_app.messages.get("pdg_converter.error.fit_failed_title", "Fit Failed"),
                    self.parent_app.messages.get("pdg_converter.error.fit_failed",
                        "Could not fit model. Have {n_pairs} pairs but fitting returned None.\nCheck console for details."
                    ).format(n_pairs=len(paired_pdg))
                )
            return
        
        self._update_display(params)
        
        # Refresh animal dialog tabs if they exist
        # _pdg_tabs is stored in PdGCapability (self.plugin.app.pdg_cap.hooks)
        cap = getattr(self.plugin.app, 'pdg_cap', None)
        if cap and hasattr(cap.hooks, '_pdg_tabs') and self.animal_name in cap.hooks._pdg_tabs:
            _, conv_tab = cap.hooks._pdg_tabs[self.animal_name]
            if hasattr(conv_tab, '_refresh_table'):
                conv_tab._refresh_table()
            if hasattr(conv_tab, '_update_info'):
                conv_tab._update_info()
        
        QMessageBox.information(
            self,
            self.parent_app.messages.get("pdg_converter.success.title", "Success"),
            self.parent_app.messages.get("pdg_converter.success.model_fitted", "Model fitted successfully!")
        )
    
    def _clear_model(self):
        """Clear the fitted model."""
        if not self.animal_name:
            return
        
        reply = QMessageBox.question(
            self,
            self.parent_app.messages.get("pdg_converter.confirm.title", "Confirm"),
            self.parent_app.messages.get("pdg_converter.confirm.clear_model", "Clear the fitted model? This cannot be undone."),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.plugin.save_parameters(self.animal_name, None)
            self._update_display(None)
    
    def _on_animal_changed(self, animal_name):
        """Handle animal selection change."""
        self.animal_name = animal_name
        self._load_data()


class PdGTabWidget(QWidget):
    """Tab widget for displaying/editing PdG measurements.
    
    Used by extend_animal_dialog() hook.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # Phase 1: Move code from _dlg_partner / _dlg_female_animal here
        pass


class UnifiedProgTabWidget(QWidget):
    """Tab widget for displaying unified progesterone (blood + converted PdG).
    
    Used by extend_animal_dialog() hook.
    """
    
    def __init__(self, parent=None):
        super().__init__(parent)
        # Phase 1: Move code from _dlg_partner / _dlg_female_animal here
        pass


# Utility functions for building UI components

def create_pdg_table(parent, pdg_items, eval_formula_func):
    """Create table widget showing PdG values and converted progesterone.
    
    Args:
        parent: Parent widget
        pdg_items: List of {'datum': datetime, 'wert': float} dicts
        eval_formula_func: Function to convert PdG to progesterone
        
    Returns:
        QTableWidget configured with columns: Date, PdG, Prog Equivalent
    """
    table = QTableWidget()
    table.setColumnCount(3)
    # Headers set by caller for i18n
    table.setHorizontalHeaderLabels(["Date", "PdG (µg/mg Cr)", "Prog Equiv. (ng/ml)"])
    
    # Phase 1: Move table population code here
    # Phase 2: Update to use parameter-based prediction
    
    return table


def create_color_button(parent, color_hex, callback=None):
    """Create color picker button with given initial color.
    
    Args:
        parent: Parent widget
        color_hex: Initial color as hex string (e.g., '#FF8C00')
        callback: Function to call when color changes
        
    Returns:
        QPushButton configured as color picker
    """
    btn = QPushButton(parent)
    btn.setStyleSheet(f"background-color: {color_hex}; border: 1px solid #000;")
    btn.setProperty('color', color_hex)
    btn.setFixedSize(30, 20)
    
    # Phase 1: Connect to existing color picker dialog
    # Phase 2: Keep same behavior
    
    return btn


def create_marker_combo(parent, current_marker='o'):
    """Create combo box with marker style options.
    
    Args:
        parent: Parent widget
        current_marker: Currently selected marker style
        
    Returns:
        QComboBox with marker options
    """
    combo = QComboBox(parent)
    
    marker_options = [
        ('o', 'Circle'),
        ('s', 'Square'),
        ('^', 'Triangle Up'),
        ('v', 'Triangle Down'),
        ('D', 'Diamond'),
        ('*', 'Star'),
        ('+', 'Plus'),
        ('x', 'Cross'),
    ]
    
    for value, label in marker_options:
        combo.addItem(label, value)
    
    idx = combo.findData(current_marker)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    
    return combo
