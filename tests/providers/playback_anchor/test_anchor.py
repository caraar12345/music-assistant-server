"""Tests for resolving a sendspin player's wall-clock playback anchor."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from music_assistant_models.enums import PlaybackState

from music_assistant.providers.playback_anchor.anchor import (
    CONF_SENDSPIN_STATIC_DELAY,
    _flow_track_offset_us,
    player_anchor,
)

# server clock and wall clock are deliberately far apart: the anchor must only ever
# carry the *difference* between two same-clock readings across into wall time.
SERVER_NOW_US = 9_000_000_000
ANCHOR_AGO_S = 7.5

# Distinguishes "argument not given" from an explicit None.
_UNSET = object()


def _player(
    *,
    domain: str = "sendspin",
    state: PlaybackState = PlaybackState.PLAYING,
    anchor_us: int | None = SERVER_NOW_US - int(ANCHOR_AGO_S * 1_000_000),
    logged_item_id: str = "i1",
    static_delay_ms: Any = 0,
    synced_to: str | None = None,
    leader: Any = None,
    current_media: Any = _UNSET,
    playback_session: Any = _UNSET,
) -> Any:
    """
    Build the minimal shape ``player_anchor`` reads.

    :param logged_item_id: Item id the flow log's last entry carries; anything other than
        the playing item means the log has not recorded this track yet.
    :param leader: Player returned for ``synced_to``, for the follower path.
    :param current_media: This player's own current media; pass None to prove a
        follower reads the leader's rather than its own.
    :param playback_session: Pass None for a sendspin player that owns no session.
    """
    return SimpleNamespace(
        state=SimpleNamespace(
            playback_state=state,
            current_media=(
                SimpleNamespace(source_id="q1", queue_item_id="i1")
                if current_media is _UNSET
                else current_media
            ),
        ),
        synced_to=synced_to,
        mass=SimpleNamespace(
            players=SimpleNamespace(get_player=lambda _id: leader),
            player_queues=SimpleNamespace(
                get_item=lambda *_args: SimpleNamespace(
                    queue_item_id="i1", streamdetails=SimpleNamespace(seek_position=0)
                ),
                queue_data_or_none=lambda _id: SimpleNamespace(
                    flow_mode_stream_log=[
                        SimpleNamespace(queue_item_id=logged_item_id, seconds_streamed=None)
                    ]
                ),
            ),
        ),
        playback_session=(
            SimpleNamespace(flow_track_anchor_us=lambda _offset: anchor_us)
            if playback_session is _UNSET
            else playback_session
        ),
        provider=SimpleNamespace(
            domain=domain,
            server_api=SimpleNamespace(clock=SimpleNamespace(now_us=lambda: SERVER_NOW_US)),
        ),
        config=SimpleNamespace(get_value=lambda *_args: static_delay_ms),
        static_delay_default_ms=0,
    )


def _leader(anchor_us: int) -> Any:
    """Build a stand-in group leader that owns the playback session and the media."""
    return SimpleNamespace(
        state=SimpleNamespace(
            playback_state=PlaybackState.PLAYING,
            current_media=SimpleNamespace(source_id="q1", queue_item_id="i1"),
        ),
        playback_session=SimpleNamespace(flow_track_anchor_us=lambda _offset: anchor_us),
        provider=SimpleNamespace(
            domain="sendspin",
            server_api=SimpleNamespace(clock=SimpleNamespace(now_us=lambda: SERVER_NOW_US)),
        ),
    )


def test_anchor_is_the_timeline_start_in_wall_time() -> None:
    """The server-clock anchor maps onto wall time via the two clocks' difference."""
    before = time.time()
    anchor = player_anchor(_player())
    after = time.time()
    assert anchor is not None
    assert before - ANCHOR_AGO_S <= anchor <= after - ANCHOR_AGO_S


def test_static_delay_pushes_the_anchor_later() -> None:
    """A device that renders 250ms late hears the track start 250ms later."""
    plain = player_anchor(_player())
    delayed = player_anchor(_player(static_delay_ms=250))
    assert plain is not None
    assert delayed is not None
    assert delayed - plain == pytest.approx(0.25, abs=0.01)


def test_unreadable_static_delay_is_treated_as_none() -> None:
    """A config value of the wrong type must not propagate into the arithmetic."""
    anchor = player_anchor(_player(static_delay_ms="soon"))
    plain = player_anchor(_player())
    assert anchor is not None
    assert plain is not None
    assert anchor == pytest.approx(plain, abs=0.01)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"domain": "airplay"},  # a player that schedules against no synchronised clock
        {"state": PlaybackState.PAUSED},
        {"anchor_us": None},  # nothing committed yet
        {"logged_item_id": "i0"},  # flow log has not recorded this track
        {"current_media": None},  # nothing loaded on the session owner
        {"playback_session": None},  # a bridge/virtual player, which owns no timeline
    ],
)
def test_no_anchor_when_the_timeline_is_unknown(kwargs: dict[str, Any]) -> None:
    """Anything short of a known timeline yields None so the caller can fall back."""
    assert player_anchor(_player(**kwargs)) is None


def test_follower_reads_the_leaders_timeline() -> None:
    """
    A synced follower anchors to the leader's session, not its own.

    Its own session and media are deliberately useless here: reading either would
    produce a wildly different answer or None, so this pins which object is used.
    """
    leader_anchor_us = SERVER_NOW_US - int(2.0 * 1_000_000)
    follower = _player(
        synced_to="leader-id",
        leader=_leader(leader_anchor_us),
        anchor_us=SERVER_NOW_US - int(600.0 * 1_000_000),  # own session: never read
        current_media=None,  # own media: absent, so reading it would bail out
    )
    before = time.time()
    anchor = player_anchor(follower)
    after = time.time()
    assert anchor is not None
    assert before - 2.0 <= anchor <= after - 2.0


def test_follower_applies_its_own_static_delay() -> None:
    """The timeline is the leader's, but the delay is the follower's own device's."""
    leader_anchor_us = SERVER_NOW_US - int(2.0 * 1_000_000)
    plain = player_anchor(_player(synced_to="l", leader=_leader(leader_anchor_us)))
    delayed = player_anchor(
        _player(synced_to="l", leader=_leader(leader_anchor_us), static_delay_ms=400)
    )
    assert plain is not None
    assert delayed is not None
    assert delayed - plain == pytest.approx(0.4, abs=0.01)


def test_follower_without_a_reachable_leader_has_no_anchor() -> None:
    """A leader that has gone yields no anchor rather than the follower's own timeline."""
    assert player_anchor(_player(synced_to="leader-id", leader=None)) is None


def test_flow_offset_sums_prior_tracks_minus_seek() -> None:
    """A later track anchors past the summed stream-time of all prior flow tracks."""
    queue_data = SimpleNamespace(
        flow_mode_stream_log=[
            SimpleNamespace(queue_item_id="t1", seconds_streamed=100.0),
            SimpleNamespace(queue_item_id="t2", seconds_streamed=200.5),
            SimpleNamespace(queue_item_id="t3", seconds_streamed=None),  # still streaming
        ]
    )
    queue_item = SimpleNamespace(
        queue_item_id="t3", streamdetails=SimpleNamespace(seek_position=10)
    )
    # a file seek moves position 0 earlier than the track's own start in the flow stream
    assert _flow_track_offset_us(queue_data, queue_item) == int((100.0 + 200.5 - 10) * 1_000_000)


def test_flow_offset_unknown_without_the_track_in_the_log() -> None:
    """A log that has not reached this track yet times nothing."""
    queue_data = SimpleNamespace(
        flow_mode_stream_log=[SimpleNamespace(queue_item_id="t1", seconds_streamed=100.0)]
    )
    queue_item = SimpleNamespace(queue_item_id="t2", streamdetails=SimpleNamespace(seek_position=0))
    assert _flow_track_offset_us(queue_data, queue_item) is None
    assert _flow_track_offset_us(None, queue_item) is None


def test_static_delay_key_still_matches_sendspin() -> None:
    """
    The copied config key must keep naming the same sendspin setting.

    Copied rather than imported (see anchor.py), so nothing but this catches a rename:
    the plugin would silently read a missing key and apply no delay at all.
    """
    sendspin_constants = pytest.importorskip(
        "music_assistant.providers.sendspin.constants",
        reason="sendspin provider deps not installed",
    )
    assert sendspin_constants.CONF_SENDSPIN_STATIC_DELAY == CONF_SENDSPIN_STATIC_DELAY
