"""建立旅程管理 Lambda 用的 DynamoDB 表,並寫入假資料。

在 AWS CloudShell 執行:  python3 seed_dynamodb.py
可重複執行:表已存在就跳過建立,資料用 put_item 覆蓋。
Policies / Flights 沿用 legacy 版的表名與主鍵,只換成新版的屬性;舊資料留著不影響。
Weather 主鍵改為 city + forecast_time:若既有 Weather 表是舊主鍵,須先在 console 刪表再執行。

測試帳號:
  user-001:正常狀態旅程(BR192)
  user-002:沒有旅程,GET 回 {}
  user-003:異常狀態旅程(CI100 延誤 5 小時,已達理賠門檻並立案)
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal  # boto3 的 DynamoDB 不接受 float,數字要用 Decimal

import boto3

dynamodb = boto3.resource("dynamodb")

# ---------- 資料表定義:名稱 -> (主鍵 PK, 排序鍵 SK) ----------
TABLES = {
    "Trips": ("sub", None),
    "Policies": ("sub", None),
    "Flights": ("flight_no", "departure_date"),
    "Weather": ("city", "forecast_time"),
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

# 天氣:只放日本城市,從執行當天 00:00(UTC)起往後 WEATHER_DAYS 天,每 3 小時一筆。
# 以執行當天為起點,之後重跑 seed 資料仍會涵蓋「現在」。
# 城市 -> (白天最高溫, 早晚溫差, 各天的降雨機率);同樣的輸入每次產生同樣的資料。
WEATHER_CITIES = {
    "東京": (20, 7, [10, 10, 30, 60, 80, 40, 10, 0, 20, 50, 30]),
    "大阪": (21, 7, [20, 70, 90, 50, 20, 10, 0, 10, 40, 60, 20]),
    "福岡": (21, 6, [40, 30, 10, 0, 10, 30, 60, 90, 70, 30, 10]),
    "沖繩": (27, 4, [30, 50, 80, 90, 60, 30, 20, 10, 20, 40, 60]),
}
WEATHER_DAYS = 11
WEATHER_INTERVAL_HOURS = 3
JAPAN_UTC_OFFSET_HOURS = 9


def condition_for(rain_probability):
    """依降雨機率決定天氣代碼(對應 trip_management.WEATHER_CODES)。"""
    if rain_probability >= 85:
        return "THUNDERSTORM"
    if rain_probability >= 60:
        return "RAIN"
    if rain_probability >= 40:
        return "CLOUDY"
    if rain_probability >= 20:
        return "PARTLY_CLOUDY"
    return "SUNNY"


def build_weather(now):
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    updated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    items = []
    for city, (high, swing, daily_rain) in WEATHER_CITIES.items():
        for step in range(WEATHER_DAYS * 24 // WEATHER_INTERVAL_HOURS):
            moment = start + timedelta(hours=step * WEATHER_INTERVAL_HOURS)
            local_hour = (moment.hour + JAPAN_UTC_OFFSET_HOURS) % 24
            # 當地 14 點最熱、2 點最冷(跨午夜計算與 14 點相差幾小時,最多 12)
            hours_from_peak = min(abs(local_hour - 14), 24 - abs(local_hour - 14))
            temperature = high - swing * hours_from_peak / 12
            rain = daily_rain[(moment - start).days]
            items.append({
                "city": city,
                "forecast_time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "condition_code": condition_for(rain),
                "temperature_c": round(temperature, 1),
                "rain_probability_percent": rain,
                "updated_at": updated_at,  # 這批資料寫入的時間(之後由天氣排程寫入)
            })
    return items


WEATHER = build_weather(datetime.now(timezone.utc))

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
        current = [k["AttributeName"] for k in dynamodb.Table(name).key_schema]
        if current != [k for k in (pk, sk) if k]:
            # DynamoDB 不能改既有表的主鍵,要先手動刪表再重跑
            raise SystemExit(f"{name}: 主鍵是 {current},新版要 {[pk, sk]},請先在 console 刪除 {name} 表再重跑")
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
        with dynamodb.Table(name).batch_writer() as batch:  # 每 25 筆一批寫入,天氣資料較多
            for item in items:
                batch.put_item(Item=to_decimal(item))
        print(f"{name}: 寫入 {len(items)} 筆")


if __name__ == "__main__":
    main()
