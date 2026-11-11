import copy
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import trip_management as tm

NOW = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)  # 台灣時間 11/2 11:00
NOW_ISO = "2026-11-02T03:00:00Z"


class FakeTrips:
    """用 dict 模擬 Trips 表;行為和真的 TripStore 一致(重複建立、找不到時丟例外,覆蓋時整筆取代)。"""

    def __init__(self, items=None, error=None, race=False):
        self.items, self.error = copy.deepcopy(items or {}), error
        self.race = race  # 模擬讀取後、寫入前 checklist 被其他流程改掉

    def get(self, sub):
        if self.error:
            raise self.error
        return copy.deepcopy(self.items.get(sub))

    def set_checklist_ready(self, sub, checklist):
        claim = self.items[sub]["delay_claim"]
        if self.race or [c["label"] for c in claim["checklist"]] != [label for label, _ in checklist]:
            raise tm.ChecklistMismatch()
        for stored, (_, is_ready) in zip(claim["checklist"], checklist):
            stored["is_ready"] = is_ready

    def save(self, sub, flight_number, departure_date, now, *, replace):
        if self.error:
            raise self.error
        if replace and sub not in self.items:
            raise tm.TripNotFound()
        if not replace and sub in self.items:
            raise tm.TripAlreadyExists()
        self.items[sub] = {"flight_number": flight_number, "departure_date": departure_date,
                           "created_at": now, "updated_at": now}


EXISTING = {"sub-1": {"flight_number": "BR192", "departure_date": "2026-11-03",
                      "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z",
                      "trip_status": {"state": "ABNORMAL"}}}  # 模擬事件驅動流程寫入的監控狀態


def make_event(method, resource, body=None, sub="sub-1", http_api=False, raw_body=None):
    claims = {"sub": sub} if sub else {}
    auth = {"jwt": {"claims": claims}} if http_api else {"claims": claims}
    return {
        "httpMethod": method,
        "resource": resource,
        "requestContext": {"authorizer": auth},
        "body": raw_body if raw_body is not None else (json.dumps(body) if body is not None else None),
    }


def call(event, trips, now=NOW):
    resp = tm.handle(event, tm.Deps(trips, now=lambda: now))
    return resp["statusCode"], json.loads(resp["body"])


class CreateTripTest(unittest.TestCase):
    def test_creates_trip(self):
        trips = FakeTrips()
        status, body = call(make_event("POST", "/v1/trip",
                                       {"flightNumber": "BR192", "departureDate": "2026-11-03"}), trips)
        self.assertEqual((status, body), (200, {}))
        self.assertEqual(trips.items["sub-1"], {"flight_number": "BR192", "departure_date": "2026-11-03",
                                                "created_at": NOW_ISO, "updated_at": NOW_ISO})

    def test_normalizes_flight_number(self):
        trips = FakeTrips()
        call(make_event("POST", "/v1/trip", {"flightNumber": " br 192 ", "departureDate": "2026-11-03"}), trips)
        self.assertEqual(trips.items["sub-1"]["flight_number"], "BR192")

    def test_accepts_http_api_claims(self):
        trips = FakeTrips()
        status, _ = call(make_event("POST", "/v1/trip", {"flightNumber": "7C1101", "departureDate": "2026-11-03"},
                                    http_api=True), trips)
        self.assertEqual(status, 200)

    def test_existing_trip_returns_409(self):
        trips = FakeTrips(EXISTING)
        status, body = call(make_event("POST", "/v1/trip",
                                       {"flightNumber": "CI100", "departureDate": "2026-11-04"}), trips)
        self.assertEqual((status, body["error"]), (409, "trip_already_exists"))
        self.assertEqual(trips.items["sub-1"]["flight_number"], "BR192")  # 不可覆蓋

    def test_ignores_user_id_in_body(self):
        trips = FakeTrips()
        call(make_event("POST", "/v1/trip", {"flightNumber": "BR192", "departureDate": "2026-11-03",
                                             "sub": "someone-else"}), trips)
        self.assertEqual(list(trips.items), ["sub-1"])


class UpdateFlightTest(unittest.TestCase):
    def test_replaces_whole_trip(self):
        trips = FakeTrips(EXISTING)
        status, body = call(make_event("PUT", "/v1/trip/flight",
                                       {"flightNumber": "CI100", "departureDate": "2026-11-04"}), trips)
        self.assertEqual((status, body), (200, {}))
        # 舊旅程(含監控狀態)整筆被新旅程取代
        self.assertEqual(trips.items["sub-1"], {"flight_number": "CI100", "departure_date": "2026-11-04",
                                                "created_at": NOW_ISO, "updated_at": NOW_ISO})

    def test_no_trip_returns_404(self):
        status, body = call(make_event("PUT", "/v1/trip/flight",
                                       {"flightNumber": "CI100", "departureDate": "2026-11-04"}), FakeTrips())
        self.assertEqual((status, body["error"]), (404, "trip_not_found"))


class ValidationTest(unittest.TestCase):
    def assert_invalid(self, event):
        status, body = call(event, FakeTrips())
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        self.assertTrue(body["message"])

    def test_invalid_json(self):
        self.assert_invalid(make_event("POST", "/v1/trip", raw_body="{not json"))

    def test_missing_body(self):
        self.assert_invalid(make_event("POST", "/v1/trip"))

    def test_body_not_object(self):
        self.assert_invalid(make_event("POST", "/v1/trip", ["BR192"]))

    def test_missing_field(self):
        self.assert_invalid(make_event("PUT", "/v1/trip/flight", {"flightNumber": "BR192"}))

    def test_bad_flight_number(self):
        for value in ["", "B", "BR", "BR12345", "BR-192", 192]:
            with self.subTest(value=value):
                self.assert_invalid(make_event("POST", "/v1/trip",
                                               {"flightNumber": value, "departureDate": "2026-11-03"}))

    def test_bad_date(self):
        for value in ["2026/11/03", "20261103", "2026-02-30", "2026-11-03T10:00", None]:
            with self.subTest(value=value):
                self.assert_invalid(make_event("POST", "/v1/trip",
                                               {"flightNumber": "BR192", "departureDate": value}))


class DepartureDateTest(unittest.TestCase):
    def post(self, departure_date, now=NOW):
        return call(make_event("POST", "/v1/trip", {"flightNumber": "BR192", "departureDate": departure_date}),
                    FakeTrips(), now)

    def test_past_date_rejected(self):
        status, body = self.post("2026-10-31")
        self.assertEqual((status, body["error"], body["message"]),
                         (400, "departure_date_in_past", "時間不對,請重新填寫"))

    def test_today_and_future_accepted(self):
        for value in ["2026-11-02", "2026-11-03", "2027-01-01"]:
            with self.subTest(value=value):
                self.assertEqual(self.post(value)[0], 200)

    def test_put_also_checks_past_date(self):
        status, body = call(make_event("PUT", "/v1/trip/flight",
                                       {"flightNumber": "BR192", "departureDate": "2026-10-31"}),
                            FakeTrips(EXISTING))
        self.assertEqual((status, body["error"]), (400, "departure_date_in_past"))

    def test_uses_latest_time_zone(self):
        # UTC 11/2 03:00 時,UTC-12 還是 11/1,所以 11/1 仍接受;10/31 已經全球都過了
        self.assertEqual(self.post("2026-11-01")[0], 200)
        # UTC 11/2 12:00 時,UTC-12 也進入 11/2,11/1 就不接受
        late = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(self.post("2026-11-01", now=late)[1]["error"], "departure_date_in_past")


class CommonTest(unittest.TestCase):
    def test_missing_sub_returns_401(self):
        status, body = call(make_event("POST", "/v1/trip",
                                       {"flightNumber": "BR192", "departureDate": "2026-11-03"}, sub=None),
                            FakeTrips())
        self.assertEqual((status, body["error"]), (401, "unauthorized"))

    def test_unknown_route_returns_404(self):
        status, body = call(make_event("DELETE", "/v1/trip"), FakeTrips())
        self.assertEqual((status, body["error"]), (404, "route_not_found"))

    def test_store_failure_returns_500(self):
        status, body = call(make_event("POST", "/v1/trip",
                                       {"flightNumber": "BR192", "departureDate": "2026-11-03"}),
                            FakeTrips(error=RuntimeError("boom")))
        self.assertEqual((status, body["error"]), (500, "internal_error"))

    def test_response_headers(self):
        resp = tm.handle(make_event("POST", "/v1/trip", {"flightNumber": "BR192", "departureDate": "2026-11-03"}),
                         tm.Deps(FakeTrips(), now=lambda: NOW))
        self.assertEqual(resp["headers"]["Cache-Control"], "private, no-store")


# ---------- 06:GET /v1/trip、PATCH /v1/trip/claim/checklist ----------

class FakeTable:
    """模擬只用 get 查一筆的資料表:rows 以 key 查,error 時丟例外。"""

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or {}, error, []

    def _get(self, key):
        self.calls.append(key)
        if self.error:
            raise self.error
        return copy.deepcopy(self.rows.get(key))


class FakePolicies(FakeTable):
    def fetch(self, sub):
        return self._get(sub)


class FakeFlights(FakeTable):
    def get(self, flight_number, departure_date):
        return self._get((flight_number, departure_date))


class FakeByKey(FakeTable):
    def get(self, key):
        return self._get(key)


class FakeWeather(FakeTable):
    def get(self, city, now):
        return self._get((city, now))


POLICY = {
    "policy_number": "TA-20261103-01",
    "policyholder_name": "王小明",
    "product_name": "國泰安心遊日本 5 日型",
    "coverage_start": "2026-11-01",
    "coverage_end": "2026-11-07",
    "coverages": [{"label": "海外突發醫療", "amount": Decimal("1000000")},
                  {"label": "班機延誤保險金", "amount": Decimal("5000")}],
}
OTHER_POLICY = dict(POLICY, policy_number="TA-20261201-02", product_name="另一張",
                    coverage_start="2026-12-01", coverage_end="2026-12-05")

FLIGHT = {
    "flight_no": "BR192", "departure_date": "2026-11-03",
    "origin_airport": "TPE", "destination_airport": "NRT",
    "scheduled_departure": "2026-11-03T10:15:00+08:00",
    "scheduled_arrival": "2026-11-03T14:30:00+09:00",
    "terminal_gate_text": "第一航廈・登機門 B6",
    "baggage_carousel_text": None,
}

TRIP = {"flight_number": "BR192", "departure_date": "2026-11-03",
        "created_at": "2026-11-01T00:00:00Z", "updated_at": "2026-11-01T00:00:00Z"}

ABNORMAL_TRIP = dict(
    TRIP,
    trip_status={"state": "ABNORMAL", "type": "FLIGHT_DELAY", "message": "航班延誤監測中・已超過 4 小時",
                 "updated_at": "2026-11-03T05:15:00Z"},
    disruption={"type": "FLIGHT_DELAY", "title": "航班延誤", "description": "航班預計延誤 5 小時",
                "occurred_at": "2026-11-03T01:15:00Z", "delay_minutes": Decimal("300")},
    delay_claim={"eligible": True, "estimated_benefit_amount": Decimal("5000"), "case_status": "CREATED",
                 "case_status_text": "系統已快速立案",
                 "checklist": [{"label": "原班機登機證", "is_ready": True},
                               {"label": "航司班機延誤證明書", "is_ready": False}]},
)


def make_full_deps(trip=TRIP, **overrides):
    parts = {
        "trips": FakeTrips({"sub-1": trip} if trip else {}),
        "policies": FakePolicies({"sub-1": [OTHER_POLICY, POLICY]}),
        "flights": FakeFlights({("BR192", "2026-11-03"): FLIGHT}),
        "weather": FakeWeather({("東京", NOW): {"city": "東京", "condition_code": "PARTLY_CLOUDY",
                                               "temperature_c": Decimal("22"), "rain_probability_percent": Decimal("10")}}),
        "rates": FakeByKey({"JPY": {"currency": "JPY", "rate_per_twd": Decimal("4.85")}}),
    }
    parts.update(overrides)
    return tm.Deps(**parts, now=lambda: NOW)


def get_trip(deps):
    resp = tm.handle(make_event("GET", "/v1/trip"), deps)
    return resp["statusCode"], json.loads(resp["body"])


class GetTripTest(unittest.TestCase):
    def test_no_trip_returns_empty(self):
        deps = make_full_deps(trip=None)
        self.assertEqual(get_trip(deps), (200, {}))
        self.assertEqual(deps.policies.calls, [])  # 沒旅程就不查其他資料

    def test_normal_trip(self):
        status, body = get_trip(make_full_deps())
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertEqual(data["user"], {"displayName": "王小明"})
        self.assertEqual(data["tripStatus"], {"state": "NORMAL", "type": None, "message": "航班動態監測中",
                                              "updatedAt": "2026-11-01T00:00:00Z"})
        self.assertEqual(data["flight"], {
            "flightNumber": "BR192", "departureDate": "2026-11-03",
            "origin": {"city": "台北", "airportCode": "TPE"},
            "destination": {"city": "東京", "airportCode": "NRT", "countryCode": "JP", "localTime": "12:00"},
            "departureTime": "10:15", "arrivalTime": "14:30", "scheduledDurationMinutes": 195,
            "isArrivalTimeEstimated": False, "terminalGateText": "第一航廈・登機門 B6", "baggageCarouselText": None,
        })
        self.assertEqual(data["policy"], {
            "productName": "國泰安心遊日本 5 日型", "policyNumberMasked": "TA-2026****-01",
            "statusText": "保障生效中",
            "coverages": [{"label": "海外突發醫療", "amount": 1000000}, {"label": "班機延誤保險金", "amount": 5000}],
        })
        self.assertEqual(data["weather"], {"conditionCode": "PARTLY_CLOUDY", "temperatureCelsius": 22,
                                           "rainProbabilityPercent": 10})
        self.assertEqual(data["exchangeRate"], {"value": 4.85, "targetCurrency": "JPY"})
        self.assertIsNone(data["disruption"])
        self.assertEqual(data["delayClaim"], {"eligible": False, "estimatedBenefitAmount": None,
                                              "caseStatus": "NOT_CREATED", "caseStatusText": None, "checklist": []})
        self.assertEqual(data["support"], {"alternateFlightUrl": "https://tw.trip.com/flights/",
                                           "accommodationUrl": "https://www.agoda.com/",
                                           "airlinePhone": "0800098666"})

    def test_abnormal_trip_with_claim(self):
        flight = dict(FLIGHT, estimated_arrival="2026-11-03T19:30:00+09:00", baggage_carousel_text="行李轉盤 4 號")
        deps = make_full_deps(trip=ABNORMAL_TRIP, flights=FakeFlights({("BR192", "2026-11-03"): flight}))
        data = get_trip(deps)[1]["data"]
        self.assertEqual(data["tripStatus"]["state"], "ABNORMAL")
        self.assertEqual(data["tripStatus"]["type"], "FLIGHT_DELAY")
        self.assertEqual(data["disruption"], {"type": "FLIGHT_DELAY", "title": "航班延誤",
                                              "description": "航班預計延誤 5 小時",
                                              "occurredAt": "2026-11-03T01:15:00Z", "delayMinutes": 300})
        self.assertEqual((data["flight"]["arrivalTime"], data["flight"]["isArrivalTimeEstimated"]), ("19:30", True))
        self.assertEqual(data["flight"]["scheduledDurationMinutes"], 195)  # 原定航程不受延誤影響
        self.assertEqual(data["delayClaim"], {
            "eligible": True, "estimatedBenefitAmount": 5000, "caseStatus": "CREATED",
            "caseStatusText": "系統已快速立案",
            "checklist": [{"label": "原班機登機證", "isReady": True}, {"label": "航司班機延誤證明書", "isReady": False}],
        })

    def test_abnormal_but_not_eligible(self):
        trip = dict(ABNORMAL_TRIP, delay_claim={"eligible": False, "estimated_benefit_amount": Decimal("5000")})
        claim = get_trip(make_full_deps(trip=trip))[1]["data"]["delayClaim"]
        self.assertEqual((claim["eligible"], claim["estimatedBenefitAmount"]), (False, None))

    def test_normal_state_hides_disruption(self):
        trip = dict(ABNORMAL_TRIP, trip_status={"state": "NORMAL", "type": "FLIGHT_DELAY",
                                                "message": "已恢復", "updated_at": "2026-11-03T06:30:00Z"})
        data = get_trip(make_full_deps(trip=trip))[1]["data"]
        self.assertIsNone(data["disruption"])
        self.assertIsNone(data["tripStatus"]["type"])

    def test_policy_must_cover_departure_date(self):
        deps = make_full_deps(policies=FakePolicies({"sub-1": [OTHER_POLICY]}))
        status, body = get_trip(deps)
        self.assertEqual((status, body["error"], body["message"]), (404, "policy_not_found", "查無保單資訊"))

    def test_no_policyholder_returns_404(self):
        status, body = get_trip(make_full_deps(policies=FakePolicies({})))
        self.assertEqual((status, body["error"]), (404, "policy_not_found"))

    def test_policy_status_text(self):
        policy = dict(POLICY, coverage_start="2026-11-03")
        data = get_trip(make_full_deps(policies=FakePolicies({"sub-1": [policy]})))[1]["data"]
        self.assertEqual(data["policy"]["statusText"], "保障尚未生效")

    def test_flight_not_found_returns_502(self):
        status, body = get_trip(make_full_deps(flights=FakeFlights({})))
        self.assertEqual((status, body["error"], body["message"]), (502, "flight_unavailable", "查無航班資訊,請修改航班"))

    def test_unknown_airport_returns_502(self):
        flight = dict(FLIGHT, destination_airport="XXX")
        status, body = get_trip(make_full_deps(flights=FakeFlights({("BR192", "2026-11-03"): flight})))
        self.assertEqual((status, body["error"]), (502, "flight_unavailable"))

    def test_upstream_failures(self):
        cases = {
            "trips": (FakeTrips(error=RuntimeError("down")), "trip_unavailable"),
            "policies": (FakePolicies(error=RuntimeError("down")), "policy_unavailable"),
            "flights": (FakeFlights(error=RuntimeError("down")), "flight_unavailable"),
        }
        for name, (fake, code) in cases.items():
            with self.subTest(name=name):
                status, body = get_trip(make_full_deps(**{name: fake}))
                self.assertEqual((status, body["error"]), (502, code))

    def test_weather_and_rate_failures_return_null(self):
        deps = make_full_deps(weather=FakeWeather(error=RuntimeError("down")), rates=FakeByKey(error=RuntimeError("down")))
        status, body = get_trip(deps)
        self.assertEqual(status, 200)
        self.assertIsNone(body["data"]["weather"])
        self.assertIsNone(body["data"]["exchangeRate"])

    def test_unknown_weather_code_returns_null(self):
        weather = FakeWeather({("東京", NOW): {"condition_code": "HAZE", "temperature_c": 1, "rain_probability_percent": 0}})
        self.assertIsNone(get_trip(make_full_deps(weather=weather))[1]["data"]["weather"])

    def test_no_recent_weather_returns_null(self):
        self.assertIsNone(get_trip(make_full_deps(weather=FakeWeather()))[1]["data"]["weather"])

    def test_weather_time_range_is_last_three_hours(self):
        self.assertEqual(tm.weather_time_range(NOW), ("2026-11-02T00:00:00Z", NOW_ISO))

    def test_domestic_trip_has_no_exchange_rate(self):
        flight = dict(FLIGHT, destination_airport="KHH")
        deps = make_full_deps(flights=FakeFlights({("BR192", "2026-11-03"): flight}))
        self.assertIsNone(get_trip(deps)[1]["data"]["exchangeRate"])
        self.assertEqual(deps.rates.calls, [])

    def test_unknown_airline_phone_is_null(self):
        trip = dict(TRIP, flight_number="CI100")
        deps = make_full_deps(trip=trip, flights=FakeFlights({("CI100", "2026-11-03"): FLIGHT}))
        self.assertIsNone(get_trip(deps)[1]["data"]["support"]["airlinePhone"])


class MaskPolicyNumberTest(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(tm._mask_policy_number("TA-20261103-01"), "TA-2026****-01")
        self.assertEqual(tm._mask_policy_number("AB12345"), "****345")


def patch_checklist(deps, checklist, raw_body=None):
    resp = tm.handle(make_event("PATCH", "/v1/trip/claim/checklist", {"checklist": checklist}, raw_body=raw_body), deps)
    return resp["statusCode"], json.loads(resp["body"])


READY_ALL = [{"label": "原班機登機證", "isReady": True}, {"label": "航司班機延誤證明書", "isReady": True}]


class UpdateChecklistTest(unittest.TestCase):
    def test_updates_is_ready(self):
        deps = make_full_deps(trip=ABNORMAL_TRIP)
        self.assertEqual(patch_checklist(deps, READY_ALL), (200, {}))
        stored = deps.trips.items["sub-1"]["delay_claim"]["checklist"]
        self.assertEqual([c["is_ready"] for c in stored], [True, True])

    def test_label_mismatch(self):
        for checklist in [READY_ALL[::-1], READY_ALL[:1], READY_ALL + [{"label": "新增", "isReady": True}],
                          [{"label": "改寫", "isReady": True}, READY_ALL[1]]]:
            with self.subTest(checklist=checklist):
                status, body = patch_checklist(make_full_deps(trip=ABNORMAL_TRIP), checklist)
                self.assertEqual((status, body["error"]), (409, "checklist_mismatch"))

    def test_changed_after_read(self):
        deps = make_full_deps(trip=ABNORMAL_TRIP, trips=FakeTrips({"sub-1": ABNORMAL_TRIP}, race=True))
        status, body = patch_checklist(deps, READY_ALL)
        self.assertEqual((status, body["error"]), (409, "checklist_mismatch"))

    def test_not_eligible(self):
        for trip in [TRIP, dict(ABNORMAL_TRIP, delay_claim=dict(ABNORMAL_TRIP["delay_claim"], eligible=False))]:
            with self.subTest(trip=trip.get("delay_claim")):
                status, body = patch_checklist(make_full_deps(trip=trip), READY_ALL)
                self.assertEqual((status, body["error"]), (409, "claim_not_eligible"))

    def test_no_trip(self):
        status, body = patch_checklist(make_full_deps(trip=None), READY_ALL)
        self.assertEqual((status, body["error"]), (404, "trip_not_found"))

    def test_invalid_body(self):
        for checklist in [None, [], ["原班機登機證"], [{"label": "原班機登機證"}],
                          [{"label": "原班機登機證", "isReady": "true"}]]:
            with self.subTest(checklist=checklist):
                status, body = patch_checklist(make_full_deps(trip=ABNORMAL_TRIP), checklist)
                self.assertEqual((status, body["error"]), (400, "invalid_request"))


if __name__ == "__main__":
    unittest.main()
