import unittest
from decimal import Decimal

import get_custom_info as gci
import trip_management as tm
from test_trip_management import (ABNORMAL_TRIP, FLIGHT, NOW, NOW_ISO, OTHER_POLICY, POLICY, TRIP,
                                  FakeFlights, FakePolicies, FakeTrips, FakeWeather)

WEATHER_ITEM = {"city": "東京", "forecast_time": "2026-11-02T03:00:00Z", "condition_code": "PARTLY_CLOUDY",
                "temperature_c": Decimal("16.5"), "rain_probability_percent": Decimal("10"),
                "updated_at": "2026-11-01T00:00:00Z"}


def make_deps(trip=TRIP, **overrides):
    parts = {
        "trips": FakeTrips({"sub-1": trip} if trip else {}),
        "policies": FakePolicies({"sub-1": [OTHER_POLICY, POLICY]}),
        "flights": FakeFlights({("BR192", "2026-11-03"): FLIGHT}),
        "weather": FakeWeather({("東京", NOW): WEATHER_ITEM}),
    }
    parts.update(overrides)
    return tm.Deps(**parts, now=lambda: NOW)


def call(sections, deps=None, sub="sub-1"):
    return gci.handle({"sections": sections}, deps or make_deps(), sub)


class EnvelopeTest(unittest.TestCase):
    def test_success_envelope(self):
        result = call(["flight_status"])
        self.assertEqual(result["schemaVersion"], "1.0")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["source"], {"system": "trip_management", "isMock": True, "retrievedAt": NOW_ISO})
        self.assertIsNone(result["error"])

    def test_error_envelope(self):
        result = call(["flight"], make_deps(trip=None))
        self.assertEqual(result["status"], "error")
        self.assertIsNone(result["data"])
        self.assertEqual(result["error"], {"code": "NO_ACTIVE_TRIP", "message": "目前沒有可查詢的旅程",
                                           "retryable": False})


class SectionsTest(unittest.TestCase):
    def test_flight(self):
        self.assertEqual(call(["flight"])["data"], {"flight": {
            "flightNumber": "BR192", "departureDate": "2026-11-03",
            "originAirportCode": "TPE", "originCity": "台北",
            "destinationAirportCode": "NRT", "destinationCity": "東京",
            "scheduledDeparture": "2026-11-03T10:15:00+08:00", "scheduledArrival": "2026-11-03T14:30:00+09:00",
            "estimatedArrival": None, "terminalGateText": "第一航廈・登機門 B6", "baggageCarouselText": None,
        }})

    def test_flight_status_normal(self):
        self.assertEqual(call(["flight_status"])["data"], {"flightStatus": {
            "state": "NORMAL", "delayMinutes": 0, "updatedAt": "2026-11-01T00:00:00Z"}})

    def test_flight_status_abnormal(self):
        self.assertEqual(call(["flight_status"], make_deps(trip=ABNORMAL_TRIP))["data"], {"flightStatus": {
            "state": "ABNORMAL", "delayMinutes": 300, "updatedAt": "2026-11-03T05:15:00Z"}})

    def test_weather(self):
        self.assertEqual(call(["weather"])["data"], {"weather": {
            "city": "東京", "conditionCode": "PARTLY_CLOUDY", "temperatureCelsius": 16.5,
            "rainProbabilityPercent": 10, "forecastTime": "2026-11-02T03:00:00Z",
            "updatedAt": "2026-11-01T00:00:00Z"}})

    def test_policy_summary_masks_number(self):
        self.assertEqual(call(["policy_summary"])["data"], {"policySummary": {
            "productName": "國泰安心遊日本 5 日型", "policyNumberMasked": "TA-2026****-01",
            "statusText": "保障生效中",
            "coverages": [{"label": "海外突發醫療", "amount": 1000000}, {"label": "班機延誤保險金", "amount": 5000}],
        }})

    def test_only_requested_sections(self):
        deps = make_deps()
        data = call(["flight_status", "weather"], deps)["data"]
        self.assertEqual(set(data), {"flightStatus", "weather"})
        self.assertEqual(deps.policies.calls, [])  # 沒要求保單就不查

    def test_flight_status_only_does_not_need_flights_table(self):
        deps = make_deps(flights=FakeFlights(error=RuntimeError("down")))
        self.assertEqual(call(["flight_status"], deps)["status"], "success")

    def test_all_sections(self):
        data = call(list(gci.SECTIONS))["data"]
        self.assertEqual(set(data), {"flight", "flightStatus", "weather", "policySummary"})


class NullableTest(unittest.TestCase):
    def test_weather_failure_returns_null(self):
        result = call(["weather"], make_deps(weather=FakeWeather(error=RuntimeError("down"))))
        self.assertEqual((result["status"], result["data"]), ("success", {"weather": None}))

    def test_no_recent_weather_returns_null(self):
        self.assertEqual(call(["weather"], make_deps(weather=FakeWeather()))["data"], {"weather": None})

    def test_no_matching_policy_returns_null(self):
        deps = make_deps(policies=FakePolicies({"sub-1": [OTHER_POLICY]}))
        self.assertEqual(call(["policy_summary"], deps)["data"], {"policySummary": None})

    def test_no_policy_record_returns_null(self):
        self.assertEqual(call(["policy_summary"], make_deps(policies=FakePolicies()))["data"],
                         {"policySummary": None})


class ErrorTest(unittest.TestCase):
    def assert_error(self, result, code, retryable=False):
        self.assertEqual((result["status"], result["error"]["code"], result["error"]["retryable"]),
                         ("error", code, retryable))

    def test_no_sub_is_unauthorized(self):
        self.assert_error(call(["flight"], sub=None), "UNAUTHORIZED")

    def test_invalid_sections(self):
        cases = {
            "missing": {},
            "empty": {"sections": []},
            "unknown": {"sections": ["hotel"]},
            "duplicate": {"sections": ["flight", "flight"]},
            "not list": {"sections": "flight"},
            "object item": {"sections": [{}]},
            "extra key (模型不可傳 userId)": {"sections": ["flight"], "userId": "sub-2"},
            "not dict": ["flight"],
        }
        for name, event in cases.items():
            with self.subTest(name=name):
                self.assert_error(gci.handle(event, make_deps(), "sub-1"), "INVALID_ARGUMENT")

    def test_no_active_trip(self):
        self.assert_error(call(["flight_status"], make_deps(trip=None)), "NO_ACTIVE_TRIP")

    def test_flight_not_in_table(self):
        for sections in (["flight"], ["weather"]):
            with self.subTest(sections=sections):
                self.assert_error(call(sections, make_deps(flights=FakeFlights())), "NOT_FOUND")

    def test_incomplete_flight_data(self):
        flights = FakeFlights({("BR192", "2026-11-03"): dict(FLIGHT, destination_airport="JFK")})
        self.assert_error(call(["flight"], make_deps(flights=flights)), "NOT_FOUND")

    def test_store_failures_are_retryable(self):
        cases = {
            "trips": (["flight_status"], FakeTrips(error=RuntimeError("down"))),
            "flights": (["flight"], FakeFlights(error=RuntimeError("down"))),
            "policies": (["policy_summary"], FakePolicies(error=RuntimeError("down"))),
        }
        for name, (sections, fake) in cases.items():
            with self.subTest(name=name):
                self.assert_error(call(sections, make_deps(**{name: fake})), "SERVICE_UNAVAILABLE", retryable=True)


class ResolveSubTest(unittest.TestCase):
    def test_reads_mock_sub(self):
        self.assertEqual(gci.resolve_sub({}, None, env={"MOCK_SUB": "user-003"}), "user-003")

    def test_missing_mock_sub_has_no_fallback(self):
        self.assertIsNone(gci.resolve_sub({}, None, env={}))
        self.assertIsNone(gci.resolve_sub({}, None, env={"MOCK_SUB": ""}))

    def test_ignores_ids_in_event(self):
        self.assertIsNone(gci.resolve_sub({"sub": "user-003", "userId": "user-003"}, None, env={}))


if __name__ == "__main__":
    unittest.main()
