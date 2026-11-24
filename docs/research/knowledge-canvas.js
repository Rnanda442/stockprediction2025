"use strict";

(() => {
  /** A documentary graph workspace. Positions affect presentation only. */
  class ProjectKnowledgeCanvas {
    constructor(element, callbacks = {}) {
      if (typeof window.cytoscape !== "function") throw new Error("Local graph renderer unavailable");
      this.element = element;
      this.callbacks = callbacks;
      this.cache = new Map();
      this.initialLayouts = new Map();
      this.connectedPositions = new Map();
      this.roadmapPlan = new Map(); this.roadmapBandEnds = new Map(); this.showCrosslinks = false;
      this.currentKey = "";
      this.graph = null;
      this.suppressTapUntil = 0;
      this.dragged = false;
      this.pendingFit = false;
      this.cy = window.cytoscape({
        container: element,
        renderer: { name: "canvas" },
        elements: [],
        layout: { name: "preset" },
        minZoom: 0.06,
        maxZoom: 2.5,
        wheelSensitivity: 0.2,
        boxSelectionEnabled: false,
        autounselectify: true,
        userPanningEnabled: true,
        userZoomingEnabled: true,
        motionBlur: false,
        style: [
          { selector: "node", style: {
            "shape": "round-rectangle", "width": 190, "height": 82,
            "background-color": "#ffffff", "border-color": "#9eb9c6", "border-width": 1.5,
            "label": "data(label)", "color": "#15394b", "font-family": "Segoe UI, Arial, sans-serif",
            "font-size": 20, "font-weight": 500, "text-wrap": "wrap", "text-max-width": 166,
            "text-valign": "center", "text-halign": "center", "line-height": 1.3,
            "min-zoomed-font-size": 0, "overlay-opacity": 0,
          } },
          { selector: "node[role = 'category']", style: { "background-color": "#eaf6f3", "border-color": "#70a8a2", "font-weight": 600 } },
          { selector: "node[role = 'stage']", style: { "background-color": "#eef4fc", "border-color": "#8aa8c9", "font-weight": 600 } },
          { selector: "node[role = 'formula']", style: { "width": 300, "height": 112, "font-size": 14, "text-max-width": 272, "background-color": "#fff7de", "border-color": "#ccb874" } },
          { selector: "node[role = 'component']", style: { "width": 230, "height": 76, "font-size": 14, "text-max-width": 208, "background-color": "#f2f2f8", "border-color": "#aaa4c2" } },
          { selector: "node[role = 'definition']", style: { "width": 270, "height": 110, "font-size": 14, "text-max-width": 244, "background-color": "#f4f8ed", "border-color": "#a0b58a" } },
          { selector: "edge", style: {
            "curve-style": "bezier", "control-point-step-size": 55,
            "width": 1.65, "line-color": "#b7c9d1", "target-arrow-color": "#9fb7c3",
            "target-arrow-shape": "triangle", "arrow-scale": 1.1,
            "label": "", "font-family": "Segoe UI, Arial, sans-serif", "font-size": 14,
            "text-wrap": "wrap", "text-max-width": 140, "text-rotation": "autorotate",
            "text-background-color": "#ffffff", "text-background-opacity": 0.96,
            "text-background-padding": 4, "text-background-shape": "roundrectangle",
            "color": "#36596b", "min-zoomed-font-size": 10, "overlay-opacity": 0,
          } },
          { selector: "edge[relation = 'contains']", style: { "line-style": "dashed", "line-dash-pattern": [7, 4] } },
          { selector: "edge[kind = 'membership']", style: { "line-style": "dashed", "line-color": "#c5ccd4", "target-arrow-color": "#c5ccd4", "width": 1.1 } },
          { selector: "edge[kind = 'declared_reference']", style: { "line-style": "dotted", "line-color": "#539990", "target-arrow-color": "#539990", "width": 2 } },
          { selector: "edge[kind = 'annotation']", style: { "line-style": "dotted", "line-color": "#b9a774", "target-arrow-color": "#b9a774", "width": 1.2 } },
          { selector: "edge[kind = 'main']", style: { "width": 4, "line-color": "#244e67", "target-arrow-color": "#244e67", "target-arrow-shape": "triangle", "arrow-scale": 1.4, "curve-style": "taxi", "taxi-direction": "downward", "taxi-turn": "50%" } },
          { selector: "edge[mainBypass = 1]", style: { "width": 2.2, "line-style": "dashed", "line-color": "#6e8797", "target-arrow-color": "#6e8797", "curve-style": "unbundled-bezier", "control-point-distances": 280, "control-point-weights": 0.5 } },
          { selector: ".canvas-muted", style: { "opacity": 0.75 } },
          { selector: "node.canvas-neighbor", style: { "border-color": "#498e9e", "border-width": 2, "background-color": "#f1f8fb" } },
          { selector: "node.canvas-selected", style: { "border-color": "#173f59", "border-width": 4, "underlay-color": "#173f59", "underlay-opacity": 0.13, "underlay-padding": 6, "z-index": 10 } },
          { selector: "edge.canvas-linked", style: { "width": 2.6, "label": "data(label)", "z-index": 8 } },
          { selector: "edge.canvas-incoming", style: { "line-color": "#6b8197", "target-arrow-color": "#6b8197" } },
          { selector: "edge.canvas-outgoing", style: { "line-color": "#365d79", "target-arrow-color": "#365d79" } },
          { selector: "edge.canvas-selected", style: { "width": 4, "line-color": "#173f59", "target-arrow-color": "#173f59", "label": "data(label)", "z-index": 12 } },
          { selector: "edge[kind = 'main'].canvas-muted", style: { "opacity": 0.85 } },
          { selector: "node[role = 'stage'].canvas-muted", style: { "opacity": 0.9 } },
          { selector: "edge.roadmap-cross-quiet", style: { "opacity": 0.07, "width": 1, "label": "" } },
          { selector: "edge.roadmap-cross-quiet.canvas-linked", style: { "opacity": 0.55, "label": "data(label)" } },
          ...Object.entries({ connected: ["#e7f4ec", "#397c58"], needs_connection: ["#fff3d9", "#a67321"], blocked: ["#faeae8", "#aa5250"], historical: ["#eff1f3", "#7b8995"], unknown: ["#ffffff", "#9aabb5"] }).flatMap(([status, colors]) => [
            { selector: `node[readiness = '${status}']`, style: { "background-color": colors[0], "border-color": colors[1] } },
            { selector: `edge[readiness = '${status}']`, style: { "line-color": colors[1], "target-arrow-color": colors[1] } },
          ]),
          { selector: "node[filterContext = true]", style: { "border-style": "dotted" } },
        ],
      });
      this.cy.on("grab", "node", () => { this.dragged = false; });
      this.cy.on("drag", "node", () => { this.dragged = true; });
      this.cy.on("free", "node", () => {
        if (this.dragged) this.suppressTapUntil = Date.now() + 320;
        this.remember();
      });
      this.cy.on("onetap", "node", (event) => {
        if (Date.now() < this.suppressTapUntil) return;
        this.select(event.target.id());
        this.callbacks.onSelect?.(event.target.id());
      });
      this.cy.on("dbltap", "node", (event) => {
        if (Date.now() < this.suppressTapUntil) return;
        this.callbacks.onExplore?.(event.target.id());
      });
      this.cy.on("tap", "edge", (event) => {
        if (Date.now() < this.suppressTapUntil) return;
        this.selectEdge(event.target.id());
        this.callbacks.onEdge?.(event.target.data());
      });
      this.cy.on("mouseover", "node, edge", (event) => {
        const data = event.target.data();
        this.element.title = `${data.title || data.label || "Project record"}\n${data.readinessLabel || "Documentary relation · operational connection not separately reviewed"}${data.filterContext ? " · retained context" : ""}${data.readinessReason ? `\n${data.readinessReason}` : ""}${data.readinessEvidenceIds?.length ? `\nEvidence: ${data.readinessEvidenceIds.join(", ")}` : ""}`;
      });
      this.cy.on("mouseout", "node, edge", () => { this.element.title = ""; });
      this.cy.on("tap", (event) => {
        if (event.target === this.cy && Date.now() >= this.suppressTapUntil) this.clearHighlight();
      });
      this.cy.on("pan zoom", () => {
        const entry = this.cache.get(this.currentKey);
        if (entry) { entry.pan = { ...this.cy.pan() }; entry.zoom = this.cy.zoom(); }
        this.callbacks.onViewport?.(this.cy.zoom());
      });
      this.observer = new ResizeObserver(() => this.resize());
      this.observer.observe(element);
      this.keydown = (event) => {
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const step = event.shiftKey ? 100 : 45, pan = this.cy.pan();
        if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) {
          event.preventDefault();
          this.cy.pan({ x: pan.x + (event.key === "ArrowLeft" ? step : event.key === "ArrowRight" ? -step : 0), y: pan.y + (event.key === "ArrowUp" ? step : event.key === "ArrowDown" ? -step : 0) });
        } else if (["+", "=", "-", "_"].includes(event.key)) {
          event.preventDefault(); this.zoomBy(event.key === "-" || event.key === "_" ? 1 / 1.22 : 1.22);
        } else if (event.key.toLowerCase() === "f") { event.preventDefault(); this.fit(); }
      };
      element.addEventListener("keydown", this.keydown);
    }

    remember() {
      if (!this.currentKey) return;
      const positions = {};
      this.cy.nodes().forEach((node) => { positions[node.id()] = { ...node.position() }; });
      if (this.currentKey === "connected-branches") for (const [id, position] of Object.entries(positions)) this.connectedPositions.set(id, { ...position });
      this.cache.set(this.currentKey, { positions, zoom: this.cy.zoom(), pan: { ...this.cy.pan() } });
      if (this.cache.size > 45) this.cache.delete(this.cache.keys().next().value);
    }

    setGraph(graph) {
      if (graph.incremental) { this.updateConnectedGraph(graph); return; }
      const signature = `${graph.key}|${graph.nodes.map((node) => node.id).sort().join("|")}|${graph.edges.map((edge) => edge.id).sort().join("|")}`;
      this.graph = graph;
      if (signature === this.currentKey) { this.select(graph.selectedId); return; }
      this.remember();
      this.currentKey = signature;
      const previous = this.cache.get(signature);
      const columns = Math.max(2, Math.ceil(Math.sqrt(graph.nodes.length * 1.35)));
      this.cy.batch(() => {
        this.cy.elements().remove();
        this.cy.add(graph.nodes.map((node, index) => ({ group: "nodes", data: { ...node }, position: previous?.positions[node.id] || { x: (index % columns) * 300, y: Math.floor(index / columns) * 220 } })));
        this.cy.add(graph.edges.map((edge) => ({ group: "edges", data: { ...edge } })));
      });
      this.cy.resize();
      if (previous) { this.cy.zoom(previous.zoom); this.cy.pan(previous.pan); }
      else { this.runLayout(false); this.initialLayouts.set(signature, this.readPositions()); this.initialViewport(); }
      this.select(graph.selectedId);
      this.remember();
    }

    updateConnectedGraph(graph) {
      const first = this.currentKey !== "connected-branches";
      if (first) { this.remember(); this.cy.elements().remove(); this.currentKey = "connected-branches"; }
      this.graph = graph;
      if (graph.roadmap) this.prepareRoadmap(graph);
      this.cy.nodes().forEach((node) => this.connectedPositions.set(node.id(), { ...node.position() }));
      const wantedNodes = new Set(graph.nodes.map((node) => node.id)), wantedEdges = new Set(graph.edges.map((edge) => edge.id));
      this.cy.batch(() => {
        this.cy.edges().filter((edge) => !wantedEdges.has(edge.id())).remove();
        this.cy.nodes().filter((node) => !wantedNodes.has(node.id())).remove();
        for (const data of graph.nodes) {
          const existing = this.cy.getElementById(data.id);
          if (existing.length) { existing.data({ ...data }); continue; }
          let position = this.connectedPositions.get(data.id);
          if (graph.roadmap && data.role === "stage") position = this.roadmapPlan.get(data.id);
          if (position && (!Number.isFinite(position.x) || !Number.isFinite(position.y) || data.role !== "stage" && this.positionOccupied(data, position))) position = null;
          if (!position && data.role === "stage") { const index = graph.nodes.filter((node) => node.role === "stage").findIndex((node) => node.id === data.id); position = { x: index % 3 * 300, y: Math.floor(index / 3) * 190 }; }
          if (!position) position = graph.roadmap ? this.placeRoadmapNode(data) : this.placePetal(data, graph);
          const added = this.cy.add({ group: "nodes", data: { ...data }, position });
          if (graph.roadmap && data.role === "stage") added.lock();
          this.connectedPositions.set(data.id, { ...position });
        }
        for (const data of graph.edges) {
          const edge = this.cy.getElementById(data.id);
          if (edge.length) edge.data({ ...data }); else this.cy.add({ group: "edges", data: { ...data } });
        }
      });
      this.cy.resize();
      if (first) {
        const cached = this.cache.get("connected-branches");
        if (cached) { this.cy.zoom(cached.zoom); this.cy.pan(cached.pan); } else this.initialViewport();
      }
      this.select(graph.selectedId);
      this.setCrosslinks(this.showCrosslinks);
      this.remember();
    }

    prepareRoadmap(graph) {
      const hubs = { market_data: [0, 0], availability_clock: [0, 600], forecast_contract: [0, 1200], features_universe: [0, 1800], model: [0, 2400], entry_review: [-300, 3000], holding_review: [300, 3000], constraints: [0, 3600], paper_report: [0, 4200], paper_ledger: [0, 4800], mature_outcomes: [0, 5400], portfolio_state: [900, 3000] };
      this.roadmapPlan = new Map(Object.entries(hubs).map(([id, value]) => [id, { x: value[0], y: value[1] }])); this.roadmapBandEnds = new Map();
      for (const [stageId, hub] of Object.entries(hubs)) {
        const catalog = graph.roadmapCatalog.filter((node) => node.stageId === stageId), children = new Map();
        for (const node of catalog) { const parent = node.parent_id || stageId; if (!children.has(parent)) children.set(parent, []); children.get(parent).push(node); }
        for (const list of children.values()) list.sort((a, b) => (a.order || 0) - (b.order || 0) || a.id.localeCompare(b.id));
        const direction = stageId === "entry_review" ? -1 : 1;
        const offset = stageId === "holding_review" ? 1000 : stageId === "portfolio_state" ? (this.roadmapBandEnds.get("holding_review")?.x || 4500) - hub[0] + 600 : 550;
        let column = 0;
        for (const root of children.get(stageId) || []) {
          const ordered = [], seen = new Set([root.id]);
          const visit = (id) => { for (const child of children.get(id) || []) if (!seen.has(child.id)) { seen.add(child.id); ordered.push(child); visit(child.id); } }; visit(root.id);
          this.roadmapPlan.set(root.id, { x: hub[0] + direction * (offset + column * 260), y: hub[1] });
          ordered.forEach((node, index) => this.roadmapPlan.set(node.id, { x: hub[0] + direction * (offset + (column + 1 + Math.floor(index / 4)) * 260), y: hub[1] + (index % 4 - 1.5) * 130 }));
          column += 2 + Math.ceil(ordered.length / 4);
        }
        this.roadmapBandEnds.set(stageId, { x: hub[0] + direction * (offset + column * 260), y: hub[1], direction });
      }
      const counters = new Map();
      for (const node of graph.nodes.filter((node) => node.display_only).sort((a, b) => a.id.localeCompare(b.id))) {
        const end = this.roadmapBandEnds.get(node.stageId); if (!end) continue;
        const index = counters.get(node.stageId) || 0; counters.set(node.stageId, index + 1);
        this.roadmapPlan.set(node.id, { x: end.x + end.direction * (300 + Math.floor(index / 4) * 340), y: end.y + (index % 4 - 1.5) * 130 });
      }
    }

    placeRoadmapNode(data) {
      const planned = this.roadmapPlan.get(data.id) || { x: 550, y: 0 }, result = { ...planned }, direction = this.roadmapBandEnds.get(data.stageId)?.direction || 1;
      for (let attempt = 0; attempt < 500 && this.positionOccupied(data, result); attempt++) result.x += direction * 340;
      return result;
    }

    focusRoadmapStop(stageId) {
      const hub = this.cy.getElementById(stageId); if (!hub.length) return;
      this.cy.resize(); const zoom = 0.75, pos = hub.position();
      const height = this.cy.height();
      const top = height < 420 ? Math.max(65, height * 0.4) : Math.min(230, Math.max(195 * zoom + 60, height - 600 * zoom - 45));
      this.cy.zoom(zoom); this.cy.pan({ x: this.cy.width() * 0.32 - pos.x * zoom, y: top - pos.y * zoom }); this.remember();
    }

    setCrosslinks(show) {
      this.showCrosslinks = Boolean(show);
      this.cy.edges().forEach((edge) => edge.toggleClass("roadmap-cross-quiet", this.graph?.roadmap && !show && Boolean(edge.data("isCrosslink"))));
    }

    placePetal(data, graph) {
      const parent = this.cy.getElementById(data.parent_id || data.owner_id || data.stageId), hub = this.cy.getElementById(data.stageId);
      const rawOrigin = parent.length ? parent.position() : hub.length ? hub.position() : { x: 0, y: 0 };
      const origin = Number.isFinite(rawOrigin.x) && Number.isFinite(rawOrigin.y) ? rawOrigin : { x: 0, y: 0 };
      const siblings = graph.nodes.filter((node) => (node.parent_id || node.owner_id || node.stageId) === (data.parent_id || data.owner_id || data.stageId) && node.id !== data.stageId);
      const index = Math.max(0, siblings.findIndex((node) => node.id === data.id)), count = Math.max(1, siblings.length);
      const outward = hub.length ? Math.atan2(origin.y - hub.position().y, origin.x - hub.position().x) : -Math.PI / 2;
      const angle = parent.data("role") === "stage" ? -Math.PI / 2 + index * Math.PI * 2 / count : outward + (index - (count - 1) / 2) * Math.min(0.52, Math.PI * 1.5 / count);
      const { width } = this.nodeDimensions(data.role);
      let candidate;
      for (let attempt = 0; attempt < 70; attempt++) {
        const radius = (parent.data("role") === "stage" ? 260 : 180) + Math.floor(index / 10) * 80 + attempt * 25;
        const adjusted = angle + (attempt % 2 ? 1 : -1) * Math.floor(attempt / 2) * 0.085;
        candidate = { x: origin.x + Math.cos(adjusted) * radius, y: origin.y + Math.sin(adjusted) * radius };
        if (!this.positionOccupied(data, candidate)) return candidate;
      }
      // A finite occupied graph always has free space beyond its rightmost bounding edge.
      // This deterministic fallback guarantees separation even if every nearby orbit is occupied.
      const right = this.cy.nodes().reduce((value, other) => Math.max(value, other.position().x + this.nodeDimensions(other.data("role")).width / 2), origin.x);
      return { x: right + width / 2 + 24, y: origin.y };
    }

    nodeDimensions(role) {
      return role === "formula" ? { width: 300, height: 112 } : role === "definition" ? { width: 270, height: 110 } : role === "component" ? { width: 230, height: 76 } : { width: 190, height: 82 };
    }

    positionOccupied(data, position) {
      // Batch-added nodes may not have computed styles: use explicit role dimensions on both sides.
      const candidate = this.nodeDimensions(data.role);
      return this.cy.nodes().some((other) => { const pos = other.position(), size = this.nodeDimensions(other.data("role")); return Math.abs(pos.x - position.x) < (size.width + candidate.width) / 2 + 24 && Math.abs(pos.y - position.y) < (size.height + candidate.height) / 2 + 18; });
    }

    focusBranch(stageId, peek = false) {
      if (this.graph?.roadmap && peek) { this.focusRoadmapStop(stageId); return; }
      const nodes = this.cy.nodes().filter((node) => node.id() === stageId || node.data("stageId") === stageId || (node.data("stageIds") || []).includes(stageId));
      if (nodes.length) { this.cy.fit(nodes, 65); if (peek && this.cy.zoom() < 0.6) { this.cy.zoom(0.6); this.cy.center(this.cy.getElementById(stageId)); } this.remember(); }
    }
    focusSelection(id, stageId, peek = false) {
      const node = this.cy.getElementById(id || "");
      if (!node.length || node.data("role") === "stage") { this.focusBranch(stageId); return; }
      let neighborhood = node.union(node.connectedEdges().filter((edge) => edge.data("kind") === "annotation").connectedNodes());
      if (node.data("role") === "category") neighborhood = neighborhood.union(this.cy.nodes().filter((child) => child.data("parent_id") === id));
      const parent = this.cy.getElementById(node.data("parent_id") || node.data("owner_id") || "");
      if (parent.length) neighborhood = neighborhood.union(parent);
      this.cy.fit(neighborhood, 65); if (peek && this.cy.zoom() < 0.6) { this.cy.zoom(0.6); this.cy.center(node); } this.remember();
    }

    runLayout(fit = true) {
      if (this.cy.nodes().length <= 24) {
        // Small scopes get a bounded two-dimensional starting layout, not a long force chain.
        const columns = Math.max(2, Math.round(Math.sqrt(this.cy.nodes().length)));
        this.cy.nodes().forEach((node, index) => node.position({ x: (index % columns) * 300, y: Math.floor(index / columns) * 190 }));
        if (fit) this.initialViewport();
        return;
      }
      this.cy.layout({
        name: "cose", animate: false, fit: false, randomize: false,
        nodeDimensionsIncludeLabels: true, componentSpacing: 110,
        nodeRepulsion: () => 7000, nodeOverlap: 14,
        idealEdgeLength: () => 75, edgeElasticity: () => 75,
        gravity: 1.1, numIter: this.cy.nodes().length > 100 ? 350 : 650,
        initialTemp: 180, coolingFactor: 0.96, minTemp: 1,
      }).run();
      if (fit) this.initialViewport();
    }
    readPositions() {
      const result = {}; this.cy.nodes().forEach((node) => { result[node.id()] = { ...node.position() }; }); return result;
    }

    initialViewport() {
      if (this.element.clientWidth < 10 || this.element.clientHeight < 10) { this.pendingFit = true; return; }
      this.pendingFit = false;
      if (this.graph?.roadmap) { this.focusRoadmapStop(this.graph.selectedStageId || "market_data"); return; }
      this.cy.fit(undefined, 50);
      const minimum = this.graph?.nodes.length > 100 ? 0 : this.element.clientWidth < 550 ? 0.8 : 0.65;
      if (this.cy.zoom() < minimum) {
        this.cy.zoom(minimum);
        const selected = this.cy.getElementById(this.graph?.selectedId || "");
        this.cy.center(selected.length ? selected : this.cy.nodes().first());
      }
    }

    clearHighlight() {
      this.cy.elements().removeClass("canvas-selected canvas-neighbor canvas-linked canvas-muted canvas-incoming canvas-outgoing");
    }

    select(id) {
      this.clearHighlight();
      const node = this.cy.getElementById(id || ""); if (!node.length || !node.isNode()) return;
      const connected = node.closedNeighborhood();
      this.cy.elements().not(connected).addClass("canvas-muted");
      node.neighborhood("node").addClass("canvas-neighbor");
      node.connectedEdges().addClass("canvas-linked").forEach((edge) => edge.addClass(edge.target().id() === id ? "canvas-incoming" : "canvas-outgoing"));
      node.addClass("canvas-selected");
    }

    selectEdge(id) {
      this.clearHighlight();
      const edge = this.cy.getElementById(id || ""); if (!edge.length || !edge.isEdge()) return;
      const linked = edge.union(edge.connectedNodes());
      this.cy.elements().not(linked).addClass("canvas-muted");
      edge.connectedNodes().addClass("canvas-neighbor"); edge.addClass("canvas-selected");
    }

    zoomBy(factor) {
      this.cy.zoom({ level: Math.max(this.cy.minZoom(), Math.min(this.cy.maxZoom(), this.cy.zoom() * factor)), renderedPosition: { x: this.cy.width() / 2, y: this.cy.height() / 2 } });
    }

    fit() { this.cy.resize(); this.cy.fit(undefined, 45); this.remember(); }
    resetLayout() {
      if (this.graph?.incremental) {
        const graph = this.graph; this.connectedPositions.clear(); this.cache.delete("connected-branches"); this.currentKey = ""; this.cy.elements().remove(); this.updateConnectedGraph(graph); if (graph.roadmap) this.focusRoadmapStop(graph.selectedStageId || "market_data"); else this.fit(); return;
      }
      const original = this.initialLayouts.get(this.currentKey);
      if (original) this.cy.nodes().positions((node) => original[node.id()]);
      else { this.runLayout(false); this.initialLayouts.set(this.currentKey, this.readPositions()); }
      this.initialViewport(); this.remember();
    }
    resize() {
      if (this.element.clientWidth < 10 || this.element.clientHeight < 10) return;
      this.cy.resize();
      if (this.pendingFit) { this.initialViewport(); this.remember(); }
    }
    focus() { this.element.focus({ preventScroll: true }); }
    destroy() { this.observer.disconnect(); this.element.removeEventListener("keydown", this.keydown); this.cy.destroy(); this.cache.clear(); this.initialLayouts.clear(); }
  }
  window.ProjectKnowledgeCanvas = ProjectKnowledgeCanvas;
})();
