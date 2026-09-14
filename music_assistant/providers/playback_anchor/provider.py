"""Playback Anchor plugin provider implementation."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.auth import Scope
from music_assistant_models.enums import PlaybackState
from music_assistant_models.errors import InvalidDataError

from music_assistant.controllers.player_queues.helpers import get_current_playback_speed
from music_assistant.models.plugin import PluginProvider

from .anchor import player_anchor

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

# The command this plugin serves. Deliberately in its own namespace: a future server that
# grows a native equivalent under player_queues/ can then coexist with this plugin instead
# of failing to register one of the two.
API_COMMAND = "playback_anchor/get"
# The name the same feature carries when the server implements it itself. Only used to
# point the user at the better of the two if their server already has it.
NATIVE_API_COMMAND = "player_queues/playback_anchor"


class PlaybackAnchorProvider(PluginProvider):
    """Serves the wall-clock anchor of what a player is rendering, over the API."""

    _unregister_api: Callable[[], None] | None = None

    async def loaded_in_mass(self) -> None:
        """Register the API command once the provider is fully loaded."""
        await super().loaded_in_mass()
        if NATIVE_API_COMMAND in self.mass.command_handlers:
            self.logger.info(
                "This server already serves %s natively, which knows about every player "
                "type rather than only the ones this plugin can reach; prefer it.",
                NATIVE_API_COMMAND,
            )
        self._unregister_api = self.mass.register_api_command(
            API_COMMAND,
            # the handler is synchronous (it only reads state that is already in memory),
            # which the API layer supports but its type hint does not express
            cast("Callable[..., Coroutine[Any, Any, Any]]", self.playback_anchor),
            required_scope=Scope.QUEUES_READ,
        )

    async def unload(self, is_removed: bool = False) -> None:
        """Unregister the API command."""
        if self._unregister_api is not None:
            self._unregister_api()
            self._unregister_api = None

    def playback_anchor(self, queue_id: str, player_id: str | None = None) -> dict[str, Any]:
        """
        Return the wall-clock anchor of the current item, for clock-accurate position.

        A caller that knows the anchor derives the position from its own clock -
        ``position = (now - anchor) * playback_speed`` - instead of re-reading a reported
        elapsed time, which is only republished about once a second and is rounded to
        whole seconds by most consumers. That is the difference between lyrics landing
        on the syllable and landing somewhere in the line.

        ``anchor_source`` says how good the answer is. ``player`` means the rendering
        player scheduled the audio against a synchronised clock and the anchor is exact
        to within that clock's sync (sub-10ms for Sendspin), with its send-ahead buffer
        and per-device static delay already accounted for. ``elapsed_time`` means it was
        derived from the reported position instead and is only as good as that - use it,
        but expect it to be off by up to a second.

        :param queue_id: The queue to report on (the player_id of the queue's player).
        :param player_id: Optionally, the group member the anchor should apply to, so its
            own static delay is taken into account rather than the group leader's.
        """
        now = time.time()
        queue = self.mass.player_queues.get(queue_id)
        if queue is None:
            raise InvalidDataError(f"Queue {queue_id} not found")
        speed = get_current_playback_speed(queue)
        anchor: float | None = None
        if queue.state == PlaybackState.PLAYING and (
            player := self.mass.players.get_player(player_id or queue_id)
        ):
            # Only a player actually rendering this queue carries its timeline. Without
            # this check an unrelated player_id would pair that player's audio with this
            # queue's metadata - a confidently wrong answer rather than a missing one.
            # get_active_queue follows sync leaders, groups and protocol parents, so a
            # member of the group playing this queue still passes.
            active_queue = self.mass.players.get_active_queue(player)
            if active_queue is not None and active_queue.queue_id == queue_id:
                anchor = player_anchor(player.resolve_output_player())
        anchor_source = "player"
        if anchor is None:
            # No synchronised clock to ask (or nothing playing): fall back to the reported
            # position, expressed as the same kind of anchor so callers need only one path.
            anchor_source = "elapsed_time"
            anchor = now - (queue.corrected_elapsed_time / speed if speed else 0.0)
        current_item = queue.current_item
        return {
            "queue_id": queue.queue_id,
            "state": queue.state.value,
            "anchor": anchor,
            "anchor_source": anchor_source,
            "playback_speed": speed,
            "server_time": now,
            "elapsed_time": queue.corrected_elapsed_time,
            "queue_item_id": current_item.queue_item_id if current_item else None,
            "uri": current_item.uri if current_item else None,
            "duration": current_item.duration if current_item else None,
        }
