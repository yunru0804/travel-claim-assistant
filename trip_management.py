"""旅程管理 Lambda(Trip Management)

一支 Lambda 處理所有 /v1/trip* 路由,依 (HTTP method, resource) 分派:
  POST  /v1/trip                   建立目前旅程(一位使用者只能有一個)
  PUT   /v1/trip/flight            以新航班整筆覆蓋目前旅程
  GET   /v1/trip                   取得 Dashboard 全部資料;沒有旅程回 {}
  PATCH /v1/trip/claim/checklist   更新理賠 checklist 的 isReady

身分只從 API Gateway 驗證後的 Cognito claims 取得 sub,不接受 body 帶入的使用者 ID。

DynamoDB 資料表:
  Trips         : PK sub                          flight_number, departure_date, created_at, updated_at,
                                                  trip_status, disruption, delay_claim(由事件驅動流程寫入)
  Policies      : PK sub                          policies(保單清單,含要保人姓名、保障期間、保障項目)
  Flights       : PK flight_no, SK departure_date 起訖機場、表定/預計時間、航廈登機門、行李轉盤
  Weather       : PK city                         condition_code, temperature_c, rain_probability_percent
  ExchangeRates : PK currency                     rate_per_twd(1 TWD 可換多少該幣別)

環境變數:TRIP_TABLE、POLICY_TABLE、FLIGHT_TABLE、WEATHER_TABLE、EXCHANGE_RATE_TABLE
IAM:Trips 需要 GetItem、PutItem、UpdateItem;其他四張表需要 GetItem

一位使用者只存一筆旅程。PUT 會整筆覆蓋(含監控狀態、理賠資料),等同換成新旅程。
已有旅程時 POST 回 409,由前端詢問使用者是否覆蓋,確認後改呼叫 PUT。

回應:
  200 {} 或 {"data": ...}
  400 invalid_request          body 不是 JSON 或欄位格式錯誤
  400 departure_date_in_past   出發日期早於今天
  401 unauthorized
  404 trip_not_found           PUT / PATCH 時沒有目前旅程
  404 policy_not_found         沒有保單涵蓋這趟旅程的出發日期
  404 route_not_found
  409 trip_already_exists      POST 時已有目前旅程
  409 claim_not_eligible       PATCH 時理賠未達門檻或沒有 checklist
  409 checklist_mismatch       PATCH 的 label 與後端不一致,前端應重新 GET
  502 trip_unavailable / policy_unavailable / flight_unavailable(查無航班也回這個)
  500 internal_error
天氣、匯率查詢失敗時對應欄位回 null,其他資料照常回傳。
"""
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 航空公司代碼(IATA 2 碼或 ICAO 3 碼)+ 1〜4 位數字 + 可選的字尾字母,例如 BR192、7C1101
FLIGHT_NUMBER_PATTERN = re.compile(r"^(?:[A-Z0-9]{2}|[A-Z]{3})[0-9]{1,4}[A-Z]?$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 出發日期是出發地當地日期,但後端不知道出發地時區。用全球最晚的時區(UTC-12)算「今天」,
# 只要地球上還有地方是這一天就接受,避免在台灣、日本等時區誤擋當天出發的航班。
EARLIEST_UTC_OFFSET = timedelta(hours=-12)
# 保單狀態以台灣日期判斷
TAIPEI = timezone(timedelta(hours=8))

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
CURRENCIES = {"JP": "JPY", "KR": "KRW", "HK": "HKD", "TH": "THB", "SG": "SGD"}
# 航空公司客服電話,查不到時回 null
AIRLINE_PHONES = {"BR": "0800098666"}
ALTERNATE_FLIGHT_URL = "https://tw.trip.com/flights/"
ACCOMMODATION_URL = "https://www.agoda.com/"
WEATHER_CODES = {"SUNNY", "PARTLY_CLOUDY", "CLOUDY", "RAIN", "THUNDERSTORM", "SNOW"}
DEFAULT_STATUS_MESSAGE = "航班動態監測中"

ERROR_MESSAGES = {
    "invalid_request": "輸入資料格式不正確",
    "departure_date_in_past": "時間不對,請重新填寫",
    "unauthorized": "請重新登入",
    "trip_not_found": "目前沒有旅程,請先建立旅程",
    "policy_not_found": "查無保單資訊",
    "route_not_found": "找不到這個 API",
    "trip_already_exists": "已經有進行中的旅程",
    "claim_not_eligible": "目前沒有可更新的理賠清單",
    "checklist_mismatch": "理賠清單已更新,請重新整理",
    "trip_unavailable": "旅程資料暫時無法取得,請稍後再試",
    "policy_unavailable": "保單資料暫時無法取得,請稍後再試",
    "flight_unavailable": "查無航班資訊,請修改航班",
    "internal_error": "系統暫時無法處理,請稍後再試",
}


class RequestError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


class TripAlreadyExists(Exception):
    pass


class TripNotFound(Exception):
    pass


class ChecklistMismatch(Exception):
    pass


# ---------- 共用工具 ----------

def _json_default(value):
    if isinstance(value, Decimal):  # DynamoDB 的數字會變成 Decimal
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "private, no-store",
        },
        "body": json.dumps(body, ensure_ascii=False, default=_json_default),
    }


def _error(status, code):
    return _response(status, {"error": code, "message": ERROR_MESSAGES[code]})


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def get_user_sub(event):
    """只從 API Gateway 驗證後的 claims 取身分。"""
    auth = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = auth.get("claims")  # REST API + Cognito authorizer
    if claims is None:
        claims = (auth.get("jwt") or {}).get("claims")  # HTTP API + JWT authorizer
    sub = (claims or {}).get("sub")
    if not sub:
        raise RequestError(401, "unauthorized")
    return sub


def _parse_body(event):
    try:
        body = json.loads(event.get("body") or "")
    except (TypeError, ValueError):
        raise RequestError(400, "invalid_request")
    if not isinstance(body, dict):
        raise RequestError(400, "invalid_request")
    return body


def parse_flight_input(event, now):
    """驗證 flightNumber / departureDate,回傳 (正規化後的航班編號, 日期字串)。"""
    body = _parse_body(event)
    flight_number = body.get("flightNumber")
    departure_date = body.get("departureDate")
    if not isinstance(flight_number, str) or not isinstance(departure_date, str):
        raise RequestError(400, "invalid_request")

    flight_number = re.sub(r"\s+", "", flight_number).upper()  # 前端已處理,後端再做一次
    if not FLIGHT_NUMBER_PATTERN.match(flight_number):
        raise RequestError(400, "invalid_request")

    if not DATE_PATTERN.match(departure_date):
        raise RequestError(400, "invalid_request")
    try:
        parsed = date.fromisoformat(departure_date)  # 擋掉 2026-02-30 這種不存在的日期
    except ValueError:
        raise RequestError(400, "invalid_request")
    if parsed < (now + EARLIEST_UTC_OFFSET).date():
        raise RequestError(400, "departure_date_in_past")
    return flight_number, departure_date


def parse_checklist_input(event):
    """驗證 checklist,回傳 [(label, is_ready), ...]。"""
    checklist = _parse_body(event).get("checklist")
    if not isinstance(checklist, list) or not checklist:
        raise RequestError(400, "invalid_request")
    result = []
    for entry in checklist:
        if not isinstance(entry, dict):
            raise RequestError(400, "invalid_request")
        label, is_ready = entry.get("label"), entry.get("isReady")
        if not isinstance(label, str) or not isinstance(is_ready, bool):
            raise RequestError(400, "invalid_request")
        result.append((label, is_ready))
    return result


# ---------- 實際的 DynamoDB 元件(只在 Lambda 啟動時建立) ----------

def _table(env_name):
    import boto3
    return boto3.resource("dynamodb").Table(os.environ[env_name])


class TripStore:
    """Trips 表:一位使用者一筆目前旅程。用條件寫入避免同時送出時互相覆蓋。"""

    def __init__(self):
        self._table = _table("TRIP_TABLE")

    def get(self, sub):
        # POST 後前端會立刻 GET,用強一致讀取避免讀不到剛寫入的旅程
        return self._table.get_item(Key={"sub": sub}, ConsistentRead=True).get("Item")

    def save(self, sub, flight_number, departure_date, now, *, replace):
        """replace=False:只在沒有旅程時建立;replace=True:只在已有旅程時整筆覆蓋。"""
        condition = "attribute_exists(#sub)" if replace else "attribute_not_exists(#sub)"
        try:
            self._table.put_item(
                Item={
                    "sub": sub,
                    "flight_number": flight_number,
                    "departure_date": departure_date,
                    "created_at": now,
                    "updated_at": now,
                },
                ConditionExpression=condition,
                ExpressionAttributeNames={"#sub": "sub"},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            raise TripNotFound() if replace else TripAlreadyExists()

    def set_checklist_ready(self, sub, checklist):
        """只改每一項的 is_ready;條件確認 label 與數量沒被其他流程改掉,否則丟 ChecklistMismatch。"""
        sets, conditions = [], ["delay_claim.eligible = :eligible", "size(delay_claim.checklist) = :size"]
        values = {":eligible": True, ":size": len(checklist)}
        for i, (label, is_ready) in enumerate(checklist):
            sets.append(f"delay_claim.checklist[{i}].is_ready = :r{i}")
            conditions.append(f"delay_claim.checklist[{i}].label = :l{i}")
            values[f":r{i}"] = is_ready
            values[f":l{i}"] = label
        try:
            self._table.update_item(
                Key={"sub": sub},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=" AND ".join(conditions),
                ExpressionAttributeValues=values,
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            raise ChecklistMismatch()


class PolicyStore:
    """Policies 表:一位使用者一筆,policies 欄位放保單清單。找不到使用者回 None。"""

    def __init__(self):
        self._table = _table("POLICY_TABLE")

    def fetch(self, sub):
        item = self._table.get_item(Key={"sub": sub}).get("Item")
        return None if item is None else item.get("policies", [])


class FlightStore:
    def __init__(self):
        self._table = _table("FLIGHT_TABLE")

    def get(self, flight_number, departure_date):
        return self._table.get_item(
            Key={"flight_no": flight_number, "departure_date": departure_date}).get("Item")


class WeatherStore:
    def __init__(self):
        self._table = _table("WEATHER_TABLE")

    def get(self, city):
        return self._table.get_item(Key={"city": city}).get("Item")


class ExchangeRateStore:
    def __init__(self):
        self._table = _table("EXCHANGE_RATE_TABLE")

    def get(self, currency):
        return self._table.get_item(Key={"currency": currency}).get("Item")


@dataclass
class Deps:
    trips: object
    policies: object = None
    flights: object = None
    weather: object = None
    rates: object = None
    now: object = _now


_default_deps = None


def _get_default_deps():
    global _default_deps
    if _default_deps is None:  # Lambda 暖機時重複使用,不必每次重建
        _default_deps = Deps(TripStore(), PolicyStore(), FlightStore(), WeatherStore(), ExchangeRateStore())
    return _default_deps


# ---------- GET /v1/trip 的組裝 ----------

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


def build_policy(policy, now):
    today = (now.astimezone(TAIPEI)).date().isoformat()
    return {
        "productName": policy["product_name"],
        "policyNumberMasked": _mask_policy_number(policy["policy_number"]),
        "statusText": _policy_status_text(policy, today),
        "coverages": [{"label": c["label"], "amount": c["amount"]} for c in policy.get("coverages", [])],
    }


def _hhmm(iso_text):
    return datetime.fromisoformat(iso_text).strftime("%H:%M")


def build_flight(flight_number, departure_date, flight, now):
    """flight 是 Flights 表的一筆;資料不完整或機場不在對照表時丟 KeyError / ValueError。"""
    origin_city, _, _ = AIRPORTS[flight["origin_airport"]]
    dest_city, dest_country, dest_offset = AIRPORTS[flight["destination_airport"]]
    scheduled_departure = datetime.fromisoformat(flight["scheduled_departure"])
    scheduled_arrival = datetime.fromisoformat(flight["scheduled_arrival"])
    estimated_arrival = flight.get("estimated_arrival")
    return {
        "flightNumber": flight_number,
        "departureDate": departure_date,
        "origin": {"city": origin_city, "airportCode": flight["origin_airport"]},
        "destination": {
            "city": dest_city,
            "airportCode": flight["destination_airport"],
            "countryCode": dest_country,
            "localTime": (now + timedelta(hours=dest_offset)).strftime("%H:%M"),
        },
        "departureTime": _hhmm(flight["scheduled_departure"]),
        "arrivalTime": _hhmm(estimated_arrival or flight["scheduled_arrival"]),
        "scheduledDurationMinutes": int((scheduled_arrival - scheduled_departure).total_seconds() // 60),
        "isArrivalTimeEstimated": bool(estimated_arrival),
        "terminalGateText": flight.get("terminal_gate_text"),
        "baggageCarouselText": flight.get("baggage_carousel_text"),
    }


def build_weather(item):
    if not item or item.get("condition_code") not in WEATHER_CODES:
        return None  # 查不到或供應商代號無法對應時回 null
    return {
        "conditionCode": item["condition_code"],
        "temperatureCelsius": item["temperature_c"],
        "rainProbabilityPercent": item["rain_probability_percent"],
    }


def build_exchange_rate(currency, item):
    if not item:
        return None
    return {"value": item["rate_per_twd"], "targetCurrency": currency}


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


def _optional(source, future):
    """天氣、匯率失敗不影響其他資料,回 None。"""
    try:
        return future.result()
    except Exception as exc:
        logger.warning("source=%s failed: %s", source, type(exc).__name__)
        return None


def _required(future, status, code):
    try:
        return future.result()
    except Exception as exc:
        logger.error("%s: %s", code, type(exc).__name__)
        raise RequestError(status, code)


def _load_trip(deps, sub):
    try:
        return deps.trips.get(sub)
    except Exception as exc:
        logger.error("trip lookup failed: %s", type(exc).__name__)
        raise RequestError(502, "trip_unavailable")


# ---------- 各路由 ----------

def create_trip(event, deps):
    sub = get_user_sub(event)
    now = deps.now()
    flight_number, departure_date = parse_flight_input(event, now)
    try:
        deps.trips.save(sub, flight_number, departure_date, _iso(now), replace=False)
    except TripAlreadyExists:
        raise RequestError(409, "trip_already_exists")
    return _response(200, {})


def update_trip_flight(event, deps):
    sub = get_user_sub(event)
    now = deps.now()
    flight_number, departure_date = parse_flight_input(event, now)
    try:
        deps.trips.save(sub, flight_number, departure_date, _iso(now), replace=True)
    except TripNotFound:
        raise RequestError(404, "trip_not_found")
    return _response(200, {})


def get_trip(event, deps):
    sub = get_user_sub(event)
    now = deps.now()
    trip = _load_trip(deps, sub)
    if trip is None:
        return _response(200, {})  # 前端進入航班資訊輸入頁
    flight_number, departure_date = trip["flight_number"], trip["departure_date"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        # 第一階段:保單與航班同時查
        policy_future = pool.submit(deps.policies.fetch, sub)
        flight_future = pool.submit(deps.flights.get, flight_number, departure_date)
        policies = _required(policy_future, 502, "policy_unavailable")
        flight_item = _required(flight_future, 502, "flight_unavailable")

        policy = find_policy(policies, departure_date)
        if policy is None:
            raise RequestError(404, "policy_not_found")
        if flight_item is None:
            raise RequestError(502, "flight_unavailable")
        try:
            flight = build_flight(flight_number, departure_date, flight_item, now)
        except (KeyError, ValueError) as exc:
            logger.error("flight data incomplete: %s %s", type(exc).__name__, exc)
            raise RequestError(502, "flight_unavailable")

        # 第二階段:依目的地查天氣與匯率
        currency = CURRENCIES.get(flight["destination"]["countryCode"])
        weather_future = pool.submit(deps.weather.get, flight["destination"]["city"])
        rate_future = pool.submit(deps.rates.get, currency) if currency else None
        weather = build_weather(_optional("weather", weather_future))
        exchange_rate = build_exchange_rate(currency, _optional("exchange_rate", rate_future)) if rate_future else None

    trip_status, disruption, delay_claim = build_status_parts(trip)
    return _response(200, {"data": {
        "user": {"displayName": policy["policyholder_name"]},
        "tripStatus": trip_status,
        "flight": flight,
        "policy": build_policy(policy, now),
        "weather": weather,
        "exchangeRate": exchange_rate,
        "disruption": disruption,
        "delayClaim": delay_claim,
        "support": {
            "alternateFlightUrl": ALTERNATE_FLIGHT_URL,
            "accommodationUrl": ACCOMMODATION_URL,
            "airlinePhone": AIRLINE_PHONES.get(flight_number[:2]),
        },
    }})


def update_claim_checklist(event, deps):
    sub = get_user_sub(event)
    checklist = parse_checklist_input(event)
    trip = _load_trip(deps, sub)
    if trip is None:
        raise RequestError(404, "trip_not_found")

    claim = trip.get("delay_claim") or {}
    stored_labels = [c["label"] for c in claim.get("checklist", [])]
    if not claim.get("eligible") or not stored_labels:
        raise RequestError(409, "claim_not_eligible")
    if [label for label, _ in checklist] != stored_labels:
        raise RequestError(409, "checklist_mismatch")

    try:
        deps.trips.set_checklist_ready(sub, checklist)
    except ChecklistMismatch:  # 讀取後到寫入前被事件驅動流程或旅程覆蓋改掉
        raise RequestError(409, "checklist_mismatch")
    return _response(200, {})


ROUTES = {
    ("POST", "/v1/trip"): create_trip,
    ("PUT", "/v1/trip/flight"): update_trip_flight,
    ("GET", "/v1/trip"): get_trip,
    ("PATCH", "/v1/trip/claim/checklist"): update_claim_checklist,
}


def handle(event, deps):
    try:
        route = ROUTES.get((event.get("httpMethod"), event.get("resource")))
        if route is None:
            raise RequestError(404, "route_not_found")
        return route(event, deps)
    except RequestError as exc:
        return _error(exc.status, exc.code)
    except Exception:
        logger.exception("unexpected error")
        return _error(500, "internal_error")


def lambda_handler(event, context):
    return handle(event, _get_default_deps())
