# BoCha Search Retriever

# libraries
import os
import re
import requests
import json
import logging

from gpt_researcher.utils.domains import normalize_domain

# Google-style site:domain operators, which the BoCha web-search API does not
# support (it would treat "site:" as literal query text).
_SITE_OPERATOR_PATTERN = re.compile(r"site:(\S+)", re.IGNORECASE)

_MAX_INCLUDE_DOMAINS = 100


class BoChaSearch():
    """
    BoCha Search Retriever
    """

    def __init__(self, query, query_domains=None):
        """
        Initializes the BoChaSearch object
        Args:
            query:
        """
        self.query = query
        self.query_domains = query_domains or None
        self.api_key = os.environ["BOCHA_API_KEY"]

    def _build_payload(self, max_results: int) -> dict:
        """Build the request body, translating domain targeting to ``include``.

        ``query_domains`` and any site: operators in the query are normalized
        to hosts, de-duplicated (order preserved), capped at 100 and joined
        with ``|``. When nothing remains the field is omitted entirely, so
        calls without domain targeting keep the previous request shape.
        """
        query = self.query or ""
        include = [normalize_domain(domain) for domain in (self.query_domains or [])]

        site_tokens = _SITE_OPERATOR_PATTERN.findall(query)
        if site_tokens:
            query = _SITE_OPERATOR_PATTERN.sub("", query)
            query = re.sub(r"\s+", " ", query).strip()
            include.extend(normalize_domain(token.strip(",")) for token in site_tokens)

        include = list(dict.fromkeys(domain for domain in include if domain))
        include = include[:_MAX_INCLUDE_DOMAINS]

        data = {
            "query": query,
            "freshness": "noLimit",  # 搜索的时间范围，
            "summary": True,  # 是否返回长文本摘要
            "count": max_results,
        }
        if include:
            data["include"] = "|".join(include)
        return data

    def search(self, max_results=7) -> list[dict[str]]:
        """
        Searches the query
        Returns:

        """
        url = 'https://api.bochaai.com/v1/web-search'
        headers = {
            'Authorization': f'Bearer {self.api_key}',  # 请替换为你的API密钥
            'Content-Type': 'application/json'
        }
        data = self._build_payload(max_results)

        try:
            response = requests.post(url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            json_response = response.json()
        except (requests.RequestException, ValueError) as e:
            logging.getLogger(__name__).warning(
                f"Error: {e}. Failed fetching sources. Resulting in empty response."
            )
            return []

        # The BoCha response shape is data.webPages.value; any of these may be
        # missing on an error/empty payload, so walk it defensively rather than
        # KeyError-ing the whole research run.
        results = (
            ((json_response or {}).get("data") or {}).get("webPages") or {}
        ).get("value") or []
        if not isinstance(results, list):
            return []

        search_results = []

        if not isinstance(results, list):
            return []

        # Normalize the results to match the format of the other search APIs.
        # Skip non-dict rows / empty URLs; default missing fields to "".
        for result in results:
            if not isinstance(result, dict):
                continue
            href = result.get("url") or ""
            if not href:
                continue
            search_results.append(
                {
                    "title": result.get("name") or "",
                    "href": href,
                    "body": result.get("snippet") or "",
                }
            )

        return search_results