# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: Embryo Track gestation-day prediction tools.

import os
import sys
import json
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from scipy import interpolate
from scipy.optimize import curve_fit

# PyQt6 imports
from PyQt6 import QtWidgets
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QDoubleValidator
from PyQt6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QTableWidget, QTableWidgetItem, QGroupBox, QFormLayout,
    QComboBox, QFileDialog, QMessageBox, QTextEdit
)

# Marmoset gestation period (days) - standard range 143-148 days
# Using conservative upper estimate for due date calculation
MARMOSET_GESTATION_DAYS = 144

# Set up paths
PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(PLUGIN_DIR))
REFERENCE_DATA_FILE = os.path.join(PLUGIN_DIR, 'cranimetry_reference.json')
ICON_DIR = os.path.join(ROOT_DIR, 'icons')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('EmbryoTracker')

# Icon helper functions (same as main ProgTrack)
def _set_shared_icon(box: QMessageBox, mtype: str):
    """Set shared icon for message box."""
    from PyQt6.QtGui import QPixmap
    icon_file = os.path.join(ICON_DIR, f"{mtype}.png")
    if os.path.exists(icon_file):
        pix = QPixmap(icon_file)
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

def show_message(parent, msg_type: str, title: str, text: str, buttons=QMessageBox.StandardButton.Ok):
    """Show message box with custom icons (same as main ProgTrack)."""
    msg = QMessageBox(parent)
    msg.setWindowTitle(title)
    msg.setText(text)
    _set_shared_icon(msg, msg_type)
    msg.setStandardButtons(buttons)
    return msg.exec()

class EmbryoTrackerWidget(QDialog):
    """Main widget for the Embryo Tracker plugin."""
    
    def __init__(self, messages: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.messages = messages or {}
        self.reference_data = {}
        self.prediction_models = {}
        
        # Set window properties
        self.setWindowTitle(self.messages.get("embryo_track.window_title", "Embryo Track - Marmoset Gestation Prediction"))
        self.resize(450, 450)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        
        # Set window icon
        try:
            icon_path = os.path.join(ICON_DIR, 'progtrack_icon.ico')
            if os.path.exists(icon_path):
                from PyQt6.QtGui import QIcon
                self.setWindowIcon(QIcon(icon_path))
        except Exception as e:
            logger.warning(f"Could not set window icon: {e}")
        
        # Initialize UI and load data
        self._init_ui()
        self._load_reference_data()
        
    def _init_ui(self):
        """Initialize the user interface."""
        main_layout = QVBoxLayout(self)
        
        # Single panel - Input and controls only
        input_widget = self._create_input_panel()
        main_layout.addWidget(input_widget)
        
    def _create_input_panel(self) -> QWidget:
        """Create the input panel for measurements and predictions."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # Measurement input group
        measurement_group = QGroupBox(self.messages.get("embryo_track.group.measurements", "Ultrasound Measurements"))
        measurement_layout = QFormLayout()
        
        # Cranio-occipital length
        self.col_input = QLineEdit()
        self.col_input.setValidator(QDoubleValidator(0.0, 100.0, 2))
        self.col_input.setPlaceholderText("mm")
        col_label = QLabel(self.messages.get("embryo_track.label.col", "Cranio-Occipital Length (COL):"))
        col_help = QLabel(self.messages.get("embryo_track.help.col_html", "<i>Reliable range: 5-35 mm</i>"))
        col_help.setStyleSheet("color: gray; font-size: 9pt;")
        measurement_layout.addRow(col_label, self.col_input)
        measurement_layout.addRow("", col_help)
        
        # Temporo-temporal length
        self.ttl_input = QLineEdit()
        self.ttl_input.setValidator(QDoubleValidator(0.0, 100.0, 2))
        self.ttl_input.setPlaceholderText("mm")
        ttl_label = QLabel(self.messages.get("embryo_track.label.ttl", "Temporo-Temporal Length (TTL):"))
        ttl_help = QLabel(self.messages.get("embryo_track.help.ttl_html", "<i>Reliable range: 4-25 mm</i>"))
        ttl_help.setStyleSheet("color: gray; font-size: 9pt;")
        measurement_layout.addRow(ttl_label, self.ttl_input)
        measurement_layout.addRow("", ttl_help)
        
        # Number of embryos
        self.embryo_count = QComboBox()
        self.embryo_count.addItems(["1", "2", "3"])
        measurement_layout.addRow(self.messages.get("embryo_track.label.embryo_count", "Number of Embryos:"), self.embryo_count)
        
        # Measurement date
        self.measurement_date = QLineEdit()
        self.measurement_date.setPlaceholderText(self.messages.get("embryo_track.placeholder.measurement_date", "DD.MM.YYYY"))
        measurement_layout.addRow(self.messages.get("embryo_track.label.measurement_date", "Measurement Date:"), self.measurement_date)
        
        measurement_group.setLayout(measurement_layout)
        layout.addWidget(measurement_group)
        
        # Prediction button
        self.predict_button = QPushButton(self.messages.get("embryo_track.button.predict", "Predict Gestation Day"))
        self.predict_button.clicked.connect(self._predict_gestation)
        layout.addWidget(self.predict_button)
        
        # Results group
        results_group = QGroupBox(self.messages.get("embryo_track.group.results", "Prediction Results"))
        results_layout = QFormLayout()
        
        self.predicted_day_label = QLabel("-")
        self.predicted_day_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        results_layout.addRow(self.messages.get("embryo_track.label.predicted_day", "Predicted Gestation Day:"), self.predicted_day_label)
        
        self.confidence_label = QLabel("-")
        results_layout.addRow(self.messages.get("embryo_track.label.confidence", "Confidence:"), self.confidence_label)
        
        self.conception_date_label = QLabel("-")
        results_layout.addRow(self.messages.get("embryo_track.label.conception_date", "Estimated Conception Date:"), self.conception_date_label)
        
        self.due_date_label = QLabel("-")
        results_layout.addRow(self.messages.get("embryo_track.label.due_date", "Estimated Due Date:"), self.due_date_label)
        
        results_group.setLayout(results_layout)
        layout.addWidget(results_group)
        
        # Data management group
        data_group = QGroupBox(self.messages.get("embryo_track.group.reference_data", "Reference Data Management"))
        data_layout = QVBoxLayout()
        
        self.load_excel_button = QPushButton(self.messages.get("embryo_track.button.load_reference", "Load Reference Data (Excel/CSV)"))
        self.load_excel_button.clicked.connect(self._load_excel_data)
        data_layout.addWidget(self.load_excel_button)
        
        # Debug/diagnostic button
        self.debug_button = QPushButton(self.messages.get("embryo_track.button.show_diagnostics", "Show Model Diagnostics"))
        self.debug_button.clicked.connect(self._show_model_diagnostics)
        data_layout.addWidget(self.debug_button)
        
        data_group.setLayout(data_layout)
        layout.addWidget(data_group)
        
        return widget
        
        
    def _load_reference_data(self):
        """Load reference data from JSON file."""
        try:
            if os.path.exists(REFERENCE_DATA_FILE):
                with open(REFERENCE_DATA_FILE, 'r', encoding='utf-8') as f:
                    self.reference_data = json.load(f)
                self._build_prediction_models()
                logger.info("Reference data loaded successfully")
            else:
                # Create default data structure
                self.reference_data = {
                    "1_embryo": [],
                    "2_embryo": [],
                    "3_embryo": []
                }
                logger.info("No reference data found, using empty dataset")
        except Exception as e:
            logger.error(f"Error loading reference data: {e}")
            show_message(self, "warning", "Error", self.messages.get("embryo_track.error.load_reference", "Failed to load reference data: {error}").format(error=e))
            
                
    def _build_prediction_models(self):
        """Build interpolation models from reference data."""
        self.prediction_models = {}
        
        for embryo_count in [1, 2, 3]:
            data_key = f"{embryo_count}_embryo"
            data = self.reference_data.get(data_key, [])
            
            if len(data) < 3:
                continue
                
            # Extract valid data points
            col_data = []
            ttl_data = []
            col_gestation_data = []
            ttl_gestation_data = []
            
            for entry in data:
                try:
                    gest = float(entry.get('gestation_day', 0))
                    if gest <= 0:
                        continue
                    
                    # Handle COL measurement (required)
                    col = entry.get('col_mm')
                    if col is not None and col != '':
                        try:
                            col_val = float(col)
                            if col_val > 0:
                                col_data.append(col_val)
                                col_gestation_data.append(gest)
                        except (ValueError, TypeError):
                            pass
                    
                    # Handle TTL measurement (optional - may be missing)
                    # Apply same plausibility threshold as for user input (TTL_MIN_RELIABLE = 4.0 mm)
                    ttl = entry.get('ttl_mm')
                    if ttl is not None and ttl != '':
                        try:
                            ttl_val = float(ttl)
                            if ttl_val >= 4.0:  # TTL_MIN_RELIABLE threshold
                                ttl_data.append(ttl_val)
                                ttl_gestation_data.append(gest)
                            else:
                                logger.warning(f"Row with gestation day {gest}: TTL value {ttl_val} mm below plausibility threshold (4.0 mm), skipping")
                        except (ValueError, TypeError):
                            pass
                        
                except (ValueError, TypeError):
                    continue
                    
            # Create regression models with prediction intervals
            models = {}
            if len(col_data) >= 3:
                col_array = np.array(col_data)
                col_gest_array = np.array(col_gestation_data)

                # Check for NaN or invalid values in data
                if np.any(np.isnan(col_array)) or np.any(np.isnan(col_gest_array)):
                    logger.error(f"NaN values found in COL data for {embryo_count} embryo(s)")
                    continue

                # Fit power law model: gestation_day = a * COL^b + c
                # This captures the non-linear growth curve better than linear interpolation
                def power_model(col, a, b, c):
                    return a * np.power(col, b) + c

                try:
                    # Initial parameter guess [a, b, c]
                    p0 = [10.0, 0.7, 50.0]
                    popt, _ = curve_fit(power_model, col_array, col_gest_array, p0=p0, maxfev=5000)

                    # Compute residuals to characterize biological variance
                    predicted = power_model(col_array, *popt)
                    residuals = col_gest_array - predicted
                    residual_std = np.std(residuals)

                    # Store model parameters and uncertainty
                    models['col_model'] = {
                        'params': popt.tolist(),  # [a, b, c]
                        'residual_std': float(residual_std),
                        'model_func': power_model
                    }
                    logger.info(f"Built COL model for {embryo_count} embryo(s): power law fit on {len(col_data)} points, residual std={residual_std:.1f} days")
                except Exception as e:
                    logger.error(f"Failed to fit COL model for {embryo_count} embryo(s): {e}")
                
            if len(ttl_data) >= 3:
                ttl_array = np.array(ttl_data)
                ttl_gest_array = np.array(ttl_gestation_data)

                # Check for NaN or invalid values in data
                if np.any(np.isnan(ttl_array)) or np.any(np.isnan(ttl_gest_array)):
                    logger.error(f"NaN values found in TTL data for {embryo_count} embryo(s)")
                else:
                    # Fit power law model for TTL
                    def power_model_ttl(ttl, a, b, c):
                        return a * np.power(ttl, b) + c

                    try:
                        p0 = [10.0, 0.7, 50.0]
                        popt, _ = curve_fit(power_model_ttl, ttl_array, ttl_gest_array, p0=p0, maxfev=5000)

                        predicted = power_model_ttl(ttl_array, *popt)
                        residuals = ttl_gest_array - predicted
                        residual_std = np.std(residuals)

                        models['ttl_model'] = {
                            'params': popt.tolist(),
                            'residual_std': float(residual_std),
                            'model_func': power_model_ttl
                        }
                        logger.info(f"Built TTL model for {embryo_count} embryo(s): power law fit on {len(ttl_data)} points, residual std={residual_std:.1f} days")
                    except Exception as e:
                        logger.error(f"Failed to fit TTL model for {embryo_count} embryo(s): {e}")
            else:
                logger.info(f"Insufficient TTL data for {embryo_count} embryo(s): only {len(ttl_data)} points (need 3+)")
                
            self.prediction_models[embryo_count] = models
            
    def _predict_gestation(self):
        """Predict gestation day from input measurements."""
        try:
            embryo_count = int(self.embryo_count.currentText())
            col_text = self.col_input.text().strip()
            ttl_text = self.ttl_input.text().strip()
            
            if not col_text and not ttl_text:
                show_message(self, "warning", "Error", self.messages.get("embryo_track.warn.no_measurements", "Please enter at least one measurement (COL or TTL)"))
                return
                
            # Use twin (2 embryo) model as baseline for all predictions
            # This ensures consistent biological corrections
            baseline_models = self.prediction_models.get(2, {})
            if not baseline_models:
                # Fallback: try to use the selected embryo count's own model directly
                fallback_models = self.prediction_models.get(embryo_count, {})
                if fallback_models:
                    baseline_models = fallback_models
                    # No biological correction needed when using count-specific model
                else:
                    show_message(self, "warning", "Error", self.messages.get("embryo_track.error.no_baseline_model", "No baseline model available (need twin/2 embryo reference data)"))
                    return
                
            # Define measurement ranges for reliability (based on reference data)
            COL_MIN_RELIABLE = 5.0   # mm - below this, embryo too small for reliable measurement
            COL_MAX_RELIABLE = 35.0  # mm - above this, beyond normal marmoset gestation
            TTL_MIN_RELIABLE = 4.0   # mm
            TTL_MAX_RELIABLE = 25.0  # mm
            
            # Parse measurements once at the top
            col_value = None
            ttl_value = None
            if col_text:
                col_value = float(col_text)
            if ttl_text:
                ttl_value = float(ttl_text)

            # Check for out-of-range measurements
            warnings = []
            if col_value is not None:
                if col_value < COL_MIN_RELIABLE:
                    warnings.append(self.messages.get("embryo_track.warn.col_too_small", "⚠ COL measurement ({value} mm) is very small.\nEarly-stage embryos cannot be reliably measured via ultrasound.\nPrediction accuracy is LOW.").format(value=col_value))
                elif col_value > COL_MAX_RELIABLE:
                    warnings.append(self.messages.get("embryo_track.warn.col_too_large", "⚠ COL measurement ({value} mm) exceeds normal range.\nThis is beyond typical marmoset gestation measurements.\nPlease verify the measurement or check for measurement errors.").format(value=col_value))

            if ttl_value is not None:
                if ttl_value < TTL_MIN_RELIABLE:
                    warnings.append(self.messages.get("embryo_track.warn.ttl_too_small", "⚠ TTL measurement ({value} mm) is very small.\nEarly-stage embryos cannot be reliably measured via ultrasound.\nPrediction accuracy is LOW.").format(value=ttl_value))
                elif ttl_value > TTL_MAX_RELIABLE:
                    warnings.append(self.messages.get("embryo_track.warn.ttl_too_large", "⚠ TTL measurement ({value} mm) exceeds normal range.\nThis is beyond typical marmoset gestation measurements.\nPlease verify the measurement or check for measurement errors.").format(value=ttl_value))

            # Track if we're using a fallback model (no correction needed)
            using_fallback = embryo_count != 2 and baseline_models == self.prediction_models.get(embryo_count, {})
            
            # Show warnings if any - with OK/Cancel to allow user to abort
            if warnings:
                result = show_message(
                    self, "warning", "Warning",
                    "\n\n".join(warnings) + "\n\n" + self.messages.get("embryo_track.warn.proceed_question", "Proceed with prediction anyway?"),
                    buttons=QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
                )
                if result == QMessageBox.StandardButton.Cancel:
                    return
                
            predictions = []
            uncertainty_scores = []  # Store residual_std for each prediction
            confidence_scores = []

            def predict_from_model(model_dict, measurement, is_col=True):
                """Predict gestation day using power law model with uncertainty."""
                params = model_dict['params']
                residual_std = model_dict['residual_std']
                a, b, c = params

                # Power law: day = a * measurement^b + c
                prediction = a * np.power(measurement, b) + c

                # Check for valid prediction
                if np.isnan(prediction) or np.isinf(prediction):
                    return None, None

                # Return prediction and uncertainty (±2 std = ~95% range)
                return prediction, residual_std

            # Predict from COL if available
            if col_value is not None and 'col_model' in baseline_models:
                col_prediction, col_uncertainty = predict_from_model(
                    baseline_models['col_model'], col_value, is_col=True
                )

                if col_prediction is None:
                    logger.error(f"COL model returned invalid value for COL={col_value}")
                    show_message(
                        self, "error", "Error",
                        self.messages.get("embryo_track.error.col_invalid", "COL value {value} mm produced an invalid result.\nThis may be outside the valid interpolation range.").format(value=col_value)
                    )
                    return

                # Apply biological correction based on embryo count
                # Single embryos grow FASTER → reach same size EARLIER → subtract days
                # Triple embryos grow SLOWER → reach same size LATER → add days
                if not using_fallback:
                    if embryo_count == 1:
                        col_prediction = col_prediction - 6.0
                    elif embryo_count == 3:
                        col_prediction = col_prediction + 4.0

                predictions.append(col_prediction)
                uncertainty_scores.append(col_uncertainty)

                # Confidence based on residual variance and measurement range
                col_confidence = 0.8
                if col_value < COL_MIN_RELIABLE:
                    col_confidence = 0.3
                elif col_value > COL_MAX_RELIABLE:
                    col_confidence = 0.4
                confidence_scores.append(col_confidence)

            # Predict from TTL if available
            if ttl_value is not None and 'ttl_model' in baseline_models:
                ttl_prediction, ttl_uncertainty = predict_from_model(
                    baseline_models['ttl_model'], ttl_value, is_col=False
                )

                if ttl_prediction is None:
                    logger.error(f"TTL model returned invalid value for TTL={ttl_value}")
                    show_message(
                        self, "error", "Error",
                        self.messages.get("embryo_track.error.ttl_invalid", "TTL value {value} mm produced an invalid result.\nThis may be outside the valid interpolation range.").format(value=ttl_value)
                    )
                    return

                # Apply same biological correction as for COL (if not using fallback)
                if not using_fallback:
                    if embryo_count == 1:
                        ttl_prediction = ttl_prediction - 6.0
                    elif embryo_count == 3:
                        ttl_prediction = ttl_prediction + 4.0

                predictions.append(ttl_prediction)
                uncertainty_scores.append(ttl_uncertainty)

                # Confidence based on residual variance and measurement range
                ttl_confidence = 0.7
                if ttl_value < TTL_MIN_RELIABLE:
                    ttl_confidence = 0.3
                elif ttl_value > TTL_MAX_RELIABLE:
                    ttl_confidence = 0.4
                confidence_scores.append(ttl_confidence)

            if not predictions:
                show_message(self, "warning", "Error", self.messages.get("embryo_track.error.no_model_for_measurements", "No suitable model found for the given measurements"))
                return

            # Calculate weighted average and combined uncertainty
            if len(predictions) > 1:
                weights = np.array(confidence_scores)
                final_prediction = np.average(predictions, weights=weights)
                # Combined uncertainty: weighted average of individual uncertainties
                final_uncertainty = np.average(uncertainty_scores, weights=weights)
                final_confidence = min(np.mean(confidence_scores) * 1.1, 1.0)
            else:
                final_prediction = predictions[0]
                final_uncertainty = uncertainty_scores[0]
                final_confidence = confidence_scores[0]

            # Final validation check
            if np.isnan(final_prediction) or np.isinf(final_prediction):
                logger.error(f"Final prediction is invalid: {final_prediction}")
                show_message(
                    self, "error", "Error",
                    self.messages.get("embryo_track.error.final_invalid", "Unable to generate a valid prediction from the given measurements.\nPlease check your input values and try again.")
                )
                return

            # Compute prediction interval (±2 std = ~95% confidence)
            lower_bound = final_prediction - 2 * final_uncertainty
            upper_bound = final_prediction + 2 * final_uncertainty

            # Update results with prediction interval
            self.predicted_day_label.setText(f"{int(final_prediction)} days ({int(lower_bound)}–{int(upper_bound)})")
            self.confidence_label.setText(f"±{final_uncertainty:.1f} days (95% range)")
            
            # Calculate dates if measurement date is provided
            measurement_date_text = self.measurement_date.text().strip()
            if measurement_date_text:
                try:
                    measurement_date = datetime.strptime(measurement_date_text, "%d.%m.%Y").date()
                    conception_date = measurement_date - timedelta(days=int(final_prediction))
                    due_date = conception_date + timedelta(days=MARMOSET_GESTATION_DAYS)
                    
                    self.conception_date_label.setText(conception_date.strftime("%d.%m.%Y"))
                    self.due_date_label.setText(due_date.strftime("%d.%m.%Y"))
                except ValueError:
                    self.conception_date_label.setText(self.messages.get("embryo_track.label.invalid_date_format", "Invalid date format"))
                    self.due_date_label.setText(self.messages.get("embryo_track.label.invalid_date_format", "Invalid date format"))
            else:
                self.conception_date_label.setText("-")
                self.due_date_label.setText("-")
            
        except Exception as e:
            logger.error(f"Prediction error: {e}")
            show_message(self, "error", "Error", self.messages.get("embryo_track.error.predict_failed", "Failed to predict gestation day: {error}").format(error=e))
            
    def _load_excel_data(self):
        """Load reference data from Excel or CSV file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, self.messages.get("embryo_track.dialog.load_reference_title", "Load Reference Data"), "",
            self.messages.get("embryo_track.dialog.load_reference_filter", "Excel and CSV files (*.xlsx *.xls *.csv)")
        )
        
        if not file_path:
            return
            
        try:
            # Read file based on extension
            file_extension = os.path.splitext(file_path)[1].lower()
            if file_extension == '.csv':
                df = pd.read_csv(file_path)
                # Strip whitespace from column names (common issue)
                df.columns = df.columns.str.strip()
                # Remove completely empty rows
                df = df.dropna(how='all')
            else:
                # Try to read Excel file - requires openpyxl
                try:
                    # Read Excel with explicit parameters to handle various formatting
                    # sheet_name=0 reads the first sheet
                    # header=0 assumes first row is the header
                    # Try to auto-detect header row if first row seems to be data
                    df = pd.read_excel(file_path, engine='openpyxl', sheet_name=0, header=0)
                    
                    # Strip whitespace from column names (common Excel issue)
                    df.columns = df.columns.str.strip()
                    
                    # Remove completely empty rows
                    df = df.dropna(how='all')
                    
                except ImportError:
                    show_message(
                        self, "error", "Error",
                        self.messages.get("embryo_track.error.missing_openpyxl",
                            "The 'openpyxl' library is required to read Excel files.\n\n"
                            "Please install it using:\n"
                            "pip install openpyxl\n\n"
                            "Alternatively, you can save your data as a CSV file (.csv) and load it instead.")
                    )
                    return
                except Exception as excel_error:
                    # If reading fails, try to provide helpful error message
                    logger.error(f"Excel read error: {excel_error}")
                    show_message(
                        self, "error", "Error",
                        self.messages.get("embryo_track.error.excel_read", "Failed to read Excel file: {error}\n\nPlease ensure:\n• The file is a valid Excel file (.xlsx or .xls)\n• The first sheet contains your data\n• The first row contains column headers\n• Required columns exist: embryo_count, gestation_day, col_mm").format(error=excel_error)
                    )
                    return
            
            # Debug: Print column names
            logger.info(f"Loaded file columns: {list(df.columns)}")
            
            # Validate required columns
            required_columns = ['embryo_count', 'gestation_day', 'col_mm']
            missing_columns = [col for col in required_columns if col not in df.columns]
            
            if missing_columns:
                show_message(
                    self, "warning", "Error",
                    self.messages.get("embryo_track.error.invalid_format", "Missing required columns: {missing}\n\nFound columns: {found}\n\nRequired columns: embryo_count, gestation_day, col_mm\nOptional columns: ttl_mm, notes").format(missing=", ".join(missing_columns), found=", ".join(df.columns))
                )
                return
                
            # Process data by embryo count
            new_data = {"1_embryo": [], "2_embryo": [], "3_embryo": []}
            
            processed_rows = 0
            skipped_rows = 0
            
            for index, row in df.iterrows():
                try:
                    embryo_count = int(row['embryo_count'])
                    if embryo_count not in [1, 2, 3]:
                        skipped_rows += 1
                        continue
                        
                    # Build entry with required fields
                    entry = {
                        'gestation_day': float(row['gestation_day']),
                        'col_mm': float(row['col_mm']),
                        'notes': str(row.get('notes', '')) if pd.notna(row.get('notes', '')) else ''
                    }
                    
                    # Add TTL only if it exists and is valid
                    if 'ttl_mm' in row and pd.notna(row['ttl_mm']):
                        try:
                            ttl_value = float(row['ttl_mm'])
                            if ttl_value > 0:  # Only include positive values
                                entry['ttl_mm'] = ttl_value
                            else:
                                logger.info(f"Row {index + 1}: TTL value {ttl_value} <= 0, skipping TTL for this entry")
                        except (ValueError, TypeError):
                            logger.info(f"Row {index + 1}: Invalid TTL value, skipping TTL for this entry")
                    else:
                        logger.info(f"Row {index + 1}: No TTL measurement provided")
                    
                    data_key = f"{embryo_count}_embryo"
                    new_data[data_key].append(entry)
                    processed_rows += 1
                    
                except (ValueError, TypeError) as e:
                    logger.warning(f"Skipping row {index + 1}: {e}")
                    skipped_rows += 1
                    continue
                
            # Update reference data
            self.reference_data = new_data
            self._build_prediction_models()
            
            # Auto-save the loaded data
            try:
                with open(REFERENCE_DATA_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.reference_data, f, indent=2, ensure_ascii=False)
            except Exception as save_error:
                logger.warning(f"Could not auto-save reference data: {save_error}")
            
            # Count entries with and without TTL
            total_entries = sum(len(v) for v in new_data.values())
            entries_with_ttl = sum(
                1 for entries in new_data.values() 
                for entry in entries 
                if 'ttl_mm' in entry and entry['ttl_mm'] is not None
            )
            entries_without_ttl = total_entries - entries_with_ttl
            
            success_msg = self.messages.get("embryo_track.info.import_success_header", "Successfully processed {count} data points").format(count=processed_rows) + "\n"
            success_msg += self.messages.get("embryo_track.info.import_col_only", "• Entries with COL only: {count}").format(count=entries_without_ttl) + "\n"
            success_msg += self.messages.get("embryo_track.info.import_col_ttl", "• Entries with COL + TTL: {count}").format(count=entries_with_ttl)
            if skipped_rows > 0:
                success_msg += "\n" + self.messages.get("embryo_track.info.import_skipped_rows", "• Skipped rows: {count}").format(count=skipped_rows)
            success_msg += "\n\n" + self.messages.get("embryo_track.info.import_note_ttl_optional", "Note: TTL measurements are optional. Predictions can be made using COL alone.")

            show_message(self, "information", "Success", success_msg)
            
        except Exception as e:
            logger.error(f"Excel import error: {e}")
            show_message(self, "error", "Error", self.messages.get("embryo_track.error.import_excel", "Failed to load Excel file: {error}").format(error=e))
            
    def _show_model_diagnostics(self):
        """Display diagnostic information about prediction models."""
        try:
            # Create diagnostic dialog
            diag_dialog = QDialog(self)
            diag_dialog.setWindowTitle(self.messages.get("embryo_track.diagnostics.title", "Model Diagnostics"))
            diag_dialog.resize(700, 500)
            layout = QVBoxLayout(diag_dialog)
            
            # Info text
            info_label = QLabel(self.messages.get("embryo_track.diagnostics.info_html",
                "<b>Model Diagnostics</b><br>"
                "This tool shows how the same measurement produces different predictions "
                "across embryo counts.<br>"
                "Use this to validate biological correction factors."
            ))
            info_label.setWordWrap(True)
            layout.addWidget(info_label)
            
            # Input for test measurement
            test_group = QGroupBox(self.messages.get("embryo_track.diagnostics.group.test", "Test Measurement"))
            test_layout = QHBoxLayout()
            test_layout.addWidget(QLabel(self.messages.get("embryo_track.diagnostics.label.col", "COL (mm):")))
            test_col_input = QLineEdit()
            test_col_input.setValidator(QDoubleValidator(0.0, 100.0, 2))
            test_col_input.setPlaceholderText(self.messages.get("embryo_track.diagnostics.placeholder.col_example", "e.g., 25.0"))
            test_layout.addWidget(test_col_input)
            test_group.setLayout(test_layout)
            layout.addWidget(test_group)
            
            # Results table
            results_table = QTableWidget()
            results_table.setColumnCount(4)
            results_table.setHorizontalHeaderLabels([
                self.messages.get("embryo_track.diagnostics.table.embryo_count", "Embryo Count"),
                self.messages.get("embryo_track.diagnostics.table.baseline", "Baseline (Twin)"),
                self.messages.get("embryo_track.diagnostics.table.correction", "Correction"),
                self.messages.get("embryo_track.diagnostics.table.final_prediction", "Final Prediction")
            ])
            results_table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(results_table)
            
            # Data summary
            summary_text = QTextEdit()
            summary_text.setReadOnly(True)
            summary_text.setMaximumHeight(150)
            layout.addWidget(summary_text)
            
            def update_diagnostics():
                """Update diagnostic table based on input."""
                col_text = test_col_input.text().strip()
                if not col_text:
                    return
                    
                try:
                    col_value = float(col_text)
                    results_table.setRowCount(0)
                    
                    summary_lines = [self.messages.get("embryo_track.diagnostics.summary_header", "=== Diagnostic Summary ===\n")]
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_test_col", "Test COL measurement: {value} mm\n").format(value=col_value))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_baseline_intro", "Using TWIN (2 embryo) model as baseline for all predictions\n\n"))
                    
                    # Get baseline prediction from twin model
                    baseline_models = self.prediction_models.get(2, {})
                    if 'col_model' not in baseline_models:
                        summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_no_baseline", "ERROR: No twin (2 embryo) baseline model available!\n"))
                        summary_text.setText("".join(summary_lines))
                        return

                    # Use power law model: day = a * COL^b + c
                    col_model = baseline_models['col_model']
                    a, b, c = col_model['params']
                    baseline_pred = a * np.power(col_value, b) + c
                    
                    # Check for NaN or invalid values
                    if np.isnan(baseline_pred) or np.isinf(baseline_pred):
                        summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_invalid_value", "\nERROR: COL value {value} mm produced invalid result (NaN/Inf)\nThis value may be outside the model range.\n").format(value=col_value))
                        summary_text.setText("".join(summary_lines))
                        return
                    
                    for embryo_count in [1, 2, 3]:
                        # Correction factor
                        if embryo_count == 1:
                            correction = -6.0
                        elif embryo_count == 3:
                            correction = 4.0
                        else:
                            correction = 0.0
                        
                        corrected_pred = baseline_pred + correction
                        
                        # Add to table
                        row = results_table.rowCount()
                        results_table.insertRow(row)
                        results_table.setItem(row, 0, QTableWidgetItem(str(embryo_count)))
                        days_unit = self.messages.get("unit.days", "days")
                        results_table.setItem(row, 1, QTableWidgetItem(f"{baseline_pred:.1f} {days_unit}"))
                        results_table.setItem(row, 2, QTableWidgetItem(f"{correction:+.1f} {days_unit}"))
                        results_table.setItem(row, 3, QTableWidgetItem(f"{corrected_pred:.1f} {days_unit}"))
                        
                        summary_lines.append(
                            self.messages.get("embryo_track.diagnostics.summary_line", "{count} embryo(s): {baseline:.1f} → {corrected:.1f} days (correction: {correction:+.1f})\n").format(
                                count=embryo_count, baseline=baseline_pred, corrected=corrected_pred, correction=correction
                            )
                        )
                    
                    # Add biological interpretation
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_bio_header", "\n=== Biological Interpretation ===\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_bio_intro",
                        "All predictions use the TWIN (2 embryo) model as baseline.\n"
                        "Corrections account for biological growth rate differences:\n\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_single",
                        "• Single embryo: Gets MORE resources → grows FASTER\n"
                        "  → reaches same size EARLIER → SUBTRACT 6 days\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_twin",
                        "• Twin embryos: BASELINE (no correction)\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_triplet",
                        "• Triple embryos: Share resources → grow SLOWER\n"
                        "  → reach same size LATER → ADD 4 days\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_expected_pattern",
                        "\n✓ EXPECTED PATTERN for same COL measurement:\n"
                        "  Single < Twin < Triplet (gestation days)\n\n"
                        "For COL={value}mm: Check that {low:.1f} < {baseline:.1f} < {high:.1f}\n"
                    ).format(value=col_value, low=baseline_pred-6, baseline=baseline_pred, high=baseline_pred+4))
                    
                    # Check if data is available
                    data_info = []
                    for embryo_count in [1, 2, 3]:
                        data_key = f"{embryo_count}_embryo"
                        data = self.reference_data.get(data_key, [])
                        total_entries = len(data)
                        with_ttl = sum(1 for entry in data if 'ttl_mm' in entry and entry.get('ttl_mm'))
                        data_info.append(
                            self.messages.get("embryo_track.diagnostics.summary_data_line", "{count} embryo: {total} total ({with_ttl} with TTL, {col_only} COL only)").format(
                                count=embryo_count, total=total_entries, with_ttl=with_ttl, col_only=total_entries - with_ttl
                            )
                        )
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_reference_header", "\n=== Reference Data ===\n"))
                    summary_lines.append("\n".join(data_info))
                    
                    # Add reliability ranges
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_ranges_header", "\n\n=== Reliable Measurement Ranges ===\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_col_range", "COL: 5-35 mm (below 5mm: too small, above 35mm: out of range)\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_ttl_range", "TTL: 4-25 mm (below 4mm: too small, above 25mm: out of range)\n"))
                    summary_lines.append(self.messages.get("embryo_track.diagnostics.summary_range_note", "\nMeasurements outside these ranges will have reduced confidence scores."))
                    
                    summary_text.setText("".join(summary_lines))
                    
                except ValueError as e:
                    logger.warning(f"Invalid input: {e}")
                    
            # Connect update
            test_col_input.textChanged.connect(update_diagnostics)
            
            # Buttons
            button_layout = QHBoxLayout()
            close_btn = QPushButton(self.messages.get("button.close", "Close"))
            close_btn.clicked.connect(diag_dialog.accept)
            button_layout.addStretch()
            button_layout.addWidget(close_btn)
            layout.addLayout(button_layout)
            
            # Show initial state
            test_col_input.setText("25.0")
            update_diagnostics()
            
            diag_dialog.exec()
            
        except Exception as e:
            logger.error(f"Diagnostic error: {e}")
            show_message(self, "error", "Error", self.messages.get("embryo_track.error.diagnostics_failed", "Failed to show diagnostics: {error}").format(error=e))

def show_embryo_tracker(messages=None, parent=None):
    """Function to show the Embryo Tracker dialog."""
    dialog = EmbryoTrackerWidget(messages, parent)
    dialog.exec()
    return dialog

if __name__ == "__main__":
    # For testing the plugin standalone
    app = QtWidgets.QApplication(sys.argv)
    dialog = EmbryoTrackerWidget()
    dialog.show()
    sys.exit(app.exec())
