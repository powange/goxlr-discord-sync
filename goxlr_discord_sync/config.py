"""User settings stored in config.json (secrets are kept elsewhere, see secure_store)."""

import logging
from dataclasses import asdict, dataclass, field, fields

from . import paths

log = logging.getLogger(__name__)

# GoXLR mute functions (the "mute_type" of the Cough button and of each fader).
MUTE_TARGETS = ("All", "ToStream", "ToVoiceChat", "ToPhones", "ToLineOut")

MUTE_TARGET_LABELS = {
    "All": "Muted to everything",
    "ToStream": "Muted to Stream",
    "ToVoiceChat": "Muted to Voice Chat",
    "ToPhones": "Muted to Headphones",
    "ToLineOut": "Muted to Line Out",
}

# GoXLR channels that can be assigned to a fader.
FADER_CHANNELS = (
    "Chat", "Game", "Music", "System", "Console", "LineIn", "Sample",
    "Mic", "Headphones", "MicMonitor", "LineOut",
)

# GoXLR buttons that can trigger a Discord action (Cough and fader mutes are already synced).
ACTION_BUTTONS = {
    "Bleep": "Bleep",
    "EffectFx": "FX",
    "EffectMegaphone": "Megaphone",
    "EffectRobot": "Robot",
    "EffectHardTune": "Hard Tune",
    **{f"EffectSelect{i}": f"Effect preset {i}" for i in range(1, 7)},
    "SamplerSelectA": "Sampler bank A",
    "SamplerSelectB": "Sampler bank B",
    "SamplerSelectC": "Sampler bank C",
    "SamplerTopLeft": "Sampler top left",
    "SamplerTopRight": "Sampler top right",
    "SamplerBottomLeft": "Sampler bottom left",
    "SamplerBottomRight": "Sampler bottom right",
    "SamplerClear": "Sampler clear",
}

BUTTON_ACTIONS = {
    "toggle_mute": "Toggle Discord mute",
    "toggle_deafen": "Toggle Discord deafen",
    "leave_voice": "Leave the voice channel",
}

DEFAULT_GOXLR_URL = "ws://localhost:14564/api/websocket"
DEFAULT_OAUTH_PORT = 9543


@dataclass
class Config:
    client_id: str = ""
    cough_sync: bool = True
    chat_sync: bool = True
    # Which effective mute targets count as "muted in Discord" / "deafened in Discord".
    mute_triggers: list[str] = field(default_factory=lambda: ["All", "ToVoiceChat"])
    deafen_triggers: list[str] = field(default_factory=lambda: ["All", "ToPhones"])
    deafen_channel: str = "Chat"
    mixer_serial: str = ""  # empty: first GoXLR found
    goxlr_url: str = DEFAULT_GOXLR_URL
    bidirectional: bool = False
    notifications: bool = True
    check_updates: bool = True
    oauth_port: int = DEFAULT_OAUTH_PORT
    # Discord action -> GoXLR button that triggers it, e.g. {"leave_voice": "SamplerClear"}
    button_actions: dict[str, str] = field(default_factory=dict)

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.oauth_port}/callback"

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        cfg = cls()
        known = {f.name: f for f in fields(cls)}
        for key, value in data.items():
            if key not in known:
                continue
            default = getattr(cfg, key)
            if isinstance(default, bool):
                if isinstance(value, bool):
                    setattr(cfg, key, value)
            elif isinstance(default, int):
                if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 65535:
                    setattr(cfg, key, value)
            elif isinstance(default, list):
                if isinstance(value, list):
                    setattr(cfg, key, [v for v in value if v in MUTE_TARGETS])
            elif isinstance(default, dict):
                if isinstance(value, dict):
                    setattr(cfg, key, {a: b for a, b in value.items()
                                       if a in BUTTON_ACTIONS and b in ACTION_BUTTONS})
            elif isinstance(value, str):
                setattr(cfg, key, value.strip())
        if not cfg.goxlr_url:
            cfg.goxlr_url = DEFAULT_GOXLR_URL
        if not cfg.deafen_channel:
            cfg.deafen_channel = "Chat"
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)

    def updated(self, **changes) -> "Config":
        """A validated copy with these fields changed (unknown or invalid values are ignored)."""
        return Config.from_dict({**self.to_dict(), **changes})


def load() -> Config:
    data = paths.read_json(paths.config_file())
    if data is None:
        return Config()
    return Config.from_dict(data)


def save(cfg: Config) -> None:
    paths.write_json(paths.config_file(), cfg.to_dict())
    log.debug("Configuration saved")


def load_state() -> dict:
    """Small non-user state (update check timestamps...)."""
    return paths.read_json(paths.state_file()) or {}


def save_state(state: dict) -> None:
    paths.write_json(paths.state_file(), state)
