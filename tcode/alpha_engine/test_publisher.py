import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from publisher import SignalPublisher
from consensus import ModelSignal, SignalDirection, ModelType

@pytest.mark.asyncio
async def test_publisher_connect():
    """Test NATS connection logic with mocking."""
    publisher = SignalPublisher(nats_url="nats://localhost:4222")
    
    with patch("nats.connect", new_callable=AsyncMock) as mock_connect:
        await publisher.connect()
        mock_connect.assert_called_once_with("nats://localhost:4222")

@pytest.mark.asyncio
async def test_publisher_publish():
    """Test signal publication payload and flush."""
    publisher = SignalPublisher()
    publisher.nc = AsyncMock()
    
    test_sig = ModelSignal(ModelType.MACRO, SignalDirection.BULLISH, 0.95, 123456789.0)
    
    await publisher.publish_signal(test_sig)
    
    # Verify NATS publish was called with correct subject and JSON payload
    publisher.nc.publish.assert_called_once()
    args, _ = publisher.nc.publish.call_args
    assert args[0] == "tsla.alpha.signals"
    assert b"BULLISH" in args[1]
    assert b"0.95" in args[1]
    
    publisher.nc.flush.assert_called_once()
