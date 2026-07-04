"""
LLM Client for QUANT-1 Trading Agent

Supports multiple backends for LLM inference:
1. Proxima (RECOMMENDED) - Uses your ChatGPT Plus & Gemini Pro subscriptions!
2. Ollama (FALLBACK) - Local models when Proxima unavailable
3. OpenAI-compatible APIs (LM Studio, vLLM, etc.)

Backend Priority:
-----------------
1. Proxima (GPT-5.5 + Gemini) → Most powerful, uses your subscriptions
2. Ollama (local) → Fast fallback, always available offline
3. Rule-based → Last resort if no LLM available

Setup Instructions:
-------------------
OPTION A - Proxima (Recommended):
1. Clone: git clone https://github.com/Zen4-bit/Proxima.git
2. Install: cd Proxima && npm install && npm start
3. Login to ChatGPT & Gemini in Proxima window
4. Enable REST API in Proxima Settings

OPTION B - Ollama (Fallback):
1. Install Ollama: https://ollama.ai
2. Pull a model: ollama pull qwen2.5:7b-instruct
3. Run: ollama serve
"""

import os
import json
import requests
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

from llm.schemas import (
    parse_json_response,
    normalize_news_from_json,
    NEWS_ANALYSIS_JSON_SCHEMA,
    FINAL_DECISION_JSON_SCHEMA,
)


class LLMBackend(Enum):
    PROXIMA = "proxima"  # ChatGPT Plus & Gemini via Proxima
    OLLAMA = "ollama"    # Local models
    OPENAI_COMPATIBLE = "openai_compatible"
    NONE = "none"  # Fallback to rule-based


@dataclass
class LLMResponse:
    """Structured LLM response."""
    content: str
    model: str
    backend: LLMBackend
    tokens_used: Optional[int] = None
    raw_response: Optional[Dict] = None


class LLMClient:
    """
    Unified LLM client supporting Proxima (ChatGPT/Gemini) and Ollama.
    
    Backend Priority:
    1. Proxima (localhost:3210) - GPT-5.5 & Gemini Pro via your subscriptions
    2. Ollama (localhost:11434) - Local models as fallback
    3. OpenAI-compatible endpoint if configured
    4. None - Falls back to rule-based analysis
    """
    
    PROXIMA_URL = "http://localhost:3210"
    OLLAMA_URL = "http://localhost:11434"
    
    # Default models for different backends
    DEFAULT_MODELS = {
        "proxima_primary": "chatgpt",      # GPT-5.5 via ChatGPT Plus
        "proxima_secondary": "gemini",      # Gemini Pro
        "ollama": "qwen2.5:7b-instruct",   # Local fallback
        "reasoning": "chatgpt",             # Best for complex analysis
        "fast": "gemini",                   # Quick responses
    }
    
    # Proxima model mapping
    PROXIMA_MODELS = {
        "chatgpt": "chatgpt",    # Uses your ChatGPT Plus (GPT-5.5)
        "gpt": "chatgpt",
        "gpt5": "chatgpt",
        "gemini": "gemini",      # Uses your Gemini Pro
        "perplexity": "perplexity",  # If you have it
    }
    
    # Smart routing: Which model for which task
    TASK_MODEL_ROUTING = {
        # GPT-5.5 for complex reasoning tasks
        "news_analysis": "chatgpt",        # Better at understanding context
        "sentiment_aggregation": "chatgpt", # Better at synthesizing signals
        "trade_decision": "chatgpt",       # Critical - needs best reasoning
        "market_reasoning": "chatgpt",     # Complex analysis
        "risk_assessment": "chatgpt",      # Important decisions
        
        # Gemini for speed-critical tasks
        "quick_check": "gemini",           # Fast responses
        "data_parsing": "gemini",          # Good with numbers
        "technical_summary": "gemini",     # Quick summaries
        
        # Consensus (both models) for critical decisions
        "final_decision": "consensus",     # Ask both, compare
    }
    
    # System prompts for different tasks
    SYSTEM_PROMPTS = {
        "news_analysis": f"""You are a senior financial analyst specializing in Indian stock markets (NSE/BSE).
Your task is to analyze news articles and extract actionable trading insights.

STRICT RULES:
1. Only analyze the actual news content provided - do NOT make up information
2. If news is insufficient, set bias to NEUTRAL and confidence to 0
3. Be specific about which companies/sectors are affected
4. Consider Indian market context (FII/DII flows, RBI policy, rupee movement)

You MUST respond with ONLY valid JSON (no markdown, no prose outside JSON) matching exactly this shape:
{NEWS_ANALYSIS_JSON_SCHEMA}""",

        "sentiment_aggregation": """You are a quantitative sentiment analyst for Indian equity markets.
Your task is to synthesize sentiment signals from multiple sources into a coherent view.

STRICT RULES:
1. Weight each source by its reliability and relevance
2. Identify conflicting signals and explain the divergence
3. Do NOT hallucinate or assume data not provided
4. Be explicit about uncertainty when data is limited

Output your analysis as:
- OVERALL_SENTIMENT: [VERY_BULLISH/BULLISH/NEUTRAL/BEARISH/VERY_BEARISH]
- CONFIDENCE: [0-100]%
- SIGNAL_ALIGNMENT: [ALIGNED/MIXED/CONFLICTING]
- REASONING: [brief explanation]
- RECOMMENDATION: [specific trading recommendation]""",

        "market_reasoning": """You are a senior portfolio manager at a top Indian hedge fund.
Analyze the given market data and provide your professional assessment.

STRICT RULES:
1. Base analysis ONLY on provided data - no assumptions
2. Consider Indian market specifics (market hours, circuit limits, FII/DII)
3. Think step-by-step before concluding
4. Quantify your confidence level
5. Identify key risks to your thesis""",

        "trade_decision": """You are a risk-aware F&O trading analyst for Indian index options (NSE).

STRICT RULES:
1. Base conclusions ONLY on the data provided in the user message
2. Prioritize capital preservation; if uncertain, bias is NEUTRAL
3. Be concise

You MUST respond with ONLY valid JSON (no markdown, no prose outside JSON) matching exactly this shape:
{
  "bias": "BULLISH" | "BEARISH" | "NEUTRAL",
  "confidence": <number 0-100>,
  "regime_agreement": <true|false>,
  "iv_view": <string>,
  "oi_interpretation": <string>,
  "recommended_strategy": <string>,
  "summary": <string>
}""",

        "final_decision": f"""You are the final risk gate for an automated Indian F&O intraday system.

You MUST respond with ONLY valid JSON (no markdown, no prose outside JSON) matching exactly this shape:
{FINAL_DECISION_JSON_SCHEMA}

Rules:
- decision must be EXECUTE only if both risk and edge clearly favor the trade; otherwise NO_TRADE
- confidence is 0-100 for your judgment
- reasoning is one short paragraph""",
    }
    
    def __init__(
        self,
        preferred_backend: Optional[LLMBackend] = None,
        model: Optional[str] = None,
        openai_base_url: Optional[str] = None,
        openai_api_key: Optional[str] = None,
        use_proxima_primary: bool = True,  # Use Proxima as primary if available
    ):
        """
        Initialize LLM client.
        
        Args:
            preferred_backend: Force a specific backend
            model: Model name to use (chatgpt, gemini, or ollama model)
            openai_base_url: Base URL for OpenAI-compatible API
            openai_api_key: API key for OpenAI-compatible API
            use_proxima_primary: If True, prefer Proxima over Ollama
        """
        self.model = model
        self.openai_base_url = openai_base_url or os.getenv("LLM_BASE_URL")
        self.openai_api_key = openai_api_key or os.getenv("LLM_API_KEY")
        self.use_proxima_primary = use_proxima_primary
        
        # Track available backends for fallback
        self._proxima_available = self._check_proxima()
        self._ollama_available = self._check_ollama()
        
        # Auto-detect backend
        if preferred_backend:
            self.backend = preferred_backend
        else:
            self.backend = self._detect_backend()
        
        # Set default model based on backend
        if not self.model:
            if self.backend == LLMBackend.PROXIMA:
                self.model = self.DEFAULT_MODELS["proxima_primary"]
            elif self.backend == LLMBackend.OLLAMA:
                self.model = self.DEFAULT_MODELS["ollama"]
            else:
                self.model = "gpt-3.5-turbo"
    
    def _check_proxima(self) -> bool:
        """Check if Proxima is running."""
        try:
            response = requests.get(f"{self.PROXIMA_URL}/api/status", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def _check_ollama(self) -> bool:
        """Check if Ollama is running."""
        try:
            response = requests.get(f"{self.OLLAMA_URL}/api/tags", timeout=2)
            return response.status_code == 200
        except:
            return False
    
    def _detect_backend(self) -> LLMBackend:
        """Auto-detect available LLM backend. Proxima preferred."""
        # Try Proxima first (GPT-5.5 + Gemini)
        if self.use_proxima_primary and self._proxima_available:
            return LLMBackend.PROXIMA
        
        # Fallback to Ollama
        if self._ollama_available:
            return LLMBackend.OLLAMA
        
        # Try OpenAI-compatible
        if self.openai_base_url and self.openai_api_key:
            return LLMBackend.OPENAI_COMPATIBLE
        
        return LLMBackend.NONE
    
    def is_available(self) -> bool:
        """Check if LLM is available."""
        return self.backend != LLMBackend.NONE
    
    def get_available_models(self) -> List[str]:
        """Get list of available models from all backends."""
        models = []
        
        # Proxima models
        if self._proxima_available:
            models.extend(["chatgpt (GPT-5.5)", "gemini (Gemini Pro)", "perplexity"])
        
        # Ollama models
        if self._ollama_available:
            try:
                response = requests.get(f"{self.OLLAMA_URL}/api/tags", timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    ollama_models = [f"ollama:{m['name']}" for m in data.get("models", [])]
                    models.extend(ollama_models)
            except:
                pass
        
        return models
    
    def get_backend_status(self) -> Dict[str, Any]:
        """Get status of all backends."""
        return {
            "active_backend": self.backend.value,
            "active_model": self.model,
            "proxima": {
                "available": self._proxima_available,
                "url": self.PROXIMA_URL,
                "models": ["chatgpt", "gemini", "perplexity"] if self._proxima_available else [],
            },
            "ollama": {
                "available": self._ollama_available,
                "url": self.OLLAMA_URL,
                "models": self.get_available_models() if self._ollama_available else [],
            },
        }
    
    def diagnose_proxima_auth(self) -> Dict[str, Any]:
        """
        Diagnose Proxima authentication status for trading.
        
        Returns detailed info about which providers are working.
        Call this at startup if you see "dual_gate" rejections.
        """
        if self.backend != LLMBackend.PROXIMA:
            return {"status": "not_using_proxima", "backend": self.backend.value}
        
        diagnosis = {
            "backend": "proxima",
            "status": "checking",
            "chatgpt": {"available": False, "auth": False, "error": None},
            "gemini": {"available": False, "auth": False, "error": None},
        }
        
        # Test ChatGPT
        try:
            test_msg = [{"role": "user", "content": "OK"}]
            result = self._chat_proxima(test_msg, "chatgpt", 0.7)
            if not result.content.startswith("ERROR:"):
                diagnosis["chatgpt"]["available"] = True
                diagnosis["chatgpt"]["auth"] = True
            else:
                diagnosis["chatgpt"]["error"] = result.content[:100]
        except Exception as e:
            diagnosis["chatgpt"]["error"] = str(e)[:100]
        
        # Test Gemini
        try:
            test_msg = [{"role": "user", "content": "OK"}]
            result = self._chat_proxima(test_msg, "gemini", 0.7)
            if not result.content.startswith("ERROR:"):
                diagnosis["gemini"]["available"] = True
                diagnosis["gemini"]["auth"] = True
            else:
                diagnosis["gemini"]["error"] = result.content[:100]
        except Exception as e:
            diagnosis["gemini"]["error"] = str(e)[:100]
        
        diagnosis["recommendation"] = self._recommend_proxima_fix(diagnosis)
        return diagnosis
    
    def _recommend_proxima_fix(self, diagnosis: Dict) -> str:
        """Recommend how to fix Proxima auth issues."""
        gpt_ok = diagnosis["chatgpt"]["auth"]
        gem_ok = diagnosis["gemini"]["auth"]
        
        if gpt_ok and gem_ok:
            return "✅ Both ChatGPT and Gemini authenticated. Dual-gate will work optimally."
        elif gpt_ok and not gem_ok:
            return "⚠️ GEMINI NOT AUTHENTICATED. Trades will fallback to ChatGPT only (-5% confidence penalty). FIX: Open Proxima window, click Gemini tab, login with Google account."
        elif gem_ok and not gpt_ok:
            return "⚠️ CHATGPT NOT AUTHENTICATED. Trades will fallback to Gemini only. FIX: Open Proxima window, click ChatGPT tab, login with OpenAI account."
        else:
            return "❌ CRITICAL: Both providers offline. Trades will be blocked. FIX: Start Proxima (npm start in Proxima directory) and login to both ChatGPT and Gemini tabs."
    
    def _get_model_for_task(self, task_type: Optional[str]) -> str:
        """Get the best model for a given task type using smart routing."""
        if not task_type:
            return self.model
        
        # Check if we have a routing rule for this task
        if task_type in self.TASK_MODEL_ROUTING:
            routed_model = self.TASK_MODEL_ROUTING[task_type]
            if routed_model != "consensus":
                return routed_model
        
        # Default to primary model
        return self.model
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        task_type: Optional[str] = None,
        model_override: Optional[str] = None,  # Override model for this call
        use_smart_routing: bool = True,  # Use task-based model routing
    ) -> LLMResponse:
        """
        Send chat messages to LLM with smart routing.
        
        Args:
            messages: List of {"role": "user/assistant/system", "content": "..."}
            temperature: Sampling temperature (0-1)
            max_tokens: Max response tokens
            task_type: Optional task type (also used for smart routing)
            model_override: Use specific model (overrides smart routing)
            use_smart_routing: If True, auto-select best model for task
        
        Returns:
            LLMResponse with content and metadata
        """
        # Prepend system prompt if task_type specified
        if task_type and task_type in self.SYSTEM_PROMPTS:
            system_msg = {"role": "system", "content": self.SYSTEM_PROMPTS[task_type]}
            messages = [system_msg] + messages
        
        # Determine which model to use (priority: override > smart routing > default)
        if model_override:
            model_to_use = model_override
        elif use_smart_routing and self.backend == LLMBackend.PROXIMA:
            model_to_use = self._get_model_for_task(task_type)
        else:
            model_to_use = self.model
        
        # Route to appropriate backend
        if self.backend == LLMBackend.PROXIMA:
            result = self._chat_proxima(messages, model_to_use, temperature)
            # Fallback to Ollama if Proxima fails
            if result.content.startswith("ERROR:") and self._ollama_available:
                print(f"   ⚠️ Proxima failed, falling back to Ollama...")
                self.model = self.DEFAULT_MODELS["ollama"]
                return self._chat_ollama(messages, temperature, max_tokens)
            return result
        elif self.backend == LLMBackend.OLLAMA:
            return self._chat_ollama(messages, temperature, max_tokens)
        elif self.backend == LLMBackend.OPENAI_COMPATIBLE:
            return self._chat_openai_compatible(messages, temperature, max_tokens)
        else:
            return LLMResponse(
                content="ERROR: No LLM backend available. Install Proxima or Ollama.",
                model="none",
                backend=LLMBackend.NONE,
            )
    
    def _chat_proxima(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
    ) -> LLMResponse:
        """
        Chat using Proxima backend (ChatGPT/Gemini via your subscriptions).
        
        Uses OpenAI-compatible endpoint at localhost:3210.
        """
        try:
            # Map model name to Proxima model
            proxima_model = self.PROXIMA_MODELS.get(model.lower(), model)
            
            # Combine messages into a single prompt for Proxima
            # Proxima uses 'message' field, not 'messages'
            combined_message = ""
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if role == "system":
                    combined_message += f"[SYSTEM INSTRUCTIONS]\n{content}\n\n"
                elif role == "user":
                    combined_message += f"{content}\n"
                elif role == "assistant":
                    combined_message += f"[Previous response: {content}]\n"
            
            response = requests.post(
                f"{self.PROXIMA_URL}/v1/chat/completions",
                json={
                    "model": proxima_model,
                    "message": combined_message.strip(),
                },
                headers={"Content-Type": "application/json"},
                timeout=120,  # LLMs can be slow
            )
            
            if response.status_code == 200:
                data = response.json()
                
                # Extract content from Proxima response format
                content = ""
                if "choices" in data and len(data["choices"]) > 0:
                    content = data["choices"][0].get("message", {}).get("content", "")
                elif "content" in data:
                    content = data["content"]
                elif "response" in data:
                    content = data["response"]
                
                return LLMResponse(
                    content=content,
                    model=f"proxima:{proxima_model}",
                    backend=LLMBackend.PROXIMA,
                    raw_response=data,
                )
            else:
                return LLMResponse(
                    content=f"ERROR: Proxima returned status {response.status_code}: {response.text[:200]}",
                    model=proxima_model,
                    backend=LLMBackend.PROXIMA,
                )
                
        except requests.exceptions.ConnectionError:
            return LLMResponse(
                content="ERROR: Cannot connect to Proxima. Make sure it's running at localhost:3210",
                model="none",
                backend=LLMBackend.PROXIMA,
            )
        except Exception as e:
            return LLMResponse(
                content=f"ERROR: Proxima error: {str(e)}",
                model="none",
                backend=LLMBackend.PROXIMA,
            )
    
    def consensus_chat(
        self,
        messages: List[Dict[str, str]],
        task_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ask BOTH GPT-5.5 and Gemini for critical decisions.
        
        Returns consensus analysis with confidence based on agreement.
        Use this for important trade decisions!
        
        Returns:
            {
                "gpt_response": str,
                "gemini_response": str,
                "agree": bool,
                "confidence_boost": float,  # Higher if models agree
                "consensus_decision": str,
                "reasoning": str,
            }
        """
        if self.backend != LLMBackend.PROXIMA:
            # If not using Proxima, just use single model
            response = self.chat(messages, task_type=task_type)
            return {
                "gpt_response": response.content,
                "gemini_response": None,
                "agree": True,
                "confidence_boost": 0,
                "consensus_decision": response.content,
                "reasoning": "Single model used (Proxima not available)",
            }
        
        print("   🧠 Consulting BOTH GPT-5.5 and Gemini...")
        
        # Ask GPT-5.5
        gpt_response = self.chat(messages, task_type=task_type, model_override="chatgpt")
        print(f"      GPT-5.5: Done")
        
        # Ask Gemini
        gemini_response = self.chat(messages, task_type=task_type, model_override="gemini")
        print(f"      Gemini: Done")
        
        # Structured JSON path for final_decision (EXECUTE / NO_TRADE)
        if task_type == "final_decision":
            gpt_obj, gpt_err = parse_json_response(gpt_response.content)
            gem_obj, gem_err = parse_json_response(gemini_response.content)
            
            gpt_available = gpt_obj is not None
            gem_available = gem_obj is not None
            
            # Both models return valid JSON
            if gpt_available and gem_available:
                gd = str(gpt_obj.get("decision", "NO_TRADE")).upper().strip()
                gmd = str(gem_obj.get("decision", "NO_TRADE")).upper().strip()
                both_execute = gd == "EXECUTE" and gmd == "EXECUTE"
                agree = both_execute
                if both_execute:
                    consensus = "EXECUTE"
                    confidence_boost = 0.15
                elif gd == "EXECUTE" or gmd == "EXECUTE":
                    consensus = "NO_TRADE"
                    confidence_boost = -0.25
                else:
                    consensus = "NO_TRADE"
                    confidence_boost = 0.0
                reasoning = f"GPT={gd} | Gemini={gmd} | both_models_active"
                if both_execute:
                    print(f"      ✅ MODELS AGREE: EXECUTE")
                else:
                    print(f"      ⚠️ Dual gate: no unanimous EXECUTE (GPT={gd}, Gemini={gmd})")
                return {
                    "gpt_response": gpt_response.content,
                    "gemini_response": gemini_response.content,
                    "agree": agree,
                    "confidence_boost": confidence_boost,
                    "consensus_decision": consensus,
                    "reasoning": reasoning,
                    "gpt_json": gpt_obj,
                    "gemini_json": gem_obj,
                }
            
            # One model failed — fallback to single model if available
            elif gpt_available and not gem_available:
                print(f"      ⚠️ Gemini auth/parse failed ({gem_err}) — falling back to GPT-5.5 only")
                gd = str(gpt_obj.get("decision", "NO_TRADE")).upper().strip()
                if gd == "EXECUTE":
                    consensus = "EXECUTE"
                    confidence_boost = -0.015  # Minimal penalty for single model (1.5%)
                    reasoning = f"GPT={gd} | Gemini_unavailable({gem_err}) | fallback_mode"
                else:
                    consensus = "NO_TRADE"
                    confidence_boost = 0
                    reasoning = f"GPT={gd} | Gemini_unavailable({gem_err})"
                return {
                    "gpt_response": gpt_response.content,
                    "gemini_response": f"UNAVAILABLE: {gem_err}",
                    "agree": True,  # Fallback accepted
                    "confidence_boost": confidence_boost,
                    "consensus_decision": consensus,
                    "reasoning": reasoning,
                    "gpt_json": gpt_obj,
                    "gemini_json": None,
                }
            
            elif gem_available and not gpt_available:
                print(f"      ⚠️ ChatGPT parse failed ({gpt_err}) — falling back to Gemini only")
                gmd = str(gem_obj.get("decision", "NO_TRADE")).upper().strip()
                if gmd == "EXECUTE":
                    consensus = "EXECUTE"
                    confidence_boost = -0.015  # Minimal penalty for single model (1.5%)
                    reasoning = f"Gemini={gmd} | ChatGPT_unavailable({gpt_err}) | fallback_mode"
                else:
                    consensus = "NO_TRADE"
                    confidence_boost = 0
                    reasoning = f"Gemini={gmd} | ChatGPT_unavailable({gpt_err})"
                return {
                    "gpt_response": f"UNAVAILABLE: {gpt_err}",
                    "gemini_response": gemini_response.content,
                    "agree": True,  # Fallback accepted
                    "confidence_boost": confidence_boost,
                    "consensus_decision": consensus,
                    "reasoning": reasoning,
                    "gpt_json": None,
                    "gemini_json": gem_obj,
                }
            
            # Both models failed
            else:
                print(f"      ❌ Both models failed: GPT({gpt_err}) | Gemini({gem_err}) — BLOCKING")
                return {
                    "gpt_response": gpt_response.content,
                    "gemini_response": gemini_response.content,
                    "agree": False,
                    "confidence_boost": -0.3,
                    "consensus_decision": "NO_TRADE",
                    "reasoning": f"Both models_failed: GPT({gpt_err}) | Gemini({gem_err})",
                }
        
        # Analyze agreement (legacy substring path)
        gpt_text = gpt_response.content.upper()
        gemini_text = gemini_response.content.upper()
        
        # Check for sentiment agreement
        gpt_bullish = "BULLISH" in gpt_text or "BUY" in gpt_text
        gpt_bearish = "BEARISH" in gpt_text or "SELL" in gpt_text
        gemini_bullish = "BULLISH" in gemini_text or "BUY" in gemini_text
        gemini_bearish = "BEARISH" in gemini_text or "SELL" in gemini_text
        
        # Determine agreement
        if gpt_bullish and gemini_bullish:
            agree = True
            consensus = "BULLISH"
            confidence_boost = 0.15  # 15% confidence boost for agreement
        elif gpt_bearish and gemini_bearish:
            agree = True
            consensus = "BEARISH"
            confidence_boost = 0.15
        elif (gpt_bullish and gemini_bearish) or (gpt_bearish and gemini_bullish):
            agree = False
            consensus = "CONFLICTING - BE CAUTIOUS"
            confidence_boost = -0.20  # Reduce confidence on disagreement
        else:
            agree = True  # Both neutral
            consensus = "NEUTRAL"
            confidence_boost = 0
        
        reasoning = f"GPT-5.5: {'BULLISH' if gpt_bullish else 'BEARISH' if gpt_bearish else 'NEUTRAL'} | "
        reasoning += f"Gemini: {'BULLISH' if gemini_bullish else 'BEARISH' if gemini_bearish else 'NEUTRAL'}"
        
        if agree:
            print(f"      ✅ MODELS AGREE: {consensus}")
        else:
            print(f"      ⚠️ MODELS DISAGREE - Reducing confidence")
        
        return {
            "gpt_response": gpt_response.content,
            "gemini_response": gemini_response.content,
            "agree": agree,
            "confidence_boost": confidence_boost,
            "consensus_decision": consensus,
            "reasoning": reasoning,
        }
    
    def _chat_ollama(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Chat using Ollama backend."""
        try:
            response = requests.post(
                f"{self.OLLAMA_URL}/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
                timeout=120,  # LLMs can be slow
            )
            
            if response.status_code == 200:
                data = response.json()
                return LLMResponse(
                    content=data.get("message", {}).get("content", ""),
                    model=self.model,
                    backend=LLMBackend.OLLAMA,
                    tokens_used=data.get("eval_count"),
                    raw_response=data,
                )
            else:
                return LLMResponse(
                    content=f"ERROR: Ollama returned {response.status_code}: {response.text}",
                    model=self.model,
                    backend=LLMBackend.OLLAMA,
                )
        except Exception as e:
            return LLMResponse(
                content=f"ERROR: Ollama request failed: {str(e)}",
                model=self.model,
                backend=LLMBackend.OLLAMA,
            )
    
    def _chat_openai_compatible(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Chat using OpenAI-compatible API."""
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.openai_api_key}",
            }
            
            response = requests.post(
                f"{self.openai_base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
                timeout=120,
            )
            
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                return LLMResponse(
                    content=content,
                    model=self.model,
                    backend=LLMBackend.OPENAI_COMPATIBLE,
                    tokens_used=data.get("usage", {}).get("total_tokens"),
                    raw_response=data,
                )
            else:
                return LLMResponse(
                    content=f"ERROR: API returned {response.status_code}: {response.text}",
                    model=self.model,
                    backend=LLMBackend.OPENAI_COMPATIBLE,
                )
        except Exception as e:
            return LLMResponse(
                content=f"ERROR: API request failed: {str(e)}",
                model=self.model,
                backend=LLMBackend.OPENAI_COMPATIBLE,
            )
    
    def analyze_news(self, news_items: List[Dict], symbol: str) -> Dict[str, Any]:
        """
        Analyze news using LLM reasoning.
        
        Args:
            news_items: List of news items with title/description
            symbol: Stock symbol being analyzed
        
        Returns:
            Structured analysis result
        """
        if not news_items:
            return {
                "sentiment": "NEUTRAL",
                "confidence": 0,
                "reasoning": "No news data provided",
                "llm_used": False,
            }
        
        # Format news for LLM
        news_text = "\n\n".join([
            f"[{i+1}] {item.get('title', '')}\n{item.get('description', '')}"
            for i, item in enumerate(news_items[:10])  # Limit to 10 items
        ])
        
        prompt = f"""Analyze the following news articles about {symbol} and provide your assessment:

NEWS ARTICLES:
{news_text}

Provide your analysis following the format specified."""
        
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            task_type="news_analysis",
            temperature=0.3,  # Lower temp for more consistent analysis
        )
        
        if response.content.startswith("ERROR:"):
            return {
                "sentiment": "NEUTRAL",
                "confidence": 0,
                "reasoning": response.content,
                "llm_used": False,
            }
        
        parsed, err = parse_json_response(response.content)
        if parsed and err is None:
            return normalize_news_from_json(parsed, symbol, response.content)
        # Parse LLM response (legacy line-based)
        return self._parse_news_analysis(response.content, symbol)
    
    def _parse_news_analysis(self, response: str, symbol: str) -> Dict[str, Any]:
        """Parse LLM news analysis response."""
        result = {
            "symbol": symbol,
            "sentiment": "NEUTRAL",
            "confidence": 50,
            "key_factors": [],
            "trading_implication": "",
            "risks": [],
            "raw_response": response,
            "llm_used": True,
        }
        
        lines = response.upper().split("\n")
        for line in lines:
            if "SENTIMENT:" in line:
                if "BULLISH" in line:
                    result["sentiment"] = "VERY_BULLISH" if "VERY" in line else "BULLISH"
                elif "BEARISH" in line:
                    result["sentiment"] = "VERY_BEARISH" if "VERY" in line else "BEARISH"
            elif "CONFIDENCE:" in line:
                try:
                    conf = int(''.join(filter(str.isdigit, line.split(":")[-1])))
                    result["confidence"] = min(100, max(0, conf))
                except:
                    pass
        
        # Extract sections from original (non-uppercase) response
        for section in ["KEY_FACTORS", "TRADING_IMPLICATION", "RISKS"]:
            if section in response.upper():
                idx = response.upper().index(section)
                end_idx = len(response)
                for other in ["KEY_FACTORS", "TRADING_IMPLICATION", "RISKS", "SENTIMENT", "CONFIDENCE"]:
                    if other != section and other in response.upper()[idx+len(section):]:
                        potential_end = response.upper().index(other, idx+len(section))
                        if potential_end < end_idx:
                            end_idx = potential_end
                content = response[idx:end_idx].split(":", 1)[-1].strip()
                if section == "KEY_FACTORS":
                    result["key_factors"] = [f.strip() for f in content.split("\n") if f.strip()][:5]
                elif section == "RISKS":
                    result["risks"] = [r.strip() for r in content.split("\n") if r.strip()][:3]
                else:
                    result["trading_implication"] = content[:200]
        
        return result
    
    def aggregate_sentiment(
        self,
        technical_sentiment: Dict,
        news_sentiment: Dict,
        symbol: str,
    ) -> Dict[str, Any]:
        """
        Use LLM to aggregate sentiment from multiple sources.
        """
        prompt = f"""Analyze and aggregate the following sentiment signals for {symbol}:

TECHNICAL ANALYSIS SENTIMENT:
- Bias: {technical_sentiment.get('bias', 'N/A')}
- Confidence: {technical_sentiment.get('confidence', 'N/A')}
- Key Indicators: RSI={technical_sentiment.get('indicators', {}).get('rsi', 'N/A')}

NEWS SENTIMENT:
- Sentiment: {news_sentiment.get('sentiment', 'N/A')}
- Confidence: {news_sentiment.get('confidence', 'N/A')}
- Key Factors: {news_sentiment.get('key_factors', [])}

Synthesize these signals into a unified view following the format specified."""
        
        response = self.chat(
            messages=[{"role": "user", "content": prompt}],
            task_type="sentiment_aggregation",
            temperature=0.3,
        )
        
        if response.content.startswith("ERROR:"):
            # Fallback to simple averaging
            return {
                "overall_sentiment": technical_sentiment.get("bias", "NEUTRAL"),
                "confidence": (
                    technical_sentiment.get("confidence", 0) * 0.6 +
                    news_sentiment.get("confidence", 0) * 0.4
                ) / 100,
                "reasoning": "LLM unavailable - using simple aggregation",
                "llm_used": False,
            }
        
        return self._parse_sentiment_aggregation(response.content)
    
    def _parse_sentiment_aggregation(self, response: str) -> Dict[str, Any]:
        """Parse LLM sentiment aggregation response."""
        result = {
            "overall_sentiment": "NEUTRAL",
            "confidence": 50,
            "signal_alignment": "MIXED",
            "reasoning": "",
            "recommendation": "",
            "raw_response": response,
            "llm_used": True,
        }
        
        lines = response.upper().split("\n")
        for line in lines:
            if "OVERALL_SENTIMENT:" in line:
                for sent in ["VERY_BULLISH", "BULLISH", "NEUTRAL", "BEARISH", "VERY_BEARISH"]:
                    if sent in line:
                        result["overall_sentiment"] = sent
                        break
            elif "CONFIDENCE:" in line:
                try:
                    conf = int(''.join(filter(str.isdigit, line.split(":")[-1])))
                    result["confidence"] = min(100, max(0, conf)) / 100
                except:
                    pass
            elif "SIGNAL_ALIGNMENT:" in line:
                for align in ["ALIGNED", "MIXED", "CONFLICTING"]:
                    if align in line:
                        result["signal_alignment"] = align
                        break
        
        # Extract reasoning and recommendation
        for section in ["REASONING", "RECOMMENDATION"]:
            if section in response.upper():
                idx = response.upper().index(section)
                content = response[idx:].split(":", 1)[-1].split("\n")[0].strip()
                result[section.lower()] = content[:300]
        
        return result


# Singleton instance
_llm_client = None

def get_llm_client(reset: bool = False) -> LLMClient:
    """Get or create LLM client singleton."""
    global _llm_client
    if _llm_client is None or reset:
        _llm_client = LLMClient()
    return _llm_client


def check_llm_status() -> Dict[str, Any]:
    """Check LLM availability status for all backends."""
    client = get_llm_client()
    
    status = {
        "available": client.is_available(),
        "backend": client.backend.value,
        "model": client.model,
        "backends": client.get_backend_status(),
    }
    
    if client.backend == LLMBackend.PROXIMA:
        status["power_level"] = "MAXIMUM"  # GPT-5.5 + Gemini Pro!
        status["models_available"] = ["chatgpt (GPT-5.5)", "gemini (Gemini Pro)"]
        status["setup_instructions"] = None
    elif client.backend == LLMBackend.OLLAMA:
        status["power_level"] = "LOCAL"
        status["models_available"] = client.get_available_models()
        status["setup_instructions"] = """
For more powerful analysis, set up Proxima:

1. Clone: git clone https://github.com/Zen4-bit/Proxima.git
2. Install: cd Proxima && npm install && npm start
3. Login to ChatGPT & Gemini in Proxima window
4. Enable REST API in Proxima Settings
5. Restart the trading agent

This uses your existing ChatGPT Plus & Gemini Pro subscriptions!
"""
    else:
        status["power_level"] = "NONE"
        status["setup_instructions"] = """
To enable LLM-powered analysis:

OPTION A - Proxima (Recommended - uses your ChatGPT/Gemini subscriptions):
1. git clone https://github.com/Zen4-bit/Proxima.git
2. cd Proxima && npm install && npm start
3. Login to ChatGPT & Gemini in Proxima window
4. Enable REST API in Settings

OPTION B - Ollama (Free local models):
1. brew install ollama (or download from ollama.ai)
2. ollama serve
3. ollama pull qwen2.5:7b-instruct

Then restart the trading agent.
"""
    
    return status
