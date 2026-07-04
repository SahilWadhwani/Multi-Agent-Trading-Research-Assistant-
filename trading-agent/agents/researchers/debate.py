"""
Bull vs Bear Researcher Debate System.

Two LLM researchers take opposing views and debate.
A judge synthesizes the debate into a final recommendation.

Inspired by TradingAgents multi-agent debate mechanism.
"""

import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from enum import Enum

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from llm.client import get_llm_client, LLMClient


class Stance(Enum):
    BULL = "bullish"
    BEAR = "bearish"
    NEUTRAL = "neutral"


@dataclass
class DebateArgument:
    """A single argument in the debate."""
    stance: Stance
    argument: str
    evidence: List[str]
    confidence: float
    counter_to: Optional[str] = None  # What this counters


@dataclass
class DebateResult:
    """Result of a bull vs bear debate."""
    symbol: str
    bull_arguments: List[DebateArgument]
    bear_arguments: List[DebateArgument]
    
    # Judge's verdict
    verdict: Stance
    verdict_confidence: float
    verdict_reasoning: str
    
    # Synthesized recommendation
    action: str  # BUY_CE, BUY_PE, HOLD, AVOID
    recommended_strike: Optional[str] = None
    key_factors: List[str] = None
    
    # Debate metadata
    rounds: int = 0
    consensus_reached: bool = False


class BullResearcher:
    """
    Bullish researcher - looks for reasons to go long.
    
    Focuses on:
    - Positive technical patterns
    - Supportive news
    - Upside catalysts
    - Strong fundamentals
    """
    
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or get_llm_client()
        self.stance = Stance.BULL
    
    def make_argument(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        counter_argument: str = None,
    ) -> DebateArgument:
        """Generate a bullish argument."""
        context = self._build_context(market_data)
        
        if counter_argument:
            prompt = f"""You are a BULLISH researcher analyzing {symbol}. 
A bearish colleague made this argument:
"{counter_argument}"

Counter this argument with BULLISH evidence. Find the holes in their reasoning.
Focus on upside potential, positive catalysts, and why bears are wrong.

Market Data:
{context}

Provide:
1. Your counter-argument (2-3 sentences, be specific)
2. Three supporting pieces of evidence (bullet points)
3. Your confidence (0.0 to 1.0)

Format:
ARGUMENT: [your argument]
EVIDENCE:
- [evidence 1]
- [evidence 2]
- [evidence 3]
CONFIDENCE: [0.X]"""
        else:
            prompt = f"""You are a BULLISH researcher analyzing {symbol}.
Make the strongest case for going LONG (buying calls).

Market Data:
{context}

Provide:
1. Your bullish thesis (2-3 sentences)
2. Three supporting pieces of evidence (bullet points)
3. Your confidence (0.0 to 1.0)

Format:
ARGUMENT: [your argument]
EVIDENCE:
- [evidence 1]
- [evidence 2]
- [evidence 3]
CONFIDENCE: [0.X]"""
        
        result = self.llm.chat(prompt=prompt, task_type="analysis")
        response = result.get("response", "")
        
        return self._parse_argument(response, counter_argument)
    
    def _build_context(self, data: Dict) -> str:
        """Build context string from market data."""
        lines = []
        
        if "spot_price" in data:
            lines.append(f"Spot Price: {data['spot_price']}")
        if "trend" in data:
            lines.append(f"Trend: {data['trend']}")
        if "pcr" in data:
            lines.append(f"Put-Call Ratio: {data['pcr']}")
        if "iv" in data:
            lines.append(f"IV Level: {data['iv']}%")
        if "support" in data:
            lines.append(f"Support: {data['support']}")
        if "resistance" in data:
            lines.append(f"Resistance: {data['resistance']}")
        if "news_sentiment" in data:
            lines.append(f"News Sentiment: {data['news_sentiment']}")
        if "technical_signal" in data:
            lines.append(f"Technical Signal: {data['technical_signal']}")
        
        return "\n".join(lines) if lines else "Limited data available"
    
    def _parse_argument(self, response: str, counter: str = None) -> DebateArgument:
        """Parse LLM response into DebateArgument."""
        argument = ""
        evidence = []
        confidence = 0.5
        
        lines = response.split("\n")
        current_section = None
        
        for line in lines:
            line = line.strip()
            if line.startswith("ARGUMENT:"):
                current_section = "argument"
                argument = line.replace("ARGUMENT:", "").strip()
            elif line.startswith("EVIDENCE:"):
                current_section = "evidence"
            elif line.startswith("CONFIDENCE:"):
                try:
                    conf_str = line.replace("CONFIDENCE:", "").strip()
                    confidence = float(conf_str.replace("%", "").strip())
                    if confidence > 1:
                        confidence /= 100
                except:
                    pass
            elif current_section == "argument" and line and not line.startswith("-"):
                argument += " " + line
            elif current_section == "evidence" and line.startswith("-"):
                evidence.append(line[1:].strip())
        
        return DebateArgument(
            stance=Stance.BULL,
            argument=argument.strip(),
            evidence=evidence,
            confidence=min(max(confidence, 0.1), 0.95),
            counter_to=counter,
        )


class BearResearcher:
    """
    Bearish researcher - looks for reasons to go short.
    
    Focuses on:
    - Negative technical patterns
    - Risk factors
    - Downside catalysts
    - Warning signs
    """
    
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or get_llm_client()
        self.stance = Stance.BEAR
    
    def make_argument(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        counter_argument: str = None,
    ) -> DebateArgument:
        """Generate a bearish argument."""
        context = self._build_context(market_data)
        
        if counter_argument:
            prompt = f"""You are a BEARISH researcher analyzing {symbol}. 
A bullish colleague made this argument:
"{counter_argument}"

Counter this argument with BEARISH evidence. Find the holes in their reasoning.
Focus on downside risks, negative catalysts, and why bulls are overconfident.

Market Data:
{context}

Provide:
1. Your counter-argument (2-3 sentences, be specific)
2. Three supporting pieces of evidence (bullet points)
3. Your confidence (0.0 to 1.0)

Format:
ARGUMENT: [your argument]
EVIDENCE:
- [evidence 1]
- [evidence 2]
- [evidence 3]
CONFIDENCE: [0.X]"""
        else:
            prompt = f"""You are a BEARISH researcher analyzing {symbol}.
Make the strongest case for going SHORT (buying puts).

Market Data:
{context}

Provide:
1. Your bearish thesis (2-3 sentences)
2. Three supporting pieces of evidence (bullet points)
3. Your confidence (0.0 to 1.0)

Format:
ARGUMENT: [your argument]
EVIDENCE:
- [evidence 1]
- [evidence 2]
- [evidence 3]
CONFIDENCE: [0.X]"""
        
        result = self.llm.chat(prompt=prompt, task_type="analysis")
        response = result.get("response", "")
        
        return self._parse_argument(response, counter_argument)
    
    def _build_context(self, data: Dict) -> str:
        """Build context string from market data."""
        lines = []
        
        if "spot_price" in data:
            lines.append(f"Spot Price: {data['spot_price']}")
        if "trend" in data:
            lines.append(f"Trend: {data['trend']}")
        if "pcr" in data:
            lines.append(f"Put-Call Ratio: {data['pcr']}")
        if "iv" in data:
            lines.append(f"IV Level: {data['iv']}%")
        if "support" in data:
            lines.append(f"Support: {data['support']}")
        if "resistance" in data:
            lines.append(f"Resistance: {data['resistance']}")
        if "news_sentiment" in data:
            lines.append(f"News Sentiment: {data['news_sentiment']}")
        if "technical_signal" in data:
            lines.append(f"Technical Signal: {data['technical_signal']}")
        
        return "\n".join(lines) if lines else "Limited data available"
    
    def _parse_argument(self, response: str, counter: str = None) -> DebateArgument:
        """Parse LLM response into DebateArgument."""
        argument = ""
        evidence = []
        confidence = 0.5
        
        lines = response.split("\n")
        current_section = None
        
        for line in lines:
            line = line.strip()
            if line.startswith("ARGUMENT:"):
                current_section = "argument"
                argument = line.replace("ARGUMENT:", "").strip()
            elif line.startswith("EVIDENCE:"):
                current_section = "evidence"
            elif line.startswith("CONFIDENCE:"):
                try:
                    conf_str = line.replace("CONFIDENCE:", "").strip()
                    confidence = float(conf_str.replace("%", "").strip())
                    if confidence > 1:
                        confidence /= 100
                except:
                    pass
            elif current_section == "argument" and line and not line.startswith("-"):
                argument += " " + line
            elif current_section == "evidence" and line.startswith("-"):
                evidence.append(line[1:].strip())
        
        return DebateArgument(
            stance=Stance.BEAR,
            argument=argument.strip(),
            evidence=evidence,
            confidence=min(max(confidence, 0.1), 0.95),
            counter_to=counter,
        )


class DebateJudge:
    """
    Judge that evaluates the bull vs bear debate.
    
    Weighs both sides and makes final recommendation.
    """
    
    def __init__(self, llm: LLMClient = None):
        self.llm = llm or get_llm_client()
    
    def judge_debate(
        self,
        symbol: str,
        bull_args: List[DebateArgument],
        bear_args: List[DebateArgument],
        market_data: Dict[str, Any],
    ) -> Tuple[Stance, float, str, str]:
        """
        Judge the debate and provide verdict.
        
        Returns:
            (stance, confidence, reasoning, recommended_action)
        """
        # Format debate for judge
        bull_summary = "\n".join([
            f"Round {i+1} (conf: {a.confidence:.0%}): {a.argument}"
            for i, a in enumerate(bull_args)
        ])
        
        bear_summary = "\n".join([
            f"Round {i+1} (conf: {a.confidence:.0%}): {a.argument}"
            for i, a in enumerate(bear_args)
        ])
        
        bull_evidence = []
        for a in bull_args:
            bull_evidence.extend(a.evidence)
        
        bear_evidence = []
        for a in bear_args:
            bear_evidence.extend(a.evidence)
        
        prompt = f"""You are a neutral JUDGE evaluating a bull vs bear debate on {symbol}.

BULLISH ARGUMENTS:
{bull_summary}

Key Bull Evidence:
{chr(10).join('- ' + e for e in bull_evidence[:5])}

BEARISH ARGUMENTS:
{bear_summary}

Key Bear Evidence:
{chr(10).join('- ' + e for e in bear_evidence[:5])}

CURRENT MARKET DATA:
- Spot: {market_data.get('spot_price', 'N/A')}
- Trend: {market_data.get('trend', 'N/A')}
- PCR: {market_data.get('pcr', 'N/A')}
- IV: {market_data.get('iv', 'N/A')}%

As an objective judge, evaluate both sides:

1. Which side has stronger evidence?
2. Which arguments are most compelling?
3. What's your verdict? (BULLISH, BEARISH, or NEUTRAL)
4. Confidence in verdict (0.0 to 1.0)
5. Recommended action (BUY_CE, BUY_PE, HOLD, or AVOID)

Format:
VERDICT: [BULLISH/BEARISH/NEUTRAL]
CONFIDENCE: [0.X]
ACTION: [BUY_CE/BUY_PE/HOLD/AVOID]
REASONING: [2-3 sentences explaining your decision]"""

        result = self.llm.chat(prompt=prompt, task_type="critical_decision")
        response = result.get("response", "")
        
        return self._parse_verdict(response)
    
    def _parse_verdict(self, response: str) -> Tuple[Stance, float, str, str]:
        """Parse judge's verdict."""
        verdict = Stance.NEUTRAL
        confidence = 0.5
        reasoning = ""
        action = "HOLD"
        
        lines = response.split("\n")
        
        for line in lines:
            line = line.strip()
            if line.startswith("VERDICT:"):
                v = line.replace("VERDICT:", "").strip().upper()
                if "BULL" in v:
                    verdict = Stance.BULL
                elif "BEAR" in v:
                    verdict = Stance.BEAR
                else:
                    verdict = Stance.NEUTRAL
            elif line.startswith("CONFIDENCE:"):
                try:
                    conf_str = line.replace("CONFIDENCE:", "").strip()
                    confidence = float(conf_str.replace("%", "").strip())
                    if confidence > 1:
                        confidence /= 100
                except:
                    pass
            elif line.startswith("ACTION:"):
                action = line.replace("ACTION:", "").strip().upper()
            elif line.startswith("REASONING:"):
                reasoning = line.replace("REASONING:", "").strip()
        
        return verdict, confidence, reasoning, action


class DebateEngine:
    """
    Orchestrates the bull vs bear debate.
    
    Runs multiple rounds of debate, then judge decides.
    """
    
    def __init__(self):
        self.llm = get_llm_client()
        self.bull = BullResearcher(self.llm)
        self.bear = BearResearcher(self.llm)
        self.judge = DebateJudge(self.llm)
    
    def run_debate(
        self,
        symbol: str,
        market_data: Dict[str, Any],
        rounds: int = 2,
    ) -> DebateResult:
        """
        Run a full bull vs bear debate.
        
        Args:
            symbol: Symbol to debate (NIFTY, BANKNIFTY, etc.)
            market_data: Current market data
            rounds: Number of debate rounds (default 2)
        
        Returns:
            DebateResult with verdict and recommendation
        """
        print(f"\n{'='*60}")
        print(f"BULL vs BEAR DEBATE: {symbol}")
        print(f"{'='*60}")
        
        bull_args = []
        bear_args = []
        
        # Initial arguments
        print("\n--- OPENING STATEMENTS ---")
        
        bull_arg = self.bull.make_argument(symbol, market_data)
        bull_args.append(bull_arg)
        print(f"\nBULL (conf: {bull_arg.confidence:.0%}): {bull_arg.argument}")
        
        bear_arg = self.bear.make_argument(symbol, market_data)
        bear_args.append(bear_arg)
        print(f"\nBEAR (conf: {bear_arg.confidence:.0%}): {bear_arg.argument}")
        
        # Debate rounds
        for round_num in range(1, rounds):
            print(f"\n--- ROUND {round_num + 1} ---")
            
            # Bull counters bear
            bull_counter = self.bull.make_argument(
                symbol, market_data,
                counter_argument=bear_args[-1].argument,
            )
            bull_args.append(bull_counter)
            print(f"\nBULL COUNTERS (conf: {bull_counter.confidence:.0%}): {bull_counter.argument}")
            
            # Bear counters bull
            bear_counter = self.bear.make_argument(
                symbol, market_data,
                counter_argument=bull_args[-1].argument,
            )
            bear_args.append(bear_counter)
            print(f"\nBEAR COUNTERS (conf: {bear_counter.confidence:.0%}): {bear_counter.argument}")
        
        # Judge decides
        print(f"\n--- JUDGE'S VERDICT ---")
        
        verdict, confidence, reasoning, action = self.judge.judge_debate(
            symbol, bull_args, bear_args, market_data
        )
        
        print(f"\nVERDICT: {verdict.value.upper()} (confidence: {confidence:.0%})")
        print(f"ACTION: {action}")
        print(f"REASONING: {reasoning}")
        
        # Check for consensus
        avg_bull_conf = sum(a.confidence for a in bull_args) / len(bull_args)
        avg_bear_conf = sum(a.confidence for a in bear_args) / len(bear_args)
        consensus = abs(avg_bull_conf - avg_bear_conf) > 0.3
        
        # Determine key factors
        key_factors = []
        for arg in bull_args + bear_args:
            if arg.confidence > 0.7:
                key_factors.extend(arg.evidence[:1])
        
        print(f"{'='*60}\n")
        
        return DebateResult(
            symbol=symbol,
            bull_arguments=bull_args,
            bear_arguments=bear_args,
            verdict=verdict,
            verdict_confidence=confidence,
            verdict_reasoning=reasoning,
            action=action,
            key_factors=key_factors[:3],
            rounds=rounds,
            consensus_reached=consensus,
        )


# Singleton
_debate_engine = None

def get_debate_engine() -> DebateEngine:
    """Get or create debate engine singleton."""
    global _debate_engine
    if _debate_engine is None:
        _debate_engine = DebateEngine()
    return _debate_engine


def run_quick_debate(
    symbol: str,
    spot_price: float,
    trend: str = "SIDEWAYS",
    pcr: float = 1.0,
    iv: float = 15,
) -> DebateResult:
    """Convenience function to run a quick debate."""
    engine = get_debate_engine()
    
    market_data = {
        "spot_price": spot_price,
        "trend": trend,
        "pcr": pcr,
        "iv": iv,
    }
    
    return engine.run_debate(symbol, market_data, rounds=2)
