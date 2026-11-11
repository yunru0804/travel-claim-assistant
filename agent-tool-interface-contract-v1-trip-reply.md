# 旅程管理端回覆:Agent ↔ Tool Interface Contract v1

針對 [agent-tool-interface-contract-v1.md](agent-tool-interface-contract-v1.md)(v1.0-draft)中與旅程管理有關的部分,說明 `get-custom-info` 目前的實作、第 10 節待定項目的建議,以及需要 Agent／Gateway 端協助確認的事項。

## 1. 目前狀態

| 項目 | 內容 |
|---|---|
| Tool | `get-custom-info`(Mock 版已完成) |
| Lambda | `get-custom-info`,`us-east-1`,Python 3.12 |
| 資料 | DynamoDB seed 假資料,回傳 `source.isMock: true` |
| 身分 | 暫用 Lambda 環境變數 `MOCK_SUB` 固定一位測試使用者(見第 3 節 #6) |
| 權限 | Lambda 只有 DynamoDB 讀取權限,無法寫入任何資料 |
| 尚未完成 | 註冊為 Gateway Target、端對端身分傳遞 |

`get-clinic-info` 不在旅程管理範圍,本文件不涉及。

## 2. 實際回傳格式

輸入 schema 與合約 3.2 相同。以下為四個 sections 全部要求時的回傳(異常旅程測試帳號):

```json
{
  "schemaVersion": "1.0",
  "status": "success",
  "source": { "system": "trip_management", "isMock": true, "retrievedAt": "2026-11-20T06:00:00Z" },
  "data": {
    "flight": {
      "flightNumber": "CI100",
      "departureDate": "2026-11-20",
      "originAirportCode": "TPE",
      "originCity": "台北",
      "destinationAirportCode": "NRT",
      "destinationCity": "東京",
      "scheduledDeparture": "2026-11-20T10:15:00+08:00",
      "scheduledArrival": "2026-11-20T14:30:00+09:00",
      "estimatedArrival": "2026-11-20T19:30:00+09:00",
      "terminalGateText": "第一航廈・登機門 B6",
      "baggageCarouselText": "行李轉盤 4 號"
    },
    "flightStatus": { "state": "ABNORMAL", "delayMinutes": 300, "updatedAt": "2026-11-20T05:15:00Z" },
    "weather": {
      "city": "東京",
      "conditionCode": "SUNNY",
      "temperatureCelsius": 16.2,
      "rainProbabilityPercent": 10,
      "forecastTime": "2026-11-20T06:00:00Z",
      "updatedAt": "2026-11-11T14:53:44Z"
    },
    "policySummary": {
      "productName": "國泰安心遊日本 5 日型",
      "policyNumberMasked": "TA-2026****-03",
      "statusText": "保障生效中",
      "coverages": [
        { "label": "海外突發醫療", "amount": 1000000 },
        { "label": "班機延誤保險金", "amount": 5000 }
      ]
    }
  },
  "error": null
}
```

欄位說明:

- `data` 只包含本次 `sections` 要求的 key;未要求的類別不會查詢,也不會出現。
- `flight` 比合約範例多了城市、表定／預計時間、登機門、行李轉盤。時間為含時差的 ISO 格式(出發地／目的地當地時間),模型可直接回答「幾點抵達」。`estimatedArrival` 沒有時為 `null`。
- `weather` 多了 `city`(讓模型知道是哪個城市)與 `forecastTime`(這筆天氣代表的時間點)。`updatedAt` 為資料寫入時間,與合約 3.3 的 `retrievedAt` 區分。
- `conditionCode` 與前端 Dashboard 相同:`SUNNY`、`PARTLY_CLOUDY`、`CLOUDY`、`RAIN`、`THUNDERSTORM`、`SNOW`。

## 3. 第 10 節待定項目:旅程管理端建議

| # | 項目 | 建議 |
|---|---|---|
| 1 | `policy_summary` 是否提供、資料來源 | **提供**。來源為保單資料(Policies),取保障期間涵蓋出發日期的那張;保單號碼已遮罩,不回完整號碼與要保人姓名。沒有涵蓋出發日的保單時 `policySummary` 為 `null`。 |
| 2 | Tool 直接讀 DB,還是共用旅程管理服務 | **直接讀 DB,共用程式碼**。Tool Lambda 與旅程管理 Lambda 共用同一份資料存取與組裝邏輯(`trip_management.py`),不透過 HTTP 呼叫旅程管理 API。原因:Gateway 的 event 與輸出格式都和 API Gateway 不同;共用程式碼可確保兩邊的狀態判斷與遮罩規則一致。 |
| 3 | `flightStatus` 欄位與新鮮度 | 欄位依合約:`state`(`NORMAL`／`ABNORMAL`)、`delayMinutes`(正常為 0)、`updatedAt`。**限制**:航班監控(事件驅動流程)尚未實作,目前 `NORMAL` 的 `updatedAt` 是旅程最後修改時間,不是最後一次檢查航班的時間。監控上線後改為實際檢查時間。 |
| 4 | 天氣缺值、是否需要 `partial_success` | **建議不需要 `partial_success`**。天氣查詢失敗或沒有近 3 小時的資料時,`weather` 為 `null`,`status` 仍為 `success`,其他類別照常回傳。與前端 Dashboard 的行為一致。Agent 看到 `weather: null` 時應回答「目前無法取得天氣」,不可自行推測。 |
| 6 | 使用者身分如何可信地傳入 | **需 Gateway 端決定**。旅程管理端的需求:Tool Lambda 必須從**可信來源**取得 Cognito `sub`,不能來自模型參數。目前取得身分的邏輯集中在一個函式,方式定案後只需修改該處。在此之前 Tool 只能使用 Mock 身分,不可接正式資料。 |
| 7 | Gateway event、輸出封裝是否相容 | **需 Gateway 端驗證**。目前假設 event 即為 Tool input(`{"sections": [...]}`),回傳為上述 envelope(JSON 物件)。Lambda log 會記錄 event 的 key 與 `bedrockAgentCoreToolName`(不記錄個資),整合時可用來確認實際格式。 |

#5(診所資料維護)、#8(版本流程)不在旅程管理範圍。

## 4. 建議新增的待定項目

### 4.1 天氣是指哪個時間點?

合約只寫「目的地天氣」。目前實作回傳**目的地現在的天氣**(現在往前 3 小時內最新一筆)。另一種解讀是「抵達時」或「出發日」的天氣預報,對尚未出發的旅客可能更有用。請確認要哪一種;若要預報,需另外定義欄位。

### 4.2 使用者問的城市不是旅程目的地時

驗收案例「東京天氣如何?」若旅客的目的地是大阪,Tool 會回傳大阪的天氣(`city: "大阪"`)。Tool input 沒有城市參數,所以無法查任意城市。

建議 Agent 端比對 `weather.city` 與使用者問的城市,不一致時明確說明「你的目的地是大阪」,不要把大阪的天氣當成東京的天氣回答。若需要查任意城市,需在 input schema 增加參數,並另外討論。

## 5. 合約未規定、旅程管理端自行決定的行為

| 情境 | 行為 |
|---|---|
| 沒有可信身分 | `UNAUTHORIZED`,不改用預設帳號 |
| event 有 `sections` 以外的 key(例如 `userId`) | `INVALID_ARGUMENT`。即使 Gateway schema 已設 `additionalProperties: false`,Lambda 仍再檢查一次 |
| `sections` 為空、重複或不在允許清單 | `INVALID_ARGUMENT` |
| 沒有目前旅程 | `NO_ACTIVE_TRIP` |
| 要求 `flight` 或 `weather`,但查無航班資料 | `NOT_FOUND`(天氣需要航班資料才知道目的地) |
| 只要求 `flight_status` | 不查航班資料,所以航班資料有問題也能回傳狀態 |
| DynamoDB 查詢失敗 | `SERVICE_UNAVAILABLE`,`retryable: true` |
| 程式非預期錯誤 | 不轉成業務錯誤,直接由 Lambda 平台回報(依合約第 5 節,區分平台例外與業務錯誤) |

錯誤的 `message` 為中文,供 Agent 參考,不建議直接顯示給使用者。

## 6. 需要 Agent／Gateway 端協助

1. 將 `get-custom-info` 註冊為 Gateway Lambda Target,input schema 使用合約 3.2。
2. Gateway 的 IAM role 需要對此 Lambda 的 `lambda:InvokeFunction` 權限。
3. 整合後執行一次呼叫,提供 Lambda log 中的 `tool=` 與 `event_keys=`,確認 event 格式(#7)。
4. 決定身分傳遞方式(#6),以及第 4 節的兩個新問題。

另外,合約文件目前沒有第 2 節(第 1 節之後直接是第 3 節),請確認是否漏掉內容。
