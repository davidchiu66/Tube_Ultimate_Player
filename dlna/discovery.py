from __future__ import annotations

import logging
import select
import socket
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

from dlna.models import DlnaDevice


logger = logging.getLogger("tube_player.dlna")

SSDP_ADDRESS = ("239.255.255.250", 1900)
SEARCH_TARGETS = (
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
    "upnp:rootdevice",
    "ssdp:all",
)


def device_control_endpoint(device: DlnaDevice) -> tuple[str, int] | None:
    for url in (
        device.av_transport_url,
        device.rendering_control_url,
        device.location,
    ):
        if not url:
            continue
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or device.host
        if not host:
            continue
        try:
            port = parsed.port
        except ValueError:
            continue
        if port is None:
            port = 443 if parsed.scheme.lower() == "https" else 80
        return host, port
    return None


def is_device_endpoint_reachable(device: DlnaDevice, timeout: float = 0.6) -> bool:
    endpoint = device_control_endpoint(device)
    if endpoint is None:
        return False
    timeout = max(0.1, min(2.0, float(timeout)))
    try:
        with socket.create_connection(endpoint, timeout=timeout):
            return True
    except OSError as exc:
        logger.debug(
            "DLNA cached device probe failed device=%s endpoint=%s:%s error=%s",
            device.friendly_name,
            endpoint[0],
            endpoint[1],
            exc,
        )
        return False


def probe_cached_devices(devices: list[DlnaDevice], timeout: float = 0.6) -> list[DlnaDevice]:
    candidates = _deduplicate_devices(list(devices))
    if not candidates:
        return []
    reachable: list[DlnaDevice] = []
    worker_count = min(8, len(candidates))
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="DlnaCacheProbe") as executor:
        futures = {
            executor.submit(is_device_endpoint_reachable, device, timeout): device
            for device in candidates
        }
        for future in as_completed(futures):
            device = futures[future]
            try:
                available = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.debug("DLNA cached device probe crashed device=%s: %s", device.friendly_name, exc)
                continue
            if available:
                reachable.append(device)
    result = sorted(reachable, key=lambda item: item.friendly_name.lower())
    logger.info("DLNA cached device probe finished cached=%s reachable=%s", len(candidates), len(result))
    return result


def discover_devices(timeout: float = 3.0) -> list[DlnaDevice]:
    timeout = max(1.0, min(10.0, float(timeout)))
    interfaces = local_ipv4_addresses()
    sockets: list[socket.socket] = []
    locations: set[str] = set()
    try:
        for interface in interfaces:
            try:
                sock = _discovery_socket(interface)
            except OSError as exc:
                logger.debug("DLNA discovery socket failed interface=%s: %s", interface, exc)
                continue
            sockets.append(sock)
            for target in SEARCH_TARGETS:
                message = _search_message(target)
                try:
                    sock.sendto(message, SSDP_ADDRESS)
                    sock.sendto(message, SSDP_ADDRESS)
                except OSError as exc:
                    logger.debug(
                        "DLNA discovery send failed interface=%s target=%s: %s",
                        interface,
                        target,
                        exc,
                    )

        if not sockets:
            raise RuntimeError("没有可用于 DLNA 搜索的 IPv4 网络接口")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                readable, _, _ = select.select(sockets, [], [], min(0.25, remaining))
            except (OSError, ValueError) as exc:
                logger.debug("DLNA discovery select failed: %s", exc)
                break
            if not readable:
                continue
            for sock in readable:
                try:
                    payload, address = sock.recvfrom(65535)
                except (BlockingIOError, OSError):
                    continue
                headers = parse_ssdp_headers(payload)
                location = str(headers.get("location") or "").strip()
                if (
                    _is_candidate_response(headers)
                    and location.startswith(("http://", "https://"))
                    and location not in locations
                ):
                    logger.debug(
                        "DLNA SSDP candidate source=%s interface=%s st=%s location=%s",
                        address[0],
                        sock.getsockname()[0],
                        headers.get("st", ""),
                        location,
                    )
                    locations.add(location)
    finally:
        for sock in sockets:
            sock.close()

    discovered: list[DlnaDevice] = []
    if locations:
        worker_count = min(8, len(locations))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="DlnaDescription") as executor:
            futures = {executor.submit(fetch_device_description, location): location for location in locations}
            for future in as_completed(futures):
                location = futures[future]
                try:
                    device = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("DLNA device description failed location=%s: %s", location, exc)
                    continue
                if device.av_transport_url:
                    discovered.append(device)

    result = sorted(_deduplicate_devices(discovered), key=lambda item: item.friendly_name.lower())
    logger.info(
        "DLNA discovery finished interfaces=%s locations=%s renderers=%s",
        interfaces,
        len(locations),
        len(result),
    )
    return result


def local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    hostname = socket.gethostname()
    try:
        addresses.update(socket.gethostbyname_ex(hostname)[2])
    except OSError:
        pass
    try:
        addresses.update(
            info[4][0]
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET, socket.SOCK_DGRAM)
        )
    except OSError:
        pass

    usable = sorted(
        address
        for address in addresses
        if address and not address.startswith(("0.", "127.", "169.254."))
    )
    # A wildcard socket preserves discovery on hosts where hostname lookup does
    # not expose the active adapter. Explicit interfaces remain the priority.
    return usable or ["0.0.0.0"]


def _deduplicate_devices(devices: list[DlnaDevice]) -> list[DlnaDevice]:
    by_host: dict[str, DlnaDevice] = {}
    without_host: dict[str, DlnaDevice] = {}
    for device in devices:
        if device.host:
            current = by_host.get(device.host)
            if current is None or _device_preference(device) > _device_preference(current):
                by_host[device.host] = device
            continue
        key = device.uuid or device.location
        without_host[key] = device
    return [*by_host.values(), *without_host.values()]


def _device_preference(device: DlnaDevice) -> tuple[int, int, int, int]:
    name = device.friendly_name.lower()
    model = device.model_name.lower()
    return (
        int("dlna" in name or "投屏" in name),
        int(bool(device.rendering_control_url)),
        int("windows media player" not in model),
        len(device.friendly_name),
    )


def _discovery_socket(interface: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        if interface != "0.0.0.0":
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface))
            sock.bind((interface, 0))
        sock.setblocking(False)
        return sock
    except Exception:
        sock.close()
        raise


def parse_ssdp_headers(payload: bytes | str) -> dict[str, str]:
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    headers: dict[str, str] = {}
    for line in text.replace("\r\n", "\n").split("\n")[1:]:
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        headers[name.strip().lower()] = value.strip()
    return headers


def _is_candidate_response(headers: dict[str, str]) -> bool:
    search_target = str(headers.get("st") or "").strip().lower()
    return (
        search_target == "upnp:rootdevice"
        or ":device:mediarenderer:" in search_target
        or ":service:avtransport:" in search_target
    )


def fetch_device_description(location: str, timeout: float = 4.0) -> DlnaDevice:
    request = urllib.request.Request(
        location,
        headers={"User-Agent": "TubeUltimatePlayer/0.2 UPnP/1.0 DLNADOC/1.50"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        payload = response.read()
    return parse_device_description(payload, location)


def parse_device_description(payload: bytes | str, location: str) -> DlnaDevice:
    root = ET.fromstring(payload)
    url_base = _child_text(root, "URLBase") or location
    device_nodes = [node for node in root.iter() if _local_name(node.tag) == "device"]
    device_node = next(
        (
            node
            for node in device_nodes
            if "MediaRenderer" in _direct_child_text(node, "deviceType")
        ),
        device_nodes[0] if device_nodes else None,
    )
    if device_node is None:
        raise RuntimeError("UPnP 设备描述缺少 device 节点")

    services: dict[str, str] = {}
    for service in device_node.iter():
        if _local_name(service.tag) != "service":
            continue
        service_type = _child_text(service, "serviceType")
        control_url = _child_text(service, "controlURL")
        if service_type and control_url:
            services[service_type] = urllib.parse.urljoin(url_base, control_url)

    def service_url(name: str) -> str:
        for service_type, url in services.items():
            if f":service:{name}:" in service_type:
                return url
        return ""

    parsed_location = urllib.parse.urlparse(location)
    udn = _child_text(device_node, "UDN")
    return DlnaDevice(
        uuid=udn.removeprefix("uuid:"),
        friendly_name=_child_text(device_node, "friendlyName") or parsed_location.hostname or "DLNA 设备",
        manufacturer=_child_text(device_node, "manufacturer"),
        model_name=_child_text(device_node, "modelName"),
        location=location,
        host=parsed_location.hostname or "",
        av_transport_url=service_url("AVTransport"),
        rendering_control_url=service_url("RenderingControl"),
        connection_manager_url=service_url("ConnectionManager"),
    )


def _search_message(target: str) -> bytes:
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        f"ST: {target}\r\n"
        "USER-AGENT: TubeUltimatePlayer/0.2 UPnP/1.0\r\n"
        "\r\n"
    ).encode("ascii")


def _child_text(node: ET.Element, name: str) -> str:
    for child in node.iter():
        if _local_name(child.tag) == name:
            return str(child.text or "").strip()
    return ""


def _direct_child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local_name(child.tag) == name:
            return str(child.text or "").strip()
    return ""


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]
