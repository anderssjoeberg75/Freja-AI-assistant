"""Tests for the Telegram integration helper utilities."""

import unittest
import sqlite3
from backend.config import DB_FILE
from backend.services.telegram_service import markdown_to_html, fetch_db_health_summary, get_telegram_config

class TelegramBotTests(unittest.TestCase):
    def test_markdown_to_html_formatting(self):
        text = "Hej **användare**, testa *detta* `kod`block."
        result = markdown_to_html(text)
        self.assertEqual(result, "Hej <b>användare</b>, testa <i>detta</i> <code>kod</code>block.")

    def test_get_telegram_config_falls_back(self):
        token, chat_id = get_telegram_config()
        # Should return a tuple of string config values (or empty if unconfigured)
        self.assertIsInstance(token, str)
        self.assertIsInstance(chat_id, str)

    def test_fetch_db_health_summary_structure(self):
        summary = fetch_db_health_summary(days=1)
        self.assertIn("garmin", summary)
        self.assertIn("strava", summary)
        self.assertIn("withings", summary)
        
        # Test that they are lists of records
        self.assertIsInstance(summary["garmin"], list)
        self.assertIsInstance(summary["strava"], list)
        self.assertIsInstance(summary["withings"], list)

    def test_telegram_api_endpoints(self):
        from fastapi.testclient import TestClient
        from server import app
        
        client = TestClient(app)
        
        # Get Status
        response = client.get("/api/telegram/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("is_active", data)
        self.assertIn("recent_messages", data)
        
        # Save Config
        response = client.post("/api/telegram/config", json={
            "token": "123456:ABC-DEF1234ghIkl-zyx",
            "chat_id": "987654321"
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        
        # Verify status is updated
        response = client.get("/api/telegram/status")
        data = response.json()
        self.assertTrue(data["token_configured"])
        self.assertTrue(data["chat_id_configured"])
        self.assertEqual(data["chat_id"], "987654321")

    def test_telegram_google_calendar_tool_handling(self):
        from backend.routes.google_calendar import (
            core_get_calendar_data,
            core_save_calendar_event,
            core_delete_calendar_event
        )
        
        import asyncio
        # 1. Create event via core
        res = asyncio.run(core_save_calendar_event(
            summary="Telegram Möte",
            start_time="2026-06-12T17:00:00",
            end_time="2026-06-12T18:00:00",
            description="Telegram test händelse",
            location="Distans"
        ))
        self.assertEqual(res["status"], "success")
        event_id = res["event"]["id"]
        
        # 2. List events via core
        events = core_get_calendar_data(days=30)
        matching = [e for e in events if e["id"] == event_id]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["summary"], "Telegram Möte")
        
        # 3. Delete event via core
        del_res = asyncio.run(core_delete_calendar_event(db_id=event_id))
        self.assertEqual(del_res["status"], "success")
        
        # Verify deleted
        events_post = core_get_calendar_data(days=30)
        matching_post = [e for e in events_post if e["id"] == event_id]
        self.assertEqual(len(matching_post), 0)

if __name__ == "__main__":
    unittest.main()
