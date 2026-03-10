# Twin Deactivation Hysteresis Policy

## Overview
This document specifies the hysteresis behavior for session twin mode deactivation in the OIG Proxy. The policy prevents rapid flapping between active and inactive twin states by introducing a time-based delay with anti-flap protection.

## Reference Implementation
- **Function**: `_maybe_deactivate_session_twin_mode_if_idle` in `addon/oig-proxy/proxy.py`
- **Current behavior**: Immediate deactivation when idle conditions are met
- **Target behavior**: Delayed deactivation with hysteresis

## Policy Specification

### 1. Deactivation Delay (300 seconds)
- **Requirement**: Twin mode MUST remain active for at least 300 seconds (5 minutes) of stable cloud connectivity before deactivation
- **Rationale**: Prevents unnecessary twin mode cycling during brief idle periods
- **State**: `twin_deactivate_timer` - tracks elapsed time since idle conditions first met

### 2. Anti-Flap Guard
- **Requirement**: Any fail event during the hysteresis window MUST reset the deactivation timer
- **Fail events include**:
  - Cloud connection failure
  - Twin request failure
  - Twin communication error
  - Queue buildup detected
- **Rationale**: Ensures stable cloud connectivity before committing to deactivation
- **State**: `twin_deactivate_timer` - reset to 0 on any fail event

### 3. Timer Start Condition
- **Requirement**: The deactivation timer starts ONLY after:
  1. Twin mode is currently active (`_twin_mode_active == True`)
  2. Idle conditions are met (no inflight requests, empty queue, no routing via twin)
  3. First successful cloud response is received AFTER initial activation
- **Rationale**: Prevents timer from starting during initial activation phase
- **State**: `twin_activation_timestamp` - records time of first successful cloud response

## State Transitions

```
IDLE_DETECTED ──► TIMER_STARTING (if first_success_received)
TIMER_STARTING ──► COUNTDOWN (300s window begins)
COUNTDOWN ──► ACTIVE (if fail event occurs, timer resets)
COUNTDOWN ──► DEACTIVATED (if 300s elapses with no fail events)
```

## Implementation Requirements

### New State Variables
```python
# In Proxy class
_twin_deactivate_timer: float | None = None  # Seconds remaining until deactivation
_twin_first_success_received: bool = False   # Flag for first success after activation
```

### Modified Function: `_maybe_deactivate_session_twin_mode_if_idle`
1. Check if idle conditions are met (existing logic)
2. If idle AND timer not started AND first_success_received:
   - Start timer (`_twin_deactivate_timer = 300.0`)
3. If idle AND timer running:
   - Decrement timer by elapsed time
   - If timer <= 0: deactivate twin mode
4. On any fail event:
   - Reset timer to None
   - Reset first_success_received to False

### Time Simulator Support
- The implementation MUST support time simulation for testing
- Use `self._twin_deactivate_timer` as decrementable value
- Test scenarios MUST verify:
  - Stable 300s window leads to deactivation
  - Fail event at 240s resets timer (no deactivation)

## Test Scenarios

### Scenario 1: Stable 300s Deactivation (task-4-hysteresis-pass.txt)
```
t=0s:    Twin activated, first_success_received=True
t=60s:   Idle detected, timer starts (300s remaining)
t=120s:  Idle continues, timer=240s
t=180s:  Idle continues, timer=180s
t=240s:  Idle continues, timer=120s
t=300s:  Idle continues, timer=0s → DEACTIVATED
```

### Scenario 2: Flap at 240s (task-4-hysteresis-error.txt)
```
t=0s:    Twin activated, first_success_received=True
t=60s:   Idle detected, timer starts (300s remaining)
t=120s:  Idle continues, timer=240s
t=180s:  Idle continues, timer=180s
t=240s:  FAIL EVENT → timer reset, first_success_received=False
t=300s:  No idle (twin active due to fail recovery)
t=360s:  Idle detected, timer starts (300s remaining)
t=660s:  Idle continues, timer=0s → DEACTIVATED
```

## Verification
- Policy is testable with time simulator
- Evidence files demonstrate expected behavior
- Implementation blocks Task 13 (actual implementation)