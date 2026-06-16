"""Facebook Photo Downloader Service using Playwright."""

import os
import re
import httpx
import hashlib
from pathlib import Path
from playwright.async_api import async_playwright
from backend.config import PROJECT_ROOT

ABORT_DOWNLOAD = False

def cancel_facebook_download():
    global ABORT_DOWNLOAD
    ABORT_DOWNLOAD = True
    print("[Facebook Scraper] Abort signal sent.")

async def download_facebook_photos_impl(profile_url: str, limit: int = 20) -> dict:
    """
    Scrapes public photos from a Facebook profile or photo gallery URL using Playwright.
    Saves them under PROJECT_ROOT/downloads/facebook_photos/<profile_id>/ and returns list of relative paths.
    """
    global ABORT_DOWNLOAD
    ABORT_DOWNLOAD = False

    downloads_dir = PROJECT_ROOT / "downloads" / "facebook_photos"
    downloads_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract profile ID or username for subfolder grouping
    match_id = re.search(r"id=(\d+)", profile_url)
    profile_id = match_id.group(1) if match_id else "unknown"
    
    if profile_id == "unknown":
        # Match /people/<name>/<id>
        match_people = re.search(r"/people/[^/]+/(\d+)", profile_url)
        if match_people:
            profile_id = match_people.group(1)
            
    if profile_id == "unknown":
        match_user = re.search(r"facebook\.com/([^/?]+)", profile_url)
        profile_id = match_user.group(1) if match_user else "shared"
        
    target_dir = downloads_dir / profile_id
    target_dir.mkdir(parents=True, exist_ok=True)
    
    downloaded_files = []
    
    print(f"[Facebook Scraper] Initiating download for {profile_url} (limit: {limit})")
    
    async with async_playwright() as p:
        # Check if we have a saved Facebook session/state and if it is valid
        state_path = Path("facebook_state.json")
        is_logged_in = False
        
        # Try verifying with headless browser first if state exists
        if state_path.exists():
            print("[Facebook Scraper] Loading saved session to verify...")
            verify_browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            try:
                verify_context = await verify_browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    storage_state=str(state_path)
                )
                verify_page = await verify_context.new_page()
                await verify_page.goto("https://www.facebook.com", wait_until="domcontentloaded", timeout=20000)
                # Check for login email input
                email_input = verify_page.locator("input[name='email']")
                if await email_input.count() == 0:
                    is_logged_in = True
                    print("[Facebook Scraper] Session verified. Already logged in.")
            except Exception as verify_err:
                print(f"[Facebook Scraper] Session verification failed: {verify_err}")
            finally:
                await verify_browser.close()

        if not is_logged_in:
            print("[Facebook Scraper] Not logged in. Launching interactive browser for login...")
            login_browser = await p.chromium.launch(
                headless=False,
                args=["--no-sandbox", "--disable-setuid-sandbox"]
            )
            login_context = await login_browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            login_page = await login_context.new_page()
            await login_page.goto("https://www.facebook.com")
            
            print("[Facebook Scraper] Waiting for user to complete login in the opened browser window...")
            # Wait up to 120 seconds for the user to log in
            for _ in range(60):
                await login_page.wait_for_timeout(2000)
                if ABORT_DOWNLOAD:
                    break
                try:
                    email_count = await login_page.locator("input[name='email']").count()
                    # If email field is gone and we are not on a login/checkpoint page, we assume logged in
                    if email_count == 0 and "facebook.com" in login_page.url and "login" not in login_page.url:
                        is_logged_in = True
                        print("[Facebook Scraper] Login detected! Saving session state...")
                        await login_context.storage_state(path=str(state_path))
                        break
                except Exception:
                    pass
            
            await login_browser.close()
            
            if not is_logged_in:
                raise Exception("Facebook-inloggning misslyckades eller avbröts av användaren.")
        
        # Now launch the headless browser for scraping using the validated/saved state
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            storage_state=str(state_path)
        )
        page = await context.new_page()
        
        try:
            print(f"[Facebook Scraper] Loading profile page...")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)
            
            # Dismiss cookie consent dialogs
            cookie_buttons = [
                "Decline optional cookies",
                "Decline all",
                "Reject all",
                "Avvisa valfria cookies",
                "Avvisa alla",
                "Tillåt alla cookies",
                "Allow all cookies",
                "Accept all",
                "Only allow essential cookies",
                "Neka valfria cookies"
            ]
            for btn_text in cookie_buttons:
                try:
                    button = page.locator(f"role=button[name*='{btn_text}' i]")
                    if await button.count() > 0:
                        await button.first.click()
                        print(f"[Facebook Scraper] Cookie banner dismissed via: '{btn_text}'")
                        await page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            # Try to hit Escape to close initial modals or popups
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(1000)
            
            # Scroll down dynamically to load all photo thumbnails
            print("[Facebook Scraper] Scrolling dynamically to load all thumbnails...")
            previous_count = 0
            no_change_count = 0
            max_scrolls = 35
            
            for scroll_idx in range(max_scrolls):
                if ABORT_DOWNLOAD:
                    print("[Facebook Scraper] Abort signal detected in scroll loop. Stopping.")
                    break
                # Run JS bypass only starting from Scroll 5 (index 4)
                if scroll_idx >= 4:
                    try:
                        await page.evaluate("""() => {
                            const findDeepestTextNode = (text) => {
                                const elems = Array.from(document.body.querySelectorAll('*'));
                                const matches = elems.filter(el => el.textContent && el.textContent.includes(text));
                                if (matches.length === 0) return null;
                                return matches[matches.length - 1];
                            };
                            
                            const findDialog = () => {
                                const targetEl = findDeepestTextNode('Se mer på Facebook');
                                if (targetEl) {
                                    let parent = targetEl;
                                    while (parent && parent.parentElement && parent.parentElement !== document.body) {
                                        const style = window.getComputedStyle(parent);
                                        if (style.position === 'fixed' || parent.getAttribute('role') === 'dialog') {
                                            return parent;
                                        }
                                        parent = parent.parentElement;
                                    }
                                }
                                return null;
                            };
                            
                            const modal = findDialog();
                            if (modal) {
                                console.log("[Scraper JS] Found Facebook Login Modal! Removing it...");
                                modal.remove();
                            }
                            
                            // Remove fullscreen fixed backdrops (no text, covers viewport)
                            document.querySelectorAll('div').forEach(div => {
                                const style = window.getComputedStyle(div);
                                if (style.position === 'fixed') {
                                    const rect = div.getBoundingClientRect();
                                    if (rect.width > window.innerWidth * 0.9 && rect.height > window.innerHeight * 0.9) {
                                        if (div.innerText.trim().length === 0) {
                                            console.log("[Scraper JS] Removing fullscreen backdrop.");
                                            div.remove();
                                        }
                                    }
                                }
                            });
                            
                            // Inject style overrides to kill all filters/blurs and unlock overflow
                            let styleOverride = document.getElementById('freja-bypass-style');
                            if (!styleOverride) {
                                styleOverride = document.createElement('style');
                                styleOverride.id = 'freja-bypass-style';
                                styleOverride.innerHTML = `
                                    * {
                                        filter: none !important;
                                        backdrop-filter: none !important;
                                    }
                                    html, body {
                                        overflow: auto !important;
                                        position: static !important;
                                        height: auto !important;
                                    }
                                `;
                                document.head.appendChild(styleOverride);
                            }
                        }""")
                    except Exception:
                        pass
                
                # Perform the scroll action
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                
                # Press Escape to clear any tooltips or popups
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                
                # Check current count of candidate links to see if new content loaded
                links = await page.locator("a").all()
                current_count = 0
                for link in links:
                    href = await link.get_attribute("href")
                    if href and ("/photo.php" in href or "/photo/" in href or "fbid=" in href):
                        current_count += 1
                
                print(f"[Facebook Scraper] Scroll {scroll_idx+1}: Found {current_count} thumbnail candidates.")
                
                if current_count == previous_count:
                    no_change_count += 1
                    if no_change_count >= 4:
                        print("[Facebook Scraper] No new photos loaded after 4 consecutive scrolls. Stopping scroll loop.")
                        break
                else:
                    no_change_count = 0
                
                previous_count = current_count
                
            # Collect potential photo links
            links = await page.locator("a").all()
            photo_urls = []
            for link in links:
                href = await link.get_attribute("href")
                if href and ("/photo.php" in href or "/photo/" in href or "fbid=" in href):
                    if href.startswith("/"):
                        href = "https://www.facebook.com" + href
                    if href not in photo_urls:
                        photo_urls.append(href)
            
            print(f"[Facebook Scraper] Total found: {len(photo_urls)} photo link candidates.")
            
            # Limit download count
            photo_urls = photo_urls[:limit]
            
            # Visit each photo page, wait for the image to render, and download it
            for idx, photo_url in enumerate(photo_urls):
                if ABORT_DOWNLOAD:
                    print("[Facebook Scraper] Abort signal detected in download loop. Stopping.")
                    break
                try:
                    # Extract unique photo ID from URL to check if the file already exists
                    match_fbid = re.search(r"fbid=(\d+)", photo_url)
                    photo_id = match_fbid.group(1) if match_fbid else None
                    if not photo_id:
                        match_path = re.search(r"/photo/(\d+)", photo_url)
                        photo_id = match_path.group(1) if match_path else None
                    if not photo_id:
                        photo_id = hashlib.md5(photo_url.encode('utf-8')).hexdigest()[:12]
                    
                    filename = f"fb_{photo_id}.jpg"
                    file_path = target_dir / filename
                    
                    if file_path.exists():
                        relative_path = f"/downloads/facebook_photos/{profile_id}/{filename}"
                        downloaded_files.append(relative_path)
                        print(f"[Facebook Scraper] Photo {photo_id} already exists in folder. Skipping page load!")
                        continue

                    print(f"[Facebook Scraper] Processing photo {idx+1}/{len(photo_urls)}: {photo_url}")
                    await page.goto(photo_url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(2500) # give image renderer time
                    
                    # Try dismissing login prompts if they overlay the photo theater
                    await page.keyboard.press("Escape")
                    
                    # Extract image metadata directly via JS to prevent Playwright locator timeouts
                    img_data = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('img')).map(img => {
                            const rect = img.getBoundingClientRect();
                            return {
                                src: img.src,
                                width: rect.width || img.width || 0,
                                height: rect.height || img.height || 0
                            };
                        });
                    }""")
                    
                    candidate_src = None
                    max_area = 0
                    
                    for data in img_data:
                        src = data["src"]
                        if not src or "fbcdn.net" not in src:
                            continue
                        
                        # Calculate area
                        width = data["width"]
                        height = data["height"]
                        area = width * height
                        
                        # Filter out small icons/avatars (usually < 150px)
                        if width >= 150 and height >= 150:
                            if area > max_area:
                                max_area = area
                                candidate_src = src
                    
                    if candidate_src:
                        print(f"[Facebook Scraper] Fetching image content from CDN...")
                        async with httpx.AsyncClient() as client:
                            resp = await client.get(candidate_src, timeout=15.0)
                            if resp.status_code == 200:
                                with open(file_path, "wb") as f:
                                    f.write(resp.content)
                                
                                relative_path = f"/downloads/facebook_photos/{profile_id}/{filename}"
                                downloaded_files.append(relative_path)
                                print(f"[Facebook Scraper] Saved: {relative_path}")
                            else:
                                print(f"[Facebook Scraper] Failed to download CDN image, status: {resp.status_code}")
                    else:
                        print(f"[Facebook Scraper] No suitable high-resolution image element found.")
                except Exception as item_err:
                    print(f"[Facebook Scraper] Error on photo detail page {photo_url}: {item_err}")
                    
        except Exception as main_err:
            print(f"[Facebook Scraper] Critical error in scraper loop: {main_err}")
            return {"error": str(main_err), "downloaded_count": len(downloaded_files), "images": downloaded_files}
        finally:
            await browser.close()
            
    status_str = "cancelled" if ABORT_DOWNLOAD else "success"
    print(f"[Facebook Scraper] Download batch complete. Status: {status_str}. Downloaded {len(downloaded_files)} images.")
    return {
        "status": status_str,
        "downloaded_count": len(downloaded_files),
        "images": downloaded_files
    }
