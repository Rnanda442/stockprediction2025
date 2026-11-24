"use strict";

(() => {
  const GROUPS = [
    { id: "evidence", title: "Evidence and timing", ids: ["market_data", "availability_clock", "forecast_contract", "features_universe", "model"] },
    { id: "input", title: "Separate input", ids: ["portfolio_state"] },
    { id: "reviews", title: "Two review paths", ids: ["entry_review", "holding_review"] },
    { id: "record", title: "Checks and records", ids: ["constraints", "paper_report", "paper_ledger", "mature_outcomes"] },
  ];
  const TITLES = {
    market_data: "Market observations", availability_clock: "Availability and clock",
    forecast_contract: "Forecast question", features_universe: "Features and universe",
    model: "Return and risk model", entry_review: "New-stock review",
    holding_review: "Holding review", portfolio_state: "Holdings and cash",
    constraints: "Cash and risk checks", paper_report: "Daily paper review",
    paper_ledger: "Paper state ledger", mature_outcomes: "Later outcomes",
  };
  const ORDER = GROUPS.flatMap((group) => group.ids);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (mark) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[mark]));

  /** Location navigation only. This component never opens or closes a branch. */
  class ProjectRoadmapNavigation {
    constructor(container, { onSelectStage } = {}) {
      if (!container || container.nodeType !== 1) throw new TypeError("A roadmap container is required.");
      this.container = container;
      this.onSelectStage = typeof onSelectStage === "function" ? onSelectStage : () => {};
      this.stageById = new Map();
      this.stageId = "";
      this.destroyed = false;
      this.previousAriaLabel = container.getAttribute("aria-label");
      container.classList.add("roadmap-navigation");
      if (!this.previousAriaLabel) container.setAttribute("aria-label", "Workflow roadmap");
      this.handleClick = (event) => {
        const button = event.target.closest?.("button[data-roadmap-stage], button[data-roadmap-direction]");
        if (!button || !container.contains(button) || button.disabled) return;
        if (button.dataset.roadmapStage) this.select(button.dataset.roadmapStage);
        else {
          const locations = this.locations();
          const index = locations.indexOf(this.stageId);
          const destination = index + (button.dataset.roadmapDirection === "previous" ? -1 : 1);
          if (index >= 0 && destination >= 0 && destination < locations.length) this.select(locations[destination]);
        }
      };
      this.handleChange = (event) => {
        if (event.target.matches?.("select[data-roadmap-picker]")) this.select(event.target.value);
      };
      container.addEventListener("click", this.handleClick);
      container.addEventListener("change", this.handleChange);
    }

    locations() { return ORDER.filter((id) => this.stageById.has(id)); }

    select(id) {
      if (!this.destroyed && this.stageById.has(id)) this.onSelectStage(id);
    }

    update({ stages = [], stageId = "", openBranches = [] } = {}) {
      if (this.destroyed) return;
      const previousStageId = this.stageId;
      const active = this.container.ownerDocument.activeElement;
      const focusStage = this.container.contains(active) ? active.dataset.roadmapStage : "";
      const focusDirection = this.container.contains(active) ? active.dataset.roadmapDirection : "";
      const focusPicker = this.container.contains(active) && active.matches?.("select[data-roadmap-picker]");
      const scrollTop = this.container.scrollTop;
      this.stageById = new Map((Array.isArray(stages) ? stages : [])
        .filter((stage) => stage && ORDER.includes(stage.id))
        .map((stage) => [stage.id, { ...stage, title: String(stage.title || stage.label || TITLES[stage.id]) }]));
      const locations = this.locations();
      this.stageId = this.stageById.has(stageId) ? stageId : locations[0] || "";
      const open = new Set(openBranches instanceof Set ? openBranches : Array.isArray(openBranches) ? openBranches : []);
      const button = (id, input = false) => {
        const stage = this.stageById.get(id);
        if (!stage) return "";
        const selected = id === this.stageId;
        const label = `${stage.title}${selected ? ", selected location" : ""}${open.has(id) ? ", open on map" : ""}. Go to this location.`;
        return `<button type="button" class="roadmap-stop${selected ? " is-current" : ""}${input ? " roadmap-input-stop" : ""}" data-roadmap-stage="${esc(id)}"${selected ? ' aria-current="location"' : ""} aria-label="${esc(label)}"><span class="roadmap-stop-title">${esc(stage.title)}</span><span class="roadmap-stop-badges">${selected ? '<span class="roadmap-current-badge">Here</span>' : ""}${open.has(id) ? '<span class="roadmap-open-badge">Open on map</span>' : ""}</span></button>`;
      };
      const groups = GROUPS.map((group) => {
        const ids = group.ids.filter((id) => this.stageById.has(id));
        if (!ids.length) return "";
        if (group.id === "input") return `<section class="roadmap-input"><h4>${esc(group.title)}</h4>${button(ids[0], true)}<p>Supplies holding review and cash checks.</p></section>`;
        const fork = group.id === "reviews";
        return `<section class="roadmap-group${fork ? " roadmap-review-group" : ""}"><h4>${esc(group.title)}</h4><ul class="${fork ? "roadmap-fork" : "roadmap-sequence"}">${ids.map((id) => `<li>${button(id)}</li>`).join("")}</ul></section>`;
      }).join("");
      const options = GROUPS.map((group) => {
        const ids = group.ids.filter((id) => this.stageById.has(id));
        return ids.length ? `<optgroup label="${esc(group.title)}">${ids.map((id) => `<option value="${esc(id)}"${id === this.stageId ? " selected" : ""}>${esc(this.stageById.get(id).title)}${open.has(id) ? " · open" : ""}</option>`).join("")}</optgroup>` : "";
      }).join("");
      const index = locations.indexOf(this.stageId);
      const currentTitle = this.stageById.get(this.stageId)?.title;
      this.container.innerHTML = `<div class="roadmap-desktop"><header class="roadmap-heading"><h3>Roadmap</h3><p>Go to a location on the map.</p></header><nav aria-label="Roadmap locations">${groups || '<p class="roadmap-empty">Roadmap locations are unavailable.</p>'}</nav><p class="roadmap-note">Open means expanded on this map, not a running service.</p></div><nav class="roadmap-compact" aria-label="Compact roadmap"><label class="roadmap-picker-label"><span>Roadmap location</span><span class="roadmap-picker-wrap"><select data-roadmap-picker aria-label="Choose a roadmap location"${locations.length ? "" : " disabled"}>${options || '<option value="">Locations unavailable</option>'}</select></span></label><div class="roadmap-location-controls"><button type="button" data-roadmap-direction="previous"${index <= 0 ? " disabled" : ""}>Previous location</button><button type="button" data-roadmap-direction="next"${index < 0 || index >= locations.length - 1 ? " disabled" : ""}>Next location</button></div><p class="roadmap-location-note">${currentTitle ? `<strong>${esc(currentTitle)}</strong>. ` : ""}These controls browse locations; they do not run the workflow.</p></nav>`;
      this.container.scrollTop = scrollTop;
      if (focusPicker) this.container.querySelector("select[data-roadmap-picker]")?.focus({ preventScroll: true });
      else if (focusDirection) {
        const requested = this.container.querySelector(`button[data-roadmap-direction="${focusDirection}"]`);
        const target = requested && !requested.disabled ? requested
          : this.container.querySelector(".roadmap-location-controls button:not(:disabled)")
            || this.container.querySelector("select[data-roadmap-picker]:not(:disabled)");
        target?.focus({ preventScroll: true });
      }
      else if (focusStage) [...this.container.querySelectorAll("button[data-roadmap-stage]")].find((item) => item.dataset.roadmapStage === focusStage)?.focus({ preventScroll: true });
      if (previousStageId && previousStageId !== this.stageId) this.revealSelected();
    }

    revealSelected() {
      const selected = this.container.querySelector('.roadmap-desktop [aria-current="location"]');
      if (!selected || !selected.getClientRects().length) return;
      const rail = this.container.getBoundingClientRect();
      const item = selected.getBoundingClientRect();
      if (item.top < rail.top + 12) this.container.scrollTop -= rail.top + 12 - item.top;
      else if (item.bottom > rail.bottom - 12) this.container.scrollTop += item.bottom - rail.bottom + 12;
    }

    destroy() {
      if (this.destroyed) return;
      this.destroyed = true;
      this.container.removeEventListener("click", this.handleClick);
      this.container.removeEventListener("change", this.handleChange);
      this.container.replaceChildren();
      this.container.classList.remove("roadmap-navigation");
      if (this.previousAriaLabel == null) this.container.removeAttribute("aria-label");
      else this.container.setAttribute("aria-label", this.previousAriaLabel);
      this.stageById.clear();
    }
  }

  window.ProjectRoadmapNavigation = ProjectRoadmapNavigation;
})();
