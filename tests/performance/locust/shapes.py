"""Staged load profile.

A flat ``-u 50 -r 5`` run answers exactly one question: what happens at fifty
users. It cannot tell you where the knee is, whether the service recovers from
a burst, or whether latency creeps upward while the load is held constant -
which is how connection-pool exhaustion and unbounded caches announce
themselves.

This shape walks the system through five stages and, because Locust records the
timestamp of every request, the resulting chart is readable stage by stage.

Run it by loading this module alongside the locustfile::

    locust -f tests/performance/locust/locustfile.py,tests/performance/locust/shapes.py \
        --headless --host http://127.0.0.1:8000

``-u`` and ``-r`` are ignored while a shape is active: the shape owns the user
count. ``-t`` is not needed either - the run ends when the last stage does.

Scale the whole profile without editing this file:

    PERF_SHAPE_USER_SCALE=2.0   double every user count
    PERF_SHAPE_TIME_SCALE=0.1   run a 90-second rehearsal of the 15-minute profile
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from locust import LoadTestShape

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


USER_SCALE: float = _float_env("PERF_SHAPE_USER_SCALE", 1.0)
TIME_SCALE: float = _float_env("PERF_SHAPE_TIME_SCALE", 1.0)


@dataclass(frozen=True)
class Stage:
    """One step of the profile.

    ``ends_at`` is measured from the start of the run, not from the previous
    stage, because that is the number the chart's x-axis shows - which makes
    lining a latency spike up against a stage boundary a matter of reading
    across rather than of adding durations up.
    """

    name: str
    ends_at: int
    users: int
    spawn_rate: float
    why: str


STAGES: tuple[Stage, ...] = (
    Stage(
        name="warm-up",
        ends_at=60,
        users=5,
        spawn_rate=1,
        why=(
            "Fill the connection pool, let SQLAlchemy compile its statements and give the "
            "JIT-free Python process a chance to touch every code path once. Requests here "
            "are the slowest of the whole run and they are not a defect - excluding this "
            "stage is why the numbers that follow are trustworthy."
        ),
    ),
    Stage(
        name="ramp",
        ends_at=240,
        users=40,
        spawn_rate=2,
        why=(
            "Climb gradually to the target. A slow spawn rate separates 'the service is "
            "slow at 40 users' from 'the service is slow because 40 users arrived at once' - "
            "two different problems with two different fixes."
        ),
    ),
    Stage(
        name="steady",
        ends_at=540,
        users=40,
        spawn_rate=2,
        why=(
            "Hold the target for five minutes. This is the only stage whose percentiles "
            "mean anything, and its shape is the real finding: latency that drifts upward "
            "under constant load is a leak - pool handles, cached objects, unbounded "
            "in-memory state - that a shorter run would report as a healthy average."
        ),
    ),
    Stage(
        name="spike",
        ends_at=660,
        users=120,
        spawn_rate=20,
        why=(
            "Triple the load in six seconds, the way a promo email or a social media post "
            "does. Errors here are acceptable; what is being measured is whether the "
            "service sheds load cleanly (fast 5xx, honest 409s) or collapses into timeouts "
            "and half-written orders."
        ),
    ),
    Stage(
        name="recovery",
        ends_at=780,
        users=40,
        spawn_rate=20,
        why=(
            "Drop straight back to the steady-state level. The question is whether "
            "latency returns to its pre-spike baseline within a couple of minutes. If it "
            "does not, something did not clean up after the burst, and that is a far more "
            "serious finding than the spike's error rate."
        ),
    ),
    Stage(
        name="ramp-down",
        ends_at=900,
        users=5,
        spawn_rate=10,
        why=(
            "Wind down gently rather than cutting to zero, so in-flight checkouts finish "
            "and are recorded. Killing users mid-request would leave orders in 'pending' "
            "and make the post-run database state impossible to interpret."
        ),
    ),
)


class StagedRampShape(LoadTestShape):
    """Warm-up -> ramp -> steady -> spike -> recovery -> ramp-down."""

    def __init__(self) -> None:
        super().__init__()
        self._announced: str | None = None

    def tick(self) -> tuple[int, float] | None:
        run_time = self.get_run_time()
        for stage in STAGES:
            if run_time < stage.ends_at * TIME_SCALE:
                self._announce(stage)
                # Always at least one user, so a small USER_SCALE cannot
                # accidentally end the run by asking for zero.
                return max(1, round(stage.users * USER_SCALE)), stage.spawn_rate
        return None  # Past the last stage: Locust stops the run.

    def _announce(self, stage: Stage) -> None:
        """Log each transition so the console log can be aligned with the chart."""
        if self._announced == stage.name:
            return
        self._announced = stage.name
        logger.info(
            "Load stage '%s': %d users at %.1f/s until t=%ds",
            stage.name,
            max(1, round(stage.users * USER_SCALE)),
            stage.spawn_rate,
            round(stage.ends_at * TIME_SCALE),
        )
