import pytest
from risk_engine import RiskEngine, TradeProposal, PositionType, SentimentTrigger, TradeRejection

def test_ap001_earnings_iv_crush():
    """Test rejection of long premium < 7 days from earnings."""
    # Should reject
    trade = TradeProposal(
        position_type=PositionType.LONG_CALL,
        dte=10,
        days_to_earnings=5,
        delta=0.5,
        position_pnl=0.0,
        action_is_add=False,
        kelly_wager_pct=0.02,
        sentiment_trigger=SentimentTrigger.NONE,
        vix_level=20.0,
        spy_trend_bearish=False
    )
    with pytest.raises(TradeRejection, match="AP-001"):
        RiskEngine.evaluate_trade(trade)
        
    # Should allow (days to earnings >= 7)
    trade.days_to_earnings = 8
    assert RiskEngine.evaluate_trade(trade) == 0.02

def test_ap002_averaging_down_depleting():
    """Test rejection of averaging down on losing, short DTE options."""
    trade = TradeProposal(
        position_type=PositionType.LONG_CALL,
        dte=10,
        days_to_earnings=20,
        delta=0.5,
        position_pnl=-150.0,
        action_is_add=True,
        kelly_wager_pct=0.01,
        sentiment_trigger=SentimentTrigger.NONE,
        vix_level=20.0,
        spy_trend_bearish=False
    )
    with pytest.raises(TradeRejection, match="AP-002"):
        RiskEngine.evaluate_trade(trade)

def test_ap003_lottery_ticket():
    """Test rejection of low delta high wager trades."""
    trade = TradeProposal(
        position_type=PositionType.LONG_CALL,
        dte=30,
        days_to_earnings=20,
        delta=0.05,
        position_pnl=0.0,
        action_is_add=False,
        kelly_wager_pct=0.01, # 1% wager > 0.5% limit
        sentiment_trigger=SentimentTrigger.NONE,
        vix_level=20.0,
        spy_trend_bearish=False
    )
    with pytest.raises(TradeRejection, match="AP-003"):
        RiskEngine.evaluate_trade(trade)

def test_ap004_macro_correlation():
    """Test 50% wager penalty in high VIX bear trend."""
    trade = TradeProposal(
        position_type=PositionType.LONG_CALL,
        dte=30,
        days_to_earnings=20,
        delta=0.5,
        position_pnl=0.0,
        action_is_add=False,
        kelly_wager_pct=0.04,
        sentiment_trigger=SentimentTrigger.NONE,
        vix_level=26.0,
        spy_trend_bearish=True
    )
    approved_wager = RiskEngine.evaluate_trade(trade)
    assert approved_wager == 0.02  # 50% of 0.04

def test_ap005_elon_tweet_leverage():
    """Test max Quarter-Kelly constraint on social media triggers."""
    trade = TradeProposal(
        position_type=PositionType.LONG_CALL,
        dte=30,
        days_to_earnings=20,
        delta=0.5,
        position_pnl=0.0,
        action_is_add=False,
        kelly_wager_pct=0.08,
        sentiment_trigger=SentimentTrigger.SOCIAL_MEDIA,
        vix_level=20.0,
        spy_trend_bearish=False
    )
    approved_wager = RiskEngine.evaluate_trade(trade)
    assert approved_wager == 0.02  # 0.25 * 0.08

def test_fractional_kelly():
    """Test Fractional Kelly mathematical accuracy."""
    # Win probability 50%, win/loss ratio 2.0
    # f* = (2 * 0.5 - 0.5) / 2 = 0.5 / 2 = 0.25 Full Kelly
    # 0.25 * 0.25 fraction = 0.0625
    wager = RiskEngine.calculate_fractional_kelly(0.5, 2.0, fraction=0.25)
    assert wager == 0.0625
    
    # Negative expectancy, should be 0.0
    # Win probability 30%, win/loss ratio 1.0
    # f* = (1 * 0.3 - 0.7) / 1 = -0.4
    wager = RiskEngine.calculate_fractional_kelly(0.3, 1.0, fraction=0.25)
    assert wager == 0.0
