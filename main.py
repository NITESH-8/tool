"""
Performance GUI Application - Real-time System Performance Monitoring and Stress Testing Dashboard

This module provides a comprehensive GUI application for monitoring and testing system performance
across multiple subsystems (CPU, GPU, DRAM) with real-time visualization, data collection,
and stress testing capabilities.

Key Features:
- Real-time performance monitoring with live graphs
- Per-core CPU utilization tracking
- GPU monitoring via external stress tools
- DRAM usage monitoring
- Stress testing with external tools
- Data export (CSV, PNG)
- Multi-protocol communication console (UART, ADB, SSH, CMD)
- Scheduled performance target changes
- Harmonic and sudden target transitions

Author: Performance GUI Team
Version: 1.0
Dependencies: PySide6, pyqtgraph, pyserial
"""

from __future__ import annotations

# Standard library imports
import csv
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# GUI framework imports
from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

# Local module imports
from data_sources import Subsystem, get_timestamp
from adb_utils import (
	is_adb_available as _adb_available,
	list_devices as _adb_list_devices,
	wait_for_device as _adb_wait_for_device,
	shell as _adb_shell,
)
from comm_console import CommConsole


class TimeAxis(pg.AxisItem):
	"""
	Custom time axis formatter for PyQtGraph plots.
	
	This class extends PyQtGraph's AxisItem to provide human-readable time formatting
	on the X-axis of performance graphs. It automatically formats time values based
	on the duration being displayed.
	
	Time Format Rules:
	- < 60 seconds: Show seconds with optional decimals (e.g., "12s", "12.5s")
	- 1-60 minutes: Show minutes:seconds format (e.g., "2:05", "15:30")
	- >= 60 minutes: Show hours:minutes format (e.g., "1h05m", "2h30m")
	
	The formatting adapts based on the tick spacing to provide appropriate precision.
	For small time intervals, decimal seconds are shown for better granularity.
	
	Attributes:
		None (inherits from pg.AxisItem)
	"""
	
	def tickStrings(self, values, scale, spacing):  # type: ignore[override]
		"""
		Convert numeric time values to formatted string labels for axis ticks.
		
		This method is called by PyQtGraph to generate tick labels. It handles
		the conversion from raw time values (in seconds) to human-readable strings
		with appropriate formatting based on the time range and tick spacing.
		
		Args:
			values: List of numeric values representing time positions on the axis
			scale: Scale factor applied to the values (may be None during layout)
			spacing: Spacing between ticks (used to determine decimal precision)
			
		Returns:
			List[str]: Formatted time strings for each tick position
			
		Note:
			This method is defensive against None/0 values that PyQtGraph may pass
			during initial layout calculations.
		"""
		# Be defensive: PyQtGraph may call with None/0 values during layout
		labels: List[str] = []
		
		# Safely extract scale and spacing values with fallbacks
		try:
			sc = float(scale) if isinstance(scale, (int, float)) else 1.0
			sps = float(spacing) if isinstance(spacing, (int, float)) and float(spacing) > 0 else 1.0
		except Exception:
			sc = 1.0
			sps = 1.0
			
		# Process each tick value
		for v in values:
			try:
				sec = float(v) * sc  # Convert to actual seconds
			except Exception:
				sec = 0.0
				
			# Format based on time duration
			if sec >= 3600:  # 1 hour or more
				hours = int(sec // 3600)
				minutes = int((sec % 3600) // 60)
				labels.append(f"{hours}h{minutes:02d}m")
			elif sec >= 60:  # 1 minute to 1 hour
				minutes = int(sec // 60)
				seconds = int(sec % 60)
				labels.append(f"{minutes}:{seconds:02d}")
			else:  # Less than 1 minute
				# Show 1 decimal when spacing is small for better precision
				if sps < 1:
					labels.append(f"{sec:.1f}s")
				else:
					labels.append(f"{int(sec)}s")
					
		return labels


def _nice_tick_seconds(raw_step: float) -> float:
	"""
	Calculate a 'nice' tick step value close to the raw step size.
	
	This utility function takes a raw time step value and returns a more
	human-friendly tick spacing that follows the 1-2-5 progression pattern
	scaled by powers of 10. This ensures that time axis ticks are evenly
	spaced and easy to read.
	
	The 1-2-5 progression means tick steps will be multiples of:
	1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, etc.
	
	Args:
		raw_step (float): The desired raw step size in seconds
		
	Returns:
		float: A 'nice' step size close to raw_step, or 1.0 if raw_step <= 0
		
	Example:
		>>> _nice_tick_seconds(0.7)  # Returns 1.0
		>>> _nice_tick_seconds(1.3)  # Returns 1.0
		>>> _nice_tick_seconds(2.1)  # Returns 2.0
		>>> _nice_tick_seconds(4.8)  # Returns 5.0
		>>> _nice_tick_seconds(8.2)  # Returns 10.0
	"""
	if raw_step <= 0:
		return 1.0
		
	import math
	# Find the order of magnitude (power of 10)
	exp = math.floor(math.log10(raw_step))
	# Normalize to 1-10 range
	frac = raw_step / (10 ** exp)
	
	# Apply 1-2-5 progression
	if frac < 1.5:
		nice = 1.0
	elif frac < 3.5:
		nice = 2.0
	elif frac < 7.5:
		nice = 5.0
	else:
		nice = 10.0
		
	# Scale back to original magnitude
	return nice * (10 ** exp)


# Global constant defining the available performance subsystems
SUBSYSTEMS = [Subsystem.CPU, Subsystem.GPU, Subsystem.DRAM]


@dataclass
class SubsystemState:
	"""
	Data class representing the state of a performance subsystem.
	
	This class holds all the data and UI elements associated with monitoring
	a specific subsystem (CPU, GPU, or DRAM). It tracks performance values
	over time, target values, and the corresponding PyQtGraph visualization
	elements.
	
	Attributes:
		name (str): The name of the subsystem (e.g., "CPU", "GPU", "DRAM")
		target_percent (int): The target performance percentage (0-100)
		values (List[Tuple[float, float]]): List of (timestamp, value) pairs
		curve (Optional[pg.PlotDataItem]): PyQtGraph curve object for plotting
		target_line (Optional[pg.InfiniteLine]): PyQtGraph line for target indicator
		
	Note:
		The values list stores tuples of (timestamp, percentage) where:
		- timestamp is Unix epoch time in seconds
		- percentage is the subsystem utilization (0.0-100.0)
	"""
	name: str
	target_percent: int = 50
	values: List[Tuple[float, float]] = field(default_factory=list)
	curve: Optional[pg.PlotDataItem] = None
	target_line: Optional[pg.InfiniteLine] = None


@dataclass
class CoreState:
	"""
	Data class representing the state of a CPU core.
	
	This class is similar to SubsystemState but specifically for individual
	CPU cores. It tracks per-core performance metrics and visualization
	elements.
	
	Attributes:
		core_id (int): The CPU core identifier (0-based index)
		target_percent (int): The target performance percentage for this core (0-100)
		values (List[Tuple[float, float]]): List of (timestamp, value) pairs
		curve (Optional[pg.PlotDataItem]): PyQtGraph curve object for plotting
		target_line (Optional[pg.InfiniteLine]): PyQtGraph line for target indicator
		
	Note:
		The values list stores tuples of (timestamp, percentage) where:
		- timestamp is Unix epoch time in seconds
		- percentage is the core utilization (0.0-100.0)
	"""
	core_id: int
	target_percent: int = 50
	values: List[Tuple[float, float]] = field(default_factory=list)
	curve: Optional[pg.PlotDataItem] = None
	target_line: Optional[pg.InfiniteLine] = None


class PerformanceApp(QtWidgets.QMainWindow):
	"""
	Main application class for the Performance Dashboard.
	
	This is the central class that orchestrates all functionality of the performance
	monitoring and stress testing application. It provides a comprehensive GUI for
	monitoring system performance across multiple subsystems (CPU, GPU, DRAM) with
	real-time visualization, data collection, and stress testing capabilities.
	
	Key Features:
	- Real-time performance monitoring with live graphs
	- Per-core CPU utilization tracking
	- GPU monitoring via external stress tools
	- DRAM usage monitoring
	- External stress tool integration
	- Data export (CSV, PNG)
	- Multi-protocol communication console
	- Scheduled performance target changes
	- Harmonic and sudden target transitions
	
	The application uses PyQtGraph for high-performance real-time plotting and
	PySide6 for the GUI framework. It supports both internal sampling and external
	stress tool integration for comprehensive performance testing.
	
	Attributes:
		states (Dict[str, SubsystemState]): Performance data for each subsystem
		core_states (Dict[int, CoreState]): Performance data for each CPU core
		active_subsystems (List[str]): Currently monitored subsystems
		active_cores (List[int]): Currently monitored CPU cores
		is_running (bool): Whether monitoring is currently active
		process (Optional[QtCore.QProcess]): External stress tool process
		scheduled_changes (List[Tuple]): Scheduled target changes
		active_harmonics (Dict): Active harmonic target transitions
	"""
	
	def __init__(self) -> None:
		"""
		Initialize the Performance Dashboard application.
		
		This constructor sets up the main window, initializes all data structures,
		creates the user interface, configures timers for data collection, and
		establishes the communication console. The application starts in a
		maximized window with a dark theme for professional appearance.
		
		The initialization process includes:
		1. Window setup and basic configuration
		2. Data structure initialization for subsystems and cores
		3. UI construction and layout
		4. Timer setup for data collection and file monitoring
		5. Communication console initialization
		6. Theme application and window display
		
		Note:
			The application defaults to monitoring 7 CPU cores until the actual
			core count is detected from the system.
		"""
		super().__init__()
		
		# Basic window configuration
		self.setWindowTitle("Performance Dashboard")
		self.resize(1200, 720)

		# Initialize performance data structures
		# Dictionary mapping subsystem names to their state objects
		self.states: Dict[str, SubsystemState] = {name: SubsystemState(name=name) for name in SUBSYSTEMS}
		# Dictionary mapping core IDs to their state objects (initialized later)
		self.core_states: Dict[int, CoreState] = {}
		# Lists of currently active subsystems and cores for monitoring
		self.active_subsystems: List[str] = []
		self.active_cores: List[int] = []
		# Runtime state flags
		self.is_running: bool = False
		self.end_time_epoch: Optional[float] = None

		# External process management
		self.process: Optional[QtCore.QProcess] = None
		self.line_buffer: bytes = b""

		# Initialize CPU cores (detects actual core count)
		self._init_cpu_cores()
		
		# Initialize scheduling system for target changes
		# Each entry: (time_offset_seconds, subsystem, target_value, mode)
		# mode: "sudden" or "harmonic"
		self.scheduled_changes: List[Tuple[float, str, int, str]] = []
		self.schedule_timer: Optional[QtCore.QTimer] = None
		self.test_start_time: Optional[float] = None
		# Active harmonic ramps: subsystem -> (start_time_s, end_time_s, start_value, end_value)
		self.active_harmonics: Dict[str, Tuple[float, float, int, int]] = {}

		# Build the user interface
		self._build_ui()
		self._build_toolbar()
		self._configure_plot()
		# Initialize the active combo with all subsystems
		self._rebuild_active_combo()
		
		# Display the window maximized with controls visible
		self.showMaximized()
		self._apply_dark_theme()
		
		# Internal sampler timer removed - using external backend for data collection
		
		# File tail reader for stress tool output monitoring
		# Monitors external stress tool output files for real-time data
		self._file_tail_timer = QtCore.QTimer(self)
		self._file_tail_timer.setInterval(500)
		self._file_tail_timer.timeout.connect(self._read_stress_file_tail)
		self._file_tail_path: Optional[str] = None
		self._file_tail_pos: int = 0
		self._file_tail_rem: bytes = b""
		self._file_block_idx: int = -1
		self._file_start_epoch: float = 0.0
		self._file_watcher = QtCore.QFileSystemWatcher(self)
		self._file_watcher.fileChanged.connect(lambda _p: self._read_stress_file_tail())
		
		# Block playback queue and scheduler (emit blocks every 250ms)
		# Used for processing stress tool output in blocks
		self._block_queue: List[Tuple[Optional[float], Dict[int, float], Optional[float], Optional[float]]] = []
		self._next_block_due_epoch: Optional[float] = None
		self._block_timer = QtCore.QTimer(self)
		self._block_timer.setInterval(250)
		self._block_timer.timeout.connect(self._maybe_emit_block)
		
		# Persistent parse state for partial blocks across timer ticks
		# Maintains state when parsing incomplete data blocks
		self._blk_active: bool = False
		self._blk_cpu_overall: Optional[float] = None
		self._blk_core_vals: Dict[int, float] = {}
		self._blk_dram_val: Optional[float] = None
		self._blk_gpu_val: Optional[float] = None
		
		# Raw log buffer used for Show Log (works for local tail and adb tail)
		self._raw_log_buffer: str = ""
		
		# Execution UI helpers for process management
		self._exec_info_msg: Optional[QtWidgets.QMessageBox] = None
		self._exec_completed: bool = False

	# No app-level event filter required; handled inside CommConsole

	def _init_cpu_cores(self) -> None:
		"""
		Initialize CPU core states for monitoring.
		
		This method sets up the data structures needed to monitor individual
		CPU cores. It creates CoreState objects for each core and sets a
		default core count until the actual system core count is detected.
		
		The method:
		1. Sets a default core count (7 cores) as a fallback
		2. Creates CoreState objects for each core ID
		3. Stores them in the core_states dictionary
		
		Note:
			The actual core count is detected later during runtime when
			the system metrics are first sampled. This default ensures
			the application can start even if core detection fails.
		"""
		# Default to 7 cores until Linux reports actual core count
		# This provides a reasonable fallback for most systems
		self.core_count = 7
		
		# Create CoreState objects for each core
		# Each core gets its own state object for independent monitoring
		self.core_states = {i: CoreState(core_id=i) for i in range(self.core_count)}

	def _build_ui(self) -> None:
		"""
		Build the main user interface layout.
		
		This method constructs the complete GUI layout including:
		- Main window with central widget
		- Left control panel with scrollable area
		- Collapsible control panel with toggle handle
		- Right side with performance graphs and communication console
		- All control widgets and their layouts
		
		The UI is designed with a professional layout that maximizes
		the space for performance graphs while keeping controls easily
		accessible. The control panel can be collapsed to provide more
		space for the graphs when needed.
		
		Layout Structure:
		- Central widget with horizontal layout
		- Left: Scrollable control panel (collapsible)
		- Middle: Toggle handle for panel collapse/expand
		- Right: Performance graphs and communication console
		"""
		# Create the central widget and set it as the main window's central widget
		central = QtWidgets.QWidget(self)
		central.setObjectName("rootCentral")
		self.setCentralWidget(central)

		# Main horizontal layout for the entire interface
		root = QtWidgets.QHBoxLayout(central)

		# Left panel inside a scroll area to avoid forcing huge window height
		# This allows the control panel to scroll if there are many controls
		controls_scroll = QtWidgets.QScrollArea()
		controls_scroll.setWidgetResizable(True)
		controls_container = QtWidgets.QWidget()
		controls_container.setObjectName("controlsContainer")
		controls = QtWidgets.QVBoxLayout(controls_container)
		controls.setContentsMargins(10, 10, 10, 10)
		controls.setSpacing(10)
		controls_scroll.setWidget(controls_container)
		
		# Keep reference for collapse/expand animation
		self.controls_scroll = controls_scroll
		self.controls_container = controls_container
		self.controls_collapsed = False
		self.controls_initial_width = 320
		
		# Configure the scroll area size and behavior
		controls_scroll.setMinimumWidth(0)
		controls_scroll.setMaximumWidth(self.controls_initial_width)
		controls_scroll.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
		root.addWidget(controls_scroll, 0)
		
		# Slider-like handle next to the panel to toggle visibility (top-right aligned)
		# This provides an intuitive way to hide/show the control panel
		handle = QtWidgets.QToolButton()
		handle.setObjectName("panelHandle")
		handle.setCheckable(True)
		handle.setChecked(True)
		handle.setFixedWidth(36)
		handle.setFixedHeight(36)
		handle.setText("")
		handle.setArrowType(QtCore.Qt.LeftArrow)
		handle.setAutoRaise(True)
		handle.setIconSize(QtCore.QSize(20, 20))
		handle.setToolTip("Hide/Show controls")
		handle.toggled.connect(lambda _: self._toggle_controls())
		self.panel_handle = handle
		
		# Container for the toggle handle
		handle_container = QtWidgets.QWidget()
		handle_container.setFixedWidth(40)
		vc = QtWidgets.QVBoxLayout(handle_container)
		vc.setContentsMargins(2, 4, 2, 0)
		vc.setSpacing(0)
		vc.addWidget(handle, 0, QtCore.Qt.AlignTop)
		vc.addStretch(1)
		root.addWidget(handle_container, 0)

		# Subsystems group
		sys_group = QtWidgets.QGroupBox("Subsystems")
		sys_layout = QtWidgets.QGridLayout(sys_group)
		sys_layout.setContentsMargins(10, 8, 10, 10)
		sys_layout.setSpacing(8)
		sys_layout.setColumnMinimumWidth(0, 80)  # Subsystem names column
		sys_layout.setColumnMinimumWidth(1, 60)  # Adaptive checkboxes column
		
		# Header row
		adaptive_header = QtWidgets.QLabel("Adaptive")
		adaptive_header.setAlignment(QtCore.Qt.AlignCenter)
		adaptive_header.setStyleSheet("font-weight: bold; color: #A0AEC0; font-size: 11px;")
		sys_layout.addWidget(adaptive_header, 0, 1)  # Row 0, Column 1
		
		# Subsystem checkboxes with adaptive checkboxes
		self.checkbox_group: Dict[str, QtWidgets.QCheckBox] = {}
		self.adaptive_checkbox_group: Dict[str, QtWidgets.QCheckBox] = {}
		for i, name in enumerate(SUBSYSTEMS, 1):  # Start from row 1
			# Subsystem checkbox
			cb = QtWidgets.QCheckBox(name)
			cb.setToolTip(f"Toggle {name} tracking and target controls")
			cb.stateChanged.connect(self._on_subsystem_toggled)
			sys_layout.addWidget(cb, i, 0)  # Row i, Column 0
			self.checkbox_group[name] = cb
			
			# Adaptive checkbox - centered under the header
			adaptive_cb = QtWidgets.QCheckBox()
			adaptive_cb.setToolTip(f"Enable adaptive mode for {name} (requires {name} to be selected first)")
			adaptive_cb.stateChanged.connect(self._on_adaptive_toggled)
			adaptive_cb.setStyleSheet("QCheckBox { margin-left: 20px; }")  # Just center it
			adaptive_cb.setEnabled(False)  # Initially disabled
			sys_layout.addWidget(adaptive_cb, i, 1)  # Row i, Column 1
			self.adaptive_checkbox_group[name] = adaptive_cb
		
		# Add stretch to the right to push content left
		sys_layout.setColumnStretch(2, 1)
		
		controls.addWidget(sys_group)

		# CPU Cores section (initially hidden)
		controls.addSpacing(8)
		self.cpu_cores_group = QtWidgets.QGroupBox("CPU Cores")
		self.cpu_cores_group.setVisible(False)
		cores_layout = QtWidgets.QVBoxLayout(self.cpu_cores_group)
		cores_layout.setContentsMargins(10, 8, 10, 10)
		cores_layout.setSpacing(4)
		self.cores_layout = cores_layout
		
		# Header above cores to label slider column
		header_row = QtWidgets.QHBoxLayout()
		header_row.setSpacing(6)
		header_row.addWidget(QtWidgets.QLabel(""))  # spacer for checkbox column
		header_target = QtWidgets.QLabel("Target (%)")
		header_row.addWidget(header_target)
		header_row.addWidget(QtWidgets.QLabel(""))  # spacer for live value column
		header_row.addStretch(1)
		cores_layout.addLayout(header_row)
		
		self.core_checkboxes: Dict[int, QtWidgets.QCheckBox] = {}
		self.core_sliders: Dict[int, QtWidgets.QSlider] = {}
		self.core_labels: Dict[int, QtWidgets.QLabel] = {}
		self.core_texts: Dict[int, QtWidgets.QLineEdit] = {}
		controls.addWidget(self._make_label("Targets (%)", bold=True))
		# CPU Target row (replaces Core 0)
		cpu_target_row = QtWidgets.QHBoxLayout()
		cpu_target_cb = QtWidgets.QCheckBox("CPU Target")
		cpu_target_cb.stateChanged.connect(self._on_cpu_target_toggled)
		cpu_target_row.addWidget(cpu_target_cb)
		self.cpu_target_checkbox = cpu_target_cb
		
		# CPU Target slider
		cpu_target_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
		cpu_target_slider.setRange(0, 100)
		cpu_target_slider.setValue(self.states[Subsystem.CPU].target_percent)
		cpu_target_slider.setMinimumWidth(120)
		cpu_target_slider.setMaximumWidth(16777215)
		cpu_target_slider.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
		cpu_target_slider.valueChanged.connect(lambda val: self._on_cpu_target_changed(val))
		cpu_target_row.addWidget(cpu_target_slider)
		self.cpu_target_slider = cpu_target_slider
		cpu_target_slider.setVisible(False)
		
		# CPU Target text box
		cpu_target_text = QtWidgets.QLineEdit(str(self.states[Subsystem.CPU].target_percent))
		cpu_target_text.setValidator(QtGui.QIntValidator(0, 100, self))
		cpu_target_text.setFixedWidth(40)
		cpu_target_text.setAlignment(QtCore.Qt.AlignCenter)
		cpu_target_slider.valueChanged.connect(lambda val, field=cpu_target_text: field.setText(str(int(val))))
		def _apply_cpu_target_txt():
			try:
				val_int = int(cpu_target_text.text() or 0)
				val_int = max(0, min(100, val_int))
				if cpu_target_slider.value() != val_int:
					cpu_target_slider.setValue(val_int)
			except Exception:
				pass
		cpu_target_text.editingFinished.connect(_apply_cpu_target_txt)
		cpu_target_row.addWidget(cpu_target_text)
		self.cpu_target_text = cpu_target_text
		
		cpu_target_row.addStretch(1)
		cores_layout.addLayout(cpu_target_row)
		
		# CPU cores 0-6 (7 cores total)
		self.core_row_widgets: Dict[int, QtWidgets.QWidget] = {}
		for core_id in range(self.core_count):
			# Create row container for each core for easy show/hide later
			row_container = QtWidgets.QWidget()
			core_row = QtWidgets.QHBoxLayout(row_container)
			core_row.setContentsMargins(0, 0, 0, 0)
			core_row.setSpacing(6)
			
			# Checkbox
			cb = QtWidgets.QCheckBox(f"Core {core_id}")
			cb.stateChanged.connect(self._on_core_toggled)
			core_row.addWidget(cb)
			self.core_checkboxes[core_id] = cb
			
			# Slider
			slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
			slider.setRange(0, 100)
			slider.setValue(self.core_states[core_id].target_percent)
			slider.setMinimumWidth(120)
			slider.setMaximumWidth(16777215)
			slider.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
			slider.valueChanged.connect(lambda val, cid=core_id: self._on_core_target_changed(cid, val))
			core_row.addWidget(slider)
			self.core_sliders[core_id] = slider
			slider.setVisible(False)
			
			# Editable percent to the right of slider
			txt = QtWidgets.QLineEdit(str(self.core_states[core_id].target_percent))
			txt.setValidator(QtGui.QIntValidator(0, 100, self))
			txt.setFixedWidth(40)
			txt.setAlignment(QtCore.Qt.AlignCenter)
			slider.valueChanged.connect(lambda val, field=txt: field.setText(str(int(val))))
			def _apply_txt(cid=core_id, field_ref=None):
				# On edit, push to slider and target
				try:
					val_int = int((field_ref or txt).text() or 0)
					val_int = max(0, min(100, val_int))
					sel_slider = self.core_sliders[cid]
					if sel_slider.value() != val_int:
						sel_slider.setValue(val_int)
				except Exception:
					pass
			txt.editingFinished.connect(lambda cid=core_id, field_ref=txt: _apply_txt(cid, field_ref))
			core_row.addWidget(txt)
			self.core_texts[core_id] = txt
			
			# Add stretch to push everything to the left
			core_row.addStretch(1)
			
			cores_layout.addWidget(row_container)
			self.core_row_widgets[core_id] = row_container
		
		controls.addWidget(self.cpu_cores_group)

		controls.addSpacing(8)
		self.slider_container = QtWidgets.QWidget()
		self.slider_form = QtWidgets.QFormLayout(self.slider_container)
		self.slider_form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
		self.slider_form.setHorizontalSpacing(10)
		self.slider_form.setVerticalSpacing(6)
		controls.addWidget(self.slider_container, 0)

		# User Setup group
		controls.addSpacing(8)
		user_group = QtWidgets.QGroupBox("User Setup")
		user_group.setObjectName("userGroup")
		user_v = QtWidgets.QVBoxLayout(user_group)
		user_v.setContentsMargins(10, 8, 10, 10)
		user_v.addWidget(self._make_label("Duration", bold=True))
		duration_row = QtWidgets.QHBoxLayout()
		self.duration_spin = QtWidgets.QSpinBox()
		self.duration_spin.setRange(1, 24 * 60 * 60)
		self.duration_spin.setValue(60)
		self.duration_spin.setSuffix(" s")
		duration_row.addWidget(self.duration_spin)
		user_v.addLayout(duration_row)
		# Log file path input
		log_file_row = QtWidgets.QHBoxLayout()
		log_file_row.addWidget(self._make_label("Log file path:"))
		self.log_file_edit = QtWidgets.QLineEdit()
		# Default empty; AAOS logs are streamed via adb automatically
		self.log_file_edit.setText("")
		self.log_file_edit.setPlaceholderText("/path/to/log (for Yocto/Ubuntu flows)")
		self.log_file_edit.setToolTip("Full path to the stress tool output file")
		log_file_row.addWidget(self.log_file_edit)
		user_v.addLayout(log_file_row)
		# Slider + editable text field synced with the spinbox
		slider_row = QtWidgets.QHBoxLayout()
		self.duration_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
		self.duration_slider.setRange(1, 3600)
		self.duration_slider.setValue(60)
		slider_row.addWidget(self.duration_slider, 1)
		self.duration_text = QtWidgets.QLineEdit()
		self.duration_text.setFixedWidth(64)
		self.duration_text.setText("60")
		self.duration_text.setValidator(QtGui.QIntValidator(1, 24 * 60 * 60, self))
		slider_row.addWidget(self.duration_text)
		user_v.addLayout(slider_row)
		# Wiring: keep all three in sync
		self.duration_slider.valueChanged.connect(lambda v: (self.duration_spin.setValue(int(v)), self._update_command_preview()))
		self.duration_spin.valueChanged.connect(lambda v: (self.duration_text.setText(str(int(v))), self._update_command_preview()))
		self.duration_spin.valueChanged.connect(lambda v: (self.duration_slider.setValue(int(v)) if 1 <= int(v) <= 3600 else None))
		self.duration_text.editingFinished.connect(lambda: (self.duration_spin.setValue(int(self.duration_text.text() or 60)), self._update_command_preview()))
		# Generated command preview (for Linux stress tool)
		user_v.addWidget(self._make_label("Generated command (auto-updates):", bold=True))
		cmd_row = QtWidgets.QHBoxLayout()
		self.command_preview = QtWidgets.QTextEdit()
		self.command_preview.setObjectName("commandPreview")
		mono = QtGui.QFont()
		mono.setFamily("Consolas")
		mono.setPointSize(10)
		self.command_preview.setFont(mono)
		self.command_preview.setReadOnly(True)
		self.command_preview.setLineWrapMode(QtWidgets.QTextEdit.WidgetWidth)
		self.command_preview.setFixedHeight(72)
		cmd_row.addWidget(self.command_preview, 1)
		self.btn_copy_cmd = QtWidgets.QToolButton()
		self.btn_copy_cmd.setText("Copy")
		self.btn_copy_cmd.setToolTip("Copy command to clipboard")
		self.btn_copy_cmd.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.command_preview.toPlainText()))
		cmd_row.addWidget(self.btn_copy_cmd)
		user_v.addLayout(cmd_row)
		
		# Test mode removed
		controls.addWidget(user_group)

		# Actions moved to toolbar; keep object handles for shortcuts
		self.btn_start = QtWidgets.QPushButton()
		self.btn_start.clicked.connect(self._on_start)
		self.btn_stop = QtWidgets.QPushButton()
		self.btn_stop.clicked.connect(self._on_stop)
		self.btn_clear = QtWidgets.QPushButton()
		self.btn_clear.clicked.connect(self._on_clear)
		self.btn_export_csv = QtWidgets.QPushButton()
		self.btn_export_csv.clicked.connect(self._on_export_csv)
		self.btn_export_png = QtWidgets.QPushButton()
		self.btn_export_png.clicked.connect(self._on_export_png)
		self.btn_schedule_load = QtWidgets.QPushButton()
		self.btn_schedule_load.clicked.connect(self._on_schedule_load)
		# Shortcuts
		QtGui.QShortcut(QtGui.QKeySequence("Ctrl+R"), self, activated=self._on_start)
		QtGui.QShortcut(QtGui.QKeySequence("Escape"), self, activated=self._on_stop)
		QtGui.QShortcut(QtGui.QKeySequence("Ctrl+S"), self, activated=self._on_export_csv)
		QtGui.QShortcut(QtGui.QKeySequence("Ctrl+P"), self, activated=self._on_export_png)
		controls.addStretch(1)

		# View Data card (replaces Current Values)
		values_group = QtWidgets.QGroupBox("View Data")
		values_layout = QtWidgets.QVBoxLayout(values_group)
		values_layout.setContentsMargins(8, 6, 8, 8)
		# Toggle button
		toggle_row = QtWidgets.QHBoxLayout()
		self.view_data_btn = QtWidgets.QPushButton("Show Log")
		self.view_data_btn.clicked.connect(self._open_log_dialog)
		toggle_row.addWidget(self.view_data_btn)
		toggle_row.addStretch(1)
		values_layout.addLayout(toggle_row)
		# Keep compact list inside the card
		self.numeric_list = QtWidgets.QListWidget()
		self.numeric_list.setMinimumWidth(260)
		self.numeric_list.setAlternatingRowColors(True)
		values_layout.addWidget(self.numeric_list)
		controls.addWidget(values_group, 1)

		right = QtWidgets.QVBoxLayout()
		root.addLayout(right, 1)

		select_row = QtWidgets.QHBoxLayout()
		select_row.addWidget(self._make_label("Active graph:"))
		self.combo_active = QtWidgets.QComboBox()
		self.combo_active.currentTextChanged.connect(self._on_active_changed)
		select_row.addWidget(self.combo_active)
		select_row.addStretch(1)
		# KPI header chips (clickable)
		self.kpi_cpu = self._create_kpi_label("CPU")
		self.kpi_gpu = self._create_kpi_label("GPU")
		self.kpi_dram = self._create_kpi_label("DRAM")
		select_row.addWidget(self.kpi_cpu)
		select_row.addWidget(self.kpi_gpu)
		select_row.addWidget(self.kpi_dram)
		select_row.addStretch(1)
		# Tail status (debug)
		self.tail_status = QtWidgets.QLabel("")
		self.tail_status.setMinimumWidth(140)
		select_row.addWidget(self.tail_status)
		# Toggle UART / Graph button
		self.btn_uart_toggle = QtWidgets.QPushButton("Access UART")
		self.btn_uart_toggle.setCheckable(True)
		self.btn_uart_toggle.toggled.connect(self._on_toggle_uart)
		select_row.addWidget(self.btn_uart_toggle)
		# Move Reset View into the same row to keep a single compact header
		reset_button = QtWidgets.QPushButton("Reset View")
		reset_button.setMaximumHeight(28)
		reset_button.clicked.connect(self._on_reset_graph)
		select_row.addWidget(reset_button)
		select_row.setContentsMargins(0, 0, 0, 0)
		right.addLayout(select_row)

		# Stacked area: Graph view and Communication console
		self.main_stack = QtWidgets.QStackedWidget()
		# Graph page
		graph_page = QtWidgets.QWidget()
		graph_v = QtWidgets.QVBoxLayout(graph_page)
		graph_v.setContentsMargins(0, 0, 0, 0)
		self.plot_widget = pg.PlotWidget(axisItems={'bottom': TimeAxis(orientation='bottom')})
		self.plot_widget.setBackground("w")
		graph_v.addWidget(self.plot_widget, 1)
		self.main_stack.addWidget(graph_page)  # index 0
		# Communication console page (UART now; extensible for SSH/ADB)
		self.comm_console = CommConsole(self)
		self.main_stack.addWidget(self.comm_console)  # index 1
		self.main_stack.setCurrentIndex(0)
		right.addWidget(self.main_stack, 1)

	def _configure_plot(self) -> None:
		self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
		self.plot_widget.setLabel("left", "Performance", units="%")
		self.plot_widget.setLabel("bottom", "Time", units="s")
		self.plot_widget.setYRange(0, 100)
		
		# Label the custom time axis
		bottom_axis = self.plot_widget.getAxis('bottom')
		bottom_axis.setLabel(text="Time")
		
		# Enhanced scrolling and interaction
		self.plot_widget.setMouseEnabled(x=True, y=True)
		self.plot_widget.setLimits(xMin=0, yMin=0, yMax=100)
		
		# Better axis formatting
		self.plot_widget.getAxis('left').setTickSpacing(10, 5)
		
		# Improved scrolling behavior
		self.plot_widget.setAutoVisible(y=True)
		self.plot_widget.enableAutoRange(axis='y')
		
		# Set initial view range
		self.plot_widget.setXRange(0, 60)
		
		# Enable smooth scrolling and better performance
		self.plot_widget.setClipToView(True)
		self.plot_widget.setDownsampling(mode='peak')
		self.plot_widget.setDownsampling(auto=True)
		
		# Better performance for real-time updates
		self.plot_widget.setCacheMode(QtWidgets.QGraphicsView.CacheBackground)
		
		# Configure mouse interaction for better scrolling
		viewbox = self.plot_widget.getViewBox()
		viewbox.setMouseMode(pg.ViewBox.PanMode)
		viewbox.setAspectLocked(False)
		viewbox.setLimits(xMin=0, yMin=0, yMax=100)
		
		# Enable smooth mouse interaction
		viewbox.setMouseEnabled(x=True, y=True)
		
		# Connect to view range change for dynamic time scaling
		viewbox.sigRangeChanged.connect(self._on_view_range_changed)

	def _build_toolbar(self) -> None:
		"""Create a compact toolbar for primary actions."""
		toolbar = QtWidgets.QToolBar("Main")
		toolbar.setMovable(False)
		toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
		self.addToolBar(toolbar)
		# Target OS dropdown placed at the far left
		self.combo_target_os = QtWidgets.QComboBox()
		self.combo_target_os.addItems(["Yocto", "Ubuntu", "AAOS"])
		self.combo_target_os.setToolTip("Select target OS")
		self.combo_target_os.setMinimumWidth(120)
		self.combo_target_os.currentTextChanged.connect(self._on_os_changed)
		self.selected_target_os = self.combo_target_os.currentText()
		toolbar.addWidget(self.combo_target_os)
		
		# Initialize OS-specific running states
		self.os_running_states = {"Yocto": False, "Ubuntu": False, "AAOS": False}
		# Load Binary button between OS dropdown and Execute Test
		self.btn_load_binary = QtWidgets.QToolButton()
		self.btn_load_binary.setText("Load Binary")
		self.btn_load_binary.setToolTip("Choose a binary to use with the test")
		self.btn_load_binary.setObjectName("btn_load")
		self.btn_load_binary.clicked.connect(self._on_load_binary)
		toolbar.addWidget(self.btn_load_binary)
		
		# Actions
		a_start = QtGui.QAction("Execute Test", self)
		a_start.triggered.connect(self._on_start)
		a_stop = QtGui.QAction("Stop", self)
		a_stop.triggered.connect(self._on_stop)
		a_clear = QtGui.QAction("Clear Data", self)
		a_clear.triggered.connect(self._on_clear)
		a_csv = QtGui.QAction("Export CSV", self)
		a_csv.triggered.connect(self._on_export_csv)
		a_png = QtGui.QAction("Export PNG", self)
		a_png.triggered.connect(self._on_export_png)
		a_sched = QtGui.QAction("Schedule Load", self)
		a_sched.triggered.connect(self._on_schedule_load)
		# Collapse/expand handled by side handle; Reset View exists in plot header
		for act in [a_start, a_stop, a_clear, a_sched, a_csv, a_png]:
			toolbar.addAction(act)
		# Tag important toolbar buttons for themed styling
		# Keep references so we can enable/disable the visible toolbar buttons
		self.action_execute = a_start
		self.action_stop = a_stop
		btn_exec = toolbar.widgetForAction(a_start)
		if isinstance(btn_exec, QtWidgets.QToolButton):
			btn_exec.setObjectName("btn_execute")
			self.btn_execute_widget = btn_exec
		btn_stop_w = toolbar.widgetForAction(a_stop)
		if isinstance(btn_stop_w, QtWidgets.QToolButton):
			btn_stop_w.setObjectName("btn_stop")
			self.btn_stop_widget = btn_stop_w

		# Push the theme toggle to the extreme right
		spacer = QtWidgets.QWidget()
		spacer.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
		toolbar.addWidget(spacer)
		self.btn_theme = QtWidgets.QToolButton()
		self.btn_theme.setText("Light Mode")  # default we start in dark
		self.btn_theme.clicked.connect(self._toggle_theme)
		toolbar.addWidget(self.btn_theme)
		# Remove text action toggle; handled by side handle now

	def _kill_android_stress_tool_via_adb(self) -> None:
		"""Run adb root, find android_stress_tool PID, and kill it.

		We parse the output of `adb shell ps | grep android_stress_tool` and kill
		all matching PIDs to be safe. Errors are ignored silently.
		"""
		try:
			import subprocess, re
			
			# Show stop commands in CMD terminal if available
			if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'cmd_terms') and self.comm_console.cmd_terms:
				cmd_term = self.comm_console.cmd_terms[0]  # Use first CMD terminal
				if hasattr(cmd_term, 'input'):
					# Send stop commands to CMD terminal
					stop_commands = [
						"adb root",
						"adb shell pidof android_stress_tool",
						"adb shell \"ps | grep android_stress_tool | grep -v grep\"",
						"adb shell kill $(pidof android_stress_tool)"
					]
					for cmd in stop_commands:
						cmd_term.input.setText(cmd)
						cmd_term.input.returnPressed.emit()
			
			def _adb(args, **kw):
				return subprocess.run(["adb", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5, **kw)
			# Try to elevate
			try:
				_adb(["root"])  # ignore result
			except Exception:
				pass
			# First try pidof (simplest)
			pid_res = _adb(["shell", "pidof android_stress_tool"])  # toybox/busybox
			pids: list[str] = []
			if pid_res.returncode == 0 and pid_res.stdout.strip():
				pids = [p for p in pid_res.stdout.strip().split() if p.isdigit()]
			# Fallback to ps | grep via sh -c
			if not pids:
				ps_res = _adb(["shell", "sh", "-c", "ps | grep android_stress_tool | grep -v grep"]) 
				out = ps_res.stdout.strip()
				for line in out.splitlines():
					line = line.strip()
					if not line:
						continue
					m = re.search(r"\b(\d{2,})\b", line)
					if m:
						pids.append(m.group(1))
			# Kill all collected PIDs
			for pid in pids:
				_adb(["shell", "kill", pid])
		except Exception:
			pass

	def _kill_stress_tool_via_uart(self) -> None:
		"""Send pkill stress_tool command over UART to stop background stress_tool processes."""
		try:
			# Check if UART is connected and we have comm_console
			if hasattr(self, 'comm_console') and self.comm_console.uart_connect_btn.isChecked():
				# Send pkill command over UART
				print("[DEBUG] Sending pkill stress_tool command over UART")
				self.comm_console.send_commands([
					"pkill stress_tool"
				], spacing_ms=200)
				print("[DEBUG] pkill command sent successfully")
			else:
				print("[DEBUG] UART not connected, trying alternative stop method")
				# Try alternative method to stop the process
				self._kill_stress_tool_alternative()
				# Show error message in UART console if available
				if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'log'):
					self.comm_console.log.appendPlainText("\n[WARNING] UART not connected - using alternative stop method")
		except Exception as e:
			print(f"[DEBUG] Failed to send pkill command over UART: {e}")
			# Show error message in UART console if available
			if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'log'):
				self.comm_console.log.appendPlainText(f"\n[ERROR] Failed to send stop command: {e}")

	def _kill_stress_tool_alternative(self) -> None:
		"""Alternative method to stop stress_tool when UART is not available."""
		try:
			import subprocess
			import psutil
			
			print("[DEBUG] Trying alternative stop method...")
			
			# Try to find and kill stress_tool processes locally
			killed_processes = []
			for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
				try:
					if proc.info['name'] and 'stress_tool' in proc.info['name'].lower():
						print(f"[DEBUG] Found stress_tool process: PID {proc.info['pid']}")
						proc.kill()
						killed_processes.append(proc.info['pid'])
				except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
					pass
			
			if killed_processes:
				print(f"[DEBUG] Killed {len(killed_processes)} stress_tool processes: {killed_processes}")
				# Show success message in UART console if available
				if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'log'):
					self.comm_console.log.appendPlainText(f"\n[SUCCESS] Killed {len(killed_processes)} stress_tool processes locally")
			else:
				print("[DEBUG] No stress_tool processes found to kill")
				# Show info message in UART console if available
				if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'log'):
					self.comm_console.log.appendPlainText("\n[INFO] No stress_tool processes found to stop")
					
		except Exception as e:
			print(f"[DEBUG] Alternative stop method failed: {e}")
			# Show error message in UART console if available
			if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'log'):
				self.comm_console.log.appendPlainText(f"\n[ERROR] Alternative stop method failed: {e}")

	def _on_load_binary(self) -> None:
		"""Auto-run UART workflow or AAOS ADB workflow based on target OS."""
		try:
			print("[DEBUG] Load Binary button clicked")
			# Update tooltip to indicate automated flow
			if hasattr(self, 'btn_load_binary'):
				self.btn_load_binary.setToolTip("Auto loading via UART (no file selection)")
			# Route based on OS selection
			os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
			print(f"[DEBUG] Selected OS: {os_sel}")
			
			if os_sel == "AAOS":
				print("[DEBUG] Using ADB workflow for AAOS")
				self._load_binary_via_adb_aaos()
				return
			if os_sel not in ("Yocto", "Ubuntu"):
				print(f"[DEBUG] OS {os_sel} not supported")
				self._show_info_dialog("Not Supported", "Load Binary is supported for Yocto, Ubuntu, or AAOS.")
				return
			
			print("[DEBUG] Using UART workflow for Yocto/Ubuntu")
			# Refresh core count dynamically by querying Linux via hidden UART
			print("[DEBUG] Step 1: Updating core count from Linux")
			self._update_core_count_from_linux()
			
			# Rebuild core UI and active-graph list
			print("[DEBUG] Step 2: Rebuilding core UI")
			self._rebuild_core_ui()
			print("[DEBUG] Step 3: Rebuilding active combo")
			self._rebuild_active_combo()
			print("[DEBUG] Step 4: Auto loading binary over UART")
			self._auto_load_binary_over_uart()
		except Exception as e:
			print(f"[DEBUG] Load binary error: {e}")
			import traceback
			traceback.print_exc()

	def _load_binary_via_adb_aaos(self) -> None:
		"""Send two adb commands to the embedded CMD terminal for AAOS.

		Commands executed in CMD:
		  adb root
		  adb push d:\\android_stress_tool /tmp/
		If a device is connected, the commands are prefixed with -s <first-serial>.
		"""
		try:
			# Discover first connected device (optional)
			serial = None
			try:
				devs = _adb_list_devices()
				if devs:
					serial = devs[0][0]
			except Exception:
				serial = None
			# Prepare commands
			if serial:
				cmd1 = f"adb -s {serial} root"
				cmd2 = f"adb -s {serial} push d:\\android_stress_tool /tmp/"
			else:
				cmd1 = "adb root"
				cmd2 = "adb push d:\\android_stress_tool /tmp/"
			# Show console and switch to CMD protocol
			try:
				self.btn_uart_toggle.setChecked(True)
				self.main_stack.setCurrentIndex(1)
				if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'proto_combo'):
					self.comm_console.proto_combo.setCurrentIndex(3)  # CMD
					self.comm_console._on_proto_changed()
			except Exception:
				pass
			# Use first CMD terminal if available
			term = None
			try:
				if hasattr(self.comm_console, 'cmd_terms') and self.comm_console.cmd_terms:
					term = self.comm_console.cmd_terms[0]
			except Exception:
				term = None
			if term and hasattr(term, 'input') and hasattr(term, '_send'):
				for c in (cmd1, cmd2):
					term.input.setText(c)
					term._send()
				return
			# Fallback: run in background cmd if CMD terminal not available
			import subprocess
			fallback = f"{cmd1} && {cmd2}"
			subprocess.Popen(["cmd", "/d", "/c", fallback], creationflags=0)
		except Exception:
			pass

	def _update_core_count_from_linux(self) -> None:
		"""Query Linux over UART for number of cores using nproc and update UI state.

		Falls back to existing core_count on failure.
		"""
		try:
			from serial.tools import list_ports
			import serial
			import re
			
			needle = "VID:PID=067B:23A3"
			candidates = []
			for p in list_ports.comports():
				if needle in (getattr(p, 'hwid', '') or ''):
					candidates.append(p.device)
			
			if not candidates:
				print(f"[DEBUG] No UART ports found with VID:PID={needle}")
				return
			
			def _com_num(name: str) -> int:
				m = re.search(r"COM(\d+)$", name.upper())
				return int(m.group(1)) if m else 1_000_000
			candidates.sort(key=_com_num)
			port = candidates[0]
			print(f"[DEBUG] Using UART port: {port}")
			
			ser = None
			try:
				ser = serial.Serial(port=port, baudrate=921600, timeout=3, write_timeout=2)
				print(f"[DEBUG] Connected to {port}, sending nproc command...")
				
				# Clear any pending bytes to avoid mixing old output
				try:
					ser.reset_input_buffer()
				except Exception:
					pass
				# Send nproc command
				ser.write(b"nproc\n")
				ser.flush()  # Ensure command is sent
				
				# Wait a bit longer and read more data
				time.sleep(1.0)
				resp = ser.read(256).decode(errors='ignore')
				print(f"[DEBUG] nproc response: {repr(resp)}")
				
				# Look for a line that is purely a number (avoid timestamps/prompts)
				val: Optional[int] = None
				for line in resp.splitlines():
					if re.fullmatch(r"\s*\d+\s*", line or ""):
						try:
							val_candidate = int(line.strip())
							# Cap core count to a maximum of 6 as requested
							if 1 <= val_candidate <= 10:
								val = val_candidate
								break
						except Exception:
							pass
				if val is not None:
					old_count = getattr(self, 'core_count', 0)
					if val != old_count:
						self.core_count = val
						print(f"[DEBUG] Updated core count from {old_count} to {val}")
					else:
						print(f"[DEBUG] Core count already set to {val}")
				else:
					print(f"[DEBUG] No number found in nproc response: {resp}")
					
			except Exception as e:
				print(f"[DEBUG] UART communication error: {e}")
			finally:
				try:
					if ser is not None:
						ser.close()
						print(f"[DEBUG] Closed UART connection to {port}")
				except Exception as e:
					print(f"[DEBUG] Error closing UART: {e}")
		except Exception as e:
			print(f"[DEBUG] Core count detection failed: {e}")

	def _rebuild_core_ui(self) -> None:
		"""Recreate CPU cores UI based on self.core_count."""
		if not hasattr(self, 'cores_layout'):
			print("[DEBUG] cores_layout not found, skipping UI rebuild")
			return
		
		core_count = getattr(self, 'core_count', 0)
		print(f"[DEBUG] Rebuilding core UI with {core_count} cores")
		
		# Remove all existing items (widgets and sub-layouts) to avoid duplicates
		self._clear_layout(self.cores_layout)
		# Reset data structures
		self.core_checkboxes.clear()
		self.core_sliders.clear()
		self.core_labels.clear()
		self.core_texts.clear()
		self.core_row_widgets.clear()
		# Header row
		header_row = QtWidgets.QHBoxLayout()
		header_row.setSpacing(6)
		header_row.addWidget(QtWidgets.QLabel(""))
		header_row.addWidget(QtWidgets.QLabel("Target (%)"))
		header_row.addWidget(QtWidgets.QLabel(""))
		header_row.addStretch(1)
		self.cores_layout.addLayout(header_row)
		# CPU Target + core rows
		cpu_target_row = QtWidgets.QHBoxLayout()
		cpu_target_cb = QtWidgets.QCheckBox("CPU Target")
		cpu_target_cb.stateChanged.connect(self._on_cpu_target_toggled)
		cpu_target_row.addWidget(cpu_target_cb)
		self.cpu_target_checkbox = cpu_target_cb
		cpu_target_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
		cpu_target_slider.setRange(0, 100)
		cpu_target_slider.setValue(self.states[Subsystem.CPU].target_percent)
		cpu_target_slider.setMinimumWidth(120)
		cpu_target_slider.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
		cpu_target_slider.valueChanged.connect(lambda val: self._on_cpu_target_changed(val))
		cpu_target_row.addWidget(cpu_target_slider)
		self.cpu_target_slider = cpu_target_slider
		cpu_target_slider.setVisible(False)
		cpu_target_text = QtWidgets.QLineEdit(str(self.states[Subsystem.CPU].target_percent))
		cpu_target_text.setValidator(QtGui.QIntValidator(0, 100, self))
		cpu_target_text.setFixedWidth(40)
		cpu_target_text.setAlignment(QtCore.Qt.AlignCenter)
		cpu_target_slider.valueChanged.connect(lambda val, field=cpu_target_text: field.setText(str(int(val))))
		cpu_target_text.editingFinished.connect(lambda: self._on_cpu_target_changed(int(cpu_target_text.text() or 0)))
		cpu_target_row.addWidget(cpu_target_text)
		self.cpu_target_text = cpu_target_text
		cpu_target_row.addStretch(1)
		self.cores_layout.addLayout(cpu_target_row)
		# Create rows per core
		core_count = getattr(self, 'core_count', 0)
		print(f"[DEBUG] Creating UI for {core_count} cores")
		for core_id in range(core_count):
			row_container = QtWidgets.QWidget()
			core_row = QtWidgets.QHBoxLayout(row_container)
			core_row.setContentsMargins(0, 0, 0, 0)
			core_row.setSpacing(6)
			cb = QtWidgets.QCheckBox(f"Core {core_id}")
			cb.stateChanged.connect(self._on_core_toggled)
			core_row.addWidget(cb)
			self.core_checkboxes[core_id] = cb
			slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
			slider.setRange(0, 100)
			slider.setValue(self.core_states.get(core_id, CoreState(core_id)).target_percent)
			slider.setMinimumWidth(120)
			slider.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
			slider.valueChanged.connect(lambda val, cid=core_id: self._on_core_target_changed(cid, val))
			core_row.addWidget(slider)
			self.core_sliders[core_id] = slider
			slider.setVisible(False)
			txt = QtWidgets.QLineEdit(str(self.core_states.get(core_id, CoreState(core_id)).target_percent))
			txt.setValidator(QtGui.QIntValidator(0, 100, self))
			txt.setFixedWidth(40)
			txt.setAlignment(QtCore.Qt.AlignCenter)
			slider.valueChanged.connect(lambda val, field=txt: field.setText(str(int(val))))
			txt.editingFinished.connect(lambda cid=core_id, field_ref=txt: self._on_core_target_changed(cid, int(field_ref.text() or 0)))
			core_row.addWidget(txt)
			self.core_texts[core_id] = txt
			core_row.addStretch(1)
			self.cores_layout.addWidget(row_container)
			self.core_row_widgets[core_id] = row_container

	def _clear_layout(self, layout: QtWidgets.QLayout) -> None:
		"""Recursively remove all items (widgets and sub-layouts) from a layout."""
		try:
			while layout.count():
				item = layout.takeAt(0)
				if item is None:
					continue
				child_widget = item.widget()
				child_layout = item.layout()
				if child_widget is not None:
					child_widget.deleteLater()
				elif child_layout is not None:
					self._clear_layout(child_layout)
		except Exception:
			pass

	def _auto_load_binary_over_uart(self) -> None:
		"""Find Linux UART by VID:PID, open the console, and send commands visibly."""
		# Locate the Linux UART using the same VID:PID logic as elsewhere
		linux_port = None
		try:
			linux_port = self.comm_console.find_linux_port("VID:PID=067B:23A3")
		except Exception:
			linux_port = None
		if not linux_port:
			self._show_error_dialog("Linux UART Not Found", "Couldn't locate a COM port with VID:PID=067B:23A3.")
			return
		# Show the console UI and ensure UART protocol is selected
		try:
			self.btn_uart_toggle.setChecked(True)
			self.main_stack.setCurrentIndex(1)
			if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'proto_combo'):
				self.comm_console.proto_combo.setCurrentIndex(0)  # UART
				self.comm_console._on_proto_changed()
		except Exception:
			pass
		# If connected to a different port, disconnect first
		try:
			current_connected = bool(self.comm_console.uart_connect_btn.isChecked())
			different_port = (getattr(self.comm_console, '_current_port', '') or '') != linux_port
			if current_connected and different_port:
				self.comm_console._uart_disconnect_if_needed()
		except Exception:
			pass
		# Connect to the Linux UART at 921600
		connected = False
		try:
			if not self.comm_console.uart_connect_btn.isChecked() or (getattr(self.comm_console, '_current_port', '') or '') != linux_port:
				connected = self.comm_console.connect_to_port(linux_port, baud=921600)
			else:
				connected = True
		except Exception:
			connected = False
		if not connected:
			QtWidgets.QMessageBox.critical(self, "UART Connect Failed", f"Failed to open {linux_port} at 921600.")
			return
		# Send the setup/copy commands through the console so they are visible in UI
		cmds = [
			"sudo su",
			"nvidia",
			"cd /",
			"mkdir -p /mnt/usb",
			"mkdir -p /stress_tools",
			"DEV=\"$(lsblk -rpno NAME,TYPE,TRAN | awk '$2==\"disk\" && $3==\"usb\"{print $1; exit}')\"",
			"if [ -z \"$DEV\" ]; then DEV=\"$(lsblk -rpno NAME,TYPE,RM | awk '$2==\"disk\" && $3==\"1\"{print $1; exit}')\"; fi",
			"PART=\"$(lsblk -rpno NAME,TYPE \"$DEV\" | awk '$2==\"part\"{print $1; exit}')\"",
			"if [ -z \"$PART\" ]; then PART=\"$(lsblk -rpno NAME,TYPE | awk '$2==\"part\"{print $1; exit}')\"; fi",
			"mount \"$PART\" /mnt/usb || (sleep 1; mount \"$PART\" /mnt/usb)",
			"cp /mnt/usb/stress_tool /stress_tools/",
			"umount /mnt/usb",
			"cd /",
		]
		def _on_done() -> None:
			self._show_info_dialog("Binary Loaded", f"Binary loaded successfully over {linux_port}.")
		try:
			self.comm_console.send_commands(cmds, spacing_ms=1000, on_complete=_on_done)
		except Exception:
			# Fall back to showing success dialog even if logging fails
			self._show_info_dialog("Binary Loaded", f"Binary loaded successfully over {linux_port}.")

	def _msgbox_style(self, accent_hex: str) -> str:
		"""Return a stylesheet for QColor-accented message boxes respecting theme."""
		is_dark = getattr(self, '_is_dark', True)
		bg = '#14171C' if is_dark else '#FFFFFF'
		border = '#2A2F36' if is_dark else '#E5E7EB'
		text = '#E8ECEF' if is_dark else '#0B1220'
		btn_bg = '#1D2228' if is_dark else '#FFFFFF'
		btn_border = '#2D333B' if is_dark else '#E5E7EB'
		btn_hover = '#242A32' if is_dark else '#F3F4F6'
		btn_text = '#E8ECEF' if is_dark else '#0F172A'
		return (
			f"QMessageBox {{ background: {bg}; border: 1px solid {border}; border-left: 6px solid {accent_hex}; }}"
			f"QMessageBox QLabel {{ color: {text}; }}"
			f"QMessageBox QPushButton {{ background: {btn_bg}; border: 1px solid {btn_border}; border-radius: 6px; padding: 6px 12px; color: {btn_text}; }}"
			f"QMessageBox QPushButton:hover {{ background: {btn_hover}; }}"
		)

	def _show_info_dialog(self, title: str, text: str) -> None:
		msg = QtWidgets.QMessageBox(self)
		msg.setIcon(QtWidgets.QMessageBox.Information)
		msg.setWindowTitle(title)
		msg.setText(text)
		msg.setStyleSheet(self._msgbox_style('#10B981'))  # emerald
		msg.exec()

	def _show_warning_dialog(self, title: str, text: str) -> None:
		msg = QtWidgets.QMessageBox(self)
		msg.setIcon(QtWidgets.QMessageBox.Warning)
		msg.setWindowTitle(title)
		msg.setText(text)
		msg.setStyleSheet(self._msgbox_style('#F59E0B'))  # amber
		msg.exec()

	def _show_error_dialog(self, title: str, text: str) -> None:
		msg = QtWidgets.QMessageBox(self)
		msg.setIcon(QtWidgets.QMessageBox.Critical)
		msg.setWindowTitle(title)
		msg.setText(text)
		msg.setStyleSheet(self._msgbox_style('#EF4444'))  # red
		msg.exec()
	def _apply_dark_theme(self) -> None:
		"""Apply a subtle dark theme and modern widget styling."""
		pal = self.palette()
		pal.setColor(QtGui.QPalette.Window, QtGui.QColor(15, 17, 20))
		pal.setColor(QtGui.QPalette.Base, QtGui.QColor(20, 23, 28))
		pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(23, 26, 31))
		pal.setColor(QtGui.QPalette.Text, QtGui.QColor(232, 236, 239))
		pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(237, 242, 247))
		pal.setColor(QtGui.QPalette.Button, QtGui.QColor(28, 32, 38))
		pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(232, 236, 239))
		pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(59, 130, 246))
		pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
		self.setPalette(pal)
		self.setStyleSheet(
			"""
			QWidget { font-size: 12px; color: #E8ECEF; }
			QScrollArea { background: #0F1114; border: none; }
			#controlsContainer { background: #0F1114; }
			QCheckBox { spacing: 8px; }
			QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3A404A; background: #1C2127; }
			QCheckBox::indicator:hover { border-color: #3B82F6; }
			QCheckBox::indicator:checked { background: #3B82F6; border-color: #3B82F6; }
			QMenu, QAbstractItemView { background: #14171C; color: #E8ECEF; border: 1px solid #2A2F36; }
			QComboBox QAbstractItemView { background: #14171C; selection-background-color: #1E2530; selection-color: #E8ECEF; }
			QGraphicsView { background: #101114; }
			QGroupBox { border: 1px solid #2A2F36; border-radius: 8px; margin-top: 8px; }
			QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #A0AEC0; }
			QPushButton, QToolButton { background: #1D2228; border: 1px solid #2D333B; border-radius: 6px; padding: 6px 10px; }
			QPushButton:hover, QToolButton:hover { background: #242A32; }
			/* Semantic toolbar buttons (subtle tones) */
			#btn_execute { background: #1A2A1F; border-color: #24402B; color: #D7F5E1; }
			#btn_execute:hover { background: #20402A; }
			#btn_load { background: #1A2A1F; border-color: #24402B; color: #D7F5E1; }
			#btn_load:hover { background: #20402A; }
			#btn_stop { background: #2A1A1A; border-color: #402424; color: #F5D7D7; }
			#btn_stop:hover { background: #401F1F; }
			QPushButton:disabled { color: #7D8896; }
			QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
				background: #1E2329; border: 1px solid #323842; border-radius: 6px; padding: 6px 8px; color: #E8ECEF;
			}
			QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
				border-color: #3B82F6;
			}
			/* Message boxes - make text and buttons clearly visible */
			QMessageBox { background: #14171C; border: 1px solid #2A2F36; }
			QMessageBox QLabel { color: #E8ECEF; }
			QMessageBox QPushButton { background: #1D2228; border: 1px solid #2D333B; border-radius: 6px; padding: 6px 12px; color: #E8ECEF; }
			QMessageBox QPushButton:hover { background: #242A32; }
			#userGroup { border: 1px solid #2A2F36; }
			#commandPreview { background: #14171C; border-radius: 6px; }
			QSlider::groove:horizontal { height: 6px; background: #2A2F36; border-radius: 3px; }
			QSlider::handle:horizontal { width: 14px; background: #3B82F6; margin: -5px 0; border-radius: 7px; }
			QListWidget { background: #161A1F; border: 1px solid #2A2F36; border-radius: 6px; }
			QToolBar { background: #0F1114; border: 0; spacing: 8px; }
			#panelHandle { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B2026, stop:1 #13171B); border: 1px solid #2D333B; border-radius: 18px; color: #EAF2FF; font-weight: 700; }
			#panelHandle:hover { border-color: #3B82F6; }
			#panelHandle:pressed { background: #1D2228; }
			#kpiButton { background: #14171C; border: 1px solid #2A2F36; border-radius: 12px; padding: 6px 12px; color: #A0AEC0; }
			#kpiButton:hover { border-color: #3B82F6; color: #E8F0FF; }
			"""
		)
		self._is_dark = True
		if hasattr(self, 'btn_theme') and self.btn_theme is not None:
			self.btn_theme.setText("Light Mode")
		self._apply_plot_theme(True)

	def _apply_light_theme(self) -> None:
		"""Apply a bright light theme alternative."""
		pal = self.palette()
		pal.setColor(QtGui.QPalette.Window, QtGui.QColor(248, 250, 255))
		pal.setColor(QtGui.QPalette.Base, QtGui.QColor(255, 255, 255))
		pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor(248, 250, 252))
		pal.setColor(QtGui.QPalette.Text, QtGui.QColor(15, 23, 42))
		pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor(15, 23, 42))
		pal.setColor(QtGui.QPalette.Button, QtGui.QColor(255, 255, 255))
		pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor(15, 23, 42))
		pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor(14, 165, 233))
		pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor(255, 255, 255))
		self.setPalette(pal)
		self.setStyleSheet(
			"""
			QWidget { font-size: 11px; color: #0B1220; }
			QScrollArea { background: #F8FAFF; border: none; }
			#controlsContainer { background: #F8FAFF; }
			QCheckBox { spacing: 8px; }
			QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; border: 1px solid #CBD5E1; background: #FFFFFF; }
			QCheckBox::indicator:hover { border-color: #0EA5E9; }
			QCheckBox::indicator:checked { background: #0EA5E9; border-color: #0EA5E9; }
			QMenu, QAbstractItemView { background: #FFFFFF; color: #0F172A; border: 1px solid #E2E8F0; }
			QComboBox QAbstractItemView { background: #FFFFFF; selection-background-color: #E2E8F0; selection-color: #0F172A; }
			QGraphicsView { background: #FFFFFF; }
			QGroupBox { border: 1px solid #E5E7EB; border-radius: 10px; margin-top: 6px; background: #FFFFFF; }
			QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 1px 8px; color: #374151; }
			QPushButton, QToolButton { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 8px; padding: 6px 10px; }
			QPushButton:hover, QToolButton:hover { background: #F3F4F6; border-color: #D1D5DB; }
			/* Semantic toolbar buttons (subtle tones) */
			#btn_execute { background: #EBF7EE; border-color: #BFE3C8; color: #14532D; }
			#btn_execute:hover { background: #DDF0E3; }
			#btn_load { background: #EBF7EE; border-color: #BFE3C8; color: #14532D; }
			#btn_load:hover { background: #DDF0E3; }
			#btn_stop { background: #FBEAEA; border-color: #F0C2C2; color: #7F1D1D; }
			#btn_stop:hover { background: #F6DDDD; }
			QPushButton:disabled { color: #94A3B8; }
			QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
				background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 8px; padding: 5px 8px; color: #0F172A;
			}
			QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
				border-color: #3B82F6;
			}
			/* Message boxes - make text and buttons clearly visible */
			QMessageBox { background: #FFFFFF; border: 1px solid #E5E7EB; }
			QMessageBox QLabel { color: #0B1220; }
			QMessageBox QPushButton { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 8px; padding: 6px 12px; color: #0F172A; }
			QMessageBox QPushButton:hover { background: #F3F4F6; border-color: #D1D5DB; }
			#userGroup { border: 1px solid #E5E7EB; }
			#commandPreview { background: #FFFFFF; border-radius: 8px; }
			QSlider::groove:horizontal { height: 6px; background: #E5E7EB; border-radius: 3px; }
			QSlider::handle:horizontal { width: 14px; background: #3B82F6; margin: -5px 0; border-radius: 7px; }
			QListWidget { background: #FFFFFF; border: 1px solid #E5E7EB; border-radius: 8px; }
			QToolBar { background: #FFFFFF; border: 0; spacing: 8px; border-bottom: 1px solid #E5E7EB; }
			#panelHandle { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #FFFFFF, stop:1 #F3F4F6); border: 1px solid #E5E7EB; border-radius: 18px; color: #0F172A; font-weight: 700; }
			#panelHandle:hover { border-color: #3B82F6; }
			#panelHandle:pressed { background: #FFFFFF; }
			#kpiButton { background: #F8FAFF; border: 1px solid #E5E7EB; border-radius: 12px; padding: 6px 12px; color: #6B7280; }
			#kpiButton:hover { border-color: #3B82F6; color: #0F172A; }
			/* Extra polish for Light Mode */
			QMainWindow, #rootCentral { background: #F8FAFF; }
			QScrollArea > QWidget > QWidget { background: #F8FAFF; }
			QAbstractItemView::item:selected { background: #DBEAFE; color: #0B1220; }
			QListWidget::item:selected { background: #DBEAFE; color: #0B1220; }
			QScrollBar:vertical, QScrollBar:horizontal { background: #F3F4F6; }
			"""
		)
		self._is_dark = False
		if hasattr(self, 'btn_theme') and self.btn_theme is not None:
			self.btn_theme.setText("Dark Mode")
		self._apply_plot_theme(False)

	def _on_toggle_uart(self, checked: bool) -> None:
		"""Switch between graph and UART views."""
		if hasattr(self, 'main_stack'):
			self.main_stack.setCurrentIndex(1 if checked else 0)
		if hasattr(self, 'btn_uart_toggle'):
			self.btn_uart_toggle.setText("Access Graph" if checked else "Access UART")
		# Refresh COM ports when entering console view
		if checked and hasattr(self, 'comm_console'):
			self.comm_console.refresh_ports()

	def _toggle_theme(self) -> None:
		if getattr(self, '_is_dark', True):
			self._apply_light_theme()
		else:
			self._apply_dark_theme()

	def _toggle_controls(self) -> None:
		"""Smoothly collapse/expand the left controls panel."""
		if not hasattr(self, 'controls_scroll'):
			return
		self.controls_collapsed = not getattr(self, 'controls_collapsed', False)
		start = self.controls_scroll.width()
		end = 0 if self.controls_collapsed else self.controls_initial_width
		anim = QtCore.QPropertyAnimation(self.controls_scroll, b"maximumWidth", self)
		anim.setDuration(220)
		anim.setStartValue(start)
		anim.setEndValue(end)
		anim.setEasingCurve(QtCore.QEasingCurve.InOutCubic)
		anim.start()
		self._controls_anim = anim
		# Update handle arrow
		if hasattr(self, 'panel_handle') and self.panel_handle:
			self.panel_handle.setArrowType(QtCore.Qt.RightArrow if self.controls_collapsed else QtCore.Qt.LeftArrow)

	def _apply_plot_theme(self, is_dark: bool) -> None:
		"""Adjust plot background and axis pens to match theme."""
		if not hasattr(self, 'plot_widget'):
			return
		if is_dark:
			self.plot_widget.setBackground(QtGui.QColor(16, 17, 20))
			axis_pen = pg.mkPen(color=(220, 224, 228))
		else:
			self.plot_widget.setBackground('w')
			axis_pen = pg.mkPen(color=(60, 60, 60))
		for axis in ['left', 'bottom']:
			ax = self.plot_widget.getAxis(axis)
			ax.setPen(axis_pen)
			ax.setTextPen(axis_pen)

	def _create_kpi_label(self, text: str) -> QtWidgets.QToolButton:
		btn = QtWidgets.QToolButton()
		btn.setObjectName("kpiButton")
		btn.setText(text + ": --%")
		btn.setAutoRaise(True)
		btn.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
		btn.clicked.connect(lambda _, name=text: self._select_graph(name))
		return btn

	# UART/console UI is provided by CommConsole

	# All UART logic moved into CommConsole

	def _select_graph(self, name: str) -> None:
		index = self.combo_active.findText(name)
		if index >= 0:
			self.combo_active.setCurrentIndex(index)
			self._on_active_changed(name)

	def _on_view_range_changed(self) -> None:
		"""Dynamically adjust time axis tick spacing based on zoom level."""
		viewbox = self.plot_widget.getViewBox()
		view_range = viewbox.viewRange()
		x_range = view_range[0]  # (x_min, x_max)
		time_span = x_range[1] - x_range[0]
		
		# Only apply custom spacing if we have a reasonable time span
		if time_span < 1:
			return  # Don't format if time span is too small
		
		# Determine tick spacing based on time span with nice rounding
		# Aim for ~6-8 major ticks across the current view
		ideal_major = max(1.0, time_span / 7.0)
		major_tick = _nice_tick_seconds(ideal_major)
		minor_tick = max(1.0, major_tick / 5.0)
		
		# Apply the new tick spacing
		self.plot_widget.getAxis('bottom').setTickSpacing(major_tick, minor_tick)

	def _setup_time_axis_formatting(self, time_span: float) -> None:
		"""Setup time axis formatting to show seconds and minutes format."""
		# For now, let PyQtGraph handle the default formatting
		# The custom tick spacing will provide the main benefit
		pass

	def _on_reset_graph(self) -> None:
		"""Reset graph zoom and pan to show full data range."""
		# Get all data points to determine the range
		all_x_values = []
		all_y_values = []
		
		# Collect data from all subsystems
		for state in self.states.values():
			if state.values:
				for ts, val in state.values:
					all_x_values.append(ts)
					all_y_values.append(val)
		
		# Collect data from all CPU cores
		for core_state in self.core_states.values():
			if core_state.values:
				for ts, val in core_state.values:
					all_x_values.append(ts)
					all_y_values.append(val)
		
		if not all_x_values:
			# No data yet, reset to default view
			self.plot_widget.setXRange(0, 60)
			self.plot_widget.setYRange(0, 100)
			return
		
		# Calculate data range
		min_x = min(all_x_values)
		max_x = max(all_x_values)
		min_y = min(all_y_values)
		max_y = max(all_y_values)
		
		# Convert to relative time (seconds from start)
		if min_x > 0:
			# Data exists, show from 0 to max relative time
			max_relative_time = max_x - min_x
			self.plot_widget.setXRange(0, max_relative_time + 10)  # Add 10 seconds padding
		else:
			# No data yet, show default range
			self.plot_widget.setXRange(0, 60)
		
		# Set Y range with some padding
		y_padding = (max_y - min_y) * 0.1 if max_y > min_y else 10
		self.plot_widget.setYRange(max(0, min_y - y_padding), min(100, max_y + y_padding))
		
		# Reset the view to auto-range for better display
		self.plot_widget.enableAutoRange(axis='xy')

	def _make_label(self, text: str, bold: bool = False) -> QtWidgets.QLabel:
		lbl = QtWidgets.QLabel(text)
		if bold:
			font = lbl.font()
			font.setBold(True)
			lbl.setFont(font)
		return lbl

	def _get_core_pen(self, core_id: int) -> pg.mkPen:
		# Use distinct color per core via color wheel
		color = pg.intColor(core_id, hues=len(self.core_states) or 8, maxValue=200)
		return pg.mkPen(color=color, width=2)

	def _on_subsystem_toggled(self) -> None:
		self.active_subsystems = [name for name, cb in self.checkbox_group.items() if cb.isChecked()]
		
		# Enable/disable adaptive checkboxes based on subsystem selection
		if hasattr(self, 'adaptive_checkbox_group'):
			for name, cb in self.checkbox_group.items():
				adaptive_cb = self.adaptive_checkbox_group.get(name)
				if adaptive_cb:
					# Enable adaptive checkbox only if corresponding subsystem is selected
					adaptive_cb.setEnabled(cb.isChecked())
					# If subsystem is deselected, also uncheck the adaptive checkbox
					if not cb.isChecked() and adaptive_cb.isChecked():
						adaptive_cb.setChecked(False)
		
		# Show/hide CPU cores section when CPU is selected
		cpu_selected = self.checkbox_group[Subsystem.CPU].isChecked()
		self.cpu_cores_group.setVisible(cpu_selected)
		
		# If CPU is deselected, clear active cores and hide sliders
		if not cpu_selected:
			for cb in self.core_checkboxes.values():
				cb.setChecked(False)
			self.active_cores.clear()
			for slider in self.core_sliders.values():
				slider.setVisible(False)
			for label in self.core_labels.values():
				label.setVisible(False)
		
		self._rebuild_sliders()
		self._rebuild_active_combo()
		self._refresh_numeric_list()
		self._refresh_plot_items()
		self._update_command_preview()

	def _on_adaptive_toggled(self) -> None:
		"""Handle adaptive checkbox toggles for subsystems."""
		# Get the current adaptive subsystems (for future functionality)
		adaptive_subsystems = [name for name, cb in self.adaptive_checkbox_group.items() if cb.isChecked()]
		# Update command preview to reflect adaptive changes
		self._update_command_preview()

	def _on_cpu_target_toggled(self) -> None:
		"""Handle CPU Target checkbox toggle."""
		cpu_target_selected = self.cpu_target_checkbox.isChecked()
		self.cpu_target_slider.setVisible(cpu_target_selected)
		self.cpu_target_text.setVisible(cpu_target_selected)
		
		# Update active subsystems
		if cpu_target_selected:
			if Subsystem.CPU not in self.active_subsystems:
				self.active_subsystems.append(Subsystem.CPU)
			# Enable all CPU cores when CPU Target is selected
			self._setting_all_cores = True  # Flag to prevent core toggle from unchecking CPU Target
			for core_id, cb in self.core_checkboxes.items():
				cb.setChecked(True)
			self.active_cores = list(self.core_checkboxes.keys())
			# Show sliders for all cores
			for slider in self.core_sliders.values():
				slider.setVisible(True)
			delattr(self, '_setting_all_cores')  # Remove the flag
		else:
			if Subsystem.CPU in self.active_subsystems:
				self.active_subsystems.remove(Subsystem.CPU)
			# Don't automatically disable cores when CPU Target is deselected
			# Let the user manually manage individual cores
			# Just hide the sliders for cores that aren't individually selected
			for core_id, slider in self.core_sliders.items():
				slider.setVisible(core_id in self.active_cores)
		
		self._rebuild_active_combo()
		self._refresh_plot_items()
		self._update_command_preview()

	def _on_core_toggled(self) -> None:
		self.active_cores = [core_id for core_id, cb in self.core_checkboxes.items() if cb.isChecked()]
		
		# If CPU Target is checked and any core is unchecked, uncheck CPU Target
		# But only if we're not in the middle of setting all cores (which happens when CPU Target is toggled)
		if (hasattr(self, 'cpu_target_checkbox') and 
			self.cpu_target_checkbox.isChecked() and 
			len(self.active_cores) < len(self.core_checkboxes) and
			not hasattr(self, '_setting_all_cores')):
			self.cpu_target_checkbox.setChecked(False)
			# CPU Target unchecking will be handled by _on_cpu_target_toggled
		
		# Update slider visibility
		for core_id, slider in self.core_sliders.items():
			slider.setVisible(core_id in self.active_cores)
		for core_id, label in self.core_labels.items():
			label.setVisible(core_id in self.active_cores)
		self._rebuild_active_combo()
		self._refresh_numeric_list()
		self._refresh_plot_items()
		self._update_command_preview()

	def _rebuild_sliders(self) -> None:
		while self.slider_form.rowCount() > 0:
			self.slider_form.removeRow(0)
		
		# Add sliders for regular subsystems (excluding CPU)
		for name in self.active_subsystems:
			if name == Subsystem.CPU:
				continue  # Skip CPU, cores have their own sliders
			slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
			slider.setRange(0, 100)
			slider.setValue(self.states[name].target_percent)
			slider.valueChanged.connect(lambda val, n=name: self._on_target_changed(n, val))
			# Composite row widget with slider and editable percent to the right
			row_widget = QtWidgets.QWidget()
			row_layout = QtWidgets.QHBoxLayout(row_widget)
			row_layout.setContentsMargins(0, 0, 0, 0)
			row_layout.addWidget(slider, 1)
			txt = QtWidgets.QLineEdit(str(self.states[name].target_percent))
			txt.setValidator(QtGui.QIntValidator(0, 100, self))
			txt.setFixedWidth(40)
			slider.valueChanged.connect(lambda val, field=txt: field.setText(str(int(val))))
			def _apply_txt_sys(sys_name=name, field_ref=None):
				try:
					val_int = int((field_ref or txt).text() or 0)
					val_int = max(0, min(100, val_int))
					if slider.value() != val_int:
						slider.setValue(val_int)
				except Exception:
					pass
			txt.editingFinished.connect(lambda sys_name=name, field_ref=txt: _apply_txt_sys(sys_name, field_ref))
			row_layout.addWidget(txt)
			self.slider_form.addRow(QtWidgets.QLabel(f"{name} Target:"), row_widget)

	def _rebuild_active_combo(self) -> None:
		current = self.combo_active.currentText()
		self.combo_active.blockSignals(True)
		self.combo_active.clear()
		
		# Always show all subsystems regardless of checkbox selection
		for name in SUBSYSTEMS:
			self.combo_active.addItem(name)
		
		# Always show CPU cores option
		self.combo_active.addItem("CPU (cores)")
		
		# Always show individual CPU cores
		for core_id in range(getattr(self, 'core_count', 0)):
			self.combo_active.addItem(f"Core {core_id}")
		
		self.combo_active.blockSignals(False)
		if current in [self.combo_active.itemText(i) for i in range(self.combo_active.count())]:
			self.combo_active.setCurrentText(current)
		elif self.combo_active.count() > 0:
			self.combo_active.setCurrentIndex(0)
		self._refresh_plot_items()
		self._update_command_preview()

	def _on_target_changed(self, name: str, value: int) -> None:
		self.states[name].target_percent = int(value)
		if self.combo_active.currentText() == name and self.states[name].target_line is not None:
			self.states[name].target_line.setValue(value)
		self._update_numeric_colors()
		self._update_command_preview()

	def _on_cpu_target_changed(self, value: int) -> None:
		"""Handle CPU target slider changes."""
		self.states[Subsystem.CPU].target_percent = int(value)
		
		# When CPU Target is selected, update all core targets to match
		if (hasattr(self, 'cpu_target_checkbox') and 
			self.cpu_target_checkbox.isChecked()):
			for core_id in self.core_states:
				self.core_states[core_id].target_percent = int(value)
				# Update core sliders if they exist
				if core_id in self.core_sliders:
					self.core_sliders[core_id].setValue(int(value))
				# Update core text fields if they exist
				if core_id in self.core_texts:
					self.core_texts[core_id].setText(str(int(value)))
		
		if self.combo_active.currentText() == Subsystem.CPU and self.states[Subsystem.CPU].target_line is not None:
			self.states[Subsystem.CPU].target_line.setValue(value)
		self._update_numeric_colors()
		self._update_command_preview()

	def _on_core_target_changed(self, core_id: int, value: int) -> None:
		self.core_states[core_id].target_percent = int(value)
		active = self.combo_active.currentText()
		if (active == "CPU (cores)" or active == f"Core {core_id}") and self.core_states[core_id].target_line is not None:
			self.core_states[core_id].target_line.setValue(value)
		self._update_numeric_colors()
		self._update_command_preview()

	def _refresh_plot_items(self) -> None:
		self.plot_widget.clear()
		active = self.combo_active.currentText()
		if not active:
			return
		
		if active == "CPU (cores)":
			# Plot all CPU cores with distinct colors and own target lines
			for core_id in range(getattr(self, 'core_count', 0)):
				state = self.core_states[core_id]
				state.curve = self.plot_widget.plot([], [], pen=self._get_core_pen(core_id))
				# Only show target line if this core is active (has target set)
				if core_id in self.active_cores:
					line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color=self._get_core_pen(core_id).color(), width=1, style=QtCore.Qt.DotLine))
					line.setValue(state.target_percent)
					self.plot_widget.addItem(line)
					state.target_line = line
			return
		
		# Single item view - show all subsystems but only targets for active ones
		if active.startswith("Core "):
			core_id = int(active.split()[1])
			state = self.core_states[core_id]
		else:
			state = self.states[active]
		
		curve = self.plot_widget.plot([], [], pen=pg.mkPen(color=(0, 122, 204), width=2))
		state.curve = curve
		
		# Only show target line if this subsystem is active (has target set)
		if (active in self.active_subsystems or 
			(active.startswith("Core ") and int(active.split()[1]) in self.active_cores)):
			line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color=(200, 0, 0), width=2, style=QtCore.Qt.DotLine))
			line.setValue(state.target_percent)
			self.plot_widget.addItem(line)
			state.target_line = line

	def _on_active_changed(self, _: str) -> None:
		self._refresh_plot_items()
		self._redraw_curve()
		self._update_export_enabled()

	def _on_start(self) -> None:
		# Prepare fresh state
		for name in self.states:
			self.states[name].values.clear()
		for core_id in self.core_states:
			self.core_states[core_id].values.clear()
		# Track running state per OS
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		if not hasattr(self, 'os_running_states'):
			self.os_running_states = {"Yocto": False, "Ubuntu": False, "AAOS": False}
		
		self.os_running_states[os_sel] = True
		self.is_running = True
		duration_s = int(self.duration_spin.value())
		self.end_time_epoch = get_timestamp() + duration_s
		# Ensure command preview reflects current selections and reset live log buffer
		self._update_command_preview()
		self._raw_log_buffer = ""
		# Also clear any pending local tail state so Show Log starts fresh
		self._file_tail_path = None
		self._file_tail_pos = 0
		self._file_tail_rem = b""
		cmd_line = self.command_preview.toPlainText().strip()
		# If AAOS binary, execute via ADB instead of UART
		if cmd_line.startswith("./android_stress_tool"):
			# Find first device serial (optional)
			serial = None
			try:
				devs = _adb_list_devices()
				if devs:
					serial = devs[0][0]
			except Exception:
				serial = None
			self._execute_test_via_adb(cmd_line, serial)
			# Stream device status log into the app from /tmp/android_stress_tool/stress_tool_status.txt
			self._start_adb_tail(serial)
			# Auto-open Show Log after a short delay so it doesn't steal focus
			QtCore.QTimer.singleShot(200, self._open_log_dialog)
			# Start schedule timer if there are scheduled changes
			self._start_schedule_timer()
			# Disable inputs while running (both action and toolbar button)
			# Disable/Enable buttons immediately, but also re-assert shortly after
			self.btn_start.setEnabled(False)
			self.btn_stop.setEnabled(True)
			try:
				if hasattr(self, 'action_execute'):
					self.action_execute.setEnabled(False)
				if hasattr(self, 'action_stop'):
					self.action_stop.setEnabled(True)
			except Exception:
				pass
			# Some styles reflow the toolbar; ensure enabled state sticks
			def _reassert_enabled():
				try:
					self.btn_start.setEnabled(False)
					self.btn_stop.setEnabled(True)
					if hasattr(self, 'action_execute'):
						self.action_execute.setEnabled(False)
					if hasattr(self, 'action_stop'):
						self.action_stop.setEnabled(True)
				except Exception:
					pass
			QtCore.QTimer.singleShot(100, _reassert_enabled)
			self.duration_spin.setEnabled(False)
			return
		# Otherwise proceed with UART (Linux)
		linux_port = None
		try:
			linux_port = self.comm_console.find_linux_port("VID:PID=067B:23A3")
		except Exception:
			linux_port = None
		if not linux_port:
			QtWidgets.QMessageBox.critical(self, "Linux UART Not Found", "Couldn't locate a COM port with VID:PID=067B:23A3.")
			self._on_stop()
			return
		try:
			current_connected = bool(self.comm_console.uart_connect_btn.isChecked())
			different_port = (getattr(self.comm_console, '_current_port', '') or '') != linux_port
			if current_connected and different_port:
				self.comm_console._uart_disconnect_if_needed()
		except Exception:
			pass
		connected = False
		try:
			if not self.comm_console.uart_connect_btn.isChecked() or (getattr(self.comm_console, '_current_port', '') or '') != linux_port:
				connected = self.comm_console.connect_to_port(linux_port, baud=921600)
			else:
				connected = True
		except Exception:
			connected = False
		if not connected:
			QtWidgets.QMessageBox.critical(self, "UART Connect Failed", f"Failed to open {linux_port} at 921600.")
			self._on_stop()
			return
		# For Yocto/Ubuntu, add header message first before switching protocols
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		if os_sel in ("Yocto", "Ubuntu"):
			# Add header message without clearing UART console history
			if hasattr(self.comm_console, 'log'):
				self.comm_console.log.appendPlainText(f"\n=== Starting {os_sel} Test ===")
				self.comm_console.log.appendPlainText(f"Command: {cmd_line}")
				self.comm_console.log.appendPlainText("=" * 50)
				# Ensure cursor is at the end for smooth scrolling
				self.comm_console.log.moveCursor(QtGui.QTextCursor.End)
		
		try:
			self.btn_uart_toggle.setChecked(True)
			self.main_stack.setCurrentIndex(1)
			# Switch to UART protocol to show the commands
			if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'proto_combo'):
				self.comm_console.proto_combo.setCurrentIndex(0)  # UART
				self.comm_console._on_proto_changed()
		except Exception:
			pass
		if not cmd_line:
			QtWidgets.QMessageBox.warning(self, "No Command", "Generated command is empty.")
		else:
			# For Yocto/Ubuntu, show commands in UART console
			if os_sel in ("Yocto", "Ubuntu"):
				
				self.comm_console.send_commands([
					"cd /",
					"cd stress_tools", 
					cmd_line,
				], spacing_ms=400)
			else:
				self.comm_console.send_commands([
					"cd /",
					"cd stress_tools",
					cmd_line,
				], spacing_ms=400)
		# _sample_timer removed - using external backend
		# Only check for log file path for non-Yocto/Ubuntu OS (AAOS uses ADB, others use local file)
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		if os_sel not in ("Yocto", "Ubuntu"):
			tail_path = self.log_file_edit.text().strip()
			if not tail_path:
				QtWidgets.QMessageBox.warning(self, "No Log File", "Please specify the log file path.")
				return
			self._start_tail_file(tail_path)
		self._start_schedule_timer()
		self.btn_start.setEnabled(False)
		self.btn_stop.setEnabled(True)
		try:
			if hasattr(self, 'action_execute'):
				self.action_execute.setEnabled(False)
			if hasattr(self, 'action_stop'):
				self.action_stop.setEnabled(True)
		except Exception:
			pass
		self.duration_spin.setEnabled(False)
		# Command input removed

	def _execute_test_via_adb(self, cmd_line: str, serial: Optional[str] = None) -> None:
		"""Execute the generated AAOS command by injecting it into the first CMD terminal.

		This sends one adb shell command to the CMD tab so the user can see it:
		  adb [-s <serial>] shell "cd /tmp/android_stress_tool && chmod +x android_stress_tool && <generated>"
		"""
		try:
			# Determine first connected device (optional) if not provided
			if serial is None:
				try:
					devs = _adb_list_devices()
					if devs:
						serial = devs[0][0]
				except Exception:
					serial = None
			serial_arg = f" -s {serial}" if serial else ""
			adb_cmd = (
				f"adb{serial_arg} shell \"cd /tmp/android_stress_tool && chmod +x android_stress_tool && {cmd_line}\""
			)
			# Show console and switch to CMD protocol so the command is visible
			try:
				self.btn_uart_toggle.setChecked(True)
				self.main_stack.setCurrentIndex(1)
				if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'proto_combo'):
					self.comm_console.proto_combo.setCurrentIndex(3)  # CMD
					self.comm_console._on_proto_changed()
			except Exception:
				pass
			# Inject into first CMD terminal
			term = None
			try:
				if hasattr(self.comm_console, 'cmd_terms') and self.comm_console.cmd_terms:
					term = self.comm_console.cmd_terms[0]
			except Exception:
				term = None
			if term and hasattr(term, 'input') and hasattr(term, '_send'):
				term.input.setText(adb_cmd)
				term._send()
			else:
				# Fallback: background cmd so execution still happens
				import subprocess
				subprocess.Popen(["cmd", "/d", "/c", adb_cmd], creationflags=0)
		except Exception:
			pass

	def _start_adb_tail(self, serial: Optional[str]) -> None:
		"""Start streaming the device log file into the app via adb tail -f."""
		try:
			self._stop_process()
			self.process = QtCore.QProcess(self)
			self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
			self.process.readyReadStandardOutput.connect(self._on_process_output)
			self.process.finished.connect(self._on_process_finished)
			args: List[str] = []
			if serial:
				args += ["-s", serial]
			# Wait for the file to appear on device, then follow it from the start.
			# Avoid a race where tail runs before the file is created. We must pass a
			# single command string to `adb shell` (it will invoke /system/bin/sh -c
			# '<cmd>'), so do not add an extra "sh -c" layer here.
			status_path = "/tmp/android_stress_tool/stress_tool_status.txt"
			# Portable wait (first run) without requiring external tools; then follow from EOF
			# so we do not include any previous run's content in the Show Log.
			wait_and_tail = (
				f"while [ ! -e \"{status_path}\" ]; do sleep 0.5; done; "
				f"tail -n 0 -F \"{status_path}\" 2>/dev/null"
			)
			args += ["shell", wait_and_tail]
			self.process.start("adb", args)
			# don't wait; stream as available
		except Exception:
			pass

	def _start_process(self, cmd: str) -> None:
		self._stop_process()
		self.process = QtCore.QProcess(self)
		self.process.setProcessChannelMode(QtCore.QProcess.MergedChannels)
		self.process.readyReadStandardOutput.connect(self._on_process_output)
		self.process.finished.connect(self._on_process_finished)
		# Use shell for Windows to interpret full command line
		self.process.start("powershell.exe", ["-NoProfile", "-Command", cmd])
		if not self.process.waitForStarted(3000):
			QtWidgets.QMessageBox.critical(self, "Error", "Failed to start command.")
			self._on_stop()

	def _stop_process(self) -> None:
		if self.process is not None:
			self.process.kill()
			self.process = None
		# Stop tailing file when stopping
		self._stop_tail_file()

	def _on_stop(self) -> None:
		"""Stop the test for the currently selected OS."""
		print("[DEBUG] Stop button clicked")
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		print(f"[DEBUG] Stopping test for OS: {os_sel}")
		
		# Show appropriate console and switch protocol
		try:
			self.btn_uart_toggle.setChecked(True)
			self.main_stack.setCurrentIndex(1)
			if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'proto_combo'):
				if os_sel == "AAOS":
					# Switch to CMD protocol for AAOS
					self.comm_console.proto_combo.setCurrentIndex(3)  # CMD
					self.comm_console._on_proto_changed()
					print("[DEBUG] Switched to CMD terminal for AAOS stop")
				elif os_sel in ("Yocto", "Ubuntu"):
					# For Linux, only switch to UART if not already there
					current_proto = self.comm_console.proto_combo.currentIndex()
					if current_proto != 0:
						self.comm_console.proto_combo.setCurrentIndex(0)  # UART
						self.comm_console._on_proto_changed()
						print("[DEBUG] Switched to UART console for Linux stop")
					else:
						print("[DEBUG] Already on UART console, no switching needed")
		except Exception as e:
			print(f"[DEBUG] Error switching console: {e}")
		
		# Mark this OS as not running
		if hasattr(self, 'os_running_states'):
			self.os_running_states[os_sel] = False
		
		# Update global running state
		self.is_running = any(self.os_running_states.values()) if hasattr(self, 'os_running_states') else False
		
		# Stop processes and timers
		self._stop_process()
		if not self.is_running:  # Only stop timers if no OS has running tests
			self.end_time_epoch = None
			self._stop_schedule_timer()
		
		# OS-specific cleanup with UI feedback
		try:
			if os_sel == "AAOS":
				print("[DEBUG] Stopping AAOS test")
				# Add stop header to CMD terminal
				if hasattr(self, 'comm_console') and hasattr(self.comm_console, 'cmd_terms') and self.comm_console.cmd_terms:
					cmd_term = self.comm_console.cmd_terms[0]
					if hasattr(cmd_term, 'input'):
						cmd_term.input.setText("echo === Stopping AAOS Test ===")
						cmd_term.input.returnPressed.emit()
						cmd_term.input.setText("echo Sending stop commands...")
						cmd_term.input.returnPressed.emit()
						cmd_term.input.setText("echo ================================================")
						cmd_term.input.returnPressed.emit()
				self._kill_android_stress_tool_via_adb()
			elif os_sel in ("Yocto", "Ubuntu"):
				print("[DEBUG] Stopping Linux test")
				# Ensure UART is connected before sending stop command
				self._ensure_uart_connected_for_stop()
				# Just send the stop command directly without clearing or headers
				self._kill_stress_tool_via_uart()
		except Exception as e:
			print(f"[DEBUG] Error during OS-specific cleanup: {e}")
		
		# Update button states for current OS
		self._update_button_states_for_os(os_sel)

	def _ensure_uart_connected_for_stop(self) -> None:
		"""Ensure UART is connected before sending stop command."""
		try:
			# Check if UART is currently connected
			uart_connected = bool(self.comm_console.uart_connect_btn.isChecked())
			print(f"[DEBUG] UART currently connected: {uart_connected}")
			
			if not uart_connected:
				print("[DEBUG] UART not connected, attempting to reconnect...")
				# Try to find and connect to Linux port
				linux_port = self.comm_console.find_linux_port("VID:PID=067B:23A3")
				if linux_port:
					print(f"[DEBUG] Found Linux port: {linux_port}")
					connected = self.comm_console.connect_to_port(linux_port, baud=921600)
					if connected:
						print("[DEBUG] Successfully reconnected to UART")
					else:
						print("[DEBUG] Failed to reconnect to UART")
				else:
					print("[DEBUG] No Linux port found")
			else:
				print("[DEBUG] UART already connected")
		except Exception as e:
			print(f"[DEBUG] Error ensuring UART connection: {e}")

	def _on_process_finished(self) -> None:
		"""Handle when a test process finishes naturally."""
		print("[DEBUG] test process finished naturally")
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		
		# Mark this OS as not running
		if hasattr(self, 'os_running_states'):
			self.os_running_states[os_sel] = False
		
		# Update global running state
		self.is_running = any(self.os_running_states.values()) if hasattr(self, 'os_running_states') else False
		
		# Add completion message to UART console for Linux tests
		if os_sel in ("Yocto", "Ubuntu") and hasattr(self, 'comm_console') and hasattr(self.comm_console, 'log'):
			self.comm_console.log.appendPlainText(f"\n=== {os_sel} Test Completed ===")
			self.comm_console.log.appendPlainText("=" * 50)
		
		self._stop_process()
		
		# Update button states for current OS
		self._update_button_states_for_os(os_sel)

	def _on_process_output(self) -> None:
		now = get_timestamp()
		if self.is_running and self.end_time_epoch is not None and now >= self.end_time_epoch:
			self._on_stop()
			return
		if self.process is not None:
			chunk = self.process.readAllStandardOutput().data()
			if chunk:
				try:
					self._raw_log_buffer += chunk.decode(errors='ignore')
				except Exception:
					self._raw_log_buffer += str(chunk)
			self.line_buffer += chunk
		self._redraw_curve()
		self._refresh_numeric_list()
		self._update_export_enabled()
		# Re-enable Execute if completion text is seen in AAOS status file stream
		try:
			if "Stress test completed" in getattr(self, '_raw_log_buffer', ''):
				print("[DEBUG] AAOS test completed naturally")
				# Mark AAOS as not running
				if hasattr(self, 'os_running_states'):
					self.os_running_states["AAOS"] = False
				# Update global running state
				self.is_running = any(self.os_running_states.values()) if hasattr(self, 'os_running_states') else False
				# Update button states for current OS
				current_os = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
				self._update_button_states_for_os(current_os)
		except Exception:
			pass

	def _try_parse_and_store(self, line: str, ts: float) -> None:
		# Parse Linux stress tool output format
		# Examples: cpu0: 61.38619%, DRAM usage: 4.87443%, GPU usage: 0%
		
		# Parse CPU cores (cpu0: 61.38619%, cpu1: 80%, etc.)
		for core_id in self.active_cores:
			core_patterns = [
				rf"cpu{core_id}:\s*([0-9]+(?:\.[0-9]+)?)%",
				rf"Core\s+{core_id}:\s*([0-9]+(?:\.[0-9]+)?)%",
			]
			for pat in core_patterns:
				m = re.search(pat, line)
				if m:
					val = float(m.group(1))
					self.core_states[core_id].values.append((ts, val))
					break
		
		# Parse DRAM usage
		if Subsystem.DRAM in self.active_subsystems:
			dram_patterns = [
				r"DRAM usage:\s*([0-9]+(?:\.[0-9]+)?)%",
				r"DRAM\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%",
			]
			for pat in dram_patterns:
				m = re.search(pat, line)
				if m:
					val = float(m.group(1))
					self.states[Subsystem.DRAM].values.append((ts, val))
					break
		
		# Parse GPU usage
		if Subsystem.GPU in self.active_subsystems:
			gpu_patterns = [
				r"GPU usage:\s*([0-9]+(?:\.[0-9]+)?)%",
				r"GPU\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%",
			]
			for pat in gpu_patterns:
				m = re.search(pat, line)
				if m:
					val = float(m.group(1))
					self.states[Subsystem.GPU].values.append((ts, val))
					break
		
		# Fallback to old patterns for compatibility
		patterns = {
			Subsystem.CPU: [r"CPU\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%", r"cpu\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%"],
			Subsystem.GPU: [r"GPU\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%", r"gpu\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%"],
			Subsystem.DRAM: [r"DRAM\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%", r"MEM(?:ORY)?\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%", r"mem\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)%"],
		}
		
		# Parse regular subsystems (excluding CPU)
		for name in self.active_subsystems:
			if name != Subsystem.CPU:  # Skip CPU, handle cores separately
				for pat in patterns.get(name, []):
					m = re.search(pat, line)
					if m:
						val = float(m.group(1))
						self.states[name].values.append((ts, val))
						break
		
		# psutil fallback removed - using external backend for data collection

	# _sample_metrics function removed - using external backend for data collection

	def _redraw_curve(self) -> None:
		active = self.combo_active.currentText()
		if not active:
			return
		
		if active == "CPU (cores)":
			# For each core, draw its curve (show all cores, not just active ones)
			for core_id in range(getattr(self, 'core_count', 0)):
				state = self.core_states[core_id]
				if state.curve is None or not state.values:
					if state.curve is not None and not state.values:
						state.curve.setData([], [])
					continue
				t0 = state.values[0][0]
				x = [ts - t0 for ts, _ in state.values]
				y = [max(0, min(100, v)) for _, v in state.values]  # Clamp values to 0-100%
				state.curve.setData(x, y)
			if any(self.core_states[c].values for c in range(getattr(self, 'core_count', 0))):
				last_x = max((self.core_states[c].values[-1][0] - self.core_states[c].values[0][0]) for c in range(getattr(self, 'core_count', 0)) if self.core_states[c].values)
				if last_x > 60:
					# Smooth scrolling: show last 60 seconds
					self.plot_widget.setXRange(max(0, last_x - 60), last_x)
				else:
					# Show from 0 to at least 60 seconds
					self.plot_widget.setXRange(0, max(60, last_x + 10))
				# Trigger dynamic time scaling
				self._on_view_range_changed()
			return
		
		# Single item view
		if active.startswith("Core "):
			core_id = int(active.split()[1])
			state = self.core_states[core_id]
		else:
			state = self.states[active]
		
		if state.curve is None:
			return
		if not state.values:
			state.curve.setData([], [])
			return
		t0 = state.values[0][0]
		x = [ts - t0 for ts, _ in state.values]
		y = [max(0, min(100, v)) for _, v in state.values]  # Clamp values to 0-100%
		state.curve.setData(x, y)
		if x:
			span = x[-1]
			# Dynamic trailing window that grows with total span but caps for readability
			# 0-2min: show last 60s, 2-10min: last 3min, 10-60min: last 10min, >1h: last 20min
			if span > 3600:
				window = 1200  # 20 min
			elif span > 600:
				window = 600   # 10 min
			elif span > 120:
				window = 180   # 3 min
			else:
				window = 60
			left = max(0, span - window)
			self.plot_widget.setXRange(left, span)
		# Trigger dynamic time scaling
		self._on_view_range_changed()

	def _refresh_numeric_list(self) -> None:
		self.numeric_list.clear()
		
		# Add all subsystems (show all, not just active ones)
		for name in SUBSYSTEMS:
			state = self.states[name]
			val: Optional[float] = state.values[-1][1] if state.values else None
			# Show target only if this subsystem is active
			target_text = f" (target {state.target_percent}%)" if name in self.active_subsystems else " (no target)"
			text = f"{name}: {val:.1f}%{target_text}" if val is not None else f"{name}: --{target_text}"
			item = QtWidgets.QListWidgetItem(text)
			self.numeric_list.addItem(item)
		
		# Add all CPU cores (show all, not just active ones)
		for core_id in range(getattr(self, 'core_count', 0)):
			state = self.core_states[core_id]
			val: Optional[float] = state.values[-1][1] if state.values else None
			# Show target only if this core is active
			target_text = f" (target {state.target_percent}%)" if core_id in self.active_cores else " (no target)"
			text = f"Core {core_id}: {val:.1f}%{target_text}" if val is not None else f"Core {core_id}: --{target_text}"
			item = QtWidgets.QListWidgetItem(text)
			self.numeric_list.addItem(item)
		
		self._update_numeric_colors()
		# Update KPI header labels with latest values
		cpu_val = self.states[Subsystem.CPU].values[-1][1] if self.states[Subsystem.CPU].values else None
		gpu_val = self.states[Subsystem.GPU].values[-1][1] if self.states[Subsystem.GPU].values else None
		dram_val = self.states[Subsystem.DRAM].values[-1][1] if self.states[Subsystem.DRAM].values else None
		if hasattr(self, 'kpi_cpu'):
			self.kpi_cpu.setText(f"CPU: {cpu_val:.1f}%" if cpu_val is not None else "CPU: --%")
		if hasattr(self, 'kpi_gpu'):
			self.kpi_gpu.setText(f"GPU: {gpu_val:.1f}%" if gpu_val is not None else "GPU: --%")
		if hasattr(self, 'kpi_dram'):
			self.kpi_dram.setText(f"DRAM: {dram_val:.1f}%" if dram_val is not None else "DRAM: --%")

	def _update_numeric_colors(self) -> None:
		item_idx = 0
		
		# Color all subsystems
		for name in SUBSYSTEMS:
			state = self.states[name]
			val: Optional[float] = state.values[-1][1] if state.values else None
			item = self.numeric_list.item(item_idx)
			if item is not None:
				if val is None:
					item.setForeground(QtGui.QBrush(QtGui.QColor(120, 120, 120)))
				elif name in self.active_subsystems and val >= state.target_percent:
					item.setForeground(QtGui.QBrush(QtGui.QColor(0, 130, 0)))
				elif name in self.active_subsystems and val < state.target_percent:
					item.setForeground(QtGui.QBrush(QtGui.QColor(180, 0, 0)))
				else:
					# No target set - neutral color
					item.setForeground(QtGui.QBrush(QtGui.QColor(100, 100, 100)))
			item_idx += 1

	def _open_log_dialog(self) -> None:
		# Create a modal dialog to display the log content (snapshot for Yocto/Ubuntu; live for AAOS)
		d = QtWidgets.QDialog(self)
		d.setWindowTitle("Live Log Viewer")
		d.resize(900, 600)
		v = QtWidgets.QVBoxLayout(d)
		# Toolbar row
		row = QtWidgets.QHBoxLayout()
		row.addWidget(self._make_label("File:"))
		path_lbl = QtWidgets.QLabel(self.log_file_edit.text() if hasattr(self, 'log_file_edit') else "")
		row.addWidget(path_lbl)
		row.addStretch(1)
		btn_close = QtWidgets.QPushButton("Close")
		btn_close.clicked.connect(d.accept)
		row.addWidget(btn_close)
		v.addLayout(row)
		# Text area
		text = QtWidgets.QPlainTextEdit()
		text.setReadOnly(True)
		text.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
		mono = QtGui.QFont("Consolas", 10)
		text.setFont(mono)
		v.addWidget(text, 1)
		# Decide path based on target OS
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		if os_sel in ("Yocto", "Ubuntu") and hasattr(self, 'comm_console'):
			# Simple UART approach - send cat command and show result
			text.setPlainText("Fetching latest status from device…")
			
			# Ensure connected; if not, try best-effort connect
			connected = bool(self.comm_console.uart_connect_btn.isChecked())
			if not connected:
				try:
					linux_port = self.comm_console.find_linux_port("VID:PID=067B:23A3")
					if linux_port:
						connected = self.comm_console.connect_to_port(linux_port, baud=921600)
				except Exception as e:
					print(f"UART connect failed: {e}")
					connected = False
			
			if not connected:
				text.setPlainText("Failed to connect to Linux UART.")
			else:
				# Simple approach: send cat command and extract result from console
				def _show_result():
					try:
						port = self.comm_console.uart_port_combo.currentText()
						console_text = self.comm_console._port_logs.get(port, "")
						
						# Find the last cat command output
						lines = console_text.split('\n')
						start_idx = -1
						for i, line in enumerate(lines):
							if 'cat /stress_tools/stress_tool_status.txt' in line:
								start_idx = i + 1
						if start_idx >= 0 and start_idx < len(lines):
							file_content = '\n'.join(lines[start_idx:])
							text.setPlainText(file_content)
						else:
							text.setPlainText("No status data found in console output")
					except Exception as e:
						text.setPlainText(f"Error parsing console output: {e}")
				
				# Send the command (will show in console)
				self.comm_console.send_commands([
					"cat /stress_tools/stress_tool_status.txt",
				], spacing_ms=500, on_complete=_show_result)
			
			# No live timer for UART snapshot; close dialog to fetch again later
			d.exec()
			return
		# Non-Yocto/Ubuntu path (AAOS): existing behavior
		# Clear buffer before showing to guarantee "fresh" view
		self._raw_log_buffer = ""
		# Load initial snapshot via ADB, then fallbacks
		_initial_set = False
		try:
			import subprocess
			res = subprocess.run([
				"adb", "shell", "cat", "/tmp/android_stress_tool/stress_tool_status.txt"
			], capture_output=True, text=True, timeout=3)
			if res.returncode == 0 and (res.stdout or res.stderr):
				text.setPlainText(res.stdout or res.stderr)
				_initial_set = True
		except Exception:
			pass
		if not _initial_set:
			try:
				buf = getattr(self, '_raw_log_buffer', '')
				if buf:
					text.setPlainText(buf)
					_initial_set = True
				else:
					p = getattr(self, '_file_tail_path', None)
					if p and os.path.exists(p):
						with open(p, 'r', encoding='utf-8', errors='ignore') as f:
							text.setPlainText(f.read())
						_initial_set = True
			except Exception:
				pass
		# Live updates: reflect _raw_log_buffer into the dialog periodically (AAOS)
		append_timer = QtCore.QTimer(d)
		append_timer.setInterval(300)
		_prev_len = {'n': len(getattr(self, '_raw_log_buffer', ''))}
		def _pump():
			try:
				buf = getattr(self, '_raw_log_buffer', '')
				if not buf:
					return
				cur = len(buf)
				if cur > _prev_len['n']:
					new_txt = buf[_prev_len['n']:]
					text.moveCursor(QtGui.QTextCursor.End)
					text.insertPlainText(new_txt)
					text.moveCursor(QtGui.QTextCursor.End)
					_prev_len['n'] = cur
			except Exception:
				pass
		append_timer.timeout.connect(_pump)
		append_timer.start()
		d.exec()

	def _on_os_changed(self, new_os: str) -> None:
		"""Handle OS dropdown changes - update button states based on current OS running state."""
		print(f"[DEBUG] OS changed to: {new_os}")
		self.selected_target_os = new_os
		
		# Initialize OS running states if not exists
		if not hasattr(self, 'os_running_states'):
			self.os_running_states = {"Yocto": False, "Ubuntu": False, "AAOS": False}
		
		# Update button states based on the selected OS
		self._update_button_states_for_os(new_os)

	def _update_button_states_for_os(self, os_name: str) -> None:
		"""Update button states based on whether the selected OS has a running test."""
		print(f"[DEBUG] Updating button states for OS: {os_name}")
		
		# Check if this OS has a running test
		is_running = self.os_running_states.get(os_name, False)
		print(f"[DEBUG] OS {os_name} running state: {is_running}")
		
		if is_running:
			# This OS has a running test - show stop button
			self.btn_start.setEnabled(False)
			self.btn_stop.setEnabled(True)
			self.duration_spin.setEnabled(False)
			print("[DEBUG] Showing stop button for running test")
		else:
			# This OS has no running test - show execute button
			self.btn_start.setEnabled(True)
			self.btn_stop.setEnabled(False)
			self.duration_spin.setEnabled(True)
			print("[DEBUG] Showing execute button for idle OS")
		
		# Update action states
		try:
			if hasattr(self, 'action_execute'):
				self.action_execute.setEnabled(not is_running)
			if hasattr(self, 'action_stop'):
				self.action_stop.setEnabled(is_running)
		except Exception:
			pass

	def _reset_button_states(self) -> None:
		"""Reset all button states to their default (ready to start) state."""
		print("[DEBUG] Resetting button states")
		self.is_running = False
		self.end_time_epoch = None
		
		# Reset buttons to default state
		self.btn_start.setEnabled(True)
		self.btn_stop.setEnabled(False)
		
		# Reset action states
		try:
			if hasattr(self, 'action_execute'):
				self.action_execute.setEnabled(True)
			if hasattr(self, 'action_stop'):
				self.action_stop.setEnabled(False)
		except Exception:
			pass
		
		# Re-enable duration spin
		self.duration_spin.setEnabled(True)
		
		# Stop any running timers
		self._stop_schedule_timer()
		self._stop_tail_file()

	def _parse_stress_output(self, output: str) -> None:
		"""Parse Linux stress tool output and update graph data."""
		import re
		ts = get_timestamp()
		
		# Parse CPU overall usage (from "cpu: XX.XX%" line)
		cpu_match = re.search(r'cpu:\s+([\d.]+)%', output)
		if cpu_match and Subsystem.CPU in self.active_subsystems:
			cpu_value = float(cpu_match.group(1))
			self.states[Subsystem.CPU].values.append((ts, cpu_value))
		
		# Parse CPU core usage (from "cpu0: XX.XX%" lines)
		for core_id in range(getattr(self, 'core_count', 0)):
			core_match = re.search(rf'cpu{core_id}:\s+([\d.]+)%', output)
			if core_match and core_id in self.active_cores:
				core_value = float(core_match.group(1))
				self.core_states[core_id].values.append((ts, core_value))
		
		# Parse DRAM usage (from "DRAM usage: XX.XXXXX%" line)
		dram_match = re.search(r'DRAM usage:\s+([\d.]+)%', output)
		if dram_match and Subsystem.DRAM in self.active_subsystems:
			dram_value = float(dram_match.group(1))
			self.states[Subsystem.DRAM].values.append((ts, dram_value))
		
		# Parse GPU usage (from "GPU usage: X%" line)
		gpu_match = re.search(r'GPU usage:\s+([\d.]+)%', output)
		if gpu_match and Subsystem.GPU in self.active_subsystems:
			gpu_value = float(gpu_match.group(1))
			self.states[Subsystem.GPU].values.append((ts, gpu_value))
		
		self._redraw_curve()
		self._refresh_numeric_list()
		self._update_export_enabled()
	# ===== END TEST MODE FUNCTIONS =====

	def _on_export_csv(self) -> None:
		"""
		Export performance data to CSV file.
		
		This method allows users to export all collected performance data to a CSV file
		for further analysis, reporting, or archival purposes. The exported data includes
		timestamps, subsystem names, performance values, and target values.
		
		CSV Format:
		- timestamp: Unix epoch time (integer)
		- subsystem: Name of the subsystem or "Core X" for CPU cores
		- value_percent: Performance value as percentage (3 decimal places)
		- target_percent: Target value for the subsystem/core
		
		The method:
		1. Checks if there is any data to export
		2. Shows a file dialog for save location
		3. Writes data in CSV format with proper headers
		4. Includes data from both active subsystems and CPU cores
		
		Note:
			Only data from currently active subsystems and cores is exported.
			The file is saved with UTF-8 encoding for international compatibility.
		"""
		# Check if there is any data to export
		has_data = any(self.states[name].values for name in self.active_subsystems) or any(self.core_states[core_id].values for core_id in self.active_cores)
		if not has_data:
			QtWidgets.QMessageBox.information(self, "Info", "No data to export.")
			return
			
		# Show file dialog for save location
		path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save CSV", os.path.join(os.getcwd(), "results.csv"), "CSV Files (*.csv)")
		if not path:
			return
			
		# Write CSV file with performance data
		with open(path, "w", newline="", encoding="utf-8") as f:
			writer = csv.writer(f)
			# Write CSV header
			writer.writerow(["timestamp", "subsystem", "value_percent", "target_percent"])
			
			# Export data from active subsystems
			for name in self.active_subsystems:
				state = self.states[name]
				for ts, val in state.values:
					writer.writerow([int(ts), name, f"{val:.3f}", state.target_percent])
					
			# Export data from active CPU cores
			for core_id in self.active_cores:
				state = self.core_states[core_id]
				for ts, val in state.values:
					writer.writerow([int(ts), f"Core {core_id}", f"{val:.3f}", state.target_percent])

	def _on_export_png(self) -> None:
		"""
		Export the current performance graph to PNG image file.
		
		This method allows users to save the currently displayed performance graph
		as a high-quality PNG image for reports, presentations, or documentation.
		The exported image is 1200 pixels wide for good resolution.
		
		Export Process:
		1. Check if there is an active graph to export
		2. Show file dialog for save location
		3. Use PyQtGraph's ImageExporter for high-quality export
		4. Fall back to widget grab if exporter fails
		
		The method uses PyQtGraph's built-in ImageExporter for optimal quality,
		but falls back to a simple widget grab if the exporter is not available.
		
		Note:
			The exported image will show the currently selected subsystem/core
			graph with all its current data and formatting.
		"""
		# Check if there is an active graph to export
		if self.combo_active.currentText() == "":
			QtWidgets.QMessageBox.information(self, "Info", "No active graph to export.")
			return
			
		# Show file dialog for save location
		path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save PNG", os.path.join(os.getcwd(), "graph.png"), "PNG Files (*.png)")
		if not path:
			return
			
		# Try to use PyQtGraph's high-quality ImageExporter
		try:
			from pyqtgraph.exporters import ImageExporter
			exporter = ImageExporter(self.plot_widget.plotItem)
			exporter.parameters()["width"] = 1200  # Set width to 1200 pixels
			exporter.export(path)
		except Exception:
			# Fall back to simple widget grab if exporter fails
			pixmap = self.plot_widget.grab()
			pixmap.save(path, "PNG")

	def _on_clear(self) -> None:
		for name in self.states:
			self.states[name].values.clear()
		for core_id in self.core_states:
			self.core_states[core_id].values.clear()
		self._file_tail_pos = 0
		self._file_tail_rem = b""
		self._redraw_curve()
		self._refresh_numeric_list()
		self._update_export_enabled()

	def _update_export_enabled(self) -> None:
		has_data = any(self.states[name].values for name in self.active_subsystems) or any(self.core_states[core_id].values for core_id in self.active_cores)
		self.btn_export_csv.setEnabled(has_data)
		self.btn_export_png.setEnabled(self.combo_active.currentText() != "")

	def _update_command_preview(self) -> None:
		# Build stress command from current selections
		os_sel = getattr(self, 'selected_target_os', None) or (self.combo_target_os.currentText() if hasattr(self, 'combo_target_os') else "")
		is_aaos = (os_sel == "AAOS")
		binary = "./android_stress_tool" if is_aaos else "./stress_tool"
		parts: List[str] = [binary]
		
		# CPU load (overall CPU target) - only if CPU Target is checked
		if (Subsystem.CPU in self.active_subsystems and 
			hasattr(self, 'cpu_target_checkbox') and 
			self.cpu_target_checkbox.isChecked()):
			target = self.states[Subsystem.CPU].target_percent
			parts.extend(["--cpu-core", "all", "--cpu-load", str(target)])
		else:
			# CPU cores - only if individual cores are checked (and CPU Target is not checked)
			for core_id in self.active_cores:
				if (hasattr(self, 'core_checkboxes') and 
					core_id in self.core_checkboxes and 
					self.core_checkboxes[core_id].isChecked()):
					target = self.core_states[core_id].target_percent
					parts.extend(["--cpu-core-load", f"{core_id}:{target}"])
		
		# GPU - only if GPU is checked
		if Subsystem.GPU in self.active_subsystems:
			target = self.states[Subsystem.GPU].target_percent
			parts.extend(["--gpu-load", str(target)])
		
		# DRAM - only if DRAM is checked
		if Subsystem.DRAM in self.active_subsystems:
			target = self.states[Subsystem.DRAM].target_percent
			parts.extend(["--dram-load", str(target)])
		
		# Duration
		dur = int(self.duration_spin.value())
		parts.extend(["--duration", str(dur)])
		
		# Add adaptive parameter if any adaptive checkboxes are selected
		if hasattr(self, 'adaptive_checkbox_group'):
			adaptive_subsystems = [name.lower() for name, cb in self.adaptive_checkbox_group.items() if cb.isChecked()]
			if adaptive_subsystems:
				adaptive_param = ",".join(adaptive_subsystems)
				parts.extend(["--adaptive", adaptive_param])
		
		# For all OS targets, add quiet and background
		cmd = " ".join(parts) + " --quiet &"
		self.command_preview.setPlainText(cmd)

	def _start_tail_file(self, path: str) -> None:
		"""Start tailing the stress output file located at path."""
		try:
			self._file_tail_path = path
			# Start from the beginning so we read existing blocks first
			self._file_tail_pos = 0
			self._file_tail_rem = b""
			self._file_block_idx = -1
			self._file_start_epoch = get_timestamp()
			# Reset persistent block state
			self._blk_active = False
			self._blk_cpu_overall = None
			self._blk_core_vals = {}
			self._blk_dram_val = None
			self._blk_gpu_val = None
			# Immediately process current contents so first block shows at t=0
			self._read_stress_file_tail()
			# Continue listening for new blocks until completion line is seen
			self._file_tail_timer.start()
			# Add file watcher for immediate reaction on append/truncate
			try:
				self._file_watcher.removePaths(self._file_watcher.files())
			except Exception:
				pass
			if os.path.exists(path):
				self._file_watcher.addPath(path)
		except Exception:
			pass

	def _stop_tail_file(self) -> None:
		self._file_tail_timer.stop()
		self._file_tail_path = None
		self._file_tail_rem = b""
		self._file_block_idx = -1

	def _read_stress_file_tail(self) -> None:
		"""Read newly appended lines from the stress output file and parse blocks.

		We look for blocks beginning with '[Monitor] CPU Usage (per core):' and ending
		when we have seen DRAM and GPU lines that follow. Each block is timestamped
		by the time we process it so graphs advance with arrivals.
		"""
		if not self._file_tail_path:
			return
		try:
			# Handle truncation/rotation: if file shrank, restart at 0
			try:
				cur_size = os.path.getsize(self._file_tail_path)
				if cur_size < self._file_tail_pos:
					self._file_tail_pos = 0
			except Exception:
				pass
			with open(self._file_tail_path, 'rb') as f:
				f.seek(self._file_tail_pos)
				data = f.read()
				if not data:
					return
				self._file_tail_pos += len(data)
				buf = self._file_tail_rem + data
		except Exception:
			return
		# Split into lines keeping remainder
		lines = buf.split(b"\n")
		self._file_tail_rem = lines[-1]
		new_lines = [ln.decode(errors='ignore').rstrip('\r') for ln in lines[:-1] if ln]
		self._parse_stress_lines(new_lines)
		# Append raw lines to live log view if visible
		if hasattr(self, 'log_text_view') and self.view_data_stack.currentIndex() == 1 and new_lines:
			try:
				self.log_text_view.moveCursor(QtGui.QTextCursor.End)
				self.log_text_view.insertPlainText("\n".join(new_lines) + "\n")
				self.log_text_view.moveCursor(QtGui.QTextCursor.End)
			except Exception:
				pass
		# Stop tailing if completion marker is seen
		for s in new_lines:
			if "Stress test completed" in s:
				self._stop_tail_file()
				break
		# Update debug tail status
		if hasattr(self, 'tail_status'):
			try:
				file_size = os.path.getsize(self._file_tail_path) if self._file_tail_path and os.path.exists(self._file_tail_path) else 0
				self.tail_status.setText(f"size={file_size} pos={self._file_tail_pos} lines={len(new_lines)} q={len(self._block_queue)} idx={self._file_block_idx}")
			except Exception:
				pass
		# Also append to raw buffer for Show Log
		try:
			if new_lines:
				self._raw_log_buffer += ("\n".join(new_lines) + "\n")
		except Exception:
			pass

	def _parse_stress_lines(self, lines: List[str]) -> None:
		# Continue filling the current block across timer ticks
		for line in lines:
			if line.startswith('[Monitor] CPU Usage (per core):'):
				# If we were in a block, commit what we have before starting a new one
				if self._blk_active and any([self._blk_cpu_overall is not None, self._blk_core_vals, self._blk_dram_val is not None, self._blk_gpu_val is not None]):
					self._enqueue_block(self._blk_cpu_overall, self._blk_core_vals, self._blk_dram_val, self._blk_gpu_val)
				# Start a new block
				self._blk_active = True
				self._blk_cpu_overall = None
				self._blk_core_vals = {}
				self._blk_dram_val = None
				self._blk_gpu_val = None
				continue
			if not self._blk_active:
				continue
			# Parse CPU overall and per-core lines
			m = re.search(r"^\s*cpu:\s*([0-9]+(?:\.[0-9]+)?)%", line)
			if m:
				try:
					self._blk_cpu_overall = float(m.group(1))
				except Exception:
					pass
				continue
			m2 = re.search(r"^\s*cpu(\d+):\s*([0-9]+(?:\.[0-9]+)?)%", line)
			if m2:
				try:
					cid = int(m2.group(1))
					self._blk_core_vals[cid] = float(m2.group(2))
				except Exception:
					pass
				continue
			# DRAM and GPU (exact match to sample)
			m3 = re.search(r"^\[Monitor\]\s*DRAM\s*usage:\s*([0-9]+(?:\.[0-9]+)?)%", line)
			if m3:
				try:
					self._blk_dram_val = float(m3.group(1))
				except Exception:
					pass
				continue
			m4 = re.search(r"^\[Monitor\]\s*GPU\s*usage:\s*([0-9]+(?:\.[0-9]+)?)%", line)
			if m4:
				try:
					self._blk_gpu_val = float(m4.group(1))
				except Exception:
					pass
				# Commit a completed block when GPU arrives
				self._enqueue_block(self._blk_cpu_overall, self._blk_core_vals, self._blk_dram_val, self._blk_gpu_val)
				self._blk_active = False
				self._blk_cpu_overall = None
				self._blk_core_vals = {}
				self._blk_dram_val = None
				self._blk_gpu_val = None
				continue
			# If we see a new block start while active, flush the previous
			if self._blk_active and line.startswith('[Monitor] CPU Usage (per core):'):
				if any([self._blk_cpu_overall is not None, self._blk_core_vals, self._blk_dram_val is not None, self._blk_gpu_val is not None]):
					self._enqueue_block(self._blk_cpu_overall, self._blk_core_vals, self._blk_dram_val, self._blk_gpu_val)
				self._blk_active = True
				self._blk_cpu_overall = None
				self._blk_core_vals = {}
				self._blk_dram_val = None
				self._blk_gpu_val = None
			# Commit on blank separator if we have at least DRAM (and possibly GPU)
			if not line.strip() and self._blk_active and (self._blk_dram_val is not None or self._blk_gpu_val is not None):
				self._enqueue_block(self._blk_cpu_overall, self._blk_core_vals, self._blk_dram_val, self._blk_gpu_val)
				self._blk_active = False
				self._blk_cpu_overall = None
				self._blk_core_vals = {}
				self._blk_dram_val = None
				self._blk_gpu_val = None

	def _enqueue_block(self, cpu_overall: Optional[float], core_vals: Dict[int, float], dram_val: Optional[float], gpu_val: Optional[float]) -> None:
		"""Commit a parsed block to time series using current timestamp.

		Blocks are played back at 5s intervals via a queue, so even if the
		file already contains many blocks, the graph advances one step at a
		time instead of jumping all at once.
		"""
		self._block_queue.append((cpu_overall, dict(core_vals), dram_val, gpu_val))
		# If this is the first block queued, schedule immediate emit and 5s cadence
		if not self._block_timer.isActive():
			self._file_block_idx = -1
			self._next_block_due_epoch = get_timestamp()
			self._block_timer.start()

	def _maybe_emit_block(self) -> None:
		if not self._block_queue or self._next_block_due_epoch is None:
			return
		if get_timestamp() < self._next_block_due_epoch:
			return
		cpu_overall, core_vals, dram_val, gpu_val = self._block_queue.pop(0)
		self._file_block_idx += 1
		start_epoch = getattr(self, '_file_start_epoch', get_timestamp())
		block_ts = start_epoch + 5.0 * max(0, self._file_block_idx)
		if cpu_overall is not None:
			self.states[Subsystem.CPU].values.append((block_ts, max(0, min(100, cpu_overall))))
		for cid, v in core_vals.items():
			if cid in self.core_states:
				self.core_states[cid].values.append((block_ts, max(0, min(100, v))))
		if dram_val is not None:
			self.states[Subsystem.DRAM].values.append((block_ts, max(0, min(100, dram_val))))
		if gpu_val is not None:
			self.states[Subsystem.GPU].values.append((block_ts, max(0, min(100, gpu_val))))
		# Update UI
		self._redraw_curve()
		self._refresh_numeric_list()
		self._update_export_enabled()
		# Schedule next block 5s later (if any)
		self._next_block_due_epoch = get_timestamp() + 5.0
		if not self._block_queue:
			# Keep timer running to catch future blocks; do not stop
			pass

	# Command browse removed with command feature

	def _on_schedule_load(self) -> None:
		"""Open the schedule load dialog."""
		dialog = ScheduleLoadDialog(self, self.scheduled_changes)
		if dialog.exec() == QtWidgets.QDialog.Accepted:
			self.scheduled_changes = dialog.get_scheduled_changes()
			self._update_schedule_display()

	def _update_schedule_display(self) -> None:
		"""Update the schedule load button text to show number of scheduled changes."""
		if self.scheduled_changes:
			self.btn_schedule_load.setText(f"Schedule Load ({len(self.scheduled_changes)} scheduled)")
		else:
			self.btn_schedule_load.setText("Schedule Load")

	def _start_schedule_timer(self) -> None:
		"""Start the timer for checking scheduled changes."""
		if not self.scheduled_changes:
			return
		
		self.test_start_time = get_timestamp()
		self.schedule_timer = QtCore.QTimer(self)
		self.schedule_timer.setInterval(1000)  # Check every second
		self.schedule_timer.timeout.connect(self._check_scheduled_changes)
		self.schedule_timer.start()

	def _stop_schedule_timer(self) -> None:
		"""Stop the schedule timer."""
		if self.schedule_timer:
			self.schedule_timer.stop()
			self.schedule_timer = None
		self.test_start_time = None

	def _check_scheduled_changes(self) -> None:
		"""Check if any scheduled changes need to be applied."""
		if not self.is_running or not self.test_start_time:
			return
		
		elapsed_time = get_timestamp() - self.test_start_time

		# Handle harmonic ramps in progress
		if self.active_harmonics:
			to_delete = []
			for subsystem, (start_s, end_s, start_v, end_v) in self.active_harmonics.items():
				if elapsed_time >= end_s:
					self._apply_scheduled_change(subsystem, end_v)
					to_delete.append(subsystem)
				elif elapsed_time >= start_s:
					# Interpolate linearly between start and end
					span = max(1e-6, end_s - start_s)
					alpha = (elapsed_time - start_s) / span
					cur_val = int(round(start_v + (end_v - start_v) * alpha))
					self._apply_scheduled_change(subsystem, cur_val)
			for k in to_delete:
				self.active_harmonics.pop(k, None)

		# Find changes that should be applied or initialized now
		remaining_changes: List[Tuple[float, str, int, str]] = []
		RAMP_SECS = 120.0  # 2 minutes ramp window
		for time_offset, subsystem, target_value, mode in self.scheduled_changes:
			if mode.lower() == "harmonic":
				start_s = max(0.0, time_offset - RAMP_SECS)
				if elapsed_time >= time_offset:
					# Ensure final value is applied
					self._apply_scheduled_change(subsystem, target_value)
					continue
				elif elapsed_time >= start_s:
					# Initialize harmonic if not present
					if subsystem not in self.active_harmonics:
						# Determine current target as start value
						start_v = self._get_current_target_for(subsystem)
						self.active_harmonics[subsystem] = (start_s, time_offset, start_v, target_value)
					# Keep in list until completed
					remaining_changes.append((time_offset, subsystem, target_value, mode))
				else:
					# Already ramping; keep until end
					remaining_changes.append((time_offset, subsystem, target_value, mode))
			else:
				# sudden
				if elapsed_time >= time_offset:
					self._apply_scheduled_change(subsystem, target_value)
					continue
				else:
					remaining_changes.append((time_offset, subsystem, target_value, mode))

		self.scheduled_changes = remaining_changes
		self._update_schedule_display()

	def _get_current_target_for(self, subsystem: str) -> int:
		"""Return the current target value for a subsystem or core."""
		if subsystem == "CPU":
			return int(self.states[Subsystem.CPU].target_percent)
		elif subsystem.startswith("Core "):
			try:
				cid = int(subsystem.split()[1])
				return int(self.core_states.get(cid, CoreState(cid)).target_percent)
			except Exception:
				return 50
		elif subsystem in self.states:
			return int(self.states[subsystem].target_percent)
		return 50

	def _apply_scheduled_change(self, subsystem: str, target_value: int) -> None:
		"""Apply a scheduled change to a subsystem."""
		if subsystem == "CPU" and hasattr(self, 'cpu_target_checkbox'):
			# Apply to CPU Target
			self.states[Subsystem.CPU].target_percent = target_value
			self.cpu_target_slider.setValue(target_value)
			self.cpu_target_text.setText(str(target_value))
			# Update all core targets if CPU Target is checked
			if self.cpu_target_checkbox.isChecked():
				for core_id in self.core_states:
					self.core_states[core_id].target_percent = target_value
					if core_id in self.core_sliders:
						self.core_sliders[core_id].setValue(target_value)
					if core_id in self.core_texts:
						self.core_texts[core_id].setText(str(target_value))
		elif subsystem.startswith("Core ") and hasattr(self, 'core_states'):
			# Apply to individual CPU core
			try:
				core_id = int(subsystem.split()[1])
				if core_id in self.core_states:
					self.core_states[core_id].target_percent = target_value
					# Update core slider and text if they exist
					if core_id in self.core_sliders:
						self.core_sliders[core_id].setValue(target_value)
					if core_id in self.core_texts:
						self.core_texts[core_id].setText(str(target_value))
			except (ValueError, IndexError):
				pass
		elif subsystem in self.states:
			# Apply to regular subsystem
			self.states[subsystem].target_percent = target_value
			# Update slider if it exists
			for i in range(self.slider_form.rowCount()):
				label = self.slider_form.itemAt(i, QtWidgets.QFormLayout.LabelRole).widget()
				if label and label.text().startswith(f"{subsystem} Target:"):
					row_widget = self.slider_form.itemAt(i, QtWidgets.QFormLayout.FieldRole).widget()
					if row_widget:
						slider = row_widget.findChild(QtWidgets.QSlider)
						if slider:
							slider.setValue(target_value)
						text_field = row_widget.findChild(QtWidgets.QLineEdit)
						if text_field:
							text_field.setText(str(target_value))
					break
		
		self._update_command_preview()
		self._update_numeric_colors()


class InputValidationDelegate(QtWidgets.QStyledItemDelegate):
	"""Custom delegate for input validation in the schedule table."""
	
	def createEditor(self, parent, option, index):
		"""Create appropriate editor based on column."""
		if index.column() == 0:  # Time column
			editor = QtWidgets.QLineEdit(parent)
			editor.setValidator(QtGui.QDoubleValidator(0.0, 9999.9, 1, parent))
			editor.setPlaceholderText("e.g., 5.0 (5th minute)")
			# Select all text when editor is created
			editor.selectAll()
			return editor
		elif index.column() == 2:  # Target percentage column
			editor = QtWidgets.QLineEdit(parent)
			editor.setValidator(QtGui.QIntValidator(0, 100, parent))
			editor.setPlaceholderText("0-100")
			# Select all text when editor is created
			editor.selectAll()
			return editor
		else:
			return super().createEditor(parent, option, index)
	
	def setEditorData(self, editor, index):
		"""Set the data in the editor."""
		if index.column() in [0, 2]:  # Time or Target columns
			editor.setText(index.data(QtCore.Qt.DisplayRole) or "")
			# Select all text for easy replacement
			editor.selectAll()
		else:
			super().setEditorData(editor, index)
	
	def setModelData(self, editor, model, index):
		"""Set the data in the model."""
		if index.column() in [0, 2]:  # Time or Target columns
			text = editor.text().strip()
			if text:
				model.setData(index, text, QtCore.Qt.EditRole)
		else:
			super().setModelData(editor, model, index)


class ScheduleLoadDialog(QtWidgets.QDialog):
	"""Dialog for scheduling load changes during the test."""
	
	def __init__(self, parent, scheduled_changes: List[Tuple[float, str, int, str]]):
		super().__init__(parent)
		self.scheduled_changes = scheduled_changes.copy()
		self.setWindowTitle("Schedule Load Changes")
		self.setModal(True)
		self.resize(900, 480)
		
		self._build_ui()
		self._populate_table()

	def _build_ui(self) -> None:
		layout = QtWidgets.QVBoxLayout(self)
		
		# Instructions
		instructions = QtWidgets.QLabel(
			"Schedule load changes during the test. Time is the exact minute when the change should occur.\n"
			"Example: Enter '5' to set target at the 5th minute (300 seconds) of the test.\n"
			"Available subsystems: CPU, GPU, DRAM, and individual CPU cores (Core 0, Core 1, etc.)"
		)
		instructions.setWordWrap(True)
		layout.addWidget(instructions)
		
		# Table for scheduled changes
		self.table = QtWidgets.QTableWidget()
		# Columns: Time, CPU, Core0..Core6, GPU, DRAM, Graph Type
		self.core_count = 7
		self.col_time = 0
		self.col_cpu = 1
		self.col_core0 = 2
		self.col_gpu = self.col_core0 + self.core_count
		self.col_dram = self.col_gpu + 1
		self.col_type = self.col_dram + 1
		self.total_cols = self.col_type + 1
		self.table.setColumnCount(self.total_cols)
		headers = ["Time (min)", "CPU"] + [f"Core {i}" for i in range(self.core_count)] + ["GPU", "DRAM", "Graph Type"]
		self.table.setHorizontalHeaderLabels(headers)
		
		# Set column widths
		self.table.setColumnWidth(self.col_time, 100)
		for c in range(1, self.col_type):
			self.table.setColumnWidth(c, 80)
		self.table.setColumnWidth(self.col_type, 120)
		
		# Enable sorting
		self.table.setSortingEnabled(True)
		
		# Set up input validation
		self.table.setItemDelegate(InputValidationDelegate(self.table))
		
		layout.addWidget(self.table)
		
		# Buttons for adding/removing changes
		button_layout = QtWidgets.QHBoxLayout()
		
		self.btn_add = QtWidgets.QPushButton("Add Change")
		self.btn_add.clicked.connect(self._add_change)
		button_layout.addWidget(self.btn_add)
		
		self.btn_remove = QtWidgets.QPushButton("Remove Selected")
		self.btn_remove.clicked.connect(self._remove_change)
		button_layout.addWidget(self.btn_remove)
		
		button_layout.addStretch()
		layout.addLayout(button_layout)
		
		# Dialog buttons
		dialog_buttons = QtWidgets.QDialogButtonBox(
			QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
		)
		dialog_buttons.accepted.connect(self.accept)
		dialog_buttons.rejected.connect(self.reject)
		layout.addWidget(dialog_buttons)

	def _populate_table(self) -> None:
		"""Populate the table with existing scheduled changes."""
		# Group existing flat list into rows by (time, mode)
		# Build mapping so we can prefill cells
		rows: Dict[Tuple[float, str], Dict[str, int]] = {}
		for time_offset, subsystem, target_value, mode in self.scheduled_changes:
			key = (time_offset, mode)
			rows.setdefault(key, {})[subsystem] = target_value
		self.table.setRowCount(len(rows) if rows else 1)
		if not rows:
			self._init_row_widgets(0)
			return
		for row_index, ((time_offset, mode), subs_map) in enumerate(rows.items()):
			self._init_row_widgets(row_index)
			self.table.item(row_index, self.col_time).setText(f"{time_offset / 60:.1f}")
			# Fill CPU/cores/GPU/DRAM
			if "CPU" in subs_map:
				self.table.item(row_index, self.col_cpu).setText(str(subs_map["CPU"]))
			for i in range(self.core_count):
				val = subs_map.get(f"Core {i}")
				if val is not None:
					self.table.item(row_index, self.col_core0 + i).setText(str(val))
			if Subsystem.GPU in subs_map:
				self.table.item(row_index, self.col_gpu).setText(str(subs_map[Subsystem.GPU]))
			if Subsystem.DRAM in subs_map:
				self.table.item(row_index, self.col_dram).setText(str(subs_map[Subsystem.DRAM]))
			# Type
			combo: QtWidgets.QComboBox = self.table.cellWidget(row_index, self.col_type)
			idx = combo.findText(self._normalize_mode(mode), QtCore.Qt.MatchFixedString)
			if idx >= 0:
				combo.setCurrentIndex(idx)

	def _init_row_widgets(self, row: int) -> None:
		"""Create default widgets and validators for a new row."""
		if row >= self.table.rowCount():
			self.table.insertRow(row)
		time_item = QtWidgets.QTableWidgetItem("")
		self.table.setItem(row, self.col_time, time_item)
		# percentage cells
		for col in range(self.col_cpu, self.col_type):
			item = QtWidgets.QTableWidgetItem("")
			self.table.setItem(row, col, item)
		# type combo
		combo = QtWidgets.QComboBox()
		combo.addItems(["Sudden", "Harmonic"])
		self.table.setCellWidget(row, self.col_type, combo)

	def _normalize_mode(self, mode: str) -> str:
		return "Harmonic" if str(mode).lower().startswith("harm") else "Sudden"

	def _add_change(self) -> None:
		"""Add a new scheduled change."""
		row = self.table.rowCount()
		self.table.insertRow(row)
		self._init_row_widgets(row)

	def _remove_change(self) -> None:
		"""Remove the selected scheduled change."""
		current_row = self.table.currentRow()
		if current_row >= 0:
			self.table.removeRow(current_row)

	def get_scheduled_changes(self) -> List[Tuple[float, str, int, str]]:
		"""Get the scheduled changes from the table.
		Returns a flat list of (time_seconds, subsystem, target_value, mode)."""
		changes: List[Tuple[float, str, int, str]] = []
		for row in range(self.table.rowCount()):
			time_item = self.table.item(row, self.col_time)
			if not time_item:
				continue
			time_text = (time_item.text() or "").strip()
			if not time_text:
				continue
			try:
				time_minutes = float(time_text)
				when_s = time_minutes * 60.0
			except ValueError:
				continue
			mode_combo: QtWidgets.QComboBox = self.table.cellWidget(row, self.col_type)
			mode = (mode_combo.currentText() if mode_combo else "Sudden")
			mode_norm = "harmonic" if mode.lower().startswith("harm") else "sudden"
			# CPU
			cpu_txt = (self.table.item(row, self.col_cpu).text() if self.table.item(row, self.col_cpu) else "").strip()
			if cpu_txt:
				try:
					changes.append((when_s, "CPU", int(cpu_txt), mode_norm))
				except ValueError:
					pass
			# Cores
			for i in range(self.core_count):
				cell = self.table.item(row, self.col_core0 + i)
				val_txt = (cell.text() if cell else "").strip()
				if val_txt:
					try:
						changes.append((when_s, f"Core {i}", int(val_txt), mode_norm))
					except ValueError:
						pass
			# GPU / DRAM
			gpu_txt = (self.table.item(row, self.col_gpu).text() if self.table.item(row, self.col_gpu) else "").strip()
			if gpu_txt:
				try:
					changes.append((when_s, Subsystem.GPU, int(gpu_txt), mode_norm))
				except ValueError:
					pass
			dram_txt = (self.table.item(row, self.col_dram).text() if self.table.item(row, self.col_dram) else "").strip()
			if dram_txt:
				try:
					changes.append((when_s, Subsystem.DRAM, int(dram_txt), mode_norm))
				except ValueError:
					pass
		# Sort by time
		changes.sort(key=lambda x: x[0])
		return changes


def main() -> None:
	app = QtWidgets.QApplication(sys.argv)
	pg.setConfigOptions(antialias=True)
	w = PerformanceApp()
	w.show()
	sys.exit(app.exec())


if __name__ == "__main__":
	main()
