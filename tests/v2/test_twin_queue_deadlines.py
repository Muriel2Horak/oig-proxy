"""Exercise command ordering, coalescing and expiry with a monotonic clock."""

from dataclasses import dataclass

from twin.state import TwinQueue


@dataclass
class Clock:
    """Advance queue time without sleeping."""

    now: float = 10.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move time forward by the given number of seconds."""
        self.now += seconds


def make_queue():
    """Collect the exact terminal disposition of each unsent request."""
    clock = Clock()
    queue = TwinQueue(clock=clock)
    drops = []
    queue.set_drop_callback(lambda request, reason: drops.append((request.audit_id, reason)))
    return queue, clock, drops


def enqueue(queue, key, value=1, *, device_id="box", confirm="New"):
    """Submit one setting through the queue's compatibility entry point."""
    setting = queue.enqueue("tbl_box_prms", key, value, confirm, device_id=device_id)
    assert setting is not None
    return setting


def settle(queue, setting):
    """Complete one request without removing a newer replacement."""
    assert queue.remove_if_current(setting)
    queue.release_sent(setting)


def test_only_head_ages_and_in_flight_pauses_promotion():
    """Waiters get their own five minutes after preceding transport releases."""
    queue, clock, drops = make_queue()
    first = enqueue(queue, "FIRST")
    second = enqueue(queue, "SECOND")
    third = enqueue(queue, "THIRD")
    queue.expire_waiting()
    assert queue.mark_sent(first)

    clock.advance(400)
    queue.expire_waiting(blocked_device_id="box")
    assert not drops
    settle(queue, first)
    queue.expire_waiting()

    clock.advance(299)
    queue.expire_waiting()
    assert queue.get_pending(unsent_only=True) == [second, third]
    clock.advance(1)
    queue.expire_waiting()
    assert drops == [(second.audit_id, "expired")]
    clock.advance(299)
    queue.expire_waiting()
    assert queue.get_pending(unsent_only=True) == [third]


def test_batch_expires_per_device_without_finishing_sent_request():
    """Batch aging continues during blocked delivery and is device-scoped."""
    queue, clock, drops = make_queue()
    first = enqueue(queue, "FIRST")
    second = enqueue(queue, "SECOND")
    assert queue.mark_sent(first)
    queue.expire_waiting(blocked_device_id="box")
    clock.advance(1799)
    another = enqueue(queue, "FIRST", device_id="other")
    queue.expire_waiting(blocked_device_id="box")

    clock.advance(1)
    queue.expire_waiting(blocked_device_id="box")
    assert drops == [(second.audit_id, "batch_expired")]
    assert queue.get_pending() == [first, another]
    assert queue.is_sent(first)
    settle(queue, first)

    replacement = enqueue(queue, "NEW")
    queue.expire_waiting()
    clock.advance(299)
    queue.expire_waiting()
    assert queue.get_pending(device_id="box") == [replacement]


def test_replay_preserves_identity_order_and_batch_deadline():
    """Only a reconciled replay gets a fresh five-minute wait as the head."""
    queue, clock, drops = make_queue()
    first = enqueue(queue, "FIRST")
    second = enqueue(queue, "SECOND")
    assert queue.mark_sent(first)
    clock.advance(1550)
    queue.expire_waiting(blocked_device_id="box")
    assert queue.retry_once(first)
    queue.expire_waiting()

    retry, waiting = queue.get_pending(unsent_only=True)
    assert waiting is second
    assert retry is not first and retry.retried and retry.audit_id == first.audit_id
    assert retry.enqueued_at == first.enqueued_at
    clock.advance(250)
    queue.expire_waiting()
    assert drops == [(first.audit_id, "batch_expired"), (second.audit_id, "batch_expired")]


def test_replay_gets_five_minutes_from_promotion():
    """Neither the first wait nor the first wire attempt ages a replay's head budget."""
    queue, clock, drops = make_queue()
    first = enqueue(queue, "FIRST")
    queue.expire_waiting()
    clock.advance(299)
    assert queue.mark_sent(first)
    clock.advance(60)
    assert queue.retry_once(first)
    queue.expire_waiting()
    retry, = queue.get_pending(unsent_only=True)
    assert retry.retried

    clock.advance(299)
    queue.expire_waiting()
    assert queue.is_current(retry)
    clock.advance(1)
    queue.expire_waiting()
    assert drops == [(first.audit_id, "expired")]


def test_adjacent_equal_values_reuse_original_identity_and_deadline():
    """Equivalent accepted values keep their first wire identity and expiry."""
    queue, clock, drops = make_queue()
    first = enqueue(queue, "MODE", 3)
    queue.expire_waiting()
    clock.advance(299)

    same, duplicate = queue.admit("tbl_box_prms", "MODE", "3.0", device_id="box")
    assert same is first and duplicate
    clock.advance(1)
    queue.expire_waiting()
    assert drops == [(first.audit_id, "expired")]


def test_duplicate_of_sent_identity_is_ignored_but_intervening_command_breaks_adjacency():
    """A, A, B, A preserves the latter request and completes the sent original independently."""
    queue, _, drops = make_queue()
    first = enqueue(queue, "MODE", 3)
    assert queue.mark_sent(first)
    assert queue.admit("tbl_box_prms", "MODE", 3, device_id="box") == (first, True)
    second = enqueue(queue, "OTHER")
    last, duplicate = queue.admit("tbl_box_prms", "MODE", 3, device_id="box")
    assert last is not None and last is not first and not duplicate
    assert queue.get_pending(unsent_only=True) == [second, last]
    assert not queue.remove_if_current(first)
    queue.release_sent(first)
    assert not drops


def test_different_value_or_confirm_replaces_unsent_same_key():
    """Only equal consecutive intent is deduplicated."""
    queue, _, drops = make_queue()
    first = enqueue(queue, "MODE", 3)
    next_value = enqueue(queue, "MODE", 1)
    next_confirm = enqueue(queue, "MODE", 1, confirm="Repeat")
    assert queue.get_pending() == [next_confirm]
    assert drops == [(first.audit_id, "replaced"), (next_value.audit_id, "replaced")]


def test_head_wait_stops_while_cloud_blocks_the_device():
    """Only time spent eligible counts toward an already promoted head's deadline."""
    queue, clock, drops = make_queue()
    setting = enqueue(queue, "MODE")
    clock.advance(100)
    queue.expire_waiting(blocked_device_id="box")
    clock.advance(500)
    queue.expire_waiting(blocked_device_id="box")
    assert queue.is_current(setting)
    queue.expire_waiting()
    clock.advance(200)
    queue.expire_waiting()
    assert drops == [(setting.audit_id, "expired")]


def test_expired_head_cannot_reach_wire_between_housekeeping_ticks():
    """Binding is the last point where a selected request can expire safely."""
    queue, clock, drops = make_queue()
    head = enqueue(queue, "FIRST")
    follower = enqueue(queue, "SECOND")
    clock.advance(300)

    assert not queue.mark_sent(head)
    assert drops == [(head.audit_id, "expired")]
    assert queue.get_pending() == [follower]
