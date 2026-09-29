"""Ticket 11 regression suite: real DOCX footnotes (self-built injection).

Converts the evidence-driven markdown contract (``[^n]`` + definitions) to
.docx through the existing backend conversion exit, then unzips the result and
asserts the OOXML footnote part, body references, hyperlink relationships and
content types. No Word installation required; no-footnote input must stay
byte-identical in structure (no footnote part added).
"""

import urllib.parse
import zipfile

import pytest

from backend.utils import write_md_to_word
from gpt_researcher.evidence.docx import (
    FOOTNOTES_CONTENT_TYPE,
    FOOTNOTES_REL_TYPE,
    HYPERLINK_REL_TYPE,
    mark_footnote_references,
    split_footnote_definitions,
)

FOOTNOTE_ONE = "统计公报 — 国家统计局（A）· 2026-09-29 · https://stats.gov.cn/x"
FOOTNOTE_TWO = "行业观察 — 某行业媒体（C）· 2026-09-28 · https://media.example/y"

MARKDOWN = (
    "# 新能源报告\n\n"
    "销量为100万辆[^1]，再次引用销量[^1]。出口为50万辆[^2]。未知编号[^9]。\n\n"
    f"[^1]: {FOOTNOTE_ONE}\n"
    f"[^2]: {FOOTNOTE_TWO}\n"
)


def test_split_and_mark_helpers():
    body, definitions = split_footnote_definitions(MARKDOWN)

    assert definitions == {1: FOOTNOTE_ONE, 2: FOOTNOTE_TWO}
    assert "[^1]:" not in body

    marked = mark_footnote_references(body, definitions)
    assert marked.count("\u27e6FN:1\u27e7") == 2
    assert marked.count("\u27e6FN:2\u27e7") == 1
    assert "[^9]" not in marked


@pytest.mark.asyncio
async def test_docx_contains_real_footnotes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = urllib.parse.unquote(await write_md_to_word(MARKDOWN, "report"))
    assert path

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        document = archive.read("word/document.xml").decode("utf-8")
        footnotes = archive.read("word/footnotes.xml").decode("utf-8")
        rels = archive.read("word/_rels/footnotes.xml.rels").decode("utf-8")
        content_types = archive.read("[Content_Types].xml").decode("utf-8")
        document_rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")

    assert "word/footnotes.xml" in names
    assert 'w:type="separator"' in footnotes
    assert 'w:type="continuationSeparator"' in footnotes

    # One reference per body occurrence: [^1] twice + [^2] once = 3 notes,
    # each with its own id and identical content for repeated citations.
    assert document.count("footnoteReference") == 3
    for footnote_id in ("1", "2", "3"):
        assert f'w:id="{footnote_id}"' in document
        assert f'<w:footnote w:id="{footnote_id}">' in footnotes
    # Footnote text is the contract (prefix + URL as a hyperlink run).
    assert footnotes.count("统计公报 — 国家统计局（A）· 2026-09-29") == 2
    assert footnotes.count("https://stats.gov.cn/x") == 2
    assert footnotes.count("行业观察 — 某行业媒体（C）· 2026-09-28") == 1
    assert HYPERLINK_REL_TYPE in rels
    assert 'Target="https://stats.gov.cn/x" TargetMode="External"' in rels
    assert "<w:hyperlink" in footnotes

    # The markdown artifacts must not leak into the document body.
    assert "[^1]" not in document
    assert "\u27e6FN:" not in document
    assert "统计公报 —" not in document

    assert FOOTNOTES_CONTENT_TYPE in content_types
    assert FOOTNOTES_REL_TYPE in document_rels

    # The rewritten package still parses as a normal .docx document.
    from docx import Document as DocxDocument

    reopened = DocxDocument(path)
    body_text = "\n".join(paragraph.text for paragraph in reopened.paragraphs)
    assert "销量为100万辆" in body_text


@pytest.mark.asyncio
async def test_docx_without_footnotes_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = urllib.parse.unquote(
        await write_md_to_word("# 普通报告\n\n没有任何脚注的数据 100 万辆。", "plain")
    )

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        document = archive.read("word/document.xml").decode("utf-8")

    assert "word/footnotes.xml" not in names
    assert "footnoteReference" not in document
    assert "[^" not in document


@pytest.mark.asyncio
async def test_footnote_without_url_has_no_hyperlink(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    markdown = "数据[^1]。\n\n[^1]: 某来源 — 某媒体（D）· 2026-09-01\n"
    path = urllib.parse.unquote(await write_md_to_word(markdown, "nourl"))

    with zipfile.ZipFile(path) as archive:
        footnotes = archive.read("word/footnotes.xml").decode("utf-8")
        rels = archive.read("word/_rels/footnotes.xml.rels").decode("utf-8")

    assert "某来源 — 某媒体（D）· 2026-09-01" in footnotes
    assert "<w:hyperlink" not in footnotes
    assert HYPERLINK_REL_TYPE not in rels
