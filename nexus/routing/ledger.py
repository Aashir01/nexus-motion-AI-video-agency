"""Cost accounting for a single production run."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from nexus.util.errors import BudgetExceededError


@dataclass
class LedgerEntry:
    role: str
    model_id: str
    provider: str
    cost_usd: float
    latency_ms: float
    label: str = ""
    fallback_chain: list[str] = field(default_factory=list)


@dataclass
class CostLedger:
    """Tracks spend for one job and hard-stops when the budget is gone."""

    budget_usd: float | None = None
    spent_usd: float = 0.0
    entries: list[LedgerEntry] = field(default_factory=list)

    @property
    def remaining_usd(self) -> float | None:
        if self.budget_usd is None:
            return None
        return max(0.0, self.budget_usd - self.spent_usd)

    def can_afford(self, estimate_usd: float) -> bool:
        if self.budget_usd is None:
            return True
        return self.spent_usd + estimate_usd <= self.budget_usd + 1e-9

    def check(self, estimate_usd: float, *, what: str = "call") -> None:
        if not self.can_afford(estimate_usd):
            raise BudgetExceededError(
                f"{what} would cost ${estimate_usd:.4f} but only "
                f"${self.remaining_usd:.4f} of the ${self.budget_usd:.2f} budget remains",
                detail={"estimate_usd": estimate_usd, "remaining_usd": self.remaining_usd},
            )

    def record(self, entry: LedgerEntry) -> None:
        self.entries.append(entry)
        self.spent_usd = round(self.spent_usd + entry.cost_usd, 6)

    def by_role(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for e in self.entries:
            out[e.role] += e.cost_usd
        return {k: round(v, 6) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}

    def by_model(self) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for e in self.entries:
            out[e.model_id] += e.cost_usd
        return {k: round(v, 6) for k, v in sorted(out.items(), key=lambda kv: -kv[1])}

    def summary(self) -> dict:
        return {
            "spent_usd": round(self.spent_usd, 4),
            "budget_usd": self.budget_usd,
            "remaining_usd": None if self.remaining_usd is None else round(self.remaining_usd, 4),
            "calls": len(self.entries),
            "by_role": self.by_role(),
            "by_model": self.by_model(),
            "fallbacks": sum(1 for e in self.entries if len(e.fallback_chain) > 1),
        }
