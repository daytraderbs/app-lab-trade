import asyncio
import copy
import os
from datetime import datetime, timezone

from market_context import (
    create_market_context,
    fetch_ecb_deposit_rate,
    fetch_fed_effective_rate,
    fetch_us_economic_indicators,
    fetch_eurozone_economic_indicators,
)

REFRESH_SECONDS = max(
    300, int(os.getenv("MARKET_CONTEXT_REFRESH_SECONDS", "21600"))
)

_context = create_market_context()
_lock = asyncio.Lock()

_state = {
    "last_attempt": None,
    "last_success": None,
    "last_error": None,
    "last_refresh_status": "NEVER",
    "refresh_interval_seconds": REFRESH_SECONDS,
}


async def refresh_market_context():
    async with _lock:
        _state["last_attempt"] = datetime.now(timezone.utc).isoformat()

        candidate = create_market_context()

        results = await asyncio.gather(
            asyncio.to_thread(fetch_ecb_deposit_rate, candidate),
            asyncio.to_thread(fetch_fed_effective_rate, candidate),
            asyncio.to_thread(fetch_us_economic_indicators, candidate),
            asyncio.to_thread(fetch_eurozone_economic_indicators, candidate),
            return_exceptions=True,
        )

        banks = ("ECB", "FED")

        for bank in banks:
            fetched = candidate["central_banks"][bank]["interest_rate"]
            current = _context["central_banks"][bank]["interest_rate"]

            if fetched.get("status") == "VALID":
                _context["central_banks"][bank]["interest_rate"] = fetched
            elif current.get("status") == "UNKNOWN":
                _context["central_banks"][bank]["interest_rate"] = fetched
            else:
                current["last_check_error"] = fetched.get("error")

        for name in ("inflation", "employment", "gdp"):
            fetched = candidate["economic_indicators"]["united_states"][name]
            current = _context["economic_indicators"]["united_states"][name]
            if fetched.get("status") == "VALID":
                _context["economic_indicators"]["united_states"][name] = fetched
            elif current.get("status") == "UNKNOWN":
                _context["economic_indicators"]["united_states"][name] = fetched
            else:
                current["last_check_error"] = fetched.get("error")

        for name in ("inflation", "employment", "gdp"):
            fetched = candidate["economic_indicators"]["eurozone"][name]
            current = _context["economic_indicators"]["eurozone"][name]

            if fetched.get("status") == "VALID":
                _context["economic_indicators"]["eurozone"][name] = fetched
            elif current.get("status") == "UNKNOWN":
                _context["economic_indicators"]["eurozone"][name] = fetched
            else:
                current["last_check_error"] = fetched.get("error")

        errors = [
            str(result)
            for result in results
            if isinstance(result, Exception)
        ]

        fresh_metrics = []
        for bank in ("ECB", "FED"):
            fresh_metrics.append(
                candidate["central_banks"][bank]["interest_rate"]
            )

        for region in ("eurozone", "united_states"):
            for name in ("inflation", "employment", "gdp"):
                fresh_metrics.append(
                    candidate["economic_indicators"][region][name]
                )

        fresh_valid_count = sum(
            metric.get("status") == "VALID"
            for metric in fresh_metrics
        )

        error_details = list(errors)
        for region in ("eurozone", "united_states"):
            for name in ("inflation", "employment", "gdp"):
                metric = candidate["economic_indicators"][region][name]
                if metric.get("status") == "ERROR":
                    error_details.append(
                        f"{region}.{name}: {metric.get('error')}"
                    )

        for bank in ("ECB", "FED"):
            metric = candidate["central_banks"][bank]["interest_rate"]
            if metric.get("status") == "ERROR":
                error_details.append(
                    f"{bank}: {metric.get('error')}"
                )

        _context["updated_at"] = datetime.now(timezone.utc).isoformat()
        _context["context_status"] = "INCOMPLETE"
        _context["decision"] = "AGUARDAR"
        _context["execution"] = False

        if fresh_valid_count:
            _state["last_success"] = _context["updated_at"]

        _state["last_refresh_status"] = (
            "SUCCESS" if fresh_valid_count == len(fresh_metrics)
            else "PARTIAL" if fresh_valid_count
            else "ERROR"
        )
        _state["last_error"] = (
            "; ".join(error_details) if error_details else None
        )

        return get_market_context()


def get_market_context():
    result = copy.deepcopy(_context)
    result["service"] = copy.deepcopy(_state)
    return result


async def run_market_context_scheduler():
    while True:
        try:
            await refresh_market_context()
        except Exception as exc:
            _state["last_error"] = str(exc)
            _state["last_refresh_status"] = "ERROR"

        await asyncio.sleep(REFRESH_SECONDS)
