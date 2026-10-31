# 旅遊理賠助手(個人 side project)

## 關於使用者
- Patty,資料工程師,AWS 與 Lambda 初學者。請用繁體中文,技術名詞中英並列,一步一步帶,先解釋再動手。
- 個人專案,不用 Terraform。目前手動部署(console / AWS CLI),之後可能改 SAM。

## 整體架構(摘要)
- 前端:CloudFront + S3 靜態網頁,WAF,Cognito(JWT)。API Gateway 用 Cognito Authorizer,依路徑轉到 Lambda(旅程管理)與 Lambda(AI Adapter)。
- AI:Lambda(AI Adapter)呼叫 Bedrock AgentCore Runtime;Gateway 以 MCP 呼叫工具 Lambda;Knowledge Base 放保單條款。
- 事件驅動(與 Bedrock 無關):EventBridge Scheduler → CheckFlight → EventBridge Bus → ClaimEligible → EventBridge Bus → Notification → SNS。航班監控狀態存 DynamoDB。

## 目前進度:Lambda「旅程管理」
- 檔案:`trip_management.py`(Lambda)、`test_trip_management.py`(16 個單元測試)、`seed_dynamodb.py`(建表+假資料)。
- 執行測試:`python -m unittest test_trip_management -v`
- 使用者身分只從 API Gateway 驗證後的 `requestContext.authorizer.claims.sub` 取得,不接受 body / query 帶入的 ID。
- 保單與天氣原本是外部 API,為了學習改成 DynamoDB 假資料表,之後可能換回真 API。
- DynamoDB 資料表:
  - Policies:PK `sub`,屬性 `policies`(保單清單,內含 `itinerary`:flight_no / departure_date / destination 城市字串)
  - Flights:PK `flight_no`,SK `departure_date`,屬性 status / delay_minutes / scheduled_departure / last_updated / last_notified_level(內部欄位,不回傳前端)
  - Weather:PK `city`,屬性 summary / temperature_c
- Lambda 環境變數:`POLICY_TABLE`、`FLIGHT_TABLE`、`WEATHER_TABLE`
- IAM:Policies 需 GetItem;Flights、Weather 需 BatchGetItem。
- 回應:200(`data` / `errors` / `partial`)、401、404 policyholder_not_found、502 policy_unavailable、500 internal_error。航班或天氣失敗回部分結果。
- 測試用 event:`{"requestContext":{"authorizer":{"claims":{"sub":"user-001"}}}}`;`user-002` 沒資料,測 404。

## 下一步
1. 在 CloudShell 跑 `seed_dynamodb.py` 建表(若尚未做)。
2. 在 console 建 Lambda、設環境變數與 IAM 權限、跑測試 event、看 CloudWatch Logs。
3. 之後:航班狀態、理賠規則(結構化規則表,商品代碼+版本)、診所資訊、事件驅動 Lambda。

## 約定
- 修改程式後要跑單元測試。
- 不要把 AWS 憑證或金鑰寫進程式或 commit。
- 先在開發用 AWS 帳號操作,並設定 AWS Budgets。
