# Storage-message contract (RabbitMQ → stream_stats)

Both `twitch_consumer` and `youtube_consumer` publish the **same** payload schema,
`MessageCreate`. The two definitions are field-identical; only the docstring
language and the home of the `Source` enum differ.

## Payload

```json
{
  "body":       "string  — message text",
  "source":     0,        // 0 = twitch, 1 = youtube
  "message_id": "string  — external system message id",
  "author_id":  "string  — chatter id",
  "author":     "string  — chatter display name (optional; not yet published)",
  "stream_id":  "string  — stream id",
  "channel_id": "string  — channel id"
}
```

`author` is optional: when a publisher includes it, termchat's `AmqpProvider` renders it as the
message author and falls back to `author_id` when it is absent or empty. The `*_consumer` sources
and `stream_stats_consumer` below currently publish only the six required fields.

| Field | Type | Twitch | YouTube |
|---|---|---|---|
| `body` | str | `message.text` | `message.text` |
| `source` | `Source` (IntEnum) | `Source.TWITCH` = 0 | `Source.YOUTUBE` = 1 |
| `message_id` | str | `message.id` | `message.id` |
| `author_id` | str | `message.author_id or message.author` | `message.author_id or message.author` |
| `stream_id` | str | `message.stream_id or channel_id` | `message.stream_id` (resolved live video id) |
| `channel_id` | str | broadcaster user id | `settings.youtube_channel.lstrip("@")` |

## Definitions

- **Twitch**: `MessageCreate` in `twitch_consumer/src/application/schemas.py`;
  `Source` in `twitch_consumer/src/domain/entities.py`.
- **YouTube**: `MessageCreate` and `Source` both in `youtube_consumer/src/models.py`.
- **Consumer-side peer**: `stream_stats_consumer/src/models.py` (identical field set).

## Wire / transport

- Published as JSON (`payload.model_dump(mode="json")`) to a **durable fanout
  exchange** (`settings.amqp_exchange`); fanout needs no routing key.
- `Source` serializes as an int (`0`/`1`) because `IntEnum` + `mode="json"`.
- Topology difference: the YouTube publisher only declares the exchange
  (`ensure_exchange`); the Twitch publisher also declares and binds the durable
  queue (`ensure_topology`), so a fanout with no bound queue cannot drop messages.

## Downstream

`stream_stats_consumer` → `stream_stats` API → PostgreSQL all validate against
this same 6-field contract.
