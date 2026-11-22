"""
Post-Upload — Official Python SDK

One API call to publish videos, photos, text and documents to 22 platforms:
Instagram, YouTube, TikTok, Facebook, X, LinkedIn, Threads, Pinterest, Reddit,
Bluesky, Google Business Profile, Discord, Telegram, Slack, Mastodon,
WordPress, Dev.to, Hashnode, Lemmy, Nostr, Whop and Listmonk.

Usage:
    from post_upload import PostUpload

    client = PostUpload(api_key="your-api-key")   # or set POST_UPLOAD_API_KEY
    result = client.upload_video(
        user="my-profile",
        platforms=["instagram", "tiktok", "youtube"],
        video_path="./video.mp4",   # files over 95 MiB use the chunked upload
        title="My awesome video",
    )
"""

from ._version import __version__
from .client import (
    API_KEY_ENV_VARS,
    CHUNKED_THRESHOLD,
    CREDENTIAL_OPTIONAL_FIELDS,
    CREDENTIAL_PLATFORMS,
    DEFAULT_BASE_URL,
    MAX_VIDEO_BYTES,
    PLATFORMS,
    IndReelForge,
    IndReelForgeError,
    InReelForge,
    InReelForgeError,
    PostUpload,
    PostUploadError,
    get_skipped_platforms,
)
from .types import (
    CompletedUploadSession,
    SkippedPlatform,
    UploadData,
    UploadResponse,
    UploadSession,
    UploadSessionStatus,
)

__all__ = [
    "PostUpload",
    "PostUploadError",
    "get_skipped_platforms",
    "DEFAULT_BASE_URL",
    "PLATFORMS",
    "CREDENTIAL_PLATFORMS",
    "CREDENTIAL_OPTIONAL_FIELDS",
    "CHUNKED_THRESHOLD",
    "MAX_VIDEO_BYTES",
    "API_KEY_ENV_VARS",
    "SkippedPlatform",
    "UploadData",
    "UploadResponse",
    "UploadSession",
    "UploadSessionStatus",
    "CompletedUploadSession",
    # Deprecated aliases (the SDK's names before 1.2.0)
    "IndReelForge",
    "IndReelForgeError",
    "InReelForge",
    "InReelForgeError",
    "__version__",
]
