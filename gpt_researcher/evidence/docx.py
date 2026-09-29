"""Real Word footnotes for evidence-driven reports (ticket 11, ADR/self-built).

The report Markdown carries ``[^n]`` markers plus a definition block; Word
itself has no markdown, so before HTML conversion the definitions are lifted
out and markers become private placeholders. After python-docx saves the
file, this module injects the OOXML footnote part (``word/footnotes.xml``),
its hyperlink relationships, the content-type override and the body
``w:footnoteReference`` runs — no pandoc or other external tool involved.

Footnote text contract: ``标题 — 发布主体（等级）· 日期 · URL`` with the URL
rendered as a clickable hyperlink. Every body occurrence gets its own
footnote id (content may repeat), matching the markdown numbering.
"""

from __future__ import annotations

import copy
import logging
import os
import re
import zipfile
from xml.sax.saxutils import escape

from lxml import etree

from .writing import CITATION_TOKEN_RE

logger = logging.getLogger(__name__)

MARKER_TEMPLATE = "\u27e6FN:{number}\u27e7"
MARKER_RE = re.compile(r"\u27e6FN:(\d+)\u27e7")
MARKER_SPLIT_RE = re.compile(r"(\u27e6FN:\d+\u27e7)")
_URL_RE = re.compile(r"(https?://\S+?)\s*$")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_NS = "http://www.w3.org/XML/1998/namespace"

FOOTNOTES_REL_TYPE = f"{R_NS}/footnotes"
HYPERLINK_REL_TYPE = f"{R_NS}/hyperlink"
FOOTNOTES_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
)


def _qn(tag: str) -> str:
    prefix, _, local = tag.partition(":")
    return f"{{{W_NS}}}{local}" if prefix == "w" else tag


def split_footnote_definitions(markdown: str) -> tuple[str, dict[int, str]]:
    """Lift ``[^n]: text`` definition lines out of the report body."""
    definitions: dict[int, str] = {}
    body_lines: list[str] = []
    for line in (markdown or "").splitlines():
        match = re.match(r"^\[\^(\d+)\]:\s*(.+)$", line.strip())
        if match:
            definitions[int(match.group(1))] = match.group(2).strip()
        else:
            body_lines.append(line)
    return "\n".join(body_lines), definitions


def mark_footnote_references(body: str, definitions: dict[int, str]) -> str:
    """Replace ``[^n]`` markers with placeholders; drop undefined numbers."""
    def _replace(match: re.Match) -> str:
        number = int(match.group(1))
        if number not in definitions:
            return ""
        return MARKER_TEMPLATE.format(number=number)

    return CITATION_TOKEN_RE.sub(_replace, body or "")


def _text_run_like(run_el: etree._Element, text: str) -> etree._Element:
    run = etree.Element(_qn("w:r"))
    rpr = run_el.find(_qn("w:rPr"))
    if rpr is not None:
        run.append(copy.deepcopy(rpr))
    text_el = etree.SubElement(run, _qn("w:t"))
    text_el.set(f"{{{XML_NS}}}space", "preserve")
    text_el.text = text
    return run


def _reference_run(footnote_id: int) -> etree._Element:
    run = etree.Element(_qn("w:r"))
    rpr = etree.SubElement(run, _qn("w:rPr"))
    vert = etree.SubElement(rpr, _qn("w:vertAlign"))
    vert.set(_qn("w:val"), "superscript")
    reference = etree.SubElement(run, _qn("w:footnoteReference"))
    reference.set(_qn("w:id"), str(footnote_id))
    return run


def _inject_references(
    document_root: etree._Element, definitions: dict[int, str]
) -> dict[int, str]:
    """Swap placeholders for footnote references; returns id -> footnote text."""
    occurrences: dict[int, str] = {}
    next_id = 1
    for text_el in list(document_root.iter(_qn("w:t"))):
        text = text_el.text or ""
        if MARKER_RE.search(text) is None:
            continue
        run_el = text_el.getparent()
        if run_el is None:
            continue
        parent = run_el.getparent()
        if parent is None:
            continue
        parts = MARKER_SPLIT_RE.split(text)
        new_elements: list[etree._Element] = []
        for part in parts:
            match = MARKER_RE.fullmatch(part)
            if match is not None:
                number = int(match.group(1))
                if number not in definitions:
                    continue
                occurrences[next_id] = definitions[number]
                new_elements.append(_reference_run(next_id))
                next_id += 1
            elif part:
                new_elements.append(_text_run_like(run_el, part))
        index = list(parent).index(run_el)
        for offset, element in enumerate(new_elements):
            parent.insert(index + offset, element)
        parent.remove(run_el)
    return occurrences


def _footnote_paragraph(footnote_id: int, text: str, hyperlink_rid: str | None) -> str:
    match = _URL_RE.search(text)
    prefix = text
    url = ""
    if match:
        url = match.group(1)
        prefix = text[: match.start(1)].rstrip()
    prefix_xml = (
        f'<w:r><w:t xml:space="preserve">{escape(prefix)} </w:t></w:r>'
        if prefix
        else ""
    )
    hyperlink_xml = ""
    if url and hyperlink_rid:
        hyperlink_xml = (
            f'<w:hyperlink r:id="{hyperlink_rid}" w:history="1"><w:r>'
            f'<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape(url)}</w:t></w:r></w:hyperlink>'
        )
    elif url:
        hyperlink_xml = f'<w:r><w:t xml:space="preserve">{escape(url)}</w:t></w:r>'
    return (
        f'<w:footnote w:id="{footnote_id}"><w:p>'
        f'<w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:footnoteRef/></w:r>'
        f"{prefix_xml}{hyperlink_xml}"
        f"</w:p></w:footnote>"
    )


def _build_footnotes_xml(
    occurrences: dict[int, str], rels: dict[str, str]
) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<w:footnotes xmlns:w="{W_NS}" xmlns:r="{R_NS}">',
        '<w:footnote w:type="separator" w:id="-1"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>',
        '<w:footnote w:type="continuationSeparator" w:id="0"><w:p><w:r>'
        "<w:continuationSeparator/></w:r></w:p></w:footnote>",
    ]
    for footnote_id in sorted(occurrences):
        url_match = _URL_RE.search(occurrences[footnote_id])
        hyperlink_rid = None
        if url_match:
            rid = f"rIdHyperlink{footnote_id}"
            rels[rid] = url_match.group(1)
            hyperlink_rid = rid
        parts.append(_footnote_paragraph(footnote_id, occurrences[footnote_id], hyperlink_rid))
    parts.append("</w:footnotes>")
    return "".join(parts).encode("utf-8")


def _build_rels_xml(rels: dict[str, str]) -> bytes:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        f'<Relationships xmlns="{PKG_REL_NS}">',
    ]
    for rid, target in rels.items():
        parts.append(
            f'<Relationship Id="{escape(rid)}" Type="{HYPERLINK_REL_TYPE}"'
            f' Target="{escape(target)}" TargetMode="External"/>'
        )
    parts.append("</Relationships>")
    return "".join(parts).encode("utf-8")


def _ensure_content_type(entries: dict[str, bytes]) -> None:
    name = "[Content_Types].xml"
    root = etree.fromstring(entries[name])
    for override in root.findall(f"{{{CT_NS}}}Override"):
        if override.get("PartName") == "/word/footnotes.xml":
            return
    override = etree.SubElement(root, f"{{{CT_NS}}}Override")
    override.set("PartName", "/word/footnotes.xml")
    override.set("ContentType", FOOTNOTES_CONTENT_TYPE)
    entries[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _ensure_document_relationship(entries: dict[str, bytes]) -> None:
    name = "word/_rels/document.xml.rels"
    root = etree.fromstring(entries[name])
    for rel in root:
        if rel.get("Type") == FOOTNOTES_REL_TYPE:
            return
    used_ids = {rel.get("Id") for rel in root}
    index = 1
    while f"rIdFootnotes{index}" in used_ids:
        index += 1
    rel = etree.SubElement(root, f"{{{PKG_REL_NS}}}Relationship")
    rel.set("Id", f"rIdFootnotes{index}")
    rel.set("Type", FOOTNOTES_REL_TYPE)
    rel.set("Target", "footnotes.xml")
    entries[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def inject_footnotes(docx_path: str, definitions: dict[int, str]) -> bool:
    """Inject real Word footnotes into an existing .docx; returns True if any."""
    if not definitions:
        return False
    try:
        with zipfile.ZipFile(docx_path, "r") as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        if "word/document.xml" not in entries:
            return False
        document = etree.fromstring(entries["word/document.xml"])
        occurrences = _inject_references(document, definitions)
        if not occurrences:
            return False
        entries["word/document.xml"] = etree.tostring(
            document, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        if entries.get("word/footnotes.xml"):
            # Injection targets the reports this module converts itself; an
            # existing part means a previous injection and is replaced so body
            # references and note ids stay consistent.
            logger.warning("Replacing existing footnotes part in %s", docx_path)
        rels: dict[str, str] = {}
        entries["word/footnotes.xml"] = _build_footnotes_xml(occurrences, rels)
        entries["word/_rels/footnotes.xml.rels"] = _build_rels_xml(rels)
        _ensure_content_type(entries)
        _ensure_document_relationship(entries)

        tmp_path = f"{docx_path}.tmp"
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in entries.items():
                archive.writestr(name, payload)
        os.replace(tmp_path, docx_path)
        return True
    except Exception as exc:
        logger.warning("Footnote injection failed for %s: %s", docx_path, exc)
        return False
