"""DIAL adapter for CommonCast.

This module implements the DIAL (Discovery and Launch) backend.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urljoin

import aiohttp
from async_upnp_client.aiohttp import AiohttpSessionRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.const import SsdpSource
from async_upnp_client.ssdp_listener import SsdpDevice, SsdpListener

import commoncast.types as _types

if TYPE_CHECKING:
    import commoncast.types as _types_mod
    from commoncast.registry import Registry

_LOGGER = logging.getLogger(__name__)

DIAL_SERVICE_TYPE = "urn:dial-multiscreen-org:service:dial:1"
DIAL_VERSION = "2.1"
CLIENT_FRIENDLY_NAME = "CommonCast"
DISCOVERY_INTERVAL = 60.0  # Seconds between periodic searches


class DialMediaController(_types.MediaController):
    """Implementation of MediaController for DIAL devices.

    DIAL itself only supports discovery and launching apps. Playback control
    is usually app-specific. This controller provides a basic implementation
    that supports stopping the app instance if it was tracked.
    """

    def __init__(
        self, session: aiohttp.ClientSession, instance_url: str | None
    ) -> None:
        """Initialize the controller.

        :param session: aiohttp ClientSession for making requests.
        :param instance_url: The URL of the app instance, used for stopping.
        """
        self._session = session
        self._instance_url = instance_url

    async def play(self) -> None:
        """Resume playback. (Not supported by generic DIAL).

        :returns: None
        """
        _LOGGER.warning("play() is not supported by generic DIAL")

    async def pause(self) -> None:
        """Pause playback. (Not supported by generic DIAL).

        :returns: None
        """
        _LOGGER.warning("pause() is not supported by generic DIAL")

    async def stop(self) -> None:
        """Stop playback by deleting the app instance.

        :returns: None
        """
        if not self._instance_url:
            _LOGGER.warning("No instance URL available to stop")
            return

        try:
            async with self._session.delete(
                self._instance_url, allow_redirects=False
            ) as response:
                if response.status not in (200, 204):
                    _LOGGER.error("Failed to stop DIAL instance: %s", response.status)
        except Exception:
            _LOGGER.exception("Error stopping DIAL instance")

    async def seek(self, position: float) -> None:
        """Seek to a specific position. (Not supported by generic DIAL).

        :param position: Target position in seconds.
        :returns: None
        """
        _LOGGER.warning("seek() is not supported by generic DIAL")

    async def set_volume(self, level: float) -> None:
        """Set the volume level. (Not supported by generic DIAL).

        :param level: Volume level between 0.0 and 1.0.
        :returns: None
        """
        _LOGGER.warning("set_volume() is not supported by generic DIAL")

    async def set_mute(self, mute: bool) -> None:
        """Mute or unmute the device. (Not supported by generic DIAL).

        :param mute: True to mute, False to unmute.
        :returns: None
        """
        _LOGGER.warning("set_mute() is not supported by generic DIAL")


class DialAdapter(_types.BackendAdapter):
    """Adapter for DIAL devices."""

    def __init__(self, registry: Registry) -> None:
        """Initialize the adapter.

        :param registry: The CommonCast registry instance.
        """
        self._registry = registry
        self._ssdp_listener: SsdpListener | None = None
        self._upnp_factory: UpnpFactory | None = None
        self._discovered_devices: dict[str, dict[str, Any]] = {}
        self._session: aiohttp.ClientSession | None = None
        self._requester: AiohttpSessionRequester | None = None
        self._discovery_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start DIAL discovery.

        :returns: None
        """
        if self._ssdp_listener:
            return

        _LOGGER.info("Starting DIAL discovery")

        self._session = aiohttp.ClientSession()
        self._requester = AiohttpSessionRequester(self._session)
        self._upnp_factory = UpnpFactory(self._requester)
        self._ssdp_listener = SsdpListener(
            async_callback=self._on_device_found, search_target=DIAL_SERVICE_TYPE
        )
        await self._ssdp_listener.async_start()

        # Start periodic discovery task
        self._discovery_task = asyncio.create_task(self._periodic_discovery())

    async def stop(self) -> None:
        """Stop DIAL discovery.

        :returns: None
        """
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
            self._discovery_task = None

        if self._ssdp_listener:
            await self._ssdp_listener.async_stop()
            self._ssdp_listener = None

        self._discovered_devices.clear()
        self._upnp_factory = None
        self._requester = None

        if self._session:
            await self._session.close()
            self._session = None

    async def _periodic_discovery(self) -> None:
        """Periodically trigger SSDP search.

        :returns: None
        """
        # Wait for the registry to be fully started before sending probes.
        # This ensures the overall system is ready and avoids race conditions
        # during rapid startup/shutdown.
        await self._registry.wait_until_ready()

        while True:
            try:
                if self._ssdp_listener:
                    _LOGGER.debug("Triggering DIAL SSDP search")
                    await self._ssdp_listener.async_search()
            except Exception:
                _LOGGER.exception("Error during periodic DIAL discovery")

            await asyncio.sleep(DISCOVERY_INTERVAL)

    async def _on_device_found(
        self, device: SsdpDevice, dtype: str, source: SsdpSource
    ) -> None:
        """Handle new device found via SSDP.

        :param device: The SSDP device object.
        :param dtype: Device type string.
        :param source: Source of the advertisement.
        """
        if source == SsdpSource.ADVERTISEMENT_BYEBYE:
            if device.udn in self._discovered_devices:
                _LOGGER.info("DIAL device lost: %s", device.udn)
                self._discovered_devices.pop(device.udn)
                await self._registry.unregister_device(
                    _types.DeviceID(device.udn), reason="lost"
                )
            return

        if DIAL_SERVICE_TYPE not in dtype:
            return

        location = device.location
        if not location:
            return

        if device.udn in self._discovered_devices:
            return

        try:
            info = await self._fetch_device_info(device, dtype)
            app_url, friendly_name, model_name, wakeup_info = info

            if not app_url:
                _LOGGER.debug("Could not find Application-URL for %s", location)
                return

            self._discovered_devices[device.udn] = {
                "udn": device.udn,
                "location": location,
                "app_url": app_url,
            }

            await self._register_device(
                device.udn, friendly_name, model_name, app_url, wakeup_info
            )

        except Exception:
            _LOGGER.exception("Error processing DIAL device from %s", location)

    async def _fetch_device_info(
        self, device: SsdpDevice, dtype: str
    ) -> tuple[str | None, str, str, dict[str, str]]:
        """Fetch DIAL device information from SSDP and location URL.

        :param device: The SSDP device object.
        :param dtype: Device type string.
        :returns: A tuple of (app_url, friendly_name, model_name, wakeup_info).
        """
        location = device.location
        if not location:
            return None, "DIAL Device", "Generic DIAL", {}

        # 1. Try to find Application-URL and WAKEUP in SSDP headers
        app_url: str | None = None
        wakeup_info: dict[str, str] = {}
        ssdp_headers = cast(
            Mapping[Any, Any],
            device.search_headers.get(dtype) or device.advertisement_headers.get(dtype),
        )
        if ssdp_headers:
            for key, value in ssdp_headers.items():
                k = str(key).lower()
                if k == "application-url":
                    app_url = str(value)
                elif k == "wakeup":
                    wakeup_info = self._parse_wakeup_header(str(value))

        friendly_name = "DIAL Device"
        model_name = "Generic DIAL"

        # 2. Fetch device description for friendly name and potentially Application-URL
        if self._session:
            try:
                async with self._session.get(
                    location, allow_redirects=False
                ) as response:
                    if response.status == 200:  # noqa: PLR2004
                        if not app_url:
                            app_url = response.headers.get("Application-URL")

                        body = await response.text()

                        # Try UpnpFactory first
                        try:
                            if self._upnp_factory:
                                upnp_dev = await self._upnp_factory.async_create_device(
                                    location
                                )
                                friendly_name = upnp_dev.friendly_name
                                model_name = upnp_dev.model_name
                        except Exception:
                            _LOGGER.debug(
                                "UpnpFactory failed for %s, parsing XML manually",
                                location,
                            )
                            friendly_name, model_name = self._parse_description_xml(
                                body, friendly_name, model_name
                            )
                    else:
                        _LOGGER.warning(
                            "Failed to fetch DIAL description from %s: %s",
                            location,
                            response.status,
                        )
            except Exception:
                _LOGGER.debug("Error fetching DIAL description from %s", location)

        return app_url, friendly_name, model_name, wakeup_info

    def _parse_wakeup_header(self, header: str) -> dict[str, str]:
        """Parse the DIAL WAKEUP header.

        :param header: The WAKEUP header value.
        :returns: A dictionary of parsed parameters.
        """
        params: dict[str, str] = {}
        for part in header.split(";"):
            if "=" in part:
                key, value = part.split("=", 1)
                params[key.strip().lower()] = value.strip()
        return params

    def _parse_description_xml(
        self, xml_body: str, default_name: str, default_model: str
    ) -> tuple[str, str]:
        """Manually parse UPnP device description XML for metadata.

        :param xml_body: The XML body string.
        :param default_name: Default friendly name.
        :param default_model: Default model name.
        :returns: A tuple of (friendly_name, model_name).
        """
        friendly_name = default_name
        model_name = default_model
        try:
            root = ET.fromstring(xml_body)
            ns = {"ns": "urn:schemas-upnp-org:device-1-0"}
            device_el = root.find(".//ns:device", ns)
            if device_el is not None:
                fname_el = device_el.find("ns:friendlyName", ns)
                if fname_el is not None:
                    friendly_name = fname_el.text or friendly_name
                mname_el = device_el.find("ns:modelName", ns)
                if mname_el is not None:
                    model_name = mname_el.text or model_name
        except Exception:
            _LOGGER.debug("Manual XML parsing failed")
        return friendly_name, model_name

    async def _register_device(
        self, udn: str, name: str, model: str, app_url: str, wakeup_info: dict[str, str]
    ) -> None:
        """Register device with CommonCast registry.

        :param udn: Unique device name.
        :param name: Friendly name.
        :param model: Model name.
        :param app_url: DIAL Application-URL.
        :param wakeup_info: Wake-on-LAN information.
        """
        device = _types.Device(
            id=_types.DeviceID(udn),
            name=name,
            model=model,
            transport="dial",
            capabilities={_types.Capability("video"), _types.Capability("audio")},
            transport_info={
                "udn": udn,
                "app_url": app_url,
                "wakeup": wakeup_info,
            },
            media_types=set(),  # DIAL doesn't expose this directly
        )
        await self._registry.register_device(device)

    async def send_media(
        self,
        device: _types.Device,
        media: _types_mod.MediaPayload,
        *,
        format: str | None = None,
        timeout: float = 30.0,
        options: dict[str, Any] | None = None,
    ) -> _types.SendResult:
        """Send media to a DIAL device.

        :param device: The target Device.
        :param media: The MediaPayload to send.
        :param format: Optional format hint.
        :param timeout: Timeout in seconds.
        :param options: Optional transport-specific options.
        :returns: SendResult.
        """
        app_url = device.transport_info.get("app_url")
        if not app_url or self._session is None:
            return _types.SendResult(
                success=False,
                reason="missing_app_url" if not app_url else "no_session",
            )

        # Options can specify the app name. Default to "YouTube" as a common one,
        # or maybe we should have a better default.
        app_name = (options or {}).get("app_name", "YouTube")

        # Construct launch URL: [Application-URL]/[appName]
        if not app_url.endswith("/"):
            app_url += "/"
        launch_url = app_url + app_name

        try:
            # 1. Query application information (Recommended discovery flow)
            # This checks if the app exists and its current state.
            async with self._session.get(
                launch_url,
                params={"clientDialVer": DIAL_VERSION},
                allow_redirects=False,
            ) as response:
                if response.status == 404:  # noqa: PLR2004
                    return _types.SendResult(
                        success=False, reason=f"Application {app_name} not found"
                    )
                if response.status != 200:  # noqa: PLR2004
                    _LOGGER.warning(
                        "Unexpected status during app discovery for %s: %s",
                        app_name,
                        response.status,
                    )

            # 2. Launch the application (or update its content if already running)
            url = media.url
            if not url:
                payload_id = str(uuid.uuid4())
                url = self._registry.register_media_payload(payload_id, media)
                if not url:
                    return _types.SendResult(
                        success=False, reason="media_server_not_available"
                    )

            params = {
                "friendlyName": CLIENT_FRIENDLY_NAME,
                "clientDialVer": DIAL_VERSION,
            }
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
            }

            async with self._session.post(
                launch_url,
                params=params,
                data=url or "",
                headers=headers,
                allow_redirects=False,
            ) as response:
                if response.status in (200, 201, 204):
                    instance_url = response.headers.get("Location")
                    # If instance_url is relative, resolve it against launch_url
                    if instance_url and not instance_url.startswith("http"):
                        instance_url = urljoin(launch_url + "/", instance_url)

                    return _types.SendResult(
                        success=True,
                        controller=DialMediaController(self._session, instance_url),
                    )
                else:
                    return _types.SendResult(
                        success=False,
                        reason=f"Failed to launch app: {response.status}",
                    )

        except Exception as e:
            _LOGGER.exception("Failed to send media via DIAL")
            return _types.SendResult(success=False, reason=str(e))
