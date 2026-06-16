import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright

async def run():
    print("=" * 60)
    print("FACEBOOK LOGIN SESSION SAVER FOR FREJA")
    print("=" * 60)
    print("This script will open a Chromium browser window so you can log in.")
    print("1. Log in to Facebook in the browser window.")
    print("2. Once logged in, come back here and press Enter to save your session.")
    print("=" * 60)
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto("https://www.facebook.com")
        
        # Wait for user input in terminal
        input("\nPress ENTER here once you have finished logging in to Facebook...")
        
        # Save state
        state_path = Path("facebook_state.json")
        await context.storage_state(path=str(state_path))
        print(f"\n[Success] Session state saved to: {state_path.resolve()}")
        await browser.close()

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nSession saving cancelled.")
