"""Compatibility imports for code that used the former global scheduler."""

from app.providers.google_flow.scheduler import (
    GoogleFlowScheduler,
    estimated_credit_cost,
)

GlobalScheduler = GoogleFlowScheduler

__all__ = ["GlobalScheduler", "GoogleFlowScheduler", "estimated_credit_cost"]
