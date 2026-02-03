# pylint: disable=missing-module-docstring,missing-function-docstring,missing-class-docstring,protected-access,unused-argument,too-few-public-methods,no-member,use-implicit-booleaness-not-comparison,line-too-long,invalid-name,too-many-statements,too-many-instance-attributes,wrong-import-position,wrong-import-order,deprecated-module,too-many-locals,too-many-lines,attribute-defined-outside-init,unexpected-keyword-arg,duplicate-code
import os
import re
import sys

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADDON_DIR = os.path.join(ROOT_DIR, "addon", "oig-proxy")
sys.path.insert(0, ADDON_DIR)

from oig_frame import build_frame, compute_frame_checksum  # noqa: E402


def test_compute_frame_checksum_known_samples():
    samples = [
        (b"<Frame><Result>END</Result><CRC>34500</CRC></Frame>\r\n",
         34500),
        (b"<Frame><Result>ACK</Result><ToDo>GetActual</ToDo><CRC>00167</CRC></Frame>\r\n",
            167,
         ),
        (b"<Frame><Result>END</Result><Time>2025-12-18 07:09:28</Time>"
            b"<UTCTime>2025-12-18 06:09:28</UTCTime><CRC>02378</CRC></Frame>\r\n",
            2378,
         ),
    ]

    for raw, want in samples:
        assert compute_frame_checksum(raw) == want


def test_build_frame_injects_crc_and_crlf():
    inner_xml = "<Result>ACK</Result><ToDo>GetActual</ToDo><CRC>99999</CRC>"
    frame = build_frame(inner_xml)
    assert frame.endswith("\r\n")

    match = re.search(r"<CRC>(\d+)</CRC>", frame)
    assert match is not None

    computed = compute_frame_checksum(frame.encode("utf-8"))
    assert int(match.group(1)) == computed


def test_build_frame_without_crlf():
    inner_xml = "<Result>ACK</Result>"
    frame = build_frame(inner_xml, add_crlf=False)
    assert frame.endswith("</Frame>")
    assert not frame.endswith("\r\n")
