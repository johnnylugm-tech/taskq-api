# 漏洞掃描報告 — 2026-08-13

## 1. 掃描摘要

| 模組 | Critical | High | Medium | Low |
|---|---|---|---|---|
| taskq_api.api.tasks | 0 | 1 | 0 | 0 |
| taskq_api.service.ratelimit | 0 | 1 | 1 | 0 |
| migrations.versions.v3_split_results | 0 | 2 | 0 | 0 |
| taskq_api.app | 0 | 1 | 0 | 0 |
| taskq_api.repository.task_repo | 0 | 1 | 0 | 0 |
| taskq_api.service.auth | 0 | 0 | 1 | 0 |
| **合計** | **0** | **6** | **2** | **0** |

威脅模型 11 條 (`threat_model`):T-01/T-02/T-03/T-04/T-05/T-07/T-08/T-09/T-11 mitigation effective; T-06 mitigation broken (HIGH-2); T-10 mitigation broken (HIGH-3)。

## 2. 確認的 Bugs (severity 降序)

### HIGH-1 — POST /v1/tasks/{id}/run 沒有 timeout
**位置**: `taskq_api/api/tasks.py:177`
**問題**: `TaskRunner().run(task["command"])` 沒有帶 `timeout_seconds`,導致 subprocess 可無限阻塞 worker thread。
**證據**: `runner.py:75-77` 的 `if timeout_seconds is None: return await proc.communicate()` 是無界路徑;從 HTTP 入口永遠走這條。
**修復**: 從 `TASKQ_TASK_TIMEOUT` env 讀秒數傳入。

### HIGH-2 — Rate-limit bucket 無界增長 (T-06)
**位置**: `taskq_api/repository/rate_repo.py:46-82`
**問題**: `_buckets` 是 module-level dict,以原始 token 為 key。每次不同 garbage token 都新增 bucket,無 eviction / TTL / cap。攻擊者送 N 個不同 X-API-Key 即可 OOM。
**證據**: `rate_repo.py:79` 無條件覆寫,無 pop / del。`deps.py:141` 在 verify_key 之前就消耗 token。
**修復**: 加 max size + LRU eviction(或 fallback to allowed=True 當滿載)。

### HIGH-3 — Alembic v3 downgrade 丟 task 結果資料 (T-10)
**位置**: `migrations/versions/v3_split_results.py:98-114`
**問題**: `_REPOPULATE_RESULT_JSON` 用 `LIMIT 1`,每個 task 只保留最新一筆 task_results。多 run 的 task 其餘執行紀錄被永久丟失。
**證據**: line 111 `ORDER BY tr.rowid DESC LIMIT 1`;line 205 `drop_table(_TASK_RESULTS_TABLE)` 後資料無法回復。
**修復**: 把多 run 聚合進 `runs: [...]` JSON array,或 downgrade 時保留 task_results 直到 operator 確認。

### HIGH-4 — Alembic v3 upgrade 吞錯誤後仍 drop column
**位置**: `migrations/versions/v3_split_results.py:158-179`
**問題**: `try/except SQLAlchemyError: pass` 把 backfill 失敗吞掉,接著無條件 `drop_column(_RESULT_JSON_COLUMN)`。backfill 失敗 = 資料必丟,且無 trace。
**證據**: line 166-174 bare except + line 179 unconditional drop。
**修復**: rowcount 檢查後才 drop,或 reraise 讓 alembic abort migration。

### HIGH-5 — /readyz 每次 request 創建新 SQLAlchemy engine
**位置**: `taskq_api/app.py:110-124`
**問題**: `_check_migration_state` 每次都 `create_engine(db_url)`,engine 不 cache、不 dispose。k8s 探針每 2s 打一次,1 小時累積 1800 個 engine + pool,fd / memory 耗盡。
**證據**: line 111 無 cache;lifespan (line 190-225) 無 engine.dispose。
**修復**: module-level lazy engine + lifespan dispose。

### HIGH-6 — TaskRepo.list cursor 參數被忽略 (FR-01 契約破壞)
**位置**: `taskq_api/repository/task_repo.py:137-188`
**問題**: SELECT 沒有套用 `cursor` 過濾。`list_tasks` handler 收到 `?cursor=X` 但 DB query 完全忽略它,回傳相同首頁。`next_cursor` 有 emit 但客戶端拿著 cursor 永遠停在第一頁。
**證據**: line 166-170 SELECT 沒有 `where(_task_table.c.id > cursor)`。
**修復**: 加 `if cursor is not None: stmt = stmt.where(_task_table.c.id > cursor)`,並 ORDER BY id ASC。

## 3. 被反駁的 Findings
(無 — 所有發現均確認成立)

## 4. 修復優先順序
1. HIGH-3, HIGH-4 (資料遺失 — production 不可逆)
2. HIGH-2 (memory DoS — 一行觸發)
3. HIGH-1 (DoS via 任務 hang — 易利用)
4. HIGH-5 (resource 累積 — probe 流量大時顯著)
5. HIGH-6 (功能缺失 — 不致命但契約破壞)
6. MEDIUM-1, MEDIUM-2 (記錄留檔,不擋 Gate 3)

## 5. 掃描方法
- Targets manifest: `.methodology/bug_hunt_targets.json` (26 high-risk + 19 standard + 11 threat-model)
- 直接 read 全部 14 個 high-risk source files 完整內容,逐函式比對 SPEC 條款
- 對照 `tests/test_fr02.py` / `test_fr05.py` / `test_fr08.py` 確認既有測試覆蓋面,辨識「測試沒覆蓋但 production 可達」的缺陷
- 對 11 條 `threat_model` entry 逐條驗證宣告的 mitigation 是否真的擋住攻擊向量
- 報告 schema 遵循 `harness/schemas/bug_hunt_report.schema.json`;`mitigation_effective` 欄位嵌入 description 文字內
