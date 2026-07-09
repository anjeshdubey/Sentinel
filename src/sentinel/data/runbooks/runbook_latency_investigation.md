---
title: "Latency Spike Investigation"
environment: prod
service_tags: [api-gateway, checkout, search, order-service, inventory, auth]
severity: high
last_updated: "2026-07-01"
---

# Latency Spike Investigation

## Symptoms

- Service p95 or p99 latency crosses its alerting threshold for
  5+ minutes.
- Error rate is normal or only slightly elevated — the primary
  signal is slow responses, not failures.
- Users report "the site is slow" or "requests are taking forever"
  (often surfacing before the p95 alert fires).
- Downstream retry counts climb as clients time out and retry
  slow requests.
- CPU on the affected service is normal or low — the service is
  waiting on something.

## Common Causes

1. **Downstream service degradation.** A dependency's p99 rose,
   which pulls this service's p95/p99 up proportional to fan-out.
2. **Database slow query.** A newly-common query pattern lacks an
   index; each request is scanning millions of rows.
3. **Connection pool contention.** Requests are queuing waiting
   for a DB or Redis connection — see
   `runbook_connection_pooling.md`.
4. **Cold cache after a deploy or Redis restart.** Cache-miss
   rate is 100% until warmed, and the miss-path is expensive.
5. **Noisy neighbor / resource contention.** Another workload on
   the same node is starving the pod of CPU or IO.
6. **Network path issue.** Cross-region traffic, DNS resolution
   delay, TLS handshake regression.

## Investigation Steps

1. Look at the service latency dashboard — where does the spike
   start? Was it a step change (deploy correlation) or a gradual
   climb (traffic or cache)?
2. Check the distributed trace waterfall for a slow representative
   request. Identify the span that owns most of the wall time.
   Common offenders: DB query, downstream HTTP call, external API.
3. Compare p95 latency on immediate downstream dependencies. If a
   downstream's p95 rose in lockstep, the root cause is downstream.
4. Query slow queries: in Postgres, check `pg_stat_statements`
   sorted by `mean_exec_time DESC LIMIT 10`. Look for a query
   whose call count has spiked.
5. Check cache hit rate. A `redis.cache.hit_ratio` drop from 0.95
   to 0.30 points to cold cache.
6. Confirm recent deploy correlation with
   `kubectl rollout history deployment/<svc>`.
7. Check node-level metrics on the pods (`kubectl top pod`) — high
   CPU wait or throttling implies noisy neighbor.

## Resolution

- **Downstream root cause:** Pivot investigation to that service;
  this one is a victim. Add a client-side timeout so we fail fast
  rather than queuing.
- **Slow query:** Add the missing index in a maintenance window
  or use `CREATE INDEX CONCURRENTLY`. As an immediate stopgap,
  kill the runaway sessions with `pg_cancel_backend`.
- **Pool contention:** Follow `runbook_connection_pooling.md`.
- **Cold cache:** Warm the cache with a synthetic replay of the
  top-100 keys, or scale up temporarily to absorb the load until
  natural warm-up.
- **Noisy neighbor:** Cordon the affected node and drain, or
  reschedule the pod to a different node. Long-term: enforce
  CPU/memory requests-limits parity.
- **Post-mitigation:** Add a SLO alert for the specific downstream
  dependency latency that triggered this, so the next event is
  attributed correctly.

## Escalation

- Primary: on-call SRE for the affected service
- Secondary: DBA on-call for slow-query issues
- Tier: 2 unless customer-visible latency exceeds the SLA
