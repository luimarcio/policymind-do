"""OrchestratorAgent — coordena o fluxo completo entre os agentes especializados.

Upload → DocumentAgent → PolicyExtractionAgent → NormalizationAgent
       → ComparisonAgent → AnalysisAgent → Interface

Responsabilidades:
- executar as etapas na ordem e registrar o status de cada uma (com duração);
- aplicar a política de falhas: um erro é registrado e devolvido à interface,
  nunca derruba a aplicação;
- decidir o uso de cache (SQLite) e do fallback por regras quando a IA falha.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from src.agents.analysis_agent import AnalysisAgent
from src.agents.comparison_agent import ComparisonAgent
from src.agents.document_agent import DocumentAgent, DocumentError
from src.agents.normalization_agent import NormalizationAgent
from src.agents.policy_extraction_agent import PolicyExtractionAgent
from src.config import Settings, get_settings
from src.llm_client import LLMClient, LLMError
from src.schemas import ApoliceExtraida, DocumentoProcessado, Etapa, ResultadoPipeline, StatusEtapa
from src.storage import PolicyStore
from src.text_utils import parse_date_br

logger = logging.getLogger(__name__)

ETAPAS = [
    ("DocumentAgent", "Leitura do documento (texto nativo ou OCR)"),
    ("PolicyExtractionAgent", "Extração estruturada com IA generativa"),
    ("NormalizationAgent", "Padronização de termos e valores"),
    ("ComparisonAgent", "Comparação campo a campo e item a item"),
    ("AnalysisAgent", "Síntese executiva com IA generativa"),
]


class PipelineAbort(Exception):
    """Interrompe o fluxo após uma etapa com erro (já registrado)."""


class OrchestratorAgent:
    nome = "OrchestratorAgent"

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMClient | None = None,
        store: PolicyStore | None = None,
        on_update: Callable[[list[Etapa]], None] | None = None,
    ):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)
        self.document_agent = DocumentAgent(self.settings)
        self.extraction_agent = PolicyExtractionAgent(self.settings, self.llm)
        self.normalization_agent = NormalizationAgent()
        self.comparison_agent = ComparisonAgent()
        self.analysis_agent = AnalysisAgent(self.settings, self.llm)
        self.store = store
        if self.store is None and self.settings.cache_enabled:
            try:
                self.store = PolicyStore(self.settings.cache_path)
            except Exception as exc:  # sem cache, o fluxo continua
                logger.warning("Cache SQLite indisponível: %s", exc)
        self.on_update = on_update

    # ================================================================== run
    def run(self, arquivos: list[tuple[str, bytes]]) -> ResultadoPipeline:
        res = ResultadoPipeline(etapas=[Etapa(agente=a, descricao=d) for a, d in ETAPAS])
        if len(arquivos) < 2:
            res.erro_fatal = "Envie pelo menos dois documentos para comparar."
            self._set(res, 0, StatusEtapa.ERRO, res.erro_fatal)
            return res
        try:
            docs = self._step(res, 0, lambda: self._read_all(arquivos))
            res.documentos = docs
            apolices = self._step(res, 1, lambda: self._extract_all(res, docs))
            normalizadas = self._step(
                res,
                2,
                lambda: [self.normalization_agent.normalize(a) for a in apolices],
                ok_msg="Termos, datas e valores padronizados.",
            )
            res.apolices = normalizadas
            ra, rb = (self.label(normalizadas[i], docs[i]) for i in (0, 1))
            comp = self._step(res, 3, lambda: self.comparison_agent.compare(normalizadas[0], normalizadas[1], ra, rb))
            res.comparacao = comp
            res.analise = self._step(res, 4, lambda: self._analyze(res, comp))
            res.sucesso = True
        except PipelineAbort:
            res.sucesso = False
        return res

    # ============================================================== etapas
    def _read_all(self, arquivos) -> list[DocumentoProcessado]:
        docs = []
        for nome, conteudo in arquivos:
            try:
                docs.append(self.document_agent.process(nome, conteudo))
            except DocumentError as exc:
                raise DocumentError(f"{nome}: {exc}") from exc
        resumo = "; ".join(f"{d.nome_arquivo} ({d.num_paginas} pág., {d.metodo_extracao})" for d in docs)
        falhas = [d.nome_arquivo for d in docs if d.metodo_extracao == "falhou"]
        if falhas:
            raise DocumentError(f"Nenhum texto extraído de: {', '.join(falhas)}")
        self._msg_ok = resumo
        return docs

    def _extract_all(self, res: ResultadoPipeline, docs: list[DocumentoProcessado]) -> list[ApoliceExtraida]:
        apolices, mensagens, motivos, alerta = [], [], [], False
        for doc in docs:
            apolice, msg, motivo = self._extract_one(doc)
            apolices.append(apolice)
            mensagens.append(f"{doc.nome_arquivo}: {msg}")
            if motivo:
                alerta = True
                if motivo not in motivos:
                    motivos.append(motivo)
        self._msg_ok = " | ".join(mensagens) + (f" — Motivo: {'; '.join(motivos)}" if motivos else "")
        self._alerta = alerta
        return apolices

    def _extract_one(self, doc: DocumentoProcessado) -> tuple[ApoliceExtraida, str, str | None]:
        """Retorna (apólice, descrição do método, motivo do fallback ou None)."""
        modelo = self.llm.model
        # 1) cache de uma extração anterior feita pelo LLM
        if self.store:
            cached = self.store.get(doc.hash_sha256, modelo)
            if cached:
                cached.avisos.append(f"Resultado recuperado do cache local (extraído anteriormente por {modelo}).")
                return cached, f"cache ({modelo})", None
        # 2) LLM
        try:
            apolice = self.extraction_agent.extract(doc)
            if self.store:
                self.store.save(doc.hash_sha256, modelo, doc.nome_arquivo, apolice)
            return apolice, f"extraído por IA ({modelo})", None
        except LLMError as exc:
            if not self.settings.allow_rule_fallback:
                raise
            # 3) fallback por regras — explicitamente SEM IA
            apolice = self.extraction_agent.extract_with_rules(doc)
            apolice.avisos.insert(0, f"IA generativa indisponível: {exc}")
            return apolice, "extração por REGRAS (sem IA)", str(exc)

    def _analyze(self, res: ResultadoPipeline, comp):
        analise = self.analysis_agent.analyze(comp)
        if analise.gerado_por_ia:
            self._msg_ok = f"Síntese gerada por IA ({analise.modelo_llm})."
            self._alerta = False
        else:
            motivo = (analise.erro or "motivo desconhecido").rstrip(".")
            self._msg_ok = f"Síntese por IA NÃO gerada: {motivo}. Exibindo apenas destaques por regras."
            self._alerta = True
        return analise

    # ============================================================ controle
    def _step(self, res: ResultadoPipeline, idx: int, fn: Callable, ok_msg: str | None = None):
        self._msg_ok, self._alerta = ok_msg, False
        etapa = res.etapas[idx]
        etapa.status, etapa.inicio = StatusEtapa.EXECUTANDO, datetime.now()
        self._notify(res)
        t0 = time.perf_counter()
        try:
            out = fn()
        except Exception as exc:  # qualquer falha vira status de erro, sem derrubar a app
            logger.exception("Falha em %s", etapa.agente)
            etapa.duracao_s = round(time.perf_counter() - t0, 2)
            res.erro_fatal = f"{etapa.agente}: {exc}"
            self._set(res, idx, StatusEtapa.ERRO, str(exc))
            raise PipelineAbort from exc
        etapa.duracao_s = round(time.perf_counter() - t0, 2)
        self._set(res, idx, StatusEtapa.ALERTA if self._alerta else StatusEtapa.SUCESSO, self._msg_ok or "Concluído.")
        return out

    def _set(self, res: ResultadoPipeline, idx: int, status: StatusEtapa, msg: str | None) -> None:
        res.etapas[idx].status = status
        res.etapas[idx].mensagem = msg
        self._notify(res)

    def _notify(self, res: ResultadoPipeline) -> None:
        if self.on_update:
            try:
                self.on_update(res.etapas)
            except Exception:  # a UI nunca deve quebrar o pipeline
                logger.debug("on_update falhou", exc_info=True)

    # ================================================================ util
    @staticmethod
    def label(ap: ApoliceExtraida, doc: DocumentoProcessado) -> str:
        """Rótulo legível: 'Tokio Marine 2024–2025' ou o nome do arquivo."""
        seg = ap.seguradora.valor
        ini = parse_date_br(ap.vigencia_inicio.valor)
        fim = parse_date_br(ap.vigencia_fim.valor)
        if seg and ini and fim:
            return f"{seg} {ini[:4]}–{fim[:4]}"
        if seg:
            return f"{seg} ({doc.nome_arquivo[:30]})"
        return doc.nome_arquivo[:40]
