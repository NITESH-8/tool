"""
CMD Utilities Module - Terminal Widget Implementation

This module provides a comprehensive CMD terminal widget implementation using QProcess.
It creates embedded terminal-like interfaces that behave like real command prompts
with support for both local shell commands and ADB shell sessions.

Key Features:
- Persistent shell sessions (cmd.exe on Windows, bash on Linux/Mac)
- Interactive ADB shell support with proper prompt handling
- Real-time output display with immediate updates
- Command echoing and proper terminal behavior
- Platform-specific shell detection and configuration
- Process state monitoring and error handling

Dependencies:
- PySide6: GUI framework
- platform: Platform detection
- os: Environment and path handling
- shlex: Command parsing

Author: Performance GUI Team
Version: 1.0
"""

from __future__ import annotations

from typing import Optional
import os
import platform

from PySide6 import QtCore, QtGui, QtWidgets


class TerminalWidget(QtWidgets.QWidget):
	"""
	Simple embedded CMD-like terminal using QProcess.

	Starts a hidden cmd.exe (Windows) or bash/sh (other) and wires stdin/stdout to a text view.
	Enter sends the last line; Shift+Enter inserts newline.
	
	Key Features:
	- Persistent shell sessions that behave like real terminals
	- Interactive ADB shell support with proper prompt display
	- Real-time output capture and display
	- Command echoing for better user experience
	- Platform-specific shell configuration
	- Process state monitoring and cleanup
	
	UI Components:
	- Read-only text view for output display
	- Input line edit for command entry
	- Control buttons (Run, Clear)
	- Keyboard shortcuts (Enter, Ctrl+Enter)
	"""

	def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
		"""
		Initialize the CMD terminal widget.
		
		This constructor sets up the terminal interface, initializes the shell process,
		configures the UI layout, and establishes all necessary connections for
		real-time command execution and output display.
		
		The terminal starts with a persistent shell process (cmd.exe on Windows,
		bash on Linux/Mac) and is ready to execute commands immediately.
		"""
		super().__init__(parent)
		v = QtWidgets.QVBoxLayout(self)
		v.setContentsMargins(0, 0, 0, 0)
		
		# Create the output display area
		self.view = QtWidgets.QPlainTextEdit()
		self.view.setReadOnly(True)
		self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
		mono = QtGui.QFont("Consolas", 10)
		self.view.setFont(mono)
		v.addWidget(self.view, 1)
		
		# Create the input area
		self.input = QtWidgets.QLineEdit()
		self.input.returnPressed.connect(self._send)
		v.addWidget(self.input)
		
		# Control row: Run and Clear buttons as a safety path if Enter filter fails
		ctrl = QtWidgets.QHBoxLayout()
		btn_run = QtWidgets.QPushButton("Run")
		btn_run.clicked.connect(self._send)
		ctrl.addWidget(btn_run)
		btn_clear = QtWidgets.QPushButton("Clear")
		btn_clear.clicked.connect(lambda: (self.view.clear(), self._print_prompt()))
		ctrl.addWidget(btn_clear)
		ctrl.addStretch(1)
		v.addLayout(ctrl)
		
		# Shortcut: Ctrl+Enter to run
		QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Return"), self, activated=self._send)
		
		# Initialize process and platform detection
		self.proc = None
		self._is_windows = platform.system().lower().startswith('win')
		
		# Track current working directory for header/clear prompt convenience
		try:
			if self._is_windows:
				self.cwd = os.path.expandvars("%USERPROFILE%") or os.path.expanduser("~") or "C:\\"
			else:
				self.cwd = os.path.expanduser("~") or "/"
		except Exception:
			self.cwd = "C:\\" if self._is_windows else "/"
		
		# Start a persistent shell so it behaves like a normal terminal
		self.proc = QtCore.QProcess(self)
		self.proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
		self.proc.readyReadStandardOutput.connect(self._on_out)
		self.proc.readyReadStandardError.connect(self._on_out)
		
		# Ensure the shell starts in home directory like a normal CMD window
		try:
			self.proc.setWorkingDirectory(self.cwd)
		except Exception:
			pass
		
		# Start the appropriate shell
		if self._is_windows:
			self.proc.start("cmd.exe")
		else:
			self.proc.start("bash")
		
		# Initialize ADB shell session management
		self._subproc = None
		self._in_subsession = False
		
		# Timer to periodically check for output (helps catch prompts)
		self._adb_output_timer = QtCore.QTimer(self)
		self._adb_output_timer.setInterval(100)  # Check every 100ms
		self._adb_output_timer.timeout.connect(self._check_adb_output)

	def _print_prompt(self) -> None:
		"""
		Print a platform-specific prompt header to the terminal.
		
		This method displays a welcome message and prompt similar to what
		users would see in a real terminal window. It shows system information
		and the current working directory.
		"""
		try:
			if self._is_windows:
				ver = platform.version()
				self.view.appendPlainText(f"Microsoft Windows [Version {ver}]")
				self.view.appendPlainText("(c) Microsoft Corporation. All rights reserved.")
				self.view.appendPlainText("")
				self.view.appendPlainText(self.cwd + ">")
			else:
				user = os.environ.get('USER') or os.environ.get('USERNAME') or ''
				host = platform.node()
				self.view.appendPlainText(f"{user}@{host}:{self.cwd}$")
		except Exception:
			pass

	def _on_out(self) -> None:
		"""
		Handle output from the main shell process.
		
		This method is called whenever the main shell process (cmd.exe or bash)
		produces output. It decodes the output and displays it in the terminal view.
		"""
		try:
			data = self.proc.readAllStandardOutput().data()
			if data:
				try:
					text = data.decode(errors='replace')
				except Exception:
					text = str(data)
				self.view.moveCursor(QtGui.QTextCursor.End)
				self.view.insertPlainText(text)
				self.view.moveCursor(QtGui.QTextCursor.End)
		except Exception:
			pass

	def _on_sub_out(self) -> None:
		"""
		Handle output from ADB shell subprocess.
		
		This method processes output from ADB shell sessions, ensuring that
		Android device prompts and command output are displayed immediately
		with proper formatting and error handling.
		"""
		try:
			if self._subproc is None:
				return
			
			# Check if process is still running
			if self._subproc.state() != QtCore.QProcess.Running:
				return
			
			# Read from both stdout and stderr separately to catch all output
			stdout_data = self._subproc.readAllStandardOutput().data()
			stderr_data = self._subproc.readAllStandardError().data()
			
			# Process stdout
			if stdout_data:
				try:
					text = stdout_data.decode(errors='replace')
					print(f"[DEBUG] ADB stdout received: {repr(text)}")
					
					# Check if this looks like an Android shell prompt
					if self._detect_android_prompt(text):
						print(f"[DEBUG] Android prompt detected in stdout: {repr(text)}")
					
					# Display the text immediately
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.insertPlainText(text)
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.repaint()
				except Exception as e:
					print(f"[DEBUG] Error decoding stdout: {e}")
					text = str(stdout_data)
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.insertPlainText(text)
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.repaint()
			
			# Process stderr
			if stderr_data:
				try:
					text = stderr_data.decode(errors='replace')
					print(f"[DEBUG] ADB stderr received: {repr(text)}")
					
					# Check if this looks like an Android shell prompt
					if self._detect_android_prompt(text):
						print(f"[DEBUG] Android prompt detected in stderr: {repr(text)}")
					
					# Display the text immediately
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.insertPlainText(text)
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.repaint()
				except Exception as e:
					print(f"[DEBUG] Error decoding stderr: {e}")
					text = str(stderr_data)
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.insertPlainText(text)
					self.view.moveCursor(QtGui.QTextCursor.End)
					self.view.repaint()
					
		except Exception as e:
			print(f"[DEBUG] Error in _on_sub_out: {e}")
			pass

	def _check_adb_output(self) -> None:
		"""
		Periodically check for ADB output to catch any missed prompts.
		
		This timer-based method ensures that Android device prompts and other
		output that might be missed by the standard output handlers are still
		captured and displayed to the user.
		"""
		try:
			if self._in_subsession and self._subproc is not None:
				if self._subproc.state() == QtCore.QProcess.Running:
					bytes_available = self._subproc.bytesAvailable()
					can_read_line = self._subproc.canReadLine()
					
					if bytes_available > 0 or can_read_line:
						print(f"[DEBUG] Timer check - bytes available: {bytes_available}, can read line: {can_read_line}")
					
					# Check if there's data available
					if bytes_available > 0:
						self._on_sub_out()
					# Also try to read any pending data
					if can_read_line:
						self._on_sub_out()
					# Force a repaint to ensure any buffered output is displayed
					self.view.repaint()
		except Exception as e:
			print(f"[DEBUG] Error in _check_adb_output: {e}")
			pass

	def _end_subsession(self) -> None:
		"""
		Clean up ADB shell subprocess and reset session state.
		
		This method is called when an ADB shell session ends, either by user
		command (exit) or process termination. It cleans up resources and
		returns the terminal to normal shell mode.
		"""
		try:
			if self._subproc is not None:
				self._subproc.deleteLater()
			self._subproc = None
			self._in_subsession = False
			# Stop the output checking timer
			self._adb_output_timer.stop()
			self.view.appendPlainText("\n[adb shell exited]\n")
		except Exception:
			pass

	def _detect_android_prompt(self, text: str) -> bool:
		"""
		Detect if the text contains an Android shell prompt.
		
		Args:
			text (str): Text to check for prompt patterns
			
		Returns:
			bool: True if Android prompt detected
		"""
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
				print(f"[DEBUG] Prompt pattern matched: {pattern}")
				return True
		return False

	def _send(self) -> None:
		"""
		Process and send user input to the appropriate shell.
		
		This method handles command input, routing it to either the main shell
		process or an active ADB shell session. It includes special handling
		for ADB shell commands and proper command echoing.
		"""
		msg = self.input.text() if hasattr(self, 'input') else ""
		if not msg:
			return
		try:
			line = msg.strip()
			
			# If we're in an interactive adb shell subsession, route input there
			if self._in_subsession and self._subproc is not None:
				# Echo the command to the terminal (like a real terminal does)
				self.view.appendPlainText(f"$ {line}")
				# Send the command
				self._subproc.write((line + "\n").encode())
				self._subproc.waitForBytesWritten(100)
				# Wait a moment for output to be available
				self._subproc.waitForReadyRead(100)
				self.input.clear()
				return
			
			# If user requested adb shell, spawn a dedicated interactive subsession
			try:
				import shlex
				parts = shlex.split(line)
			except Exception:
				parts = line.split()
			
			if parts and parts[0].lower() == "adb" and parts[-1].lower() == "shell":
				print(f"[DEBUG] Starting ADB shell with args: {parts[1:]}")
				self._subproc = QtCore.QProcess(self)
				self._subproc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
				self._subproc.readyReadStandardOutput.connect(self._on_sub_out)
				self._subproc.readyReadStandardError.connect(self._on_sub_out)
				self._subproc.finished.connect(lambda _c, _s: self._end_subsession())
				
				# Start adb shell exactly as typed (excluding the leading 'adb')
				self._subproc.start("adb", parts[1:])
				print(f"[DEBUG] ADB shell process started, state: {self._subproc.state()}")
				
				# Wait for the process to start
				if not self._subproc.waitForStarted(3000):  # Wait up to 3 seconds
					print("[DEBUG] Failed to start ADB shell process")
					self.view.appendPlainText("[ERROR] Failed to start adb shell\n")
					self._subproc = None
					self.input.clear()
					return
				
				print("[DEBUG] ADB shell process started successfully")
				self._in_subsession = True
				# Start the output checking timer with faster interval for better responsiveness
				self._adb_output_timer.setInterval(50)  # Check every 50ms instead of 100ms
				self._adb_output_timer.start()
				self.view.appendPlainText("> " + " ".join(parts) + "\n[interactive adb shell - type 'exit' to return]\n")
				
				# Wait longer for the initial prompt to appear and force output reading
				print("[DEBUG] Waiting for initial ADB shell output...")
				
				# Try multiple approaches to get the initial prompt
				for attempt in range(5):  # Try 5 times
					print(f"[DEBUG] Attempt {attempt + 1} to read initial output...")
					
					# Wait for data to be available
					if self._subproc.waitForReadyRead(1000):  # Wait 1 second each time
						print(f"[DEBUG] Data ready, bytes available: {self._subproc.bytesAvailable()}")
						self._on_sub_out()
					else:
						print(f"[DEBUG] No data ready on attempt {attempt + 1}")
					
					# Also try reading immediately without waiting
					self._on_sub_out()
					
					# Check if we got any output
					if self._subproc.bytesAvailable() == 0:
						print(f"[DEBUG] No more data available after attempt {attempt + 1}")
						break
				
				print("[DEBUG] Finished initial output reading attempts")
				self.input.clear()
				return
			
			# Otherwise write input directly to the persistent shell (cmd/bash)
			newline = "\r\n" if self._is_windows else "\n"
			if self.proc is not None:
				self.proc.write((msg + newline).encode())
				self.proc.waitForBytesWritten(100)
				self.input.clear()
		except Exception as e:
			self.view.appendPlainText(f"[terminal error] {e}")

	def _run_adb_command(self, argline: str) -> None:
		"""
		Run a single ADB command and display its output.
		
		This method executes a one-time ADB command (not an interactive shell)
		and displays the output in the terminal. It's used for non-interactive
		ADB operations.
		
		Args:
			argline (str): The ADB command line to execute
		"""
		try:
			import shlex
			args = shlex.split(argline)
			
			# Determine serial from parent CommConsole if available
			serial = None
			parent = self.parent()
			try:
				if hasattr(parent, 'adb_device_combo'):
					serial = parent.adb_device_combo.itemData(parent.adb_device_combo.currentIndex())
			except Exception:
				serial = None
			
			# Build final args; inject -s if serial available and not already specified
			final_args = []
			if serial and "-s" not in args:
				final_args.extend(["-s", str(serial)])
			final_args.extend(args)
			
			# Run adb via a short-lived QProcess for non-blocking output
			p = QtCore.QProcess(self)
			p.setProcessChannelMode(QtCore.QProcess.MergedChannels)
			p.readyReadStandardOutput.connect(lambda p=p: self._append_proc_output(p))
			p.readyReadStandardError.connect(lambda p=p: self._append_proc_output(p))
			p.finished.connect(lambda _c, _s, p=p: p.deleteLater())
			self.view.appendPlainText("> adb " + " ".join(final_args))
			p.start("adb", final_args)
		except Exception as e:
			self.view.appendPlainText(f"[adb error] {e}")

	def _append_proc_output(self, p: QtCore.QProcess) -> None:
		"""
		Append output from a QProcess to the terminal view.
		
		This helper method processes output from temporary processes
		(such as one-time ADB commands) and displays it in the terminal.
		
		Args:
			p (QtCore.QProcess): The process whose output to display
		"""
		try:
			data = p.readAllStandardOutput().data() + p.readAllStandardError().data()
			if data:
				try:
					text = data.decode(errors='replace')
				except Exception:
					text = str(data)
				self.view.moveCursor(QtGui.QTextCursor.End)
				self.view.insertPlainText(text)
				self.view.moveCursor(QtGui.QTextCursor.End)
		except Exception:
			pass

	def _print_host_prompt(self) -> None:
		"""
		Append a CMD-like header and current directory prompt to the log.
		
		This method displays a system information header similar to what
		users see when opening a new command prompt window.
		"""
		try:
			if not hasattr(self, 'view'):
				return
			# Only print when empty to avoid spamming
			if self.view.toPlainText().strip():
				return
			is_windows = platform.system().lower().startswith('win')
			cwd = os.getcwd()
			if is_windows:
				ver = platform.version()
				self.view.appendPlainText(f"Microsoft Windows [Version {ver}]")
				self.view.appendPlainText("(c) Microsoft Corporation. All rights reserved.")
				self.view.appendPlainText("")
				self.view.appendPlainText(cwd + ">")
			else:
				user = os.environ.get('USER') or os.environ.get('USERNAME') or ''
				host = platform.node()
				self.view.appendPlainText(f"{user}@{host}:{cwd}$")
		except Exception:
			pass
