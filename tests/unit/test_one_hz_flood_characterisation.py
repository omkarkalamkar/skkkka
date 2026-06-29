"""SKB-1306: does a ~1 Hz emit rate, on its own, saturate the SignalBus?

This characterises the question directly against the real ``ska_tango_base``
SignalBus, instead of assuming the answer.

Finding
-------
The bus processes emissions one at a time on a single background thread. A
backlog (and therefore the 3.2s request-side timeout) builds up only when
processing one emission takes longer than the gap between emissions.

- At ~1 Hz with realistic (fast) delivery, each emission drains long before the
  next arrives, so the request-side wait stays fast -> **1 Hz alone does NOT
  saturate the bus**.
- The bus only backs up when *delivery* (e.g. ``push_change_event`` to slow /
  contended subscribers) is slower than the emit interval.

This is exactly why the RCA treats "1 Hz flood -> timeout" as a contributing
factor amplified by slow/contended delivery, not as a standalone cause. The fix
is still correct regardless: it removes the avoidable redundant emissions, so
the availability signal can never contribute to such a backlog.
"""

import logging
import time

import pytest
from ska_tango_base.software_bus import TimedOutError, _SignalBus

REQUEST_TIMEOUT = 3.2  # SignalBusMixin._CLIENT_NEW_REQUEST_TIMEOUT


class _Observer:
    """Observer with a configurable per-emission delivery cost."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.count = 0

    def notify_emission(self, signal: str, value: object) -> None:
        if self.delay:
            time.sleep(self.delay)
        self.count += 1


def test_one_hz_with_fast_delivery_does_not_saturate():
    """~1 Hz emits with fast delivery never build a backlog."""
    bus = _SignalBus(logger=logging.getLogger("one-hz-test"))
    observer = _Observer(delay=0.001)  # ~1ms per delivery (realistic, fast)
    bus.register_observer(observer)
    bus.start_thread()
    ticks = 5
    try:
        for _ in range(ticks):
            bus.emit("isSubsystemAvailable", True)
            start = time.monotonic()
            bus.wait_for_thread(REQUEST_TIMEOUT)  # must not raise
            # Drains almost instantly -- nowhere near the 3.2s budget.
            assert time.monotonic() - start < 0.2
            time.sleep(1.0)  # next liveliness tick ~1s later
    finally:
        bus.shutdown_thread()
    assert observer.count == ticks


def test_backlog_builds_only_when_delivery_slower_than_emit_rate():
    """A backlog (and timeout) appears only when delivery is the bottleneck.

    Here each delivery takes 0.3s and we emit a burst faster than that, so the
    queue grows and the request-side wait exceeds the 3.2s budget. This isolates
    the real saturation condition: slow delivery, not the emit rate per se.
    """
    bus = _SignalBus(logger=logging.getLogger("one-hz-test"))
    observer = _Observer(delay=0.3)  # slow delivery
    bus.register_observer(observer)
    bus.start_thread()
    try:
        for _ in range(30):  # ~30 * 0.3s = 9s of work, >> 3.2s budget
            bus.emit("isSubsystemAvailable", True)
        with pytest.raises(TimedOutError):
            bus.wait_for_thread(REQUEST_TIMEOUT)
    finally:
        bus.shutdown_thread()
