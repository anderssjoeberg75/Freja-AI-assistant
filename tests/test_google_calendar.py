"""Tests for the Google Calendar integration endpoints."""

import unittest
import sqlite3
from fastapi.testclient import TestClient
from server import app
from backend.config import DB_FILE

class GoogleCalendarTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_list_google_calendar_events(self):
        response = self.client.get("/api/google_calendar/data?days=30")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data, list)
        
        # Verify the structure of returned events
        if len(data) > 0:
            evt = data[0]
            self.assertIn("id", evt)
            self.assertIn("summary", evt)
            self.assertIn("start_time", evt)
            self.assertIn("end_time", evt)
            self.assertIn("description", evt)
            self.assertIn("location", evt)

    def test_create_and_delete_google_calendar_event(self):
        # Create event
        payload = {
            "summary": "Möte med Antigravity",
            "start_time": "2026-06-12T15:00:00",
            "end_time": "2026-06-12T16:00:00",
            "description": "Diskussion om integrationstester",
            "location": "Högkvarteret"
        }
        response = self.client.post("/api/google_calendar/data", json=payload)
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertEqual(res_data["status"], "success")
        
        # Get list and find created event
        response = self.client.get("/api/google_calendar/data?days=30")
        data = response.json()
        matching = [evt for evt in data if evt["summary"] == "Möte med Antigravity"]
        self.assertTrue(len(matching) > 0)
        
        created_evt = matching[0]
        event_id = created_evt["id"]
        
        # Edit event
        payload["id"] = event_id
        payload["summary"] = "Möte med Antigravity - Uppdaterat"
        response = self.client.post("/api/google_calendar/data", json=payload)
        self.assertEqual(response.status_code, 200)
        
        # Verify edit
        response = self.client.get("/api/google_calendar/data?days=30")
        data = response.json()
        updated = [evt for evt in data if evt["id"] == event_id]
        self.assertEqual(updated[0]["summary"], "Möte med Antigravity - Uppdaterat")
        
        # Delete event
        response = self.client.get(f"/api/google_calendar/delete?id={event_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        
        # Verify deleted
        response = self.client.get("/api/google_calendar/data?days=30")
        data = response.json()
        deleted = [evt for evt in data if evt["id"] == event_id]
        self.assertEqual(len(deleted), 0)

    def test_sync_google_calendar_route(self):
        response = self.client.get("/api/google_calendar/sync")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "syncing")

if __name__ == "__main__":
    unittest.main()
