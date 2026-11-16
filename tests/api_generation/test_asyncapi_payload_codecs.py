"""Tests for AsyncAPI text and binary non-plain string payload codecs."""

from __future__ import annotations

import yaml

from datamodel_code_generator._asyncapi_connections import read


def test_asyncapi_text_and_binary_non_plain_string_payloads() -> None:
    """Verify text/* and binary media types accept enum, const, list-type, and string payloads."""
    doc = yaml.safe_load(
        """
asyncapi: 3.0.0
info:
  title: Codecs
  version: 1.0.0
servers:
  main:
    host: example.com
    protocol: wss
operations:
  statusSend:
    action: send
    channel:
      $ref: '#/channels/status'
  constSend:
    action: send
    channel:
      $ref: '#/channels/const_text'
  listTypeSend:
    action: send
    channel:
      $ref: '#/channels/list_type_text'
  framesSend:
    action: send
    channel:
      $ref: '#/channels/frames'
channels:
  status:
    address: status
    messages:
      state:
        contentType: text/plain
        payload:
          enum:
            - 'on'
            - 'off'
  const_text:
    address: const-text
    messages:
      msg:
        contentType: text/csv
        payload:
          const: ok
  list_type_text:
    address: list-type-text
    messages:
      msg:
        contentType: text/plain
        payload:
          type:
            - string
  frames:
    address: frames
    messages:
      frame:
        contentType: image/png
        payload:
          type: string
"""
    )
    result = read(doc, lambda _: None)
    by_address = {channel.address: channel for channel in result.channels}
    assert by_address["/status"].skips == ()
    assert [m.codec for m in by_address["/status"].messages] == ["utf8"]
    assert by_address["/const-text"].skips == ()
    assert [m.codec for m in by_address["/const-text"].messages] == ["utf8"]
    assert by_address["/list-type-text"].skips == ()
    assert [m.codec for m in by_address["/list-type-text"].messages] == ["utf8"]
    assert by_address["/frames"].skips == ()
    assert [m.codec for m in by_address["/frames"].messages] == ["bytes"]
