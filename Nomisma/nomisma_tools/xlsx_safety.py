"""Validate bounded OOXML parts before handing them to workbook readers."""

import codecs
from xml.parsers import expat


class UnsafeWorkbookXML(ValueError):
    pass


def _reject_dtd(*_args):
    raise UnsafeWorkbookXML("Workbook XML cannot contain a DTD or entities")


def _xml_prefix(chunk):
    for bom in (codecs.BOM_UTF32_BE, codecs.BOM_UTF32_LE, codecs.BOM_UTF8,
                codecs.BOM_UTF16_BE, codecs.BOM_UTF16_LE):
        if chunk.startswith(bom):
            chunk = chunk[len(bom):]
            break
    start = chunk.lstrip(b" \t\r\n\0")
    if not start:
        return None
    return start.startswith((b"<", b"\x4c\x6f\xa7\x94"))


def validate_xml_parts(archive):
    """Call after checking the archive's entry count and expanded size."""
    for item in archive.infolist():
        if item.is_dir():
            continue
        try:
            with archive.open(item) as part:
                chunk = part.read(64 * 1024)
                if not item.filename.lower().endswith((".xml", ".rels", ".vml")):
                    # OOXML relationships can reference parts with arbitrary suffixes.
                    while chunk and _xml_prefix(chunk) is None:
                        chunk = part.read(64 * 1024)
                    if not chunk or not _xml_prefix(chunk):
                        continue
                parser = expat.ParserCreate()
                parser.StartDoctypeDeclHandler = _reject_dtd
                parser.Parse(chunk, False)
                while chunk := part.read(64 * 1024):
                    parser.Parse(chunk, False)
                parser.Parse(b"", True)
        except expat.ExpatError as error:
            raise UnsafeWorkbookXML("Workbook contains invalid XML") from error
