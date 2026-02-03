# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,line-too-long
"""Comprehensive tests for local_oig_crc module to achieve 100% coverage."""

import local_oig_crc


def test_crc16_modbus_empty():
    """Test CRC on empty bytes."""
    assert local_oig_crc.crc16_modbus(b"") == 0xFFFF


def test_crc16_modbus_known_values():
    """Test CRC16/MODBUS with known test vectors."""
    # Standard test vector
    assert local_oig_crc.crc16_modbus(b"123456789") == 0x4B37
    # Additional test vectors
    assert local_oig_crc.crc16_modbus(b"A") == 0x707F
    assert local_oig_crc.crc16_modbus(b"Hello") == 0xF377


def test_crc16_table_cached():
    """Test that CRC table is cached."""
    table1 = local_oig_crc._crc16_table_modbus()
    table2 = local_oig_crc._crc16_table_modbus()
    assert table1 is table2  # Same object (cached)
    assert len(table1) == 256


def test_reflect_bits():
    """Test bit reflection helper."""
    # 0b10110000 (176) reflected in 8 bits -> 0b00001101 (13)
    assert local_oig_crc._reflect_bits(0b10110000, 8) == 0b00001101
    # Zero stays zero
    assert local_oig_crc._reflect_bits(0, 8) == 0
    # All ones stays all ones
    assert local_oig_crc._reflect_bits(0xFF, 8) == 0xFF
    # 16-bit reflection
    assert local_oig_crc._reflect_bits(0x8005, 16) == 0xA001


def test_frame_inner_bytes_with_frame_tag():
    """Test extracting inner content from <Frame> tags."""
    # With CRLF
    frame = b"<Frame>ABC</Frame>\r\n"
    assert local_oig_crc.frame_inner_bytes(frame) == b"ABC"
    
    # Without CRLF
    frame = b"<Frame>DEF</Frame>"
    assert local_oig_crc.frame_inner_bytes(frame) == b"DEF"
    
    # With only CR or LF
    frame = b"<Frame>GHI</Frame>\r"
    assert local_oig_crc.frame_inner_bytes(frame) == b"GHI"
    
    frame = b"<Frame>JKL</Frame>\n"
    assert local_oig_crc.frame_inner_bytes(frame) == b"JKL"


def test_frame_inner_bytes_without_frame_tag():
    """Test fallback when no <Frame> tags present."""
    # No frame tags - returns input as-is
    data = b"Just some data"
    assert local_oig_crc.frame_inner_bytes(data) == data
    
    # Partial frame tag
    data = b"<Frame>Incomplete"
    assert local_oig_crc.frame_inner_bytes(data) == data


def test_frame_inner_bytes_with_crc():
    """Test frame inner extraction with CRC tag present."""
    frame = b"<Frame><Result>ACK</Result><CRC>12345</CRC></Frame>\r\n"
    inner = local_oig_crc.frame_inner_bytes(frame)
    assert b"<Result>ACK</Result>" in inner
    assert b"<CRC>12345</CRC>" in inner


def test_compute_frame_crc_simple():
    """Test CRC computation on simple frame."""
    frame = b"<Frame>ABC</Frame>"
    crc = local_oig_crc.compute_frame_crc(frame)
    assert isinstance(crc, int)
    assert 0 <= crc <= 0xFFFF


def test_compute_frame_crc_with_existing_crc():
    """Test CRC computation strips existing CRC tag."""
    # Frame with wrong CRC - should be stripped and recalculated
    frame = b"<Frame><Result>ACK</Result><CRC>99999</CRC></Frame>"
    crc1 = local_oig_crc.compute_frame_crc(frame)
    
    # Same frame without CRC - should produce same result
    frame_no_crc = b"<Frame><Result>ACK</Result></Frame>"
    crc2 = local_oig_crc.compute_frame_crc(frame_no_crc)
    
    assert crc1 == crc2


def test_compute_frame_crc_without_frame_tags():
    """Test CRC computation on raw data without frame tags."""
    data = b"<Result>TEST</Result>"
    crc = local_oig_crc.compute_frame_crc(data)
    assert isinstance(crc, int)


def test_compute_frame_crc_multiline():
    """Test CRC computation on multi-line XML."""
    frame = b"<Frame>\n<Result>ACK</Result>\n<Data>123</Data>\n</Frame>\r\n"
    crc = local_oig_crc.compute_frame_crc(frame)
    assert isinstance(crc, int)


def test_build_frame_simple():
    """Test building frame with CRC."""
    inner = "<Result>ACK</Result>"
    frame = local_oig_crc.build_frame(inner, add_crlf=False)
    
    assert frame.startswith("<Frame>")
    assert frame.endswith("</Frame>")
    assert "<CRC>" in frame
    assert "</CRC>" in frame
    assert "<Result>ACK</Result>" in frame


def test_build_frame_with_crlf_default():
    """Test that CRLF is added by default."""
    inner = "<Result>ACK</Result>"
    frame = local_oig_crc.build_frame(inner)
    assert frame.endswith("\r\n")


def test_build_frame_without_crlf():
    """Test building frame without CRLF."""
    inner = "<Result>ACK</Result>"
    frame = local_oig_crc.build_frame(inner, add_crlf=False)
    assert not frame.endswith("\r\n")
    assert frame.endswith("</Frame>")


def test_build_frame_removes_existing_crc():
    """Test that existing CRC tag is removed before calculation."""
    # Inner XML with wrong CRC
    inner = "<Result>ACK</Result><CRC>99999</CRC>"
    frame = local_oig_crc.build_frame(inner, add_crlf=False)
    
    # Should have only one CRC tag (the correct one)
    assert frame.count("<CRC>") == 1
    assert "<CRC>99999</CRC>" not in frame


def test_build_frame_crc_format():
    """Test that CRC is formatted as 5-digit decimal."""
    inner = "<Result>ACK</Result>"
    frame = local_oig_crc.build_frame(inner, add_crlf=False)
    
    # Extract CRC value
    import re
    match = re.search(r"<CRC>(\d{5})</CRC>", frame)
    assert match is not None
    crc_str = match.group(1)
    assert len(crc_str) == 5
    assert crc_str.isdigit()


def test_build_frame_crc_correctness():
    """Test that built frame has correct CRC."""
    inner = "<Result>TEST</Result><Data>12345</Data>"
    frame = local_oig_crc.build_frame(inner, add_crlf=False)
    
    # Verify CRC by recalculating
    frame_bytes = frame.encode("utf-8")
    computed_crc = local_oig_crc.compute_frame_crc(frame_bytes)
    
    # Extract CRC from frame
    import re
    match = re.search(r"<CRC>(\d+)</CRC>", frame)
    embedded_crc = int(match.group(1))
    
    assert computed_crc == embedded_crc


def test_build_frame_complex_xml():
    """Test building frame with complex multi-element XML."""
    inner = """<Device>DEV123</Device>
<Table>tbl_actual</Table>
<Data>
  <Field>VALUE</Field>
  <Count>42</Count>
</Data>"""
    frame = local_oig_crc.build_frame(inner, add_crlf=True)
    
    assert frame.startswith("<Frame>")
    assert frame.endswith("\r\n")
    assert "<CRC>" in frame
    # Verify all inner content is present
    assert "<Device>DEV123</Device>" in frame
    assert "<Table>tbl_actual</Table>" in frame


def test_build_frame_unicode():
    """Test building frame with UTF-8 characters."""
    inner = "<Message>Příliš žluťoučký kůň</Message>"
    frame = local_oig_crc.build_frame(inner, add_crlf=False)
    
    assert frame.startswith("<Frame>")
    assert "<CRC>" in frame
    # UTF-8 should be preserved
    assert "Příliš žluťoučký kůň" in frame


def test_crc_end_to_end():
    """End-to-end test: build frame, parse it, verify CRC matches."""
    original_inner = "<Result>SUCCESS</Result><Code>200</Code>"
    
    # Build frame with CRC
    built_frame = local_oig_crc.build_frame(original_inner, add_crlf=True)
    
    # Extract inner bytes
    built_bytes = built_frame.encode("utf-8")
    extracted_inner = local_oig_crc.frame_inner_bytes(built_bytes)
    
    # Compute CRC from built frame
    computed_crc = local_oig_crc.compute_frame_crc(built_bytes)
    
    # Extract embedded CRC
    import re
    match = re.search(rb"<CRC>(\d+)</CRC>", extracted_inner)
    embedded_crc = int(match.group(1))
    
    # They should match
    assert computed_crc == embedded_crc


def test_frame_inner_bytes_dotall():
    """Test that frame content can span multiple lines (DOTALL regex)."""
    frame = b"""<Frame>
<Result>ACK</Result>
<Data>
  <Line1>Value1</Line1>
  <Line2>Value2</Line2>
</Data>
</Frame>\r\n"""
    inner = local_oig_crc.frame_inner_bytes(frame)
    assert b"<Result>ACK</Result>" in inner
    assert b"<Line1>Value1</Line1>" in inner
    assert b"<Line2>Value2</Line2>" in inner
