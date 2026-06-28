"""This is DishLeafNode TANGO device."""
# flake8: noqa
from __future__ import annotations

import json
from threading import Event
from typing import List, Tuple, Union

import tango
from numpy import isnan
from numpy import nan as NaN
from ska_control_model import HealthState
from ska_tango_base.commands import ResultCode
from ska_tango_base.long_running_commands import (
    LRCReqType,
    long_running_command,
)
from ska_tango_base.software_bus import Signal, attribute_from_signal
from ska_tango_base.type_hints import TaskCallbackType
from ska_tmc_common import (
    CommandNotAllowed,
    DeviceUnresponsive,
    DishMode,
    LivelinessProbeType,
    PointingState,
)
from ska_tmc_common.v1.tmc_base_leaf_device import TMCBaseLeafDevice
from tango import (
    ArgType,
    AttrDataFormat,
    AttrQuality,
    AttrWriteType,
    Database,
    DebugIt,
    TimeVal,
)
from tango.server import attribute, command, device_property, run

from ska_tmc_dishleafnode import release
from ska_tmc_dishleafnode.commands.set_kvalue import SetKValue
from ska_tmc_dishleafnode.commands.setstowmode import StowCommand
from ska_tmc_dishleafnode.enums.stow_status import StowStatus
from ska_tmc_dishleafnode.manager import DishLNComponentManager

__all__ = ["MidTmcLeafNodeDish", "main"]


# pylint: disable = attribute-defined-outside-init
class MidTmcLeafNodeDish(TMCBaseLeafDevice):
    """
    A Leaf control node for DishMaster.

    :Device Properties:
    :MidDishControl: FQDN of Dish Master Device
    :Device Attributes:
    :commandExecuted: Stores command executed on the device.
    :dishMasterDevName: Stores Dish Master Device name.
    """

    # -----------------
    # Device Properties
    # -----------------
    MidDishControl = device_property(
        dtype="str",
        doc="FQDN of Dish Master Device",
    )

    DefaultArrayLayoutSourceUris = device_property(
        dtype="str",
        doc="Default source URIs for the array layout definition.",
        default_value=(
            "gitlab://gitlab.com/ska-telescope/ska-telmodel-data?main#tmdata"
        ),
    )
    DefaultArrayLayoutPath = device_property(
        dtype="str",
        doc="Default path for the array layout definition.",
        default_value="instrument/ska1_mid/layout/mid-layout.json",
    )
    MidPointingDevice = device_property(
        dtype="str",
        doc="FQDN of DishLeaf Node Pointing Device",
    )

    DishAvailabilityCheckTimeout = device_property(
        dtype="DevUShort", default_value=3
    )
    IsDishAbortEnabled = device_property(
        dtype="DevBoolean", default_value=False
    )

    # Dish Track command properties
    MaxTrackTableRetry = device_property(
        dtype="DevShort",
        default_value=3,
        doc="Maximum retries for the programTrackTable write operations",
    )
    TrackTableRetryDuration = device_property(
        dtype="DevFloat",
        default_value=0.2,
        doc="Retry duration for programTrackTable write operation in seconds",
    )
    WeatherStationDeviceNames = device_property(
        dtype=("str",),
        doc="FQDN's of Weather Station devices",
        default_value=tuple(),
    )

    # wind thresholds
    MaxAllowedWindspeed = device_property(
        dtype=float,
        doc="Threshold on wind speed(unit m/s) for auto stowing",
        default_value=13.5,
    )
    MaxAllowedGustWindspeed = device_property(
        dtype=float,
        doc="Threshold on gust wind speed(unit m/s) for auto stowing",
        default_value=20.0,
    )
    MaxAllowedOpsWindspeed = device_property(
        dtype=float,
        doc="Threshold on operational wind speed(unit m/s) for auto stowing",
        default_value=10.0,
    )
    MaxAllowedWindspeedDifference = device_property(
        dtype=float,
        doc="Threshold on operational wind speed(unit m/s) for auto stowing",
        default_value=4.5,
    )

    # Wind time windows
    MeanWindspeedMeasurementTimeWindow = device_property(
        dtype=float,
        doc="Wind speed tracking duration(unit seconds) for auto stowing",
        default_value=600.0,
    )
    GustWindspeedMeasurementTimeWindow = device_property(
        dtype=float,
        doc="Gust wind speed tracking duration(unit seconds) for \
            auto stowing",
        default_value=3.0,
    )
    WindspeedMeasurementTimeWindow = device_property(
        dtype=float,
        doc="Operational wind speed tracking duration(unit seconds) for \
            auto stowing",
        default_value=1000.0,
    )
    MaxAllowedOpsMeanWindspeedMeasurementTimeWindow = device_property(
        dtype=float,
        doc="Operational wind speed mean and percentile difference \
            duration(unit seconds) for auto stowing",
        default_value=600.0,
    )

    # Temperature thresholds
    MinTemperatureThreshold = device_property(
        dtype=float,
        doc="Minimum Temperature(unit °C) threshold for auto stowing",
        default_value=-5,
    )
    MaxTemperatureThreshold = device_property(
        dtype=float,
        doc="Maximum Temperature(unit °C) threshold for auto stowing",
        default_value=40,
    )
    TemperatureDelta = device_property(
        dtype=float,
        doc="""
        Temperature delta(unit °C) to calculate 
        the rate of change in temperature for auto stowing
        """,
        default_value=4.5,
    )
    TimeDelta = device_property(
        dtype=float,
        doc="""
        Time delta(unit seconds) to calculate
        the rate of change in temperature for auto stowing""",
        default_value=1000.0,
    )
    EnableAutoStow = device_property(
        dtype=bool, doc="Flag to enable AutoStow feature", default_value=True
    )
    # ----------
    # Attributes
    # ----------

    dishMasterDevName = attribute(
        dtype="DevString",
        access=AttrWriteType.READ_WRITE,
    )

    dishlnPointingDevName = attribute(
        dtype="DevString",
        access=AttrWriteType.READ_WRITE,
    )

    _track_table_errors: Signal[str] = Signal[str](
        stored=True, initial_value=""
    )

    trackTableErrors = attribute_from_signal(
        _track_table_errors,
        dtype=str,
        max_dim_x=1024,
        access=AttrWriteType.READ,
        description="TrackTable errors to be reported",
        to_tango=json.dumps,
    )

    _is_subsystem_available: Signal[bool] = Signal[bool](
        stored=True, initial_value=False
    )

    isSubsystemAvailable: attribute_from_signal = attribute_from_signal(
        _is_subsystem_available,
        access=AttrWriteType.READ,
        dtype="DevBoolean",
        description="Boolean Flag for sub system available",
    )

    _dishMode: Signal[DishMode] = Signal[DishMode](stored=True)

    dishMode = attribute_from_signal(
        _dishMode,
        access=AttrWriteType.READ,
        dtype=DishMode,
        description="current value of the dishMode attribute",
    )

    _pointingState: Signal[PointingState] = Signal[PointingState](
        stored=True, initial_value=PointingState.NONE
    )

    pointingState = attribute_from_signal(
        _pointingState,
        access=AttrWriteType.READ,
        dtype=PointingState,
        description="current value of the dishMode attribute",
    )

    _sourceOffset: Signal[float] = Signal[float](
        stored=True, initial_value=[NaN, NaN]
    )

    sourceOffset = attribute_from_signal(
        _sourceOffset,
        dtype=ArgType.DevDouble,
        dformat=AttrDataFormat.SPECTRUM,
        access=AttrWriteType.READ,
        max_dim_x=2,
        description="Stores offsets from delta/partial configuration",
        abs_change=0.000001,
    )

    InitCommand = None

    # ---------------
    # General methods
    # ---------------

    def init_device(self: MidTmcLeafNodeDish):
        self._dishln_name = self.get_name()
        super().init_device()
        self._build_state = f"""{release.name},{release.version},
        {release.description}"""
        self._version_id = release.version
        self._update_health_state(HealthState.DEGRADED)
        self.op_state_model.perform_action("component_on")
        self._pointingState = PointingState.NONE
        self._sdpQueueConnectorFqdn = ""
        self._lastPointingData: str = "Not Set"
        self._last_pointing_data_attr_quality = getattr(
            AttrQuality, "ATTR_VALID"
        )

        for attribute_name in [
            "sdpQueueConnectorFqdn",
            "lastPointingData",
            "kValue",
            "gpmVersion",
            "gpmValidationResult",
            "actualPointing",
        ]:
            self.set_change_event(attribute_name, True, False)
            self.set_archive_event(attribute_name, True)
        self.init_completed()

    def delete_device(self) -> None:
        # if the init is called more than once
        # I need to stop all threads
        if hasattr(self, "component_manager"):
            # pylint: disable=unnecessary-dunder-call
            self.component_manager.__del__()
            # pylint: enable=unnecessary-dunder-call
        super().delete_device()

    def update_health_state_callback(self, healthState: HealthState) -> None:
        """Change event callback for healthState attribute
        Args:
            healthState (HealthState): New health state to be set.
        """
        self._health_state = healthState

    def update_health_info_callback(self, health_info: dict) -> None:
        """Change event callback for healthInfo attribute
        Args:
            health_info (dict): New health info to be set.
        """
        self._health_info = json.dumps(health_info)
        self.logger.info(
            "Updated HealthInfo is: %s",
            self._health_info,
        )

    def update_gpm_paths_data_callback(
        self, source_path: str, file_path: str
    ) -> None:
        """
        Store GPM paths received from Central node
        (At the time of TMC initialization).

        Args:
            source_path (str): GPM source path.
            file_path (str): GPM file path.
        """
        try:
            db = Database()
            value = {"gpmSourcePath": {"__value": [source_path]}}
            db.put_device_attribute_property(self._dishln_name, value)
            value = db.get_device_attribute_property(
                self._dishln_name, "gpmSourcePath"
            )
            with tango.EnsureOmniThread():
                self.push_archive_event("gpmSourcePath", source_path)
            self.logger.info(
                "%s: Memorized GPM source path %s", self._dishln_name, value
            )

            value = {"gpmFilePath": {"__value": [file_path]}}
            db.put_device_attribute_property(self._dishln_name, value)
            value = db.get_device_attribute_property(
                self._dishln_name, "gpmFilePath"
            )
            with tango.EnsureOmniThread():
                self.push_archive_event("gpmFilePath", file_path)
            self.logger.info(
                "%s: Memorized GPM file path %s", self._dishln_name, value
            )
        except Exception as e:
            self.logger.exception(
                "Exception occurred %s while storing gpm path in tango DB", e
            )

    def update_gpm_version_callback(self, gpm_version: str) -> None:
        """
        Callback to update gpmVersion attribute with the provided
        GPM version string. Stores the value in the device database,
        triggers change/archive events, and logs the memorized GPM version.

        Args:
            gpm_version (str): New GPM version string to set for
            the gpmVersion attribute.
        """
        try:
            db = Database()
            value = {"gpmVersion": {"__value": [gpm_version]}}
            db.put_device_attribute_property(self._dishln_name, value)
            value = db.get_device_attribute_property(
                self._dishln_name, "gpmVersion"
            )
            with tango.EnsureOmniThread():
                self.push_change_archive_events("gpmVersion", gpm_version)
            self.logger.info(
                "%s: Memorized GPM version %s", self._dishln_name, value
            )
        except Exception as e:
            self.logger.exception(
                "Exception occurred %s while storing gpm version in tango DB",
                e,
            )

    def update_gpm_validation_result_callback(
        self, band: str, validation_result: str
    ) -> None:
        """
        Callback to update gpmValidationResult attribute.

        Args:
            gpm_validation_result (str): GPM validation result.
            band(str): Band name for which validation result is to be updated.
        """
        try:
            self.component_manager.gpm_validation_result[
                band
            ] = validation_result
            with tango.EnsureOmniThread():
                self.push_change_archive_events(
                    "gpmValidationResult",
                    json.dumps(self.component_manager.gpm_validation_result),
                )
            self.logger.info(
                "GPM validation result %s",
                self.component_manager.gpm_validation_result,
            )
        except Exception as e:
            self.logger.exception("Exception occurred: %s", e)

    def update_source_offset_callback(self, source_offset: List) -> None:
        """Change event callback for sourceOffset attribute"""
        self._sourceOffset = source_offset

    def update_last_pointing_data_cb(self, last_pointing_data: List) -> None:
        """Change event callback for lastPointingData attribute"""
        if isnan(last_pointing_data).any():
            self._last_pointing_data_attr_quality = getattr(
                AttrQuality, "ATTR_ALARM"
            )
        else:
            self._last_pointing_data_attr_quality = getattr(
                AttrQuality, "ATTR_VALID"
            )
        self._lastPointingData = json.dumps(last_pointing_data.tolist())
        with tango.EnsureOmniThread():
            self.push_change_archive_events(
                "lastPointingData", self._lastPointingData
            )
        self.logger.info(
            "Updated lastPointingData of %s is: %s ",
            self._dishln_name,
            last_pointing_data,
        )

    def update_availablity_callback(self, availability):
        """Change event callback for isSubsystemAvailable.

        This callback is invoked by the liveliness probe on every monitoring
        tick (~1 Hz), regardless of whether the availability actually changed.
        Setting the ``_is_subsystem_available`` signal emits a value on the
        SignalBus, so we must only do so on a genuine transition. Emitting on
        every tick floods the bus with redundant events; because
        ``SignalBusMixin.always_executed_hook`` waits for the bus thread to
        drain before servicing each Tango request, that flood starves the bus
        thread and causes requests to time out, which makes the device appear
        unavailable to TMC (SKB-1306).
        """
        if self._is_subsystem_available != availability:
            self.logger.info("Updating availability to %s", availability)
            self._is_subsystem_available = availability

    @command(
        dtype_in=int,
        doc_in="Number of liveliness ticks to simulate (each reports available).",
    )
    def SimulateAvailabilityTicks(self, number_of_ticks: int) -> None:
        """SKB-1306 measurement: invoke the availability callback `n` times.

        Each call mimics one liveliness-probe tick reporting the dish as
        available. Counting the resulting "Updating availability" log lines /
        SignalBus emissions shows the flood (N ticks -> N emits on the broken
        build, vs N ticks -> 1 emit on the fixed build).
        """
        for _ in range(int(number_of_ticks)):
            self.update_availablity_callback(True)

    def update_track_table_errors_callback(self, value: list):
        """Push an event for the trackTableErrors attribute."""
        self._track_table_errors = value
        self.logger.debug("Pushed the trackTableErrors event: %s", value)

    actualPointing = attribute(
        dtype=str,
        access=AttrWriteType.READ,
    )

    def read_actualPointing(self) -> str:
        """Gets the actualPointing attribute value.

        Returns:
            str: actualPointing attribute value.
        """
        return json.dumps(self.component_manager.actual_pointing)

    def pointing_callback(self, actual_pointing: list) -> None:
        """Push an event for the actualPointing attribute."""
        with tango.EnsureOmniThread():
            self.push_change_archive_events(
                "actualPointing", json.dumps(actual_pointing)
            )

    def update_global_pointing_param_callback(
        self, global_pointing_model_params: str
    ):
        """Push an event for the change of dishMode attribute."""
        self._global_pointing_model_params = global_pointing_model_params

        self.logger.info(
            "GPM PARAMS on Dish: %s", global_pointing_model_params
        )

    def update_dishmode_callback(self, dish_mode: DishMode) -> None:
        """Push an event for the change of dishMode attribute."""
        self._dishMode = dish_mode
        self.logger.debug(
            "Dish mode is updated to %s", DishMode(dish_mode).name
        )

    def update_pointingstate_callback(
        self, pointing_state: PointingState
    ) -> None:
        """Push an event for change of pointingState attribute."""

        self._pointingState = pointing_state

    def kvalue_validation_callback(self, result: ResultCode) -> None:
        """Push an event for the kValueValidationResult attribute."""
        self._kvalue_validation_result = str(int(result))

        self.logger.info(
            "k-value validation Result for %s is : %s",
            self._dishln_name,
            ResultCode(result).name,
        )

    def update_kvalue_callback(self) -> None:
        """Push an event for the kValue attribute."""
        with tango.EnsureOmniThread():
            self.push_change_archive_events(
                "kValue",
                int(self.component_manager.kValue),
            )
        self.logger.debug(
            "Updated k-value to: %s",
            self.component_manager.kValue,
        )

    # ------------------
    # Attributes methods
    # ------------------

    def read_dishMasterDevName(self) -> str:
        """Reads the dishMasterDevName attribute value.

        Returns:
            str: dishMasterDevName attribute value.
        """
        return self.component_manager.dish_dev_name

    def write_dishMasterDevName(self, value: str) -> None:
        """Set the dishMasterDevName attribute."""
        self.component_manager.dish_dev_name = value

    def read_dishlnPointingDevName(self) -> str:
        """Reads the dishlnPointingDevName attribute value.

        Returns:
            dishlnPointingDevName attribute value.
        """
        return self.component_manager.dish_pointing_dev_name

    def write_dishlnPointingDevName(self, value: str) -> None:
        """Set the dishlnPointingDevName attribute."""
        self.component_manager.dish_pointing_dev_name = value

    @attribute(
        dtype="DevLong",
        access=AttrWriteType.READ_WRITE,
        memorized=True,
        hw_memorized=True,
    )
    def kValue(self: MidTmcLeafNodeDish) -> int:
        """Returns the k-value attribute value."""
        return self.component_manager.kValue

    @attribute(
        dtype="str",
        access=AttrWriteType.READ_WRITE,
    )
    def arrayLayout(self: MidTmcLeafNodeDish) -> str:
        """Returns the array-layout attribute value."""
        return json.dumps(self.component_manager.array_layout)

    @arrayLayout.write
    def arrayLayout(self: MidTmcLeafNodeDish, array_layout: str) -> None:
        """Set the dish array-layout.

        Args:
            array_layout (str): array layout to be set.
        """
        try:
            layout = json.loads(array_layout)
        except Exception as e:
            self.logger.exception("Array layout must be valid JSON: %s", e)
            raise
        self.component_manager.array_layout = layout

    @kValue.write
    def kValue(self: MidTmcLeafNodeDish, k_value: int) -> None:
        """Set the dish k-value.

        Args:
            k_value (int): k-value to be set.
        """
        self.component_manager.kValue = k_value

    _global_pointing_model_params = Signal[str](
        stored=True, initial_value="{}"
    )
    globalPointingModelParams = attribute_from_signal(
        _global_pointing_model_params,
        dtype=str,
        access=AttrWriteType.READ,
        to_tango=json.dumps,
    )

    _kvalue_validation_result = Signal[str](
        stored=True, initial_value=str(ResultCode.UNKNOWN.value)
    )
    kValueValidationResult = attribute_from_signal(
        _kvalue_validation_result,
        dtype="str",
        access=AttrWriteType.READ,
    )

    @attribute(
        dtype=ArgType.DevString,
        dformat=AttrDataFormat.SCALAR,
        access=AttrWriteType.READ_WRITE,
    )
    def sdpQueueConnectorFqdn(self: MidTmcLeafNodeDish) -> str:
        """
        This attribute is used for storing the FQDN of pointing_cal
        attribute from SDP queue connector device, which is required in
        calibration scan.
        :return: str
        """
        return self._sdpQueueConnectorFqdn

    @sdpQueueConnectorFqdn.write
    def sdpQueueConnectorFqdn(
        self: MidTmcLeafNodeDish, sdpqc_fqdn: str
    ) -> None:
        """
        This Method is used to get the SDP queue connector FQDN from
        subarray node and then Dish Leaf Node have to subscribe to its
        respective pointing_cal attribute on queue connector device.

        Args:
            sdpqc_fqdn (str): sdpQueueConnectorFqdn

        """

        self._sdpQueueConnectorFqdn = sdpqc_fqdn
        self.component_manager.process_sqpqc_attribute_fqdn(sdpqc_fqdn)
        self.push_change_archive_events(
            "sdpQueueConnectorFqdn", self._sdpQueueConnectorFqdn
        )

    @attribute(
        dtype=ArgType.DevString,
        dformat=AttrDataFormat.SCALAR,
        access=AttrWriteType.READ,
    )
    def lastPointingData(self: MidTmcLeafNodeDish):
        """
        This attribute is used to store the recent
        pointing data received in calibration scan

        :return: str
        """
        if self._last_pointing_data_attr_quality is AttrQuality.ATTR_VALID:
            return self._lastPointingData
        return (
            self._lastPointingData,
            TimeVal.totime(TimeVal.now()),
            self._last_pointing_data_attr_quality,
        )

    @attribute(
        dtype="str",
        access=AttrWriteType.READ_WRITE,
        memorized=True,
        hw_memorized=True,
    )
    def gpmVersion(self: MidTmcLeafNodeDish) -> str:
        """
        Returns the band-specific GPM version
        (stored in component manager as a dictionary).
        Format: {"band": "version"}.

        :return: JSON string of band-to-GPM version mapping
        :rtype: str
        """
        return json.dumps(self.component_manager.gpm_version)

    @gpmVersion.write
    def gpmVersion(self: MidTmcLeafNodeDish, gpm_version: str) -> None:
        """Set the GPM version.

        Args:
            gpm_version(str): string in json dumps format
        """
        self.component_manager._gpm_version = json.loads(gpm_version)

    @attribute(
        dtype="str",
        access=AttrWriteType.READ_WRITE,
        memorized=True,
        hw_memorized=True,
    )
    def gpmSourcePath(self: MidTmcLeafNodeDish) -> str:
        """
        Returns the tm data source path

        :return: source path
        :rtype: str
        """
        return json.dumps(self.component_manager.gpm_source_path)

    @gpmSourcePath.write
    def gpmSourcePath(self: MidTmcLeafNodeDish, gpm_source_path: str) -> None:
        """Set the GPM source path.

        Args:
            gpm_source_path(str): string in json dumps format
        """
        self.component_manager.gpm_source_path = gpm_source_path

    @attribute(
        dtype="str",
        access=AttrWriteType.READ_WRITE,
        memorized=True,
        hw_memorized=True,
    )
    def gpmFilePath(self: MidTmcLeafNodeDish) -> str:
        """
        Returns the tm data file path

        :return: gpm data file path
        :rtype: str
        """
        return json.dumps(self.component_manager.gpm_file_path)

    @gpmFilePath.write
    def gpmFilePath(self: MidTmcLeafNodeDish, gpm_file_path: str) -> None:
        """Set the GPM file path.

        Args:
            gpm_file_path(str): string in json dumps format
        """
        self.component_manager.gpm_file_path = gpm_file_path

    @attribute(
        dtype="str",
        access=AttrWriteType.READ,
    )
    def gpmValidationResult(self: MidTmcLeafNodeDish) -> str:
        """
        Returns the band-specific GPM validation result.
        (dictionary stored in component manager).
        Format: {"band": ResultCode(UNKNOWN/OK/FAILED)}.

        :return: JSON string of band-to-GPM validation result mapping
        :rtype: str
        """
        return json.dumps(self.component_manager.gpm_validation_result)

    _stow_status = Signal[StowStatus](
        stored=True, initial_value=StowStatus.DISH_NOT_IN_STOW
    )
    stowStatus = attribute_from_signal(
        _stow_status, dtype=StowStatus, access=AttrWriteType.READ
    )

    def update_stow_status_callback(self, status: StowStatus):
        """Callback to update the stow status.

        :param status: stow status.
        :type status: StowStatus
        """
        self._stow_status = status

    _mean_wind_speed = Signal[float](stored=True, initial_value=0.0)
    meanWindSpeed = attribute_from_signal(
        _mean_wind_speed, dtype=float, access=AttrWriteType.READ
    )

    def update_mean_wind_speed_callback(self, mean_speed: float):
        """Callback to update the mean wind speed.

        :param mean_speed: mean wind speed.
        :type mean_speed: float
        """
        self._mean_wind_speed = mean_speed

    _mean_gust_speed = Signal[float](stored=True, initial_value=0.0)
    meanGustSpeed = attribute_from_signal(
        _mean_gust_speed, dtype=float, access=AttrWriteType.READ
    )

    def update_mean_gust_speed_callback(self, mean_speed: float):
        """Callback to update the mean gust speed.

        :param mean_speed: mean gust speed.
        :type mean_speed: float
        """
        self._mean_gust_speed = mean_speed

    _mean_ops_wind_speed = Signal[float](stored=True, initial_value=0.0)
    meanOpsWindSpeed = attribute_from_signal(
        _mean_ops_wind_speed,
        dtype=float,
        access=AttrWriteType.READ,
        abs_change=0.0001,
    )

    def update_mean_operational_speed_callback(self, mean_speed: float):
        """Callback to update the mean operational speed.

        :param mean_speed: mean operational speed.
        :type mean_speed: float
        """
        self._mean_ops_wind_speed = mean_speed

    _ops_mean_wind_speed_diff = Signal[float](stored=True, initial_value=0.0)
    opsMeanWindSpeedDifference = attribute_from_signal(
        _ops_mean_wind_speed_diff,
        dtype=float,
        access=AttrWriteType.READ,
        abs_change=0.0001,
    )

    def update_mean_operational_diff_callback(self, mean_speed: float):
        """Callback to update the mean operational wind speed
        difference.

        :param mean_speed: ops_mean_difference
        :type mean_speed: float
        """
        self._ops_mean_wind_speed_diff = mean_speed

    _rate_of_change_in_temperature = Signal[float](
        stored=True, initial_value="{}"
    )
    rateOfChangeTemperature = attribute_from_signal(
        _rate_of_change_in_temperature,
        dtype=str,
        access=AttrWriteType.READ,
    )

    def update_roc_temperature_callback(self, roc_temp: str):
        """Callback to update the rate of change in temperature

        :param roc_temp: rate of change in temperature
        :type roc_temp: str
        """
        self._rate_of_change_in_temperature = roc_temp

    _health_info = Signal[str](stored=True, initial_value=json.dumps({}))
    healthInfo = attribute_from_signal(
        _health_info,
        dtype=str,
        access=AttrWriteType.READ,
    )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="m/s",
        display_unit="m/s",
    )
    def maxAllowedWindspeed(self: MidTmcLeafNodeDish) -> float:
        """Reads the maxAllowedWindspeed attribute value.

        Returns:
            float: maxAllowedWindspeed attribute value.
        """
        return self.component_manager.auto_stow.wind_speed_threshold

    @maxAllowedWindspeed.write
    def maxAllowedWindspeed(
        self: MidTmcLeafNodeDish, wind_speed_threshold: float
    ) -> None:
        """Set the maxAllowedWindspeed attribute value.

        Args:
            wind_speed_threshold(float): value to update
        """
        self.component_manager.auto_stow.wind_speed_threshold = (
            wind_speed_threshold
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="m/s",
        display_unit="m/s",
    )
    def maxAllowedGustWindspeed(self: MidTmcLeafNodeDish) -> float:
        """Reads the maxAllowedGustWindspeed attribute value.

        Returns:
            float: maxAllowedGustWindspeed attribute value.
        """
        return self.component_manager.auto_stow.gust_speed_threshold

    @maxAllowedGustWindspeed.write
    def maxAllowedGustWindspeed(
        self: MidTmcLeafNodeDish, gust_speed_threshold: float
    ) -> None:
        """Set the maxAllowedGustWindspeed attribute value.

        Args:
            gust_speed_threshold(float): value to update
        """
        self.component_manager.auto_stow.gust_speed_threshold = (
            gust_speed_threshold
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="m/s",
        display_unit="m/s",
    )
    def maxAllowedOpsWindspeed(self: MidTmcLeafNodeDish) -> float:
        """Reads the maxAllowedOpsWindspeed attribute value.

        Returns:
            float: maxAllowedOpsWindspeed attribute value.
        """
        return (
            self.component_manager.auto_stow.operational_wind_speed_threshold
        )

    @maxAllowedOpsWindspeed.write
    def maxAllowedOpsWindspeed(
        self: MidTmcLeafNodeDish, operational_wind_speed_threshold: float
    ) -> None:
        """Set the maxAllowedOpsWindspeed attribute value.

        Args:
            operational_wind_speed_threshold(float): value to update
        """
        self.component_manager.auto_stow.operational_wind_speed_threshold = (
            operational_wind_speed_threshold
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="m/s",
        display_unit="m/s",
    )
    def maxAllowedWindspeedDifference(self: MidTmcLeafNodeDish) -> float:
        """Reads the maxAllowedWindspeedDifference attribute value.

        Returns:
            float: maxAllowedWindspeedDifference attribute value.
        """
        auto_stow = self.component_manager.auto_stow
        return auto_stow.operational_perc_mean_diff_threshold

    @maxAllowedWindspeedDifference.write
    def maxAllowedWindspeedDifference(
        self: MidTmcLeafNodeDish, operational_perc_mean_diff_threshold: float
    ) -> None:
        """Set the maxAllowedWindspeedDifference attribute value.

        Args:
            operational_perc_mean_diff_threshold(float): value to update
        """
        auto_stow = self.component_manager.auto_stow
        auto_stow.operational_perc_mean_diff_threshold = (
            operational_perc_mean_diff_threshold
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="s",
        display_unit="s",
    )
    def meanWindspeedMeasurementTimeWindow(self: MidTmcLeafNodeDish) -> float:
        """Reads the meanWindspeedMeasurementTimeWindow attribute value.

        Returns:
            float: meanWindSpeedDuration attribute value.
        """
        return self.component_manager.auto_stow.mean_wind_speed_duration

    @meanWindspeedMeasurementTimeWindow.write
    def meanWindspeedMeasurementTimeWindow(
        self: MidTmcLeafNodeDish, mean_wind_speed_duration: float
    ) -> None:
        """Set the meanWindspeedMeasurementTimeWindow attribute value.

        Args:
            mean_wind_speed_duration(float): value to update
        """
        self.component_manager.auto_stow.mean_wind_speed_duration = (
            mean_wind_speed_duration
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="s",
        display_unit="s",
    )
    def gustWindspeedMeasurementTimeWindow(self: MidTmcLeafNodeDish) -> float:
        """Reads the gustWindspeedMeasurementTimeWindow attribute value.

        Returns:
            float: gustWindspeedMeasurementTimeWindow attribute value.
        """
        return self.component_manager.auto_stow.mean_gust_speed_duration

    @gustWindspeedMeasurementTimeWindow.write
    def gustWindspeedMeasurementTimeWindow(
        self: MidTmcLeafNodeDish, mean_gust_speed_duration: float
    ) -> None:
        """Set the gustWindspeedMeasurementTimeWindow attribute value.

        Args:
            mean_gust_speed_duration(float): value to update
        """
        self.component_manager.auto_stow.mean_gust_speed_duration = (
            mean_gust_speed_duration
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="s",
        display_unit="s",
    )
    def windSpeedMeasurementTimeWindow(self: MidTmcLeafNodeDish) -> float:
        """Reads the windSpeedMeasurementTimeWindow attribute value.

        Returns:
            float: windSpeedMeasurementTimeWindow attribute value.
        """
        return self.component_manager.auto_stow.operational_wind_speed_duration

    @windSpeedMeasurementTimeWindow.write
    def windSpeedMeasurementTimeWindow(
        self: MidTmcLeafNodeDish, operational_wind_speed_duration: float
    ) -> None:
        """Set the windSpeedMeasurementTimeWindow attribute value.

        Args:
            operational_wind_speed_duration(float): value to update
        """
        self.component_manager.auto_stow.operational_wind_speed_duration = (
            operational_wind_speed_duration
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="s",
        display_unit="s",
    )
    def maxAllowedOpsMeanWindspeedMeasurementTimeWindow(
        self: MidTmcLeafNodeDish,
    ) -> float:
        """Reads the maxAllowedOpsMeanWindspeedMeasurementTimeWindow
        attribute value.

        Returns:
            float: maxAllowedOpsMeanWindspeedMeasurementTimeWindow
            attribute value.
        """
        auto_stow = self.component_manager.auto_stow
        return auto_stow.operational_perc_mean_diff_duration

    @maxAllowedOpsMeanWindspeedMeasurementTimeWindow.write
    def maxAllowedOpsMeanWindspeedMeasurementTimeWindow(
        self: MidTmcLeafNodeDish, operational_perc_mean_diff_duration: float
    ) -> None:
        """Set the opsPercMeanDiffDuration attribute value.

        Args:
            operational_perc_mean_diff_duration(float): value to update
        """
        auto_stow = self.component_manager.auto_stow

        auto_stow.operational_perc_mean_diff_duration = (
            operational_perc_mean_diff_duration
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
    )
    def percentileForDiff(self: MidTmcLeafNodeDish) -> float:
        """Reads the percentileForDiff attribute value.

        Returns:
            float: percentileForDiff attribute value.
        """
        return self.component_manager.auto_stow.percentile_for_diff

    @percentileForDiff.write
    def percentileForDiff(
        self: MidTmcLeafNodeDish, percentile_for_diff: float
    ) -> None:
        """Set the percentileForDiff attribute value.

        Args:
            operational_perc_mean_diff_duration(float): value to update
        """
        self.component_manager.auto_stow.percentile_for_diff = (
            percentile_for_diff
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="°C",
        display_unit="°C",
    )
    def minTemperatureThreshold(self: MidTmcLeafNodeDish) -> float:
        """Reads the minTemperatureThreshold attribute value.

        Returns:
            float: minTemperatureThreshold attribute value.
        """
        return self.component_manager.auto_stow.min_temp_threshold

    @minTemperatureThreshold.write
    def minTemperatureThreshold(
        self: MidTmcLeafNodeDish, min_temp_threshold: float
    ) -> None:
        """Set the minTemperatureThreshold attribute value.

        Args:
            min_temp_threshold(float): value to update
        """
        self.component_manager.auto_stow.min_temp_threshold = (
            min_temp_threshold
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="°C",
        display_unit="°C",
    )
    def maxTemperatureThreshold(self: MidTmcLeafNodeDish) -> float:
        """Reads the maxTemperatureThreshold attribute value.

        Returns:
            float: maxTemperatureThreshold attribute value.
        """
        return self.component_manager.auto_stow.max_temp_threshold

    @maxTemperatureThreshold.write
    def maxTemperatureThreshold(
        self: MidTmcLeafNodeDish, max_temp_threshold: float
    ) -> None:
        """Set the maxTemperatureThreshold attribute value.

        Args:
            max_temp_threshold(float): value to update
        """
        self.component_manager.auto_stow.max_temp_threshold = (
            max_temp_threshold
        )

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="°C",
        display_unit="°C",
    )
    def temperatureDelta(self: MidTmcLeafNodeDish) -> float:
        """Reads the temperatureDelta attribute value.

        Returns:
            float: temperatureDelta attribute value.
        """
        return self.component_manager.auto_stow.temp_delta

    @temperatureDelta.write
    def temperatureDelta(self: MidTmcLeafNodeDish, temp_delta: float) -> None:
        """Set the temperatureDelta attribute value.

        Args:
            temp_delta(float): value to update
        """
        self.component_manager.auto_stow.temp_delta = temp_delta

    @attribute(
        dtype=float,
        access=AttrWriteType.READ,
        memorized=True,
        hw_memorized=True,
        unit="s",
        display_unit="s",
    )
    def timeDelta(self: MidTmcLeafNodeDish) -> float:
        """Reads the timeDelta attribute value.

        Returns:
            float: timeDelta attribute value.
        """
        return self.component_manager.auto_stow.time_delta

    @timeDelta.write
    def timeDelta(self: MidTmcLeafNodeDish, time_delta: float) -> None:
        """Set the timeDelta attribute value.

        Args:
            time_delta(float): value to update
        """
        self.component_manager.auto_stow.time_delta = time_delta

    # --------
    # Commands
    # --------

    def is_SetStowMode_allowed(self: MidTmcLeafNodeDish) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        dish mode.

        :return: True if this command is allowed to be run in current
            dish mode.

        :rtype: boolean
        """
        return self.component_manager.is_setstowmode_allowed()

    @command(dtype_out="DevVarLongStringArray")
    @DebugIt()
    def SetStowMode(
        self: MidTmcLeafNodeDish,
    ) -> Tuple[List[ResultCode], List[str]]:
        """Invokes SetStowMode command on DishMaster.

        :rtype: Tuple
        """
        set_stow_mode = StowCommand(
            self._command_tracker,
            self.component_manager,
            None,
            self.logger,
        )
        result_code, unique_id = set_stow_mode.do()

        return [result_code], [str(unique_id)]

    # pylint: disable=unused-argument
    def is_SetStandbyLPMode_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        dish mode.

        :return: True if this command is allowed to be run in current
            dish mode
        :rtype: boolean
        """
        return self.component_manager.is_setstandbylpmode_allowed()

    @long_running_command
    @DebugIt()
    def SetStandbyLPMode(self: MidTmcLeafNodeDish):
        """Invokes SetStandbyLPMode command on DishMaster (Standby-Low power)
        mode."""

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.setstandbylpmode(
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_SetStandbyFPMode_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        dish mode.

        :return: True if this command is allowed to be run in current
            dish mode.

        :rtype: boolean
        """
        return self.component_manager.is_setstandbyfpmode_allowed()

    @long_running_command
    @DebugIt()
    def SetStandbyFPMode(
        self: MidTmcLeafNodeDish,
    ) -> Tuple[List[ResultCode], List[str]]:
        """Invokes SetStandbyFPMode command on DishMaster (Standby-Full power)
        mode.

        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.setstandbyfpmode(
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    @long_running_command
    @DebugIt()
    def Scan(
        self: MidTmcLeafNodeDish, argin: str
    ) -> Tuple[List[ResultCode], List[str]]:
        """
        Invokes Scan command on DishMaster

        :rtype: Tuple[List[ResultCode], List[str]]
        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.scan(
                argin=argin,
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_Scan_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> Union[bool, CommandNotAllowed, DeviceUnresponsive]:
        """
        Checks whether this command is allowed to be run in the current
        dish mode.

        :return: True if this command is allowed to be run in current
            dish mode, raises CommandNotAllowed in case is is not allowed and
            DeviceUnresponsive in case Device is not responsive.

        :rtype: Union[bool, CommandNotAllowed, DeviceUnresponsive]
        """
        return self.component_manager.is_scan_allowed()

    # pylint: disable=unused-argument
    def is_EndScan_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> Union[bool, CommandNotAllowed, DeviceUnresponsive]:
        """
        Checks whether this command is allowed to be run in the current
        dish mode.

        :return: True if this command is allowed to be run in current
            dish mode, raises CommandNotAllowed in case is is not allowed and
            DeviceUnresponsive in case Device is not responsive.

        :rtype: Union[bool, CommandNotAllowed, DeviceUnresponsive]
        """
        return self.component_manager.is_endscan_allowed()

    @long_running_command
    @DebugIt()
    def EndScan(
        self: MidTmcLeafNodeDish,
    ) -> Tuple[List[ResultCode], List[str]]:
        """
        Updates the scanID attribute of Dish Master to empty string

        :rtype: Tuple[List[ResultCode], List[str]]
        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.endscan(
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_off_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ):
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return self.component_manager.is_off_allowed()

    @long_running_command
    @DebugIt()
    def Off(self: MidTmcLeafNodeDish) -> tuple:
        """
        Invokes On command on Dish Master.
        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.off(
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_Configure_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        dish mode.

        :return: True if this command is allowed to be run in current
            dish mode.

        :rtype: boolean
        """
        return self.component_manager.is_configure_allowed()

    @long_running_command
    @DebugIt()
    def Configure(self: MidTmcLeafNodeDish, argin: str) -> tuple:
        """
        Invokes Configure command on Dish Master.
        #"""

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.configure(
                argin=argin,
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    def is_StartCapture_allowed(self: MidTmcLeafNodeDish) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return False

    @command(
        dtype_in="str",
        doc_in="""The timestamp indicates the time, in UTC, at which command
        execution should start.""",
        dtype_out="DevVarLongStringArray",
    )
    @DebugIt()
    def StartCapture(self: MidTmcLeafNodeDish):
        """Triggers the DishMaster to start data capturing on the configured
        band."""

        return [
            [ResultCode.FAILED],
            ["StartCapture command will be refactored in later PI's"],
        ]

    def is_StopCapture_allowed(self: MidTmcLeafNodeDish) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return False

    @command(
        dtype_in="str",
        doc_in="""The timestamp indicates the time, in UTC, at which command
        execution should start.""",
        dtype_out="DevVarLongStringArray",
    )
    @DebugIt()
    def StopCapture(self: MidTmcLeafNodeDish):
        """Invokes StopCapture command on DishMaster on the set configured
        band."""

        return [
            [ResultCode.FAILED],
            ["StopCapture command will be refactored in later PI's"],
        ]

    # pylint: disable=unused-argument
    def is_Track_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return self.component_manager.is_track_allowed()

    @long_running_command
    @DebugIt()
    def Track(self: MidTmcLeafNodeDish, argin: str) -> tuple:
        """Invokes Track command on the DishMaster."""

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.track(
                argin=argin,
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_ConfigureBand_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        self.logger.debug("Checking if ConfigureBand is allowed")
        return self.component_manager.is_configureband_allowed()

    @long_running_command
    @DebugIt()
    def ConfigureBand(self: MidTmcLeafNodeDish, argin: str) -> tuple:
        """Invokes ConfigureBand command on the DishMaster."""

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.configureband(
                argin=argin,
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    @long_running_command
    @DebugIt()
    def TrackStop(
        self: MidTmcLeafNodeDish,
    ) -> Tuple[List[ResultCode], List[str]]:
        """
        Invokes TrackStop command on DishMaster

        :rtype: tuple
        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.trackstop(
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_TrackStop_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return self.component_manager.is_trackstop_allowed()

    @long_running_command
    @DebugIt()
    def TrackLoadStaticOff(
        self: MidTmcLeafNodeDish, argin: str
    ) -> Tuple[List[ResultCode], List[str]]:
        """
        Invokes TrackLoadStaticOff command on DishMaster

        :rtype: tuple
        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.track_load_static_off(
                argin=argin,
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_TrackLoadStaticOff_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return self.component_manager.is_trackloadstaticoff_allowed()

    def is_Abort_allowed(self: MidTmcLeafNodeDish) -> bool:
        """
        Checks whether this command is allowed to be run in current
        device state

        :return: True if this command is allowed to be run in current device
            state

        :rtype: boolean
        """
        return self.component_manager.is_abort_allowed()

    @command(dtype_out="DevVarLongStringArray")
    @DebugIt()
    def Abort(self: MidTmcLeafNodeDish):
        """Invokes Abort command on the DishMaster."""

        abort_command = self.AbortCommand(
            self._command_tracker,
            self.component_manager,
            None,
            self.logger,
        )
        result_code, unique_id = abort_command.do()
        return [result_code], [unique_id]

    def is_Restart_allowed(self: MidTmcLeafNodeDish) -> bool:
        """
        Checks whether this command is allowed to be run in current
        device state

        :return: True if this command is allowed to be run in current
            device state
        :rtype: boolean
        """
        return False

    @command(dtype_out="DevVarLongStringArray")
    @DebugIt()
    def Restart(self: MidTmcLeafNodeDish):
        """Invokes Restart command on the DishMaster."""

        return [
            [ResultCode.FAILED],
            ["Restart command will be refactored in later PI's"],
        ]

    def is_ObsReset_allowed(self: MidTmcLeafNodeDish) -> bool:
        """
        Checks whether this command is allowed to be run in current
        device state

        :return: True if this command is allowed to be run in current device
            state

        :rtype: boolean
        """
        return False

    @command(dtype_out="DevVarLongStringArray")
    @DebugIt()
    def ObsReset(self: MidTmcLeafNodeDish):
        """Invokes ObsReset command on the MidTmcLeafNodeDish."""

        return [
            [ResultCode.FAILED],
            ["ObsReset command will be refactored in later PI's"],
        ]

    # pylint: disable=unused-argument
    def is_SetKValue_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in current
        device state

        :return: True if this command is allowed to be run in current device
            state

        :rtype: boolean
        """
        return self.component_manager.is_set_kvalue_allowed()

    @command(dtype_out="DevVarLongStringArray")
    @DebugIt()
    def SetKValue(
        self: MidTmcLeafNodeDish, k_value: int
    ) -> Tuple[List[ResultCode], List[str]]:
        """Invokes SetKValue command on the DishMaster."""

        set_k_value = SetKValue(self.component_manager, logger=self.logger)
        result_code, unique_id = set_k_value.do(k_value)
        if result_code == ResultCode.OK:
            db = Database()
            value = {"kValue": {"__value": [self.component_manager.kValue]}}
            db.put_device_attribute_property(self._dishln_name, value)
            value = db.get_device_attribute_property(
                self._dishln_name, "kValue"
            )
            self.logger.info(
                "k-value for %s is memorized successfully: %s",
                self._dishln_name,
                value,
            )
            self.kvalue_validation_callback(
                self.component_manager.kValueValidationResult
            )
            self.update_kvalue_callback()
            self.component_manager.update_kvalue_data_for_health_aggregation()
        return [result_code], [unique_id]

    @long_running_command
    @DebugIt()
    def ApplyPointingModel(
        self: MidTmcLeafNodeDish, argin: str
    ) -> Tuple[List[ResultCode], List[str]]:
        """
        Invokes ApplyPointingModel command on DishMaster
        Its a dummy command at present.
        Will be renamed, once Dish ICD gets updated.

        :rtype: tuple
        """

        def task(
            task_callback: TaskCallbackType, task_abort_event: Event
        ) -> None:
            self.component_manager.apply_pointing_model(
                argin=argin,
                task_callback=task_callback,
                task_abort_event=task_abort_event,
            )

        return task

    # pylint: disable=unused-argument
    def is_ApplyPointingModel_allowed(
        self: MidTmcLeafNodeDish,
        request_type: LRCReqType = LRCReqType.ENQUEUE_REQ,
    ) -> bool:
        """
        Checks whether this command is allowed to be run in the current
        device state.

        :return: True if this command is allowed to be run in current
            device state.

        :rtype: boolean
        """
        return self.component_manager.is_apply_pointing_model_allowed()

    def create_component_manager(
        self: MidTmcLeafNodeDish,
    ) -> DishLNComponentManager:
        """
        Creates component manger instance.

        Returns:
            DishLNComponentManager: Instance of DishLNComponentManager.
        """
        update_track_err_cb = self.update_track_table_errors_callback
        cm = DishLNComponentManager(
            dish_dev_name=self.MidDishControl,
            dishln_pointing_fqdn=self.MidPointingDevice,
            logger=self.logger,
            communication_state_callback=None,
            component_state_callback=None,
            pointing_callback=self.pointing_callback,
            kvalue_validation_callback=self.kvalue_validation_callback,
            _liveliness_probe=LivelinessProbeType.MULTI_DEVICE,
            _event_manager=True,
            _update_dishmode_callback=self.update_dishmode_callback,
            _update_dish_pointing_model_param=(
                self.update_global_pointing_param_callback
            ),
            default_array_layout_source_uris=self.DefaultArrayLayoutSourceUris,
            default_array_layout_path=self.DefaultArrayLayoutPath,
            _update_pointingstate_callback=self.update_pointingstate_callback,
            event_subscription_check_period=self.EventSubscriptionCheckPeriod,
            liveliness_check_period=self.LivelinessCheckPeriod,
            dish_availability_check_timeout=self.DishAvailabilityCheckTimeout,
            adapter_timeout=self.AdapterTimeOut,
            command_timeout=self.CommandTimeOutDefault,
            is_dish_abort_commands_enabled=self.IsDishAbortEnabled,
            _update_availablity_callback=self.update_availablity_callback,
            _update_source_offset_callback=self.update_source_offset_callback,
            _update_last_pointing_data_cb=self.update_last_pointing_data_cb,
            _update_track_table_errors_callback=update_track_err_cb,
            _update_health_state_callback=self.update_health_state_callback,
            _update_health_info_callback=self.update_health_info_callback,
            _update_gpm_version_callback=self.update_gpm_version_callback,
            _update_gpm_validation_result_callback=(
                self.update_gpm_validation_result_callback
            ),
            _update_gpm_paths_data_callback=(
                self.update_gpm_paths_data_callback
            ),
            max_track_table_retry=self.MaxTrackTableRetry,
            track_table_retry_duration=self.TrackTableRetryDuration,
            weather_station_device_names=self.WeatherStationDeviceNames,
            wind_speed_threshold=self.MaxAllowedWindspeed,
            gust_speed_threshold=self.MaxAllowedGustWindspeed,
            operational_wind_speed_threshold=self.maxAllowedOpsWindspeed,
            operational_perc_mean_diff_threshold=(
                self.MaxAllowedWindspeedDifference
            ),
            mean_wind_speed_duration=self.MeanWindspeedMeasurementTimeWindow,
            mean_gust_speed_duration=self.GustWindspeedMeasurementTimeWindow,
            operational_wind_speed_duration=(
                self.WindspeedMeasurementTimeWindow
            ),
            operational_perc_mean_diff_duration=(
                self.MaxAllowedOpsMeanWindspeedMeasurementTimeWindow
            ),
            max_temp_threshold=self.MaxTemperatureThreshold,
            min_temp_threshold=self.MinTemperatureThreshold,
            time_delta=self.TimeDelta,
            temp_delta=self.TemperatureDelta,
            is_auto_stow_enabled=self.EnableAutoStow,
            _update_roc_temp_callback=self.update_roc_temperature_callback,
            _update_mean_gust_speed_callback=(
                self.update_mean_gust_speed_callback
            ),
            _update_mean_wind_speed_callback=(
                self.update_mean_wind_speed_callback
            ),
            _update_mean_operational_speed_callback=(
                self.update_mean_operational_speed_callback
            ),
            _update_mean_operational_diff_callback=(
                self.update_mean_operational_diff_callback
            ),
            _update_stow_status_callback=self.update_stow_status_callback,
        )
        return cm


# ----------
# Run server
# ----------


def main(args=None, **kwargs):
    """
    Runs the MidTmcLeafNodeDish.

    :param args: Arguments internal to TANGO
    :param kwargs: Arguments internal to TANGO
    :return: MidTmcLeafNodeDish TANGO object.

    """
    return run((MidTmcLeafNodeDish,), args=args, **kwargs)


if __name__ == "__main__":
    main()
