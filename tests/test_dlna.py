"""Tests for the DLNA backend."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import commoncast.dlna.adapter as _dlna_adapter
import commoncast.registry as _registry
import commoncast.types as _types


@pytest.fixture
def registry() -> _registry.Registry:
    """Create a registry instance for testing.

    :returns: A Registry instance.
    """
    return _registry.Registry()


@pytest.fixture
def mock_dmr() -> MagicMock:
    """Create a mock DmrDevice object.

    :returns: A MagicMock simulating a DmrDevice.
    """
    dmr = MagicMock()
    dmr.device.udn = "uuid:test-udn"
    dmr.device.friendly_name = "Test DLNA Device"
    dmr.device.model_name = "Test Model"

    # Mock high-level methods
    dmr.async_play = AsyncMock()
    dmr.async_pause = AsyncMock()
    dmr.async_stop = AsyncMock()
    dmr.async_seek_rel_time = AsyncMock()
    dmr.async_set_volume_level = AsyncMock()
    dmr.async_mute_volume = AsyncMock()
    dmr.async_set_transport_uri = AsyncMock()
    dmr.construct_play_media_metadata = AsyncMock(return_value="<xml>metadata</xml>")

    return dmr


@pytest.mark.asyncio
async def test_adapter_discovery(
    registry: _registry.Registry, mock_dmr: MagicMock
) -> None:
    """Test DLNA adapter discovery process.

    :param registry: The Registry fixture.
    :param mock_dmr: The mock_dmr fixture.
    :returns: None
    """
    adapter = _dlna_adapter.DlnaAdapter(registry)

    with (
        patch("commoncast.dlna.adapter.SsdpListener") as mock_ssdp_class,
        patch("commoncast.dlna.adapter.UpnpFactory") as mock_factory_class,
        patch("commoncast.dlna.adapter.AiohttpSessionRequester"),
        patch("commoncast.dlna.adapter.DmrDevice") as mock_dmr_class,
        patch("aiohttp.ClientSession") as mock_session_class,
    ):
        mock_ssdp = mock_ssdp_class.return_value
        mock_ssdp.async_start = AsyncMock()
        mock_ssdp.async_search = AsyncMock()
        mock_ssdp.async_stop = AsyncMock()

        mock_session = mock_session_class.return_value
        mock_session.close = AsyncMock()

        mock_factory = mock_factory_class.return_value
        mock_factory.async_create_device = AsyncMock()

        # Setup device creation mock
        mock_device = MagicMock()
        mock_device.udn = "uuid:test-udn"
        mock_device.friendly_name = "Test DLNA Device"
        mock_device.model_name = "Test Model"
        mock_factory.async_create_device.return_value = mock_device

        mock_dmr_class.return_value = mock_dmr
        mock_dmr.device = mock_device

        await adapter.start()

        # Simulate device found
        mock_ssdp_device = MagicMock()
        mock_ssdp_device.location = "http://192.168.1.10:8080/desc.xml"
        mock_ssdp_device.udn = "uuid:test-udn"

        await adapter._on_device_found(  # type: ignore[reportPrivateUsage]
            mock_ssdp_device, "urn:schemas-upnp-org:device:MediaRenderer:1", MagicMock()
        )

        devices = registry.list_devices()
        assert len(devices) == 1
        assert devices[0].name == "Test DLNA Device"
        assert devices[0].transport == "dlna"

        await adapter.stop()


@pytest.mark.asyncio
async def test_send_media(registry: _registry.Registry, mock_dmr: MagicMock) -> None:
    """Test sending media to a DLNA device.

    :param registry: The Registry fixture.
    :param mock_dmr: The mock_dmr fixture.
    :returns: None
    """
    adapter = _dlna_adapter.DlnaAdapter(registry)
    adapter._discovered_devices["uuid:test-udn"] = mock_dmr  # type: ignore[reportPrivateUsage]

    device = _types.Device(
        id=_types.DeviceID("uuid:test-udn"),
        name="Test Device",
        model="Test Model",
        transport="dlna",
        capabilities=set(),
        transport_info={"udn": "uuid:test-udn"},
    )

    payload = _types.MediaPayload.from_url("http://example.com/media.mp4")

    result = await adapter.send_media(device, payload)

    assert result.success
    assert result.controller is not None

    mock_dmr.construct_play_media_metadata.assert_called_once()
    mock_dmr.async_set_transport_uri.assert_called_once()
    mock_dmr.async_play.assert_called_once()


@pytest.mark.asyncio
async def test_media_controller(mock_dmr: MagicMock) -> None:
    """Test the DLNA media controller.

    :param mock_dmr: The mock_dmr fixture.
    :returns: None
    """
    controller = _dlna_adapter.DlnaMediaController(mock_dmr)

    mock_dmr.can_play = True
    await controller.play()
    mock_dmr.async_play.assert_called_once()

    mock_dmr.can_pause = True
    await controller.pause()
    mock_dmr.async_pause.assert_called_once()

    mock_dmr.can_stop = True
    await controller.stop()
    mock_dmr.async_stop.assert_called_once()

    mock_dmr.can_seek_rel_time = True
    await controller.seek(65.0)
    # Check if called with timedelta
    mock_dmr.async_seek_rel_time.assert_called_with(timedelta(seconds=65.0))

    mock_dmr.has_volume_level = True
    await controller.set_volume(0.5)
    mock_dmr.async_set_volume_level.assert_called_with(0.5)

    mock_dmr.has_volume_mute = True
    await controller.set_mute(True)
    mock_dmr.async_mute_volume.assert_called_with(True)
