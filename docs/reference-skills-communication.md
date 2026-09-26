# Reference — Communication Skills

Defined in `src/channels.metta`. Dispatch depends on the `commchannel` configuration parameter (see [reference-configuration.md](./reference-configuration.md)).

---

## `send`

### Signature
```metta
(send "message")
```

### Purpose
Send a message to the currently active communication channel (IRC, Telegram, Slack, Mattermost, or WebSocket).

### Parameters
- `message` — the text to send. Newlines are replaced with `\n` before transmission.

### Returns
No meaningful return value. Used for its side effect.

### Examples
```metta
(send "Hello — deployment completed at 10:02.")
```

### Notes / Limits
- **Deduplication:** `send` silently drops the call if `message` is identical to the previous one (`&lastsend` state). Change the text to send a near-duplicate.
- Channel selection is set at `initChannels` time via `commchannel`.

---

## `receive`

### Signature
```metta
(receive)
```

### Purpose
Return the latest message received on the active channel since the previous call. Invoked once per loop iteration by `src/loop.metta`.

### Parameters
None.

### Returns
A string. Empty if nothing new has arrived.

### Examples
The agent does not normally call `receive` itself; the loop wraps it:

```metta
(let $msgrcv (string-safe (repr (receive))) ...)
```

### Notes / Limits
- Delegates to `wschat.getLastMessage`, `irc.getLastMessage`, `telegram.getLastMessage`, `slack.getLastMessage`, or `mattermost.getLastMessage`.
- The loop treats an unchanged message as "no new input" via the `&prevmsg` state.

---

## `websearch`

### Signature
```metta
(websearch "query")
```

### Purpose
Perform a web search through the `src/websearch.py` adapter.

### Parameters
- `query` — the search string.

### Returns
A string of search results suitable for feeding back into the prompt.

### Examples
```metta
(websearch "MeTTa AtomSpace tutorial")
```

### Notes / Limits
- Result format depends on the backend used by `websearch.search`.

## GM control commands

GM controls are not skills. The model cannot call them, so it cannot be talked
into calling them. The CLI and the channel `/gm` handler are clients of one
router, so the two surfaces cannot drift apart.

| Command | Effect |
|---|---|
| `status` | Campaign, scene, and clock summary |
| `entity <id>` | One campaign entity |
| `facts` | Facts visible to the caller's viewpoint |
| `relationships <id>` | Relationship edges for one entity |
| `npc <id>` | One NPC record |
| `recent [n]` | The last `n` authoritative events, 1 to 100 |
| `scene open/close/transition` | Scene lifecycle, each emitting its event |
| `scene enter/exit/location` | Presence and location, each emitting its event |
| `time set <label>` | Campaign clock, emitting `scene.time_changed` |
| `ruling record/promote` | Ruling lifecycle |

Recording a ruling does not confirm it. A new ruling starts proposed and
unrevealed; promoting it confirms campaign precedent without revealing it to
players, because precedence and player knowledge are separate questions.
