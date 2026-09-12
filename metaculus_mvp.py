import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from forecasting_tools import GeneralLlm, MetaculusClient

from main import SummerTemplateBot2026

MODEL = "openrouter/liquid/lfm-2.5-2.6b:free"
PARSER = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
OUTPUT_DIR = Path(os.getenv("FORECAST_OUTPUT_ROOT", "outputs")) / "metaculus"


def iso(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def six_hour_bucket(now: datetime) -> datetime:
    hour = (now.hour // 6) * 6
    return now.replace(hour=hour, minute=0, second=0, microsecond=0)


async def run():
    if not os.getenv("METACULUS_TOKEN"):
        raise RuntimeError("METACULUS_TOKEN is required")
    if not os.getenv("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY is required")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bot = SummerTemplateBot2026(
        research_reports_per_question=1,
        predictions_per_research_report=3,
        use_research_summary_to_forecast=False,
        publish_reports_to_metaculus=True,
        folder_to_save_reports_to=str(OUTPUT_DIR),
        skip_previously_forecasted_questions=True,
        extra_metadata_in_explanation=True,
        llms={
            "default": GeneralLlm(
                model=MODEL,
                temperature=0.3,
                timeout=60,
                allowed_tries=2,
            ),
            "summarizer": MODEL,
            "researcher": MODEL,
            "parser": PARSER,
        },
    )

    client = MetaculusClient()
    seasonal = await bot.forecast_on_tournament(
        client.CURRENT_AI_COMPETITION_ID,
        return_exceptions=True,
    )
    minibench = await bot.forecast_on_tournament(
        client.CURRENT_MINIBENCH_ID,
        return_exceptions=True,
    )
    reports = seasonal + minibench

    now = datetime.now(timezone.utc)
    bucket = six_hour_bucket(now)
    records = []
    errors = []

    for report in reports:
        if isinstance(report, Exception):
            errors.append(str(report)[:500])
            continue
        prediction = getattr(report, "prediction", None)
        if not isinstance(prediction, (int, float)):
            continue
        probability = float(prediction)
        if probability < 0 or probability > 1:
            continue

        question = getattr(report, "question", None)
        page_url = getattr(question, "page_url", None)
        source_id = str(page_url or getattr(question, "id", None) or getattr(question, "post_id", None) or "")
        if not source_id:
            continue

        close_at = getattr(question, "close_time", None) or getattr(question, "scheduled_resolve_time", None)
        records.append(
            {
                "venue": "metaculus-live",
                "source_opportunity_id": source_id,
                "title": getattr(question, "question_text", None),
                "model_key": "openrouter:liquid/lfm-2.5-2.6b:free:metaculus-mvp-v1",
                "forecast_probability": round(probability, 6),
                "market_probability": None,
                "edge": None,
                "direction": None,
                "notional_usd": 0,
                "locked_at": iso(now),
                "lock_bucket": iso(bucket),
                "closes_at": iso(close_at),
                "status": "open",
                "metadata": {
                    "live_submission": True,
                    "financial_trade": False,
                    "prompt_version": "metaculus-mvp-v1",
                    "model": MODEL,
                    "parser": PARSER,
                    "page_url": page_url,
                    "question_type": type(question).__name__ if question is not None else None,
                    "explanation": getattr(report, "explanation", None),
                    "git_sha": os.getenv("GITHUB_SHA"),
                },
            }
        )

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    records_path = OUTPUT_DIR / f"records-{stamp}.json"
    records_path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (OUTPUT_DIR / f"run-{stamp}.json").write_text(
        json.dumps(
            {
                "observed_at": iso(now),
                "seasonal_reports": len(seasonal),
                "minibench_reports": len(minibench),
                "binary_records": len(records),
                "errors": errors,
                "model": MODEL,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    bot.log_report_summary(reports)
    print(json.dumps({"metaculus_records": len(records), "errors": len(errors), "path": str(records_path)}))


if __name__ == "__main__":
    asyncio.run(run())
