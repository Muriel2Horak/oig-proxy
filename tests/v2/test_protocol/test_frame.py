"""Testy pro protocol/frame.py (OIG Proxy v2)."""
# pylint: disable=missing-function-docstring,use-implicit-booleaness-not-comparison
import dataclasses

import pytest

from protocol.crc import crc16_modbus
from protocol.frame import (
    AssembledFrame,
    FrameDirection,
    FrameStreamAssembler,
    FrameStreamError,
    FrameValidationError,
    StreamErrorCode,
    build_frame,
    extract_frame_from_buffer,
    infer_device_id,
    infer_table_name,
    parse_frame,
    validate_frame,
)


def valid_frame(inner: bytes) -> bytes:
    crc = crc16_modbus(inner)
    return b"<Frame>" + inner + f"<CRC>{crc:05d}</CRC>".encode("ascii") + b"</Frame>\r\n"


def test_build_frame_contains_crc():
    """build_frame musí obsahovat CRC tag."""
    frame = build_frame("<Result>ACK</Result>")
    assert "<CRC>" in frame
    assert "</CRC>" in frame


def test_build_frame_wraps_in_frame_tags():
    """build_frame musí mít <Frame>...</Frame>."""
    frame = build_frame("<Result>ACK</Result>")
    assert frame.startswith("<Frame>")
    assert "</Frame>" in frame


def test_build_frame_crlf_default():
    """Výchozí build_frame přidává \\r\\n."""
    frame = build_frame("<Result>ACK</Result>")
    assert frame.endswith("\r\n")


def test_build_frame_no_crlf():
    frame = build_frame("<Result>ACK</Result>", add_crlf=False)
    assert not frame.endswith("\r\n")


def test_build_and_parse_roundtrip():
    """Round-trip: build_frame → parse_frame vrátí původní inner xml."""
    inner = "<Result>ACK</Result>"
    frame_str = build_frame(inner)
    frame_bytes = frame_str.encode("utf-8")
    parsed = parse_frame(frame_bytes)
    assert parsed is not None
    assert parsed.decode("utf-8") == inner


def test_parse_frame_invalid_returns_none():
    assert parse_frame(b"not a frame") is None


def test_extract_frame_from_buffer_basic():
    """extract_frame_from_buffer extrahuje frame a odstraní ho z bufferu."""
    frame_str = build_frame("<Result>ACK</Result>")
    buf = bytearray(frame_str.encode("utf-8"))
    frame = extract_frame_from_buffer(buf)
    assert frame is not None
    assert b"<Frame>" in frame
    assert len(buf) == 0  # Buffer byl konzumován


def test_extract_frame_from_buffer_partial():
    """Neúplný frame vrátí None."""
    buf = bytearray(b"<Frame><Result>ACK</Result>")
    assert extract_frame_from_buffer(buf) is None


def test_extract_frame_from_buffer_multiple():
    """Více framů v bufferu – extrahuje je postupně."""
    frame1 = build_frame("<Result>ACK</Result>").encode("utf-8")
    frame2 = build_frame("<TblName>tbl_actual</TblName>").encode("utf-8")
    buf = bytearray(frame1 + frame2)

    f1 = extract_frame_from_buffer(buf)
    assert f1 is not None
    assert b"ACK" in f1

    f2 = extract_frame_from_buffer(buf)
    assert f2 is not None
    assert b"tbl_actual" in f2

    assert len(buf) == 0


def test_infer_table_name_tblname():
    xml = "<TblName>tbl_actual</TblName><ID_Device>123</ID_Device>"
    assert infer_table_name(xml) == "tbl_actual"


def test_infer_table_name_result():
    xml = "<Result>ACK</Result>"
    assert infer_table_name(xml) == "ACK"


def test_infer_table_name_none():
    assert infer_table_name("<foo>bar</foo>") is None


def test_infer_device_id():
    xml = "<ID_Device>42</ID_Device>"
    assert infer_device_id(xml) == "42"


def test_infer_device_id_none():
    assert infer_device_id("<foo>bar</foo>") is None


def test_frame_direction_values_are_stable() -> None:
    assert FrameDirection.BOX_TO_PROXY.value == "box_to_proxy"
    assert FrameDirection.CLOUD_TO_PROXY.value == "cloud_to_proxy"


def test_stream_assembly_preserves_exact_raw_frames_and_remainder() -> None:
    first = valid_frame(b"<Result>IsNewSet</Result>")
    second = valid_frame(b"<Result>ACK</Result><Reason>Setting</Reason>")
    assembler = FrameStreamAssembler(max_frame_bytes=1_048_576)

    assert assembler.feed(first[:-1], received_at_ms=10) == ()
    frames = assembler.feed(first[-1:] + second, received_at_ms=11)

    assert tuple(frame.raw for frame in frames) == (first, second)
    assert tuple(frame.received_at_ms for frame in frames) == (11, 11)


def test_stream_assembler_returns_complete_frame_and_keeps_trailing_partial() -> None:
    first = valid_frame(b"<Result>ACK</Result>")
    second = valid_frame(b"<Result>NACK</Result>")
    assembler = FrameStreamAssembler()

    assert assembler.feed(first + second[:-2], received_at_ms=20) == (
        AssembledFrame(first, 20),
    )
    assert assembler.feed(second[-2:], received_at_ms=21) == (
        AssembledFrame(second, 21),
    )


@pytest.mark.parametrize("split_at", range(1, len(b"<Frame>") + 1))
def test_stream_assembler_waits_at_every_frame_prefix_split(split_at: int) -> None:
    raw = valid_frame(b"<Result>ACK</Result>")
    assembler = FrameStreamAssembler()

    assert assembler.feed(raw[:split_at], received_at_ms=1) == ()
    assert assembler.feed(raw[split_at:], received_at_ms=2) == (
        AssembledFrame(raw=raw, received_at_ms=2),
    )


@pytest.mark.parametrize("tail_bytes", (1, 2, 3))
def test_stream_assembler_waits_for_split_crlf_terminator(tail_bytes: int) -> None:
    raw = valid_frame(b"<Result>ACK</Result>")
    assembler = FrameStreamAssembler()

    assert assembler.feed(raw[:-tail_bytes], received_at_ms=1) == ()
    assert assembler.feed(raw[-tail_bytes:], received_at_ms=2) == (
        AssembledFrame(raw=raw, received_at_ms=2),
    )


def test_stream_assembler_allows_exact_limit_and_rejects_next_byte() -> None:
    exact = b"<Frame>" + b"x" * (64 - len(b"<Frame></Frame>\r\n")) + b"</Frame>\r\n"

    assert FrameStreamAssembler(max_frame_bytes=64).feed(
        exact, received_at_ms=1
    )[0].raw == exact
    with pytest.raises(FrameStreamError, match="buffer_overflow") as error:
        FrameStreamAssembler(max_frame_bytes=63).feed(exact, received_at_ms=1)
    assert error.value.code is StreamErrorCode.BUFFER_OVERFLOW


@pytest.mark.parametrize(
    "raw",
    (
        b"junk<Frame></Frame>\r\n",
        b"<frame></Frame>\r\n",
        b" <Frame></Frame>\r\n",
    ),
)
def test_stream_assembler_rejects_non_exact_prefix(raw: bytes) -> None:
    assembler = FrameStreamAssembler()

    with pytest.raises(FrameStreamError, match="invalid_prefix") as error:
        assembler.feed(raw, received_at_ms=1)

    assert error.value.code is StreamErrorCode.INVALID_PREFIX


@pytest.mark.parametrize(
    "raw",
    (
        b"<Frame>x</Frame>\n",
        b"<Frame>x</Frame>\rX",
        b"<Frame>x</Frame>X\r\n",
        b"<Frame>x</Frame></Frame>\r\n",
    ),
)
def test_stream_assembler_rejects_forbidden_or_embedded_terminators(raw: bytes) -> None:
    assembler = FrameStreamAssembler()

    with pytest.raises(FrameStreamError, match="forbidden_terminator") as error:
        assembler.feed(raw, received_at_ms=1)

    assert error.value.code is StreamErrorCode.FORBIDDEN_TERMINATOR


def test_stream_assembler_waits_for_lone_cr_but_finish_rejects_it() -> None:
    assembler = FrameStreamAssembler()

    assert assembler.feed(b"<Frame>x</Frame>\r", received_at_ms=1) == ()
    with pytest.raises(FrameStreamError, match="eof_partial") as error:
        assembler.finish()

    assert error.value.code is StreamErrorCode.EOF_PARTIAL


def test_stream_assembler_finish_rejects_partial_and_clears_state() -> None:
    assembler = FrameStreamAssembler()
    assembler.feed(b"<Frame>partial", received_at_ms=1)

    with pytest.raises(FrameStreamError, match="eof_partial"):
        assembler.finish()

    complete = valid_frame(b"<Result>ACK</Result>")
    assert assembler.feed(complete, received_at_ms=2)[0].raw == complete


def test_stream_assembler_empty_chunk_and_reset_are_noops_for_output() -> None:
    assembler = FrameStreamAssembler()
    assembler.feed(b"<Fra", received_at_ms=1)

    assert assembler.feed(b"", received_at_ms=2) == ()
    assembler.reset()
    complete = valid_frame(b"<Result>ACK</Result>")
    assert assembler.feed(complete, received_at_ms=3) == (
        AssembledFrame(complete, 3),
    )
    assembler.finish()


def test_stream_assembler_recovers_after_prefix_and_terminator_errors() -> None:
    assembler = FrameStreamAssembler()
    complete = valid_frame(b"<Result>ACK</Result>")

    with pytest.raises(FrameStreamError):
        assembler.feed(b"X", received_at_ms=1)
    assert assembler.feed(complete, received_at_ms=2)[0].raw == complete
    with pytest.raises(FrameStreamError):
        assembler.feed(b"<Frame>x</Frame>\n", received_at_ms=3)
    assert assembler.feed(complete, received_at_ms=4)[0].raw == complete


def test_stream_assembler_recovers_after_overflow() -> None:
    assembler = FrameStreamAssembler(max_frame_bytes=64)
    complete = valid_frame(b"<Result>ACK</Result>")

    with pytest.raises(FrameStreamError, match="buffer_overflow"):
        assembler.feed(b"<Frame>" + b"x" * 58, received_at_ms=1)

    assert assembler.feed(complete, received_at_ms=2)[0].raw == complete


def test_stream_error_precedence_checks_prefix_before_size() -> None:
    assembler = FrameStreamAssembler(max_frame_bytes=1)

    with pytest.raises(FrameStreamError) as error:
        assembler.feed(b"junk", received_at_ms=1)

    assert error.value.code is StreamErrorCode.INVALID_PREFIX


def test_validate_frame_returns_frozen_validated_frame_with_exact_bytes_and_time() -> None:
    inner = b"<Result>ACK</Result><Reason>Setting</Reason>"
    raw = valid_frame(inner)
    assembled = AssembledFrame(raw=raw, received_at_ms=123)

    validation = validate_frame(assembled)

    assert validation.frame is assembled
    assert validation.error is None
    assert validation.validated is not None
    assert validation.validated.raw == raw
    assert validation.validated.received_at_ms == 123
    assert validation.validated.inner_without_crc == inner
    assert validation.validated.transmitted_crc == crc16_modbus(inner)
    assert validation.validated.computed_crc == crc16_modbus(inner)
    with pytest.raises(dataclasses.FrozenInstanceError):
        validation.validated.received_at_ms = 999  # type: ignore[misc]


@pytest.mark.parametrize(
    "raw",
    (
        b"<Frame><CRC>00000</CRC></Frame>\n",
        b"<Frame><CRC>00000</CRC></Frame>",
        b"junk<Frame><CRC>00000</CRC></Frame>\r\n",
        b"<Frame><CRC>00000</CRC></Frame>\r\nextra",
    ),
)
def test_validate_frame_rejects_non_exact_envelope_and_preserves_input(raw: bytes) -> None:
    assembled = AssembledFrame(raw, 55)

    validation = validate_frame(assembled)

    assert validation.frame is assembled
    assert validation.validated is None
    assert validation.error is FrameValidationError.INVALID_ENVELOPE


def test_validate_frame_preserves_original_raw_and_time_after_crc_mismatch() -> None:
    raw = valid_frame(b"<Result>ACK</Result>").replace(b"ACK", b"NCK")
    assembled = AssembledFrame(raw, 56)

    validation = validate_frame(assembled)

    assert validation.frame.raw == raw
    assert validation.frame.received_at_ms == 56
    assert validation.validated is None
    assert validation.error is FrameValidationError.CRC_MISMATCH


@pytest.mark.parametrize(
    ("inner", "expected_error"),
    [
        (b"<Result>ACK</Result>", FrameValidationError.MISSING_CRC),
        (b"<Result>ACK</Result><CRC>1234</CRC>", FrameValidationError.MALFORMED_CRC),
        (
            b"<CRC>00000</CRC><Result>ACK</Result>",
            FrameValidationError.CRC_NOT_FINAL,
        ),
        (
            b"<CRC>00000</CRC><CRC>00000</CRC>",
            FrameValidationError.DUPLICATE_CRC,
        ),
    ],
)
def test_validate_frame_maps_crc_shape_errors(
    inner: bytes, expected_error: FrameValidationError
) -> None:
    raw = b"<Frame>" + inner + b"</Frame>\r\n"

    validation = validate_frame(AssembledFrame(raw, 1))

    assert validation.error is expected_error
    assert validation.validated is None


@pytest.mark.parametrize(
    "inner",
    (
        b"<Result>ACK</Reason>",
        b"<!DOCTYPE x><Result>ACK</Result>",
        b"<!dOcTyPe x><Result>ACK</Result>",
        b"<!ENTITY x 'ACK'><Result>&x;</Result>",
        b"<!eNtItY x 'ACK'><Result>&x;</Result>",
    ),
)
def test_validate_frame_rejects_invalid_xml_and_declarations(inner: bytes) -> None:
    raw = valid_frame(inner)

    validation = validate_frame(AssembledFrame(raw, 1))

    assert validation.error is FrameValidationError.INVALID_XML
    assert validation.validated is None


def test_parse_frame_compatibility_wrapper_rejects_bad_crc() -> None:
    raw = valid_frame(b"<Result>ACK</Result>").replace(b"ACK", b"NCK")

    assert parse_frame(raw) is None
