"""Real tests: full proposal lifecycle with token-weighted tally math."""
import os
import tempfile
import pytest
from fastapi.testclient import TestClient

tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DAO_DB"] = tmp.name

import main  # noqa: E402

main.DB_PATH = tmp.name
main.init_db(tmp.name)
client = TestClient(main.app)


def test_full_lifecycle_passes():
    # 3 members: alice 60 tokens, bob 30, cara 10 (supply = 100)
    alice = client.post("/members", json={"name": "alice", "tokens": 60}).json()
    bob = client.post("/members", json={"name": "bob", "tokens": 30}).json()
    cara = client.post("/members", json={"name": "cara", "tokens": 10}).json()

    prop = client.post("/proposals", json={
        "title": "Fund marketing", "description": "Q4 budget",
        "quorum_pct": 25.0, "threshold_pct": 50.0}).json()
    pid = prop["id"]

    # alice yes (60), bob no (30), cara abstains (10)
    r1 = client.post(f"/proposals/{pid}/vote", json={"member_id": alice["id"], "choice": "yes"})
    r2 = client.post(f"/proposals/{pid}/vote", json={"member_id": bob["id"], "choice": "no"})
    r3 = client.post(f"/proposals/{pid}/vote", json={"member_id": cara["id"], "choice": "abstain"})
    assert all(r.status_code == 200 for r in (r1, r2, r3))
    # receipts carry weight
    assert r1.json()["receipt"]["weight"] == 60
    assert r3.json()["receipt"]["weight"] == 10

    # double vote rejected
    dup = client.post(f"/proposals/{pid}/vote", json={"member_id": alice["id"], "choice": "yes"})
    assert dup.status_code == 400

    res = client.post(f"/proposals/{pid}/close").json()
    assert res["yes_weight"] == 60
    assert res["no_weight"] == 30
    assert res["abstain_weight"] == 10
    assert res["participation_pct"] == 100.0
    assert res["quorum_met"] is True
    # approval = 60/(60+30) = 66.67% >= 50% -> PASSED
    assert abs(res["approval_pct"] - 66.666666) < 1e-4
    assert res["result"] == "PASSED"

    # voting after close rejected
    late = client.post(f"/proposals/{pid}/vote", json={"member_id": bob["id"], "choice": "yes"})
    assert late.status_code == 400


def test_quorum_failure():
    prop = client.post("/proposals", json={
        "title": "Low turnout", "quorum_pct": 90.0, "threshold_pct": 50.0}).json()
    pid = prop["id"]
    members = client.get("/members").json()["members"]
    alice = next(m for m in members if m["name"] == "alice")
    client.post(f"/proposals/{pid}/vote", json={"member_id": alice["id"], "choice": "yes"})
    res = client.post(f"/proposals/{pid}/close").json()
    assert res["participation_pct"] == 60.0
    assert res["quorum_met"] is False
    assert res["result"] == "FAILED"


def test_threshold_failure():
    prop = client.post("/proposals", json={
        "title": "Tough vote", "quorum_pct": 10.0, "threshold_pct": 80.0}).json()
    pid = prop["id"]
    members = {m["name"]: m for m in client.get("/members").json()["members"]}
    client.post(f"/proposals/{pid}/vote", json={"member_id": members["alice"]["id"], "choice": "yes"})
    client.post(f"/proposals/{pid}/vote", json={"member_id": members["bob"]["id"], "choice": "no"})
    client.post(f"/proposals/{pid}/vote", json={"member_id": members["cara"]["id"], "choice": "no"})
    res = client.post(f"/proposals/{pid}/close").json()
    # 60/(60+40) = 60% < 80% -> FAILED despite quorum
    assert res["quorum_met"] is True
    assert abs(res["approval_pct"] - 60.0) < 1e-4
    assert res["result"] == "FAILED"


def test_duplicate_member_rejected():
    r = client.post("/members", json={"name": "alice", "tokens": 5})
    assert r.status_code == 400
