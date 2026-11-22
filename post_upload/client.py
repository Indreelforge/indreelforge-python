"""Post-Upload API client."""

import io
import json as _json
import math
import mimetypes
import os
import shutil
import tempfile
import time
from contextlib import ExitStack
from typing import (
    IO, Any, BinaryIO, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union,
)
from urllib.parse import quote

import requests

from ._version import __version__
from .types import (
    CompletedUploadSession, SkippedPlatform, UploadResponse, UploadSession, UploadSessionStatus,
)

DEFAULT_BASE_URL = "https://api.post-upload.com"
USER_AGENT = f"post-upload-python/{__version__}"
MAX_PHOTOS = 10

#: Environment variables read (in order) when no ``api_key`` is passed.
API_KEY_ENV_VARS = ("POST_UPLOAD_API_KEY", "INDREELFORGE_API_KEY")

#: Local videos larger than this (bytes) are sent with the chunked upload
#: endpoints automatically. Requests reach the API through Cloudflare, which
#: caps a single request body at 100 MB.
CHUNKED_THRESHOLD = 95 * 1024 * 1024

#: Largest video the API accepts (via chunked upload or ``video_url``).
MAX_VIDEO_BYTES = 500 * 1024 * 1024

#: Statuses on which a chunk upload is retried (plus network errors / timeouts).
RETRYABLE_STATUSES = frozenset({0, 408, 425, 429, 500, 502, 503, 504})

#: Every platform key known to this SDK version.
PLATFORMS = (
    "instagram", "youtube", "tiktok", "facebook", "x", "linkedin", "threads", "pinterest",
    "reddit", "bluesky", "google_business", "discord", "telegram", "slack", "mastodon", "wordpress",
    "devto", "hashnode", "lemmy", "nostr", "whop", "listmonk",
)

#: Platforms connected with :meth:`PostUpload.connect_with_credentials`, and their required fields.
CREDENTIAL_PLATFORMS: Dict[str, Tuple[str, ...]] = {
    "discord": ("webhook_url",),
    "telegram": ("bot_token", "chat_id"),
    "slack": ("webhook_url",),
    "mastodon": ("instance_url", "access_token"),
    "wordpress": ("site_url", "username", "app_password"),
    "devto": ("api_key",),
    "hashnode": ("access_token", "publication_id"),
    "lemmy": ("instance_url", "username", "password", "community"),
    "nostr": ("nsec",),
    "whop": ("api_key",),
    "listmonk": ("base_url", "api_user", "api_token", "list_ids"),
}

#: Optional credential fields per platform (see :data:`CREDENTIAL_PLATFORMS`).
#: Whop needs exactly one of ``experience_id`` (forum) or ``channel_id`` (chat).
CREDENTIAL_OPTIONAL_FIELDS: Dict[str, Tuple[str, ...]] = {
    "devto": ("base_url",),
    "lemmy": ("totp",),
    "nostr": ("relays", "blossom_server"),
    "whop": ("experience_id", "channel_id"),
}

ProgressCallback = Callable[[int, int], None]
VideoSource = Union[str, "os.PathLike[str]", bytes, bytearray, memoryview, IO[bytes]]

Field = Tuple[str, Tuple[Optional[str], Any]]


class PostUploadError(Exception):
    """Error from the Post-Upload API (or a network failure / timeout).

    Attributes:
        status_code: HTTP status code (0 = network error, 408 = client-side timeout).
        code: Machine-readable error code from the API (``error_code`` / ``code``),
            or ``NETWORK_ERROR`` / ``TIMEOUT``; ``None`` when not provided.
        body: Parsed JSON response body (dict) or raw text, if any.
        headers: Response headers (``None`` for network errors).
    """

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        code: Optional[str] = None,
        body: Any = None,
        headers: Optional[Mapping[str, str]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.body = body
        self.headers = headers

    @property
    def status(self) -> int:
        """Alias of :attr:`status_code`."""
        return self.status_code

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self.message!r}, status_code={self.status_code!r}, "
            f"code={self.code!r})"
        )


#: Deprecated aliases (the SDK was called IndReelForge / InReelForge before
#: 1.2.0). They are the same class, so ``except IndReelForgeError`` keeps working.
IndReelForgeError = PostUploadError
InReelForgeError = PostUploadError


def _form_value(value: Any) -> Optional[List[str]]:
    """Convert a Python value to one or more multipart field values."""
    if value is None:
        return None
    if isinstance(value, bool):
        return ["true" if value else "false"]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for v in value:
            if v is None:
                continue
            out.append(("true" if v else "false") if isinstance(v, bool) else str(v))
        return out
    if isinstance(value, dict):
        return [_json.dumps(value)]
    return [str(value)]


def get_skipped_platforms(response: Any) -> List[SkippedPlatform]:
    """
    Platforms an upload skipped because the file is too large for them.

    They are stored as ``failed`` with ``FILE_TOO_LARGE_FOR_PLATFORM``, never
    sent, and use none of the platform's quota; the other platforms publish.
    Returns ``[]`` when nothing was skipped.

    Example:
        >>> res = client.upload_video("my-brand", ["youtube", "bluesky"], video_path="big.mp4")
        >>> for s in get_skipped_platforms(res):
        ...     print(s["platform"], s.get("message"))
    """
    if not isinstance(response, dict):
        return []
    data = response.get("data")
    skipped = data.get("skipped_platforms") if isinstance(data, dict) else None
    if skipped is None:
        skipped = response.get("skipped_platforms")
    return list(skipped) if isinstance(skipped, list) else []


def _seg(value: Any) -> str:
    return quote(str(value), safe="")


def _require(value: Any, name: str) -> None:
    if value is None or value == "":
        raise ValueError(f"{name} is required")


class PostUpload:
    """
    Official Python client for the Post-Upload API.

    Publish videos, photos, text and documents to 22 platforms: Instagram,
    YouTube, TikTok, Facebook, X (Twitter), LinkedIn, Threads, Pinterest,
    Reddit, Bluesky, Google Business Profile, Discord, Telegram, Slack,
    Mastodon, WordPress, Dev.to, Hashnode, Lemmy, Nostr, Whop and Listmonk.

    Args:
        api_key: Your Post-Upload API key. When omitted it is read from the
            ``POST_UPLOAD_API_KEY`` environment variable (or the older
            ``INDREELFORGE_API_KEY``).
        base_url: API base URL (default: https://api.post-upload.com).
        timeout: Request timeout in seconds (default: 120).
        session: Optional ``requests.Session`` to use.
        headers: Optional extra headers sent with every request.
        chunked_threshold: Local videos larger than this many bytes are sent
            with the chunked upload endpoints (default :data:`CHUNKED_THRESHOLD`,
            95 MiB).
        max_retries: How often a failed chunk is retried on 429 / 5xx /
            network errors (default 5).

    The client can be used as a context manager to close its HTTP session::

        with PostUpload(api_key="...") as client:
            client.get_me()

    Example:
        >>> client = PostUpload(api_key="your-api-key")
        >>> result = client.upload_video(
        ...     user="my-profile",
        ...     platforms=["instagram", "tiktok"],
        ...     video_path="./video.mp4",
        ...     title="My video",
        ... )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: Union[int, float] = 120,
        session: Optional[requests.Session] = None,
        headers: Optional[Mapping[str, str]] = None,
        *,
        chunked_threshold: Optional[int] = None,
        max_retries: int = 5,
    ):
        if not api_key:
            for var in API_KEY_ENV_VARS:
                api_key = os.environ.get(var)
                if api_key:
                    break
        if not api_key:
            raise ValueError("api_key is required (or set POST_UPLOAD_API_KEY)")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.chunked_threshold = chunked_threshold
        self.max_retries = max(0, int(max_retries))
        self._session = session or requests.Session()
        # Always send an explicit User-Agent: the API sits behind Cloudflare,
        # which blocks generic library agents such as "Python-urllib".
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        if headers:
            self._session.headers.update(dict(headers))
        self._session.headers["Authorization"] = f"Apikey {self.api_key}"

    # ── Lifecycle ──

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "PostUpload":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ── Core request ──

    def _request(
        self,
        method: str,
        path: str,
        json: Optional[Any] = None,
        data: Optional[Any] = None,
        files: Optional[Any] = None,
        params: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> Any:
        """Make an API request and return the decoded JSON body."""
        url = f"{self.base_url}{path}"
        if params:
            params = {k: v for k, v in params.items() if v is not None and v != ""}
        try:
            resp = self._session.request(
                method,
                url,
                json=json,
                data=data,
                files=files,
                params=params or None,
                headers=dict(headers) if headers else None,
                timeout=self.timeout,
            )
        except requests.exceptions.Timeout:
            raise PostUploadError("Request timed out", 408, "TIMEOUT") from None
        except requests.exceptions.ConnectionError as e:
            raise PostUploadError(f"Connection failed: {e}", 0, "NETWORK_ERROR") from None

        text = resp.text or ""
        body: Any = {}
        if text.strip():
            try:
                body = resp.json()
            except ValueError:
                body = text

        if not resp.ok:
            obj = body if isinstance(body, dict) else None
            message = None
            if obj:
                message = obj.get("error_description") or obj.get("error") or obj.get("message")
            if not message:
                message = text.strip()[:300] if isinstance(body, str) and text.strip() else f"HTTP {resp.status_code}"
            code = (obj.get("error_code") or obj.get("code")) if obj else None
            raise PostUploadError(
                str(message),
                resp.status_code,
                str(code) if code is not None else None,
                body if text.strip() else None,
                dict(resp.headers),
            )

        return body

    @staticmethod
    def _idem(idempotency_key: Optional[str]) -> Optional[Dict[str, str]]:
        return {"Idempotency-Key": idempotency_key} if idempotency_key else None

    @staticmethod
    def _fields(pairs: Sequence[Tuple[str, Any]]) -> List[Field]:
        """Multipart text fields (``(name, (None, value))``) from (name, value) pairs."""
        out: List[Field] = []
        for name, value in pairs:
            values = _form_value(value)
            if values is None:
                continue
            for v in values:
                out.append((name, (None, v)))
        return out

    def _build_form_fields(
        self,
        user: str,
        platforms: Optional[Sequence[str]],
        title: Optional[str] = None,
        first_comment: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        description: Optional[str] = None,
        add_to_queue: Optional[bool] = None,
        async_upload: Optional[bool] = None,
        request_id: Optional[str] = None,
        platform_options: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
    ) -> List[Field]:
        _require(user, "user")
        pairs: List[Tuple[str, Any]] = [("user", user)]
        for p in platforms or []:
            pairs.append(("platform[]", p))
        pairs += [
            ("title", title or None),
            ("description", description or None),
            ("first_comment", first_comment or None),
            ("schedule", schedule or None),
            ("timezone", timezone or None),
            ("add_to_queue", add_to_queue),
            ("async_upload", async_upload),
            ("request_id", request_id or None),
        ]
        for source in (platform_options, extra):
            for key, value in (source or {}).items():
                pairs.append((key, value))
        return self._fields(pairs)

    def _build_form_data(
        self,
        user: str,
        platforms: List[str],
        title: Optional[str] = None,
        first_comment: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Deprecated 1.0 helper kept for compatibility; returns a plain form dict."""
        data: Dict[str, Any] = {"user": user, "platform[]": platforms}
        if title:
            data["title"] = title
        if first_comment:
            data["first_comment"] = first_comment
        if schedule:
            data["schedule"] = schedule
        if timezone:
            data["timezone"] = timezone
        for key, value in kwargs.items():
            data[key] = value
        return data

    # ── Upload ──

    def upload_video(
        self,
        user: str,
        platforms: List[str],
        video_path: Optional[Union[str, "os.PathLike[str]"]] = None,
        title: Optional[str] = None,
        first_comment: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        *,
        video_url: Optional[str] = None,
        video_file: Optional[Union[bytes, bytearray, memoryview, IO[bytes]]] = None,
        staged_file_id: Optional[str] = None,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        chunked: Optional[bool] = None,
        on_progress: Optional[ProgressCallback] = None,
        description: Optional[str] = None,
        add_to_queue: Optional[bool] = None,
        async_upload: Optional[bool] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        platform_options: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> UploadResponse:
        """
        Upload a video to one or more platforms.

        Give the video in exactly one way: ``video_path`` (local file),
        ``video_file`` (bytes or a binary file object / stream), ``video_url``
        (public URL the server downloads, up to 500 MB) or ``staged_file_id``
        (a file already sent with :meth:`upload_large_file`).

        Local files larger than :data:`CHUNKED_THRESHOLD` (95 MiB) are sent with
        the chunked upload endpoints automatically (up to 500 MB), then posted
        by ``staged_file_id``. Pass ``chunked=True`` to always do that, or
        ``chunked=False`` to always send one multipart request.

        Args:
            user: Profile name (managed user).
            platforms: Platforms, e.g. ``["instagram", "tiktok", "youtube"]``.
            video_path: Path to a local video file.
            title: Post title / caption.
            first_comment: First comment text.
            schedule: ISO-8601 datetime to publish later.
            timezone: IANA timezone used with ``schedule``.
            video_url: Public http(s) URL of the video; the server downloads it.
            video_file: Video bytes or a binary file object (read part by part
                for a chunked upload; a non-seekable stream is spooled to a
                temporary file first).
            staged_file_id: ID returned by :meth:`complete_upload_session` /
                :meth:`upload_large_file` (usable once).
            filename: File name sent for ``video_file`` (default: the file
                object's name, else ``video.mp4``).
            mime_type: MIME type for a chunked upload (default: from the name).
            chunked: ``True`` / ``False`` to force or disable the chunked
                upload; ``None`` (default) decides by size.
            on_progress: ``on_progress(sent_bytes, total_bytes)``, called after
                each part of a chunked upload.
            description: Long description.
            add_to_queue: Put the post in the profile's next free queue slot.
            async_upload: ``False`` = wait (up to ~50 s) for the publish result;
                the response then has ``sync: True`` and per-platform results.
            request_id: Your own correlation id (see :meth:`get_request_status`).
            idempotency_key: Sent as the ``Idempotency-Key`` header.
            platform_options: Platform-specific fields (e.g. ``{"privacy_level": "PUBLIC_TO_EVERYONE"}``).
            **kwargs: Platform-specific fields (same as ``platform_options``).

        Returns:
            Upload response with ``data.upload_id`` and per-platform statuses.
            Platforms the file is too large for are skipped (the others still
            publish) and listed in ``data.skipped_platforms``; see
            :func:`get_skipped_platforms`.
        """
        sources = [
            x for x in (video_path, video_file, video_url, staged_file_id)
            if x is not None and not (isinstance(x, str) and x == "")
        ]
        if not sources:
            raise ValueError(
                "upload_video requires video_path, video_file, video_url or staged_file_id"
            )
        if len(sources) > 1:
            raise ValueError(
                "upload_video takes only one of video_path, video_file, video_url or staged_file_id"
            )
        if video_path is not None and not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        fields = self._build_form_fields(
            user, platforms, title, first_comment, schedule, timezone, description,
            add_to_queue, async_upload, request_id, platform_options, kwargs,
        )
        with ExitStack() as stack:
            if video_path is not None or video_file is not None:
                source: VideoSource = video_path if video_path is not None else video_file  # type: ignore[assignment]
                fh, name, size = self._open_source(stack, source, filename)
                if chunked or (chunked is None and size > self._threshold()):
                    staged_file_id = self._chunked_upload(fh, name, size, mime_type, on_progress)
                else:
                    fields.append(("video", (name, fh)))
            if staged_file_id:
                fields.append(("staged_file_id", (None, str(staged_file_id))))
            elif video_url:
                fields.append(("video", (None, str(video_url))))
            return self._request(
                "POST", "/api/upload", files=fields, headers=self._idem(idempotency_key)
            )

    # ── Chunked upload (videos up to 500 MB) ──

    def _threshold(self) -> int:
        return CHUNKED_THRESHOLD if self.chunked_threshold is None else int(self.chunked_threshold)

    @staticmethod
    def _open_source(
        stack: ExitStack, source: VideoSource, filename: Optional[str]
    ) -> Tuple[BinaryIO, str, int]:
        """Return a seekable binary file positioned at the start, its name and size."""
        if isinstance(source, (str, os.PathLike)):
            path = os.fspath(source)
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Video file not found: {path}")
            fh = stack.enter_context(open(path, "rb"))
            return fh, filename or os.path.basename(path), os.path.getsize(path)
        if isinstance(source, (bytes, bytearray, memoryview)):
            data = bytes(source)
            return io.BytesIO(data), filename or "video.mp4", len(data)
        if not hasattr(source, "read"):
            raise TypeError("video must be a path, bytes or a binary file object")
        name = filename or os.path.basename(str(getattr(source, "name", "") or "")) or "video.mp4"
        try:
            seekable = bool(source.seekable()) if hasattr(source, "seekable") else False
        except Exception:  # pragma: no cover - exotic streams
            seekable = False
        if seekable and source.tell() == 0:
            size = source.seek(0, os.SEEK_END)
            source.seek(0)
            return source, name, size  # type: ignore[return-value]
        # Non-seekable stream (or one not at offset 0): spool it to a temporary
        # file (in memory up to the threshold, then on disk) so its size is
        # known and every part can be re-read for a retry.
        spool = stack.enter_context(tempfile.SpooledTemporaryFile(max_size=CHUNKED_THRESHOLD))
        shutil.copyfileobj(source, spool, 1024 * 1024)
        size = spool.tell()
        spool.seek(0)
        return spool, name, size  # type: ignore[return-value]

    def create_upload_session(
        self, filename: str, size: int, mime_type: Optional[str] = None
    ) -> UploadSession:
        """
        Start a chunked upload (``POST /api/upload/chunked/init``).

        Args:
            filename: Video file name, e.g. ``"episode.mp4"`` (MP4, MOV, AVI, WebM, MKV).
            size: Total size in bytes (at most 500 MB).
            mime_type: e.g. ``"video/mp4"`` (default: guessed from ``filename``).

        Returns:
            ``upload_session_id``, ``chunk_size``, ``total_chunks`` and ``expires_at``.
        """
        _require(filename, "filename")
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise ValueError("size must be a positive number of bytes")
        mime = mime_type or mimetypes.guess_type(filename)[0] or "video/mp4"
        return self._request(
            "POST",
            "/api/upload/chunked/init",
            json={"filename": filename, "size": size, "mime_type": mime},
        )

    def upload_part(
        self,
        session_id: str,
        index: int,
        data: Union[bytes, bytearray, memoryview],
        *,
        retries: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Send one part (``PUT /api/upload/chunked/{id}/{index}``).

        Every part must be exactly the session's ``chunk_size`` bytes except the
        last. Retried with exponential backoff on 429, 5xx and network errors
        (honouring ``Retry-After``); re-sending an index replaces it.

        Args:
            session_id: ``upload_session_id`` from :meth:`create_upload_session`.
            index: 0-based part number.
            data: The part's bytes.
            retries: Override the client's ``max_retries`` for this part.
        """
        _require(session_id, "session_id")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError("index must be a non-negative integer")
        body = bytes(data)
        attempts = (self.max_retries if retries is None else max(0, int(retries))) + 1
        for attempt in range(attempts):
            try:
                return self._request(
                    "PUT",
                    f"/api/upload/chunked/{_seg(session_id)}/{index}",
                    data=body,
                    headers={"Content-Type": "application/octet-stream"},
                )
            except PostUploadError as e:
                if e.status_code not in RETRYABLE_STATUSES or attempt == attempts - 1:
                    raise
                self._sleep(self._retry_delay(e, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def complete_upload_session(self, session_id: str) -> CompletedUploadSession:
        """
        Assemble and check the file (``POST /api/upload/chunked/{id}/complete``).

        Returns:
            ``staged_file_id`` (pass it to :meth:`upload_video`; usable once).
        """
        _require(session_id, "session_id")
        return self._request("POST", f"/api/upload/chunked/{_seg(session_id)}/complete")

    def get_upload_session(self, session_id: str) -> UploadSessionStatus:
        """Parts received so far (``GET /api/upload/chunked/{id}``), to resume an upload."""
        _require(session_id, "session_id")
        return self._request("GET", f"/api/upload/chunked/{_seg(session_id)}")

    def abort_upload_session(self, session_id: str) -> Dict[str, Any]:
        """Cancel a chunked upload and delete its parts (``DELETE /api/upload/chunked/{id}``)."""
        _require(session_id, "session_id")
        return self._request("DELETE", f"/api/upload/chunked/{_seg(session_id)}")

    def upload_large_file(
        self,
        video: VideoSource,
        *,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> str:
        """
        Send a video with the chunked upload endpoints and return its ``staged_file_id``.

        Runs init, every part (sequentially, with retries) and complete. If
        anything fails after the session was created, the session is cancelled
        (best effort) and the error is raised.

        Args:
            video: Path, bytes or binary file object.
            filename: File name (default: from the path / file object, else ``video.mp4``).
            mime_type: MIME type (default: guessed from the name).
            on_progress: ``on_progress(sent_bytes, total_bytes)`` after each part.

        Example:
            >>> staged = client.upload_large_file("./episode.mp4")
            >>> client.upload_video("my-brand", ["youtube"], staged_file_id=staged, title="Episode 1")
        """
        with ExitStack() as stack:
            fh, name, size = self._open_source(stack, video, filename)
            return self._chunked_upload(fh, name, size, mime_type, on_progress)

    def _chunked_upload(
        self,
        fh: BinaryIO,
        name: str,
        size: int,
        mime_type: Optional[str],
        on_progress: Optional[ProgressCallback],
    ) -> str:
        if size <= 0:
            raise ValueError("The video file is empty")
        if size > MAX_VIDEO_BYTES:
            raise ValueError(
                f"The video is {size} bytes; the API accepts at most {MAX_VIDEO_BYTES} bytes (500 MB)"
            )
        session = self.create_upload_session(name, size, mime_type)
        session_id = session.get("upload_session_id") if isinstance(session, dict) else None
        if not session_id:
            raise PostUploadError("Chunked upload init returned no upload_session_id", 0, None, session)
        try:
            chunk_size = int(session.get("chunk_size") or 0)
            if chunk_size <= 0:
                raise PostUploadError("Chunked upload init returned no chunk_size", 0, None, session)
            total = int(session.get("total_chunks") or math.ceil(size / chunk_size))
            sent = 0
            for index in range(total):
                offset = index * chunk_size
                length = min(chunk_size, size - offset)
                fh.seek(offset)
                part = fh.read(length)
                if len(part) != length:
                    raise IOError(
                        f"Could not read part {index}: expected {length} bytes, got {len(part)}"
                    )
                self.upload_part(session_id, index, part)
                sent += length
                if on_progress:
                    on_progress(sent, size)
            done = self.complete_upload_session(session_id)
            staged = done.get("staged_file_id") if isinstance(done, dict) else None
            if not staged:
                raise PostUploadError(
                    "Chunked upload complete returned no staged_file_id", 0, None, done
                )
            return str(staged)
        except BaseException:
            try:
                self.abort_upload_session(session_id)
            except Exception:
                pass
            raise

    @staticmethod
    def _retry_delay(error: PostUploadError, attempt: int) -> float:
        retry_after = None
        for key, value in (error.headers or {}).items():
            if key.lower() == "retry-after":
                retry_after = value
                break
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), 60.0)
            except (TypeError, ValueError):
                pass
        return min(1.0 * (2 ** attempt), 30.0)

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(seconds)

    def upload_photos(
        self,
        user: str,
        platforms: List[str],
        photo_paths: Optional[List[str]] = None,
        title: Optional[str] = None,
        first_comment: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        *,
        photo_urls: Optional[List[str]] = None,
        description: Optional[str] = None,
        add_to_queue: Optional[bool] = None,
        async_upload: Optional[bool] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        platform_options: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Upload photos (single image or carousel) to one or more platforms.

        Args:
            user: Profile name.
            platforms: List of platforms.
            photo_paths: Paths to local photo files.
            photo_urls: Public http(s) photo URLs. At most 10 photos in total.
            title: Post title / caption.

        Other arguments are the same as :meth:`upload_video`.
        """
        paths = list(photo_paths or [])
        urls = list(photo_urls or [])
        if not paths and not urls:
            raise ValueError("upload_photos requires photo_paths or photo_urls")
        if len(paths) + len(urls) > MAX_PHOTOS:
            raise ValueError(f"upload_photos accepts at most {MAX_PHOTOS} photos")
        for p in paths:
            if not os.path.isfile(p):
                raise FileNotFoundError(f"Photo file not found: {p}")

        fields = self._build_form_fields(
            user, platforms, title, first_comment, schedule, timezone, description,
            add_to_queue, async_upload, request_id, platform_options, kwargs,
        )
        with ExitStack() as stack:
            for p in paths:
                fh = stack.enter_context(open(p, "rb"))
                fields.append(("photos[]", (os.path.basename(p), fh)))
            for u in urls:
                fields.append(("photo_urls[]", (None, str(u))))
            return self._request(
                "POST", "/api/upload_photos", files=fields, headers=self._idem(idempotency_key)
            )

    def upload_text(
        self,
        user: str,
        platforms: List[str],
        title: Optional[str] = None,
        first_comment: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        *,
        text: Optional[str] = None,
        description: Optional[str] = None,
        add_to_queue: Optional[bool] = None,
        async_upload: Optional[bool] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        platform_options: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Publish a text-only post.

        Args:
            user: Profile name.
            platforms: List of platforms.
            title: Text content (kept for 1.0 compatibility; same as ``text``).
            text: Text content. Either ``text`` or ``title`` is required.

        Other arguments are the same as :meth:`upload_video`.
        """
        content = text if text is not None else title
        if not content:
            raise ValueError("upload_text requires text (or title)")
        fields = self._build_form_fields(
            user, platforms, None, first_comment, schedule, timezone, description,
            add_to_queue, async_upload, request_id, platform_options, kwargs,
        )
        fields.append(("text", (None, content)))
        return self._request(
            "POST", "/api/upload_text", files=fields, headers=self._idem(idempotency_key)
        )

    def upload_document(
        self,
        user: str,
        document_path: str,
        title: Optional[str] = None,
        *,
        description: Optional[str] = None,
        first_comment: Optional[str] = None,
        visibility: Optional[str] = None,
        target_linkedin_page_id: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        platform_options: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Publish a LinkedIn document post (PDF, PPT, PPTX, DOC, DOCX; max 100 MB).

        Args:
            user: Profile name.
            document_path: Path to the document file.
            title: Post title (defaults to the file name on the server).
            visibility: LinkedIn visibility, e.g. ``"PUBLIC"``.
            target_linkedin_page_id: Post as a LinkedIn company page.
        """
        _require(user, "user")
        _require(document_path, "document_path")
        if not os.path.isfile(document_path):
            raise FileNotFoundError(f"Document file not found: {document_path}")
        pairs: List[Tuple[str, Any]] = [
            ("user", user),
            ("title", title or None),
            ("description", description or None),
            ("first_comment", first_comment or None),
            ("visibility", visibility or None),
            ("target_linkedin_page_id", target_linkedin_page_id or None),
            ("schedule", schedule or None),
            ("timezone", timezone or None),
            ("request_id", request_id or None),
        ]
        for source in (platform_options, kwargs):
            pairs.extend((source or {}).items())
        fields = self._fields(pairs)
        with ExitStack() as stack:
            fh = stack.enter_context(open(document_path, "rb"))
            fields.append(("document", (os.path.basename(document_path), fh)))
            return self._request(
                "POST", "/api/upload_document", files=fields, headers=self._idem(idempotency_key)
            )

    # ── Account / uploads ──

    def get_me(self) -> Dict[str, Any]:
        """Current account: plan, monthly usage and API key id."""
        return self._request("GET", "/api/me")

    def get_upload_status(self, upload_id: str) -> Dict[str, Any]:
        """Status of one upload (per-platform results, post ids, errors)."""
        _require(upload_id, "upload_id")
        return self._request("GET", f"/api/upload/{_seg(upload_id)}/status")

    def get_request_status(self, request_id: str) -> Dict[str, Any]:
        """Look an upload up by your own ``request_id`` (or by upload id)."""
        _require(request_id, "request_id")
        return self._request("GET", "/api/uploadposts/status", params={"request_id": request_id})

    def list_uploads(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Paginated upload history (``limit`` 1-100, ``page`` 1-based, optional ``status`` filter)."""
        return self._request(
            "GET", "/api/uploads", params={"limit": limit, "page": page, "status": status}
        )

    def get_upload(self, upload_id: str) -> Dict[str, Any]:
        """A single upload record."""
        _require(upload_id, "upload_id")
        return self._request("GET", f"/api/uploads/{_seg(upload_id)}")

    def delete_upload(self, upload_id: str) -> Dict[str, Any]:
        """Delete an upload record and its stored media (not allowed while processing)."""
        _require(upload_id, "upload_id")
        return self._request("DELETE", f"/api/uploads/{_seg(upload_id)}")

    def retry_upload(self, upload_id: str) -> Dict[str, Any]:
        """Retry a failed upload."""
        _require(upload_id, "upload_id")
        return self._request("POST", f"/api/uploads/{_seg(upload_id)}/retry")

    def get_platform_limits(self) -> Dict[str, Any]:
        """Per-platform daily posting limits and current usage."""
        return self._request("GET", "/api/platform-limits")

    # ── Profiles ──

    def get_profiles(self) -> Dict[str, Any]:
        """List all managed profiles."""
        return self._request("GET", "/api/profiles")

    def create_profile(self, name: str) -> Dict[str, Any]:
        """Create a new managed profile."""
        _require(name, "name")
        return self._request("POST", "/api/profiles", json={"name": name})

    def delete_profile(self, name: str) -> Dict[str, Any]:
        """Delete a managed profile."""
        _require(name, "name")
        return self._request("DELETE", f"/api/profiles/{_seg(name)}")

    def connect_with_credentials(
        self,
        profile: str,
        platform: str,
        credentials: Optional[Mapping[str, str]] = None,
        **fields: str,
    ) -> Dict[str, Any]:
        """
        Connect a platform that uses credentials instead of OAuth.

        Pass the platform's fields as a dict, as keyword arguments, or both
        (keyword arguments win)::

            client.connect_with_credentials("my-brand", "telegram", bot_token="123:AA...", chat_id="@mychannel")
            client.connect_with_credentials("my-brand", "mastodon", {"instance_url": "https://mastodon.social",
                                                                    "access_token": "..."})

        Fields per platform (``?`` = optional; see ``CREDENTIAL_PLATFORMS`` and
        ``CREDENTIAL_OPTIONAL_FIELDS``):
            discord: webhook_url | telegram: bot_token, chat_id | slack: webhook_url |
            mastodon: instance_url, access_token | wordpress: site_url, username, app_password |
            devto: api_key, base_url? | hashnode: access_token, publication_id |
            lemmy: instance_url, username, password, community, totp? |
            nostr: nsec, relays?, blossom_server? |
            whop: api_key + one of experience_id (forum) / channel_id (chat) |
            listmonk: base_url, api_user, api_token, list_ids

        The server verifies the credentials live before saving them; connecting
        again replaces them.

        Returns:
            ``{"success": True, "message": ..., "platform_username": ...}``

        Raises:
            PostUploadError: 400 with ``code`` ``MISSING_FIELD``, ``FIELD_TOO_LONG``
                or ``INVALID_CREDENTIALS`` (``e.body["details"]["field"]`` names the
                field), 404 if the profile does not exist, 502 if the platform
                could not be reached.
        """
        _require(profile, "profile")
        _require(platform, "platform")
        body: Dict[str, Any] = dict(credentials or {})
        body.update(fields)
        if not body:
            raise ValueError("credentials are required")
        return self._request(
            "POST",
            f"/api/profiles/{_seg(profile)}/connect/{_seg(platform)}/credentials",
            json=body,
        )

    # ── Scheduling ──

    def get_scheduled_posts(self) -> Dict[str, Any]:
        """All scheduled (not yet published) posts: ``{"scheduled_posts": [...]}``."""
        return self._request("GET", "/api/schedule")

    def update_scheduled_post(
        self,
        job_id: str,
        scheduled_date: Optional[str] = None,
        timezone: Optional[str] = None,
        title: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reschedule or edit a scheduled post."""
        _require(job_id, "job_id")
        body = {
            k: v
            for k, v in {
                "scheduled_date": scheduled_date,
                "timezone": timezone,
                "title": title,
                "caption": caption,
            }.items()
            if v is not None
        }
        return self._request("PATCH", f"/api/schedule/{_seg(job_id)}", json=body)

    def cancel_scheduled_post(self, job_id: str) -> Dict[str, Any]:
        """Cancel a scheduled post."""
        _require(job_id, "job_id")
        return self._request("DELETE", f"/api/schedule/{_seg(job_id)}")

    # ── Queue ──

    def get_queue_settings(self, profile: str) -> Dict[str, Any]:
        """Queue settings (time slots, days, timezone) for a profile."""
        _require(profile, "profile")
        return self._request("GET", "/api/queue/settings", params={"profile": profile})

    def update_queue_settings(
        self,
        profile: str,
        slots: Optional[List[Any]] = None,
        days_of_week: Optional[List[int]] = None,
        timezone: Optional[str] = None,
        max_posts_per_slot: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create or update a profile's queue (``slots`` like ``["09:00", "17:30"]``, days 0=Sunday)."""
        _require(profile, "profile")
        body: Dict[str, Any] = {"profile_username": profile}
        if slots is not None:
            body["slots"] = slots
        if days_of_week is not None:
            body["days_of_week"] = days_of_week
        if timezone is not None:
            body["timezone"] = timezone
        if max_posts_per_slot is not None:
            body["max_posts_per_slot"] = max_posts_per_slot
        return self._request("POST", "/api/queue/settings", json=body)

    def get_queue_preview(self, profile: str, count: Optional[int] = None) -> Dict[str, Any]:
        """Upcoming queue slots for a profile."""
        _require(profile, "profile")
        return self._request("GET", "/api/queue/preview", params={"profile": profile, "count": count})

    def get_next_queue_slot(self, profile: str) -> Dict[str, Any]:
        """The next free queue slot for a profile."""
        _require(profile, "profile")
        return self._request("GET", "/api/queue/next-slot", params={"profile": profile})

    # ── Platform helpers ──

    def get_facebook_pages(self, profile: str) -> Dict[str, Any]:
        """Facebook pages for the profile (use the id as ``facebook_page_id``)."""
        _require(profile, "profile")
        return self._request("GET", "/api/platforms/facebook/pages", params={"profile": profile})

    def get_linkedin_pages(self, profile: str) -> Dict[str, Any]:
        """LinkedIn company pages (use the id as ``target_linkedin_page_id``)."""
        _require(profile, "profile")
        return self._request("GET", "/api/platforms/linkedin/pages", params={"profile": profile})

    def get_pinterest_boards(self, profile: str) -> Dict[str, Any]:
        """Pinterest boards (use the id as ``pinterest_board_id``)."""
        _require(profile, "profile")
        return self._request("GET", "/api/platforms/pinterest/boards", params={"profile": profile})

    def get_google_business_locations(self, profile: str) -> Dict[str, Any]:
        """Google Business Profile locations (use the id as ``gbp_location_id``)."""
        _require(profile, "profile")
        return self._request(
            "GET", "/api/platforms/google-business/locations", params={"profile": profile}
        )

    # ── Analytics ──

    def get_analytics(self, range: str = "30d") -> Dict[str, Any]:
        """Account analytics overview (``range`` such as ``"7d"``, ``"30d"``, ``"90d"``)."""
        return self._request("GET", "/api/analytics/overview", params={"range": range})

    def get_live_analytics(
        self, profile: str, platforms: Optional[Sequence[str]] = None
    ) -> Dict[str, Any]:
        """Live follower / engagement stats pulled from the platforms for a profile."""
        _require(profile, "profile")
        return self._request(
            "GET",
            f"/api/analytics/live/{_seg(profile)}",
            params={"platforms": ",".join(platforms) if platforms else None},
        )

    def get_post_analytics(self, upload_id: str, platform: Optional[str] = None) -> Dict[str, Any]:
        """Per-post analytics for an upload (optionally for one platform)."""
        _require(upload_id, "upload_id")
        return self._request(
            "GET", f"/api/analytics/post/{_seg(upload_id)}", params={"platform": platform}
        )

    def get_total_impressions(
        self,
        profile: str,
        platform: Optional[str] = None,
        period: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Total impressions for a profile."""
        _require(profile, "profile")
        return self._request(
            "GET",
            f"/api/analytics/total-impressions/{_seg(profile)}",
            params={
                "platform": platform,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
            },
        )

    # ── FFmpeg ──

    def get_ffmpeg_usage(self) -> Dict[str, Any]:
        """FFmpeg processing minutes used / remaining this month."""
        return self._request("GET", "/api/ffmpeg/usage")

    def list_ffmpeg_jobs(self) -> Dict[str, Any]:
        """Recent FFmpeg jobs (latest 50): ``{"jobs": [...]}``."""
        return self._request("GET", "/api/ffmpeg/jobs")

    def get_ffmpeg_job(self, job_id: str) -> Dict[str, Any]:
        """A single FFmpeg job."""
        _require(job_id, "job_id")
        return self._request("GET", f"/api/ffmpeg/jobs/{_seg(job_id)}")

    # ── Plans ──

    def get_plans(self) -> Dict[str, Any]:
        """List available subscription plans."""
        return self._request("GET", "/api/plans")


#: Deprecated aliases (the SDK was called IndReelForge / InReelForge before 1.2.0).
IndReelForge = PostUpload
InReelForge = PostUpload
