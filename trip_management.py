"""旅程管理 Lambda(Trip Management)——學習版:資料全部來自 DynamoDB 假資料

輸入:API Gateway 驗證過的登入身分(Cognito sub)。
      不接受 body / query 帶入的使用者 ID,避免查到別人的資料。
輸出:{"data": {"policies": [...], "flights": [...], "weather": [...]},
       "errors": [...], "partial": bool}

流程:
  1. 由登入身分取得 sub
  2. sub -> Policies 表,取得保單與行程(找不到回 404)
  3. 平行取得:Flights 表(航班狀態)、Weather 表(各目的地天氣)
  4. 航班或天氣失敗時回傳部分結果,並在 errors 標記缺哪一塊

DynamoDB 資料表(PK = 主鍵,SK = 排序鍵):
  Policies : PK sub                              屬性 policies(保單清單,內含 itinerary)
  Flights  : PK flight_no, SK departure_date     屬性 status, delay_minutes ...
  Weather  : PK city                             屬性 summary, temperature_c

環境變數:POLICY_TABLE、FLIGHT_TABLE、WEATHER_TABLE
"""
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 只回傳這些航班欄位,避免把事件驅動的內部欄位(例如上次通知等級)露給前端
FLIGHT_FIELDS = ("status", "delay_minutes", "scheduled_departure", "last_updated")


class RequestError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


# ---------- 共用工具 ----------

def _json_default(value):
    if isinstance(value, Decimal):  # DynamoDB 的數字會變成 Decimal
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"not serializable: {type(value).__name__}")


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json; charset=utf-8"},
        "body": json.dumps(body, ensure_ascii=False, default=_json_default),
    }


def _normalize_flight_no(flight_no):
    return flight_no.replace(" ", "").upper()


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


def _batch_get(resource, table, keys):
    """一次查多筆,處理單次 100 筆上限與 UnprocessedKeys(DynamoDB 忙碌時未處理的部分)。"""
    items = []
    for start in range(0, len(keys), 100):
        request = {table: {"Keys": keys[start:start + 100]}}
        for _ in range(3):
            resp = resource.batch_get_item(RequestItems=request)
            items.extend(resp["Responses"].get(table, []))
            request = resp.get("UnprocessedKeys") or {}
            if not request:
                break
        else:
            raise RuntimeError("batch_get_item unprocessed keys")
    return items


# ---------- 實際的 DynamoDB 元件(只在 Lambda 啟動時建立) ----------

class PolicyStore:
    """Policies 表:一位使用者一筆,policies 欄位放保單清單。"""

    def __init__(self):
        import boto3
        self._table = boto3.resource("dynamodb").Table(os.environ["POLICY_TABLE"])

    def fetch(self, sub):
        item = self._table.get_item(Key={"sub": sub}).get("Item")
        return None if item is None else item.get("policies", [])


class FlightStore:
    def __init__(self):
        import boto3
        self._resource = boto3.resource("dynamodb")
        self._table = os.environ["FLIGHT_TABLE"]

    def get_many(self, keys):
        keys = [{"flight_no": f, "departure_date": d} for f, d in keys]
        items = _batch_get(self._resource, self._table, keys)
        return {(i["flight_no"], i["departure_date"]): i for i in items}


class WeatherStore:
    def __init__(self):
        import boto3
        self._resource = boto3.resource("dynamodb")
        self._table = os.environ["WEATHER_TABLE"]

    def get_many(self, cities):
        items = _batch_get(self._resource, self._table, [{"city": c} for c in cities])
        return {
            i["city"]: {"summary": i.get("summary"), "temperature_c": i.get("temperature_c")}
            for i in items
        }


@dataclass
class Deps:
    policies: object
    flights: object
    weather: object


_default_deps = None


def _get_default_deps():
    global _default_deps
    if _default_deps is None:  # Lambda 暖機時重複使用,不必每次重建
        _default_deps = Deps(PolicyStore(), FlightStore(), WeatherStore())
    return _default_deps


# ---------- 主流程 ----------

def _collect(policies):
    flight_keys, cities = [], []
    for policy in policies:
        for leg in policy.get("itinerary", []):
            if leg.get("flight_no") and leg.get("departure_date"):
                flight_keys.append((_normalize_flight_no(leg["flight_no"]), leg["departure_date"]))
            if leg.get("destination"):
                cities.append(leg["destination"])
    return list(dict.fromkeys(flight_keys)), list(dict.fromkeys(cities))


def _result(source, future, errors):
    try:
        return future.result()
    except Exception as exc:  # 單項失敗不影響其他資料
        logger.warning("source=%s failed: %s", source, type(exc).__name__)
        errors.append({"source": source, "reason": type(exc).__name__})
        return None


def handle(event, deps):
    try:
        sub = get_user_sub(event)
        try:
            policies = deps.policies.fetch(sub)
        except Exception as exc:
            logger.error("policy lookup failed: %s", type(exc).__name__)
            raise RequestError(502, "policy_unavailable")
        if policies is None:
            raise RequestError(404, "policyholder_not_found")

        flight_keys, cities = _collect(policies)
        errors = []
        with ThreadPoolExecutor(max_workers=2) as pool:  # 航班與天氣同時查
            flight_future = pool.submit(deps.flights.get_many, flight_keys) if flight_keys else None
            weather_future = pool.submit(deps.weather.get_many, cities) if cities else None
            found = _result("flights", flight_future, errors) if flight_future else {}
            weather_rows = _result("weather", weather_future, errors) if weather_future else {}

        flights = []
        if found is not None:
            for flight_no, date in flight_keys:
                item = found.get((flight_no, date))
                row = {"flight_no": flight_no, "departure_date": date, "monitored": item is not None}
                if item:
                    row.update({k: item[k] for k in FLIGHT_FIELDS if k in item})
                flights.append(row)

        weather = []
        if weather_rows is not None:
            for city in cities:
                if city in weather_rows:
                    weather.append({"destination": city, **weather_rows[city]})
                else:  # 表裡沒有這個城市,屬於資料缺漏,不是系統故障
                    errors.append({"source": "weather", "reason": "NotFound", "target": city})

        return _response(200, {
            "data": {"policies": policies, "flights": flights, "weather": weather},
            "errors": errors,
            "partial": bool(errors),
        })
    except RequestError as exc:
        return _response(exc.status, {"error": exc.code})
    except Exception:
        logger.exception("unexpected error")
        return _response(500, {"error": "internal_error"})


def lambda_handler(event, context):
    return handle(event, _get_default_deps())
