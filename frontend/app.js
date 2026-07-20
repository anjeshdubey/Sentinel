// Sentinel demo frontend — no build step, plain JS.
//
// Auto-detects local dev: if the page itself is served from localhost/127.0.0.1,
// point at the local backend (port 8000) instead of the deployed Modal backend.
const isLocalHost = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const API_BASE = isLocalHost
  ? "http://localhost:8000"
  : "https://anjeshdubey--sentinel-demo-web.modal.run";

const grid = document.getElementById("scenario-grid");
const tracePanel = document.getElementById("trace-panel");
const traceTitle = document.getElementById("trace-title");
const cacheBadge = document.getElementById("cache-badge");
const timeline = document.getElementById("timeline");
const closeBtn = document.getElementById("close-trace");

// The scenario currently being traced — the human-approval gate needs it to
// address its /triage/resume call back at the right scenario.
let activeScenarioId = null;

closeBtn.addEventListener("click", () => {
  tracePanel.hidden = true;
});

async function loadScenarios() {
  try {
    const res = await fetch(`${API_BASE}/scenarios`);
    const scenarios = await res.json();
    renderScenarios(scenarios);
  } catch (e) {
    // Fallback to local scenarios.json if the backend isn't reachable yet.
    const res = await fetch("scenarios.json");
    const scenarios = await res.json();
    renderScenarios(scenarios);
  }
}

function renderScenarios(scenarios) {
  grid.innerHTML = "";
  for (const s of scenarios) {
    const card = document.createElement("button");
    card.className = "scenario-card";
    card.innerHTML = `
      <h3>${s.title}</h3>
      <p>${s.description}</p>
      <p class="why">${s.why_interesting}</p>
    `;
    card.addEventListener("click", () => runScenario(s, card));
    grid.appendChild(card);
  }
}

function addStep(cls, label, bodyHtml) {
  const step = document.createElement("div");
  step.className = `step ${cls}`;
  step.innerHTML = `<span class="step-label">${label}</span>${bodyHtml}`;
  timeline.appendChild(step);
  step.scrollIntoView({ behavior: "smooth", block: "end" });
  return step;
}

function renderEvent(eventType, data) {
  switch (eventType) {
    case "start":
      cacheBadge.hidden = !data.cache_hit;
      break;
    case "alert":
      addStep(
        "alert",
        "[1] ALERT RECEIVED",
        `<pre>${escapeHtml(JSON.stringify(data.payload, null, 2))}</pre>`
      );
      break;
    case "node_start":
      // Subtle marker showing the graph walking through its nodes.
      addStep("node-start", `▶ ${escapeHtml(String(data.node || "").toUpperCase())}`, "");
      break;
    case "tool_call":
      addStep(
        "tool_call",
        `TOOL CALL → ${data.tool}(${escapeHtml(JSON.stringify(data.args))})`,
        ""
      );
      break;
    case "tool_result":
      addStep(
        "tool_result",
        `↳ result (${data.duration_ms}ms)`,
        `<pre>${escapeHtml(JSON.stringify(data.result, null, 2))}</pre>`
      );
      break;
    case "rag_query":
      addStep("rag_query", "RAG RETRIEVAL", `<pre>query: ${escapeHtml(data.query || "")}</pre>`);
      break;
    case "rag_results":
      addStep(
        "rag_results",
        "↳ retrieved context",
        `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`
      );
      break;
    case "llm_call":
      addStep("llm_call", `LLM CALL → ${data.model}`, "");
      break;
    case "diagnosis":
      addStep(
        "diagnosis",
        "DIAGNOSIS (structured output)",
        `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`
      );
      break;
    case "interrupt":
      addApprovalGate(data);
      break;
    case "finalized":
      renderFinalized(data);
      break;
    case "done":
      break;
    default:
      addStep("step", eventType, `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`);
  }
}

// Human-in-the-loop gate: the run paused at `interrupt`. Render the draft
// diagnosis + proposed remediation with Approve / Reject buttons that resume
// the paused thread via its correlation_id.
function addApprovalGate(data) {
  const scenarioId = activeScenarioId;
  const step = document.createElement("div");
  step.className = "step interrupt gate";
  const conf = typeof data.confidence === "number" ? data.confidence.toFixed(2) : data.confidence;
  step.innerHTML = `
    <span class="step-label">⏸ AWAITING HUMAN APPROVAL</span>
    <div class="gate-draft">
      <div class="gate-title">${escapeHtml(data.title || "")}</div>
      <div class="gate-meta">
        <span class="sev ${escapeHtml(data.severity || "")}">${escapeHtml(data.severity || "")}</span>
        · ${escapeHtml(data.service || "—")}
        · confidence ${conf}
        · team ${escapeHtml(data.assigned_team || "—")}
      </div>
      ${
        data.suspected_root_cause
          ? `<div class="gate-cause">Suspected cause: ${escapeHtml(data.suspected_root_cause)}</div>`
          : ""
      }
      ${
        data.proposed_remediation
          ? `<div class="gate-remediation"><span class="gate-remediation-label">Proposed remediation</span>${escapeHtml(
              data.proposed_remediation
            )}</div>`
          : `<div class="gate-remediation none">No grounded remediation — needs a human call.</div>`
      }
    </div>
    <div class="gate-actions">
      <button class="gate-btn approve" type="button">✓ Approve</button>
      <button class="gate-btn reject" type="button">✗ Reject</button>
    </div>
  `;
  timeline.appendChild(step);

  const approveBtn = step.querySelector(".approve");
  const rejectBtn = step.querySelector(".reject");
  const decide = (decision) => {
    approveBtn.disabled = true;
    rejectBtn.disabled = true;
    step.classList.add(`decided-${decision}`);
    resumeScenario(scenarioId, data.correlation_id, decision);
  };
  approveBtn.addEventListener("click", () => decide("approve"));
  rejectBtn.addEventListener("click", () => decide("reject"));
  step.scrollIntoView({ behavior: "smooth", block: "end" });
}

function renderFinalized(data) {
  const status = data.approval_status || "auto";
  const actionable = data.proposed_remediation
    ? `<pre>remediation: ${escapeHtml(data.proposed_remediation)}</pre>`
    : `<pre>(no actionable remediation)</pre>`;
  addStep(`finalized ${status}`, `✓ FINALIZED — ${status.toUpperCase()}`, actionable);
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

// Read a POST-based SSE response body and render each frame. Native EventSource
// only supports GET, so we parse "event:"/"data:" frames ourselves. Shared by
// the initial stream and the resume continuation so both feed one timeline.
async function readSSE(res) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let frameEnd;
    while ((frameEnd = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);

      let eventType = "message";
      let dataLine = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) eventType = line.slice(6).trim();
        if (line.startsWith("data:")) dataLine += line.slice(5).trim();
      }
      if (dataLine) {
        try {
          renderEvent(eventType, JSON.parse(dataLine));
        } catch (e) {
          // ignore malformed frames
        }
      }
      // Small delay between events so the trace visibly animates.
      await new Promise((r) => setTimeout(r, 150));
    }
  }
}

async function runScenario(scenario, card) {
  timeline.innerHTML = "";
  cacheBadge.hidden = true;
  activeScenarioId = scenario.id;
  traceTitle.textContent = `Reasoning trace — ${scenario.title}`;
  tracePanel.hidden = false;
  tracePanel.scrollIntoView({ behavior: "smooth" });

  const allCards = grid.querySelectorAll(".scenario-card");
  allCards.forEach((c) => (c.disabled = true));
  if (card) {
    card.classList.add("loading");
    card.insertAdjacentHTML("beforeend", '<span class="spinner"></span>');
  }

  try {
    const res = await fetch(`${API_BASE}/triage/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_id: scenario.id }),
    });

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      addStep("alert", "ERROR", `<pre>${escapeHtml(JSON.stringify(errBody, null, 2))}</pre>`);
      return;
    }

    await readSSE(res);
  } catch (e) {
    addStep(
      "alert",
      "ERROR",
      `<pre>Could not reach the backend at ${API_BASE}. Is it running?\n${escapeHtml(String(e))}</pre>`
    );
  } finally {
    allCards.forEach((c) => (c.disabled = false));
    if (card) {
      card.classList.remove("loading");
      card.querySelector(".spinner")?.remove();
    }
  }
}

// Resume a paused run with the human decision, continuing frames into the same
// timeline. The paused thread lives in the backend's in-process checkpointer,
// keyed by correlation_id.
async function resumeScenario(scenarioId, correlationId, decision) {
  addStep(`resume ${decision}`, `HUMAN DECISION → ${decision.toUpperCase()}`, "");

  try {
    const res = await fetch(`${API_BASE}/triage/resume`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scenario_id: scenarioId,
        correlation_id: correlationId,
        decision,
      }),
    });

    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const detail = errBody.detail || errBody;
      if (res.status === 409) {
        addStep(
          "alert",
          "SESSION EXPIRED",
          `<pre>${escapeHtml(
            (detail && detail.message) || "Approval session expired — re-run the scenario."
          )}</pre>`
        );
      } else {
        addStep("alert", "ERROR", `<pre>${escapeHtml(JSON.stringify(detail, null, 2))}</pre>`);
      }
      return;
    }

    await readSSE(res);
  } catch (e) {
    addStep(
      "alert",
      "ERROR",
      `<pre>Could not reach the backend at ${API_BASE}.\n${escapeHtml(String(e))}</pre>`
    );
  }
}

loadScenarios();
