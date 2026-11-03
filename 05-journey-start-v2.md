# 開始旅程頁 API

本頁共 **2 支 API**。

最後更新：2026-11-03(補上驗證規則、錯誤回應與覆蓋旅程流程)

## 索引

- [錯誤回應格式](#journey-errors)
- [1. POST /v1/trip－建立目前旅程](#journey-create)
- [2. PUT /v1/trip/flight－修改航班](#journey-update)
- [航班輸入驗證規則](#journey-validation)

<a id="journey-errors"></a>

## 錯誤回應格式

所有錯誤（非 `200`）回傳相同結構：

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `error` | string | 是 | 錯誤代碼，前端以此判斷處理方式 |
| `message` | string | 是 | 可直接顯示給使用者的中文訊息 |

```json
{
  "error": "departure_date_in_past",
  "message": "時間不對,請重新填寫"
}
```

- 前端以 HTTP status 加 `error` 判斷流程，不要比對 `message` 文字；`message` 只用於顯示。
- access token 缺少或失效時，`401` 由 API Gateway 直接回傳，body 為 `{"message": "Unauthorized"}`，**沒有 `error` 欄位**。前端遇到 `401` 一律導向重新登入。
- API Gateway 逾時等基礎設施錯誤（`502` / `503` / `504`）也可能沒有 `error` 欄位，前端顯示通用錯誤訊息。

<a id="journey-create"></a>

## 1. POST `/v1/trip`

- 授權：Bearer Cognito access token
- 用途：為登入使用者建立唯一的目前旅程。
- 一位使用者只會有一個目前旅程。已有旅程時本 API 不會覆蓋，回 `409`。

### 上行欄位

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `flightNumber` | string | 是 | 航班編號；前端轉大寫並移除空白。規則見[航班輸入驗證規則](#journey-validation) |
| `departureDate` | date string | 是 | 出發地當地日期，格式 `YYYY-MM-DD`，不可早於今天 |

```json
{
  "flightNumber": "BR192",
  "departureDate": "2026-11-03"
}
```

### 下行（`200 OK`）

```json
{}
```

成功後前端呼叫 `GET /v1/trip` 取得 Dashboard 完整資料。

### 錯誤回應

| HTTP | `error` | `message` | 前端處理 |
|---:|---|---|---|
| 400 | `invalid_request` | 輸入資料格式不正確 | 停留在輸入頁，提示檢查航班編號與日期 |
| 400 | `departure_date_in_past` | 時間不對,請重新填寫 | 停留在輸入頁，提示重新選擇日期 |
| 401 | — | — | 導向重新登入 |
| 409 | `trip_already_exists` | 已經有進行中的旅程 | 見下方「已有旅程時的覆蓋流程」 |
| 500 | `internal_error` | 系統暫時無法處理,請稍後再試 | 顯示 `message`，可讓使用者重試 |

### 已有旅程時的覆蓋流程

一位使用者只保存一筆旅程，不保留舊旅程紀錄。收到 `409 trip_already_exists` 時：

1. 前端跳出確認視窗，例如「已有進行中的旅程，要用新航班覆蓋嗎？」
2. 使用者取消：不呼叫 API。
3. 使用者確認：以**相同的** `flightNumber` / `departureDate` 呼叫 `PUT /v1/trip/flight`。
4. PUT 成功後呼叫 `GET /v1/trip`。

<a id="journey-update"></a>

## 2. PUT `/v1/trip/flight`

- 授權：Bearer Cognito access token
- 用途：以新航班**整筆覆蓋**登入使用者的目前旅程。
- 覆蓋後等同一趟新旅程：舊航班的監控狀態、異常資訊、理賠資料與 checklist 都會清除，`GET /v1/trip` 的 `tripStatus` 回到 `NORMAL`。
- 後端不檢查航班是否存在。航班查無資料時，之後 `GET /v1/trip` 會回 `502 flight_unavailable`，前端再引導使用者修改航班。

### 前端流程

1. 進入編輯頁時使用 Dashboard 現有航班資料建立本機草稿。
2. 放棄更改時不呼叫 API。
3. 確認更改後呼叫本 API。目前旅程為異常或已立案時，建議先提示使用者「修改航班會清除目前的延誤與理賠資訊」。
4. 成功後重新呼叫 `GET /v1/trip`。

### 上行欄位

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `flightNumber` | string | 是 | 新航班編號；規則同 POST |
| `departureDate` | date string | 是 | 新出發日期；規則同 POST |

```json
{
  "flightNumber": "CI100",
  "departureDate": "2026-11-04"
}
```

### 下行（`200 OK`）

```json
{}
```

### 錯誤回應

| HTTP | `error` | `message` | 前端處理 |
|---:|---|---|---|
| 400 | `invalid_request` | 輸入資料格式不正確 | 停留在編輯頁，提示檢查航班編號與日期 |
| 400 | `departure_date_in_past` | 時間不對,請重新填寫 | 停留在編輯頁，提示重新選擇日期 |
| 401 | — | — | 導向重新登入 |
| 404 | `trip_not_found` | 目前沒有旅程,請先建立旅程 | 改呼叫 `POST /v1/trip`，或導向航班資訊輸入頁 |
| 500 | `internal_error` | 系統暫時無法處理,請稍後再試 | 顯示 `message`，可讓使用者重試 |

<a id="journey-validation"></a>

## 航班輸入驗證規則

POST 與 PUT 共用。後端會再驗證一次，前端先做相同檢查可以減少錯誤請求。

| 欄位 | 規則 | 違反時 |
|---|---|---|
| `flightNumber` | 移除所有空白並轉大寫後，須為航空公司代碼（2 碼英數字，或 3 碼英文字母）+ 1〜4 位數字 + 可選 1 個英文字母。例如 `BR192`、`CI100`、`7C1101` | `400 invalid_request` |
| `departureDate` | 格式 `YYYY-MM-DD`，且必須是存在的日期（例如 `2026-02-30` 不接受） | `400 invalid_request` |
| `departureDate` | 不可早於今天 | `400 departure_date_in_past` |
| request body | 必須是 JSON object，兩個欄位都是 string | `400 invalid_request` |

**「今天」的判斷方式：** `departureDate` 是出發地當地日期，後端不知道出發地時區，因此以全球最晚的時區（UTC-12）判斷今天，避免誤擋其他時區當天出發的航班。所以在台灣時間每天 20:00 之前，前一天的日期仍會被接受。前端可以用使用者裝置的當地日期做較嚴格的檢查。
