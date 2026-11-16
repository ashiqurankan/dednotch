"""SwiftUI-style spring animations for an immediate-mode renderer.

`Animator.get(key, target, response, damping)` returns the current animated
value for `key`, retargeting its spring if needed.  `step(dt)` advances all
springs.  Response/damping follow SwiftUI's `.spring(response:dampingFraction:)`.
"""
import math

SMOOTH = (0.5, 1.0)            # .smooth
OPEN = (0.42, 0.8)             # notch opening spring
CLOSE = (0.45, 1.0)            # notch closing spring
INTERACTIVE = (0.38, 0.8)      # interactiveSpring used for hover
QUICK = (0.25, 1.0)


class Spring:
    __slots__ = ("value", "target", "velocity", "response", "damping", "eps")

    def __init__(self, value, response, damping, eps):
        self.value = float(value)
        self.target = float(value)
        self.velocity = 0.0
        self.response = response
        self.damping = damping
        self.eps = eps

    def settled(self):
        return self.value == self.target and self.velocity == 0.0

    def step(self, dt):
        if self.settled():
            return False
        stiffness = (2.0 * math.pi / max(self.response, 1e-3)) ** 2
        friction = 4.0 * math.pi * self.damping / max(self.response, 1e-3)
        steps = max(1, int(dt / 0.004) + 1)
        h = dt / steps
        for _ in range(steps):
            accel = -stiffness * (self.value - self.target) - friction * self.velocity
            self.velocity += accel * h
            self.value += self.velocity * h
        if abs(self.value - self.target) < self.eps and abs(self.velocity) < self.eps * 8:
            self.value = self.target
            self.velocity = 0.0
            return False
        return True


class Animator:
    def __init__(self):
        self.springs = {}

    def get(self, key, target, spring=SMOOTH, eps=None, immediate=False):
        target = float(target)
        s = self.springs.get(key)
        if eps is None:
            eps = 0.05 if abs(target) > 2 else 0.0015
        if s is None or immediate:
            s = Spring(target, spring[0], spring[1], eps)
            self.springs[key] = s
            return target
        if s.target != target:
            s.target = target
        s.response, s.damping = spring
        s.eps = eps
        return s.value

    def peek(self, key, default=0.0):
        s = self.springs.get(key)
        return s.value if s else default

    def set(self, key, value):
        s = self.springs.get(key)
        if s:
            s.value = s.target = float(value)
            s.velocity = 0.0

    def step(self, dt):
        active = False
        for s in self.springs.values():
            if s.step(dt):
                active = True
        return active

    def active(self):
        return any(not s.settled() for s in self.springs.values())


def lerp(a, b, t):
    return a + (b - a) * t


def clamp(v, lo=0.0, hi=1.0):
    return lo if v < lo else hi if v > hi else v
