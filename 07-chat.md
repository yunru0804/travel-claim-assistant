# AI 對話頁 API

本頁共 **5 支 API**。首頁建議問題由前端固定；對話中的建議回答由送出訊息 API 當次回傳。

## 索引

- [訊息資料結構](#message-model)
- [AI 所有回應種類](#assistant-response-types)
- [Markdown 顯示規格](#markdown-rendering)
- [互動元件範例](#interactive-examples)
- [建議回答規則](#suggested-reply-rule)
- [1. GET /v1/conversations](#chat-list)
- [2. GET /v1/conversations/{conversationId}](#chat-detail)
- [3. POST /v1/chat/messages](#chat-send)
- [4. PATCH checklist step](#chat-task-step)
- [5. DELETE /v1/conversations/{conversationId}](#chat-delete)

<a id="message-model"></a>

## 訊息資料結構

`timestamp` 一律為後端產生的 RFC 3339 timestamp。前端依使用者時區格式化時間，並在相鄰訊息換日處顯示日期 badge。

### USER 訊息

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `role` | enum | 是 | 固定 `USER` |
| `text` | string | 是 | 使用者輸入內容 |
| `timestamp` | RFC 3339 string | 是 | 訊息建立時間 |

### ASSISTANT 訊息

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `role` | enum | 是 | 固定 `ASSISTANT` |
| `timestamp` | RFC 3339 string | 是 | 回應建立時間 |
| `content.text` | Markdown string | 是 | 非互動內容；沒有時為空字串 |
| `content.tasks` | object[] | 是 | 互動式 checklist 卡片；沒有時為 `[]` |
| `content.question` | object/null | 是 | 追問與建議回答；沒有時為 `null` |
| `content.question.text` | string | `question` 非 null 時 | 追問文字 |
| `content.question.suggestedReplies` | object[] | `question` 非 null 時 | 當次建議回答；沒有或讀取歷史時為 `[]` |
| `content.question.suggestedReplies[].label` | string | 是 | 按鈕顯示文字 |
| `content.question.suggestedReplies[].value` | string | 是 | 點擊後作為下一筆 USER 訊息送出的內容 |

### Checklist 卡片

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `content.tasks[].taskId` | UUID string | 是 | 卡片識別與 PATCH 路徑 |
| `content.tasks[].type` | enum | 是 | `LUGGAGE` / `FLIGHT` / `MEDICAL`；前端映射 icon |
| `content.tasks[].title` | string | 是 | 卡片主標題 |
| `content.tasks[].meta` | string | 是 | 主標題下方副標 |
| `content.tasks[].description` | string | 是 | 卡片說明 |
| `content.tasks[].steps` | object[] | 是 | Checklist 項目 |
| `content.tasks[].steps[].stepId` | UUID string | 是 | 項目識別與 PATCH 路徑 |
| `content.tasks[].steps[].text` | string | 是 | Checkbox 右側文字 |
| `content.tasks[].steps[].isCompleted` | boolean | 是 | 是否已完成 |

卡片右上狀態由前端依 `steps[].isCompleted` 計算，不需要後端另外回傳：

| 條件 | 顯示狀態 |
|---|---|
| 全部為 `false` | 待處理 |
| 部分為 `true` | 處理中 |
| 全部為 `true` | 已完成 |

<a id="assistant-response-types"></a>

## AI 所有回應種類

ASSISTANT 永遠回傳相同 object，沒有額外的 `responseType`。前端依 `content.tasks` 與 `content.question` 是否有內容，得到以下五種呈現。

### 1. 只有 Markdown 內容

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:30:03+08:00",
  "content": {
    "text": "請先保留以下文件：\n\n- **原班機登機證**\n- 航空公司延誤證明",
    "tasks": [],
    "question": null
  }
}
```

### 2. Markdown 加追問，沒有建議按鈕

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:31:03+08:00",
  "content": {
    "text": "我需要再確認目前狀況。",
    "tasks": [],
    "question": {
      "text": "航空公司預計延誤多久？",
      "suggestedReplies": []
    }
  }
}
```

### 3. Markdown 加追問與建議按鈕

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:32:03+08:00",
  "content": {
    "text": "先確認你目前所在的位置。",
    "tasks": [],
    "question": {
      "text": "你現在已經抵達目的地機場了嗎？",
      "suggestedReplies": [
        {
          "label": "已抵達，還在機場",
          "value": "已抵達，還在機場"
        },
        {
          "label": "還在等飛機",
          "value": "還在等飛機"
        }
      ]
    }
  }
}
```

### 4. Markdown 加 Task 卡片

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:33:03+08:00",
  "content": {
    "text": "請依序完成以下事項。",
    "tasks": [
      {
        "taskId": "936a7c2c-3bd9-4740-b039-09353e277837",
        "type": "LUGGAGE",
        "title": "行李未抵達",
        "meta": "先通報，再追蹤行李",
        "description": "尚未確認永久遺失，先以行李未抵達處理。",
        "steps": [
          {
            "stepId": "729782b3-8074-40e2-9aa3-ea72a5fbf5c4",
            "text": "向航空公司通報並保留 PIR",
            "isCompleted": true
          },
          {
            "stepId": "3a5a9ffd-2493-44c2-907c-c53ac097af25",
            "text": "保留託運收據、登機證與查詢編號",
            "isCompleted": false
          }
        ]
      }
    ],
    "question": null
  }
}
```

### 5. Markdown、Task、追問與建議按鈕全部存在

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:34:03+08:00",
  "content": {
    "text": "班機已延誤，請先處理航班異動。\n\n> ⚠️ 若航空公司尚未提供安排，請先保留所有異動通知。",
    "tasks": [
      {
        "taskId": "07b97006-65f7-46bd-ac06-2a1188c402fe",
        "type": "FLIGHT",
        "title": "班機延誤",
        "meta": "確認航班，保留異動紀錄",
        "description": "先確認最新起飛時間與轉機安排。",
        "steps": [
          {
            "stepId": "bf867726-a3f1-4fa0-b831-36771a4d4957",
            "text": "確認最新航班與後續轉機安排",
            "isCompleted": false
          },
          {
            "stepId": "4b6bd1af-c0f8-40a9-8188-231413ac439c",
            "text": "索取載明原因與時間的延誤證明",
            "isCompleted": false
          }
        ]
      }
    ],
    "question": {
      "text": "航空公司已公告新的起飛時間了嗎？",
      "suggestedReplies": [
        {
          "label": "已公告新時間",
          "value": "航空公司已經公告新的起飛時間。"
        },
        {
          "label": "尚未公告",
          "value": "航空公司尚未公告新的起飛時間。"
        }
      ]
    }
  }
}
```

前端呈現順序固定為 Markdown `text` → `tasks` 卡片 → `question.text` → `suggestedReplies` 按鈕。

<a id="markdown-rendering"></a>

## Markdown 顯示規格

`content.text` 統一承載一般文字、標題、粗體、清單與警示，不另外拆成 `list` 或 `urgent`。

| Markdown | 前端畫面 |
|---|---|
| 一般文字與空行 | 段落 |
| `**文字**` | 粗體 |
| `` `文字` `` | 行內 code |
| `#` / `##` / `###` | 標題 |
| `- 項目` | 無序清單 |
| `1. 項目` | 有序清單 |
| `> ⚠️ 文字` | 警示引用區塊 |

前端不接受 Markdown 中的原始 HTML；`<script>` 或 HTML tag 必須以純文字顯示，避免注入。

### Markdown 回應範例

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:30:03+08:00",
  "content": {
    "text": "請先保留以下文件：\n\n- **原班機登機證**\n- 航空公司延誤證明\n- 必要支出的明細與收據\n\n> ⚠️ 若身體不適或有立即危險，請先聯絡當地緊急救援。",
    "tasks": [],
    "question": null
  }
}
```

前端畫面：

```text
✦ 旅遊助手
請先保留以下文件：

• 原班機登機證（粗體）
• 航空公司延誤證明
• 必要支出的明細與收據

┌ ⚠ 若身體不適或有立即危險，
│    請先聯絡當地緊急救援。
```

<a id="interactive-examples"></a>

## 互動元件範例

### 提問與建議回答按鈕

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:31:03+08:00",
  "content": {
    "text": "先確認你目前所在的位置。",
    "tasks": [],
    "question": {
      "text": "你現在已經抵達目的地機場了嗎？",
      "suggestedReplies": [
        {
          "label": "已抵達，還在機場",
          "value": "已抵達，還在機場"
        },
        {
          "label": "還在等飛機",
          "value": "還在等飛機"
        }
      ]
    }
  }
}
```

前端畫面：

```text
✦ 旅遊助手
先確認你目前所在的位置。
────────────────
你現在已經抵達目的地機場了嗎？
[ 已抵達，還在機場 ] [ 還在等飛機 ]
```

### Checklist 卡片

```json
{
  "role": "ASSISTANT",
  "timestamp": "2026-11-03T19:32:03+08:00",
  "content": {
    "text": "請依序完成以下事項。",
    "tasks": [
      {
        "taskId": "936a7c2c-3bd9-4740-b039-09353e277837",
        "type": "LUGGAGE",
        "title": "行李未抵達",
        "meta": "先通報，再追蹤行李",
        "description": "尚未確認永久遺失，先以行李未抵達處理。",
        "steps": [
          {
            "stepId": "729782b3-8074-40e2-9aa3-ea72a5fbf5c4",
            "text": "向航空公司通報並保留 PIR",
            "isCompleted": true
          },
          {
            "stepId": "3a5a9ffd-2493-44c2-907c-c53ac097af25",
            "text": "保留託運收據、登機證與查詢編號",
            "isCompleted": false
          }
        ]
      }
    ],
    "question": null
  }
}
```

前端畫面：

```text
✦ 旅遊助手
請依序完成以下事項。

接下來要做的事
┌ 行李未抵達                         處理中
│ 先通報，再追蹤行李
│ 尚未確認永久遺失，先以行李未抵達處理。
│ ☑ 向航空公司通報並保留 PIR（刪除線）
│ □ 保留託運收據、登機證與查詢編號
└
```

Markdown、Checklist 與提問可以同時存在；前端固定依 `text` → `tasks` → `question` → `suggestedReplies` 的順序呈現。

<a id="suggested-reply-rule"></a>

## 建議回答規則

1. 只有 `POST /v1/chat/messages` 最新一筆 ASSISTANT 回應可以包含建議回答。
2. 使用者可以點擊建議回答，也可以自行輸入文字；兩者都以相同的 `text` 欄位送出下一輪訊息。
3. 使用者開始輸入或點擊任一建議回答時，前端立即移除上一輪按鈕。
4. 後端不儲存建議回答；讀取歷史對話時 `suggestedReplies` 固定為 `[]`，但 `question.text` 仍保留。

<a id="chat-list"></a>

## 1. GET `/v1/conversations`

- 授權：Bearer access token
- 用途：取得使用者所有旅程的對話紀錄，供右側歷史抽屜捲動載入。

### 上行 JSON（query parameters 的 JSON 表示）

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `limit` | integer | 否 | 預設 20，上限 50 |
| `cursor` | string | 否 | 下一頁 opaque cursor |

```json
{
  "limit": 20
}
```

### 下行 JSON

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `data.items[].conversationId` | UUID string | 是 | 載入與刪除對話 |
| `data.items[].title` | string | 是 | 當時航班編號、出發日期與行程摘要 |
| `data.items[].preview` | string | 是 | 清單摘要 |
| `data.items[].updatedAt` | RFC 3339 string | 是 | 前端格式化清單日期與排序 |
| `data.nextCursor` | string/null | 是 | 取得下一頁；到底為 `null` |

```json
{
  "data": {
    "items": [
      {
        "conversationId": "402abf9a-4439-4ffc-98ee-5fb700aac10f",
        "title": "BR192・2026/10/24 東京返台",
        "preview": "班機已延誤五小時；先確認你的位置…",
        "updatedAt": "2026-10-25T00:06:00+08:00"
      }
    ],
    "nextCursor": null
  }
}
```

<a id="chat-detail"></a>

## 2. GET `/v1/conversations/{conversationId}`

- 授權：Bearer access token

### 上行 JSON

| Key | 來源 | 類型 | 必填 | 用意 |
|---|---|---|---:|---|
| `conversationId` | path | UUID string | 是 | 對話 ID |
| `limit` | query | integer | 否 | 預設 50，上限 100 |
| `cursor` | query | string | 否 | 載入更早訊息 |

```json
{
  "conversationId": "402abf9a-4439-4ffc-98ee-5fb700aac10f",
  "limit": 50
}
```

### 下行 JSON

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `data.title` | string | 是 | Sub-header 顯示的航班與日期標題 |
| `data.messages` | array | 是 | 依時間由舊至新排列 |
| `data.nextCursor` | string/null | 是 | 更早訊息 cursor |

```json
{
  "data": {
    "title": "BR192・2026/10/24 東京返台",
    "messages": [
      {
        "role": "USER",
        "text": "班機延誤五小時，我需要保留什麼？",
        "timestamp": "2026-10-24T23:58:00+08:00"
      },
      {
        "role": "ASSISTANT",
        "timestamp": "2026-10-24T23:59:00+08:00",
        "content": {
          "text": "請保留以下文件：\n\n- 原班機登機證\n- 航空公司延誤證明",
          "tasks": [],
          "question": {
            "text": "航空公司已公告新的起飛時間了嗎？",
            "suggestedReplies": []
          }
        }
      }
    ],
    "nextCursor": null
  }
}
```

<a id="chat-send"></a>

## 3. POST `/v1/chat/messages`

- 授權：Bearer access token

### 上行 JSON

| Key | 類型 | 必填 | 用意 |
|---|---|---:|---|
| `conversationId` | UUID/null | 是 | 新對話為 `null`；延續對話傳現有 ID |
| `text` | string | 是 | 使用者輸入或建議回答的 `value`，1–2,000 字純文字 |

```json
{
  "conversationId": null,
  "text": "班機延誤超過四小時，我需要保留哪些證明？"
}
```

### 下行 JSON

| Key | 類型 | 必有 | 用意 |
|---|---|---:|---|
| `data.conversationId` | UUID string | 新對話是 | 下一輪訊息與載入對話使用；既有對話時省略 |
| `data.title` | string | 新對話是 | 建立當下的航班與日期標題；既有對話時省略 |
| `data.userTimestamp` | RFC 3339 string | 是 | 後端實際儲存 USER 訊息的時間 |
| `data.assistantMessage` | object | 是 | 固定 ASSISTANT 訊息物件 |

`data.assistantMessage` 的五種完整格式見 [AI 所有回應種類](#assistant-response-types)，包含完整 Task 與混合回應資料。

```json
{
  "data": {
    "conversationId": "13f5bb94-a35c-48b1-a0b4-54d6188d7d94",
    "title": "BR192・2026/11/03 東京返台",
    "userTimestamp": "2026-11-03T19:30:01+08:00",
    "assistantMessage": {
      "role": "ASSISTANT",
      "timestamp": "2026-11-03T19:30:03+08:00",
      "content": {
        "text": "請先向航空公司索取延誤證明。\n\n- 載明延誤原因與時間的證明\n- 原班機登機證\n- 必要支出的明細與收據",
        "tasks": [],
        "question": {
          "text": "航空公司已公告新的起飛時間了嗎？",
          "suggestedReplies": [
            {
              "label": "已公告新時間",
              "value": "航空公司已經公告新的起飛時間。"
            },
            {
              "label": "尚未公告",
              "value": "航空公司尚未公告新的起飛時間。"
            }
          ]
        }
      }
    }
  }
}
```

建立新對話時，後端保存當下航班與日期作為對話標題；之後修改目前旅程不會改寫既有對話標題。

<a id="chat-task-step"></a>

## 4. PATCH `/v1/conversations/{conversationId}/tasks/{taskId}/steps/{stepId}`

- 授權：Bearer access token
- 用途：修改指定 checklist step，不修改 Markdown `content.text`。

### 上行 JSON

| Key | 來源 | 類型 | 必填 | 用意 |
|---|---|---|---:|---|
| `conversationId` | path | UUID | 是 | 對話 ID |
| `taskId` | path | UUID | 是 | 卡片 ID |
| `stepId` | path | UUID | 是 | Checklist 項目 ID |
| `isCompleted` | body | boolean | 是 | 勾選狀態 |

```json
{
  "isCompleted": true
}
```

### 下行

```json
{}
```

前端點擊 checkbox 時先更新 `isCompleted` 並重新計算卡片狀態；成功後保留狀態。API 失敗時還原 checkbox，並顯示後端 `message` 錯誤彈窗。

<a id="chat-delete"></a>

## 5. DELETE `/v1/conversations/{conversationId}`

- 授權：Bearer access token
- 用途：前端二次確認後刪除紀錄。

### 上行 JSON

| Key | 類型 | 必填 | 用意 |
|---|---:|---:|---|
| `conversationId` | UUID string | 是 | 要刪除的對話 ID |

```json
{
  "conversationId": "402abf9a-4439-4ffc-98ee-5fb700aac10f"
}
```

### 下行

```json
{}
```
