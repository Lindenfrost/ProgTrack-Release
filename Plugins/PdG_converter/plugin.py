# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: PdG Converter plugin integration.

import json
from pathlib import Path
from datetime import datetime

from Plugins.core.animal_identity import animal_base_name

from .converter import PdGConverter, get_paired_data


class PdGConverterPlugin:
    """Plugin entry point for PdG → Progesterone conversion."""
    
    @classmethod
    def register(cls, app):
        """Register plugin with main application."""
        return PdGCapability(cls(app))
    
    def __init__(self, app):
        self.app = app
        self.data_dir = Path(__file__).parent / "data"
        self.data_dir.mkdir(exist_ok=True)
        
        # Phase 2: Use our own converter with bounded least squares
        self.converter = PdGConverter()
        
        # Storage file for model parameters
        self.models_file = self.data_dir / "models.json"
    
    def get_parameters(self, animal_name):
        """Read conversion parameters from storage.
        
        Args:
            animal_name: Name of the animal
            
        Returns:
            Dict with model parameters or None if not found
        """
        if not self.models_file.exists():
            return None
        
        try:
            with open(self.models_file, 'r') as f:
                all_models = json.load(f)
            return all_models.get(animal_name)
        except (json.JSONDecodeError, IOError):
            return None
    
    def save_parameters(self, animal_name, params):
        """Save conversion parameters to storage.
        
        Args:
            animal_name: Name of the animal
            params: Model parameters dict
        """
        all_models = {}
        if self.models_file.exists():
            try:
                with open(self.models_file, 'r') as f:
                    all_models = json.load(f)
            except (json.JSONDecodeError, IOError):
                all_models = {}
        
        if params is None:
            # Remove entry if params is None
            all_models.pop(animal_name, None)
        else:
            stored_params = dict(params)
            stored_params["ipid"] = animal_name
            stored_params["name"] = animal_base_name(
                animal_name,
                getattr(self.app, "animals", {}).get(animal_name, {}),
            )
            all_models[animal_name] = stored_params
        
        with open(self.models_file, 'w') as f:
            json.dump(all_models, f, indent=2)
    
    def fit_animal_model(self, animal_name):
        """Fit conversion model for an animal using paired data.
        
        Args:
            animal_name: Name of the animal
            
        Returns:
            Tuple (params, paired_pdg, paired_prog) or (None, [], [])
        """
        import logging
        logger = logging.getLogger(__name__)
        
        animal = self.app.animals.get(animal_name)
        if not animal:
            logger.warning(f"Animal {animal_name} not found")
            return None, [], []
        
        # Get paired data
        paired_dates, paired_pdg, paired_prog = get_paired_data(animal)
        logger.info(f"Fitting {animal_name}: {len(paired_pdg)} paired points")
        
        if len(paired_pdg) < 3:
            logger.info(f"Insufficient data: {len(paired_pdg)} < 3 pairs")
            return None, paired_pdg, paired_prog
        
        # Fit model
        try:
            params = self.converter.fit_model(paired_pdg, paired_prog)
            logger.info(f"Fit result: {params}")
        except Exception as e:
            logger.error(f"Fit failed with exception: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise  # Re-raise so UI can show error
        
        if params:
            # Save to storage
            self.save_parameters(animal_name, params)
        else:
            logger.info("Fit returned None - no model could be fitted")
        
        return params, paired_pdg, paired_prog
    
    def show_converter_dialog(self, animal_name=None):
        """Open the PdG converter dialog.
        
        Phase 2: Full implementation - to be added in ui_hooks.py
        """
        from .ui_hooks import ConverterDialog
        dlg = ConverterDialog(self.app, self, animal_name)
        dlg.exec()


class PdGCapability:
    """Capability object returned to main app - implements all hook methods."""
    
    def __init__(self, plugin):
        self._plugin = plugin
        self.has_pdg = True
        # Hooks reference for main app compatibility
        self.hooks = self
        # Storage for animal dialog tab references
        self._pdg_tabs = {}
    
    def on_female_dialog_tabs(self, tabs, animal_rec, editing, parent_app=None, name=None):
        """Hook for female animal dialog - add PdG tabs.
        
        Args:
            tabs: QTabWidget to add tabs to
            animal_rec: Animal record dict
            editing: Whether in edit mode
            parent_app: Reference to main ProgTrackApp for accessing _build_editable_list
            name: Animal name string
        """
        animal_name = name if name else animal_rec.get('name', '')
        return self.extend_animal_dialog(tabs, animal_rec, animal_name, parent_app)
    
    def on_partner_dialog_tabs(self, tabs, animal_rec, editing, parent_app=None, name=None):
        """Hook for partner dialog - add PdG tabs."""
        animal_name = name if name else animal_rec.get('name', '')
        return self.extend_animal_dialog(tabs, animal_rec, animal_name, parent_app, add_unified_prog=False)
    
    def add_menu_items(self, tools_menu):
        """Add PdG converter action to Tools menu.
        
        Phase 2: Connects to self._plugin.show_converter_dialog
        """
        from PyQt6.QtGui import QAction
        action = QAction(self._plugin.app.messages.get("menu.tools.pdg_converter", "PdG → Progesterone Converter"), self._plugin.app)
        # Check permission before enabling the action
        action.setEnabled(self._plugin.app._master_can('pdg_converter.use'))
        # Phase 2: Use plugin's own converter dialog
        action.triggered.connect(lambda: self._show_converter_dialog_checked())
        tools_menu.addAction(action)

    def _show_converter_dialog_checked(self):
        """Show converter dialog after checking permission."""
        if not self._plugin.app._master_can('pdg_converter.use'):
            self._plugin.app._show_permission_denied()
            return
        self._plugin.show_converter_dialog()
    
    def extend_style_dialog(self, colors_layout, markers_layout,
                          color_buttons, marker_combos, parent_app):
        """Add PdG color and marker settings to style dialog.
        
        Phase 1: Move existing code from main app's _create_style_dialog
        Phase 2: Keep same structure
        """
        # Phase 1: Move code from main app here
        # - color_buttons['urine'] = _create_color_button('#FF8C00')
        # - color_buttons['pdg'] = _create_color_button('#008000')
        # - marker_combos['urine'] with marker options
        pass
    
    def extend_animal_dialog(self, tabs, animal_rec, animal_name, parent_app=None, add_unified_prog=True):
        """Add PdG tabs to animal dialogs.
        
        Args:
            tabs: QTabWidget to add tabs to
            animal_rec: Animal record dict
            animal_name: Animal name string
            parent_app: Reference to main ProgTrackApp for accessing _build_editable_list
            add_unified_prog: If False, skip adding the Unified Prog tab (used for partners)
            
        Returns:
            Tuple (pdg_tab, conv_tab) widgets or (pdg_tab, None) if add_unified_prog=False
        """
        from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QTableWidget, 
                                     QTableWidgetItem, QLabel, QHeaderView, QPushButton)
        from PyQt6.QtCore import Qt
        
        DATE_FORMAT = '%d.%m.%Y'
        
        pdg_records = animal_rec.get('pdg', [])
        
        # --- PdG Tab (Editable like Progesterone tab) ---
        pdg_tab = QWidget()
        pdg_layout = QVBoxLayout(pdg_tab)
        
        # Use _build_editable_list from parent app if available
        if parent_app and hasattr(parent_app, '_build_editable_list'):
            def fmt_pdg(item): 
                return (item['datum'].strftime(DATE_FORMAT), str(item['wert']), item.get('probennummer', ''))
            def def_pdg(widgets):
                return (datetime.now().date().strftime(DATE_FORMAT), '0', '')
            
            sorted_pdg = sorted(pdg_records, key=lambda x: x['datum'])
            pdg_sc, pdg_w = parent_app._build_editable_list(
                self._plugin.app.messages.get("pdg_converter.tab.pdg_values", "PdG Values"),
                sorted_pdg,
                fmt_pdg,
                def_pdg,
                col_headers=(
                    self._plugin.app.messages.get("pdg_converter.table.date", "Date"),
                    self._plugin.app.messages.get("pdg_converter.table.pdg", "PdG (µg/mg Cr)"),
                    self._plugin.app.messages.get("pdg_converter.table.sample_id", "Sample ID")
                )
            )
            pdg_layout.addWidget(pdg_sc, 1)
            # Store reference for save functionality
            pdg_tab._pdg_widgets = pdg_w
        else:
            # Fallback to read-only table
            pdg_table = QTableWidget()
            pdg_table.setColumnCount(3)
            pdg_table.setHorizontalHeaderLabels([
                self._plugin.app.messages.get("pdg_converter.table.date", "Date"),
                self._plugin.app.messages.get("pdg_converter.table.pdg", "PdG (µg/mg Cr)"),
                self._plugin.app.messages.get("pdg_converter.table.sample_id", "Sample ID")
            ])
            pdg_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
            
            if pdg_records:
                sorted_records = sorted(pdg_records, key=lambda x: x.get('datum', datetime.min) if isinstance(x.get('datum'), datetime) else datetime.min)
                pdg_table.setRowCount(len(sorted_records))
                for i, rec in enumerate(sorted_records):
                    date_val = rec.get('datum')
                    if isinstance(date_val, datetime):
                        date_str = date_val.strftime('%Y-%m-%d')
                    else:
                        date_str = str(date_val)[:10] if date_val else ""
                    pdg_table.setItem(i, 0, QTableWidgetItem(date_str))
                    
                    val = rec.get('wert', 0)
                    val_item = QTableWidgetItem(f"{float(val):.2f}")
                    val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    pdg_table.setItem(i, 1, val_item)
                    
                    sample_id = rec.get('probennummer', '')
                    pdg_table.setItem(i, 2, QTableWidgetItem(str(sample_id)))
            
            pdg_layout.addWidget(pdg_table)
        
        # Add PdG tab
        tabs.addTab(pdg_tab, self._plugin.app.messages.get("pdg_converter.tab.pdg", "PdG"))
        
        # --- Unified Prog Tab ---
        if not add_unified_prog:
            return pdg_tab, None
        conv_tab = QWidget()
        conv_layout = QVBoxLayout(conv_tab)
        
        # Create a table for converted values
        conv_table = QTableWidget()
        conv_table.setColumnCount(3)
        conv_table.setHorizontalHeaderLabels([
            self._plugin.app.messages.get("pdg_converter.table.date", "Date"),
            self._plugin.app.messages.get("pdg_converter.table.pdg", "PdG (µg/mg Cr)"),
            self._plugin.app.messages.get("pdg_converter.table.prog_equiv", "Prog Equiv. (ng/ml)")
        ])
        conv_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        
        # Function to refresh the unified table
        def refresh_unified_table():
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"refresh_unified_table called for animal: {animal_name}")
            
            params = self._plugin.get_parameters(animal_name)
            logger.info(f"Got params: {params}")
            
            # Re-read current PdG data from animal record (may have been updated)
            animal = self._plugin.app.animals.get(animal_name, {})
            current_pdg_records = animal.get('pdg', [])
            logger.info(f"Found {len(current_pdg_records)} PdG records for {animal_name}")
            
            conv_table.setRowCount(0)  # Clear
            
            if not current_pdg_records:
                logger.info("No PdG records to display")
                return
                
            sorted_records = sorted(current_pdg_records, key=lambda x: x.get('datum', datetime.min) if isinstance(x.get('datum'), datetime) else datetime.min)
            conv_table.setRowCount(len(sorted_records))
            logger.info(f"Populating table with {len(sorted_records)} rows")
            
            for i, rec in enumerate(sorted_records):
                # Date
                date_val = rec.get('datum')
                if isinstance(date_val, datetime):
                    date_str = date_val.strftime('%Y-%m-%d')
                else:
                    date_str = str(date_val)[:10] if date_val else ""
                conv_table.setItem(i, 0, QTableWidgetItem(date_str))
                
                # PdG value
                val = rec.get('wert', 0)
                val_item = QTableWidgetItem(f"{float(val):.2f}")
                val_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                conv_table.setItem(i, 1, val_item)
                
                # Converted prog value
                if params:
                    try:
                        converted = self._plugin.converter.predict([float(val)], params)[0]
                        if converted >= 0:
                            conv_item = QTableWidgetItem(f"{converted:.2f}")
                        else:
                            conv_item = QTableWidgetItem("-")
                    except Exception as e:
                        logger.error(f"Error converting value {val}: {e}")
                        conv_item = QTableWidgetItem("-")
                else:
                    conv_item = QTableWidgetItem("-")
                conv_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                conv_table.setItem(i, 2, conv_item)
        
        # Model info label
        info_label = QLabel()
        
        def update_info():
            params = self._plugin.get_parameters(animal_name)
            if params:
                model_type = params.get('model_type', 'linear').capitalize()
                n_pairs = params.get('n_pairs', 0)
                info_text = self._plugin.app.messages.get("pdg_converter.model.info",
                    "Model: {model_type} | Pairs: {n_pairs} | MSE: {mse:.4f}"
                ).format(model_type=model_type, n_pairs=n_pairs, mse=params.get('mse', 0))
                info_label.setText(info_text)
            else:
                info_label.setText(self._plugin.app.messages.get("pdg_converter.status.no_conversion_model", "No conversion model fitted"))
        
        update_info()
        conv_layout.addWidget(info_label)
        
        # Add the table
        conv_layout.addWidget(conv_table)
        
        # Refresh button
        refresh_btn = QPushButton(self._plugin.app.messages.get("pdg_converter.button.refresh", "Refresh"))
        refresh_btn.clicked.connect(lambda: (refresh_unified_table(), update_info()))
        conv_layout.addWidget(refresh_btn)
        
        # Store references for external refresh
        conv_tab._refresh_table = refresh_unified_table
        conv_tab._update_info = update_info
        
        # Initial refresh
        refresh_unified_table()
        
        if add_unified_prog:
            # Add Unified Prog tab
            tabs.addTab(conv_tab, self._plugin.app.messages.get("pdg_converter.tab.unified_prog", "Unified Prog."))
        
        # Store reference for ConverterDialog to refresh
        self._pdg_tabs[animal_name] = (pdg_tab, conv_tab if add_unified_prog else None)
        
        return pdg_tab, conv_tab if add_unified_prog else None
    
    def cleanup_animal_dialog(self, tabs):
        """Clean up plugin-created tabs before dialog closes.
        
        Called by main app when animal dialog closes.
        """
        # Disconnect custom signals to prevent dangling references
        if hasattr(self, '_pdg_tab_signals'):
            for signal in self._pdg_tab_signals:
                signal.disconnect()
        self._pdg_tab_signals = []
        # Tab widgets are parented to tabs and auto-deleted
    
    def extend_plot(self, plot_context):
        """Provide PdG data series and style config for plotting.
        
        Phase 2: Use self._plugin.converter.predict()
        
        Args:
            plot_context: Object with axes, animal_name, animal_data, dates
            
        Returns:
            Dict with 'pdg_series', 'conversion_params', 'style_config' or None
        """
        animal_data = plot_context.animal_data
        if not animal_data.get('pdg'):
            return None
        
        # Prepare PdG data
        pdg_data = [(r['datum'], r['wert']) for r in animal_data['pdg']]
        pdg_data.sort()
        
        # Phase 2: Get conversion params from plugin storage
        animal_name = getattr(plot_context, 'animal_name', None)
        params = None
        if animal_name:
            params = self._plugin.get_parameters(animal_name)
        
        # Calculate converted values if params exist
        converted_data = None
        if params:
            import numpy as np
            dates, values = zip(*pdg_data) if pdg_data else ([], [])
            # Convert tuple to numpy array to match predict() expectations
            values_array = np.array(values, dtype=float)
            converted_values = self._plugin.converter.predict(values_array, params)
            converted_data = list(zip(dates, converted_values))
        
        return {
            'pdg_series': pdg_data,
            'converted_series': converted_data,
            'conversion_params': params,
            'style_config': {
                'urine_color': getattr(plot_context, 'urine_color', '#FF8C00'),
                'pdg_color': getattr(plot_context, 'pdg_color', '#008000'),
                'urine_marker': getattr(plot_context, 'urine_marker', 's'),
            }
        }
