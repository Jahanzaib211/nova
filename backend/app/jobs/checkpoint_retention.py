"""Checkpoint retention from the command line.

    python -m app.jobs.checkpoint_retention                 # dry run: estimate only
    python -m app.jobs.checkpoint_retention --apply         # delete (needs checkpoint_retention.enabled)
    python -m app.jobs.checkpoint_retention --keep-active-days 0 --keep-per-thread 20

Uses the same config.yaml and database as the gateway. A dry run issues only
SELECTs and is always allowed; ``--apply`` refuses unless
``checkpoint_retention.enabled`` is true, so turning the feature on stays a
config decision. Flags override the configured policy for this invocation.
See :mod:`nova.persistence.checkpoint_retention` for the policy.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from nova.config.app_config import get_app_config
from nova.persistence.checkpoint_retention import RetentionPolicy, prune_checkpoints
from nova.persistence.engine import close_engine, get_engine, init_engine_from_config


def build_policy(args: argparse.Namespace, cfg) -> RetentionPolicy:
    policy = RetentionPolicy.from_config(cfg)
    for name in ("keep_per_thread", "keep_active_days", "batch_size", "max_batches"):
        value = getattr(args, name)
        if value is not None:
            setattr(policy, name, value)
    policy.__post_init__()
    return policy


async def run(args: argparse.Namespace) -> int:
    config = get_app_config()
    cfg = config.checkpoint_retention
    if args.apply and not cfg.enabled:
        print("checkpoint-retention: --apply refused: checkpoint_retention.enabled is false in config.yaml", file=sys.stderr)
        return 2
    policy = build_policy(args, cfg)
    await init_engine_from_config(config.database)
    try:
        engine = get_engine()
        if engine is None:
            print("checkpoint-retention: database.backend is memory; nothing to prune", file=sys.stderr)
            return 2
        report = await prune_checkpoints(engine, policy, dry_run=not args.apply)
    finally:
        await close_engine()
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete (default is a dry run)")
    parser.add_argument("--keep-per-thread", dest="keep_per_thread", type=int, default=None)
    parser.add_argument("--keep-active-days", dest="keep_active_days", type=float, default=None)
    parser.add_argument("--batch-size", dest="batch_size", type=int, default=None)
    parser.add_argument("--max-batches", dest="max_batches", type=int, default=None)
    return asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
