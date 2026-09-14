"""
Playback Anchor plugin provider for Music Assistant.

Install this provider to let an external client line something up with the audio a player
is actually rendering - syllable-timed lyrics, visuals, lighting. It serves one API
command, ``playback_anchor/get``, returning the wall-clock instant at which the current
track's first sample leaves the speakers, from which the caller derives the position
using its own clock:

    position = (now - anchor) * playback_speed

For a Sendspin player that instant is known exactly, because every chunk of audio is
committed with the server-clock timestamp its clients are to render it at. For every
other player the reported elapsed time is returned in the same shape, labelled
``anchor_source: elapsed_time``, so callers need only one code path.

See README.md in this folder for the response shape and how it compares to a server that
implements the same thing natively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .provider import PlaybackAnchorProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return PlaybackAnchorProvider(mass, manifest, config)
