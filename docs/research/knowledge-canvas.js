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
          { selector: ".canvas-muted", style: { "opacity": 0.5 } },
          { selector: "node.canvas-neighbor", style: { "border-color": "#498e9e", "border-width": 2, "background-color": "#f1f8fb" } },
          { selector: "node.canvas-selected", style: { "border-color": "#147d73", "border-width": 4, "background-color": "#dff2eb", "z-index": 10 } },
          { selector: "edge.canvas-linked", style: { "width": 2.6, "label": "data(label)", "z-index": 8 } },
          { selector: "edge.canvas-incoming", style: { "line-color": "#6b87bc", "target-arrow-color": "#6b87bc" } },
          { selector: "edge.canvas-outgoing", style: { "line-color": "#238d81", "target-arrow-color": "#238d81" } },
          { selector: "edge.canvas-selected", style: { "width": 4, "line-color": "#147d73", "target-arrow-color": "#147d73", "label": "data(label)", "z-index": 12 } },
        ],
      });
      this.cy.on("grab", "node", () => { this.dragged = false; });
      this.cy.on("drag", "node", () => { this.dragged = true; });
      this.cy.on("free", "node", () => {
        if (this.dragged) this.suppressTapUntil = Date.now() + 320;
        this.remember();
      });
      this.cy.on("tap", "node", (event) => {
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
      this.cache.set(this.currentKey, { positions, zoom: this.cy.zoom(), pan: { ...this.cy.pan() } });
      if (this.cache.size > 45) this.cache.delete(this.cache.keys().next().value);
    }

    setGraph(graph) {
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
