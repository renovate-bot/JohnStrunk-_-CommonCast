"""Registry implementation for CommonCast.

This module contains the Registry class that manages discovered devices,
subscriptions, backends, and event publication.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from commoncast.events import (
    DeviceAdded,
    DeviceEvent,
    DeviceID,
    DeviceRemoved,
)
from commoncast.types import (
    Device,
    MediaPayload,
    SendResult,
    Subscription,
)


def _safe_call_sync(cb: Callable[[DeviceEvent], None], ev: DeviceEvent) -> None:
    """Call a synchronous subscriber safely on a background thread.

    Exceptions are swallowed to avoid propagating user errors into the
    registry internals.

    :param cb: Synchronous callback accepting a DeviceEvent.
    :param ev: The event to deliver.
    :returns: None
    """
    try:
        cb(ev)
    except Exception:
        # Swallow exceptions from user callbacks to avoid crashing the threadpool.
        pass


class Registry:
    """Device registry used by the public API.

    The registry maintains a snapshot of discovered devices and provides both
    push (subscriptions) and pull (async iterator) models for events.
    """

    def __init__(self) -> None:
        """Create a new Registry instance.

        Initializes internal state used to track devices and subscribers.

        :returns: None
        """
        self._devices: dict[DeviceID, Device] = {}
        self._event_queue: asyncio.Queue[DeviceEvent] = asyncio.Queue()
        self._subscribers: list[Callable[[DeviceEvent], Awaitable[None]]] = []
        self._subscribers_sync: list[Callable[[DeviceEvent], None]] = []
        self._backends: dict[str, dict[str, Any]] = {}
        # Use a set to hold strong references to background tasks
        self._tasks: set[asyncio.Task[Any]] = set()
        self._running = False
        self._lock = asyncio.Lock()

    def list_devices(self) -> list[Device]:
        """Return a snapshot list of currently-known devices.

        :returns: List of Device objects currently tracked by the registry.
        """
        return list(self._devices.values())

    def subscribe(
        self, callback: Callable[[DeviceEvent], Awaitable[None]]
    ) -> Subscription:
        """Register an async callback to receive DeviceEvent objects.

        The callback is scheduled on the running event loop for each event.

        :param callback: Async callable that accepts a DeviceEvent.
        :returns: Subscription handle with an unsubscribe() method.
        """
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return Subscription(_unsubscribe)

    def subscribe_sync(self, callback: Callable[[DeviceEvent], None]) -> Subscription:
        """Register a synchronous callback executed on a threadpool for each event.

        :param callback: Synchronous callable that accepts a DeviceEvent.
        :returns: Subscription handle with an unsubscribe() method.
        """
        self._subscribers_sync.append(callback)

        def _unsubscribe() -> None:
            try:
                self._subscribers_sync.remove(callback)
            except ValueError:
                pass

        return Subscription(_unsubscribe)

    async def events(self) -> AsyncIterator[DeviceEvent]:
        """Async iterator that yields DeviceEvent objects as they occur.

        Use this for pull-style consumption of registry events.
        :returns: Async iterator over DeviceEvent objects.
        """
        while True:
            ev = await self._event_queue.get()
            yield ev

    async def start(
        self, *, media_host: str | None = "0.0.0.0", media_port: int = 0
    ) -> None:
        """Start background discovery and optional media server.

        Start adapters and (optionally) an embedded media server so discovery
        can begin.

        :param media_host: Host interface to bind the media server to, or None to
            disable the embedded server.
        :param media_port: Port to bind the media server to (0 selects a free port).
        :returns: None
        """
        async with self._lock:
            if self._running:
                return
            self._running = True
            # In a full implementation adapters would start here.

    async def stop(self) -> None:
        """Stop discovery, shut down adapters and clear tracked devices.

        Stop adapters and emit DeviceRemoved events for any devices that are
        no longer reachable as a result.

        :returns: None
        """
        async with self._lock:
            if not self._running:
                return
            self._running = False
            # Emit DeviceRemoved for all devices
            now = datetime.now(timezone.utc)
            for device_id in list(self._devices.keys()):
                ev = DeviceRemoved(
                    timestamp=now, device_id=device_id, reason="shutdown"
                )
                await self._publish_event(ev)
            self._devices.clear()

    def start_sync(self, *args: Any, **kwargs: Any) -> None:
        """Start the registry synchronously.

        Convenience wrapper for start() for consumers that prefer blocking APIs.

        :param args: Positional arguments passed to start().
        :param kwargs: Keyword arguments passed to start().
        :returns: None
        """
        asyncio.run(self.start(*args, **kwargs))

    def stop_sync(self) -> None:
        """Stop the registry synchronously.

        Convenience wrapper for stop() for consumers that prefer blocking APIs.

        :returns: None
        """
        asyncio.run(self.stop())

    def enable_backend(self, name: str) -> None:
        """Enable a named protocol backend.

        :param name: Backend identifier to enable.
        :returns: None
        """
        info = self._backends.setdefault(name, {})
        info.setdefault("enabled", True)
        info["enabled"] = True

    def disable_backend(self, name: str) -> None:
        """Disable a named protocol backend.

        :param name: Backend identifier to disable.
        :returns: None
        """
        info = self._backends.setdefault(name, {})
        info["enabled"] = False

    def list_backends(self) -> dict[str, dict[str, Any]]:
        """Return a mapping of backend names to their state information.

        :returns: Dictionary mapping backend names to their status info.
        """
        return dict(self._backends)

    async def _publish_event(self, ev: DeviceEvent) -> None:
        """Publish an event to subscribers and the async iterator queue.

        :param ev: The DeviceEvent to publish.
        :returns: None
        """
        # Put on queue for async pull consumers
        await self._event_queue.put(ev)
        # Dispatch to async subscribers
        for cb in list(self._subscribers):
            try:
                # schedule but don't await to avoid blocking; store task to avoid GC
                # Use ensure_future to handle general Awaitables
                task: asyncio.Task[Any] = asyncio.ensure_future(cb(ev))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            except Exception:
                pass
        # Dispatch to sync subscribers on threadpool
        loop = asyncio.get_running_loop()
        for cb in list(self._subscribers_sync):
            loop.run_in_executor(None, _safe_call_sync, cb, ev)

    async def send_media(
        self,
        device: Device,
        media: MediaPayload,
        *,
        format: str | None = None,
        timeout: float = 30.0,
        options: dict[str, Any] | None = None,
    ) -> SendResult:
        """Send media to a device by delegating to adapters.

        This minimal implementation simulates success for known devices.

        :param device: Target Device.
        :param media: MediaPayload to send.
        :param format: Optional format hint.
        :param timeout: Operation timeout in seconds.
        :param options: Optional transport-specific options.
        :returns: SendResult describing the outcome.
        """
        # Minimal behavior: accept anything and return success if device known
        if device.id not in self._devices:
            return SendResult(success=False, reason="device_unknown")
        # In real implementation, this would route to the adapter based on device.transport
        await asyncio.sleep(0)  # yield control

        # Return success with no controller for now, as we have no real backends
        return SendResult(
            success=True,
            metadata={"device_id": device.id},
            controller=None,
        )

    async def _add_device(self, device: Device) -> None:
        """Inject a device into the registry (helper for tests/examples).

        :param device: Device to add into the registry.
        :returns: None
        """
        self._devices[device.id] = device
        ev = DeviceAdded(timestamp=datetime.now(timezone.utc), device=device)
        await self._publish_event(ev)


default_registry = Registry()

__all__ = ["Registry", "default_registry"]
