"""Optional LangGraph generation support."""

from n8n_transcriper.langgraph_mode.detection import AiRequestDetection, detect_ai_request

__all__ = ["AiRequestDetection", "detect_ai_request"]
