import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def run():
    print("=" * 60)
    print("FACEBOOK LOGIN SESSION SAVER FOR FREJA")
    print("=" * 60)
    print("This script will open a Chromium browser window so you can log in.")
    print("1. Log in to Facebook in the opened browser window.")
    print("2. The script will automatically detect when you are logged in and save your session.")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com")
        
        script_dir = Path(__file__).resolve().parent
        state_path = script_dir / "facebook_state.json"
        
        print("\nWaiting for you to log in to Facebook in the opened browser window...")
        
        logged_in = False
        # Loop and check for the c_user cookie (up to 10 minutes)
        for _ in range(300):
            await asyncio.sleep(2)
            try:
                if page.is_closed():
                    print("\n[Error] The browser window was closed before the login completed.")
                    break
                
                state = await context.storage_state()
                cookies = state.get("cookies", [])
                if any(c.get("name") == "c_user" for c in cookies):
                    # Double-save to ensure state is flushed
                    await context.storage_state(path=str(state_path))
                    await asyncio.sleep(1)
                    await context.storage_state(path=str(state_path))
                    print(f"\n[Success] Login detected. Session saved to: {state_path.resolve()}")
                    logged_in = True
                    
                    # Sync session to remote backend server if configured
                    backend_url = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
                    try:
                        import httpx
                        sync_url = f"{backend_url}/api/facebook/session"
                        print(f"[Sync] Uploading session state to backend ({sync_url})...")
                        async with httpx.AsyncClient(timeout=10.0) as client:
                            res = await client.post(sync_url, json=state)
                            if res.status_code == 200:
                                print(f"[Success] Session synced to backend server at {backend_url}.")
                            else:
                                print(f"[Warning] Could not sync session to backend ({res.status_code}): {res.text}")
                    except Exception as upload_err:
                        print(f"[Warning] Backend sync attempt failed: {upload_err}")

                    break
            except Exception as e:
                print(f"\n[Error] Failure while monitoring the login: {e}")
                break

        if not logged_in:
            print("\nThe login failed or was aborted.")
            
        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nSession saving cancelled.")
