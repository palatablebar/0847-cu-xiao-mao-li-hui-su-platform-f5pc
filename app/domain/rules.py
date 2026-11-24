from app.catalog import SPEC

# 登记在册的毛利核算规则。签发批次时按当时的规则核算并把规则文本记入凭据；
# 财务抽查旧批次时必须沿用签发规则重算，新规则算出的差额不能记到旧签发凭据头上。


def current_rule(item):
    """供应商出资先冲减单件成本，优惠券再冲减行收入，两项各自封顶后计算行毛利。"""
    revenue = item["sell_price_cents"] * item["line_qty"] - item["coupon_cents"]
    cost = item["unit_cost_cents"] * item["line_qty"] - item["supplier_fund_cents"]
    return revenue, cost, revenue - cost


def alternate_rule(item):
    """供应商出资与优惠券一并冲减行收入后计算行毛利（登记在册的另一版规则）。"""
    revenue = (item["sell_price_cents"] * item["line_qty"]
               - item["coupon_cents"] - item["supplier_fund_cents"])
    cost = item["unit_cost_cents"] * item["line_qty"]
    return revenue, cost, revenue - cost


ALTERNATE_RULE = "供应商出资与优惠券一并冲减行收入后计算行毛利。"

RULES = {
    SPEC["rule"]: current_rule,
    ALTERNATE_RULE: alternate_rule,
}


def resolve(rule_text):
    """按规则文本找到登记在册的核算函数；找不到返回 None。"""
    if not rule_text:
        return None
    return RULES.get(str(rule_text).strip())
