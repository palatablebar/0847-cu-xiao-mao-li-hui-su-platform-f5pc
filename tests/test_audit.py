import json

import pytest

from app.catalog import SPEC
from app.config import SAMPLES
from app.domain.rules import ALTERNATE_RULE
from app.main import create_app


@pytest.fixture
def bench(tmp_path):
    app = create_app(str(tmp_path / "workbench.sqlite3"))
    app.config.update(TESTING=True)
    return app, app.test_client()


def approve(client, profile="clean"):
    run = client.post("/api/runs", json={"profile": profile}).get_json()
    client.post(f"/api/runs/{run['run_id']}/review",
                json={"decision": "APPROVED", "comment": "复核完成", "expected_revision": 0})
    return run


def audit(client, run_id, **materials):
    return client.post(f"/api/runs/{run_id}/audit", json=materials)


def tamper_payload(app, run_id, mutate):
    store = app.extensions["runs"]
    payload = json.loads(store.get(run_id)["payload"])
    mutate(payload)
    with store.connect() as db:
        db.execute("UPDATE runs SET payload=? WHERE id=?",
                   (json.dumps(payload, ensure_ascii=False), run_id))


def test_draft_run_cannot_be_audited(bench):
    _app, client = bench
    run = client.post("/api/runs", json={"profile": "clean"}).get_json()
    response = audit(client, run["run_id"])
    assert response.status_code == 409
    assert response.get_json()["code"] == "NOT_APPROVED"


def test_missing_run_returns_404(bench):
    _app, client = bench
    assert audit(client, "no-such-run").status_code == 404
    assert client.get("/api/runs/no-such-run/audits").status_code == 404


def test_bad_material_type_is_rejected(bench):
    _app, client = bench
    run = approve(client)
    assert audit(client, run["run_id"], source_csv=123).status_code == 400


def test_full_materials_pass(bench):
    _app, client = bench
    run = approve(client)
    result = audit(client, run["run_id"]).get_json()
    assert result["conclusion"] == "PASSED"
    assert result["conclusion_label"] == "复核通过"
    assert result["reason"] == "OK"
    assert result["detail"]["checked"] == 3
    assert result["detail"]["diffs"] == []


def test_missing_source_is_pending_and_voucher_stays_available(bench):
    _app, client = bench
    run = approve(client)
    result = audit(client, run["run_id"], source_profile="no-such-profile").get_json()
    assert result["conclusion"] == "PENDING"
    assert result["conclusion_label"] == "待补"
    assert result["reason"] == "SOURCE_MISSING"
    assert result["detail"]["missing"] == ["source"]
    assert result["evidence_available"] is True
    assert client.get(f"/api/runs/{run['run_id']}/evidence").status_code == 200


def test_blank_source_csv_counts_as_missing(bench):
    _app, client = bench
    run = approve(client)
    result = audit(client, run["run_id"], source_csv="  ").get_json()
    assert result["conclusion"] == "PENDING"
    assert result["reason"] == "SOURCE_MISSING"


def test_unknown_issuance_rule_is_pending(bench):
    _app, client = bench
    run = approve(client)
    result = audit(client, run["run_id"], issuance_rule="查无此规则").get_json()
    assert result["conclusion"] == "PENDING"
    assert result["reason"] == "RULE_MISSING"
    assert result["detail"]["missing"] == ["rule"]
    assert client.get(f"/api/runs/{run['run_id']}/evidence").status_code == 200


def test_run_without_archived_rule_is_pending(bench):
    app, client = bench
    run = approve(client)
    tamper_payload(app, run["run_id"], lambda payload: payload.pop("rule_text"))
    result = audit(client, run["run_id"]).get_json()
    assert result["conclusion"] == "PENDING"
    assert result["reason"] == "RULE_MISSING"


def test_missing_source_and_rule_reported_together(bench):
    _app, client = bench
    run = approve(client)
    result = audit(client, run["run_id"], source_csv="", issuance_rule="").get_json()
    assert result["conclusion"] == "PENDING"
    assert result["reason"] == "MATERIALS_MISSING"
    assert result["detail"]["missing"] == ["source", "rule"]


def test_source_mismatch_even_when_total_margin_matches(bench):
    _app, client = bench
    run = approve(client)
    raw = (SAMPLES / "clean.csv").read_text(encoding="utf-8")
    # 只动吊牌价：整批毛利不变，但原表字节与签发来源对不上。
    modified = raw.replace("A,10,1000,", "A,10,1234,")
    assert modified != raw
    result = audit(client, run["run_id"], source_csv=modified).get_json()
    assert result["conclusion"] == "SOURCE_MISMATCH"
    assert result["conclusion_label"] == "来源不符"
    assert result["reason"] == "SOURCE_MISMATCH"
    assert result["detail"]["expected_sha256"] == run["source"]["sha256"]
    assert result["detail"]["actual_sha256"] != run["source"]["sha256"]


def test_margin_difference_fails_audit(bench):
    app, client = bench
    run = approve(client)

    def break_book(payload):
        payload["margin_book"]["A"] += 1

    tamper_payload(app, run["run_id"], break_book)
    result = audit(client, run["run_id"]).get_json()
    assert result["conclusion"] == "FAILED"
    assert result["conclusion_label"] == "复核失败"
    assert result["reason"] == "MARGIN_DIFF"
    diffs = {item["sku"]: item for item in result["detail"]["diffs"]}
    assert diffs["A"]["issued"] == 3501
    assert diffs["A"]["recalculated"] == 3500


def test_same_materials_do_not_create_new_record(bench):
    _app, client = bench
    run = approve(client)
    first = audit(client, run["run_id"]).get_json()
    second = audit(client, run["run_id"]).get_json()
    assert first["audit_id"] == second["audit_id"]
    assert len(client.get(f"/api/runs/{run['run_id']}/audits").get_json()["audits"]) == 1
    # 换了材料（补来的原表对不上来源）才新增复核记录。
    third = audit(client, run["run_id"], source_profile="dirty").get_json()
    assert third["conclusion"] == "SOURCE_MISMATCH"
    assert third["audit_id"] != first["audit_id"]
    audits = client.get(f"/api/runs/{run['run_id']}/audits").get_json()["audits"]
    assert len(audits) == 2
    assert {item["conclusion"] for item in audits} == {"PASSED", "SOURCE_MISMATCH"}


def test_new_rule_difference_stays_off_old_voucher(bench, monkeypatch):
    _app, client = bench
    issued_rule = SPEC["rule"]
    run = approve(client)
    # 新规则上线：当前规则文本切换，但旧凭据仍按签发时的规则重算。
    monkeypatch.setitem(SPEC, "rule", ALTERNATE_RULE)
    result = audit(client, run["run_id"]).get_json()
    assert result["conclusion"] == "PASSED"
    assert result["detail"]["rule"] == issued_rule


def test_workbench_lists_conclusions(bench):
    _app, client = bench
    run = approve(client)
    assert client.get(f"/api/runs/{run['run_id']}/audits").get_json()["audits"] == []
    audit(client, run["run_id"], source_profile="unknown")
    audits = client.get(f"/api/runs/{run['run_id']}/audits").get_json()["audits"]
    assert len(audits) == 1
    assert audits[0]["conclusion"] == "PENDING"
    assert audits[0]["conclusion_label"] == "待补"
    assert audits[0]["run_id"] == run["run_id"]
