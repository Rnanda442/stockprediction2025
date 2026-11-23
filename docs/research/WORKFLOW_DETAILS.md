# Detailed workflow diagrams

The main workflow has twelve stable stage IDs and fifteen reviewed dependencies. Opening a stage reveals a separately reviewed subgraph. Categories contain more specific parts; other edges describe the stated dependency, input or output. A containment edge is not an execution sequence or a claim of predictive influence.

Each part has a stable ID, a status, a summary, explicit inputs and outputs, detailed notes, evidence IDs and optional canonical-record, variable-definition and study links. Formulas retain their original scope. Code component names refer to documented software symbols; naming a component does not establish an integrated daily service.

## Reading the status

- Existing: a component or record exists; current daily operation is not implied.
- Historical reviewed: a saved result or legacy definition, with its original limits.
- Implemented mechanics: software/accounting rules exist; predictive skill is a different claim.
- Partially connected: separate pieces exist but the reviewed connection is absent.
- Design needed: the choice or interface remains undecided.
- Blocked: a prerequisite has not passed.
- Unverified: the operational fact has not been established.

Definitions include model inputs, targets, outcomes, evaluation measures and boundaries. All legacy catalog entries remain inspectable without being adopted as predictive features. All registered study/design records link to their original scoped evidence cards. No model is promoted.

## Maintenance and provenance

The detail catalog is a dated documentary supplement to the base project graph, method catalog, workflow and evidence index. Its manifest records their content hashes. The base snapshot and the detail supplement have separate identities; neither is a live Neo4j query or a native Notion refresh. Existing private knowledge releases and their acknowledgements remain unchanged.

Maintainers assemble the catalog using scripts/build_workflow_detail_catalog.py from reviewed documentary content shards. The script validates exact twelve-stage coverage, stable IDs, explicit parent/child edges, relationship endpoints, evidence IDs, study links, legacy-variable coverage and a strict public-field/privacy boundary. Rebuilding requires the private reviewed inputs; those inputs are not all part of the public package.

The builder reads only the named documentary files. It does not open price databases, notebooks, credential stores, actual portfolio payloads or consumed-holdout observations, and it does not fit models or run backtests. Documentation and browser checks are distinct from scientific validation.
