"""Chunked upload, skipped_platforms and the deprecated ``indreelforge`` import name."""

import importlib
import io
import json
import sys
import warnings

import pytest
import requests

import post_upload
import post_upload.client as client_mod
from post_upload import PostUpload, PostUploadError, get_skipped_platforms
from test_client import BASE, MockHTTP, multipart  # noqa: F401  (MockHTTP used by the fixture)
from requests.adapters import HTTPAdapter


@pytest.fixture
def http(monkeypatch):
    mock = MockHTTP()
    monkeypatch.setattr(HTTPAdapter, "send", lambda self, request, **kw: mock.send(self, request, **kw))
    return mock


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr(PostUpload, "_sleep", staticmethod(lambda s: calls.append(s)))
    return calls


@pytest.fixture
def client():
    return PostUpload(api_key="test-key", base_url=BASE, chunked_threshold=10)


@pytest.fixture
def video25(tmp_path):
    path = tmp_path / "episode.mp4"
    path.write_bytes(bytes(range(25)))
    return path


SKIPPED = [{
    "platform": "bluesky",
    "error_code": "FILE_TOO_LARGE_FOR_PLATFORM",
    "message": "Not posted to Bluesky: the file is 150 MB and Bluesky accepts at most 100 MB.",
    "file_size_mb": 150,
    "max_size_mb": 100,
}]


def queue_session(http, total=3, chunk=10, sid="ses_1"):
    http.queue(status=201, body={
        "success": True, "upload_session_id": sid, "chunk_size": chunk,
        "total_chunks": total, "expires_at": "2026-11-22T18:00:00.000Z",
    })


def calls(http):
    return [(r.method, r.url.replace(BASE, "")) for r, _, _ in http.requests]


# ── Automatic chunked upload ──


def test_large_file_goes_through_chunked_upload_with_retries(http, client, video25, sleeps):
    queue_session(http)
    http.queue(body={"success": True, "index": 0})
    http.queue(status=503, body={"error": "busy"}, headers={"Retry-After": "0"})
    http.queue(body={"success": True, "index": 1})
    http.queue(status=429, body={"error": "slow down", "error_code": "UPLOAD_CHUNK_RATE_LIMIT"})
    http.queue(body={"success": True, "index": 2})
    http.queue(body={"success": True, "staged_file_id": "ses_1", "status": "complete"})
    http.queue(status=201, body={
        "success": True,
        "data": {"upload_id": "u1", "platforms": {"youtube": "queued", "bluesky": "failed"},
                 "skipped_platforms": SKIPPED},
    })
    progress = []

    res = client.upload_video(
        "brand", ["youtube", "bluesky"], str(video25), title="Ep 1",
        idempotency_key="idem-1", on_progress=lambda a, b: progress.append((a, b)),
    )

    assert calls(http) == [
        ("POST", "/api/upload/chunked/init"),
        ("PUT", "/api/upload/chunked/ses_1/0"),
        ("PUT", "/api/upload/chunked/ses_1/1"),
        ("PUT", "/api/upload/chunked/ses_1/1"),
        ("PUT", "/api/upload/chunked/ses_1/2"),
        ("PUT", "/api/upload/chunked/ses_1/2"),
        ("POST", "/api/upload/chunked/ses_1/complete"),
        ("POST", "/api/upload"),
    ]
    init_req, init_body, _ = http.requests[0]
    assert json.loads(init_body) == {"filename": "episode.mp4", "size": 25, "mime_type": "video/mp4"}
    data = bytes(range(25))
    parts = [(r, b) for r, b, _ in http.requests if r.method == "PUT"]
    assert [b for _, b in parts] == [data[0:10], data[10:20], data[10:20], data[20:25], data[20:25]]
    for r, _ in parts:
        assert r.headers["Content-Type"] == "application/octet-stream"
        assert r.headers["Authorization"] == "Apikey test-key"
        assert "Idempotency-Key" not in r.headers
    assert sleeps == [0.0, 1.0]  # Retry-After honoured, then exponential backoff
    assert progress == [(10, 25), (20, 25), (25, 25)]

    final_req, final_body, _ = http.last
    form = multipart(final_req, final_body)
    assert form["staged_file_id"] == ["ses_1"]
    assert "video" not in form
    assert form["title"] == ["Ep 1"] and form["platform[]"] == ["youtube", "bluesky"]
    assert final_req.headers["Idempotency-Key"] == "idem-1"
    assert get_skipped_platforms(res) == SKIPPED


def test_module_threshold_and_bytes_source(http, monkeypatch, sleeps):
    monkeypatch.setattr(client_mod, "CHUNKED_THRESHOLD", 4)
    c = PostUpload(api_key="k", base_url=BASE)
    queue_session(http, total=2, chunk=5)
    http.queue(body={})
    http.queue(body={})
    http.queue(body={"staged_file_id": "stg"})
    http.queue(status=201, body={"data": {"upload_id": "u"}})
    c.upload_video("brand", ["youtube"], video_file=b"0123456789", filename="clip.mov")
    assert json.loads(http.requests[0][1]) == {"filename": "clip.mov", "size": 10, "mime_type": "video/quicktime"}
    assert [b for r, b, _ in http.requests if r.method == "PUT"] == [b"01234", b"56789"]
    assert sleeps == []


def test_small_file_is_one_multipart_request(http, client, tmp_path):
    small = tmp_path / "s.mp4"
    small.write_bytes(b"0123456789")  # == threshold, not above it
    http.queue(status=201, body={"data": {"upload_id": "u"}})
    res = client.upload_video("brand", ["tiktok"], str(small))
    assert calls(http) == [("POST", "/api/upload")]
    assert multipart(*http.last[:2])["video"] == [("s.mp4", b"0123456789")]
    assert get_skipped_platforms(res) == []


def test_chunked_flag_forces_or_disables(http, client, tmp_path, video25):
    small = tmp_path / "s.mp4"
    small.write_bytes(b"abc")
    queue_session(http, total=1, chunk=32 * 1024 * 1024)
    http.queue(body={})
    http.queue(body={"staged_file_id": "stg"})
    http.queue(status=201, body={"data": {"upload_id": "u"}})
    client.upload_video("brand", ["youtube"], str(small), chunked=True)
    assert [m for m, _ in calls(http)] == ["POST", "PUT", "POST", "POST"]
    assert http.requests[1][1] == b"abc"

    http.requests.clear()
    http.queue(status=201, body={"data": {"upload_id": "u2"}})
    client.upload_video("brand", ["youtube"], str(video25), chunked=False)
    assert calls(http) == [("POST", "/api/upload")]
    assert multipart(*http.last[:2])["video"] == [("episode.mp4", bytes(range(25)))]


def test_non_seekable_stream_is_spooled(http, client, sleeps):
    class Pipe:
        name = "/tmp/stream.webm"

        def __init__(self, data):
            self._buf = io.BytesIO(data)

        def read(self, n=-1):
            return self._buf.read(n)

        def seekable(self):
            return False

    queue_session(http, total=2, chunk=10)
    http.queue(body={})
    http.queue(body={})
    http.queue(body={"staged_file_id": "stg"})
    staged = client.upload_large_file(Pipe(b"x" * 15))
    assert staged == "stg"
    assert json.loads(http.requests[0][1])["filename"] == "stream.webm"
    assert json.loads(http.requests[0][1])["mime_type"] == "video/webm"
    assert [len(b) for r, b, _ in http.requests if r.method == "PUT"] == [10, 5]


# ── Failures ──


def test_non_retryable_part_error_aborts_session(http, client, video25, sleeps):
    queue_session(http)
    http.queue(status=400, body={"error": "bad size", "error_code": "CHUNK_SIZE_MISMATCH", "expected_bytes": 10})
    http.queue(body={"success": True})  # DELETE
    with pytest.raises(PostUploadError) as ei:
        client.upload_video("brand", ["youtube"], str(video25))
    assert ei.value.status_code == 400 and ei.value.code == "CHUNK_SIZE_MISMATCH"
    assert calls(http) == [
        ("POST", "/api/upload/chunked/init"),
        ("PUT", "/api/upload/chunked/ses_1/0"),
        ("DELETE", "/api/upload/chunked/ses_1"),
    ]
    assert sleeps == []


def test_retries_exhausted_then_abort_and_abort_errors_are_swallowed(http, video25, sleeps):
    c = PostUpload(api_key="k", base_url=BASE, chunked_threshold=10, max_retries=2)
    queue_session(http)
    for _ in range(3):
        http.queue(status=502, body={"error": "bad gateway"})
    http.queue(status=500, body={"error": "abort failed too"})
    with pytest.raises(PostUploadError) as ei:
        c.upload_large_file(str(video25))
    assert ei.value.status_code == 502
    assert [m for m, _ in calls(http)] == ["POST", "PUT", "PUT", "PUT", "DELETE"]
    assert sleeps == [1.0, 2.0]


def test_network_error_is_retried(http, client, video25, sleeps):
    queue_session(http, total=3)
    http.queue(exc=requests.exceptions.ConnectionError("reset"))
    for _ in range(3):
        http.queue(body={})
    http.queue(body={"staged_file_id": "stg"})
    assert client.upload_large_file(video25) == "stg"  # PathLike works too
    assert [m for m, _ in calls(http)] == ["POST", "PUT", "PUT", "PUT", "PUT", "POST"]
    assert sleeps == [1.0]


def test_complete_failure_aborts(http, client, video25, sleeps):
    queue_session(http, total=3)
    for _ in range(3):
        http.queue(body={})
    http.queue(status=400, body={"error": "not a video", "error_code": "INVALID_FILE_CONTENT"})
    http.queue(body={})
    with pytest.raises(PostUploadError) as ei:
        client.upload_large_file(str(video25))
    assert ei.value.code == "INVALID_FILE_CONTENT"
    assert calls(http)[-1] == ("DELETE", "/api/upload/chunked/ses_1")


def test_client_side_limits(http, client, monkeypatch, video25):
    monkeypatch.setattr(client_mod, "MAX_VIDEO_BYTES", 20)
    with pytest.raises(ValueError, match="at most"):
        client.upload_large_file(str(video25))
    with pytest.raises(ValueError, match="empty"):
        client.upload_large_file(b"")
    with pytest.raises(TypeError):
        client.upload_large_file(12345)
    assert http.requests == []


# ── Low-level methods ──


def test_low_level_session_methods(http, client):
    http.queue(status=201, body={"upload_session_id": "s9", "chunk_size": 33554432, "total_chunks": 2})
    s = client.create_upload_session("film.mkv", 40_000_000)
    assert s["upload_session_id"] == "s9"
    req, body, _ = http.last
    assert (req.method, req.url) == ("POST", f"{BASE}/api/upload/chunked/init")
    assert json.loads(body) == {"filename": "film.mkv", "size": 40_000_000, "mime_type": "video/x-matroska"}

    client.create_upload_session("a.mp4", 5, mime_type="video/mp4")
    assert json.loads(http.last[1])["mime_type"] == "video/mp4"

    http.queue(body={"status": "uploading", "received_chunks": [0]})
    assert client.get_upload_session("s 9")["received_chunks"] == [0]
    assert (http.last[0].method, http.last[0].url) == ("GET", f"{BASE}/api/upload/chunked/s%209")

    http.queue(body={"success": True, "index": 1})
    client.upload_part("s9", 1, bytearray(b"zz"))
    assert (http.last[0].method, http.last[0].url, http.last[1]) == ("PUT", f"{BASE}/api/upload/chunked/s9/1", b"zz")

    http.queue(body={"staged_file_id": "s9"})
    assert client.complete_upload_session("s9")["staged_file_id"] == "s9"
    assert (http.last[0].method, http.last[0].url) == ("POST", f"{BASE}/api/upload/chunked/s9/complete")

    http.queue(body={"success": True})
    client.abort_upload_session("s9")
    assert (http.last[0].method, http.last[0].url) == ("DELETE", f"{BASE}/api/upload/chunked/s9")


def test_upload_part_retries_override(http, client, sleeps):
    http.queue(status=503, body={})
    with pytest.raises(PostUploadError):
        client.upload_part("s9", 0, b"a", retries=0)
    assert len(http.requests) == 1 and sleeps == []


def test_low_level_validation(http, client):
    with pytest.raises(ValueError):
        client.create_upload_session("a.mp4", 0)
    with pytest.raises(ValueError):
        client.create_upload_session("", 10)
    with pytest.raises(ValueError):
        client.upload_part("s", -1, b"x")
    with pytest.raises(ValueError):
        client.complete_upload_session("")
    assert http.requests == []


def test_upload_video_with_staged_file_id_and_source_validation(http, client, video25):
    http.queue(status=201, body={"data": {"upload_id": "u"}})
    client.upload_video("brand", ["youtube"], staged_file_id="stg_1", title="T")
    form = multipart(*http.last[:2])
    assert form["staged_file_id"] == ["stg_1"] and "video" not in form

    with pytest.raises(ValueError, match="only one"):
        client.upload_video("brand", ["youtube"], str(video25), video_url="https://x.test/v.mp4")
    with pytest.raises(ValueError, match="only one"):
        client.upload_video("brand", ["youtube"], video_file=b"a", staged_file_id="s")
    assert len(http.requests) == 1


def test_get_skipped_platforms_shapes():
    assert get_skipped_platforms(None) == []
    assert get_skipped_platforms({"data": {}}) == []
    assert get_skipped_platforms({"data": {"skipped_platforms": SKIPPED}}) == SKIPPED
    assert get_skipped_platforms({"skipped_platforms": SKIPPED}) == SKIPPED


def test_error_carries_headers(http, client):
    http.queue(status=429, body={"error": "x"}, headers={"Retry-After": "7"})
    with pytest.raises(PostUploadError) as ei:
        client.get_me()
    assert ei.value.headers["Retry-After"] == "7"
    assert PostUpload._retry_delay(ei.value, 3) == 7.0
    ei.value.headers = {"retry-after": "999"}
    assert PostUpload._retry_delay(ei.value, 0) == 60.0
    ei.value.headers = None
    assert PostUpload._retry_delay(ei.value, 10) == 30.0


# ── Deprecated import name ──


def test_indreelforge_shim_warns_and_reexports():
    for name in list(sys.modules):
        if name == "indreelforge" or name.startswith("indreelforge."):
            del sys.modules[name]
    with pytest.warns(DeprecationWarning, match="post_upload"):
        legacy = importlib.import_module("indreelforge")
    assert legacy.IndReelForge is post_upload.PostUpload
    assert legacy.InReelForge is post_upload.PostUpload
    assert legacy.IndReelForgeError is post_upload.PostUploadError
    assert legacy.PostUpload is post_upload.PostUpload
    assert legacy.__version__ == post_upload.__version__
    assert legacy.PLATFORMS == post_upload.PLATFORMS

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # already imported: no second warning
        from indreelforge.client import IndReelForge, IndReelForgeError  # noqa: F401
        import indreelforge.client as legacy_client
    assert legacy_client is client_mod
    assert IndReelForge is PostUpload

    try:
        raise post_upload.PostUploadError("boom", 500)
    except legacy.IndReelForgeError as e:  # old except clauses still catch
        assert e.status_code == 500
