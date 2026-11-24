# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `POST /api/runs/{id}/audit`：财务抽查旧批次，可附 `source_csv`、`source_profile`、`issuance_rule` 作为复核材料。
- `GET /api/runs/{id}/audits`：查询该批次历次抽查结论。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。
