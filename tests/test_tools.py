"""Tests for livery.runtimes.tools — mocks urllib for web_fetch / web_search."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from livery.runtimes import tools


def _mock_urlopen(body: bytes, content_type: str = "text/html; charset=utf-8") -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__.return_value.read.return_value = body
    ctx.__enter__.return_value.headers.get.return_value = content_type
    ctx.__exit__.return_value = False
    return MagicMock(return_value=ctx)


# ---------------------------------------------------------------------------
# html_to_text
# ---------------------------------------------------------------------------


def test_html_to_text_strips_scripts_styles_and_tags():
    html = """
    <html>
      <head><style>body { color: red }</style></head>
      <body>
        <script>alert('x')</script>
        <h1>Hello &amp; Welcome</h1>
        <p>This is a test.</p>
      </body>
    </html>
    """
    out = tools.html_to_text(html)
    assert "alert" not in out
    assert "color: red" not in out
    assert "Hello & Welcome" in out
    assert "This is a test." in out


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------


def test_web_fetch_rejects_non_http_urls():
    out = tools.web_fetch("file:///etc/passwd")
    assert out.startswith("error:")


def test_web_fetch_returns_stripped_text():
    html = b"<html><body><h1>Brand</h1><p>About us.</p></body></html>"
    with patch("urllib.request.urlopen", _mock_urlopen(html)):
        out = tools.web_fetch("https://example.com")
    assert "Brand" in out
    assert "About us." in out
    assert "<" not in out


def test_web_fetch_truncates_long_bodies():
    big = ("x" * 50_000).encode("utf-8")
    with patch("urllib.request.urlopen", _mock_urlopen(big, content_type="text/plain")):
        out = tools.web_fetch("https://example.com", max_chars=1000)
    assert "truncated" in out
    assert len(out) < 1500  # 1000 + truncation notice


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


def test_web_search_parses_ddg_results():
    fake_html = b"""
    <html><body>
      <a class="result__a" href="/l/?uddg=https%3A%2F%2Fbrand.com">Brand Homepage</a>
      <a class="result__snippet">Brand is a microbrand watchmaker.</a>
      <a class="result__a" href="https://direct.example.com">Direct</a>
      <a class="result__snippet">Another result.</a>
    </body></html>
    """
    with patch("urllib.request.urlopen", _mock_urlopen(fake_html)):
        raw = tools.web_search("brand")
    results = json.loads(raw)
    assert isinstance(results, list)
    assert results[0]["url"] == "https://brand.com"
    assert "Brand Homepage" in results[0]["title"]
    assert results[0]["snippet"].startswith("Brand is a microbrand")
    assert results[1]["url"] == "https://direct.example.com"


def test_web_search_no_results_returns_warning_json():
    empty = b"<html><body>no results</body></html>"
    with patch("urllib.request.urlopen", _mock_urlopen(empty)):
        raw = tools.web_search("obscure query that matches nothing")
    parsed = json.loads(raw)
    assert "warning" in parsed


# ---------------------------------------------------------------------------
# execute_tool_call
# ---------------------------------------------------------------------------


def test_execute_tool_call_unknown_tool():
    out = tools.execute_tool_call("not_a_tool", {})
    assert out.startswith("error: unknown tool")


def test_execute_tool_call_routes_to_web_fetch():
    body = b"<html><body><p>routed body</p></body></html>"
    with patch("urllib.request.urlopen", _mock_urlopen(body)):
        out = tools.execute_tool_call("web_fetch", {"url": "https://x.com"})
    assert "routed body" in out


def test_execute_tool_call_bad_args():
    out = tools.execute_tool_call("web_fetch", {"wrong_key": "x"})
    assert out.startswith("error: bad arguments")


def test_tool_schemas_is_openai_shaped():
    schemas = tools.tool_schemas()
    assert all(s["type"] == "function" for s in schemas)
    names = [s["function"]["name"] for s in schemas]
    assert "web_fetch" in names
    assert "web_search" in names
