"""建立 Policies / Flights / Weather 三張 DynamoDB 表,並寫入假資料。

在 AWS CloudShell 執行:  python3 seed_dynamodb.py
可重複執行:表已存在就跳過建立,資料用 put_item 覆蓋。
"""
from decimal import Decimal  # boto3 的 DynamoDB 不接受 float,數字要用 Decimal

import boto3

dynamodb = boto3.resource("dynamodb")

# ---------- 資料表定義:名稱 -> (主鍵 PK, 排序鍵 SK) ----------
TABLES = {
    "Policies": ("sub", None),
    "Flights": ("flight_no", "departure_date"),
    "Weather": ("city", None),
}

# ---------- 假資料 ----------
POLICIES = [
    {
        "sub": "user-001",
        "policies": [
            {
                "policy_id": "P1",
                "product_code": "TRAVEL-A",
                "product_version": "2025-04",
                "coverage_start": "2026-11-01",
                "coverage_end": "2026-11-10",
                "itinerary": [
                    {"flight_no": "BR198", "departure_date": "2026-11-01", "destination": "Tokyo"},
                    {"flight_no": "BR197", "departure_date": "2026-11-08", "destination": "Taipei"},
                ],
            }
        ],
    },
    # user-002 刻意不放,用來測試「找不到使用者 -> 404」
]

FLIGHTS = [
    {
        "flight_no": "BR198", "departure_date": "2026-11-01",
        "status": "DELAYED", "delay_minutes": 45,
        "scheduled_departure": "2026-11-01T09:30:00+08:00",
        "last_updated": "2026-11-01T08:10:00+08:00",
        "last_notified_level": 2,  # 事件驅動用的內部欄位,Lambda 不會回傳給前端
    },
    {
        "flight_no": "BR197", "departure_date": "2026-11-08",
        "status": "ON_TIME", "delay_minutes": 0,
        "scheduled_departure": "2026-11-08T15:00:00+09:00",
        "last_updated": "2026-11-08T12:00:00+09:00",
        "last_notified_level": 0,
    },
]

WEATHER = [
    {"city": "Tokyo", "summary": "sunny", "temperature_c": 18.5},
    {"city": "Taipei", "summary": "rainy", "temperature_c": 24},
]

DATA = {"Policies": POLICIES, "Flights": FLIGHTS, "Weather": WEATHER}


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
