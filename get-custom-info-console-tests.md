# get-custom-info Lambda 部署與 console 測試

AgentCore Gateway 的 Tool Lambda,規格見 [agent-tool-interface-contract-v1.md](agent-tool-interface-contract-v1.md) 第 3、5 節。

> 這支 Lambda 不經過 API Gateway。console 測試的 event 就是 Tool input(`{"sections": [...]}`),回傳也不是 `statusCode` / `body`,而是合約的 envelope。

## 部署(region `us-east-1`)

### 1. 建立 Lambda

**Lambda** → **Create function** → **Author from scratch**

| 欄位 | 值 |
|---|---|
| Function name | `get-custom-info` |
| Runtime | Python 3.12 |
| Execution role | Create a new role with basic Lambda permissions(預設) |

### 2. 放程式(兩個檔案)

`get_custom_info.py` 會 `import trip_management`,所以 Lambda 裡要有兩個檔案:

1. **Code** 分頁,把 `lambda_function.py` 的內容換成本機 `get_custom_info.py` 的全部內容。
2. 左側檔案列表空白處按右鍵 → **New File**,檔名 `trip_management.py`,貼上本機 `trip_management.py` 的全部內容。
3. 按 **Deploy**。

Handler 維持 `lambda_function.lambda_handler`。

> 之後改了 `trip_management.py`,`trip-management` 和 `get-custom-info` 兩支 Lambda 都要重貼。

### 3. 一般設定

**Configuration** → **General configuration** → **Edit**:Memory 512 MB、Timeout 10 秒。

### 4. 環境變數

**Configuration** → **Environment variables** → **Edit**:

| Key | Value |
|---|---|
| `TRIP_TABLE` | `Trips` |
| `POLICY_TABLE` | `Policies` |
| `FLIGHT_TABLE` | `Flights` |
| `WEATHER_TABLE` | `Weather` |
| `MOCK_SUB` | `user-003` |

`MOCK_SUB` 是暫時代替「使用者身分」的設定。Gateway 的身分傳遞方式定案前,所有呼叫都會查這個使用者的資料;沒設定就回 `UNAUTHORIZED`。

### 5. IAM(只讀)

**Configuration** → **Permissions** → 點 Role name → **Add permissions** → **Create inline policy** → **JSON**,policy 名稱 `get-custom-info-dynamodb-read`:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TableRead",
      "Effect": "Allow",
      "Action": ["dynamodb:GetItem"],
      "Resource": [
        "arn:aws:dynamodb:us-east-1:730406059845:table/Trips",
        "arn:aws:dynamodb:us-east-1:730406059845:table/Policies",
        "arn:aws:dynamodb:us-east-1:730406059845:table/Flights"
      ]
    },
    {
      "Sid": "WeatherQuery",
      "Effect": "Allow",
      "Action": ["dynamodb:Query"],
      "Resource": "arn:aws:dynamodb:us-east-1:730406059845:table/Weather"
    }
  ]
}
```

這個 Tool 只查資料,不給 `PutItem` / `UpdateItem`,AI 出錯也改不到資料。

## 測試

**Test** 分頁 → **Create new event** → 填 Event name → 貼上 Event JSON → **Save** → **Test**。

| # | Event name | Event JSON | `MOCK_SUB` | 預期 |
|---|---|---|---|---|
| ① | `flight-and-status` | `{"sections": ["flight", "flight_status"]}` | `user-003` | `success`;`flight` 為 CI100 TPE→NRT、`estimatedArrival` 有值;`flightStatus.state` `ABNORMAL`、`delayMinutes` 300 |
| ② | `weather` | `{"sections": ["weather"]}` | `user-003` | `success`;`weather.city` 東京,有 `forecastTime`、`updatedAt`;`data` 裡**只有** `weather` |
| ③ | `policy-summary` | `{"sections": ["policy_summary"]}` | `user-003` | `success`;`policyNumberMasked` 為 `TA-2026****-03`(遮罩),`statusText` 保障生效中 |
| ④ | `all-sections` | `{"sections": ["flight", "flight_status", "weather", "policy_summary"]}` | `user-003` | `success`;`data` 有 `flight`、`flightStatus`、`weather`、`policySummary` |
| ⑤ | `invalid-section` | `{"sections": ["hotel"]}` | `user-003` | `error`,`INVALID_ARGUMENT` |
| ⑥ | `model-passes-user-id` | `{"sections": ["flight"], "userId": "user-001"}` | `user-003` | `error`,`INVALID_ARGUMENT`(模型不能指定使用者) |
| ⑦ | `flight-and-status` | 同 ① | 改成 `user-001` | `success`;BR192、`state` `NORMAL`、`delayMinutes` 0 |
| ⑧ | `flight-and-status` | 同 ① | 改成 `user-002` | `error`,`NO_ACTIVE_TRIP` |
| ⑨ | `flight-and-status` | 同 ① | **刪除** `MOCK_SUB` | `error`,`UNAUTHORIZED` |

⑦〜⑨ 要到 **Configuration** → **Environment variables** 改 `MOCK_SUB` 再按 Test。**測完改回 `user-003`**。

每次結果都應該有 5 個頂層欄位:`schemaVersion`、`status`、`source`、`data`、`error`,`source.isMock` 為 `true`。

## 失敗時怎麼查

**Log output** 或 **Monitor** → **View CloudWatch logs**。

| 看到的結果 | 原因 |
|---|---|
| `No module named 'trip_management'` | 少了第二個檔案,或檔名打錯 |
| `SERVICE_UNAVAILABLE`,log 有 `source=... failed: ClientError` | inline policy 沒設好(Weather 要 `Query`) |
| `KeyError: 'TRIP_TABLE'` 之類 | 環境變數漏設 |
| `weather` 是 `null`,log 有 `source=weather failed` | Weather 權限或資料表問題 |
| `weather` 是 `null`,log 沒錯誤 | 近 3 小時沒有天氣資料,重跑 seed |
| `NOT_FOUND` | 旅程的航班不在 Flights 表(例如 trip-management 測試 ⑩ 把 user-001 改成 BR999,重跑 seed 還原) |

Log 第一行 `tool=None event_keys=['sections']` 是正常的:console 測試沒有 Gateway 的 context。接上 Gateway 後,`tool=` 會出現 Tool 名稱,可用來確認 Gateway 實際傳入的格式。
