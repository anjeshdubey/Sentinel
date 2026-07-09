---
title: "Deploy Rollback Procedure"
environment: prod
service_tags: [checkout, payments, inventory, auth, order-service, api-gateway, search, notification-service]
severity: high
last_updated: "2026-07-01"
---

# Deploy Rollback Procedure

## Symptoms

- Elevated error rate, latency spike, or crash-loop that began
  within 30 minutes of a deploy.
- Deploy correlation is the strongest signal: the incident timeline
  starts at or just after a rollout event.
- New pods showing `CrashLoopBackOff` or failing readiness probes
  after a rollout.
- Error signatures in logs that did not exist in the previous
  release (new exception types, new stack traces).
- Traffic-shifted canary showing worse error rate than the stable
  version and never converging.

## Common Causes

1. **Bad code change.** Regression in the new release — connection
   leak, off-by-one, incorrect config default, uncaught exception.
2. **Config or feature-flag change bundled with the deploy.** A
   flag flip that only takes effect on the new binary.
3. **Migration ordering issue.** Application deployed before DB
   migration ran, or migration ran but is incompatible with the
   older instances still receiving traffic during rollout.
4. **Dependency version bump.** Bumped library introduced a subtle
   behavior change (default timeout, retry policy, TLS setting).

## Investigation Steps

1. Confirm deploy timing: `kubectl rollout history
   deployment/<svc> -n <namespace>`. Note revision numbers and
   change-cause annotations.
2. Compare error rate now vs. the 30-minute window before deploy.
3. Check pod status:
   `kubectl get pods -l app=<svc> -n <namespace> -o wide` — look
   for `CrashLoopBackOff` or old-vs-new pod age split.
4. Diff config between the last two revisions:
   `kubectl rollout history deployment/<svc> --revision=<N>` for
   both revisions and diff env, image, and args.
5. Check the CI system for the merge SHA that shipped in this
   release and read the PR diff.
6. If a DB migration is involved, verify the schema version:
   `SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 5;`

## Resolution

- **Standard rollback:**
  `kubectl rollout undo deployment/<svc> -n <namespace>` — this
  reverts to the previous ReplicaSet. Watch pods reach Ready with
  `kubectl rollout status deployment/<svc> -n <namespace>`.
- **Rollback to a specific revision:**
  `kubectl rollout undo deployment/<svc> --to-revision=<N> -n <ns>`.
- **Rollback with DB migration involved:** Stop traffic to the
  service first (scale to 0 or take out of the load balancer),
  reverse the migration if it is backward-incompatible, then roll
  back the deployment, then restore traffic.
- **Post-rollback:** Confirm error rate returns to baseline within
  5 minutes. If it does not, the deploy was likely not the cause —
  reopen investigation.
- Notify the on-call channel, page the release owner, and file a
  ticket for the follow-up fix.

## Escalation

- Primary: #release-eng (Slack)
- Secondary: on-call SRE for the affected service tier
- Tier: 1 if customer-facing, 2 otherwise
