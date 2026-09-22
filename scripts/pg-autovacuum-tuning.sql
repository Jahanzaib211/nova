-- Autovacuum tuning for the checkpoint and run-event tables.
--
-- Apply after creating or restoring the Postgres database:
--
--     docker exec -i deer-flow-postgres psql -U nova -d nova \
--       < scripts/pg-autovacuum-tuning.sql
--
-- The role and database are both `nova` (POSTGRES_USER / POSTGRES_DB on the
-- container). `-U postgres -d postgres` -- what this header said until
-- 2026-09-01 -- fails with `role "postgres" does not exist`.
--
-- WHY THIS EXISTS
-- ---------------
-- On 2026-08-31 the database was 6,084 MB. 5,423 MB of that was
-- `checkpoint_blobs` -- holding **2.3 MB** of live data. `checkpoint_writes`
-- was 225 MB holding 406 kB. Measured, not estimated:
--
--     select pg_size_pretty(sum(pg_column_size(blob))::bigint) from checkpoint_blobs;
--      -> 2344 kB     against a 5423 MB table
--
-- The bloat is entirely TOAST. LangGraph rewrites checkpoint blobs constantly,
-- and every rewrite orphans the previous TOAST chain. The *heap* of these tables
-- stays tiny (464 kB), so the default autovacuum trigger --
-- `threshold + scale_factor * n_live_tup`, i.e. 50 + 0.2 * 163 ≈ 83 dead rows --
-- is computed against a table that barely has 163 rows and effectively never
-- fires. `last_autovacuum` was NULL on every one of these tables.
--
-- Setting scale_factor to 0 makes the trigger a flat 50 dead tuples regardless
-- of table size, which is what a small-heap/large-TOAST table needs. The
-- `toast.` variants matter most here: that is where the bytes actually live, and
-- a TOAST table has its own autovacuum counters.
--
-- Note this does NOT reclaim space that is already lost -- plain VACUUM makes
-- pages reusable but does not shrink the file. That needs a rewrite:
--
--     VACUUM (FULL, ANALYZE) checkpoint_blobs;
--     VACUUM (FULL, ANALYZE) checkpoint_writes;
--
-- which took 148 ms combined and returned 6,084 MB -> 439 MB, because there was
-- so little live data to copy. VACUUM FULL takes an ACCESS EXCLUSIVE lock, so it
-- is safe here only *because* the live set is tiny; do not assume that on a table
-- whose live data is genuinely large.
--
-- `scripts/prune-checkpoints.py` is a different tool for a different problem: it
-- drops *old checkpoints* by retention. It reported `doomed: 0` here, correctly
-- -- there were only 399 checkpoints and none were old. Retention was never the
-- issue; vacuuming was.

ALTER TABLE checkpoint_blobs SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 50,
    toast.autovacuum_vacuum_scale_factor = 0.0,
    toast.autovacuum_vacuum_threshold = 50
);

ALTER TABLE checkpoint_writes SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 50,
    toast.autovacuum_vacuum_scale_factor = 0.0,
    toast.autovacuum_vacuum_threshold = 50
);

ALTER TABLE checkpoints SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 50,
    toast.autovacuum_vacuum_scale_factor = 0.0,
    toast.autovacuum_vacuum_threshold = 50
);

-- run_events is different in kind: its 366 MB is genuine content (335 MB live),
-- not bloat. Tuning helps it stay compact under churn, but the thing that bounds
-- it is retention -- see `run_events.retention_days` in config.yaml.
ALTER TABLE run_events SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 50,
    toast.autovacuum_vacuum_scale_factor = 0.0,
    toast.autovacuum_vacuum_threshold = 50
);

-- admin_audit: DELIBERATELY NOT TUNED HERE.
-- --------------------------------------------------------------------------
-- It looks like the next checkpoint_blobs and is not. Measured 2026-09-01:
--
--     total 42 MB | heap 26 MB | indexes 16 MB | TOAST 8192 bytes
--     151,387 rows holding 24 MB live
--
-- 24 MB live in a 26 MB heap is a healthy table, and the TOAST that dominated
-- the checkpoint tables is 8 kB here. Applying the settings above would be
-- cargo-culting a remedy for bloat this table does not have.
--
-- What DID mislead: `pg_stat_user_tables.n_live_tup` read 3,097 -- a 49x
-- undercount -- with `last_autovacuum` and `last_analyze` both NULL. That is a
-- stats-collector reset (PG16 keeps cumulative stats in shared memory and
-- discards them on an unclean shutdown), not a vacuum problem: `pg_class.
-- reltuples` had survived at 135,578, within ~10% of the truth, so the *planner*
-- was never badly misled. Only autovacuum's own thresholds, which read
-- n_live_tup, were computed against the wrong number.
--
-- A plain `ANALYZE admin_audit;` restored it (n_live_tup 3,097 -> 151,414). If
-- you find NULL autovacuum timestamps on a table here, check `stats_reset` in
-- pg_stat_database and compare `reltuples` before concluding anything is wrong.
--
-- The real open question for this table is growth, not bloat: it is an
-- append-only audit log with no retention policy, the same shape of risk that
-- `run_events.retention_days` exists to bound. Left as a decision rather than
-- silently deleting a user's audit history.
