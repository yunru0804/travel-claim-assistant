# 旅遊理賠助手(個人 side project)

## 關於使用者
- Patty,資料工程師,AWS 與 Lambda 初學者。請用繁體中文,技術名詞中英並列,一步一步帶,先解釋再動手。
- 個人專案,不用 Terraform。目前手動部署(console / AWS CLI),之後可能改 SAM。

## 整體架構(摘要)
- 前端:CloudFront + S3 靜態網頁,WAF,Cognito(JWT)。API Gateway 用 Cognito Authorizer,依路徑轉到 Lambda(旅程管理)與 Lambda(AI Adapter)。
- AI:Lambda(AI Adapter)呼叫 Bedrock AgentCore Runtime;Gateway 以 MCP 呼叫工具 Lambda;Knowledge Base 放保單條款。
- 事件驅動(與 Bedrock 無關):EventBridge Scheduler → CheckFlight → EventBridge Bus → ClaimEligible → EventBridge Bus → Notification → SNS。航班監控狀態存 DynamoDB。

## API 規格
- `05-journey-start.md`(開始旅程頁)、`06-dashboard.md`(旅程主頁)、`07-chat.md`(AI 對話頁)。
- Lambda 分組:`trip-management`(`/v1/trip*`)、`push-subscription`、`chat-send`(`POST /v1/chat/messages`)、`conversation-management`(其餘 `/v1/conversations*`)。每支 Lambda 內用 `(httpMethod, resource)` 分派路由。
- `legacy/`:舊版程式(Policies / Flights / Weather 假資料版),已不使用,僅供參考。

## 目前進度:Lambda「旅程管理」(新版,依 05、06 規格)
- 檔案:`trip_management.py`(Lambda)、`test_trip_management.py`(43 個單元測試)、`seed_dynamodb.py`(建表+假資料)。
- 執行測試:`python -m unittest test_trip_management -v`
- 已完成:`POST /v1/trip`、`PUT /v1/trip/flight`、`GET /v1/trip`、`PATCH /v1/trip/claim/checklist`。尚未完成：推播訂閱(`push-subscription` Lambda)、Web Push 發送。
- 使用者身分只從 API Gateway 驗證後的 `requestContext.authorizer.claims.sub` 取得,不接受 body / query 帶入的 ID。
- DynamoDB 資料表(Policies / Flights / Weather 沿用 legacy 表名與主鍵，屬性改新版):
  - Trips:PK `sub`,屬性 flight_number / departure_date / created_at / updated_at,以及事件驅動流程寫入的 trip_status / disruption / delay_claim。讀取用 ConsistentRead(POST 後立即 GET)。
  - Policies:PK `sub`,屬性 `policies`(policy_number / policyholder_name / product_name / coverage_start / coverage_end / coverages)
  - Flights:PK `flight_no`,SK `departure_date`,屬性 origin_airport / destination_airport / scheduled_departure / scheduled_arrival / estimated_arrival / terminal_gate_text / baggage_carousel_text
  - Weather:PK `city`(中文城市名),屬性 condition_code / temperature_c / rain_probability_percent
  - ExchangeRates:PK `currency`,屬性 rate_per_twd
- 機場(城市、國家、固定 UTC 時差)、國家→幣別、航空公司電話、support 連結寫在程式常數。不用 zoneinfo(Lambda 不一定有時區資料),只放無日光節約的地區。
- 一位使用者只存一筆旅程，不保留歷史。PUT 整筆覆蓋(含監控狀態、理賠資料),等同換成新旅程。POST 遇到已有旅程回 409,由前端詢問是否覆蓋，確認後改呼叫 PUT;PUT 沒有旅程回 404,前端改呼叫 POST。
- 出發日期只接受今天以後;「今天」以 UTC-12 計算，避免誤擋其他時區當天出發的航班。建立旅程時不檢查航班是否存在。
- GET:displayName 取保單的要保人姓名;保單取保障期間涵蓋出發日期的第一張，沒有則 404 policy_not_found;查無航班 502 flight_unavailable;天氣、匯率失敗回 null。
- PATCH checklist:label 數量與順序須與後端一致，否則 409 checklist_mismatch;未達理賠門檻 409 claim_not_eligible。寫入用條件式只改 is_ready。
- Lambda 環境變數:`TRIP_TABLE`、`POLICY_TABLE`、`FLIGHT_TABLE`、`WEATHER_TABLE`、`EXCHANGE_RATE_TABLE`。IAM:Trips 需 GetItem / PutItem / UpdateItem;其他四張表需 GetItem。
- 錯誤回應格式:`{"error": "<code>", "message": "<中文訊息>"}`。400 invalid_request / departure_date_in_past、401 unauthorized、404 trip_not_found / policy_not_found / route_not_found、409 trip_already_exists / claim_not_eligible / checklist_mismatch、502 trip_unavailable / policy_unavailable / flight_unavailable、500 internal_error。
- 測試帳號(seed):user-001 正常旅程、user-002 無旅程、user-003 異常且已立案。

## 已部署(2026-11-01,舊版,尚未換成新版)
- 已用 CloudShell 跑 `seed_dynamodb.py` 建好三張表與假資料。
- Lambda `trip-management`(Python 3.x,console 手動建立,程式貼在 `lambda_function.py`):已設環境變數與 inline policy `trip-management-dynamodb`,`user-001`(200)與 `user-002`(404)測試通過。
- 預設 128 MB / 3 秒冷啟動會逾時,已調成 Memory 512 MB、Timeout 10 秒。

## 下一步
1. API Gateway(REST)+ Cognito Authorizer 接到 `trip-management`。
2. 之後:航班狀態、理賠規則(結構化規則表,商品代碼+版本)、診所資訊、事件驅動 Lambda。

## 約定
- 修改程式後要跑單元測試。
- 不要把 AWS 憑證或金鑰寫進程式或 commit。
- 先在開發用 AWS 帳號操作,並設定 AWS Budgets。
