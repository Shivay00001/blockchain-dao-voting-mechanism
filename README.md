# DAO Voting Mechanism — off-chain prototype

**What it does:** Member registry with token balances, proposal creation with
configurable quorum and approval-threshold rules, token-weighted yes/no/abstain
ballots (one vote per member per proposal), immutable vote receipts in SQLite,
and close/tally producing a pass/fail result with full weight accounting.

**Honest label:** This is an **off-chain prototype**. No blockchain, no smart
contracts, no on-chain interaction. State lives in `dao.db` (SQLite).

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --port 8000
# DAO_DB=/path/to/db.sqlite uvicorn main:app   # custom DB location
```

## Lifecycle

```bash
curl -X POST localhost:8000/members -H 'Content-Type: application/json' \
  -d '{"name":"alice","tokens":60}'
curl -X POST localhost:8000/proposals -H 'Content-Type: application/json' \
  -d '{"title":"Fund marketing","quorum_pct":25,"threshold_pct":50}'
curl -X POST localhost:8000/proposals/1/vote -H 'Content-Type: application/json' \
  -d '{"member_id":1,"choice":"yes"}'
curl -X POST localhost:8000/proposals/1/close
curl localhost:8000/proposals/1/result
```

## Tests

```bash
pytest -q   # full lifecycle: pass, quorum-fail, threshold-fail cases
```
