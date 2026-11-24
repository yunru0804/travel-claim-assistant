import copy
import unittest
from datetime import datetime, timezone
from decimal import Decimal

import trip_stores as stores

NOW = datetime(2026, 11, 2, 3, 0, tzinfo=timezone.utc)  # 台灣時間 11/2 11:00
NOW_ISO = "2026-11-02T03:00:00Z"


class FakeTable:
    """模擬只用 get 查一筆的資料表:rows 以 key 查,error 時丟例外。"""

    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or {}, error, []

    def _get(self, key):
        self.calls.append(key)
        if self.error:
            raise self.error
        return copy.deepcopy(self.rows.get(key))


class FakeTrips(FakeTable):
    def get(self, sub):
        return self._get(sub)


class FakePolicies(FakeTable):
    def fetch(self, sub):
        return self._get(sub)


class FakePlans(FakeTable):
    def get(self, project_code):
        return self._get(project_code)


class FakeFlights(FakeTable):
    def get(self, flight_number, departure_date):
        return self._get((flight_number, departure_date))


class FakeWeather(FakeTable):
    def get(self, city, now):
        return self._get((city, now))


POLICY = {
    "policy_number": "TA-20261103-01",
    "policyholder_name": "王小明",
    "project_code": "T6",
    "coverage_start": "2026-11-01",
    "coverage_end": "2026-11-07",
}
OTHER_POLICY = dict(POLICY, policy_number="TA-20261201-02",
                    coverage_start="2026-12-01", coverage_end="2026-12-05")


def _coverage(main_id, main_name, sub_id, name, amount, amount_type, **extra):
    return {"product_main_id": main_id, "product_public_name": main_name, "sub_id": sub_id, "name": name,
            "name_en": f"{name} EN", "amount": amount, "currency": "NTD", "amount_type_name": amount_type, **extra}


PLAN = {
    "project_code": "T6",
    "plan_name": "輕鬆定額型",
    "not_covered": ["租車事故慰問"],
    "notes": ["僅載列部分重要理賠限制事項"],
    "coverages": [
        _coverage("P41", "海外旅行不便保險", "2I", "班機延誤費用", Decimal("6000.00"), "定額",
                  note="每四小時 3,000 元, 每次最高 6,000 元, 最多可理賠2次", restrictions=["戰爭不給付"]),
        _coverage("P41", "海外旅行不便保險", "2K", "行李延誤費用", Decimal("3000.00"), "定額"),
        _coverage("P54", "個人海外旅行補償保險", "3E", "劫機保險費用", Decimal("3000.00"), "日額",
                  note="最高給付 10 日"),
        _coverage("PAB", "20意外險", "21", "身故及失能保險金", None, "限定額"),
    ],
}

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


class PolicyTest(unittest.TestCase):
    def test_mask(self):
        self.assertEqual(stores._mask_policy_number("TA-20261103-01"), "TA-2026****-01")
        self.assertEqual(stores._mask_policy_number("AB12345"), "****345")

    def test_find_policy_must_cover_departure_date(self):
        self.assertEqual(stores.find_policy([OTHER_POLICY, POLICY], "2026-11-03"), POLICY)
        self.assertIsNone(stores.find_policy([OTHER_POLICY, POLICY], "2026-11-20"))
        self.assertIsNone(stores.find_policy(None, "2026-11-03"))

    def test_summary_uses_plan_name_and_masks_number(self):
        summary = stores.build_policy_summary(POLICY, NOW, PLAN)
        self.assertEqual(summary, {"productName": "輕鬆定額型", "projectCode": "T6",
                                   "policyNumberMasked": "TA-2026****-01", "statusText": "保障生效中"})

    def test_summary_without_plan_has_no_product_name(self):
        self.assertIsNone(stores.build_policy_summary(POLICY, NOW, None)["productName"])

    def test_status_text(self):
        not_started = dict(POLICY, coverage_start="2026-11-03")
        expired = dict(POLICY, coverage_end="2026-11-01")
        self.assertEqual(stores.build_policy_summary(not_started, NOW, PLAN)["statusText"], "保障尚未生效")
        self.assertEqual(stores.build_policy_summary(expired, NOW, PLAN)["statusText"], "保障已到期")


class WeatherTest(unittest.TestCase):
    def test_time_range_is_last_three_hours(self):
        self.assertEqual(stores.weather_time_range(NOW), ("2026-11-02T00:00:00Z", NOW_ISO))

    def test_unknown_code_or_missing_item_returns_none(self):
        self.assertIsNone(stores.build_weather(None))
        self.assertIsNone(stores.build_weather({"condition_code": "HAZE", "temperature_c": 1,
                                                "rain_probability_percent": 0}))


class StatusPartsTest(unittest.TestCase):
    def test_default_is_normal(self):
        trip_status, disruption, delay_claim = stores.build_status_parts(TRIP)
        self.assertEqual(trip_status, {"state": "NORMAL", "type": None, "message": "航班動態監測中",
                                       "updatedAt": "2026-11-01T00:00:00Z"})
        self.assertIsNone(disruption)
        self.assertEqual(delay_claim, {"eligible": False, "estimatedBenefitAmount": None,
                                       "caseStatus": "NOT_CREATED", "caseStatusText": None, "checklist": []})

    def test_abnormal_with_claim(self):
        trip_status, disruption, delay_claim = stores.build_status_parts(ABNORMAL_TRIP)
        self.assertEqual((trip_status["state"], trip_status["type"]), ("ABNORMAL", "FLIGHT_DELAY"))
        self.assertEqual(disruption["delayMinutes"], Decimal("300"))
        self.assertTrue(delay_claim["eligible"])
        self.assertEqual(delay_claim["caseStatusText"], "系統已快速立案")
        self.assertEqual(delay_claim["checklist"][1], {"label": "航司班機延誤證明書", "isReady": False})

    def test_normal_state_hides_disruption(self):
        trip = dict(ABNORMAL_TRIP, trip_status={"state": "NORMAL"})
        self.assertIsNone(stores.build_status_parts(trip)[1])

    def test_not_eligible_hides_benefit_amount(self):
        trip = dict(ABNORMAL_TRIP, delay_claim={"eligible": False, "estimated_benefit_amount": Decimal("5000")})
        self.assertIsNone(stores.build_status_parts(trip)[2]["estimatedBenefitAmount"])


if __name__ == "__main__":
    unittest.main()
