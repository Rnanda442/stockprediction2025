# Prospective security-master shadow operations

## Daily operation

Run after the source directories have updated for the day:

    export SEC_USER_AGENT="project name plus SEC contact email"
    python scripts/collect_prospective_security_master_shadow.py --root .

Supply the SEC contact through the process environment or a secure scheduler secret; never commit it. One completed run per UTC date is allowed. Repeating the command on the same date is a no-op with zero requests.

## Storage

- SQLite: warehouse/prospective_security_master/prospective_security_master_shadow_v1.sqlite
- Raw snapshots: warehouse/prospective_security_master/raw/YYYY-MM-DD/
- Manifests: warehouse/prospective_security_master/manifests/YYYY-MM-DD.json

## Review rule

Daily differences are event candidates only. Confirm removals, ticker changes, reuse, mergers, bankruptcies, and terminal values against authoritative event records before research use. The database is forward-only and cannot satisfy the historical 756-date Architecture v3 gate.

## Scheduling limitation

An OpenScienceLab workspace may stop while idle. A local cron entry is therefore not treated as reliable evidence of collection. Use an external reminder or scheduler and verify a completed manifest each day.
