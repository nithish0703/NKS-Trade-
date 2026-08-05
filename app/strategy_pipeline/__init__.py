"""
New institutional strategy pipeline, replacing the legacy 14-stage
InstitutionalSMCStrategyEngine.

Flow:

    HTF Bias (1H)
        v
    Liquidity Sweep
        v
    BOS (Trade idea valid?)
        v
    IFVG (Good entry location?)
        v
    OI + CVD (Order flow agrees?)
        v
    Signal

Each stage is a small, independently testable pure-function module under
this package, built and verified in isolation before being wired into a
replacement strategy engine. This package is self-contained during
staged development: nothing here is imported by the live
InstitutionalSMCStrategyEngine or app.scanner.engine_factory until the
final, separately-approved cutover.
"""
