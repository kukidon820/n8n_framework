"""Поддержка дополнительного режима генерации через LangGraph."""

from n8n_transcriper.langgraph_mode.detection import AiRequestDetection, detect_ai_request

__all__ = ["AiRequestDetection", "detect_ai_request"]
