# Changelog

## 1.2.0 (2026-11-22)

### Renamed: `indreelforge` is now `post-upload`

- The distribution is now **`post-upload`** on PyPI (`pip install post-upload`) and the import package is **`post_upload`**. The main names are `PostUpload` and `PostUploadError`.
- Backward compatible: `IndReelForge`, `InReelForge`, `IndReelForgeError` and `InReelForgeError` are aliases of the new names, and the package still ships an `indreelforge` module (`import indreelforge`, `from indreelforge.client import ...`) that re-exports `post_upload` and emits a `DeprecationWarning`.
- Default API address is now `https://api.post-upload.com` (the old address keeps working while the move completes).
- `api_key` is optional: when omitted it is read from `POST_UPLOAD_API_KEY`, then `INDREELFORGE_API_KEY`.
- `User-Agent` is now `post-upload-python/<version>`.
- The old `indreelforge` PyPI package gets no further releases.

### Large videos: chunked upload up to 500 MB

- `upload_video` sends local files larger than 95 MiB (`CHUNKED_THRESHOLD`, or the `chunked_threshold` client option) through the chunked upload endpoints automatically, then posts them by `staged_file_id`. `chunked=True` / `chunked=False` force or disable it.
- Parts are sent sequentially, exactly the server's `chunk_size` each, read from disk part by part, and retried with exponential backoff on 429, 5xx and network errors (honouring `Retry-After`; `max_retries` client option, default 5). A failed upload cancels its session.
- `on_progress(sent_bytes, total_bytes)` callback.
- New methods: `upload_large_file()` (returns the `staged_file_id`), `create_upload_session()`, `upload_part()`, `complete_upload_session()`, `get_upload_session()`, `abort_upload_session()`.
- `upload_video` also accepts `video_file` (bytes or a binary file object / stream) and `staged_file_id`; exactly one video source is allowed.
- `PostUploadError.headers` exposes the response headers.

### Platforms and responses

- Six new platforms: `devto`, `hashnode`, `lemmy`, `nostr`, `whop`, `listmonk` (22 in total) in `PLATFORMS` and `CREDENTIAL_PLATFORMS`, with optional credential fields in the new `CREDENTIAL_OPTIONAL_FIELDS`.
- `data.skipped_platforms` (platforms the file is too large for) is typed (`SkippedPlatform`, `UploadResponse`) and returned by the new `get_skipped_platforms(response)` helper.
- Response `TypedDict`s are exported and the package ships `py.typed`.

## 1.1.0

- Typed methods for the full REST API, typed errors, explicit `User-Agent`.
- Discord, Telegram, Slack, Mastodon and WordPress, and `connect_with_credentials()`.
- Class renamed `IndReelForge` (`InReelForge` kept as an alias).

## 1.0.0

- First release.
