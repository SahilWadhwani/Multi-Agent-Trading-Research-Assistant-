"""
Researchers Module - Multi-Agent Debate System.

Bull vs Bear researchers debate to improve decision quality.
Risk debate team provides position sizing guidance.
"""

from .debate import (
    BullResearcher,
    BearResearcher,
    DebateJudge,
    DebateEngine,
    get_debate_engine,
    run_quick_debate,
    Stance,
    DebateArgument,
    DebateResult,
)

from .risk_debate import (
    AggressiveRiskAnalyst,
    ConservativeRiskAnalyst,
    NeutralRiskAnalyst,
    RiskDebateEngine,
    get_risk_debate_engine,
    get_risk_recommendation,
    RiskProfile,
    RiskRecommendation,
    RiskConsensus,
)

__all__ = [
    # Bull vs Bear debate
    "BullResearcher",
    "BearResearcher",
    "DebateJudge",
    "DebateEngine",
    "get_debate_engine",
    "run_quick_debate",
    "Stance",
    "DebateArgument",
    "DebateResult",
    # Risk debate
    "AggressiveRiskAnalyst",
    "ConservativeRiskAnalyst",
    "NeutralRiskAnalyst",
    "RiskDebateEngine",
    "get_risk_debate_engine",
    "get_risk_recommendation",
    "RiskProfile",
    "RiskRecommendation",
    "RiskConsensus",
]
