"""Schemas Pydantic compartilhados entre os agentes.

Cada agente recebe e devolve objetos destes tipos, o que torna os contratos
entre os componentes explícitos e validados.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# DocumentAgent
# ---------------------------------------------------------------------------


class PaginaTexto(BaseModel):
    numero: int
    texto: str
    metodo: Literal["texto", "ocr", "vazia"] = "texto"


class DocumentoProcessado(BaseModel):
    """Saída do DocumentAgent."""

    nome_arquivo: str
    tipo_arquivo: Literal["pdf", "imagem"]
    hash_sha256: str
    tamanho_bytes: int
    num_paginas: int
    metodo_extracao: Literal["texto", "ocr", "misto", "falhou"]
    paginas: list[PaginaTexto] = Field(default_factory=list)
    avisos: list[str] = Field(default_factory=list)

    @property
    def texto_completo(self) -> str:
        return "\n\n".join(p.texto for p in self.paginas if p.texto)

    @property
    def total_caracteres(self) -> int:
        return sum(len(p.texto) for p in self.paginas)

    def texto_com_marcadores(self) -> str:
        """Texto com marcadores de página, usados pelo LLM para citar a fonte."""
        return "\n\n".join(f"[PÁGINA {p.numero}]\n{p.texto}" for p in self.paginas if p.texto)


# ---------------------------------------------------------------------------
# PolicyExtractionAgent / NormalizationAgent
# ---------------------------------------------------------------------------


class Campo(BaseModel):
    """Um campo extraído, sempre acompanhado (quando possível) da evidência."""

    valor: Any = None  # valor tratado (ex.: 10880.98, "2024-09-30"); None se ausente
    texto_original: str | None = None  # como aparece no documento
    pagina: int | None = None
    trecho: str | None = None  # trecho de origem (evidência)

    @property
    def encontrado(self) -> bool:
        return self.valor is not None or bool(self.texto_original)


class ItemApolice(BaseModel):
    """Cobertura, extensão, exclusão ou cláusula."""

    nome: str  # nome como aparece no documento
    nome_normalizado: str | None = None  # preenchido pelo NormalizationAgent
    chave: str | None = None  # identificador canônico usado na comparação
    limite: str | None = None  # ex.: "100% do LMI", "R$ 500.000,00"
    franquia: str | None = None
    descricao: str | None = None
    pagina: int | None = None
    trecho: str | None = None


CAMPOS_ESCALARES: dict[str, str] = {
    "tipo_documento": "Tipo de documento",
    "seguradora": "Seguradora",
    "seguradora_anterior": "Seguradora anterior",
    "segurado": "Segurado / Tomador",
    "cnpj_segurado": "CNPJ do segurado",
    "numero_apolice": "Número da apólice",
    "corretor": "Corretor",
    "vigencia_inicio": "Início de vigência",
    "vigencia_fim": "Fim de vigência",
    "premio_liquido": "Prêmio líquido",
    "iof": "IOF",
    "premio_total": "Prêmio total",
    "lmg": "LMG (Limite Máximo de Garantia)",
    "lmi": "LMI (Limite Máximo de Indenização)",
    "franquia": "Franquia / POS",
    "tipo_contratacao": "Tipo de contratação",
    "retroatividade": "Retroatividade",
    "ambito_territorial": "Âmbito territorial",
    "prazo_complementar": "Prazo complementar / adicional",
    "processo_susep": "Processo SUSEP",
}

CAMPOS_LISTA: dict[str, str] = {
    "coberturas": "Coberturas",
    "extensoes": "Extensões",
    "exclusoes": "Exclusões",
    "clausulas_relevantes": "Cláusulas relevantes",
}


class ApoliceExtraida(BaseModel):
    """JSON padronizado de uma apólice D&O."""

    tipo_documento: Campo = Field(default_factory=Campo)
    seguradora: Campo = Field(default_factory=Campo)
    seguradora_anterior: Campo = Field(default_factory=Campo)
    segurado: Campo = Field(default_factory=Campo)
    cnpj_segurado: Campo = Field(default_factory=Campo)
    numero_apolice: Campo = Field(default_factory=Campo)
    corretor: Campo = Field(default_factory=Campo)
    vigencia_inicio: Campo = Field(default_factory=Campo)
    vigencia_fim: Campo = Field(default_factory=Campo)
    premio_liquido: Campo = Field(default_factory=Campo)
    iof: Campo = Field(default_factory=Campo)
    premio_total: Campo = Field(default_factory=Campo)
    lmg: Campo = Field(default_factory=Campo)
    lmi: Campo = Field(default_factory=Campo)
    franquia: Campo = Field(default_factory=Campo)
    tipo_contratacao: Campo = Field(default_factory=Campo)
    retroatividade: Campo = Field(default_factory=Campo)
    ambito_territorial: Campo = Field(default_factory=Campo)
    prazo_complementar: Campo = Field(default_factory=Campo)
    processo_susep: Campo = Field(default_factory=Campo)

    coberturas: list[ItemApolice] = Field(default_factory=list)
    extensoes: list[ItemApolice] = Field(default_factory=list)
    exclusoes: list[ItemApolice] = Field(default_factory=list)
    clausulas_relevantes: list[ItemApolice] = Field(default_factory=list)
    observacoes: list[str] = Field(default_factory=list)

    # Metadados do processamento (não vêm do documento)
    metodo_extracao: Literal["llm", "regras", "nenhum"] = "nenhum"
    modelo_llm: str | None = None
    normalizado: bool = False
    avisos: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ComparisonAgent
# ---------------------------------------------------------------------------


class StatusCampo(str, Enum):
    IGUAL = "igual"
    ALTERADO = "alterado"
    SOMENTE_A = "somente_a"  # identificado só no documento A
    SOMENTE_B = "somente_b"
    NAO_IDENTIFICADO = "nao_identificado"  # ausente nos dois


class StatusItem(str, Enum):
    MANTIDO = "mantido"
    ALTERADO = "alterado"  # existe nos dois, mas limite/franquia mudou
    ADICIONADO = "adicionado"  # existe só em B
    REMOVIDO = "removido"  # existe só em A
    SEM_BASE = "sem_base_comparacao"  # a lista não foi identificada em um dos documentos


class DiferencaCampo(BaseModel):
    secao: str
    campo: str
    rotulo: str
    valor_a: Any = None
    valor_b: Any = None
    texto_a: str | None = None
    texto_b: str | None = None
    status: StatusCampo
    variacao_percentual: float | None = None
    fonte_a: str | None = None
    fonte_b: str | None = None


class DiferencaItem(BaseModel):
    secao: str  # Coberturas, Extensões, ...
    chave: str
    nome: str
    nome_a: str | None = None
    nome_b: str | None = None
    limite_a: str | None = None
    limite_b: str | None = None
    franquia_a: str | None = None
    franquia_b: str | None = None
    status: StatusItem
    fonte_a: str | None = None
    fonte_b: str | None = None


class ResultadoComparacao(BaseModel):
    rotulo_a: str
    rotulo_b: str
    campos: list[DiferencaCampo] = Field(default_factory=list)
    itens: list[DiferencaItem] = Field(default_factory=list)
    contagem: dict[str, int] = Field(default_factory=dict)
    avisos: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# AnalysisAgent
# ---------------------------------------------------------------------------

RESSALVA_PADRAO = (
    "Análise gerada para fins acadêmicos (Projeto Final InsurMinds/I2A2). "
    "Não constitui recomendação de contratação e não substitui a avaliação de "
    "corretor de seguros, da seguradora ou parecer jurídico."
)


class AnaliseExecutiva(BaseModel):
    gerado_por_ia: bool = False
    modelo_llm: str | None = None
    sintese: str | None = None
    ganhos: list[str] = Field(default_factory=list)
    perdas: list[str] = Field(default_factory=list)
    pontos_atencao: list[str] = Field(default_factory=list)
    # Fatos calculados por regras a partir da comparação (sempre disponíveis, SEM IA)
    destaques_regras: list[str] = Field(default_factory=list)
    ressalva: str = RESSALVA_PADRAO
    erro: str | None = None


# ---------------------------------------------------------------------------
# OrchestratorAgent
# ---------------------------------------------------------------------------


class StatusEtapa(str, Enum):
    PENDENTE = "pendente"
    EXECUTANDO = "executando"
    SUCESSO = "sucesso"
    ALERTA = "alerta"  # concluiu, mas com ressalvas (ex.: fallback sem IA)
    ERRO = "erro"


class Etapa(BaseModel):
    agente: str
    descricao: str
    status: StatusEtapa = StatusEtapa.PENDENTE
    mensagem: str | None = None
    inicio: datetime | None = None
    duracao_s: float | None = None


class ResultadoPipeline(BaseModel):
    etapas: list[Etapa] = Field(default_factory=list)
    documentos: list[DocumentoProcessado | None] = Field(default_factory=list)
    apolices: list[ApoliceExtraida | None] = Field(default_factory=list)
    comparacao: ResultadoComparacao | None = None
    analise: AnaliseExecutiva | None = None
    sucesso: bool = False
    erro_fatal: str | None = None
