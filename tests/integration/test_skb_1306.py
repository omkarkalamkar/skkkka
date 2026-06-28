"""Integration test for SKB-1306.

After REL-2559 (upgrade to ska-tango-base 1.4.0), ``isSubsystemAvailable`` is
backed by the SignalBus via ``attribute_from_signal``. The availability
callback was emitting on the bus on every liveliness-probe tick (~1 Hz), even
when the value had not changed. Because ``SignalBusMixin.always_executed_hook``
waits for the bus thread to drain before servicing each Tango request, that
flood of redundant emissions starved the bus thread and caused requests to time
out, so TMC saw the dish as unavailable (``Unavailable dishes: [...]``) and
Configure Scan never proceeded.

The fix only sets the signal on a genuine transition. This test verifies, on a
real deployment, that while the Dish Manager is reachable the leaf node reports
it available through a change event and stays stably available and responsive
across several liveliness periods.
"""
import time

import pytest
import tango
from ska_tmc_common.dev_factory import DevFactory

from tests.settings import (
    DISH_LEAF_NODE_DEVICE,
    DISH_MASTER_DEVICE,
    logger,
)

# Number of liveliness periods (~1s each) to hold and re-check availability.
SUSTAINED_AVAILABILITY_CHECKS = 15


def subsystem_availability(tango_context, dishln_name, group_callback):
    logger.info(f"{tango_context}")
    dev_factory = DevFactory()
    dish_leaf_node = dev_factory.get_device(dishln_name)
    dish_master = dev_factory.get_device(DISH_MASTER_DEVICE)

    # Precondition: the Dish Manager is reachable.
    assert dish_master.ping() >= 0

    availability_event_id = dish_leaf_node.subscribe_event(
        "isSubsystemAvailable",
        tango.EventType.CHANGE_EVENT,
        group_callback["isSubsystemAvailable"],
    )

    # The signal mechanism must deliver the "available" value to subscribers.
    group_callback["isSubsystemAvailable"].assert_change_event(
        True,
        lookahead=5,
    )
    assert dish_leaf_node.isSubsystemAvailable

    # SKB-1306 regression guard: the liveliness probe keeps reporting the dish
    # available every ~1s. Previously each tick re-emitted on the SignalBus,
    # which starved the bus thread and timed out Tango requests. Verify the
    # attribute stays readable as True across several liveliness periods
    # (i.e. the device stays responsive and does not flap to False).
    for _ in range(SUSTAINED_AVAILABILITY_CHECKS):
        assert dish_leaf_node.read_attribute("isSubsystemAvailable").value
        time.sleep(1)

    # No further change events should fire for the unchanged value; a flap to
    # False (the SKB-1306 symptom) would produce an event here.
    group_callback["isSubsystemAvailable"].assert_not_called()

    dish_leaf_node.unsubscribe_event(availability_event_id)


@pytest.mark.post_deployment
@pytest.mark.SKA_mid
def test_subsystem_availability(tango_context, group_callback):
    """isSubsystemAvailable stays True/responsive while the dish is reachable."""
    subsystem_availability(
        tango_context, DISH_LEAF_NODE_DEVICE, group_callback
    )
