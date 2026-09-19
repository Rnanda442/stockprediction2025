# Architecture v3 historical-lineage source qualification

Date: 2026-09-09  
Status: preregistered — waiting for existing data access or an explicit source choice

## Decision

Architecture v3 is not ready for model fitting. The existing price database has enough dates but is survivor-selected, and the current Massive Basic entitlement cannot supply the early and middle history needed to reconstruct the universe.

The preferred source is CRSP US Daily Stock data through an existing institutional or WRDS license. It directly supplies stable identifiers and delisting information. If CRSP access is unavailable, the operational fallback is a Massive plan with five-year history plus an independently qualified delisted-security and symbol-history source such as EODHD. Ambiguous lifecycle events must be corroborated against SEC, FINRA, or Nasdaq records.

No purchase is authorized. The next execution is a bounded qualification, not a bulk download.

## Qualification boundary

Test 2021-08-23, 2024-01-02, and 2026-05-28 together with sampled continuously listed securities, new listings, symbol changes, mergers, bankruptcies or delistings, ticker reuse, and case-normalization collisions.

The source must demonstrate stable identifiers, effective-dated aliases, adjustment consistency, lifecycle-event agreement, and explicit terminal-return treatment. Any failed gate stops the reconstruction before model fitting.

The consumed 2026-05-29 through 2026-08-24 holdout is prohibited for source selection, model development, or threshold tuning.

## Architecture implication

The separately preregistered identity-free return-and-downside policy remains only a design. Its compact ridge/logistic control, optional compact ANN challenger, deterministic state engine, and training-only scenario constraint may be implemented only after the source qualification and full lineage reconstruction pass.
