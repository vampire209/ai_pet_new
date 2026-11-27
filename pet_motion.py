"""60fps procedural micro-motion, elastic poke bounce, drag sway, and wandering."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random


@dataclass(frozen=True, slots=True)
class MotionTransformState:
    """Computed per-frame transform anchored at the pet's feet."""

    scale_x: float = 1.0
    scale_y: float = 1.0
    offset_y: float = 0.0
    rotation_deg: float = 0.0
    facing: int = 1  # +1 facing default direction, -1 horizontally mirrored

    @property
    def is_identity(self) -> bool:
        return (
            self.facing == 1
            and abs(self.scale_x - 1.0) < 1e-4
            and abs(self.scale_y - 1.0) < 1e-4
            and abs(self.offset_y) < 0.05
            and abs(self.rotation_deg) < 0.05
        )


class PetMotionEngine:
    """Combine breathing, elastic poke bounce, drag pendulum sway, and walk bob."""

    POKE_DURATION_S = 0.44
    SWAY_SETTLE_S = 0.45
    MAX_SWAY_DEG = 14.0

    def __init__(self, *, start_time: float = 0.0) -> None:
        self._origin = start_time
        self._facing = 1
        self._poke_started: float | None = None
        self._dragging = False
        self._drag_angle = 0.0
        self._sway_release_time: float | None = None
        self._sway_release_angle = 0.0
        self._walking = False
        self._walk_started = start_time
        self._breathing_enabled = True

    @property
    def facing(self) -> int:
        return self._facing

    def set_facing(self, facing: int) -> None:
        self._facing = -1 if facing < 0 else 1

    def trigger_poke(self, now: float) -> None:
        self._poke_started = now

    def begin_drag(self, now: float) -> None:
        self._dragging = True
        self._sway_release_time = None
        self._walking = False

    def update_drag(self, dx: float, dt: float) -> None:
        if not self._dragging:
            return
        safe_dt = max(0.008, min(0.1, dt))
        velocity_x = dx / safe_dt
        target_angle = max(
            -self.MAX_SWAY_DEG,
            min(self.MAX_SWAY_DEG, -velocity_x * 0.012),
        )
        # Smooth low-pass blend so rapid cursor jitter stays silky.
        alpha = min(1.0, safe_dt * 14.0)
        self._drag_angle += (target_angle - self._drag_angle) * alpha

    def end_drag(self, now: float) -> None:
        self._dragging = False
        if abs(self._drag_angle) >= 0.2:
            self._sway_release_time = now
            self._sway_release_angle = self._drag_angle
        else:
            self._drag_angle = 0.0
            self._sway_release_time = None

    def set_walking(self, active: bool, now: float) -> None:
        normalized = bool(active)
        if normalized and not self._walking:
            self._walk_started = now
        self._walking = normalized

    def needs_high_fps(self, now: float) -> bool:
        if self._dragging or self._walking:
            return True
        if self._poke_started is not None and (now - self._poke_started) < self.POKE_DURATION_S:
            return True
        if (
            self._sway_release_time is not None
            and (now - self._sway_release_time) < self.SWAY_SETTLE_S
        ):
            return True
        return False

    def sample(self, now: float) -> MotionTransformState:
        scale_x = 1.0
        scale_y = 1.0
        offset_y = 0.0
        rotation = 0.0

        # 1. Gentle organic idle breathing (~2.6s period)
        if self._breathing_enabled and not self._dragging:
            phase = (now - self._origin) * (2.0 * math.pi / 2.6)
            breath = math.sin(phase)
            scale_y *= 1.0 + 0.012 * breath
            scale_x *= 1.0 - 0.006 * breath

        # 2. Elastic squash-and-stretch Q-bounce when poked/clicked
        if self._poke_started is not None:
            elapsed = now - self._poke_started
            if 0.0 <= elapsed < self.POKE_DURATION_S:
                t = elapsed / self.POKE_DURATION_S
                decay = math.exp(-elapsed * 5.2)
                squash = math.sin(t * math.pi * 2.2) * 0.085 * decay
                scale_x *= 1.0 + squash
                scale_y *= 1.0 - squash
                # Subtle dip down then rebound slightly upward
                offset_y += math.sin(t * math.pi * 2.0) * 5.5 * decay
            else:
                self._poke_started = None

        # 3. Velocity-driven pendulum sway during drag + damped spring release
        if self._dragging:
            rotation += self._drag_angle
            # Slight stretch when lifted
            scale_y *= 1.018
            scale_x *= 0.985
        elif self._sway_release_time is not None:
            elapsed = now - self._sway_release_time
            if 0.0 <= elapsed < self.SWAY_SETTLE_S:
                decay = math.exp(-elapsed * 6.5)
                rotation += self._sway_release_angle * math.cos(elapsed * 16.0) * decay
            else:
                self._drag_angle = 0.0
                self._sway_release_time = None

        # 4. Light walking bob while strolling
        if self._walking and not self._dragging:
            walk_t = now - self._walk_started
            step = abs(math.sin(walk_t * math.pi * 2.8))
            offset_y -= step * 4.0
            rotation += math.sin(walk_t * math.pi * 2.8) * 1.8

        return MotionTransformState(
            scale_x=scale_x,
            scale_y=scale_y,
            offset_y=offset_y,
            rotation_deg=rotation,
            facing=self._facing,
        )


@dataclass(frozen=True, slots=True)
class WanderStep:
    """Result of one autonomous wandering tick."""

    x: int
    facing: int
    walking: bool
    arrived: bool = False
    trigger_variant: bool = False


class WanderController:
    """Autonomous short desktop strolls and occasional idle variant triggers."""

    SPEED_PX_PER_S = 78.0

    def __init__(
        self,
        *,
        enabled: bool = True,
        min_wait_s: float = 18.0,
        max_wait_s: float = 34.0,
        rng: random.Random | None = None,
    ) -> None:
        self._enabled = bool(enabled)
        self._min_wait_s = max(1.0, float(min_wait_s))
        self._max_wait_s = max(self._min_wait_s, float(max_wait_s))
        self._rng = rng or random.Random()
        self._walking = False
        self._target_x: float | None = None
        self._current_x: float = 0.0
        self._facing = 1
        self._next_event_at: float | None = None
        self._last_tick: float | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def walking(self) -> bool:
        return self._walking

    @property
    def facing(self) -> int:
        return self._facing

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self.cancel()

    def cancel(self, *, now: float | None = None) -> None:
        self._walking = False
        self._target_x = None
        self._last_tick = None
        if now is not None:
            self.schedule_next(now)

    def schedule_next(self, now: float) -> None:
        self._next_event_at = now + self._rng.uniform(self._min_wait_s, self._max_wait_s)

    def start_walk_to(self, current_x: int, target_x: int, now: float) -> None:
        self._current_x = float(current_x)
        self._target_x = float(target_x)
        self._facing = -1 if target_x < current_x else 1
        self._walking = abs(target_x - current_x) >= 4
        self._last_tick = now

    def tick(
        self,
        *,
        now: float,
        current_x: int,
        min_x: int,
        max_x: int,
        allowed: bool,
    ) -> WanderStep | None:
        if not self._enabled or not allowed or max_x <= min_x:
            if self._walking:
                self.cancel(now=now)
                return WanderStep(x=current_x, facing=self._facing, walking=False, arrived=True)
            return None

        if self._next_event_at is None:
            self.schedule_next(now)

        if not self._walking:
            if now < self._next_event_at:
                return None
            # 65% short stroll, 35% trigger an idle variant action in place.
            if self._rng.random() < 0.35:
                self.schedule_next(now)
                return WanderStep(
                    x=current_x,
                    facing=self._facing,
                    walking=False,
                    trigger_variant=True,
                )
            span = max_x - min_x
            step_dist = self._rng.randint(min(90, span), min(260, span))
            direction = self._rng.choice((-1, 1))
            candidate = current_x + direction * step_dist
            if candidate < min_x or candidate > max_x:
                candidate = current_x - direction * step_dist
            candidate = max(min_x, min(max_x, candidate))
            if abs(candidate - current_x) < 24:
                self.schedule_next(now)
                return None
            self.start_walk_to(current_x, candidate, now)
            return WanderStep(x=current_x, facing=self._facing, walking=True)

        # Currently walking toward target_x
        dt = 0.016 if self._last_tick is None else max(0.001, min(0.1, now - self._last_tick))
        self._last_tick = now
        target = self._target_x if self._target_x is not None else float(current_x)
        delta = target - self._current_x
        move = self.SPEED_PX_PER_S * dt
        if abs(delta) <= move:
            final_x = max(min_x, min(max_x, int(round(target))))
            self._current_x = float(final_x)
            self._walking = False
            self._target_x = None
            self.schedule_next(now)
            return WanderStep(x=final_x, facing=self._facing, walking=False, arrived=True)

        self._current_x += move if delta > 0 else -move
        clamped_x = max(min_x, min(max_x, int(round(self._current_x))))
        return WanderStep(x=clamped_x, facing=self._facing, walking=True)
