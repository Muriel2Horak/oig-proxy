"""Check native evidence for the last completed SA."""
# pylint: disable=protected-access,missing-function-docstring
from types import SimpleNamespace
from unittest.mock import Mock

from proxy.server import ProxyServer, _BoxFrameObservation


BOX = "1234567890"
SESSION = "qualified"


def field_frame(table, stamp, **fields):
    return _BoxFrameObservation({"TblName": table, "ID_Device": BOX, "Reason": "Table", "DT": str(stamp), **fields})


def test_counts_one_confirmed_sa_after_fresh_parameters(make_config, monkeypatch):
    elapsed = Mock(return_value=1.0)
    wall = Mock(return_value=2000.0)
    monkeypatch.setattr("proxy.server.time", SimpleNamespace(monotonic=elapsed, time=wall))
    delivery = Mock()
    delivery.box_readback_time.side_effect = lambda _device, dt: (3, float(dt))
    completed = Mock()
    proxy = ProxyServer(
        make_config(), twin_delivery=delivery, device_id_provider=lambda: BOX,
        request_readback=Mock(),
    )
    proxy.configure_parameter_readback(on_sa_confirmed=completed)
    proxy._box_devices[SESSION] = BOX
    proxy._track_sa_sent(BOX, "1000")
    assert proxy.parameter_refresh_in_flight()
    proxy._observe_parameter_readback(field_frame("tbl_actual", 1001, LOAD="120"), SESSION)
    proxy._observe_parameter_readback(field_frame("tbl_box_prms", 999, MODE="3"), SESSION)
    proxy._note_ack_readback(
        delivery, SimpleNamespace(table="tbl_box_prms", key="SA", value=1), BOX, "1001",
    )
    completed.assert_not_called()
    proxy._observe_parameter_readback(field_frame("tbl_box_prms", 1002, MODE="3"), SESSION)
    completed.assert_called_once_with(2000.0)
    assert not proxy.parameter_refresh_in_flight()
    proxy._observe_parameter_readback(field_frame("tbl_batt_prms", 1003, BAT_MIN="10"), SESSION)
    completed.assert_called_once()


def test_explicit_sa_event_counts_only_with_its_fresh_table(make_config, monkeypatch):
    elapsed = Mock(return_value=1.0)
    monkeypatch.setattr("proxy.server.time", SimpleNamespace(monotonic=elapsed, time=lambda: 2000.0))
    delivery = Mock()
    delivery.box_readback_time.side_effect = lambda _device, dt: (3, float(dt))
    completed = Mock()
    proxy = ProxyServer(
        make_config(), twin_delivery=delivery, device_id_provider=lambda: BOX,
        request_readback=Mock(),
    )
    proxy.configure_parameter_readback(on_sa_confirmed=completed)
    proxy._box_devices[SESSION] = BOX
    event = field_frame("tbl_events", 1001, Type="Setting", Content="Remotely : tbl_box_prms / SA: [0]->[1]")
    table = field_frame("tbl_box_prms", 1002, MODE="3")
    proxy._observe_parameter_readback(event, SESSION)
    proxy._observe_parameter_readback(table, SESSION)
    completed.assert_not_called()
    proxy._track_sa_sent(BOX, "1000")
    proxy._observe_parameter_readback(table, SESSION)
    completed.assert_not_called()
    proxy._observe_parameter_readback(event, SESSION)
    completed.assert_called_once_with(2000.0)
