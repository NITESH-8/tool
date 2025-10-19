"""
Android Debug Bridge (ADB) Utilities Module

This module provides a comprehensive interface for interacting with Android devices
through the Android Debug Bridge (ADB). It includes functions for device management,
command execution, file operations, and device communication.

Key Features:
- Device discovery and listing
- Command execution via ADB shell
- File push/pull operations
- Device connection management
- Root access handling
- Command batching with timing control

Dependencies:
- subprocess: For executing ADB commands
- shutil: For checking ADB availability

Author: Performance GUI Team
Version: 1.0
"""

from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional, Tuple, Union


def _run(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:
	"""
	Execute a command and return the result with error handling.
	
	This is a low-level utility function that executes system commands
	and provides consistent error handling and return format. It's used
	by all ADB-related functions to execute commands safely.
	
	Args:
		cmd (List[str]): Command to execute as a list of arguments
		timeout (int): Maximum execution time in seconds (default: 15)
		
	Returns:
		Tuple[int, str, str]: (return_code, stdout, stderr)
			- return_code: Process exit code (0 = success)
			- stdout: Standard output as string
			- stderr: Standard error as string
			
	Error Handling:
		- TimeoutExpired: Returns code 124 with timeout message
		- FileNotFoundError: Returns code 127 with "not found" message
		- Other exceptions: Returns code 1 with exception message
		
	Example:
		>>> code, out, err = _run(["adb", "version"])
		>>> print(f"Exit code: {code}")
		Exit code: 0
	"""
	try:
		proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
		return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
	except subprocess.TimeoutExpired as e:
		return 124, "", f"Timeout running: {' '.join(cmd)}"
	except FileNotFoundError:
		return 127, "", f"Executable not found: {cmd[0]}"
	except Exception as e:
		return 1, "", str(e)


def is_adb_available() -> bool:
	"""
	Check if ADB (Android Debug Bridge) is available on the system.
	
	This function checks if the ADB executable is available in the system PATH.
	It's used to determine if ADB functionality should be enabled in the UI.
	
	Returns:
		bool: True if ADB is available, False otherwise
		
	Example:
		>>> if is_adb_available():
		...     print("ADB is available")
		... else:
		...     print("ADB not found")
	"""
	return shutil.which("adb") is not None


def adb_version() -> str:
	"""
	Get the version information of the installed ADB.
	
	This function executes 'adb version' and returns the version string.
	It's useful for debugging and ensuring the correct ADB version is installed.
	
	Returns:
		str: ADB version string, or error message if ADB is not available
		
	Example:
		>>> version = adb_version()
		>>> print(f"ADB version: {version}")
		ADB version: Android Debug Bridge version 1.0.41
	"""
	code, out, err = _run(["adb", "version"]) if is_adb_available() else (127, "", "adb not found")
	return out or err


def list_devices() -> List[Tuple[str, str]]:
	"""
	List all connected Android devices.
	
	This function executes 'adb devices -l' to get a list of all connected
	Android devices with their serial numbers and status information.
	The -l flag provides additional model information when available.
	
	Returns:
		List[Tuple[str, str]]: List of (serial, label) tuples
			- serial: Device serial number
			- label: Human-readable device description with status
			
	Device States:
		- device: Connected and authorized
		- authorizing: Waiting for user authorization
		- unauthorized: Device not authorized
		- offline: Device not responding
		
	Example:
		>>> devices = list_devices()
		>>> for serial, label in devices:
		...     print(f"{serial}: {label}")
		emulator-5554: emulator-5554 (device) - Android SDK built for x86
	"""
	if not is_adb_available():
		return []
		
	code, out, err = _run(["adb", "devices", "-l"])  # -l gives model info when available
	if code != 0:
		return []
		
	devices: List[Tuple[str, str]] = []
	for line in out.splitlines():
		line = line.strip()
		if not line or line.startswith("List of devices"):
			continue
			
		parts = line.split()
		if len(parts) >= 2 and parts[1] in ("device", "authorizing", "unauthorized", "offline"):
			serial = parts[0]
			desc = " ".join(parts[2:]) if len(parts) > 2 else ""
			state = parts[1]
			label = f"{serial} ({state})" + (f" - {desc}" if desc else "")
			devices.append((serial, label))
			
	return devices


def shell(serial: Optional[str], command: str, timeout: int = 30) -> Tuple[int, str, str]:
	"""
	Execute a shell command on an Android device via ADB.
	
	This function executes a command in the shell of the specified Android device.
	It's the primary way to interact with Android devices for command execution.
	
	Args:
		serial (Optional[str]): Device serial number, or None for default device
		command (str): Shell command to execute
		timeout (int): Maximum execution time in seconds (default: 30)
		
	Returns:
		Tuple[int, str, str]: (return_code, stdout, stderr)
			- return_code: Command exit code (0 = success)
			- stdout: Command output
			- stderr: Command error output
			
	Example:
		>>> code, out, err = shell("emulator-5554", "ls /sdcard")
		>>> print(f"Files: {out}")
		Files: Download Documents Pictures
	"""
	if not is_adb_available():
		return 127, "", "adb not found"
		
	args = ["adb"]
	if serial:
		args += ["-s", serial]
	args += ["shell", command]
	return _run(args, timeout=timeout)


def push(serial: Optional[str], local_path: str, remote_path: str, timeout: int = 60) -> Tuple[int, str, str]:

	if not is_adb_available():
		return 127, "", "adb not found"
	args = ["adb"]
	if serial:
		args += ["-s", serial]
	args += ["push", local_path, remote_path]
	return _run(args, timeout=timeout)


def pull(serial: Optional[str], remote_path: str, local_path: str, timeout: int = 60) -> Tuple[int, str, str]:

	if not is_adb_available():
		return 127, "", "adb not found"
	args = ["adb"]
	if serial:
		args += ["-s", serial]
	args += ["pull", remote_path, local_path]
	return _run(args, timeout=timeout)


def ensure_root(serial: Optional[str], timeout: int = 20) -> Tuple[int, str, str]:

	if not is_adb_available():
		return 127, "", "adb not found"
	args = ["adb"]
	if serial:
		args += ["-s", serial]
	args += ["root"]
	return _run(args, timeout=timeout)


def wait_for_device(serial: Optional[str], timeout: int = 60) -> Tuple[int, str, str]:

	if not is_adb_available():
		return 127, "", "adb not found"
	args = ["adb"]
	if serial:
		args += ["-s", serial]
	args += ["wait-for-device"]
	return _run(args, timeout=timeout)


def send_commands(serial: Optional[str], commands: List[str], spacing_ms: int = 300) -> Tuple[int, str, str]:

	if not is_adb_available():
		return 127, "", "adb not found"
	stdout_all: List[str] = []
	stderr_all: List[str] = []
	for idx, cmd in enumerate(commands):
		code, out, err = shell(serial, cmd)
		stdout_all.append(out)
		stderr_all.append(err)
		if code != 0:
			return code, "\n".join(stdout_all).strip(), "\n".join(stderr_all).strip()
		if spacing_ms > 0 and idx < len(commands) - 1:
			import time
			time.sleep(max(0.01, spacing_ms / 1000.0))
	return 0, "\n".join(stdout_all).strip(), "\n".join(stderr_all).strip()


# ===== Interactive Shell Management =====

def start_interactive_shell(serial: Optional[str]) -> Tuple[bool, Union[subprocess.Popen, None], str]:
	"""
	Start an interactive ADB shell session.
	
	This function creates a persistent ADB shell process that can be used for
	interactive communication with an Android device. The process runs in the
	background and can receive commands and return output in real-time.
	
	Args:
		serial (Optional[str]): Device serial number, or None for default device
		
	Returns:
		Tuple[bool, Union[subprocess.Popen, None], str]: (success, process, message)
			- success: True if shell started successfully
			- process: Popen object for the shell process, or None if failed
			- message: Success or error message
			
	Example:
		>>> success, proc, msg = start_interactive_shell("emulator-5554")
		>>> if success:
		...     print(f"Shell started: {msg}")
		...     # Use proc to send commands
	"""
	if not is_adb_available():
		return False, None, "adb not found"
	
	try:
		args = ["adb"]
		if serial:
			args.extend(["-s", serial])
		args.append("shell")
		
		# Start the interactive shell process
		process = subprocess.Popen(
			args,
			stdin=subprocess.PIPE,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True,
			bufsize=0  # Unbuffered for real-time output
		)
		
		# Wait a moment to ensure the process started
		import time
		time.sleep(0.1)
		
		if process.poll() is None:  # Process is still running
			return True, process, f"Interactive shell started for device {serial or 'default'}"
		else:
			return False, None, "Failed to start interactive shell"
			
	except Exception as e:
		return False, None, f"Error starting interactive shell: {str(e)}"


def stop_interactive_shell(process: Optional[subprocess.Popen]) -> Tuple[bool, str]:
	"""
	Stop an interactive ADB shell session.
	
	This function properly terminates an interactive ADB shell process,
	ensuring all resources are cleaned up.
	
	Args:
		process (Optional[subprocess.Popen]): The shell process to terminate
		
	Returns:
		Tuple[bool, str]: (success, message)
			- success: True if shell stopped successfully
			- message: Success or error message
			
	Example:
		>>> success, msg = stop_interactive_shell(proc)
		>>> print(f"Shell stopped: {msg}")
	"""
	if process is None:
		return True, "No process to stop"
	
	try:
		if process.poll() is None:  # Process is still running
			# Send exit command to gracefully close the shell
			try:
				process.stdin.write("exit\n")
				process.stdin.flush()
			except Exception:
				pass  # Ignore if stdin is already closed
			
			# Wait for process to terminate gracefully
			try:
				process.wait(timeout=2)
			except subprocess.TimeoutExpired:
				# Force terminate if it doesn't stop gracefully
				process.terminate()
				process.wait(timeout=1)
		
		return True, "Interactive shell stopped"
		
	except Exception as e:
		return False, f"Error stopping interactive shell: {str(e)}"


def send_shell_command(process: Optional[subprocess.Popen], command: str) -> Tuple[bool, str]:
	"""
	Send a command to an interactive ADB shell session.
	
	This function sends a command to a running interactive ADB shell process
	and returns whether the command was sent successfully.
	
	Args:
		process (Optional[subprocess.Popen]): The interactive shell process
		command (str): Command to send
		
	Returns:
		Tuple[bool, str]: (success, message)
			- success: True if command was sent successfully
			- message: Success or error message
			
	Example:
		>>> success, msg = send_shell_command(proc, "ls /sdcard")
		>>> if success:
		...     print("Command sent successfully")
	"""
	if process is None:
		return False, "No shell process available"
	
	if process.poll() is not None:
		return False, "Shell process is not running"
	
	try:
		# Send the command with newline
		process.stdin.write(command + "\n")
		process.stdin.flush()
		return True, "Command sent successfully"
		
	except Exception as e:
		return False, f"Error sending command: {str(e)}"


def read_shell_output(process: Optional[subprocess.Popen]) -> Tuple[bool, str, str]:
	"""
	Read output from an interactive ADB shell session.
	
	This function reads available output from a running interactive ADB shell
	process, checking both stdout and stderr.
	
	Args:
		process (Optional[subprocess.Popen]): The interactive shell process
		
	Returns:
		Tuple[bool, str, str]: (success, stdout, stderr)
			- success: True if output was read successfully
			- stdout: Standard output from the shell
			- stderr: Standard error from the shell
			
	Example:
		>>> success, stdout, stderr = read_shell_output(proc)
		>>> if success:
		...     print(f"Output: {stdout}")
	"""
	if process is None:
		return False, "", "No shell process available"
	
	if process.poll() is not None:
		return False, "", "Shell process is not running"
	
	try:
		stdout = ""
		stderr = ""
		
		# Read from stdout
		try:
			import select
			if hasattr(select, 'select'):
				# Unix-like systems
				if select.select([process.stdout], [], [], 0)[0]:
					stdout = process.stdout.read()
			else:
				# Windows - try to read what's available
				stdout = process.stdout.read()
		except Exception:
			# Fallback for systems without select
			try:
				stdout = process.stdout.read()
			except Exception:
				pass
		
		# Read from stderr
		try:
			if hasattr(select, 'select'):
				if select.select([process.stderr], [], [], 0)[0]:
					stderr = process.stderr.read()
			else:
				stderr = process.stderr.read()
		except Exception:
			try:
				stderr = process.stderr.read()
			except Exception:
				pass
		
		return True, stdout, stderr
		
	except Exception as e:
		return False, "", f"Error reading output: {str(e)}"


def is_shell_running(process: Optional[subprocess.Popen]) -> bool:
	"""
	Check if an interactive ADB shell session is still running.
	
	Args:
		process (Optional[subprocess.Popen]): The shell process to check
		
	Returns:
		bool: True if the shell process is still running
		
	Example:
		>>> if is_shell_running(proc):
		...     print("Shell is still active")
	"""
	if process is None:
		return False
	
	return process.poll() is None


# ===== Enhanced Device Management =====

def get_device_info(serial: Optional[str]) -> Tuple[int, str, str]:
	"""
	Get detailed information about an ADB device.
	
	This function retrieves system properties and other information
	about the specified Android device.
	
	Args:
		serial (Optional[str]): Device serial number, or None for default device
		
	Returns:
		Tuple[int, str, str]: (return_code, stdout, stderr)
			- return_code: Command exit code (0 = success)
			- stdout: Device information
			- stderr: Error output
			
	Example:
		>>> code, info, err = get_device_info("emulator-5554")
		>>> if code == 0:
		...     print(f"Device info: {info}")
	"""
	if not is_adb_available():
		return 127, "", "adb not found"
	
	args = ["adb"]
	if serial:
		args += ["-s", serial]
	args += ["shell", "getprop"]
	return _run(args, timeout=10)


def check_device_root(serial: Optional[str]) -> bool:
	"""
	Check if the device has root access.
	
	This function checks whether the Android device has root privileges
	by examining the user ID.
	
	Args:
		serial (Optional[str]): Device serial number, or None for default device
		
	Returns:
		bool: True if device has root access
		
	Example:
		>>> if check_device_root("emulator-5554"):
		...     print("Device has root access")
	"""
	code, out, err = shell(serial, "id")
	return code == 0 and "uid=0" in out


def get_device_model(serial: Optional[str]) -> str:
	"""
	Get the device model name.
	
	Args:
		serial (Optional[str]): Device serial number, or None for default device
		
	Returns:
		str: Device model name, or empty string if not available
		
	Example:
		>>> model = get_device_model("emulator-5554")
		>>> print(f"Device model: {model}")
	"""
	code, out, err = shell(serial, "getprop ro.product.model")
	if code == 0 and out.strip():
		return out.strip()
	return ""


def get_device_android_version(serial: Optional[str]) -> str:
	"""
	Get the Android version of the device.
	
	Args:
		serial (Optional[str]): Device serial number, or None for default device
		
	Returns:
		str: Android version, or empty string if not available
		
	Example:
		>>> version = get_device_android_version("emulator-5554")
		>>> print(f"Android version: {version}")
	"""
	code, out, err = shell(serial, "getprop ro.build.version.release")
	if code == 0 and out.strip():
		return out.strip()
	return ""


