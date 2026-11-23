# Third-party graph renderer

The movable graph workspace uses Cytoscape.js 3.35.0, licensed under the MIT license.

- Official project: https://js.cytoscape.org/
- Source repository: https://github.com/cytoscape/cytoscape.js
- Package metadata: https://registry.npmjs.org/cytoscape/3.35.0
- Local browser bundle: `vendor/cytoscape.min.js`
- Full license and copyright notice: `vendor/cytoscape.LICENSE`

The pinned package tarball was verified against the registry's SHA-512 integrity value before copying these two files. The browser loads the renderer from this package's own static files, not from an external CDN. It uses the browser's ordinary canvas renderer and does not require WebGL, a graph database connection or a paid service.

Graph positions are presentation coordinates. Moving a node does not change research records, evidence, model weights or predictions.
