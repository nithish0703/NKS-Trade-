"""
Detects Market Structure Shift (MSS) events.
"""

from typing import Sequence

from app.market_structure._ids import make_break_id
from app.market_structure.shift_results import (
    BreakConfirmation,
    StructureBreakResult,
    StructureBreakType,
)


class MSSDetector:
    """
    Promotes confirmed CHoCH events into Market Structure Shift (MSS)
    results: the confirmed institutional structure shift following a
    valid liquidity sweep and confirmed displacement.

    A follow-up confirmed BOS in the same direction, occurring after the
    CHoCH, is attached as supporting metadata but is never required —
    a valid CHoCH already satisfies the shift rules on its own.
    """

    def detect(
        self,
        bos_results: Sequence[StructureBreakResult],
        choch_results: Sequence[StructureBreakResult],
    ) -> list[StructureBreakResult]:
        """
        Detect MSS results from confirmed CHoCH events.

        A CHoCH is eligible to become MSS only when it is CONFIRMED, has
        a valid (confirmed) preceding liquidity sweep, a confirmed
        displacement, and is not a wick-only break. Each eligible CHoCH
        produces exactly one MSS result.
        """
        results: list[StructureBreakResult] = []
        seen_source_ids: set[str] = set()

        for choch in choch_results:
            if choch.confirmation != BreakConfirmation.CONFIRMED:
                continue
            if choch.preceding_liquidity_sweep is None or not choch.preceding_liquidity_sweep.confirmed:
                continue
            if choch.wick_only_break:
                continue
            if not choch.displacement.confirmed:
                continue
            if choch.break_id in seen_source_ids:
                continue
            seen_source_ids.add(choch.break_id)

            supporting_bos = self._find_supporting_bos(bos_results, choch)

            metadata = {"source_choch_break_id": choch.break_id}
            if supporting_bos is not None:
                metadata["supporting_bos_break_id"] = supporting_bos.break_id

            mss_break_id = make_break_id(
                choch.symbol,
                choch.timeframe,
                StructureBreakType.MSS,
                choch.broken_swing.swing_id,
                choch.break_candle_index,
            )

            results.append(
                StructureBreakResult(
                    break_id=mss_break_id,
                    symbol=choch.symbol,
                    timeframe=choch.timeframe,
                    break_type=StructureBreakType.MSS,
                    direction=choch.direction,
                    broken_swing=choch.broken_swing,
                    break_candle_timestamp=choch.break_candle_timestamp,
                    break_candle_index=choch.break_candle_index,
                    break_price=choch.break_price,
                    close_price=choch.close_price,
                    displacement=choch.displacement,
                    preceding_liquidity_sweep=choch.preceding_liquidity_sweep,
                    strong_close_beyond_structure=choch.strong_close_beyond_structure,
                    wick_only_break=choch.wick_only_break,
                    confirmation=BreakConfirmation.CONFIRMED,
                    reason=(
                        f"Confirmed {choch.direction.value.lower()} MSS derived from "
                        f"CHoCH {choch.break_id}."
                    ),
                    metadata=metadata,
                )
            )

        results.sort(key=lambda r: (r.break_candle_timestamp, r.break_id))
        return results

    @staticmethod
    def _find_supporting_bos(
        bos_results: Sequence[StructureBreakResult], choch: StructureBreakResult
    ):
        candidates = [
            bos
            for bos in bos_results
            if bos.confirmation == BreakConfirmation.CONFIRMED
            and bos.direction == choch.direction
            and bos.break_candle_timestamp >= choch.break_candle_timestamp
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda b: b.break_candle_timestamp)
