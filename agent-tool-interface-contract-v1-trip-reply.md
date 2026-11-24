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

## 2. 輸入與回傳格式

### 2.1 Input schema(**比合約 3.2 多了選填的 `weatherCity`**)

Gateway 註冊時請使用這份:

```json
{
  "name": "get-custom-info",
  "description": "查詢目前已授權旅客的旅程相關資訊,包括航班、航班異常狀態、天氣,以及資料服務可提供的保單摘要。天氣預設為旅程目的地;使用者詢問其他城市時填 weatherCity。僅在使用者需要相關資訊時呼叫,不用於正式理賠核定。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "sections": {
        "type": "array",
        "description": "這次需要取得的資訊類別",
        "items": {
          "type": "string",
          "enum": ["flight", "flight_status", "weather", "policy_summary"]
        },
        "minItems": 1,
        "uniqueItems": true
      },
      "weatherCity": {
        "type": "string",
        "enum": ["東京", "大阪", "福岡", "沖繩"],
        "description": "選填。使用者詢問旅程目的地以外的城市天氣時填寫,sections 須包含 weather。不填則回傳旅程目的地的天氣。不在清單內的城市目前不支援。"
      }
    },
    "required": ["sections"],
    "additionalProperties": false
  }
}
```

示例:「大阪天氣如何?」

```json
{"sections": ["weather"], "weatherCity": "大阪"}
```

- 只要求 `weather` 且有填 `weatherCity` 時,不需要旅程資料,沒有旅程的使用者也能查詢;仍需可信的使用者身分。
- `weatherCity` 用固定選項,讓模型從 schema 就知道支援哪些城市,不會猜測或填入英文名稱。新增城市時需同步更新此 enum。

### 2.2 回傳

以下為四個 sections 全部要求、未填 `weatherCity` 時的回傳(異常旅程測試帳號):

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
      "productName": "豪華定額型",
      "projectCode": "U3",
      "policyNumberMasked": "TA-2026****-03",
      "statusText": "保障生效中",
      "plan": {
        "notCovered": ["行動電話被竊損失費用", "租車事故慰問", "特殊活動取消慰問", "寵物寄宿延長補償費用"],
        "notes": ["保障內容為加保保額固定；僅載列部分重要理賠限制事項，其他未載列事項請詳閱保險單條款"],
        "coverageGroups": [
          {
            "productMainId": "P41",
            "name": "海外旅行不便保險",
            "items": [
              {
                "subId": "2I", "name": "班機延誤費用", "nameEn": "Flight Delay", "amount": 12000,
                "currency": "NTD", "payoutMethod": "定額",
                "note": "每四小時 6,000 元,每次最高 12,000 元, 最多可理賠2次",
                "restrictions": ["因戰爭造成班機延誤或取消(如美伊戰爭、以伊戰爭、俄烏戰爭等)，無法申請", "…共 6 條"]
              }
            ]
          }
        ]
      }
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
- `policySummary.plan` 是保單所屬方案(Policies 的 `project_code` 對應 Plans 表)的保障內容,來源是公司正式的方案資訊 CSV,依保障類別分組(上面範例只列一項)。查無方案時 `plan` 為 `null`。取代合約建議的 `coverages[]`。每個項目:
  - `amount`:`null` 代表沒有固定金額(依條款約定),不是 0,模型不可自行推測金額。
  - `payoutMethod`:給付方式,如 `定額`(固定金額)、`限額`(以金額為上限)、`日額`、`日限額`、`限定額`。
  - `note`:理賠方式說明,如「最多可理賠 2 次」「最高給付 10 日」「每四小時 6,000 元,每次最高 12,000 元」「每一事故自負額 2,500 元」「不含傳染病及檢疫」。沒有 `note` 時不代表沒有次數或日數上限(例如意外險、突發疾病尚無這類資料)。
  - `restrictions`:不給付或限制給付的情況(旅程取消 2 條、班機延誤 6 條)。
  - `productName` 是方案名稱(Plans 的 `plan_name`),`projectCode` 是保單的方案代碼;兩者在 `plan` 之外。`plan` 內為方案層的 `notes`(含「僅載列部分重要限制,其他請詳閱條款」,Agent 應提醒以保單條款為準);`notCovered` 是這個方案沒有的項目,被問到時應回答未承保。

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

### 4.2 使用者問的城市不是旅程目的地時(已由旅程管理端處理)

驗收案例「東京天氣如何?」若旅客的目的地是大阪,原本的 schema 只能回傳大阪的天氣。旅程管理端已新增選填參數 `weatherCity`(見 2.1),模型應填入使用者問的城市。

請 Agent 端確認:

- 使用者問的城市在 enum 內 → 填 `weatherCity`。
- 不在 enum 內(例如首爾)→ 不呼叫天氣,直接說明目前不支援該城市,不可用其他城市的天氣代替。
- 回傳的 `weather.city` 一律代表實際查詢的城市,回答時以此為準。

## 5. 合約未規定、旅程管理端自行決定的行為

| 情境 | 行為 |
|---|---|
| 沒有可信身分 | `UNAUTHORIZED`,不改用預設帳號 |
| event 有 `sections`、`weatherCity` 以外的 key(例如 `userId`) | `INVALID_ARGUMENT`。即使 Gateway schema 已設 `additionalProperties: false`,Lambda 仍再檢查一次 |
| `sections` 為空、重複或不在允許清單 | `INVALID_ARGUMENT` |
| `weatherCity` 不在清單,或 `sections` 沒有 `weather` | `INVALID_ARGUMENT` |
| 沒有目前旅程 | `NO_ACTIVE_TRIP` |
| 要求 `flight`,或要求 `weather` 但沒填 `weatherCity`,且查無航班資料 | `NOT_FOUND`(天氣需要航班資料才知道目的地) |
| 只要求 `flight_status` | 不查航班資料,所以航班資料有問題也能回傳狀態 |
| DynamoDB 查詢失敗 | `SERVICE_UNAVAILABLE`,`retryable: true` |
| 程式非預期錯誤 | 不轉成業務錯誤,直接由 Lambda 平台回報(依合約第 5 節,區分平台例外與業務錯誤) |

錯誤的 `message` 為中文,供 Agent 參考,不建議直接顯示給使用者。

## 6. 需要 Agent／Gateway 端協助

1. 將 `get-custom-info` 註冊為 Gateway Lambda Target,input schema 使用本文件 2.1(含 `weatherCity`)。
2. Gateway 的 IAM role 需要對此 Lambda 的 `lambda:InvokeFunction` 權限。
3. 整合後執行一次呼叫,提供 Lambda log 中的 `tool=` 與 `event_keys=`,確認 event 格式(#7)。
4. 決定身分傳遞方式(#6)與天氣時間點(4.1),並確認 4.2 的 Agent 行為。

另外,合約文件目前沒有第 2 節(第 1 節之後直接是第 3 節),請確認是否漏掉內容。
