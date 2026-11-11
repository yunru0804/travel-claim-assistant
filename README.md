# 旅遊理賠助手(Travel Claim Assistant)

旅客登入後輸入航班,系統監控航班狀態;航班延誤達保單理賠門檻時通知使用者,並提供理賠準備清單與 AI 對話協助。

後端以 AWS Serverless 建置(API Gateway + Lambda + DynamoDB),目前以 console / AWS CLI 手動部署。

## 整體架構

```
前端(CloudFront + S3 靜態網頁,WAF)
  │  Cognito 登入,帶 access token
  ▼
API Gateway(REST,Cognito Authorizer)
  ├─ /v1/trip*                 → Lambda trip-management        ✅ 已完成
  ├─ /v1/push/subscriptions*   → Lambda push-subscription      ⬜ 未開始
  ├─ POST /v1/chat/messages    → Lambda chat-send              ⬜ 未開始(呼叫 Bedrock AgentCore)
  └─ /v1/conversations*        → Lambda conversation-management ⬜ 未開始

事件驅動(航班監控,未開始)
  EventBridge Scheduler → CheckFlight → EventBridge Bus → ClaimEligible → Notification → Web Push
  負責寫入 Trips 表的 trip_status / disruption / delay_claim
```

- 使用者身分只從 API Gateway 驗證後的 Cognito claims(`sub`)取得,不接受 body / query 帶入的使用者 ID。
- 保單、航班、天氣、匯率目前都是 DynamoDB 假資料表,之後可能換成真實的外部 API。

## API 開發進度

### 05 開始旅程頁

| API | 狀態 |
|---|---|
| `POST /v1/trip` 建立目前旅程 | ✅ 完成 |
| `PUT /v1/trip/flight` 修改航班(整筆覆蓋) | ✅ 完成 |

### 06 旅程主頁

| API | 狀態 | 說明 |
|---|---|---|
| `GET /v1/trip` 取得 Dashboard 資料 | ✅ 完成 | 異常狀態與理賠資料目前只有 seed 假資料,見下方「已知限制」 |
| `PATCH /v1/trip/claim/checklist` 更新理賠 checklist | ✅ 完成 | |
| Cognito 登出 | — | 前端直接呼叫 Amplify,後端不需開發 |
| `POST /v1/push/subscriptions` 訂閱推播 | ⬜ 未開始 | 需新的 Lambda `push-subscription`、訂閱資料表、VAPID 金鑰 |
| `DELETE /v1/push/subscriptions/{subscriptionId}` 取消訂閱 | ⬜ 未開始 | 同上 |
| Web Push 發送 | ⬜ 未開始 | 由事件驅動流程在旅程狀態改變後發送,需先完成航班監控 |

### 07 AI 對話頁

| API | 狀態 |
|---|---|
| `GET /v1/conversations` 對話列表 | ⬜ 未開始 |
| `GET /v1/conversations/{conversationId}` 對話內容 | ⬜ 未開始 |
| `POST /v1/chat/messages` 送出訊息 | ⬜ 未開始 |
| `PATCH /v1/conversations/{conversationId}/tasks/{taskId}/steps/{stepId}` 勾選 checklist step | ⬜ 未開始 |
| `DELETE /v1/conversations/{conversationId}` 刪除對話 | ⬜ 未開始 |

### 已知限制

- **航班監控尚未實作**:沒有流程會把旅程改成 `ABNORMAL`、寫入延誤資訊或建立理賠案件。目前 `GET /v1/trip` 對新建立的旅程一律回 `NORMAL`;要測試異常畫面請使用 seed 的 `user-003`。
- **推播尚未實作**:前端在推播上線前,以 Dashboard 初始化時呼叫 `GET /v1/trip` 取得最新狀態。
- **機場只支援部分地區**:目的地當地時間以固定 UTC 時差計算,目前只放台灣、日本、韓國、香港、泰國、新加坡等無日光節約時間的機場。
- **航空公司客服電話只有 BR**,其他航空公司回 `null`。

## API 規格文件

| 檔案 | 說明 |
|---|---|
| `05-journey-start-v2.md` | 開始旅程頁 **新版規格**(含錯誤回應、驗證規則、覆蓋流程),**前端請以此為準** |
| `06-dashboard-v2.md` | 旅程主頁 **新版規格**(含錯誤回應、欄位計算規則、checklist 限制),**前端請以此為準** |
| `05-journey-start.md`、`06-dashboard.md`、`07-chat.md` | 原始規格,保留不修改 |

錯誤回應統一格式:

```json
{ "error": "trip_already_exists", "message": "已經有進行中的旅程" }
```

## 專案結構

```
.
├── trip_management.py                Lambda trip-management(05、06 的 4 支 API)
├── test_trip_management.py           單元測試(45 個,不連 AWS)
├── seed_dynamodb.py                  建立 DynamoDB 表並寫入假資料(在 CloudShell 執行)
├── trip-management-console-tests.md  Lambda console 手動測試腳本
├── 05/06/07-*.md                     API 規格(見上方)
├── legacy/                           舊版程式,已不使用,僅供參考
└── CLAUDE.md                         給 Claude Code 的專案說明
```

## DynamoDB 資料表

| 資料表 | 主鍵 | 用途 |
|---|---|---|
| `Trips` | PK `sub` | 每位使用者一筆目前旅程,含航班與監控狀態、理賠資料 |
| `Policies` | PK `sub` | 保單清單(要保人、保障期間、保障項目) |
| `Flights` | PK `flight_no`、SK `departure_date` | 航班資料(起訖機場、表定/預計時間、登機門) |
| `Weather` | PK `city`、SK `forecast_time` | 目的地天氣,每 3 小時一筆(假資料只有日本 4 個城市) |
| `ExchangeRates` | PK `currency` | 匯率(1 TWD 可換多少外幣) |

全部使用 On-demand(`PAY_PER_REQUEST`)計費。

## 本機開發

需要 Python 3.12。單元測試使用假的資料表物件,不需要 AWS 帳號,也不需要安裝 boto3。

```bash
python -m unittest test_trip_management -v
```

修改程式後請先跑單元測試。

## 部署 trip-management

所有資源都建在 **`us-east-1`**(維吉尼亞北部),操作前請確認 console 右上角的 region。

以 console 手動部署,詳細步驟與測試 event 見 [trip-management-console-tests.md](trip-management-console-tests.md)。

1. **建表與假資料**:在 CloudShell 上傳並執行 `python3 seed_dynamodb.py`。
2. **建立 Lambda**:Runtime Python 3.12,把 `trip_management.py` 的內容貼到 `lambda_function.py`,Handler 維持 `lambda_function.lambda_handler`。
3. **一般設定**:Memory 512 MB、Timeout 10 秒(預設 128 MB / 3 秒冷啟動會逾時)。
4. **環境變數**:

   | Key | Value |
   |---|---|
   | `TRIP_TABLE` | `Trips` |
   | `POLICY_TABLE` | `Policies` |
   | `FLIGHT_TABLE` | `Flights` |
   | `WEATHER_TABLE` | `Weather` |
   | `EXCHANGE_RATE_TABLE` | `ExchangeRates` |

5. **IAM**:execution role 加上 inline policy。Trips 需要 `GetItem`、`PutItem`、`UpdateItem`;Weather 需要 `Query`;Policies、Flights、ExchangeRates 需要 `GetItem`。
6. **測試**:依測試腳本 ①〜⑩ 執行。

### 測試帳號(seed 假資料)

| sub | 情境 |
|---|---|
| `user-001` | 正常狀態旅程 |
| `user-002` | 沒有旅程 |
| `user-003` | 異常狀態旅程,延誤 5 小時,已達理賠門檻並立案 |

## 安全注意事項

- 不要把 AWS 憑證或金鑰寫進程式或 commit。
- 在開發用 AWS 帳號操作,並設定 AWS Budgets 預算警示。
