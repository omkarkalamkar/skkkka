"""conftest module for CSP Subarray Leaf Node."""
# pylint: disable=unused-argument,redefined-outer-name

import json
import logging
import time
from os.path import dirname, join
from time import sleep
from typing import Generator

import pytest
import tango
from astropy.utils import iers
from ska_ser_logging.configuration import configure_logging
from ska_tango_testing.mock import MockCallable
from ska_tango_testing.mock.tango import MockTangoEventCallbackGroup
from ska_tmc_common import DevFactory, LivelinessProbeType
from ska_tmc_simulators.helper_dish_device import HelperDishDevice
from ska_tmc_simulators.helper_sdp_queue_connector_device import (
    HelperSdpQueueConnector,
)
from tango.test_context import DeviceTestContext, MultiDeviceTestContext

from ska_dishln_pointing_device import DishlnPointingDataComponentManager
from ska_dishln_pointing_device.dishln_pointing_device import (
    DishPointingDevice,
)
from ska_tmc_dishleafnode import MidTmcLeafNodeDish
from ska_tmc_dishleafnode.manager import DishLNComponentManager
from tests.settings import (
    ARRAY_LAYOUT,
    DISH_LEAF_NODE_DEVICE,
    DISH_MASTER_DEVICE,
    DISHLN_POINTING_DEVICE,
    SDP_QUEUE_CONNECTOR_DEVICE,
    SDP_QUEUE_CONNECTOR_DEVICE2,
)

configure_logging()
logger = logging.getLogger(__name__)


def pytest_sessionstart(session):
    """
    Pytest hook; prints info about tango version.
    :param session: a pytest Session object
    :type session: :py:class:`pytest.Session`
    """
    print(tango.utils.info())


def pytest_addoption(parser):
    """
    Pytest hook; implemented to add the `--true-context` option, used to
    indicate that a true Tango subsystem is available, so there is no
    need for a :py:class:`tango.test_context.MultiDeviceTestContext`.
    :param parser: the command line options parser
    :type parser: :py:class:`argparse.ArgumentParser`
    """
    parser.addoption(
        "--true-context",
        action="store_true",
        default=False,
        help=(
            "Tell pytest that you have a true Tango context and don't "
            "need to spin up a Tango test context"
        ),
    )


@pytest.fixture
def dish_master_device():
    """Returns dish 1 device name."""
    return DISH_MASTER_DEVICE


@pytest.fixture()
def devices_to_load():
    """Returns helper state devices."""
    return (
        {
            "class": MidTmcLeafNodeDish,
            "devices": [
                {
                    "name": DISH_LEAF_NODE_DEVICE,
                    "properties": {
                        "MidDishControl": DISH_MASTER_DEVICE,
                        "MidPointingDevice": DISHLN_POINTING_DEVICE,
                    },
                },
            ],
        },
        {
            "class": DishPointingDevice,
            "devices": [
                {
                    "name": DISHLN_POINTING_DEVICE,
                },
            ],
        },
        {
            "class": HelperDishDevice,
            "devices": [
                {"name": DISH_MASTER_DEVICE},
            ],
        },
        {
            "class": HelperSdpQueueConnector,
            "devices": [
                {
                    "name": SDP_QUEUE_CONNECTOR_DEVICE,
                },
                {
                    "name": SDP_QUEUE_CONNECTOR_DEVICE2,
                },
            ],
        },
    )


@pytest.fixture
def tango_context(devices_to_load, request):
    """Provides context to run devices without database."""
    true_context = request.config.getoption("--true-context")
    logging.info("true context: %s", true_context)
    if not true_context:
        try:
            with MultiDeviceTestContext(
                devices_to_load, process=True, timeout=30
            ) as context:
                DevFactory._test_context = context
                logging.info("test context set right")
                yield context
        except RuntimeError:
            pass
    else:
        yield None


@pytest.fixture
def dishln_device(request):
    """Create DeviceProxy for tests"""
    true_context = request.config.getoption("--true-context")
    if not true_context:
        with DeviceTestContext(
            MidTmcLeafNodeDish,
            device_name=DISH_LEAF_NODE_DEVICE,
            properties={
                "MidDishControl": DISH_MASTER_DEVICE,
                "MidPointingDevice": DISHLN_POINTING_DEVICE,
                "DishAvailabilityCheckTimeout": 5,
            },
            process=True,
            timeout=180,
        ) as proxy:
            yield proxy
    else:
        database = tango.Database()
        instance_list = database.get_device_exported_for_class(
            "MidTmcLeafNodeDish"
        )
        for instance in instance_list.value_string:
            yield tango.DeviceProxy(instance)
            break


@pytest.fixture
def task_callback() -> MockCallable:
    """Creates a mock callable for asynchronous testing

    :rtype: MockCallable
    """
    task_callback = MockCallable(100)
    return task_callback


@pytest.fixture
def group_callback() -> MockTangoEventCallbackGroup:
    """Creates a mock callback group for asynchronous testing

    :rtype: MockTangoEventCallbackGroup
    """
    group_callback = MockTangoEventCallbackGroup(
        "longRunningCommandResult",
        "dishMode",
        "isSubsystemAvailable",
        "pointingState",
        "kValueValidationResult",
        "sourceOffset",
        "kValue",
        "commandCallInfo",
        "trackTableErrors",
        "globalPointingModelParams",
        "band1PointingModelParams",
        "pointingProgramTrackTable",
        "programTrackTableError",
        "gpmVersion",
        "gpmValidationResult",
        "healthState",
        "stowStatus",
        "b1CapabilityState",
        "b2CapabilityState",
        timeout=80,
    )
    return group_callback


def get_input_str(path: str) -> str:
    """
    Returns input json string
    :rtype: String
    """
    with open(path, "r", encoding="utf-8") as f:
        input_arg = json.load(f)
    return json.dumps(input_arg)


@pytest.fixture()
def json_factory():
    """
    Json factory for getting json files
    """

    def _get_json(slug):
        return get_input_str(join(dirname(__file__), "data", f"{slug}.json"))

    return _get_json


def dish_mode_callback(argin):
    """An empty dishmode callback"""


def pointing_model_param_callaback(temp):
    """An empty dishmode callback"""
    logger.debug(temp)


def pointing_state_callback(argin):
    """An empty pointingstate callback"""


def communication_state_callback():
    """An empty communication_state callback"""


def component_state_callback():
    """An empty component_state callback"""


def pointing_callback(argin):
    """An empty pointing callback"""


def kvalue_validation_callback(*args):
    """An empty kvalue_validation callback"""


def update_availablity_callback(argin):
    """An empty update_availablity callback"""


def update_source_offset_callback(source_offset):
    """An empty update_source_offset callback"""
    logger.info("Source offset is: %s", source_offset)


def update_last_pointing_data_callback(temp):
    """An empty last pointing data callback"""
    logger.debug(temp)


def update_track_table_errors_callback(value):
    """An empty update_track_table_error callback"""
    logger.info("Track Table error is: %s", value)


def update_health_state_callback(temp):
    """An empty health state callback"""
    logger.debug(temp)


def update_pointing_program_track_table_callback(temp):
    """An empty pointing program track table callback"""
    logger.debug(temp)


def update_program_track_table_error_callback(temp):
    """An empty program track table error callback"""
    logger.debug(temp)


def update_gpm_version_callback(temp):
    """An empty gpm version callback"""
    logger.debug(temp)


def update_gpm_validation_result_callback(temp, temp2):
    """An empty gpm validation result callback"""
    logger.debug(temp, temp2)


def update_gpm_path_data_callback(temp1, temp2):
    """
    Empty update_gpm_path_data_callback
    """
    logger.debug("%s %s", temp1, temp2)


def update_health_info_callback(temp):
    """An empty health info callback"""
    logger.debug(temp)


@pytest.fixture()
def cm() -> Generator[DishLNComponentManager, None, None]:
    """Creates component manager for Dish Leaf Node."""
    cm = DishLNComponentManager(
        dish_dev_name=DISH_MASTER_DEVICE,
        dishln_pointing_fqdn=DISHLN_POINTING_DEVICE,
        logger=logger,
        _update_dishmode_callback=dish_mode_callback,
        _update_dish_pointing_model_param=(pointing_model_param_callaback),
        _update_pointingstate_callback=pointing_state_callback,
        communication_state_callback=communication_state_callback,
        component_state_callback=communication_state_callback,
        pointing_callback=pointing_callback,
        kvalue_validation_callback=kvalue_validation_callback,
        _update_availablity_callback=update_availablity_callback,
        _update_source_offset_callback=update_source_offset_callback,
        _update_last_pointing_data_cb=update_last_pointing_data_callback,
        _update_track_table_errors_callback=update_track_table_errors_callback,
        _update_gpm_version_callback=update_gpm_version_callback,
        _update_gpm_validation_result_callback=(
            update_gpm_validation_result_callback,
        ),
        _update_gpm_paths_data_callback=update_gpm_path_data_callback,
        dish_availability_check_timeout=3,
        _liveliness_probe=LivelinessProbeType.NONE,
        command_timeout=30,
        _update_health_state_callback=update_health_state_callback,
        _update_health_info_callback=update_health_info_callback,
    )

    start_time = time.time()
    while not cm.actual_pointing_process.is_alive():
        time.sleep(0.5)
        if time.time() - start_time > 30:
            logger.info("actual_pointing_process thread is not alive")
            break

    yield cm

    # pylint: disable=unnecessary-dunder-call
    cm.__del__()
    # pylint: enable=unnecessary-dunder-call


@pytest.fixture()
def cm_without_er_lp() -> Generator[DishLNComponentManager, None, None]:
    """Creates component manager without event receiver and liveliness
    probe for Dish Leaf Node.
    """
    cm = DishLNComponentManager(
        dish_dev_name=DISH_MASTER_DEVICE,
        dishln_pointing_fqdn=DISHLN_POINTING_DEVICE,
        logger=logger,
        _update_dishmode_callback=dish_mode_callback,
        _update_dish_pointing_model_param=(pointing_model_param_callaback),
        _event_manager=False,
        _liveliness_probe=LivelinessProbeType.NONE,
        _update_pointingstate_callback=pointing_state_callback,
        communication_state_callback=communication_state_callback,
        component_state_callback=communication_state_callback,
        pointing_callback=pointing_callback,
        kvalue_validation_callback=kvalue_validation_callback,
        is_dish_abort_commands_enabled=True,
        _update_availablity_callback=update_availablity_callback,
        _update_source_offset_callback=update_source_offset_callback,
        _update_last_pointing_data_cb=update_last_pointing_data_callback,
        _update_track_table_errors_callback=update_track_table_errors_callback,
        _update_gpm_version_callback=update_gpm_version_callback,
        _update_gpm_validation_result_callback=(
            update_gpm_validation_result_callback,
        ),
        _update_gpm_paths_data_callback=update_gpm_path_data_callback,
        dish_availability_check_timeout=3,
        command_timeout=30,
        _update_health_state_callback=update_health_state_callback,
        _update_health_info_callback=update_health_info_callback,
    )
    cm.stop_actual_pointing_process.set()
    cm.array_layout = ARRAY_LAYOUT
    yield cm
    # pylint: disable=unnecessary-dunder-call
    try:
        cm.__del__()
    except AttributeError as e:
        logger.error("process already cleaned up: %s", e)
    # Give some time to pytest cleanup
    # pylint: enable=unnecessary-dunder-call


@pytest.fixture()
def cm_new() -> Generator[DishLNComponentManager, None, None]:
    """Creates component manager for Dish Leaf Node."""
    cm = DishLNComponentManager(
        dish_dev_name=DISH_MASTER_DEVICE,
        dishln_pointing_fqdn=DISHLN_POINTING_DEVICE,
        logger=logger,
        _update_dishmode_callback=dish_mode_callback,
        _update_dish_pointing_model_param=(pointing_model_param_callaback),
        _update_pointingstate_callback=pointing_state_callback,
        communication_state_callback=communication_state_callback,
        component_state_callback=communication_state_callback,
        pointing_callback=pointing_callback,
        kvalue_validation_callback=kvalue_validation_callback,
        _update_availablity_callback=update_availablity_callback,
        _update_source_offset_callback=update_source_offset_callback,
        _update_last_pointing_data_cb=update_last_pointing_data_callback,
        _update_track_table_errors_callback=update_track_table_errors_callback,
        _update_gpm_version_callback=update_gpm_version_callback,
        _update_gpm_validation_result_callback=(
            update_gpm_validation_result_callback,
        ),
        _update_gpm_paths_data_callback=update_gpm_path_data_callback,
        dish_availability_check_timeout=3,
        _update_health_state_callback=update_health_state_callback,
        _update_health_info_callback=update_health_info_callback,
        _liveliness_probe=LivelinessProbeType.NONE,
    )

    elapsed_time = 0
    timeout = 120

    while cm.iers_a is not None and elapsed_time <= timeout:
        elapsed_time = elapsed_time + 1
        sleep(1)

    start_time = time.time()
    while not cm.actual_pointing:
        time.sleep(0.5)

        if time.time() - start_time > 120:
            logger.info("actual_pointing values are not populated")
            break

    yield cm
    # pylint: disable=unnecessary-dunder-call
    cm.__del__()


@pytest.fixture
def cm_pointing_device() -> (
    Generator[DishlnPointingDataComponentManager, None, None]
):
    """Create DishLeaf Node Pointing Device component manager"""
    cm = DishlnPointingDataComponentManager(
        disln_pointing_device_name=DISHLN_POINTING_DEVICE,
        logger=logger,
        update_pointing_program_track_table_callback=(
            update_pointing_program_track_table_callback
        ),
        update_program_track_table_error_callback=(
            lambda temp: logger.debug(temp)
            or setattr(
                cm, "current_track_table_error", str(temp) if temp else ""
            )
        ),
        track_table_update_rate=50,
        elevation_max_limit=90.0,
        elevation_min_limit=17.5,
        track_table_advance_sec=7,
    )
    cm.array_layout = ARRAY_LAYOUT
    cm.converter.create_antenna_obj()
    cm.iers_a = iers.IERS_A.open(
        join(dirname(__file__), "data", "iers_file.all")
    )
    yield cm


@pytest.fixture
def initialise_kvalue(dishln_device):
    """Ensure DishLN and DishMaster start with matching KValue."""
    try:
        dishln_device.SetKValue(1)
    except Exception as e:
        logger.exception("Failed initialise_kvalue fixture %s", e)
