"""AgentCore Gateway Tool Lambda:get-custom-info

依 agent-tool-interface-contract-v1.md 第 3 節,查詢已授權旅客的航班、航班狀態、目的地天氣、保單摘要。
重複使用 trip_management 的資料表元件與組裝函式,部署時兩個檔案要一起放進 Lambda。

輸入:Gateway 傳入的 event 就是 Tool input,例如 {"sections": ["flight", "flight_status"]}
     (Gateway 實際的 event 格式需在整合時確認,log 會記下 event 的 key 方便比對)
輸出:合約第 5 節的 envelope:schemaVersion / status / source / data / error
     data 只放這次 sections 要求的資料:flight / flightStatus / weather / policySummary

身分:合約規定 sub 不得由模型填入。Gateway 的身分傳遞方式尚未定案,目前只讀環境變數 MOCK_SUB;
     沒設定就回 UNAUTHORIZED,不會改用預設帳號。定案後只需修改 resolve_sub。

錯誤:
  UNAUTHORIZED         沒有可信的使用者身分
  INVALID_ARGUMENT     sections 缺少、重複或不在允許清單
  NO_ACTIVE_TRIP       使用者沒有目前旅程
  NOT_FOUND            需要航班資料(flight / weather),但 Flights 表查無這班或資料不完整
  SERVICE_UNAVAILABLE  DynamoDB 查詢失敗(retryable)
天氣查詢失敗或沒有近 3 小時的資料時 weather 為 null;沒有保單涵蓋出發日期時 policySummary 為 null。
非預期的程式錯誤不轉成業務錯誤,直接丟出讓 Lambda 平台記錄(合約第 5 節:平台例外與業務錯誤分開)。

環境變數:TRIP_TABLE、POLICY_TABLE、FLIGHT_TABLE、WEATHER_TABLE、MOCK_SUB
IAM:Trips、Policies、Flights 需要 GetItem;Weather 需要 Query。只給讀取權限。
"""
import json
import logging
import os

import trip_management as tm

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SCHEMA_VERSION = "1.0"
SOURCE_SYSTEM = "trip_management"
IS_MOCK = True  # 資料表目前都是 seed 假資料;接上真實資料後改為 False
SECTIONS = ("flight", "flight_status", "weather", "policy_summary")

ERROR_MESSAGES = {
    "UNAUTHORIZED": "無法確認使用者身分",
    "INVALID_ARGUMENT": "sections 參數不正確",
    "NO_ACTIVE_TRIP": "目前沒有可查詢的旅程",
    "NOT_FOUND": "查無航班資料",
    "SERVICE_UNAVAILABLE": "資料服務暫時無法使用,請稍後再試",
}
RETRYABLE = {"SERVICE_UNAVAILABLE"}


class ToolError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def resolve_sub(event, context, env=os.environ):
    """取得可信的使用者 sub。目前只有 Mock:讀 MOCK_SUB;不從 event(模型填的參數)取。"""
    return env.get("MOCK_SUB") or None


def parse_sections(event):
    if not isinstance(event, dict) or set(event) != {"sections"}:
        raise ToolError("INVALID_ARGUMENT")
    sections = event["sections"]
    if (not isinstance(sections, list) or not sections
            or any(s not in SECTIONS for s in sections) or len(set(sections)) != len(sections)):
        raise ToolError("INVALID_ARGUMENT")
    return set(sections)


def _call(source, func, *args):
    try:
        return func(*args)
    except Exception as exc:
        logger.error("source=%s failed: %s", source, type(exc).__name__)
        raise ToolError("SERVICE_UNAVAILABLE")


def build_flight(trip, flight):
    """flight 是 Flights 表的一筆;資料不完整或機場不在對照表時丟 KeyError。"""
    origin, destination = flight["origin_airport"], flight["destination_airport"]
    return {
        "flightNumber": trip["flight_number"],
        "departureDate": trip["departure_date"],
        "originAirportCode": origin,
        "originCity": tm.AIRPORTS[origin][0],
        "destinationAirportCode": destination,
        "destinationCity": tm.AIRPORTS[destination][0],
        # 含時差的 ISO 時間,模型可直接讀出當地時間
        "scheduledDeparture": flight["scheduled_departure"],
        "scheduledArrival": flight["scheduled_arrival"],
        "estimatedArrival": flight.get("estimated_arrival"),
        "terminalGateText": flight.get("terminal_gate_text"),
        "baggageCarouselText": flight.get("baggage_carousel_text"),
    }


def build_flight_status(trip):
    trip_status, disruption, _ = tm.build_status_parts(trip)
    return {
        "state": trip_status["state"],
        "delayMinutes": (disruption or {}).get("delayMinutes", 0),
        "updatedAt": trip_status["updatedAt"],
    }


def load_weather(deps, city, now):
    try:
        item = deps.weather.get(city, now)
    except Exception as exc:
        logger.warning("source=weather failed: %s", type(exc).__name__)
        return None
    weather = tm.build_weather(item)
    if weather is None:
        return None
    return {"city": city, **weather, "forecastTime": item["forecast_time"], "updatedAt": item.get("updated_at")}


def collect(sections, deps, sub, now):
    trip = _call("trip", deps.trips.get, sub)
    if trip is None:
        raise ToolError("NO_ACTIVE_TRIP")
    data = {}

    if sections & {"flight", "weather"}:  # 天氣要靠航班資料才知道目的地
        item = _call("flight", deps.flights.get, trip["flight_number"], trip["departure_date"])
        try:
            flight = build_flight(trip, item)
        except (KeyError, TypeError) as exc:  # 查無航班(item 為 None)或資料不完整
            logger.error("flight data missing or incomplete: %s", type(exc).__name__)
            raise ToolError("NOT_FOUND")
        if "flight" in sections:
            data["flight"] = flight
        if "weather" in sections:
            data["weather"] = load_weather(deps, flight["destinationCity"], now)

    if "flight_status" in sections:
        data["flightStatus"] = build_flight_status(trip)

    if "policy_summary" in sections:
        policies = _call("policy", deps.policies.fetch, sub)
        policy = tm.find_policy(policies, trip["departure_date"])
        data["policySummary"] = tm.build_policy(policy, now) if policy else None

    return data


def envelope(now, data=None, code=None):
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "status": "error" if code else "success",
        "source": {"system": SOURCE_SYSTEM, "isMock": IS_MOCK, "retrievedAt": tm._iso(now)},
        "data": None if code else data,
        "error": {"code": code, "message": ERROR_MESSAGES[code], "retryable": code in RETRYABLE} if code else None,
    }
    return json.loads(json.dumps(body, default=tm._json_default))  # Decimal 轉成一般數字


def handle(event, deps, sub):
    now = deps.now()
    try:
        if not sub:
            raise ToolError("UNAUTHORIZED")
        data = collect(parse_sections(event), deps, sub, now)
    except ToolError as err:
        logger.info("tool error: %s", err.code)
        return envelope(now, code=err.code)
    return envelope(now, data)


_default_deps = None


def _get_default_deps():
    global _default_deps
    if _default_deps is None:  # Lambda 暖機時重複使用,不必每次重建
        _default_deps = tm.Deps(tm.TripStore(), tm.PolicyStore(), tm.FlightStore(), tm.WeatherStore())
    return _default_deps


def lambda_handler(event, context):
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    # 只記 key 與 Tool 名稱,不記個資;用來確認 Gateway 實際傳入的格式
    logger.info("tool=%s event_keys=%s", custom.get("bedrockAgentCoreToolName"),
                sorted(event) if isinstance(event, dict) else type(event).__name__)
    return handle(event, _get_default_deps(), resolve_sub(event, context))
