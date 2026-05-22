# ACK Implementation — Removed Code Reference

This document captures the full ACK (mcmType=9) implementation that was removed from `vehicle.py` and `bridge.py`. `build_ack` is still present in `mcm_builder.py`.

## Why ACKs existed

ACKs were delivery confirmations: after sending a MERGE_REQUEST, SLOWDOWN_REQUEST, SLOWDOWN_GRANT, or MERGE_GRANT, the recipient echoed back an ACK carrying the `generationDeltaTime` of the original message. The sender matched the ACK against its recently-sent delta times to confirm delivery and, in one case, trigger a speed reduction.

## Action remapped on removal

| Was triggered by | Action | Now triggered by |
|---|---|---|
| ACK received matching `slowdown_sent_delta_time` | Sender reduces own speed to `own_sugg` | SLOWDOWN_GRANT received from behind (in `on_mcm`) |

All other ACK-triggered actions were delivery-confirmation prints with no protocol effect.

---

## `vehicle/vehicle.py` — removed code

### Import (removed `build_ack`)
```python
from mcm_builder import (build_merge_request, build_slowdown_request,
                         build_ack, build_merge_grant, build_slowdown_grant,
                         build_execution_status)
```

### `_send_ack()` helper (fully removed)
```python
def _send_ack(delta_time, mid):
    _demo_pause()
    ack = build_ack(
        station_id=own_station_id,
        lat=vehicle_state["lat"],
        lon=vehicle_state["lon"],
        manoeuvre_id=mid,
        acknowledged_delta_time=delta_time,
    )
    session.put("vanetza/in/mcm", json.dumps(ack).encode())
```

### Call sites removed

**MERGE_REQUEST handler** — in-conflict branch, right before propagating the slowdown chain:
```python
_send_ack(delta_time, manoeuvre_id)
```

**SLOWDOWN_GRANT handler** (manoeuvre_id >= 128) — right after the print:
```python
_send_ack(delta_time, manoeuvre_id)
```

**MERGE_GRANT handler** (manoeuvre_id < 128):
```python
_send_ack(delta_time, manoeuvre_id)
```

**SLOWDOWN_REQUEST handler** — right after reading state:
```python
_send_ack(delta_time, mid)
```

### Delta-time tracking removed from `_send_slowdown()`
```python
sent_delta = mcm["basicContainer"]["generationDeltaTime"]
# ...
protocol_state["slowdown_sent_delta_time"] = sent_delta
```

### Delta-time tracking removed from `_send_slowdown_grant()`
```python
sent_delta = grant["basicContainer"]["generationDeltaTime"]
# ...
protocol_state["slowdown_grant_sent_delta"] = sent_delta
```

### Delta-time tracking removed from `_send_merge_grant()`
```python
sent_delta = grant["basicContainer"]["generationDeltaTime"]
# ...
protocol_state["merge_grant_sent_delta"] = sent_delta
```

### Fields removed from `protocol_state`
```python
"slowdown_sent_delta_time":  None,
"slowdown_grant_sent_delta": None,
"merge_grant_sent_delta":    None,
```

### Full ACK handler block removed from `on_mcm`
```python
# ── ACK (confirmação de entrega) ───────────────────────────────────
elif mcm_type == 9:
    ack_delta = (inner["mcmContainer"]
                 .get("acknowledgmentContainer", {})
                 .get("generationDeltaTime"))
    if ack_delta is None:
        return

    with protocol_lock:
        slowdown_sent_delta   = protocol_state["slowdown_sent_delta_time"]
        slowdown_grant_delta  = protocol_state.get("slowdown_grant_sent_delta")
        merge_grant_delta     = protocol_state.get("merge_grant_sent_delta")

    def _matches(ref):
        return ref is not None and abs(ack_delta - ref) <= 1.0

    if _matches(slowdown_sent_delta):
        label = "SLOWDOWN_REQUEST"
        with protocol_lock:
            mc_advice = list(protocol_state["mc_advice"])
        own_sugg = _suggested_speed_for(own_station_id, mc_advice)
        if own_sugg is None:
            own_sugg = vehicle_state["speed_ms"] * 0.7
        vehicle_state["target_speed_ms"] = own_sugg
        ts_now = time.strftime("%H:%M:%S")
        print(f"[{ts_now}] [{vehicle_id}] velocidade reduzida para {own_sugg:.2f} m/s")
    elif _matches(slowdown_grant_delta):
        label = "SLOWDOWN_GRANT"
    elif _matches(merge_grant_delta):
        label = "MERGE_GRANT"
    else:
        label = "mensagem desconhecida"

    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{vehicle_id}] ACK de entrega recebido de stationID={sender_id} (confirma {label})")
```

---

## `bridge.py` — removed code

### Global removed
```python
_msg_by_delta = {}  # {generationDeltaTime → sender_name}  — para ACKs
```

### `on_coordinator_scenario` — line removed
```python
_msg_by_delta.clear()
```

### `_classify_mcm` — branch removed
```python
if mcm_type == 9:
    return "ACK", None
```

### `on_mcm` — delta-time tracking block removed
```python
# Normalize to integer milliseconds so vanetza/time/mcm (original float, full precision)
# and vanetza/out/mcm (decoded, sub-ms precision lost) produce the same key.
# e.g. 1741192835.4648783 and 1741192835.464 both → int key 1741192835464
# Use a list to handle simultaneous messages with the same ms key (e.g. two MERGE_GRANTs).
key = int(delta_time * 1000)
if key not in _msg_by_delta:
    _msg_by_delta[key] = sender_name
elif _msg_by_delta[key] != sender_name:
    existing = _msg_by_delta[key]
    if isinstance(existing, list):
        if sender_name not in existing:
            existing.append(sender_name)
    else:
        _msg_by_delta[key] = [existing, sender_name]
```

### `on_mcm` — ACK `to_str` resolution branch removed
```python
if to_str is None:
    if label == "ACK":
        ack_delta = (inner.get("mcmContainer", {})
                          .get("acknowledgmentContainer", {})
                          .get("generationDeltaTime"))
        if ack_delta is not None:
            val = _msg_by_delta.get(int(ack_delta * 1000), "?")
            to_str = ", ".join(val) if isinstance(val, list) else val
    elif label == "SLOWDOWN_GRANT":
        to_str = _slowdown_sent_to.get(station_id, "?")
```
→ simplified to just:
```python
if to_str is None and label == "SLOWDOWN_GRANT":
    to_str = _slowdown_sent_to.get(station_id, "?")
```

---

## `vehicle/mcm_builder.py` — `build_ack` kept

```python
def build_ack(station_id, lat, lon, manoeuvre_id, acknowledged_delta_time):
    """
    Last vehicle in chain (or propagated forward). Confirms slowdown acceptance.

    acknowledged_delta_time: generationDeltaTime of the SLOWDOWN_REQUEST being acknowledged.

    Returns a dict ready for json.dumps(). No side effects.
    """
    return {
        "basicContainer": _make_basic_container(
            station_id, lat, lon, mcm_type=9, its_role=3,
            manoeuvre_id=manoeuvre_id,
        ),
        "mcmContainer": {
            "acknowledgmentContainer": {
                "acknowledgedType": 1,
                "generationDeltaTime": acknowledged_delta_time,
            }
        },
    }
```
