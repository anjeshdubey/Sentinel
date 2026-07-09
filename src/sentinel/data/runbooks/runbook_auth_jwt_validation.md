---
title: "Auth Service JWT Validation Failures"
environment: prod
service_tags: [auth, api-gateway]
severity: critical
last_updated: "2026-07-01"
---

# Auth Service JWT Validation Failures

## Symptoms

- Elevated HTTP 401 or 403 responses from the API gateway on
  routes that require authentication (`/api/*` protected paths).
- Application logs contain `InvalidTokenError`, `JWT signature
  verification failed`, `token has expired`, or `unable to find
  key with kid=<id>`.
- Auth service logs show a spike in `/token/validate` failures
  even though the JWTs themselves look well-formed.
- Downstream services log `unauthenticated` errors on requests
  that were succeeding minutes earlier.
- Users report "I keep getting logged out" or "the app keeps
  asking me to sign in again".
- Alert firing: `auth.jwt.validation.failure_rate > 0.05 for 5m`.

## Common Causes

1. **JWKS key rotation not propagated.** The auth service rotated
   its signing key but the JWKS endpoint cache in downstream
   services still returns the old public keys.
2. **Clock skew between services.** NTP drift causes some pods to
   see valid tokens as `not_before` (`nbf`) violations or
   already-expired.
3. **Redis session store outage.** For opaque-token flows, if
   Redis is unavailable the auth service can't look up tokens
   and returns 401 for all validations.
4. **Recent auth service deploy.** New release changed the
   token-issuing algorithm (RS256 → ES256), key ID, or claim
   schema — old tokens are still in circulation and now invalid.
5. **Downstream misconfiguration.** A service was redeployed with
   an outdated `AUTH_JWKS_URL` or hard-coded old public key.
6. **Aggressive token TTL reduction.** TTL was shortened but
   refresh-token flow is broken, so clients get 401 sooner than
   expected.

## Investigation Steps

1. Check `auth.jwt.validation.failure_rate` broken down by
   failure reason: `signature_invalid`, `expired`, `key_not_found`.
   The dominant reason usually points at the cause.
2. If `key_not_found` dominates: JWKS propagation issue. Hit the
   JWKS endpoint directly:
   `curl -s https://auth.internal/.well-known/jwks.json | jq '.keys[].kid'`
   and compare against the `kid` in a failing token.
3. If `signature_invalid` dominates and started after a deploy:
   suspect a signing-algorithm change. Diff the last two auth
   service revisions for signing config.
4. If `expired` dominates: check clock skew across nodes with
   `chronyc sources` or the platform's NTP monitor.
5. Check Redis health for the auth session store:
   `redis-cli -h auth-session.internal ping`. If Redis is down or
   OOM, this is your root cause.
6. Verify the auth service pods are healthy and not crash-looping:
   `kubectl get pods -n auth -l app=auth-service`.

## Resolution

- **JWKS propagation:** Bump the JWKS cache TTL down to 60s
  during the incident. Restart downstream services to force a
  refetch as a stopgap.
- **Signing algorithm mismatch after deploy:** Rollback the auth
  service — see `runbook_deploy_rollback.md`. Then coordinate a
  proper migration where new keys are published before old ones
  are retired.
- **Clock skew:** Restart chronyd on drifted nodes. Add a
  monitoring alert on NTP sync lag.
- **Redis outage:** Follow the Redis on-call runbook. As a
  temporary measure, if the auth service supports a "trust
  gateway" fallback, enable it and accept the reduced revocation
  guarantee.
- **Downstream misconfig:** Redeploy the misconfigured service
  with the correct `AUTH_JWKS_URL`.

## Escalation

- Primary: #auth-oncall (Slack)
- Secondary: security-oncall for any suspected token-tampering
- Tier: 1 (auth failures block the entire platform)
