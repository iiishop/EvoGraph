import httpx

from . import register


@register
class Tavily:
    descriptor = {
        "id": "tavily",
        "name": "Tavily",
        "description": "网页搜索与正文提取",
        "fields": [],
        "secret_label": "Tavily API Key",
    }

    def search(self, config, secret, query):
        if not secret:
            raise ValueError("请在设置中填写 Tavily API Key")
        response = httpx.post(
            "https://api.tavily.com/search",
            headers={"Authorization": "Bearer " + secret},
            json={
                "query": query,
                "max_results": 5,
                "search_depth": "basic",
                "include_raw_content": False,
            },
            timeout=30,
        )
        response.raise_for_status()
        return [
            {"title": r.get("title", ""), "url": r["url"], "excerpt": r.get("content", "")[:4000]}
            for r in response.json().get("results", [])[:5]
        ]

    def extract(self, config, secret, url):
        if not secret:
            raise ValueError("请在设置中填写 Tavily API Key")
        response = httpx.post(
            "https://api.tavily.com/extract",
            headers={"Authorization": "Bearer " + secret},
            json={"urls": [url], "extract_depth": "basic"},
            timeout=30,
        )
        response.raise_for_status()
        return [
            {
                "title": r.get("url", url),
                "url": r.get("url", url),
                "excerpt": r.get("raw_content", "")[:12000],
            }
            for r in response.json().get("results", [])[:1]
        ]
