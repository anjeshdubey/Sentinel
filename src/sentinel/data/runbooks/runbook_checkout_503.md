---
title: "Checkout Service 503 Service Unavailable"
environment: prod
service_tags: [checkout, payments]
severity: critical
last_updated: "2026-07-01"
---

# Checkout Service 503 Service Unavailable

## Symptoms

- HTTP 503 (Service Unavailable) responses on `/checkout/charge`,
  `/checkout/refund`, and `/checkout/cart` endpoints.
- Error rate on the checkout service exceeds 50% as measured by the
  API gateway; often spikes to 80-95% during full pool exhaustion.
- Alert firing: `checkout.http.5xx.rate > 0.5 for 5m`.
- Latency p95 climbs above 3s just before 503s start; requests are
  queued waiting for a database connection and eventually time out.
- Downstream services (`order-service`, `notification-service`)
  begin reporting timeouts from `checkout-api`.

## Common Causes

1. **Database connection pool exhaustion.** The checkout-api maintains
   a pool of PostgreSQL connections to the `payments` database. When
   long-running queries or a traffic burst hold all connections, new
   requests block on pool acquisition and eventually surface as 503.
2. **Downstream payment provider outage.** Stripe/Adyen returning 5xx
   for 5+ consecutive requests opens the circuit breaker, causing the
   checkout-api to return 503 to callers.
3. **Recent deploy regression.** Connection leaks introduced in a new
   release cause the pool to drain over 5-15 minutes after rollout.
4. **Memory pressure / OOMKill.** Pods being OOM-killed by Kubernetes
   cause the effective replica count to drop below what traffic needs.

## Investigation Steps

1. Check the Grafana dashboard `checkout-api-overview` for the exact
   time the 5xx rate crossed 50%. Correlate with recent deploys.
2. Query the connection pool metrics:
   `sum(pg_pool_connections_active{service="checkout"})` vs
   `sum(pg_pool_connections_max{service="checkout"})`. If active is
   pinned at max, this is pool exhaustion.
3. Check pod health: `kubectl get pods -n payments -l app=checkout-api`.
   Look for `CrashLoopBackOff`, `OOMKilled`, or missing replicas.
4. Verify downstream payment provider status:
   `curl -s https://status.stripe.com/api/v2/status.json`.
5. Check recent deploy history:
   `kubectl rollout history deployment/checkout-api -n payments`.
6. Tail application logs for `PoolTimeoutError` or
   `HikariPool-1 - Connection is not available`.

## Resolution

- **Pool exhaustion:** Increase `checkout.db.pool.max` from 50 to 100
  in the config map and roll the deployment. Long-term fix is to
  see `runbook_connection_pooling.md` and `runbook_database_connection_pool.md`.
- **Downstream outage:** Flip `checkout.payment.provider.primary` to
  the fallback provider in feature flags. Notify #payments-oncall.
- **Recent deploy:** Immediately rollback with
  `kubectl rollout undo deployment/checkout-api -n payments`.
  See `runbook_deploy_rollback.md` for the standard rollback flow.
- **OOMKill:** Bump memory limits in the pod spec by 25%, apply,
  and wait for the new pods to become Ready.

## Escalation

- Primary: #payments-oncall (Slack)
- Secondary: payments-lead@company.com
- Tier: 1 (revenue-impacting outage)
