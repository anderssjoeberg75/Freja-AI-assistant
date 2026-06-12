"""Integration tests for persistent chat history and async background sync tasks."""

import unittest
import sqlite3
from fastapi.testclient import TestClient
from server import app
from backend.config import DB_FILE
from backend.services.sync_status import set_sync_state, get_sync_states

class AsyncAndChatTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Clear chat history table before testing
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM chat_history")
        conn.commit()
        conn.close()

    def test_chat_history_endpoints(self):
        # 1. Initially chat history should be empty
        response = self.client.get("/api/chat/history")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 0)

        # 2. POST a message to chat history
        payload = {
            "sender": "user",
            "content": "Test user query for Freja",
            "channel": "web"
        }
        response = self.client.post("/api/chat/message", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        # 3. Fetch history and verify it contains the inserted message
        response = self.client.get("/api/chat/history")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["sender"], "user")
        self.assertEqual(data[0]["content"], "Test user query for Freja")
        self.assertEqual(data[0]["channel"], "web")

        # 4. POST another message
        assistant_payload = {
            "sender": "assistant",
            "content": "Cybernetic response template activated.",
            "channel": "web"
        }
        self.client.post("/api/chat/message", json=assistant_payload)

        response = self.client.get("/api/chat/history")
        self.assertEqual(len(response.json()), 2)

        # 5. Clear chat history
        response = self.client.post("/api/chat/clear")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")

        # Verify it's empty again
        response = self.client.get("/api/chat/history")
        self.assertEqual(len(response.json()), 0)

    def test_sync_status_endpoints(self):
        # Set state to syncing
        set_sync_state("garmin", "syncing")
        
        # Verify sync status endpoint returns the state
        response = self.client.get("/api/sync/status")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("states", data)
        self.assertEqual(data["states"]["garmin"], "syncing")

        # Complete sync
        set_sync_state("garmin", "success")
        response = self.client.get("/api/sync/status")
        data = response.json()
        self.assertEqual(data["states"]["garmin"], "success")

if __name__ == "__main__":
    unittest.main()
