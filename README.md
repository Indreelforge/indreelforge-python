# IndReelForge — Python SDK

[![PyPI version](https://img.shields.io/pypi/v/indreelforge.svg)](https://pypi.org/project/indreelforge/)
[![Python versions](https://img.shields.io/pypi/pyversions/indreelforge.svg)](https://pypi.org/project/indreelforge/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

Official Python SDK for the [IndReelForge](https://indreelforge.com) Social Media API. Publish videos, photos, text and documents to **Instagram, YouTube, TikTok, Facebook, X (Twitter), LinkedIn, Threads, Pinterest, Reddit, Bluesky & Google Business Profile** with a single API call.

## Installation

```bash
pip install indreelforge
```

Requires Python 3.8+. The only dependency is `requests`.

## Quick start

```python
from indreelforge import IndReelForge

client = IndReelForge(api_key="your-api-key-here")

result = client.upload_video(
    user="my-profile",
    platforms=["instagram", "tiktok", "youtube"],
    video_path="./my-video.mp4",
    title="Check out this video!",
)

print(result["data"]["upload_id"])
```

## Configuration

```python
client = IndReelForge(
    api_key="your-api-key",                  # Required. Dashboard → API Keys
    base_url="https://api.indreelforge.com", # Optional (default)
    timeout=120,                             # Optional, seconds (default 120)
    session=None,                            # Optional requests.Session
    headers={"X-Trace": "abc"},              # Optional extra headers
)

# Or as a context manager, which closes the HTTP session afterwards:
with IndReelForge(api_key="your-api-key") as client:
    print(client.get_me())
```

Every request sends `Authorization: Apikey <key>` and an explicit `User-Agent: indreelforge-python/<version>`. (The API sits behind Cloudflare, which blocks generic agents such as `Python-urllib`; if you call the REST API without this SDK, set your own User-Agent.)

## Publishing

### Common upload options

All `upload_*` methods accept:

| Argument | API field | Description |
|---|---|---|
| `user` | `user` | Profile name (required) |
| `platforms` | `platform[]` | `instagram`, `youtube`, `tiktok`, `facebook`, `x`, `linkedin`, `threads`, `pinterest`, `reddit`, `bluesky`, `google_business` |
| `title` | `title` | Title / caption |
| `description` | `description` | Long description |
| `first_comment` | `first_comment` | First comment |
| `schedule` | `schedule` | ISO-8601 datetime to publish later |
| `timezone` | `timezone` | IANA timezone for `schedule` |
| `add_to_queue` | `add_to_queue` | Put the post in the profile's next free queue slot |
| `async_upload` | `async_upload` | `False` = wait (up to ~50 s) for the publish result |
| `request_id` | `request_id` | Your own correlation id (see `get_request_status`) |
| `idempotency_key` | `Idempotency-Key` header | Retries with the same key never double-post |
| `platform_options` / `**kwargs` | any | Platform-specific fields, sent as-is (see below) |

### Upload a video

```python
# From a local file
client.upload_video(
    user="my-profile",
    platforms=["instagram", "tiktok", "youtube", "facebook"],
    video_path="./video.mp4",
    title="My video",
    first_comment="#socialmedia",
    schedule="2026-12-01T10:00:00Z",
    youtube_privacy="public",
    youtube_tags="tutorial,tech",
    idempotency_key="video-2026-12-01",
)

# From a public URL (the server downloads it)
client.upload_video(
    user="my-profile",
    platforms=["tiktok"],
    video_url="https://cdn.example.com/clip.mp4",
)
```

### Upload photos

Local files, public URLs, or both — up to 10 in total.

```python
client.upload_photos(
    user="my-profile",
    platforms=["instagram", "facebook", "pinterest"],
    photo_paths=["./photo1.jpg"],
    photo_urls=["https://cdn.example.com/photo2.jpg"],
    title="Photo carousel",
    platform_options={"pinterest_board_id": "1234567890"},
)
```

### Text post

```python
client.upload_text(
    user="my-profile",
    platforms=["x", "threads", "bluesky", "linkedin"],
    text="Just shipped a new feature!",   # `title=` still works as in 1.0
)
```

### Document post (LinkedIn)

```python
client.upload_document(
    user="my-profile",
    document_path="./deck.pdf",           # PDF, PPT, PPTX, DOC, DOCX (max 100 MB)
    title="Q4 results",
    visibility="PUBLIC",
    target_linkedin_page_id="12345",      # optional: post as a company page
)
```

### Synchronous mode

By default an upload returns immediately and publishes in the background. With `async_upload=False` the server waits up to about 50 seconds and returns the outcome:

```python
res = client.upload_text(user="my-profile", platforms=["bluesky"], text="hi", async_upload=False)
if res.get("sync"):
    print(res["data"]["status"], res["data"]["platforms"], res["data"]["platform_post_ids"])
else:
    # Took longer than the wait window: poll instead
    print(client.get_upload_status(res["data"]["upload_id"]))
```

### Platform-specific options

Pass any platform-specific API field through `platform_options` (or as extra keyword arguments). Lists are sent as repeated fields, booleans as `"true"`/`"false"`, dicts as JSON.

```python
client.upload_photos(
    user="my-profile",
    platforms=["tiktok", "google_business", "reddit"],
    photo_urls=["https://cdn.example.com/menu.jpg"],
    title="New menu is live",
    platform_options={
        "privacy_level": "PUBLIC_TO_EVERYONE",   # TikTok
        "auto_add_music": True,
        "gbp_location_id": "locations/123",      # Google Business Profile
        "gbp_cta_type": "LEARN_MORE",
        "gbp_cta_url": "https://example.com",
        "reddit_subreddit": "food",              # Reddit
    },
)
```

Use the platform helper methods below to look up page, board and location ids.

## All methods

| Area | Method | Endpoint |
|---|---|---|
| Upload | `upload_video(user, platforms, video_path=None, ..., video_url=None)` | `POST /api/upload` |
| | `upload_photos(user, platforms, photo_paths=None, ..., photo_urls=None)` | `POST /api/upload_photos` |
| | `upload_text(user, platforms, title=None, ..., text=None)` | `POST /api/upload_text` |
| | `upload_document(user, document_path, title=None, ...)` | `POST /api/upload_document` |
| Account | `get_me()` | `GET /api/me` |
| Uploads | `get_upload_status(upload_id)` | `GET /api/upload/:id/status` |
| | `get_request_status(request_id)` | `GET /api/uploadposts/status?request_id=` |
| | `list_uploads(limit=None, page=None, status=None)` | `GET /api/uploads` |
| | `get_upload(upload_id)` | `GET /api/uploads/:id` |
| | `delete_upload(upload_id)` | `DELETE /api/uploads/:id` |
| | `retry_upload(upload_id)` | `POST /api/uploads/:id/retry` |
| | `get_platform_limits()` | `GET /api/platform-limits` |
| Profiles | `get_profiles()` | `GET /api/profiles` |
| | `create_profile(name)` | `POST /api/profiles` |
| | `delete_profile(name)` | `DELETE /api/profiles/:name` |
| Schedule | `get_scheduled_posts()` | `GET /api/schedule` |
| | `update_scheduled_post(job_id, scheduled_date=None, timezone=None, title=None, caption=None)` | `PATCH /api/schedule/:jobId` |
| | `cancel_scheduled_post(job_id)` | `DELETE /api/schedule/:jobId` |
| Queue | `get_queue_settings(profile)` | `GET /api/queue/settings` |
| | `update_queue_settings(profile, slots=None, days_of_week=None, timezone=None, max_posts_per_slot=None)` | `POST /api/queue/settings` |
| | `get_queue_preview(profile, count=None)` | `GET /api/queue/preview` |
| | `get_next_queue_slot(profile)` | `GET /api/queue/next-slot` |
| Platform helpers | `get_facebook_pages(profile)` | `GET /api/platforms/facebook/pages` |
| | `get_linkedin_pages(profile)` | `GET /api/platforms/linkedin/pages` |
| | `get_pinterest_boards(profile)` | `GET /api/platforms/pinterest/boards` |
| | `get_google_business_locations(profile)` | `GET /api/platforms/google-business/locations` |
| Analytics | `get_analytics(range="30d")` | `GET /api/analytics/overview` |
| | `get_live_analytics(profile, platforms=None)` | `GET /api/analytics/live/:profile` |
| | `get_post_analytics(upload_id, platform=None)` | `GET /api/analytics/post/:upload_id` |
| | `get_total_impressions(profile, platform=None, period=None, start_date=None, end_date=None)` | `GET /api/analytics/total-impressions/:profile` |
| FFmpeg | `get_ffmpeg_usage()` | `GET /api/ffmpeg/usage` |
| | `list_ffmpeg_jobs()` | `GET /api/ffmpeg/jobs` |
| | `get_ffmpeg_job(job_id)` | `GET /api/ffmpeg/jobs/:jobId` |
| Plans | `get_plans()` | `GET /api/plans` |

Examples:

```python
me = client.get_me()                                   # plan + monthly usage
failed = client.list_uploads(status="failed", limit=10)["uploads"]
if failed:
    client.retry_upload(failed[0]["id"])

client.update_queue_settings(
    "my-profile",
    slots=["09:00", "17:30"],
    days_of_week=[1, 2, 3, 4, 5],
    timezone="Europe/London",
)
client.upload_video("my-profile", ["youtube"], "./v.mp4", add_to_queue=True)

posts = client.get_scheduled_posts()["scheduled_posts"]
client.update_scheduled_post(posts[0]["id"], scheduled_date="2026-12-24T09:00:00Z")
```

## Error handling

Every API failure raises `IndReelForgeError`:

| Attribute | Description |
|---|---|
| `status_code` (alias `status`) | HTTP status (`0` = network error, `408` = client-side timeout) |
| `code` | API error code (`error_code` / `code`), e.g. `MISSING_VIDEO`, or `NETWORK_ERROR` / `TIMEOUT` |
| `body` | Parsed response body (dict) or raw text |
| `str(err)` | Human-readable message from the API |

```python
from indreelforge import IndReelForge, IndReelForgeError

try:
    client.upload_video(user="my-profile", platforms=["tiktok"], video_path="./v.mp4")
except IndReelForgeError as e:
    print(f"API error {e.status_code} ({e.code}): {e}", e.body)
except FileNotFoundError as e:
    print(f"File not found: {e}")
```

Invalid arguments (for example neither `video_path` nor `video_url`, or more than 10 photos) raise `ValueError` before any request is made.

## MCP: use IndReelForge from AI agents

IndReelForge runs a hosted [Model Context Protocol](https://modelcontextprotocol.io) server, so Claude, ChatGPT, Cursor and other agents can publish posts, check upload status and read analytics with your API key. No Python is needed for this.

**Hosted URL (Streamable HTTP)** — for clients that support remote MCP servers with headers:

- URL: `https://api.indreelforge.com/mcp`
- Header: `Authorization: ApiKey your-api-key`

**stdio clients (Claude Desktop, Cursor)** — use the bridge from the [Node.js package](https://www.npmjs.com/package/indreelforge) (requires Node.js 18+):

```json
{
  "mcpServers": {
    "indreelforge": {
      "command": "npx",
      "args": ["-y", "indreelforge", "mcp"],
      "env": {
        "INDREELFORGE_API_KEY": "your-api-key"
      }
    }
  }
}
```

See the [Node.js SDK README](https://github.com/Indreelforge/indreelforge-node#mcp-use-indreelforge-from-ai-agents) for all MCP options.

## Supported platforms

| Platform | Video | Photo | Text | Document | Scheduling |
|----------|-------|-------|------|----------|------------|
| Instagram | ✓ | ✓ | - | - | ✓ |
| YouTube | ✓ | - | - | - | ✓ |
| TikTok | ✓ | ✓ | - | - | ✓ |
| Facebook | ✓ | ✓ | ✓ | - | ✓ |
| X (Twitter) | ✓ | ✓ | ✓ | - | ✓ |
| LinkedIn | ✓ | ✓ | ✓ | ✓ | ✓ |
| Threads | ✓ | ✓ | ✓ | - | ✓ |
| Pinterest | ✓ | ✓ | - | - | ✓ |
| Reddit | ✓ | ✓ | ✓ | - | ✓ |
| Bluesky | ✓ | ✓ | ✓ | - | ✓ |
| Google Business Profile | - | ✓ | ✓ | - | ✓ |

## Upgrading from 1.0

1.1 is backward compatible: existing method names, positional arguments and return values keep working.

- The class is now `IndReelForge`; `InReelForge` is still exported as an alias.
- The exception is `IndReelForgeError`; `InReelForgeError` is an alias, so `except InReelForgeError` still works.
- Uploads are always sent as `multipart/form-data`, and file handles are closed after each request.
- `upload_text` sends your text as `text` (the API treats `text` and `title` the same).

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e ".[test]"
.venv/bin/python -m pytest
```

## Links

- [Website](https://indreelforge.com)
- [API documentation](https://indreelforge.com/docs)
- [Dashboard](https://indreelforge.com/dashboard)
- [Pricing](https://indreelforge.com/pricing)
- [Node.js SDK & MCP bridge](https://www.npmjs.com/package/indreelforge)

## License

MIT
