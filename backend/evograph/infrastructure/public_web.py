"""Bounded public HTTP reads, with DNS-pinned connections and checked redirects."""

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

MAX_BYTES = 2_000_000


def public_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("网页地址必须为不含凭据的公开 HTTP(S) 地址")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("网页资料必须使用公开地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("网页资料不能引用私有网络地址")


def public_addresses(host, port):
    try:
        addresses = list(
            dict.fromkeys(
                item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            )
        )
    except OSError as exc:
        raise ValueError("无法解析网页地址") from exc
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("网页地址解析到了非公开网络")
    return addresses


def download(url):
    """Return final public URL, content type and decoded body. Never execute scripts."""
    with httpx.Client(timeout=20, trust_env=False, follow_redirects=False) as client:
        for _ in range(6):
            public_url(url)
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            addresses = public_addresses(parsed.hostname, port)
            # Connect to the validated address, not a second DNS lookup. Preserve
            # the real hostname for HTTP virtual hosts and TLS verification/SNI.
            address = addresses[0]
            netloc = f"[{address}]" if ":" in address else address
            target = urlunsplit((parsed.scheme, f"{netloc}:{port}", parsed.path, parsed.query, ""))
            with client.stream(
                "GET",
                target,
                headers={
                    "Host": parsed.netloc,
                    "User-Agent": "EvoGraph/0.1 (public web reader)",
                    "Accept": "text/html,application/xhtml+xml,application/xml,text/plain,application/json",
                },
                extensions={"sni_hostname": parsed.hostname},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("网页重定向缺少目标地址")
                    url = urljoin(url, location)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].lower()
                allowed = content_type.startswith("text/") or content_type in {
                    "application/xhtml+xml",
                    "application/xml",
                    "application/rss+xml",
                    "application/json",
                }
                if not allowed:
                    raise ValueError("基础网页读取仅支持 HTML、文本、XML 和 JSON")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("网页正文超过 2 MB，请读取更具体的页面")
                return (
                    url,
                    content_type,
                    bytes(body).decode(response.encoding or "utf-8", errors="replace"),
                )
    raise ValueError("网页重定向次数过多")
