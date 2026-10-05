"""Fixtures compartilhadas.

Os testes usam documentos SINTÉTICOS (apólices fictícias geradas em memória),
para não depender das apólices reais nem expor dados de clientes no repositório.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import Settings  # noqa: E402
from src.llm_client import LLMError  # noqa: E402

TEXTO_APOLICE_A = """APÓLICE DE SEGURO D&O
Seguradora: Seguradora Alfa S.A.
Apólice: 1111.2024.0001
Tomador: EMPRESA FICTICIA INDUSTRIAL LTDA.
CNPJ: 00.000.000/0001-00
Vigência: a partir das 24 horas de 01/01/2024 até as 24 horas de 01/01/2025
Prêmio Líquido: R$ 10.000,00
Prêmio Total: R$ 10.738,00
Limite máximo de garantia da Apólice: R$ 5.000.000,00
Franquia: R$ 50.000,00
Cobertura A - Pagamento ao Administrador     100% do LMI
Extensão: Custos Emergenciais                 100% do LMI
Extensão: Danos Morais                        50% do LMI
"""

TEXTO_APOLICE_B = """APÓLICE DE SEGURO D&O
Seguradora: Seguradora Beta S.A.
Apólice: 2222.2025.0001
Tomador: EMPRESA FICTICIA INDUSTRIAL LTDA.
CNPJ: 00.000.000/0001-00
Vigência: a partir das 24 horas de 01/01/2025 até as 24 horas de 01/01/2026
Prêmio Líquido: R$ 8.000,00
Prêmio Total: R$ 8.590,40
Limite máximo de garantia da Apólice: R$ 5.000.000,00
Franquia/ Participação Obrigatória do Segurado: Não há aplicação de franquia
Cobertura A - Pagamento ao Administrador     100% do LMI
Extensão: Despesas Emergenciais               100% do LMI
Extensão: Danos Morais                        100% do LMI
Extensão: Práticas Trabalhistas               100% do LMI
"""


def make_pdf(texto: str, paginas_em_branco: int = 0) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for linha in texto.splitlines():
        page.insert_text((40, y), linha, fontsize=9)
        y += 13
    for _ in range(paginas_em_branco):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def pdf_a() -> bytes:
    return make_pdf(TEXTO_APOLICE_A)


@pytest.fixture
def pdf_b() -> bytes:
    return make_pdf(TEXTO_APOLICE_B)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(openai_api_key=None, cache_enabled=False, cache_path=tmp_path / "c.sqlite")


tesseract_disponivel = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract não instalado")


# ----------------------------------------------------------------- LLM falso
def _campo(valor, texto=None, pagina=1):
    return {"valor": valor, "texto_original": texto if texto is not None else valor, "pagina": pagina, "trecho": texto}


RESPOSTA_EXTRACAO = {
    "Seguradora Alfa": {
        "tipo_documento": {"valor": "apolice"},
        "seguradora": _campo("Seguradora Alfa S.A."),
        "segurado": _campo("EMPRESA FICTICIA INDUSTRIAL LTDA."),
        "numero_apolice": _campo("1111.2024.0001"),
        "vigencia_inicio": _campo("2024-01-01", "01/01/2024"),
        "vigencia_fim": _campo("2025-01-01", "01/01/2025"),
        "premio_liquido": _campo(10000.0, "R$ 10.000,00"),
        "premio_total": _campo(10738.0, "R$ 10.738,00"),
        "lmg": _campo(5000000.0, "R$ 5.000.000,00"),
        "lmi": None,
        "franquia": _campo("R$ 50.000,00"),
        # valor inventado: não existe no documento e deve ser descartado pelo guardrail
        "retroatividade": _campo("Ilimitada desde 1990", "Retroatividade ilimitada desde 1990"),
        "coberturas": [{"nome": "Cobertura A - Pagamento ao Administrador", "limite": "100% do LMI"}],
        "extensoes": [
            {"nome": "Custos Emergenciais", "limite": "100% do LMI"},
            {"nome": "Danos Morais", "limite": "50% do LMI"},
        ],
        "exclusoes": [],
        "clausulas_relevantes": [],
        "observacoes": [],
    },
    "Seguradora Beta": {
        "tipo_documento": {"valor": "apolice"},
        "seguradora": _campo("Seguradora Beta S.A."),
        "segurado": _campo("EMPRESA FICTICIA INDUSTRIAL LTDA."),
        "numero_apolice": _campo("2222.2025.0001"),
        "vigencia_inicio": _campo("2025-01-01", "01/01/2025"),
        "vigencia_fim": _campo("2026-01-01", "01/01/2026"),
        "premio_liquido": _campo(8000.0, "R$ 8.000,00"),
        "premio_total": _campo(8590.40, "R$ 8.590,40"),
        "lmg": _campo(5000000.0, "R$ 5.000.000,00"),
        "franquia": _campo("Não há aplicação de franquia"),
        "coberturas": [{"nome": "Cobertura A - Pagamento ao Administrador", "limite": "100% do LMI"}],
        "extensoes": [
            {"nome": "Despesas Emergenciais", "limite": "100% do LMI"},
            {"nome": "Danos Morais", "limite": "100% do LMI"},
            {"nome": "Práticas Trabalhistas", "limite": "100% do LMI"},
        ],
        "observacoes": [],
    },
}

RESPOSTA_ANALISE = {
    "sintese": "A renovação trocou a seguradora e reduziu o prêmio total.",
    "ganhos": ["Prêmio total reduzido.", "Inclusão de Práticas Trabalhistas."],
    "perdas": [],
    "pontos_atencao": ["Validar condições gerais com o corretor."],
}


class FakeLLM:
    """Simula a OpenAI: devolve respostas fixas conforme o prompt."""

    model = "fake-llm"
    available = True

    def __init__(self, falhar: bool = False):
        self.falhar = falhar
        self.chamadas = 0

    def complete_json(self, system: str, user: str) -> dict:
        self.chamadas += 1
        if self.falhar:
            raise LLMError("Simulação: OpenAI indisponível (timeout).")
        if "COMPARAÇÃO ESTRUTURADA" in user:
            return json.loads(json.dumps(RESPOSTA_ANALISE))
        for chave, resposta in RESPOSTA_EXTRACAO.items():
            if chave in user:
                return json.loads(json.dumps(resposta))
        return {}
