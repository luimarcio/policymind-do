"""AnalysisAgent — transforma a comparação estruturada numa análise executiva.

Usa IA generativa para redigir, em linguagem clara, a síntese, os ganhos, as
perdas e os pontos de atenção. O LLM recebe SOMENTE o resultado da comparação
(não o documento inteiro), o que reduz custo e risco de alucinação.

Independentemente da IA, o agente calcula "destaques por regras" (fatos objetivos
derivados da comparação). Se a OpenAI estiver indisponível, apenas esses
destaques são exibidos — e a interface informa que NÃO houve IA generativa.
"""

from __future__ import annotations

import json
import logging

from src.config import Settings, get_settings
from src.llm_client import LLMClient, LLMError
from src.schemas import AnaliseExecutiva, ResultadoComparacao, StatusCampo, StatusItem
from src.text_utils import format_date_br, format_money_br

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você é um consultor técnico de seguros D&O que escreve para executivos
não especialistas. Você receberá a COMPARAÇÃO ESTRUTURADA entre duas apólices (A e B).

REGRAS:
1. Baseie-se exclusivamente nos dados recebidos. Não acrescente coberturas, valores ou
   condições que não estejam na comparação.
2. Cite valores exatamente como recebidos (R$, datas, percentuais).
3. NÃO afirme que uma apólice é "melhor" ou "pior" juridicamente, nem recomende contratação.
   Use termos como "ampliação", "redução", "mudança" e "ponto de atenção".
4. "ganhos": mudanças de A para B que ampliam proteção ou reduzem custo.
   "perdas": mudanças que reduzem proteção ou aumentam custo.
   "pontos_atencao": informações ausentes, comparações sem base, divergências que exigem
   leitura do documento original ou validação com corretor/seguradora.
5. Se uma informação estiver "não identificada", diga isso — nunca suponha o valor.
   Campos ou itens com status "somente_a", "somente_b" ou "sem_base_comparacao" NÃO são
   ganhos nem perdas: como não há o dado equivalente no outro documento, classifique-os
   SEMPRE em "pontos_atencao" (ex.: "LMG de R$ 5.000.000,00 identificado apenas em B;
   o LMG de A não consta no documento analisado").
6. Escreva em português do Brasil, frases curtas. Máximo de 6 itens por lista.
7. Responda apenas com JSON:
{"sintese": "parágrafo de 4 a 7 frases", "ganhos": [str], "perdas": [str], "pontos_atencao": [str]}"""


class AnalysisAgent:
    nome = "AnalysisAgent"

    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)

    def analyze(self, comp: ResultadoComparacao) -> AnaliseExecutiva:
        analise = AnaliseExecutiva(destaques_regras=self.rule_highlights(comp))
        try:
            payload = json.dumps(self.build_payload(comp), ensure_ascii=False, default=str)
            bruto = self.llm.complete_json(SYSTEM_PROMPT, f"COMPARAÇÃO ESTRUTURADA:\n{payload}")
        except LLMError as exc:
            analise.erro = str(exc)
            return analise
        analise.gerado_por_ia = True
        analise.modelo_llm = self.llm.model
        analise.sintese = _str(bruto.get("sintese"))
        analise.ganhos = _lista(bruto.get("ganhos"))
        analise.perdas = _lista(bruto.get("perdas"))
        analise.pontos_atencao = _lista(bruto.get("pontos_atencao"))
        if not analise.sintese:
            analise.erro = "O modelo respondeu sem a síntese executiva."
        return analise

    # --------------------------------------------------------------- payload
    @staticmethod
    def build_payload(comp: ResultadoComparacao) -> dict:
        """Versão compacta da comparação para o LLM (sem trechos longos)."""

        def fmt(campo: str, v, texto):
            if v is None:
                return texto or "não identificado"
            if campo.startswith("vigencia"):
                return format_date_br(v)
            if isinstance(v, (int, float)):
                return format_money_br(v)
            return v

        campos = [
            {
                "secao": d.secao,
                "campo": d.rotulo,
                "A": fmt(d.campo, d.valor_a, d.texto_a),
                "B": fmt(d.campo, d.valor_b, d.texto_b),
                "status": d.status.value,
                **({"variacao_percentual": d.variacao_percentual} if d.variacao_percentual is not None else {}),
            }
            for d in comp.campos
            if d.status != StatusCampo.NAO_IDENTIFICADO
        ]
        itens = [
            {
                "secao": i.secao,
                "item": i.nome,
                "status": i.status.value,
                **({"limite_A": i.limite_a} if i.limite_a else {}),
                **({"limite_B": i.limite_b} if i.limite_b else {}),
            }
            for i in comp.itens
            if i.status != StatusItem.MANTIDO
        ]
        mantidos = [i.nome for i in comp.itens if i.status == StatusItem.MANTIDO]
        return {
            "apolice_A": comp.rotulo_a,
            "apolice_B": comp.rotulo_b,
            "campos": campos,
            "itens_com_diferenca": itens[:120],
            "itens_mantidos": mantidos[:80],
            "contagem": comp.contagem,
            "avisos": comp.avisos,
        }

    # --------------------------------------------------- destaques sem IA
    @staticmethod
    def rule_highlights(comp: ResultadoComparacao) -> list[str]:
        out: list[str] = []
        por_campo = {d.campo: d for d in comp.campos}

        seg = por_campo.get("seguradora")
        if seg and seg.status == StatusCampo.ALTERADO:
            out.append(f"Troca de seguradora: {seg.valor_a} → {seg.valor_b}.")
        for campo in ("premio_total", "premio_liquido", "lmg", "lmi"):
            d = por_campo.get(campo)
            if d and d.status == StatusCampo.ALTERADO and d.variacao_percentual is not None:
                sinal = "+" if d.variacao_percentual > 0 else ""
                pct = f"{d.variacao_percentual:.2f}".replace(".", ",")
                out.append(f"{d.rotulo}: {format_money_br(d.valor_a)} → {format_money_br(d.valor_b)} ({sinal}{pct}%).")
        for d in comp.campos:
            if d.status == StatusCampo.SOMENTE_A:
                out.append(f"{d.rotulo}: identificado apenas em {comp.rotulo_a}.")
            elif d.status == StatusCampo.SOMENTE_B:
                out.append(f"{d.rotulo}: identificado apenas em {comp.rotulo_b}.")
        fr = por_campo.get("franquia")
        if fr and fr.status == StatusCampo.ALTERADO:
            out.append(f"Franquia/POS mudou: '{fr.texto_a or fr.valor_a}' → '{fr.texto_b or fr.valor_b}'.")

        c = _contar_itens(comp)
        if c["adicionado"]:
            out.append(
                f"{c['adicionado']} cobertura(s)/extensão(ões)/exclusão(ões) presentes apenas em {comp.rotulo_b}."
            )
        if c["removido"]:
            out.append(f"{c['removido']} item(ns) presentes apenas em {comp.rotulo_a}.")
        if c["alterado"]:
            out.append(f"{c['alterado']} item(ns) com limite ou franquia diferentes.")
        if c["mantido"]:
            out.append(f"{c['mantido']} item(ns) presentes nas duas apólices sem diferença de limite.")
        out.extend(comp.avisos)
        return out


def _contar_itens(comp: ResultadoComparacao) -> dict[str, int]:
    contagem = {s.value: 0 for s in StatusItem}
    for item in comp.itens:
        contagem[item.status.value] += 1
    return contagem


def _str(v) -> str | None:
    return str(v).strip() if v else None


def _lista(v) -> list[str]:
    if isinstance(v, str):
        return [v.strip()] if v.strip() else []
    if isinstance(v, list):
        return [str(x).strip() for x in v if x and str(x).strip()]
    return []
