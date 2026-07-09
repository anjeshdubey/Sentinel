---
title: "Database Connection Pool Saturation"
environment: prod
service_tags: [checkout, payments, inventory, order-service, auth]
severity: high
last_updated: "2026-07-01"
---

# Database Connection Pool Saturation

## Symptoms

- Service-level metric `db.pool.connections.active` is pinned at
  or near `db.pool.connections.max` for 5+ minutes.
- Application logs show `HikariPool-1 - Connection is not
  available, request timed out after 30000ms`, `psycopg2.pool.
  PoolError: connection pool exhausted`, or
  `sqlalchemy.exc.TimeoutError: QueuePool limit of size X reached`.
- Database side: `SELECT count(*) FROM pg_stat_activity WHERE
  application_name = '<svc>';` equals the configured pool max.
- Endpoint p95 latency climbs to several seconds even though
  DB query latency itself remains normal — the wait is inside
  the pool acquire.
- Cascading effect: upstream services see 503 from the affected
  service because its handlers time out waiting for a connection.

## Common Causes

1. **Traffic surge outstripping pool capacity.** A marketing event
   or upstream retry storm doubled request volume; even with normal
   per-request duration, concurrency exceeds pool max.
2. **Long-running transactions holding connections.** Code path
   opens a transaction, does long external work, then commits —
   connection is held the whole time.
3. **Connection leak.** New release fails to release connections
   on error paths; pool drains monotonically over minutes.
4. **DB-side slowness backing up the pool.** Locks, replica lag,
   or a missing index makes each query hold its connection longer,
   so the same traffic saturates a pool that used to be fine.
5. **DB `max_connections` reduced.** DBA reduced the DB-side
   limit; application pool max is now unreachable.

## Investigation Steps

1. Confirm exhaustion: dashboard panel for
   `db_pool_connections_active` vs `db_pool_connections_max`
   should show active pinned to max.
2. Check the DB side. Long-idle transactions:
   `SELECT pid, state, now() - xact_start AS xact_age, query
    FROM pg_stat_activity
    WHERE state = 'idle in transaction'
    ORDER BY xact_age DESC LIMIT 20;`
3. Slow active queries:
   `SELECT pid, now() - query_start AS duration, query
    FROM pg_stat_activity
    WHERE state = 'active'
    ORDER BY duration DESC LIMIT 10;`
4. Locks blocking others:
   `SELECT blocked.pid AS blocked_pid, blocking.pid AS blocking_pid
    FROM pg_locks blocked
    JOIN pg_locks blocking ON blocked.locktype = blocking.locktype
     AND blocked.database = blocking.database
     AND blocked.relation = blocking.relation
     AND NOT blocked.granted AND blocking.granted;`
5. Correlate with recent deploys of the affected service. Time
   correlation < 30 min → suspect a connection leak.

## Resolution

- **Immediate mitigation:** Restart the affected pods to reset the
  pool. `kubectl rollout restart deployment/<svc> -n <ns>`. This
  clears leaked connections and returns the service to nominal
  for at least a few minutes.
- **Increase pool max:** Confirm DB `max_connections` has headroom
  (target: `sum(app pool max) <= DB max_connections * 0.8`), then
  bump `db.pool.max` in the config map by 50% and roll.
- **Kill offending sessions on the DB:**
  `SELECT pg_cancel_backend(<pid>);` for the worst long-runners.
- **Suspected leak:** Roll back the recent deploy via
  `runbook_deploy_rollback.md`. Confirm active connection count
  stops climbing after rollback.
- **Downstream cause (locks, replica lag, missing index):** Fix
  the underlying query or index — the pool is only the symptom.
  See `runbook_latency_investigation.md` for slow-query workflow.
- **Long-term:** Ensure the service uses connection context
  managers or `finally` blocks. Alert on `db.pool.utilization > 0.8`
  well before saturation.

## Escalation

- Primary: on-call SRE for the affected service
- Secondary: DBA on-call for any DB-side changes
- Tier: 1 if the service is customer-facing and saturating,
  2 otherwise
