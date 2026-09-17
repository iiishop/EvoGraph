import httpx

from . import register


@register
class SearXNG:
    descriptor = {
        "id": "searxng",
        "name": "SearXNG",
        "description": "自托管搜索，需开启 JSON 格式；不支持正文提取",
        "fields": [
            {
                "key": "base_url",
                "label": "SearXNG 地址",
                "default": "http://127.0.0.1:8080",
                "placeholder": "https://search.example.com",
                "required": True,
            }
        ],
        "secret_label": "",
    }

    def search(self, config, secret, query):
        response = httpx.get(
            config["base_url"].rstrip("/") + "/search",
            params={"q": query, "format": "json"},
            timeout=30,
            follow_redirects=False,
        )
        response.raise_for_status()
        return [
            {"title": r.get("title", ""), "url": r["url"], "excerpt": r.get("content", "")[:4000]}
            for r in response.json().get("results", [])[:5]
        ]
