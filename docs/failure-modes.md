# Failure modes and recovery

This page is about what happens when a turn dies part-way, and what the system
guarantees afterward. The short version: an action effect commits exactly once,
and delivery is only ever claimed as far as the transport can prove.

## Turn states

A durable turn moves through a closed set of states. An illegal transition
raises rather than writing, so a bad recovery decision cannot rewrite history.

```
received -> interpreting -> awaiting_player -> interpreting
                    |             |
                    |             +-> awaiting_gm -> interpreting
                    v
              resolving -> narrating -> completed -> delivery_pending -> delivered
```

`failed` and `cancelled` are terminal. `delivered`, `failed`, and `cancelled`
have no exits.

## Recovery decides on evidence, not age

`decide_recovery` reads the durable records, never the wall clock. Age cannot
distinguish a turn that crashed before committing from one that crashed after,
and rerunning the latter would apply a mechanical effect twice.

| Situation | Action |
|---|---|
| Terminal state | `none` |
| An action effect is already committed | `commit_pending` |
| Resolving, nothing committed | `retry_resolution` |
| Narrating | `retry_narration` |
| Completed or delivery pending | `retry_delivery` |
| Received, interpreting, or awaiting | `retry_resolution` |

A committed effect outranks the state. A turn that crashed between committing
and updating its own state still reads as `interpreting`, and classifying it
from the state alone would rerun the action.

## What commits together

| Together | Never apart |
|---|---|
| The action claim | The `action.resolved` event |
| The event | Its state changes |
| A delivery row | The act of sending it |

An action's claim, event, and state changes land in one transaction. A claim
written separately would leave two failure modes: a claim saying committed when
the effect is not, and an effect with no claim, so a recovery pass would rerun
it.

## Delivery guarantees

The guarantee is the transport's, and the system never claims more.

| Transport | Guarantee | Mechanism |
|---|---|---|
| WebSocket | Exactly once with a stable `client_seq` and server acknowledgement | The seq survives a retry, so the remote deduplicates |
| Telegram, Slack, Mattermost, IRC | Durable at-least-once with explicit ambiguity | No universal remote idempotency key |

When a transport cannot confirm a send, the delivery becomes `ambiguous`. It
is not retried automatically. An operator may resend and accept a possible
duplicate, but the system never reports a delivery it cannot prove.

Retries are bounded. Past the cap a delivery is reported as failed rather than
retried forever, so a channel that is down does not spin a worker.

## Operator actions

| Command | Effect |
|---|---|
| `/gm retry-generation` | Re-runs only the current non-authoritative phase. Refuses once an action effect is committed. |
| `/gm retry-delivery` | Resends stored output under its existing delivery id. Never reruns an action. |
| `/gm cancel` | Cancels a turn and states whether authoritative effects were already committed. |

Inspect a turn without changing it:

```python
from tabletop.orchestration.turn_job import explain_turn
explain_turn(conn, turn_id)
```

`explain_turn` is a read-only join over the turn, its effect claims, its
generations, and its deliveries. Evidence the log does not record is reported
as absent, never reconstructed from chat history.

## Duplicate ingress

A channel retry carries the same native message identity, and a partial unique
index on `(campaign_id, channel, conversation_id, external_message_id)` makes
the second claim return the turn that already owns it. Equal text from a
different message is a different turn and is processed normally.
