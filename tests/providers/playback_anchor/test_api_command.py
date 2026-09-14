"""Tests for ``playback_anchor/get``, the clock-accurate position anchor command."""

from __future__ import annotations

import logging
import time
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from music_assistant_models.enums import PlaybackState
from music_assistant_models.errors import InvalidDataError
from music_assistant_models.player_queue import PlayerQueue
from music_assistant_models.queue_item import QueueItem

from music_assistant.providers.playback_anchor.provider import (
    API_COMMAND,
    NATIVE_API_COMMAND,
    PlaybackAnchorProvider,
)

QUEUE_ID = "q1"
ELAPSED = 42.0


def _provider(
    *,
    state: PlaybackState = PlaybackState.PLAYING,
    player_plays_queue: str = QUEUE_ID,
    command_handlers: dict[str, Any] | None = None,
) -> tuple[PlaybackAnchorProvider, PlayerQueue]:
    """
    Build a bare provider with one playing queue and a stubbed rendering player.

    :param player_plays_queue: The queue the stubbed player is actually rendering, as
        get_active_queue reports it. Defaults to the queue under test.
    """
    provider = PlaybackAnchorProvider.__new__(PlaybackAnchorProvider)
    provider.logger = logging.getLogger("test.playback_anchor")
    queue = PlayerQueue(queue_id=QUEUE_ID, active=True, display_name="Q1", available=True, items=1)
    item = QueueItem(queue_id=QUEUE_ID, queue_item_id="i1", name="track", duration=210)
    queue.current_index = 0
    queue.current_item = item
    queue.state = state
    queue.elapsed_time = ELAPSED
    queue.elapsed_time_last_updated = time.time()
    mass = MagicMock()
    mass.command_handlers = {} if command_handlers is None else command_handlers
    mass.player_queues.get.side_effect = lambda queue_id: queue if queue_id == QUEUE_ID else None
    # the player itself is opaque here: what it resolves to is decided by player_anchor,
    # which is exercised against real sendspin shapes in test_anchor.py
    player = MagicMock()
    player.resolve_output_player.return_value.provider.domain = "sendspin"
    mass.players.get_player.return_value = player
    mass.players.get_active_queue.return_value = PlayerQueue(
        queue_id=player_plays_queue,
        active=True,
        display_name=player_plays_queue,
        available=True,
        items=1,
    )
    provider.mass = mass
    return provider, queue


@pytest.fixture
def anchor_from_player(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Pin what ``player_anchor`` returns, so the command is tested on its own."""

    def _set(value: float | None) -> None:
        monkeypatch.setattr(
            "music_assistant.providers.playback_anchor.provider.player_anchor",
            lambda _player: value,
        )

    return _set


def test_anchor_prefers_the_rendering_player(anchor_from_player: Any) -> None:
    """A player with a synchronised clock supplies the anchor verbatim."""
    anchor = time.time() - 12.5
    anchor_from_player(anchor)
    provider, _ = _provider()
    result = provider.playback_anchor(QUEUE_ID)
    assert result["anchor"] == anchor
    assert result["anchor_source"] == "player"
    assert result["queue_item_id"] == "i1"
    assert result["uri"] == "i1"  # QueueItem.uri falls back to the item id without a media item
    assert result["duration"] == 210


def test_anchor_falls_back_to_elapsed_time(anchor_from_player: Any) -> None:
    """Without a player anchor the reported position is expressed as the same anchor."""
    anchor_from_player(None)
    provider, queue = _provider()
    result = provider.playback_anchor(QUEUE_ID)
    assert result["anchor_source"] == "elapsed_time"
    # position derived from the anchor reproduces the position the queue reports
    derived = (result["server_time"] - result["anchor"]) * result["playback_speed"]
    assert derived == pytest.approx(queue.corrected_elapsed_time, abs=0.01)


def test_paused_queue_never_asks_the_player(anchor_from_player: Any) -> None:
    """A paused queue has no live timeline, so the anchor comes from elapsed time."""
    anchor_from_player(time.time())
    provider, _ = _provider(state=PlaybackState.PAUSED)
    result = provider.playback_anchor(QUEUE_ID)
    assert result["anchor_source"] == "elapsed_time"
    assert result["state"] == PlaybackState.PAUSED.value


def test_unknown_queue_raises() -> None:
    """An unknown queue_id is a caller error, not an empty answer."""
    provider, _ = _provider()
    with pytest.raises(InvalidDataError):
        provider.playback_anchor("nope")


def test_player_rendering_another_queue_is_ignored(anchor_from_player: Any) -> None:
    """
    A player_id playing something else must not lend its timeline to this queue.

    Its anchor would otherwise be returned alongside this queue's track metadata,
    timing one queue's audio against another queue's item.
    """
    anchor_from_player(time.time() - 99)
    provider, queue = _provider(player_plays_queue="other-queue")
    result = provider.playback_anchor(QUEUE_ID, player_id="unrelated-player")
    assert result["anchor_source"] == "elapsed_time"
    derived = (result["server_time"] - result["anchor"]) * result["playback_speed"]
    assert derived == pytest.approx(queue.corrected_elapsed_time, abs=0.01)


def test_group_member_rendering_this_queue_is_accepted(anchor_from_player: Any) -> None:
    """A member whose active queue resolves back to this one keeps its own anchor."""
    anchor = time.time() - 3.5
    anchor_from_player(anchor)
    provider, _ = _provider(player_plays_queue=QUEUE_ID)
    result = provider.playback_anchor(QUEUE_ID, player_id="group-member")
    assert result["anchor"] == anchor
    assert result["anchor_source"] == "player"


async def test_command_is_registered_and_released() -> None:
    """Loading registers the command; unloading gives the name back."""
    provider, _ = _provider()
    mass = cast("MagicMock", provider.mass)
    await provider.loaded_in_mass()
    assert mass.register_api_command.call_args.args[0] == API_COMMAND
    await provider.unload()
    mass.register_api_command.return_value.assert_called_once_with()


async def test_a_server_with_the_native_command_is_pointed_at_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    The plugin still loads next to a server that implements this natively.

    The two live in different namespaces on purpose, so neither fails to register; the
    native one knows about every player type, so say so rather than silently shadowing it.
    """
    provider, _ = _provider(command_handlers={NATIVE_API_COMMAND: object()})
    mass = cast("MagicMock", provider.mass)
    with caplog.at_level(logging.INFO):
        await provider.loaded_in_mass()
    assert NATIVE_API_COMMAND in caplog.text
    assert mass.register_api_command.call_args.args[0] == API_COMMAND
