import json
import unittest
from decimal import Decimal

import trip_management as tm

POLICY = {
    "policy_id": "P1",
    "product_code": "TRAVEL-A",
    "product_version": "2025-04",
    "coverage_start": "2026-11-01",
    "coverage_end": "2026-11-10",
    "itinerary": [
        {"flight_no": "br 198", "departure_date": "2026-11-01", "destination": "Tokyo"},
        {"flight_no": "BR198", "departure_date": "2026-11-01", "destination": "Tokyo"},  # 重複:只查一次
        {"flight_no": "JL802", "departure_date": "2026-11-08", "destination": "Osaka"},
    ],
}

WEATHER = {
    "Tokyo": {"summary": "sunny", "temperature_c": Decimal("18.5")},
    "Osaka": {"summary": "cloudy", "temperature_c": Decimal("20")},
}


class FakePolicies:
    """fetch 回傳保單清單;找不到使用者回 None(和真的 PolicyStore 一致)。"""

    def __init__(self, by_sub=None, error=None):
        self.by_sub, self.error, self.calls = by_sub or {}, error, []

    def fetch(self, sub):
        self.calls.append(sub)
        if self.error:
            raise self.error
        return self.by_sub.get(sub)


class FakeFlights:
    def __init__(self, found=None, error=None):
        self.found, self.error, self.calls = found or {}, error, []

    def get_many(self, keys):
        self.calls.append(keys)
        if self.error:
            raise self.error
        return self.found


class FakeWeather:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows if rows is not None else WEATHER, error, []

    def get_many(self, cities):
        self.calls.append(cities)
        if self.error:
            raise self.error
        return {c: self.rows[c] for c in cities if c in self.rows}


def make_event(sub="sub-1", body=None, http_api=False):
    claims = {"sub": sub} if sub else {}
    auth = {"jwt": {"claims": claims}} if http_api else {"claims": claims}
    return {"requestContext": {"authorizer": auth}, "body": json.dumps(body) if body else None}


def make_deps(**overrides):
    parts = {
        "policies": FakePolicies({"sub-1": [POLICY]}),
        "flights": FakeFlights({("BR198", "2026-11-01"): {
            "flight_no": "BR198", "departure_date": "2026-11-01", "status": "DELAYED",
            "delay_minutes": Decimal("45"), "last_notified_level": 2}}),
        "weather": FakeWeather(),
    }
    parts.update(overrides)
    return tm.Deps(**parts)


def call(event, deps):
    resp = tm.handle(event, deps)
    return resp["statusCode"], json.loads(resp["body"])


class TripManagementTest(unittest.TestCase):
    def test_happy_path(self):
        deps = make_deps()
        status, body = call(make_event(), deps)
        self.assertEqual(status, 200)
        self.assertFalse(body["partial"])
        self.assertEqual(body["errors"], [])
        self.assertEqual(body["data"]["policies"][0]["product_code"], "TRAVEL-A")
        self.assertEqual({w["destination"] for w in body["data"]["weather"]}, {"Tokyo", "Osaka"})
        self.assertEqual(deps.policies.calls, ["sub-1"])

    def test_flight_keys_are_normalized_and_deduplicated(self):
        deps = make_deps()
        call(make_event(), deps)
        self.assertEqual(deps.flights.calls[0], [("BR198", "2026-11-01"), ("JL802", "2026-11-08")])

    def test_cities_are_deduplicated(self):
        deps = make_deps()
        call(make_event(), deps)
        self.assertEqual(deps.weather.calls[0], ["Tokyo", "Osaka"])

    def test_flight_fields_are_whitelisted_and_decimal_serialized(self):
        _, body = call(make_event(), make_deps())
        flight = body["data"]["flights"][0]
        self.assertEqual(flight["status"], "DELAYED")
        self.assertEqual(flight["delay_minutes"], 45)
        self.assertNotIn("last_notified_level", flight)

    def test_weather_decimal_serialized(self):
        _, body = call(make_event(), make_deps())
        tokyo = next(w for w in body["data"]["weather"] if w["destination"] == "Tokyo")
        self.assertEqual(tokyo["temperature_c"], 18.5)

    def test_unmonitored_flight_is_marked(self):
        _, body = call(make_event(), make_deps())
        osaka = next(f for f in body["data"]["flights"] if f["flight_no"] == "JL802")
        self.assertFalse(osaka["monitored"])
        self.assertNotIn("status", osaka)

    def test_missing_identity_returns_401(self):
        status, body = call(make_event(sub=None), make_deps())
        self.assertEqual((status, body["error"]), (401, "unauthorized"))

    def test_http_api_jwt_claims_supported(self):
        status, _ = call(make_event(http_api=True), make_deps())
        self.assertEqual(status, 200)

    def test_user_id_in_body_is_ignored(self):
        deps = make_deps()
        call(make_event(body={"user_id": "attacker", "sub": "attacker"}), deps)
        self.assertEqual(deps.policies.calls, ["sub-1"])

    def test_unknown_user_returns_404(self):
        status, body = call(make_event(sub="nobody"), make_deps())
        self.assertEqual((status, body["error"]), (404, "policyholder_not_found"))

    def test_policy_lookup_failure_returns_502(self):
        deps = make_deps(policies=FakePolicies(error=TimeoutError("down")))
        status, body = call(make_event(), deps)
        self.assertEqual((status, body["error"]), (502, "policy_unavailable"))

    def test_weather_failure_returns_partial_result(self):
        deps = make_deps(weather=FakeWeather(error=TimeoutError("slow")))
        status, body = call(make_event(), deps)
        self.assertEqual(status, 200)
        self.assertTrue(body["partial"])
        self.assertEqual(body["errors"], [{"source": "weather", "reason": "TimeoutError"}])
        self.assertEqual(body["data"]["weather"], [])
        self.assertEqual(len(body["data"]["flights"]), 2)

    def test_missing_city_reports_not_found(self):
        deps = make_deps(weather=FakeWeather(rows={"Tokyo": WEATHER["Tokyo"]}))
        status, body = call(make_event(), deps)
        self.assertEqual(status, 200)
        self.assertTrue(body["partial"])
        self.assertEqual(body["errors"], [{"source": "weather", "reason": "NotFound", "target": "Osaka"}])
        self.assertEqual([w["destination"] for w in body["data"]["weather"]], ["Tokyo"])

    def test_flight_failure_returns_partial_result(self):
        deps = make_deps(flights=FakeFlights(error=RuntimeError("ddb")))
        status, body = call(make_event(), deps)
        self.assertEqual(status, 200)
        self.assertTrue(body["partial"])
        self.assertEqual(body["data"]["flights"], [])
        self.assertEqual(len(body["data"]["weather"]), 2)

    def test_user_without_policies_returns_empty_data(self):
        deps = make_deps(policies=FakePolicies({"sub-1": []}))
        status, body = call(make_event(), deps)
        self.assertEqual(status, 200)
        self.assertEqual(body["data"], {"policies": [], "flights": [], "weather": []})
        self.assertEqual(deps.flights.calls, [])
        self.assertEqual(deps.weather.calls, [])

    def test_unexpected_error_returns_500_without_details(self):
        deps = make_deps(policies=FakePolicies({"sub-1": ["bad-data"]}))  # 保單格式錯誤
        status, body = call(make_event(), deps)
        self.assertEqual((status, body), (500, {"error": "internal_error"}))


if __name__ == "__main__":
    unittest.main()
