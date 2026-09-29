"""Domain normalization helpers shared by retrievers and the evidence layer."""

from urllib.parse import urlsplit


def normalize_domain(value: str | None) -> str:
    """Normalize a URL or bare host into a lowercase host without port/path.

    Accepts full URLs (``https://www.stats.gov.cn:443/path``), scheme-less
    hosts (``stats.gov.cn:8080/a``) and plain domains (``Stats.Gov.CN``).
    Returns ``""`` when no host can be extracted.
    """
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if "://" not in text:
        text = "//" + text
    host = urlsplit(text).hostname or ""
    return host.rstrip(".").lower()
