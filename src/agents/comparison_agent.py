"""ComparisonAgent — compara duas apólices normalizadas de forma determinística.

Por que determinístico (sem LLM)? A comparação precisa ser exata, reproduzível e
auditável: o mesmo par de apólices deve gerar sempre o mesmo resultado. A IA
generativa entra depois, no AnalysisAgent, para explicar as diferenças.

Saída:
- campos escalares: igual / alterado / somente_a / somente_b / nao_identificado
- listas (coberturas, extensões, cláusulas, exclusões):
  mantido / alterado / adicionado / removido / sem_base_comparacao
"""

from __future__ import annotations

from collections import Counter

from src.schemas import (
    CAMPOS_ESCALARES,
    ApoliceExtraida,
    Campo,
    DiferencaCampo,
    DiferencaItem,
    ItemApolice,
    ResultadoComparacao,
    StatusCampo,
    StatusItem,
)
from src.text_utils import normalize_for_match

SECOES_CAMPOS: dict[str, list[str]] = {
    "Dados Gerais": [
        "tipo_documento",
        "seguradora",
        "seguradora_anterior",
        "segurado",
        "cnpj_segurado",
        "numero_apolice",
        "corretor",
        "vigencia_inicio",
        "vigencia_fim",
        "tipo_contratacao",
        "retroatividade",
        "ambito_territorial",
        "prazo_complementar",
        "processo_susep",
    ],
    "Prêmio": ["premio_liquido", "iof", "premio_total"],
    "Limites": ["lmg", "lmi"],
    "Franquias": ["franquia"],
}

# Coberturas, extensões e cláusulas particulares são comparadas num "pool" único:
# uma seguradora pode chamar de extensão o que outra chama de cláusula particular.
GRUPO_POSITIVO = {"coberturas": "Coberturas", "extensoes": "Extensões", "clausulas_relevantes": "Cláusulas relevantes"}
GRUPO_EXCLUSOES = {"exclusoes": "Exclusões"}


def _fonte(c: Campo | ItemApolice) -> str | None:
    partes = []
    if c.pagina:
        partes.append(f"p. {c.pagina}")
    if c.trecho:
        partes.append(f"“{c.trecho[:160]}”")
    return " — ".join(partes) or None


class ComparisonAgent:
    nome = "ComparisonAgent"

    def compare(
        self, a: ApoliceExtraida, b: ApoliceExtraida, rotulo_a: str = "Apólice A", rotulo_b: str = "Apólice B"
    ) -> ResultadoComparacao:
        res = ResultadoComparacao(rotulo_a=rotulo_a, rotulo_b=rotulo_b)
        for secao, campos in SECOES_CAMPOS.items():
            for campo in campos:
                res.campos.append(self.compare_field(secao, campo, getattr(a, campo), getattr(b, campo)))
        res.itens.extend(self.compare_items(a, b, GRUPO_POSITIVO, res.avisos))
        res.itens.extend(self.compare_items(a, b, GRUPO_EXCLUSOES, res.avisos))

        cont = Counter(f"campos_{d.status.value}" for d in res.campos)
        cont.update(f"itens_{d.status.value}" for d in res.itens)
        res.contagem = dict(cont)
        return res

    # ------------------------------------------------------------ escalares
    @staticmethod
    def compare_field(secao: str, campo: str, ca: Campo, cb: Campo) -> DiferencaCampo:
        va, vb = ca.valor, cb.valor
        tem_a = va is not None or bool(ca.texto_original)
        tem_b = vb is not None or bool(cb.texto_original)
        variacao = None
        if not tem_a and not tem_b:
            status = StatusCampo.NAO_IDENTIFICADO
        elif tem_a and not tem_b:
            status = StatusCampo.SOMENTE_A
        elif tem_b and not tem_a:
            status = StatusCampo.SOMENTE_B
        elif isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            status = StatusCampo.IGUAL if abs(va - vb) < 0.005 else StatusCampo.ALTERADO
            if va:
                variacao = round((vb - va) / va * 100, 2)
        else:
            ta = normalize_for_match(str(va if va is not None else ca.texto_original))
            tb = normalize_for_match(str(vb if vb is not None else cb.texto_original))
            status = StatusCampo.IGUAL if ta == tb else StatusCampo.ALTERADO
        return DiferencaCampo(
            secao=secao,
            campo=campo,
            rotulo=CAMPOS_ESCALARES.get(campo, campo),
            valor_a=va,
            valor_b=vb,
            texto_a=ca.texto_original,
            texto_b=cb.texto_original,
            status=status,
            variacao_percentual=variacao,
            fonte_a=_fonte(ca),
            fonte_b=_fonte(cb),
        )

    # --------------------------------------------------------------- listas
    @staticmethod
    def _index(ap: ApoliceExtraida, grupo: dict[str, str]) -> dict[str, tuple[str, ItemApolice]]:
        idx: dict[str, tuple[str, ItemApolice]] = {}
        for campo, secao in grupo.items():
            for item in getattr(ap, campo):
                chave = item.chave or normalize_for_match(item.nome).replace(" ", "_")
                idx.setdefault(chave, (secao, item))
        return idx

    def compare_items(
        self, a: ApoliceExtraida, b: ApoliceExtraida, grupo: dict[str, str], avisos: list[str]
    ) -> list[DiferencaItem]:
        ia, ib = self._index(a, grupo), self._index(b, grupo)
        nome_grupo = " / ".join(grupo.values())
        sem_base = bool(ia) != bool(ib)
        if sem_base:
            lado = "A" if not ia else "B"
            avisos.append(
                f"{nome_grupo}: nenhuma informação identificada no documento {lado}. "
                "Os itens do outro documento não são tratados como adicionados/removidos "
                "(sem base de comparação)."
            )
        saida: list[DiferencaItem] = []
        for chave in list(ia) + [k for k in ib if k not in ia]:
            sa, item_a = ia.get(chave, (None, None))
            sb, item_b = ib.get(chave, (None, None))
            if sem_base:
                status = StatusItem.SEM_BASE
            elif item_a and item_b:
                mudou = _diferente(item_a.limite, item_b.limite) or _diferente(item_a.franquia, item_b.franquia)
                status = StatusItem.ALTERADO if mudou else StatusItem.MANTIDO
            elif item_b:
                status = StatusItem.ADICIONADO
            else:
                status = StatusItem.REMOVIDO
            ref = item_b or item_a
            saida.append(
                DiferencaItem(
                    secao=sb or sa,
                    chave=chave,
                    nome=ref.nome_normalizado or ref.nome,
                    nome_a=item_a.nome if item_a else None,
                    nome_b=item_b.nome if item_b else None,
                    limite_a=item_a.limite if item_a else None,
                    limite_b=item_b.limite if item_b else None,
                    franquia_a=item_a.franquia if item_a else None,
                    franquia_b=item_b.franquia if item_b else None,
                    status=status,
                    fonte_a=_fonte(item_a) if item_a else None,
                    fonte_b=_fonte(item_b) if item_b else None,
                )
            )
        return saida


def _diferente(x: str | None, y: str | None) -> bool:
    """Só considera alteração quando os DOIS lados informam o dado."""
    if x is None or y is None:
        return False
    return normalize_for_match(x) != normalize_for_match(y)
