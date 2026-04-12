import pytest
from consensus import ConsensusEngine, ModelSignal, SignalDirection, ModelType

def test_consensus_agreement_bullish():
    """Test weighted consensus in a bullish scenario with 3 models."""
    engine = ConsensusEngine(agreement_threshold=0.6, min_models=3)
    
    signals = [
        ModelSignal(ModelType.SENTIMENT, SignalDirection.BULLISH, 0.8, 100),
        ModelSignal(ModelType.OPTIONS_FLOW, SignalDirection.BULLISH, 0.9, 101),
        ModelSignal(ModelType.MACRO, SignalDirection.BULLISH, 0.7, 102),
    ]
    
    consensus = engine.aggregate_signals(signals)
    assert consensus is not None
    assert consensus.direction == SignalDirection.BULLISH
    assert consensus.confidence > 0.6  # Agreement is high (1.0), so confidence ~ avg_conf (0.8)

def test_consensus_disagreement_rejection():
    """Test consensus failure when models are split."""
    engine = ConsensusEngine(agreement_threshold=0.6, min_models=3)
    
    signals = [
        ModelSignal(ModelType.SENTIMENT, SignalDirection.BULLISH, 0.9, 100),
        ModelSignal(ModelType.OPTIONS_FLOW, SignalDirection.BEARISH, 0.9, 101),
        ModelSignal(ModelType.MACRO, SignalDirection.BULLISH, 0.4, 102),
    ]
    
    # Weighted sum: (1*0.9) + (-1*0.9) + (1*0.4) = 0.4
    # Max possible: 0.9 + 0.9 + 0.4 = 2.2
    # Agreement score: 0.4 / 2.2 = 0.18 < 0.6
    consensus = engine.aggregate_signals(signals)
    assert consensus is None

def test_consensus_min_models_requirement():
    """Test that engine rejects if too few models report."""
    engine = ConsensusEngine(agreement_threshold=0.6, min_models=3)
    
    signals = [
        ModelSignal(ModelType.SENTIMENT, SignalDirection.BULLISH, 0.9, 100),
        ModelSignal(ModelType.OPTIONS_FLOW, SignalDirection.BULLISH, 0.9, 101),
    ]
    
    consensus = engine.aggregate_signals(signals)
    assert consensus is None

def test_consensus_neutral_exclusion():
    """Test that neutral signals dilute the consensus agreement score."""
    engine = ConsensusEngine(agreement_threshold=0.5, min_models=3)
    
    signals = [
        ModelSignal(ModelType.SENTIMENT, SignalDirection.BULLISH, 1.0, 100),
        ModelSignal(ModelType.OPTIONS_FLOW, SignalDirection.NEUTRAL, 1.0, 101),
        ModelSignal(ModelType.MACRO, SignalDirection.NEUTRAL, 1.0, 102),
    ]
    
    # Weighted sum: (1*1.0) + (0*1.0) + (0*1.0) = 1.0
    # Max weight: 3.0
    # Agreement: 1/3 = 0.33 < 0.5
    consensus = engine.aggregate_signals(signals)
    assert consensus is None
