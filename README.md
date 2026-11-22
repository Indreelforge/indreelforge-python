# Post-Upload — Python SDK

[![PyPI version](https://img.shields.io/pypi/v/post-upload.svg)](https://pypi.org/project/post-upload/)
[![Python versions](https://img.shields.io/pypi/pyversions/post-upload.svg)](https://pypi.org/project/post-upload/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

Official Python SDK for the [Post-Upload](https://post-upload.com) API. Publish videos, photos, text and documents to **22 platforms** with a single API call: **Instagram, YouTube, TikTok, Facebook, X (Twitter), LinkedIn, Threads, Pinterest, Reddit, Bluesky, Google Business Profile, Discord, Telegram, Slack, Mastodon, WordPress, Dev.to, Hashnode, Lemmy, Nostr, Whop & Listmonk**.

> **Moving from the `indreelforge` package?** The SDK was renamed with the product. Run `pip uninstall indreelforge && pip install post-upload`, then import from `post_upload`. Old code keeps running: this package still ships an `indreelforge` module (with a `DeprecationWarning`), and `IndReelForge` / `IndReelForgeError` are aliases of `PostUpload` / `PostUploadError`. The `indreelforge` package on PyPI gets no further updates. See [Upgrading](#upgrading).

## Installation

```bash
pip install post-upload
```

Requires Python 3.8+. The only dependency is `requests`.

## Quick start

```python
from post_upload import PostUpload

client = PostUpload(api_key="your-api-key-here")   # or set POST_UPLOAD_API_KEY

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
client = PostUpload(
    api_key="your-api-key",                  # Dashboard → API Keys. Default: $POST_UPLOAD_API_KEY
    base_url="https://api.post-upload.com",  # Optional (default)
    timeout=120,                             # Optional, seconds per request (default 120)
    session=None,                            # Optional requests.Session
    headers={"X-Trace": "abc"},              # Optional extra headers
    chunked_threshold=None,                  # Optional, bytes (default 95 MiB), see "Large videos"
    max_retries=5,                           # Optional, retries per failed chunk
)

# Or as a context manager, which closes the HTTP session afterwards:
with PostUpload() as client:                 # key from POST_UPLOAD_API_KEY
    print(client.get_me())
```

Without `api_key` the client reads `POST_UPLOAD_API_KEY` (or the older `INDREELFORGE_API_KEY`). Every request sends `Authorization: Apikey <key>` and an explicit `User-Agent: post-upload-python/<version>`. (The API sits behind Cloudflare, which blocks generic agents such as `Python-urllib`; if you call the REST API without this SDK, set your own User-Agent.)

## Publishing

### Common upload options

All `upload_*` methods accept:

| Argument | API field | Description |
|---|---|---|
| `user` | `user` | Profile name (required) |
| `platforms` | `platform[]` | `instagram`, `youtube`, `tiktok`, `facebook`, `x`, `linkedin`, `threads`, `pinterest`, `reddit`, `bluesky`, `google_business`, `discord`, `telegram`, `slack`, `mastodon`, `wordpress`, `devto`, `hashnode`, `lemmy`, `nostr`, `whop`, `listmonk` (also exported as `PLATFORMS`) |
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

# From a public URL (the server downloads it, up to 500 MB)
client.upload_video(
    user="my-profile",
    platforms=["tiktok"],
    video_url="https://cdn.example.com/clip.mp4",
)

# From bytes or an open binary file / stream
with open("./video.mp4", "rb") as f:
    client.upload_video(user="my-profile", platforms=["x"], video_file=f, title="Clip")
```

Give the video in exactly one way: `video_path`, `video_file`, `video_url` or `staged_file_id`.

### Large videos (chunked upload, up to 500 MB)

Requests reach the API through Cloudflare, which caps one request at 100 MB. **Local files larger than 95 MiB are sent in parts automatically**: the SDK starts an upload session, sends each part (sequentially, exactly the server's `chunk_size`, retrying on `429`, `5xx` and network errors with backoff and `Retry-After`), completes it and posts the result by `staged_file_id`. Files are read part by part, never loaded whole into memory. If anything fails after the session started, the session is cancelled and the error is raised.

```python
def progress(sent, total):
    print(f"{sent * 100 // total}%")

client.upload_video(
    user="my-profile",
    platforms=["youtube", "facebook"],
    video_path="./full-episode.mp4",   # e.g. 380 MB
    title="Full episode",
    on_progress=progress,              # called after each part
)
```

`chunked=True` forces the chunked path for any size, `chunked=False` disables it. To upload once and post later, or to drive the parts yourself:

```python
staged = client.upload_large_file("./full-episode.mp4", on_progress=progress)
client.upload_video("my-profile", ["youtube"], staged_file_id=staged, title="Full episode")

# Low level (POST/PUT/GET/DELETE /api/upload/chunked/...)
s = client.create_upload_session("episode.mp4", size, "video/mp4")
# -> upload_session_id, chunk_size (32 MiB), total_chunks, expires_at (6 hours)
for i in range(s["total_chunks"]):
    client.upload_part(s["upload_session_id"], i, read_part(i, s["chunk_size"]))
client.get_upload_session(s["upload_session_id"])      # received_chunks, to resume
staged = client.complete_upload_session(s["upload_session_id"])["staged_file_id"]
# client.abort_upload_session(s["upload_session_id"])  # cancel
```

A staged file can be posted once, only by the account (and workspace) that uploaded it. Chunked upload is for videos (MP4, MOV, AVI, WebM, MKV); at most 5 sessions can be in progress per account.

### Skipped platforms

Each platform has its own maximum file size (for example Bluesky 100 MB, Discord 10 MB). Platforms the file is too large for are **skipped**: the post goes to the others, and the skipped ones are marked `failed` with `FILE_TOO_LARGE_FOR_PLATFORM` and listed in `data.skipped_platforms`. `get_skipped_platforms()` returns that list (or `[]`):

```python
from post_upload import get_skipped_platforms

res = client.upload_video("my-profile", ["youtube", "bluesky"], video_path="./big.mp4")
for s in get_skipped_platforms(res):
    print(s["platform"], s["message"])   # also: error_code, file_size_mb, max_size_mb
```

Only when no selected platform can take the file is the request refused (`413 FILE_TOO_LARGE_FOR_PLATFORM`). The response types are exported as `TypedDict`s (`UploadResponse`, `SkippedPlatform`, `UploadSession`, …) for type checkers.

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

**Per-platform text overrides (every platform).** `<platform>_title` and `<platform>_description` replace the shared `title` / `description` for that platform only, e.g. `x_title`, `linkedin_description`, `mastodon_title`, `wordpress_description`.

**Mastodon**

| Field | Values |
|---|---|
| `mastodon_visibility` | `public` (default), `unlisted`, `private` or `direct` |
| `mastodon_spoiler_text` | Content warning shown before the post |
| `mastodon_sensitive` | `True` to mark media as sensitive. Omit it otherwise: the server treats any non-empty value, including `"false"`, as true |
| `mastodon_alt_text` | Alt text for attached media (defaults to the title) |
| `mastodon_language` | ISO 639 language code, e.g. `"en"` |

**WordPress**

| Field | Values |
|---|---|
| `wordpress_status` | `publish` (default) or `draft` |
| `wordpress_categories` | Numeric category IDs: `"3,7"` or `[3, 7]` |
| `wordpress_tags` | Numeric tag IDs: `"12,15"` or `[12, 15]` |

```python
client.upload_text(
    user="my-profile",
    platforms=["mastodon", "wordpress", "slack"],
    text="Release notes",
    description="Full changelog for 1.1 …",
    platform_options={
        "mastodon_title": "Release notes are out (short version for Mastodon)",
        "mastodon_visibility": "unlisted",
        "mastodon_language": "en",
        "wordpress_status": "draft",
        "wordpress_categories": [3, 7],
        "wordpress_tags": "12,15",
    },
)
```

## Connect platforms with credentials

Discord, Telegram, Slack, Mastodon, WordPress, Dev.to, Hashnode, Lemmy, Nostr, Whop and Listmonk connect with credentials instead of an OAuth login. `connect_with_credentials(profile, platform, credentials=None, **fields)` sends them to the API, which verifies them live with the platform before saving them (encrypted). Connecting again replaces the stored credentials. Pass the fields as a dict, as keyword arguments, or both (keyword arguments win):

| Platform | Fields (`?` = optional) |
|---|---|
| `discord` | `webhook_url` — channel webhook URL |
| `telegram` | `bot_token`, `chat_id` — token from @BotFather; public channel `@username` or numeric chat ID |
| `slack` | `webhook_url` — incoming webhook URL (Slack can only verify it on the first post) |
| `mastodon` | `instance_url`, `access_token` — e.g. `https://mastodon.social`; token with `write:statuses` and `write:media` |
| `wordpress` | `site_url`, `username`, `app_password` — self-hosted site, a user who can publish, an application password |
| `devto` | `api_key`, `base_url?` — DEV API key (Settings → Extensions); `base_url` only for another Forem site |
| `hashnode` | `access_token`, `publication_id` — personal access token; the publication needs a Hashnode Pro plan |
| `lemmy` | `instance_url`, `username`, `password`, `community`, `totp?` — community as `name` or `name@instance`; `totp` only with 2FA (used once, not stored) |
| `nostr` | `nsec`, `relays?`, `blossom_server?` — `nsec1…` or hex key; comma-separated `wss://` relays; Blossom server for photos |
| `whop` | `api_key` + **one of** `experience_id` (forum, `exp_…`) or `channel_id` (chat) |
| `listmonk` | `base_url`, `api_user`, `api_token`, `list_ids` — API user with campaigns:manage, campaigns:send, lists:get_all, media:manage; list IDs comma-separated |

```python
from post_upload import PostUploadError

res = client.connect_with_credentials("my-profile", "telegram", bot_token="123456789:AA…", chat_id="@mychannel")
print(res["platform_username"])

client.connect_with_credentials("my-profile", "devto", api_key="…")
client.connect_with_credentials("my-profile", "whop", api_key="…", experience_id="exp_XXXXXXXX")

try:
    client.connect_with_credentials(
        "my-profile", "mastodon", {"instance_url": "https://mastodon.social", "access_token": "bad"}
    )
except PostUploadError as e:
    if e.status_code == 400:
        # e.code: "MISSING_FIELD" | "FIELD_TOO_LONG" | "INVALID_CREDENTIALS"
        print(e.code, (e.body.get("details") or {}).get("field"), e)
```

Other failures: `404` (profile not found) and `502` (the platform could not be reached to verify the details). The required fields per platform are also exported as `CREDENTIAL_PLATFORMS`, the optional ones as `CREDENTIAL_OPTIONAL_FIELDS`.

## All methods

| Area | Method | Endpoint |
|---|---|---|
| Upload | `upload_video(user, platforms, video_path=None, ..., video_url=None, video_file=None, staged_file_id=None, chunked=None, on_progress=None)` | `POST /api/upload` (+ chunked endpoints for large files) |
| | `upload_photos(user, platforms, photo_paths=None, ..., photo_urls=None)` | `POST /api/upload_photos` |
| | `upload_text(user, platforms, title=None, ..., text=None)` | `POST /api/upload_text` |
| | `upload_document(user, document_path, title=None, ...)` | `POST /api/upload_document` |
| Chunked upload | `upload_large_file(video, filename=None, mime_type=None, on_progress=None)` → `staged_file_id` | init + parts + complete |
| | `create_upload_session(filename, size, mime_type=None)` | `POST /api/upload/chunked/init` |
| | `upload_part(session_id, index, data, retries=None)` | `PUT /api/upload/chunked/:id/:index` |
| | `complete_upload_session(session_id)` | `POST /api/upload/chunked/:id/complete` |
| | `get_upload_session(session_id)` | `GET /api/upload/chunked/:id` |
| | `abort_upload_session(session_id)` | `DELETE /api/upload/chunked/:id` |
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
| | `connect_with_credentials(profile, platform, credentials=None, **fields)` | `POST /api/profiles/:name/connect/:platform/credentials` |
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

Every API failure raises `PostUploadError` (`IndReelForgeError` is the same class):

| Attribute | Description |
|---|---|
| `status_code` (alias `status`) | HTTP status (`0` = network error, `408` = client-side timeout) |
| `code` | API error code (`error_code` / `code`), e.g. `MISSING_VIDEO`, or `NETWORK_ERROR` / `TIMEOUT` |
| `body` | Parsed response body (dict) or raw text |
| `headers` | Response headers (`None` for network errors), e.g. `Retry-After` |
| `str(err)` | Human-readable message from the API |

```python
from post_upload import PostUploadError

try:
    client.upload_video(user="my-profile", platforms=["tiktok"], video_path="./v.mp4")
except PostUploadError as e:
    print(f"API error {e.status_code} ({e.code}): {e}", e.body)
except FileNotFoundError as e:
    print(f"File not found: {e}")
```

Invalid arguments (for example neither `video_path` nor `video_url`, or more than 10 photos) raise `ValueError` before any request is made.

## MCP: use Post-Upload from AI agents

Post-Upload runs a hosted [Model Context Protocol](https://modelcontextprotocol.io) server, so Claude, ChatGPT, Cursor and other agents can publish posts, check upload status and read analytics with your API key. No Python is needed for this.

**Hosted URL (Streamable HTTP)** — for clients that support remote MCP servers with headers:

- URL: `https://api.post-upload.com/mcp`
- Header: `Authorization: ApiKey your-api-key`

**stdio clients (Claude Desktop, Cursor)** — use the bridge from the [Node.js package](https://www.npmjs.com/package/post-upload) (requires Node.js 18+):

```json
{
  "mcpServers": {
    "post-upload": {
      "command": "npx",
      "args": ["-y", "post-upload", "mcp"],
      "env": {
        "POST_UPLOAD_API_KEY": "your-api-key"
      }
    }
  }
}
```

See the [Node.js SDK README](https://github.com/Indreelforge/indreelforge-node) and the [MCP guide](https://docs.post-upload.com/guides/mcp-server) for all MCP options. Agents that can't stream large files through MCP can pass a public `video_url` (up to 500 MB).

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
| Discord | ✓ | ✓ | ✓ | - | ✓ |
| Telegram | ✓ | ✓ | ✓ | - | ✓ |
| Slack | - | - | ✓ | - | ✓ |
| Mastodon | ✓ | ✓ | ✓ | - | ✓ |
| WordPress | ✓ | ✓ | ✓ | - | ✓ |
| Dev.to | - | - | ✓ | - | ✓ |
| Hashnode | - | ✓ | ✓ | - | ✓ |
| Lemmy | - | ✓ | ✓ | - | ✓ |
| Nostr | - | ✓ | ✓ | - | ✓ |
| Whop | ✓ | ✓ | ✓ | - | ✓ |
| Listmonk | - | ✓ | ✓ | - | ✓ |

Discord, Telegram, Slack, Mastodon, WordPress, Dev.to, Hashnode, Lemmy, Nostr, Whop and Listmonk are connected with [credentials](#connect-platforms-with-credentials); the others connect through an OAuth login in the dashboard (or an app password for Bluesky).

## Upgrading

### From `indreelforge` (1.1 and earlier) to `post-upload` 1.2

```bash
pip uninstall indreelforge
pip install post-upload
```

```python
# before
from indreelforge import IndReelForge, IndReelForgeError
client = IndReelForge(api_key="...")

# after
from post_upload import PostUpload, PostUploadError
client = PostUpload(api_key="...")
```

Nothing else changes: methods, arguments and return values are the same. Until you update your imports, `import indreelforge` keeps working from the `post-upload` package (it warns with `DeprecationWarning`), and `IndReelForge`, `InReelForge`, `IndReelForgeError` and `InReelForgeError` remain aliases of the new names. The default API address is now `https://api.post-upload.com` (the old address keeps working while the move completes), and the key can come from `POST_UPLOAD_API_KEY` (`INDREELFORGE_API_KEY` is still read).

Don't keep both packages installed: both provide an `indreelforge` module.

### From 1.0 to 1.1

1.1 is backward compatible: existing method names, positional arguments and return values keep working.

- The class was renamed `IndReelForge` (now `PostUpload`); `InReelForge` is still exported as an alias.
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

- [Website](https://post-upload.com)
- [API documentation](https://docs.post-upload.com)
- [Dashboard](https://post-upload.com/dashboard)
- [Pricing](https://post-upload.com/pricing)
- [Node.js SDK & MCP bridge](https://www.npmjs.com/package/post-upload)
- [Changelog](https://github.com/Indreelforge/indreelforge-python/blob/main/CHANGELOG.md)

## License

MIT
