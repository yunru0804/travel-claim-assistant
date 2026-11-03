# trip-management Lambda console 測試腳本

在 Lambda console 的 **Test** 分頁手動測試 05、06 的 4 支 API:`POST /v1/trip`、`PUT /v1/trip/flight`、`GET /v1/trip`、`PATCH /v1/trip/claim/checklist`。

> ⚠️ console 的 Test 會呼叫**真正的 DynamoDB**,回 200 就代表資料已經寫入。

## 事前準備

1. CloudShell 已執行新版 `python3 seed_dynamodb.py`(檔案開頭有 `user-003:異常狀態旅程`)。
2. Lambda 已貼上最新的 `trip_management.py` 並按 **Deploy**。
3. Memory 512 MB、Timeout 10 秒。
4. 已設定 5 個環境變數:`TRIP_TABLE`、`POLICY_TABLE`、`FLIGHT_TABLE`、`WEATHER_TABLE`、`EXCHANGE_RATE_TABLE`。
5. execution role 已掛上 inline policy `trip-management-dynamodb`。

## 測試帳號(seed 假資料)

| sub | 狀態 |
|---|---|
| `user-001` | 正常旅程,BR192 / 2026-11-20 |
| `user-002` | 沒有旅程 |
| `user-003` | 異常旅程,CI100 / 2026-11-20 延誤 5 小時,已達理賠門檻並立案 |
| `user-004` | 沒有旅程也沒有保單(由 ⑧ 建立旅程) |

## 操作方式

**Test** 分頁 → **Create new event** → 填 Event name → 貼上 Event JSON → **Save** → **Test**。

- `body` 必須是**字串**,裡面的雙引號要寫成 `\"`(API Gateway 傳給 Lambda 的格式就是這樣)。
- Response 的 `body` 也是字串,中文會顯示成 `王` 這種編碼,屬於正常現象。

## 測試總覽

| # | Event name | API | 預期 | 會寫入 DynamoDB |
|---|---|---|---|---|
| ① | `get-user-001` | GET | 200,`NORMAL` | 否 |
| ② | `get-user-002` | GET | 200,`{}` | 否 |
| ③ | `get-user-003` | GET | 200,`ABNORMAL`、`eligible: true` | 否 |
| ④ | `patch-checklist-user-003` | PATCH | 200 `{}` | **是**(checklist) |
| ⑤ | `post-user-001` | POST | 409 `trip_already_exists` | 否 |
| ⑥ | `put-user-002` | PUT | 404 `trip_not_found` | 否 |
| ⑦ | `post-user-002-past-date` | POST | 400 `departure_date_in_past` | 否 |
| ⑧-1 | `post-user-004` | POST | 200 `{}` | **是**(新增旅程) |
| ⑧-2 | `get-user-004` | GET | 404 `policy_not_found` | 否 |
| ⑨-1 | `put-user-001` | PUT | 200 `{}` | **是**(整筆覆蓋) |
| ⑨-2 | `get-user-001` | GET | 200,航班變成 CI100 | 否 |
| ⑩-1 | `put-user-001-unknown-flight` | PUT | 200 `{}` | **是**(整筆覆蓋) |
| ⑩-2 | `get-user-001` | GET | 502 `flight_unavailable` | 否 |

---

## ① GET 正常旅程

Event name:`get-user-001`

```json
{
  "httpMethod": "GET",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-001"}}}
}
```

預期:

- `statusCode` 200
- `displayName` 王小明、`tripStatus.state` `NORMAL`
- `flight` 為 BR192、台北 TPE → 東京 NRT、`departureTime` 10:15、`arrivalTime` 14:30、`scheduledDurationMinutes` 195
- `weather`、`exchangeRate` **不是 null**(PARTLY_CLOUDY 22 度、JPY 4.85)
- `disruption` null、`delayClaim.eligible` false
- `support.airlinePhone` `0800098666`

## ② GET 沒有旅程

Event name:`get-user-002`

```json
{
  "httpMethod": "GET",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-002"}}}
}
```

預期:`statusCode` 200,`body` 為 `"{}"`

## ③ GET 異常旅程

Event name:`get-user-003`

```json
{
  "httpMethod": "GET",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-003"}}}
}
```

預期:

- `statusCode` 200,`displayName` 陳美玲
- `tripStatus.state` `ABNORMAL`、`type` `FLIGHT_DELAY`
- `flight.arrivalTime` 19:30、`isArrivalTimeEstimated` true、`baggageCarouselText` 行李轉盤 4 號
- `disruption.delayMinutes` 300
- `delayClaim.eligible` true、`estimatedBenefitAmount` 5000、`caseStatus` `CREATED`
- checklist 第二項「航司班機延誤證明書」`isReady` false
- `support.airlinePhone` null(程式只放了 BR 的電話)

## ④ PATCH 更新 checklist

Event name:`patch-checklist-user-003`

```json
{
  "httpMethod": "PATCH",
  "resource": "/v1/trip/claim/checklist",
  "requestContext": {"authorizer": {"claims": {"sub": "user-003"}}},
  "body": "{\"checklist\":[{\"label\":\"原班機登機證\",\"isReady\":true},{\"label\":\"航司班機延誤證明書\",\"isReady\":true}]}"
}
```

預期:`statusCode` 200,`body` 為 `"{}"`

驗證:再跑一次 ③,checklist 第二項變成 `isReady` true。

## ⑤ POST 已有旅程

Event name:`post-user-001`

```json
{
  "httpMethod": "POST",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-001"}}},
  "body": "{\"flightNumber\":\"BR192\",\"departureDate\":\"2026-11-20\"}"
}
```

預期:`statusCode` 409,`error` `trip_already_exists`

## ⑥ PUT 沒有旅程

Event name:`put-user-002`

```json
{
  "httpMethod": "PUT",
  "resource": "/v1/trip/flight",
  "requestContext": {"authorizer": {"claims": {"sub": "user-002"}}},
  "body": "{\"flightNumber\":\"BR192\",\"departureDate\":\"2026-11-20\"}"
}
```

預期:`statusCode` 404,`error` `trip_not_found`

## ⑦ POST 過去日期

Event name:`post-user-002-past-date`

```json
{
  "httpMethod": "POST",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-002"}}},
  "body": "{\"flightNumber\":\"BR192\",\"departureDate\":\"2026-10-01\"}"
}
```

預期:`statusCode` 400,`error` `departure_date_in_past`,`message` 時間不對,請重新填寫

## ⑧ 有旅程但沒有保單

**⑧-1** Event name:`post-user-004`

```json
{
  "httpMethod": "POST",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-004"}}},
  "body": "{\"flightNumber\":\"BR192\",\"departureDate\":\"2026-11-20\"}"
}
```

預期:`statusCode` 200,`body` 為 `"{}"`

**⑧-2** Event name:`get-user-004`

```json
{
  "httpMethod": "GET",
  "resource": "/v1/trip",
  "requestContext": {"authorizer": {"claims": {"sub": "user-004"}}}
}
```

預期:`statusCode` 404,`error` `policy_not_found`,`message` 查無保單資訊

## ⑨ PUT 整筆覆蓋

**⑨-1** Event name:`put-user-001`

```json
{
  "httpMethod": "PUT",
  "resource": "/v1/trip/flight",
  "requestContext": {"authorizer": {"claims": {"sub": "user-001"}}},
  "body": "{\"flightNumber\":\"CI100\",\"departureDate\":\"2026-11-20\"}"
}
```

預期:`statusCode` 200,`body` 為 `"{}"`

**⑨-2** 再跑一次 ① `get-user-001`

預期:`flight.flightNumber` 變成 CI100、`arrivalTime` 19:30(CI100 有預計抵達時間),`tripStatus.state` 仍是 `NORMAL`(Trips 沒有監控狀態)。

> PUT 的航班必須在 Flights 表裡,否則之後 GET 會回 502 `flight_unavailable`(見 ⑩)。目前 Flights 只有 BR192、CI100 的 2026-11-20。

## ⑩ PUT 不存在的航班

建立或修改旅程時不檢查航班是否存在,所以 PUT 會成功;要等到 GET 查 Flights 表時才回錯誤。

**⑩-1** Event name:`put-user-001-unknown-flight`

```json
{
  "httpMethod": "PUT",
  "resource": "/v1/trip/flight",
  "requestContext": {"authorizer": {"claims": {"sub": "user-001"}}},
  "body": "{\"flightNumber\":\"BR999\",\"departureDate\":\"2026-11-20\"}"
}
```

預期:`statusCode` 200,`body` 為 `"{}"`(BR999 格式正確,但 Flights 表沒有這班)

**⑩-2** 再跑一次 ① `get-user-001`

預期:`statusCode` 502,`error` `flight_unavailable`,`message` 查無航班資訊,請修改航班

> ⑩ 會讓 user-001 的旅程變成 BR999,① 之後都會回 502。測完請重跑 seed 還原(見「測完還原」)。

---

## 失敗時怎麼查

結果下方的 **Log output**,或 **Monitor** → **View CloudWatch logs**。

| 看到的結果 | 原因 |
|---|---|
| 502 `trip_unavailable` / `policy_unavailable` / `flight_unavailable`,log 有 `AccessDeniedException` | inline policy 沒設好,或 ARN 的 region / account-id 錯 |
| log 有 `ResourceNotFoundException` | 表不在這個 region,或環境變數的表名拼錯 |
| `KeyError: 'TRIP_TABLE'` 之類 | 環境變數漏設 |
| `Task timed out` | Timeout 沒改成 10 秒 |
| `weather` 或 `exchangeRate` 是 null | Weather / ExchangeRates 的權限、環境變數或資料有問題(log 會有 `source=weather failed`) |
| 500 `internal_error` | 程式錯誤,log 有完整 traceback |

## 測完還原

1. CloudShell 再跑一次 `python3 seed_dynamodb.py`:user-001、user-003 恢復原始資料(還原 ④、⑨、⑩ 的修改)。
2. 手動刪除 ⑧ 建立的 user-004:**DynamoDB** → **Tables** → `Trips` → **Explore table items** → 勾選 `user-004` → **Actions** → **Delete items**。
