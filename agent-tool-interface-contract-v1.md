# 旅行保險 AI 助手 — Agent ↔ Tool Interface Contract v1
## 1. Tool 清單

| Gateway Tool name | 用途 | 資料來源 |
|---|---|---|
| `get-custom-info` | 查詢已授權旅客的航班、異常狀態、旅遊地天氣、可用的保單摘要 | 旅程管理／保單服務，現階段可用 Mock |
| `get-clinic-info` | 依使用者目前所在地、科別及語言偏好尋找診所／醫院 | 診所服務，現階段可用 Mock |


## 3. Tool 1 — `get-custom-info`

### 3.1 何時呼叫

使用者詢問本人旅程、航班、航班狀態、旅遊目的地天氣或已授權保單摘要時呼叫。不可用此 Tool 的原始資料直接作正式核賠判定。使用者只問診所時，不應無理由額外取得完整保單。

### 3.2 Gateway Input Schema（建議）

```json
{
  "name": "get-custom-info",
  "description": "查詢目前已授權旅客的旅程相關資訊，包括航班、航班異常狀態、目的地天氣，以及資料服務可提供的保單摘要。僅在使用者需要相關資訊時呼叫，不用於正式理賠核定。",
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
      }
    },
    "required": ["sections"],
    "additionalProperties": false
  }
}
```

示例：詢問班機延誤

```json
{"sections": ["flight", "flight_status"]}
```

示例：詢問東京天氣

```json
{"sections": ["weather"]}
```

**請勿**將 `userId`、`actorId`、`cognitoSub` 或任意保單號碼設為由模型填寫的授權參數。

### 3.3 建議成功回傳（Mock 值僅供示意）

```json
{
  "schemaVersion": "1.0",
  "status": "success",
  "source": {
    "system": "trip_management",
    "isMock": true,
    "retrievedAt": "2026-11-11T02:00:00Z"
  },
  "data": {
    "flight": {
      "flightNumber": "BR198",
      "departureDate": "2026-12-15",
      "originAirportCode": "TPE",
      "destinationAirportCode": "NRT"
    },
    "flightStatus": {
      "state": "NORMAL",
      "delayMinutes": 0,
      "updatedAt": "2026-11-11T01:55:00Z"
    }
  },
  "error": null
}
```

**輸出規則：**

- `data` 只回傳本次請求 `sections` 所需欄位；例子未包含 `weather` 與 `policy_summary`，並非表示兩者不能查。
- `flightStatus.state` 對齊旅程管理既有狀態 `NORMAL`／`ABNORMAL`，不可由模型自行判斷。
- 天氣資料至少須包含 `conditionCode`、`temperatureCelsius`、`rainProbabilityPercent`、`updatedAt`（資料來源若缺值，需先議定 null 與部分成功語意）。
- `policy_summary` 若可提供，建議使用 `productName`、`policyNumberMasked`、`statusText`、`coverages[]`，避免提供完整保單號碼。
- `retrievedAt` 代表本次讀取時間；`updatedAt` 代表該資料本身最後更新時間，兩者不能混用。
- `flightStatus` 此處是 Tool 精簡結構草案，並非強制沿用前端 Dashboard 的完整欄位。以旅程管理團隊核定的欄位為準。

## 4. Tool 2 — `get-clinic-info`

### 4.1 何時呼叫

使用者需要就醫、尋找附近診所或醫院時使用。**登錄的旅遊目的地不等於即時所在地**，位置不足時應追問區域；緊急症狀優先建議當地急救。

### 4.2 Gateway Input Schema（建議）

```json
{
  "name": "get-clinic-info",
  "description": "依使用者已提供的目前所在地查詢附近醫療院所；可指定科別與就診語言偏好。不得將旅遊目的地直接當成目前所在地。",
  "inputSchema": {
    "type": "object",
    "properties": {
      "location": {
        "type": "string",
        "description": "目前所在地，例如 Tokyo Shinjuku"
      },
      "specialty": {
        "type": "string",
        "enum": ["general", "internal_medicine", "emergency", "other"],
        "description": "需要的醫療科別，選填"
      },
      "language": {
        "type": "string",
        "enum": ["zh", "en", "ja", "any"],
        "description": "偏好的就診語言，選填"
      }
    },
    "required": ["location"],
    "additionalProperties": false
  }
}
```

呼叫示例：

```json
{
  "location": "Tokyo Shinjuku",
  "specialty": "internal_medicine",
  "language": "zh"
}
```

### 4.3 建議成功回傳（Mock 值僅供示意）

```json
{
  "schemaVersion": "1.0",
  "status": "success",
  "source": {
    "system": "clinic_service",
    "isMock": true,
    "retrievedAt": "2026-11-11T02:00:00Z"
  },
  "data": {
    "queriedLocation": "Tokyo Shinjuku",
    "clinics": [
      {
        "clinicId": "CLINIC001",
        "name": "Mock Tokyo Clinic",
        "address": "Mock address, Tokyo",
        "specialties": ["internal_medicine"],
        "languages": ["ja", "en"],
        "isPartner": null,
        "openingStatus": "UNKNOWN"
      }
    ]
  },
  "error": null
}
```

- `isPartner` 必須有可信合作院所清單支持；未知回傳 `null`。
- `openingStatus` 沒有即時資訊時用 `UNKNOWN`，不可假設營業中。
- 沒有可靠證據時，不可聲稱支援中文、英語或特定科別。
- POC 若只有東京資料，可回傳查無結果／不支援區域，不可捏造其他地點。

## 5. 共用回傳 Envelope 與錯誤規範

每個工具固定使用以下五個頂層欄位：

| 欄位 | 型別 | 規範 |
|---|---|---|
| `schemaVersion` | string | 目前 `1.0` |
| `status` | string | `success`／`error`；部分成功要另行議定 |
| `source` | object | `system`、`isMock`、`retrievedAt` |
| `data` | object/null | 成功時為對應業務資料，錯誤時為 `null` |
| `error` | object/null | 成功為 `null`，錯誤時有 `code`、`message`、`retryable` |

錯誤示例：

```json
{
  "schemaVersion": "1.0",
  "status": "error",
  "source": {
    "system": "trip_management",
    "isMock": false,
    "retrievedAt": "2026-11-11T02:00:00Z"
  },
  "data": null,
  "error": {
    "code": "NO_ACTIVE_TRIP",
    "message": "目前沒有可查詢的旅程",
    "retryable": false
  }
}
```

| 錯誤碼 | 用途 | 建議 retryable |
|---|---|---|
| `INVALID_ARGUMENT` | 缺少、格式錯誤或不合法的參數 | `false` |
| `NO_ACTIVE_TRIP` | 旅客沒有目前旅程 | `false` |
| `NOT_FOUND` | 無符合條件的院所或資料 | `false` |
| `UNAUTHORIZED` | 未通過身分或資料歸屬授權 | `false` |
| `DATA_STALE` | 資料過期且不能作為當前狀態 | 視是否可更新而定 |
| `SERVICE_UNAVAILABLE` | 下游服務暫時失敗 | `true` |

Lambda 的 transport／平台例外與上述業務回傳錯誤應區分；**不要把 `statusCode: 200` 當成業務查詢成功的唯一訊號**。

## 6. Cognito、IAM 與授權界線（必須確認）

目前前端使用 Cognito Access Token，API Gateway 對外驗證 JWT；Chat Lambda 使用 IAM / SigV4 呼叫 Harness。這**不表示** Cognito 終端使用者身分自動傳達 Gateway Target。

- Cognito `sub` 由已驗證的後端上下文取得，**不**由 Agent 填入 Tool Input。
- Harness 的 `actorId`／Session ID 只用於記憶與對話識別，**不能作為授權憑證**。
- Gateway Target／旅程管理服務須有可信的身分傳遞與資料歸屬驗證方法；正式資料上線前必須完成端對端驗證。
- 缺少可信身分時回傳 `UNAUTHORIZED`，不能 fallback 到 `default_actor` 讀取正式資料。
- 此處不假設 Gateway 自動注入 Cognito `sub` 到 Lambda `event`；請依實際 Harness／Gateway 授權配置設計並驗證。

**待決策：** 保留 IAM 並額外建立可信身分關聯，或正式整合前評估 JWT／AgentCore Identity 的使用者身分傳遞。

## 7. Knowledge Base 與 ClaimRule 範圍

- 本版暫時**不建立** `ClaimRule Tool`。
- KB 專責保單條款、一般理賠條件及文件要求的檢索與引用。
- 個人保單資訊應由已授權的個人資料服務取得；KB 不替代個人保障查詢。
- 正式理賠資格／核賠結果不可僅憑模型對 KB 文本的解讀直接確定；應先透過測試集評估 KB 能力，再決定是否需規則服務。

## 8. Gateway ↔ Lambda 技術約定

- Gateway 註冊的 Tool input schema 為模型可選擇的**業務參數**。
- Lambda Target 的 `event` 結構需依實際 Gateway Target 方式驗證，**不要假設是 API Gateway HTTP event**。
- Tool Lambda 須輸出有效 JSON，並保持同一版本格式一致。
- 對敏感欄位應遮罩、採最小回傳原則；不得把完整保單／個資直接寫入一般應用日誌。
- 實際 Tool 名稱、Target 名稱、Lambda ARN、IAM 權限及錯誤行為，須在整合測試時確認。

## 9. 接口驗收案例

| 案例 | Agent 預期呼叫 | Tool 預期結果 |
|---|---|---|
| 「我的航班有延誤嗎？」 | `get-custom-info`，`["flight", "flight_status"]` | 航班狀態＋更新時間 |
| 「東京天氣如何？」 | `get-custom-info`，`["weather"]`（有旅程身份上下文） | 天氣＋更新時間 |
| 「我在新宿發燒，找診所」 | `get-clinic-info` | 院所清單或查無資料 |
| 「我的航班和天氣如何？」 | `get-custom-info`，兩類 sections | 只回傳指定類別 |
| 尚未登入而查個人保單 | 不得返回個人資料 | `UNAUTHORIZED` |
| 查無目前旅程 | `get-custom-info` | `NO_ACTIVE_TRIP` |
| Tool 下游暫時不可用 | 對應 Tool | `SERVICE_UNAVAILABLE`，Agent 不捏造資料 |
| 「我的航班延誤五小時，一定能理賠嗎？」 | KB 依條款檢索，必要時查個人旅程資料 | 不宣稱正式核賠結論 |

## 10. 待團隊定案項目

1. `get-custom-info` 是否真的可提供 `policy_summary`？資料來自旅程服務還是保單服務？
2. 旅程 Tool 直接讀 DB，還是共用旅程管理的內部查詢服務？
3. `flightStatus`／`tripStatus` 的實際欄位及資料新鮮度規則。
4. 天氣缺值、部分可用時如何表示？是否需要 `partial_success`？
5. Clinic 的合作狀態、語言、科別與營業時間資訊由誰維護？
6. 使用者身分如何可信地傳入 Tool Lambda，並完成資料歸屬授權？
7. Gateway 的實際 Tool schema 格式、Lambda event 與輸出封裝，是否與現有 Mock Target 相容？
8. Schema 改版與相容性／版本發布流程。

---

**提議版號：v1.0-draft**。本文件中的航班、天氣、醫療院所及保單資料均為格式示例，不代表真實或即時資訊。
