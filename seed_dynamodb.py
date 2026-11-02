"""建立旅程管理 Lambda 用的 DynamoDB 表,並寫入假資料。

在 AWS CloudShell 執行:  python3 seed_dynamodb.py
可重複執行:表已存在就跳過建立,資料用 put_item 覆蓋。
Policies / Flights / Weather 沿用 legacy 版的表名與主鍵,只換成新版的屬性;舊資料留著不影響。

測試帳號:
  user-001:正常狀態旅程(BR192)
  user-002:沒有旅程,GET 回 {}
  user-003:異常狀態旅程(CI100 延誤 5 小時,已達理賠門檻並立案)
"""
from decimal import Decimal  # boto3 的 DynamoDB 不接受 float,數字要用 Decimal

import boto3

dynamodb = boto3.resource("dynamodb")

# ---------- 資料表定義:名稱 -> (主鍵 PK, 排序鍵 SK) ----------
TABLES = {
    "Trips": ("sub", None),
    "Policies": ("sub", None),
    "Flights": ("flight_no", "departure_date"),
    "Weather": ("city", None),
    "ExchangeRates": ("currency", None),
}

# ---------- 假資料 ----------
POLICY = {
    "policy_number": "TA-20261103-01",
    "policyholder_name": "王小明",
    "product_name": "國泰安心遊日本 5 日型",
    "coverage_start": "2026-11-01",
    "coverage_end": "2026-11-30",
    "coverages": [
        {"label": "海外突發醫療", "amount": 1000000},
        {"label": "班機延誤保險金", "amount": 5000},
    ],
}

POLICIES = [
    {"sub": "user-001", "policies": [POLICY]},
    {"sub": "user-003", "policies": [dict(POLICY, policy_number="TA-20261103-03", policyholder_name="陳美玲")]},
]

TRIPS = [
    {
        "sub": "user-001",
        "flight_number": "BR192", "departure_date": "2026-11-20",
        "created_at": "2026-11-02T03:00:00Z", "updated_at": "2026-11-02T03:00:00Z",
    },
    {
        "sub": "user-003",
        "flight_number": "CI100", "departure_date": "2026-11-20",
        "created_at": "2026-11-02T03:00:00Z", "updated_at": "2026-11-20T05:15:00Z",
        # 以下三個欄位之後由事件驅動流程寫入,這裡先手動放假資料
        "trip_status": {
            "state": "ABNORMAL", "type": "FLIGHT_DELAY",
            "message": "航班延誤監測中・已超過 4 小時", "updated_at": "2026-11-20T05:15:00Z",
        },
        "disruption": {
            "type": "FLIGHT_DELAY", "title": "航班延誤",
            "description": "航班預計延誤 5 小時,請確認最新航班安排並保留航空公司異動通知。",
            "occurred_at": "2026-11-20T01:15:00Z", "delay_minutes": 300,
        },
        "delay_claim": {
            "eligible": True, "estimated_benefit_amount": 5000,
            "case_status": "CREATED", "case_status_text": "系統已快速立案",
            "checklist": [
                {"label": "原班機登機證", "is_ready": True},
                {"label": "航司班機延誤證明書", "is_ready": False},
            ],
        },
    },
]

FLIGHTS = [
    {
        "flight_no": "BR192", "departure_date": "2026-11-20",
        "origin_airport": "TPE", "destination_airport": "NRT",
        "scheduled_departure": "2026-11-20T10:15:00+08:00",
        "scheduled_arrival": "2026-11-20T14:30:00+09:00",
        "terminal_gate_text": "第一航廈・登機門 B6",
    },
    {
        "flight_no": "CI100", "departure_date": "2026-11-20",
        "origin_airport": "TPE", "destination_airport": "NRT",
        "scheduled_departure": "2026-11-20T10:15:00+08:00",
        "scheduled_arrival": "2026-11-20T14:30:00+09:00",
        "estimated_arrival": "2026-11-20T19:30:00+09:00",
        "terminal_gate_text": "第一航廈・登機門 B6",
        "baggage_carousel_text": "行李轉盤 4 號",
    },
]

WEATHER = [
    {"city": "東京", "condition_code": "PARTLY_CLOUDY", "temperature_c": 22, "rain_probability_percent": 10},
    {"city": "大阪", "condition_code": "RAIN", "temperature_c": 18.5, "rain_probability_percent": 80},
]

EXCHANGE_RATES = [
    {"currency": "JPY", "rate_per_twd": 4.85},
    {"currency": "KRW", "rate_per_twd": 43.2},
]

DATA = {
    "Trips": TRIPS,
    "Policies": POLICIES,
    "Flights": FLIGHTS,
    "Weather": WEATHER,
    "ExchangeRates": EXCHANGE_RATES,
}


def to_decimal(value):
    """把資料裡的 float / int 轉成 Decimal(遞迴處理巢狀的 dict 與 list)。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_decimal(v) for v in value]
    return value


def create_table(name, pk, sk):
    existing = [t.name for t in dynamodb.tables.all()]
    if name in existing:
        print(f"{name}: 已存在,跳過建立")
        return
    key_schema = [{"AttributeName": pk, "KeyType": "HASH"}]
    attributes = [{"AttributeName": pk, "AttributeType": "S"}]
    if sk:
        key_schema.append({"AttributeName": sk, "KeyType": "RANGE"})
        attributes.append({"AttributeName": sk, "AttributeType": "S"})
    table = dynamodb.create_table(
        TableName=name,
        KeySchema=key_schema,
        AttributeDefinitions=attributes,
        BillingMode="PAY_PER_REQUEST",  # 按用量計費,沒用就幾乎不花錢
    )
    table.wait_until_exists()
    print(f"{name}: 已建立")


def main():
    print("Region:", dynamodb.meta.client.meta.region_name)
    for name, (pk, sk) in TABLES.items():
        create_table(name, pk, sk)
    for name, items in DATA.items():
        table = dynamodb.Table(name)
        for item in items:
            table.put_item(Item=to_decimal(item))
        print(f"{name}: 寫入 {len(items)} 筆")


if __name__ == "__main__":
    main()
