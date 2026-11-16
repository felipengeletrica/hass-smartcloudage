"""TV remotes (beta): options flow, entities and key presses."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartcloudage.air_conditioner import (
    IR_NO_RESPONSE,
    IR_NO_SEGMENTS,
    IR_SEGMENTS,
    IR_UNSUPPORTED,
    IrSender,
)
from custom_components.smartcloudage.button import TvKeyButton
from custom_components.smartcloudage.config_flow import DOMAIN
from custom_components.smartcloudage.remote import SmartCloudAgeTvRemote, normalize_key
from custom_components.smartcloudage.tv_codes import ESSENTIAL_KEYS, PRESETS, encode_key
from custom_components.smartcloudage.tv_remote import TvRemote, build_tv_remotes

TV = {"id": "t1v2", "name": "TV da sala", "preset": "samsung-tv", "ir_output": 26}


def _ack(status: int):
    return SimpleNamespace(
        topic="CloudAge/controller-01/OutTopic/response",
        payload=json.dumps({"response": {"type": 2, "command": 32, "status": status}}),
    )


@pytest.fixture
def mqtt_subscribe():
    with patch(
        "custom_components.smartcloudage.air_conditioner.mqtt.async_subscribe",
        new_callable=AsyncMock,
        return_value=Mock(),
    ) as subscribe:
        yield subscribe


@pytest.fixture
def ack_publish(mqtt_subscribe):
    """Publish mock that answers every IR command with the given status."""
    status = {"value": 1}

    async def publish(hass, topic, payload, qos, retain):
        callback = mqtt_subscribe.call_args.args[2]
        if status["value"] is not None:
            await callback(_ack(status["value"]))

    with patch(
        "custom_components.smartcloudage.air_conditioner.mqtt.async_publish",
        new_callable=AsyncMock,
        side_effect=publish,
    ) as mock:
        mock.status = status
        yield mock


def _sent(ack_publish) -> list[dict]:
    return [json.loads(c.args[2])["payload"] for c in ack_publish.call_args_list]


def _tv(hass, **config):
    return TvRemote(IrSender(hass, "controller-01"), "Bancada", {**TV, **config})


def _remote(tv):
    entity = SmartCloudAgeTvRemote(tv)
    entity.async_write_ha_state = Mock()
    return entity


# ---------------------------------------------------------------------------
# Key presses
# ---------------------------------------------------------------------------


async def test_button_sends_the_key_frame(hass, ack_publish):
    tv = _tv(hass)
    await TvKeyButton(tv, "volume_up").async_press()

    (payload,) = _sent(ack_publish)
    assert payload == encode_key(PRESETS["samsung-tv"], "volume_up", True, 26)
    message = json.loads(ack_publish.call_args.args[2])
    assert message["command"] == 32
    assert ack_publish.call_args.args[1] == "CloudAge/controller-01"


async def test_send_command_names_digits_and_repeats(hass, ack_publish):
    remote = _remote(_tv(hass))
    await remote.async_send_command(["1", "Volume Up", "ok"], num_repeats=2, delay_secs=0)

    preset = PRESETS["samsung-tv"]
    datas = [p["data"] for p in _sent(ack_publish)]
    keys = ["digit_1", "volume_up", "ok"] * 2
    assert datas == [encode_key(preset, k)["data"] for k in keys]


async def test_unknown_key_is_refused_before_sending(hass, ack_publish):
    remote = _remote(_tv(hass, preset="philips-tv-rc5"))  # RC-5 remote has no arrows
    with pytest.raises(HomeAssistantError):
        await remote.async_send_command(["volume_up", "up"])
    assert ack_publish.call_count == 0


async def test_turn_on_prefers_discrete_power_on(hass, ack_publish):
    remote = _remote(_tv(hass))
    await remote.async_turn_on()
    assert _sent(ack_publish)[-1]["data"] == encode_key(PRESETS["samsung-tv"], "power_on")["data"]
    assert remote.is_on is True

    rc5 = _remote(_tv(hass, preset="philips-tv-rc5"))  # no discrete power keys
    await rc5.async_turn_off()
    assert _sent(ack_publish)[-1] == encode_key(PRESETS["philips-tv-rc5"], "power", rc5.tv._toggle, 26)
    assert rc5.is_on is False


async def test_rc6_toggle_flips_on_every_press(hass, ack_publish):
    tv = _tv(hass, preset="philips-tv-rc6")
    await tv.async_press("mute")
    await tv.async_press("mute")
    first, second = _sent(ack_publish)
    assert first != second
    assert first == encode_key(PRESETS["philips-tv-rc6"], "mute", True, 26)
    assert second == encode_key(PRESETS["philips-tv-rc6"], "mute", False, 26)


async def test_unconfirmed_key_raises(hass, ack_publish):
    ack_publish.status["value"] = 0
    with pytest.raises(HomeAssistantError):
        await _tv(hass).async_press("mute")


def test_normalize_key():
    assert normalize_key(" Volume-Up ") == "volume_up"
    assert normalize_key("7") == "digit_7"
    assert normalize_key("vol_down") == "volume_down"


def test_essential_buttons_enabled_others_disabled(hass):
    tv = _tv(hass)
    buttons = {key: TvKeyButton(tv, key) for key in tv.keys}
    for key, button in buttons.items():
        assert button.entity_registry_enabled_default == (key in ESSENTIAL_KEYS)
    assert buttons["digit_5"].unique_id == "smartcloudage_controller-01_tv_t1v2_key_digit_5"


def test_build_shares_the_controller_sender(hass):
    senders = {"controller-01": IrSender(hass, "controller-01")}
    devices = [{"device_id": "controller-01", "alias": "Bancada", "tv_remotes": [TV]}]
    (tv,) = build_tv_remotes(hass, devices, senders)
    assert tv.sender is senders["controller-01"]


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------


@pytest.fixture
def firmware_probe():
    with patch(
        "custom_components.smartcloudage.air_conditioner.IrSender.async_probe",
        new_callable=AsyncMock,
        return_value=IR_SEGMENTS,
    ) as probe:
        yield probe


def _entry(hass, tv_remotes=None):
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
                    "tv_remotes": tv_remotes or [],
                }
            ]
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_add_tv(hass, firmware_probe):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {"add_tv", "edit_tv", "delete_tv"} <= set(result["menu_options"])
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_tv"}
    )
    assert result["step_id"] == "tv"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "TV do quarto", "preset": "lg-tv", "ir_output": "23"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    (tv,) = result["data"]["devices"][0]["tv_remotes"]
    assert tv["name"] == "TV do quarto"
    assert tv["preset"] == "lg-tv"
    assert tv["ir_output"] == 23
    assert len(tv["id"]) == 8


@pytest.mark.parametrize("probe", [IR_SEGMENTS, IR_NO_SEGMENTS])
async def test_tv_accepts_any_firmware_with_ir(hass, firmware_probe, probe):
    """TV frames need only command 32, not the segmented A/C frames."""
    firmware_probe.return_value = probe
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_tv"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "TV", "preset": "sony-tv", "ir_output": "26"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("probe", "error"),
    [(IR_UNSUPPORTED, "firmware_no_ir"), (IR_NO_RESPONSE, "controller_no_response")],
)
async def test_tv_firmware_errors(hass, firmware_probe, probe, error):
    firmware_probe.return_value = probe
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_tv"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "TV", "preset": "sony-tv", "ir_output": "26"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_edit_and_delete_tv(hass, firmware_probe):
    entry = _entry(hass, [TV])
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_tv"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"tv_remote": 0})
    assert result["step_id"] == "tv"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "TV nova", "preset": "panasonic-tv", "ir_output": "26"}
    )
    (tv,) = result["data"]["devices"][0]["tv_remotes"]
    assert tv["id"] == "t1v2"  # same device, new settings
    assert tv["preset"] == "panasonic-tv"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "delete_tv"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"tv_remote": 0})
    assert result["step_id"] == "confirm_delete_tv"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"confirm": True})
    assert result["data"]["devices"][0]["tv_remotes"] == []


async def test_no_tvs_aborts_edit(hass):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "edit_tv"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_tvs_configured"


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def test_platforms_create_remote_and_one_button_per_key(hass):
    from custom_components.smartcloudage import button, remote

    entry = _entry(hass, [TV])
    tv = _tv(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"tv_remotes": [tv]}

    remotes, buttons = [], []
    await remote.async_setup_entry(hass, entry, remotes.extend)
    await button.async_setup_entry(hass, entry, buttons.extend)

    (entity,) = remotes
    assert entity.unique_id == "smartcloudage_controller-01_tv_t1v2_remote"
    assert entity.device_info["model"] == "TV Samsung"
    assert [b.key for b in buttons] == tv.keys
    assert all(b.device_info == entity.device_info for b in buttons)


async def test_platforms_without_tvs_add_nothing(hass):
    from custom_components.smartcloudage import button, remote

    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"air_conditioners": []}
    added = []
    await remote.async_setup_entry(hass, entry, added.extend)
    await button.async_setup_entry(hass, entry, added.extend)
    assert added == []
