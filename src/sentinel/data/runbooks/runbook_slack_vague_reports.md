---
title: "Triaging Vague Slack Reports from Users"
environment: any
service_tags: [checkout, payments, inventory, auth, order-service, api-gateway, search, notification-service]
severity: medium
last_updated: "2026-07-01"
---

# Triaging Vague Slack Reports from Users

## Symptoms

- A message lands in #site-issues or #support-esc with wording like
  "the site is slow", "checkout is broken", "I can't log in",
  "orders aren't going through", "search isn't working".
- No stack trace, no error ID, no timestamp, no user ID, no browser.
- Sometimes accompanied by a screenshot of a generic error page
  ("Something went wrong") or a spinning loader.
- The user is a non-engineer (customer support, sales, exec)
  reporting on behalf of a customer, and the customer's own
  description is second-hand.

## Common Causes

1. **A real ongoing incident** the user noticed before monitoring
   caught it. Treat this as the most likely case until disproven.
2. **A user-side issue** — cached bundle, extension conflict,
   corporate proxy, out-of-date app version.
3. **An intermittent partial outage** hitting only one region,
   one CDN pop, or one tenant.
4. **A retention or state issue** — session expired, feature-flag
   rollout only reached some users.

## Investigation Steps

1. **Ask three clarifying questions in-thread, in one message:**
   - "What exact URL or screen were you on?"
   - "What time did this happen (roughly)? Local timezone is fine."
   - "Do you have a screenshot with a request-ID or timestamp?"
2. While waiting, check the global health dashboard for any
   service showing degraded state (`error_rate > baseline_p95`
   over the last 30 minutes).
3. Scan the last 30 minutes of alerts across `checkout`,
   `auth`, `api-gateway`, `search`, and `order-service` — the
   vague symptom often maps to a specific service alert that has
   already fired but not yet been triaged.
4. Query recent 5xx by route in the API gateway to see if any
   endpoint has an anomalous spike matching the reporter's flow
   (checkout → `/checkout/*`, login → `/auth/*`, etc.).
5. If a request-ID or timestamp comes back, pivot to the logs
   for the exact request and pattern-match the exception against
   known runbooks.

## Resolution

- **If a matching alert exists:** Acknowledge the report in-thread,
  link the incident, and continue triage against the appropriate
  service-specific runbook.
- **If no matching alert but reporter provides a request-ID:**
  Fetch the trace, identify the failing service, and page its
  on-call if the error is reproducible for other users.
- **If truly a one-off:** Ask the user to hard-refresh, clear
  cache, or try an incognito window. Log the report for pattern
  matching later (see `runbook_past_incident_pattern_matching.md`).
- **If reports pile up (3+ within 15 minutes with similar
  wording):** Escalate as a probable partial outage even if
  monitors are green — user reports precede alerts often.

## Escalation

- Primary: #site-issues triage rotation
- Secondary: incident-commander on-call if 3+ reports in 15 min
- Tier: 3 initially, escalate to 2 or 1 as evidence grows
