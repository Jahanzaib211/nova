"""Job worker composition (handlers + process entrypoint).

The engine lives in the harness (``deerflow.jobs``); this package is where
app-level handlers are registered and the worker process is started:

    python -m app.jobs.worker --queues default,email --concurrency 4
"""
