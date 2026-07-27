class AIProviderError(Exception):
    """Błąd dowolnego backendu AI (openai_compat/gemini/anthropic) —
    provider.py łapie to i przechodzi do następnego ogniwa łańcucha."""
