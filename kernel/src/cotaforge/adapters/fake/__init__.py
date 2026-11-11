"""In-memory analytic fake adapter with fault injection."""

from cotaforge.adapters.fake.adapter import FakeAdapter
from cotaforge.adapters.fake.faults import Fault, FaultKind, FaultPlan

__all__ = ["Fault", "FaultKind", "FaultPlan", "FakeAdapter"]
