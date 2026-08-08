"""
Baseline snapshot: writes the funnel and performance reports to a
timestamped JSON file for later comparison (a future phase's
`--compare <baseline.json>`). Schema is versioned so later phases can
detect and handle an older baseline file's shape.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from app.analytics.funnel_report import DEFAULT_WINDOW_DAYS, generate_funnel_report
from app.analytics.performance_report import generate_performance_report
from app.storage.analytics_repository import AnalyticsRepository
from app.storage.signal_repository import SignalRepository

BASELINE_SCHEMA_VERSION = 1
DEFAULT_BASELINE_DIR = Path("data/baselines")


async def save_baseline(
    *,
    name: str,
    analytics_repository: AnalyticsRepository,
    signal_repository: SignalRepository,
    window_days: int = DEFAULT_WINDOW_DAYS,
    output_dir: Path = DEFAULT_BASELINE_DIR,
    now: datetime | None = None,
) -> Path:
    """
    Generate the funnel and performance reports for `window_days` days
    and write them to a single timestamped JSON file under
    `output_dir`. The timestamp in the filename means this never
    overwrites a prior baseline saved under the same `name`.

    Raises:
        ValueError: If `name` is empty.
    """
    if not name or not name.strip():
        raise ValueError("name must be a non-empty baseline identifier.")

    generated_at = now or datetime.now(timezone.utc)
    funnel = await generate_funnel_report(analytics_repository, window_days=window_days, now=generated_at)
    performance = await generate_performance_report(
        signal_repository, window_days=window_days, now=generated_at
    )

    payload = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "name": name,
        "generated_at_utc": generated_at.isoformat(),
        "window_days": window_days,
        "funnel": funnel.model_dump(mode="json"),
        "performance": performance.model_dump(mode="json"),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    file_path = output_dir / f"{name}_{timestamp}.json"
    file_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return file_path
