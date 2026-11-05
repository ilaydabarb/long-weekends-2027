import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import fetch_prices as fp  # noqa: E402


class QualifiesTest(unittest.TestCase):
    def test_allowed_patterns(self):
        # Thu->Sun, Fri->Mon, Thu->Mon, Fri->Tue, Wed->Mon, Thu->Tue (Jan 2027: Fri is the 15th)
        for dep, ret in [(14, 17), (15, 18), (14, 18), (15, 19), (13, 18), (14, 19)]:
            self.assertTrue(fp.qualifies(date(2027, 1, dep), date(2027, 1, ret)), (dep, ret))

    def test_rejected_patterns(self):
        cases = [
            (date(2027, 1, 11), date(2027, 1, 14)),  # Mon->Thu, no weekend
            (date(2027, 1, 16), date(2027, 1, 19)),  # Sat->Tue, misses Friday night
            (date(2027, 1, 13), date(2027, 1, 16)),  # Wed->Sat, misses Saturday night
            (date(2027, 1, 15), date(2027, 1, 17)),  # only 2 nights
            (date(2027, 1, 13), date(2027, 1, 19)),  # 6 nights
        ]
        for dep, ret in cases:
            self.assertFalse(fp.qualifies(dep, ret), (dep, ret))


class ParseTest(unittest.TestCase):
    def test_parse_fares_keeps_cheapest_qualifying(self):
        payload = {"data": [
            {"departure_at": "2027-01-14T19:05:00+00:00", "return_at": "2027-01-18T21:00:00+00:00",
             "price": 92, "airline": "FR", "transfers": 0, "return_transfers": 0, "origin_airport": "STN", "link": "/x"},
            {"departure_at": "2027-01-14T07:00:00+00:00", "return_at": "2027-01-18T10:00:00+00:00",
             "price": 78.4, "airline": "U2", "transfers": 0, "return_transfers": 1, "origin_airport": "LGW"},
            {"departure_at": "2027-01-11T07:00:00+00:00", "return_at": "2027-01-14T10:00:00+00:00", "price": 30},
            {"departure_at": "bad", "price": 10},
        ]}
        out = fp.parse_fares(payload)
        self.assertEqual(list(out), ["2027-01-14|2027-01-18"])
        self.assertEqual(out["2027-01-14|2027-01-18"], [78, "easyJet", 1, "LGW", "", "tp"])

    def test_parse_hotels_median_of_cheapest(self):
        payload = {"properties": [{"rate_per_night": {"extracted_lowest": v}} for v in [120, 60, 80, 70, 300]]
                   + [{"rate_per_night": {}}, {}]}
        self.assertEqual(fp.parse_hotels(payload), {"nightly": 80, "samples": 5})
        self.assertIsNone(fp.parse_hotels({"properties": [{"rate_per_night": {"extracted_lowest": 50}}]}))

    def test_search_codes(self):
        self.assertEqual(fp.search_code({"id": "rom", "code": "FCO"}), "ROM")
        self.assertEqual(fp.search_code({"id": "grx", "code": "GRX"}), "AGP")
        self.assertEqual(fp.search_code({"id": "lis", "code": "LIS"}), "LIS")


class RyanairTest(unittest.TestCase):
    def test_parse_ryanair(self):
        def fare(arr, dep, ret, price):
            return {"outbound": {"departureAirport": {"iataCode": "STN"}, "arrivalAirport": {"iataCode": arr},
                                 "departureDate": dep + "T06:00:00"},
                    "inbound": {"departureDate": ret + "T18:00:00"}, "summary": {"price": {"value": price}}}
        payload = {"fares": [fare("KRK", "2027-02-11", "2027-02-15", 53.33),
                             fare("LIS", "2027-02-08", "2027-02-11", 20),  # Mon->Thu, not a weekend
                             {"outbound": {}}]}
        self.assertEqual(fp.parse_ryanair(payload),
                         [("KRK", "2027-02-11|2027-02-15", [53, "Ryanair", 0, "STN", "KRK", "fr"])])

    def test_airports_include_secondary_and_gateway(self):
        self.assertEqual(fp.airports_for({"id": "rom", "code": "FCO"}), {"FCO", "CIA"})
        self.assertIn("AGP", fp.airports_for({"id": "grx", "code": "GRX", "sky": "agp"}))

    def test_weekend_searches_cover_all_patterns(self):
        searches = fp.weekend_searches(date(2026, 11, 5))
        self.assertIn((date(2027, 1, 14), 3), searches)   # Thu -> Sun
        self.assertIn((date(2027, 8, 25), 5), searches)   # Wed -> Mon, last weekend of August
        self.assertEqual(len(searches), len(set(searches)))

    def test_cheaper_fare_wins_and_failed_searches_keep_old_fares(self):
        dests = [{"id": "krk", "code": "KRK", "months": [1]}]
        prices = {"flights": {"krk": {"2027-02-11|2027-02-15": [70, "Wizz Air", 0, "LTN", "", "tp"]}}}
        previous = {"krk": {"2027-02-12|2027-02-15": [40, "Ryanair", 0, "LTN", "KRK", "fr"]}}

        def fake(origin, start, nights):
            if origin == "LTN":
                raise IOError("blocked")
            if (start, nights) == (date(2027, 2, 11), 4):
                return [("KRK", "2027-02-11|2027-02-15", [53, "Ryanair", 0, origin, "KRK", "fr"])]
            return []

        orig, fp.fetch_ryanair, fp.RYANAIR_DELAY = fp.fetch_ryanair, fake, 0
        try:
            failed = fp.refresh_ryanair(dests, prices, date(2027, 2, 1))
        finally:
            fp.fetch_ryanair = orig
        fp.restore_failed(prices, previous, failed)
        krk = prices["flights"]["krk"]
        self.assertEqual(krk["2027-02-11|2027-02-15"][:2], [53, "Ryanair"])
        self.assertEqual(krk["2027-02-12|2027-02-15"][0], 40)


class RefreshTest(unittest.TestCase):
    def test_failed_month_keeps_previous_fares(self):
        dests = [{"id": "lis", "code": "LIS", "months": [0, 1]}]
        prices = {"flights": {"lis": {"2027-02-11|2027-02-15": [60, "FR", 0, "STN", ""]}}}

        def fake_fetch(dest, m, token):
            if m == 1:
                raise IOError("timeout")
            return {"2027-01-14|2027-01-18": [80, "U2", 0, "LGW", ""]}

        orig, fp.fetch_flights, fp.TP_DELAY = fp.fetch_flights, fake_fetch, 0
        try:
            fp.refresh_flights(dests, prices, "t", date(2026, 11, 4))
        finally:
            fp.fetch_flights = orig
        self.assertEqual(set(prices["flights"]["lis"]), {"2027-01-14|2027-01-18", "2027-02-11|2027-02-15"})
        self.assertIn("2027-01", prices["checked"]["lis"])


if __name__ == "__main__":
    unittest.main()
