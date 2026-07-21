import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

from app.domain.exceptions import InvalidUrlError

URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def extract_urls(text: str) -> list[str]:
    return [match.rstrip(".,;:!?)]}") for match in URL_RE.findall(text)]


def validate_public_url(url: str, allowed_hosts: set[str] | None = None) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise InvalidUrlError("Некорректный URL")
    host = parsed.hostname.rstrip(".").lower()
    if allowed_hosts and not any(host == item or host.endswith(f".{item}") for item in allowed_hosts):
        raise InvalidUrlError("Домен не разрешён")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not address.is_global:
        raise InvalidUrlError("Локальные и служебные адреса запрещены")
    return url


async def ensure_public_dns(url: str) -> None:
    host = urlsplit(url).hostname
    if not host:
        raise InvalidUrlError("URL не содержит домен")
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    if not infos or any(not ipaddress.ip_address(item[4][0]).is_global for item in infos):
        raise InvalidUrlError("Домен указывает на локальный или служебный адрес")
