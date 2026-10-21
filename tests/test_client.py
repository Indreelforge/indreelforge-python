"""Tests for the IndReelForge client with HTTP mocked at the transport adapter.

Requests are fully prepared by ``requests`` (headers, multipart encoding,
query strings) and captured before anything touches the network.
"""

import io
import json
import re
from email.parser import BytesParser
from email.policy import HTTP
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict

import indreelforge
from indreelforge import (
    IndReelForge,
    IndReelForgeError,
    InReelForge,
    InReelForgeError,
    __version__,
)

BASE = "https://api.example.test"


class MockHTTP:
    def __init__(self):
        self.requests = []
        self.responses = []

    def queue(self, status=200, body=None, headers=None, raw=None, exc=None):
        self.responses.append((status, body, headers, raw, exc))

    def send(self, adapter, request, **kwargs):
        # Materialise streamed/multipart bodies so tests can inspect them.
        body = request.body
        if hasattr(body, "read"):
            body = body.read()
        self.requests.append((request, body, kwargs))
        status, payload, headers, raw, exc = (
            self.responses.pop(0) if self.responses else (200, {}, None, None, None)
        )
        if exc is not None:
            raise exc
        resp = requests.Response()
        resp.status_code = status
        resp.url = request.url
        resp.request = request
        resp.headers = CaseInsensitiveDict({"Content-Type": "application/json", **(headers or {})})
        if raw is not None:
            content = raw.encode() if isinstance(raw, str) else raw
        elif payload is None:
            content = b""
        else:
            content = json.dumps(payload).encode()
        resp.raw = io.BytesIO(content)
        resp.encoding = "utf-8"
        return resp

    @property
    def last(self):
        return self.requests[-1]


@pytest.fixture
def http(monkeypatch):
    mock = MockHTTP()
    monkeypatch.setattr(HTTPAdapter, "send", lambda self, request, **kw: mock.send(self, request, **kw))
    return mock


@pytest.fixture
def client():
    return IndReelForge(api_key="test-key", base_url=BASE + "/")


def multipart(request, body):
    """Parse a multipart body into {name: [value-or-(filename, bytes)]}."""
    ctype = request.headers["Content-Type"]
    assert ctype.startswith("multipart/form-data"), ctype
    msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + body)
    out = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        value = (filename, payload) if filename else payload.decode()
        out.setdefault(name, []).append(value)
    return out


def path_and_query(request):
    parts = urlsplit(request.url)
    return parts.path, parse_qs(parts.query)


@pytest.fixture
def files(tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-video")
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"fake-jpg")
    doc = tmp_path / "deck.pdf"
    doc.write_bytes(b"%PDF-1.4")
    return {"video": str(video), "photo": str(photo), "doc": str(doc)}


# ── Package metadata ──


def test_version_single_source():
    assert __version__ == "1.1.0"
    assert indreelforge.__version__ == __version__
    with open("setup.py", encoding="utf-8") as f:
        setup_src = f.read()
    assert "version=version" in setup_src
    assert not re.search(r'version="\d', setup_src)


def test_aliases():
    assert InReelForge is IndReelForge
    assert InReelForgeError is IndReelForgeError
    err = InReelForgeError("boom", 500)  # 1.0 signature still works
    assert err.status_code == 500 and err.status == 500 and err.code is None and str(err) == "boom"


def test_requires_api_key():
    with pytest.raises(ValueError):
        IndReelForge(api_key="")


# ── Headers ──


def test_sends_auth_and_explicit_user_agent(http, client):
    http.queue(body={"id": 1, "plan": "free"})
    assert client.get_me() == {"id": 1, "plan": "free"}
    req, _, kwargs = http.last
    assert req.method == "GET"
    assert req.url == f"{BASE}/api/me"
    assert req.headers["Authorization"] == "Apikey test-key"
    assert req.headers["User-Agent"] == f"indreelforge-python/{__version__}"
    assert "python-urllib" not in req.headers["User-Agent"].lower()
    assert "python-requests" not in req.headers["User-Agent"].lower()
    assert kwargs["timeout"] == 120


def test_custom_headers_cannot_override_auth(http):
    c = IndReelForge(api_key="k", base_url=BASE, headers={"X-Trace": "t", "Authorization": "nope"})
    http.queue(body={})
    c.get_plans()
    req = http.last[0]
    assert req.headers["X-Trace"] == "t"
    assert req.headers["Authorization"] == "Apikey k"


# ── Uploads ──


def test_upload_video_file_with_all_options(http, client, files):
    http.queue(status=201, body={"success": True, "data": {"upload_id": "u1", "platforms": {}}})
    res = client.upload_video(
        "brand",
        ["tiktok", "youtube"],
        files["video"],
        "T",
        "FC",
        "2030-01-01T10:00:00Z",
        "UTC",
        description="D",
        add_to_queue=False,
        async_upload=False,
        request_id="req-1",
        idempotency_key="idem-1",
        platform_options={"privacy_level": "PUBLIC_TO_EVERYONE", "disable_duet": True, "user_tags": {"a": 1}, "skip": None},
        youtube_tags="a,b",
        poll_options=["x", "y"],
    )
    assert res["data"]["upload_id"] == "u1"
    req, body, _ = http.last
    assert req.method == "POST"
    assert req.url == f"{BASE}/api/upload"
    assert req.headers["Idempotency-Key"] == "idem-1"
    form = multipart(req, body)
    assert form["user"] == ["brand"]
    assert form["platform[]"] == ["tiktok", "youtube"]
    assert form["title"] == ["T"]
    assert form["first_comment"] == ["FC"]
    assert form["schedule"] == ["2030-01-01T10:00:00Z"]
    assert form["timezone"] == ["UTC"]
    assert form["description"] == ["D"]
    assert form["add_to_queue"] == ["false"]
    assert form["async_upload"] == ["false"]
    assert form["request_id"] == ["req-1"]
    assert form["privacy_level"] == ["PUBLIC_TO_EVERYONE"]
    assert form["disable_duet"] == ["true"]
    assert form["user_tags"] == ['{"a": 1}']
    assert "skip" not in form
    assert form["youtube_tags"] == ["a,b"]
    assert form["poll_options"] == ["x", "y"]
    assert form["video"] == [("clip.mp4", b"fake-video")]


def test_upload_video_closes_file_handles(http, client, files, monkeypatch):
    opened = []
    real_open = open

    def tracking_open(*a, **kw):
        fh = real_open(*a, **kw)
        opened.append(fh)
        return fh

    monkeypatch.setattr("builtins.open", tracking_open)
    http.queue(status=201, body={"data": {"upload_id": "u"}})
    client.upload_video(user="b", platforms=["tiktok"], video_path=files["video"])
    http.queue(status=500, body={"error": "boom"})
    with pytest.raises(IndReelForgeError):
        client.upload_photos(user="b", platforms=["instagram"], photo_paths=[files["photo"], files["photo"]])
    assert len(opened) == 3
    assert all(fh.closed for fh in opened)


def test_upload_video_url(http, client):
    http.queue(status=201, body={"data": {"upload_id": "u2"}})
    client.upload_video(user="brand", platforms=["instagram"], video_url="https://cdn.example.com/v.mp4")
    req, body, _ = http.last
    assert "Idempotency-Key" not in req.headers
    assert multipart(req, body)["video"] == ["https://cdn.example.com/v.mp4"]


def test_upload_video_validation(http, client):
    with pytest.raises(ValueError):
        client.upload_video(user="b", platforms=["x"])
    with pytest.raises(FileNotFoundError):
        client.upload_video(user="b", platforms=["x"], video_path="/nope/missing.mp4")
    assert http.requests == []


def test_upload_photos_files_and_urls(http, client, files):
    http.queue(status=201, body={"data": {"upload_id": "p1"}})
    client.upload_photos(
        user="brand",
        platforms=["instagram", "google_business"],
        photo_paths=[files["photo"]],
        photo_urls=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
        platform_options={"gbp_location_id": "locations/1"},
    )
    req, body, _ = http.last
    assert req.url == f"{BASE}/api/upload_photos"
    form = multipart(req, body)
    assert form["photos[]"] == [("a.jpg", b"fake-jpg")]
    assert form["photo_urls[]"] == ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"]
    assert form["platform[]"] == ["instagram", "google_business"]
    assert form["gbp_location_id"] == ["locations/1"]


def test_upload_photos_url_only_is_still_multipart(http, client):
    http.queue(status=201, body={"data": {"upload_id": "p2"}})
    client.upload_photos("brand", ["instagram"], photo_urls=["https://cdn.example.com/1.jpg"])
    req, body, _ = http.last
    assert multipart(req, body)["photo_urls[]"] == ["https://cdn.example.com/1.jpg"]


def test_upload_photos_limits(http, client):
    with pytest.raises(ValueError, match="at most 10"):
        client.upload_photos("b", ["instagram"], photo_urls=[f"https://c.test/{i}.jpg" for i in range(11)])
    with pytest.raises(ValueError):
        client.upload_photos("b", ["instagram"])
    assert http.requests == []


def test_upload_text_title_and_text(http, client):
    http.queue(status=201, body={"data": {"upload_id": "t1"}})
    http.queue(status=201, body={"data": {"upload_id": "t2"}})
    client.upload_text("brand", ["x", "threads"], "hello")  # 1.0 positional style
    client.upload_text(user="brand", platforms=["bluesky"], text="world", async_upload=False)
    first = multipart(*http.requests[0][:2])
    second = multipart(*http.requests[1][:2])
    assert http.requests[0][0].url == f"{BASE}/api/upload_text"
    assert first["text"] == ["hello"] and "title" not in first
    assert second["text"] == ["world"] and second["async_upload"] == ["false"]
    with pytest.raises(ValueError):
        client.upload_text(user="b", platforms=["x"])


def test_upload_document(http, client, files):
    http.queue(status=201, body={"data": {"upload_id": "d1"}})
    client.upload_document(
        "brand", files["doc"], "Deck", visibility="PUBLIC", target_linkedin_page_id="42", idempotency_key="k"
    )
    req, body, _ = http.last
    assert req.url == f"{BASE}/api/upload_document"
    assert req.headers["Idempotency-Key"] == "k"
    form = multipart(req, body)
    assert form["document"] == [("deck.pdf", b"%PDF-1.4")]
    assert form["title"] == ["Deck"]
    assert form["visibility"] == ["PUBLIC"]
    assert form["target_linkedin_page_id"] == ["42"]


# ── Read / manage endpoints ──


@pytest.mark.parametrize(
    "call,method,path,query",
    [
        (lambda c: c.get_upload_status("u 1"), "GET", "/api/upload/u%201/status", {}),
        (lambda c: c.get_request_status("r1"), "GET", "/api/uploadposts/status", {"request_id": ["r1"]}),
        (lambda c: c.list_uploads(limit=5, page=2, status="failed"), "GET", "/api/uploads", {"limit": ["5"], "page": ["2"], "status": ["failed"]}),
        (lambda c: c.list_uploads(), "GET", "/api/uploads", {}),
        (lambda c: c.get_upload("u1"), "GET", "/api/uploads/u1", {}),
        (lambda c: c.delete_upload("u1"), "DELETE", "/api/uploads/u1", {}),
        (lambda c: c.retry_upload("u1"), "POST", "/api/uploads/u1/retry", {}),
        (lambda c: c.get_platform_limits(), "GET", "/api/platform-limits", {}),
        (lambda c: c.get_profiles(), "GET", "/api/profiles", {}),
        (lambda c: c.delete_profile("my brand"), "DELETE", "/api/profiles/my%20brand", {}),
        (lambda c: c.get_scheduled_posts(), "GET", "/api/schedule", {}),
        (lambda c: c.cancel_scheduled_post("j1"), "DELETE", "/api/schedule/j1", {}),
        (lambda c: c.get_queue_settings("brand"), "GET", "/api/queue/settings", {"profile": ["brand"]}),
        (lambda c: c.get_queue_preview("brand", 5), "GET", "/api/queue/preview", {"profile": ["brand"], "count": ["5"]}),
        (lambda c: c.get_next_queue_slot("brand"), "GET", "/api/queue/next-slot", {"profile": ["brand"]}),
        (lambda c: c.get_facebook_pages("brand"), "GET", "/api/platforms/facebook/pages", {"profile": ["brand"]}),
        (lambda c: c.get_linkedin_pages("brand"), "GET", "/api/platforms/linkedin/pages", {"profile": ["brand"]}),
        (lambda c: c.get_pinterest_boards("brand"), "GET", "/api/platforms/pinterest/boards", {"profile": ["brand"]}),
        (lambda c: c.get_google_business_locations("brand"), "GET", "/api/platforms/google-business/locations", {"profile": ["brand"]}),
        (lambda c: c.get_analytics(), "GET", "/api/analytics/overview", {"range": ["30d"]}),
        (lambda c: c.get_analytics("7d"), "GET", "/api/analytics/overview", {"range": ["7d"]}),
        (lambda c: c.get_live_analytics("brand", ["instagram", "youtube"]), "GET", "/api/analytics/live/brand", {"platforms": ["instagram,youtube"]}),
        (lambda c: c.get_post_analytics("u1", "tiktok"), "GET", "/api/analytics/post/u1", {"platform": ["tiktok"]}),
        (lambda c: c.get_total_impressions("brand", period="30d"), "GET", "/api/analytics/total-impressions/brand", {"period": ["30d"]}),
        (lambda c: c.get_ffmpeg_usage(), "GET", "/api/ffmpeg/usage", {}),
        (lambda c: c.list_ffmpeg_jobs(), "GET", "/api/ffmpeg/jobs", {}),
        (lambda c: c.get_ffmpeg_job("j1"), "GET", "/api/ffmpeg/jobs/j1", {}),
        (lambda c: c.get_plans(), "GET", "/api/plans", {}),
    ],
)
def test_endpoints(http, client, call, method, path, query):
    http.queue(body={"ok": True})
    assert call(client) == {"ok": True}
    req = http.last[0]
    assert req.method == method
    assert path_and_query(req) == (path, query)


def test_json_body_endpoints(http, client):
    http.queue(status=201, body={"profile": {"id": 1}})
    client.create_profile("n")
    req, body, _ = http.last
    assert req.headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"name": "n"}

    http.queue(body={})
    client.update_scheduled_post("j1", scheduled_date="2030-01-01T00:00:00Z", timezone="UTC", caption="c")
    req, body, _ = http.last
    assert req.method == "PATCH" and req.url == f"{BASE}/api/schedule/j1"
    assert json.loads(body) == {"scheduled_date": "2030-01-01T00:00:00Z", "timezone": "UTC", "caption": "c"}

    http.queue(body={})
    client.update_queue_settings("brand", slots=["09:00"], days_of_week=[1, 2], timezone="UTC", max_posts_per_slot=2)
    req, body, _ = http.last
    assert req.method == "POST" and req.url == f"{BASE}/api/queue/settings"
    assert json.loads(body) == {
        "profile_username": "brand", "slots": ["09:00"], "days_of_week": [1, 2], "timezone": "UTC", "max_posts_per_slot": 2,
    }


# ── Errors ──


def test_api_error_fields(http, client):
    http.queue(status=400, body={"error": "Missing video", "error_code": "MISSING_VIDEO"})
    with pytest.raises(IndReelForgeError) as ei:
        client.get_me()
    e = ei.value
    assert isinstance(e, InReelForgeError)
    assert str(e) == "Missing video"
    assert e.status_code == 400 and e.code == "MISSING_VIDEO"
    assert e.body == {"error": "Missing video", "error_code": "MISSING_VIDEO"}


def test_api_error_code_field_and_html(http, client):
    http.queue(status=401, body={"error": "Invalid session", "code": "INVALID_SESSION"})
    with pytest.raises(IndReelForgeError) as ei:
        client.get_me()
    assert ei.value.status_code == 401 and ei.value.code == "INVALID_SESSION"

    http.queue(status=502, raw="<html>Bad gateway</html>", headers={"Content-Type": "text/html"})
    with pytest.raises(IndReelForgeError) as ei:
        client.get_me()
    assert ei.value.status_code == 502 and ei.value.code is None
    assert ei.value.body == "<html>Bad gateway</html>"


def test_network_and_timeout_errors(http, client):
    http.queue(exc=requests.exceptions.ConnectionError("dns failure"))
    with pytest.raises(IndReelForgeError) as ei:
        client.get_me()
    assert ei.value.status_code == 0 and ei.value.code == "NETWORK_ERROR"

    http.queue(exc=requests.exceptions.ReadTimeout("slow"))
    with pytest.raises(IndReelForgeError) as ei:
        client.get_me()
    assert ei.value.status_code == 408 and ei.value.code == "TIMEOUT"


def test_empty_success_body(http, client):
    http.queue(status=204, body=None)
    assert client.delete_upload("u1") == {}


def test_context_manager_closes_session(monkeypatch):
    closed = []
    with IndReelForge(api_key="k") as c:
        monkeypatch.setattr(c._session, "close", lambda: closed.append(True))
    assert closed == [True]


# ── Credential platforms ──


def test_platform_constants():
    from indreelforge import CREDENTIAL_PLATFORMS, PLATFORMS

    for p in ("discord", "telegram", "slack", "mastodon", "wordpress", "google_business"):
        assert p in PLATFORMS
    assert CREDENTIAL_PLATFORMS == {
        "discord": ("webhook_url",),
        "telegram": ("bot_token", "chat_id"),
        "slack": ("webhook_url",),
        "mastodon": ("instance_url", "access_token"),
        "wordpress": ("site_url", "username", "app_password"),
    }


def test_connect_with_credentials_kwargs(http, client):
    http.queue(body={"success": True, "message": "Connected to Telegram as @ch", "platform_username": "@ch"})
    res = client.connect_with_credentials("my brand", "telegram", bot_token="123:AA", chat_id="@ch")
    assert res["platform_username"] == "@ch"
    req, body, _ = http.last
    assert req.method == "POST"
    assert req.url == f"{BASE}/api/profiles/my%20brand/connect/telegram/credentials"
    assert req.headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"bot_token": "123:AA", "chat_id": "@ch"}


def test_connect_with_credentials_dict_and_override(http, client):
    http.queue(body={"success": True, "platform_username": "admin"})
    client.connect_with_credentials(
        "brand",
        "wordpress",
        {"site_url": "https://example.com", "username": "admin", "app_password": "old"},
        app_password="abcd efgh",
    )
    assert json.loads(http.last[1]) == {
        "site_url": "https://example.com", "username": "admin", "app_password": "abcd efgh",
    }


@pytest.mark.parametrize(
    "status,payload,code,field",
    [
        (400, {"error": "Channel or chat is required.", "code": "MISSING_FIELD", "field": "chat_id", "details": {"field": "chat_id"}}, "MISSING_FIELD", "chat_id"),
        (400, {"error": "Bot token is too long.", "code": "FIELD_TOO_LONG", "field": "bot_token", "details": {"field": "bot_token"}}, "FIELD_TOO_LONG", "bot_token"),
        (400, {"error": "Telegram rejected the bot token.", "code": "INVALID_CREDENTIALS", "error_code": "INVALID_CREDENTIALS", "details": {"field": "bot_token"}}, "INVALID_CREDENTIALS", "bot_token"),
        (404, {"error": "Profile not found"}, None, None),
        (502, {"error": "Could not reach Telegram to check these details. Please try again."}, None, None),
    ],
)
def test_connect_with_credentials_errors(http, client, status, payload, code, field):
    http.queue(status=status, body=payload)
    with pytest.raises(IndReelForgeError) as ei:
        client.connect_with_credentials("brand", "telegram", bot_token="b", chat_id="@c")
    e = ei.value
    assert e.status_code == status
    assert e.code == code
    assert str(e) == payload["error"]
    assert (e.body.get("details") or {}).get("field") == field


def test_connect_with_credentials_validation(http, client):
    with pytest.raises(ValueError):
        client.connect_with_credentials("", "slack", webhook_url="u")
    with pytest.raises(ValueError):
        client.connect_with_credentials("brand", "")
    with pytest.raises(ValueError):
        client.connect_with_credentials("brand", "slack")
    assert http.requests == []
