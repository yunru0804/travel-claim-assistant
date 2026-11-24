"""旅程資料讀取元件(給 get-custom-info Lambda 用)

只放「讀 DynamoDB」與「把資料列整理成 Tool 回傳內容」需要的部分,不含任何 API 路由、輸入驗證或 HTTP 回應。
trip-management Lambda 由其他人維護,有自己的 trip_management.py,兩邊互不 import。

DynamoDB 資料表(全部唯讀):
  Trips    : PK sub                          flight_number, departure_date, updated_at,
                                             trip_status, disruption, delay_claim(由事件驅動流程寫入)
  Policies : PK sub                          policies(保單清單,含保障期間、方案代碼 project_code)
  Plans    : PK project_code                 plan_name, coverages 等(來自公司正式的方案資訊 CSV)
  Flights  : PK flight_no, SK departure_date 起訖機場、表定/預計時間、航廈登機門、行李轉盤
  Weather  : PK city, SK forecast_time       condition_code, temperature_c, rain_probability_percent
                                             (每 3 小時一筆,取現在往前 3 小時內最新的一筆)

環境變數:TRIP_TABLE、POLICY_TABLE、PLAN_TABLE、FLIGHT_TABLE、WEATHER_TABLE
IAM:Trips / Policies / Plans / Flights 需要 GetItem;Weather 需要 Query
"""
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# 保單狀態以台灣日期判斷
TAIPEI = timezone(timedelta(hours=8))
WEATHER_INTERVAL = timedelta(hours=3)

# 機場對照表:IATA -> (城市, 國家代碼, UTC 時差小時)。
# 用固定時差而不是 zoneinfo,因為 Lambda Python 3.12 的執行環境不一定有時區資料庫;
# 目前只放沒有日光節約時間的地區,之後要加美國、歐洲機場時需改用 tzdata 套件。
AIRPORTS = {
    "TPE": ("台北", "TW", 8),
    "TSA": ("台北", "TW", 8),
    "KHH": ("高雄", "TW", 8),
    "NRT": ("東京", "JP", 9),
    "HND": ("東京", "JP", 9),
    "KIX": ("大阪", "JP", 9),
    "FUK": ("福岡", "JP", 9),
    "OKA": ("沖繩", "JP", 9),
    "ICN": ("首爾", "KR", 9),
    "HKG": ("香港", "HK", 8),
    "BKK": ("曼谷", "TH", 7),
    "SIN": ("新加坡", "SG", 8),
}
WEATHER_CODES = {"SUNNY", "PARTLY_CLOUDY", "CLOUDY", "RAIN", "THUNDERSTORM", "SNOW"}
DEFAULT_STATUS_MESSAGE = "航班動態監測中"


# ---------- 共用工具 ----------

def json_default(value):
    if isinstance(value, Decimal):  # DynamoDB 的數字會變成 Decimal
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


def _now():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------- 實際的 DynamoDB 元件(只在 Lambda 啟動時建立) ----------

def _table(env_name):
    import boto3
    return boto3.resource("dynamodb").Table(os.environ[env_name])


class TripStore:
    """Trips 表:一位使用者一筆目前旅程。"""

    def __init__(self):
        self._table = _table("TRIP_TABLE")

    def get(self, sub):
        # POST 後前端會立刻查詢,用強一致讀取避免讀不到剛寫入的旅程
        return self._table.get_item(Key={"sub": sub}, ConsistentRead=True).get("Item")


class PolicyStore:
    """Policies 表:一位使用者一筆,policies 欄位放保單清單。找不到使用者回 None。"""

    def __init__(self):
        self._table = _table("POLICY_TABLE")

    def fetch(self, sub):
        item = self._table.get_item(Key={"sub": sub}).get("Item")
        return None if item is None else item.get("policies", [])


class PlanStore:
    """Plans 表:一個方案一筆(PK project_code),coverages 欄位放該方案的全部保障項目。"""

    def __init__(self):
        self._table = _table("PLAN_TABLE")

    def get(self, project_code):
        return self._table.get_item(Key={"project_code": project_code}).get("Item")


class FlightStore:
    def __init__(self):
        self._table = _table("FLIGHT_TABLE")

    def get(self, flight_number, departure_date):
        return self._table.get_item(
            Key={"flight_no": flight_number, "departure_date": departure_date}).get("Item")


def weather_time_range(now):
    """天氣每 3 小時一筆;只採用現在往前 WEATHER_INTERVAL 內的資料,更舊的視為過期。"""
    return iso(now - WEATHER_INTERVAL), iso(now)


class WeatherStore:
    def __init__(self):
        self._table = _table("WEATHER_TABLE")

    def get(self, city, now):
        """取時間範圍內 forecast_time 最新的一筆;沒有回 None。"""
        start, end = weather_time_range(now)
        items = self._table.query(
            KeyConditionExpression="city = :city AND forecast_time BETWEEN :start AND :end",
            ExpressionAttributeValues={":city": city, ":start": start, ":end": end},
            ScanIndexForward=False,  # 由新到舊
            Limit=1,
        ).get("Items", [])
        return items[0] if items else None


@dataclass
class Deps:
    trips: object
    policies: object = None
    flights: object = None
    weather: object = None
    plans: object = None
    now: object = _now


# ---------- 資料列整理 ----------

def _mask_policy_number(number):
    """保留前 7 碼與後 3 碼,中間以 * 遮罩,例如 TA-20261103-01 -> TA-2026****-01。"""
    if len(number) <= 10:
        return "*" * (len(number) - 3) + number[-3:]
    return number[:7] + "*" * (len(number) - 10) + number[-3:]


def _policy_status_text(policy, today):
    if today < policy["coverage_start"]:
        return "保障尚未生效"
    if today > policy["coverage_end"]:
        return "保障已到期"
    return "保障生效中"


def find_policy(policies, departure_date):
    """取保障期間涵蓋出發日期的第一張保單。"""
    for policy in policies or []:
        if policy.get("coverage_start", "") <= departure_date <= policy.get("coverage_end", ""):
            return policy
    return None


def build_policy_summary(policy, now, plan):
    """productName 取自方案名稱(Plans 的 plan_name);沒有 project_code 或查無方案時為 None。"""
    today = (now.astimezone(TAIPEI)).date().isoformat()
    return {
        "productName": (plan or {}).get("plan_name"),
        "projectCode": policy.get("project_code"),
        "policyNumberMasked": _mask_policy_number(policy["policy_number"]),
        "statusText": _policy_status_text(policy, today),
    }


def build_weather(item):
    if not item or item.get("condition_code") not in WEATHER_CODES:
        return None  # 查不到或供應商代號無法對應時回 null
    return {
        "conditionCode": item["condition_code"],
        "temperatureCelsius": item["temperature_c"],
        "rainProbabilityPercent": item["rain_probability_percent"],
    }


def build_status_parts(trip):
    """tripStatus / disruption / delayClaim 由事件驅動流程寫入 Trips;還沒寫入時回預設的正常狀態。"""
    stored = trip.get("trip_status") or {}
    state = stored.get("state", "NORMAL")
    trip_status = {
        "state": state,
        "type": stored.get("type") if state == "ABNORMAL" else None,
        "message": stored.get("message", DEFAULT_STATUS_MESSAGE),
        "updatedAt": stored.get("updated_at", trip["updated_at"]),
    }

    disruption = None
    stored_disruption = trip.get("disruption")
    if state == "ABNORMAL" and stored_disruption:  # 正常狀態固定為 null
        disruption = {
            "type": stored_disruption["type"],
            "title": stored_disruption["title"],
            "description": stored_disruption["description"],
            "occurredAt": stored_disruption["occurred_at"],
        }
        if "delay_minutes" in stored_disruption:
            disruption["delayMinutes"] = stored_disruption["delay_minutes"]

    claim = trip.get("delay_claim") or {}
    eligible = bool(claim.get("eligible"))
    case_status = claim.get("case_status", "NOT_CREATED")
    delay_claim = {
        "eligible": eligible,
        "estimatedBenefitAmount": claim.get("estimated_benefit_amount") if eligible else None,
        "caseStatus": case_status,
        "caseStatusText": claim.get("case_status_text") if case_status == "CREATED" else None,
        "checklist": [{"label": c["label"], "isReady": c["is_ready"]} for c in claim.get("checklist", [])],
    }
    return trip_status, disruption, delay_claim
