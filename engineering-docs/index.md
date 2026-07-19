# Sentinel — Engineering Docs

Sentinel is an LLM-powered agent that reads incident alerts, retrieves relevant
runbooks (RAG), calls tools to gather context (ownership, deploys, dependencies,
past incidents), produces a structured diagnosis, and — when confidence is low
or no grounded remediation exists — pauses for human approval before its
remediation is treated as actionable. The whole run streams to a browser as a
live trace.

Structured extraction is done directly on provider SDKs +
[Instructor](https://github.com/instructor-ai/instructor); a thin
[LangGraph](https://langchain-ai.github.io/langgraph/) state machine orchestrates
the human-in-the-loop approval flow (interrupt at the gate, resume on decision).
LLM providers are pluggable (Anthropic, Gemini, Groq).

**Live demo:** https://anjeshdubey.github.io/sentinel/

This site is for people extending or operating Sentinel. If you just want to
see it work, use the live demo above instead.

## Where to start

- [Architecture](architecture.md) — how the pieces fit together
- [Repository Layout](repository-layout.md) — what's in each directory
- [Testing Strategy](testing.md) — what's covered, what isn't, how to run it
- [Deployment & Secrets](deployment.md) — Modal + GitHub Pages, provider keys
- [Contributing](contributing.md) — local dev setup, PR checklist, docs workflow
- [Architecture Decisions](adr/index.md) — why things are built the way they are

## Source of truth

This site is generated from `engineering-docs/` and rebuilt automatically on
every merge to `main` — see [Contributing](contributing.md) for how the CI
publish job works. The root [`README.md`](https://github.com/anjeshdubey/sentinel/blob/main/README.md)
stays the canonical quick-reference for anyone browsing the repo directly on
GitHub; this site goes deeper for people working in the codebase day to day.
