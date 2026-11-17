from datetime import datetime, timezone


def empty_metric():
    return {
        "value": None,
        "previous": None,
        "source": None,
        "published_at": None,
        "status": "UNKNOWN"
    }


def create_market_context():
    return {
        "asset": "EUR/USD",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "central_banks": {
            "ECB": {
                "interest_rate": empty_metric(),
                "next_meeting": empty_metric(),
                "policy_outlook": empty_metric()
            },
            "FED": {
                "interest_rate": empty_metric(),
                "next_meeting": empty_metric(),
                "policy_outlook": empty_metric()
            }
        },
        "economic_indicators": {
            "eurozone": {
                "inflation": empty_metric(),
                "employment": empty_metric(),
                "gdp": empty_metric()
            },
            "united_states": {
                "inflation": empty_metric(),
                "employment": empty_metric(),
                "gdp": empty_metric()
            }
        },
        "news": {
            "events": [],
            "source": None,
            "status": "UNKNOWN"
        },
        "market_conditions": {
            "session": "UNKNOWN",
            "volatility": empty_metric()
        },
        "context_status": "INCOMPLETE",
        "decision": "AGUARDAR",
        "execution": False
    }


def fetch_ecb_deposit_rate(context, timeout=20):
    import csv
    import io
    import requests

    url = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV"

    try:
        response = requests.get(
            url,
            params={"lastNObservations": 3, "format": "csvdata"},
            timeout=timeout
        )
        response.raise_for_status()

        rows = list(csv.DictReader(io.StringIO(response.text)))
        valid = [
            row for row in rows
            if row.get("OBS_VALUE") not in ("", None)
        ]

        if not valid:
            raise ValueError("BCE não retornou observações válidas.")

        valid.sort(key=lambda row: row["TIME_PERIOD"])
        latest = valid[-1]
        previous = valid[-2] if len(valid) > 1 else None

        context["central_banks"]["ECB"]["interest_rate"] = {
            "value": float(latest["OBS_VALUE"]),
            "previous": (
                float(previous["OBS_VALUE"]) if previous else None
            ),
            "source": url,
            "effective_date": latest["TIME_PERIOD"],
            "published_at": None,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "status": "VALID"
        }

    except Exception as exc:
        context["central_banks"]["ECB"]["interest_rate"]["status"] = "ERROR"
        context["central_banks"]["ECB"]["interest_rate"]["error"] = str(exc)

    return context


def fetch_fed_effective_rate(context, timeout=20):
    import csv
    import io
    import requests

    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    try:
        response = requests.get(
            url,
            params={"id": "DFF"},
            timeout=timeout
        )
        response.raise_for_status()

        rows = list(csv.DictReader(io.StringIO(response.text)))
        valid = [
            row for row in rows
            if row.get("DFF") not in ("", ".", None)
        ]

        if not valid:
            raise ValueError("FRED não retornou observações válidas.")

        latest = valid[-1]
        previous = valid[-2] if len(valid) > 1 else None

        context["central_banks"]["FED"]["interest_rate"] = {
            "value": float(latest["DFF"]),
            "previous": (
                float(previous["DFF"]) if previous else None
            ),
            "rate_type": "EFFECTIVE_FEDERAL_FUNDS_RATE",
            "source": url,
            "observation_date": latest["observation_date"],
            "published_at": None,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "status": "VALID"
        }

    except Exception as exc:
        context["central_banks"]["FED"]["interest_rate"]["status"] = "ERROR"
        context["central_banks"]["FED"]["interest_rate"]["error"] = str(exc)

    return context


def fetch_us_economic_indicators(context, timeout=20):
    import csv
    import io
    import requests

    url = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    series_config = {
        "inflation": ("CPIAUCSL", "PERCENT_YEAR_OVER_YEAR"),
        "employment": ("UNRATE", "PERCENT_UNEMPLOYMENT"),
        "gdp": ("A191RL1Q225SBEA", "PERCENT_QUARTERLY_ANNUALIZED"),
    }

    indicators = context["economic_indicators"]["united_states"]

    for name, (series_id, units) in series_config.items():
        try:
            response = requests.get(
                url,
                params={"id": series_id, "cosd": "2023-01-01"},
                timeout=timeout,
            )
            response.raise_for_status()

            rows = list(csv.DictReader(io.StringIO(response.text)))
            valid = [
                row for row in rows
                if row.get(series_id) not in ("", ".", None)
            ]

            if name == "inflation":
                if len(valid) < 13:
                    raise ValueError("Histórico insuficiente para CPI.")

                latest = float(valid[-1][series_id])
                previous = float(valid[-2][series_id])
                base = float(valid[-13][series_id])
                previous_base = float(valid[-14][series_id]) if len(valid) >= 14 else None

                value = (latest / base - 1) * 100
                previous_value = (
                    (previous / previous_base - 1) * 100
                    if previous_base else None
                )
            else:
                if len(valid) < 2:
                    raise ValueError("Observações insuficientes.")

                value = float(valid[-1][series_id])
                previous_value = float(valid[-2][series_id])

            indicators[name] = {
                "value": round(value, 4),
                "previous": (
                    round(previous_value, 4)
                    if previous_value is not None else None
                ),
                "units": units,
                "indicator_name": (
                    "UNEMPLOYMENT_RATE" if name == "employment"
                    else name.upper()
                ),
                "semantic_key": (
                    "unemployment_rate" if name == "employment" else name
                ),
                "series_id": series_id,
                "source": f"https://fred.stlouisfed.org/series/{series_id}",
                "observation_date": valid[-1]["observation_date"],
                "published_at": None,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "status": "VALID",
            }

        except Exception as exc:
            indicators[name]["status"] = "ERROR"
            indicators[name]["error"] = str(exc)

    return context


def fetch_eurozone_economic_indicators(context, timeout=25):
    import requests

    base = (
        "https://ec.europa.eu/eurostat/api/"
        "dissemination/statistics/1.0/data/"
    )

    config = {
        "inflation": (
            "prc_hicp_minr",
            {
                "freq": "M", "geo": "EA21",
                "coicop18": "TOTAL", "unit": "RCH_A",
                "sinceTimePeriod": "2025-01", "lang": "en"
            },
            "PERCENT_YEAR_OVER_YEAR", "INFLATION"
        ),
        "employment": (
            "une_rt_m",
            {
                "freq": "M", "geo": "EA21",
                "sex": "T", "age": "TOTAL",
                "unit": "PC_ACT", "s_adj": "SA",
                "sinceTimePeriod": "2025-01", "lang": "en"
            },
            "PERCENT_UNEMPLOYMENT", "UNEMPLOYMENT_RATE"
        ),
        "gdp": (
            "namq_10_gdp",
            {
                "freq": "Q", "geo": "EA21",
                "na_item": "B1GQ", "unit": "CLV_PCH_PRE",
                "s_adj": "SCA",
                "sinceTimePeriod": "2025-Q1", "lang": "en"
            },
            "PERCENT_QUARTERLY_CHANGE", "GDP_GROWTH"
        )
    }

    indicators = context["economic_indicators"]["eurozone"]

    for name, (dataset, params, units, label) in config.items():
        try:
            response = requests.get(
                base + dataset, params=params, timeout=timeout
            )
            response.raise_for_status()
            data = response.json()

            time_index = data["dimension"]["time"]["category"]["index"]
            values = data.get("value", {})

            if isinstance(time_index, dict):
                periods = sorted(time_index.items(), key=lambda x: x[1])
            else:
                periods = list(enumerate(time_index))
                periods = [(period, index) for index, period in periods]

            valid = []
            for period, index in periods:
                if isinstance(values, dict):
                    value = values.get(str(index))
                elif isinstance(values, list):
                    value = values[index] if index < len(values) else None
                else:
                    value = None

                if value is not None:
                    valid.append((period, float(value)))

            if not valid:
                raise ValueError(f"Eurostat sem observações válidas: {label}")

            latest_period, latest_value = valid[-1]
            previous_value = valid[-2][1] if len(valid) > 1 else None

            indicators[name] = {
                "value": round(latest_value, 4),
                "previous": (
                    round(previous_value, 4)
                    if previous_value is not None else None
                ),
                "units": units,
                "indicator_name": label,
                "dataset": dataset,
                "geo": "EA21",
                "source": base + dataset,
                "observation_date": latest_period,
                "published_at": None,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "status": "VALID"
            }

        except Exception as exc:
            indicators[name]["status"] = "ERROR"
            indicators[name]["error"] = str(exc)

    return context
