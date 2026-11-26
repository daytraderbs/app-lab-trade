from datetime import datetime, timezone
from price_analysis import analyze_recent_price
from adaptive_engine import evaluate_decision, avaliar_duas_direcoes


def _timestamp_epoch(value):
    if isinstance(value, (int, float)):
        return float(value)

    parsed = datetime.fromisoformat(
        str(value).replace("Z", "+00:00")
    )

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.timestamp()


def _metric_summary(metric):
    metric = metric or {}
    return {
        "value": metric.get("value"),
        "previous": metric.get("previous"),
        "status": metric.get("status", "UNKNOWN"),
        "source": metric.get("source"),
        "observation_date": metric.get(
            "observation_date",
            metric.get("effective_date")
        ),
        "units": metric.get("units")
    }


def analyze_hybrid(
    market_context,
    candles,
    timeframe="1m",
    monitor_latest=None,
    adaptive_rows=None,
    adaptive_summary=None
):
    if timeframe not in ("1m", "15m"):
        raise ValueError("Timeframe deve ser 1m ou 15m.")

    if not isinstance(market_context, dict):
        raise ValueError("Contexto de mercado inválido.")

    if not isinstance(candles, list):
        raise ValueError("Lista de candles inválida.")

    seconds = 60 if timeframe == "1m" else 900
    cutoff = datetime.now(timezone.utc).timestamp() - 1

    closed_candles = []

    for candle in candles:
        try:
            timestamp = _timestamp_epoch(candle["timestamp"])

            if timestamp + seconds <= cutoff:
                closed_candles.append(candle)

        except (KeyError, TypeError, ValueError, OverflowError):
            continue

    banks = market_context.get("central_banks", {})
    indicators = market_context.get("economic_indicators", {})

    metrics = {
        "ecb_rate": _metric_summary(
            banks.get("ECB", {}).get("interest_rate")
        ),
        "fed_rate": _metric_summary(
            banks.get("FED", {}).get("interest_rate")
        ),
    }

    for region in ("eurozone", "united_states"):
        region_data = indicators.get(region, {})

        for name in ("inflation", "employment", "gdp"):
            metrics[f"{region}_{name}"] = _metric_summary(
                region_data.get(name)
            )

    valid_count = sum(
        item["status"] == "VALID"
        for item in metrics.values()
    )

    context_summary = {
        "status": (
            "AVAILABLE" if valid_count == len(metrics)
            else "PARTIAL" if valid_count > 0
            else "UNAVAILABLE"
        ),
        "valid_metrics": valid_count,
        "total_metrics": len(metrics),
        "metrics": metrics,
        "news_status": market_context.get(
            "news", {}
        ).get("status", "UNKNOWN")
    }

    if len(closed_candles) < 30:
        sinal_base = (
            monitor_latest.get("sinal")
            if isinstance(monitor_latest, dict)
            else None
        )
        adaptativo = evaluate_decision(
            adaptive_rows or [],
            "INDEFINIDO",
            sinal_base,
            adaptive_summary,
            monitor_latest
        )
        adaptativo["comparacao_direcoes"] = avaliar_duas_direcoes(
            adaptive_rows or [], "INDEFINIDO", monitor_latest
        )

        return {
            "status": "INSUFFICIENT_DATA",
            "adaptive_analysis": adaptativo,
            "timeframe": timeframe,
            "closed_candles": len(closed_candles),
            "market_context": context_summary,
            "decision": "AGUARDAR",
            "execution": False,
            "reason": "São necessários pelo menos 30 candles fechados."
        }

    price = analyze_recent_price(closed_candles)

    regime_info = price.get("market_regime", {})
    regime = (
        regime_info.get("regime", "INDEFINIDO")
        if isinstance(regime_info, dict)
        else "INDEFINIDO"
    )

    sinal_base = (
        monitor_latest.get("sinal")
        if isinstance(monitor_latest, dict)
        else None
    )

    adaptativo = evaluate_decision(
        adaptive_rows or [],
        regime,
        sinal_base,
        adaptive_summary,
        monitor_latest
    )
    adaptativo["comparacao_direcoes"] = avaliar_duas_direcoes(
        adaptive_rows or [], regime, monitor_latest
    )

    return {
        "status": "OK",
        "asset": "EUR/USD",
        "timeframe": timeframe,
        "analysis_timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "market_context": context_summary,
        "price_analysis": price,
        "adaptive_analysis": adaptativo,
        "integration": {
            "macro_direction": "NOT_ESTABLISHED",
            "reason": (
                "Os indicadores econômicos ainda não possuem "
                "interpretação direcional validada."
            )
        },
        "decision": adaptativo["decisao"],
        "decision_reasons": [
            "Sinal-base recebido do monitor: "
            + str(sinal_base or "AUSENTE"),
            "Influência adaptativa: "
            + adaptativo["influencia"],
            adaptativo["motivo"],
            "Nenhuma ordem é executada automaticamente."
        ],
        "execution": False
    }
