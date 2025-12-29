"""Unit tests for commoncast.registry module."""

import asyncio
from datetime import datetime, timezone

import pytest

from commoncast.events import (
    DeviceAdded,
    DeviceEvent,
    DeviceHeartbeat,
    DeviceRemoved,
)
from commoncast.registry import Registry
from commoncast.types import Capability, Device, DeviceID, MediaPayload


@pytest.fixture
def registry() -> Registry:
    """Fixture to provide a fresh Registry instance."""
    return Registry()


@pytest.fixture
def device() -> Device:
    """Fixture to provide a sample Device instance."""
    return Device(
        id=DeviceID("dev1"),
        name="Test Device",
        model="TestModel",
        transport="test",
        capabilities={Capability("video")},
        transport_info={},
    )


@pytest.mark.asyncio
async def test_add_list_devices(registry: Registry, device: Device) -> None:
    """Test adding and listing devices."""
    assert registry.list_devices() == []
    await registry._add_device(device)  # type: ignore[reportPrivateUsage]
    devices = registry.list_devices()
    assert len(devices) == 1
    assert devices[0].id == device.id


@pytest.mark.asyncio
async def test_subscribe_async(registry: Registry, device: Device) -> None:
    """Test async subscription."""
    events: list[DeviceEvent] = []

    async def callback(ev: DeviceEvent) -> None:
        events.append(ev)

    sub = registry.subscribe(callback)
    await registry._add_device(device)  # type: ignore[reportPrivateUsage]

    # Wait for event to propagate
    await asyncio.sleep(0.01)

    assert len(events) == 1
    assert isinstance(events[0], DeviceAdded)
    assert events[0].device.id == device.id

    sub.unsubscribe()
    # Should not receive next event
    await registry._publish_event(  # type: ignore[reportPrivateUsage]
        DeviceHeartbeat(timestamp=datetime.now(timezone.utc), device_id=device.id)
    )
    await asyncio.sleep(0.01)
    assert len(events) == 1


def test_subscribe_sync(registry: Registry, device: Device) -> None:
    """Test synchronous subscription."""
    # This test needs to run in an environment where the loop is running because _publish_event schedules it
    # But pytest-asyncio runs the test in a loop.
    # Registry.subscribe_sync uses loop.run_in_executor

    events: list[DeviceEvent] = []

    def callback(ev: DeviceEvent) -> None:
        events.append(ev)

    sub = registry.subscribe_sync(callback)

    async def trigger() -> None:
        await registry._add_device(device)  # type: ignore[reportPrivateUsage]
        await asyncio.sleep(0.1)  # Wait for threadpool execution

    asyncio.run(trigger())

    assert len(events) == 1
    assert isinstance(events[0], DeviceAdded)

    sub.unsubscribe()


@pytest.mark.asyncio
async def test_events_iterator(registry: Registry, device: Device) -> None:
    """Test events async iterator."""
    # We need to run the iterator consumption concurrently
    events_received: list[DeviceEvent] = []

    async def consumer() -> None:
        async for ev in registry.events():
            events_received.append(ev)
            if len(events_received) >= 1:
                break

    task = asyncio.create_task(consumer())
    await registry._add_device(device)  # type: ignore[reportPrivateUsage]
    await task

    assert len(events_received) == 1
    assert isinstance(events_received[0], DeviceAdded)


@pytest.mark.asyncio
async def test_lifecycle_stop_clears_devices(
    registry: Registry, device: Device
) -> None:
    """Test that stop() clears devices and emits removal events."""
    await registry.start()
    await registry._add_device(device)  # type: ignore[reportPrivateUsage]
    assert len(registry.list_devices()) == 1

    events: list[DeviceEvent] = []

    async def cb(ev: DeviceEvent) -> None:
        events.append(ev)

    registry.subscribe(cb)

    await registry.stop()

    assert len(registry.list_devices()) == 0
    await asyncio.sleep(0.01)
    # Check for DeviceRemoved event
    removed_events = [e for e in events if isinstance(e, DeviceRemoved)]
    assert len(removed_events) == 1
    assert removed_events[0].device_id == device.id


def test_backend_management(registry: Registry) -> None:
    """Test enabling and disabling backends."""
    registry.enable_backend("chromecast")
    backends = registry.list_backends()
    assert backends["chromecast"]["enabled"] is True

    registry.disable_backend("chromecast")
    backends = registry.list_backends()
    assert backends["chromecast"]["enabled"] is False


@pytest.mark.asyncio
async def test_send_media(registry: Registry, device: Device) -> None:
    """Test sending media."""
    # Unknown device
    res = await registry.send_media(device, MediaPayload.from_bytes(b""))
    assert not res.success
    assert res.reason == "device_unknown"

    # Known device
    await registry._add_device(device)  # type: ignore[reportPrivateUsage]
    res = await registry.send_media(device, MediaPayload.from_bytes(b""))
    assert res.success
