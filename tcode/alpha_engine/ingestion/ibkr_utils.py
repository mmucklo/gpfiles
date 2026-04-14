"""
IBKR contract-qualification helper.

The root cause of Error 321 ("Invalid contract id") is passing an unqualified
contract (conId == 0) to any reqMktData / reqContractDetails / order call.

Use ensure_qualified() on any Contract before passing it to IBKR API calls
that require a valid conId.
"""
import logging

logger = logging.getLogger("IBKRUtils")


def ensure_qualified(ib, contract):
    """
    Return a qualified copy of `contract` (one with conId > 0).

    If the contract already has a valid conId, returns it unchanged.
    If qualification fails (IBKR returns no matching contracts), raises
    RuntimeError with a diagnostic message so the caller can log it and
    skip the contract rather than triggering Error 321 downstream.

    Usage:
        from ingestion.ibkr_utils import ensure_qualified
        qc = ensure_qualified(ib, Option("TSLA", "20260420", 350.0, "C", "SMART"))
        ticker = ib.reqMktData(qc, "", True, False)
    """
    if getattr(contract, "conId", 0) > 0:
        return contract

    details = ib.reqContractDetails(contract)
    if not details:
        raise RuntimeError(
            f"[CONTRACT-QUAL-FAIL] {contract.symbol} {getattr(contract, 'lastTradeDateOrContractMonth', '')} "
            f"strike={getattr(contract, 'strike', '')} right={getattr(contract, 'right', '')} — "
            "IBKR returned no contract details"
        )
    qc = details[0].contract
    logger.debug(
        "[CONTRACT-QUAL-OK] %s conId=%d",
        getattr(qc, "localSymbol", str(qc)), qc.conId,
    )
    return qc
