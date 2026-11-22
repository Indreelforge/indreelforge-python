"""Typed shapes of selected API responses (plain dicts at runtime)."""

from typing import Any, Dict, List, TypedDict


class SkippedPlatform(TypedDict, total=False):
    """A platform the file was too large for (``data.skipped_platforms[]``)."""

    platform: str
    error_code: str  # "FILE_TOO_LARGE_FOR_PLATFORM"
    message: str
    file_size_mb: float
    max_size_mb: float


class UploadData(TypedDict, total=False):
    upload_id: str
    request_id: str
    platforms: Dict[str, str]
    scheduled_date: str
    estimated_completion: str
    skipped_platforms: List[SkippedPlatform]
    error_message: str


class UploadResponse(TypedDict, total=False):
    """Response of ``upload_video`` (and the other upload calls)."""

    success: bool
    message: str
    sync: bool
    data: UploadData
    warnings: List[Dict[str, Any]]


class UploadSession(TypedDict, total=False):
    """Response of ``create_upload_session`` (``POST /api/upload/chunked/init``)."""

    success: bool
    upload_session_id: str
    filename: str
    size: int
    mime_type: str
    chunk_size: int
    total_chunks: int
    expires_at: str


class UploadSessionStatus(UploadSession, total=False):
    """Response of ``get_upload_session`` (``GET /api/upload/chunked/{id}``)."""

    status: str  # "uploading" | "complete"
    received_chunks: List[int]
    chunks_received: int
    staged_file_id: str


class CompletedUploadSession(UploadSession, total=False):
    """Response of ``complete_upload_session`` (completing again returns the same file)."""

    status: str  # "complete"
    staged_file_id: str
    message: str
