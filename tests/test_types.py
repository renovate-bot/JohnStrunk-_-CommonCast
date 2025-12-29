"""Unit tests for commoncast.types module."""

from pathlib import Path

from commoncast.types import (
    Capability,
    Device,
    DeviceID,
    MediaImage,
    MediaMetadata,
    MediaPayload,
)


def test_media_metadata() -> None:
    """Test MediaMetadata creation."""
    img = MediaImage(url="http://example.com/art.jpg", width=100, height=100)
    meta = MediaMetadata(
        title="Test Title",
        subtitle="Subtitle",
        images=[img],
        type="video",
        extra={"custom": "data"},
    )
    assert meta.title == "Test Title"
    assert meta.images[0].url == "http://example.com/art.jpg"
    assert meta.extra["custom"] == "data"


def test_media_payload_from_bytes() -> None:
    """Test creating MediaPayload from raw bytes."""
    data = b"hello world"
    payload = MediaPayload.from_bytes(data, mime_type="text/plain")
    assert payload.data == data
    assert payload.mime_type == "text/plain"
    assert payload.size == len(data)
    assert payload.path is None
    assert payload.url is None
    assert payload.metadata is None


def test_media_payload_from_path(tmp_path: Path) -> None:
    """Test creating MediaPayload from a file path."""
    f = tmp_path / "test.txt"
    f.write_text("hello file")
    payload = MediaPayload.from_path(f, mime_type="text/plain")
    assert payload.path == f
    assert payload.mime_type == "text/plain"
    assert payload.size == 10
    assert payload.data is None
    assert payload.url is None


def test_media_payload_from_url() -> None:
    """Test creating MediaPayload from a URL."""
    url = "http://example.com/movie.mp4"
    meta = MediaMetadata(title="Movie")
    payload = MediaPayload.from_url(url, mime_type="video/mp4", metadata=meta)
    assert payload.url == url
    assert payload.mime_type == "video/mp4"
    assert payload.size is None
    assert payload.data is None
    assert payload.path is None
    assert payload.metadata is not None
    assert payload.metadata.title == "Movie"


def test_device_creation() -> None:
    """Test Device initialization."""
    dev = Device(
        id=DeviceID("dev1"),
        name="Living Room",
        model="Chromecast",
        transport="chromecast",
        capabilities={Capability("video"), Capability("audio")},
        transport_info={"ip": "192.168.1.10"},
    )
    assert dev.id == "dev1"
    assert dev.name == "Living Room"
    assert "video" in dev.capabilities
