"""Cliente mínimo para a API da OpenAI (IA Generativa).

Centraliza: leitura da chave via ambiente, modo JSON, timeout e tradução de
erros para uma exceção única (`LLMError`) com mensagem clara para a interface.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from src.config import Settings, get_settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """A IA generativa não pôde ser usada (sem chave, rede, cota, resposta inválida...)."""


class LLMClient:
    # Limite de espera (s) quando o provedor responde 429 (limite de tokens/requisições por minuto)
    MAX_ESPERA_RATE_LIMIT = 65
    TENTATIVAS_RATE_LIMIT = 2

    def __init__(self, settings: Settings | None = None, client: Any | None = None, sleep=time.sleep):
        self.settings = settings or get_settings()
        self._client = client  # permite injetar um cliente falso nos testes
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self.settings.openai_model

    @property
    def available(self) -> bool:
        return self._client is not None or self.settings.llm_available

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.settings.llm_available:
            raise LLMError(
                "OPENAI_API_KEY não configurada. Crie o arquivo .env a partir do .env.example "
                "e informe sua chave (OpenAI ou provedor compatível) para habilitar a IA generativa."
            )
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise LLMError("Pacote 'openai' não instalado (pip install -r requirements.txt).") from exc
        self._client = OpenAI(
            api_key=self.settings.openai_api_key,
            base_url=self.settings.openai_base_url,  # None = API oficial da OpenAI
            timeout=self.settings.openai_timeout,
        )
        return self._client

    def complete_json(self, system: str, user: str) -> dict:
        """Envia o prompt e devolve o JSON da resposta como dicionário."""
        client = self._get_client()
        params = {
            "model": self.model,
            "temperature": self.settings.openai_temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = self._create_with_retry(client, params)
        except Exception as exc:
            nome = type(exc).__name__
            raise LLMError(f"Falha na chamada à OpenAI ({nome}): {exc}") from exc

        try:
            content = resp.choices[0].message.content or ""
        except (AttributeError, IndexError) as exc:
            raise LLMError("Resposta da OpenAI em formato inesperado.") from exc
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            logger.error("JSON inválido retornado pelo LLM: %s", content[:500])
            raise LLMError("O modelo não retornou um JSON válido.") from exc

    def _create_with_retry(self, client, params: dict):
        """Chama a API tratando dois casos previsíveis:
        - modelo que não aceita 'temperature' → repete sem o parâmetro;
        - 429 (limite por minuto, comum em planos gratuitos) → espera o tempo indicado e repete.
        """
        tentativas = 0
        while True:
            try:
                return client.chat.completions.create(**params)
            except Exception as exc:
                msg = str(exc)
                if "temperature" in msg.lower() and "temperature" in params:
                    params.pop("temperature")
                    continue
                if getattr(exc, "status_code", None) == 429 and tentativas < self.TENTATIVAS_RATE_LIMIT:
                    tentativas += 1
                    m = re.search(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)s", msg)
                    espera = (int(m.group(1) or 0) * 60 + float(m.group(2))) if m else 20.0
                    espera = min(espera + 1, self.MAX_ESPERA_RATE_LIMIT)
                    logger.warning("Limite por minuto do provedor atingido; aguardando %.0fs.", espera)
                    self._sleep(espera)
                    continue
                raise
