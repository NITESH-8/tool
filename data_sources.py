"""
Data Sources Module - System Performance Metrics Collection

This module provides basic utility functions for the Performance Dashboard application.
Data collection is handled by external backend systems.

Key Functions:
- get_timestamp(): Get current Unix timestamp

Author: Performance GUI Team
Version: 1.0
"""


class Subsystem:
	"""
	Constants defining the available performance subsystems.
	
	This class provides string constants for the different types of
	performance subsystems that can be monitored by the application.
	These constants are used throughout the application to identify
	and reference specific subsystems.
	
	Attributes:
		CPU (str): CPU subsystem identifier
		GPU (str): GPU subsystem identifier  
		DRAM (str): DRAM (memory) subsystem identifier
	"""
	CPU = "CPU"
	GPU = "GPU"
	DRAM = "DRAM"


def get_timestamp() -> float:
	"""
	Get the current Unix timestamp.
	
	Returns the current time as a Unix timestamp (seconds since epoch).
	This is used throughout the application to timestamp all performance
	measurements for time-series data collection and visualization.
	
	Returns:
		float: Current Unix timestamp in seconds
		
	Example:
		>>> timestamp = get_timestamp()
		>>> print(f"Current time: {timestamp}")
		Current time: 1640995200.123
	"""
	import time
	return time.time()

# CPU data collection functions removed - using external backend
