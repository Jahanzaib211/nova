"""Execution adapters — typed translators from domain intent to kernel requests.

Phase C7 — adapters never create processes.  They build
``ExecutionRequest`` objects and dispatch through the one kernel.
"""

from nova.execution.adapters.browser import BrowserAdapter
from nova.execution.adapters.docker import DockerAdapter
from nova.execution.adapters.git import GitAdapter
from nova.execution.adapters.pm2 import Pm2Adapter
from nova.execution.adapters.python import PythonAdapter
from nova.execution.adapters.shell import ShellAdapter
from nova.execution.adapters.systemd import SystemdAdapter

__all__ = [
    "BrowserAdapter",
    "DockerAdapter",
    "GitAdapter",
    "Pm2Adapter",
    "PythonAdapter",
    "ShellAdapter",
    "SystemdAdapter",
]
