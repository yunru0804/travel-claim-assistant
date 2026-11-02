# 開始旅程頁 API

本頁共 **2 支 API**。

## 索引

- [1. POST /v1/trip－建立目前旅程](#journey-create)
- [2. PUT /v1/trip/flight－修改航班](#journey-update)

<a id="journey-create"></a>

## 1. POST `/v1/trip`

- 授權：Bearer Cognito access token
- 用途：為登入使用者建立唯一的目前旅程。

### 上行欄位

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `flightNumber` | string | 是 | 航班編號；前端轉大寫並移除空白 |
| `departureDate` | date string | 是 | 出發地當地日期，格式 `YYYY-MM-DD` |

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

<a id="journey-update"></a>

## 2. PUT `/v1/trip/flight`

- 授權：Bearer Cognito access token
- 用途：替換登入使用者目前旅程的航班資訊。

### 前端流程

1. 進入編輯頁時使用 Dashboard 現有航班資料建立本機草稿。
2. 放棄更改時不呼叫 API。
3. 確認更改後呼叫本 API。
4. 成功後重新呼叫 `GET /v1/trip`。

### 上行欄位

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `flightNumber` | string | 是 | 新航班編號 |
| `departureDate` | date string | 是 | 新出發日期 |

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

