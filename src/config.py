"""Configuração central do PolicyMind D&O.

Todas as configurações vêm de variáveis de ambiente (arquivo .env).
Nenhuma chave de API é escrita no código.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "sim", "yes", "on"}


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Parâmetros de execução. Use `get_settings()` para obter a instância."""

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    # Endpoint compatível com a API da OpenAI (ex.: xAI/Grok = https://api.x.ai/v1). Vazio = OpenAI.
    openai_base_url: str | None = None
    openai_timeout: int = 120
    openai_temperature: float = 0.0

    # Quantidade máxima de caracteres do documento enviada ao LLM.
    max_chars_llm: int = 120_000
    # Mínimo de caracteres por página para considerar que a página tem texto extraível.
    min_chars_per_page: int = 40
    ocr_enabled: bool = True
    ocr_lang: str = "por"
    ocr_dpi: int = 200
    # Caminho do executável do Tesseract (útil no Windows se não estiver no PATH)
    tesseract_cmd: str | None = None

    # Se a OpenAI estiver indisponível, permite uma extração mínima por regras
    # (claramente identificada como "regras - sem IA" na interface).
    allow_rule_fallback: bool = True

    cache_enabled: bool = True
    cache_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "cache.sqlite")

    @property
    def llm_available(self) -> bool:
        return bool(self.openai_api_key)


def get_settings() -> Settings:
    """Lê as variáveis de ambiente a cada chamada (facilita testes)."""
    key = os.getenv("OPENAI_API_KEY", "").strip() or None
    if key and key.startswith("sk-coloque"):
        key = None  # valor de exemplo do .env.example
    return Settings(
        openai_api_key=key,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
        openai_base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        openai_timeout=_int(os.getenv("OPENAI_TIMEOUT"), 120),
        max_chars_llm=_int(os.getenv("MAX_CHARS_LLM"), 120_000),
        ocr_enabled=_bool(os.getenv("OCR_ENABLED"), True),
        ocr_lang=os.getenv("OCR_LANG", "por") or "por",
        tesseract_cmd=os.getenv("TESSERACT_CMD") or None,
        allow_rule_fallback=_bool(os.getenv("ALLOW_RULE_FALLBACK"), True),
        cache_enabled=_bool(os.getenv("CACHE_ENABLED"), True),
        cache_path=Path(os.getenv("CACHE_PATH") or PROJECT_ROOT / "data" / "cache.sqlite"),
    )
