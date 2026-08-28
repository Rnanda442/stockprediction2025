"use client";

import { useState } from "react";
import styles from "./variable-lineage-explorer.module.css";
import { VARIABLE_CATALOG, VARIABLE_STAGES, type EvidenceStatus } from "./pipeline-variable-catalog";

const statusLabel: Record<EvidenceStatus, string> = {
  supported: "Green: active or supported",
  mixed: "Gold: mixed or exploratory",
  harmful: "Red: harmful or disconnected",
  protected: "Protected label or holdout",
};

const statusClass = (status: EvidenceStatus) => styles[status];

export default function VariableLineageExplorer() {
  const [selectedId, setSelectedId] = useState("ret_20d");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<"all" | EvidenceStatus>("all");
  const [zoom, setZoom] = useState(0.82);
  const selected = VARIABLE_CATALOG.find((node) => node.id === selectedId) ?? VARIABLE_CATALOG[0];
  const visible = VARIABLE_CATALOG.filter((node) => filter === "all" || node.status === filter);
  const search = query.trim().toLowerCase();

  return <section className={styles.explorer} aria-label="Variable lineage explorer">
    <header className={styles.heading}>
      <div><p>Clickable evidence map</p><h3>Every variable and modification</h3><span>Green means active or provisionally useful, not approved for live trading. Click any card for the evidence and downstream connection.</span></div>
      <strong>{VARIABLE_CATALOG.length} tracked variables</strong>
    </header>

    <div className={styles.toolbar}>
      <label>Find a variable<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="momentum, similarity, loss..." /></label>
      <label>Evidence<select value={filter} onChange={(event) => setFilter(event.target.value as "all" | EvidenceStatus)}><option value="all">All</option><option value="supported">Green</option><option value="mixed">Mixed</option><option value="harmful">Red</option><option value="protected">Protected</option></select></label>
      <div className={styles.zoom}><span>{Math.round(zoom * 100)}%</span><button onClick={() => setZoom((value) => Math.max(.55, value - .1))}>-</button><button onClick={() => setZoom(1)}>Reset</button><button onClick={() => setZoom((value) => Math.min(1.5, value + .1))}>+</button></div>
    </div>

    <div className={styles.legend}>{(Object.keys(statusLabel) as EvidenceStatus[]).map((status) => <button key={status} className={statusClass(status)} onClick={() => setFilter(status)}><i />{statusLabel[status]}</button>)}</div>

    <div className={styles.workspace}>
      <div className={styles.viewport}>
        <div className={styles.canvas} style={{ transform: `scale(${zoom})`, width: `${100 / zoom}%` }}>
          {VARIABLE_STAGES.map((stage, index) => <section className={styles.stage} key={stage.id}>
            <header><b>{String(index + 1).padStart(2, "0")}</b><h4>{stage.label}</h4><p>{stage.note}</p></header>
            <div>{visible.filter((node) => node.stage === stage.id).map((node) => {
              const matches = !search || `${node.id} ${node.label} ${node.formula} ${node.why}`.toLowerCase().includes(search);
              return <button key={node.id} className={`${styles.node} ${statusClass(node.status)} ${selected.id === node.id ? styles.selected : ""} ${matches ? "" : styles.dimmed}`} onClick={() => setSelectedId(node.id)}>
                <span><strong>{node.label}</strong><i>{node.status === "supported" ? "Green" : node.status === "harmful" ? "Red" : node.status}</i></span>
                <code>{node.id}</code><small>{node.formula}</small><em>Feeds -&gt; {node.feeds.slice(0, 2).join(" / ")}</em>
              </button>;
            })}</div>
          </section>)}
        </div>
      </div>

      <aside className={`${styles.detail} ${statusClass(selected.status)}`}>
        <div className={styles.verdict}><i />{statusLabel[selected.status]}</div>
        <h4>{selected.label}</h4><code>{selected.id}</code>
        <section><b>Modification</b><p>{selected.formula}</p></section>
        <section><b>Why it is {selected.status}</b><p>{selected.why}</p></section>
        <section><b>Feeds into</b><p>{selected.feeds.join(" -> ")}</p></section>
        <section className={styles.guardrail}><b>Interpretation</b><p>{selected.status === "supported" ? "Supported for the current research flow, not promoted for trading." : selected.status === "harmful" ? "Red means reject, disconnect, or redesign this tested form; it does not always mean delete the raw data." : selected.status === "protected" ? "Unavailable at prediction time or sealed for final confirmation." : "Preserve as a narrow hypothesis until future unseen evidence resolves it."}</p></section>
      </aside>
    </div>
    <footer>Coordinates, bubble sizes, colors, and visual separation remain hypotheses until they improve an out-of-sample metric. The final holdout remains sealed.</footer>
  </section>;
}
