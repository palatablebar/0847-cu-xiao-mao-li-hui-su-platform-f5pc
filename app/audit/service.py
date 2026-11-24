import hashlib
import json

from app.catalog import SPEC
from app.config import SAMPLES
from app.domain.prepare import prepare_rows
from app.domain.rules import resolve
from app.errors import DomainIssue
from app.intake.service import load_bytes

# 抽查结论：待补（材料不齐）、来源不符、复核失败、复核通过。
LABELS = {"PENDING": "待补", "SOURCE_MISMATCH": "来源不符", "FAILED": "复核失败", "PASSED": "复核通过"}
PROFILES = ("dirty", "clean")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text(data, field):
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainIssue("BAD_REQUEST")
    return value.strip()


def _source_material(run, data):
    """定位原表，返回 (字节, 材料标记)；找不到原表时字节为 None。

    显式提交的 source_csv 优先；空白内容视为材料缺失而不是未提交。
    未提交时按批次登记的来源样例重新取表；样例不存在即找不到原表。
    """
    if "source_csv" in data and data["source_csv"] is not None:
        text = _text(data, "source_csv")
        if not text:
            return None, "missing"
        raw = text.encode("utf-8")
        return raw, "sha256:" + _sha256(raw)
    profile = _text(data, "source_profile") or run["source"].get("profile")
    if profile not in PROFILES:
        return None, "missing"
    path = SAMPLES / f"{profile}.csv"
    if not path.is_file():
        return None, "missing"
    raw = path.read_bytes()
    return raw, "sha256:" + _sha256(raw)


def _rule_material(run, data):
    """定位签发规则，返回 (规则文本, 材料标记)；找不到签发规则时文本为 None。"""
    if "issuance_rule" in data and data["issuance_rule"] is not None:
        text = _text(data, "issuance_rule")
        return (text, "rule:" + text) if text else (None, "missing")
    archived = (run.get("rule_text") or "").strip()
    return (archived, "rule:" + archived) if archived else (None, "missing")


def _recalculate(calculator, raw):
    """按签发规则对原表逐商品重算毛利。"""
    intake = load_bytes("audit", raw, "audit-source.csv")
    prepared, _issues = prepare_rows(intake["rows"])
    book = {}
    for item in prepared:
        _revenue, _cost, margin = calculator(item)
        book[str(item[SPEC["key"]])] = margin
    return book


def spot_check(store, run_id, data):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if row["status"] != "APPROVED":
        raise DomainIssue("NOT_APPROVED", 409)
    run = json.loads(row["payload"])
    raw, source_marker = _source_material(run, data)
    rule_text, rule_marker = _rule_material(run, data)
    # 同样的材料重试命中同一复核记录，不新增。
    audit_key = hashlib.sha256((run_id + "|" + source_marker + "|" + rule_marker).encode("utf-8")).hexdigest()[:24]
    existing = store.find_audit(audit_key)
    if existing:
        return serialize_audit(existing, row)
    calculator = resolve(rule_text)
    missing = []
    if raw is None:
        missing.append("source")
    if calculator is None:
        missing.append("rule")
    if missing:
        conclusion = "PENDING"
        reason = "MATERIALS_MISSING" if len(missing) > 1 else ("SOURCE_MISSING" if missing[0] == "source" else "RULE_MISSING")
        detail = {"missing": missing}
    elif _sha256(raw) != run["source"].get("sha256"):
        # 原表对不上签发时的来源，即使整批毛利相同也报来源不符。
        conclusion = "SOURCE_MISMATCH"
        reason = "SOURCE_MISMATCH"
        detail = {"expected_sha256": run["source"].get("sha256"), "actual_sha256": _sha256(raw)}
    else:
        book = run.get("margin_book") or {}
        recalculated = _recalculate(calculator, raw)
        diffs = [{"sku": sku, "issued": book.get(sku), "recalculated": recalculated.get(sku)}
                 for sku in sorted(set(book) | set(recalculated))
                 if book.get(sku) != recalculated.get(sku)]
        conclusion = "FAILED" if diffs else "PASSED"
        reason = "MARGIN_DIFF" if diffs else "OK"
        detail = {"rule": rule_text, "source_sha256": _sha256(raw),
                  "checked": len(recalculated), "diffs": diffs}
    record = store.create_audit(audit_key, run_id, conclusion, reason, detail)
    return serialize_audit(record, row)


def list_audits(store, run_id):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    return [serialize_audit(record, row) for record in store.audits_for(run_id)]


def serialize_audit(record, run_row):
    return {"audit_id": record["id"], "run_id": record["run_id"],
            "conclusion": record["conclusion"], "conclusion_label": LABELS[record["conclusion"]],
            "reason": record["reason"], "detail": json.loads(record["detail"]),
            "created_at": record["created_at"],
            "evidence_available": run_row["status"] == "APPROVED"}
