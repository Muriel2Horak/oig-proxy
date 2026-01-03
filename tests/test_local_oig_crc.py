# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code
import local_oig_crc


def test_crc16_modbus_known_value():
    assert local_oig_crc.crc16_modbus(b"123456789") == 0x4B37


def test_frame_inner_and_crc():
    frame = b"<Frame>ABC</Frame>\r\n"
    assert local_oig_crc.frame_inner_bytes(frame) == b"ABC"

    with_crc = b"<Frame>ABC<CRC>00000</CRC></Frame>"
    crc = local_oig_crc.compute_frame_crc(with_crc)
    assert isinstance(crc, int)


def test_build_frame_includes_crc():
    built = local_oig_crc.build_frame("<Result>ACK</Result>", add_crlf=False)
    assert built.startswith("<Frame>")
    assert "<CRC>" in built
    assert built.endswith("</Frame>")
