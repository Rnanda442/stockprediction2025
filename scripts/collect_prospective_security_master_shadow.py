#!/usr/bin/env python3
"""Append-only prospective US equity identity snapshots. No prices, models, or trading."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

EXPERIMENT_ID = "architecture_v3_prospective_security_master_shadow_v1"
DESIGN_SIGNATURE = "architecture-v3-prospective-security-master-shadow-v1:sec-current-cik+nasdaq-current-symbols:daily-append-only:sqlite+raw-hashes:event-diff:20260919-forward:no-prices:no-model:no-consumed-holdout"
START_DATE = "2026-09-19"
HOLDOUT_END = "2026-08-24"
SOURCES = {
    "sec_company_tickers_exchange": "https://www.sec.gov/files/company_tickers_exchange.json",
    "nasdaq_nasdaqlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
    "nasdaq_otherlisted": "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt",
}

def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()

def fetch(url: str, user_agent: str) -> tuple[bytes, int]:
    req = urllib.request.Request(url, headers={"User-Agent": user_agent, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=90) as response:
        return response.read(), int(response.status)

def parse_sec(payload: bytes) -> list[dict]:
    doc = json.loads(payload.decode("utf-8-sig"))
    fields = doc["fields"]
    return [dict(zip(fields, row)) for row in doc["data"]]

def parse_pipe(payload: bytes, source: str) -> list[dict]:
    text = payload.decode("utf-8-sig", errors="replace")
    lines = [line for line in text.splitlines() if "|" in line and not line.startswith("File Creation Time")]
    rows = list(csv.DictReader(io.StringIO("\n".join(lines)), delimiter="|"))
    clean = []
    for row in rows:
        symbol = (row.get("Symbol") if source == "nasdaq_nasdaqlisted" else row.get("ACT Symbol")) or ""
        if not symbol.strip():
            continue
        clean.append({k:(v or "").strip() for k,v in row.items() if k is not None})
    return clean

def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    PRAGMA foreign_keys=ON;
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS snapshot_runs (
      run_id TEXT PRIMARY KEY, observation_date TEXT NOT NULL UNIQUE, observed_at_utc TEXT NOT NULL,
      status TEXT NOT NULL, previous_run_id TEXT, source_count INTEGER NOT NULL DEFAULT 0,
      sec_rows INTEGER NOT NULL DEFAULT 0, listed_rows INTEGER NOT NULL DEFAULT 0,
      event_candidates INTEGER NOT NULL DEFAULT 0, design_signature TEXT NOT NULL,
      holdout_rows_read INTEGER NOT NULL DEFAULT 0, price_rows_read INTEGER NOT NULL DEFAULT 0,
      models_fit INTEGER NOT NULL DEFAULT 0, error_type TEXT
    );
    CREATE TABLE IF NOT EXISTS source_artifacts (
      run_id TEXT NOT NULL, source TEXT NOT NULL, url TEXT NOT NULL, http_status INTEGER NOT NULL,
      sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, relative_path TEXT NOT NULL,
      PRIMARY KEY (run_id, source), FOREIGN KEY (run_id) REFERENCES snapshot_runs(run_id)
    );
    CREATE TABLE IF NOT EXISTS sec_ticker_snapshot (
      run_id TEXT NOT NULL, cik TEXT NOT NULL, issuer_name TEXT NOT NULL, ticker TEXT NOT NULL,
      exchange TEXT NOT NULL, provisional_entity_id TEXT NOT NULL, identity_scope TEXT NOT NULL,
      PRIMARY KEY (run_id, cik, ticker, exchange), FOREIGN KEY (run_id) REFERENCES snapshot_runs(run_id)
    );
    CREATE TABLE IF NOT EXISTS listed_symbol_snapshot (
      run_id TEXT NOT NULL, source TEXT NOT NULL, symbol TEXT NOT NULL, security_name TEXT NOT NULL,
      exchange TEXT NOT NULL, etf_flag TEXT, test_issue TEXT, financial_status TEXT, raw_json TEXT NOT NULL,
      PRIMARY KEY (run_id, source, symbol), FOREIGN KEY (run_id) REFERENCES snapshot_runs(run_id)
    );
    CREATE TABLE IF NOT EXISTS event_candidates (
      run_id TEXT NOT NULL, event_type TEXT NOT NULL, symbol TEXT, cik TEXT, prior_value TEXT, new_value TEXT,
      reason TEXT NOT NULL, review_status TEXT NOT NULL DEFAULT 'unreviewed',
      FOREIGN KEY (run_id) REFERENCES snapshot_runs(run_id)
    );
    CREATE INDEX IF NOT EXISTS idx_sec_ticker ON sec_ticker_snapshot(ticker, run_id);
    CREATE INDEX IF NOT EXISTS idx_sec_cik ON sec_ticker_snapshot(cik, run_id);
    CREATE INDEX IF NOT EXISTS idx_listed_symbol ON listed_symbol_snapshot(symbol, run_id);
    CREATE INDEX IF NOT EXISTS idx_events_run ON event_candidates(run_id, event_type);
    """)

def listed_tuple(row: dict, source: str) -> tuple[str,str,str,str,str,str,str,str]:
    if source == "nasdaq_nasdaqlisted":
        return (source,row.get("Symbol",""),row.get("Security Name",""),"Q",row.get("ETF",""),row.get("Test Issue",""),row.get("Financial Status",""),json.dumps(row,sort_keys=True))
    return (source,row.get("ACT Symbol",""),row.get("Security Name",""),row.get("Exchange",""),row.get("ETF",""),row.get("Test Issue",""),"",json.dumps(row,sort_keys=True))

def compute_events(con: sqlite3.Connection, run_id: str, previous_run_id: str | None) -> list[tuple]:
    if previous_run_id is None:
        return []
    events = []
    current_listed = {r[0]:(r[1],r[2],r[3]) for r in con.execute("SELECT symbol,source,exchange,security_name FROM listed_symbol_snapshot WHERE run_id=?",(run_id,))}
    prior_listed = {r[0]:(r[1],r[2],r[3]) for r in con.execute("SELECT symbol,source,exchange,security_name FROM listed_symbol_snapshot WHERE run_id=?",(previous_run_id,))}
    for symbol in sorted(current_listed.keys()-prior_listed.keys()):
        events.append((run_id,"listing_candidate",symbol,None,None,json.dumps(current_listed[symbol]),"Present now but absent from previous completed snapshot"))
    for symbol in sorted(prior_listed.keys()-current_listed.keys()):
        events.append((run_id,"removal_candidate",symbol,None,json.dumps(prior_listed[symbol]),None,"Absent now but present in previous completed snapshot; requires lifecycle review"))
    cur = {(r[0],r[1]):r[2] for r in con.execute("SELECT cik,ticker,exchange FROM sec_ticker_snapshot WHERE run_id=?",(run_id,))}
    prv = {(r[0],r[1]):r[2] for r in con.execute("SELECT cik,ticker,exchange FROM sec_ticker_snapshot WHERE run_id=?",(previous_run_id,))}
    for cik,ticker in sorted(cur.keys()-prv.keys()):
        events.append((run_id,"ticker_added_to_cik",ticker,cik,None,cur[(cik,ticker)],"Current SEC issuer-ticker mapping added; effective date not yet certified"))
    for cik,ticker in sorted(prv.keys()-cur.keys()):
        events.append((run_id,"ticker_removed_from_cik",ticker,cik,prv[(cik,ticker)],None,"Current SEC issuer-ticker mapping removed; effective date not yet certified"))
    for key in sorted(cur.keys() & prv.keys()):
        if cur[key] != prv[key]:
            cik,ticker = key
            events.append((run_id,"exchange_change_candidate",ticker,cik,prv[key],cur[key],"Exchange field changed between daily SEC snapshots"))
    cur_ticker = {r[0]:r[1] for r in con.execute("SELECT ticker,cik FROM sec_ticker_snapshot WHERE run_id=?",(run_id,))}
    prv_ticker = {r[0]:r[1] for r in con.execute("SELECT ticker,cik FROM sec_ticker_snapshot WHERE run_id=?",(previous_run_id,))}
    for ticker in sorted(cur_ticker.keys() & prv_ticker.keys()):
        if cur_ticker[ticker] != prv_ticker[ticker]:
            events.append((run_id,"ticker_reuse_candidate",ticker,cur_ticker[ticker],prv_ticker[ticker],cur_ticker[ticker],"Ticker maps to a different SEC CIK than in previous snapshot"))
    return events

def collect(root: Path, observation_date: str | None = None) -> dict:
    root = root.resolve()
    obs_date = observation_date or date.today().isoformat()
    if obs_date < START_DATE or obs_date <= HOLDOUT_END:
        raise RuntimeError("Observation date is outside the prospective-only allowed period")
    user_agent = os.environ.get("SEC_USER_AGENT","").strip()
    if "@" not in user_agent:
        raise RuntimeError("SEC_USER_AGENT with contact email is required in the process environment")
    store = root / "warehouse" / "prospective_security_master"
    raw_dir = store / "raw" / obs_date
    manifest_dir = store / "manifests"
    db_path = store / "prospective_security_master_shadow_v1.sqlite"
    raw_dir.mkdir(parents=True, exist_ok=True); manifest_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    ensure_schema(con)
    existing = con.execute("SELECT run_id,status FROM snapshot_runs WHERE observation_date=?",(obs_date,)).fetchone()
    if existing and existing[1] == "complete":
        counts = con.execute("SELECT sec_rows,listed_rows,event_candidates FROM snapshot_runs WHERE run_id=?",(existing[0],)).fetchone()
        con.close()
        return {"status":"no_op_already_complete","run_id":existing[0],"observation_date":obs_date,"sec_rows":counts[0],"listed_rows":counts[1],"event_candidates":counts[2],"network_requests":0,"holdout_rows_read":0,"price_rows_read":0,"models_fit":0}
    payloads = {}; artifacts = []
    for source,url in SOURCES.items():
        payload,status = fetch(url,user_agent)
        filename = source + (".json" if source.startswith("sec_") else ".txt")
        path = raw_dir / filename; path.write_bytes(payload)
        artifacts.append({"source":source,"url":url,"http_status":status,"sha256":sha256_bytes(payload),"bytes":len(payload),"relative_path":str(path.relative_to(root))})
        payloads[source] = payload
    sec_rows = parse_sec(payloads["sec_company_tickers_exchange"])
    listed_rows = []
    for source in ("nasdaq_nasdaqlisted","nasdaq_otherlisted"):
        listed_rows.extend(listed_tuple(row,source) for row in parse_pipe(payloads[source],source))
    observed_at = datetime.now(timezone.utc).isoformat(); run_id = obs_date
    previous = con.execute("SELECT run_id FROM snapshot_runs WHERE status='complete' AND observation_date<? ORDER BY observation_date DESC LIMIT 1",(obs_date,)).fetchone()
    previous_run_id = previous[0] if previous else None
    with con:
        con.execute("DELETE FROM snapshot_runs WHERE observation_date=?",(obs_date,))
        con.execute("INSERT INTO snapshot_runs(run_id,observation_date,observed_at_utc,status,previous_run_id,design_signature) VALUES(?,?,?,?,?,?)",(run_id,obs_date,observed_at,"collecting",previous_run_id,DESIGN_SIGNATURE))
        con.executemany("INSERT INTO source_artifacts VALUES(?,?,?,?,?,?,?)",[(run_id,a["source"],a["url"],a["http_status"],a["sha256"],a["bytes"],a["relative_path"]) for a in artifacts])
        con.executemany("INSERT INTO sec_ticker_snapshot VALUES(?,?,?,?,?,?,?)",[(run_id,str(r["cik"]).zfill(10),str(r["name"]),str(r["ticker"]),str(r["exchange"]),"SEC-CIK-"+str(r["cik"]).zfill(10),"issuer_level_provisional") for r in sec_rows])
        con.executemany("INSERT INTO listed_symbol_snapshot VALUES(?,?,?,?,?,?,?,?,?)",[(run_id,*row) for row in listed_rows])
        events = compute_events(con,run_id,previous_run_id)
        con.executemany("INSERT INTO event_candidates(run_id,event_type,symbol,cik,prior_value,new_value,reason) VALUES(?,?,?,?,?,?,?)",events)
        con.execute("UPDATE snapshot_runs SET status='complete',source_count=?,sec_rows=?,listed_rows=?,event_candidates=? WHERE run_id=?",(len(artifacts),len(sec_rows),len(listed_rows),len(events),run_id))
    manifest = {"experiment_id":EXPERIMENT_ID,"design_signature":DESIGN_SIGNATURE,"run_id":run_id,"observation_date":obs_date,"observed_at_utc":observed_at,"status":"complete","previous_run_id":previous_run_id,"artifacts":artifacts,"sec_rows":len(sec_rows),"listed_rows":len(listed_rows),"event_candidates":len(events),"holdout_rows_read":0,"price_rows_read":0,"models_fit":0,"trades_or_orders":0,"credentials_persisted":False}
    (manifest_dir / f"{obs_date}.json").write_text(json.dumps(manifest,indent=2)+"\n")
    con.close(); return {**manifest,"network_requests":len(artifacts),"database":str(db_path.relative_to(root))}

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root",default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--observation-date")
    args = parser.parse_args()
    print(json.dumps(collect(Path(args.root),args.observation_date),indent=2))

if __name__ == "__main__":
    main()
