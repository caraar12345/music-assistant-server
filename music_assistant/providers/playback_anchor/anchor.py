"""
Resolve the wall-clock anchor of what a player is currently rendering.

A player that schedules its audio against a synchronised clock knows exactly when a
given sample leaves the speakers, so it can name the one instant at which the current
track's position 0 is (or was) heard. Everything a consumer needs then follows from its
own clock - ``position = (now - anchor) * playback_speed`` - with none of the reporting
lag, one-second quantisation or extrapolation error that an elapsed time carries.

Sendspin is the only such player today: it commits every chunk of audio with the
server-clock timestamp its clients are to render it at, and records that as the playback
session's timeline anchor. This module reads that timeline from the outside, which is
what lets the whole feature ship as a plugin instead of a patched server.

Nothing here imports the sendspin package. Importing it pulls in aiosendspin, which is
only installed once that provider is set up - and this plugin still has a job to do (the
elapsed_time fallback) on a server that has no sendspin players at all. What it reads
instead is guarded attribute by attribute, so a server whose sendspin internals have moved
returns no anchor rather than raising.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.enums import PlaybackState

if TYPE_CHECKING:
    from music_assistant.models.player import Player

SENDSPIN_DOMAIN = "sendspin"
# Config key of the sendspin per-player static delay, mirroring
# music_assistant.providers.sendspin.constants.CONF_SENDSPIN_STATIC_DELAY. Copied rather
# than imported, for the reason in the module docstring; test_anchor.py asserts the two
# still agree wherever the sendspin package is installed.
CONF_SENDSPIN_STATIC_DELAY = "sendspin_static_delay"


def player_anchor(player: Player) -> float | None:
    """
    Return the wall-clock time at which this player renders the current track's start.

    The anchor already accounts for everything between the server and the speaker (the
    send-ahead buffer, this device's own static delay), so it moves only on a seek, a
    track change or a re-sync - never with wall time.

    Returns None for a player with no synchronised clock, and whenever the timeline is
    not currently known, so the caller can fall back to the reported elapsed time.

    :param player: The player actually rendering the audio.
    """
    if player.provider.domain != SENDSPIN_DOMAIN:
        return None
    return _sendspin_anchor(cast("Any", player))


def _sendspin_anchor(player: Any) -> float | None:
    """Return the anchor of a sendspin player, or None if its timeline is unknown."""
    if player.state.playback_state != PlaybackState.PLAYING:
        return None
    # Only the group leader owns the playback session and its timeline; a follower
    # renders the very same timeline, offset by its own static delay.
    session_owner = player
    if leader_id := player.synced_to:
        leader = player.mass.players.get_player(leader_id)
        if leader is None:
            return None
        session_owner = leader
    # Absent on the bridge and virtual players sendspin also exposes: they render no
    # timeline of their own, so there is nothing to anchor to.
    session = getattr(session_owner, "playback_session", None)
    if session is None:
        return None
    current_media = session_owner.state.current_media
    if current_media is None or not current_media.source_id or not current_media.queue_item_id:
        return None
    mass = player.mass
    queue_item = mass.player_queues.get_item(current_media.source_id, current_media.queue_item_id)
    if queue_item is None:
        return None
    queue_data = mass.player_queues.queue_data_or_none(current_media.source_id)
    offset_us = _flow_track_offset_us(queue_data, queue_item)
    if offset_us is None:
        return None
    anchor_us = session.flow_track_anchor_us(offset_us)
    if anchor_us is None:
        return None
    # Read both clocks together so their difference is the only thing that carries over.
    now_us = session_owner.provider.server_api.clock.now_us()
    wall_now = time.time()
    static_delay_ms = player.config.get_value(
        CONF_SENDSPIN_STATIC_DELAY, getattr(player, "static_delay_default_ms", 0)
    )
    if not isinstance(static_delay_ms, int):
        static_delay_ms = 0
    return float(wall_now - (now_us - anchor_us) / 1_000_000 + static_delay_ms / 1000)


def _flow_track_offset_us(queue_data: Any, queue_item: Any) -> int | None:
    """
    Return the current track's flow-stream start offset (minus file seek), in microseconds.

    The timeline anchor is the start of the *flow stream*, which spans the whole queue, so
    the current track's own start is that anchor plus everything streamed before it.
    Returns None when the flow log has not recorded this track yet.

    This mirrors ``SendspinPlayer._flow_track_offset_us``, reading the same public queue
    state. A track whose intro was crossfade-trimmed therefore carries the same
    up-to-one-crossfade error as it does there.
    """
    if queue_data is None or queue_item.streamdetails is None:
        return None
    log = queue_data.flow_mode_stream_log
    if not log or log[-1].queue_item_id != queue_item.queue_item_id:
        return None
    track_flow_start_s = sum(entry.seconds_streamed or 0.0 for entry in log[:-1])
    file_seek_s = float(queue_item.streamdetails.seek_position or 0)
    return int((track_flow_start_s - file_seek_s) * 1_000_000)
