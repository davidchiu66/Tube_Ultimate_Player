from __future__ import annotations

import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from dlna.models import DlnaDevice


logger = logging.getLogger("tube_player.dlna")

AV_TRANSPORT = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL = "urn:schemas-upnp-org:service:RenderingControl:1"


class DlnaError(RuntimeError):
    pass


class DlnaController:
    def __init__(self, timeout: float = 6.0) -> None:
        self.timeout = max(2.0, float(timeout))
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def cast(
        self,
        device: DlnaDevice,
        media_url: str,
        metadata: str,
        start_position: float = 0.0,
    ) -> None:
        try:
            self.set_uri(device, media_url, metadata)
        except DlnaError:
            logger.warning("DLNA SetAVTransportURI metadata rejected; retrying without metadata")
            self.set_uri(device, media_url, "")
        self.play(device)
        if start_position > 1.0:
            try:
                self.seek(device, start_position)
            except DlnaError as exc:
                logger.warning("DLNA initial seek failed device=%s: %s", device.friendly_name, exc)

    def set_uri(self, device: DlnaDevice, media_url: str, metadata: str) -> None:
        self._action(
            device.av_transport_url,
            AV_TRANSPORT,
            "SetAVTransportURI",
            {
                "InstanceID": "0",
                "CurrentURI": media_url,
                "CurrentURIMetaData": metadata,
            },
        )

    def play(self, device: DlnaDevice) -> None:
        self._action(
            device.av_transport_url,
            AV_TRANSPORT,
            "Play",
            {"InstanceID": "0", "Speed": "1"},
        )

    def pause(self, device: DlnaDevice) -> None:
        self._action(
            device.av_transport_url,
            AV_TRANSPORT,
            "Pause",
            {"InstanceID": "0"},
        )

    def stop(self, device: DlnaDevice) -> None:
        self._action(
            device.av_transport_url,
            AV_TRANSPORT,
            "Stop",
            {"InstanceID": "0"},
        )

    def seek(self, device: DlnaDevice, seconds: float) -> None:
        self._action(
            device.av_transport_url,
            AV_TRANSPORT,
            "Seek",
            {
                "InstanceID": "0",
                "Unit": "REL_TIME",
                "Target": format_dlna_time(seconds),
            },
        )

    def set_volume(self, device: DlnaDevice, volume: int) -> None:
        if not device.rendering_control_url:
            raise DlnaError(f"{device.friendly_name} 不支持远程音量控制")
        self._action(
            device.rendering_control_url,
            RENDERING_CONTROL,
            "SetVolume",
            {
                "InstanceID": "0",
                "Channel": "Master",
                "DesiredVolume": str(max(0, min(100, int(volume)))),
            },
        )

    def get_position(self, device: DlnaDevice) -> tuple[float, float]:
        payload = self._action(
            device.av_transport_url,
            AV_TRANSPORT,
            "GetPositionInfo",
            {"InstanceID": "0"},
        )
        values = _soap_values(payload)
        return parse_dlna_time(values.get("RelTime", "")), parse_dlna_time(values.get("TrackDuration", ""))

    def _action(
        self,
        control_url: str,
        service_type: str,
        action: str,
        arguments: dict[str, str],
    ) -> bytes:
        if not control_url:
            raise DlnaError(f"设备不支持 {action}")
        body = _soap_envelope(service_type, action, arguments)
        request = urllib.request.Request(
            control_url,
            data=body,
            method="POST",
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPACTION": f'"{service_type}#{action}"',
                "User-Agent": "TubeUltimatePlayer/0.2 UPnP/1.0 DLNADOC/1.50",
                "Connection": "close",
            },
        )
        logger.info("DLNA action start device_url=%s action=%s", control_url, action)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise DlnaError(f"{action} 返回 HTTP {exc.code}: {detail[:500]}") from exc
        except OSError as exc:
            raise DlnaError(f"{action} 请求失败: {exc}") from exc
        logger.info("DLNA action success action=%s bytes=%s", action, len(payload))
        return payload


def build_didl_lite(title: str, media_url: str, mime_type: str) -> str:
    media_class = "object.item.videoItem.movie"
    if mime_type.startswith("audio/"):
        media_class = "object.item.audioItem.musicTrack"
    elif mime_type.startswith("image/"):
        media_class = "object.item.imageItem.photo"
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/" '
        'xmlns:dlna="urn:schemas-dlna-org:metadata-1-0/">'
        '<item id="1" parentID="0" restricted="1">'
        f"<dc:title>{escape(str(title or '在线视频'))}</dc:title>"
        f"<upnp:class>{media_class}</upnp:class>"
        f'<res protocolInfo="http-get:*:{escape(mime_type)}:*">{escape(media_url)}</res>'
        "</item></DIDL-Lite>"
    )


def format_dlna_time(seconds: float) -> str:
    total = max(0, int(float(seconds or 0)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_dlna_time(value: str) -> float:
    text = str(value or "").strip()
    if not text or text.upper() == "NOT_IMPLEMENTED":
        return 0.0
    parts = text.split(":")
    if len(parts) != 3:
        return 0.0
    try:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return 0.0


def _soap_envelope(service_type: str, action: str, arguments: dict[str, str]) -> bytes:
    argument_xml = "".join(f"<{name}>{escape(str(value))}</{name}>" for name, value in arguments.items())
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service_type}">{argument_xml}</u:{action}></s:Body>'
        "</s:Envelope>"
    )
    return envelope.encode("utf-8")


def _soap_values(payload: bytes) -> dict[str, str]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return {}
    return {str(node.tag).rsplit("}", 1)[-1]: str(node.text or "").strip() for node in root.iter()}
