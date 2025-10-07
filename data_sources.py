import psutil


class Subsystem:
	CPU = "CPU"
	GPU = "GPU"
	DRAM = "DRAM"


def get_timestamp() -> float:
	import time
	return time.time()


def get_cpu_core_count() -> int:
	"""Get number of CPU cores."""
	return psutil.cpu_count(logical=True)


def get_cpu_core_percent() -> list[float]:
	"""Get CPU usage per core as percentages."""
	return psutil.cpu_percent(interval=None, percpu=True)
