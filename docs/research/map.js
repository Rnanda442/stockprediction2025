"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const NS = "http://www.w3.org/2000/svg";
  const list = (value) => Array.isArray(value) ? value : [];
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (mark) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[mark]));
  const human = (value) => String(value ?? "").replace(/[_]+/g, " ").replace(/\s+/g, " ").trim();
  const normalizeSearch = (value) => String(value ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").replace(/[‐‑‒–—−]/g, "-").replace(/[^\p{L}\p{N}]+/gu, " ").trim().toLowerCase();
  const plain = (value) => {
    if (value == null || value === "") return "Not recorded";
    if (Array.isArray(value)) return value.map(plain).join(" · ");
    if (typeof value === "object") return Object.entries(value).map(([key, item]) => `${human(key)}: ${plain(item)}`).join(" · ");
    return String(value);
  };
  const sentences = (value) => value == null ? [] : Array.isArray(value) ? value.map(plain) : [plain(value)];
  const useLabels = { reusable_software: "Reusable software", exploratory_signal: "Exploratory finding", diagnostic_only: "Diagnostic only", failed: "Negative result", blocked: "Blocked", notrun: "Not run", "blocked/notrun": "Blocked or not run" };
  const useClasses = { reusable_software: "software", exploratory_signal: "signal", diagnostic_only: "diagnostic", failed: "failed", blocked: "blocked", notrun: "notrun", "blocked/notrun": "blocked" };
  const highlightIds = new Set([
    "trajectory_master_month_v1_20261108", "residual_sleeve_family_confirmation_v1",
    "architecture_v2_volatility_conditioned_dual_head_mc_v1", "market_pattern_group_generalization_walk_forward_v1",
    "earnings_geometry_paper_replay_v1_20261117",
  ]);
  const stageAliases = {
    market_data: ["Market observations", "Input", "Archive exists; fresh feed unqualified"],
    availability_clock: ["Availability & clock", "Timing", "Deadline still unverified"],
    forecast_contract: ["Forecast question", "Question", "Target not frozen"],
    features_universe: ["Features & universe", "Measurement", "Measurements exist"],
    model: ["Return & risk model", "Estimation", "No model promoted"],
    entry_review: ["New-stock review", "Entry branch", "Policy not connected"],
    portfolio_state: ["Holdings & cash", "State input", "Actual state unconfirmed"],
    holding_review: ["Holding review", "Holding branch", "Mechanics exist; policy pending"],
    constraints: ["Cash & risk checks", "Validation", "Configuration pending"],
    paper_report: ["Daily paper review", "Review", "Integration pending"],
    paper_ledger: ["Paper state ledger", "Accounting", "Engine built; runner missing"],
    mature_outcomes: ["Later outcomes", "Evaluation", "Future process"],
  };
  const state = { view: "workflow", stageId: "", methodId: "", nodeId: "", graph: null, stages: [], pipelineEdges: [], studies: [], nodes: [], edges: [], evidence: new Map(), nodeById: new Map(), studyById: new Map(), expandedConnections: false, expandedStageIds: new Set(), workflowSections: new Map(), workflowPartIndex: new Map(), crossSectionEdges: [], childStageId: "", partId: "", partFocusId: "", showAllParts: false, overviewScrollY: 0, visibleParts: [], detailMode: "stage", loaded: false };
  const flowSlots = {
    market_data: [1, 2], availability_clock: [2, 2], forecast_contract: [3, 2], features_universe: [4, 2],
    model: [5, 2], portfolio_state: [5, 3], entry_review: [6, 1], holding_review: [6, 3],
    constraints: [7, 2], paper_report: [8, 2], paper_ledger: [9, 2], mature_outcomes: [10, 2],
  };
  const flowRelationLabels = {
    attested_as_of: "is checked against", constrains_timing: "sets the timing for", defines_requirements: "defines the required measurements for",
    supplies_features: "supplies measurements to", defines_target: "defines the forecast target for", supplies_estimates: "supplies estimates to",
    identifies_held_positions: "identifies the held positions for", proposes_entry: "sends entry proposals to",
    proposes_continuation_or_exit: "sends continuation or exit proposals to", constrains_cash_and_positions: "supplies cash and position limits to",
    accepts_or_blocks: "accepts or blocks actions for", supplies_immutable_valid_intents: "passes saved valid intents to",
    records_outcome_accounting: "records accounting for", preserves_prior_decisions: "preserves advance decisions for",
  };

  let detailScrollEpoch = 0;
  state.workflowView = "canvas"; state.canvasScope = "connected"; state.canvas = null; state.canvasSelectedEdge = null;
  state.openBranches = new Set(); state.collapsedCategories = new Set(); state.openLeaves = new Set(); state.displayFieldIndex = new Map(); state.displaySelectionId = "";
  state.readiness = null; state.stageReadiness = new Map(); state.partReadiness = new Map(); state.edgeReadiness = new Map(); state.readinessFilter = "current";
  const readinessLabels = { connected: "Connected", needs_connection: "Needs connection", blocked: "Blocked", historical: "Historical", unknown: "Not reviewed" };
  function readinessFor(id) { return state.stageReadiness.get(id) || state.partReadiness.get(id) || { operational_status: "unknown", reason: "No reviewed readiness statement is loaded for this record." }; }
  function readinessPill(status, suffix = "") { return `<span class="readiness-pill readiness-${esc(status || "unknown")}">${esc(readinessLabels[status] || "Not reviewed")}${suffix ? ` · ${esc(suffix)}` : ""}</span>`; }
  function readinessCard(id) {
    const record = readinessFor(id), status = record.operational_status, missing = record.missing || record.reason || "No missing-work statement was recorded here.", done = record.done_when || "No completion criterion was recorded for this record.";
    const answer = (title, value) => `<div class="readiness-answer"><h5>${esc(title)}</h5><p>${esc(plain(value || "Not established by the reviewed record."))}</p></div>`;
    const saved = list(record.stored_study_results);
    const results = saved.length ? `<div class="readiness-saved-results"><p class="meta">Saved study evidence is separate from operational readiness. No model is promoted; blocked or unrun plans are not completed tests.</p>${saved.map((study) => `<article><h5>${esc(plain(study.friendly_title || study.canonical_id || study.experiment_id))}</h5><p>${esc(plain(study.reviewed_verdict))}</p><p class="meta">Recorded use: ${esc(human(study.appropriate_use_status || "Not recorded"))} · model not promoted</p></article>`).join("")}</div>` : "";
    return `<section class="readiness-card" aria-label="Reviewed workflow readiness"><div class="readiness-card-heading">${readinessPill(status)}<span class="evidence-separate">${saved.length ? `${saved.length} saved-study evidence links · separate` : "Research results are separate"}</span></div>${answer("What is missing?", missing)}${answer("When is this done?", done)}<details><summary>Purpose, inputs, existing work and learning</summary>${answer("What is this for?", record.purpose)}${answer("What does it need?", record.needs)}${answer("What already exists?", record.exists)}${answer("What did the research teach us?", record.research_result)}${results}</details><details><summary>Readiness evidence and review date</summary><p class="meta">Reviewed ${esc(record.last_reviewed || state.readiness?.as_of || "date unavailable")} · snapshot, not continuous monitoring.</p>${evidenceMarkup(list(record.evidence_ids))}<p class="meta">Green means a reviewed connection for this record’s displayed purpose. It does not mean a profitable model or an operating daily service.</p></details></section>`;
  }
  function bindReadiness(data) {
    const statuses = new Set(["connected", "needs_connection", "blocked", "historical"]), stages = list(data.stage_cards), parts = list(data.part_readiness), edges = list(data.main_edge_statuses);
    const edgeKey = (edge) => `${edge.source}\u0000${edge.target}\u0000${edge.relation}`;
    if (["workflow-view.public.json", "workflow-details.public.json", "method-catalog.public.json", "project-graph.public.json", "evidence-index.public.json"].some((file) => !state.sourceContentHashes?.[file] || data.source_snapshot?.[file]?.content_sha256 !== state.sourceContentHashes[file])) return false;
    if (stages.length !== state.stages.length || parts.length !== state.workflowPartIndex.size || edges.length !== state.pipelineEdges.length) return false;
    if (new Set(stages.map((record) => record.stage_id)).size !== stages.length || new Set(parts.map((record) => record.part_id)).size !== parts.length || new Set(edges.map(edgeKey)).size !== edges.length) return false;
    if (stages.some((record) => !state.stages.some((stage) => stage.id === record.stage_id) || !statuses.has(record.operational_status)) || parts.some((record) => !state.workflowPartIndex.has(record.part_id) || !statuses.has(record.operational_status)) || edges.some((record) => !state.pipelineEdges.some((edge) => edgeKey(edge) === edgeKey(record)) || !statuses.has(record.operational_status))) return false;
    state.readiness = data; state.stageReadiness = new Map(stages.map((record) => [record.stage_id, record])); state.partReadiness = new Map(parts.map((record) => [record.part_id, record])); state.edgeReadiness = new Map(edges.map((record) => [edgeKey(record), record])); return true;
  }
  function readinessMatches(id) {
    if (!state.readiness || state.readinessFilter === "everything") return true;
    const facets = readinessFor(id).view_facets;
    if (Array.isArray(facets)) return facets.includes({ current: "current_workflow", attention: "needs_attention", past: "past_research" }[state.readinessFilter]);
    const status = readinessFor(id).operational_status;
    return state.readinessFilter === "attention" ? ["blocked", "needs_connection"].includes(status) : state.readinessFilter === "past" ? status === "historical" : status !== "historical";
  }
  function readinessDetailFilter() {
    const matched = new Set([...state.workflowPartIndex.keys()].filter(readinessMatches)), allowed = new Set(matched);
    const retain = (id) => { const entry = state.workflowPartIndex.get(id); if (!entry) return; for (const node of partAncestors(state.workflowSections.get(entry.stageId), id)) allowed.add(node.id); };
    for (const id of matched) { retain(id); const node = state.workflowPartIndex.get(id).node; for (const ref of [...list(node.inputs), ...list(node.outputs)]) if (typeof ref === "string" && state.workflowPartIndex.has(ref)) { allowed.add(ref); retain(ref); } }
    if (state.readinessPinnedPart) { allowed.add(state.readinessPinnedPart); retain(state.readinessPinnedPart); }
    return { matched, allowed };
  }
  function decorateReadiness(graph) {
    for (const node of graph.nodes) {
      let record = readinessFor(node.id);
      if (node.display_only && node.owners?.length) {
        const rank = { blocked: 4, needs_connection: 3, unknown: 2, connected: 1, historical: 0 };
        record = node.owners.map(readinessFor).sort((a, b) => (rank[b.operational_status] || 0) - (rank[a.operational_status] || 0))[0];
      }
      node.title = node.title || node.label; node.readiness = record.operational_status || "unknown"; node.readinessLabel = readinessLabels[node.readiness] || "Not reviewed"; node.readinessReason = plain(record.reason || record.missing || "Read the reviewed readiness card.");
      if (node.role === "stage") node.label = `${node.title}\n${node.readinessLabel}`;
    }
    for (const edge of graph.edges) {
      const record = state.edgeReadiness.get(`${edge.source}\u0000${edge.target}\u0000${edge.relation}`);
      if (record) { edge.readiness = record.operational_status; edge.readinessLabel = readinessLabels[record.operational_status]; edge.readinessReason = plain(record.reason); edge.readinessEvidenceIds = list(record.evidence_ids); }
    }
    return graph;
  }
  function renderReadinessControls() {
    $("readiness-snapshot").textContent = state.readiness ? `Readiness snapshot · ${state.readiness.as_of}` : "Readiness review unavailable · no connection inferred";
    const blocked = [...state.stageReadiness.values()].filter((record) => record.operational_status === "blocked").length, needs = [...state.stageReadiness.values()].filter((record) => record.operational_status === "needs_connection").length;
    $("readiness-blockers").textContent = state.readiness ? `${blocked} stages blocked · ${needs} need connection` : "Readiness is not verified";
    $("readiness-connections").textContent = state.readiness ? `${[...state.edgeReadiness.values()].filter((record) => record.operational_status === "connected").length} verified main connections; the 15 dependency arrows remain documentary context.` : "No connection status is inferred without the reviewed supplement.";
    $("readiness-snapshot").dataset.shortLabel = state.readiness ? `Snapshot · ${state.readiness.as_of}` : "Readiness unavailable";
    $("workflow-readiness-filter").value = state.readinessFilter; $("workflow-readiness-filter").disabled = !state.readiness || state.canvasScope !== "connected";
    $("readiness-scope-note").textContent = state.canvasScope === "connected" ? state.partId && !readinessMatches(state.partId) ? "This linked record is outside the selected filter and remains visible as context." : "Filters affect detail records; all 12 road stops and 15 main arrows remain as context." : "Readiness filters apply to the ordered roadmap. This secondary scope keeps its complete selected record set.";
  }
  function announce(value) { $("announcement").textContent = value; }
  function resetWorkflowDetailScroll() {
    detailScrollEpoch += 1;
    for (const id of ["stage-detail", "stage-sheet", "stage-sheet-content"]) $(id).scrollTop = 0;
  }
  function showWorkflowDetailSheet() {
    const sheet = $("stage-sheet");
    if (!sheet.open) sheet.showModal();
    // Opening a previously hidden dialog can restore its old scroll/focus after content was reset.
    $("close-stage-sheet").focus({ preventScroll: true });
    resetWorkflowDetailScroll();
    const epoch = detailScrollEpoch;
    requestAnimationFrame(() => {
      if (sheet.open && epoch === detailScrollEpoch) for (const id of ["stage-sheet", "stage-sheet-content"]) $(id).scrollTop = 0;
    });
  }
  function useStatus(study) { return study.appropriate_use?.status || study.use_status || "diagnostic_only"; }
  function positiveResearch(study) { return study.has_positive_finding === true && study.positive_finding_type !== "completed_software_or_data"; }
  function studyTitle(study) { return study.friendly_title || study.title || human(study.canonical_id); }
  function familyTitle(study) { return typeof study.family === "object" ? study.family.title || study.family.name || study.family.id : study.family_title || study.family || study.family_id || "Other research"; }
  function familyId(study) { return typeof study.family === "object" ? study.family.id || familyTitle(study) : study.family_id || study.family || "other"; }
  function nodeTitle(node) { return node?.experiment?.friendly_title || node?.title || node?.label || human(node?.id) || "Project record"; }
  function evidenceIds(record) { return list(record.evidence_ids || record.source_refs || record.sources).map((item) => typeof item === "string" ? item : item.id).filter(Boolean); }
  function pill(status, label) { return `<span class="pill ${esc(useClasses[status] || "diagnostic")}">${esc(label || useLabels[status] || human(status) || "Status not recorded")}</span>`; }
  function itemList(items) {
    return `<ul class="clean-list">${sentences(items).map((item) => {
      const reference = state.workflowPartIndex.get(item);
      if (!reference) return `<li>${esc(item)}</li>`;
      const stage = state.stages.find((candidate) => candidate.id === reference.stageId);
      return `<li><button class="part-inline-link" type="button" data-part-reference="${esc(item)}" title="${esc(item)}">${esc(reference.node.title)}</button><small class="part-reference-context">${esc(stage ? shortStageTitle(stage) : reference.stageId)}</small><span class="sr-only">Canonical part ID: ${esc(item)}</span></li>`;
    }).join("")}</ul>`;
  }
  function section(title, content) { return content ? `<section class="detail-section"><h4>${esc(title)}</h4>${content}</section>` : ""; }

  function evidenceMarkup(ids) {
    const entries = [...new Set(ids)].map((id) => state.evidence.get(id) || { id, title: human(id) });
    if (!entries.length) return '<p class="meta">No additional evidence reference was attached.</p>';
    return `<ul class="evidence-list">${entries.map((source) => `<li><strong>${esc(source.title || human(source.id))}</strong>${source.authority ? `<div class="meta">${esc(human(source.authority))}</div>` : ""}${source.scope ? `<div class="meta">${esc(plain(source.scope))}</div>` : ""}${source.sha256 ? `<div class="file-path">Source digest ${esc(source.sha256.slice(0, 16))}…</div>` : ""}</li>`).join("")}</ul>`;
  }

  function setHash() {
    const params = new URLSearchParams({ view: state.view });
    if (state.view === "workflow" && state.stageId) params.set("stage", state.stageId);
    if (state.view === "workflow" && state.childStageId) { params.set("parts", "1"); if (state.partFocusId) params.set("focus", state.partFocusId); if (state.partId) params.set("part", state.partId); if (state.showAllParts) params.set("all", "1"); }
    if (state.view === "workflow" && state.canvasScope === "all") params.set("scope", "all");
    if (state.view === "workflow" && state.canvasScope === "connected") { params.set("scope", "connected"); if (state.openBranches.size) params.set("branches", [...state.openBranches].join(",")); }
    if (state.view === "workflow" && state.canvasScope === "context") params.set("scope", "context");
    if (state.view === "models" && state.methodId) params.set("method", state.methodId);
    if (state.view === "relations" && state.nodeId) params.set("node", state.nodeId);
    try { history.replaceState(null, "", `#${params.toString()}`); } catch { /* Read-only embedded previews may disallow URL updates. */ }
  }
  function setView(view, updateHash = true) {
    if (!["workflow", "models", "relations"].includes(view)) view = "workflow";
    state.view = view;
    document.querySelectorAll("[data-view]").forEach((button) => {
      const selected = button.dataset.view === view;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    });
    ["workflow", "models", "relations"].forEach((name) => { $(`view-${name}`).hidden = name !== view; });
    if (view === "workflow") requestAnimationFrame(drawWorkflowWires);
    if (view === "workflow") requestAnimationFrame(() => state.canvas?.resize());
    if (view === "relations") requestAnimationFrame(drawRelations);
    if (updateHash) setHash();
  }
  function openMethod(id) {
    if (!state.studyById.has(id)) return;
    state.methodId = id; $("model-search").value = ""; $("model-status").value = "all"; $("model-family").value = "all";
    renderModels(); setView("models");
    $("model-detail").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  function openNode(id) {
    if (!state.nodeById.has(id)) return;
    state.nodeId = id; state.expandedConnections = false;
    $("relation-search").value = ""; $("relation-type").value = "all";
    renderRecords(); renderRecordDetail(); setView("relations");
    $("record-detail").scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function stageClass(stage) {
    const value = String(stage.readiness || stage.status || "").toLowerCase();
    if (/missing|block|pending|not.?connect|not.?run|design|unregister|unqualif|unverif|unconfirm/.test(value)) return "gap";
    if (/research|explor|diagnos|unpromot|study/.test(value)) return "research";
    return "built";
  }
  function stageTitle(stage) { return stage.title || stage.label || human(stage.id); }
  function shortStageTitle(stage) { return stage.short_title || stageAliases[stage.id]?.[0] || stageTitle(stage); }
  function stageStatus(stage) { return stage.status_label || stage.readiness_label || stageAliases[stage.id]?.[2] || human(stage.readiness || stage.status) || "Readiness described in evidence"; }
  function edgeSource(edge) { return edge.source || edge.from; }
  function edgeTarget(edge) { return edge.target || edge.to; }
  function edgeRelation(edge) { return human(edge.relation || edge.type || edge.label || "connects to"); }
  function stageLinks(stage) {
    const ids = list(stage.study_ids || stage.experiment_ids || stage.linked_studies || stage.model_ids).map((item) => typeof item === "string" ? item : item.canonical_id || item.id);
    const nodeIds = new Set(list(stage.node_ids || stage.canonical_node_ids || stage.record_ids));
    for (const edge of state.edges) {
      if (nodeIds.has(edge.source) || nodeIds.has(edge.target)) {
        const other = nodeIds.has(edge.source) ? edge.target : edge.source;
        if (other.startsWith("experiment:") && state.studyById.has(other.slice(11))) ids.push(other.slice(11));
      }
    }
    return [...new Set(ids)].map((id) => state.studyById.get(id)).filter(Boolean);
  }
  function openStage(id, showSheet = true) {
    if (!state.stages.some((stage) => stage.id === id)) return;
    state.stageId = id; renderWorkflow(); setHash();
    if (showSheet && window.matchMedia("(max-width: 850px)").matches && !$("stage-sheet").open) showWorkflowDetailSheet();
    else $("workflow-map").querySelector(".stage-button.selected")?.focus({ preventScroll: true });
    announce(`Selected ${stageTitle(state.stages.find((stage) => stage.id === id))}`);
  }
  function activeWorkflowButton() { return state.workflowView === "canvas" ? $("canvas-viewport") : state.childStageId ? $("workflow-map").querySelector(".part-button.selected") || $("part-about-stage") : $("workflow-map").querySelector(".stage-button.selected"); }
  function setPartSearchVisible(visible, focus = false) {
    $("part-find-field").hidden = !visible;
    $("part-find-toggle").setAttribute("aria-expanded", String(visible));
    if (visible && focus) $("part-search").focus({ preventScroll: true });
  }
  function openWorkflowStep(id) {
    if (!state.stages.some((stage) => stage.id === id)) return;
    if (state.canvasScope === "connected") { activateConnectedNode(id); return; }
    state.canvasScope = "context"; state.canvasSelectedEdge = null;
    if (!state.childStageId) state.overviewScrollY = window.scrollY;
    if ($("stage-sheet").open) $("stage-sheet").close();
    state.stageId = id; state.childStageId = id; state.partId = ""; state.partFocusId = ""; state.showAllParts = false; state.detailMode = "stage"; $("part-search").value = "";
    setPartSearchVisible(false);
    renderWorkflow(); setHash();
    requestAnimationFrame(() => { $("workflow-breadcrumb").scrollIntoView({ block: "start" }); $("workflow-breadcrumb").querySelector("button")?.focus({ preventScroll: true }); });
    announce(`Opened the connected parts of ${shortStageTitle(state.stages.find((stage) => stage.id === id))}`);
  }
  function returnToWorkflow() {
    if ($("stage-sheet").open) $("stage-sheet").close();
    state.canvasScope = "context"; state.canvasSelectedEdge = null;
    state.childStageId = ""; state.partId = ""; state.partFocusId = ""; state.showAllParts = false; state.detailMode = "stage"; $("part-search").value = "";
    renderWorkflow(); setHash(); requestAnimationFrame(() => { window.scrollTo({ top: state.overviewScrollY }); activeWorkflowButton()?.focus({ preventScroll: true }); });
  }
  function renderWorkflow() {
    if (!state.stages.length) {
      $("workflow-map").innerHTML = '<p class="empty">No reviewed workflow stages were supplied.</p>';
      $("stage-detail").innerHTML = '<h3>Workflow evidence unavailable</h3><p class="detail-summary">The page does not infer a working pipeline from variable definitions.</p>';
      return;
    }
    if (!state.stages.some((stage) => stage.id === state.stageId)) state.stageId = state.stages[0].id;
    $("workflow-map").classList.add("flow-network"); $("workflow-map").classList.remove("part-network");
    $("workflow-map").innerHTML = '<svg class="workflow-wires" aria-hidden="true"></svg>' + state.stages.map((stage, index) => {
      const [row, column] = flowSlots[stage.id] || [index + 1, 2];
      const selected = stage.id === state.stageId;
      const lane = stage.id === "entry_review" ? "New-stock branch" : stage.id === "holding_review" ? "Held-stock branch" : stage.id === "portfolio_state" ? "Separate supplied input" : "";
      return `<div class="flow-slot flow-${esc(stage.id)} ${selected ? "selected" : ""}" style="--flow-row:${row};--flow-column:${column}" data-flow-id="${esc(stage.id)}">${lane ? `<span class="flow-lane-title">${esc(lane)}</span>` : ""}<button class="stage-button flow-node ${stageClass(stage)} ${selected ? "selected" : ""}" type="button" data-stage="${esc(stage.id)}" aria-label="Explore parts of ${esc(shortStageTitle(stage))}" title="Open this step's connected child diagram"><b>${esc(shortStageTitle(stage))}<span class="expand-mark" aria-hidden="true"> +</span></b><span class="stage-state">${esc(stageStatus(stage))}</span></button><details class="flow-node-notes" data-stage-note="${esc(stage.id)}" ${state.expandedStageIds.has(stage.id) ? "open" : ""}><summary>Step summary</summary><p>${esc(stage.question || "Read this stage's purpose and evidence.")}</p><p>${esc(plain(stage.known || stage.existing || "See the selected-stage inspector."))}</p></details></div>`;
    }).join("");
    $("workflow-map").querySelectorAll("[data-stage]").forEach((button) => button.addEventListener("click", () => openWorkflowStep(button.dataset.stage)));
    $("workflow-map").querySelectorAll("[data-stage-note]").forEach((detail) => detail.addEventListener("toggle", () => { detail.open ? state.expandedStageIds.add(detail.dataset.stageNote) : state.expandedStageIds.delete(detail.dataset.stageNote); requestAnimationFrame(drawWorkflowWires); }));
    const stage = state.stages.find((item) => item.id === state.stageId);
    const existing = stage.existing || stage.what_exists || stage.what_we_have || stage.known || stage.built || stage.current_state;
    const remaining = stage.remaining || stage.remaining_delta || stage.missing || stage.open_dependencies;
    const studies = stageLinks(stage);
    const nodeIds = list(stage.node_ids || stage.canonical_node_ids || stage.record_ids || stage.linked_nodes);
    const records = nodeIds.map((id) => state.nodeById.get(id)).filter(Boolean);
    const linked = studies.map((study) => `<button class="record-chip" data-open-method="${esc(study.canonical_id)}">${esc(studyTitle(study))}</button>`).join("");
    const connections = state.pipelineEdges.filter((edge) => edgeSource(edge) === stage.id || edgeTarget(edge) === stage.id);
    const connectionMarkup = connections.map((edge) => {
      const from = state.stages.find((item) => item.id === edgeSource(edge)); const to = state.stages.find((item) => item.id === edgeTarget(edge));
      if (!from || !to) return "";
      const incoming = to.id === stage.id;
      const verb = flowRelationLabels[edge.relation] || edgeRelation(edge);
      return `<div class="flow-dependency"><span>${incoming ? "Incoming dependency" : "Outgoing connection"}</span><p><button data-connected-stage="${esc(from.id)}">${esc(shortStageTitle(from))}</button> ${esc(verb)} <button data-connected-stage="${esc(to.id)}">${esc(shortStageTitle(to))}</button>.</p></div>`;
    }).join("");
    const libraryAction = stage.id === "model" ? `<button class="button subtle workflow-library-link" data-workflow-library="models">View saved models and results (${state.studies.length})</button>` : stage.id === "features_universe" ? `<button class="button subtle workflow-library-link" data-workflow-library="variables">Explore the 59 variable definitions</button>` : "";
    const detailMarkup = `<div class="detail-kicker"><p class="eyebrow">Selected step</p>${pill(stageClass(stage) === "gap" ? "blocked" : "diagnostic_only", stageStatus(stage))}</div><h3>${esc(stageTitle(stage))}</h3><p class="detail-summary">${esc(stage.summary || stage.question || stage.description || stage.purpose || "Read the saved work and its limits for this stage.")}</p>${libraryAction}${section("What we already have", itemList(existing || ["See the linked reviewed project records."]))}${section("What remains", itemList(remaining || ["No additional implementation claim is inferred from this map."]))}${section("Connects with", connectionMarkup)}${studies.length ? section("Related studies", `<div class="linked-records">${linked}</div>`) : ""}${records.length ? section("Explore connections", `<div class="linked-records">${records.map((node) => `<button class="record-chip" data-open-node="${esc(node.id)}">${esc(nodeTitle(node))}</button>`).join("")}</div>`) : ""}<details class="detail-section"><summary>Source evidence</summary>${evidenceMarkup(evidenceIds(stage))}</details>`;
    $("stage-detail").innerHTML = readinessCard(stage.id) + detailMarkup; $("stage-sheet-content").innerHTML = readinessCard(stage.id) + detailMarkup;
    resetWorkflowDetailScroll();
    $("stage-sheet-title").textContent = shortStageTitle(stage); $("mobile-stage-label").textContent = shortStageTitle(stage);
    for (const container of [$("stage-detail"), $("stage-sheet-content")]) {
      container.querySelectorAll("[data-open-method]").forEach((button) => button.onclick = () => { if ($("stage-sheet").open) $("stage-sheet").close(); openMethod(button.dataset.openMethod); });
      container.querySelectorAll("[data-open-node]").forEach((button) => button.onclick = () => { if ($("stage-sheet").open) $("stage-sheet").close(); openNode(button.dataset.openNode); });
      container.querySelectorAll("[data-connected-stage]").forEach((button) => button.onclick = () => openWorkflowStep(button.dataset.connectedStage));
      container.querySelectorAll("[data-workflow-library]").forEach((button) => button.onclick = () => {
        if ($("stage-sheet").open) $("stage-sheet").close();
        if (button.dataset.workflowLibrary === "models") {
          $("model-search").value = ""; $("model-status").value = "all"; $("model-family").value = "all"; renderModels(); setView("models");
        } else {
          $("relation-search").value = ""; $("relation-type").value = "variable";
          state.nodeId = state.nodes.find((node) => node.kind === "variable")?.id || state.nodeId;
          renderRecords(); renderRecordDetail(); setView("relations");
        }
      });
    }
    $("workflow-connections").innerHTML = '<p>Arrows show the 15 source-defined dependencies, not a live or approved trading pipeline.</p>';
    if (state.childStageId) renderChildWorkflow();
    else {
      $("workflow-breadcrumb").hidden = true; $("part-controls").hidden = true; $("part-catalog").hidden = true;
      $("workflow-board-title").textContent = "Data to decisions"; $("workflow-board-subtitle").textContent = "12 steps · open a step to explore";
      $("workflow-board-introduction").textContent = "Open any box to explore its connected parts. Supplied holdings and cash are a separate input.";
    }
    renderWorkflowCanvas();
    requestAnimationFrame(drawWorkflowWires);
  }
  function svgElement(name, attrs = {}, value) { const item = document.createElementNS(NS, name); Object.entries(attrs).forEach(([key, val]) => item.setAttribute(key, val)); if (value !== undefined) item.textContent = value; return item; }
  function drawWorkflowWires() {
    if (state.view !== "workflow") return;
    if (state.workflowView === "canvas") return;
    if (state.childStageId) { drawPartWires(); return; }
    const board = $("workflow-map"), svg = board.querySelector(".workflow-wires"); if (!svg) return;
    const bounds = board.getBoundingClientRect(), mobile = bounds.width < 500; svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`); svg.replaceChildren();
    const defs = svgElement("defs");
    for (const [id, color] of [["flow-arrow", "#8ea7b3"], ["flow-arrow-selected", "#147d79"]]) { const marker = svgElement("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" }); marker.append(svgElement("path", { d: "M0,0 L10,5 L0,10 Z", fill: color })); defs.append(marker); }
    svg.append(defs);
    for (const edge of state.pipelineEdges) {
      const start = [...board.querySelectorAll("[data-stage]")].find((button) => button.dataset.stage === edgeSource(edge));
      const end = [...board.querySelectorAll("[data-stage]")].find((button) => button.dataset.stage === edgeTarget(edge));
      if (!start || !end) continue;
      const a = start.getBoundingClientRect(), b = end.getBoundingClientRect();
      const sourceSlot = start.closest(".flow-slot");
      const visibleNote = sourceSlot?.querySelector(".flow-node-notes");
      const sourceBottom = visibleNote && visibleNote.getClientRects().length ? sourceSlot.getBoundingClientRect().bottom : a.bottom;
      const active = edgeSource(edge) === state.stageId || edgeTarget(edge) === state.stageId;
      const source = edgeSource(edge), target = edgeTarget(edge);
      const ax = a.left - bounds.left, ay = a.top - bounds.top, bx = b.left - bounds.left, by = b.top - bounds.top;
      let x1 = ax + a.width / 2, y1 = sourceBottom - bounds.top + 2, x2 = bx + b.width / 2, y2 = by - 3;
      let path, label = "", labelX = 0, labelY = 0, rotate = 0;
      if (source === "forecast_contract" && target === "model") {
        x1 = ax - 2; y1 = ay + a.height / 2; x2 = bx - 3; y2 = by + b.height / 2;
        const gutter = Math.max(10, Math.min(x1, x2) - (mobile ? 16 : 32));
        path = `M${x1},${y1}H${gutter}V${y2}H${x2}`; label = "Target"; labelX = gutter - 5; labelY = (y1 + y2) / 2; rotate = -90;
      } else if (source === "paper_report" && target === "mature_outcomes") {
        x1 = ax + a.width + 2; y1 = ay + a.height / 2; x2 = bx + b.width + 3; y2 = by + b.height / 2;
        const gutter = Math.min(bounds.width + 8, Math.max(x1, x2) + (mobile ? 16 : 32));
        path = `M${x1},${y1}H${gutter}V${y2}H${x2}`; label = "Prior decisions"; labelX = gutter + 5; labelY = (y1 + y2) / 2; rotate = 90;
      } else if (source === "portfolio_state" && target === "constraints") {
        x1 = ax + a.width + 2; y1 = ay + a.height / 2; x2 = bx + b.width + 3; y2 = by + b.height / 2;
        const gutter = bounds.width + 7; path = `M${x1},${y1}H${gutter}V${y2}H${x2}`;
      } else if (source === "model" && target === "holding_review" && mobile) {
        const turn = y2 - 18; path = `M${x1 + 8},${y1}V${turn}H${x2}V${y2}`;
      } else if (Math.abs(x1 - x2) < 2) path = `M${x1},${y1}V${y2}`;
      else {
        const middle = source === "model" ? y1 + 18 : (y1 + y2) / 2;
        path = `M${x1},${y1}V${middle}H${x2}V${y2}`;
      }
      const line = svgElement("path", { d: path, fill: "none", stroke: active ? "#147d79" : "#a4b8c2", "stroke-width": active ? 2.4 : 1.5, "stroke-dasharray": source === "portfolio_state" ? "5 3" : "", "marker-end": `url(#${active ? "flow-arrow-selected" : "flow-arrow"})`, "data-source": source, "data-target": target });
      line.append(svgElement("title", {}, `${shortStageTitle(state.stages.find((stage) => stage.id === source))} · ${edgeRelation(edge)} · ${shortStageTitle(state.stages.find((stage) => stage.id === target))}`)); svg.append(line);
      if (label) svg.append(svgElement("text", { x: labelX, y: labelY, transform: `rotate(${rotate} ${labelX} ${labelY})`, "text-anchor": "middle", "font-size": 14, fill: active ? "#147d79" : "#738e9c", class: "flow-wire-label" }, label));
    }
  }

  function currentWorkflowSection() { return state.workflowSections.get(state.childStageId); }
  function orderedParts(nodes) { return [...nodes].sort((a, b) => (Number(a.order) || 0) - (Number(b.order) || 0) || String(a.title || a.id).localeCompare(String(b.title || b.id))); }
  function partChildren(section, id) {
    const ids = new Set(list(section.nodes).filter((node) => node.parent_id === id).map((node) => node.id));
    for (const edge of list(section.edges)) if (edge.source === id && /^(contains|includes|has_part|decomposes_into)$/.test(edge.relation)) ids.add(edge.target);
    return orderedParts(list(section.nodes).filter((node) => ids.has(node.id)));
  }
  function partParent(section, node) {
    if (node.parent_id && list(section.nodes).some((item) => item.id === node.parent_id)) return node.parent_id;
    return list(section.edges).find((edge) => edge.target === node.id && /^(contains|includes|has_part|decomposes_into)$/.test(edge.relation))?.source || "";
  }
  function partAncestors(section, id) {
    const byId = new Map(list(section.nodes).map((node) => [node.id, node])), result = [], seen = new Set(); let node = byId.get(id);
    while (node && !seen.has(node.id)) { seen.add(node.id); result.unshift(node); node = byId.get(partParent(section, node)); }
    return result;
  }
  function isPartCategory(section, node) { return node.role === "category" || partChildren(section, node.id).length > 0; }
  function partStatus(part) {
    if (part.formula && list(part.linked_variable_ids).length) {
      if (part.status === "design_needed") return "Known definition · use undecided";
      if (part.status === "historical_reviewed") return "Known definition · historical review";
    }
    return human(part.status || "Source-linked definition");
  }
  function selectWorkflowPart(id, showSheet = true) {
    const section = currentWorkflowSection(), part = section && list(section.nodes).find((node) => node.id === id); if (!part) return;
    state.partId = id; state.detailMode = "part";
    if (isPartCategory(section, part)) { state.partFocusId = id; state.showAllParts = false; $("part-search").value = ""; }
    else if (!state.showAllParts) { state.partFocusId = partParent(section, part); $("part-search").value = ""; }
    if (isPartCategory(section, part) && $("stage-sheet").open) $("stage-sheet").close();
    renderWorkflow(); setHash();
    if (showSheet && !isPartCategory(section, part) && window.matchMedia("(max-width: 850px)").matches && !$("stage-sheet").open) showWorkflowDetailSheet();
    else if ($("stage-sheet").open) showWorkflowDetailSheet();
    else activeWorkflowButton()?.focus({ preventScroll: true });
    announce(isPartCategory(section, part) ? `Opened ${part.title} and its connected parts.` : `Selected ${part.title}. Inputs, outputs and evidence are available.`);
  }
  function rootPartView() {
    state.partFocusId = ""; state.partId = ""; state.showAllParts = false; state.detailMode = "stage"; $("part-search").value = "";
    renderWorkflow(); setHash(); requestAnimationFrame(() => $("workflow-breadcrumb").scrollIntoView({ block: "start" }));
  }
  function focusPartCategory(id) {
    const sectionData = currentWorkflowSection(), node = list(sectionData?.nodes).find((part) => part.id === id);
    if (!node) return;
    state.partId = id; state.partFocusId = id; state.showAllParts = false; state.detailMode = "part"; $("part-search").value = "";
    renderWorkflow(); setHash(); requestAnimationFrame(() => $("workflow-breadcrumb").scrollIntoView({ block: "start" }));
  }
  function openPartAcrossStages(id) {
    const entry = state.workflowPartIndex.get(id); if (!entry) return;
    if (state.canvasScope === "connected") {
      if ($("stage-sheet").open) $("stage-sheet").close();
      state.openBranches.add(entry.stageId); for (const ancestor of partAncestors(state.workflowSections.get(entry.stageId), id)) state.collapsedCategories.delete(ancestor.id);
      inspectCanvasNode(id); if (window.matchMedia("(max-width:850px)").matches) showWorkflowDetailSheet(); return;
    }
    if ($("stage-sheet").open) $("stage-sheet").close();
    state.stageId = entry.stageId; state.childStageId = entry.stageId; state.partId = id; state.detailMode = "part"; state.showAllParts = false; $("part-search").value = "";
    const sectionData = currentWorkflowSection(); state.partFocusId = isPartCategory(sectionData, entry.node) ? id : partParent(sectionData, entry.node);
    renderWorkflow(); setHash();
    requestAnimationFrame(() => { $("workflow-breadcrumb").scrollIntoView({ block: "start" }); if ($("stage-sheet").open) $("close-stage-sheet").focus({ preventScroll: true }); else activeWorkflowButton()?.focus({ preventScroll: true }); });
    if (window.matchMedia("(max-width: 850px)").matches && !isPartCategory(sectionData, entry.node)) showWorkflowDetailSheet();
    announce(`Opened ${entry.node.title} in ${shortStageTitle(state.stages.find((stage) => stage.id === entry.stageId))}.`);
  }
  function visibleWorkflowParts(section) {
    const nodes = list(section.nodes), byId = new Map(nodes.map((node) => [node.id, node])), query = normalizeSearch($("part-search").value);
    let chosen;
    if (query) {
      chosen = nodes.filter((node) => normalizeSearch([node.title, node.summary, node.inputs, node.outputs, node.details, node.formula, node.code_components].map(plain).join(" ")).includes(query));
      const ids = new Set(chosen.map((node) => node.id));
      for (const node of chosen) { let parent = partParent(section, node), guard = 0; while (parent && guard++ < nodes.length && !ids.has(parent)) { ids.add(parent); parent = partParent(section, byId.get(parent) || {}); } }
      return orderedParts(nodes.filter((node) => ids.has(node.id)));
    }
    if (state.showAllParts) return orderedParts(nodes);
    const focus = byId.get(state.partFocusId);
    if (focus) {
      const ids = new Set([focus.id, ...partChildren(section, focus.id).map((node) => node.id)]), parent = partParent(section, focus);
      if (parent) ids.add(parent);
      chosen = nodes.filter((node) => ids.has(node.id));
    } else {
      chosen = nodes.filter((node) => !partParent(section, node));
      if (chosen.length === 1) chosen = [...chosen, ...partChildren(section, chosen[0].id)];
    }
    return orderedParts(chosen.length ? chosen : nodes);
  }
  function partLevels(nodes, edges) {
    const ids = new Set(nodes.map((node) => node.id)), degree = new Map(nodes.map((node) => [node.id, 0])), rank = new Map(nodes.map((node) => [node.id, 0]));
    const links = edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target));
    links.forEach((edge) => degree.set(edge.target, degree.get(edge.target) + 1));
    const queue = orderedParts(nodes.filter((node) => degree.get(node.id) === 0)), visited = new Set();
    while (queue.length) {
      const node = queue.shift(); if (visited.has(node.id)) continue; visited.add(node.id);
      for (const edge of links.filter((link) => link.source === node.id)) { rank.set(edge.target, Math.max(rank.get(edge.target), rank.get(node.id) + 1)); degree.set(edge.target, degree.get(edge.target) - 1); if (degree.get(edge.target) === 0) queue.push(nodes.find((item) => item.id === edge.target)); }
    }
    // A source-defined cycle remains a relationship, not an invented chronology.
    for (const node of nodes) if (!visited.has(node.id)) rank.set(node.id, 1);
    const levels = new Map(); for (const node of orderedParts(nodes)) { const level = rank.get(node.id); if (!levels.has(level)) levels.set(level, []); levels.get(level).push(node); }
    return [...levels.entries()].sort((a, b) => a[0] - b[0]).map((entry) => entry[1]);
  }
  function renderChildWorkflow() {
    const stage = state.stages.find((item) => item.id === state.stageId), sectionData = currentWorkflowSection();
    const topGroups = sectionData ? orderedParts(list(sectionData.nodes).filter((node) => !partParent(sectionData, node) && isPartCategory(sectionData, node))) : [];
    if (topGroups.length && !state.partFocusId && !state.showAllParts && !normalizeSearch($("part-search").value)) {
      state.partFocusId = topGroups[0].id;
      if (!state.partId) { state.partId = topGroups[0].id; state.detailMode = "part"; }
    }
    $("workflow-breadcrumb").hidden = false;
    const ancestors = sectionData && state.partFocusId ? partAncestors(sectionData, state.partFocusId) : [];
    $("workflow-breadcrumb").innerHTML = `<button type="button" data-back-overview>Whole workflow</button><span aria-hidden="true">/</span><button type="button" data-back-step>${esc(shortStageTitle(stage))}</button>${ancestors.map((node, index) => `<span aria-hidden="true">/</span>${index === ancestors.length - 1 ? `<strong>${esc(node.title)}</strong>` : `<button type="button" data-focus-ancestor="${esc(node.id)}">${esc(node.title)}</button>`}`).join("")}`;
    $("workflow-breadcrumb").querySelector("[data-back-overview]").onclick = returnToWorkflow;
    $("workflow-breadcrumb").querySelector("[data-back-step]").onclick = rootPartView;
    $("workflow-breadcrumb").querySelectorAll("[data-focus-ancestor]").forEach((button) => button.onclick = () => focusPartCategory(button.dataset.focusAncestor));
    $("workflow-board-title").textContent = sectionData?.title || shortStageTitle(stage);
    $("workflow-board-subtitle").textContent = sectionData ? `${list(sectionData.nodes).length} specific parts` : "Child breakdown unavailable";
    $("workflow-board-introduction").textContent = sectionData?.introduction || "The detailed breakdown is not available in this saved source. The original stage evidence remains available in About this step.";
    $("workflow-map").classList.remove("flow-network"); $("workflow-map").classList.add("part-network");
    $("part-controls").hidden = !sectionData; $("part-catalog").hidden = !sectionData;
    if (!sectionData) { $("workflow-map").innerHTML = '<p class="empty">No child nodes were supplied for this stage. Nothing is inferred or fabricated.</p>'; return; }
    const nodes = list(sectionData.nodes), visible = visibleWorkflowParts(sectionData), query = normalizeSearch($("part-search").value);
    $("part-group-field").hidden = topGroups.length < 2;
    const selectedGroup = partAncestors(sectionData, state.partFocusId)[0]?.id;
    $("part-group-select").innerHTML = topGroups.map((node) => `<option value="${esc(node.id)}" ${node.id === selectedGroup ? "selected" : ""}>${esc(node.title)}</option>`).join("");
    state.visibleParts = visible;
    const levels = partLevels(visible, list(sectionData.edges));
    $("workflow-map").innerHTML = '<svg class="workflow-wires part-wires" aria-hidden="true"></svg>' + levels.map((level) => `<div class="part-level">${level.map((part) => { const children = partChildren(sectionData, part.id), category = isPartCategory(sectionData, part); return `<button class="part-button ${category ? "part-category" : "part-leaf"} ${part.id === state.partId ? "selected" : ""}" type="button" data-part="${esc(part.id)}" aria-pressed="${part.id === state.partId}"><span class="part-role">${category ? "Connected group" : "Specific part"}</span><strong>${esc(part.title)}</strong><span class="part-state">${esc(partStatus(part))}</span><span class="part-open">${children.length ? `Explore ${children.length} parts` : part.formula ? "Read formula & evidence" : "Read inputs & outputs"}</span></button>`; }).join("")}</div>`).join("");
    $("workflow-map").querySelectorAll("[data-part]").forEach((button) => button.onclick = () => selectWorkflowPart(button.dataset.part));
    $("part-show-all").textContent = state.showAllParts ? "Show groups" : `Show all ${nodes.length}`;
    $("part-show-all").title = state.showAllParts ? "Return to the grouped child diagram" : `Show all ${nodes.length} parts in this step`;
    const shownEdges = list(sectionData.edges).filter((edge) => visible.some((node) => node.id === edge.source) && visible.some((node) => node.id === edge.target)).length;
    $("part-view-note").textContent = `${visible.length} of ${nodes.length} parts · ${shownEdges} connections${query ? " · matches + parent context" : ""}`;
    const catalog = orderedParts(nodes).filter((part) => !query || normalizeSearch([part.title, part.summary, part.inputs, part.outputs, part.formula].map(plain).join(" ")).includes(query));
    $("part-catalog-label").textContent = query ? `${catalog.length} matching parts` : `Find any of the ${nodes.length} parts in this step`;
    $("part-catalog").open = Boolean(query);
    $("part-catalog-list").innerHTML = catalog.map((part) => `<button type="button" data-catalog-part="${esc(part.id)}"><strong>${esc(part.title)}</strong><span>${isPartCategory(sectionData, part) ? "Connected group" : "Specific part"}</span></button>`).join("") || '<p class="empty">No matching parts.</p>';
    $("part-catalog-list").querySelectorAll("[data-catalog-part]").forEach((button) => button.onclick = () => { $("part-search").value = ""; selectWorkflowPart(button.dataset.catalogPart); });
    $("workflow-connections").innerHTML = '<p>The child diagram contains source-defined project relationships. It does not run a model or create stock actions.</p>';
    if (state.detailMode === "part" && nodes.some((part) => part.id === state.partId)) renderPartInspector(nodes.find((part) => part.id === state.partId), sectionData);
    else {
      const scope = section("Scope of this detailed map", `<p class="detail-summary">${esc(sectionData.introduction || "No additional scope was recorded.")}</p>`);
      $("stage-detail").insertAdjacentHTML("beforeend", scope); $("stage-sheet-content").insertAdjacentHTML("beforeend", scope);
    }
    requestAnimationFrame(drawPartWires);
  }
  function renderPartInspector(part, sectionData) {
    const byId = new Map(list(sectionData.nodes).map((node) => [node.id, node]));
    const refs = list(part.evidence_ids).length ? part.evidence_ids : sectionData.evidence_ids;
    const studies = list(part.linked_study_ids).map((id) => state.studyById.get(id)).filter(Boolean);
    const variableIds = list(part.linked_variable_ids);
    const variables = variableIds.map((id) => state.nodeById.get(id) || state.nodeById.get(`variable:${id}`)).filter((node) => node?.kind === "variable");
    const canonical = list(part.canonical_node_ids).map((id) => state.nodeById.get(id)).filter(Boolean);
    const related = list(sectionData.edges).filter((edge) => edge.source === part.id || edge.target === part.id);
    const relations = related.map((edge) => { const incoming = edge.target === part.id, other = byId.get(incoming ? edge.source : edge.target); return other ? `<button class="part-related" type="button" data-related-part="${esc(other.id)}"><span>${incoming ? "From" : "To"} · ${esc(human(edge.relation))}</span><strong>${esc(other.title)}</strong></button>` : ""; }).join("");
    const external = state.crossSectionEdges.filter((edge) => edge.source === part.id || edge.target === part.id).map((edge) => {
      const incoming = edge.target === part.id, other = state.workflowPartIndex.get(incoming ? edge.source : edge.target); if (!other) return "";
      const otherStage = state.stages.find((stage) => stage.id === other.stageId);
      return `<button class="part-related" type="button" data-cross-part="${esc(other.node.id)}"><span>${incoming ? "From" : "To"} · ${esc(human(edge.relation))} · ${esc(otherStage ? shortStageTitle(otherStage) : other.stageId)}</span><strong>${esc(other.node.title)}</strong></button>`;
    }).join("");
    const markup = `<div class="detail-kicker"><p class="eyebrow">${isPartCategory(sectionData, part) ? "Connected group" : "Specific workflow part"}</p><span class="pill diagnostic">${esc(partStatus(part))}</span></div><h3>${esc(part.title)}</h3><p class="detail-summary">${esc(part.summary || "Read the recorded inputs, outputs and source evidence.")}</p>${section("Inputs", itemList(list(part.inputs).length ? part.inputs : ["No additional input was recorded for this part."]))}${section("Outputs", itemList(list(part.outputs).length ? part.outputs : ["No additional output was recorded for this part."]))}${part.formula ? section("Formula or rule", `<pre class="part-formula"><code>${esc(plain(part.formula))}</code></pre>${part.units ? `<p class="meta"><strong>Units:</strong> ${esc(plain(part.units))}</p>` : ""}`) : part.units ? section("Units", `<p>${esc(plain(part.units))}</p>`) : ""}${list(part.details).length ? section("Specific details and limits", itemList(part.details)) : ""}${variables.length ? section("Linked variable definitions", variables.map((node) => `<div class="part-variable"><button class="record-chip" data-open-node="${esc(node.id)}">${esc(nodeTitle(node))}</button><p class="file-path">${esc(node.formula || "No formula recorded")}</p></div>`).join("")) : ""}${studies.length ? section("Saved studies and results", `<div class="linked-records">${studies.map((study) => `<button class="record-chip" data-open-method="${esc(study.canonical_id)}">${esc(studyTitle(study))}</button>`).join("")}</div>`) : ""}${list(part.code_components).length ? section("Code and implementation components", itemList(part.code_components)) : ""}${canonical.length ? section("Project record connections", `<div class="linked-records">${canonical.map((node) => `<button class="record-chip" data-open-node="${esc(node.id)}">${esc(nodeTitle(node))}</button>`).join("")}</div>`) : ""}${related.length ? section("Connected parts", `<div class="part-related-list">${relations}</div>`) : ""}<details class="detail-section"><summary>Source evidence</summary>${evidenceMarkup(list(refs))}<p class="identifier">${esc(part.id)}<br>Recorded source status: ${esc(part.status || "Not recorded")}</p></details>`;
    const completeMarkup = readinessCard(part.id) + markup + (external ? section("Connections in other steps", `<div class="part-related-list">${external}</div>`) : "");
    $("stage-detail").innerHTML = completeMarkup; $("stage-sheet-content").innerHTML = completeMarkup; $("stage-sheet-title").textContent = part.title; $("mobile-stage-label").textContent = part.title;
    resetWorkflowDetailScroll();
    for (const container of [$("stage-detail"), $("stage-sheet-content")]) {
      container.querySelectorAll("[data-open-method]").forEach((button) => button.onclick = () => { if ($("stage-sheet").open) $("stage-sheet").close(); openMethod(button.dataset.openMethod); });
      container.querySelectorAll("[data-open-node]").forEach((button) => button.onclick = () => { if ($("stage-sheet").open) $("stage-sheet").close(); openNode(button.dataset.openNode); });
      container.querySelectorAll("[data-related-part]").forEach((button) => button.onclick = () => selectWorkflowPart(button.dataset.relatedPart, false));
      container.querySelectorAll("[data-cross-part]").forEach((button) => button.onclick = () => openPartAcrossStages(button.dataset.crossPart));
      container.querySelectorAll("[data-part-reference]").forEach((button) => button.onclick = () => openPartAcrossStages(button.dataset.partReference));
    }
  }
  function drawPartWires() {
    if (state.view !== "workflow" || !state.childStageId) return;
    const sectionData = currentWorkflowSection(), board = $("workflow-map"), svg = board.querySelector(".part-wires"); if (!svg || !sectionData) return;
    const bounds = board.getBoundingClientRect(); if (!bounds.width || !bounds.height) return;
    svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`); svg.replaceChildren();
    const defs = svgElement("defs"), marker = svgElement("marker", { id: "part-arrow", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto" }); marker.append(svgElement("path", { d: "M0,0 L10,5 L0,10 Z", fill: "#6d9b9e" })); defs.append(marker); svg.append(defs);
    const buttons = new Map([...board.querySelectorAll("[data-part]")].map((button) => [button.dataset.part, button]));
    for (const edge of list(sectionData.edges)) {
      const start = buttons.get(edge.source), end = buttons.get(edge.target); if (!start || !end) continue;
      const a = start.getBoundingClientRect(), b = end.getBoundingClientRect();
      let x1 = a.left + a.width / 2 - bounds.left, y1 = a.bottom - bounds.top + 2, x2 = b.left + b.width / 2 - bounds.left, y2 = b.top - bounds.top - 3, path;
      if (Math.abs(a.top - b.top) < 8) {
        const right = a.left < b.left; x1 = (right ? a.right : a.left) - bounds.left; x2 = (right ? b.left : b.right) - bounds.left; y1 = a.top + a.height / 2 - bounds.top; y2 = b.top + b.height / 2 - bounds.top;
        const bend = Math.min(y1, y2) - Math.max(a.height, b.height) / 2 - 17; path = `M${x1},${y1}C${x1},${bend} ${x2},${bend} ${x2},${y2}`;
      } else { const middle = (y1 + y2) / 2; path = `M${x1},${y1}C${x1},${middle} ${x2},${middle} ${x2},${y2}`; }
      const selected = edge.source === state.partId || edge.target === state.partId;
      const line = svgElement("path", { d: path, fill: "none", stroke: selected ? "#2a8482" : "#b5cdd1", "stroke-width": selected ? 2.2 : 1.35, "marker-end": "url(#part-arrow)", "stroke-dasharray": /contains|includes|has_part/.test(edge.relation) ? "" : "4 3", "data-source": edge.source, "data-target": edge.target });
      line.append(svgElement("title", {}, human(edge.relation))); svg.append(line);
    }
  }

  function connectedGraph() {
    const nodes = state.stages.map((stage) => ({ id: stage.id, label: shortStageTitle(stage), role: "stage", stageId: stage.id })), edges = [];
    const visible = new Set(), allCanonical = [];
    const readinessFilter = readinessDetailFilter();
    for (const [stageId, sectionData] of state.workflowSections) {
      allCanonical.push(...sectionData.edges);
      if (!state.openBranches.has(stageId)) continue;
      const parts = orderedParts(sectionData.nodes).filter((part) => readinessFilter.allowed.has(part.id) && !partAncestors(sectionData, part.id).slice(0, -1).some((ancestor) => state.collapsedCategories.has(ancestor.id)));
      parts.sort((a, b) => partAncestors(sectionData, a.id).length - partAncestors(sectionData, b.id).length);
      for (const part of parts) {
        visible.add(part.id); const parent = partParent(sectionData, part);
        nodes.push({ id: part.id, label: part.title, role: isPartCategory(sectionData, part) ? "category" : "leaf", stageId, parent_id: parent || stageId, order: part.order || 0, filterContext: !readinessFilter.matched.has(part.id) });
        if (!parent) edges.push({ id: `membership:${stageId}:${part.id}`, source: stageId, target: part.id, relation: "section_membership", label: "part of this step", kind: "membership", display_only: true });
      }
    }
    allCanonical.push(...state.crossSectionEdges);
    const references = new Map();
    for (const [ownerId, entry] of state.workflowPartIndex) for (const field of ["inputs", "outputs"]) list(entry.node[field]).forEach((value, index) => {
      if (typeof value !== "string" || !state.workflowPartIndex.has(value)) return;
      const source = field === "inputs" ? value : ownerId, target = field === "inputs" ? ownerId : value, key = `${source}\u0000${target}`;
      if (!references.has(key)) references.set(key, { source, target, references: [] });
      references.get(key).references.push({ owner_id: ownerId, field, index });
    });
    const canonicalPairs = new Set(allCanonical.map((edge) => `${edge.source}\u0000${edge.target}`));
    for (const [index, edge] of state.pipelineEdges.entries()) edges.push({ id: `main:${index}`, source: edge.source, target: edge.target, relation: edge.relation, label: flowRelationLabels[edge.relation] || human(edge.relation), kind: "main" });
    for (const [index, edge] of allCanonical.entries()) if (visible.has(edge.source) && visible.has(edge.target)) edges.push({ id: `canonical:${index}`, source: edge.source, target: edge.target, relation: edge.relation, label: human(edge.relation), kind: "canonical", references: references.get(`${edge.source}\u0000${edge.target}`)?.references || [] });
    for (const [key, pair] of references) if (!canonicalPairs.has(key) && visible.has(pair.source) && visible.has(pair.target)) {
      const sides = new Set(pair.references.map((ref) => ref.field));
      edges.push({ id: `declared:${pair.source}:${pair.target}`, source: pair.source, target: pair.target, relation: "declared_reference", label: sides.size > 1 ? "declared input + output" : sides.has("inputs") ? "declared input" : "declared output", kind: "declared_reference", references: pair.references, display_only: true });
    }
    const fields = new Map();
    const addField = (id, label, role, value, ownerId, field, index, canonicalId = "") => {
      const owner = state.workflowPartIndex.get(ownerId);
      if (!fields.has(id)) fields.set(id, { id, label, role, value, owner_id: ownerId, parent_id: ownerId, stageId: owner.stageId, owners: [], source_refs: [], canonicalId, display_only: true });
      const record = fields.get(id); if (!record.owners.includes(ownerId)) record.owners.push(ownerId); record.stageIds = [...new Set(record.owners.map((id) => state.workflowPartIndex.get(id).stageId))]; record.source_refs.push({ owner_id: ownerId, field, index });
      edges.push({ id: `annotation:${ownerId}:${field}:${index}`, source: ownerId, target: id, relation: "recorded_annotation", label: role === "formula" ? "recorded formula / rule" : role === "component" ? "recorded code component" : "linked variable definition", kind: "annotation", display_only: true, references: [{ owner_id: ownerId, field, index }] });
    };
    for (const ownerId of state.openLeaves) {
      if (!visible.has(ownerId)) continue;
      const part = state.workflowPartIndex.get(ownerId).node;
      if (part.formula) addField(`display:formula:${ownerId}`, plain(part.formula), "formula", plain(part.formula), ownerId, "formula", 0);
      list(part.code_components).forEach((component, index) => addField(`display:code:${encodeURIComponent(plain(component))}`, plain(component), "component", plain(component), ownerId, "code_components", index));
      list(part.linked_variable_ids).forEach((id, index) => {
        const variable = state.nodeById.get(id) || state.nodeById.get(`variable:${id}`); if (!variable) return;
        const label = `${nodeTitle(variable)}\n${plain(variable.formula || variable.variable?.formula || "Definition recorded")}`;
        addField(`display:definition:${variable.id}`, label, "definition", plain(variable.formula || variable.variable?.formula), ownerId, "linked_variable_ids", index, variable.id);
      });
    }
    state.displayFieldIndex = fields; nodes.push(...fields.values());
    const selectedId = fields.has(state.displaySelectionId) ? state.displaySelectionId : visible.has(state.partId) ? state.partId : state.stageId;
    for (const edge of edges) {
      edge.isCrosslink = edge.kind === "declared_reference" || edge.kind === "canonical" && edge.relation !== "contains";
      edge.mainBypass = edge.kind === "main" && (edge.source === "forecast_contract" && edge.target === "model" || edge.source === "portfolio_state" && edge.target === "constraints" || edge.source === "paper_report" && edge.target === "mature_outcomes") ? 1 : 0;
    }
    return decorateReadiness({ key: "connected-branches", incremental: true, roadmap: true, selectedStageId: state.stageId, roadmapCatalog: [...state.workflowPartIndex].map(([id, entry]) => ({ id, stageId: entry.stageId, parent_id: partParent(state.workflowSections.get(entry.stageId), entry.node) || entry.stageId, order: entry.node.order || 0 })), nodes, edges, selectedId, counts: { hubs: 12, parts: visible.size, matchedVisible: [...visible].filter((id) => readinessFilter.matched.has(id)).length, contextVisible: [...visible].filter((id) => !readinessFilter.matched.has(id)).length, catalogMatched: readinessFilter.matched.size, fields: fields.size, canonical: edges.filter((edge) => edge.kind === "canonical").length, references: edges.filter((edge) => edge.kind === "declared_reference").length, membership: edges.filter((edge) => edge.kind === "membership").length } });
  }
  function focusRoadmapStop(id) {
    if (!state.stages.some((stage) => stage.id === id)) return;
    state.canvasScope = "connected"; state.openBranches.add(id); inspectCanvasNode(id);
    requestAnimationFrame(() => state.canvas?.focusRoadmapStop(id));
  }
  function activateConnectedNode(id) {
    const field = state.displayFieldIndex.get(id); if (field) { inspectCanvasNode(id); return; }
    const entry = state.workflowPartIndex.get(id);
    const opening = !entry ? !state.openBranches.has(id) : isPartCategory(state.workflowSections.get(entry.stageId), entry.node) ? state.collapsedCategories.has(id) : !state.openLeaves.has(id);
    if (!entry) { if (state.openBranches.has(id)) state.openBranches.delete(id); else { state.openBranches.add(id); for (const part of list(state.workflowSections.get(id)?.nodes)) state.collapsedCategories.delete(part.id); } state.partId = ""; state.stageId = id; }
    else if (isPartCategory(state.workflowSections.get(entry.stageId), entry.node)) state.collapsedCategories.has(id) ? state.collapsedCategories.delete(id) : state.collapsedCategories.add(id);
    else state.openLeaves.has(id) ? state.openLeaves.delete(id) : state.openLeaves.add(id);
    inspectCanvasNode(id);
    if (opening) requestAnimationFrame(() => { if (!entry) state.canvas?.focusBranch(id, true); else state.canvas?.focusSelection(id, entry.stageId, true); });
  }
  function inspectDisplayField(field) {
    const owner = state.workflowPartIndex.get(field.owner_id);
    const fieldEvidence = [...new Set(field.owners.flatMap((id) => list(state.workflowPartIndex.get(id)?.node.evidence_ids)))];
    const markup = `<p class="eyebrow">Display annotation · exact recorded field</p><h3>${esc(field.role === "formula" ? "Formula / rule" : field.role === "component" ? "Code component" : "Linked variable definition")}</h3><pre class="part-formula"><code>${esc(field.value)}</code></pre><p class="scope-note">This annotation exposes a recorded formula, code name or definition. It adds no market observation or predictive claim.</p>${section("Recorded by", `<div class="linked-records">${field.owners.map((id) => `<button class="record-chip" data-field-owner="${esc(id)}">${esc(state.workflowPartIndex.get(id)?.node.title || id)}</button>`).join("")}</div>`)}${field.canonicalId ? `<button class="record-chip" data-field-variable="${esc(field.canonicalId)}">Open canonical variable definition</button>` : ""}<p class="identifier">${field.source_refs.map((ref) => `${esc(ref.owner_id)} · ${esc(ref.field)}[${ref.index}]`).join("<br>")}</p>${evidenceMarkup(fieldEvidence)}`;
    const ownerStatuses = [...new Set(field.owners.map((id) => readinessFor(id).operational_status))];
    const inherited = `<section class="readiness-card"><p class="meta">Display annotation · owner readiness</p>${ownerStatuses.map((status) => readinessPill(status, "inherited")).join(" ")}<p class="meta">This formula, code name or definition inherits its owning records’ reviewed status. The annotation is not an additional implemented connection or research result.</p></section>`;
    $("stage-detail").innerHTML = inherited + markup; $("stage-sheet-content").innerHTML = inherited + markup; $("stage-sheet-title").textContent = field.role === "formula" ? "Formula / rule" : field.label; $("mobile-stage-label").textContent = "Selected recorded field"; resetWorkflowDetailScroll();
    for (const container of [$("stage-detail"), $("stage-sheet-content")]) {
      container.querySelectorAll("[data-field-owner]").forEach((button) => button.onclick = () => inspectCanvasNode(button.dataset.fieldOwner));
      container.querySelectorAll("[data-field-variable]").forEach((button) => button.onclick = () => { if ($("stage-sheet").open) $("stage-sheet").close(); openNode(button.dataset.fieldVariable); });
    }
  }
  function canvasNode(id) {
    const field = state.displayFieldIndex.get(id); if (field) return field;
    const entry = state.workflowPartIndex.get(id);
    if (entry) return { id, label: entry.node.title, role: isPartCategory(state.workflowSections.get(entry.stageId), entry.node) ? "category" : "leaf", stageId: entry.stageId };
    const stage = state.stages.find((item) => item.id === id);
    return stage ? { id, label: shortStageTitle(stage), role: "stage", stageId: id } : null;
  }
  function inspectCanvasNode(id) {
    state.canvasSelectedEdge = null;
    if (state.displayFieldIndex.has(id)) { state.displaySelectionId = id; renderWorkflow(); inspectDisplayField(state.displayFieldIndex.get(id)); setHash(); return; }
    state.displaySelectionId = "";
    const entry = state.workflowPartIndex.get(id);
    if (entry) {
      if (state.canvasScope === "connected") {
        state.readinessPinnedPart = readinessMatches(id) ? "" : id; state.openBranches.add(entry.stageId);
        for (const ancestor of partAncestors(state.workflowSections.get(entry.stageId), id).slice(0, -1)) state.collapsedCategories.delete(ancestor.id);
      }
      if (state.childStageId !== entry.stageId) { state.partFocusId = partParent(state.workflowSections.get(entry.stageId), entry.node); state.showAllParts = false; }
      state.stageId = entry.stageId; state.childStageId = entry.stageId; state.partId = id; state.detailMode = "part";
    } else if (state.stages.some((stage) => stage.id === id)) { state.stageId = id; state.detailMode = "stage"; if (state.canvasScope === "connected") { state.childStageId = ""; state.partId = ""; state.partFocusId = ""; } }
    else return;
    renderWorkflow(); setHash(); announce(`Selected ${canvasNode(id)?.label}. Read details or explore its connected parts.`);
  }
  function setCanvasExpanded(expanded) {
    const layout = document.querySelector(".workflow-layout"), wasExpanded = layout.classList.contains("canvas-expanded");
    if (expanded && !wasExpanded) state.canvasExpandedScrollY = window.scrollY;
    layout.classList.toggle("canvas-expanded", expanded); document.body.classList.toggle("graph-expanded", expanded);
    $("canvas-expand").setAttribute("aria-pressed", String(expanded)); $("canvas-expand").textContent = expanded ? "Restore" : "Expand";
    requestAnimationFrame(() => { state.canvas?.resize(); if (!expanded) window.scrollTo({ top: state.canvasExpandedScrollY || 0 }); if (state.canvasScope === "connected") state.canvas?.focusRoadmapStop(state.stageId); else state.canvas?.fit(); });
  }
  function exploreCanvasNode(id) {
    if (state.canvasScope === "connected") { activateConnectedNode(id); return; }
    const entry = state.workflowPartIndex.get(id);
    state.canvasScope = "context"; state.canvasSelectedEdge = null;
    if (!entry) { openWorkflowStep(id); return; }
    if (state.childStageId !== entry.stageId) { state.childStageId = entry.stageId; state.stageId = entry.stageId; }
    if (isPartCategory(state.workflowSections.get(entry.stageId), entry.node)) selectWorkflowPart(id, false);
    else { inspectCanvasNode(id); showWorkflowDetailSheet(); }
  }
  function workflowCanvasGraph() {
    if (state.canvasScope === "connected") return connectedGraph();
    let nodes, edges, key, selectedId;
    if (state.canvasScope === "all") {
      nodes = [...state.workflowPartIndex.keys()].map(canvasNode);
      edges = [...state.workflowSections.values()].flatMap((sectionData) => sectionData.edges).concat(state.crossSectionEdges);
      key = "all-details"; selectedId = state.partId;
    } else if (state.childStageId) {
      const sectionData = currentWorkflowSection(); nodes = state.visibleParts.map((node) => canvasNode(node.id));
      edges = list(sectionData?.edges); key = `step:${state.childStageId}:${state.showAllParts ? "whole" : state.partFocusId}:${normalizeSearch($("part-search").value)}`; selectedId = state.partId;
    } else {
      nodes = state.stages.map((stage) => canvasNode(stage.id)); edges = state.pipelineEdges; key = "workflow"; selectedId = state.stageId;
    }
    const ids = new Set(nodes.filter(Boolean).map((node) => node.id));
    return decorateReadiness({ key, nodes: nodes.filter(Boolean), selectedId, edges: edges.filter((edge) => ids.has(edgeSource(edge)) && ids.has(edgeTarget(edge))).map((edge, index) => ({ id: `canvas-edge:${key}:${index}`, source: edgeSource(edge), target: edgeTarget(edge), relation: edge.relation || edge.type || "connects_to", label: flowRelationLabels[edge.relation] || human(edge.relation || edge.type || "connects to") })) });
  }
  function inspectCanvasEdge(edge) {
    state.canvasSelectedEdge = edge;
    const source = canvasNode(edge.source), target = canvasNode(edge.target);
    const provenance = list(edge.references).map((ref) => `${state.workflowPartIndex.get(ref.owner_id)?.node.title || ref.owner_id} · ${ref.field}[${ref.index}]`);
    const markup = `<p class="eyebrow">${edge.kind === "membership" ? "Display structure · section membership" : edge.kind === "annotation" ? "Display annotation · exact recorded field" : edge.kind === "declared_reference" ? "Display reference · declared input / output" : "Source-defined relationship"}</p><h3>${esc(source?.label || edge.source)} → ${esc(target?.label || edge.target)}</h3><p class="detail-summary">${esc(edge.label)}</p><p class="scope-note">The arrow records a documented project relationship. It does not establish predictive influence or a working connection.</p>${provenance.length ? section("Exact field declarations", itemList(provenance)) : ""}<div class="linked-records"><button class="record-chip" data-canvas-inspect="${esc(edge.source)}">Inspect source</button><button class="record-chip" data-canvas-inspect="${esc(edge.target)}">Inspect destination</button></div><p class="identifier">Recorded type: ${esc(edge.relation)}<br>${esc(edge.source)} → ${esc(edge.target)}</p>`;
    const readinessMarkup = edge.readiness ? `<section class="readiness-card">${readinessPill(edge.readiness)}<p>${esc(edge.readinessReason)}</p><details><summary>Connection review evidence</summary>${evidenceMarkup(list(edge.readinessEvidenceIds))}<p class="meta">Snapshot ${esc(state.readiness?.as_of || "date unavailable")} · the dependency arrow does not prove an implemented connection.</p></details></section>` : '<p class="scope-note">Documentary relationship · no separate operational connection has been verified for this arrow.</p>';
    $("stage-detail").innerHTML = readinessMarkup + markup; $("stage-sheet-content").innerHTML = readinessMarkup + markup; $("stage-sheet-title").textContent = "Connection details"; $("mobile-stage-label").textContent = "Selected connection"; resetWorkflowDetailScroll();
    for (const container of [$("stage-detail"), $("stage-sheet-content")]) container.querySelectorAll("[data-canvas-inspect]").forEach((button) => button.onclick = () => { if ($("stage-sheet").open) $("stage-sheet").close(); inspectCanvasNode(button.dataset.canvasInspect); });
    $("canvas-node-picker").value = ""; $("canvas-explore").disabled = true; $("canvas-details").disabled = false;
  }
  function renderWorkflowCanvas() {
    renderReadinessControls();
    const graph = workflowCanvasGraph(), canvasMode = state.workflowView === "canvas";
    $("workflow-canvas-mode").setAttribute("aria-pressed", String(canvasMode)); $("workflow-reading-mode").setAttribute("aria-pressed", String(!canvasMode));
    $("canvas-workspace").hidden = !canvasMode; $("workflow-reading").hidden = canvasMode || ["all", "connected"].includes(state.canvasScope);
    $("canvas-reading-all").hidden = canvasMode || !["all", "connected"].includes(state.canvasScope);
    document.querySelector(".workflow-board").classList.toggle("canvas-mode", canvasMode);
    $("canvas-scope").value = state.canvasScope === "connected" ? "connected" : state.canvasScope === "all" ? "all" : !state.childStageId ? "workflow" : state.showAllParts ? "step" : "focus";
    $("canvas-node-picker").innerHTML = '<option value="">Choose a record…</option>' + [...graph.nodes].sort((a, b) => a.label.localeCompare(b.label)).map((node) => `<option value="${esc(node.id)}">${esc(node.title || node.label)} · ${esc(node.readinessLabel || "Not reviewed")}${node.filterContext ? " · Context" : ""}${state.canvasScope === "all" ? ` · ${esc(shortStageTitle(state.stages.find((stage) => stage.id === node.stageId)))}` : ""}</option>`).join("");
    $("canvas-node-picker").value = graph.selectedId || "";
    const selected = canvasNode(graph.selectedId);
    $("canvas-explore").disabled = !selected; $("canvas-details").disabled = !selected;
    $("canvas-explore").textContent = selected?.role === "stage" ? "Open step" : selected?.role === "category" ? "Explore group" : "Read part";
    if (state.canvasScope === "connected") $("canvas-explore").textContent = selected?.role === "stage" ? state.openBranches.has(selected.id) ? "Collapse step" : "Unfold step" : selected?.role === "category" ? state.collapsedCategories.has(selected.id) ? "Unfold group" : "Fold group" : state.displayFieldIndex.has(selected?.id) ? "Inspect field" : state.openLeaves.has(selected?.id) ? "Hide fields" : "Reveal fields";
    $("canvas-count").textContent = `${graph.nodes.length} ${state.canvasScope === "all" ? "detail parts across 12 steps" : state.childStageId ? "parts" : "workflow steps"} · ${graph.edges.length} directed connections · moving nodes changes only this view`;
    if (state.canvasScope === "all") { $("workflow-board-title").textContent = "All workflow details"; $("workflow-board-subtitle").textContent = `${graph.nodes.length} source-linked parts`; $("part-controls").hidden = true; }
    if (state.canvasScope === "connected") {
      $("workflow-breadcrumb").hidden = true; $("part-controls").hidden = true;
      $("workflow-board-title").textContent = "The project roadmap"; $("workflow-board-subtitle").textContent = `${state.openBranches.size} open branches · follow the road from top to bottom`;
      $("workflow-board-introduction").textContent = "Use the road stops to follow the pathway. Click a box to unfold its actual parts in a side bay; several branches can remain open.";
      $("canvas-count").textContent = `12 road stops · ${graph.counts.parts} visible catalog parts (${graph.counts.matchedVisible} matched + ${graph.counts.contextVisible} context) · ${graph.counts.catalogMatched} filter matches /303 catalog parts · ${graph.counts.fields} display annotations · all15 main arrows retained`;
    }
    $("canvas-focus-branch").hidden = state.canvasScope !== "connected"; $("canvas-collapse-branch").hidden = state.canvasScope !== "connected"; $("canvas-collapse-all").hidden = state.canvasScope !== "connected";
    $("canvas-focus-branch").textContent = selected?.role === "leaf" || state.displayFieldIndex.has(selected?.id) ? "Focus selection" : "Focus branch";
    if (state.canvasScope === "connected" && selected?.role === "stage") $("canvas-focus-branch").textContent = "Focus road stop";
    $("canvas-reset").textContent = state.canvasScope === "connected" ? "Restore roadmap" : "Reset layout";
    if (!canvasMode && ["all", "connected"].includes(state.canvasScope)) {
      $("canvas-reading-all").innerHTML = '<p class="reading-mode-intro">All detail parts. Select a record to inspect its exact inputs, outputs and evidence.</p>' + graph.nodes.map((node) => `<button type="button" class="all-part-record" data-all-part="${esc(node.id)}"><strong>${esc(node.label)}</strong><span>${esc(shortStageTitle(state.stages.find((stage) => stage.id === node.stageId)))}</span></button>`).join("");
      $("canvas-reading-all").querySelectorAll("[data-all-part]").forEach((button) => button.onclick = () => { inspectCanvasNode(button.dataset.allPart); if (window.matchMedia("(max-width:850px)").matches) showWorkflowDetailSheet(); });
    }
    if (!canvasMode) return;
    try {
      if (!state.canvas) state.canvas = new window.ProjectKnowledgeCanvas($("canvas-viewport"), { onSelect: (id) => state.canvasScope === "connected" ? activateConnectedNode(id) : inspectCanvasNode(id), onExplore: exploreCanvasNode, onEdge: inspectCanvasEdge, onViewport: (zoom) => { $("canvas-zoom-label").textContent = `${Math.round(zoom * 100)}%`; } });
      state.canvas.setGraph(graph); $("canvas-fallback").hidden = true;
      $("roadmap-navigation").hidden = state.canvasScope !== "connected";
      if (!state.roadmapNavigation && window.ProjectRoadmapNavigation) state.roadmapNavigation = new window.ProjectRoadmapNavigation($("roadmap-navigation"), { onSelectStage: focusRoadmapStop });
      state.roadmapNavigation?.update({ stages: state.stages.map((stage) => ({ id: stage.id, title: shortStageTitle(stage) })), stageId: state.stageId, openBranches: [...state.openBranches] });
    } catch {
      state.canvas?.destroy(); state.canvas = null; state.workflowView = "reading"; renderWorkflowCanvas(); $("canvas-fallback").hidden = false;
      announce("The movable renderer is unavailable. The accessible reading view contains the reviewed records.");
    }
  }

  function metricMarkup(metric) {
    const label = metric.label || metric.name || metric.metric || "Saved measure";
    const preciseUnit = /AUC|squared probability|Brier/i.test(metric.unit || "");
    const formatted = typeof metric.value === "number" ? metric.value.toLocaleString("en-US", { maximumFractionDigits: preciseUnit ? 6 : /training SD|correlation/i.test(metric.unit || "") ? 4 : 2 }) : metric.value;
    const value = metric.displayValue || metric.display_value || (metric.value == null ? "Not recorded" : `${formatted}${metric.unit ? ` ${metric.unit}` : ""}`);
    const interval = metric.confidenceInterval || metric.confidence_interval;
    const digits = interval && /AUC|squared probability|Brier/i.test(interval.unit || "") ? 6 : 2;
    const note = [metric.definition, metric.scope, interval ? `${interval.level ? `${interval.level * 100}% ` : ""}interval: ${Number(interval.lower).toFixed(digits)} to ${Number(interval.upper).toFixed(digits)} ${interval.unit || ""}` : ""].filter(Boolean).map(plain).join(" · ");
    return `<tr><th scope="row">${esc(label)}${note ? `<p class="metric-note">${esc(note)}</p>` : ""}</th><td title="${esc(metric.value ?? "Not recorded")}">${esc(value)}</td></tr>`;
  }
  function renderModels() {
    const query = normalizeSearch($("model-search").value), status = $("model-status").value, family = $("model-family").value;
    const visible = state.studies.filter((study) => (!query || normalizeSearch([studyTitle(study), study.question_title, study.canonical_id, study.what_it_predicts, study.inputs_and_method, familyTitle(study)].map(plain).join(" ")).includes(query)) && (status === "all" || status === "positive" ? status === "all" || positiveResearch(study) : useStatus(study) === status) && (family === "all" || familyId(study) === family));
    if (visible.length && !visible.some((study) => study.canonical_id === state.methodId)) state.methodId = visible[0].canonical_id;
    $("model-count").textContent = `${visible.length} / ${state.studies.length} records`;
    $("model-empty").hidden = Boolean(visible.length);
    $("model-list").innerHTML = visible.map((study) => `<button class="model-item ${study.canonical_id === state.methodId ? "selected" : ""}" type="button" data-method="${esc(study.canonical_id)}" aria-pressed="${study.canonical_id === state.methodId}"><strong>${esc(studyTitle(study))}</strong><span class="item-family">${esc(familyTitle(study))}</span>${pill(useStatus(study))}</button>`).join("");
    $("model-list").querySelectorAll("[data-method]").forEach((button) => button.onclick = () => { state.methodId = button.dataset.method; renderModels(); setHash(); announce(`Selected ${studyTitle(state.studyById.get(state.methodId))}`); });
    const study = visible.find((item) => item.canonical_id === state.methodId);
    if (!study) { $("model-detail").innerHTML = '<h3>No matching study</h3><p class="detail-summary">Clear a filter to return to the reviewed records.</p>'; return; }
    const statusCode = useStatus(study), metrics = list(study.metrics);
    const reuse = study.appropriate_use?.reusable_parts || study.reusable_parts;
    const limitations = study.limits || study.what_failed_or_limits || study.limitations;
    const scope = study.scope || study.dateWindows || study.dates_and_counts;
    const costs = study.costs || study.scope?.costs;
    const hasPositive = study.has_positive_finding === true;
    const positiveType = String(study.positive_finding_type || "");
    const positiveHeading = /software|data|display|coverage/i.test(positiveType) ? "Useful completed work" : "Positive finding, with limits";
    const experimentNode = state.nodeById.get(`experiment:${study.canonical_id}`) || state.nodes.find((node) => node.experiment?.canonical_id === study.canonical_id || node.id === study.canonical_id);
    $("model-detail").innerHTML = `<div class="detail-kicker"><p class="eyebrow">${esc(familyTitle(study))}</p>${pill(statusCode)}</div><h3>${esc(studyTitle(study))}</h3>${study.question_title ? `<p class="question">${esc(study.question_title)}</p>` : ""}<div class="definition-grid"><dl><dt>What it predicts or measures</dt><dd>${esc(plain(study.what_it_predicts || study.target))}</dd></dl><dl><dt>Compared with</dt><dd>${esc(plain(study.baseline || study.baseline_or_control))}</dd></dl></div>${section("How it worked", `<p>${esc(plain(study.inputs_and_method || study.what_was_done))}</p>`)}${hasPositive && study.positive_finding ? `<div class="evidence-banner"><h4>${positiveHeading}</h4><p>${esc(plain(study.positive_finding))}</p></div>` : ""}${section("Saved answer", `<p>${esc(plain(study.reviewed_verdict || study.answer || study.verdict))}</p>`)}${metrics.length ? section("Saved measurements", `<table class="metrics"><tbody>${metrics.map(metricMarkup).join("")}</tbody></table>`) : '<p class="scope-note">No measured performance score is recorded for this design, data stage or diagnostic. Missing results are not zero.</p>'}${scope ? `<div class="scope-note"><strong>Sample and timing:</strong> ${esc(plain(scope))}${costs ? `<br><strong>Costs:</strong> ${esc(plain(costs))}` : ""}</div>` : '<p class="scope-note">Exact date, sample or cost fields were not attached here. Do not assume they match another test.</p>'}${section("What can be reused", itemList(reuse || ["Use the referenced code or idea only within its recorded evidence and current project permissions."]))}${section("What this does not establish", itemList(limitations || ["No promoted model or trading authorization."]))}${experimentNode ? `<section class="detail-section"><button class="record-chip" id="open-model-connections">See this study's project connections</button></section>` : ""}<details><summary>Evidence and canonical record</summary>${evidenceMarkup(evidenceIds(study))}<p class="identifier">${esc(study.canonical_id)} · ${esc(human(study.status || study.registry_status))}</p><p class="meta">A positive finding, completed software, and model promotion are separate claims. No model is promoted.</p></details>`;
    $("open-model-connections")?.addEventListener("click", () => openNode(experimentNode.id));
  }
  function renderHighlights() {
    $("highlight-list").innerHTML = state.studies.filter((study) => highlightIds.has(study.canonical_id) && positiveResearch(study)).map((study) => `<button type="button" class="highlight-card" data-highlight="${esc(study.canonical_id)}"><span class="pill signal">Exploratory finding</span><strong>${esc(studyTitle(study))}</strong><span>${esc(plain(study.positive_finding))}</span><small>Read the target, control and limits</small></button>`).join("");
    $("highlight-list").querySelectorAll("[data-highlight]").forEach((button) => button.onclick = () => openMethod(button.dataset.highlight));
  }

  function nodeGroup(node) {
    const kind = String(node.kind || "other").toLowerCase();
    if (kind === "variable_stage") return { label: "Variable group", color: "#789d94" };
    if (/variable|feature/.test(kind)) return { label: "Variable", color: "#5b8d82" };
    if (/experiment|model|diagnostic|study/.test(kind)) return { label: "Study", color: "#6682ab" };
    if (/code|script|component|workflow/.test(kind)) return { label: "Code & workflow", color: "#7481a3" };
    if (/source|data|artifact/.test(kind)) return { label: "Data & evidence", color: "#7c98a9" };
    if (/block|constraint|gate|risk/.test(kind)) return { label: "Boundary", color: "#b59462" };
    return { label: human(node.kind || "Project record"), color: "#7a9b9c" };
  }
  function renderRecords() {
    const query = normalizeSearch($("relation-search").value), kind = $("relation-type").value;
    const visible = state.nodes.filter((node) => (kind === "all" || node.kind === kind) && (!query || normalizeSearch([nodeTitle(node), node.id, node.summary, node.variable?.formula || node.formula, node.kind].map(plain).join(" ")).includes(query))).sort((a, b) => nodeTitle(a).localeCompare(nodeTitle(b)));
    $("record-count").textContent = `${visible.length} / ${state.nodes.length} records`;
    $("record-list").innerHTML = visible.length ? visible.map((node) => `<button class="kg-record ${node.id === state.nodeId ? "selected" : ""}" type="button" data-node="${esc(node.id)}" aria-pressed="${node.id === state.nodeId}"><strong>${esc(nodeTitle(node))}</strong><small>${esc(nodeGroup(node).label)}</small></button>`).join("") : '<p class="empty">No matching records. Try another word or record type.</p>';
    $("record-list").querySelectorAll("[data-node]").forEach((button) => button.onclick = () => { state.nodeId = button.dataset.node; state.expandedConnections = false; renderRecords(); renderRecordDetail(); setHash(); });
  }
  function relationsFor(id) { return state.edges.filter((edge) => edge.source === id || edge.target === id); }
  function renderRecordDetail() {
    const node = state.nodeById.get(state.nodeId); if (!node) return;
    const related = relationsFor(node.id);
    $("neighborhood-title").textContent = nodeTitle(node);
    const studyId = node.experiment?.canonical_id || node.id.replace(/^experiment:/, "");
    const study = state.studyById.get(studyId);
    const variable = node.variable || (node.kind === "variable" ? node : null);
    $("record-detail").innerHTML = `<div class="detail-kicker"><p class="eyebrow">${esc(nodeGroup(node).label)}</p><span class="pill diagnostic">${esc(human(node.lifecycle_status || node.status || "Documentary record"))}</span></div><h3>${esc(nodeTitle(node))}</h3><p class="detail-summary">${esc(node.summary || "No additional summary was supplied.")}</p>${node.scope ? `<p class="scope-note">${esc(plain(node.scope))}</p>` : ""}${variable ? section("Variable definition", `<p>${esc(plain(variable.formula || variable.definition))}</p><p class="meta">${esc(plain(variable.stage || ""))} · A definition is not predictive proof.</p>${variable.consumers ? itemList(variable.consumers) : ""}`) : ""}${study ? `<button id="open-related-study" class="record-chip">Read the study and its saved results</button>` : ""}${section(`Typed connections (${related.length})`, `<div class="relation-links">${related.map((edge) => { const outgoing = edge.source === node.id, other = state.nodeById.get(outgoing ? edge.target : edge.source); return other ? `<button class="relation-link" data-related="${esc(other.id)}"><span>${outgoing ? "To" : "From"} · ${esc(edgeRelation(edge))}</span><strong>${esc(nodeTitle(other))}</strong></button>` : ""; }).join("") || '<p class="meta">No typed connections were supplied for this record.</p>'}</div>`)}<details class="detail-section"><summary>Source evidence</summary>${evidenceMarkup(evidenceIds(node))}<p class="identifier">${esc(node.id)}</p></details>`;
    $("record-detail").querySelectorAll("[data-related]").forEach((button) => button.onclick = () => { state.nodeId = button.dataset.related; state.expandedConnections = false; renderRecords(); renderRecordDetail(); setHash(); });
    $("open-related-study")?.addEventListener("click", () => openMethod(study.canonical_id));
    drawRelations(); announce(`Selected ${nodeTitle(node)}. ${related.length} typed connections.`);
  }
  function shortLines(value, width, limit = 3) {
    const words = String(value).split(/\s+/), lines = []; let current = "";
    for (const word of words) { if (current && `${current} ${word}`.length > width) { lines.push(current); current = word; } else current += `${current ? " " : ""}${word}`; }
    if (current) lines.push(current);
    if (lines.length > limit) return [...lines.slice(0, limit - 1), `${lines[limit - 1].slice(0, Math.max(1, width - 1))}…`];
    return lines;
  }
  function drawRelations() {
    if (state.view !== "relations") return;
    const node = state.nodeById.get(state.nodeId); if (!node) return;
    const svg = $("relation-svg"), width = Math.max(270, $("graph-wrap").clientWidth), mobile = width < 540;
    const unique = new Map(); for (const edge of relationsFor(node.id)) { const id = edge.source === node.id ? edge.target : edge.source; if (state.nodeById.has(id) && !unique.has(id)) unique.set(id, { node: state.nodeById.get(id), edge }); }
    const all = [...unique.values()].sort((a, b) => nodeTitle(a.node).localeCompare(nodeTitle(b.node)));
    const initialLimit = mobile ? 3 : width < 760 ? 6 : 10;
    const shown = all.slice(0, state.expandedConnections ? 40 : initialLimit);
    const boxWidth = mobile ? Math.min(260, width - 55) : Math.min(205, (width - 120) / 3);
    const boxHeight = 77;
    let height, selectedPosition, positions;
    if (mobile) {
      height = Math.max(285, 110 + shown.length * 102); selectedPosition = { x: width / 2, y: 57 };
      positions = shown.map((item, index) => ({ ...item, x: width / 2, y: 158 + index * 102 }));
    } else if (width < 760) {
      height = Math.max(340, 145 + Math.ceil(shown.length / 2) * 110); selectedPosition = { x: width / 2, y: 58 };
      positions = shown.map((item, index) => ({ ...item, x: index % 2 ? width * .73 : width * .27, y: 165 + Math.floor(index / 2) * 110 }));
    } else {
      const rows = Math.ceil(shown.length / 2); height = Math.max(380, rows * 98 + 55); selectedPosition = { x: width / 2, y: height / 2 };
      positions = shown.map((item, index) => ({ ...item, x: index % 2 ? width - boxWidth / 2 - 17 : boxWidth / 2 + 17, y: 55 + Math.floor(index / 2) * 98 }));
    }
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.style.maxHeight = "none"; svg.style.height = `${height}px`; svg.replaceChildren();
    const defs = svgElement("defs"), marker = svgElement("marker", { id: "project-edge-marker", viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 5, markerHeight: 5, orient: "auto-start-reverse" }); marker.append(svgElement("path", { d: "M0,0 L10,5 L0,10 Z", fill: "#afc7d2" })); defs.append(marker); svg.append(defs);
    for (const item of positions) {
      const sourceIsSelected = item.edge.source === node.id;
      let x1 = selectedPosition.x, y1 = selectedPosition.y, x2 = item.x, y2 = item.y;
      if (mobile) { x1 -= boxWidth / 2 + 5; x2 -= boxWidth / 2 + 5; }
      else if (width < 760) { y1 += boxHeight / 2; y2 -= boxHeight / 2; }
      else { x1 += Math.sign(x2 - x1) * (boxWidth / 2 + 2); x2 -= Math.sign(x2 - x1) * (boxWidth / 2 + 2); }
      const path = mobile ? `M${x1},${y1}C10,${y1} 10,${y2} ${x2},${y2}` : `M${x1},${y1}C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
      svg.append(svgElement("path", { d: path, class: "graph-edge", [sourceIsSelected ? "marker-end" : "marker-start"]: "url(#project-edge-marker)" }));
    }
    function drawNode(item, position, selected) {
      const group = svgElement("g", { class: "svg-node", transform: `translate(${position.x - boxWidth / 2},${position.y - boxHeight / 2})`, tabindex: "0", role: "button", "aria-label": `${selected ? "Selected: " : "Open "}${nodeTitle(item)}` });
      const color = nodeGroup(item).color;
      group.append(svgElement("rect", { x: 0, y: 0, width: boxWidth, height: boxHeight, rx: 8, fill: selected ? "#e5f2ef" : "#ffffff", stroke: selected ? "#16847b" : "#cedee4", "stroke-width": selected ? 2 : 1 }));
      group.append(svgElement("rect", { x: 0, y: 14, width: 3, height: boxHeight - 28, rx: 1, fill: color }));
      group.append(svgElement("text", { x: 13, y: 19, "font-size": 14, fill: "#6b8291" }, nodeGroup(item).label));
      const lines = shortLines(nodeTitle(item), Math.max(16, Math.floor((boxWidth - 26) / 7.5)), 2);
      lines.forEach((line, index) => group.append(svgElement("text", { x: 13, y: 40 + index * 18, "font-size": 15, "font-weight": selected ? 650 : 550 }, line)));
      group.append(svgElement("title", {}, nodeTitle(item)));
      const select = () => { if (selected) { $("record-detail").scrollIntoView({ behavior: "smooth", block: "nearest" }); return; } state.nodeId = item.id; state.expandedConnections = false; renderRecords(); renderRecordDetail(); setHash(); };
      group.addEventListener("click", select); group.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); select(); } }); svg.append(group);
    }
    positions.forEach((item) => drawNode(item.node, item, false)); drawNode(node, selectedPosition, true);
    $("more-connections").hidden = state.expandedConnections || all.length <= initialLimit;
    $("more-connections").textContent = `Show ${Math.min(40, all.length)} connections`;
    $("graph-note").textContent = all.length ? `${shown.length} of ${all.length} related records shown here. Every typed connection is listed below; choose a record to follow its neighborhood.` : "This record has no supplied relationships. Search the full list to explore another record.";
  }

  function readHash() {
    const params = new URLSearchParams(location.hash.slice(1));
    const method = params.get("method"), node = params.get("node"), stage = params.get("stage");
    if (method && state.studyById.has(method)) state.methodId = method;
    if (node && state.nodeById.has(node)) state.nodeId = node;
    if (stage && state.stages.some((item) => item.id === stage)) state.stageId = stage;
    state.childStageId = params.get("parts") === "1" ? state.stageId : "";
    const sectionData = currentWorkflowSection(), partIds = new Set(list(sectionData?.nodes).map((part) => part.id));
    state.partFocusId = partIds.has(params.get("focus")) ? params.get("focus") : "";
    state.partId = partIds.has(params.get("part")) ? params.get("part") : "";
    state.showAllParts = params.get("all") === "1";
    state.canvasScope = params.get("scope") === "all" ? "all" : params.get("scope") === "context" || params.get("parts") === "1" && !params.get("scope") ? "context" : "connected";
    if (state.canvasScope === "connected") {
      state.openBranches = new Set((params.get("branches") || "").split(",").filter((id) => state.workflowSections.has(id)));
      if (state.partId) { const entry = state.workflowPartIndex.get(state.partId); if (entry) state.openBranches.add(entry.stageId); }
    }
    state.detailMode = state.partId ? "part" : "stage";
    state.readinessPinnedPart = state.partId && !readinessMatches(state.partId) ? state.partId : "";
    renderWorkflow(); renderModels(); renderRecords(); renderRecordDetail();
    setView(params.get("view") || (node ? "relations" : method ? "models" : "workflow"), false);
  }
  function populateSelect(element, values) { element.querySelectorAll("option:not(:first-child)").forEach((option) => option.remove()); for (const [value, label] of values) element.add(new Option(label, value)); }
  async function load() {
    $("loading").hidden = false; $("load-error").hidden = true; $("app").hidden = true;
    state.readiness = null; state.stageReadiness.clear(); state.partReadiness.clear(); state.edgeReadiness.clear();
    try {
      const files = ["workflow-view.public.json", "method-catalog.public.json", "project-graph.public.json", "evidence-index.public.json"];
      const [workflow, catalog, graph, evidence] = await Promise.all(files.map(async (file) => { const response = await fetch(`./${file}`, { cache: "no-store", credentials: "same-origin" }); if (!response.ok) throw Error("Reviewed data file unavailable"); return response.json(); }));
      state.sourceContentHashes = Object.fromEntries(files.map((file, index) => [file, [workflow, catalog, graph, evidence][index].content_sha256]));
      if (!Array.isArray(graph.nodes) || !Array.isArray(graph.edges) || !Array.isArray(catalog.studies)) throw Error("Unsupported reviewed data format");
      state.graph = graph; state.nodes = graph.nodes; state.edges = graph.edges; state.studies = catalog.studies;
      state.nodeById = new Map(state.nodes.map((node) => [node.id, node])); state.studyById = new Map(state.studies.map((study) => [study.canonical_id, study]));
      state.evidence = new Map(list(evidence.evidence).map((item) => [item.id, item]));
      state.stages = list(workflow.pipeline_stages || workflow.workflow_stages); state.pipelineEdges = list(workflow.pipeline_edges || workflow.workflow_edges);
      // The original overview remains available if an older documentary snapshot has no child file.
      state.workflowSections = new Map(); state.workflowPartIndex = new Map(); state.crossSectionEdges = []; $("detail-content-hash").hidden = true;
      try {
        const response = await fetch("./workflow-details.public.json", { cache: "no-store", credentials: "same-origin" });
        if (response.ok) {
          const details = await response.json();
          state.sourceContentHashes["workflow-details.public.json"] = details.content_sha256;
          state.workflowSections = new Map(list(details.sections).filter((sectionData) => state.stages.some((stage) => stage.id === sectionData.stage_id) && Array.isArray(sectionData.nodes) && Array.isArray(sectionData.edges)).map((sectionData) => [sectionData.stage_id, sectionData]));
          for (const [stageId, sectionData] of state.workflowSections) for (const node of sectionData.nodes) state.workflowPartIndex.set(node.id, { node, stageId });
          state.crossSectionEdges = list(details.cross_section_edges).filter((edge) => state.workflowPartIndex.has(edge.source) && state.workflowPartIndex.has(edge.target));
          $("detail-content-hash").textContent = `Step details · ${String(details.as_of || "Undated").slice(0, 10)} · ${state.workflowPartIndex.size} specific parts${details.content_sha256 ? ` · Content ${details.content_sha256.slice(0, 12)}…` : ""}`;
          $("detail-content-hash").title = details.content_sha256 || "Content digest not recorded"; $("detail-content-hash").hidden = false;
        }
      } catch { /* A missing optional child snapshot is stated explicitly when that step is opened. */ }
      try { const response = await fetch("./workflow-readiness.public.json", { cache: "no-store", credentials: "same-origin" }); if (response.ok) bindReadiness(await response.json()); } catch { /* Missing or stale readiness is shown as unreviewed, never connected. */ }
      $("study-count").textContent = state.studies.length; $("variable-count").textContent = state.nodes.filter((node) => node.kind === "variable").length; $("relation-count").textContent = state.edges.length;
      const date = graph.as_of || catalog.as_of || workflow.as_of;
      $("as-of").textContent = date ? `Public documentary snapshot · ${String(date).slice(0, 10)}` : "Public documentary snapshot";
      $("public-content-hash").textContent = graph.content_sha256 ? `Content ${graph.content_sha256.slice(0, 12)}…` : "Content digest not recorded";
      $("public-content-hash").title = graph.content_sha256 || "Content digest not recorded";
      $("current-question").textContent = workflow.current_question?.question || workflow.current_question || workflow.working_question || workflow.next_design_decision || "How should existing evidence, holdings and cash mechanics connect into a clear paper review?";
      $("current-mode").textContent = workflow.current_question?.mode ? human(workflow.current_question.mode) : "Workflow explanation and design. No new experiments or real stock actions.";
      populateSelect($("model-family"), [...new Map(state.studies.map((study) => [familyId(study), familyTitle(study)])).entries()].sort((a, b) => a[1].localeCompare(b[1])));
      populateSelect($("relation-type"), [...new Set(state.nodes.map((node) => node.kind || "other"))].sort().map((kind) => [kind, human(kind)]));
      state.methodId = state.studies.find((study) => study.canonical_id === "trajectory_master_month_v1_20261108")?.canonical_id || state.studies[0]?.canonical_id || "";
      state.stageId = state.stages[0]?.id || "";
      state.nodeId = list(graph.focus_ids).find((id) => state.nodeById.has(id)) || state.nodes.find((node) => /goal|question/.test(node.kind || ""))?.id || state.nodes[0]?.id || "";
      $("loading").hidden = true; $("app").hidden = false; state.loaded = true; renderHighlights(); readHash(); announce("Reviewed project map loaded.");
    } catch { $("loading").hidden = true; $("load-error").hidden = false; announce("The reviewed project files could not be loaded."); }
  }
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.view)));
  $("tab-workflow").parentElement.addEventListener("keydown", (event) => { if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return; const buttons = [...document.querySelectorAll("[data-view]")]; const current = buttons.indexOf(document.activeElement); if (current < 0) return; event.preventDefault(); const index = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length; buttons[index].focus(); setView(buttons[index].dataset.view); });
  ["model-search", "model-status", "model-family"].forEach((id) => $(id).addEventListener(id.includes("search") ? "input" : "change", renderModels));
  $("clear-models").onclick = () => { $("model-search").value = ""; $("model-status").value = "all"; $("model-family").value = "all"; renderModels(); };
  $("relation-search").addEventListener("input", renderRecords); $("relation-type").onchange = renderRecords;
  $("clear-relations").onclick = () => { $("relation-search").value = ""; $("relation-type").value = "all"; renderRecords(); };
  $("more-connections").onclick = () => { state.expandedConnections = true; drawRelations(); };
  $("retry-load").onclick = load;
  $("workflow-readiness-filter").onchange = () => {
    state.readinessFilter = $("workflow-readiness-filter").value; state.readinessPinnedPart = ""; state.partId = ""; state.displaySelectionId = "";
    for (const [id, entry] of state.workflowPartIndex) if (readinessMatches(id)) state.openBranches.add(entry.stageId);
    inspectCanvasNode(state.stageId);
  };
  $("readiness-reset").onclick = () => { state.readinessFilter = "current"; state.readinessPinnedPart = ""; state.partId = ""; state.displaySelectionId = ""; inspectCanvasNode(state.stageId); };
  $("workflow-canvas-mode").onclick = () => { state.workflowView = "canvas"; renderWorkflow(); requestAnimationFrame(() => state.canvas?.resize()); };
  $("workflow-reading-mode").onclick = () => { if (document.querySelector(".workflow-layout").classList.contains("canvas-expanded")) setCanvasExpanded(false); state.workflowView = "reading"; renderWorkflow(); };
  $("canvas-node-picker").onchange = () => { if ($("canvas-node-picker").value) inspectCanvasNode($("canvas-node-picker").value); };
  $("canvas-explore").onclick = () => { if ($("canvas-node-picker").value) exploreCanvasNode($("canvas-node-picker").value); };
  $("canvas-details").onclick = showWorkflowDetailSheet;
  $("canvas-zoom-in").onclick = () => state.canvas?.zoomBy(1.25);
  $("canvas-zoom-out").onclick = () => state.canvas?.zoomBy(0.8);
  $("canvas-fit").onclick = () => state.canvas?.fit();
  $("canvas-focus-branch").onclick = () => { const selected = state.displaySelectionId || state.partId; if (selected) state.canvas?.focusSelection(selected, state.stageId); else state.canvas?.focusRoadmapStop(state.stageId); };
  $("roadmap-crosslinks").onchange = () => state.canvas?.setCrosslinks($("roadmap-crosslinks").checked);
  $("canvas-collapse-branch").onclick = () => { state.openBranches.delete(state.stageId); state.partId = ""; state.displaySelectionId = ""; inspectCanvasNode(state.stageId); };
  $("canvas-collapse-all").onclick = () => { state.openBranches.clear(); state.partId = ""; state.displaySelectionId = ""; inspectCanvasNode(state.stageId); };
  $("canvas-reset").onclick = () => state.canvas?.resetLayout();
  $("canvas-menu-toggle").onclick = () => { const open = document.querySelector(".canvas-navigation").classList.toggle("map-controls-open"); $("canvas-menu-toggle").setAttribute("aria-expanded", String(open)); };
  $("canvas-expand").onclick = () => setCanvasExpanded(!document.querySelector(".workflow-layout").classList.contains("canvas-expanded"));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("stage-sheet").open && document.querySelector(".workflow-layout").classList.contains("canvas-expanded")) { event.preventDefault(); setCanvasExpanded(false); } });
  $("canvas-scope").onchange = () => {
    const scope = $("canvas-scope").value; state.canvasSelectedEdge = null;
    if (scope === "connected") { state.canvasScope = "connected"; renderWorkflow(); setHash(); return; }
    if (scope === "workflow") { returnToWorkflow(); return; }
    if (scope === "all") state.canvasScope = "all";
    else {
      state.canvasScope = "context";
      if (!state.childStageId) { state.childStageId = state.stageId; state.partFocusId = ""; state.partId = ""; }
      state.showAllParts = scope === "step"; $("part-search").value = "";
      if (scope === "focus" && state.partId) {
        const entry = state.workflowPartIndex.get(state.partId), sectionData = currentWorkflowSection();
        if (entry && entry.stageId === state.childStageId) state.partFocusId = isPartCategory(sectionData, entry.node) ? entry.node.id : partParent(sectionData, entry.node);
      }
    }
    renderWorkflow(); setHash();
  };
  $("part-search").addEventListener("input", () => { renderWorkflow(); setHash(); });
  $("part-find-toggle").onclick = () => setPartSearchVisible($("part-find-field").hidden, true);
  $("part-group-select").onchange = () => focusPartCategory($("part-group-select").value);
  $("part-show-all").onclick = () => { state.showAllParts = !state.showAllParts; state.partFocusId = ""; $("part-search").value = ""; renderWorkflow(); setHash(); };
  $("part-about-stage").onclick = () => { state.detailMode = "stage"; state.partId = ""; renderWorkflow(); setHash(); if (window.matchMedia("(max-width: 850px)").matches) showWorkflowDetailSheet(); };
  $("mobile-stage-peek").onclick = () => { if (state.stages.length) showWorkflowDetailSheet(); };
  $("close-stage-sheet").onclick = () => $("stage-sheet").close();
  $("stage-sheet").addEventListener("close", () => { if (state.view === "workflow" && !$("stage-sheet").open) activeWorkflowButton()?.focus({ preventScroll: true }); });
  $("stage-sheet").addEventListener("click", (event) => { if (event.target === $("stage-sheet")) { const box = $("stage-sheet").getBoundingClientRect(); if (event.clientY < box.top || event.clientY > box.bottom || event.clientX < box.left || event.clientX > box.right) $("stage-sheet").close(); } });
  window.addEventListener("resize", () => { if (!window.matchMedia("(max-width: 850px)").matches && $("stage-sheet").open) $("stage-sheet").close(); });
  window.addEventListener("hashchange", () => { if (state.loaded) readHash(); });
  new ResizeObserver(() => { if (state.loaded) { drawWorkflowWires(); drawRelations(); } }).observe($("workflow-map"));
  new ResizeObserver(() => { if (state.loaded && state.view === "relations") drawRelations(); }).observe($("graph-wrap"));
  load();
})();
