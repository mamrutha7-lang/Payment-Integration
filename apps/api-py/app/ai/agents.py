"""ADK agents.

Phase 4 target (docs/python-fastapi-migration.md): a Student Profile Manager and
a Resume Optimizer that read a student's record — those are student-data agents
and must run through the egress gate (local or paid model only). This first cut
ships the general/public agent, which carries no student data and so may run on
any provider (including a free key).
"""

from google.adk.agents import Agent

from .adk import build_model

REEP_GENERAL_INSTRUCTION = (
    "You are the REEP assistant for a college placement dashboard. Answer questions "
    "clearly and concisely in plain language. This is your general/public mode: you "
    "do NOT have access to any student's private records here, so if asked about a "
    "specific student's marks, attendance, or personal data, say that must be asked "
    "through the authenticated student-data assistant instead."
)


def build_general_agent() -> Agent:
    """A general-purpose assistant. No student-data tools, so no PII involved."""
    return Agent(name="reep_general", model=build_model(), instruction=REEP_GENERAL_INSTRUCTION)
