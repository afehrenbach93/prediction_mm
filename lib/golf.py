"""
Golf win-probability model — a field/winner model, not head-to-head.

Golf isn't pairwise: a tournament is ~100–150 players and the market is "will player
X win?". So instead of Elo we track a per-player skill in [0,1], seeded from recent
finishing positions (1.0 = won, 0.0 = last), and turn the field's skills into win
probabilities with a softmax (temperature sets how much the favorite is favored).

Pure, no I/O — unit-tested. The worker feeds recent event results (`core.golffeed`)
and the upcoming field; this returns P(win) per player, which the tracker records and
later scores against who actually won. Deliberately simple v1 — calibration tells us
whether the skill signal or the temperature needs work.
"""
import math
from dataclasses import dataclass, field as dc_field

BASE_SKILL = 0.5
EMA = 0.25          # weight on the latest event when updating a player's skill
DEFAULT_TEMP = 0.2  # softmax temperature over skills in [0,1]


def performance(position: int, field_size: int) -> float:
    """Normalized finish: 1.0 for the winner, 0.0 for last. Single-entry → 0.5."""
    if field_size <= 1:
        return 0.5
    p = 1.0 - (position - 1) / (field_size - 1)
    return min(1.0, max(0.0, p))


@dataclass
class SkillTable:
    ema: float = EMA
    base: float = BASE_SKILL
    skills: dict[str, float] = dc_field(default_factory=dict)

    def skill(self, player: str) -> float:
        return self.skills.get(player, self.base)

    def observe_event(self, results: list[dict]) -> None:
        """Update skills from one finished event. results: [{player, position}] with
        integer finishing positions (ties may share a position; that's fine)."""
        ranked = [r for r in results if r.get("position")]
        n = len(ranked)
        if n == 0:
            return
        for r in ranked:
            perf = performance(int(r["position"]), n)
            old = self.skill(r["player"])
            self.skills[r["player"]] = (1 - self.ema) * old + self.ema * perf

    def seed(self, events: list[list[dict]]) -> "SkillTable":
        for ev in events:
            self.observe_event(ev)
        return self

    def win_probs(self, field: list[str], temp: float = DEFAULT_TEMP) -> dict[str, float]:
        """Softmax over the field's skills → P(win) per player, summing to 1."""
        if not field:
            return {}
        weights = {p: math.exp(self.skill(p) / temp) for p in field}
        total = sum(weights.values()) or 1.0
        return {p: w / total for p, w in weights.items()}
