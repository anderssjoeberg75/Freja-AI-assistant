"""manage_google_calendar and download_facebook_photos tools."""

from backend.routes.google_calendar import (
    core_get_calendar_data,
    core_save_calendar_event,
    core_delete_calendar_event,
)
from backend.services.facebook_service import download_facebook_photos_impl
from ._registry import registry

@registry.register(
    name="manage_google_calendar",
    description="Manages the user's calendar events. You can create new events, edit existing events, delete events, or list events within a given time window (days).",
    permission_key="freja_tool_manage_google_calendar_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action to perform: 'list', 'create', 'edit' or 'delete'.",
                "enum": ["list", "create", "edit", "delete"]
            },
            "event_id": {
                "type": "INTEGER",
                "description": "The unique database ID of the event (required for 'edit' and 'delete')."
            },
            "summary": {
                "type": "STRING",
                "description": "The event title or summary (required for 'create' and 'edit')."
            },
            "start_time": {
                "type": "STRING",
                "description": "Start time in ISO format (e.g. '2026-06-12T14:00:00', required for 'create' and 'edit')."
            },
            "end_time": {
                "type": "STRING",
                "description": "End time in ISO format (e.g. '2026-06-12T15:00:00', required for 'create' and 'edit')."
            },
            "description": {
                "type": "STRING",
                "description": "Detailed description or meeting notes (optional)."
            },
            "location": {
                "type": "STRING",
                "description": "Location or meeting link (optional)."
            },
            "days": {
                "type": "INTEGER",
                "description": "Number of days before and after today to fetch when using 'list'. Default is 30 days."
            }
        },
        "required": ["action"]
    },
)
async def exec_manage_google_calendar(args):
    action = args.get("action", "").lower()
    if not action:
        return {"error": "Calendar action is missing."}
        
    try:
        if action == "list":
            days = int(args.get("days", 30) or 30)
            events = core_get_calendar_data(days=days)
            return {
                "message": f"Found {len(events)} calendar events.",
                "events": events
            }
        elif action in ("create", "edit"):
            summary = args.get("summary", "")
            start_time = args.get("start_time", "")
            end_time = args.get("end_time", "")
            description = args.get("description", "")
            location = args.get("location", "")
            db_id = args.get("event_id")
            
            if not summary or not start_time or not end_time:
                return {"error": "Title (summary), start_time and end_time are required."}
                
            return await core_save_calendar_event(
                summary=summary,
                start_time=start_time,
                end_time=end_time,
                description=description,
                location=location,
                db_id=db_id
            )
        elif action == "delete":
            db_id = args.get("event_id")
            if not db_id:
                return {"error": "The event ID (event_id) is required in order to delete."}
            return await core_delete_calendar_event(db_id=db_id)
        else:
            return {"error": f"Unknown action: {action}"}
    except Exception as e:
        return {"error": f"Calendar operation failed: {str(e)}"}

@registry.register(
    name="download_facebook_photos",
    description="Downloads photos from a user's Facebook profile or photo gallery (e.g. .../photos_by) using Playwright. Fetches the images and saves them locally.",
    permission_key="freja_tool_download_facebook_photos_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "profile_url": {
                "type": "STRING",
                "description": "The full URL to the Facebook profile's photos, e.g. https://www.facebook.com/profile.php?id=61581510724534&sk=photos_by"
            },
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of images to download (default is 1000)."
            }
        },
        "required": ["profile_url"]
    },
)
async def exec_download_facebook_photos(args, progress_callback=None):
    profile_url = args.get("profile_url", "")
    limit = int(args.get("limit", 1000) or 1000)
    if not profile_url:
        return {"error": "The Facebook profile URL is missing."}
    # This loads a real, logged-in browser session and navigates it to `profile_url` - with
    # no host check, a crafted or prompt-injected URL turns "download my Facebook photos"
    # into an authenticated-browser SSRF/arbitrary-fetch primitive (can reach internal LAN
    # addresses, since the backend runs on the user's home network).
    import urllib.parse
    parsed = urllib.parse.urlparse(profile_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "facebook.com" or host.endswith(".facebook.com")):
        return {"error": "Security error: profile_url must be an https://*.facebook.com address."}
    return await download_facebook_photos_impl(profile_url, limit, progress_callback)

