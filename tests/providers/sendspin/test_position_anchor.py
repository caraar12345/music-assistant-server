"""Tests for the sendspin wall-clock playback anchor."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from music_assistant_models.enums import PlaybackState

from music_assistant.providers.sendspin.player import SendspinPlayer

# server clock and wall clock are deliberately far apart: the anchor must only ever
# carry the *difference* between two same-clock readings across into wall time.
SERVER_NOW_US = 9_000_000_000
ANCHOR_AGO_S = 7.5


def _player(
    *,
    state: PlaybackState = PlaybackState.PLAYING,
    anchor_us: int | None = SERVER_NOW_US - int(ANCHOR_AGO_S * 1_000_000),
    flow_offset_us: int | None = 0,
    static_delay_ms: int = 0,
    synced_to: str | None = None,
) -> Any:
    """Build the minimal shape ``audio_position_anchor`` reads."""
    return SimpleNamespace(
        state=SimpleNamespace(
            playback_state=state,
            current_media=SimpleNamespace(source_id="q1", queue_item_id="i1"),
        ),
        synced_to=synced_to,
        mass=SimpleNamespace(
            players=SimpleNamespace(get_player=lambda _id: None),
            player_queues=SimpleNamespace(
                get_item=lambda *_args: SimpleNamespace(queue_item_id="i1"),
                queue_data_or_none=lambda _id: SimpleNamespace(flow_mode_stream_log=[]),
            ),
        ),
        _flow_track_offset_us=lambda *_args: flow_offset_us,
        playback_session=SimpleNamespace(flow_track_anchor_us=lambda _offset: anchor_us),
        provider=SimpleNamespace(
            server_api=SimpleNamespace(clock=SimpleNamespace(now_us=lambda: SERVER_NOW_US))
        ),
        config=SimpleNamespace(get_value=lambda *_args: static_delay_ms),
        static_delay_default_ms=0,
    )


def test_anchor_is_the_timeline_start_in_wall_time() -> None:
    """The server-clock anchor maps onto wall time via the two clocks' difference."""
    before = time.time()
    anchor = SendspinPlayer.audio_position_anchor(cast("SendspinPlayer", _player()))
    after = time.time()
    assert anchor is not None
    assert before - ANCHOR_AGO_S <= anchor <= after - ANCHOR_AGO_S


def test_static_delay_pushes_the_anchor_later() -> None:
    """A device that renders 250ms late hears the track start 250ms later."""
    plain = SendspinPlayer.audio_position_anchor(cast("SendspinPlayer", _player()))
    delayed = SendspinPlayer.audio_position_anchor(
        cast("SendspinPlayer", _player(static_delay_ms=250))
    )
    assert plain is not None
    assert delayed is not None
    assert delayed - plain == pytest.approx(0.25, abs=0.01)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": PlaybackState.PAUSED},
        {"anchor_us": None},  # nothing committed yet
        {"flow_offset_us": None},  # flow log has not recorded this track
    ],
)
def test_no_anchor_when_the_timeline_is_unknown(kwargs: dict[str, Any]) -> None:
    """Anything short of a known timeline yields None so the caller can fall back."""
    assert SendspinPlayer.audio_position_anchor(cast("SendspinPlayer", _player(**kwargs))) is None
