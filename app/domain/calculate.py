from app.catalog import SPEC
from app.domain.helpers import *  # noqa: F401,F403
from app.domain.rules import current_rule, resolve


def calculate_rows(items):
    calculator = resolve(SPEC["rule"]) or current_rule
    out = []
    for item in items:
        revenue, cost, margin = calculator(item)
        item["net_revenue"] = revenue
        item["net_cost"] = cost
        item["margin"] = margin
        item["margin_rate"] = D(str(margin), "CALC") / D(str(revenue), "CALC") if revenue else D("0", "CALC")
        item["metric"] = D(str(margin), "CALC")
        item["limit"] = D(str(revenue), "LIMIT")
        out.append(item)
    for item in out:
        item.setdefault("label", str(item.get(SPEC["key"], "")))
    return out
