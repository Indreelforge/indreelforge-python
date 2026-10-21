"""
IndReelForge — Official Python SDK

Social Media API to publish videos, photos, text and documents to Instagram,
YouTube, TikTok, Facebook, X, LinkedIn, Threads, Pinterest, Reddit, Bluesky
and Google Business Profile with a single API call.

Usage:
    from indreelforge import IndReelForge

    client = IndReelForge(api_key="your-api-key")
    result = client.upload_video(
        user="my-profile",
        platforms=["instagram", "tiktok", "youtube"],
        video_path="./video.mp4",
        title="My awesome video",
    )
"""

from ._version import __version__
from .client import (
    DEFAULT_BASE_URL,
    IndReelForge,
    IndReelForgeError,
    InReelForge,
    InReelForgeError,
)

__all__ = [
    "IndReelForge",
    "IndReelForgeError",
    "InReelForge",
    "InReelForgeError",
    "DEFAULT_BASE_URL",
    "__version__",
]
