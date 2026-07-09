---
title: "Connection Pool Exhaustion — Generic Runbook"
environment: any
service_tags: [checkout, payments, inventory, order-service, auth]
severity: high
last_updated: "2026-07-01"
---

# Connection Pool Exhaustion — Generic Runbook

## Symptoms

- Requests hang or return HTTP 503, 504, or client-side timeouts.
- Application logs contain messages like `PoolTimeoutError`,
  `HikariPool-1 - Connection is not available`, or
  `remaining connection slots are reserved`.
- Latency p95 on the affected service climbs into the multi-second
  range while CPU utilization remains low — the service is waiting,
  not working.
- Database side: `pg_stat_activity` shows many idle-in-transaction
  or long-running sessions holding connections.
- `pool.connections.active / pool.connections.max` metric ratio
  sits at or near 1.0 for several minutes.

## Common Causes

1. **Traffic burst without autoscaling headroom.** A promo, marketing
   event, or upstream retry storm doubles request volume; each request
   holds a connection long enough that the pool saturates.
2. **Long-running or missing-index queries.** One slow query blocks
   its connection for tens of seconds; when many requests hit the
   same slow query, the pool empties.
3. **Connection leak in application code.** Recent code change fails
   to release a connection on an exception path; the pool decays
   over minutes to hours until fully drained.
4. **Downstream lock or replication lag.** DB is holding locks or
   the replica is lagging; queries queue up on the DB side and hold
   pool connections while waiting.
5. **Undersized pool for current replica count.** After a scale-down
   or after DB `max_connections` was reduced, the pool can no longer
   sustain steady-state load.

## Investigation Steps

1. Compare current `pool.connections.active` to `pool.connections.max`
   on the affected service dashboard. If active == max, pool is
   exhausted.
2. Check the DB side: run
   `SELECT state, count(*) FROM pg_stat_activity GROUP BY state;`
   Large counts of `idle in transaction` indicate a code leak.
3. Identify slow queries:
   `SELECT pid, now()-query_start AS duration, query FROM
    pg_stat_activity WHERE state='active' ORDER BY duration DESC LIMIT 10;`
4. Correlate with recent deploys — if this began within 30 minutes
   of a release, suspect a connection leak.
5. Check for lock contention:
   `SELECT * FROM pg_locks WHERE NOT granted;`

## Resolution

- **Immediate (mitigation):** Restart the affected pods to reset the
  pool. Use `kubectl rollout restart deployment/<svc> -n <ns>`. This
  buys 10-30 minutes before the issue recurs if the root cause is a
  leak.
- **Short-term:** Increase `<svc>.db.pool.max` in the config map by
  50% if DB `max_connections` has headroom. Watch DB connection
  count after applying.
- **Query-driven exhaustion:** Kill the worst offender with
  `SELECT pg_cancel_backend(<pid>);` then optimize or add an index.
- **Connection leak:** Roll back the suspect deploy. See
  `runbook_deploy_rollback.md`. Ship a fix that ensures connections
  release in `finally` blocks or via context managers.
- **Undersized pool:** After confirming with DBA, raise DB
  `max_connections` and update the application pool max in lockstep.

## Escalation

- Primary: #infra-oncall (Slack) for pool/DB tuning
- Secondary: DBA on-call rotation for `max_connections` changes
- Tier: 2 (unless directly causing customer-facing outage)
