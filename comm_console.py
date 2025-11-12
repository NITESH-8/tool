"""
Communication Console Module - Multi-Protocol Communication Interface

This module provides a comprehensive communication console that supports multiple
communication protocols including UART, SSH, ADB, and CMD terminals. It serves
as the communication layer for the Performance Dashboard application, enabling
interaction with various types of devices and systems.

Key Features:
- UART serial communication with configurable parameters
- ADB (Android Debug Bridge) device communication
- SSH connection support (placeholder for future implementation)
- Multiple CMD terminal instances
- Real-time data capture and logging
- Protocol switching with persistent state management

Supported Protocols:
- UART: Serial communication with baud rate, parity, stop bits configuration
- ADB: Android device communication and shell access
- SSH: Remote system access (placeholder)
- CMD: Local command execution terminals

Dependencies:
- PySide6: GUI framework
- pyserial: Serial communication (optional)
- adb_utils: Android Debug Bridge utilities

Author: Performance GUI Team
Version: 1.0
"""

from __future__ import annotations

from typing import Optional, List, Callable
import os
import platform
import re

from PySide6 import QtCore, QtGui, QtWidgets

from adb_utils import (
	is_adb_available, 
	list_devices as adb_list_devices, 
	shell as adb_shell, 
	adb_version, 
	wait_for_device,
	start_interactive_shell,
	stop_interactive_shell,
	send_shell_command,
	read_shell_output,
	is_shell_running,
	get_device_info,
	check_device_root,
	get_device_model,
	get_device_android_version
)
from cmd_utils import TerminalWidget


class CommConsole(QtWidgets.QWidget):
	"""
	Multi-protocol communication console widget.
	
	This widget provides a comprehensive communication interface supporting
	multiple protocols through a tabbed interface. It includes UART serial
	communication, ADB device communication, SSH support (placeholder), and
	multiple CMD terminal instances.
	
	Key Features:
	- Protocol switching with persistent state management
	- UART serial communication with full configuration options
	- ADB device discovery and communication
	- Multiple CMD terminal instances for local command execution
	- Real-time data capture and logging
	- Per-port log buffers for UART communication
	- Command batching and timing control
	
	Protocol Support:
	- UART: Serial communication with configurable baud rates, parity, etc.
	- ADB: Android device communication and shell access
	- SSH: Remote system access (placeholder for future implementation)
	- CMD: Local command execution with multiple terminal instances
	
	UI Components:
	- Protocol selector dropdown
	- Protocol-specific control panels
	- Scrollable log area with monospace font
	- Multi-line input area with keyboard shortcuts
	- Per-port log management for UART
	
	The widget is designed to be extensible, allowing new protocols to be
	added by implementing new control panels and adding them to the protocol stack.
	"""

	def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
		super().__init__(parent)
		self._build_ui()
		self._setup_uart()
		# ADB-only prompt is printed when ADB protocol is selected

	def _build_ui(self) -> None:
		v = QtWidgets.QVBoxLayout(self)
		v.setContentsMargins(0, 0, 0, 0)

		# Protocol selector row (future-proof)
		row = QtWidgets.QHBoxLayout()
		row.setContentsMargins(0, 0, 0, 0)
		row.setSpacing(8)
		row.addWidget(QtWidgets.QLabel("Protocol:"))
		self.proto_combo = QtWidgets.QComboBox()
		self.proto_combo.addItems(["UART", "SSH", "ADB", "CMD"])  # Extensible
		self.proto_combo.currentIndexChanged.connect(self._on_proto_changed)
		row.addWidget(self.proto_combo)
		row.addStretch(1)
		v.addLayout(row)

		# Protocol-specific control stack
		self.proto_stack = QtWidgets.QStackedWidget()
		v.addWidget(self.proto_stack)

		# UART controls page
		uart_controls = QtWidgets.QWidget()
		u = QtWidgets.QHBoxLayout(uart_controls)
		u.setContentsMargins(0, 0, 0, 0)
		u.setSpacing(8)
		u.addWidget(QtWidgets.QLabel("Port:"))
		self.uart_port_combo = QtWidgets.QComboBox()
		u.addWidget(self.uart_port_combo)
		# Track port changes to swap per-port logs
		self.uart_port_combo.currentTextChanged.connect(self._on_port_changed)
		u.addWidget(QtWidgets.QLabel("Baud:"))
		self.uart_baud = QtWidgets.QComboBox()
		self.uart_baud.addItems(["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"]) 
		self.uart_baud.setCurrentText("921600")
		u.addWidget(self.uart_baud)
		u.addWidget(QtWidgets.QLabel("Data:"))
		self.uart_databits = QtWidgets.QComboBox()
		self.uart_databits.addItems(["7", "8"]) 
		self.uart_databits.setCurrentText("8")
		u.addWidget(self.uart_databits)
		u.addWidget(QtWidgets.QLabel("Parity:"))
		self.uart_parity = QtWidgets.QComboBox()
		self.uart_parity.addItems(["None", "Even", "Odd"]) 
		u.addWidget(self.uart_parity)
		u.addWidget(QtWidgets.QLabel("Stop:"))
		self.uart_stop = QtWidgets.QComboBox()
		self.uart_stop.addItems(["1", "1.5", "2"]) 
		u.addWidget(self.uart_stop)
		u.addWidget(QtWidgets.QLabel("Flow:"))
		self.uart_flow = QtWidgets.QComboBox()
		self.uart_flow.addItems(["None", "RTS/CTS", "XON/XOFF"]) 
		u.addWidget(self.uart_flow)
		u.addStretch(1)
		# Clear current port session (placed left of Connect)
		self.uart_clear_btn = QtWidgets.QPushButton("Clear")
		self.uart_clear_btn.setToolTip("Clear only this port's session")
		self.uart_clear_btn.clicked.connect(self._on_uart_clear)
		u.addWidget(self.uart_clear_btn)
		# Stop button to interrupt running commands (Ctrl+C)
		self.uart_stop_btn = QtWidgets.QPushButton("Stop")
		self.uart_stop_btn.setToolTip("Stop/interrupt running command (Ctrl+C)")
		self.uart_stop_btn.clicked.connect(self._on_uart_stop)
		self.uart_stop_btn.setEnabled(False)  # Disabled until connected
		u.addWidget(self.uart_stop_btn)
		self.uart_connect_btn = QtWidgets.QPushButton("Connect")
		self.uart_connect_btn.setCheckable(True)
		self.uart_connect_btn.toggled.connect(self._on_uart_connect_toggle)
		u.addWidget(self.uart_connect_btn)
		self.proto_stack.addWidget(uart_controls)

		# SSH controls page (placeholder)
		ssh_controls = QtWidgets.QWidget()
		ssh = QtWidgets.QHBoxLayout(ssh_controls)
		ssh.setContentsMargins(0, 0, 0, 0)
		ssh.setSpacing(8)
		ssh.addWidget(QtWidgets.QLabel("SSH Host:"))
		self.ssh_host = QtWidgets.QLineEdit()
		self.ssh_host.setPlaceholderText("hostname or ip")
		ssh.addWidget(self.ssh_host)
		ssh.addWidget(QtWidgets.QLabel("User:"))
		self.ssh_user = QtWidgets.QLineEdit()
		ssh.addWidget(self.ssh_user)
		ssh.addWidget(QtWidgets.QLabel("Port:"))
		self.ssh_port = QtWidgets.QSpinBox()
		self.ssh_port.setRange(1, 65535)
		self.ssh_port.setValue(22)
		ssh.addWidget(self.ssh_port)
		ssh.addStretch(1)
		self.btn_ssh_connect = QtWidgets.QPushButton("Connect (todo)")
		self.btn_ssh_connect.setEnabled(False)
		ssh.addWidget(self.btn_ssh_connect)
		self.proto_stack.addWidget(ssh_controls)

		# ADB controls page (placeholder)
		adb_controls = QtWidgets.QWidget()
		adb = QtWidgets.QHBoxLayout(adb_controls)
		adb.setContentsMargins(0, 0, 0, 0)
		adb.setSpacing(8)
		adb.addWidget(QtWidgets.QLabel("ADB Device:"))
		self.adb_device_combo = QtWidgets.QComboBox()
		self.adb_device_combo.setMinimumWidth(220)
		adb.addWidget(self.adb_device_combo)
		self.btn_adb_refresh = QtWidgets.QPushButton("Refresh")
		self.btn_adb_refresh.clicked.connect(self._refresh_adb_devices)
		adb.addWidget(self.btn_adb_refresh)
		adb.addStretch(1)
		self.btn_adb_connect = QtWidgets.QPushButton("Connect")
		self.btn_adb_connect.setCheckable(True)
		self.btn_adb_connect.toggled.connect(self._on_adb_connect_toggle)
		self.btn_adb_connect.setEnabled(is_adb_available())
		adb.addWidget(self.btn_adb_connect)
		self.proto_stack.addWidget(adb_controls)

		# CMD terminals page (3 tabs, independent terminals) - lazy init
		cmd_page = QtWidgets.QWidget()
		cmd_layout = QtWidgets.QVBoxLayout(cmd_page)
		cmd_layout.setContentsMargins(0, 0, 0, 0)
		self.cmd_tabs = QtWidgets.QTabWidget()
		self.cmd_terms: List[TerminalWidget] = []  # type: ignore[var-annotated]
		placeholder = QtWidgets.QLabel("CMD terminals will start when selected…")
		placeholder.setAlignment(QtCore.Qt.AlignCenter)
		cmd_layout.addWidget(self.cmd_tabs)
		cmd_layout.addWidget(placeholder)
		self._cmd_placeholder = placeholder
		self.proto_stack.addWidget(cmd_page)

		# Distinct output log and input box
		self.log = QtWidgets.QPlainTextEdit()
		self.log.setReadOnly(True)
		self.log.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
		mono = QtGui.QFont("Consolas", 10)
		self.log.setFont(mono)
		self.log.setMinimumHeight(260)
		v.addWidget(self.log, 1)

		self.input = QtWidgets.QPlainTextEdit()
		self.input.setPlaceholderText("Type and press Enter to send. Shift+Enter for newline.")
		self.input.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
		self.input.setFixedHeight(60)
		self.input.installEventFilter(self)
		v.addWidget(self.input, 0)

	def _setup_uart(self) -> None:
		self._serial = None  # type: ignore[assignment]
		self._poll = QtCore.QTimer(self)
		self._poll.setInterval(50)  # Reduced from 100ms to 50ms for faster response with long outputs
		self._poll.timeout.connect(self._poll_uart)
		# Per-port log buffers and current port pointer
		self._port_logs = {}  # type: ignore[var-annotated]
		self._current_port = ""
		# UART capture mode (used to fetch snapshots without polluting console)
		self._capture_active = False
		self._capture_buffer = ""
		self._capture_end_token = None
		self._capture_callback = None
		self._capture_timeout = QtCore.QTimer(self)
		self._capture_timeout.setSingleShot(True)
		self.refresh_ports()
		# Default SOC USB identifier (Windows hwid format substring)
		self._soc_port_id = "VID:PID=067B:23A3"
		# ADB runtime state
		self._adb_connected = False
		self._adb_serial = None
		self._adb_shell_process = None
		self._adb_shell_timer = None  # type: ignore[assignment]
		self._adb_cmd_timer = QtCore.QTimer(self)
		self._adb_cmd_timer.setSingleShot(True)
		# Initialize ADB shell timer for interactive sessions
		self._adb_shell_timer = QtCore.QTimer(self)
		self._adb_shell_timer.setInterval(50)  # Check every 50ms for output
		self._adb_shell_timer.timeout.connect(self._check_adb_shell_output)
		# Populate ADB devices initially so the page shows data when selected
		self._refresh_adb_devices()

	def _on_proto_changed(self) -> None:
		idx = self.proto_combo.currentIndex()
		print(f"[DEBUG] Protocol changed to index: {idx}")
		self.proto_stack.setCurrentIndex(idx)
		# Clear only the input area, preserve log history
		if hasattr(self, 'input'):
			self.input.clear()
		# Don't clear the log - preserve UART console history
		# When switching protocols, disconnect UART and clear settings
		if idx != 0:
			print(f"[DEBUG] Switching to non-UART protocol")
			self._uart_disconnect_if_needed()
			self._reset_uart_controls(clear_ports=False)
			# Disable Stop button when not on UART protocol
			if hasattr(self, 'uart_stop_btn'):
				self.uart_stop_btn.setEnabled(False)
		else:
			print(f"[DEBUG] Switching to UART protocol")
			# Selected UART: reset and repopulate fresh
			self._reset_uart_controls(clear_ports=True)
			self.refresh_ports()
			port = self.uart_port_combo.currentText()
			print(f"[DEBUG] Calling _on_port_changed with port: {port}")
			self._on_port_changed(port)
			# Update Stop button state based on connection status
			if hasattr(self, 'uart_stop_btn'):
				self.uart_stop_btn.setEnabled(self._serial is not None and hasattr(self._serial, 'is_open') and self._serial.is_open)
		# When switching to ADB, refresh device list (no host prompt)
		if idx == 2:
			self._refresh_adb_devices()
		# Lazy-create CMD terminals when selected
		if idx == 3 and not getattr(self, 'cmd_terms', []):
			try:
				for i in range(3):
					term = TerminalWidget(parent=self)
					self.cmd_terms.append(term)
					self.cmd_tabs.addTab(term, f"CMD {i+1}")
				if hasattr(self, '_cmd_placeholder') and self._cmd_placeholder is not None:
					self._cmd_placeholder.setVisible(False)
			except Exception:
				pass
		# Apply shared console visibility and focus handling
		self._apply_protocol_ui_state(idx)

	def _apply_protocol_ui_state(self, idx: int) -> None:
		"""Show/hide the shared log/input and manage focus when switching.

		This fixes a UI glitch where the shared console sometimes stays hidden
		after returning from CMD. Force geometry updates so the widgets expand
		back to their intended sizes.
		"""
		try:
			is_cmd = (idx == 3)
			if hasattr(self, 'log'):
				self.log.setVisible(not is_cmd)
			if hasattr(self, 'input'):
				self.input.setVisible(not is_cmd)
			# Resize behavior: for CMD let the stack expand; for others keep it compact
			if hasattr(self, 'proto_stack'):
				if is_cmd:
					self.proto_stack.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
					self.proto_stack.setMinimumHeight(0)
					self.proto_stack.setMaximumHeight(16777215)  # reset cap
				else:
					self.proto_stack.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
					current = self.proto_stack.currentWidget()
					try:
						h = current.sizeHint().height() if current is not None else self.proto_stack.sizeHint().height()
						# Guard against zero-height hints
						h = max(44, h)
					except Exception:
						h = 64
					self.proto_stack.setMinimumHeight(h)
					self.proto_stack.setMaximumHeight(h)
				self.proto_stack.updateGeometry()
				lay = self.layout()
				if lay is not None:
					lay.invalidate()
			# When showing shared console again, ensure it regains space and focus
			if not is_cmd and hasattr(self, 'log') and hasattr(self, 'input'):
				self.log.setMinimumHeight(260)
				self.log.updateGeometry()
				self.input.setFixedHeight(60)
				self.input.updateGeometry()
				self.input.setFocus()
			# When entering CMD, focus the first terminal input if available
			if is_cmd:
				try:
					if getattr(self, 'cmd_terms', None):
						term = self.cmd_terms[0]
						if hasattr(term, 'input'):
							term.input.setFocus()
				except Exception:
					pass
		except Exception:
			pass

	def refresh_ports(self) -> None:
		"""Refresh UART ports list."""
		try:
			from serial.tools import list_ports
			ports = [p.device for p in list_ports.comports()] or ["COM1"]
			self.uart_port_combo.clear()
			self.uart_port_combo.addItems(ports)
		except Exception:
			self.uart_port_combo.clear()
			self.uart_port_combo.addItems(["COM1"]) 

	# ===== ADB integration =====
	def _refresh_adb_devices(self) -> None:
		"""Refresh the list of ADB devices in the combo box."""
		try:
			self.adb_device_combo.clear()
			if not is_adb_available():
				self.adb_device_combo.addItem("adb not found")
				self.btn_adb_connect.setEnabled(False)
				return
			devs = adb_list_devices()
			if not devs:
				self.adb_device_combo.addItem("No devices")
				self.btn_adb_connect.setEnabled(False)
			else:
				for serial, label in devs:
					self.adb_device_combo.addItem(label, serial)
				self.btn_adb_connect.setEnabled(True)
		except Exception:
			try:
				self.adb_device_combo.addItem("Error listing devices")
				self.btn_adb_connect.setEnabled(False)
			except Exception:
				pass

	def _on_adb_connect_toggle(self, checked: bool) -> None:
		"""Connect/disconnect ADB logical session (tracks selected serial)."""
		if checked:
			try:
				idx = self.adb_device_combo.currentIndex()
				serial = self.adb_device_combo.itemData(idx)
				if not serial or isinstance(serial, str) and serial.lower() in ("no devices", "adb not found"):
					raise RuntimeError("No ADB device selected")
				
				# Wait for device to be ready
				code, out, err = wait_for_device(serial)
				if code != 0:
					raise RuntimeError(err or out or "Failed waiting for device")
				
				# Simple version probe
				_ = adb_version()
				
				# Start interactive ADB shell session using utility function
				print(f"[DEBUG] Starting ADB shell for device: {serial}")
				success, process, message = start_interactive_shell(serial)
				
				if not success:
					raise RuntimeError(message)
				
				print(f"[DEBUG] {message}")
				self._adb_shell_process = process
				self._adb_connected = True
				self._adb_serial = serial
				self.btn_adb_connect.setText("Disconnect")
				
				# Start the output checking timer
				self._adb_shell_timer.start()
				
				# Show connection message and device info
				if hasattr(self, 'log'):
					self.log.appendPlainText(f"[ADB] Connected to {serial}")
					self.log.appendPlainText("[ADB] Starting interactive shell...")
					
					# Get and display device information
					model = get_device_model(serial)
					version = get_device_android_version(serial)
					root_status = "Root" if check_device_root(serial) else "No Root"
					
					if model or version:
						info_parts = []
						if model:
							info_parts.append(f"Model: {model}")
						if version:
							info_parts.append(f"Android: {version}")
						info_parts.append(root_status)
						self.log.appendPlainText(f"[ADB] Device Info: {', '.join(info_parts)}")
				
				# Wait for initial prompt
				print("[DEBUG] Waiting for initial ADB shell prompt...")
				for attempt in range(5):
					success, stdout, stderr = read_shell_output(self._adb_shell_process)
					if success and (stdout or stderr):
						print(f"[DEBUG] Data received on attempt {attempt + 1}: stdout={repr(stdout)}, stderr={repr(stderr)}")
						self._on_adb_shell_output()
					else:
						print(f"[DEBUG] No data ready on attempt {attempt + 1}")
					
					# Small delay between attempts
					import time
					time.sleep(0.2)
				
				print("[DEBUG] Finished waiting for initial ADB shell prompt")
				
			except Exception as e:
				print(f"[DEBUG] ADB connect error: {e}")
				QtWidgets.QMessageBox.critical(self, "ADB Connect Failed", str(e))
				self.btn_adb_connect.setChecked(False)
		else:
			# Disconnect ADB shell using utility function
			if self._adb_shell_process:
				print("[DEBUG] Stopping ADB shell process")
				success, message = stop_interactive_shell(self._adb_shell_process)
				print(f"[DEBUG] {message}")
				self._adb_shell_process = None
			
			if self._adb_shell_timer:
				self._adb_shell_timer.stop()
			
			self._adb_connected = False
			self._adb_serial = None
			self.btn_adb_connect.setText("Connect")
			if hasattr(self, 'log'):
				self.log.appendPlainText("[ADB] Disconnected")

	def _on_adb_shell_output(self) -> None:
		"""Handle output from ADB shell process."""
		try:
			if self._adb_shell_process is None:
				return
			
			# Check if process is still running using utility function
			if not is_shell_running(self._adb_shell_process):
				return
			
			# Read output using utility function
			success, stdout, stderr = read_shell_output(self._adb_shell_process)
			
			if not success:
				print(f"[DEBUG] Failed to read ADB shell output: {stderr}")
				return
			
			# Process stdout
			if stdout:
				print(f"[DEBUG] ADB shell stdout: {repr(stdout)}")
				
				# Check if this looks like an Android shell prompt
				if self._detect_android_prompt(stdout):
					print(f"[DEBUG] Android prompt detected in ADB shell stdout: {repr(stdout)}")
				
				# Display the text immediately
				if hasattr(self, 'log'):
					self.log.moveCursor(QtGui.QTextCursor.End)
					self.log.insertPlainText(stdout)
					self.log.moveCursor(QtGui.QTextCursor.End)
					self.log.repaint()
			
			# Process stderr
			if stderr:
				print(f"[DEBUG] ADB shell stderr: {repr(stderr)}")
				
				# Check if this looks like an Android shell prompt
				if self._detect_android_prompt(stderr):
					print(f"[DEBUG] Android prompt detected in ADB shell stderr: {repr(stderr)}")
				
				# Display the text immediately
				if hasattr(self, 'log'):
					self.log.moveCursor(QtGui.QTextCursor.End)
					self.log.insertPlainText(stderr)
					self.log.moveCursor(QtGui.QTextCursor.End)
					self.log.repaint()
					
		except Exception as e:
			print(f"[DEBUG] Error in _on_adb_shell_output: {e}")

	def _check_adb_shell_output(self) -> None:
		"""Periodically check for ADB shell output to catch any missed prompts."""
		try:
			if self._adb_shell_process is not None and is_shell_running(self._adb_shell_process):
				# Use utility function to check for output
				success, stdout, stderr = read_shell_output(self._adb_shell_process)
				
				if success and (stdout or stderr):
					print(f"[DEBUG] ADB shell timer check - found output: stdout={repr(stdout)}, stderr={repr(stderr)}")
					self._on_adb_shell_output()
				
				# Force a repaint to ensure any buffered output is displayed
				if hasattr(self, 'log'):
					self.log.repaint()
		except Exception as e:
			print(f"[DEBUG] Error in _check_adb_shell_output: {e}")

	def _on_adb_shell_finished(self, exit_code: int, exit_status: int) -> None:
		"""Handle ADB shell process finished."""
		print(f"[DEBUG] ADB shell process finished with code: {exit_code}, status: {exit_status}")
		if hasattr(self, 'log'):
			self.log.appendPlainText(f"\n[ADB] Shell session ended (exit code: {exit_code})")
		# Reset connection state
		self._adb_connected = False
		self._adb_serial = None
		self.btn_adb_connect.setChecked(False)
		self.btn_adb_connect.setText("Connect")
		if self._adb_shell_timer:
			self._adb_shell_timer.stop()

	def _detect_android_prompt(self, text: str) -> bool:
		"""Detect if the text contains an Android shell prompt."""
		import re
		prompt_patterns = [
			r'root@[^:]+:/#\s*$',  # root@device:/#
			r'[^@]+@[^:]+:/#\s*$',  # user@device:/#
			r'#\s*$',               # Just #
			r'\$\s*$',              # Just $
			r'root@[^:]+:/#\s*',    # root@device:/# (with content after)
			r'[^@]+@[^:]+:/#\s*',   # user@device:/# (with content after)
			r'root@[^:]+:/#',       # root@device:/# (anywhere in text)
			r'[^@]+@[^:]+:/#',      # user@device:/# (anywhere in text)
			r'^#\s*',               # # at start of line
			r'^\$\s*',              # $ at start of line
			r'#\s*$',               # # at end of line
			r'\$\s*$',              # $ at end of line
		]
		
		for pattern in prompt_patterns:
			if re.search(pattern, text, re.MULTILINE):
				print(f"[DEBUG] ADB prompt pattern matched: {pattern}")
				return True
		return False

		# ===== UART handlers (class methods) =====
	def _on_uart_connect_toggle(self, checked: bool) -> None:
		if checked:
			port = self.uart_port_combo.currentText()
			try:
				import serial
				baud = int(self.uart_baud.currentText() or 921600)
				bytesize = serial.SEVENBITS if self.uart_databits.currentText() == "7" else serial.EIGHTBITS
				parity_map = {"None": serial.PARITY_NONE, "Even": serial.PARITY_EVEN, "Odd": serial.PARITY_ODD}
				parity = parity_map.get(self.uart_parity.currentText(), serial.PARITY_NONE)
				stop_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE, "2": serial.STOPBITS_TWO}
				stopbits = stop_map.get(self.uart_stop.currentText(), serial.STOPBITS_ONE)
				rx = self.uart_flow.currentText()
				rtscts = (rx == "RTS/CTS")
				xonxoff = (rx == "XON/XOFF")
				self._serial = serial.Serial(port=port, baudrate=baud, bytesize=bytesize, parity=parity, stopbits=stopbits, rtscts=rtscts, xonxoff=xonxoff, timeout=0)
				self.uart_connect_btn.setText("Disconnect")
				self.uart_stop_btn.setEnabled(True)  # Enable Stop button when connected
				self._poll.start()
				self._current_port = port
				self._port_logs.setdefault(port, "")
				self._current_port = port
				if hasattr(self, 'log'):
					self.log.setPlainText(self._port_logs.get(port, ""))
					self.log.moveCursor(QtGui.QTextCursor.End)
			except ImportError:
				QtWidgets.QMessageBox.critical(
					self,
					"Serial Module Missing",
					"pyserial is not installed. Install it with:\n\n  python -m pip install pyserial\n\nThen restart the app."
				)
				self.uart_connect_btn.setChecked(False)
			except Exception as e:
				msg = str(e)
				if isinstance(e, PermissionError) or "access is denied" in msg.lower() or "busy" in msg.lower() or "resource busy" in msg.lower():
					msg = f"Port {port} is busy or access is denied. Close other apps and try again.\n\nDetails: {str(e)}"
				elif isinstance(e, FileNotFoundError) or "no such file" in msg.lower() or "cannot find the file" in msg.lower():
					msg = f"Port {port} was not found. Check the device and try again.\n\nDetails: {str(e)}"
				QtWidgets.QMessageBox.critical(self, "Open Port Failed", msg)
				self.uart_connect_btn.setChecked(False)
		else:
			self._poll.stop()
			try:
				if self._serial is not None:
					self._serial.close()
					self._serial = None
			except Exception:
				pass
			self.uart_connect_btn.setText("Connect")
			self.uart_stop_btn.setEnabled(False)  # Disable Stop button when disconnected

	def _on_uart_clear(self) -> None:
		try:
			port = self.uart_port_combo.currentText()
			self._port_logs[port] = ""
			if hasattr(self, 'log'):
				self.log.clear()
		except Exception:
			pass

	def _on_uart_stop(self) -> None:
		"""Send Ctrl+C (interrupt signal) to stop/interrupt a running command."""
		try:
			if self._serial is not None and self._serial.is_open:
				# Send Ctrl+C (ASCII code 3, interrupt signal)
				self._serial.write(b'\x03')
				# Also send a newline to ensure the interrupt is processed
				self._serial.write(b'\n')
				port = self.uart_port_combo.currentText()
				if hasattr(self, 'log'):
					self.log.appendPlainText("\n[Interrupt] Ctrl+C sent to stop command")
					self.log.moveCursor(QtGui.QTextCursor.End)
		except Exception as e:
			print(f"[DEBUG] Error sending interrupt signal: {e}")
			if hasattr(self, 'log'):
				self.log.appendPlainText(f"\n[Error] Failed to send interrupt: {e}")

	def find_linux_port(self, soc_port_id: Optional[str] = None) -> Optional[str]:
		try:
			from serial.tools import list_ports
			needle = (soc_port_id or self._soc_port_id).strip()
			candidates: List[str] = []
			for p in list_ports.comports():
				try:
					hwid = getattr(p, 'hwid', '') or ''
					if needle and needle in hwid:
						candidates.append(p.device)
				except Exception:
					pass
			if not candidates:
				return None
			def _com_num(name: str) -> int:
				import re
				m = re.search(r"COM(\d+)$", name.upper())
				return int(m.group(1)) if m else 1_000_000
			candidates.sort(key=_com_num)
			return candidates[0]
		except Exception:
			return None

	def connect_to_port(self, port: str, baud: int = 921600) -> bool:
		try:
			idx = self.uart_port_combo.findText(port)
			if idx < 0:
				self.uart_port_combo.addItem(port)
				idx = self.uart_port_combo.findText(port)
			self.uart_port_combo.setCurrentIndex(max(0, idx))
			self.uart_baud.setCurrentText(str(int(baud)))
			self.uart_connect_btn.setChecked(True)
			return bool(self._serial)
		except Exception as e:
			QtWidgets.QMessageBox.critical(self, "Open Port Failed", str(e))
			return False

	def send_commands(self, commands: List[str], spacing_ms: int = 300, on_complete: Optional[Callable[[], None]] = None) -> None:
		if not commands:
			if on_complete:
				on_complete()
			return
		queue = list(commands)
		timer = QtCore.QTimer(self)
		timer.setInterval(max(50, int(spacing_ms)))
		def _flush_next():
			if not queue:
				timer.stop()
				if on_complete:
					on_complete()
				return
			cmd = queue.pop(0)
			try:
				if self._serial is not None:
					self._serial.write((cmd + "\n").encode())
					port = self.uart_port_combo.currentText()
					self._port_logs[port] = self._port_logs.get(port, "") + cmd + "\n"
					if hasattr(self, 'log'):
						self.log.moveCursor(QtGui.QTextCursor.End)
						self.log.insertPlainText(cmd + "\n")
						self.log.moveCursor(QtGui.QTextCursor.End)
			except Exception:
				pass
		timer.timeout.connect(_flush_next)
		timer.start()
		_flush_next()

	def disconnect_serial(self) -> None:
		self._uart_disconnect_if_needed()

	def _uart_disconnect_if_needed(self) -> None:
		if self.uart_connect_btn.isChecked():
			self.uart_connect_btn.setChecked(False)

	def _on_port_changed(self, port: str) -> None:
		try:
			prev = getattr(self, '_current_port', '')
			if prev and hasattr(self, 'log'):
				self._port_logs[prev] = self.log.toPlainText()
			self._current_port = port
			if hasattr(self, 'log'):
				# Don't replace the entire log content - preserve existing content
				# Only append port-specific content if it exists and is different
				port_content = self._port_logs.get(port, "")
				current_content = self.log.toPlainText()
				
				print(f"[DEBUG] Port changed to: {port}")
				print(f"[DEBUG] Port content length: {len(port_content)}")
				print(f"[DEBUG] Current content length: {len(current_content)}")
				
				# If switching to a port that has content and current log is empty, load port content
				if port_content and not current_content.strip():
					print(f"[DEBUG] Loading port content (log was empty)")
					self.log.setPlainText(port_content)
				# If port has content and it's not already in the current log, append it
				elif port_content and port_content not in current_content:
					print(f"[DEBUG] Appending port content")
					self.log.appendPlainText(f"\n--- Port {port} Content ---")
					self.log.appendPlainText(port_content)
				# If port has no content but current log has content, keep current content
				elif not port_content and current_content.strip():
					print(f"[DEBUG] Port has no content, keeping current content")
					# Do nothing - keep current content
				else:
					print(f"[DEBUG] No port content to load/append")
				
				self.log.moveCursor(QtGui.QTextCursor.End)
		except Exception as e:
			print(f"[DEBUG] Error in _on_port_changed: {e}")
			pass

	def _strip_ansi_codes(self, text: str) -> str:
		"""Remove ANSI escape sequences from text.
		
		This function removes ANSI escape codes that are used for terminal
		formatting (colors, cursor positioning, etc.) so they don't appear
		as visible characters in the UART console.
		
		Handles both standard ANSI codes (with ESC character) and corrupted
		sequences where the ESC character may be missing during UART transmission.
		"""
		# First, remove standard ANSI escape sequences with ESC character
		ansi_pattern = re.compile(
			# Standard CSI sequences: ESC[ followed by optional parameters and command
			r'\x1b\[[0-9;]*[a-zA-Z]'
			# SGR sequences (colors/formatting): ESC[ ... m
			r'|\x1b\[[0-9;]*m'
			# Erase sequences: ESC[ ... J or ESC[ ... K
			r'|\x1b\[[0-9]*[JK]'
			# Cursor movement: ESC[ ... A-H
			r'|\x1b\[[0-9]*[ABCDEFGH]'
			# Cursor position: ESC[ ... H or ESC[ ... f
			r'|\x1b\[[0-9;]*[Hf]'
			# Mode sequences: ESC[ ... h or ESC[ ... l
			r'|\x1b\[[?0-9]*[hl]'
			# Scroll region: ESC[ ... r
			r'|\x1b\[[0-9]*[r]'
			# OSC sequences: ESC] ... BEL
			r'|\x1b\][^\x07]*\x07'
			# ESC followed by single char commands (without [)
			r'|\x1b[=<>?\(\)]'
		)
		cleaned = ansi_pattern.sub('', text)
		
		# Second pass: Remove corrupted/malformed sequences where ESC is missing
		# These patterns match common ANSI code formats that appear without ESC
		# Be more specific to avoid false matches with legitimate text
		corrupted_pattern = re.compile(
			# Color/formatting codes: [number;numberm or [numberm
			r'\[[0-9]+(;[0-9]+)*m'
			# Erase commands: [numberK or [K or [numberJ or [J
			r'|\[[0-9]*[JK]'
			# Cursor position: [number;numberH or [H or [number;numberf or [f
			r'|\[[0-9]*(;[0-9]+)*[Hf]'
			# Mode sequences: [?numberh or [?numberl or [numberh or [numberl
			r'|\[[?]?[0-9]*[hl]'
			# Cursor movement: [numberA through [numberH
			r'|\[[0-9]*[ABCDEFGH]'
			# Scroll region: [number;numberr or [numberr
			r'|\[[0-9]*(;[0-9]+)*r'
			# Common corrupted patterns seen in UART output
			r'|\[m[0-9]+'  # [m followed by numbers (corrupted reset + code)
			r'|\[[0-9]+\['  # [number[ (nested/corrupted)
		)
		cleaned = corrupted_pattern.sub('', cleaned)
		
		return cleaned

	def _clean_uart_text(self, text: str) -> str:
		"""Clean UART text by removing ANSI codes, non-printable characters, and invalid UTF-8 replacements.
		
		This function:
		1. Removes ANSI escape sequences
		2. Removes Unicode replacement characters (U+FFFD - question marks in boxes)
		3. Filters out non-printable control characters (except common ones like \n, \r, \t)
		4. Removes other problematic characters
		"""
		# First strip ANSI codes
		cleaned = self._strip_ansi_codes(text)
		
		# Remove Unicode replacement character (U+FFFD) - appears as or ? in boxes
		cleaned = cleaned.replace('\ufffd', '')
		cleaned = cleaned.replace('\uFFFD', '')
		
		# Remove other common problematic Unicode characters
		# Zero-width characters
		cleaned = cleaned.replace('\u200b', '')  # Zero-width space
		cleaned = cleaned.replace('\u200c', '')  # Zero-width non-joiner
		cleaned = cleaned.replace('\u200d', '')  # Zero-width joiner
		cleaned = cleaned.replace('\ufeff', '')  # Zero-width no-break space
		
		# Filter out non-printable control characters except common ones
		# Keep: \n (newline), \r (carriage return), \t (tab)
		result = []
		for char in cleaned:
			code = ord(char)
			# Skip Unicode replacement character (U+FFFD = 65533) - question mark in box
			if code == 0xFFFD:
				continue
			
			# Keep common control characters
			if code == 10 or code == 13 or code == 9:  # \n, \r, \t
				result.append(char)
			# Keep printable ASCII (32-126)
			elif code >= 32 and code <= 126:
				result.append(char)
			# Keep extended ASCII (128-255) - may include accented characters
			elif code >= 128 and code <= 255:
				result.append(char)
			# For Unicode characters above 255
			elif code > 255:
				# Skip private use area and other non-printable ranges
				if code >= 0xE000 and code <= 0xF8FF:  # Private Use Area
					continue
				elif code >= 0xF900 and code <= 0xFAFF:  # CJK Compatibility Ideographs (may cause issues)
					continue
				# Keep printable Unicode characters
				if char.isprintable() or char.isspace():
					result.append(char)
		
		return ''.join(result)

	def _reset_uart_controls(self, clear_ports: bool) -> None:
		if clear_ports:
			self.uart_port_combo.clear()
		self.uart_baud.setCurrentText("921600")
		self.uart_databits.setCurrentText("8")
		self.uart_parity.setCurrentText("None")
		self.uart_stop.setCurrentText("1")
		self.uart_flow.setCurrentText("None")

	def _poll_uart(self) -> None:
		try:
			if self._serial is not None:
				# Read all available data in a loop to handle long outputs
				# This ensures we capture all data even if it arrives faster than polling
				max_reads_per_poll = 100  # Prevent infinite loop
				read_count = 0
				total_text = ""
				
				while self._serial.in_waiting > 0 and read_count < max_reads_per_poll:
					# Read available bytes (up to 64KB per read to handle large outputs)
					bytes_to_read = min(self._serial.in_waiting, 65536)
					data = self._serial.read(bytes_to_read)
					if data:
						try:
							# Try UTF-8 first, then fallback to latin-1 (which can decode any byte)
							# This avoids replacement characters from invalid UTF-8
							try:
								text = data.decode('utf-8', errors='ignore')
							except Exception:
								# If UTF-8 fails completely, try latin-1 (maps bytes 0-255 directly)
								text = data.decode('latin-1', errors='ignore')
							total_text += text
						except Exception:
							# Last resort: decode as latin-1 which always works
							try:
								text = data.decode('latin-1', errors='ignore')
								total_text += text
							except Exception:
								# If all else fails, skip this data
								pass
					read_count += 1
					
					# Small delay to allow more data to arrive if streaming
					if self._serial.in_waiting > 0 and read_count < max_reads_per_poll:
						import time
						time.sleep(0.001)  # 1ms delay to allow buffer to fill
				
				# Update UI with all collected text at once (more efficient)
				if total_text:
					# Clean text: remove ANSI codes, non-printable chars, and invalid UTF-8 replacements
					cleaned_text = self._clean_uart_text(total_text)
					port = self.uart_port_combo.currentText()
					# Store cleaned text in port logs
					self._port_logs[port] = self._port_logs.get(port, "") + cleaned_text
					if hasattr(self, 'log'):
						self.log.moveCursor(QtGui.QTextCursor.End)
						self.log.insertPlainText(cleaned_text)
						self.log.moveCursor(QtGui.QTextCursor.End)
		except Exception as e:
			self._poll.stop()
			try:
				if self._serial is not None:
					self._serial.close()
					self._serial = None
			except Exception:
				pass
			QtWidgets.QMessageBox.warning(self, "Serial Disconnected", f"Serial port error: {str(e)}\nThe connection has been closed.")
			self.uart_connect_btn.setChecked(False)
			self.uart_stop_btn.setEnabled(False)  # Disable Stop button on error/disconnect

	def _on_send(self) -> None:
		# Get all lines from input (support multiple commands like TeraTerm)
		input_text = self.input.toPlainText() if hasattr(self, 'input') else ""
		if not input_text:
			return
		
		# Split into lines and filter out empty lines
		lines = [line.strip() for line in input_text.split("\n") if line.strip()]
		if not lines:
			if hasattr(self, 'input'):
				self.input.clear()
			return
		
		try:
			idx = self.proto_combo.currentIndex() if hasattr(self, 'proto_combo') else 0
			# Only handle UART and ADB here; CMD terminals have their own handler
			if idx not in (0, 2):
				if hasattr(self, 'input'):
					self.input.clear()
				return
			serial_obj = getattr(self, '_serial', None)
			if idx == 0 and serial_obj is not None:
				# Send multiple commands sequentially with proper timing (like TeraTerm)
				if len(lines) > 1:
					# Multiple commands: send them one by one with delay
					self._send_multiple_uart_commands(lines)
				else:
					# Single command: send immediately
					msg = lines[0]
					serial_obj.write((msg + "\n").encode())
					port = self.uart_port_combo.currentText()
					self._port_logs[port] = self._port_logs.get(port, "") + msg + "\n"
					if hasattr(self, 'log'):
						self.log.moveCursor(QtGui.QTextCursor.End)
						self.log.insertPlainText(msg + "\n")
						self.log.moveCursor(QtGui.QTextCursor.End)
				if hasattr(self, 'input'):
					self.input.clear()
			elif idx == 2 and self._adb_connected:
				# For ADB, send commands one by one (handle multiple commands)
				for msg in lines:
					# Send command to interactive ADB shell using utility function
					if self._adb_shell_process and is_shell_running(self._adb_shell_process):
						print(f"[DEBUG] Sending command to ADB shell: {msg}")
						# Echo the command to the terminal (like a real terminal does)
						if hasattr(self, 'log'):
							self.log.appendPlainText(f"$ {msg}")
						
						# Send the command using utility function
						success, message = send_shell_command(self._adb_shell_process, msg)
						if success:
							print(f"[DEBUG] Command sent successfully: {message}")
						else:
							print(f"[DEBUG] Failed to send command: {message}")
						
						# Small delay between commands for ADB
						if len(lines) > 1:
							import time
							time.sleep(0.2)
					else:
						print("[DEBUG] ADB shell process not running, falling back to single command")
						# Fallback to single command execution
						serial = self._adb_serial
						code, out, err = adb_shell(serial, msg)
						if hasattr(self, 'log'):
							self.log.moveCursor(QtGui.QTextCursor.End)
							self.log.insertPlainText((out + ("\n" if out else "")) or "")
							if err:
								self.log.insertPlainText((err + "\n"))
							self.log.moveCursor(QtGui.QTextCursor.End)
						# Small delay between commands for ADB
						if len(lines) > 1:
							import time
							time.sleep(0.2)
				if hasattr(self, 'input'):
					self.input.clear()
		except Exception:
			# Suppress errors from stray focus or non-UART contexts
			pass

	def eventFilter(self, source, event):  # type: ignore[override]
		try:
			if hasattr(self, 'input') and source is self.input and isinstance(event, QtGui.QKeyEvent):
				if event.type() == QtCore.QEvent.KeyPress:
					# Handle Ctrl+C to interrupt running command
					if event.key() == QtCore.Qt.Key_C and event.modifiers() & QtCore.Qt.ControlModifier:
						# Only send interrupt if UART protocol is selected and connected
						idx = self.proto_combo.currentIndex() if hasattr(self, 'proto_combo') else -1
						if idx == 0 and self._serial is not None:
							self._on_uart_stop()
							return True
					# Handle Enter/Return to send command
					elif event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
						if event.modifiers() & QtCore.Qt.ShiftModifier:
							self.input.insertPlainText("\n")
						else:
							self._on_send()
							return True
		except KeyboardInterrupt:
			return False
		except Exception:
			pass
		return super().eventFilter(source, event)

	def send_line_silent(self, line: str) -> None:
		"""Send a single line over UART without echoing to the UI/log buffers."""
		try:
			if self._serial is not None:
				self._serial.write((line + "\n").encode())
		except Exception:
			pass

	def _send_multiple_uart_commands(self, commands: List[str], spacing_ms: int = 200) -> None:
		"""Send multiple UART commands sequentially with proper timing and UI echo.
		
		This method sends commands one by one with a delay between them, similar to
		how TeraTerm handles pasted multiple commands. Each command is echoed to the UI.
		
		Args:
			commands: List of command strings to send
			spacing_ms: Delay in milliseconds between commands (default 200ms)
		"""
		if not commands:
			return
		
		queue = list(commands)
		timer = QtCore.QTimer(self)
		timer.setInterval(max(50, int(spacing_ms)))
		port = self.uart_port_combo.currentText()
		
		def _send_next():
			if not queue:
				timer.stop()
				return
			cmd = queue.pop(0)
			try:
				if self._serial is not None:
					self._serial.write((cmd + "\n").encode())
					# Echo command to UI
					self._port_logs[port] = self._port_logs.get(port, "") + cmd + "\n"
					if hasattr(self, 'log'):
						self.log.moveCursor(QtGui.QTextCursor.End)
						self.log.insertPlainText(cmd + "\n")
						self.log.moveCursor(QtGui.QTextCursor.End)
			except Exception as e:
				print(f"[DEBUG] Error sending command '{cmd}': {e}")
		
		timer.timeout.connect(_send_next)
		timer.start()
		_send_next()  # Send first command immediately

	def send_commands_silent(self, commands: List[str], spacing_ms: int = 300, on_complete: Optional[Callable[[], None]] = None) -> None:
		"""Send commands over UART without echoing them into the console UI."""
		if not commands:
			if on_complete:
				on_complete()
			return
		queue = list(commands)
		timer = QtCore.QTimer(self)
		timer.setInterval(max(50, int(spacing_ms)))
		def _flush_next():
			if not queue:
				timer.stop()
				if on_complete:
					on_complete()
				return
			cmd = queue.pop(0)
			self.send_line_silent(cmd)
		timer.timeout.connect(_flush_next)
		timer.start()
		_flush_next()

	def start_capture(self, end_token: str, timeout_ms: int, on_complete: Callable[[str], None]) -> None:
		"""Begin a UART capture session until end_token is seen or timeout occurs.

		Data received while capture is active is not echoed to the UI; it is
		collected into an internal buffer. When the end token arrives (token is
		included in the incoming stream), the callback is invoked with the text
		up to but not including the token.
		"""
		try:
			# Stop any existing capture and disconnect timeout signal
			self._capture_timeout.stop()
			self._capture_timeout.timeout.disconnect()
			
			self._capture_active = True
			self._capture_buffer = ""
			self._capture_end_token = end_token
			self._capture_callback = on_complete
			
			if timeout_ms > 0:
				self._capture_timeout.timeout.connect(self._on_capture_timeout)
				self._capture_timeout.start(int(timeout_ms))
		except Exception as e:
			# Fail closed (no capture)
			print(f"Capture start failed: {e}")
			self._capture_active = False
			self._capture_buffer = ""
			self._capture_end_token = None
			self._capture_callback = None

	def _on_capture_timeout(self) -> None:
		"""Timeout handler: finish capture with whatever we have."""
		try:
			cb = self._capture_callback
			text = self._capture_buffer
		finally:
			self._capture_active = False
			self._capture_buffer = ""
			self._capture_end_token = None
			self._capture_callback = None
			self._capture_timeout.stop()
		try:
			if cb:
				cb(text)
		except Exception:
			pass

	def stop_capture(self) -> None:
		"""Stop capture immediately without invoking the callback."""
		self._capture_active = False
		self._capture_buffer = ""
		self._capture_end_token = None
		self._capture_callback = None
		self._capture_timeout.stop()


