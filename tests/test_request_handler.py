"""Regression tests for HTTP route dispatching."""

import unittest

from backend.request_handler import resolve_route


class RequestHandlerRoutingTests(unittest.TestCase):
    def test_all_api_routes_resolve_to_the_expected_domain(self):
        expected_routes = {
            ("GET", "/api/keys"): "backend.routes.settings",
            ("POST", "/api/keys"): "backend.routes.settings",
            ("GET", "/api/search?q=weather"): "backend.routes.search",
            ("GET", "/api/garmin/data?days=7"): "backend.routes.garmin",
            ("GET", "/api/garmin/sync"): "backend.routes.garmin",
            ("GET", "/api/garmin/delete?date=2026-06-08"): "backend.routes.garmin",
            ("POST", "/api/garmin/data"): "backend.routes.garmin",
            ("GET", "/api/strava/callback?code=test"): "backend.routes.strava",
            ("GET", "/api/strava/sync"): "backend.routes.strava",
            ("GET", "/api/strava/data?days=7"): "backend.routes.strava",
            ("GET", "/api/strava/delete?id=1"): "backend.routes.strava",
            ("GET", "/api/strava/activity_details?id=1"): "backend.routes.strava",
            ("GET", "/api/strava/athlete_stats"): "backend.routes.strava",
            ("POST", "/api/strava/data"): "backend.routes.strava",
            ("GET", "/api/withings/data?days=7"): "backend.routes.withings",
            ("GET", "/api/withings/sync"): "backend.routes.withings",
            ("GET", "/api/withings/delete?date=2026-06-08"): "backend.routes.withings",
            ("POST", "/api/withings/data"): "backend.routes.withings",
        }

        for (method, path), module_name in expected_routes.items():
            with self.subTest(method=method, path=path):
                route = resolve_route(method, path)
                self.assertIsNotNone(route)
                self.assertEqual(module_name, route.handler.__module__)

    def test_unknown_api_route_does_not_resolve(self):
        self.assertIsNone(resolve_route("GET", "/api/unknown"))
        self.assertIsNone(resolve_route("POST", "/api/unknown"))

    def test_wrong_http_method_does_not_resolve(self):
        self.assertIsNone(resolve_route("POST", "/api/search?q=weather"))
        self.assertIsNone(resolve_route("DELETE", "/api/garmin/data"))


if __name__ == "__main__":
    unittest.main()
