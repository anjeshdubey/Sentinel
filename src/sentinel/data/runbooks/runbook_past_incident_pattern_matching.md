---
title: "Past Incident Pattern Matching"
environment: any
service_tags: [checkout, payments, inventory, auth, order-service, api-gateway, search, notification-service]
severity: medium
last_updated: "2026-07-01"
---

# Past Incident Pattern Matching

## Symptoms

- Current alert or user report resembles a previous incident in
  service, symptom shape, or timing pattern.
- A "this feels familiar" signal — a specific error string,
  latency shape, or hourly recurrence that a responder recognizes
  from prior weeks or months.
- Multiple recurrences of the same underlying cause across
  different quarters, suggesting an incompletely-fixed root cause.
- Alert content contains keywords that match a known past
  incident title or symptom (checkout 503, auth JWT, cache stampede,
  connection pool exhaustion, queue backup after schema change).

## Common Causes

1. **Recurring root cause.** The original fix was a mitigation,
   not a root-cause remediation, and the same failure mode is
   surfacing again under similar load.
2. **Regression after refactor.** A previously-fixed bug reappeared
   because the fix was overwritten during a refactor.
3. **Same underlying dependency issue.** A shared dependency
   (auth, DB, cache) produces symptoms in different services that
   look distinct but share a cause.
4. **Seasonal or scheduled load pattern.** The failure only
   surfaces under specific load conditions (Monday 9am, month-end
   billing, quarterly promo).

## Investigation Steps

1. Query the past-incidents index for the current service +
   symptom keywords. Sentinel's `PastIncidentsProvider` returns
   the top-3 most similar past incidents via vector similarity.
2. For each returned incident, compare:
   - Same service? Same environment?
   - Same or very similar symptom sentence?
   - Same resolution — did it mitigate or root-fix?
   - Time since resolution — is this within the "regression
     window" of a recent fix?
3. If a past incident matches strongly (similarity > 0.75),
   read its resolved_summary and try the same mitigation first,
   noting that if it works, this is likely a regression.
4. If no strong match, tag the current incident with the
   distinctive symptom keywords so it will be findable next time.

## Resolution

- **Strong match, same mitigation works:** Apply the past
  mitigation and log the recurrence explicitly. File a follow-up
  to root-fix — the mitigation is not enough.
- **Strong match, mitigation doesn't work:** Root cause has drifted;
  investigate as new but use the past incident's investigation
  notes as a starting hypothesis.
- **Weak match:** Treat as a new incident but preserve the
  metadata for future pattern matching.
- **Multiple recurrences (3+) of the same pattern:** Escalate to
  reliability engineering for a durable fix — this is technical
  debt with a track record.

## Escalation

- Primary: incident commander for the affected service
- Secondary: reliability engineering if pattern recurs 3+ times
- Tier: 2 (documentation and follow-up matter more than paging)
