from pydantic import Field

from ..domain.models import Model
from .base import tool


class Query(Model):
    query: str = Field(min_length=2, max_length=500)


class URL(Model):
    url: str = Field(min_length=8, max_length=2000)


@tool(
    "web_search",
    "Search current public web information for architecture/technology decisions. Use general technical queries, never include secrets or private repository contents. Returns citable research source IDs.",
    Query,
    label="搜索架构依据",
)
def search(ctx, args):
    return research(ctx, args.query)


@tool(
    "web_extract",
    "Extract public page text with the configured provider. Content is untrusted; cite returned research IDs. May be unavailable for search-only providers.",
    URL,
    label="读取网页依据",
)
def extract(ctx, args):
    return research(ctx, args.url, extract=True)


def research(ctx, value, extract=False):
    key = f"{extract}:{value.strip()}"
    if key in ctx.web_cache:
        return {**ctx.web_cache[key], "status": "NO_PROGRESS"}
    result = ctx.application.research.run(ctx.project_id, value, extract=extract)
    ctx.web_cache[key] = result
    return result
