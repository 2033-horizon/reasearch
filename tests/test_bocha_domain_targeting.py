"""Ticket 04 regression suite: BoCha domain targeting.

BoChaSearch must translate ``query_domains`` and Google-style ``site:``
tokens into the BoCha ``include`` parameter (``|``-joined hosts, max 100),
normalizing domains and leaving the request body unchanged when there is
nothing to include. Mocked HTTP only.
"""

import importlib.util
import os
import pathlib
from unittest.mock import MagicMock, patch

import requests

_BOCHA_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "gpt_researcher"
    / "retrievers"
    / "bocha"
    / "bocha.py"
)
_spec = importlib.util.spec_from_file_location("_bocha_domain_under_test", _BOCHA_PATH)
_bocha = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_bocha)
BoChaSearch = _bocha.BoChaSearch


def _make_search(query, query_domains=None):
    with patch.dict(os.environ, {"BOCHA_API_KEY": "test-key"}):
        return BoChaSearch(query, query_domains=query_domains)


def _run_search(search):
    """Run search() against a mocked successful empty response, return body."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"data": {"webPages": {"value": []}}}
    with patch.object(_bocha.requests, "post", return_value=resp) as post:
        results = search.search()
    assert results == []
    return post.call_args.kwargs["json"]


def test_query_domains_are_normalized_and_piped():
    search = _make_search(
        "新能源汽车销量",
        query_domains=[
            "https://www.stats.gov.cn/sj/zxfb/",
            "moa.gov.cn:443/path",
            "http://people.com.cn",
        ],
    )
    body = _run_search(search)

    assert body["include"] == "www.stats.gov.cn|moa.gov.cn|people.com.cn"
    assert body["query"] == "新能源汽车销量"


def test_site_operator_is_extracted_and_stripped():
    search = _make_search("2024年数据 site:stats.gov.cn 报告")
    body = _run_search(search)

    assert body["include"] == "stats.gov.cn"
    assert "site:" not in body["query"]
    assert body["query"] == "2024年数据 报告"


def test_site_operator_merges_with_query_domains_and_dedupes():
    search = _make_search(
        "销量 site:stats.gov.cn site:https://stats.gov.cn/other",
        query_domains=["moa.gov.cn"],
    )
    body = _run_search(search)

    assert body["include"] == "moa.gov.cn|stats.gov.cn"


def test_include_is_capped_at_100_domains():
    search = _make_search("q", query_domains=[f"d{i}.example" for i in range(105)])
    body = _run_search(search)

    assert len(body["include"].split("|")) == 100
    assert body["include"].split("|")[0] == "d0.example"


def test_empty_normalized_domains_omit_include_field():
    search = _make_search("query", query_domains=["https://", "/path/only", "   "])
    body = _run_search(search)

    assert "include" not in body
    assert body == {
        "query": "query",
        "freshness": "noLimit",
        "summary": True,
        "count": 7,
    }


def test_no_domains_request_body_is_unchanged():
    search = _make_search("query")
    body = _run_search(search)

    assert "include" not in body
    assert body["query"] == "query"


def test_request_exception_still_returns_empty_list():
    search = _make_search("query", query_domains=["stats.gov.cn"])
    with patch.object(
        _bocha.requests, "post", side_effect=requests.RequestException("boom")
    ):
        assert search.search() == []
