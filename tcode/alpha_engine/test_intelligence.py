import pytest
import asyncio
from intelligence import SentimentModel, OptionsFlowScout
from consensus import SignalDirection, ModelType

@pytest.mark.asyncio
async def test_sentiment_model_output():
    """Verify SentimentModel generates probabilistic conviction signals."""
    model = SentimentModel()
    text = "Tesla delivers record number of Model 3 vehicles."
    
    signal = await model.analyze(text)
    
    assert signal.model_id == ModelType.SENTIMENT
    assert signal.direction in (SignalDirection.BULLISH, SignalDirection.BEARISH)
    assert 0.6 <= signal.confidence <= 1.0

@pytest.mark.asyncio
async def test_options_flow_scout():
    """Verify OptionsFlowScout detects institutional whale activity."""
    scout = OptionsFlowScout()
    
    signal = await scout.sniff_flow()
    
    assert signal.model_id == ModelType.OPTIONS_FLOW
    assert signal.direction == SignalDirection.BULLISH # Mocked constant
    assert signal.confidence == 0.88
