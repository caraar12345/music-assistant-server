"""Tests for ``player_queues/playback_anchor``, the clock-accurate position anchor."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest
from music_assistant_models.enums import PlaybackState
from music_assistant_models.errors import InvalidDataError
from music_assistant_models.player_queue import PlayerQueue
from music_assistant_models.queue_item import QueueItem

from music_assistant.controllers.player_queues import PlayerQueuesController
from music_assistant.controllers.player_queues.state import PlayerQueueData

QUEUE_ID = "q1"
ELAPSED = 42.0


def _controller(
    *,
    state: PlaybackState = PlaybackState.PLAYING,
    player_anchor: float | None = None,
) -> tuple[PlayerQueuesController, PlayerQueue]:
    """Build a bare controller with one playing queue and a stubbed rendering player."""
    ctrl = PlayerQueuesController.__new__(PlayerQueuesController)
    queue = PlayerQueue(queue_id=QUEUE_ID, active=True, display_name="Q1", available=True, items=1)
    item = QueueItem(queue_id=QUEUE_ID, queue_item_id="i1", name="track", duration=210)
    queue.current_index = 0
    queue.current_item = item
    queue.state = state
    queue.elapsed_time = ELAPSED
    queue.elapsed_time_last_updated = time.time()
    queue_data = PlayerQueueData(queue=queue)
    queue_data.items = [item]
    ctrl._queue_data = {QUEUE_ID: queue_data}
    ctrl.mass = MagicMock()
    player = MagicMock()
    player.resolve_output_player.return_value.audio_position_anchor.return_value = player_anchor
    ctrl.mass.players.get_player.return_value = player
    return ctrl, queue


def test_anchor_prefers_the_rendering_player() -> None:
    """A player with a synchronised clock supplies the anchor verbatim."""
    anchor = time.time() - 12.5
    ctrl, _ = _controller(player_anchor=anchor)
    result = ctrl.playback_anchor(QUEUE_ID)
    assert result["anchor"] == anchor
    assert result["anchor_source"] == "player"
    assert result["queue_item_id"] == "i1"
    assert result["uri"] == "i1"  # QueueItem.uri falls back to the item id without a media item
    assert result["duration"] == 210


def test_anchor_falls_back_to_elapsed_time() -> None:
    """Without a player anchor the reported position is expressed as the same anchor."""
    ctrl, queue = _controller(player_anchor=None)
    result = ctrl.playback_anchor(QUEUE_ID)
    assert result["anchor_source"] == "elapsed_time"
    # position derived from the anchor reproduces the position the queue reports
    derived = (result["server_time"] - result["anchor"]) * result["playback_speed"]
    assert derived == pytest.approx(queue.corrected_elapsed_time, abs=0.01)


def test_paused_queue_never_asks_the_player() -> None:
    """A paused queue has no live timeline, so the anchor comes from elapsed time."""
    ctrl, _ = _controller(state=PlaybackState.PAUSED, player_anchor=time.time())
    result = ctrl.playback_anchor(QUEUE_ID)
    assert result["anchor_source"] == "elapsed_time"
    assert result["state"] == PlaybackState.PAUSED.value


def test_unknown_queue_raises() -> None:
    """An unknown queue_id is a caller error, not an empty answer."""
    ctrl, _ = _controller()
    with pytest.raises(InvalidDataError):
        ctrl.playback_anchor("nope")
