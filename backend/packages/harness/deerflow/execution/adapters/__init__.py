"""Execution adapters — typed translators from domain intent to kernel requests.

Phase C7 — adapters never create processes.  They build
``ExecutionRequest`` objects and dispatch through the one kernel.
"""

from deerflow.execution.adapters.browser import BrowserAdapter
from deerflow.execution.adapters.docker import DockerAdapter
from deerflow.execution.adapters.git import GitAdapter
from deerflow.execution.adapters.pm2 import Pm2Adapter
from deerflow.execution.adapters.python import PythonAdapter
from deerflow.execution.adapters.shell import ShellAdapter
from deerflow.execution.adapters.systemd import SystemdAdapter

__all__ = [
    "BrowserAdapter",
    "DockerAdapter",
    "GitAdapter",
    "Pm2Adapter",
    "PythonAdapter",
    "ShellAdapter",
    "SystemdAdapter",
]
