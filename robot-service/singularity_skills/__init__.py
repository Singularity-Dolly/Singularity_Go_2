"""Custom @skill methods for Singularity Go2 blueprint.

These skill methods are called by the CommandQueue executor when
DimOS skills are available. Each method maps to a CommandKind.

Skill name → CommandKind mapping:
    follow_start  → follow.start
    follow_hold   → follow.hold
    scan_start    → scan.start
    mission_stop  → mission.stop
"""

from .skills import SingularitySkillContainer, SkillProtocol, skill

__all__ = [
    "SingularitySkillContainer",
    "SkillProtocol",
    "skill",
]