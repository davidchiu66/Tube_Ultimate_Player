from __future__ import annotations

import socket
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

from dlna.controller import DlnaController, build_didl_lite, format_dlna_time, parse_dlna_time
from dlna.discovery import (
    SEARCH_TARGETS,
    _deduplicate_devices,
    device_control_endpoint,
    probe_cached_devices,
    _is_candidate_response,
    local_ipv4_addresses,
    parse_device_description,
    parse_ssdp_headers,
)
from dlna.media_server import DlnaMediaServer, DlnaMediaSource, build_ffmpeg_mux_command, mime_type_for_file
from dlna.models import DlnaDevice
from services.config_service import ConfigService


DEVICE_XML = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <URLBase>http://192.168.1.20:1400/</URLBase>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>Living Room TV</friendlyName>
    <manufacturer>Example</manufacturer>
    <modelName>Renderer 1</modelName>
    <UDN>uuid:device-123</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>/MediaRenderer/AVTransport/Control</controlURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <controlURL>/MediaRenderer/RenderingControl/Control</controlURL>
      </service>
    </serviceList>
  </device>
</root>
"""


class DlnaProtocolTests(unittest.TestCase):
    def test_cached_device_endpoint_uses_av_transport_port(self) -> None:
        device = DlnaDevice(
            uuid="cached",
            friendly_name="Cached TV",
            manufacturer="",
            model_name="",
            location="http://192.168.1.20:1400/device.xml",
            host="192.168.1.20",
            av_transport_url="http://192.168.1.20:49152/control",
        )
        self.assertEqual(device_control_endpoint(device), ("192.168.1.20", 49152))

    @patch("dlna.discovery.socket.create_connection")
    def test_cached_device_probe_checks_ip_and_port(self, create_connection_mock) -> None:
        create_connection_mock.return_value = MagicMock()
        device = DlnaDevice(
            uuid="cached",
            friendly_name="Cached TV",
            manufacturer="",
            model_name="",
            location="http://127.0.0.1:1400/device.xml",
            host="127.0.0.1",
            av_transport_url="http://127.0.0.1:49152/control",
        )

        self.assertEqual(probe_cached_devices([device], timeout=0.2), [device])
        create_connection_mock.assert_called_once_with(("127.0.0.1", 49152), timeout=0.2)

    def test_discovery_uses_compatibility_search_targets(self) -> None:
        self.assertIn("urn:schemas-upnp-org:device:MediaRenderer:1", SEARCH_TARGETS)
        self.assertIn("urn:schemas-upnp-org:service:AVTransport:1", SEARCH_TARGETS)
        self.assertIn("upnp:rootdevice", SEARCH_TARGETS)
        self.assertIn("ssdp:all", SEARCH_TARGETS)

    def test_discovery_ignores_unrelated_ssdp_all_responses(self) -> None:
        self.assertFalse(_is_candidate_response({"st": "linkease:agent"}))
        self.assertFalse(_is_candidate_response({"st": "urn:schemas-upnp-org:device:MediaServer:1"}))
        self.assertTrue(_is_candidate_response({"st": "upnp:rootdevice"}))
        self.assertTrue(
            _is_candidate_response({"st": "urn:schemas-upnp-org:service:AVTransport:1"})
        )

    @patch("dlna.discovery.socket.getaddrinfo")
    @patch("dlna.discovery.socket.gethostbyname_ex")
    @patch("dlna.discovery.socket.gethostname", return_value="player-pc")
    def test_local_interfaces_include_physical_and_virtual_ipv4(
        self,
        _hostname_mock,
        host_lookup_mock,
        addrinfo_mock,
    ) -> None:
        host_lookup_mock.return_value = (
            "player-pc",
            [],
            ["192.168.5.6", "100.70.162.6", "127.0.0.1", "169.254.1.2"],
        )
        addrinfo_mock.return_value = [
            (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("10.229.169.174", 0)),
        ]

        self.assertEqual(
            local_ipv4_addresses(),
            ["10.229.169.174", "100.70.162.6", "192.168.5.6"],
        )

    def test_duplicate_renderers_on_same_host_prefer_dlna_cast_entry(self) -> None:
        generic = DlnaDevice(
            uuid="generic",
            friendly_name="客厅电视",
            manufacturer="LEBO",
            model_name="HappyCast",
            location="http://192.168.5.31:49152/description.xml",
            host="192.168.5.31",
            av_transport_url="http://192.168.5.31:49152/control",
        )
        dlna = DlnaDevice(
            uuid="dlna",
            friendly_name="客厅电视 DLNA投屏",
            manufacturer="",
            model_name="HiSmart",
            location="http://192.168.5.31:38400/renderer.xml",
            host="192.168.5.31",
            av_transport_url="http://192.168.5.31:38400/control",
            rendering_control_url="http://192.168.5.31:38400/volume",
        )

        self.assertEqual(_deduplicate_devices([generic, dlna]), [dlna])

    def test_ssdp_headers_are_case_insensitive(self) -> None:
        headers = parse_ssdp_headers(
            "HTTP/1.1 200 OK\r\nLOCATION: http://192.168.1.20/device.xml\r\nST: renderer\r\n\r\n"
        )
        self.assertEqual(headers["location"], "http://192.168.1.20/device.xml")

    def test_device_description_resolves_control_urls(self) -> None:
        device = parse_device_description(DEVICE_XML, "http://192.168.1.20:1400/device.xml")
        self.assertEqual(device.uuid, "device-123")
        self.assertEqual(device.friendly_name, "Living Room TV")
        self.assertEqual(
            device.av_transport_url,
            "http://192.168.1.20:1400/MediaRenderer/AVTransport/Control",
        )
        self.assertTrue(device.rendering_control_url.endswith("/RenderingControl/Control"))

    def test_didl_metadata_escapes_content(self) -> None:
        metadata = build_didl_lite("A & B <Video>", "http://host/media/1?a=1&b=2", "video/mp4")
        self.assertIn("A &amp; B &lt;Video&gt;", metadata)
        self.assertIn("a=1&amp;b=2", metadata)

    def test_dlna_time_round_trip(self) -> None:
        self.assertEqual(format_dlna_time(3723), "01:02:03")
        self.assertEqual(parse_dlna_time("01:02:03.500"), 3723.5)


class FfmpegMuxCommandTests(unittest.TestCase):
    def test_separate_streams_are_muxed_from_current_position(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            ffmpeg = Path(temp_dir) / "ffmpeg.exe"
            ffmpeg.touch()
            source = DlnaMediaSource(
                title="Video",
                video_url="https://cdn/video.m4s",
                audio_url="https://cdn/audio.m4s",
                headers={"Referer": "https://www.bilibili.com/"},
                video_codec="avc1",
                audio_codec="mp4a.40.2",
                ffmpeg_path=str(ffmpeg),
                start_position=12.5,
            )

            command = build_ffmpeg_mux_command(source)

        self.assertEqual(command.count("-i"), 2)
        self.assertEqual(command.count("-ss"), 2)
        self.assertIn("12.500", command)
        self.assertIn("mpegts", command)
        audio_index = command.index("-c:a")
        self.assertEqual(command[audio_index + 1], "copy")


class _RangeUpstreamHandler(BaseHTTPRequestHandler):
    payload = b"0123456789"

    def do_GET(self) -> None:  # noqa: N802
        value = str(self.headers.get("Range") or "")
        start, end = 0, len(self.payload) - 1
        status = 200
        if value.startswith("bytes="):
            start_text, end_text = value[6:].split("-", 1)
            start = int(start_text or 0)
            end = int(end_text or end)
            status = 206
        data = self.payload[start:end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.payload)}")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args) -> None:
        pass


class _SoapHandler(BaseHTTPRequestHandler):
    actions: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        action = str(self.headers.get("SOAPACTION") or "").strip('"').rsplit("#", 1)[-1]
        self.actions.append(action)
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        payload = (
            '<?xml version="1.0"?>'
            '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
            f"<s:Body><u:{action}Response xmlns:u=\"urn:schemas-upnp-org:service:AVTransport:1\"/>"
            "</s:Body></s:Envelope>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        pass


class DlnaHttpRelayTests(unittest.TestCase):
    def test_range_request_is_forwarded(self) -> None:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), _RangeUpstreamHandler)
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        relay = DlnaMediaServer()
        try:
            relay._ensure_started("127.0.0.1", 0)
            token = "test-token"
            with relay._sources_lock:
                relay._sources[token] = DlnaMediaSource(
                    title="Video",
                    video_url=f"http://127.0.0.1:{upstream.server_address[1]}/video",
                )
            relay_port = relay._server.server_address[1]
            request = urllib.request.Request(
                f"http://127.0.0.1:{relay_port}/media/{token}",
                headers={"Range": "bytes=2-5"},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=5) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.headers.get("Content-Range"), "bytes 2-5/10")
                self.assertEqual(response.read(), b"2345")
        finally:
            relay.stop()
            upstream.shutdown()
            upstream.server_close()

    def test_local_file_range_request_is_served(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media = Path(temp_dir) / "sample.mp3"
            media.write_bytes(b"0123456789")
            relay = DlnaMediaServer()
            try:
                relay._ensure_started("127.0.0.1", 0)
                token = "local-token"
                with relay._sources_lock:
                    relay._sources[token] = DlnaMediaSource(
                        title="sample.mp3",
                        video_url="",
                        file_path=str(media),
                        mime_type=mime_type_for_file(media),
                    )
                relay_port = relay._server.server_address[1]
                request = urllib.request.Request(
                    f"http://127.0.0.1:{relay_port}/media/{token}",
                    headers={"Range": "bytes=3-6"},
                )
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers.get("Content-Type"), "audio/mpeg")
                    self.assertEqual(response.headers.get("Content-Range"), "bytes 3-6/10")
                    self.assertEqual(response.read(), b"3456")
            finally:
                relay.stop()

    def test_local_audio_mime_type_is_detected(self) -> None:
        self.assertEqual(mime_type_for_file("song.flac"), "audio/flac")


class ConfigServiceTests(unittest.TestCase):
    def test_dlna_media_server_port_is_clamped(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            default_path = Path(temp_dir) / "default.json"
            user_path = Path(temp_dir) / "user.json"
            default_path.write_text('{"dlna": {"media_server_port": 70000}}', encoding="utf-8")
            config = ConfigService(default_path=default_path, user_path=user_path)

        self.assertEqual(config.dlna_media_server_port(), 65535)


class DlnaSoapControlTests(unittest.TestCase):
    def test_cast_sends_set_uri_play_and_seek(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SoapHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        _SoapHandler.actions = []
        try:
            control_url = f"http://127.0.0.1:{server.server_address[1]}/control"
            device = DlnaDevice(
                uuid="test",
                friendly_name="Test TV",
                manufacturer="",
                model_name="",
                location=control_url,
                host="127.0.0.1",
                av_transport_url=control_url,
            )
            DlnaController(timeout=2).cast(
                device,
                "http://192.168.1.2/media/test",
                build_didl_lite("Test", "http://192.168.1.2/media/test", "video/mp4"),
                15,
            )
            self.assertEqual(_SoapHandler.actions, ["SetAVTransportURI", "Play", "Seek"])
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
