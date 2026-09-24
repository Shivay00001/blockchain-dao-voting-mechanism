"""Real DAO-style voting engine (off-chain prototype).

Implements genuine voting logic, honestly labeled:
  - member registry with token balances (voting weight)
  - proposal creation with quorum + approval-threshold rules
  - token-weighted yes/no/abstain ballots, one vote per member per proposal
  - vote receipts (immutable record per ballot)
  - close + tally: quorum check, threshold check, pass/fail result

HONEST LABEL: this is an OFF-CHAIN PROTOTYPE. There is no blockchain,
no smart contract, and no on-chain interaction. State lives in SQLite.
"""

import os
import sqlite3
import time
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

app = FastAPI(title="DAO Voting Mechanism (off-chain prototype)")

DB_PATH = os.environ.get("DAO_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dao.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    tokens REAL NOT NULL CHECK (tokens >= 0),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    quorum_pct REAL NOT NULL DEFAULT 25.0,
    threshold_pct REAL NOT NULL DEFAULT 50.0,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    created_at REAL NOT NULL,
    closed_at REAL
);
CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id INTEGER NOT NULL REFERENCES proposals(id),
    member_id INTEGER NOT NULL REFERENCES members(id),
    choice TEXT NOT NULL CHECK (choice IN ('yes','no','abstain')),
    weight REAL NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE (proposal_id, member_id)
);
"""


def init_db(path: str = DB_PATH):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.commit()
    con.close()


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


init_db()


class MemberIn(BaseModel):
    name: str = Field(..., min_length=1)
    tokens: float = Field(..., ge=0)


class ProposalIn(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = ""
    quorum_pct: float = Field(25.0, ge=0, le=100)
    threshold_pct: float = Field(50.0, ge=0, le=100)


class VoteIn(BaseModel):
    member_id: int
    choice: Literal["yes", "no", "abstain"]


def tally_proposal(proposal_id: int) -> dict:
    with db() as con:
        prop = con.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not prop:
            raise HTTPException(404, "Proposal not found")
        votes = con.execute(
            "SELECT choice, weight, member_id, created_at FROM votes WHERE proposal_id=?",
            (proposal_id,)).fetchall()
        supply = con.execute("SELECT COALESCE(SUM(tokens),0) FROM members").fetchone()[0] or 0.0
    yes = sum(v["weight"] for v in votes if v["choice"] == "yes")
    no = sum(v["weight"] for v in votes if v["choice"] == "no")
    abstain = sum(v["weight"] for v in votes if v["choice"] == "abstain")
    cast = yes + no + abstain
    participation_pct = (cast / supply * 100.0) if supply > 0 else 0.0
    quorum_met = participation_pct >= prop["quorum_pct"]
    decisive = yes + no
    approval_pct = (yes / decisive * 100.0) if decisive > 0 else 0.0
    passed = bool(quorum_met and decisive > 0 and approval_pct >= prop["threshold_pct"])
    return {
        "proposal_id": proposal_id,
        "title": prop["title"],
        "status": prop["status"],
        "yes_weight": yes, "no_weight": no, "abstain_weight": abstain,
        "total_supply": supply,
        "participation_pct": participation_pct,
        "quorum_pct_required": prop["quorum_pct"],
        "quorum_met": quorum_met,
        "approval_pct": approval_pct,
        "threshold_pct_required": prop["threshold_pct"],
        "result": "PASSED" if passed else "FAILED",
        "ballots": len(votes),
    }


@app.get("/health")
def health():
    return {"status": "ok", "mode": "off-chain prototype — no blockchain interaction"}


@app.post("/members")
def register_member(m: MemberIn):
    with db() as con:
        try:
            cur = con.execute(
                "INSERT INTO members (name, tokens, created_at) VALUES (?,?,?)",
                (m.name, m.tokens, time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(400, f"Member '{m.name}' already registered")
        return {"id": cur.lastrowid, "name": m.name, "tokens": m.tokens}


@app.get("/members")
def list_members():
    with db() as con:
        rows = con.execute("SELECT id, name, tokens FROM members ORDER BY id").fetchall()
    return {"members": [dict(r) for r in rows]}


@app.post("/proposals")
def create_proposal(p: ProposalIn):
    with db() as con:
        cur = con.execute(
            "INSERT INTO proposals (title, description, quorum_pct, threshold_pct, created_at)"
            " VALUES (?,?,?,?,?)",
            (p.title, p.description, p.quorum_pct, p.threshold_pct, time.time()))
    return {"id": cur.lastrowid, "title": p.title, "status": "open",
            "quorum_pct": p.quorum_pct, "threshold_pct": p.threshold_pct}


@app.get("/proposals")
def list_proposals():
    with db() as con:
        rows = con.execute(
            "SELECT id, title, status, quorum_pct, threshold_pct FROM proposals ORDER BY id").fetchall()
    return {"proposals": [dict(r) for r in rows]}


@app.post("/proposals/{proposal_id}/vote")
def cast_vote(proposal_id: int, v: VoteIn):
    with db() as con:
        prop = con.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not prop:
            raise HTTPException(404, "Proposal not found")
        if prop["status"] != "open":
            raise HTTPException(400, "Proposal is closed — voting ended")
        member = con.execute("SELECT * FROM members WHERE id=?", (v.member_id,)).fetchone()
        if not member:
            raise HTTPException(404, "Member not found")
        if member["tokens"] <= 0:
            raise HTTPException(400, "Member has zero voting weight")
        try:
            cur = con.execute(
                "INSERT INTO votes (proposal_id, member_id, choice, weight, created_at)"
                " VALUES (?,?,?,?,?)",
                (proposal_id, v.member_id, v.choice, member["tokens"], time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Member already voted on this proposal")
    receipt = {"receipt_id": cur.lastrowid, "proposal_id": proposal_id,
               "member_id": v.member_id, "member": member["name"],
               "choice": v.choice, "weight": member["tokens"]}
    return {"receipt": receipt}


@app.get("/proposals/{proposal_id}/votes")
def list_votes(proposal_id: int):
    with db() as con:
        rows = con.execute(
            "SELECT v.id AS receipt_id, m.name AS member, v.choice, v.weight, v.created_at"
            " FROM votes v JOIN members m ON m.id=v.member_id"
            " WHERE v.proposal_id=? ORDER BY v.id", (proposal_id,)).fetchall()
    return {"votes": [dict(r) for r in rows]}


@app.post("/proposals/{proposal_id}/close")
def close_proposal(proposal_id: int):
    with db() as con:
        prop = con.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not prop:
            raise HTTPException(404, "Proposal not found")
        if prop["status"] == "closed":
            raise HTTPException(400, "Proposal already closed")
        con.execute("UPDATE proposals SET status='closed', closed_at=? WHERE id=?",
                    (time.time(), proposal_id))
    return tally_proposal(proposal_id)


@app.get("/proposals/{proposal_id}/result")
def get_result(proposal_id: int):
    return tally_proposal(proposal_id)
