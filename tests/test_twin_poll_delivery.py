"""Tests for Poll-Driven Queue Delivery (IsNew*).

Task 6: Twin delivers command on IsNew* path and emits END when idle.
No unsolicited command push is allowed.

Verification:
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_poll_delivery.py --maxfail=1
  PYTHONPATH=addon/oig-proxy pytest -q tests/test_twin_poll_delivery.py -k unsolicited --maxfail=1
"""

# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access
# pylint: disable=too-few-public-methods,invalid-name,unused-variable

import re

import pytest

from digital_twin import DigitalTwin, DigitalTwinConfig
from twin_state import (
    QueueSettingDTO,
    SettingStage,
)
from twin_transaction import generate_tx_id


def make_queue_dto(
    tx_id: str | None = None,
    conn_id: int = 1,
    tbl_name: str = "tbl_box_prms",
    tbl_item: str = "MODE",
    new_value: str = "1",
) -> QueueSettingDTO:
    return QueueSettingDTO(
        tx_id=tx_id or generate_tx_id(),
        conn_id=conn_id,
        tbl_name=tbl_name,
        tbl_item=tbl_item,
        new_value=new_value,
    )


@pytest.mark.asyncio
class TestPollDrivenDelivery:
    """Tests for IsNew* poll-driven delivery."""

    async def test_on_poll_returns_end_when_idle(self):
        """
        GIVEN: Twin with no pending commands
        WHEN: on_poll is called with IsNewSet
        THEN: Returns END frame
        """
        twin = DigitalTwin(session_id="test-session")

        response = await twin.on_poll(
            tx_id=None, conn_id=1, table_name="IsNewSet"
        )

        assert response.ack is True
        assert response.frame_data is not None
        assert "<Result>END</Result>" in response.frame_data

    async def test_on_poll_delivers_pending_setting(self):
        """
        GIVEN: Twin with queued setting
        WHEN: on_poll is called with IsNewSet
        THEN: Returns Setting frame (not END)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-1", tbl_name="tbl_box_prms", tbl_item="MODE")
        await twin.queue_setting(dto)

        response = await twin.on_poll(
            tx_id=None, conn_id=1, table_name="IsNewSet"
        )

        assert response.ack is True
        assert response.frame_data is not None
        assert "<Reason>Setting</Reason>" in response.frame_data
        assert "<TblName>tbl_box_prms</TblName>" in response.frame_data
        assert "<TblItem>MODE</TblItem>" in response.frame_data
        assert "<NewValue>1</NewValue>" in response.frame_data

    async def test_on_poll_sets_delivered_conn_id(self):
        """
        GIVEN: Twin with queued setting
        WHEN: on_poll delivers the setting
        THEN: delivered_conn_id is set for INV-1 validation
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-conn-test")
        await twin.queue_setting(dto)

        response = await twin.on_poll(
            tx_id=None, conn_id=5, table_name="IsNewSet"
        )

        assert response.frame_data is not None
        assert "<Reason>Setting</Reason>" in response.frame_data

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.delivered_conn_id == 5
        assert pending.stage == SettingStage.SENT_TO_BOX

    async def test_on_poll_isnewweather_delivers_pending_setting(self):
        """
        GIVEN: Twin with queued setting
        WHEN: on_poll is called with IsNewWeather
        THEN: Returns Setting frame (delivery)
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-isnewweather")
        await twin.queue_setting(dto)

        response = await twin.on_poll(
            tx_id=None, conn_id=1, table_name="IsNewWeather"
        )

        assert response.ack is True
        assert response.frame_data is not None
        assert "<Reason>Setting</Reason>" in response.frame_data

    async def test_on_poll_non_isnew_returns_no_frame(self):
        """
        GIVEN: Twin with queued setting
        WHEN: on_poll is called with non-IsNew table
        THEN: Returns ack but no frame_data
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-non-isnew")
        await twin.queue_setting(dto)

        response = await twin.on_poll(
            tx_id=None, conn_id=1, table_name="tbl_actual"
        )

        assert response.ack is True
        assert response.frame_data is None

    async def test_on_poll_dequeues_from_queue(self):
        """
        GIVEN: Twin with multiple queued settings
        WHEN: on_poll is called multiple times
        THEN: Each poll delivers one setting
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        await twin.queue_setting(make_queue_dto(tx_id="tx-1"))
        await twin.queue_setting(make_queue_dto(tx_id="tx-2"))

        response1 = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response1.frame_data

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.tx_id == "tx-1"

        await twin.finish_inflight("tx-1", conn_id=1, success=True)

        response2 = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response2.frame_data

        pending2 = await twin.get_inflight()
        assert pending2 is not None
        assert pending2.tx_id == "tx-2"

    async def test_on_poll_returns_end_after_all_delivered(self):
        """
        GIVEN: Twin with queued settings
        WHEN: All settings are delivered and completed
        THEN: Subsequent polls return END
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-final")
        await twin.queue_setting(dto)

        response1 = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response1.frame_data

        await twin.finish_inflight("tx-final", conn_id=1, success=True)

        response2 = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Result>END</Result>" in response2.frame_data


@pytest.mark.asyncio
class TestNoUnsolicitedPush:
    """Tests verifying no unsolicited command push is allowed."""

    async def test_unsolicited_push_not_sent_on_non_poll(self):
        """
        GIVEN: Twin with queued setting
        WHEN: No poll occurs
        THEN: Setting is not sent (stays in queue)
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-unsolicited")
        result = await twin.queue_setting(dto)

        assert result.status == "accepted"

        queue_len = await twin.get_queue_length()
        assert queue_len == 1

        pending = await twin.get_inflight()
        assert pending is None

    async def test_setting_delivered_on_all_isnew_polls(self):
        """
        GIVEN: Twin with queued setting
        WHEN: IsNewSet/IsNewWeather/IsNewFW polls occur
        THEN: Setting is delivered on each IsNew* poll type
        """
        for idx, table_name in enumerate(["IsNewSet", "IsNewWeather", "IsNewFW"], start=1):
            twin = DigitalTwin(session_id=f"test-session-{table_name}")
            await twin.queue_setting(make_queue_dto(tx_id=f"tx-poll-type-{idx}"))
            response = await twin.on_poll(tx_id=None, conn_id=1, table_name=table_name)
            assert response.frame_data is not None
            assert "<Reason>Setting</Reason>" in response.frame_data

    async def test_delivery_requires_explicit_poll(self):
        """
        GIVEN: Twin with queued setting
        WHEN: on_poll is never called
        THEN: Setting never transitions to SENT_TO_BOX
        """
        twin = DigitalTwin(session_id="test-session")

        dto = make_queue_dto(tx_id="tx-no-poll")
        await twin.queue_setting(dto)

        import asyncio
        await asyncio.sleep(0.01)

        pending = await twin.get_inflight()
        assert pending is None, "Setting should not auto-start without poll"

        queue_len = await twin.get_queue_length()
        assert queue_len == 1, "Setting should remain in queue"


@pytest.mark.asyncio
class TestFrameBuilding:
    """Tests for Setting frame building."""

    async def test_frame_contains_device_id(self):
        """
        GIVEN: Twin with device_id configured
        WHEN: Setting is delivered
        THEN: Frame contains correct device_id
        """
        config = DigitalTwinConfig(device_id="99999")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-device")
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<ID_Device>99999</ID_Device>" in response.frame_data

    async def test_frame_contains_tbl_name_and_item(self):
        """
        GIVEN: Setting for specific table/item
        WHEN: Frame is built
        THEN: Frame contains correct TblName and TblItem
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(
            tx_id="tx-tbl",
            tbl_name="tbl_invertor_prm1",
            tbl_item="AAC_MAX_CHRG",
            new_value="120.0",
        )
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<TblName>tbl_invertor_prm1</TblName>" in response.frame_data
        assert "<TblItem>AAC_MAX_CHRG</TblItem>" in response.frame_data
        assert "<NewValue>120.0</NewValue>" in response.frame_data

    async def test_frame_has_valid_crc(self):
        """
        GIVEN: Setting is delivered
        WHEN: Frame is built
        THEN: Frame contains valid CRC tag
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-crc")
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        crc_match = re.search(r"<CRC>(\d+)</CRC>", response.frame_data)
        assert crc_match is not None, "Frame must contain CRC tag"

        crc_value = int(crc_match.group(1))
        assert 0 <= crc_value <= 99999, "CRC should be 5-digit decimal"

    async def test_frame_has_reason_setting(self):
        """
        GIVEN: Setting is delivered
        WHEN: Frame is built
        THEN: Frame contains Reason=Setting
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-reason")
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<Reason>Setting</Reason>" in response.frame_data

    async def test_frame_uses_raw_frame_when_provided(self):
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)
        raw = (
            "<Frame><ID>13782494</ID><ID_Device>2206237016</ID_Device>"
            "<ID_Set>1772983800</ID_Set><ID_SubD>0</ID_SubD>"
            "<DT>08.03.2026 16:30:00</DT><NewValue>0</NewValue><Confirm>New</Confirm>"
            "<TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff><Reason>Setting</Reason>"
            "<TSec>2026-03-08 15:30:18</TSec><ver>04858</ver><CRC>21427</CRC></Frame>\r\n"
        )

        dto = QueueSettingDTO(
            tx_id="tx-raw",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="1",
            raw_frame=raw,
        )
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert response.frame_data == raw

    async def test_frame_uses_raw_frame_adds_crlf_when_missing(self):
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)
        raw_no_crlf = (
            "<Frame><ID>13782494</ID><ID_Device>2206237016</ID_Device>"
            "<ID_Set>1772983800</ID_Set><ID_SubD>0</ID_SubD>"
            "<DT>08.03.2026 16:30:00</DT><NewValue>0</NewValue><Confirm>New</Confirm>"
            "<TblName>tbl_box_prms</TblName><TblItem>MODE</TblItem><ID_Server>5</ID_Server>"
            "<mytimediff>0</mytimediff><Reason>Setting</Reason>"
            "<TSec>2026-03-08 15:30:18</TSec><ver>04858</ver><CRC>21427</CRC></Frame>"
        )

        dto = QueueSettingDTO(
            tx_id="tx-raw-nocrlf",
            conn_id=1,
            tbl_name="tbl_box_prms",
            tbl_item="MODE",
            new_value="0",
            raw_frame=raw_no_crlf,
        )
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert response.frame_data == raw_no_crlf + "\r\n"


@pytest.mark.asyncio
class TestEndEmission:
    """Tests for END emission when idle."""

    async def test_end_frame_has_time_tags(self):
        """
        GIVEN: Twin is idle
        WHEN: on_poll returns END
        THEN: END frame contains Time and UTCTime tags
        """
        twin = DigitalTwin(session_id="test-session")

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<Time>" in response.frame_data
        assert "<UTCTime>" in response.frame_data
        assert "<Result>END</Result>" in response.frame_data

    async def test_end_frame_has_getactual_todo(self):
        """
        GIVEN: Twin is idle
        WHEN: on_poll returns END
        THEN: END frame contains ToDo=GetActual
        """
        twin = DigitalTwin(session_id="test-session")

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<ToDo>GetActual</ToDo>" in response.frame_data

    async def test_idle_after_disconnect_returns_end(self):
        """
        GIVEN: Setting was delivered but disconnect occurred before ACK
        WHEN: on_poll is called
        THEN: Returns END (inflight cleared on disconnect)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-disconnect")
        await twin.queue_setting(dto)

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response.frame_data

        from twin_state import OnDisconnectDTO
        await twin.on_disconnect(OnDisconnectDTO(tx_id=None, conn_id=1))

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<Result>END</Result>" in response.frame_data


@pytest.mark.asyncio
class TestDeliveryWithExistingInflight:
    """Tests for behavior when inflight already exists."""

    async def test_existing_inflight_redelivers_on_poll(self):
        """
        GIVEN: Inflight setting exists but not yet delivered
        WHEN: on_poll is called
        THEN: Existing inflight is delivered
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-existing")
        await twin.queue_setting(dto)
        await twin.start_inflight("tx-existing", conn_id=1)

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.delivered_conn_id is None

        response = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")

        assert "<Reason>Setting</Reason>" in response.frame_data

        pending = await twin.get_inflight()
        assert pending is not None
        assert pending.delivered_conn_id == 1

    async def test_delivered_inflight_not_redelivered(self):
        """
        GIVEN: Inflight was already delivered
        WHEN: on_poll is called again (before ACK)
        THEN: Redelivers the same inflight (BOX may have missed it)
        """
        config = DigitalTwinConfig(device_id="12345")
        twin = DigitalTwin(session_id="test-session", config=config)

        dto = make_queue_dto(tx_id="tx-redeliver")
        await twin.queue_setting(dto)

        response1 = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response1.frame_data

        response2 = await twin.on_poll(tx_id=None, conn_id=1, table_name="IsNewSet")
        assert "<Reason>Setting</Reason>" in response2.frame_data
