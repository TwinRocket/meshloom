"""Virtual MeshCore companion radio served over TCP."""

from app.radio_proxy.manager import radio_proxy_manager
from app.radio_proxy.protocol import (
    INSTANCE_ID_HEX_LEN,
    PROXY_MODEL_PREFIX,
    make_instance_id,
    parse_proxy_instance_id,
    proxy_model_string,
)

__all__ = [
    "INSTANCE_ID_HEX_LEN",
    "PROXY_MODEL_PREFIX",
    "make_instance_id",
    "parse_proxy_instance_id",
    "proxy_model_string",
    "radio_proxy_manager",
]
