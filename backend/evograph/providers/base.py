from typing import Protocol

REGISTRY = {}


class Adapter(Protocol):
    descriptor: dict

    async def complete(
        self, config: dict, secret: str, messages: list[dict]
    ) -> tuple[str, int]: ...


def register(cls):
    REGISTRY[cls.descriptor["id"]] = cls()
    return cls


def check_url(url: str):
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
    ):
        raise ValueError("请输入有效的 HTTP(S) Base URL，勿在地址中放置凭据")
    if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("远程 Provider 必须使用 HTTPS；本地服务可使用 HTTP")
