"""Security primitives that sit on the delivery path.

The only public surface today is :mod:`deerflow.security.secure_ship`, the
pre-deploy gate. It is imported defensively by the capability module and the
agent middleware, so importing this package has no side effects.
"""

from deerflow.security.secure_ship import (
    Finding,
    GatePolicy,
    GateReport,
    ScannerResult,
    default_policy,
    evaluate,
    run_gate,
    toolkit_manifest,
)

__all__ = [
    "Finding",
    "GatePolicy",
    "GateReport",
    "ScannerResult",
    "default_policy",
    "evaluate",
    "run_gate",
    "toolkit_manifest",
]
