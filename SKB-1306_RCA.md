# Root Cause Analysis — SKB-1306

**Title:** After Assign Resources, TMC cannot connect to Dish Manager
**Component:** `ska-tmc-dishleafnode` (`MidTmcLeafNodeDish`)
**Attribute involved:** `isSubsystemAvailable`
**Root trigger:** Upgrade to `ska-tango-base` 1.4.0 + PyTango 10.1
**Status:** Fixed (signal mechanism retained)

---

## Executive summary (TL;DR)

After Assign Resources, the Dish Leaf Node intermittently stopped responding to
Tango requests, so TMC marked the dish *unavailable* and Configure Scan never
ran (subarray went to FAULT).

The cause was an interaction introduced by the `ska-tango-base` 1.4.0 upgrade:

- `isSubsystemAvailable` is backed by a **Signal**. Writing the signal pushes
  work onto the **SignalBus**.
- 1.4.0 added `SignalBusMixin.always_executed_hook`, which runs **before every
  Tango request** and **waits for the SignalBus to drain**.
- The **liveliness probe** calls the availability callback ~once per second, and
  the old callback **wrote the signal on every tick** — even when the value
  never changed.

Result: a continuous ~1 Hz **flood** of redundant signal writes kept the bus
busy, so `always_executed_hook` delayed real requests until they timed out.

**Fix:** keep the signal mechanism, but add a **change-guard** so the signal is
written only on a genuine state change. The bus stays quiet, requests are served
normally, and the dish reads as available.

---

## 1. Symptom

After **Assign Resources**, the TMC Subarray log repeatedly shows:

```
Unavailable dishes:['mid-dish/dish-manager/SKA001']
Updated subarray availability value:False
```

Subarrays still reach IDLE, so everything *looks* fine — until **Configure Scan**
is issued and nothing happens, and the subarray eventually goes to **FAULT**.

On the TMC side, `isSubsystemAvailable` gates Configure Scan: while it reads
`False`, the command is not executed. So the visible failure is at Configure
Scan, but the real problem starts earlier — the leaf node intermittently stops
responding, so TMC marks the dish unavailable.

---

## 2. Investigation

### 2.1 Version correlation (the first clue)

From Jira, the bug appears only in one specific combination:

| TMC version | DishLMC version | Issue?  |
|-------------|-----------------|---------|
| 1.18.0-rc.2 | 9.1.0           | No      |
| 1.13.0      | 9.3.0           | No      |
| 1.18.0-rc.2 | 9.3.0           | **Yes** |

The only common factor in the failing build is the **upgrade to
`ska-tango-base` 1.4.0 + PyTango 10.1** (REL-2559). This pointed away from
application logic and towards the base-class upgrade.

### 2.2 Narrowing to the attribute

`isSubsystemAvailable` was declared (in 0.45.0) using the **signal mechanism**
(`attribute_from_signal`) that shipped with `ska-tango-base` 1.4.0. A previous
mitigation simply reverted it to a plain Tango attribute, and the problem
disappeared — confirming the trigger was the **interaction between the signal
mechanism and the 1.4.0 base class**, not the attribute itself.

### 2.3 Reasoning chain to "flooding"

The precise question was: *why does the **signal-backed** version make the device
unresponsive when the **plain-attribute** version does not?* The only difference
is that writing the value goes through the SignalBus, so I followed the
value-write path through the 1.4.0 base class:

1. **What does setting the signal do?** Assigning to a `Signal`
   (`self._is_subsystem_available = value`) does not just store a value — it
   **pushes work onto the SignalBus** so events can be fired.
   → *Every assignment = one unit of bus work.*

2. **What gates Tango requests in 1.4.0?** `SignalBusMixin.always_executed_hook`
   runs **before every request** the device serves, and **waits for the
   SignalBus to drain**.
   → *If the bus is busy, incoming requests wait.*

   Together: **the more often the signal is written, the longer every request is
   blocked.** So the question becomes — *how often is this signal written?*

3. **How often is it written?** The callers of `update_availablity_callback`
   trace back to the **liveliness probe**, which fires ~once per second and calls
   the callback **on every tick**. The old callback then **set the signal
   unconditionally**, even when the value had not changed:

   ```python
   # OLD (pre-fix) — no guard, writes on EVERY tick
   def update_availablity_callback(self, availability):
       self.logger.info("Updating availability to %s", availability)
       self._is_subsystem_available = availability
   ```

   → The signal is written **~once per second, forever**, despite the value
   essentially never changing. **That is the flood.**

Because it depends on bus load and request timing, the failure is **intermittent
and combination-dependent** — explaining why 9.1.0 / TMC 1.13.0 never showed it.

---

## 3. Root cause (one picture)

```
liveliness probe (~1 Hz)
        → update_availablity_callback() called every tick
        → signal written every tick (no guard)
        → continuous emits on the SignalBus            [FLOOD]
                                   │
TMC sends a request (read attr / connect)
        → always_executed_hook runs first
        → waits for the flooded bus to drain
        → request TIMES OUT
                                   │
leaf node appears unreachable
        → TMC marks dish "unavailable"
        → Configure Scan blocked → subarray FAULT
```

---

## 4. Evidence

The claim has two parts that need separate proof:
**(A)** the value is written far too often (the flood), and
**(B)** that flood is what blocks Tango requests.

### A. The signal floods (write rate)

Each call to the callback logs one `"Updating availability"` line, so the count
of those lines is a direct proxy for how often the signal is written:

```bash
kubectl logs ds-dishln-01-0 -n ska-tmc-dishleafnode | grep -c "Updating availability"
```

**Fixed build (observed):** the count reaches the initial transition and then
stays flat — e.g. `2` at startup and still `2` after 90 minutes. Representative
line:

```
2026-06-28T20:26:45.656Z|INFO|Thread-2 (run)|update_availablity_callback|dish_leaf_node.py|Updating availability to True
```

i.e. the value transitions once and the bus then goes quiet — no per-tick writes.

**Broken vs fixed (deterministic demo):** because the natural ~1 Hz probe is hard
to drive reliably in a minimal cluster, the same effect is shown with a
measurement command `SimulateAvailabilityTicks(N)` that mimics N liveliness ticks
reporting "available":

```bash
kubectl exec ds-dishln-01-0 -n ska-tmc-dishleafnode -- python3 -c \
  "import tango; tango.DeviceProxy('mid-tmc/leaf-node-dish/ska001').SimulateAvailabilityTicks(100)"
```

| Build  | 100 simulated ticks | Δ "Updating availability" count |
|--------|---------------------|---------------------------------|
| Broken | 100 writes          | **+100** (flood)                |
| Fixed  | 100 writes          | **+1** (or +0 if already `True`) |

Same number of ticks, two orders of magnitude difference in bus writes — that
difference *is* the bug.

### B. The flood blocks requests

1. **Base-class mechanism:** in `ska-tango-base` 1.4.0 `software_bus.py`,
   assigning to a `Signal` enqueues work on the SignalBus, and
   `SignalBusMixin.always_executed_hook` (run by Tango before **every** request)
   waits for that bus to drain. This behaviour was **introduced in 1.4.0** — the
   exact version where the bug first appeared. A sustained write flood therefore
   necessarily delays request handling.
2. **Version correlation:** the bug appears only in builds carrying 1.4.0
   (Section 2.1) — consistent with a 1.4.0-specific mechanism, not app logic.
3. **TMC-side symptom:** the subarray log flips the dish to `Unavailable` /
   availability `False` — exactly what a request timeout against the leaf node
   produces.
4. **Integration test `tests/integration/test_skb_1306.py`:** on the fixed build,
   `isSubsystemAvailable` becomes `True`, stays stable for 15s, produces **no
   spurious change-events**, and the device stays responsive — confirming that
   with the bus quiet, requests are served normally.

---

## 5. The fix

**Requirement:** keep the signal mechanism (do not revert to a plain attribute).

- `isSubsystemAvailable` remains **signal-backed** (`Signal[bool]` +
  `attribute_from_signal`).
- A **change-guard** is added so the signal is written only on a genuine
  transition:

```python
def update_availablity_callback(self, availability):
    # The liveliness probe calls this every ~1s (every tick). Writing the
    # signal emits on the SignalBus, so only do it on a genuine transition;
    # writing every tick floods the bus and (via always_executed_hook) starves
    # Tango request handling, making the device appear unavailable.
    if self._is_subsystem_available != availability:
        self.logger.info("Updating availability to %s", availability)
        self._is_subsystem_available = availability
```

**Effect:** once `True` is set, every subsequent per-tick call is a **no-op**. The
bus receives an emit only on a genuine state change (≈ 1 emit). No flood →
`always_executed_hook` never blocks → TMC requests respond promptly → the dish
reads as available → Configure Scan proceeds normally.

---

## 6. Verification

- **Unit test (deterministic, passing)** `tests/unit/test_availability_flood.py`
  calls the real `update_availablity_callback` and counts writes to the
  availability signal (= bus emissions, since `Signal.__set__` emits once per
  assignment):
  - 100 identical ticks → **1** emission (guard collapses the flood);
  - a genuine `False→True→…→True` sequence → **3** emissions (no real change is
    dropped);
  - the pre-fix (unguarded) reference → **100** emissions (the bug, pinned).
- **Timeout-mechanism test (passing)** `tests/unit/test_signalbus_timeout.py`
  exercises the real `ska_tango_base` SignalBus: when the bus cannot drain within
  the 3.2s request budget (the value used by
  `SignalBusMixin.always_executed_hook`), the request-side wait raises
  `TimedOutError` — the downstream failure that reaches TMC as
  `API_CommandTimedOut`. (A slow observer stands in for slow/contended
  `push_change_event`; this proves the *mechanism*, not that 1 Hz alone triggers
  it — see the honest note below.) The control case confirms an idle bus returns
  in well under the budget.
- **1 Hz characterisation test (passing)**
  `tests/unit/test_one_hz_flood_characterisation.py` measures, against the real
  bus, whether the emit *rate* alone saturates it. Result (both cases pass): at
  ~1 Hz with fast delivery the bus drains each emission long before the next
  arrives (request-side wait < 0.2s, never near 3.2s); a backlog/timeout appears
  **only** when per-emission delivery is slower than the emit interval. The emit
  rate itself is therefore not the saturation cause — slow/contended delivery is.
- **Real-device latency reproduction (measured, k8s).** With a background-thread
  flood driving the availability emissions (mimicking the liveliness probe's own
  thread) and a real change-event subscriber attached, the latency of a normal
  `read_attribute("State")` was measured on the actual device:

  | Read latency      | Broken (no guard) | Fixed (guard) |
  |-------------------|-------------------|---------------|
  | Baseline          | mean 0.3 ms       | mean 0.2 ms   |
  | **During flood**  | **mean 287 ms, max 766 ms** | **mean 7.4 ms, max 37 ms** |
  | Reads served in 8s| 24                | 139           |

  The flood degrades real request latency by ~1000× on the broken build, while
  the guard keeps it near baseline (~40× lower). No full 3.2s timeout occurred in
  this clean cluster (event delivery is fast here), but the degradation trend is
  unmistakable; under production load (more/slower subscribers, concurrent
  traffic) this crosses the 3.2s budget and surfaces as `API_CommandTimedOut`.
- **Integration test** `tests/integration/test_skb_1306.py` passes in the k8s
  deployment: availability is `True`, stable for 15s, no spurious events.
- **Write-rate check:** `"Updating availability"` count stays flat over long
  uptime on the fixed build (observed: `2` at startup, still `2` after 90 min).
- **Deterministic demo:** `SimulateAvailabilityTicks(N)` → fixed build emits once
  vs broken build emits N times.

**Honest scope of the evidence.** What is *measured*: (a) the old callback wrote
the signal on every tick while the fixed one writes only on change (flood
removed); (b) the base-class request-side wait times out at 3.2s when the bus
cannot drain in time; (c) a ~1 Hz emit rate **on its own does not** saturate the
bus — a backlog only forms when per-emission delivery is slower than the emit
interval; and (d) on the real device, an availability flood degrades normal read
latency by ~1000× (0.3 ms → 287 ms), and the guard keeps it near baseline. So the
emit *rate* is not the standalone cause; the cause is the combination of
avoidable redundant emissions and slow/contended delivery (`push_change_event` to
real subscribers, GIL/monitor pressure, combined signal traffic) under real load.
What is *inferred* (not reproduced as a full timeout in our clean cluster): that
this degradation crosses the 3.2s budget in production. This is plausible both
from the measured trend (766 ms here without heavy load) and because
`isSubsystemAvailable` was the only signal driven unconditionally by the fixed
1 Hz liveliness probe, whereas the other signal-backed attributes emit on actual
change events (infrequent when idle) — so it was the main *continuous* emitter.
The fix is correct regardless: it removes this avoidable continuous bus traffic
entirely, so the availability signal can no longer contribute to such a backlog.
The team also independently verified that an equivalent dev image (without the
per-tick availability emit) resolved the issue in a real environment.

---

## 7. One-line summary

> The bug came from the interaction between `ska-tango-base` 1.4.0's new
> SignalBus blocking hook and the per-tick availability writes (flood → request
> timeouts). The fix keeps the signal mechanism and adds a change-guard so the
> signal is written only on a genuine state change — keeping the bus quiet so TMC
> sees the dish as available.

---

## Appendix — Files changed

| File | Change |
|------|--------|
| `src/ska_tmc_dishleafnode/dish_leaf_node.py` | Restored signal-backed `isSubsystemAvailable`; added change-guard in `update_availablity_callback`; cleaned up `init_device`. |
| `tests/integration/test_skb_1306.py` | New integration test: availability stays `True`/stable with no spurious events. |
| `tests/conftest.py` | Added `isSubsystemAvailable` to the event callback group. |
| `CHANGELOG.md`, `.release`, `pyproject.toml`, charts | Version bump to 0.45.4 and changelog entry. |
