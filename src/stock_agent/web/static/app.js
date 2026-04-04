const STEP_ORDER = [
  "plan",
  "market",
  "search_web",
  "extract",
  "decide",
  "write_report",
];

const STEP_LABELS = {
  plan: "Plan",
  market: "Market Snapshot",
  search_web: "Search Web",
  extract: "Extract Evidence",
  decide: "Decide",
  write_report: "Write Report",
};

document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("query-form");
  const queryInput = document.getElementById("query-input");
  const submitButton = document.getElementById("query-submit");
  const statusPanel = document.getElementById("run-status");
  const statusText = document.getElementById("status-text");
  const statusRunId = document.getElementById("status-run-id");
  const statusNote = document.getElementById("status-note");
  const detailTitle = document.getElementById("detail-title");
  const detailTimestamp = document.getElementById("detail-timestamp");
  const detailEmpty = document.getElementById("detail-empty");
  const detailSummary = document.getElementById("detail-summary");
  const detailWarnings = document.getElementById("detail-warnings");
  const reportBody = document.getElementById("report-body");
  const timelineSteps = Array.from(document.querySelectorAll("[data-step]"));

  const summaryCards = {
    market: {
      value: document.getElementById("summary-market-value"),
      meta: document.getElementById("summary-market-meta"),
    },
    sources: {
      value: document.getElementById("summary-sources-value"),
      meta: document.getElementById("summary-sources-meta"),
    },
    notes: {
      value: document.getElementById("summary-notes-value"),
      meta: document.getElementById("summary-notes-meta"),
    },
    confidence: {
      value: document.getElementById("summary-confidence-value"),
      meta: document.getElementById("summary-confidence-meta"),
    },
    followups: {
      value: document.getElementById("summary-followups-value"),
      meta: document.getElementById("summary-followups-meta"),
    },
  };

  const state = {
    runId: null,
    status: "idle",
    latestNode: null,
    snapshot: {},
    summaries: {},
    finalReport: null,
    error: null,
  };

  let eventSource = null;
  let isSubmitting = false;
  let noticeMessage = "";
  let latestTimestamp = null;

  function syncControls() {
    const isBusy = isSubmitting || state.status === "queued" || state.status === "running";
    queryInput.disabled = isBusy;
    submitButton.disabled = isBusy;
  }

  function setNotice(message) {
    noticeMessage = message;
    renderStatus();
  }

  function clearNotice() {
    setNotice("");
  }

  function updateUrl(runId) {
    const nextUrl = new URL(window.location.href);
    if (runId) {
      nextUrl.searchParams.set("run_id", runId);
    } else {
      nextUrl.searchParams.delete("run_id");
    }
    window.history.replaceState({}, "", nextUrl);
  }

  function closeEventStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  function formatTimestamp(timestamp) {
    if (!timestamp) {
      return "";
    }

    const parsed = new Date(timestamp);
    if (Number.isNaN(parsed.getTime())) {
      return "";
    }

    return parsed.toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  }

  function isStaleEvent(timestamp) {
    if (!timestamp || !latestTimestamp) {
      return false;
    }

    const nextTime = new Date(timestamp).getTime();
    const currentTime = new Date(latestTimestamp).getTime();
    if (Number.isNaN(nextTime) || Number.isNaN(currentTime)) {
      return false;
    }

    return nextTime <= currentTime;
  }

  function humanizeKey(key) {
    return key
      .split("_")
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }

  function formatValue(value) {
    if (Array.isArray(value)) {
      return value.length ? value.join(", ") : "None";
    }
    if (typeof value === "boolean") {
      return value ? "Yes" : "No";
    }
    if (value === null || value === undefined || value === "") {
      return "None";
    }
    return String(value);
  }

  function deriveWarnings(node, summary) {
    if (!node || !summary) {
      return [];
    }

    const warnings = [];
    if (
      node === "market" &&
      Number(summary.ticker_count || 0) > 0 &&
      Number(summary.covered_ticker_count || 0) === 0
    ) {
      warnings.push("No usable market snapshot was returned for the planned tickers.");
    }
    if (node === "extract" && Number(summary.new_notes || 0) === 0) {
      warnings.push("No new evidence notes were extracted from the latest source batch.");
    }
    if (
      node === "decide" &&
      ["low", "insufficient"].includes(String(summary.evidence_confidence || ""))
    ) {
      warnings.push("Evidence confidence is low, so the resulting memo should be treated cautiously.");
    }
    return warnings;
  }

  function renderStatus() {
    let title = "Ready for a new research run.";
    let note = noticeMessage || "Submit a query to stream the pipeline live.";

    if (state.status === "queued") {
      title = "Run queued. Waiting for live execution to start.";
      note = noticeMessage || "The workbench will attach to the run automatically.";
    } else if (state.status === "running") {
      title = state.latestNode
        ? `Running ${STEP_LABELS[state.latestNode] || state.latestNode}.`
        : "Run started. Waiting for the first completed step.";
      note = noticeMessage || "Live updates are streaming into the timeline and summary cards.";
    } else if (state.status === "completed") {
      title = "Run completed.";
      note = noticeMessage || "The latest report and step summaries are available below.";
    } else if (state.status === "failed") {
      title = "Run failed.";
      note = noticeMessage || state.error || "The last known snapshot is still visible.";
    }

    statusPanel.dataset.state = state.status;
    statusText.textContent = title;
    statusNote.textContent = note;

    if (state.runId) {
      statusRunId.hidden = false;
      statusRunId.textContent = `Run ID: ${state.runId}`;
    } else {
      statusRunId.hidden = true;
      statusRunId.textContent = "";
    }
  }

  function renderTimeline() {
    const latestIndex = STEP_ORDER.indexOf(state.latestNode);

    timelineSteps.forEach((stepElement) => {
      const step = stepElement.dataset.step;
      const stepIndex = STEP_ORDER.indexOf(step);
      const stateLabel = stepElement.querySelector(".step-state");
      let nextState = "idle";
      let nextLabel = "Idle";

      if (state.status === "queued") {
        nextLabel = "Queued";
      } else if (state.status === "running" && state.latestNode === null && stepIndex === 0) {
        nextState = "active";
        nextLabel = "Active";
      } else if (state.status === "completed") {
        const completionIndex = latestIndex >= 0 ? latestIndex : STEP_ORDER.length - 1;
        if (stepIndex <= completionIndex) {
          nextState = "completed";
          nextLabel = "Completed";
        }
      } else if (state.status === "failed" && latestIndex >= 0) {
        if (stepIndex < latestIndex) {
          nextState = "completed";
          nextLabel = "Completed";
        } else if (stepIndex === latestIndex) {
          nextState = "failed";
          nextLabel = "Failed";
        }
      } else if (state.status === "running" && latestIndex >= 0) {
        if (stepIndex < latestIndex) {
          nextState = "completed";
          nextLabel = "Completed";
        } else if (stepIndex === latestIndex) {
          nextState = "active";
          nextLabel = "Active";
        }
      }

      stepElement.dataset.state = nextState;
      if (stateLabel) {
        stateLabel.textContent = nextLabel;
      }
    });
  }

  function renderDetail() {
    const summary = state.latestNode ? state.summaries[state.latestNode] : null;
    const warnings = deriveWarnings(state.latestNode, summary);
    const formattedTimestamp = formatTimestamp(latestTimestamp);

    detailSummary.replaceChildren();
    detailWarnings.replaceChildren();
    detailTimestamp.textContent = formattedTimestamp;

    if (!state.latestNode) {
      detailTitle.textContent = state.status === "failed" ? "Run Failed" : "Active Step Detail";
      detailEmpty.hidden = false;
      detailEmpty.textContent =
        state.status === "running"
          ? "The run has started. Waiting for the first completed step."
          : state.status === "queued"
            ? "The run is queued. Live step details will appear here once execution begins."
            : state.status === "failed"
              ? state.error || "The run failed before any step completed."
              : "Step-level details will appear here once streaming begins.";
      return;
    }

    detailTitle.textContent = STEP_LABELS[state.latestNode] || state.latestNode;
    detailEmpty.hidden = false;
    detailEmpty.textContent = "";

    if (summary && Object.keys(summary).length > 0) {
      const rows = Object.entries(summary).map(([key, value]) => {
        const wrapper = document.createElement("div");
        const term = document.createElement("dt");
        const description = document.createElement("dd");
        term.textContent = humanizeKey(key);
        description.textContent = formatValue(value);
        wrapper.append(term, description);
        return wrapper;
      });
      detailSummary.replaceChildren(...rows);
      detailEmpty.hidden = true;
    } else if (state.status === "failed" && state.error) {
      const wrapper = document.createElement("div");
      const term = document.createElement("dt");
      const description = document.createElement("dd");
      term.textContent = "Error";
      description.textContent = state.error;
      wrapper.append(term, description);
      detailSummary.replaceChildren(wrapper);
      detailEmpty.hidden = true;
    } else {
      detailEmpty.hidden = false;
      detailEmpty.textContent = "No structured summary is available for the latest step yet.";
    }

    if (warnings.length > 0) {
      const items = warnings.map((message) => {
        const item = document.createElement("li");
        item.textContent = message;
        return item;
      });
      detailWarnings.replaceChildren(...items);
    }
  }

  function updateSummaryCard(card, value, meta) {
    card.value.textContent = value;
    card.meta.textContent = meta;
  }

  function renderSummaryCards() {
    const market = state.summaries.market || null;
    const sources = state.summaries.search_web || null;
    const notes = state.summaries.extract || null;
    const decide = state.summaries.decide || null;

    if (market) {
      updateSummaryCard(
        summaryCards.market,
        `${market.covered_ticker_count || 0} / ${market.ticker_count || 0} tickers covered`,
        `${market.tickers_with_price || 0} with price, ${market.tickers_with_market_cap || 0} with market cap`,
      );
    } else {
      updateSummaryCard(
        summaryCards.market,
        "Waiting for market data.",
        "Coverage and key metrics will appear here.",
      );
    }

    if (sources) {
      updateSummaryCard(
        summaryCards.sources,
        `${sources.total_sources || 0} total sources`,
        `Latest search added ${sources.new_sources || 0} sources.`,
      );
    } else {
      updateSummaryCard(
        summaryCards.sources,
        "Waiting for source search.",
        "Source counts update after each search pass.",
      );
    }

    if (notes) {
      updateSummaryCard(
        summaryCards.notes,
        `${notes.total_notes || 0} total notes`,
        `Latest extraction added ${notes.new_notes || 0} notes.`,
      );
    } else {
      updateSummaryCard(
        summaryCards.notes,
        "Waiting for extraction.",
        "Extracted evidence notes will be counted here.",
      );
    }

    if (decide) {
      updateSummaryCard(
        summaryCards.confidence,
        `${formatValue(decide.evidence_confidence)} confidence`,
        `Need more evidence: ${formatValue(decide.need_more)}.`,
      );
    } else {
      updateSummaryCard(
        summaryCards.confidence,
        "Waiting for decision.",
        "The latest confidence assessment will appear here.",
      );
    }

    const followupCount = decide
      ? Number(decide.followup_count || 0)
      : Array.isArray(state.snapshot.followup_queries)
        ? state.snapshot.followup_queries.length
        : 0;
    updateSummaryCard(
      summaryCards.followups,
      `${followupCount} follow-up quer${followupCount === 1 ? "y" : "ies"}`,
      decide
        ? `Need more evidence: ${formatValue(decide.need_more)}.`
        : "Loop decisions will update this count.",
    );
  }

  function renderReport() {
    const reportText =
      state.status === "completed"
        ? state.finalReport || state.snapshot.final_report || ""
        : "";

    if (reportText) {
      reportBody.textContent = reportText;
      reportBody.classList.remove("empty-state");
    } else {
      reportBody.textContent =
        state.status === "failed"
          ? "No final report is available because the run ended in failure."
          : "The completed memo will render here automatically after the run finishes.";
      reportBody.classList.add("empty-state");
    }
  }

  function render() {
    renderStatus();
    renderTimeline();
    renderDetail();
    renderSummaryCards();
    renderReport();
    syncControls();
  }

  function resetForNewRun(runId, query) {
    closeEventStream();
    state.runId = runId;
    state.status = "queued";
    state.latestNode = null;
    state.snapshot = { query };
    state.summaries = {};
    state.finalReport = null;
    state.error = null;
    latestTimestamp = null;
    clearNotice();
    render();
  }

  function applySnapshot(snapshotPayload) {
    state.runId = snapshotPayload.run_id;
    state.status = snapshotPayload.status || "idle";
    state.latestNode = snapshotPayload.latest_node || null;
    state.snapshot = {
      ...(snapshotPayload.snapshot || {}),
      query: snapshotPayload.query,
    };
    state.summaries = { ...(snapshotPayload.summaries || {}) };
    state.finalReport = snapshotPayload.final_report || null;
    state.error = snapshotPayload.error || null;
    latestTimestamp = snapshotPayload.updated_at || null;
    render();
  }

  function handleWorkbenchEvent(event) {
    if (isStaleEvent(event.timestamp)) {
      return;
    }

    if (event.type === "run_started") {
      state.status = "running";
      state.latestNode = null;
      state.snapshot = {
        ...(event.snapshot || {}),
        query: event.query || state.snapshot.query || "",
      };
      state.error = null;
      state.finalReport = null;
      latestTimestamp = event.timestamp || latestTimestamp;
      clearNotice();
      render();
      return;
    }

    if (event.type === "step_completed") {
      state.status = "running";
      state.latestNode = event.node || state.latestNode;
      state.snapshot = {
        ...(event.snapshot || {}),
        query: state.snapshot.query || "",
      };
      state.summaries = {
        ...state.summaries,
        [event.node]: { ...(event.summary || {}) },
      };
      state.error = null;
      latestTimestamp = event.timestamp || latestTimestamp;
      clearNotice();
      render();
      return;
    }

    if (event.type === "run_completed") {
      state.status = "completed";
      state.latestNode = state.latestNode || "write_report";
      state.snapshot = {
        ...(event.snapshot || {}),
        query: state.snapshot.query || "",
      };
      state.finalReport = event.final_report || state.snapshot.final_report || "";
      state.error = null;
      latestTimestamp = event.timestamp || latestTimestamp;
      clearNotice();
      render();
      closeEventStream();
      return;
    }

    if (event.type === "run_failed") {
      state.status = "failed";
      state.latestNode = event.node || state.latestNode;
      state.snapshot = {
        ...(event.snapshot || {}),
        query: state.snapshot.query || "",
      };
      state.error = event.error || "Run failed.";
      latestTimestamp = event.timestamp || latestTimestamp;
      clearNotice();
      render();
      closeEventStream();
    }
  }

  function openEventStream(runId) {
    closeEventStream();
    eventSource = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
    ["run_started", "step_completed", "run_completed", "run_failed"].forEach((eventType) => {
      eventSource.addEventListener(eventType, (message) => {
        const payload = JSON.parse(message.data);
        handleWorkbenchEvent(payload);
      });
    });
  }

  async function hydrateFromLocation() {
    const runId = new URL(window.location.href).searchParams.get("run_id");
    if (!runId) {
      render();
      return;
    }

    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        updateUrl(null);
        setNotice(errorPayload.detail || "Unable to restore the requested run.");
        render();
        return;
      }

      clearNotice();
      const snapshotPayload = await response.json();
      applySnapshot(snapshotPayload);
      queryInput.value = snapshotPayload.query || "";
      if (snapshotPayload.status === "queued" || snapshotPayload.status === "running") {
        openEventStream(runId);
      }
    } catch (error) {
      setNotice("Unable to restore the requested run.");
      render();
    }
  }

  form.addEventListener("submit", async (submitEvent) => {
    submitEvent.preventDefault();
    const query = queryInput.value.trim();
    if (!query) {
      setNotice("Query must not be empty.");
      return;
    }

    isSubmitting = true;
    clearNotice();
    syncControls();

    try {
      const response = await fetch("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });

      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        setNotice(errorPayload.detail || "Unable to create a new run.");
        return;
      }

      const payload = await response.json();
      updateUrl(payload.run_id);
      resetForNewRun(payload.run_id, query);
      openEventStream(payload.run_id);
    } catch (error) {
      setNotice("Unable to create a new run.");
    } finally {
      isSubmitting = false;
      syncControls();
    }
  });

  render();
  hydrateFromLocation();
});
