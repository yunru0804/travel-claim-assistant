# 旅程主頁 API

本頁共 **4 支 BFF API**、**1 支 Cognito API**，另有 **1 種 Web Push payload**。

最後更新：2026-11-03(補上錯誤回應、欄位計算規則與 checklist 更新限制)

| 項目 | 狀態 |
|---|---|
| `GET /v1/trip`、`PATCH /v1/trip/claim/checklist` | 已完成 |
| `POST` / `DELETE /v1/push/subscriptions`、Web Push | **尚未上線**，規格可能調整 |

## 索引

- [錯誤回應格式](#dashboard-errors)
- [1. GET /v1/trip](#dashboard-get)
- [GET /v1/trip 錯誤回應](#dashboard-get-errors)
- [天氣 conditionCode 對照](#dashboard-weather-codes)
- [2. PATCH /v1/trip/claim/checklist](#dashboard-checklist)
- [Cognito 登出](#dashboard-signout)
- [3. POST /v1/push/subscriptions](#dashboard-push-create)
- [4. DELETE /v1/push/subscriptions/{subscriptionId}](#dashboard-push-delete)
- [Web Push payload](#dashboard-push-payload)

<a id="dashboard-errors"></a>

## 錯誤回應格式

與開始旅程頁（05）相同。所有錯誤（非 `200`）回傳：

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `error` | string | 是 | 錯誤代碼，前端以此判斷處理方式 |
| `message` | string | 是 | 可直接顯示給使用者的中文訊息 |

```json
{
  "error": "flight_unavailable",
  "message": "查無航班資訊,請修改航班"
}
```

- 前端以 HTTP status 加 `error` 判斷流程，不要比對 `message` 文字；`message` 只用於顯示。
- access token 缺少或失效時，`401` 由 API Gateway 直接回傳，body 為 `{"message": "Unauthorized"}`，**沒有 `error` 欄位**。前端遇到 `401` 一律導向重新登入。
- API Gateway 逾時等基礎設施錯誤（`502` / `503` / `504`）也可能沒有 `error` 欄位，前端顯示通用錯誤訊息。

<a id="dashboard-get"></a>

## 1. GET `/v1/trip`

- 授權：Bearer access token
- Cache：`Cache-Control: private, no-store`
- 後端以 access token 的使用者身分取得唯一的目前旅程。
- `tripStatus.state` 是前端切換正常／異常頁面的唯一依據；不得以理賠資格推測旅程狀態。
- 旅程狀態由後端持久化。使用者關閉頁面或未收到 Web Push，不會影響狀態；下次呼叫本 API 仍會取得最新狀態與對應頁面資料。
- `POST /v1/trip` 或 `PUT /v1/trip/flight` 成功後立即呼叫本 API，保證讀到剛寫入的旅程。
- 保單：取保障期間涵蓋 `flight.departureDate` 的保單；沒有任何保單涵蓋時回 `404 policy_not_found`。
- 航班監控尚未產生狀態前（例如剛建立旅程），`tripStatus` 為 `NORMAL`、`message` 為 `航班動態監測中`，`delayClaim` 為未達門檻、未立案。

### 上行

無 query parameters 與 request body。

### 沒有目前旅程

```json
{}
```

前端進入航班資訊輸入頁。

### 下行 JSON 欄位

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `data.user.displayName` | string | 是 | header 使用者名稱；取自該保單的要保人姓名 |
| `data.tripStatus.state` | enum | 是 | `NORMAL` / `ABNORMAL`；前端依此切換正常／異常頁面 |
| `data.tripStatus.type` | enum/null | 是 | 異常類型；目前為 `FLIGHT_DELAY`，正常狀態為 `null` |
| `data.tripStatus.message` | string | 是 | 狀態列顯示文案 |
| `data.tripStatus.updatedAt` | ISO 8601 datetime string | 是 | 後端最後一次更新目前狀態的時間 |
| `data.flight.flightNumber` | string | 是 | 航班編號與修改頁預填 |
| `data.flight.departureDate` | date string | 是 | 修改航班頁預填 |
| `data.flight.origin.city` | string | 是 | 出發城市 |
| `data.flight.origin.airportCode` | IATA string | 是 | 出發機場碼 |
| `data.flight.destination.city` | string | 是 | 目的城市 |
| `data.flight.destination.airportCode` | IATA string | 是 | 目的機場碼 |
| `data.flight.destination.countryCode` | ISO 3166-1 alpha-2 string | 是 | 正常頁面顯示目的地國家／旗幟 |
| `data.flight.destination.localTime` | `HH:mm` string | 是 | 正常頁面顯示目的地目前當地時間；後端於回應當下計算，前端如需持續更新請自行累加 |
| `data.flight.departureTime` | `HH:mm` string | 是 | 卡片顯示的出發地當地時間；為表定時間，延誤時不變 |
| `data.flight.arrivalTime` | `HH:mm` string | 是 | 卡片抵達時間（目的地當地時間）；有預計抵達時間時為預計時間，否則為表定時間 |
| `data.flight.scheduledDurationMinutes` | integer | 是 | 原定航程分鐘數，前端自行格式化 |
| `data.flight.isArrivalTimeEstimated` | boolean | 是 | `true` 時前端在抵達時間前顯示「預計」 |
| `data.flight.terminalGateText` | string/null | 是 | 底部航廈與登機門文字 |
| `data.flight.baggageCarouselText` | string/null | 是 | 底部行李轉盤文字 |
| `data.policy.productName` | string | 是 | 保單卡標題 |
| `data.policy.policyNumberMasked` | string | 是 | 畫面顯示的遮罩保單號 |
| `data.policy.statusText` | string | 是 | 保單狀態標籤，依台灣日期判斷：`保障生效中` / `保障尚未生效` / `保障已到期` |
| `data.policy.coverages[].label` | string | 是 | 保障項目名稱 |
| `data.policy.coverages[].amount` | integer | 是 | 台幣金額，前端格式化為 `NT$` |
| `data.weather` | object/null | 是 | 正常模式天氣卡；上游失敗、查無資料或天氣無法對應 `conditionCode` 時為 `null`，前端隱藏天氣卡 |
| `data.weather.conditionCode` | enum | 是 | 前端依下方對照表選擇天氣圖示與中文名稱 |
| `data.weather.temperatureCelsius` | number | 是 | 攝氏溫度數值 |
| `data.weather.rainProbabilityPercent` | integer | 是 | 降雨機率百分比數值 |
| `data.exchangeRate` | object/null | 是 | 以 `1 TWD` 為固定基準的參考匯率；目的地在台灣、上游失敗或查無匯率時為 `null`，前端隱藏匯率卡 |
| `data.exchangeRate.value` | number | 是 | 目標幣別兌 `1 TWD` 的數值 |
| `data.exchangeRate.targetCurrency` | string | 是 | 前端顯示的目標幣別，例如 `JPY` |
| `data.disruption` | object/null | 是 | 異常頁面資料；正常狀態固定為 `null` |
| `data.disruption.type` | enum | 異常時 | 異常類型；目前為 `FLIGHT_DELAY` |
| `data.disruption.title` | string | 異常時 | 異常卡片標題 |
| `data.disruption.description` | string | 異常時 | 異常說明與使用者提示 |
| `data.disruption.occurredAt` | ISO 8601 datetime string | 異常時 | 異常首次發生時間 |
| `data.disruption.delayMinutes` | integer | 航班延誤時 | 目前延誤分鐘數 |
| `data.delayClaim.eligible` | boolean | 是 | 是否達班機延誤理賠門檻；不作為正常／異常頁面判斷依據 |
| `data.delayClaim.estimatedBenefitAmount` | integer/null | 是 | 台幣預估給付；未達門檻為 `null` |
| `data.delayClaim.caseStatus` | enum | 是 | `NOT_CREATED` / `CREATED`，供前端判斷是否顯示已立案資訊 |
| `data.delayClaim.caseStatusText` | string/null | 是 | 例如 `系統已快速立案`；未立案為 `null` |
| `data.delayClaim.checklist` | array | 是 | 後端決定的 checklist 項目；未達門檻時通常為 `[]` |
| `data.delayClaim.checklist[].label` | string | 是 | checklist 顯示文字 |
| `data.delayClaim.checklist[].isReady` | boolean | 是 | 使用者是否已備妥該項目 |
| `data.support.alternateFlightUrl` | URL string | 是 | 異常頁面「尋找替代航班」連結 |
| `data.support.accommodationUrl` | URL string | 是 | 異常頁面「尋找當日住宿」連結 |
| `data.support.airlinePhone` | string/null | 是 | 航空公司客服電話；無法取得時為 `null`，前端隱藏撥號按鈕 |

### 畫面切換與狀態保存規則

1. `tripStatus.state = NORMAL` 時顯示正常頁面，使用天氣、目的地當地時間及匯率資料。
2. `tripStatus.state = ABNORMAL` 時顯示異常頁面，使用 `disruption`、應變支援及理賠資料。
3. `ABNORMAL` 不代表已符合理賠資格。例如延誤 2 小時時仍是異常頁面，但 `delayClaim.eligible = false`，不得顯示符合門檻或已立案文案。
4. `delayClaim.eligible = true` 時才顯示預估給付與理賠 checklist；`caseStatus = CREATED` 時才顯示已立案文案。
5. 後端必須保存最新 `tripStatus`、`disruption` 與 `delayClaim`。頁面關閉、瀏覽器離線或 Push 投遞失敗後，再次呼叫本 API 仍回傳已保存的最新狀態。

### 下行範例：正常狀態

```json
{
  "data": {
    "user": {
      "displayName": "王小明"
    },
    "tripStatus": {
      "state": "NORMAL",
      "type": null,
      "message": "航班動態監測中・目前準點",
      "updatedAt": "2026-11-03T01:10:00Z"
    },
    "flight": {
      "flightNumber": "BR192",
      "departureDate": "2026-11-03",
      "origin": {
        "city": "台北",
        "airportCode": "TPE"
      },
      "destination": {
        "city": "東京",
        "airportCode": "NRT",
        "countryCode": "JP",
        "localTime": "12:20"
      },
      "departureTime": "10:15",
      "arrivalTime": "14:30",
      "scheduledDurationMinutes": 195,
      "isArrivalTimeEstimated": false,
      "terminalGateText": "第一航廈・登機門 B6",
      "baggageCarouselText": null
    },
    "policy": {
      "productName": "國泰安心遊日本 5 日型",
      "policyNumberMasked": "TA-2026****-01",
      "statusText": "保障生效中",
      "coverages": [
        {
          "label": "海外突發醫療",
          "amount": 1000000
        },
        {
          "label": "班機延誤保險金",
          "amount": 5000
        }
      ]
    },
    "weather": {
      "conditionCode": "PARTLY_CLOUDY",
      "temperatureCelsius": 22,
      "rainProbabilityPercent": 10
    },
    "exchangeRate": {
      "value": 4.85,
      "targetCurrency": "JPY"
    },
    "disruption": null,
    "delayClaim": {
      "eligible": false,
      "estimatedBenefitAmount": null,
      "caseStatus": "NOT_CREATED",
      "caseStatusText": null,
      "checklist": []
    },
    "support": {
      "alternateFlightUrl": "https://tw.trip.com/flights/",
      "accommodationUrl": "https://www.agoda.com/",
      "airlinePhone": "0800098666"
    }
  }
}
```

### 下行範例：異常狀態（航班延誤且已達理賠門檻）

```json
{
  "data": {
    "user": {
      "displayName": "王小明"
    },
    "tripStatus": {
      "state": "ABNORMAL",
      "type": "FLIGHT_DELAY",
      "message": "航班延誤監測中・已超過 4 小時",
      "updatedAt": "2026-11-03T05:15:00Z"
    },
    "flight": {
      "flightNumber": "BR192",
      "departureDate": "2026-11-03",
      "origin": {
        "city": "台北",
        "airportCode": "TPE"
      },
      "destination": {
        "city": "東京",
        "airportCode": "NRT",
        "countryCode": "JP",
        "localTime": "16:15"
      },
      "departureTime": "10:15",
      "arrivalTime": "19:30",
      "scheduledDurationMinutes": 195,
      "isArrivalTimeEstimated": true,
      "terminalGateText": "第一航廈・登機門 B6",
      "baggageCarouselText": "行李轉盤 4 號"
    },
    "policy": {
      "productName": "國泰安心遊日本 5 日型",
      "policyNumberMasked": "TA-2026****-01",
      "statusText": "保障生效中",
      "coverages": [
        {
          "label": "海外突發醫療",
          "amount": 1000000
        },
        {
          "label": "班機延誤保險金",
          "amount": 5000
        }
      ]
    },
    "weather": {
      "conditionCode": "PARTLY_CLOUDY",
      "temperatureCelsius": 22,
      "rainProbabilityPercent": 10
    },
    "exchangeRate": {
      "value": 4.85,
      "targetCurrency": "JPY"
    },
    "disruption": {
      "type": "FLIGHT_DELAY",
      "title": "航班延誤",
      "description": "航班預計延誤 5 小時，請確認最新航班安排並保留航空公司異動通知。",
      "occurredAt": "2026-11-03T01:15:00Z",
      "delayMinutes": 300
    },
    "delayClaim": {
      "eligible": true,
      "estimatedBenefitAmount": 5000,
      "caseStatus": "CREATED",
      "caseStatusText": "系統已快速立案",
      "checklist": [
        {
          "label": "原班機登機證",
          "isReady": true
        },
        {
          "label": "航司班機延誤證明書",
          "isReady": false
        }
      ]
    },
    "support": {
      "alternateFlightUrl": "https://tw.trip.com/flights/",
      "accommodationUrl": "https://www.agoda.com/",
      "airlinePhone": "0800098666"
    }
  }
}
```

<a id="dashboard-get-errors"></a>

### GET `/v1/trip` 錯誤回應

| HTTP | `error` | `message` | 前端處理 |
|---:|---|---|---|
| 401 | — | — | 導向重新登入 |
| 404 | `policy_not_found` | 查無保單資訊 | 顯示 `message`；提供「修改航班」入口（出發日期可能不在保障期間內） |
| 502 | `flight_unavailable` | 查無航班資訊,請修改航班 | 顯示 `message`，導向修改航班頁 |
| 502 | `trip_unavailable` | 旅程資料暫時無法取得,請稍後再試 | 顯示 `message`，提供重試 |
| 502 | `policy_unavailable` | 保單資料暫時無法取得,請稍後再試 | 顯示 `message`，提供重試 |
| 500 | `internal_error` | 系統暫時無法處理,請稍後再試 | 顯示 `message`，提供重試 |

- 天氣與匯率失敗**不會**造成錯誤回應，只會讓 `weather` / `exchangeRate` 為 `null`。
- `flight_unavailable` 包含兩種情況：航班資料來源暫時故障，或查無此航班（例如使用者輸入錯誤的航班編號或日期）。兩者都引導使用者確認並修改航班。
- 收到 `flight_unavailable` 時，錯誤回應不含航班資料。修改航班頁的預填值請使用前端保存的上一次輸入，或讓使用者重新輸入。

<a id="dashboard-weather-codes"></a>

### 天氣 `conditionCode` 對照

| 英文代號 | 代表意義 | 前端建議圖示 |
|---|---|---|
| `SUNNY` | 晴天、天空大致無雲 | ☀️ |
| `PARTLY_CLOUDY` | 晴時多雲，仍可見陽光 | 🌤️ |
| `CLOUDY` | 多雲、陰天或霧 | ☁️ |
| `RAIN` | 小雨、一般雨勢或大雨 | 🌧️ |
| `THUNDERSTORM` | 雷雨，可能伴隨閃電或強降雨 | ⛈️ |
| `SNOW` | 降雪 | 🌨️ |

- 後端必須將天氣供應商原始代號轉換成上述其中一個值，不直接透傳供應商代號。
- 前端不直接顯示英文代號，只使用它選擇圖示與中文名稱。
- 無法取得或判斷天氣時，`weather` 回 `null`。

<a id="dashboard-checklist"></a>

## 2. PATCH `/v1/trip/claim/checklist`

- 授權：Bearer access token
- 用途：更新 checklist，不上傳檔案。

### 上行 JSON

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `checklist` | array | 是 | 最新 checklist 狀態，項目順序與下行相同 |
| `checklist[].label` | string | 是 | 原樣回傳後端提供的 label |
| `checklist[].isReady` | boolean | 是 | 前端更新後的勾選狀態 |

```json
{
  "checklist": [
    {
      "label": "原班機登機證",
      "isReady": true
    },
    {
      "label": "航司班機延誤證明書",
      "isReady": true
    }
  ]
}
```

前端只修改 `isReady`，不得自行新增、刪除、重新排序或改寫 `label`；checklist 內容以後端為準。

### 驗證規則

- `checklist` 必須是非空陣列，每一項都要有 string `label` 與 boolean `isReady`，否則回 `400 invalid_request`。
- `label` 的數量、順序與文字必須和最近一次 `GET /v1/trip` 回傳的 `delayClaim.checklist` 完全一致，否則回 `409 checklist_mismatch`。
- 只有 `delayClaim.eligible = true` 且 checklist 不是空的時候可以更新，否則回 `409 claim_not_eligible`。

### 下行

```json
{}
```

### 錯誤回應

| HTTP | `error` | `message` | 前端處理 |
|---:|---|---|---|
| 400 | `invalid_request` | 輸入資料格式不正確 | 還原勾選狀態 |
| 401 | — | — | 導向重新登入 |
| 404 | `trip_not_found` | 目前沒有旅程,請先建立旅程 | 重新呼叫 `GET /v1/trip`（會回 `{}`，進入航班資訊輸入頁） |
| 409 | `checklist_mismatch` | 理賠清單已更新,請重新整理 | 還原勾選狀態，顯示 `message`，重新呼叫 `GET /v1/trip` 取得最新 checklist |
| 409 | `claim_not_eligible` | 目前沒有可更新的理賠清單 | 還原勾選狀態，重新呼叫 `GET /v1/trip` |
| 500 | `internal_error` | 系統暫時無法處理,請稍後再試 | 還原勾選狀態，顯示 `message` |

`checklist_mismatch` 通常發生在使用者停留頁面期間，後端已更新理賠資料，或旅程被修改航班覆蓋。

<a id="dashboard-signout"></a>

## Cognito 登出

前端呼叫 Amplify `signOut({ global: true })`，由 Amplify 清除本機 session 並向 Cognito 執行 global sign-out，完成後返回首頁。

<a id="dashboard-push-create"></a>

## 3. POST `/v1/push/subscriptions`

> **尚未上線**，以下規格可能調整。

- 授權：Bearer access token
- VAPID public key：由 Angular 部署環境設定提供
- 用途：將瀏覽器 `PushSubscription` 關聯到目前旅程。

### 上行 JSON

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `subscription.endpoint` | URL string | 是 | Push Service endpoint |
| `subscription.expirationTime` | integer/null | 是 | Push API 到期時間 |
| `subscription.keys.p256dh` | base64url string | 是 | Web Push 加密公鑰 |
| `subscription.keys.auth` | base64url string | 是 | Web Push auth secret |

```json
{
  "subscription": {
    "endpoint": "https://push.example.com/send/fcm-example-token",
    "expirationTime": null,
    "keys": {
      "p256dh": "BNcExampleUrlSafeBase64P256dhKey",
      "auth": "tBHExampleAuthSecret"
    }
  }
}
```

### 下行 JSON（`200 OK`）

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `data.subscriptionId` | UUID string | 是 | 前端取消訂閱時使用 |

```json
{
  "data": {
    "subscriptionId": "c38b7fe9-e013-47ca-9d29-e68387502f10"
  }
}
```

<a id="dashboard-push-delete"></a>

## 4. DELETE `/v1/push/subscriptions/{subscriptionId}`

> **尚未上線**，以下規格可能調整。

- 授權：Bearer access token

### 上行 JSON

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `subscriptionId` | UUID string | 是 | 要取消的訂閱 ID |

```json
{
  "subscriptionId": "c38b7fe9-e013-47ca-9d29-e68387502f10"
}
```

### 下行

```json
{}
```

<a id="dashboard-push-payload"></a>

## Web Push payload

> **尚未上線**，以下規格可能調整。上線前前端仍可正常運作：Dashboard 初始化時呼叫 `GET /v1/trip` 即可取得最新狀態。

不使用 API `data` envelope，只傳 Service Worker 顯示通知、事件去重及前端刷新所需資料。不在 Push 內傳理賠金額、保單或航班完整內容，Push payload 也不是旅程狀態的權威來源。

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `type` | enum | 是 | `TRIP_STATUS_CHANGED` / `FLIGHT_DELAY_CLAIM_ELIGIBLE` |
| `eventId` | UUID string | 是 | 前端去重 |
| `fromState` | enum | 狀態轉換時 | 轉換前狀態：`NORMAL` / `ABNORMAL` |
| `toState` | enum | 狀態轉換時 | 轉換後狀態：`NORMAL` / `ABNORMAL` |
| `statusType` | enum/null | 狀態轉換時 | 轉換後異常類型；進入正常狀態時為 `null` |
| `statusUpdatedAt` | ISO 8601 datetime string | 狀態轉換時 | 對應已保存狀態的更新時間 |
| `title` | string | 是 | 系統通知標題 |
| `body` | string | 是 | 系統通知內文 |
| `deepLink` | string | 是 | 通知點擊路徑 |

### 狀態轉換範例：正常轉為異常

```json
{
  "type": "TRIP_STATUS_CHANGED",
  "eventId": "97efbd1a-ad3e-42af-a7ac-4ef86cfa480f",
  "fromState": "NORMAL",
  "toState": "ABNORMAL",
  "statusType": "FLIGHT_DELAY",
  "statusUpdatedAt": "2026-11-03T01:15:00Z",
  "title": "航班狀態異常",
  "body": "您的航班發生延誤，請查看最新旅程資訊。",
  "deepLink": "/dashboard"
}
```

### 狀態轉換範例：異常恢復正常

```json
{
  "type": "TRIP_STATUS_CHANGED",
  "eventId": "11fa8d52-59b2-495c-86d9-ee8a26f7838d",
  "fromState": "ABNORMAL",
  "toState": "NORMAL",
  "statusType": null,
  "statusUpdatedAt": "2026-11-03T06:30:00Z",
  "title": "航班狀態已恢復正常",
  "body": "您的航班目前已恢復正常，請查看最新旅程資訊。",
  "deepLink": "/dashboard"
}
```

`FLIGHT_DELAY_CLAIM_ELIGIBLE` 用於旅程已是 `ABNORMAL`、之後延誤時間再達理賠門檻的情況。此事件不代表正常／異常狀態再次轉換，前端收到後同樣重新呼叫 `GET /v1/trip` 取得最新理賠資料。

### 後端狀態更新與推播順序

1. 航班監測流程判斷最新狀態與既有狀態不同。
2. 後端先持久化完整的 `tripStatus`、`disruption` 與 `delayClaim`，完成後再送 Web Push。
3. 只有 `NORMAL → ABNORMAL` 或 `ABNORMAL → NORMAL` 時送出 `TRIP_STATUS_CHANGED`；相同狀態的資料更新不重複發送狀態轉換通知。
4. Push 投遞失敗、瀏覽器沒有通知權限或使用者關閉頁面，都不得回滾已保存的旅程狀態。
5. Push 可能延遲或亂序，因此前端不得直接以 `fromState` / `toState` 改畫面，仍以最新 `GET /v1/trip` response 為準。

### 前端收到推播後流程

1. Service Worker 顯示通知，並以 `postMessage` 通知已開啟的 PWA。
2. 前端以 `eventId` 去重，再重新呼叫 `GET /v1/trip`。
3. 通知被點擊時，不論既有 PWA 是否已開啟，都導向 `deepLink`。
4. 前端只以新 response 的 `tripStatus.state` 切換正常／異常頁面；只以 `delayClaim.eligible` 與 `caseStatus` 切換理賠區塊。
5. 使用者未收到 Push 或關閉頁面後重新開啟時，Dashboard 初始化仍必須呼叫 `GET /v1/trip`，不得只使用前端快取狀態。


