"""Attach provenance dependencies without duplicating quarterly arithmetic."""
from .quarterly import AMOUNTS, PERIODS


def bind_dependencies(payload, security_id, events):
    def financial(year, quarter, metric):
        month, day = PERIODS[quarter - 1]
        return f"financial:{security_id}:{year:04}-{month:02}-{day:02}:{metric}"

    def bind(node, dependencies, values):
        if isinstance(node, dict):
            node.update(review_dependencies=dependencies, review_values=values)

    years = payload.get("years", [])
    for metric, series in payload.get("metrics", {}).items():
        for year, column in zip(years, series["years"]):
            for quarter, cell in enumerate(column["quarters"], 1):
                deps = [financial(year, quarter, metric)]
                if metric in AMOUNTS and quarter > 1:
                    deps.append(financial(year, quarter - 1, metric))
                bind(cell, deps, ["value"])
            q = column.get("year_quarter") or 4
            deps = [financial(year, q, metric)]
            bind(column, deps, ["year_value"])
            bind(column["comparison"], deps + [financial(year-1, q, metric)], ["value"])
    for year, dividend in payload.get("dividends", {}).items():
        deps = [f"annual:{security_id}:{year}", financial(int(year), 4, "net_profit")]
        deps += [f"dividend:{security_id}:{event.get('plan_key') or event.get('announcement_id')}"
                 for event in events if str(event.get("fiscal_year")) == year]
        bind(dividend, deps, ["per_share", "implemented_per_share", "approved_per_share", "payout_ratio"])
    top = payload.get("top", {})
    period = top.get("report_period_date")
    if period:
        year, quarter = int(period[:4]), int(period[5:7]) // 3
        for metric, cell in top.get("summary", {}).items():
            deps = [financial(year, quarter, metric)]
            bind(cell, deps, ["value"])
            bind(cell["comparison"], deps + [financial(year-1, quarter, metric)], ["value"])
    payload["review_contract"] = 1
    return payload
