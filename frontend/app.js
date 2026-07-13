// Sentinel demo frontend — no build step, plain JS.
//
// API_BASE points at localhost for now. After Modal deployment, update this
// to the Modal URL (e.g. https://sentinel-demo--web.modal.run).
const API_BASE = "http://localhost:8000";

const grid = document.getElementById("scenario-grid");
const tracePanel = document.getElementById("trace-panel");
const traceTitle = document.getElementById("trace-title");
const cacheBadge = document.getElementById("cache-badge");
const timeline = document.getElementById("timeline");
const closeBtn = document.getElementById("close-trace");

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
    card.addEventListener("click", () => runScenario(s));
    grid.appendChild(card);
  }
}

function addStep(eventType, label, bodyHtml) {
  const step = document.createElement("div");
  step.className = `step ${eventType}`;
  step.innerHTML = `<span class="step-label">${label}</span>${bodyHtml}`;
  timeline.appendChild(step);
  step.scrollIntoView({ behavior: "smooth", block: "end" });
}

function renderEvent(eventType, data) {
  switch (eventType) {
    case "start":
      if (data.cache_hit) {
        cacheBadge.hidden = false;
      } else {
        cacheBadge.hidden = true;
      }
      break;
    case "alert":
      addStep(
        "alert",
        "[1] ALERT RECEIVED",
        `<pre>${escapeHtml(JSON.stringify(data.payload, null, 2))}</pre>`
      );
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
    case "done":
      break;
    default:
      addStep("step", eventType, `<pre>${escapeHtml(JSON.stringify(data, null, 2))}</pre>`);
  }
}

function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

async function runScenario(scenario) {
  timeline.innerHTML = "";
  cacheBadge.hidden = true;
  traceTitle.textContent = `Reasoning trace — ${scenario.title}`;
  tracePanel.hidden = false;
  tracePanel.scrollIntoView({ behavior: "smooth" });

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

    // POST-based SSE: native EventSource only supports GET, so we read the
    // streaming body manually and parse "event:"/"data:" frames ourselves.
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
  } catch (e) {
    addStep(
      "alert",
      "ERROR",
      `<pre>Could not reach the backend at ${API_BASE}. Is it running?\n${escapeHtml(String(e))}</pre>`
    );
  }
}

loadScenarios();
