from __future__ import annotations

import shutil
import subprocess
from typing import List, Optional, Tuple


def _run(cmd: List[str], timeout: int = 15) -> Tuple[int, str, str]:

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

	return shutil.which("adb") is not None


def adb_version() -> str:

	code, out, err = _run(["adb", "version"]) if is_adb_available() else (127, "", "adb not found")
	return out or err


def list_devices() -> List[Tuple[str, str]]:

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


