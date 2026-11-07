"""Conservative, bounded speech endpoints; punctuation alone never ends a caption."""
import re
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True)
class BoundaryPolicy:
    minimum_seconds: float = 8.0
    pause_seconds: float = 1.25
    incomplete_pause_seconds: float = 1.5
    short_pause_seconds: float = 2.0
    maximum_seconds: float = 20.0
    noise_factor: float | None = 1.2
    asr_window_seconds: float = 20.0

    def should_finish(self, duration: float, silence: float, text: str) -> bool:
        if duration >= self.maximum_seconds:
            return True
        if duration < self.minimum_seconds:
            return silence >= self.short_pause_seconds
        # A period added by ASR cannot turn a dangling function word into a sentence.
        words = re.findall(r"[a-z]+(?:'[a-z]+)?", text.lower())
        dangling = bool(words and words[-1] in {
            'a', 'an', 'the', 'and', 'or', 'but', 'because', 'if', 'although',
            'of', 'to', 'for', 'with', 'from', 'by', 'about', 'into', 'than',
            'that', 'which', 'whose', 'is', 'are', 'was', 'were', 'be', 'been',
            'can', 'could', 'would', 'should', 'will', 'must', 'not',
        })
        pause = self.incomplete_pause_seconds if dangling else self.pause_seconds
        return silence >= pause


class EndpointEnergy:
    """Bounded six-second low-energy reference, used for endpoints, never PCM filtering."""

    def __init__(self, policy: BoundaryPolicy):
        self.factor = policy.noise_factor
        self.recent = deque(maxlen=24)

    def quiet(self, energy: float, fallback: float) -> bool:
        self.recent.append(energy)
        threshold = fallback
        if self.factor is not None and len(self.recent) >= 8:
            ordered = sorted(self.recent)
            floor = ordered[int((len(ordered)-1)*.2)]
            threshold = min(.008, max(fallback, floor*self.factor))
        return energy < threshold
