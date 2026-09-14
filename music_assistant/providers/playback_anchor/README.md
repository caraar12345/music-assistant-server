# Playback Anchor

Serves the wall-clock instant at which the current track's first sample leaves the
speakers, so an external client can line up with the audio that is actually being heard.

## Why

`elapsed_time` is a poor clock for anything sample-aligned. It is republished about once a
second, most consumers round it to whole seconds, and it describes audio that has not been
heard yet — anything between the server and the speaker (a send-ahead buffer, a per-device
static delay) is playing catch-up behind the number.

A Sendspin player does not have that problem: it commits every chunk of audio with the
server-clock timestamp its clients are to render it at, and the clients — clock-synced to
the server — play it at that instant. The playback session records that as its timeline
anchor. This plugin reads it, converts it to wall-clock time and folds in the rendering
device's own static delay, so the caller can do the rest locally:

```
position = (now - anchor) * playback_speed
```

The anchor moves only on a seek, a track change or a re-sync — never with wall time — so a
client fetches it once per track rather than polling a position.

## API

`playback_anchor/get` (scope: `queues:read`)

| Argument | Description |
|----------|-------------|
| `queue_id` | The queue to report on (the `player_id` of the queue's player). |
| `player_id` | Optional. The group member the anchor should apply to, so its own static delay is used rather than the group leader's. |

```json
{
  "queue_id": "abc",
  "state": "playing",
  "anchor": 1789399012.482,
  "anchor_source": "player",
  "playback_speed": 1.0,
  "server_time": 1789399042.113,
  "elapsed_time": 29.6,
  "queue_item_id": "d4f1…",
  "uri": "library://track/1731",
  "duration": 214
}
```

`anchor_source` says how good the answer is:

- `player` — the rendering player scheduled the audio against a synchronised clock. Exact
  to within that clock's sync (sub-10ms for Sendspin), buffer and static delay included.
- `elapsed_time` — no such clock was available (not a Sendspin player, nothing committed
  yet, paused, or the flow log has not recorded the current track). The reported position
  is returned expressed as the same kind of anchor, so callers need only one code path.
  Expect it to be off by up to a second.

An unknown `queue_id` is a caller error and raises rather than returning an empty answer.

## Known limits

- A track whose intro was crossfade-trimmed can be off by up to the crossfade duration:
  the flow log keeps only the elapsed-inflated seek position, not the raw one.
- The anchor is the group leader's timeline. A grouped speaker whose own latency the
  server cannot know (a Bluetooth speaker, say) still needs a manual offset in the client.

## Relationship to a native implementation

The same feature can be implemented in the server itself, as a `Player.audio_position_anchor()`
hook plus a `player_queues/playback_anchor` command — that version knows about every player
type and needs no reaching into another provider's state. This plugin exists so the feature
can be had on a stock server, without a patched core.

If both are present, the native command is the better one and this provider says so in the
log on load. The response shape is identical, so a client can fall back from one to the
other on an `Invalid Command` error.
