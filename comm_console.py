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

from PySide6 import QtCore, QtGui, QtWidgets

from .adb_utils import is_adb_available, list_devices as adb_list_devices, shell as adb_shell, adb_version, wait_for_device
from .cmd_utils import TerminalWidget


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
		self._poll.setInterval(100)
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
		self._adb_serial = None  # type: ignore[assignment]
		self._adb_cmd_timer = QtCore.QTimer(self)
		self._adb_cmd_timer.setSingleShot(True)
		# Populate ADB devices initially so the page shows data when selected
		self._refresh_adb_devices()

	def _on_proto_changed(self) -> None:
		idx = self.proto_combo.currentIndex()
		self.proto_stack.setCurrentIndex(idx)
		# Clear the console areas
		if hasattr(self, 'log'):
			self.log.clear()
		if hasattr(self, 'input'):
			self.input.clear()
		# When switching protocols, disconnect UART and clear settings
		if idx != 0:
			self._uart_disconnect_if_needed()
			self._reset_uart_controls(clear_ports=False)
		else:
			# Selected UART: reset and repopulate fresh
			self._reset_uart_controls(clear_ports=True)
			self.refresh_ports()
			self._on_port_changed(self.uart_port_combo.currentText())
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
				self._adb_connected = True
				self._adb_serial = serial
				self.btn_adb_connect.setText("Disconnect")
				# Show a note in log
				if hasattr(self, 'log'):
					self.log.appendPlainText(f"[ADB] Connected to {serial}")
			except Exception as e:
				QtWidgets.QMessageBox.critical(self, "ADB Connect Failed", str(e))
				self.btn_adb_connect.setChecked(False)
		else:
			self._adb_connected = False
			self._adb_serial = None
			self.btn_adb_connect.setText("Connect")
			if hasattr(self, 'log'):
				self.log.appendPlainText("[ADB] Disconnected")

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

	def _on_uart_clear(self) -> None:
		try:
			port = self.uart_port_combo.currentText()
			self._port_logs[port] = ""
			if hasattr(self, 'log'):
				self.log.clear()
		except Exception:
			pass

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
				self.log.setPlainText(self._port_logs.get(port, ""))
				self.log.moveCursor(QtGui.QTextCursor.End)
		except Exception:
			pass

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
			if self._serial is not None and self._serial.in_waiting:
				data = self._serial.read(self._serial.in_waiting)
				if data:
					try:
						text = data.decode(errors="replace")
					except Exception:
						text = str(data)
					port = self.uart_port_combo.currentText()
					self._port_logs[port] = self._port_logs.get(port, "") + text
					if hasattr(self, 'log'):
						self.log.moveCursor(QtGui.QTextCursor.End)
						self.log.insertPlainText(text)
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

	def _on_send(self) -> None:
		msg = (self.input.toPlainText().rstrip("\r\n").split("\n")[-1] if hasattr(self, 'input') else "")
		if not msg:
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
				serial_obj.write((msg + "\n").encode())
				port = self.uart_port_combo.currentText()
				self._port_logs[port] = self._port_logs.get(port, "") + msg + "\n"
				if hasattr(self, 'log'):
					self.log.moveCursor(QtGui.QTextCursor.End)
					self.log.insertPlainText("\n")
					self.log.moveCursor(QtGui.QTextCursor.End)
				if hasattr(self, 'input'):
					self.input.clear()
			elif idx == 2 and self._adb_connected:
				serial = self._adb_serial
				code, out, err = adb_shell(serial, msg)
				if hasattr(self, 'log'):
					self.log.moveCursor(QtGui.QTextCursor.End)
					self.log.insertPlainText((out + ("\n" if out else "")) or "")
					if err:
						self.log.insertPlainText((err + "\n"))
					self.log.moveCursor(QtGui.QTextCursor.End)
				if hasattr(self, 'input'):
					self.input.clear()
		except Exception:
			# Suppress errors from stray focus or non-UART contexts
			pass

	def eventFilter(self, source, event):  # type: ignore[override]
		try:
			if hasattr(self, 'input') and source is self.input and isinstance(event, QtGui.QKeyEvent):
				if event.type() == QtCore.QEvent.KeyPress and event.key() in (QtCore.Qt.Key_Return, QtCore.Qt.Key_Enter):
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


