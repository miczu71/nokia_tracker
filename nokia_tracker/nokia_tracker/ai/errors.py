class AIProviderError(Exception):
    """Błąd backendu AI (openai_compat) albo odmowa wywołania (AI wyłączone,
    obwód otwarty, limit) — provider.py zapisuje porażkę i rzuca dalej."""
