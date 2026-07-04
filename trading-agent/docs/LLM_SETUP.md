# LLM Setup for QUANT-1 Smart Analysis

## Overview

QUANT-1 uses LLMs for intelligent trading analysis instead of basic keyword matching.
This gives us **real AI reasoning** for:
- News sentiment analysis
- Multi-source signal aggregation
- Trading decision generation

## Backend Options

| Backend | Models | Quality | Cost | Best For |
|---------|--------|---------|------|----------|
| **Proxima** | GPT-5.5, Gemini Pro | Excellent | $0 (uses subscriptions) | If you have ChatGPT Plus/Gemini |
| **Ollama** | qwen2.5, deepseek, llama | Good | Free | Offline / No subscription |

---

## Option A: Proxima Setup (RECOMMENDED)

**Uses your existing ChatGPT Plus & Gemini Pro subscriptions - NO API COSTS!**

### 1. Clone and Install Proxima
```bash
git clone https://github.com/Zen4-bit/Proxima.git
cd Proxima
npm install
npm start
```

### 2. Login to Your AI Providers
- Proxima window opens
- Click on ChatGPT tab → Login with your ChatGPT Plus account
- Click on Gemini tab → Login with your Gemini Pro account

### 3. Enable REST API
- Go to **Settings** in Proxima
- Enable **REST API & CLI**
- API will be available at `http://localhost:3210`

### 4. Verify Setup
```bash
curl http://localhost:3210/api/status
```

### 5. Restart Trading Agent
```bash
cd /Users/sahil/Desktop/Tradibng/trading-agent
source venv/bin/activate
python main.py --status
```

You should see: `Active Backend: proxima` and `Power Level: MAXIMUM`

---

## Option B: Ollama Setup (Fallback/Offline)

**Free local models - works offline, always available.**

### 1. Install Ollama

**macOS:**
```bash
brew install ollama
```

**Or download from:** https://ollama.ai

### 2. Start Ollama
```bash
ollama serve
```

### 3. Pull a Model

Choose one based on your hardware:

| Model | Size | RAM Needed | Best For |
|-------|------|------------|----------|
| `qwen2.5:3b-instruct` | 2GB | 4GB | Fast, basic analysis |
| `qwen2.5:7b-instruct` | 4GB | 8GB | **Recommended** - Good balance |
| `deepseek-r1:7b` | 4GB | 8GB | Best reasoning |
| `llama3.1:8b-instruct` | 5GB | 10GB | Alternative |

**Recommended:**
```bash
ollama pull qwen2.5:7b-instruct
```

### 4. Verify Setup
```bash
# Check model is installed
ollama list

# Test it works
ollama run qwen2.5:7b-instruct "What is the RSI indicator?"
```

### 5. Restart Trading Agent
```bash
python main.py --status
```
You should see: `🧠 LLM Active: ollama - qwen2.5:7b-instruct`

---

## Model Recommendations

### For Indian Market Analysis

**Best Overall:** `qwen2.5:7b-instruct`
- Fast inference
- Good at financial reasoning
- Handles Indian market terminology

**Best Reasoning:** `deepseek-r1:7b` or `deepseek-r1:14b`
- Superior chain-of-thought reasoning
- Better at complex analysis
- Based on DeepSeek-R1 (OpenAI o1 competitor)

**Fastest:** `qwen2.5:3b-instruct`
- Quick responses
- Good for real-time analysis
- Works on 8GB RAM machines

---

## Alternative: OpenAI-Compatible APIs

If you have a local LLM server (LM Studio, vLLM, etc.), configure:

```bash
# In .env file
LLM_BASE_URL=http://localhost:1234/v1
LLM_API_KEY=not-needed
```

---

## How It Works

### Without LLM (Fallback)
```
News → Keyword Matching → Simple Score → Basic Sentiment
```

### With LLM (Smart Mode)
```
News → LLM Analysis → Contextual Understanding → 
      → Reasoning about implications →
      → Trading signals with explanations
```

### Example LLM Analysis Output

```
SENTIMENT: BULLISH
CONFIDENCE: 75%

KEY_FACTORS:
- Strong quarterly earnings beat (23% YoY growth)
- FII buying continues for 3rd consecutive session
- New product launch in emerging segment

TRADING_IMPLICATION:
Near-term positive momentum expected. Consider accumulating 
on dips towards support at ₹2,450.

RISKS:
- Global market volatility could impact sentiment
- Sector rotation risk from IT to banking
```

---

## Troubleshooting

### "No LLM available"
1. Check Ollama is running: `curl http://localhost:11434/api/tags`
2. Ensure model is pulled: `ollama list`
3. Restart Ollama: `ollama serve`

### Slow responses
- Use smaller model: `qwen2.5:3b-instruct`
- Check RAM usage
- Close other applications

### Out of memory
- Use quantized models (default in Ollama)
- Try smaller model
- Increase swap space

---

## Comparison: Rule-Based vs LLM

| Feature | Rule-Based | LLM-Powered |
|---------|-----------|-------------|
| Speed | Very Fast | Moderate |
| Accuracy | Basic | High |
| Context Understanding | None | Excellent |
| Nuance Detection | Poor | Good |
| Indian Market Terms | Limited | Good |
| Reasoning | None | Chain-of-thought |
| Explanations | None | Detailed |

**Bottom Line:** Install Ollama for serious trading analysis.

---

## Automation Setup (For OpenClaw / 24/7 Trading)

For fully autonomous trading, Ollama needs to run as a background service.

### Option 1: Homebrew Service (Recommended for Mac)

```bash
# Start Ollama as a service (auto-starts on boot)
brew services start ollama

# Check status
brew services list | grep ollama

# Stop if needed
brew services stop ollama
```

### Option 2: LaunchAgent (Mac Alternative)

Create `~/Library/LaunchAgents/com.ollama.plist`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ollama</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/ollama</string>
        <string>serve</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
```

Then: `launchctl load ~/Library/LaunchAgents/com.ollama.plist`

### Option 3: Manual Background

```bash
# Run in background (stops when terminal closes)
ollama serve &

# Run with nohup (persists after terminal close)
nohup ollama serve > /tmp/ollama.log 2>&1 &
```

### Verifying Service is Running

```bash
# Check if Ollama API is responding
curl -s http://localhost:11434/api/tags | jq .

# Check process
ps aux | grep ollama
```

### OpenClaw Integration Notes

When automating with OpenClaw:
1. OpenClaw will manage Ollama startup automatically
2. The trading agent checks LLM availability at startup
3. Falls back to rule-based analysis if LLM unavailable
4. Logs will indicate which mode is being used

**Important:** Ensure at least 8GB RAM is available for optimal performance.
