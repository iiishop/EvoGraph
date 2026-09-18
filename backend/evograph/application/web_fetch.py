"""Provider-independent extraction of static public page text."""

from html.parser import HTMLParser

from ..infrastructure.public_web import download


class PageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.title = [], []
        self.hidden = []
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript", "template", "svg"}:
            self.hidden.append(tag)
        if tag == "title":
            self.in_title = True
        if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr", "section", "pre"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag == "title":
            self.in_title = False
        if tag in {"p", "div", "li", "section", "pre"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            if self.in_title:
                self.title.append(data)
            else:
                self.parts.append(data)


def fetch(url):
    final_url, content_type, content = download(url)
    title = final_url
    if content_type in {"text/html", "application/xhtml+xml"}:
        parser = PageText()
        parser.feed(content)
        title = " ".join("".join(parser.title).split()) or final_url
        content = "\n".join(
            line.strip() for line in "".join(parser.parts).splitlines() if line.strip()
        )
    if not content.strip():
        raise ValueError("网页没有可读取正文，可能需要 JavaScript 或登录")
    truncated = len(content) > 20000
    if truncated:
        content = content[:20000] + "\n[正文已截断]"
    return [{"title": title[:500], "url": final_url, "excerpt": content}]
