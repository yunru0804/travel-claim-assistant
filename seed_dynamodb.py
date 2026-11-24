"""建立旅程管理 Lambda 用的 DynamoDB 表,並寫入假資料。

在 AWS CloudShell 執行:  python3 seed_dynamodb.py
可重複執行:表已存在就跳過建立,並先清空表內所有資料再寫入,所以每次執行的結果都一樣
(包含測試時透過 API 新增或修改的資料、舊的天氣預報都會被清掉)。只在開發用帳號執行。
Policies / Flights 沿用舊版的表名與主鍵,只換成新版的屬性;舊資料留著不影響。
Weather 主鍵改為 city + forecast_time:若既有 Weather 表是舊主鍵,須先在 console 刪表再執行。

測試帳號(ACCOUNTS):
  normal      正常狀態旅程(BR192),保單方案 T6
  abnormal    異常狀態旅程(CI100 延誤 5 小時,已達理賠門檻並立案),保單方案 U3
  policy_only 有保單、沒有旅程(GET 回 {},可從前端建立旅程),保單方案 T6
  normal_u6   正常狀態旅程(BR192),保單方案 U6(user-005,測不同方案的保單摘要)
  沒列出的 sub 沒有旅程也沒有保單
user-001 / user-003 / user-005 給 console 直接呼叫 Lambda 的測試用;其餘是 Cognito 測試帳號的 sub。

Plans 表不是假資料,內容來自公司正式的方案資訊 CSV(與本檔放在同一個資料夾)。
CSV 的 NOTE / RESTRICTIONS 欄放理賠方式,PLAN_NAME / PLAN_NOT_COVERED / PLAN_NOTE 是方案層資訊,
只填在每個方案的第一列;RESTRICTIONS 與 PLAN_NOT_COVERED 的多筆內容以 | 分隔。
"""
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal  # boto3 的 DynamoDB 不接受 float,數字要用 Decimal
from pathlib import Path

import boto3

dynamodb = boto3.resource("dynamodb")

# ---------- 資料表定義:名稱 -> (主鍵 PK, 排序鍵 SK) ----------
TABLES = {
    "Trips": ("sub", None),
    "Policies": ("sub", None),
    "Plans": ("project_code", None),
    "Flights": ("flight_no", "departure_date"),
    "Weather": ("city", "forecast_time"),
    "ExchangeRates": ("currency", None),
}

# ---------- 假資料 ----------
POLICY = {
    "policy_number": "TA-20261103-01",
    "policyholder_name": "王小明",
    "project_code": "T6",  # 對應 Plans 表的 PK;方案名稱與保障內容都由方案表提供
    "coverage_start": "2026-11-01",
    "coverage_end": "2027-02-28",  # 與航班、天氣資料的結束日相同
}

NORMAL_TRIP = {
    "flight_number": "BR192", "departure_date": "2026-11-20",
    "created_at": "2026-11-02T03:00:00Z", "updated_at": "2026-11-02T03:00:00Z",
}

ABNORMAL_TRIP = {
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
}

# 情境 -> (旅程, 保單);None 表示沒有
SCENARIOS = {
    "normal": (NORMAL_TRIP, POLICY),
    "abnormal": (ABNORMAL_TRIP, dict(POLICY, policy_number="TA-20261103-03", policyholder_name="陳美玲",
                                     project_code="U3")),
    "policy_only": (None, dict(POLICY, policy_number="TA-20261103-02", policyholder_name="林志豪")),
    "normal_u6": (NORMAL_TRIP, dict(POLICY, policy_number="TA-20261103-05", policyholder_name="林志豪",
                                    project_code="U6")),
}

# sub -> 情境
ACCOUNTS = {
    "user-001": "normal",
    "user-003": "abnormal",
    "user-005": "normal_u6",
    "7418a408-c0f1-7075-63a4-744c1fd3acbd": "abnormal",     # 0912345678(最常用)
    "d488e428-2031-7037-ac17-74669afe7f54": "normal",       # 0911111111
    "74d8e4d8-a071-707b-4b1d-becd9026c0b1": "policy_only",  # 0922222222
    # 0933333333(3478e488-3041-7097-f07a-f4ac2f21debb)不寫入任何資料:沒有旅程也沒有保單
}

TRIPS = [dict(SCENARIOS[s][0], sub=sub) for sub, s in ACCOUNTS.items() if SCENARIOS[s][0]]
POLICIES = [{"sub": sub, "policies": [SCENARIOS[s][1]]} for sub, s in ACCOUNTS.items()]

# seed 旅程用的航班(CI100 這天延誤)
TRIP_FLIGHTS = [
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

# 每日航班:BR192 / CI100 從執行當天到 FLIGHT_END_DATE 每天一班、都準時,讓前端新建立的旅程查得到航班。
# 與 TRIP_FLIGHTS 同一天的不產生,不會蓋掉 seed 旅程的延誤資料。
FLIGHT_END_DATE = date(2027, 2, 28)
DAILY_FLIGHT_NUMBERS = ("BR192", "CI100")


def build_daily_flights(now):
    fixed = {(f["flight_no"], f["departure_date"]) for f in TRIP_FLIGHTS}
    day = (now - timedelta(hours=12)).date()  # 旅程 API 以 UTC-12 判斷「今天」,從那天開始產生
    items = []
    while day <= FLIGHT_END_DATE:
        departure_date = day.isoformat()
        for flight_no in DAILY_FLIGHT_NUMBERS:
            if (flight_no, departure_date) in fixed:
                continue
            items.append({
                "flight_no": flight_no, "departure_date": departure_date,
                "origin_airport": "TPE", "destination_airport": "NRT",
                "scheduled_departure": f"{departure_date}T10:15:00+08:00",
                "scheduled_arrival": f"{departure_date}T14:30:00+09:00",
                "terminal_gate_text": "第一航廈・登機門 B6",
            })
        day += timedelta(days=1)
    return items


FLIGHTS = TRIP_FLIGHTS + build_daily_flights(datetime.now(timezone.utc))

# 天氣:只放日本城市,從執行當天 00:00(UTC)起到 WEATHER_END_DATE,每 3 小時一筆。
# 以執行當天為起點,之後重跑 seed 資料仍會涵蓋「現在」。
# 城市 -> (各月白天最高溫, 早晚溫差, 降雨機率循環);同一天每次產生同樣的資料。
# 月均高溫只列 11〜2 月,WEATHER_END_DATE 往後延時要補上對應月份。
WEATHER_CITIES = {
    "東京": ({11: 17, 12: 12, 1: 10, 2: 11}, 9, [10, 10, 30, 60, 80, 40, 10, 0, 20, 50, 30, 10, 70]),
    "大阪": ({11: 17, 12: 12, 1: 9, 2: 10}, 8, [20, 70, 90, 50, 20, 10, 0, 10, 40, 60, 20, 30]),
    "福岡": ({11: 18, 12: 13, 1: 10, 2: 11}, 7, [40, 30, 10, 0, 10, 30, 60, 90, 70, 30, 10]),
    "沖繩": ({11: 24, 12: 21, 1: 19, 2: 19}, 4, [30, 50, 80, 90, 60, 30, 20, 10, 20, 40, 60, 50, 30, 10]),
}
JAPAN_UTC_OFFSET_HOURS = 9
JAPAN = timezone(timedelta(hours=JAPAN_UTC_OFFSET_HOURS))
WEATHER_END_DATE = datetime(2027, 3, 1, tzinfo=JAPAN)  # 不含,即資料到日本時間 2027-02-28 21:00
WEATHER_PATTERN_START = datetime(2026, 11, 1, tzinfo=timezone.utc)  # 降雨循環的起算日,固定才能每次相同
WEATHER_INTERVAL_HOURS = 3
SNOW_MAX_TEMPERATURE = 2  # 會下雨且氣溫在此以下時改為下雪


def condition_for(rain_probability, temperature):
    """依降雨機率與氣溫決定天氣代碼(對應 trip_management.WEATHER_CODES)。"""
    if rain_probability >= 60 and temperature <= SNOW_MAX_TEMPERATURE:
        return "SNOW"
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
    steps = int((WEATHER_END_DATE - start).total_seconds() // 3600 // WEATHER_INTERVAL_HOURS)
    for city, (monthly_high, swing, rain_cycle) in WEATHER_CITIES.items():
        for step in range(max(steps, 0)):
            moment = start + timedelta(hours=step * WEATHER_INTERVAL_HOURS)
            local = moment + timedelta(hours=JAPAN_UTC_OFFSET_HOURS)
            # 當地 14 點最熱、2 點最冷(跨午夜計算與 14 點相差幾小時,最多 12)
            hours_from_peak = min(abs(local.hour - 14), 24 - abs(local.hour - 14))
            temperature = round(monthly_high[local.month] - swing * hours_from_peak / 12, 1)
            rain = rain_cycle[(local.date() - WEATHER_PATTERN_START.date()).days % len(rain_cycle)]
            items.append({
                "city": city,
                "forecast_time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "condition_code": condition_for(rain, temperature),
                "temperature_c": temperature,
                "rain_probability_percent": rain,
                "updated_at": updated_at,  # 這批資料寫入的時間(之後由天氣排程寫入)
            })
    return items


WEATHER = build_weather(datetime.now(timezone.utc))

EXCHANGE_RATES = [
    {"currency": "JPY", "rate_per_twd": 4.85},
    {"currency": "KRW", "rate_per_twd": 43.2},
]

PLAN_CSV = Path(__file__).with_name("方案資訊_t6_u3_u6.csv")


def split_list(text):
    return [part for part in text.split("|") if part]


def load_plans(path=PLAN_CSV):
    """讀方案資訊 CSV,一個方案(PROJECT_CODE)一筆,保障項目維持 CSV 順序。"""
    plans = {}
    with open(path, encoding="utf-8-sig", newline="") as f:  # 檔案開頭有 BOM
        for row in csv.DictReader(f):
            plan = plans.setdefault(row["PROJECT_CODE"], {
                "project_code": row["PROJECT_CODE"], "plan_name": None, "not_covered": [], "notes": [],
                "coverages": []})
            # 方案層欄位只填在方案的第一列
            if row["PLAN_NAME"]:
                plan["plan_name"] = row["PLAN_NAME"]
                plan["not_covered"] = split_list(row["PLAN_NOT_COVERED"])
                plan["notes"] = split_list(row["PLAN_NOTE"])
            coverage = {
                "product_main_id": row["PRODUCT_MAIN_ID"],
                "product_public_name": row["PRODUCT_PUBLIC_NAME"],
                "sub_id": row["PRODUCT_SUB_ID"],
                "name": row["PRODUCT_SUBID_NAME"],
                "name_en": row["PRODUCT_SUBID_ENAME"],
                "amount": Decimal(row["AMOUNT"]) if row["AMOUNT"] else None,  # 空白 = 依條款約定,沒有固定金額
                "currency": row["CURRENCY"],
                "amount_type_name": row["AMOUNT_TYPE_NAME"],
            }
            if row["NOTE"]:
                coverage["note"] = row["NOTE"]
            if row["RESTRICTIONS"]:
                coverage["restrictions"] = split_list(row["RESTRICTIONS"])
            plan["coverages"].append(coverage)
    return list(plans.values())


PLANS = load_plans()

DATA = {
    "Trips": TRIPS,
    "Policies": POLICIES,
    "Plans": PLANS,
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


def clear_table(name, pk, sk):
    """刪光表內所有資料(只留表本身),讓每次執行都從乾淨狀態開始。"""
    table = dynamodb.Table(name)
    key_names = [k for k in (pk, sk) if k]
    scan_args = {
        "ProjectionExpression": ", ".join(f"#k{i}" for i in range(len(key_names))),
        "ExpressionAttributeNames": {f"#k{i}": k for i, k in enumerate(key_names)},
    }
    deleted = 0
    with table.batch_writer() as batch:
        while True:
            page = table.scan(**scan_args)
            for item in page["Items"]:
                batch.delete_item(Key={k: item[k] for k in key_names})
                deleted += 1
            if "LastEvaluatedKey" not in page:
                break
            scan_args["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    print(f"{name}: 清除 {deleted} 筆舊資料")


def main():
    print("Region:", dynamodb.meta.client.meta.region_name)
    for name, (pk, sk) in TABLES.items():
        create_table(name, pk, sk)
        clear_table(name, pk, sk)
    for name, items in DATA.items():
        with dynamodb.Table(name).batch_writer() as batch:  # 每 25 筆一批寫入,天氣資料較多
            for item in items:
                batch.put_item(Item=to_decimal(item))
        print(f"{name}: 寫入 {len(items)} 筆")


if __name__ == "__main__":
    main()
