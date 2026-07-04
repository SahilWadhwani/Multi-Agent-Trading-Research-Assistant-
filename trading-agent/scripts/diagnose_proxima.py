#!/usr/bin/env python3
"""
Diagnose Proxima authentication issues that block dual-model gate trades.

If you see "dual_model_gate" rejections in your signals, run this first:
    python scripts/diagnose_proxima.py

This will tell you which providers are authenticated in Proxima.
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.client import get_llm_client, LLMBackend


def print_header(text):
    print(f"\n{'='*70}")
    print(f"  {text}")
    print(f"{'='*70}\n")


def main():
    print_header("🔍 PROXIMA AUTHENTICATION DIAGNOSTICS")
    
    # Initialize LLM client
    llm = get_llm_client()
    
    print(f"Backend Status:")
    print(f"  Active Backend: {llm.backend.value}")
    print(f"  Active Model: {llm.model}")
    print(f"  Available: {llm.is_available()}\n")
    
    if llm.backend != LLMBackend.PROXIMA:
        print(f"⚠️  Not using Proxima! Using {llm.backend.value} instead.")
        print(f"    To use Proxima:")
        print(f"    1. cd /Users/sahil/Desktop/Tradibng/Proxima")
        print(f"    2. npm start")
        print(f"    3. Wait for the Proxima window to open")
        print(f"    4. Login to ChatGPT and Gemini")
        print(f"    5. Enable REST API in Proxima Settings")
        print(f"    6. Run this script again\n")
        return 1
    
    # Diagnose Proxima
    print("Testing Proxima providers...\n")
    diagnosis = llm.diagnose_proxima_auth()
    
    print("Provider Status:")
    print(f"  ChatGPT:")
    print(f"    Available: {diagnosis['chatgpt']['available']}")
    print(f"    Authenticated: {diagnosis['chatgpt']['auth']}")
    if diagnosis['chatgpt']['error']:
        print(f"    Error: {diagnosis['chatgpt']['error']}")
    
    print(f"\n  Gemini:")
    print(f"    Available: {diagnosis['gemini']['available']}")
    print(f"    Authenticated: {diagnosis['gemini']['auth']}")
    if diagnosis['gemini']['error']:
        print(f"    Error: {diagnosis['gemini']['error']}")
    
    print(f"\n{'─'*70}")
    print(f"Recommendation:")
    print(f"  {diagnosis['recommendation']}")
    print(f"{'─'*70}\n")
    
    # Trading impact
    print("Trading Impact:")
    gpt_ok = diagnosis['chatgpt']['auth']
    gem_ok = diagnosis['gemini']['auth']
    
    if gpt_ok and gem_ok:
        print("  ✅ High-confidence dual-model gate ENABLED")
        print("     - Requires BOTH ChatGPT AND Gemini to approve trades")
        print("     - Highest confidence level (+15% boost)")
        print("     - Most conservative but safest")
    elif gpt_ok or gem_ok:
        print("  ⚠️  Fallback mode ACTIVATED")
        working = "ChatGPT" if gpt_ok else "Gemini"
        print(f"     - Using {working} only for final decision")
        print(f"     - Confidence penalty of -5%")
        print(f"     - Fix the other provider for full dual-gate power")
    else:
        print("  ❌ CRITICAL: Dual-model gate DISABLED")
        print("     - High-confidence trades will be BLOCKED")
        print("     - Fix Proxima auth immediately!")
    
    print("\nRecent Signal Rejections (if any):")
    print("  Check /Users/sahil/Desktop/Tradibng/trading-agent/data_cache/signal_tracker.db")
    print("  SQL: SELECT timestamp, symbol, rejection_reason FROM scans")
    print("       WHERE blocked_by_gate = 'dual_model_gate' ORDER BY timestamp DESC LIMIT 5;")
    
    print("\n" + "="*70)
    print("  ✅ Diagnostics complete! Ready to trade." if (gpt_ok or gem_ok) else "  ❌ Please fix the issues above.")
    print("="*70 + "\n")
    
    return 0 if (gpt_ok or gem_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
