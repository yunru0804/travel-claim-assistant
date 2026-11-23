# 測試帳號與測試案例

Cognito 測試帳號對應的 seed 假資料，給前端 / API Gateway 端對端測試用。資料由 `seed_dynamodb.py` 寫入，帳號與情境的對應在 `ACCOUNTS`。

## 帳號一覽

| 手機 | 情境 | 旅程 | 保單 |
|---|---|---|---|
| **0912345678**(最常用) | 異常、已立案 | CI100 / 2026-11-20,延誤 5 小時 | 陳美玲 `TA-20261103-03` |
| 0911111111 | 正常旅程 | BR192 / 2026-11-20,準時 | 王小明 `TA-20261103-01` |
| 0922222222 | 有保單、沒有旅程 | 無 | 林志豪 `TA-20261103-02` |
| 0933333333 | 沒有旅程也沒有保單 | 無 | 無 |

- 保單期間都是 2026-11-01〜2027-02-28。
- Flights 表只有 BR192、CI100:除了 2026-11-20,另有執行 seed 當天到 2027-02-28 每天各一班，都是準時航班。其他航班號碼可以建立旅程，但 `GET` 會回 502 `flight_unavailable`。
- 天氣只有日本 4 城市(東京、大阪、福岡、沖繩)。BR192、CI100 都飛 TPE→NRT,目的地是東京。

## 測試案例

### 0912345678:異常、已立案

| 動作 | 預期 |
|---|---|
| `GET /v1/trip` | 200,`ABNORMAL`,有延誤說明(300 分鐘)與理賠卡片(`eligible: true`、預估 5000 元) |
| `PATCH /v1/trip/claim/checklist`,依序送兩個項目並改 `is_ready` | 200 `{}`,再 `GET` 看到勾選結果 |
| `PATCH` 的項目數量或順序與後端不同 | 409 `checklist_mismatch` |
| AI 客服問「我的航班有延誤嗎？」 | 回答 CI100 延誤約 5 小時 |

### 0911111111:正常旅程

| 動作 | 預期 |
|---|---|
| `GET /v1/trip` | 200,`NORMAL`,沒有理賠卡片 |
| `POST /v1/trip` | 409 `trip_already_exists`(前端詢問是否覆蓋) |
| 確認覆蓋後 `PUT /v1/trip/flight`,日期選今天以後 | 200 `{}`,舊旅程整筆被取代 |
| `PATCH /v1/trip/claim/checklist` | 409 `claim_not_eligible` |

### 0922222222:有保單、沒有旅程

| 動作 | 預期 |
|---|---|
| `GET /v1/trip` | 200 `{}`(前端導到開始旅程頁) |
| `PUT /v1/trip/flight` | 404 `trip_not_found`(前端改呼叫 `POST`) |
| `POST /v1/trip`,出發日為過去日期 | 400 `departure_date_in_past` |
| `POST /v1/trip`,BR192 或 CI100,日期選今天以後 | 200 `{}` |
| 建立後 `GET /v1/trip` | 200,`NORMAL`,有航班、保單、東京天氣 |

### 0933333333:沒有旅程也沒有保單

| 動作 | 預期 |
|---|---|
| `GET /v1/trip` | 200 `{}` |
| `POST /v1/trip`,BR192,日期選今天以後 | 200 `{}`(建立旅程時不檢查保單) |
| 建立後 `GET /v1/trip` | 404 `policy_not_found` |

## 測完還原

- 重跑 `python3 seed_dynamodb.py`(CloudShell,region `us-east-1`):0912345678、0911111111 的旅程與所有保單恢復原始資料。
- 0922222222、0933333333 用 `POST` 建立的旅程不會被 seed 刪除。要恢復成「沒有旅程」:**DynamoDB** → **Tables** → `Trips` → **Explore table items** → 勾選該 sub → **Actions** → **Delete items**。sub 見 `seed_dynamodb.py` 的 `ACCOUNTS`。

## 其他測試帳號

console 直接呼叫 Lambda(不經 API Gateway)的測試用假 sub `user-001`(正常)、`user-002`(沒有旅程)、`user-003`(異常),見 `trip-management-console-tests.md`。
