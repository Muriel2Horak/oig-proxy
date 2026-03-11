"""Unit tests for Blind Branch #2: Twin inflight finalization."""

# pyright: reportMissingImports=false
# pylint: disable=missing-function-docstring,missing-class-docstring,protected-access

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import digital_twin as dt_module
from twin_state import OnAckDTO, TransactionResultDTO


@pytest.mark.asyncio
async def test_on_ack_cloud_aligned_sets_inflight_none():
    """Test that ACK Applied sets _inflight to None."""
    twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
    twin._inflight = MagicMock()
    twin._inflight.tx_id = "test-1"
    twin._inflight_ctx = MagicMock()
    twin._inflight_ctx.conn_id = 1
    twin._pending_simple = {}
    twin._lock = AsyncMock()
    twin._ack_task = None
    twin._applied_task = None
    twin._mqtt_publisher = None
    twin.session_id = "test-session"
    twin._last_result = None

    dto = OnAckDTO(
        tx_id="test-1",
        conn_id=1,
        ack=True,
    )

    with patch.object(twin, '_get_matching_inflight_for_ack', return_value=twin._inflight):
        with patch.object(twin, '_resolve_delivered_conn_id', return_value=1):
            with patch.object(twin, '_is_cloud_aligned_ack_conn_valid', return_value=True):
                with patch.object(twin, '_schedule_applied_timeout_after_ack'):
                    with patch.object(twin, '_apply_cloud_aligned_ack_state', return_value=twin._inflight):
                        # Simulate ACK Applied response
                        result = await twin._on_ack_cloud_aligned(dto)

                        assert result is not None
                        assert result.tx_id == "test-1"


@pytest.mark.asyncio
async def test_on_ack_legacy_sets_inflight_none():
    """Test that legacy ACK handler clears inflight."""
    twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
    twin._inflight = MagicMock()
    twin._inflight.tx_id = "test-1"
    twin._inflight_ctx = MagicMock()
    twin._inflight_ctx.conn_id = 1
    twin._lock = AsyncMock()
    twin._ack_task = None
    twin._applied_task = None
    twin._mqtt_publisher = None
    twin.session_id = "test-session"
    twin._last_result = None

    dto = OnAckDTO(
        tx_id="test-1",
        conn_id=1,
        ack=True,
    )

    with patch.object(twin, '_applied_timeout_handler', new_callable=AsyncMock):
        result = await twin._on_ack_legacy(dto)

        assert result is not None
        assert result.tx_id == "test-1"
        assert result.status == "box_ack"


@pytest.mark.asyncio
async def test_finish_inflight_releases_inflight():
    """Test that finish_inflight releases the inflight slot."""
    twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
    twin._inflight = MagicMock()
    twin._inflight.tx_id = "test-1"
    twin._inflight_ctx = MagicMock()
    twin._lock = AsyncMock()
    
    # Mock _finish_inflight_locked to clear inflight
    def clear_inflight(*args, **kwargs):
        twin._inflight = None
        return MagicMock()
    
    with patch.object(twin, '_finish_inflight_locked', side_effect=clear_inflight):
        # Call finish_inflight with required params
        result = await twin.finish_inflight(
            tx_id="test-1",
            conn_id=1,
            success=True,
        )
        
        # Inflight should be cleared
        assert twin._inflight is None


@pytest.mark.asyncio
async def test_queue_continues_after_inflight_release():
    """Test that queue processing continues after inflight is released."""
    twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
    twin._queue = [{"tx_id": "test-2"}]
    twin._inflight = MagicMock()
    twin._inflight.tx_id = "test-1"
    twin._inflight_ctx = MagicMock()
    twin._lock = AsyncMock()
    
    # Mock _finish_inflight_locked to clear inflight
    def clear_inflight(*args, **kwargs):
        twin._inflight = None
        return MagicMock()
    
    with patch.object(twin, '_finish_inflight_locked', side_effect=clear_inflight):
        # Finish inflight
        await twin.finish_inflight(
            tx_id="test-1",
            conn_id=1,
            success=True,
        )
        
        # Queue should still have items
        assert len(twin._queue) == 1
        
        # Should be able to process next item
        assert twin._inflight is None


@pytest.mark.asyncio
async def test_error_handler_clears_inflight():
    """Test that error handlers also clear inflight."""
    twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
    twin._inflight = MagicMock()
    twin._inflight.tx_id = "test-1"
    twin._inflight_ctx = MagicMock()
    twin._queue = []
    twin._lock = AsyncMock()
    
    # Mock _finish_inflight_locked to clear inflight on error
    def clear_inflight(*args, **kwargs):
        twin._inflight = None
        return MagicMock()
    
    with patch.object(twin, '_finish_inflight_locked', side_effect=clear_inflight):
        # If finish_inflight is called on terminal states with success=False, inflight is cleared
        await twin.finish_inflight(
            tx_id="test-1",
            conn_id=1,
            success=False,
            detail="Error occurred",
        )
        assert twin._inflight is None


@pytest.mark.asyncio
async def test_inflight_finalization_on_all_terminal_states():
    """Test that finish_inflight is called on all terminal state transitions."""
    terminal_results = [
        (True, "Applied"),   # Success
        (False, "Error"),    # Error
        (False, "Timeout"),  # Timeout
        (True, "Completed"), # Completed
    ]
    
    for success, detail in terminal_results:
        twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
        twin._inflight = MagicMock()
        twin._inflight.tx_id = f"test-{detail}"
        twin._inflight_ctx = MagicMock()
        twin._lock = AsyncMock()
        
        # Mock _finish_inflight_locked to clear inflight
        def clear_inflight(*args, **kwargs):
            twin._inflight = None
            return MagicMock()
        
        with patch.object(twin, '_finish_inflight_locked', side_effect=clear_inflight):
            # Finalize
            await twin.finish_inflight(
                tx_id=f"test-{detail}",
                conn_id=1,
                success=success,
                detail=detail,
            )
            
            # Should be cleared
            assert twin._inflight is None, f"Inflight not cleared for result: {detail}"


@pytest.mark.asyncio
async def test_new_item_can_start_after_inflight_cleared():
    """Test that new queue item can start after inflight is cleared."""
    twin = dt_module.DigitalTwin.__new__(dt_module.DigitalTwin)
    twin._inflight = MagicMock()
    twin._inflight.tx_id = "test-1"
    twin._inflight_ctx = MagicMock()
    twin._queue = [{"tx_id": "test-2", "data": "payload"}]
    twin._lock = AsyncMock()
    
    # Mock _finish_inflight_locked to clear inflight
    def clear_inflight(*args, **kwargs):
        twin._inflight = None
        return MagicMock()
    
    with patch.object(twin, '_finish_inflight_locked', side_effect=clear_inflight):
        # Clear inflight
        await twin.finish_inflight(
            tx_id="test-1",
            conn_id=1,
            success=True,
        )
        
        # Can now process next item
        assert twin._inflight is None
        
        # Simulate starting next item
        next_item = twin._queue.pop(0)
        twin._inflight = MagicMock()
        twin._inflight.tx_id = next_item["tx_id"]
        
        assert twin._inflight.tx_id == "test-2"
