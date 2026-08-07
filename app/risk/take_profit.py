"""
Computes take profit levels for a trade setup.
"""

import hashlib
import logging
from typing import Optional, Sequence

from app.config.thresholds import (
    MAX_TAKE_PROFIT_ATR_MULTIPLIER,
    MIN_RISK_REWARD_BY_STOP_SOURCE,
    MIN_RISK_REWARD_RATIO,
    PARTIAL_EXIT_TP1_PERCENTAGE,
    PARTIAL_EXIT_TP1_RISK_REWARD_RATIO,
    PARTIAL_EXIT_TP2_PERCENTAGE,
)
from app.liquidity.level_selector import get_institutional_priority
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength
from app.market_structure.results import SwingPoint, SwingType
from app.models.trade_zone import TradeZone

from app.risk.results import StopLossSource, TakeProfitCandidate, TakeProfitResult, TakeProfitSource

_logger = logging.getLogger(__name__)

# Strategy priority: lower number = higher priority.
_SOURCE_PRIORITY = {
    TakeProfitSource.MAJOR_INSTITUTIONAL_LIQUIDITY: 1,
    TakeProfitSource.STRONG_SWING_HIGH: 2,
    TakeProfitSource.STRONG_SWING_LOW: 2,
    TakeProfitSource.UNMITIGATED_LIQUIDITY_POOL: 3,
    TakeProfitSource.FAIR_VALUE_GAP_COMPLETION: 4,
}


def _make_target_id(source: TakeProfitSource, price: float, reference: str) -> str:
    raw = f"{source.value}|{price}|{reference}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _calculate_rr(direction: str, entry_price: float, stop_loss: float, target: float) -> float:
    if direction == "BUY":
        risk = entry_price - stop_loss
        reward = target - entry_price
    else:
        risk = stop_loss - entry_price
        reward = entry_price - target
    if risk <= 0:
        return 0.0
    return reward / risk


def _required_min_rr(stop_loss_source: Optional[StopLossSource]) -> float:
    """
    The minimum acceptable risk-reward ratio depends on how confident
    the selected stop-loss source is. A stop with no real structural
    backing (raw ATR) has to be justified by a bigger reward than a
    stop anchored to a proven liquidity-sweep extreme. Falls back to
    the flat MIN_RISK_REWARD_RATIO when the source is unknown.
    """
    if stop_loss_source is None:
        return MIN_RISK_REWARD_RATIO
    return MIN_RISK_REWARD_BY_STOP_SOURCE.get(stop_loss_source.value, MIN_RISK_REWARD_RATIO)


class SingleTakeProfitCalculator:
    """
    Selects a volatility-aware take-profit plan from a strictly
    prioritized set of candidate sources.

    A single structurally selected final target (TP2) is chosen exactly
    as before, except two things now gate validity in addition to the
    old on-side/minimum-RR checks:

    1. Dynamic RR: the minimum required risk-reward ratio scales with
       how structurally certain the selected stop-loss source is (see
       `_required_min_rr`), instead of a single flat ratio for every
       trade.
    2. Volatility ceiling: a candidate farther than
       MAX_TAKE_PROFIT_ATR_MULTIPLIER * atr from entry is rejected as
       unrealistically far -- a good RR number on paper is not enough
       if the target sits outside the price's normal reach.

    On top of the final target, a proportional TP1 partial-exit level
    is derived at a fixed, modest risk-reward
    (PARTIAL_EXIT_TP1_RISK_REWARD_RATIO) so a portion of the position
    can be closed early if price reaches it before continuing on to the
    full TP2 target.
    """

    def calculate(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        institutional_liquidity_levels: Sequence[LiquidityLevel],
        major_swings: Sequence[SwingPoint],
        fair_value_gaps: Sequence[TradeZone],
        atr: Optional[float] = None,
        stop_loss_source: Optional[StopLossSource] = None,
    ) -> TakeProfitResult:
        """
        Calculate the take-profit plan (TP1 partial exit + TP2 final
        target).

        Generates candidates from every source -- major institutional
        liquidity, strong swing extremes, unmitigated liquidity pools,
        and FVG completion -- unconditionally, before any selection.
        Each candidate's own RR is computed independently; wrong-side,
        sub-minimum-RR, and beyond-volatility-ceiling candidates are
        marked invalid but never removed from the audit trail. Among
        all valid candidates, selects by (1) higher institutional
        probability, (2) higher RR, (3) shorter distance from entry,
        (4) target_id as a stable deterministic tie-break. Returns
        invalid when no candidate from any source meets the dynamic
        minimum risk-reward ratio and the volatility ceiling.

        `atr` enables the volatility-ceiling check; when omitted, that
        check is skipped (treated as unbounded) so existing callers
        that don't pass ATR keep working. `stop_loss_source` enables
        the dynamic minimum-RR check; when omitted, the flat
        MIN_RISK_REWARD_RATIO is used.
        """
        required_min_rr = _required_min_rr(stop_loss_source)
        max_target_distance = (
            atr * MAX_TAKE_PROFIT_ATR_MULTIPLIER if atr is not None and atr > 0 else None
        )

        if entry_price <= 0 or stop_loss <= 0:
            return self._invalid_result(
                direction, entry_price, stop_loss, [], "Entry price or stop loss is invalid.",
                "INVALID_ENTRY_OR_STOP", required_min_rr,
            )
        if direction not in ("BUY", "SELL"):
            return self._invalid_result(
                direction, entry_price, stop_loss, [], f"Unrecognized direction '{direction}'.",
                "INVALID_ENTRY_OR_STOP", required_min_rr,
            )
        if (direction == "BUY" and stop_loss >= entry_price) or (
            direction == "SELL" and stop_loss <= entry_price
        ):
            return self._invalid_result(
                direction, entry_price, stop_loss, [], "Stop loss is on the wrong side of entry.",
                "INVALID_ENTRY_OR_STOP", required_min_rr,
            )

        candidates: list[TakeProfitCandidate] = []
        candidates.extend(
            self._institutional_liquidity_candidates(
                direction, entry_price, stop_loss, institutional_liquidity_levels,
                required_min_rr, max_target_distance,
            )
        )
        candidates.extend(
            self._strong_swing_candidates(
                direction, entry_price, stop_loss, major_swings, required_min_rr, max_target_distance
            )
        )
        candidates.extend(
            self._unmitigated_pool_candidates(
                direction, entry_price, stop_loss, institutional_liquidity_levels,
                required_min_rr, max_target_distance,
            )
        )
        candidates.extend(
            self._fvg_completion_candidates(
                direction, entry_price, stop_loss, fair_value_gaps, required_min_rr, max_target_distance
            )
        )

        if not candidates:
            self._log_analysis(direction, entry_price, stop_loss, candidates, required_min_rr, selected=None)
            return self._invalid_result(
                direction, entry_price, stop_loss, candidates,
                "No institutional take-profit targets are available.",
                "NO_INSTITUTIONAL_TARGETS", required_min_rr,
            )

        valid_candidates = [c for c in candidates if c.valid]

        if not valid_candidates:
            self._log_analysis(direction, entry_price, stop_loss, candidates, required_min_rr, selected=None)
            return self._invalid_result(
                direction, entry_price, stop_loss, candidates,
                "No take-profit target meets the minimum risk-reward ratio and volatility ceiling.",
                "NO_TARGET_WITH_MINIMUM_RR", required_min_rr,
            )

        # Selection priority among every valid candidate (all sources
        # are always generated above; this only changes which one wins):
        #   1. Higher institutional probability (lower _SOURCE_PRIORITY number)
        #   2. Higher risk-reward ratio
        #   3. Shorter distance from entry
        #   4. target_id, for a stable deterministic tie-break
        selected = min(
            valid_candidates,
            key=lambda c: (
                _SOURCE_PRIORITY[c.source],
                -c.risk_reward_ratio,
                c.distance_from_entry,
                c.target_id,
            ),
        )

        self._log_analysis(direction, entry_price, stop_loss, candidates, required_min_rr, selected=selected)

        tp1_price = self._tp1_price(direction, entry_price, stop_loss)
        tp1_rr = _calculate_rr(direction, entry_price, stop_loss, tp1_price)

        return TakeProfitResult(
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            selected_take_profit=selected.price,
            selected_source=selected.source,
            selected_target_id=selected.target_id,
            risk_reward_ratio=selected.risk_reward_ratio,
            required_risk_reward_ratio=required_min_rr,
            tp1_price=tp1_price,
            tp1_risk_reward_ratio=tp1_rr,
            tp1_exit_percentage=PARTIAL_EXIT_TP1_PERCENTAGE,
            tp2_price=selected.price,
            tp2_risk_reward_ratio=selected.risk_reward_ratio,
            tp2_exit_percentage=PARTIAL_EXIT_TP2_PERCENTAGE,
            candidates=candidates,
            valid=True,
            reason="VALID_TAKE_PROFIT_PLAN",
        )

    @staticmethod
    def _tp1_price(direction: str, entry_price: float, stop_loss: float) -> float:
        """
        TP1 sits at a fixed, modest risk-reward
        (PARTIAL_EXIT_TP1_RISK_REWARD_RATIO) proportional to the actual
        selected stop-loss distance -- not a fixed price offset -- so it
        automatically tightens or widens with however tight or wide the
        stop turned out to be.
        """
        if direction == "BUY":
            risk_distance = entry_price - stop_loss
            return entry_price + (risk_distance * PARTIAL_EXIT_TP1_RISK_REWARD_RATIO)
        risk_distance = stop_loss - entry_price
        return entry_price - (risk_distance * PARTIAL_EXIT_TP1_RISK_REWARD_RATIO)

    def _institutional_liquidity_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        levels: Sequence[LiquidityLevel],
        required_min_rr: float,
        max_target_distance: Optional[float],
    ) -> list[TakeProfitCandidate]:
        required_side = LiquiditySide.BUY_SIDE if direction == "BUY" else LiquiditySide.SELL_SIDE
        eligible = [
            level
            for level in levels
            if level.active
            and level.strength in (LiquidityStrength.STRONG, LiquidityStrength.INSTITUTIONAL)
            and level.liquidity_side == required_side
        ]
        eligible.sort(key=lambda lvl: (get_institutional_priority(lvl), abs(lvl.price - entry_price)))

        candidates = []
        for level in eligible:
            candidates.append(
                self._build_candidate(
                    TakeProfitSource.MAJOR_INSTITUTIONAL_LIQUIDITY,
                    level.price,
                    direction,
                    entry_price,
                    stop_loss,
                    reference=level.liquidity_id,
                    probability_rank=1,
                    required_min_rr=required_min_rr,
                    max_target_distance=max_target_distance,
                )
            )
        return candidates

    def _strong_swing_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        major_swings: Sequence[SwingPoint],
        required_min_rr: float,
        max_target_distance: Optional[float],
    ) -> list[TakeProfitCandidate]:
        required_type = SwingType.HIGH if direction == "BUY" else SwingType.LOW
        source = (
            TakeProfitSource.STRONG_SWING_HIGH if direction == "BUY" else TakeProfitSource.STRONG_SWING_LOW
        )
        eligible = [s for s in major_swings if s.confirmed and s.swing_type == required_type]
        eligible.sort(key=lambda s: abs(s.price - entry_price))

        candidates = []
        for swing in eligible:
            candidates.append(
                self._build_candidate(
                    source,
                    swing.price,
                    direction,
                    entry_price,
                    stop_loss,
                    reference=swing.swing_id,
                    probability_rank=2,
                    required_min_rr=required_min_rr,
                    max_target_distance=max_target_distance,
                )
            )
        return candidates

    def _unmitigated_pool_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        levels: Sequence[LiquidityLevel],
        required_min_rr: float,
        max_target_distance: Optional[float],
    ) -> list[TakeProfitCandidate]:
        required_side = LiquiditySide.BUY_SIDE if direction == "BUY" else LiquiditySide.SELL_SIDE
        eligible = [
            level
            for level in levels
            if level.active
            and level.strength in (LiquidityStrength.STRONG, LiquidityStrength.INSTITUTIONAL)
            and level.liquidity_side == required_side
        ]
        eligible.sort(key=lambda lvl: abs(lvl.price - entry_price))

        candidates = []
        for level in eligible:
            candidates.append(
                self._build_candidate(
                    TakeProfitSource.UNMITIGATED_LIQUIDITY_POOL,
                    level.price,
                    direction,
                    entry_price,
                    stop_loss,
                    reference=level.liquidity_id,
                    probability_rank=3,
                    required_min_rr=required_min_rr,
                    max_target_distance=max_target_distance,
                )
            )
        return candidates

    def _fvg_completion_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        fair_value_gaps: Sequence[TradeZone],
        required_min_rr: float,
        max_target_distance: Optional[float],
    ) -> list[TakeProfitCandidate]:
        required_direction = "SELL" if direction == "BUY" else "BUY"
        eligible = [z for z in fair_value_gaps if z.direction == required_direction]
        eligible.sort(key=lambda z: abs((z.upper_price if direction == "BUY" else z.lower_price) - entry_price))

        candidates = []
        for zone in eligible:
            price = zone.upper_price if direction == "BUY" else zone.lower_price
            candidates.append(
                self._build_candidate(
                    TakeProfitSource.FAIR_VALUE_GAP_COMPLETION,
                    price,
                    direction,
                    entry_price,
                    stop_loss,
                    reference=zone.zone_id,
                    probability_rank=4,
                    required_min_rr=required_min_rr,
                    max_target_distance=max_target_distance,
                )
            )
        return candidates

    @staticmethod
    def _build_candidate(
        source: TakeProfitSource,
        price: float,
        direction: str,
        entry_price: float,
        stop_loss: float,
        reference: str,
        probability_rank: int,
        required_min_rr: float,
        max_target_distance: Optional[float],
    ) -> TakeProfitCandidate:
        target_id = _make_target_id(source, price, reference)
        distance = abs(price - entry_price)

        on_correct_side = (price > entry_price) if direction == "BUY" else (price < entry_price)

        if not on_correct_side:
            return TakeProfitCandidate(
                source=source,
                target_id=target_id,
                price=price,
                distance_from_entry=distance,
                risk_reward_ratio=0.0,
                probability_rank=probability_rank,
                valid=False,
                reason="TARGET_ON_WRONG_SIDE",
            )

        if max_target_distance is not None and distance > max_target_distance:
            rr = _calculate_rr(direction, entry_price, stop_loss, price)
            return TakeProfitCandidate(
                source=source,
                target_id=target_id,
                price=price,
                distance_from_entry=distance,
                risk_reward_ratio=rr,
                probability_rank=probability_rank,
                valid=False,
                reason=(
                    f"Target distance {distance:.8f} exceeds the realistic volatility "
                    f"ceiling of {max_target_distance:.8f}; rejected as unrealistically far."
                ),
            )

        rr = _calculate_rr(direction, entry_price, stop_loss, price)
        valid = rr >= required_min_rr

        return TakeProfitCandidate(
            source=source,
            target_id=target_id,
            price=price,
            distance_from_entry=distance,
            risk_reward_ratio=rr,
            probability_rank=probability_rank,
            valid=valid,
            reason=(
                "Valid target meeting minimum risk-reward."
                if valid
                else f"Risk-reward {rr:.4f} is below the required minimum {required_min_rr:.4f}."
            ),
        )

    @staticmethod
    def _log_analysis(
        direction: str,
        entry_price: float,
        stop_loss: float,
        candidates: list[TakeProfitCandidate],
        required_min_rr: float,
        *,
        selected: Optional[TakeProfitCandidate],
    ) -> None:
        """
        Emit a DEBUG-only audit trail of every candidate considered and
        which one (if any) was selected -- WHY the selection landed
        where it did, not just WHAT was returned. Never affects control
        flow: purely observational, guarded so it costs nothing when
        DEBUG logging is disabled (the default in production).
        """
        if not _logger.isEnabledFor(logging.DEBUG):
            return

        lines = [
            "Take Profit Analysis",
            f"Entry: {entry_price}",
            f"Stop Loss: {stop_loss}",
            f"Required RR: {required_min_rr:.4f}",
        ]
        for source, label in (
            (TakeProfitSource.MAJOR_INSTITUTIONAL_LIQUIDITY, "Institutional"),
            (TakeProfitSource.STRONG_SWING_HIGH, "Swing"),
            (TakeProfitSource.STRONG_SWING_LOW, "Swing"),
            (TakeProfitSource.UNMITIGATED_LIQUIDITY_POOL, "Liquidity Pool"),
            (TakeProfitSource.FAIR_VALUE_GAP_COMPLETION, "FVG"),
        ):
            source_candidates = [c for c in candidates if c.source == source]
            if not source_candidates:
                continue
            lines.append(label)
            for candidate in source_candidates:
                lines.append(f"Target: {candidate.price}")
                lines.append(f"RR: {candidate.risk_reward_ratio:.4f}")
                lines.append("PASS" if candidate.valid else "FAIL")

        if selected is not None:
            lines.append(f"Selected: {selected.source.value} @ {selected.price}")
            lines.append(
                f"Reason: highest institutional probability among candidates meeting "
                f"RR >= {required_min_rr:.2f} (RR={selected.risk_reward_ratio:.4f})."
            )
        else:
            lines.append(f"Rejected: No TP satisfies RR >= {required_min_rr:.2f} within the volatility ceiling")

        _logger.debug("\n".join(lines))

    @staticmethod
    def _invalid_result(
        direction: str,
        entry_price: float,
        stop_loss: float,
        candidates: list[TakeProfitCandidate],
        reason: str,
        rejection_code: str,
        required_min_rr: float,
    ) -> TakeProfitResult:
        safe_entry = entry_price if entry_price > 0 else 1.0
        safe_stop = stop_loss if stop_loss > 0 else 1.0
        return TakeProfitResult(
            direction=direction,
            entry_price=safe_entry,
            stop_loss=safe_stop,
            selected_take_profit=None,
            selected_source=None,
            selected_target_id=None,
            risk_reward_ratio=None,
            required_risk_reward_ratio=required_min_rr,
            candidates=candidates,
            valid=False,
            reason=reason,
            metadata={"rejection_code": rejection_code},
        )
