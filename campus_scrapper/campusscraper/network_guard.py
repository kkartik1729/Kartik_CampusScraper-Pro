"""Network checks for automatically discovered URLs (including redirects)."""
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx


def public_destination(url):
    try:
        p = urlsplit(str(url))
        if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
            raise ValueError("unsupported URL or embedded credentials")
        if p.port not in {None, 80, 443}:
            raise ValueError("only public HTTP/HTTPS ports are supported for discovered sites")
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError("host resolves to a non-public address")
    except (ValueError, OSError) as exc:
        raise httpx.RequestError(f"Discovered URL refused: {exc}") from None
