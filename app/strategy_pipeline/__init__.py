"""
Institutional strategy pipeline: the sole strategy engine.

Flow:

    HTF Bias (1H)
        v
    Liquidity Sweep
        v
    BOS (Trade idea valid?)
        v
    IFVG (Good entry location?)
        v
    Volume Profile + CVD (Order flow agrees?)
        v
    Signal

Each stage is a small, independently testable pure-function module
under this package, orchestrated by PipelineStrategyEngine
(app.strategy_pipeline.engine) and constructed via
build_pipeline_strategy_engine() (app.strategy_pipeline.factory).
"""
