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
    ValidatedFrame,
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


class _CountingFrameStreamAssembler(FrameStreamAssembler):
    """Count incremental terminator inspections without using wall-clock timing."""

    def __init__(self, **kwargs: int) -> None:
        super().__init__(**kwargs)
        self.inspected_bytes = 0

    def _advance_terminator(self, value: int) -> bool:
        self.inspected_bytes += 1
        return super()._advance_terminator(value)


class _BytesSubclass(bytes):
    """Non-exact bytes input rejected by immutable evidence types."""


class _IntSubclass(int):
    """Non-exact int input rejected by immutable evidence types."""


class _SideEffectBytes(bytes):
    """Bytes subclass whose overridden operation must never be reached."""

    operation_called = False

    def startswith(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        type(self).operation_called = True
        raise AssertionError("untrusted bytes method called")


class _SideEffectInt(int):
    """Int subclass whose comparison must never be reached."""

    operation_called = False

    def __lt__(self, other: object) -> bool:
        type(self).operation_called = True
        raise AssertionError("untrusted int method called")


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


def test_stream_assembler_inspects_each_incomplete_byte_only_constant_times() -> None:
    raw = b"<Frame>" + b"x" * 65_536
    assembler = _CountingFrameStreamAssembler(max_frame_bytes=len(raw))

    assert assembler.feed(raw, received_at_ms=1) == ()

    assert assembler.inspected_bytes == len(raw) - len(b"<Frame>")


def test_stream_assembler_inspects_complete_and_coalesced_bytes_linearly() -> None:
    frame = b"<Frame>" + b"x" * 32_768 + b"</Frame>\r\n"
    chunk = frame * 8
    assembler = _CountingFrameStreamAssembler(max_frame_bytes=len(frame))

    frames = assembler.feed(chunk, received_at_ms=2)

    assert len(frames) == 8
    assert assembler.inspected_bytes == 8 * (len(frame) - len(b"<Frame>"))


def test_stream_assembler_handles_maximum_bounded_incomplete_frame() -> None:
    raw = b"<Frame>" + b"x" * (1_048_576 - len(b"<Frame>"))
    assembler = FrameStreamAssembler(max_frame_bytes=1_048_576)

    assert assembler.feed(raw, received_at_ms=3) == ()
    with pytest.raises(FrameStreamError, match="buffer_overflow"):
        assembler.feed(b"x", received_at_ms=4)


def test_stream_assembler_handles_maximum_bounded_complete_frame() -> None:
    raw = (
        b"<Frame>"
        + b"x" * (1_048_576 - len(b"<Frame></Frame>\r\n"))
        + b"</Frame>\r\n"
    )

    assert FrameStreamAssembler(max_frame_bytes=1_048_576).feed(
        raw, received_at_ms=5
    ) == (AssembledFrame(raw, 5),)


def test_stream_assembler_handles_input_chunk_larger_than_frame_bound() -> None:
    frame = b"<Frame>" + b"x" * 8_192 + b"</Frame>\r\n"
    chunk = frame * 129
    assembler = FrameStreamAssembler(max_frame_bytes=len(frame))

    frames = assembler.feed(chunk, received_at_ms=6)

    assert len(chunk) > 1_048_576
    assert len(frames) == 129
    assert all(assembled.raw == frame for assembled in frames)


def test_stream_assembler_recovers_close_match_after_partial_mismatch() -> None:
    raw = b"<Frame>payload</FraXmore</Frame>\r\n"

    assert FrameStreamAssembler().feed(raw, received_at_ms=7) == (
        AssembledFrame(raw, 7),
    )


def test_stream_assembler_overflow_precedes_bad_suffix_at_boundary() -> None:
    partial = b"<Frame>x</Frame>"
    assembler = FrameStreamAssembler(max_frame_bytes=len(partial))

    assert assembler.feed(partial, received_at_ms=8) == ()
    with pytest.raises(FrameStreamError) as error:
        assembler.feed(b"X", received_at_ms=9)

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


@pytest.mark.parametrize(
    "crc_tag",
    (
        b'<CRC source="box">00000</CRC>',
        b"<CRC>00<!--note-->000</CRC>",
        b"<CRC>00<?note x?>000</CRC>",
        b"<CRC><![CDATA[00000]]></CRC>",
        b"<CRC><Value>00000</Value></CRC>",
    ),
)
def test_validate_frame_rejects_non_simple_crc_syntax(crc_tag: bytes) -> None:
    raw = b"<Frame>" + crc_tag + b"</Frame>\r\n"

    validation = validate_frame(AssembledFrame(raw, 1))

    assert validation.validated is None
    assert validation.error is FrameValidationError.MALFORMED_CRC


@pytest.mark.parametrize(
    "raw",
    (bytearray(b"x"), memoryview(b"x"), _BytesSubclass(b"x")),
)
def test_assembled_frame_rejects_non_exact_bytes(raw: object) -> None:
    with pytest.raises(TypeError, match="raw"):
        AssembledFrame(raw=raw, received_at_ms=1)  # type: ignore[arg-type]


@pytest.mark.parametrize("received_at_ms", (True, _IntSubclass(1), -1))
def test_assembled_frame_rejects_non_contract_timestamp(received_at_ms: object) -> None:
    error_type = TypeError if received_at_ms is True or isinstance(
        received_at_ms, _IntSubclass
    ) else ValueError
    with pytest.raises(error_type, match="received_at_ms"):
        AssembledFrame(b"x", received_at_ms)  # type: ignore[arg-type]


def test_assembled_frame_rejects_bytes_subclass_before_side_effect() -> None:
    raw = _SideEffectBytes(b"<Frame>x</Frame>\r\n")
    _SideEffectBytes.operation_called = False

    with pytest.raises(TypeError, match="raw"):
        AssembledFrame(raw, 1)

    assert _SideEffectBytes.operation_called is False


def test_validated_frame_rejects_mutable_or_subclassed_byte_fields() -> None:
    inner = b"<Result>ACK</Result>"
    raw = valid_frame(inner)
    crc = crc16_modbus(inner)

    for field, invalid in (
        ("raw", bytearray(raw)),
        ("raw", memoryview(raw)),
        ("raw", _BytesSubclass(raw)),
        ("inner_without_crc", bytearray(inner)),
        ("inner_without_crc", memoryview(inner)),
        ("inner_without_crc", _BytesSubclass(inner)),
    ):
        values = {
            "raw": raw,
            "received_at_ms": 1,
            "inner_without_crc": inner,
            "transmitted_crc": crc,
            "computed_crc": crc,
        }
        values[field] = invalid
        with pytest.raises(TypeError, match=field):
            ValidatedFrame(**values)  # type: ignore[arg-type]


def test_validated_frame_rejects_subclasses_before_side_effects() -> None:
    inner = b"<Result>ACK</Result>"
    raw = valid_frame(inner)
    crc = crc16_modbus(inner)
    malicious_raw = _SideEffectBytes(raw)
    malicious_time = _SideEffectInt(1)
    _SideEffectBytes.operation_called = False
    _SideEffectInt.operation_called = False

    with pytest.raises(TypeError, match="raw"):
        ValidatedFrame(malicious_raw, 1, inner, crc, crc)
    with pytest.raises(TypeError, match="received_at_ms"):
        ValidatedFrame(raw, malicious_time, inner, crc, crc)

    assert _SideEffectBytes.operation_called is False
    assert _SideEffectInt.operation_called is False


@pytest.mark.parametrize(
    ("field", "invalid", "error_type"),
    (
        ("received_at_ms", True, TypeError),
        ("received_at_ms", _IntSubclass(1), TypeError),
        ("received_at_ms", -1, ValueError),
        ("transmitted_crc", True, TypeError),
        ("transmitted_crc", _IntSubclass(1), TypeError),
        ("transmitted_crc", -1, ValueError),
        ("transmitted_crc", 65_536, ValueError),
        ("computed_crc", True, TypeError),
        ("computed_crc", _IntSubclass(1), TypeError),
        ("computed_crc", -1, ValueError),
        ("computed_crc", 65_536, ValueError),
    ),
)
def test_validated_frame_rejects_non_contract_integer_fields(
    field: str, invalid: object, error_type: type[Exception]
) -> None:
    inner = b"<Result>ACK</Result>"
    raw = valid_frame(inner)
    crc = crc16_modbus(inner)
    values = {
        "raw": raw,
        "received_at_ms": 1,
        "inner_without_crc": inner,
        "transmitted_crc": crc,
        "computed_crc": crc,
    }
    values[field] = invalid

    with pytest.raises(error_type, match=field):
        ValidatedFrame(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize("mutation", ("inner", "transmitted", "computed", "raw"))
def test_validated_frame_rejects_internal_evidence_inconsistency(
    mutation: str,
) -> None:
    inner = b"<Result>ACK</Result>"
    raw = valid_frame(inner)
    crc = crc16_modbus(inner)
    values = {
        "raw": raw,
        "received_at_ms": 1,
        "inner_without_crc": inner,
        "transmitted_crc": crc,
        "computed_crc": crc,
    }
    if mutation == "inner":
        values["inner_without_crc"] = b"<Result>NACK</Result>"
    elif mutation == "transmitted":
        values["transmitted_crc"] = (crc + 1) % 65_536
    elif mutation == "computed":
        values["computed_crc"] = (crc + 1) % 65_536
    else:
        values["raw"] = valid_frame(b"<Result>NACK</Result>")

    with pytest.raises(ValueError, match="inconsistent"):
        ValidatedFrame(**values)  # type: ignore[arg-type]


def test_parse_frame_compatibility_wrapper_rejects_bad_crc() -> None:
    raw = valid_frame(b"<Result>ACK</Result>").replace(b"ACK", b"NCK")

    assert parse_frame(raw) is None
