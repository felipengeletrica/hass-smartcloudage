"""Motorized windows: scenario parsing, position estimate, commands, cover and options flow."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartcloudage.config_flow import DOMAIN
from custom_components.smartcloudage.cover import SmartCloudAgeWindowCover
from custom_components.smartcloudage.window import (
    DEAD_TIME_S,
    SAFETY_MARGIN_S,
    WINDOW_BAD_OUTPUTS,
    WINDOW_NO_RESPONSE,
    WINDOW_NOT_CONFIGURED,
    WINDOW_OK,
    ScenarioClient,
    TravelTracker,
    Window,
    async_configure_window,
    async_probe_window,
    build_windows,
    is_scenario_response,
    mask_outputs,
    outputs_from_message,
    parse_scenario,
)

WINDOW = {
    "id": "w1n2",
    "name": "Janela da sala",
    "open_input": 1,
    "open_mask": 0b0001,
    "close_mask": 0b0010,
    "travel_time": 20,
    "device_class": "window",
}


def _read_response(scenario: dict) -> str:
    """Command-15 READ reply exactly as the firmware sends it (not valid JSON)."""
    return (
        '{"product":1,"protocol":"0.4.3","serial":"S1","device":"controller-01",'
        '"response":{"type":3,"command":15,"status":"{' + json.dumps(scenario, separators=(",", ":")) + '}"},'
        '"signature":"controller-01"}'
    )


def _window(hass, **config):
    return Window(hass, "controller-01", "Bancada", {**WINDOW, **config})


def _published(publish) -> list[dict]:
    return [json.loads(c.args[2]) for c in publish.call_args_list]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_scenario_from_unescaped_firmware_reply():
    raw = _read_response({"index": 1, "output": 1, "type": 8, "time_ms": 25000, "debounce": 100, "invert": 0})
    assert is_scenario_response(raw)
    assert parse_scenario(raw) == {
        "index": 1, "output": 1, "type": 8, "time_ms": 25000, "debounce": 100, "invert": 0,
    }


def test_parse_scenario_rejects_other_replies():
    assert parse_scenario('{"response":{"type":3,"command":15,"status":1}}') is None
    assert not is_scenario_response('{"response":{"type":3,"command":32,"status":1}}')


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"Output": {"Outputs": 5}}', 5),
        ('{"message": "{\\"Output\\": {\\"Outputs\\": 2}}"}', 2),
        ('{"message": "INPUT_STATUS", "Output": {"Outputs": 3}}', None),
        ('{"Input": {"Inputs": 1}}', None),
        ("not json", None),
    ],
)
def test_outputs_from_message(raw, expected):
    assert outputs_from_message(raw) == expected


def test_mask_outputs():
    assert mask_outputs(0b1000_0000_0000_0101) == [1, 3, 16]


# ---------------------------------------------------------------------------
# Position estimate
# ---------------------------------------------------------------------------


def test_position_unknown_until_a_full_run():
    tracker = TravelTracker(20)
    tracker.update(1, 0.0)
    assert tracker.position(5.0) is None
    tracker.update(0, 5.0)  # partial run: still unknown
    assert tracker.position(5.0) is None
    tracker.update(-1, 10.0)
    tracker.update(0, 30.0)  # 20 s closing: it is closed
    assert tracker.position(30.0) == 0.0


def test_position_follows_the_motor_once_known():
    tracker = TravelTracker(20, position=0.0)
    tracker.update(1, 100.0)
    assert tracker.position(105.0) == pytest.approx(25.0)
    tracker.update(0, 110.0)
    assert tracker.position(200.0) == pytest.approx(50.0)
    tracker.update(-1, 200.0)
    assert tracker.position(260.0) == 0.0  # clamped
    assert tracker.seconds_to(100.0, 260.0) is not None


def test_seconds_to_target():
    tracker = TravelTracker(30, position=50.0)
    assert tracker.seconds_to(80.0, 0.0) == pytest.approx(9.0)
    assert TravelTracker(30).seconds_to(80.0, 0.0) is None


# ---------------------------------------------------------------------------
# Window commands
# ---------------------------------------------------------------------------


def test_direction_from_outputs(hass):
    window = _window(hass)
    assert window.direction(0b01) == 1
    assert window.direction(0b10) == -1
    assert window.direction(0b100) == 0
    assert window.direction(0b11) == 0  # never both: treated as stopped


def test_window_needs_distinct_outputs(hass):
    with pytest.raises(ValueError):
        _window(hass, close_mask=0b0001)
    assert build_windows(hass, [{"device_id": "c", "windows": [{**WINDOW, "close_mask": 0}]}]) == []


async def test_open_writes_the_open_output(hass, mock_mqtt_publish):
    await _window(hass).async_run(1, 0)
    assert _published(mock_mqtt_publish) == [
        {"command": 11, "type": 1, "signature": "controller-01", "payload": {"id": 1, "value": 1}}
    ]


async def test_reversing_stops_and_waits_the_dead_time(hass, mock_mqtt_publish):
    with patch("custom_components.smartcloudage.window.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await _window(hass).async_run(1, -1)  # closing now, asked to open
    assert [m["payload"] for m in _published(mock_mqtt_publish)] == [
        {"id": 2, "value": 0},
        {"id": 1, "value": 1},
    ]
    sleep.assert_awaited_once_with(DEAD_TIME_S)


async def test_stop_turns_both_directions_off(hass, mock_mqtt_publish):
    await _window(hass, open_mask=0b0101).async_stop()
    assert [m["payload"] for m in _published(mock_mqtt_publish)] == [
        {"id": 1, "value": 0},
        {"id": 3, "value": 0},
        {"id": 2, "value": 0},
    ]


# ---------------------------------------------------------------------------
# Scenario probe and configuration
# ---------------------------------------------------------------------------


def _client(*replies):
    client = Mock(spec=ScenarioClient)
    client.async_read = AsyncMock(side_effect=list(replies))
    client.async_write = AsyncMock(return_value=True)
    return client


async def test_probe_window_ok():
    client = _client(
        (True, {"type": 8, "output": 4, "time_ms": 25000}),
        (True, {"type": 8, "output": 8, "time_ms": 25000}),
    )
    result, details = await async_probe_window(client, 3)
    assert result == WINDOW_OK
    assert (details["open_mask"], details["close_mask"], details["time_ms"]) == (4, 8, 25000)
    assert [c.args[0] for c in client.async_read.call_args_list] == [3, 4]


@pytest.mark.parametrize(
    ("replies", "expected"),
    [
        (((False, None),), WINDOW_NO_RESPONSE),
        (((True, {"type": 1, "output": 1}), (True, {"type": 8, "output": 2})), WINDOW_NOT_CONFIGURED),
        (((True, None), (True, None)), WINDOW_NOT_CONFIGURED),
        (((True, {"type": 8, "output": 1}), (True, {"type": 8, "output": 1})), WINDOW_BAD_OUTPUTS),
        (((True, {"type": 8, "output": 0}), (True, {"type": 8, "output": 2})), WINDOW_BAD_OUTPUTS),
    ],
)
async def test_probe_window_errors(replies, expected):
    result, _details = await async_probe_window(_client(*replies), 1)
    assert result == expected


async def test_configure_window_keeps_debounce_and_invert():
    client = _client()
    ok = await async_configure_window(
        client, 1, 3, 4, 20, previous=[{"debounce": 250, "invert": 1}, {}]
    )
    assert ok
    first, second = (c.args[0] for c in client.async_write.call_args_list)
    assert first == {
        "index": 1, "output": 0b0100, "type": 8,
        "time_ms": (20 + SAFETY_MARGIN_S) * 1000, "debounce": 250, "invert": 1,
    }
    assert second["index"] == 2 and second["output"] == 0b1000 and second["debounce"] == 100


async def test_scenario_client_reads_the_reply(hass):
    client = ScenarioClient(hass, "controller-01")
    with patch(
        "custom_components.smartcloudage.window.mqtt.async_subscribe",
        new_callable=AsyncMock,
        return_value=Mock(),
    ) as subscribe:

        async def publish(hass_, topic, payload, qos, retain):
            callback = subscribe.call_args.args[2]
            reply = _read_response({"index": 2, "output": 2, "type": 8})
            await callback(SimpleNamespace(topic="CloudAge/controller-01/OutTopic", payload=reply.encode()))

        with patch(
            "custom_components.smartcloudage.window.mqtt.async_publish",
            new_callable=AsyncMock,
            side_effect=publish,
        ) as pub:
            answered, scenario = await client.async_read(2)
    assert answered and scenario["type"] == 8
    sent = json.loads(pub.call_args.args[2])
    assert sent == {"command": 15, "type": 2, "signature": "controller-01", "payload": {"index": 2}}


# ---------------------------------------------------------------------------
# Cover entity
# ---------------------------------------------------------------------------


def _cover(hass):
    entity = SmartCloudAgeWindowCover(_window(hass))
    entity.hass = hass
    entity.async_write_ha_state = Mock()
    return entity


async def test_cover_state_follows_the_outputs(hass):
    cover = _cover(hass)
    cover.tracker.set_position(0.0)
    cover.apply_outputs(0b01)
    assert cover.is_opening and not cover.is_closing
    cover.apply_outputs(0)
    assert not cover.is_opening
    assert cover.current_cover_position is not None
    cover._cancel_timers()


async def test_set_position_needs_a_known_position(hass, mock_mqtt_publish):
    cover = _cover(hass)
    with pytest.raises(HomeAssistantError):
        await cover.async_set_cover_position(position=50)
    assert not mock_mqtt_publish.called


async def test_set_position_runs_and_schedules_the_stop(hass, mock_mqtt_publish):
    cover = _cover(hass)
    cover.tracker.set_position(0.0)
    with patch("custom_components.smartcloudage.cover.async_call_later") as later:
        await cover.async_set_cover_position(position=50)
    assert [m["payload"] for m in _published(mock_mqtt_publish)] == [{"id": 1, "value": 1}]
    assert later.call_args.args[1] == pytest.approx(10.0, abs=0.2)  # half of 20 s
    await cover._target_reached(None)
    assert [m["payload"] for m in _published(mock_mqtt_publish)][1:] == [
        {"id": 1, "value": 0},
        {"id": 2, "value": 0},
    ]


async def test_full_open_has_no_timer(hass, mock_mqtt_publish):
    cover = _cover(hass)
    with patch("custom_components.smartcloudage.cover.async_call_later") as later:
        await cover.async_set_cover_position(position=100)
    later.assert_not_called()
    assert [m["payload"] for m in _published(mock_mqtt_publish)] == [{"id": 1, "value": 1}]


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


def _entry(hass, windows=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "devices": [
                {
                    "device_id": "controller-01",
                    "outputs": 16,
                    "alias": "Bancada",
                    "meters": [],
                    "air_conditioners": [],
                    "tv_remotes": [],
                    "windows": windows or [],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    return entry


FORM = {
    "name": "Janela do quarto",
    "open_input": "1",
    "device_class": "shutter",
    "travel_time": 25,
    "source": "read",
    "open_output": 1,
    "close_output": 2,
}


async def _open_window_form(hass, entry, step="add_window"):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {"add_window", "edit_window", "delete_window"} <= set(result["menu_options"])
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})
    return result


async def test_add_window_reads_the_controller(hass):
    entry = _entry(hass)
    result = await _open_window_form(hass, entry)
    assert result["step_id"] == "window"
    with patch(
        "custom_components.smartcloudage.config_flow.async_probe_window",
        new_callable=AsyncMock,
        return_value=(WINDOW_OK, {"open_mask": 0b0100, "close_mask": 0b1000, "time_ms": 30000}),
    ):
        result = await hass.config_entries.options.async_configure(result["flow_id"], FORM)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (window,) = result["data"]["devices"][0]["windows"]
    assert window["open_mask"] == 0b0100 and window["close_mask"] == 0b1000  # from the controller
    assert window["open_input"] == 1 and window["travel_time"] == 25
    assert window["device_class"] == "shutter" and len(window["id"]) == 8


@pytest.mark.parametrize(
    ("probe", "error"),
    [
        (WINDOW_NOT_CONFIGURED, "window_not_configured"),
        (WINDOW_BAD_OUTPUTS, "window_bad_outputs"),
        (WINDOW_NO_RESPONSE, "controller_no_response"),
    ],
)
async def test_add_window_probe_errors(hass, probe, error):
    entry = _entry(hass)
    result = await _open_window_form(hass, entry)
    with patch(
        "custom_components.smartcloudage.config_flow.async_probe_window",
        new_callable=AsyncMock,
        return_value=(probe, {}),
    ):
        result = await hass.config_entries.options.async_configure(result["flow_id"], FORM)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_add_window_writes_the_scenarios(hass):
    entry = _entry(hass)
    result = await _open_window_form(hass, entry)
    with patch(
        "custom_components.smartcloudage.config_flow.ScenarioClient.async_read",
        new_callable=AsyncMock,
        return_value=(True, {"type": 1, "output": 0, "debounce": 100}),
    ), patch(
        "custom_components.smartcloudage.config_flow.async_configure_window",
        new_callable=AsyncMock,
        return_value=True,
    ) as configure, patch(
        "custom_components.smartcloudage.config_flow.async_probe_window",
        new_callable=AsyncMock,
        return_value=(WINDOW_OK, {"open_mask": 0b0001, "close_mask": 0b0010, "time_ms": 30000}),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {**FORM, "source": "write"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    args = configure.call_args.args
    assert args[1:5] == (1, 1, 2, 25.0)


async def test_add_window_manual_and_validation(hass):
    entry = _entry(hass)
    result = await _open_window_form(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FORM, "source": "manual", "close_output": 1}
    )
    assert result["errors"] == {"base": "window_same_output"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FORM, "source": "manual", "open_output": 5, "close_output": 6}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    (window,) = result["data"]["devices"][0]["windows"]
    assert (window["open_mask"], window["close_mask"]) == (0b10000, 0b100000)


async def test_input_pair_used_once(hass):
    entry = _entry(hass, windows=[WINDOW])
    result = await _open_window_form(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FORM, "source": "manual"}
    )
    assert result["errors"] == {"open_input": "window_inputs_in_use"}


async def test_edit_and_delete_window(hass):
    entry = _entry(hass, windows=[WINDOW])
    result = await _open_window_form(hass, entry, "edit_window")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"window": 0})
    assert result["step_id"] == "window"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**FORM, "name": "Sala", "source": "manual"}
    )
    (window,) = result["data"]["devices"][0]["windows"]
    assert window["id"] == WINDOW["id"] and window["name"] == "Sala"

    result = await _open_window_form(hass, entry, "delete_window")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"window": 0})
    assert result["step_id"] == "confirm_delete_window"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"confirm": True})
    assert result["data"]["devices"][0]["windows"] == []


async def test_no_windows_aborts_edit(hass):
    entry = _entry(hass)
    result = await _open_window_form(hass, entry, "edit_window")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_windows_configured"


# ---------------------------------------------------------------------------
# Travel-time calibration
# ---------------------------------------------------------------------------


async def _until(predicate, timeout=3.0):
    import asyncio

    loop_end = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < loop_end, "timeout waiting for calibration step"
        await asyncio.sleep(0.01)


@pytest.fixture
def calibrating(hass):
    cover = _cover(hass)
    cover.window.async_run = AsyncMock()
    cover.window.async_stop = AsyncMock()
    with patch.object(SmartCloudAgeWindowCover, "_async_safety_timeout", AsyncMock(return_value=30.0)), \
            patch.object(SmartCloudAgeWindowCover, "_notify") as notify, \
            patch.object(SmartCloudAgeWindowCover, "_save_travel_time") as save, \
            patch("custom_components.smartcloudage.cover.MIN_TRAVEL_S", 0):
        cover.notify, cover.save = notify, save
        yield cover
    cover._cancel_timers()


async def test_calibration_closes_then_times_the_opening(hass, calibrating):
    cover = calibrating
    await cover.async_calibrate()
    assert cover.calibration == "fechando"
    await _until(lambda: cover.window.async_run.await_count == 1)
    assert cover.window.async_run.await_args.args[0] == -1
    cover.apply_outputs(0b10)  # closing
    cover.apply_outputs(0)  # controller stopped it: closed
    await _until(lambda: cover.calibration == "abrindo")
    assert cover.window.async_run.await_args.args[0] == 1
    cover.apply_outputs(0b01)  # opening
    with pytest.raises(HomeAssistantError):
        await cover.async_open_cover()  # no moves while calibrating
    await cover.async_calibrate_mark()
    await _until(lambda: cover.window.async_stop.await_count == 1)
    cover.apply_outputs(0)  # controller confirms the stop
    await _until(lambda: cover.calibration is None)
    cover.window.async_stop.assert_awaited()
    cover.save.assert_called_once()
    assert cover.window.travel_s == cover.save.call_args.args[0]
    assert cover.current_cover_position == 100
    assert "Calibração concluída" in cover.notify.call_args.args[0]


async def test_wall_button_stop_also_ends_the_calibration(hass, calibrating):
    cover = calibrating
    await cover.async_calibrate()
    await _until(lambda: cover.window.async_run.await_count == 1)
    cover.apply_outputs(0b10)
    cover.apply_outputs(0)
    await _until(lambda: cover.calibration == "abrindo")
    cover.apply_outputs(0b01)
    cover.apply_outputs(0)  # stopped by the wall button / limit switch
    await _until(lambda: cover.calibration is None)
    cover.save.assert_called_once()


async def test_run_cut_by_the_safety_timeout_is_not_saved(hass, calibrating):
    cover = calibrating
    with patch.object(SmartCloudAgeWindowCover, "_async_safety_timeout", AsyncMock(return_value=0.0)):
        await cover.async_calibrate()
        await _until(lambda: cover.window.async_run.await_count == 1)
        cover.apply_outputs(0b10)
        cover.apply_outputs(0)
        await _until(lambda: cover.calibration == "abrindo")
        cover.apply_outputs(0b01)
        cover.apply_outputs(0)
        await _until(lambda: cover.calibration is None)
    cover.save.assert_not_called()
    assert "não concluída" in cover.notify.call_args.args[0]
    assert cover.window.travel_s == 20


async def test_stop_cancels_the_calibration(hass, calibrating):
    cover = calibrating
    await cover.async_calibrate()
    await _until(lambda: cover.window.async_run.await_count == 1)
    await cover.async_stop_cover()
    await _until(lambda: cover.calibration is None)
    cover.save.assert_not_called()
    assert "cancelada" in cover.notify.call_args.args[0]


async def test_mark_outside_calibration_is_refused(hass):
    with pytest.raises(HomeAssistantError):
        await _cover(hass).async_calibrate_mark()
