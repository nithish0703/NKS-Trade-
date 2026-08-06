"""
Computes take profit levels for a trade setup.
"""

import hashlib
import logging
from typing import Optional, Sequence

from app.config.thresholds import MIN_RISK_REWARD_RATIO
from app.liquidity.level_selector import get_institutional_priority
from app.liquidity.results import LiquidityLevel, LiquiditySide, LiquidityStrength
from app.market_structure.results import SwingPoint, SwingType
from app.models.trade_zone import TradeZone

from app.risk.results import TakeProfitCandidate, TakeProfitResult, TakeProfitSource

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


class SingleTakeProfitCalculator:
    """
    Selects exactly one take-profit target from a strictly prioritized
    set of candidate sources, requiring at least MIN_RISK_REWARD_RATIO
    risk-reward. Never returns more than one final target.
    """

    def calculate(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        institutional_liquidity_levels: Sequence[LiquidityLevel],
        major_swings: Sequence[SwingPoint],
        fair_value_gaps: Sequence[TradeZone],
    ) -> TakeProfitResult:
        """
        Calculate the single best take-profit target.

        Generates candidates from every source -- major institutional
        liquidity, strong swing extremes, unmitigated liquidity pools,
        and FVG completion -- unconditionally, before any selection.
        Each candidate's own RR is computed independently; wrong-side
        and sub-MIN_RISK_REWARD_RATIO candidates are marked invalid but
        never removed from the audit trail. Among all RR-valid
        candidates, selects by (1) higher institutional probability,
        (2) higher RR, (3) shorter distance from entry, (4) target_id
        as a stable deterministic tie-break. Returns invalid only when
        no candidate from any source meets the minimum risk-reward
        ratio.
        """
        if entry_price <= 0 or stop_loss <= 0:
            return self._invalid_result(
                direction, entry_price, stop_loss, [], "Entry price or stop loss is invalid.",
                "INVALID_ENTRY_OR_STOP",
            )
        if direction not in ("BUY", "SELL"):
            return self._invalid_result(
                direction, entry_price, stop_loss, [], f"Unrecognized direction '{direction}'.",
                "INVALID_ENTRY_OR_STOP",
            )
        if (direction == "BUY" and stop_loss >= entry_price) or (
            direction == "SELL" and stop_loss <= entry_price
        ):
            return self._invalid_result(
                direction, entry_price, stop_loss, [], "Stop loss is on the wrong side of entry.",
                "INVALID_ENTRY_OR_STOP",
            )

        candidates: list[TakeProfitCandidate] = []
        candidates.extend(
            self._institutional_liquidity_candidates(
                direction, entry_price, stop_loss, institutional_liquidity_levels
            )
        )
        candidates.extend(
            self._strong_swing_candidates(direction, entry_price, stop_loss, major_swings)
        )
        candidates.extend(
            self._unmitigated_pool_candidates(
                direction, entry_price, stop_loss, institutional_liquidity_levels
            )
        )
        candidates.extend(
            self._fvg_completion_candidates(direction, entry_price, stop_loss, fair_value_gaps)
        )

        if not candidates:
            self._log_analysis(direction, entry_price, stop_loss, candidates, selected=None)
            return self._invalid_result(
                direction, entry_price, stop_loss, candidates,
                "No institutional take-profit targets are available.",
                "NO_INSTITUTIONAL_TARGETS",
            )

        valid_candidates = [c for c in candidates if c.valid]

        if not valid_candidates:
            self._log_analysis(direction, entry_price, stop_loss, candidates, selected=None)
            return self._invalid_result(
                direction, entry_price, stop_loss, candidates,
                "No take-profit target meets the minimum risk-reward ratio.",
                "NO_TARGET_WITH_MINIMUM_RR",
            )

        # Selection priority among every RR-valid candidate (all sources
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

        self._log_analysis(direction, entry_price, stop_loss, candidates, selected=selected)

        return TakeProfitResult(
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            selected_take_profit=selected.price,
            selected_source=selected.source,
            selected_target_id=selected.target_id,
            risk_reward_ratio=selected.risk_reward_ratio,
            candidates=candidates,
            valid=True,
            reason="VALID_SINGLE_TAKE_PROFIT",
        )

    def _institutional_liquidity_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        levels: Sequence[LiquidityLevel],
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
                )
            )
        return candidates

    def _strong_swing_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        major_swings: Sequence[SwingPoint],
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
                )
            )
        return candidates

    def _unmitigated_pool_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        levels: Sequence[LiquidityLevel],
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
                )
            )
        return candidates

    def _fvg_completion_candidates(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        fair_value_gaps: Sequence[TradeZone],
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

        rr = _calculate_rr(direction, entry_price, stop_loss, price)
        valid = rr >= MIN_RISK_REWARD_RATIO

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
                else f"Risk-reward {rr:.4f} is below the minimum {MIN_RISK_REWARD_RATIO:.4f}."
            ),
        )

    @staticmethod
    def _log_analysis(
        direction: str,
        entry_price: float,
        stop_loss: float,
        candidates: list[TakeProfitCandidate],
        *,
        selected: Optional[TakeProfitCandidate],
    ) -> None:
        """
        Emit a DEBUG-only audit trail of every candidate considered and
        which one (if any) was selected, matching the module's existing
        docstring conventions -- WHY the selection landed where it did,
        not just WHAT was returned. Never affects control flow: this is
        purely observational logging, guarded so it costs nothing when
        DEBUG logging is disabled (the default in production).
        """
        if not _logger.isEnabledFor(logging.DEBUG):
            return

        lines = [
            "Take Profit Analysis",
            f"Entry: {entry_price}",
            f"Stop Loss: {stop_loss}",
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
                f"RR >= {MIN_RISK_REWARD_RATIO:.2f} (RR={selected.risk_reward_ratio:.4f})."
            )
        else:
            lines.append(f"Rejected: No TP satisfies RR >= {MIN_RISK_REWARD_RATIO:.2f}")

        _logger.debug("\n".join(lines))

    @staticmethod
    def _invalid_result(
        direction: str,
        entry_price: float,
        stop_loss: float,
        candidates: list[TakeProfitCandidate],
        reason: str,
        rejection_code: str,
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
            candidates=candidates,
            valid=False,
            reason=reason,
            metadata={"rejection_code": rejection_code},
        )
