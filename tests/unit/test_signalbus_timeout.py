"""SKB-1306: demonstrate the 3.2s request timeout mechanism of the SignalBus.

This test exercises the *real* ``ska_tango_base`` SignalBus to show the
downstream half of the bug: before servicing **every** Tango request,
``SignalBusMixin.always_executed_hook`` calls
``shared_bus.wait_for_thread(_CLIENT_NEW_REQUEST_TIMEOUT)`` with a 3.2s budget.
If the bus background thread cannot drain within that budget, the wait raises
``TimedOutError`` -- which the hook turns into ``API_CommandTimedOut``, i.e. the
client (TMC) request fails and the dish looks unavailable.

Honest scope
------------
A *literal* 1 Hz availability flood does not by itself build a 3.2s backlog --
the bus would drain it instantly. The timeout is reached only when draining the
queued emissions is slow (e.g. ``push_change_event`` contending for the Tango
monitor / GIL, or many/slow subscribers). Here a deliberately slow observer
stands in for that slow delivery so we can demonstrate the timeout contract
deterministically. This test therefore proves the *mechanism*
(bus-cannot-drain-in-time -> request-side timeout), not that 1 Hz alone triggers
it. The avoidable per-tick emissions removed by the fix are what keep the bus
clear of this backlog in the first place (see test_availability_flood.py).
"""

import logging
import time

import pytest
from ska_tango_base.software_bus import TimedOutError, _SignalBus

# Mirrors SignalBusMixin._CLIENT_NEW_REQUEST_TIMEOUT in ska_tango_base 1.4.0.
REQUEST_TIMEOUT = 3.2


class _SlowObserver:
    """Observer whose emission handling is slow.

    Stands in for a slow / monitor-contended ``push_change_event`` so the bus
    background thread cannot drain the queue quickly.
    """

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.processed = 0

    def notify_emission(self, signal: str, value: object) -> None:
        time.sleep(self.delay)
        self.processed += 1


def test_request_side_wait_times_out_when_bus_cannot_drain():
    """If the bus can't catch up within 3.2s, the request-side wait times out.

    This is exactly what always_executed_hook does before every Tango request;
    a TimedOutError here corresponds to the client receiving API_CommandTimedOut.
    """
    bus = _SignalBus(logger=logging.getLogger("signalbus-timeout-test"))
    # One emission takes longer than the request budget to process.
    slow = _SlowObserver(delay=REQUEST_TIMEOUT + 0.5)
    bus.register_observer(slow)  # keep a strong ref: observers are held weakly
    bus.start_thread()
    try:
        bus.emit("isSubsystemAvailable", True)  # bus thread now busy > 3.2s

        start = time.monotonic()
        with pytest.raises(TimedOutError):
            bus.wait_for_thread(REQUEST_TIMEOUT)
        waited = time.monotonic() - start

        # It actually waited (about) the full request budget before failing.
        assert waited >= REQUEST_TIMEOUT
    finally:
        bus.shutdown_thread()


def test_request_side_wait_returns_fast_when_bus_is_idle():
    """Control: with no backlog the same wait returns well under the budget.

    This is the fixed-build steady state -- the change-guard keeps the bus quiet,
    so always_executed_hook never approaches the 3.2s timeout.
    """
    bus = _SignalBus(logger=logging.getLogger("signalbus-timeout-test"))
    bus.start_thread()
    try:
        start = time.monotonic()
        bus.wait_for_thread(REQUEST_TIMEOUT)  # must not raise
        assert time.monotonic() - start < 1.0
    finally:
        bus.shutdown_thread()
