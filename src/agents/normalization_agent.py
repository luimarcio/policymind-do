"""NormalizationAgent — coloca apólices de seguradoras diferentes numa linguagem comum.

O que faz:
- converte valores monetários para número (float) e datas para ISO (AAAA-MM-DD),
  sem arredondar nem alterar o valor — o texto original é preservado;
- padroniza o nome da seguradora (ex.: "TOKIO MARINE SEGURADORA S.A." -> "Tokio Marine");
- mapeia sinônimos de coberturas/extensões/exclusões para uma CHAVE canônica
  (ex.: "Bloqueio de Conta Corrente (Penhora On-Line)" -> `penhora_bloqueio_contas`),
  permitindo comparar documentos com terminologias diferentes.

O que NÃO faz: não cria campos, não infere valores e não altera o texto original.
É um agente determinístico (dicionário de sinônimos) — rápido, auditável e testável.
"""

from __future__ import annotations

import re

from src.schemas import CAMPOS_LISTA, ApoliceExtraida, Campo, ItemApolice
from src.text_utils import normalize_for_match, parse_date_br, parse_money_br

CAMPOS_MONETARIOS = ["premio_liquido", "iof", "premio_total", "lmg", "lmi"]
CAMPOS_DATA = ["vigencia_inicio", "vigencia_fim"]

SEGURADORAS = [
    ("Tokio Marine", r"tokio marine"),
    ("AXA", r"\baxa\b"),
    ("Allianz", r"allianz"),
    ("Porto Seguro", r"\bporto\b"),
    ("Chubb", r"chubb"),
    ("Zurich", r"zurich"),
    ("AIG", r"\baig\b"),
    ("Sompo", r"sompo"),
    ("Fairfax", r"fairfax"),
    ("Liberty", r"liberty"),
    ("Mapfre", r"mapfre"),
    ("BTG Pactual", r"btg"),
    ("Swiss Re", r"swiss re"),
    ("Junto Seguros", r"junto"),
    ("Akad", r"akad"),
]

# (chave canônica, rótulo padronizado, padrões regex sobre o texto normalizado)
# A ordem importa: o primeiro padrão que casar define a chave.
CONCEITOS: list[tuple[str, str, str]] = [
    (
        "cobertura_a_administradores",
        "Cobertura A — pagamento direto aos administradores",
        r"^cobertura a\b|side a|pagamento ao administrador|responsabilidade civil (dos|de) administradores",
    ),
    (
        "cobertura_b_reembolso_empresa",
        "Cobertura B — reembolso à empresa",
        r"^cobertura b\b|side b|reembolso a empresa",
    ),
    (
        "cobertura_c_entidade",
        "Cobertura C — reclamações contra a empresa (mercado de capitais)",
        r"^cobertura c\b|side c|mercado de capitais",
    ),
    ("custos_emergenciais", "Custos emergenciais", r"(custos|despesas) emergenciais"),
    ("custos_investigacao", "Custos de investigação", r"investigac"),
    ("confisco_bens", "Confisco de bens / bens e liberdade", r"confisco|bens e liberdade"),
    ("extradicao_deportacao", "Extradição / deportação / restrição de liberdade", r"extradic|deportac"),
    ("penhora_bloqueio_contas", "Bloqueio de contas / penhora on-line", r"penhora|bloqueio"),
    ("indisponibilidade_bens", "Indisponibilidade de bens", r"indisponibilidade"),
    ("danos_morais", "Danos morais", r"danos morais"),
    ("danos_materiais_corporais", "Danos materiais e corporais", r"danos materiais|danos corporais"),
    ("praticas_trabalhistas", "Práticas trabalhistas", r"praticas trabalhistas|trabalhist"),
    ("erros_omissoes", "Erros e omissões", r"erros e omissoes"),
    (
        "prazo_aposentados_demissao",
        "Prazo adicional — aposentados / demissão voluntária",
        r"aposentad|demiss(ao|oes) voluntaria",
    ),
    ("subsidiarias", "Subsidiárias e novas subsidiárias", r"subsidiaria"),
    ("advogados_internos", "Advogados internos", r"advogados internos"),
    ("despesas_salvamento", "Despesas de salvamento / contenção de sinistros", r"salvamento"),
    (
        "contadores_auditores",
        "Contadores, auditores internos e risk manager",
        r"contadores|auditores internos|risk manager",
    ),
    ("entidade_externa", "Entidade externa", r"entidade externa"),
    ("administrador_motivos_legais", "Administrador por motivos legais", r"motivos legais"),
    ("responsabilidade_tributaria", "Responsabilidade tributária", r"tribut"),
    ("multas_penalidades", "Multas e penalidades", r"multas"),
    ("gerenciamento_crise", "Gerenciamento de crise", r"\bcrise\b"),
    ("inabilitacao", "Inabilitação do administrador", r"inabilitac"),
    ("publicidade_imagem", "Publicidade / proteção da imagem", r"publicidade|imagem pessoal|reputac"),
    ("garantias_pessoais", "Aval, fiança e garantias pessoais", r"\baval\b|fianca|garantias pessoais"),
    ("tac_termo_compromisso", "TAC / termo de compromisso", r"ajustamento de conduta|termo de compromisso"),
    ("acionista_majoritario", "Reclamações de acionista majoritário", r"acionista majoritario"),
    ("empresa_contra_administrador", "Empresa contra administrador", r"empresa contra administrador"),
    ("administrador_contra_administrador", "Administrador contra administrador", r"administrador contra administrador"),
    ("processos_existentes", "Processos existentes contra a empresa", r"processos existentes"),
    ("conjuge_herdeiros", "Cônjuge, herdeiros e espólio", r"conjuge|herdeiro|espolio"),
    ("ambiental", "Responsabilidade ambiental / poluição", r"ambiental|poluic"),
    ("especialistas", "Gastos adicionais com especialistas", r"especialistas"),
    ("orgaos_reguladores", "Eventos com órgãos reguladores", r"reguladores"),
    ("suporte_administrador", "Custos de suporte ao administrador", r"suporte ao administrador"),
    ("responsabilidade_solidaria", "Responsabilidade solidária de bens", r"solidaria"),
    ("responsabilidade_estatutaria", "Responsabilidade estatutária", r"estatutaria"),
    ("apolice_internacional", "Opção de apólice internacional", r"apolice internacional"),
    ("custos_defesa", "Custos de defesa", r"custos de defesa"),
]

EXCLUSOES: list[tuple[str, str, str]] = [
    ("excl_dolo_fraude", "Atos dolosos / fraude / má-fé", r"dolo|fraud|ma fe|intencional|criminos"),
    (
        "excl_vantagem_indevida",
        "Vantagem pessoal indevida / remuneração ilegal",
        r"vantagem|remuneracao|beneficio pessoal|lucro",
    ),
    (
        "excl_fatos_anteriores",
        "Fatos/reclamações anteriores ou conhecidos",
        r"anterior|previa|conhecid|pre existente|pendente",
    ),
    ("excl_danos_corporais_materiais", "Danos corporais e materiais", r"danos corporais|danos materiais|lesao"),
    ("excl_poluicao", "Poluição / danos ambientais", r"poluic|ambient"),
    ("excl_multas", "Multas e penalidades", r"multa|penalidade"),
    ("excl_eua_canada", "Reclamações nos EUA/Canadá", r"eua|estados unidos|canada"),
    ("excl_guerra_terrorismo", "Guerra / terrorismo", r"guerra|terroris"),
    ("excl_nuclear", "Riscos nucleares", r"nuclear|radioativ"),
    ("excl_contratual", "Obrigações contratuais / garantias", r"contratua|obrigac(ao|oes) economicas"),
    ("excl_tributos", "Tributos", r"tribut|impost"),
    ("excl_trabalhista", "Obrigações trabalhistas / previdenciárias", r"trabalhist|previdenc"),
    ("excl_seguradora_outra", "Coberto por outro seguro", r"outro seguro|outra apolice"),
    ("excl_segurado_vs_segurado", "Reclamação de segurado contra segurado", r"segurado contra segurado|insured"),
    ("excl_cyber", "Riscos cibernéticos / dados", r"ciber|cyber|dados pessoais|lgpd"),
]

_PREFIXOS = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?(?:extensao de cobertura (?:para|de)\s+|clausula particular (?:de|para)?\s*|"
    r"cobertura (?:adicional )?(?:para|de)\s+)"
)


class NormalizationAgent:
    nome = "NormalizationAgent"

    def normalize(self, apolice: ApoliceExtraida) -> ApoliceExtraida:
        a = apolice.model_copy(deep=True)
        self._normalize_insurer(a)
        for campo in CAMPOS_MONETARIOS:
            self._normalize_money(a, campo)
        for campo in CAMPOS_DATA:
            self._normalize_date(a, campo)
        for campo in CAMPOS_LISTA:
            dicionario = EXCLUSOES if campo == "exclusoes" else CONCEITOS
            setattr(a, campo, self._normalize_items(getattr(a, campo), dicionario))
        a.normalizado = True
        return a

    # ---------------------------------------------------------- escalares
    @staticmethod
    def canonical_insurer(nome: str | None) -> str | None:
        if not nome:
            return None
        t = normalize_for_match(nome)
        for canonico, padrao in SEGURADORAS:
            if re.search(padrao, t):
                return canonico
        return nome.strip()

    def _normalize_insurer(self, a: ApoliceExtraida) -> None:
        for campo in ("seguradora", "seguradora_anterior"):
            c: Campo = getattr(a, campo)
            if c.valor is None and not c.texto_original:
                continue
            original = c.texto_original or str(c.valor)
            c.texto_original = original
            c.valor = self.canonical_insurer(str(c.valor or original))

    def _normalize_money(self, a: ApoliceExtraida, campo: str) -> None:
        c: Campo = getattr(a, campo)
        if c.valor is None and not c.texto_original:
            return
        do_texto = parse_money_br(c.texto_original)
        do_valor = parse_money_br(c.valor)
        if do_texto is not None:
            if do_valor is not None and abs(do_texto - do_valor) > 0.005:
                a.avisos.append(
                    f"{campo}: valor numérico ({do_valor}) diverge do texto original "
                    f"('{c.texto_original}'); prevaleceu o texto original."
                )
            c.valor = do_texto
        elif do_valor is not None:
            c.valor = do_valor
        else:
            # Ex.: "100% do LMI" — não é um valor monetário; mantém só o texto.
            c.texto_original = c.texto_original or str(c.valor)
            c.valor = None

    def _normalize_date(self, a: ApoliceExtraida, campo: str) -> None:
        c: Campo = getattr(a, campo)
        if c.valor is None and not c.texto_original:
            return
        iso = parse_date_br(c.valor) or parse_date_br(c.texto_original)
        if iso is None:
            a.avisos.append(f"{campo}: data '{c.texto_original or c.valor}' não reconhecida; mantida como texto.")
            c.texto_original = c.texto_original or str(c.valor)
        c.valor = iso

    # -------------------------------------------------------------- listas
    @staticmethod
    def clean_name(nome: str) -> str:
        t = normalize_for_match(nome)
        return _PREFIXOS.sub("", t).strip()

    @classmethod
    def canonical_item(cls, nome: str, dicionario=CONCEITOS) -> tuple[str, str] | None:
        t = cls.clean_name(nome)
        for chave, rotulo, padrao in dicionario:
            if re.search(padrao, t):
                return chave, rotulo
        return None

    def _normalize_items(self, itens: list[ItemApolice], dicionario) -> list[ItemApolice]:
        usadas: set[str] = set()
        resultado = []
        for item in itens:
            novo = item.model_copy()
            achado = self.canonical_item(item.nome, dicionario)
            slug = self.clean_name(item.nome).replace(" ", "_")[:60] or "item"
            if achado and achado[0] not in usadas:
                novo.chave, novo.nome_normalizado = achado
            else:
                # sem sinônimo conhecido (ou conceito já usado neste documento):
                # usa o próprio nome limpo como chave, preservando o original.
                novo.chave = slug if not achado else f"{achado[0]}__{slug}"
                novo.nome_normalizado = item.nome.strip()
            usadas.add(novo.chave)
            resultado.append(novo)
        return resultado
