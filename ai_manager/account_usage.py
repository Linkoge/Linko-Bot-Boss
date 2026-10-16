"""Account quota metadata only; never submits inference. The app server never reads credentials; only the
Claude host reads its stored login, solely for a read-only usage request."""

import asyncio
import math
import time


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def timestamp(value):
    return value if number(value) and 0 < value <= 253402300799 else None


def reset_credits(raw):
    raw = raw if isinstance(raw, dict) else {}
    count = raw.get("availableCount")
    count = count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None
    details = raw.get("credits")
    credits = None if not isinstance(details, list) else [
        {"expires_at": timestamp(c.get("expiresAt")),
         "expiration_known": "expiresAt" in c and (c["expiresAt"] is None or timestamp(c["expiresAt"]) is not None)}
        for c in details if isinstance(c, dict) and c.get("status") == "available"
    ]
    return {"available_count": count, "credits": credits}


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

    def __init__(self, server, clock=time.time, monotonic=time.monotonic, provider=None):
        self.server, self.clock, self.monotonic = server, clock, monotonic
        self.provider = provider
        self.lock = asyncio.Lock()
        self.buckets = {}
        self.resets = reset_credits(None)
        self.observed_at = None
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
        self.resets = reset_credits(None)
        self.observed_at = None
        self.attempt_mono = None
        self.last_attempt = None
        self.last_successful_refresh = None
        self.error = None

    def update(self, payload, replace=False):
        if replace:
            self.buckets.clear()
            self.resets = reset_credits(None)
        if not isinstance(payload, dict):
            return
        self.observed_at = timestamp(payload.get("observedAt")) or self.clock()
        self.resets = reset_credits(payload.get("rateLimitResetCredits"))
        if self.resets["available_count"] is not None:
            self.last_successful_refresh = self.observed_at
        mapping = payload.get("rateLimitsByLimitId")
        if isinstance(mapping, dict):
            candidates = mapping.items()
        else:
            snapshot = payload.get("rateLimits")
            candidates = [(snapshot.get("limitId") or "codex", snapshot)] if isinstance(snapshot, dict) else []
        for key, value in candidates:
            if isinstance(key, str) and isinstance(value, dict):
                observed = self.observed_at
                self.buckets[key] = (value, observed)
                credits = self.credit_view(value.get("credits"))
                if key in ("codex", "claude") and any(v is not None for v in credits.values()):
                    self.last_successful_refresh = observed
                # Only usable account metadata establishes successful freshness.
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
                        "account/rateLimits/read", {}, timeout=10
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
                reset = timestamp(source.get("resetsAt"))
                expired = reset is not None and reset <= now
                window_observed = timestamp(source.get("observedAt")) or observed
                windows.append({
                    "observed_at": window_observed, "slot": slot, "label": window_label(minutes, slot), "duration_minutes": minutes,
                    "used_percent": used, "remaining_percent": max(0, 100 - used) if used is not None else None,
                    "resets_at": reset, "reset_in_seconds": max(0, reset - now) if reset is not None else None,
                    "reset_passed": expired, "stale": bool(self.error) or now - window_observed >= self.stale_seconds or expired,
                })
            buckets.append({
                "id": key, "name": raw.get("limitName") or key, "plan": raw.get("planType"),
                "observed_at": observed, "windows": windows,
                "credits": self.credit_view(raw.get("credits")),
            })
        metadata_known = self.resets["available_count"] is not None or any(
            b["credits"]["balance"] is not None or b["credits"]["has_credits"] is not None
            or b["credits"]["unlimited"] is not None for b in buckets
        )
        metadata_stale = (bool(self.error) or self.observed_at is None
                          or now - self.observed_at >= self.stale_seconds
                          or any(c["expires_at"] is not None and c["expires_at"] <= now
                                 for c in self.resets["credits"] or []))
        return {
            "scope": "account", "provider": self.provider or (self.server.active() if callable(getattr(self.server, "active", None)) else
            ("claude" if any(b["id"] == "claude" for b in buckets) else "codex")),
            "reset_credits": self.resets, "observed_at": self.observed_at,
            "metadata_stale": metadata_stale,
            "buckets": buckets, "last_attempt_at": self.last_attempt,
            "last_successful_refresh_at": self.last_successful_refresh, "server_time": now,
            "refresh_seconds": self.refresh_seconds, "error": self.error,
            "status": "unavailable" if not metadata_known and not any(
                w["used_percent"] is not None for b in buckets for w in b["windows"]) else
            "stale" if any(w["stale"] for b in buckets for w in b["windows"]) or metadata_stale else "available",
        }

    @staticmethod
    def credit_view(raw):
        raw = raw if isinstance(raw, dict) else {}
        return {"balance": raw.get("balance") if isinstance(raw.get("balance"), str) else None,
                "has_credits": raw.get("hasCredits") if isinstance(raw.get("hasCredits"), bool) else None,
                "unlimited": raw.get("unlimited") if isinstance(raw.get("unlimited"), bool) else None}
