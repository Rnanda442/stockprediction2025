"use client";

import { useMemo, useRef, useState } from "react";
import styles from "./causal-architecture-explorer.module.css";
import { ARCHITECTURE_EDGES, ARCHITECTURE_HEIGHT, ARCHITECTURE_NODES, ARCHITECTURE_WIDTH, EDGE_CATEGORY_COPY, type ArchitectureEdge, type ArchitectureNode, type EdgeCategory, type RouteFocus } from "./causal-architecture-data";

const NODE_W = 235;
const NODE_H = 150;
const statusClass = (status: ArchitectureNode["status"]) => styles[status];
const edgeClass = (category: EdgeCategory) => styles[`edge_${category}`];

function pathFor(edge: ArchitectureEdge) {
  const from = ARCHITECTURE_NODES.find((node) => node.id === edge.from)!;
  const to = ARCHITECTURE_NODES.find((node) => node.id === edge.to)!;
  const sx = from.x + NODE_W;
  const sy = from.y + NODE_H / 2;
  const tx = to.x;
  const ty = to.y + NODE_H / 2;
  if (tx > sx) {
    const bend = Math.max(55, (tx - sx) * .48);
    return `M ${sx} ${sy} C ${sx + bend} ${sy}, ${tx - bend} ${ty}, ${tx} ${ty}`;
  }
  const drop = Math.max(sy, ty) + 105;
  return `M ${sx} ${sy} C ${sx + 70} ${sy}, ${sx + 70} ${drop}, ${(sx + tx) / 2} ${drop} S ${tx - 70} ${ty}, ${tx} ${ty}`;
}

export default function CausalArchitectureExplorer() {
  const viewport = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [zoom, setZoom] = useState(.68);
  const [focus, setFocus] = useState<RouteFocus>("main");
  const [selectedNode, setSelectedNode] = useState<ArchitectureNode | null>(ARCHITECTURE_NODES.find((node) => node.id === "core") ?? null);
  const [selectedEdge, setSelectedEdge] = useState<ArchitectureEdge | null>(null);
  const visibleEdges = useMemo(() => ARCHITECTURE_EDGES.filter((edge) => focus === "all" || edge.routes.includes(focus)), [focus]);
  const connected = useMemo(() => new Set(visibleEdges.flatMap((edge) => [edge.from, edge.to])), [visibleEdges]);
  const selectNode = (node: ArchitectureNode) => { setSelectedNode(node); setSelectedEdge(null); };
  const selectEdge = (edge: ArchitectureEdge) => { setSelectedEdge(edge); setSelectedNode(null); };
  const fit = () => { setZoom(.68); if (viewport.current) { viewport.current.scrollLeft = 0; viewport.current.scrollTop = 0; } };

  return <section className={styles.explorer}>
    <header className={styles.heading}>
      <div><p>Diagram-first causal architecture</p><h3>Follow the main route, inspect branches, and expose wrong turns</h3><span>Every box and every arrow is clickable. Solid green lines show the main route; gold branches are experimental; gray routes protect labels and holdout data; blue routes are visualization-only; dashed red routes are blocked.</span></div>
      <strong>{ARCHITECTURE_NODES.length} systems / {ARCHITECTURE_EDGES.length} explained connections</strong>
    </header>

    <nav className={styles.routes} aria-label="Architecture route focus">
      {([ ["main", "Main route"], ["graph", "Graph + 3D"], ["validation", "Validation"], ["wrong", "Wrong turns"], ["all", "Show everything"] ] as [RouteFocus, string][]).map(([id, label]) => <button key={id} className={focus === id ? styles.activeRoute : ""} onClick={() => setFocus(id)}>{label}</button>)}
      <div className={styles.zoom}><span>Zoom {Math.round(zoom * 100)}%</span><button onClick={() => setZoom((value) => Math.max(.48, value - .1))}>-</button><button onClick={fit}>Fit</button><button onClick={() => setZoom((value) => Math.min(1.6, value + .1))}>+</button></div>
    </nav>

    <div className={styles.legend}>{(Object.keys(EDGE_CATEGORY_COPY) as EdgeCategory[]).map((category) => <button key={category} className={edgeClass(category)} onClick={() => setFocus(category === "main" ? "main" : category === "blocked" ? "wrong" : category === "protected" ? "validation" : "graph")}><i />{EDGE_CATEGORY_COPY[category].label}</button>)}</div>

    <div className={styles.workspace}>
      <div
        className={styles.viewport}
        ref={viewport}
        onWheel={(event) => { if (event.ctrlKey) { event.preventDefault(); setZoom((value) => Math.max(.48, Math.min(1.6, value + (event.deltaY < 0 ? .08 : -.08)))); } }}
        onPointerDown={(event) => { const target = event.target as Element; if (target.closest("button,[data-edge]")) return; drag.current = { x: event.clientX, y: event.clientY, left: event.currentTarget.scrollLeft, top: event.currentTarget.scrollTop }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (!drag.current) return; event.currentTarget.scrollLeft = drag.current.left - (event.clientX - drag.current.x); event.currentTarget.scrollTop = drag.current.top - (event.clientY - drag.current.y); }}
        onPointerUp={(event) => { drag.current = null; event.currentTarget.releasePointerCapture(event.pointerId); }}
      >
        <div style={{ width: ARCHITECTURE_WIDTH * zoom, height: ARCHITECTURE_HEIGHT * zoom }}>
          <div className={styles.world} style={{ width: ARCHITECTURE_WIDTH, height: ARCHITECTURE_HEIGHT, transform: `scale(${zoom})` }}>
            <svg width={ARCHITECTURE_WIDTH} height={ARCHITECTURE_HEIGHT} className={styles.edges}>
              <defs>{(Object.keys(EDGE_CATEGORY_COPY) as EdgeCategory[]).map((category) => <marker key={category} id={`arrow-${category}`} markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" className={edgeClass(category)} /></marker>)}</defs>
              {ARCHITECTURE_EDGES.map((edge) => {
                const visible = focus === "all" || edge.routes.includes(focus);
                const path = pathFor(edge);
                return <g key={edge.id} data-edge={edge.id} className={`${styles.edgeGroup} ${edgeClass(edge.category)} ${visible ? "" : styles.dimmed}`} onClick={() => selectEdge(edge)}>
                  <path id={`path-${edge.id}`} d={path} className={styles.edgePath} markerEnd={`url(#arrow-${edge.category})`} />
                  <path d={path} className={styles.edgeHit} />
                  {visible ? <text className={styles.edgeLabel}><textPath href={`#path-${edge.id}`} startOffset="50%" textAnchor="middle">{edge.label}</textPath></text> : null}
                </g>;
              })}
            </svg>
            {ARCHITECTURE_NODES.map((node) => <button key={node.id} style={{ left: node.x, top: node.y, width: NODE_W, minHeight: NODE_H }} className={`${styles.node} ${statusClass(node.status)} ${connected.has(node.id) || focus === "all" ? "" : styles.dimmed} ${selectedNode?.id === node.id ? styles.selected : ""}`} onClick={() => selectNode(node)}>
              <span><b>{node.title}</b><i>{node.status}</i></span><small>{node.subtitle}</small><em>{node.variables.length ? `${node.variables.length} variables` : "system component"}</em>
            </button>)}
          </div>
        </div>
      </div>

      <aside className={`${styles.detail} ${selectedNode ? statusClass(selectedNode.status) : selectedEdge ? edgeClass(selectedEdge.category) : ""}`}>
        {selectedNode ? <>
          <div className={styles.detailType}><i />Box explanation / {selectedNode.status}</div>
          <h4>{selectedNode.title}</h4><p className={styles.subtitle}>{selectedNode.subtitle}</p>
          <section><b>What happens here</b><p>{selectedNode.detail}</p></section>
          <section><b>Why this color</b><p>{selectedNode.verdict}</p></section>
          <section><b>Variables represented</b><div className={styles.chips}>{selectedNode.variables.length ? selectedNode.variables.map((variable) => <code key={variable}>{variable}</code>) : <span>Process, storage, or publishing component</span>}</div></section>
          <section><b>Outgoing arrows</b><p>{ARCHITECTURE_EDGES.filter((edge) => edge.from === selectedNode.id).map((edge) => edge.label).join(" / ") || "No active downstream route."}</p></section>
        </> : selectedEdge ? <>
          <div className={styles.detailType}><i />Arrow explanation / {EDGE_CATEGORY_COPY[selectedEdge.category].label}</div>
          <h4>{selectedEdge.label}</h4>
          <p className={styles.subtitle}>{ARCHITECTURE_NODES.find((node) => node.id === selectedEdge.from)?.title} -&gt; {ARCHITECTURE_NODES.find((node) => node.id === selectedEdge.to)?.title}</p>
          <section><b>What this arrow transfers</b><p>{selectedEdge.explanation}</p></section>
          <section><b>Arrow category</b><p>{EDGE_CATEGORY_COPY[selectedEdge.category].description}</p></section>
          <section><b>Route views</b><div className={styles.chips}>{selectedEdge.routes.map((route) => <code key={route}>{route}</code>)}</div></section>
        </> : null}
      </aside>
    </div>
    <footer>Drag the background to pan. Use Ctrl + mouse wheel or the controls to zoom. The 59-variable formula catalog below provides the minor-variable layer underneath this causal route map.</footer>
  </section>;
}
