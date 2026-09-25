from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from app.path_utils import normalize_contact_route, normalize_route_override

# Valid MeshCore contact types: 0=unknown, 1=client, 2=repeater, 3=room, 4=sensor.
# Corrupted radio data can produce values outside this range.
_VALID_CONTACT_TYPES = frozenset({0, 1, 2, 3, 4})


class ContactRoute(BaseModel):
    """A normalized contact route."""

    path: str = Field(description="Hex-encoded path bytes (empty string for direct/flood)")
    path_len: int = Field(description="Hop count (-1=flood, 0=direct, >0=explicit route)")
    path_hash_mode: int = Field(
        description="Path hash mode (-1=flood, 0=1-byte, 1=2-byte, 2=3-byte hop identifiers)"
    )


class ContactUpsert(BaseModel):
    """Typed write contract for contacts persisted to SQLite."""

    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0
    flags: int = 0
    direct_path: str | None = None
    direct_path_len: int | None = None
    direct_path_hash_mode: int | None = None
    direct_path_updated_at: int | None = None
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool | None = None
    last_contacted: int | None = None
    first_seen: int | None = None

    @classmethod
    def from_contact(cls, contact: Contact, **changes) -> ContactUpsert:
        return cls.model_validate(
            {
                **contact.model_dump(exclude={"last_read_at"}),
                **changes,
            }
        )

    @classmethod
    def from_radio_dict(
        cls, public_key: str, radio_data: dict, on_radio: bool = False
    ) -> ContactUpsert:
        """Convert radio contact data to the contact-row write shape."""
        direct_path, direct_path_len, direct_path_hash_mode = normalize_contact_route(
            radio_data.get("out_path"),
            radio_data.get("out_path_len", -1),
            radio_data.get(
                "out_path_hash_mode",
                -1 if radio_data.get("out_path_len", -1) == -1 else 0,
            ),
        )
        # Clamp invalid contact types to 0 (unknown) — corrupted radio data
        # can produce values like 111 or 240 that break downstream branching.
        raw_type = radio_data.get("type", 0)
        contact_type = raw_type if raw_type in _VALID_CONTACT_TYPES else 0

        # Null out impossible coordinates — the contact is still ingested,
        # but garbage lat/lon (e.g. 1953.7) is discarded rather than stored.
        lat = radio_data.get("adv_lat")
        lon = radio_data.get("adv_lon")
        if lat is not None and not (-90 <= lat <= 90):
            lat = None
        if lon is not None and not (-180 <= lon <= 180):
            lon = None

        return cls(
            public_key=public_key,
            name=radio_data.get("adv_name"),
            type=contact_type,
            flags=radio_data.get("flags", 0),
            direct_path=direct_path,
            direct_path_len=direct_path_len,
            direct_path_hash_mode=direct_path_hash_mode,
            lat=lat,
            lon=lon,
            last_advert=radio_data.get("last_advert"),
            on_radio=on_radio,
        )


class Contact(BaseModel):
    public_key: str = Field(description="Public key (64-char hex)")
    name: str | None = None
    type: int = 0  # 0=unknown, 1=client, 2=repeater, 3=room, 4=sensor
    flags: int = 0
    direct_path: str | None = None
    direct_path_len: int = -1
    direct_path_hash_mode: int = -1
    direct_path_updated_at: int | None = None
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    on_radio: bool = False
    favorite: bool = False
    pinned: bool = False
    last_contacted: int | None = None  # Last time we sent/received a message
    last_read_at: int | None = None  # Server-side read state tracking
    first_seen: int | None = None
    effective_route: ContactRoute | None = None
    effective_route_source: Literal["override", "direct", "flood"] = "flood"
    direct_route: ContactRoute | None = None
    route_override: ContactRoute | None = None

    def model_post_init(self, __context) -> None:
        direct_path, direct_path_len, direct_path_hash_mode = normalize_contact_route(
            self.direct_path,
            self.direct_path_len,
            self.direct_path_hash_mode,
        )
        self.direct_path = direct_path or None
        self.direct_path_len = direct_path_len
        self.direct_path_hash_mode = direct_path_hash_mode

        route_override_path, route_override_len, route_override_hash_mode = (
            normalize_route_override(
                self.route_override_path,
                self.route_override_len,
                self.route_override_hash_mode,
            )
        )
        self.route_override_path = route_override_path or None
        self.route_override_len = route_override_len
        self.route_override_hash_mode = route_override_hash_mode
        if (
            route_override_path is not None
            and route_override_len is not None
            and route_override_hash_mode is not None
        ):
            self.route_override = ContactRoute(
                path=route_override_path,
                path_len=route_override_len,
                path_hash_mode=route_override_hash_mode,
            )
        else:
            self.route_override = None

        if direct_path_len >= 0:
            self.direct_route = ContactRoute(
                path=direct_path,
                path_len=direct_path_len,
                path_hash_mode=direct_path_hash_mode,
            )
        else:
            self.direct_route = None

        path, path_len, path_hash_mode = self.effective_route_tuple()
        if self.has_route_override():
            self.effective_route_source = "override"
        elif self.direct_route is not None:
            self.effective_route_source = "direct"
        else:
            self.effective_route_source = "flood"
        self.effective_route = ContactRoute(
            path=path,
            path_len=path_len,
            path_hash_mode=path_hash_mode,
        )

    def has_route_override(self) -> bool:
        return self.route_override_len is not None

    def effective_route_tuple(self) -> tuple[str, int, int]:
        if self.has_route_override():
            return normalize_contact_route(
                self.route_override_path,
                self.route_override_len,
                self.route_override_hash_mode,
            )
        if self.direct_path_len >= 0:
            return normalize_contact_route(
                self.direct_path,
                self.direct_path_len,
                self.direct_path_hash_mode,
            )
        return "", -1, -1

    def to_radio_dict(self) -> dict:
        """Convert to the dict format expected by meshcore radio commands.

        The radio API uses different field names (adv_name, out_path, etc.)
        than our database schema (name, direct_path, etc.).
        """
        effective_path, effective_path_len, effective_path_hash_mode = self.effective_route_tuple()
        return {
            "public_key": self.public_key,
            "adv_name": self.name or "",
            "type": self.type,
            "flags": self.flags,
            "out_path": effective_path,
            "out_path_len": effective_path_len,
            "out_path_hash_mode": effective_path_hash_mode,
            "adv_lat": self.lat if self.lat is not None else 0.0,
            "adv_lon": self.lon if self.lon is not None else 0.0,
            "last_advert": self.last_advert if self.last_advert is not None else 0,
        }

    def to_upsert(self, **changes) -> ContactUpsert:
        """Convert the stored contact to the repository's write contract."""
        return ContactUpsert.from_contact(self, **changes)


class CreateContactRequest(BaseModel):
    """Request to create a new contact."""

    public_key: str = Field(min_length=64, max_length=64, description="Public key (64-char hex)")
    name: str | None = Field(default=None, description="Display name for the contact")
    type: int = Field(
        default=0, ge=0, le=3, description="Contact type (0=unknown, 1=client, 2=repeater, 3=room)"
    )
    try_historical: bool = Field(
        default=False,
        description="Attempt to decrypt historical DM packets for this contact",
    )


class ContactRoutingOverrideRequest(BaseModel):
    """Request to set, force, or clear a contact routing override."""

    route: str = Field(
        description=(
            "Blank clears the override, "
            '"-1" forces flood, "0" forces direct, and explicit routes are '
            "comma-separated 1/2/3-byte hop hex values"
        )
    )


# Contact type constants
CONTACT_TYPE_REPEATER = 2
CONTACT_TYPE_ROOM = 3


class ContactAdvertPath(BaseModel):
    """A unique advert path observed for a contact."""

    path: str = Field(description="Hex-encoded routing path (empty string for direct)")
    path_len: int = Field(description="Number of hops in the path")
    next_hop: str | None = Field(
        default=None,
        description="First hop toward us as a full hop identifier, or null for direct",
    )
    first_seen: int = Field(description="Unix timestamp of first observation")
    last_seen: int = Field(description="Unix timestamp of most recent observation")
    heard_count: int = Field(description="Number of times this unique path was heard")


class ContactAdvertPathSummary(BaseModel):
    """Recent unique advertisement paths for a single contact."""

    public_key: str = Field(description="Contact public key (64-char hex)")
    paths: list[ContactAdvertPath] = Field(
        default_factory=list, description="Most recent unique advert paths"
    )


class ContactNameHistory(BaseModel):
    """A historical name used by a contact."""

    name: str
    first_seen: int
    last_seen: int


class ContactActiveRoom(BaseModel):
    """A channel where a contact has been active."""

    channel_key: str
    channel_name: str
    message_count: int


class NearestRepeater(BaseModel):
    """A repeater that has relayed a contact's advertisements."""

    public_key: str
    name: str | None = None
    path_len: int
    last_seen: int
    heard_count: int


class ContactAnalyticsHourlyBucket(BaseModel):
    """A single hourly activity bucket for contact analytics."""

    bucket_start: int = Field(description="Unix timestamp for the start of the hour bucket")
    last_24h_count: int = 0
    last_week_average: float = 0
    all_time_average: float = 0


class ContactAnalyticsWeeklyBucket(BaseModel):
    """A single weekly activity bucket for contact analytics."""

    bucket_start: int = Field(description="Unix timestamp for the start of the 7-day bucket")
    message_count: int = 0


class ContactAnalytics(BaseModel):
    """Unified contact analytics payload for keyed and name-only lookups."""

    lookup_type: Literal["contact", "name"]
    name: str
    contact: Contact | None = None
    name_first_seen_at: int | None = None
    name_history: list[ContactNameHistory] = Field(default_factory=list)
    dm_message_count: int = 0
    channel_message_count: int = 0
    includes_direct_messages: bool = False
    most_active_rooms: list[ContactActiveRoom] = Field(default_factory=list)
    advert_paths: list[ContactAdvertPath] = Field(default_factory=list)
    advert_frequency: float | None = Field(
        default=None,
        description="Advert observations per hour (includes multi-path arrivals of same advert)",
    )
    nearest_repeaters: list[NearestRepeater] = Field(default_factory=list)
    hourly_activity: list[ContactAnalyticsHourlyBucket] = Field(default_factory=list)
    weekly_activity: list[ContactAnalyticsWeeklyBucket] = Field(default_factory=list)


class Channel(BaseModel):
    key: str = Field(description="Channel key (32-char hex)")
    name: str
    is_hashtag: bool = False
    on_radio: bool = False
    flood_scope_override: str | None = Field(
        default=None,
        description=(
            "Per-channel outbound flood scope override, tri-state: null = inherit the "
            "global app setting; '*' (UNSCOPED_OVERRIDE_MARKER) = force unscoped/plain "
            "flood even over a scoped global; a region name (e.g. '#Esperance') = scope "
            "this channel."
        ),
    )
    path_hash_mode_override: int | None = Field(
        default=None,
        description="Per-channel path hash mode override (0=1-byte, 1=2-byte, 2=3-byte, null = use radio default)",
    )
    last_read_at: int | None = None  # Server-side read state tracking
    favorite: bool = False
    pinned: bool = False
    muted: bool = False
    muted_until: int | None = Field(
        default=None,
        description="Unix time when a timed mute ends. None means indefinite while muted.",
    )
    membership: Literal["adopted", "pending"] = Field(
        default="adopted",
        description=(
            "adopted = in the classic chat; pending = catalogue-opened, waiting "
            "for adopt/refuse. Existing rows default to adopted."
        ),
    )


class RejectedChannel(BaseModel):
    """A refused catalogue channel that must not auto-reopen."""

    key: str = Field(description="Channel key (32-char hex, uppercase)")
    name: str


class ChannelMessageCounts(BaseModel):
    """Time-windowed message counts for a channel."""

    last_1h: int = 0
    last_24h: int = 0
    last_48h: int = 0
    last_7d: int = 0
    all_time: int = 0


class ChannelTopSender(BaseModel):
    """A top sender in a channel over the last 24 hours."""

    sender_name: str
    sender_key: str | None = None
    message_count: int


class PathHashWidthStats(BaseModel):
    """Hop byte width distribution for parsed raw packets."""

    total_packets: int = 0
    single_byte: int = 0
    double_byte: int = 0
    triple_byte: int = 0
    single_byte_pct: float = 0.0
    double_byte_pct: float = 0.0
    triple_byte_pct: float = 0.0


class ChannelDetail(BaseModel):
    """Comprehensive channel profile data."""

    channel: Channel
    message_counts: ChannelMessageCounts = Field(default_factory=ChannelMessageCounts)
    first_message_at: int | None = None
    unique_sender_count: int = 0
    top_senders_24h: list[ChannelTopSender] = Field(default_factory=list)
    path_hash_width_24h: PathHashWidthStats = Field(default_factory=PathHashWidthStats)


class MessagePath(BaseModel):
    """A single path that a message took to reach us."""

    path: str = Field(description="Hex-encoded routing path")
    received_at: int = Field(description="Unix timestamp when this path was received")
    path_len: int | None = Field(
        default=None,
        description="Hop count. None = legacy (infer as len(path)//2, i.e. 1-byte hops)",
    )
    rssi: int | None = Field(default=None, description="Last-hop RSSI in dBm")
    snr: float | None = Field(default=None, description="Last-hop SNR in dB")


class Message(BaseModel):
    id: int
    type: str = Field(description="PRIV or CHAN")
    conversation_key: str = Field(description="User pubkey for PRIV, channel key for CHAN")
    text: str
    sender_timestamp: int | None = None
    received_at: int
    paths: list[MessagePath] | None = Field(
        default=None, description="List of routing paths this message arrived via"
    )
    txt_type: int = 0
    signature: str | None = None
    sender_key: str | None = None
    outgoing: bool = False
    acked: int = 0
    sender_name: str | None = None
    channel_name: str | None = None
    packet_id: int | None = Field(
        default=None,
        description="Representative raw packet row ID when archival raw bytes exist",
    )
    transport_code: int | None = Field(
        default=None,
        description=(
            "Region scope transport code (uint16) when the message arrived via a "
            "TransportFlood/TransportDirect packet; None for unscoped (plain flood) messages"
        ),
    )
    region: str | None = Field(
        default=None,
        description="Resolved region name for the transport code, if it matched a known region",
    )
    packet_hash: str | None = Field(
        default=None,
        description="Firmware packet hash (16 hex uppercase) used for observer-reach lookup",
    )
    observer_reach_eligible: bool | None = Field(
        default=None,
        description=(
            "True when this message can have Community observers: all CHAN, and "
            "PRIV once a firmware packet_hash is known (flood or directed). "
            "Persisted so eligibility survives raw-packet purge. Sticky true."
        ),
    )


class MessagesAroundResponse(BaseModel):
    messages: list[Message]
    has_older: bool
    has_newer: bool


class ResendChannelMessageResponse(BaseModel):
    status: str
    message_id: int
    message: Message | None = None


class RawPacketGroupData(BaseModel):
    """Decrypted GroupData blob attached to a raw packet (no messages row)."""

    data_type: int
    data_len: int
    data_hex: str
    data_text: str | None = None


class RawPacketDecryptedInfo(BaseModel):
    """Decryption info for a raw packet (when successfully decrypted)."""

    channel_name: str | None = None
    sender: str | None = None
    channel_key: str | None = None
    contact_key: str | None = None
    sender_timestamp: int | None = None
    message: str | None = None
    group_data: RawPacketGroupData | None = None


class RawPacketBroadcast(BaseModel):
    """Raw packet payload broadcast via WebSocket.

    This extends the database model with runtime-computed fields
    like payload_type, snr, rssi, and decryption info.
    """

    id: int
    observation_id: int = Field(
        description=(
            "Monotonic per-process ID for this RF observation (distinct from the DB packet row ID)"
        )
    )
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    payload_type: str = Field(description="Packet type name (e.g., GROUP_TEXT, ADVERT)")
    snr: float | None = Field(default=None, description="Signal-to-noise ratio in dB")
    rssi: int | None = Field(default=None, description="Received signal strength in dBm")
    decrypted: bool = False
    decrypted_info: RawPacketDecryptedInfo | None = None
    transport_code: int | None = Field(
        default=None,
        description="Region scope transport code (uint16) for TransportFlood/TransportDirect packets",
    )
    region: str | None = Field(
        default=None,
        description="Resolved region name for the transport code, if it matched a known region",
    )
    packet_hash: str | None = Field(
        default=None,
        description="Firmware packet hash (16 hex uppercase SHA-256). Used by #live hash8 grouping.",
    )


class RawPacketDetail(BaseModel):
    """Stored raw-packet detail returned by the packet API."""

    id: int
    timestamp: int
    data: str = Field(description="Hex-encoded packet data")
    payload_type: str = Field(description="Packet type name (e.g. GROUP_TEXT, ADVERT)")
    snr: float | None = Field(default=None, description="Signal-to-noise ratio in dB if available")
    rssi: int | None = Field(
        default=None, description="Received signal strength in dBm if available"
    )
    decrypted: bool = False
    decrypted_info: RawPacketDecryptedInfo | None = None
    transport_code: int | None = Field(
        default=None,
        description="Region scope transport code (uint16) for TransportFlood/TransportDirect packets",
    )
    region: str | None = Field(
        default=None,
        description="Resolved region name for the transport code, if it matched a known region",
    )


class RawPacketHistoryResponse(BaseModel):
    """Bounded newest-first raw-packet history page."""

    items: list[RawPacketDetail]
    total: int = Field(
        description="Rows in the since/until/after_id window (idx_raw_packets_timestamp)"
    )
    truncated: bool = Field(
        description="True when the bounded header scan stopped at max_scan with more rows left"
    )
    scanned: int = Field(description="Rows walked while building this page")


class UndecryptedGroupTextSample(BaseModel):
    """One distinct encrypted GROUP_TEXT sample."""

    channel_hash: str = Field(description="Lowercase two-character channel-hash hex")
    packet_id: int
    data: str = Field(description="Lowercase hex-encoded full raw packet")
    timestamp: int = Field(description="Unix timestamp when the RF packet was heard")
    cipher_mac: str = Field(description="Lowercase four-character cipher-MAC hex")


class UndecryptedGroupTextSamplesResponse(BaseModel):
    """Bounded sample of recent undecrypted GROUP_TEXT packets."""

    hash_count: int = Field(description="Distinct channel hashes represented in samples")
    packet_count: int = Field(
        description="GROUP_TEXT rows encountered among scanned undecrypted rows in the time window"
    )
    scanned: int = Field(description="Undecrypted rows walked before a configured stop condition")
    samples: list[UndecryptedGroupTextSample] = Field(default_factory=list)


class SendMessageRequest(BaseModel):
    text: str = Field(min_length=1)


class SendDirectMessageRequest(SendMessageRequest):
    destination: str = Field(
        description="Recipient public key (64-char hex preferred; prefix must resolve uniquely)"
    )


class SendChannelMessageRequest(SendMessageRequest):
    channel_key: str = Field(description="Channel key (32-char hex)")
    flood_scope_override: str | None = Field(
        default=None,
        description=(
            "Per-send regional flood-scope override. None = use the channel's persisted "
            "override (or none); empty string = force unscoped/plain flood; a region name "
            "scopes this single send to that region. Takes precedence over the channel's "
            "persisted flood_scope_override for this send only."
        ),
    )


class RepeaterLoginRequest(BaseModel):
    """Request to log in to a repeater."""

    password: str = Field(
        default="", description="Repeater password (empty string for guest login)"
    )


class RepeaterLoginResponse(BaseModel):
    """Response from repeater login."""

    status: str = Field(description="Login result status")
    authenticated: bool = Field(description="Whether repeater authentication was confirmed")
    message: str | None = Field(
        default=None,
        description="Optional warning or error message when authentication was not confirmed",
    )


class RepeaterStatusResponse(BaseModel):
    """Status telemetry from a repeater (single attempt, no retries)."""

    battery_volts: float | None = Field(
        default=None, description="Battery voltage in volts (null when bat is absent)"
    )
    tx_queue_len: int = Field(description="Transmit queue length")
    noise_floor_dbm: int = Field(description="Noise floor in dBm")
    last_rssi_dbm: int = Field(description="Last RSSI in dBm")
    last_snr_db: float = Field(description="Last SNR in dB")
    packets_received: int = Field(description="Total packets received")
    packets_sent: int = Field(description="Total packets sent")
    airtime_seconds: int = Field(description="TX airtime in seconds")
    rx_airtime_seconds: int = Field(description="RX airtime in seconds")
    uptime_seconds: int = Field(description="Uptime in seconds")
    sent_flood: int = Field(description="Flood packets sent")
    sent_direct: int = Field(description="Direct packets sent")
    recv_flood: int = Field(description="Flood packets received")
    recv_direct: int = Field(description="Direct packets received")
    flood_dups: int = Field(description="Duplicate flood packets")
    direct_dups: int = Field(description="Duplicate direct packets")
    full_events: int = Field(description="Full event queue count")
    recv_errors: int | None = Field(default=None, description="Radio-level RX packet errors")
    telemetry_history: list[TelemetryHistoryEntry] = Field(
        default_factory=list, description="Recent telemetry history snapshots"
    )


class RepeaterNodeInfoResponse(BaseModel):
    """Identity/location info from a repeater (small CLI batch)."""

    name: str | None = Field(default=None, description="Repeater name")
    lat: str | None = Field(default=None, description="Latitude")
    lon: str | None = Field(default=None, description="Longitude")
    clock_utc: str | None = Field(default=None, description="Repeater clock in UTC")


class RepeaterRadioSettingsResponse(BaseModel):
    """Radio settings from a repeater (radio/config CLI batch)."""

    firmware_version: str | None = Field(default=None, description="Firmware version string")
    radio: str | None = Field(default=None, description="Radio settings (freq,bw,sf,cr)")
    tx_power: str | None = Field(default=None, description="TX power in dBm")
    airtime_factor: str | None = Field(default=None, description="Airtime factor")
    duty_cycle_limit: str | None = Field(
        default=None,
        description=(
            "Configured duty-cycle limit as a percentage string (e.g. '25.0%'), derived "
            "by firmware from airtime_factor (100/(af+1)). This is the configured ceiling, "
            "not the current measured duty cycle. Only available on firmware >= 1.15; None "
            "on older nodes that don't support 'get dutycycle'."
        ),
    )
    repeat_enabled: str | None = Field(default=None, description="Repeat mode enabled")
    flood_max: str | None = Field(default=None, description="Max flood hops")


class RepeaterAdvertIntervalsResponse(BaseModel):
    """Advertisement intervals from a repeater."""

    advert_interval: str | None = Field(default=None, description="Local advert interval")
    flood_advert_interval: str | None = Field(default=None, description="Flood advert interval")


class RepeaterOwnerInfoResponse(BaseModel):
    """Owner info, firmware, and guest password from a repeater.

    ``owner_info``, ``firmware_version``, and ``name`` come from the
    guest-accessible binary owner-info request (REQ_TYPE_GET_OWNER_INFO / 0x07).
    ``guest_password`` is admin-only and still comes from the CLI, so guests see
    ``None`` for it.
    """

    owner_info: str | None = Field(default=None, description="Owner info string")
    firmware_version: str | None = Field(
        default=None, description="Firmware version string (from binary owner-info request)"
    )
    name: str | None = Field(
        default=None, description="Repeater name (from binary owner-info request)"
    )
    guest_password: str | None = Field(default=None, description="Guest password (admin only)")


class RepeaterRegionEntry(BaseModel):
    """One region from a repeater's region hierarchy dump."""

    name: str = Field(description="Region name ('*' is the wildcard/global root)")
    depth: int = Field(description="Indentation depth in the hierarchy (0 = root)")
    flood_allowed: bool = Field(description="True if flood is allowed for this region")
    is_home: bool = Field(description="True if this is the repeater's home region")


class RepeaterRegionsResponse(BaseModel):
    """Region hierarchy and flood permissions from a repeater.

    Primary source is the admin `region` CLI dump — an indented tree capped at
    ~160 chars, so large region sets can be truncated (``truncated`` flags this).
    When the CLI is unavailable (e.g. guest access), ``source`` is ``"anon"`` and
    ``regions`` is the guest-accessible anon request's flat list of flood-allowed
    region names only — no hierarchy, no blocked regions, no home marker. See
    issue #309.
    """

    regions: list[RepeaterRegionEntry] = Field(default_factory=list)
    raw: str | None = Field(default=None, description="Raw CLI dump text as received")
    truncated: bool = Field(default=False, description="True if the dump was likely truncated")
    source: Literal["cli", "anon"] | None = Field(
        default=None,
        description="'cli' = full admin hierarchy; 'anon' = guest flood-allowed names only",
    )


class LppSensor(BaseModel):
    """A single CayenneLPP sensor reading from req_telemetry_sync."""

    channel: int = Field(description="LPP channel number")
    type_name: str = Field(description="Sensor type name (e.g. temperature, humidity)")
    value: float | dict = Field(
        description="Scalar value or dict for multi-value sensors (GPS, accel)"
    )


class RepeaterLppTelemetryResponse(BaseModel):
    """CayenneLPP sensor telemetry from a repeater."""

    sensors: list[LppSensor] = Field(default_factory=list, description="List of sensor readings")


class ContactTelemetryResponse(BaseModel):
    """On-demand CayenneLPP telemetry snapshot from any contact."""

    sensors: list[LppSensor] = Field(default_factory=list, description="List of sensor readings")
    fetched_at: int = Field(description="Unix timestamp when this telemetry was fetched")
    telemetry_history: list[TelemetryHistoryEntry] = Field(
        default_factory=list, description="Recent telemetry history entries"
    )


class NeighborInfo(BaseModel):
    """Information about a neighbor seen by a repeater."""

    pubkey_prefix: str = Field(description="Public key prefix (4-12 chars)")
    name: str | None = Field(default=None, description="Resolved contact name if known")
    snr: float = Field(description="Signal-to-noise ratio in dB")
    last_heard_seconds: int = Field(description="Seconds since last heard")


class AclEntry(BaseModel):
    """Access control list entry for a repeater."""

    pubkey_prefix: str = Field(description="Public key prefix (12 chars)")
    name: str | None = Field(default=None, description="Resolved contact name if known")
    permission: int = Field(
        description="Permission level: 0=Guest, 1=Read-only, 2=Read-write, 3=Admin"
    )
    permission_name: str = Field(description="Human-readable permission name")


class RepeaterNeighborsResponse(BaseModel):
    """Neighbors list from a repeater."""

    neighbors: list[NeighborInfo] = Field(
        default_factory=list, description="List of neighbors seen by repeater"
    )
    reported_count: int | None = Field(
        default=None,
        description=(
            "Total neighbor count reported by the repeater firmware, independent of "
            "how many entries were actually returned. May exceed len(neighbors) when a "
            "multi-chunk fetch is incomplete (dropped follow-up query / duty-cycle throttle)."
        ),
    )


class RepeaterAclResponse(BaseModel):
    """ACL list from a repeater."""

    acl: list[AclEntry] = Field(default_factory=list, description="Access control list")


class TraceResponse(BaseModel):
    """Result of a direct (zero-hop) trace to a contact."""

    remote_snr: float | None = Field(
        default=None, description="SNR at which the target heard us (dB)"
    )
    local_snr: float | None = Field(
        default=None, description="SNR at which we heard the target on the bounce-back (dB)"
    )
    path_len: int = Field(description="Number of hops in the trace path")


class RadioTraceHopRequest(BaseModel):
    """One requested hop in a radio trace path."""

    public_key: str | None = Field(
        default=None,
        description="Full repeater public key when this hop maps to a known repeater",
    )
    hop_hex: str | None = Field(
        default=None,
        description="Raw hop hash hex when using a custom repeater prefix",
    )


class RadioTraceRequest(BaseModel):
    """Ordered trace path for a radio trace loop."""

    hop_hash_bytes: Literal[1, 2, 4] = Field(
        default=4,
        description="Hash width in bytes for every hop in this trace path",
    )
    hops: list[RadioTraceHopRequest] = Field(
        min_length=1,
        description="Ordered repeater hops, using either known repeater keys or custom hop hex",
    )


class RadioTraceNode(BaseModel):
    """One resolved node in a radio trace result."""

    role: Literal["repeater", "custom", "local"] = Field(description="Node role in the trace")
    public_key: str | None = Field(
        default=None,
        description="Resolved full public key for this node when known",
    )
    name: str | None = Field(default=None, description="Display name for this node when known")
    observed_hash: str | None = Field(
        default=None,
        description="Observed 4-byte trace hash for this node as hex",
    )
    snr: float | None = Field(default=None, description="Reported SNR for this node in dB")


class RadioTraceResponse(BaseModel):
    """Resolved multi-hop radio trace result."""

    path_len: int = Field(description="Number of hashed nodes returned by the trace response")
    timeout_seconds: float = Field(description="Timeout window used while waiting for the trace")
    nodes: list[RadioTraceNode] = Field(
        default_factory=list,
        description="Ordered trace nodes: repeater hops followed by the terminal local radio",
    )


class PathDiscoveryRoute(BaseModel):
    """One resolved route returned by contact path discovery."""

    path: str = Field(description="Hex-encoded path bytes")
    path_len: int = Field(description="Hop count for this route")
    path_hash_mode: int = Field(
        description="Path hash mode (0=1-byte, 1=2-byte, 2=3-byte hop identifiers)"
    )


class PathDiscoveryResponse(BaseModel):
    """Round-trip routing data for a contact path discovery request."""

    contact: Contact = Field(
        description="Updated contact row after saving the learned forward path"
    )
    forward_path: PathDiscoveryRoute = Field(
        description="Route used from the local radio to the target contact"
    )
    return_path: PathDiscoveryRoute = Field(
        description="Route used from the target contact back to the local radio"
    )


class CommandRequest(BaseModel):
    """Request to send a CLI command to a repeater."""

    command: str = Field(min_length=1, description="CLI command to send")


class CommandResponse(BaseModel):
    """Response from a repeater CLI command."""

    command: str = Field(description="The command that was sent")
    response: str = Field(description="Response from the repeater")
    sender_timestamp: int | None = Field(
        default=None, description="Timestamp from the repeater's response"
    )


class RadioDiscoveryRequest(BaseModel):
    """Request to discover nearby mesh nodes from the local radio."""

    target: Literal["repeaters", "sensors", "all"] = Field(
        default="all",
        description="Which node classes to discover over the mesh",
    )


class RadioDiscoveryResult(BaseModel):
    """One mesh node heard during a discovery sweep."""

    public_key: str = Field(description="Discovered node public key as hex")
    name: str | None = Field(
        default=None,
        description="Known name for this node from contacts DB, if any",
    )
    node_type: Literal["repeater", "sensor"] = Field(description="Discovered node class")
    heard_count: int = Field(default=1, description="How many responses were heard from this node")
    local_snr: float | None = Field(
        default=None,
        description="SNR at which the local radio heard the response (dB)",
    )
    local_rssi: int | None = Field(
        default=None,
        description="RSSI at which the local radio heard the response (dBm)",
    )
    remote_snr: float | None = Field(
        default=None,
        description="SNR reported by the remote node while hearing our discovery request (dB)",
    )


class RadioDiscoveryResponse(BaseModel):
    """Response payload for a mesh discovery sweep."""

    target: Literal["repeaters", "sensors", "all"] = Field(
        description="Which node classes were requested"
    )
    duration_seconds: float = Field(description="How long the sweep listened for responses")
    results: list[RadioDiscoveryResult] = Field(
        default_factory=list,
        description="Deduplicated discovery responses heard during the sweep",
    )


class RadioRegionDiscoveryRequest(BaseModel):
    """Request to sweep nearby repeaters for their flood-allowed region names.

    Uses the guest-accessible anon regions request (direct-routed, so only
    repeaters in range answer). When ``public_keys`` is omitted, the sweep
    targets the most recently seen repeater contacts.
    """

    public_keys: list[str] | None = Field(
        default=None,
        description="Specific repeater public keys to query; None = most recent repeater contacts",
    )
    max_repeaters: int = Field(
        default=8,
        ge=1,
        le=40,
        description="Maximum number of repeaters to query in one sweep",
    )


class RadioRegionVerifyRequest(BaseModel):
    """Check candidate region names against traffic this node has already heard.

    Needs no radio and sends nothing: a region name can be confirmed offline
    because the transport code is recomputable from a stored packet. Only the
    other direction is impossible.
    """

    names: list[str] | None = Field(
        default=None,
        description="Names to test; None asks the server to propose some from what it knows",
    )
    hours: int = Field(
        default=24,
        ge=1,
        le=24 * 30,
        description="How far back to look for regionally routed packets",
    )
    max_packets: int = Field(
        default=600,
        ge=50,
        le=5000,
        description="Cap on packets read, newest first",
    )


class RadioRegionMatch(BaseModel):
    """One candidate and how much of the stored traffic it explains."""

    name: str
    packets: int = Field(description="Regional packets in the window this name decodes")


class RadioRegionVerifyResponse(BaseModel):
    """What the stored traffic says about a set of candidate names."""

    window_hours: int
    packets_read: int = Field(description="Stored packets examined, newest first")
    regional_packets: int = Field(
        description=(
            "Of those, the ones carrying a transport code. Zero means nothing "
            "regional has been heard, so no name can be confirmed either way."
        )
    )
    proposed: bool = Field(description="True when the server chose the candidates")
    matches: list[RadioRegionMatch]


class RadioRegionDiscoveryRepeater(BaseModel):
    """One repeater's result from a region discovery sweep."""

    public_key: str = Field(description="Repeater public key")
    name: str | None = Field(default=None, description="Known contact name, if any")
    answered: bool = Field(description="True if the repeater answered the anon regions request")
    outcome: Literal["answered", "no_reply", "contact_add_failed", "error"] = Field(
        default="no_reply",
        description=(
            "Why nothing came back, when nothing did. Every failure used to read "
            "as silence, which left an operator with a count and no next step: a "
            "radio whose contact list is full fails every repeater at once and is "
            "worth saying out loud."
        ),
    )
    detail: str | None = Field(
        default=None, description="What the radio reported, when it reported anything"
    )
    attempts: int = Field(default=1, description="Requests sent to this repeater")
    regions: list[str] = Field(
        default_factory=list,
        description="Flood-allowed region names reported by this repeater (wildcard excluded)",
    )


class RadioRegionDiscoveryResponse(BaseModel):
    """Aggregated result of a region discovery sweep across nearby repeaters.

    ``regions`` is the deduplicated union of every repeater's flood-allowed
    region names — the list an operator can merge into ``known_regions``. The
    anon request only reports flood-allowed names, so blocked regions and the
    hierarchy are not visible here (use the per-repeater admin regions pane for
    the full picture). See issue #309.
    """

    repeaters_queried: int = Field(description="How many repeaters were contacted")
    repeaters_answered: int = Field(description="How many repeaters answered the request")
    regions: list[str] = Field(
        default_factory=list,
        description="Deduplicated union of flood-allowed region names across all repeaters",
    )
    results: list[RadioRegionDiscoveryRepeater] = Field(
        default_factory=list,
        description="Per-repeater region results",
    )


class UnreadCounts(BaseModel):
    """Aggregated unread counts, mention flags, last-message times/previews, and read boundaries."""

    counts: dict[str, int] = Field(
        default_factory=dict, description="Map of stateKey -> unread count"
    )
    mentions: dict[str, bool] = Field(
        default_factory=dict, description="Map of stateKey -> has mention"
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict, description="Map of stateKey -> last message timestamp"
    )
    last_message_previews: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Map of stateKey -> last message text, truncated to ~120 characters. "
            "Same row as last_message_times (ROW_NUMBER by received_at DESC, id DESC)."
        ),
    )
    last_message_outgoing: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Map of stateKey -> whether the newest message was sent by this radio. "
            "Same row as last_message_previews."
        ),
    )
    first_unread_ids: dict[str, int | None] = Field(
        default_factory=dict,
        description=(
            "Map of stateKey -> id of the oldest unread message. Lets the client place "
            "the unread divider (and jump to it) without paging back through history."
        ),
    )
    last_read_ats: dict[str, int | None] = Field(
        default_factory=dict, description="Map of stateKey -> server-side last_read_at boundary"
    )


class ServerLabel(BaseModel):
    """A name for this instance, shown across the top of every page.

    Someone running Meshloom at home, at work and at a relative's house has three
    interfaces that look identical, and the only way to tell them apart was the
    address bar. Empty means no band at all, which is the default: an instance
    nobody needs to distinguish should not pay for the option.
    """

    text: str = Field(default="", max_length=60, description="Empty hides the band")
    color: str = Field(default="#062d60", description="Background, as #rrggbb")
    size_px: int = Field(
        default=14,
        ge=12,
        le=48,
        description=(
            "Text size in pixels, which also sets how tall the band is. A number "
            "rather than three named steps: someone who wants it noticeable from "
            "across a room and someone who wants a discreet strip are asking for "
            "different things, and a picker like a mail composer answers both."
        ),
    )
    bold: bool = False
    italic: bool = False
    font: Literal["system", "serif", "mono"] = Field(
        default="system",
        description=(
            "A family the browser certainly has. Naming an arbitrary font would "
            "render as something else wherever it is not installed, which is a "
            "worse answer than three that always work."
        ),
    )


class UiPreferences(BaseModel):
    """Interface preferences, kept with the instance rather than in a browser.

    The same person reaches one Meshloom from a phone and a desktop, and there is
    no notion of separate users here — a single credential pair guards the whole
    instance. Preferences stored per browser therefore had to be set again on
    every device. Migration 009 recorded this intention and stopped at a flag.
    """

    nav_rail: list[str] = Field(
        default_factory=list,
        description=(
            "The desktop rail's contents, in order, as destination and tool ids. "
            "Empty means the defaults. One ordered list rather than a set of pins "
            "plus an order, which could disagree with each other."
        ),
    )
    theme: str = Field(
        default="",
        description="Theme id, or empty to follow the operating system",
    )
    server_label: ServerLabel = Field(
        default_factory=ServerLabel,
        description=(
            "Names this instance for whoever is looking at it. Kept here rather "
            "than in a browser because it describes the server, not the device: "
            "stored per browser it had to be set again on every phone and laptop, "
            "and was missing exactly when someone arrived from a new one."
        ),
    )


class TelemetryAlertChannels(BaseModel):
    """Which transports fire when a telemetry alert latches."""

    push: bool = True
    email: bool = False
    webhook: bool = False


class TelemetryRuleSpec(BaseModel):
    """One gauge, silence, GPS, or LPP rule. Omitted fields stay unset on PATCH."""

    enabled: bool | None = None
    op: Literal["lt", "gt"] | None = None
    threshold: float | None = None
    hysteresis: float | None = None


class TelemetryAlertRuleOverride(BaseModel):
    """Per-node alerting master switch plus optional rule fragments."""

    alerting: bool | None = None
    rules: dict[str, TelemetryRuleSpec] | None = None

    @model_validator(mode="before")
    @classmethod
    def _absorb_legacy_thresholds(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        rules = data.get("rules")
        if not isinstance(rules, dict):
            rules = {}
        if "battery_volts_min" in data:
            battery = dict(rules.get("battery") or {})
            battery["threshold"] = data.pop("battery_volts_min")
            rules["battery"] = battery
        if "noise_floor_max_dbm" in data:
            noise = dict(rules.get("noise") or {})
            noise["threshold"] = data.pop("noise_floor_max_dbm")
            rules["noise"] = noise
        if "misses_before_alert" in data:
            silence = dict(rules.get("silence") or {})
            silence["threshold"] = data.pop("misses_before_alert")
            rules["silence"] = silence
        if rules:
            data["rules"] = rules
        return data

    @property
    def battery_volts_min(self) -> float | None:
        rule = (self.rules or {}).get("battery")
        return rule.threshold if rule is not None else None

    @property
    def noise_floor_max_dbm(self) -> float | None:
        rule = (self.rules or {}).get("noise")
        return rule.threshold if rule is not None else None

    @property
    def misses_before_alert(self) -> int | None:
        rule = (self.rules or {}).get("silence")
        if rule is None or rule.threshold is None:
            return None
        return int(rule.threshold)


class TelemetryAlertRules(BaseModel):
    """Global telemetry alert rules plus optional per-public-key overrides."""

    channels: TelemetryAlertChannels = Field(default_factory=TelemetryAlertChannels)
    rules: dict[str, TelemetryRuleSpec] = Field(default_factory=dict)
    overrides: dict[str, TelemetryAlertRuleOverride] | None = Field(
        default_factory=dict,
        description="Per-public-key fragments; omit or null on PATCH to keep stored",
    )

    @model_validator(mode="before")
    @classmethod
    def _absorb_legacy_thresholds(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        rules = data.get("rules")
        if not isinstance(rules, dict):
            rules = {}
        if "battery_volts_min" in data:
            battery = dict(rules.get("battery") or {})
            battery["threshold"] = data.pop("battery_volts_min")
            rules["battery"] = battery
        if "noise_floor_max_dbm" in data:
            noise = dict(rules.get("noise") or {})
            noise["threshold"] = data.pop("noise_floor_max_dbm")
            rules["noise"] = noise
        if "misses_before_alert" in data:
            silence = dict(rules.get("silence") or {})
            silence["threshold"] = data.pop("misses_before_alert")
            rules["silence"] = silence
        if rules:
            data["rules"] = rules
        return data

    @computed_field
    @property
    def battery_volts_min(self) -> float:
        rule = self.rules.get("battery")
        return float(rule.threshold) if rule is not None and rule.threshold is not None else 3.5

    @computed_field
    @property
    def noise_floor_max_dbm(self) -> float:
        rule = self.rules.get("noise")
        return float(rule.threshold) if rule is not None and rule.threshold is not None else -90.0

    @computed_field
    @property
    def misses_before_alert(self) -> int:
        rule = self.rules.get("silence")
        if rule is None or rule.threshold is None:
            return 2
        return int(rule.threshold)


class NotificationEmailDest(BaseModel):
    """SMTP destination. Assembled Apprise URLs are never persisted."""

    model_config = {"populate_by_name": True}

    host: str = ""
    port: int = 587
    mode: Literal["none", "starttls", "ssl"] = "starttls"
    user: str = ""
    password: str = ""
    from_address: str = Field(default="", alias="from")
    to: str = ""


class NotificationWebhookDest(BaseModel):
    url: str = ""
    hmac_secret: str = ""


class NotificationDestinations(BaseModel):
    email: NotificationEmailDest = Field(default_factory=NotificationEmailDest)
    webhook: NotificationWebhookDest = Field(default_factory=NotificationWebhookDest)


class NotificationEmailDestUpdate(BaseModel):
    """PATCH fragment. omit/null keeps a secret; empty string clears it."""

    model_config = {"populate_by_name": True}

    host: str | None = None
    port: int | None = None
    mode: Literal["none", "starttls", "ssl"] | None = None
    user: str | None = None
    password: str | None = None
    from_address: str | None = Field(default=None, alias="from")
    to: str | None = None


class NotificationWebhookDestUpdate(BaseModel):
    url: str | None = None
    hmac_secret: str | None = None


class NotificationDestinationsUpdate(BaseModel):
    email: NotificationEmailDestUpdate | None = None
    webhook: NotificationWebhookDestUpdate | None = None


class AppSettings(BaseModel):
    """Application settings stored in the database."""

    max_radio_contacts: int = Field(
        default=200,
        description=(
            "Configured radio contact capacity used for maintenance thresholds; "
            "favorites reload first, then background fill targets about 80% of this value"
        ),
    )
    auto_decrypt_dm_on_advert: bool = Field(
        default=True,
        description="Whether to attempt historical DM decryption on new contact advertisement",
    )
    last_message_times: dict[str, int] = Field(
        default_factory=dict,
        description="Map of conversation state keys to last message timestamps",
    )
    ui_preferences: UiPreferences = Field(
        default_factory=UiPreferences,
        description="Interface preferences shared by every device reaching this instance",
    )
    advert_interval: int = Field(
        default=0,
        description="Periodic advertisement interval in seconds (0 = disabled)",
    )
    last_advert_time: int = Field(
        default=0,
        description="Unix timestamp of last advertisement sent (0 = never)",
    )
    flood_scope: str = Field(
        default="",
        description="Outbound flood scope / region name (empty = disabled, no tagging)",
    )
    known_regions: list[str] = Field(
        default_factory=list,
        description=(
            "Region scope names used to resolve incoming TransportFlood/TransportDirect "
            "packets back to a readable region label (packet inspector + channel decoration)"
        ),
    )
    blocked_keys: list[str] = Field(
        default_factory=list,
        description="Public keys whose messages are hidden from the UI",
    )
    rejected_channels: list[RejectedChannel] = Field(
        default_factory=list,
        description="Refused catalogue channels that must not auto-reopen",
    )
    blocked_names: list[str] = Field(
        default_factory=list,
        description="Display names whose messages are hidden from the UI",
    )
    discovery_blocked_types: list[int] = Field(
        default_factory=list,
        description=(
            "Contact type codes (1=Client, 2=Repeater, 3=Room, 4=Sensor) whose "
            "advertisements should not create new contacts; existing contacts are still updated"
        ),
    )
    tracked_telemetry_repeaters: list[str] = Field(
        default_factory=list,
        description="Public keys of repeaters opted into periodic telemetry collection (max 8)",
    )
    tracked_telemetry_contacts: list[str] = Field(
        default_factory=list,
        description="Public keys of contacts opted into periodic LPP telemetry collection (max 8)",
    )
    telemetry_interval_hours: int = Field(
        default=8,
        description=(
            "User-preferred telemetry collection interval in hours. The backend "
            "clamps this up to the shortest legal interval given the number of "
            "tracked repeaters and contacts so daily checks stay under a 24/day ceiling."
        ),
    )
    telemetry_routed_hourly: bool = Field(
        default=False,
        description=(
            "When enabled, tracked repeaters/contacts with a direct or routed (non-flood) "
            "path are polled every hour instead of on the normal scheduled interval."
        ),
    )
    telemetry_alert_rules: TelemetryAlertRules = Field(
        default_factory=TelemetryAlertRules,
        description="Global telemetry alert rules plus optional per-node overrides",
    )
    notification_destinations: NotificationDestinations = Field(
        default_factory=NotificationDestinations,
        description="SMTP and webhook destinations for system telemetry alerts",
    )
    auto_resend_channel: bool = Field(
        default=False,
        description=(
            "When enabled, outgoing channel messages that receive no echo within 2 seconds "
            "are automatically byte-perfect resent once (within the 30-second dedup window)"
        ),
    )
    stale_contact_days: int = Field(
        default=0,
        description=(
            "Automatic stale-contact purge threshold in days. 0 = disabled (default). "
            "When set, a best-effort job deletes non-favorite contacts whose last_seen "
            "and first_seen are both older than this many days (same last-heard filter "
            "as the bulk-delete UI)."
        ),
    )
    auto_update: bool = Field(
        default=False,
        description="When true, apply a newer Meshloom package/image through the helper",
    )
    auto_update_window_start: str = Field(
        default="00:00",
        description="Local HH:MM when the auto-apply window opens",
    )
    auto_update_window_end: str = Field(
        default="00:00",
        description="Local HH:MM when the auto-apply window closes (equal to start = full day)",
    )
    auto_update_weekdays: list[int] = Field(
        default_factory=lambda: [0, 1, 2, 3, 4, 5, 6],
        description="ISO weekdays Monday=0 .. Sunday=6 when auto-apply may run",
    )
    directory_available: bool = Field(
        default=False,
        description=(
            "Computed: Meshloom Community is on, so the Stats directory answers. "
            "Not stored. The browser uses this to show hop names / observer reach."
        ),
    )


class DirectoryHopHit(BaseModel):
    name: str
    source: Literal["corescope"]
    hash_width: int
    public_key: str | None = None
    lat: float | None = None
    lon: float | None = None


class DirectoryResolveHopsRequest(BaseModel):
    hops: list[str] = Field(default_factory=list, max_length=64)


class DirectoryResolveHopsResponse(BaseModel):
    resolved: dict[str, DirectoryHopHit] = Field(default_factory=dict)


class DirectoryCacheResetResponse(BaseModel):
    deleted: int


class DirectoryMapNode(BaseModel):
    """Directory or local GPS node. Never mixed into RF contact rows."""

    public_key: str
    name: str
    role: Literal["repeater", "room", "client", "companion", "sensor", "observer", "unknown"] = (
        "unknown"
    )
    lat: float
    lon: float
    source: Literal["community-db", "corescope", "local"] = "community-db"
    last_seen: int | None = None


class DirectoryMapNodesResponse(BaseModel):
    nodes: list[DirectoryMapNode] = Field(default_factory=list)
    total: int | None = None


class DirectoryReachObserver(BaseModel):
    """A directory 0-hop observer. Radius is never derived from SNR."""

    public_key: str
    name: str
    count: int = 0
    avg_snr: float | None = None
    lat: float | None = None
    lon: float | None = None


class DirectoryReachNode(BaseModel):
    public_key: str
    name: str | None = None
    role: str | None = None
    lat: float | None = None
    lon: float | None = None


class DirectoryReachFirstHop(BaseModel):
    """Flood repeater that heard the advertiser. No SNR: that sample is the last hop."""

    hop_prefix: str
    public_key: str
    name: str
    lat: float
    lon: float
    count: int = 0


class DirectoryReachUnresolvedHop(BaseModel):
    prefix: str
    reason: Literal["ambiguous", "no_gps", "unmatched", "one_byte"]


class DirectoryReachResponse(BaseModel):
    node: DirectoryReachNode | None = None
    observers: list[DirectoryReachObserver] = Field(default_factory=list)
    first_hops: list[DirectoryReachFirstHop] = Field(default_factory=list)
    unresolved_first_hops: list[DirectoryReachUnresolvedHop] = Field(default_factory=list)
    directory_enabled: bool = False


class DirectoryNeighbor(BaseModel):
    """Directory neighbor-affinity entry. Distinct from firmware repeater neighbors."""

    public_key: str | None = None
    prefix: str | None = None
    name: str | None = None
    count: int = 0
    score: float | None = None
    avg_snr: float | None = None
    lat: float | None = None
    lon: float | None = None
    ambiguous: bool = False


class DirectoryNeighborsResponse(BaseModel):
    neighbors: list[DirectoryNeighbor] = Field(default_factory=list)
    directory_enabled: bool = False


class DirectoryNodeSearchHit(BaseModel):
    public_key: str
    name: str | None = None
    role: str | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: str | None = None


class DirectoryNodeSearchResponse(BaseModel):
    nodes: list[DirectoryNodeSearchHit] = Field(default_factory=list)
    directory_enabled: bool = False


class ObserverReachEntry(BaseModel):
    name: str
    public_key: str | None = None
    lat: float | None = None
    lon: float | None = None
    hops: int | None = None
    snr: float | None = None
    rssi: float | None = None
    role: str | None = None
    path: list[str] = Field(default_factory=list)
    isMLC: bool = False


class PacketObserverReachResponse(BaseModel):
    directory_enabled: bool = False
    packet_hash: str | None = None
    observer_count: int = 0
    observers: list[ObserverReachEntry] = Field(default_factory=list)
    max_hops: int | None = None
    max_distance_km: float | None = None
    origin_available: bool = False
    origin_lat: float | None = None
    origin_lon: float | None = None
    sealed: bool = False


class PacketObserverReachCountsRequest(BaseModel):
    hashes: list[str] = Field(default_factory=list, max_length=20)


class PacketObserverReachCountsResponse(BaseModel):
    directory_enabled: bool = False
    counts: dict[str, int] = Field(default_factory=dict)
    sealed: dict[str, bool] = Field(default_factory=dict)


class LocateCandidate(BaseModel):
    public_key: str
    name: str | None = None
    type: int | None = None
    last_seen: int | None = None
    role: str | None = None


class LocateIdentity(BaseModel):
    public_key: str
    name: str | None = None
    contact_type: int | None = None
    inferred: bool = False
    last_seen: int | None = None


class LocateAnchor(BaseModel):
    """One conservative RF disk. Radius is coverage, never RSSI-to-km."""

    kind: Literal["local_0hop", "first_hop", "corescope_0hop"]
    source: Literal["local", "corescope"]
    name: str
    public_key: str | None = None
    hop_prefix: str | None = None
    lat: float
    lon: float
    radius_km: float
    snr: float | None = None
    heard_count: int | None = None
    last_seen: int | None = None
    calibratable: bool = False


class LocateUnresolvedHop(BaseModel):
    prefix: str
    reason: Literal["ambiguous", "no_gps", "unmatched", "one_byte"]
    candidates: list[LocateCandidate] = Field(default_factory=list)


class LocateDeclaredGps(BaseModel):
    lat: float
    lon: float
    source: Literal["advert", "corescope"]


class LocateResponse(BaseModel):
    query: str
    identity: LocateIdentity | None = None
    source: Literal["local", "corescope", "mixte"] | None = None
    directory_enabled: bool
    default_radius_km: float
    anchors: list[LocateAnchor] = Field(default_factory=list)
    unresolved_hops: list[LocateUnresolvedHop] = Field(default_factory=list)
    declared_gps: LocateDeclaredGps | None = None
    heard_locally_0hop: bool = False
    radio_has_gps: bool = False
    empty_reason: Literal["insufficient_identity", "directory_off", "no_anchors"] | None = None


class LocateAmbiguousDetail(BaseModel):
    reason: Literal["ambiguous"] = "ambiguous"
    query: str
    candidates: list[LocateCandidate] = Field(default_factory=list)


class ContactGroup(BaseModel):
    """Local-only contact group. Not synced to the radio."""

    id: int
    name: str
    sort_order: int = 0
    created_at: int
    public_keys: list[str] = Field(default_factory=list)


class ContactGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ContactGroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = None


class ContactGroupMembersUpdate(BaseModel):
    public_keys: list[str] = Field(default_factory=list)


class BackupContact(BaseModel):
    """Contact fields included in the safer JSON backup (no computed routes)."""

    public_key: str
    name: str | None = None
    type: int = 0
    flags: int = 0
    direct_path: str | None = None
    direct_path_len: int | None = None
    direct_path_hash_mode: int | None = None
    direct_path_updated_at: int | None = None
    route_override_path: str | None = None
    route_override_len: int | None = None
    route_override_hash_mode: int | None = None
    last_advert: int | None = None
    lat: float | None = None
    lon: float | None = None
    last_seen: int | None = None
    last_contacted: int | None = None
    first_seen: int | None = None
    favorite: bool = False
    pinned: bool = False


class BackupChannel(BaseModel):
    key: str
    name: str
    is_hashtag: bool = False
    flood_scope_override: str | None = None
    path_hash_mode_override: int | None = None
    favorite: bool = False
    pinned: bool = False
    muted: bool = False
    muted_until: int | None = None
    membership: Literal["adopted", "pending"] = "adopted"


class BackupExport(BaseModel):
    """Safer JSON export of contacts, channels, settings, and groups.

    Does not include messages, raw packets, the radio private key, or VAPID keys.
    Push notification prefs are included; restore must not emit first-seen.
    """

    format: str = "meshloom-backup-v1"
    exported_at: int = 0
    contacts: list[BackupContact] = Field(default_factory=list)
    channels: list[BackupChannel] = Field(default_factory=list)
    settings: AppSettings | None = None
    groups: list[ContactGroup] = Field(default_factory=list)
    push_defaults: dict[str, Any] | None = None
    push_conversation_overrides: dict[str, bool | dict[str, bool]] | None = None
    vapid_subject: str | None = None


class BackupRestoreRequest(BackupExport):
    confirm: bool = Field(
        description=(
            "Must be true. Restore merges into the live database and does not "
            "delete existing messages, packets, or contacts/channels missing from the file."
        ),
    )


class BackupRestoreResult(BaseModel):
    contacts_upserted: int = 0
    channels_upserted: int = 0
    settings_updated: bool = False
    groups_upserted: int = 0
    private_key_warning: str = (
        "The radio private key is not stored in backups. Restore does not replace "
        "the live database file and does not delete messages or packets."
    )


class BusyChannel(BaseModel):
    channel_key: str
    channel_name: str
    message_count: int


class ContactActivityCounts(BaseModel):
    last_hour: int
    last_24_hours: int
    last_week: int


class NoiseFloorSample(BaseModel):
    timestamp: int = Field(description="Unix timestamp of the sampled reading")
    noise_floor_dbm: int = Field(description="Noise floor in dBm")


class NoiseFloorHistoryStats(BaseModel):
    sample_interval_seconds: int = Field(description="Expected spacing between samples")
    coverage_seconds: int = Field(description="How much of the last 24 hours is represented")
    latest_noise_floor_dbm: int | None = Field(
        default=None, description="Most recent sampled noise floor in dBm"
    )
    latest_timestamp: int | None = Field(
        default=None, description="Unix timestamp of the most recent sample"
    )
    samples: list[NoiseFloorSample] = Field(default_factory=list)


class PacketsPerHourBucket(BaseModel):
    timestamp: int = Field(description="Unix timestamp at the start of the hour")
    count: int = Field(description="Number of packets received in that hour")


class RegionScopeStats(BaseModel):
    """Regional flood-scope adoption over the last 24 hours.

    Two independent views, deliberately not merged — they have different
    denominators and will not agree:

    - Traffic (``total_messages``/``scoped_messages``) counts flood-routed
      channel-message packets across all channels, including ones we cannot
      decrypt. Broad coverage, but corrupt RF captures contribute false
      positives, hence ``false_positive_floor``.
    - Senders (``total_senders``/``scoped_senders``) counts distinct message
      senders, which requires decryption and so only covers channels we hold
      keys for. Narrower, but noise-free and immune to one chatty node skewing
      the result.
    """

    total_messages: int = Field(
        description="Flood-routed channel-message packets heard in the last 24h (unique payloads)"
    )
    scoped_messages: int = Field(description="Of those, how many carried a regional transport code")
    scoped_pct: float
    false_positive_floor: float = Field(
        description=(
            "Estimated false positives in scoped_messages, measured from transport-routed "
            "packets claiming a payload type the protocol does not define. A scoped_messages "
            "value at or below this is not evidence of regional adoption."
        )
    )
    total_senders: int = Field(
        description="Distinct channel-message senders in the last 24h (decryptable channels only)"
    )
    scoped_senders: int = Field(description="Of those, how many sent at least one scoped message")
    scoped_senders_pct: float


class StatisticsResponse(BaseModel):
    busiest_channels_24h: list[BusyChannel]
    contact_count: int
    repeater_count: int
    channel_count: int
    total_packets: int
    decrypted_packets: int
    undecrypted_packets: int
    total_dms: int
    total_channel_messages: int
    total_outgoing: int
    contacts_heard: ContactActivityCounts
    repeaters_heard: ContactActivityCounts
    known_channels_active: ContactActivityCounts
    path_hash_width_24h: PathHashWidthStats
    region_scope_24h: RegionScopeStats
    packets_per_hour_72h: list[PacketsPerHourBucket]
    noise_floor_24h: NoiseFloorHistoryStats


class TelemetryHistoryEntry(BaseModel):
    timestamp: int
    data: dict


RadioTransportKind = Literal["serial", "tcp", "ble"]
RadioIdentityState = Literal["identity_mismatch", "identity_unbound_legacy"]
RadioHealthState = Literal[
    "connected",
    "initializing",
    "connecting",
    "disconnected",
    "paused",
    "identity_mismatch",
    "identity_unbound_legacy",
]


class RadioTransportSnapshot(BaseModel):
    """Persisted radio transport + identity binding. PIN is internal-only."""

    transport: RadioTransportKind | None = None
    serial_port: str = ""
    serial_baudrate: int = 115200
    tcp_host: str = ""
    tcp_port: int = 5000
    ble_address: str = ""
    ble_pin: str = ""
    bound_public_key: str | None = None
    identity_state: RadioIdentityState | None = None
    previous_transport: dict[str, object] | None = None
    mismatch_previous_public_key: str | None = None
    mismatch_new_public_key: str | None = None
    mismatch_new_name: str | None = None
    env_imported: bool = False

    @property
    def configured(self) -> bool:
        return self.transport is not None

    @property
    def connection_type(self) -> RadioTransportKind:
        if self.transport in ("tcp", "ble", "serial"):
            return self.transport
        return "serial"


class RadioTransportUpdate(BaseModel):
    """PUT /api/radio/transport body. Exactly one transport family may be set."""

    transport: RadioTransportKind
    serial_port: str | None = None
    serial_baudrate: int | None = Field(default=None, ge=1200, le=921600)
    tcp_host: str | None = None
    tcp_port: int | None = Field(default=None, ge=1, le=65535)
    ble_address: str | None = None
    ble_pin: str | None = None


class RadioSerialPortInfo(BaseModel):
    path: str
    description: str = ""


class RadioBleDeviceInfo(BaseModel):
    address: str
    name: str | None = None


class RadioTransportCapabilities(BaseModel):
    """Transport choices are always offered. Reasons are live-host hints only."""

    tcp: bool = True
    serial: bool = True
    ble: bool = True
    serial_unavailable_reason: str | None = None
    ble_unavailable_reason: str | None = None


class RadioTransportResponse(BaseModel):
    """GET /api/radio/transport. Never includes the BLE PIN."""

    configured: bool
    transport: RadioTransportKind | None = None
    serial_port: str = ""
    serial_baudrate: int = 115200
    tcp_host: str = ""
    tcp_port: int = 5000
    ble_address: str = ""
    ble_pin_configured: bool = False
    bound_public_key: str | None = None
    capabilities: RadioTransportCapabilities = Field(default_factory=RadioTransportCapabilities)
    serial_ports: list[RadioSerialPortInfo] = Field(default_factory=list)


class RadioBleScanResponse(BaseModel):
    devices: list[RadioBleDeviceInfo] = Field(default_factory=list)


class RadioIdentityInfo(BaseModel):
    """Persistent identity gate details for health REST/WS. No secrets."""

    previous_public_key: str | None = None
    new_public_key: str | None = None
    new_name: str | None = None
    mesh_contacts: int = 0
    mesh_messages: int = 0
    last_activity: int | None = None


class RadioIdentityAdoptRequest(BaseModel):
    """Optional confirm payload for adopt. Empty body is allowed."""

    confirm_wipe: bool = True


class RadioIdentityActionResponse(BaseModel):
    status: str = "ok"
    radio_state: RadioHealthState
    bound_public_key: str | None = None
    connected: bool = False


class CommunityStatus(BaseModel):
    """Local Meshloom Stats community join state (OSS-owned, not a fanout row)."""

    enabled: bool
    locked: bool
    iata: str
    broker_host: str
    api_base: str
    publisher_configured: bool
    publisher_connected: bool = False
    env_seeded: bool = False


class CommunityUpdate(BaseModel):
    enabled: bool | None = None
    iata: str | None = Field(default=None, max_length=3)
    broker_host: str | None = None
    api_base: str | None = None


class CommunityIataBindRequest(BaseModel):
    iata: str = Field(pattern=r"^[A-Za-z]{3}$")
    lat: float | None = None
    lon: float | None = None


class CommunityIataBindResult(BaseModel):
    iata: str
    concordance: str
    honored_for_buckets: bool
    distance_km: float | None = None


class CommunityMeStats(BaseModel):
    unique_hashes_24h: int
    unique_hashes_7d: int
    iata: str
    concordance: str
    rank_in_iata: int | None = None


class CommunityPublicStats(BaseModel):
    observers_online: int
    iata_active: int
    unique_hashes_24h: int


class CommunityHashtag(BaseModel):
    name: str
    hash_byte: str = Field(description="Lowercase two-character SHA256(key)[0] hex")


class CommunityHashtagsResponse(BaseModel):
    hashtags: list[CommunityHashtag]


class CommunityHashtagPut(BaseModel):
    names: list[str] = Field(max_length=50)


class CommunityAirportHit(BaseModel):
    iata: str
    name: str
    city: str
    country: str
    label: str
    lat: float | None = None
    lon: float | None = None


class CommunityAirportSearchResponse(BaseModel):
    airports: list[CommunityAirportHit]


class CommunityPacketHop(BaseModel):
    token: str
    confidence: Literal["exact", "probable", "unresolved"]
    lat: float | None = None
    lon: float | None = None
    reason: str | None = None
    pubkey: str | None = None
    name: str | None = None


class CommunityPacketEar(BaseModel):
    lat: float
    lon: float
    source: Literal["advert", "iata"]


class CommunityPacketBroadcast(BaseModel):
    """Sanitized Stats live frame re-emitted on ``/api/ws`` as ``community_packet``."""

    v: int
    event_id: str
    hash8: str
    packet_hash: str | None = None
    type: str
    path: list[str]
    hop_count: int
    hops: list[CommunityPacketHop]
    ear: CommunityPacketEar | None = None
    origin: CommunityPacketHop | None = None
    route_kind: Literal["flood", "direct", "unknown"] | None = None
    snr: float | None = None
    iata: str
    t: int
    ear_id: str


class CommunityLiveStatus(BaseModel):
    session_id: str | None = None
    close_code: int | None = None
    opted_out: bool
    connected: bool
    state: Literal["connected", "reconnecting", "gate", "opted_out", "idle"] = "idle"

    @field_validator("close_code")
    @classmethod
    def _hide_non_user_close_codes(cls, value: int | None) -> int | None:
        if value in {4003, 4004, 4005}:
            return None
        return value


class CommunityLiveSubscribeRequest(BaseModel):
    session_id: str | None = None


class RadioProxyStatus(BaseModel):
    enabled: bool
    bind: str
    port: int
    max_clients: int
    listening: bool = False
    client_count: int = 0
    dropped_messages: int = 0
    dropped_logs: int = 0
    last_error: str | None = None
    instance_id: str = ""
    model: str = ""
    port_managed_by_host: bool = Field(
        default=False,
        description=(
            "The host decides the listening port — the Home Assistant add-on maps a "
            "fixed container port, and the reachable port is remapped there. Changing "
            "it here would leave the proxy listening where nothing is forwarded, so "
            "the field says so instead of accepting a value that cannot work."
        ),
    )


class RadioProxyUpdate(BaseModel):
    enabled: bool | None = None
    bind: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    max_clients: int | None = Field(default=None, ge=1, le=32)
