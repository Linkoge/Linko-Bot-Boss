"""Account quota metadata only; never submits inference or reads credentials."""

import asyncio
import math
import time


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def window_label(minutes, slot):
    if minutes == 300:
        return "Five-hour allowance"
    if minutes == 10080:
        return "Weekly allowance"
    if minutes is None:
        return f"{slot.title()} window (duration unavailable)"
    if minutes % 1440 == 0:
        return f"{minutes / 1440:g}-day allowance"
    if minutes % 60 == 0:
        return f"{minutes / 60:g}-hour allowance"
    return f"{minutes:g}-minute allowance"


class AccountUsage:
    refresh_seconds = 60
    stale_seconds = 180

    def __init__(self, server, clock=time.time, monotonic=time.monotonic):
        self.server, self.clock, self.monotonic = server, clock, monotonic
        self.lock = asyncio.Lock()
        self.buckets = {}
        self.last_attempt = None
        self.last_successful_refresh = None
        self.attempt_mono = None
        self.error = None
        self.generation = 0
        server.usage_listener = self.update
        server.usage_invalidate = self.invalidate

    def invalidate(self):
        self.generation += 1
        self.buckets.clear()
        self.attempt_mono = None
        self.last_attempt = None
        self.last_successful_refresh = None
        self.error = None

    def update(self, payload, replace=False):
        if replace:
            self.buckets.clear()
        if not isinstance(payload, dict):
            return
        mapping = payload.get("rateLimitsByLimitId")
        if isinstance(mapping, dict):
            candidates = mapping.items()
        else:
            snapshot = payload.get("rateLimits")
            candidates = [(snapshot.get("limitId") or "codex", snapshot)] if isinstance(snapshot, dict) else []
        for key, value in candidates:
            if isinstance(key, str) and isinstance(value, dict):
                observed = self.clock()
                self.buckets[key] = (value, observed)
                # Only a usable account window establishes successful freshness.
                if key in ("codex", "claude") and any(
                    isinstance(value.get(slot), dict)
                    and value[slot].get("windowDurationMins") in (300, 10080)
                    and number(value[slot].get("usedPercent"))
                    and value[slot]["usedPercent"] >= 0
                    for slot in ("primary", "secondary")
                ):
                    self.last_successful_refresh = observed

    async def read(self):
        async with self.lock:
            if self.attempt_mono is None or self.monotonic() - self.attempt_mono >= self.refresh_seconds:
                generation = self.generation
                self.attempt_mono, self.last_attempt = self.monotonic(), self.clock()
                try:
                    await asyncio.wait_for(self.server.start(), 15)
                    # Initializing/reconnecting emits account/updated; it invalidates
                    # old-account readings before this authenticated metadata read.
                    generation = self.generation
                    self.attempt_mono, self.last_attempt = self.monotonic(), self.clock()
                    payload = await self.server.request(
                        "account/rateLimits/read", {"excludeResetCreditDetails": True}, timeout=10
                    )
                    if generation == self.generation:
                        self.update(payload, replace=True)
                        self.error = None
                except Exception:
                    # Arbitrary transport error text can contain private diagnostics.
                    if generation == self.generation:
                        self.error = "Account usage refresh failed. Last available readings may be stale."
        return self.view()

    def view(self):
        now = self.clock()
        buckets = []
        for key, (raw, observed) in sorted(self.buckets.items()):
            windows = []
            for slot in ("primary", "secondary"):
                source = raw.get(slot)
                if not isinstance(source, dict):
                    continue
                used = source.get("usedPercent")
                used = used if number(used) and used >= 0 else None
                minutes = source.get("windowDurationMins")
                minutes = minutes if number(minutes) and minutes > 0 else None
                reset = source.get("resetsAt")
                reset = reset if number(reset) and 0 < reset <= 253402300799 else None
                expired = reset is not None and reset <= now
                windows.append({
                    "observed_at": observed, "slot": slot, "label": window_label(minutes, slot), "duration_minutes": minutes,
                    "used_percent": used, "remaining_percent": max(0, 100 - used) if used is not None else None,
                    "resets_at": reset, "reset_in_seconds": max(0, reset - now) if reset is not None else None,
                    "reset_passed": expired, "stale": bool(self.error) or now - observed >= self.stale_seconds or expired,
                })
            buckets.append({
                "id": key, "name": raw.get("limitName") or key, "plan": raw.get("planType"),
                "observed_at": observed, "windows": windows,
            })
        return {
            "scope": "account", "buckets": buckets, "last_attempt_at": self.last_attempt,
            "last_successful_refresh_at": self.last_successful_refresh, "server_time": now,
            "refresh_seconds": self.refresh_seconds, "error": self.error,
            "status": "unavailable" if not any(w["used_percent"] is not None for b in buckets for w in b["windows"]) else
            "stale" if any(w["stale"] for b in buckets for w in b["windows"]) else "available",
        }
