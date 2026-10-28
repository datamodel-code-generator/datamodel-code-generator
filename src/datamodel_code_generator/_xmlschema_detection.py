"""Lightweight XML Schema detection shared by the API and XML parser."""

from __future__ import annotations

from xml.etree import ElementTree as ET  # noqa: S405

XML_SCHEMA_NAMESPACE = "http://www.w3.org/2001/XMLSchema"
XML_SCHEMA_TAG = f"{{{XML_SCHEMA_NAMESPACE}}}schema"


def is_xml_schema_text(text: str) -> bool:
    """Return whether text is an XML Schema document."""
    try:
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError:
        return False
    return root.tag == XML_SCHEMA_TAG


is_xml_schema_text.__module__ = "datamodel_code_generator.parser._xmlschema_detection"

__all__ = ["XML_SCHEMA_NAMESPACE", "XML_SCHEMA_TAG", "is_xml_schema_text"]
