import copy


def mixer(cough="Unmuted", cough_type="All", faders=None):
    faders = faders or {
        "A": {"channel": "Mic", "mute_type": "All", "mute_state": "Unmuted", "scribble": None},
        "B": {"channel": "Chat", "mute_type": "ToPhones", "mute_state": "Unmuted", "scribble": None},
        "C": {"channel": "Music", "mute_type": "All", "mute_state": "Unmuted", "scribble": None},
        "D": {"channel": "System", "mute_type": "All", "mute_state": "Unmuted", "scribble": None},
    }
    return {
        "hardware": {"device_type": "Full"},
        "cough_button": {"is_toggle": True, "mute_type": cough_type, "state": cough},
        "fader_status": copy.deepcopy(faders),
        "levels": {"volumes": {"Chat": 200, "Music": 100}},
    }


def status(**mixers):
    return {"config": {"daemon_version": "1.2.3"}, "mixers": mixers or {"S1": mixer()}}
