"""Instagram tools (thin wrappers over backend.services.instagram_service)."""

from ._registry import registry

# --- Instagram executors (thin wrappers over backend.services.instagram_service) ---

@registry.register(
    name="publish_instagram_post",
    description="Publishes a photo or a reel/video with a caption to the user's linked Instagram Business/Creator account. The media URL must be a publicly accessible direct link (image for a photo, video for a reel).",
    permission_key="freja_tool_publish_instagram_post_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "media_url": {
                "type": "STRING",
                "description": "The public URL of the photo or video to publish."
            },
            "caption": {
                "type": "STRING",
                "description": "The caption/text description for the Instagram post."
            },
            "media_type": {
                "type": "STRING",
                "description": "The kind of media being published: 'IMAGE' for a photo (default) or 'REELS' for a video/reel.",
                "enum": ["IMAGE", "REELS"]
            }
        },
        "required": ["media_url", "caption"]
    },
)
async def exec_publish_instagram_post(args):
    from backend.services.instagram_service import publish_media
    media_url = (args.get("media_url") or args.get("image_url") or "").strip()
    caption = args.get("caption", "").strip()
    media_type = (args.get("media_type") or "IMAGE").strip().upper()
    return await publish_media(media_url, caption, media_type=media_type)

@registry.register(
    name="get_instagram_feed",
    description="Fetches the latest published media posts from the linked Instagram account feed.",
    permission_key="freja_tool_get_instagram_feed_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "limit": {
                "type": "INTEGER",
                "description": "Maximum number of media items to return (default 5)."
            }
        }
    },
)
async def exec_get_instagram_feed(args):
    from backend.services.instagram_service import get_recent_media
    limit = int(args.get("limit", 5) or 5)
    return await get_recent_media(limit)

@registry.register(
    name="get_instagram_post_comments",
    description="Retrieves comments on a specific Instagram media post.",
    permission_key="freja_tool_get_instagram_post_comments_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "media_id": {
                "type": "STRING",
                "description": "The unique ID of the media post."
            }
        },
        "required": ["media_id"]
    },
)
async def exec_get_instagram_post_comments(args):
    from backend.services.instagram_service import get_comments
    media_id = args.get("media_id", "").strip()
    return await get_comments(media_id)

@registry.register(
    name="reply_to_instagram_comment",
    description="Posts a reply comment to an existing comment on an Instagram post.",
    permission_key="freja_tool_reply_to_instagram_comment_allowed",
    parameters={
        "type": "OBJECT",
        "properties": {
            "comment_id": {
                "type": "STRING",
                "description": "The unique ID of the comment to reply to."
            },
            "text": {
                "type": "STRING",
                "description": "The reply comment text message."
            }
        },
        "required": ["comment_id", "text"]
    },
)
async def exec_reply_to_instagram_comment(args):
    from backend.services.instagram_service import post_comment_reply
    comment_id = args.get("comment_id", "").strip()
    text = args.get("text", "").strip()
    return await post_comment_reply(comment_id, text)


