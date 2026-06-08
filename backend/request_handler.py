"""HTTP request dispatcher for Freja backend routes and static assets."""

from dataclasses import dataclass
from typing import Callable
import http.server

from backend.routes import settings
from backend.routes import search
from backend.routes import garmin
from backend.routes import strava
from backend.routes import withings


@dataclass(frozen=True)
class Route:
    method: str
    matches: Callable[[str], bool]
    handler: Callable[[http.server.BaseHTTPRequestHandler], None]


ROUTES = (
    Route("GET", lambda path: path == '/api/keys', settings.handle_get_keys),
    Route("POST", lambda path: path == '/api/keys', settings.handle_post_keys),
    Route("GET", lambda path: path.startswith('/api/search'), search.handle_get_search),
    Route("GET", lambda path: path.startswith('/api/garmin/data'), garmin.handle_get_garmin_data),
    Route("GET", lambda path: path.startswith('/api/garmin/sync'), garmin.handle_get_garmin_sync),
    Route("GET", lambda path: path.startswith('/api/garmin/delete'), garmin.handle_get_garmin_delete),
    Route("POST", lambda path: path == '/api/garmin/data', garmin.handle_post_garmin_data),
    Route("GET", lambda path: path.startswith('/api/strava/callback'), strava.handle_get_strava_callback),
    Route("GET", lambda path: path.startswith('/api/strava/sync'), strava.handle_get_strava_sync),
    Route("GET", lambda path: path.startswith('/api/strava/data'), strava.handle_get_strava_data),
    Route("GET", lambda path: path.startswith('/api/strava/delete'), strava.handle_get_strava_delete),
    Route("GET", lambda path: path.startswith('/api/strava/activity_details'), strava.handle_get_strava_activity_details),
    Route("GET", lambda path: path.startswith('/api/strava/athlete_stats'), strava.handle_get_strava_athlete_stats),
    Route("POST", lambda path: path == '/api/strava/data', strava.handle_post_strava_data),
    Route("GET", lambda path: path.startswith('/api/withings/data'), withings.handle_get_withings_data),
    Route("GET", lambda path: path.startswith('/api/withings/sync'), withings.handle_get_withings_sync),
    Route("GET", lambda path: path.startswith('/api/withings/delete'), withings.handle_get_withings_delete),
    Route("POST", lambda path: path == '/api/withings/data', withings.handle_post_withings_data),
)


def resolve_route(method: str, path: str):
    """Return the first route matching an HTTP method and request path."""
    return next((route for route in ROUTES if route.method == method and route.matches(path)), None)


class CustomHandler(http.server.SimpleHTTPRequestHandler):
    """Dispatch API requests to focused route modules and serve static assets."""

    def _dispatch_api_request(self, method: str) -> bool:
        route = resolve_route(method, self.path)
        if route is None:
            return False
        route.handler(self)
        return True

    def do_GET(self):
        if not self._dispatch_api_request("GET"):
            super().do_GET()

    def do_POST(self):
        if not self._dispatch_api_request("POST"):
            self.send_error(404, "API route not found")
