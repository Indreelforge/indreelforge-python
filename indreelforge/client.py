"""IndReelForge API client."""

import json as _json
import os
from contextlib import ExitStack
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union
from urllib.parse import quote

import requests

from ._version import __version__

DEFAULT_BASE_URL = "https://api.indreelforge.com"
USER_AGENT = f"indreelforge-python/{__version__}"
MAX_PHOTOS = 10

Field = Tuple[str, Tuple[Optional[str], Any]]


class IndReelForgeError(Exception):
    """Error from the IndReelForge API (or a network failure / timeout).

    Attributes:
        status_code: HTTP status code (0 = network error, 408 = client-side timeout).
        code: Machine-readable error code from the API (``error_code`` / ``code``),
            or ``NETWORK_ERROR`` / ``TIMEOUT``; ``None`` when not provided.
        body: Parsed JSON response body (dict) or raw text, if any.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 0,
        code: Optional[str] = None,
        body: Any = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code
        self.body = body

    @property
    def status(self) -> int:
        """Alias of :attr:`status_code`."""
        return self.status_code

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(message={self.message!r}, status_code={self.status_code!r}, "
            f"code={self.code!r})"
        )


#: Deprecated alias kept for 1.0.x code (``except InReelForgeError`` keeps working).
InReelForgeError = IndReelForgeError


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


def _seg(value: Any) -> str:
    return quote(str(value), safe="")


def _require(value: Any, name: str) -> None:
    if value is None or value == "":
        raise ValueError(f"{name} is required")


class IndReelForge:
    """
    Official Python client for the IndReelForge Social Media API.

    Publish videos, photos, text and documents to Instagram, YouTube, TikTok,
    Facebook, X (Twitter), LinkedIn, Threads, Pinterest, Reddit, Bluesky and
    Google Business Profile.

    Args:
        api_key: Your IndReelForge API key (required).
        base_url: API base URL (default: https://api.indreelforge.com).
        timeout: Request timeout in seconds (default: 120).
        session: Optional ``requests.Session`` to use.
        headers: Optional extra headers sent with every request.

    The client can be used as a context manager to close its HTTP session::

        with IndReelForge(api_key="...") as client:
            client.get_me()

    Example:
        >>> client = IndReelForge(api_key="your-api-key")
        >>> result = client.upload_video(
        ...     user="my-profile",
        ...     platforms=["instagram", "tiktok"],
        ...     video_path="./video.mp4",
        ...     title="My video",
        ... )
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: Union[int, float] = 120,
        session: Optional[requests.Session] = None,
        headers: Optional[Mapping[str, str]] = None,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
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

    def __enter__(self) -> "IndReelForge":
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
            raise IndReelForgeError("Request timed out", 408, "TIMEOUT") from None
        except requests.exceptions.ConnectionError as e:
            raise IndReelForgeError(f"Connection failed: {e}", 0, "NETWORK_ERROR") from None

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
            raise IndReelForgeError(
                str(message),
                resp.status_code,
                str(code) if code is not None else None,
                body if text.strip() else None,
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
        video_path: Optional[str] = None,
        title: Optional[str] = None,
        first_comment: Optional[str] = None,
        schedule: Optional[str] = None,
        timezone: Optional[str] = None,
        *,
        video_url: Optional[str] = None,
        description: Optional[str] = None,
        add_to_queue: Optional[bool] = None,
        async_upload: Optional[bool] = None,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        platform_options: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Upload a video to one or more platforms.

        Args:
            user: Profile name (managed user).
            platforms: Platforms, e.g. ``["instagram", "tiktok", "youtube"]``.
            video_path: Path to a local video file (or use ``video_url``).
            title: Post title / caption.
            first_comment: First comment text.
            schedule: ISO-8601 datetime to publish later.
            timezone: IANA timezone used with ``schedule``.
            video_url: Public http(s) URL of the video; the server downloads it.
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
        """
        if not video_path and not video_url:
            raise ValueError("upload_video requires video_path or video_url")
        if video_path and not os.path.isfile(video_path):
            raise FileNotFoundError(f"Video file not found: {video_path}")

        fields = self._build_form_fields(
            user, platforms, title, first_comment, schedule, timezone, description,
            add_to_queue, async_upload, request_id, platform_options, kwargs,
        )
        with ExitStack() as stack:
            if video_path:
                fh = stack.enter_context(open(video_path, "rb"))
                fields.append(("video", (os.path.basename(video_path), fh)))
            else:
                fields.append(("video", (None, str(video_url))))
            return self._request(
                "POST", "/api/upload", files=fields, headers=self._idem(idempotency_key)
            )

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


#: Deprecated alias kept for 1.0.x code.
InReelForge = IndReelForge
