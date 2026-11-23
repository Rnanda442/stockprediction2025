# Movable project graph

The graph workspace is a presentation of the reviewed workflow and its detailed parts. It uses the same five public JSON documents as the reading view. It does not add research assertions, recalculate results or connect to a database. Its default Connected branches mode expands specific parts around persistent main boxes in the same network; see RADIAL_EXPANSION.md for field-level detail and connection provenance.

## Explore

- Drag a node to give it room. Its edges follow it.
- Drag an empty part of the canvas to pan. Zoom with the wheel, a trackpad or pinch; zoom buttons are also provided.
- Select a node to highlight its direct connections and inspect its status, inputs, outputs and evidence.
- In Connected branches, click a main box to unfold its detailed branch in place. Categories fold/unfold recorded descendants, and detailed parts reveal their exact fields. Multiple branches can stay open. Dragging does not toggle expansion.
- Fit graph frames the current graph. Reset layout restores the current scope's initial placement. These are different actions.
- Expand the canvas for more working space. Use the reading view and the part catalog for equivalent text navigation and keyboard access.

Secondary scopes include the twelve-step workflow, a selected category, all parts in the current step, and all 303 detailed parts. The full-detail view contains 453 internal relationships plus one explicit cross-step relationship. Connected branches retains the twelve main boxes as anchors and adds visible parts, declared references and field annotations with separate counts. Main boxes and annotations are not extra detail-catalog parts. Search and the existing group selector provide smaller views of the same content.

## Presentation state

Node placement, pan and zoom are cached separately for each scope during the current page session. Ordinary selection does not continuously rearrange the graph. Reset applies only to the current scope; refreshing the page starts a new session. These positions are not saved to GitHub, Neo4j or Notion.

Initial spacing groups related parts for orientation. Containment edges describe group membership, while other directed edges retain their documented relationship types. Distance, centrality, node size and placement are not claims about causal influence, variable importance, predictive performance or trading suitability.

The earlier static reading view, study library, formula definitions and evidence inspector remain available. A missing graph renderer falls back to the reading interface. Renderer attribution and the local license are recorded in THIRD_PARTY_NOTICES.md.
