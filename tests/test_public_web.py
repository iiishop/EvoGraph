import socket

import httpx
import pytest
from evograph.agent_tools import tools
from evograph.agent_tools.base import ToolContext
from evograph.agent_tools.web import URL, web_fetch
from evograph.application.web_fetch import fetch
from evograph.infrastructure import public_web
from evograph.web_search.bing import Bing


def test_bing_results_and_blocked_response(monkeypatch):
    monkeypatch.setattr(
        "evograph.web_search.bing.download",
        lambda _: (
            "https://cn.bing.com/search",
            "text/xml",
            "<rss><channel><item><title>Python</title><link>https://python.org/</link>"
            "<description>Official &amp; current</description></item></channel></rss>",
        ),
    )
    assert Bing().search({}, "", "Python")[0]["excerpt"] == "Official & current"
    monkeypatch.setattr(
        "evograph.web_search.bing.download", lambda _: ("", "text/plain", "Ref A: blocked")
    )
    with pytest.raises(ValueError, match="验证或已限流"):
        Bing().search({}, "", "Python")


def test_fetch_html_text_and_truncation(monkeypatch):
    monkeypatch.setattr(
        "evograph.application.web_fetch.download",
        lambda _: (
            "https://example.com/final",
            "text/html",
            "<title>Example &amp; Docs</title><script>secret script</script><style>bad style</style>"
            "<h1>Title</h1><p>Actual <b>body</b>.</p>",
        ),
    )
    result = fetch("https://example.com")[0]
    assert result["title"] == "Example & Docs"
    assert "Actual body." in result["excerpt"]
    assert "script" not in result["excerpt"] and "style" not in result["excerpt"]
    assert result["url"].endswith("/final")
    monkeypatch.setattr(
        "evograph.application.web_fetch.download",
        lambda _: ("https://example.com", "text/plain", "a" * 21000),
    )
    assert fetch("https://example.com")[0]["excerpt"].endswith("[正文已截断]")


def test_fetch_is_independent_and_cached_as_citable_research(app, planned, monkeypatch):
    app.research.configure("")
    monkeypatch.setattr(
        "evograph.application.research.fetch",
        lambda _: [
            {
                "title": "Documentation",
                "url": "https://example.com/docs",
                "excerpt": "Public documentation",
            }
        ],
    )
    ctx = ToolContext(planned.id, app)
    result = web_fetch(ctx, URL(url="https://example.com/docs"))
    assert result["sources"][0]["id"] == app.db.get(planned.id).research[-1].id
    assert web_fetch(ctx, URL(url="https://example.com/docs"))["status"] == "NO_PROGRESS"
    assert len(app.db.get(planned.id).research) == 1
    assert {"web_search", "web_fetch", "web_extract"} <= tools().keys()
    with pytest.raises(ValueError, match="已关闭"):
        app.research.run(planned.id, "Python")


def test_default_search_needs_no_credentials(app, planned, monkeypatch):
    from evograph.web_search import providers

    assert app.research.settings()["saved"]["provider"] == "bing"
    monkeypatch.setattr(
        providers()["bing"],
        "search",
        lambda *_: [{"title": "Docs", "url": "https://python.org", "excerpt": "Python"}],
    )
    assert app.research.run(planned.id, "Python")["sources"]


def transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(public_web, "public_addresses", lambda *_: ["93.184.216.34"])
    monkeypatch.setattr(
        public_web.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_redirect_checks_and_connection_pinning(monkeypatch):
    visited = []

    def handler(request):
        visited.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})

    transport(monkeypatch, handler)
    with pytest.raises(ValueError, match="私有网络"):
        public_web.download("https://example.com")
    assert len(visited) == 1
    assert visited[0].url.host == "93.184.216.34"
    assert visited[0].headers["host"] == "example.com"
    assert visited[0].extensions["sni_hostname"] == "example.com"


def test_dns_private_network_and_bounded_response(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_, **__: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="非公开网络"):
        public_web.public_addresses("example.com", 443)
    transport(
        monkeypatch,
        lambda _: httpx.Response(
            200, headers={"content-type": "text/plain"}, content=b"x" * (public_web.MAX_BYTES + 1)
        ),
    )
    with pytest.raises(ValueError, match="2 MB"):
        public_web.download("https://example.com")
