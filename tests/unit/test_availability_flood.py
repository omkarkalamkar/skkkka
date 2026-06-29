"""SKB-1306: the availability callback must not flood the SignalBus.

Background
----------
``MidTmcLeafNodeDish.update_availablity_callback`` is invoked by the liveliness
probe on *every* monitoring tick (~1 Hz), whether or not the availability value
actually changed. ``isSubsystemAvailable`` is signal-backed, and in
``ska_tango_base.software_bus`` assigning to a ``Signal`` calls
``shared_bus.emit(...)`` unconditionally on every assignment (``Signal.__set__``
has no internal de-duplication). So writing the signal on every tick emits on
the bus on every tick -- the flood that SKB-1306 fixes.

Because one assignment == one bus emission, counting assignments to the signal
is an exact proxy for counting bus emissions. These tests count assignments to
prove the emission behaviour, without needing a Tango runtime or a cluster.
"""

import logging

from ska_tmc_dishleafnode.dish_leaf_node import MidTmcLeafNodeDish

NUMBER_OF_TICKS = 100


class _AvailabilitySpy:
    """Minimal stand-in for the device that counts availability writes.

    ``_is_subsystem_available`` is exposed as a property so every assignment is
    counted, mirroring the one-emission-per-assignment behaviour of the real
    ``Signal`` descriptor.
    """

    def __init__(self, initial: bool = False) -> None:
        self._value = initial
        self.write_count = 0
        self.logger = logging.getLogger("availability-spy")

    @property
    def _is_subsystem_available(self) -> bool:
        return self._value

    @_is_subsystem_available.setter
    def _is_subsystem_available(self, value: bool) -> None:
        self.write_count += 1
        self._value = value


def test_repeated_same_value_emits_once():
    """N identical liveliness ticks must collapse to a single emission."""
    spy = _AvailabilitySpy(initial=False)

    for _ in range(NUMBER_OF_TICKS):
        MidTmcLeafNodeDish.update_availablity_callback(spy, True)

    assert spy._is_subsystem_available is True
    # Guard collapses NUMBER_OF_TICKS writes into exactly one bus emission.
    assert spy.write_count == 1


def test_genuine_transitions_still_emit():
    """Real state changes must still propagate -- the guard drops only no-ops."""
    spy = _AvailabilitySpy(initial=False)

    MidTmcLeafNodeDish.update_availablity_callback(spy, True)  # False -> True
    MidTmcLeafNodeDish.update_availablity_callback(spy, True)  # no-op
    MidTmcLeafNodeDish.update_availablity_callback(spy, False)  # True -> False
    MidTmcLeafNodeDish.update_availablity_callback(spy, False)  # no-op
    MidTmcLeafNodeDish.update_availablity_callback(spy, True)  # False -> True

    assert spy.write_count == 3
    assert spy._is_subsystem_available is True


def test_unguarded_callback_would_flood():
    """Reference: the pre-fix behaviour writes on every tick (the bug).

    This documents and pins the difference the fix makes: the same N ticks that
    the guarded callback collapses to 1 emission would, without the guard,
    produce N emissions on the SignalBus.
    """

    def unguarded_callback(self, availability):
        self.logger.info("Updating availability to %s", availability)
        self._is_subsystem_available = availability

    spy = _AvailabilitySpy(initial=False)

    for _ in range(NUMBER_OF_TICKS):
        unguarded_callback(spy, True)

    assert spy.write_count == NUMBER_OF_TICKS
