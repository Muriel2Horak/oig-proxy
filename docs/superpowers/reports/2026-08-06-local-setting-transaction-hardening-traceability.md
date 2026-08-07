# Local-Setting Transaction Safety Traceability

Design: [`2026-08-06-local-setting-transaction-hardening-design.md`](../specs/2026-08-06-local-setting-transaction-hardening-design.md)

| Invariant | Unit node | Integration node | E2E node |
|---|---|---|---|
| SI-1 | `tests/v2/test_twin_delivery.py::test_claim_requires_correlated_cloud_terminal_end` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_poll_reaches_cloud_and_queue_stays_pending_until_terminal_end` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_online_cloud_priority_then_local_batch` |
| SI-2 | `tests/v2/test_setting_dialog.py::test_cloud_setting_marks_cycle_cloud_owned_without_local_claim` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_cloud_setting_and_box_ack_round_trip_before_local_batch` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_online_cloud_priority_then_local_batch` |
| SI-3 | `tests/v2/test_twin_delivery.py::test_ack_requires_active_session_and_dialog_owner` | `tests/v2/test_proxy/test_setting_dialog_offline.py::test_wrong_session_ack_cannot_advance_owned_attempt` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_foreign_session_cannot_advance_active_command` |
| SI-4 | `tests/v2/test_twin_delivery.py::test_ack_moves_to_awaiting_event_without_confirmation` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_correlated_end_is_replaced_and_local_ack_returns_exact_end` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_ack_is_delivery_only_until_exact_event` |
| SI-5 | `tests/v2/test_setting_confirmation.py::test_matcher_requires_exact_device_table_key_and_canonical_value` | `tests/v2/test_proxy/test_setting_dialog_online.py::test_exact_direct_event_confirms_without_forwarding_local_evidence` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_matching_event_confirms_and_nonmatching_event_does_not` |
| SI-6 | `tests/v2/test_twin_store.py::test_enqueue_after_attempt_creates_successor_without_mutating_predecessor` | `tests/v2/test_twin_delivery.py::test_rapid_same_key_updates_preserve_attempted_predecessor` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_rapid_same_key_updates_do_not_overwrite_attempted_command` |
| SI-7 | `tests/v2/test_twin_store.py::test_retry_preserves_stable_fields_and_refreshes_attempt_fields_only` | `tests/v2/test_twin_delivery.py::test_disconnect_requeues_same_wire_identity_for_next_dialogue` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_restart_retries_with_stable_identity` |
| SI-8 | `tests/v2/test_twin_store.py::test_attempt_limits_one_and_eight_are_terminal` | `tests/v2/test_twin_delivery.py::test_timeout_stops_at_limit_and_nack_never_retries` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_retry_limit_and_terminal_nack` |
| SI-9 | `tests/v2/test_ack_parser.py::test_invalid_crc_cannot_produce_a_validated_ack_or_event` | `tests/v2/test_proxy/test_setting_dialog_offline.py::test_invalid_crc_during_active_attempt_closes_without_second_write` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_invalid_crc_never_selects_advances_or_confirms` |
| SI-10 | `tests/v2/test_protocol/test_frame.py::test_stream_assembly_preserves_exact_raw_frames_and_remainder` | `tests/v2/test_proxy/test_setting_streams.py::test_stream_pump_preserves_partial_and_coalesced_frame_bytes` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_partial_and_coalesced_frames_preserve_bytes_and_order` |
| SI-11 | `tests/v2/test_setting_dialog.py::test_local_delivery_trigger_accepts_only_isnewset_fifo_head` | `tests/v2/test_proxy/test_setting_dialog_offline.py::test_firmware_and_weather_never_claim_local_work` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_non_setting_polls_never_trigger_delivery` |
| SI-12 | `tests/v2/test_control_config.py::test_control_defaults_are_fail_closed` | `tests/v2/test_main_integration.py::test_startup_disabled_recovers_store_without_handler_or_local_write` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_disabled_control_has_no_subscription_discovery_or_write` |
| SI-13 | `tests/v2/test_twin_handler.py::test_retained_message_is_rejected_before_json_and_enqueue` | `tests/v2/test_twin_handler.py::test_retained_proxy_control_never_dispatches` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_retained_control_never_enters_local_batch` |
| SI-14 | `tests/v2/test_twin_handler.py::test_unknown_or_unsafe_device_refuses_subscription_and_audits` | `tests/v2/test_main_integration.py::test_unknown_device_poll_and_control_cannot_claim` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_no_delivery_before_valid_device_identity` |
| SI-15 | `tests/v2/test_twin_store.py::test_every_transition_and_attempt_reuses_original_command_and_audit_ids` | `tests/v2/test_settings_audit_contract.py::test_write_outcomes_and_telemetry_reuse_persisted_identity` | `tests/v2/e2e/test_local_setting_transaction.py::test_e2e_audit_identity_survives_all_write_outcomes` |

Node renames require an atomic update to this matrix.

## Invariants and Primary Owners

| Invariant | Approved safety invariant | Primary implementing task |
|---|---|---|
| SI-1 | No local Setting is emitted before the cloud returns the terminal `END` for the active `IsNewSet` cycle, unless the mode manager selected OFFLINE before the poll. | Task 8 |
| SI-2 | A cloud Setting always wins. It is forwarded byte-for-byte and its BOX acknowledgement is forwarded to the cloud. | Task 9 |
| SI-3 | A local `ACK/Setting` is consumed only inside the same connection and active local dialogue that emitted the Setting. | Task 8 |
| SI-4 | A local ACK never marks execution confirmed and never updates the reported setting state. | Task 8 |
| SI-5 | Confirmation requires a valid BOX-to-proxy `tbl_events`, `Type=Setting` frame with the same device, table, key, and canonical new value before the event deadline. | Task 7 |
| SI-6 | A sent command is immutable. A newer same-key request cannot overwrite it. | Task 7 |
| SI-7 | A retry preserves `ID`, `ID_Set`, `DT`, device, table, key, value, `Confirm=New`, and `ID_Server=9`. | Task 7 |
| SI-8 | A command is attempted at most `control_max_attempts` times and never more than eight times. A NACK is terminal and is never retried automatically. | Task 7 |
| SI-9 | Invalid-CRC traffic cannot bind identity, select a command, advance a transaction, or confirm state. | Task 5 |
| SI-10 | ONLINE traffic is byte-transparent unless one correlated cloud `END` is replaced by a local Setting or a proxy-owned local dialogue must suppress/hold traffic the cloud did not originate. | Task 5 |
| SI-11 | Local Setting delivery is triggered only by `IsNewSet`; never by `IsNewFW`, `IsNewWeather`, or an unrelated frame. | Task 9 |
| SI-12 | `control_mqtt_enabled=false` causes zero local Setting writes and zero control-topic subscriptions. | Task 3 |
| SI-13 | Retained control messages are rejected before enqueue or state mutation. | Task 12 |
| SI-14 | No command is accepted or delivered while the device identity is unknown. | Task 12 |
| SI-15 | Every durable state transition and actual outbound attempt uses the command's original `command_id` and `audit_id`. | Task 7 |
