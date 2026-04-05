"""Configuration for STS2 local agent."""

# --- LLM ---
OLLAMA_BASE_URL = "http://localhost:11434/v1"
OLLAMA_API_KEY = "ollama"  # Ollama doesn't need a real key

# Switch model here for A/B testing
ACTIVE_MODEL = "gemma4:26b"

# ACTIVE_MODEL = "koboldcpp"  # KoboldCPP on :5001
# ACTIVE_MODEL = "qwen3.5:27b"
# ACTIVE_MODEL = "phi4:14b"
# ACTIVE_MODEL = "glm4:9b"

LLM_TEMPERATURE = 0.3  # Low = more deterministic decisions
LLM_MAX_TOKENS = 512   # Room for tool call + brief reasoning; repeat_penalty handles spirals
LLM_REPEAT_PENALTY = 1.15  # Penalize repeated tokens (Ollama-specific, 1.0 = off, 1.3 was too aggressive)

# --- Game ---
GAME_BASE_URL = "http://localhost:15526"
GAME_API_URL = f"{GAME_BASE_URL}/api/v1/singleplayer"

# --- Agent ---
MAX_RETRIES_PER_ACTION = 3       # Retry on tool call errors
MAX_HISTORY_TURNS = 5            # Keep last N exchanges — 27B context is limited
TURN_TIMEOUT_SECONDS = 60        # Max time waiting for LLM response

# --- Logging ---
LOG_DIR = "logs"
LOG_THINKING = True              # Log Qwen3's <think> blocks
