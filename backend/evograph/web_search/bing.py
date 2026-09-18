"""Basic Bing RSS search; no paid API or credential required."""

from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx

from . import register


def download(url):
    # Search uses one trusted vendor endpoint. Unlike arbitrary web_fetch URLs,
    # it needs the original hostname URL for Bing's edge routing.
    with httpx.stream(
        "GET",
        url,
        timeout=20,
        follow_redirects=False,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; EvoGraph/0.1)",
            "Accept": "application/rss+xml,application/xml,text/xml",
        },
    ) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            data.extend(chunk)
            if len(data) > 2_000_000:
                raise ValueError("Bing 搜索响应过大")
        return (
            str(response.url),
            response.headers.get("content-type", ""),
            bytes(data).decode("utf-8", errors="replace"),
        )


@register
class Bing:
    descriptor = {
        "id": "bing",
        "name": "Bing（基础搜索，无需密钥）",
        "description": "读取 Bing 公开搜索摘要，无需 API Key；如遇限流或验证，可改用 Tavily / SearXNG。",
        "fields": [],
        "secret_label": "",
    }

    def search(self, config, secret, query):
        _, _, content = download(
            "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})
        )
        if "<!DOCTYPE" in content.upper() or "<!ENTITY" in content.upper():
            raise ValueError("搜索响应不是受支持的 RSS 格式")
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as exc:
            raise ValueError("Bing 暂未返回搜索结果，可能需要验证或已限流；可切换搜索服务") from exc
        if root.tag != "rss":
            raise ValueError("Bing 未返回 RSS 搜索结果，请稍后重试或切换服务")
        return [
            {
                "title": item.findtext("title", ""),
                "url": item.findtext("link", ""),
                "excerpt": item.findtext("description", "")[:4000],
            }
            for item in root.findall("./channel/item")[:5]
        ]
