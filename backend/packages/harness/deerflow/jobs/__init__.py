"""Nova job runner (harness side).

Phase 0 ships only the status vocabulary so the cross-language contract in
``contracts/job_status_contract.json`` is load-bearing from day one. The
queue, worker, scheduler and context land in the next phase.
"""
