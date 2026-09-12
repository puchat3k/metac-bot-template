import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.getenv("PAPER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
MAX_MARKETS = int(os.getenv("KALSHI_MAX_MARKETS", "8"))
PAPER_NOTIONAL_USD = float(os.getenv("PAPER_NOTIONAL_USD", "10"))
OUTPUT_DIR = Path(os.getenv("FORECAST_OUTPUT_ROOT", "outputs")) / "kalshi"


def as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def six_hour_bucket(now: datetime) -> datetime:
    hour = (now.hour // 6) * 6
    return now.replace(hour=hour, minute=0, second=0, microsecond=0)


def market_probability(market):
    bid = as_float(market.get("yes_bid_dollars"))
    ask = as_float(market.get("yes_ask_dollars"))
    if bid is not None and ask is not None and 0 < bid < 1 and 0 < ask < 1 and bid <= ask:
        return (bid + ask) / 2, bid, ask

    no_bid = as_float(market.get("no_bid_dollars"))
    if bid is not None and no_bid is not None and 0 < bid < 1 and 0 < no_bid < 1:
        derived_ask = 1 - no_bid
        if bid <= derived_ask:
            return (bid + derived_ask) / 2, bid, derived_ask

    last_price = as_float(market.get("last_price_dollars"))
    if last_price is not None and 0 < last_price < 1:
        return last_price, bid, ask
    return None, bid, ask


def fetch_open_markets():
    response = requests.get(
        f"{KALSHI_BASE}/markets",
        params={"status": "open", "mve_filter": "exclude", "limit": 300},
        headers={"user-agent": "pk2sl-forecast-mvp/0.2"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get("markets", [])


def select_markets(markets):
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=60)
    candidates = []
    for market in markets:
        if market.get("market_type") != "binary" or market.get("is_provisional"):
            continue
        close_raw = market.get("close_time") or market.get("expected_expiration_time")
        try:
            close_at = datetime.fromisoformat(str(close_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        if close_at <= now or close_at > horizon:
            continue

        probability, bid, ask = market_probability(market)
        if probability is None or probability <= 0.02 or probability >= 0.98:
            continue
        if bid is not None and ask is not None and ask - bid > 0.20:
            continue

        volume = as_float(market.get("volume_fp"), 0.0) or 0.0
        liquidity = as_float(market.get("liquidity_dollars"), 0.0) or 0.0
        score = volume + (liquidity * 5)
        candidates.append((score, close_at, market, probability, bid, ask))

    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates[:MAX_MARKETS]


def parse_probability(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty_model_output")
    text = text.strip()

    # First accept a clean JSON response.
    try:
        data = json.loads(text)
        value = float(data["probability"])
        rationale = str(data.get("rationale", ""))
        if 0 <= value <= 1:
            return value, rationale
    except Exception:
        pass

    # Some reasoning models emit prose before the requested JSON. Only accept an
    # explicit probability field, never arbitrary decimals or arithmetic in prose.
    explicit = re.findall(
        r'["\']?probability["\']?\s*[:=]\s*(0(?:\.\d+)?|1(?:\.0+)?)',
        text,
        flags=re.I,
    )
    if explicit:
        value = float(explicit[-1])
        if 0 <= value <= 1:
            return value, text[:4000]

    percent = re.findall(r'probability\s*[:=]\s*(\d{1,3}(?:\.\d+)?)\s*%', text, flags=re.I)
    if percent:
        value = float(percent[-1]) / 100.0
        if 0 <= value <= 1:
            return value, text[:4000]

    raise ValueError("no_explicit_probability_in_model_output")


def forecast_market(market):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required")

    now = datetime.now(timezone.utc)
    prompt = f"""Estimate the probability that this Kalshi market resolves YES.
Do not infer or guess the current market price. You are not shown the market price on purpose.
Use base rates, the exact resolution rules, time remaining, and uncertainty. Avoid false precision.

Current UTC date: {now.date().isoformat()}
Title: {market.get('title')}
Subtitle: {market.get('subtitle')}
YES label: {market.get('yes_sub_title')}
NO label: {market.get('no_sub_title')}
Close time: {market.get('close_time')}
Primary rules: {market.get('rules_primary')}
Secondary rules: {market.get('rules_secondary')}

Return JSON only:
{{"probability": 0.0, "rationale": "brief reason"}}
Probability must be a decimal between 0 and 1."""

    response = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/puchat3k/metac-bot-template",
            "X-Title": "PK2SL Forecast Learning MVP",
        },
        json={
            "model": MODEL,
            "temperature": 0.3,
            "max_tokens": 1200,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a calibrated probabilistic forecaster. Conclude with an explicit probability field between 0 and 1.",
                },
                {"role": "user", "content": prompt},
            ],
        },
        timeout=120,
    )
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    content = message.get("content") or message.get("reasoning")
    probability, rationale = parse_probability(content)
    return probability, rationale, prompt


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    bucket = six_hour_bucket(now)
    markets = fetch_open_markets()
    selected = select_markets(markets)
    records = []
    failures = []

    for _, close_at, market, market_p, bid, ask in selected:
        ticker = market.get("ticker")
        try:
            forecast_p, rationale, prompt = forecast_market(market)
            edge = forecast_p - market_p
            direction = "yes" if edge >= 0 else "no"
            records.append(
                {
                    "venue": "kalshi-paper",
                    "source_opportunity_id": ticker,
                    "title": market.get("title"),
                    "model_key": f"openrouter:{MODEL}:kalshi-mvp-v2",
                    "forecast_probability": round(forecast_p, 6),
                    "market_probability": round(market_p, 6),
                    "edge": round(edge, 6),
                    "direction": direction,
                    "notional_usd": PAPER_NOTIONAL_USD,
                    "locked_at": iso(now),
                    "lock_bucket": iso(bucket),
                    "closes_at": iso(close_at),
                    "status": "open",
                    "metadata": {
                        "paper_only": True,
                        "real_money": False,
                        "training_eligible": True,
                        "prompt_version": "kalshi-mvp-v2",
                        "model": MODEL,
                        "rationale": rationale,
                        "prompt": prompt,
                        "yes_bid": bid,
                        "yes_ask": ask,
                        "volume_fp": market.get("volume_fp"),
                        "liquidity_dollars": market.get("liquidity_dollars"),
                        "event_ticker": market.get("event_ticker"),
                        "rules_primary": market.get("rules_primary"),
                        "rules_secondary": market.get("rules_secondary"),
                        "git_sha": os.getenv("GITHUB_SHA"),
                    },
                }
            )
        except Exception as exc:
            failures.append({"ticker": ticker, "error": str(exc)[:300]})

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    records_path = OUTPUT_DIR / f"records-{stamp}.json"
    records_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUTPUT_DIR / f"run-{stamp}.json").write_text(
        json.dumps(
            {
                "observed_at": iso(now),
                "markets_seen": len(markets),
                "markets_selected": len(selected),
                "forecasts_written": len(records),
                "failures": failures,
                "model": MODEL,
                "paper_only": True,
                "parser_version": "strict-v2",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({"kalshi_paper_records": len(records), "failures": len(failures), "path": str(records_path)}))


if __name__ == "__main__":
    main()
