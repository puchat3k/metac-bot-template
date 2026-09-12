import glob
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

AUDIENCE = "pk2sl-forecast-ingest"
DEFAULT_ENDPOINT = "https://cfoleiwmizgayhecyymq.supabase.co/functions/v1/forecast-ingest"
OUTPUT_ROOT = Path(os.getenv("FORECAST_OUTPUT_ROOT", "outputs"))


def github_oidc_token():
    request_url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not request_url or not request_token:
        raise RuntimeError("GitHub OIDC environment is unavailable")
    response = requests.get(
        request_url,
        params={"audience": AUDIENCE},
        headers={"Authorization": f"bearer {request_token}"},
        timeout=30,
    )
    response.raise_for_status()
    value = response.json().get("value")
    if not value:
        raise RuntimeError("GitHub OIDC response did not contain a token")
    return value


def call_edge(token, payload):
    endpoint = os.getenv("FORECAST_INGEST_URL", DEFAULT_ENDPOINT)
    response = requests.post(
        endpoint,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if not response.ok:
        raise RuntimeError(f"forecast-ingest failed: {response.status_code} {response.text[:500]}")
    return response.json()


def load_records():
    records = []
    paths = sorted(glob.glob(str(OUTPUT_ROOT / "**" / "records-*.json"), recursive=True))
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise RuntimeError(f"record file is not a list: {path}")
        records.extend(data)
    return records, paths


def main():
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    records, paths = load_records()
    token = github_oidc_token()

    ingest_result = {"ok": True, "result": {"upserted": 0}}
    if records:
        ingest_result = call_edge(token, {"op": "ingest", "records": records})

    resolve_result = call_edge(token, {"op": "resolve_kalshi"})
    summary_result = call_edge(token, {"op": "summary"})

    receipt = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "record_files": paths,
        "records_sent": len(records),
        "ingest": ingest_result,
        "resolve_kalshi": resolve_result,
        "summary": summary_result,
        "git_sha": os.getenv("GITHUB_SHA"),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
    }
    receipt_path = OUTPUT_ROOT / "ledger-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    print(json.dumps(receipt, default=str))


if __name__ == "__main__":
    main()
