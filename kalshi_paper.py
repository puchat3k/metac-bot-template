import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

KALSHI_BASE = "https://external-api.kalshi.com/trade-api/v2"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.getenv("PAPER_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")

# Paper-learning coverage: 0 means all eligible markets returned by the API page.
MAX_MARKETS = int(os.getenv("KALSHI_MAX_MARKETS", "0"))
MODEL_FORECASTS = int(os.getenv("KALSHI_MODEL_FORECASTS", "24"))
FORECAST_WORKERS = max(1, int(os.getenv("KALSHI_FORECAST_WORKERS", "4")))

PAPER_BANKROLL_USD = float(os.getenv("PAPER_BANKROLL_USD", "1000"))
PAPER_GROSS_NOTIONAL_USD = float(os.getenv("PAPER_GROSS_NOTIONAL_USD", "500"))
PAPER_MIN_POSITION_USD = float(os.getenv("PAPER_MIN_POSITION_USD", "1"))
PAPER_MAX_POSITION_USD = float(os.getenv("PAPER_MAX_POSITION_USD", "25"))
PAPER_KELLY_FRACTION = float(os.getenv("PAPER_KELLY_FRACTION", "0.5"))
MODEL_EDGE_SHRINK = float(os.getenv("MODEL_EDGE_SHRINK", "0.35"))
MAX_EVENT_GROSS_FRACTION = float(os.getenv("MAX_EVENT_GROSS_FRACTION", "0.20"))
MAX_EVENT_NET_FRACTION = float(os.getenv("MAX_EVENT_NET_FRACTION", "0.35"))
CONTRARIAN_EDGE_THRESHOLD = float(os.getenv("CONTRARIAN_EDGE_THRESHOLD", "0.20"))
CONTRARIAN_HEDGE_FRACTION = float(os.getenv("CONTRARIAN_HEDGE_FRACTION", "0.50"))
CONTRARIAN_MAX_POSITION_USD = float(os.getenv("CONTRARIAN_MAX_POSITION_USD", "10"))

OUTPUT_DIR = Path(os.getenv("FORECAST_OUTPUT_ROOT", "outputs")) / "kalshi"


def as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


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
    """Walk the public cursor until the entire current open-market set is loaded."""
    markets = []
    cursor = None
    seen_cursors = set()
    while True:
        params = {"status": "open", "mve_filter": "exclude", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        response = requests.get(
            f"{KALSHI_BASE}/markets",
            params=params,
            headers={"user-agent": "pk2sl-forecast-mvp/0.5"},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        markets.extend(body.get("markets", []))
        next_cursor = body.get("cursor")
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise RuntimeError("kalshi pagination cursor repeated")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return markets


def _learning_score(market, close_at, bid, ask):
    now = datetime.now(timezone.utc)
    hours = max(0.25, (close_at - now).total_seconds() / 3600)
    volume = as_float(market.get("volume_fp"), 0.0) or 0.0
    liquidity = as_float(market.get("liquidity_dollars"), 0.0) or 0.0
    spread = (ask - bid) if bid is not None and ask is not None else 0.20
    spread_quality = clamp(1 - spread / 0.20, 0, 1)
    # Empirical overlay from the first resolved paper cohort: the unresearched
    # generic model performed very poorly inside two hours of close. Prefer
    # 12h-14d markets for expensive model inference while retaining minimum
    # paper-control exposure on the rest.
    if hours <= 2:
        horizon_score = -6.0
    elif hours <= 12:
        horizon_score = -1.0
    elif hours <= 48:
        horizon_score = 4.0
    elif hours <= 24 * 14:
        horizon_score = 3.0
    else:
        horizon_score = 1.0

    return (
        math.log1p(max(0.0, volume))
        + 2.0 * math.log1p(max(0.0, liquidity))
        + horizon_score
        + 2.0 * spread_quality
    )


def coverage_markets(markets):
    """All currently tradable binary markets with a usable paper entry price."""
    now = datetime.now(timezone.utc)
    covered = []
    for market in markets:
        if market.get("market_type") != "binary" or market.get("is_provisional"):
            continue
        close_raw = market.get("close_time") or market.get("expected_expiration_time")
        try:
            close_at = datetime.fromisoformat(str(close_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        if close_at <= now:
            continue

        probability, bid, ask = market_probability(market)
        if probability is None or probability <= 0 or probability >= 1:
            continue
        covered.append((close_at, market, probability, bid, ask))

    covered.sort(key=lambda row: row[0])
    if MAX_MARKETS > 0:
        return covered[:MAX_MARKETS]
    return covered


def select_markets(covered):
    """Quality-ranked subset eligible for scarce model inference."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=60)
    candidates = []
    for close_at, market, probability, bid, ask in covered:
        if close_at > horizon:
            continue
        if probability <= 0.02 or probability >= 0.98:
            continue
        if bid is not None and ask is not None and ask - bid > 0.20:
            continue

        score = _learning_score(market, close_at, bid, ask)
        candidates.append((score, close_at, market, probability, bid, ask))

    candidates.sort(key=lambda row: (-row[0], row[1]))
    return candidates


def parse_probability(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty_model_output")
    text = text.strip()

    try:
        data = json.loads(text)
        value = float(data["probability"])
        rationale = str(data.get("rationale", ""))
        if 0 <= value <= 1:
            return value, rationale
    except Exception:
        pass

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
Use base rates, the exact resolution rules, time remaining, and uncertainty.
Be conservative when the prompt does not contain enough current-world information.
Do not create large probability moves from weak context. Avoid false precision.

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

    last_error = None
    for attempt in range(3):
        try:
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
                    "temperature": 0.2,
                    "max_tokens": 1200,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a calibrated probabilistic forecaster. "
                                "Prefer conservative probabilities when evidence is weak. "
                                "Conclude with an explicit probability field between 0 and 1."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=120,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise RuntimeError(f"openrouter_retryable_{response.status_code}")
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            content = message.get("content") or message.get("reasoning")
            probability, rationale = parse_probability(content)
            return probability, rationale, prompt
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(str(last_error or "forecast_failed"))


def effective_edge_shrink(raw_edge, horizon_hours=None):
    """Empirical reliability overlay; provisional until a larger resolved cohort exists."""
    shrink = MODEL_EDGE_SHRINK
    edge = abs(raw_edge)
    if edge >= 0.30:
        shrink = min(shrink, 0.05)
    elif edge >= 0.20:
        shrink = min(shrink, 0.10)
    elif edge >= 0.10:
        shrink = min(shrink, 0.25)

    if horizon_hours is not None:
        if horizon_hours <= 2:
            shrink = min(shrink, 0.05)
        elif horizon_hours <= 12:
            shrink = min(shrink, 0.15)
    return shrink


def strategy_probability(model_probability, market_probability_, horizon_hours=None):
    """Shrink an unproven model toward the market prior using observed reliability."""
    raw_edge = model_probability - market_probability_
    shrink = effective_edge_shrink(raw_edge, horizon_hours)
    return clamp(
        market_probability_ + shrink * raw_edge,
        0.001,
        0.999,
    )


def empirical_risk_multiplier(raw_edge, horizon_hours):
    """Risk overlay learned from the first 80 resolved paper positions."""
    edge = abs(raw_edge)
    if edge >= 0.30:
        edge_factor = 0.10
    elif edge >= 0.20:
        edge_factor = 0.20
    elif edge >= 0.10:
        edge_factor = 0.55
    else:
        edge_factor = 1.00

    if horizon_hours <= 2:
        horizon_factor = 0.10
    elif horizon_hours <= 12:
        horizon_factor = 0.50
    else:
        horizon_factor = 1.00

    return edge_factor * horizon_factor


def full_kelly_fraction(side_price, win_probability):
    if side_price <= 0 or side_price >= 1:
        return 0.0
    return clamp((win_probability - side_price) / (1 - side_price), 0.0, 1.0)


def liquidity_quality(market, bid, ask):
    liquidity = max(0.0, as_float(market.get("liquidity_dollars"), 0.0) or 0.0)
    volume = max(0.0, as_float(market.get("volume_fp"), 0.0) or 0.0)
    spread = (ask - bid) if bid is not None and ask is not None else 0.20
    spread_quality = clamp(1 - spread / 0.20, 0, 1)
    liq_component = clamp(math.log1p(liquidity) / math.log1p(100000), 0, 1)
    volume_component = clamp(math.log1p(volume) / math.log1p(100000), 0, 1)
    return 0.5 * spread_quality + 0.3 * liq_component + 0.2 * volume_component


def build_primary_position(now, bucket, close_at, market, market_p, bid, ask, model_p, rationale, prompt):
    raw_edge = model_p - market_p
    horizon_hours = max(0.0, (close_at - now).total_seconds() / 3600)
    shrink = effective_edge_shrink(raw_edge, horizon_hours)
    adjusted_p = strategy_probability(model_p, market_p, horizon_hours)
    edge = adjusted_p - market_p
    direction = "yes" if edge >= 0 else "no"
    side_price = market_p if direction == "yes" else 1 - market_p
    win_probability = adjusted_p if direction == "yes" else 1 - adjusted_p
    kelly_full = full_kelly_fraction(side_price, win_probability)
    quality = liquidity_quality(market, bid, ask)
    empirical_multiplier = empirical_risk_multiplier(raw_edge, horizon_hours)
    proposed = (
        PAPER_BANKROLL_USD
        * PAPER_KELLY_FRACTION
        * kelly_full
        * max(0.20, quality)
        * empirical_multiplier
    )
    proposed = clamp(proposed, PAPER_MIN_POSITION_USD, PAPER_MAX_POSITION_USD)

    ticker = market.get("ticker")
    return {
        "venue": "kalshi-paper",
        "source_opportunity_id": ticker,
        "title": market.get("title"),
        "model_key": f"openrouter:{MODEL}:kalshi-portfolio-v4",
        "forecast_probability": round(adjusted_p, 6),
        "market_probability": round(market_p, 6),
        "edge": round(edge, 6),
        "direction": direction,
        "notional_usd": round(proposed, 2),
        "locked_at": iso(now),
        "lock_bucket": iso(bucket),
        "closes_at": iso(close_at),
        "status": "open",
        "metadata": {
            "paper_only": True,
            "real_money": False,
            "training_eligible": True,
            "strategy_version": "kalshi-portfolio-v4",
            "position_sizing": "market-prior-shrinkage+fractional-kelly+event-caps",
            "prompt_version": "kalshi-portfolio-v4",
            "model": MODEL,
            "raw_model_probability": round(model_p, 6),
            "raw_model_edge": round(raw_edge, 6),
            "base_model_edge_shrink": MODEL_EDGE_SHRINK,
            "effective_edge_shrink": round(shrink, 6),
            "empirical_risk_multiplier": round(empirical_multiplier, 6),
            "horizon_hours": round(horizon_hours, 3),
            "kelly_full": round(kelly_full, 6),
            "kelly_fraction": PAPER_KELLY_FRACTION,
            "liquidity_quality": round(quality, 6),
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


def build_contrarian_position(primary):
    """Paper-only shadow hedge for extreme model/market disagreement."""
    market_p = float(primary["market_probability"])
    primary_p = float(primary["forecast_probability"])
    adjusted_edge = primary_p - market_p
    raw_edge = float(primary["metadata"].get("raw_model_edge", adjusted_edge))
    contrarian_p = clamp(market_p - adjusted_edge, 0.001, 0.999)
    direction = "no" if primary["direction"] == "yes" else "yes"
    proposed = clamp(
        float(primary["notional_usd"]) * CONTRARIAN_HEDGE_FRACTION,
        PAPER_MIN_POSITION_USD,
        CONTRARIAN_MAX_POSITION_USD,
    )
    return {
        "venue": "kalshi-paper",
        "source_opportunity_id": primary["source_opportunity_id"],
        "title": primary["title"],
        "model_key": f"openrouter:{MODEL}:kalshi-contrarian-shadow-v1",
        "forecast_probability": round(contrarian_p, 6),
        "market_probability": round(market_p, 6),
        "edge": round(-adjusted_edge, 6),
        "direction": direction,
        "notional_usd": round(proposed, 2),
        "locked_at": primary["locked_at"],
        "lock_bucket": primary["lock_bucket"],
        "closes_at": primary["closes_at"],
        "status": "open",
        "metadata": {
            "paper_only": True,
            "real_money": False,
            "training_eligible": False,
            "shadow_challenger": True,
            "strategy_version": "kalshi-contrarian-shadow-v1",
            "hedges_strategy": primary["model_key"],
            "raw_model_edge": round(raw_edge, 6),
            "contrarian_edge_threshold": CONTRARIAN_EDGE_THRESHOLD,
            "contrarian_hedge_fraction": CONTRARIAN_HEDGE_FRACTION,
            "event_ticker": primary["metadata"].get("event_ticker"),
            "git_sha": os.getenv("GITHUB_SHA"),
        },
    }


def build_control_position(now, bucket, close_at, market, market_p, bid, ask, reason):
    # Coverage/control only. It is explicitly excluded from model training because it
    # contains no independent forecast edge.
    direction = "yes" if market_p >= 0.5 else "no"
    ticker = market.get("ticker")
    return {
        "venue": "kalshi-paper",
        "source_opportunity_id": ticker,
        "title": market.get("title"),
        "model_key": "kalshi:market-control:v1",
        "forecast_probability": round(market_p, 6),
        "market_probability": round(market_p, 6),
        "edge": 0.0,
        "direction": direction,
        "notional_usd": round(PAPER_MIN_POSITION_USD, 2),
        "locked_at": iso(now),
        "lock_bucket": iso(bucket),
        "closes_at": iso(close_at),
        "status": "open",
        "metadata": {
            "paper_only": True,
            "real_money": False,
            "training_eligible": False,
            "control_only": True,
            "strategy_version": "market-control-v1",
            "control_reason": reason,
            "yes_bid": bid,
            "yes_ask": ask,
            "volume_fp": market.get("volume_fp"),
            "liquidity_dollars": market.get("liquidity_dollars"),
            "event_ticker": market.get("event_ticker"),
            "git_sha": os.getenv("GITHUB_SHA"),
        },
    }


def _trim_group_gross(records, cap):
    gross = sum(float(r["notional_usd"]) for r in records)
    if gross <= cap:
        return
    for record in sorted(records, key=lambda r: abs(float(r.get("edge") or 0.0))):
        if gross <= cap:
            break
        current = float(record["notional_usd"])
        removable = max(0.0, current - PAPER_MIN_POSITION_USD)
        if removable <= 0:
            continue
        reduction = min(removable, gross - cap)
        record["notional_usd"] = round(current - reduction, 2)
        record["metadata"]["event_gross_trim_usd"] = round(
            float(record["metadata"].get("event_gross_trim_usd", 0.0)) + reduction, 2
        )
        gross -= reduction


def _trim_group_net(records):
    gross = sum(float(r["notional_usd"]) for r in records)
    if gross <= 0:
        return
    net = sum(
        float(r["notional_usd"]) if r["direction"] == "yes" else -float(r["notional_usd"])
        for r in records
    )
    max_net = gross * MAX_EVENT_NET_FRACTION
    if abs(net) <= max_net:
        return

    dominant = "yes" if net > 0 else "no"
    for record in sorted(
        [r for r in records if r["direction"] == dominant],
        key=lambda r: abs(float(r.get("edge") or 0.0)),
    ):
        if abs(net) <= max_net:
            break
        current = float(record["notional_usd"])
        removable = max(0.0, current - PAPER_MIN_POSITION_USD)
        if removable <= 0:
            continue
        reduction = min(removable, abs(net) - max_net)
        record["notional_usd"] = round(current - reduction, 2)
        record["metadata"]["event_net_trim_usd"] = round(
            float(record["metadata"].get("event_net_trim_usd", 0.0)) + reduction, 2
        )
        net += -reduction if dominant == "yes" else reduction


def allocate_portfolio(records):
    if not records:
        return records

    minimum_gross = PAPER_MIN_POSITION_USD * len(records)
    target_gross = max(minimum_gross, PAPER_GROSS_NOTIONAL_USD)
    current_gross = sum(float(r["notional_usd"]) for r in records)

    if current_gross > target_gross:
        total_extra = sum(
            max(0.0, float(r["notional_usd"]) - PAPER_MIN_POSITION_USD)
            for r in records
        )
        allowed_extra = max(0.0, target_gross - minimum_gross)
        scale = 0.0 if total_extra <= 0 else min(1.0, allowed_extra / total_extra)
        for record in records:
            current = float(record["notional_usd"])
            extra = max(0.0, current - PAPER_MIN_POSITION_USD)
            record["notional_usd"] = round(PAPER_MIN_POSITION_USD + extra * scale, 2)
            record["metadata"]["gross_book_scale"] = round(scale, 6)

    groups = {}
    for record in records:
        group = str(record["metadata"].get("event_ticker") or record["source_opportunity_id"])
        groups.setdefault(group, []).append(record)

    event_gross_cap = max(
        PAPER_MIN_POSITION_USD,
        target_gross * MAX_EVENT_GROSS_FRACTION,
    )
    for group_records in groups.values():
        _trim_group_gross(group_records, event_gross_cap)
        _trim_group_net(group_records)

    return records


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    bucket = six_hour_bucket(now)
    markets = fetch_open_markets()
    covered = coverage_markets(markets)
    selected = select_markets(covered)

    records = []
    failures = []
    primary_results = {}

    forecast_targets = selected[: max(0, MODEL_FORECASTS)]
    forecast_target_tickers = {row[2].get("ticker") for row in forecast_targets}
    with ThreadPoolExecutor(max_workers=min(FORECAST_WORKERS, max(1, len(forecast_targets)))) as pool:
        futures = {
            pool.submit(forecast_market, row[2]): row
            for row in forecast_targets
        }
        for future in as_completed(futures):
            _, close_at, market, market_p, bid, ask = futures[future]
            ticker = market.get("ticker")
            try:
                model_p, rationale, prompt = future.result()
                primary_results[ticker] = build_primary_position(
                    now, bucket, close_at, market, market_p, bid, ask,
                    model_p, rationale, prompt,
                )
            except Exception as exc:
                failures.append({"ticker": ticker, "error": str(exc)[:300]})

    control_records = []
    risk_records = []
    contrarian_records = []

    for close_at, market, market_p, bid, ask in covered:
        ticker = market.get("ticker")
        primary = primary_results.get(ticker)

        # Always keep a same-market baseline so the paper book covers the full
        # open binary universe, even when a market is not worth an LLM call.
        reason = (
            "same_market_baseline"
            if primary is not None
            else (
                "model_forecast_failed"
                if ticker in forecast_target_tickers
                else "outside_model_forecast_budget"
            )
        )
        control_records.append(
            build_control_position(
                now, bucket, close_at, market, market_p, bid, ask, reason
            )
        )

        if primary is None:
            continue

        risk_records.append(primary)
        raw_edge = abs(float(primary["metadata"].get("raw_model_edge", 0.0)))
        if raw_edge >= CONTRARIAN_EDGE_THRESHOLD:
            challenger = build_contrarian_position(primary)
            contrarian_records.append(challenger)
            risk_records.append(challenger)

    # Risk budget/correlation caps apply to active model strategies, not to the
    # $1 baseline observations used for paired evaluation.
    allocate_portfolio(risk_records)
    records = control_records + risk_records

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    records_path = OUTPUT_DIR / f"records-{stamp}.json"
    records_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
    primary_count = sum(
        1 for r in risk_records
        if r["metadata"].get("training_eligible") is True
    )
    control_count = len(control_records)
    contrarian_count = len(contrarian_records)
    risk_gross_notional = round(
        sum(float(r["notional_usd"]) for r in risk_records), 2
    )
    all_paper_notional = round(
        sum(float(r["notional_usd"]) for r in records), 2
    )

    (OUTPUT_DIR / f"run-{stamp}.json").write_text(
        json.dumps(
            {
                "observed_at": iso(now),
                "markets_seen": len(markets),
                "markets_covered": len(covered),
                "model_candidate_markets": len(selected),
                "paper_positions_written": len(records),
                "primary_model_positions": primary_count,
                "control_positions": control_count,
                "risk_gross_notional_usd": risk_gross_notional,
                "all_paper_notional_usd": all_paper_notional,
                "contrarian_shadow_positions": contrarian_count,
                "forecast_failures": failures,
                "model": MODEL,
                "paper_only": True,
                "strategy_version": "kalshi-portfolio-v4",
                "parser_version": "strict-v2",
                "base_model_edge_shrink": MODEL_EDGE_SHRINK,
                "kelly_fraction": PAPER_KELLY_FRACTION,
                "max_event_gross_fraction": MAX_EVENT_GROSS_FRACTION,
                "max_event_net_fraction": MAX_EVENT_NET_FRACTION,
                "contrarian_edge_threshold": CONTRARIAN_EDGE_THRESHOLD,
                "contrarian_hedge_fraction": CONTRARIAN_HEDGE_FRACTION,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "kalshi_paper_positions": len(records),
                "primary": primary_count,
                "controls": control_count,
                "contrarian_shadow": contrarian_count,
                "forecast_failures": len(failures),
                "risk_gross_notional_usd": risk_gross_notional,
                "all_paper_notional_usd": all_paper_notional,
                "path": str(records_path),
            }
        )
    )


if __name__ == "__main__":
    main()
