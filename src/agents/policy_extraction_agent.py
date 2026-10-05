"""PolicyExtractionAgent — usa IA generativa (LLM) para extrair o JSON da apólice.

Fluxo:
1. Seleciona o texto a enviar ao LLM (documentos longos são reduzidos às
   páginas mais relevantes, sempre preservando as primeiras páginas).
2. Envia o texto com marcadores [PÁGINA n] e um prompt com regras rígidas:
   nada de inventar dados; campo ausente = null; citar trecho e página.
3. Valida a resposta com Pydantic.
4. Guardrail anti-alucinação: confere se cada evidência citada existe de fato
   no documento. Valor sem evidência localizável é descartado (vira null).

Se a OpenAI estiver indisponível e ALLOW_RULE_FALLBACK=true, aplica uma
extração mínima por expressões regulares — sempre marcada como
`metodo_extracao="regras"` (SEM IA) para que a interface deixe isso claro.
"""

from __future__ import annotations

import logging
import re

from src.config import Settings, get_settings
from src.llm_client import LLMClient, LLMError
from src.schemas import CAMPOS_ESCALARES, CAMPOS_LISTA, ApoliceExtraida, Campo, DocumentoProcessado, ItemApolice
from src.text_utils import evidence_in_text, normalize_for_match

logger = logging.getLogger(__name__)

PALAVRAS_CHAVE = [
    "apolice",
    "seguradora",
    "segurado",
    "tomador",
    "vigencia",
    "premio",
    "iof",
    "limite maximo",
    "lmg",
    "lmi",
    "franquia",
    "participacao obrigatoria",
    "retroatividade",
    "reclamacao",
    "cobertura",
    "extensao",
    "exclusao",
    "exclusoes",
    "clausula",
    "ambito",
    "prazo complementar",
    "prazo adicional",
    "sublimite",
    "especificacao",
    "condicoes particulares",
]

# Padrões que indicam página de especificação com dados contratuais essenciais
PAGINA_CRITICA = re.compile(
    r"limite m[áa]ximo de garantia[^\n]{0,60}R\$"
    r"|^\s*\d+\.\s*FRANQUIAS?\b"
    r"|franquia[^\n]{0,60}:\s*n[ãa]o h[áa]"
    r"|\d{1,3}% do LMI",
    re.IGNORECASE | re.MULTILINE,
)

SYSTEM_PROMPT = """Você é um analista técnico de seguros especializado em apólices D&O
(Responsabilidade Civil de Administradores) no Brasil. Sua tarefa é EXTRAIR dados
de um documento, sem interpretar além do que está escrito.

REGRAS OBRIGATÓRIAS:
1. Use SOMENTE o texto fornecido. Nunca use conhecimento externo para preencher campos.
2. Se uma informação não estiver no texto, use null. Nunca estime, deduza ou complete.
3. Para cada campo preenchido, informe "texto_original" (exatamente como aparece),
   "pagina" (número do marcador [PÁGINA n]) e "trecho" (cópia literal de até 200
   caracteres do documento que comprova o valor).
4. Valores monetários: "valor" como número decimal com ponto (ex.: 10880.98) e
   "texto_original" como no documento (ex.: "R$ 10.880,98").
5. Datas: "valor" no formato AAAA-MM-DD.
6. "VALOR DO CAPITAL SUBSCRITO/REALIZADO" é o capital social da SEGURADORA, NÃO é LMG nem LMI.
7. "lmg" = limite máximo de garantia/agregado da apólice. "lmi" só se houver um limite
   máximo de indenização único declarado; limites por cobertura (ex.: "100% do LMI")
   vão no campo "limite" de cada item.
8. "franquia": se o documento disser que não há franquia, copie essa frase em "valor".
9. Se o documento for de Condições Gerais (modelo do produto, sem segurado/valores),
   preencha tipo_documento="condicoes_gerais" e deixe os campos contratuais como null.
10. Listas: inclua no máximo 40 itens por lista, com o nome como aparece no documento.
    Exclusões podem ser resumidas em uma frase curta em "descricao".
11. Responda APENAS com um objeto JSON válido no formato solicitado."""

FORMATO_JSON = """Formato de resposta (JSON):
{
  "tipo_documento":    {"valor": "apolice" | "condicoes_gerais" | "outro" | null, "texto_original": null, "pagina": null, "trecho": null},
  "seguradora":        {"valor": str|null, "texto_original": str|null, "pagina": int|null, "trecho": str|null},
  "seguradora_anterior": {...mesmo formato...},
  "segurado":          {...}, "cnpj_segurado": {...}, "numero_apolice": {...}, "corretor": {...},
  "vigencia_inicio":   {...}, "vigencia_fim": {...},
  "premio_liquido":    {...}, "iof": {...}, "premio_total": {...},
  "lmg": {...}, "lmi": {...}, "franquia": {...},
  "tipo_contratacao":  {...  ex.: "à base de reclamações com notificação" },
  "retroatividade": {...}, "ambito_territorial": {...}, "prazo_complementar": {...}, "processo_susep": {...},
  "coberturas":  [{"nome": str, "limite": str|null, "franquia": str|null, "descricao": str|null, "pagina": int|null, "trecho": str|null}],
  "extensoes":   [ ...mesmo formato... ],
  "exclusoes":   [ ...mesmo formato... ],
  "clausulas_relevantes": [ ...mesmo formato (condições/cláusulas particulares, sublimites)... ],
  "observacoes": [str]   // ex.: "documento referencia especificações anexas não incluídas"
}"""


class PolicyExtractionAgent:
    nome = "PolicyExtractionAgent"

    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)

    # ------------------------------------------------------------------ API
    def extract(self, doc: DocumentoProcessado) -> ApoliceExtraida:
        """Extrai via LLM. Lança LLMError se a IA estiver indisponível."""
        texto, avisos = self.select_text(doc)
        user_prompt = f"{FORMATO_JSON}\n\nDOCUMENTO: {doc.nome_arquivo}\n\n{texto}"
        bruto = self.llm.complete_json(SYSTEM_PROMPT, user_prompt)
        apolice = self.parse_llm_output(bruto)
        apolice.metodo_extracao = "llm"
        apolice.modelo_llm = self.llm.model
        apolice.avisos.extend(avisos)
        self.verify_evidence(apolice, doc)
        return apolice

    def extract_with_rules(self, doc: DocumentoProcessado) -> ApoliceExtraida:
        """Extração mínima SEM IA (fallback). Nunca é apresentada como IA."""
        apolice = RuleBasedExtractor().extract(doc)
        apolice.metodo_extracao = "regras"
        apolice.avisos.append(
            "Extração feita por regras (expressões regulares), SEM IA generativa. "
            "Cobertura de campos limitada; configure a OPENAI_API_KEY para a extração completa."
        )
        self.verify_evidence(apolice, doc)
        return apolice

    # ------------------------------------------------------- seleção de texto
    def select_text(self, doc: DocumentoProcessado) -> tuple[str, list[str]]:
        limite = self.settings.max_chars_llm

        def compactar(texto: str) -> str:
            # O layout reconstruído usa muitos espaços para alinhar colunas; reduzi-los
            # economiza tokens sem perder a separação entre rótulo e valor.
            return re.sub(r"[ \t]{3,}", "  ", texto)

        completo = compactar(doc.texto_com_marcadores())
        if len(completo) <= limite:
            return completo, []

        def pontuar(pagina) -> float:
            # densidade de termos-chave (por mil caracteres): favorece páginas de
            # especificação (tabelas de limites/coberturas) sobre páginas de texto corrido
            t = normalize_for_match(pagina.texto)
            return sum(t.count(k) for k in PALAVRAS_CHAVE) * 1000 / max(len(t), 1)

        # Ordem de prioridade: 1) primeiras páginas (dados gerais e prêmio);
        # 2) páginas com dados críticos de especificação (LMG, franquia, tabelas "% do LMI");
        # 3) demais páginas por densidade de termos-chave.
        primeiras = [p for p in doc.paginas if p.numero <= 3]
        resto = [p for p in doc.paginas if p.numero > 3]
        criticas = sorted((p for p in resto if PAGINA_CRITICA.search(p.texto)), key=pontuar, reverse=True)
        demais = sorted((p for p in resto if p not in criticas), key=pontuar, reverse=True)
        escolhidas, total = [], 0
        for p in primeiras + criticas + demais:
            bloco = len(compactar(p.texto)) + 20
            if total + bloco > limite:
                continue
            escolhidas.append(p)
            total += bloco
        escolhidas.sort(key=lambda p: p.numero)
        texto = "\n\n".join(f"[PÁGINA {p.numero}]\n{compactar(p.texto)}" for p in escolhidas)
        total_fmt = f"{doc.total_caracteres:,}".replace(",", ".")
        aviso = (
            f"Documento longo ({total_fmt} caracteres): enviadas ao LLM "
            f"{len(escolhidas)} de {doc.num_paginas} páginas, priorizando as mais relevantes."
        )
        return texto, [aviso]

    # -------------------------------------------------------------- parsing
    @staticmethod
    def parse_llm_output(bruto: dict) -> ApoliceExtraida:
        """Converte o dicionário do LLM no schema, tolerando pequenas variações."""
        if not isinstance(bruto, dict):
            raise LLMError("Resposta do LLM não é um objeto JSON.")
        dados: dict = {}
        for campo in CAMPOS_ESCALARES:
            v = bruto.get(campo)
            if isinstance(v, dict):
                dados[campo] = Campo(
                    valor=v.get("valor"),
                    texto_original=_str_or_none(v.get("texto_original")),
                    pagina=_int_or_none(v.get("pagina")),
                    trecho=_str_or_none(v.get("trecho")),
                )
            elif v is not None and not isinstance(v, (list, dict)):
                dados[campo] = Campo(valor=v, texto_original=str(v))
            else:
                dados[campo] = Campo()
        for campo in CAMPOS_LISTA:
            itens = []
            for it in bruto.get(campo) or []:
                if isinstance(it, str) and it.strip():
                    itens.append(ItemApolice(nome=it.strip()))
                elif isinstance(it, dict) and _str_or_none(it.get("nome")):
                    itens.append(
                        ItemApolice(
                            nome=str(it["nome"]).strip(),
                            limite=_str_or_none(it.get("limite")),
                            franquia=_str_or_none(it.get("franquia")),
                            descricao=_str_or_none(it.get("descricao")),
                            pagina=_int_or_none(it.get("pagina")),
                            trecho=_str_or_none(it.get("trecho")),
                        )
                    )
            dados[campo] = itens
        dados["observacoes"] = [str(o) for o in (bruto.get("observacoes") or []) if o]
        return ApoliceExtraida(**dados)

    # ------------------------------------------------- guardrail de evidência
    @staticmethod
    def verify_evidence(apolice: ApoliceExtraida, doc: DocumentoProcessado) -> ApoliceExtraida:
        """Descarta valores cujo texto/trecho não existe no documento (anti-alucinação)."""
        texto_doc = normalize_for_match(doc.texto_completo)
        for campo, rotulo in CAMPOS_ESCALARES.items():
            c: Campo = getattr(apolice, campo)
            if c.valor is None and not c.texto_original:
                continue
            if campo == "tipo_documento":
                continue  # classificação, não é um dado copiado do texto
            ok = evidence_in_text(c.texto_original, texto_doc) or evidence_in_text(c.trecho, texto_doc)
            if not ok:
                apolice.avisos.append(
                    f"'{rotulo}' descartado: a evidência informada pelo modelo não foi localizada no documento."
                )
                setattr(apolice, campo, Campo())
        for campo, rotulo in CAMPOS_LISTA.items():
            mantidos = []
            for item in getattr(apolice, campo):
                if evidence_in_text(item.nome, texto_doc, min_ratio=0.7) or evidence_in_text(item.trecho, texto_doc):
                    mantidos.append(item)
                else:
                    apolice.avisos.append(f"{rotulo}: item '{item.nome}' descartado (não localizado no documento).")
            setattr(apolice, campo, mantidos)
        return apolice


# ===========================================================================
# Extrator por regras (fallback SEM IA)
# ===========================================================================

CAMPOS_CONTRATUAIS = [
    "segurado",
    "cnpj_segurado",
    "numero_apolice",
    "corretor",
    "vigencia_inicio",
    "vigencia_fim",
    "premio_liquido",
    "iof",
    "premio_total",
    "lmg",
    "lmi",
    "franquia",
    "seguradora_anterior",
    "tipo_contratacao",
    "retroatividade",
    "ambito_territorial",
    "prazo_complementar",
]
_MONEY = r"R\$\s*([\d.]+,\d{2})"
_FLAGS = re.IGNORECASE


class RuleBasedExtractor:
    """Extração determinística mínima, baseada em padrões dos documentos de exemplo."""

    SEGURADORAS = {
        "Tokio Marine Seguradora S.A.": r"tokio marine seguradora s\.?a",
        "AXA Seguros S.A.": r"axa seguros s\.?a",
        "Allianz Seguros S.A.": r"allianz seguros s\.?a",
        "Porto Seguro Companhia de Seguros Gerais": r"porto seguro",
    }

    def extract(self, doc: DocumentoProcessado) -> ApoliceExtraida:
        a = ApoliceExtraida()
        paginas = doc.paginas

        def buscar(padrao: str, grupo: int = 1, flags=_FLAGS) -> Campo:
            for p in paginas:
                m = re.search(padrao, p.texto, flags)
                if m:
                    valor = m.group(grupo).strip()
                    # evidência = linha(s) completa(s) onde o padrão foi encontrado
                    inicio = p.texto.rfind("\n", 0, m.start()) + 1
                    fim = p.texto.find("\n", m.end())
                    trecho = re.sub(r"\s+", " ", p.texto[inicio : fim if fim != -1 else None]).strip()
                    return Campo(valor=valor, texto_original=valor, pagina=p.numero, trecho=trecho[:200])
            return Campo()

        # Seguradora: rótulo explícito ou cláusula "a seguir denominada SEGURADORA"
        a.seguradora = buscar(r"^\s*Seguradora:\s*([^\n]{3,60}?S[./]A\.?)", flags=_FLAGS | re.MULTILINE)
        if not a.seguradora.encontrado:
            a.seguradora = buscar(r"([A-ZÀ-Ú][A-ZÀ-Ú .&]+S\.A\.)\s*,?\s*C[óo]digo Susep[^,]*,\s*a seguir denominada")
        if not a.seguradora.encontrado:
            for nome, padrao in self.SEGURADORAS.items():
                c = buscar(f"({padrao})")
                if c.encontrado:
                    c.valor = nome
                    a.seguradora = c
                    break

        def com_continuacao(rotulo: str) -> Campo:
            """Captura valores em MAIÚSCULAS quebrados em duas linhas (ex.: razão social)."""
            c = buscar(
                rotulo + r"\s*:\s*([^\n]+?)(?:\s{2,}[^\n]*)?\n\s*([A-ZÀ-Ú][A-ZÀ-Ú .&-]*)$",
                flags=re.MULTILINE,
            )
            if c.encontrado:
                m = re.search(
                    rotulo + r"\s*:\s*([^\n]+?)(?:\s{2,}[^\n]*)?\n\s*([A-ZÀ-Ú][A-ZÀ-Ú .&-]*)$",
                    (paginas[c.pagina - 1].texto),
                    re.MULTILINE,
                )
                primeira = m.group(1).strip()
                if not primeira.rstrip().endswith((".", "LTDA", "S.A")):
                    c.valor = c.texto_original = f"{primeira} {m.group(2).strip()}"
                    return c
            return buscar(rotulo + r"\s*:\s*([^\n]+?)(?:\s{2,}|$)", flags=_FLAGS | re.MULTILINE)

        a.seguradora_anterior = com_continuacao(r"Seguradora anterior")
        a.segurado = com_continuacao(r"(?:Tomador|NOME)")
        a.cnpj_segurado = buscar(r"(?:Tomador|NOME)\s*:[^\n]*\n[^\n]*?CNPJ(?:/CPF)?:\s*([\d./-]+)")
        if not a.cnpj_segurado.encontrado:
            a.cnpj_segurado = buscar(r"(?:Tomador|NOME)\s*:[^\n]*\n[^\n]*\n\s*CNPJ(?:/CPF)?:\s*([\d./-]+)")
        a.numero_apolice = buscar(r"Ap[óo]lice:\s*([\d.]{10,})")
        if not a.numero_apolice.encontrado:
            a.numero_apolice = buscar(r"AP[ÓO]LICE N[ºo°][^\n]*\n\s*\S+\s+(\d{3} \d{6,})")
        a.corretor = com_continuacao(r"Corretor")

        a.vigencia_inicio = buscar(r"partir das 24 horas d[eo](?: dia)?\s*(\d{2}/\d{2}/\d{4})")
        if not a.vigencia_inicio.encontrado:
            a.vigencia_inicio = buscar(r"In[íi]cio de.{0,120}?(\d{2}/\d{2}/\d{4})", flags=_FLAGS | re.DOTALL)
        a.vigencia_fim = buscar(r"At[ée] (?:[àa]s )?24 horas d[eo](?: dia)?\s*(\d{2}/\d{2}/\d{4})")

        a.premio_liquido = buscar(r"Pr[êe]mio L[íi]quido\s*:?\s*" + _MONEY)
        a.iof = buscar(r"(?:I\.O\.F\.|Valor do IOF)\s*:?\s*" + _MONEY)
        a.premio_total = buscar(r"Pr[êe]mio Total\s*:?\s*" + _MONEY)
        a.lmg = buscar(r"Limite m[áa]ximo de garantia[^\n]{0,60}?" + _MONEY)
        a.franquia = buscar(r"^\s*Franquia[^:\n]{0,60}:\s*([^\n]+)", flags=_FLAGS | re.MULTILINE)
        a.tipo_contratacao = buscar(r"Tipo de ap[óo]lice:\s*([^\n]+)")
        a.retroatividade = buscar(r"Per[íi]odo de Retroatividade:\s*([^\n]+)")
        a.ambito_territorial = buscar(r"[ÂA]mbito de cobertura:\s*([^\n]+)")
        a.prazo_complementar = buscar(r"Prazo complementar:\s*([^\n]+)")
        a.processo_susep = buscar(r"Processo(?:\(s\))? SUSEP[^\d]{0,20}(15414\.\d+/\d{4}-\d{2})")

        # Tipo de documento: Condições Gerais não têm dados contratuais (segurado, prêmio...)
        inicio = " ".join(p.texto[:1500] for p in paginas[:3])
        sem_contrato = not a.premio_total.encontrado and not a.numero_apolice.encontrado
        if re.search(r"condi[çc][õo]es gerais", inicio, _FLAGS) and sem_contrato:
            a.tipo_documento = Campo(valor="condicoes_gerais")
            for campo in CAMPOS_CONTRATUAIS:  # trechos de glossário não são dados do contrato
                setattr(a, campo, Campo())
        elif not sem_contrato:
            a.tipo_documento = Campo(valor="apolice")

        self._extract_tables(doc, a)
        if re.search(r"especifica[çc][õo]es anexas", doc.texto_completo, _FLAGS):
            a.observacoes.append(
                "O documento menciona 'especificações anexas' que não fazem parte do arquivo analisado."
            )
        return a

    # Linhas de tabela no formato "<nome>   <N>% do LMI" (especificação da apólice)
    _LINHA_LMI = re.compile(
        r"^\s*(?:(\d+\.\d+)\s+)?(?P<nome>\S.*?)\s{2,}(?P<limite>\d{1,3}% do LMI)(?:\s+R\$\s*[\d.,]+)?(?:\s+(?P<franquia>\S+))?\s*$",
        _FLAGS,
    )
    _SECOES = [
        (re.compile(r"COBERTURAS E FRANQUIAS|^\s*\d+\.\s*COBERTURAS\b", _FLAGS | re.MULTILINE), "coberturas"),
        (re.compile(r"EXTENS[ÕO]ES DE COBERTURA", _FLAGS), "extensoes"),
        (re.compile(r"CONDI[ÇC][ÕO]ES PARTICULARES|CL[ÁA]USULAS PARTICULARES", _FLAGS), "clausulas_relevantes"),
        (re.compile(r"^\s*\d+\.\s*FRANQUIAS", _FLAGS | re.MULTILINE), None),
    ]

    def _extract_tables(self, doc: DocumentoProcessado, a: ApoliceExtraida) -> None:
        secao: str | None = None
        ultimo: ItemApolice | None = None
        for p in doc.paginas:
            for linha in p.texto.splitlines():
                for padrao, nome_secao in self._SECOES:
                    if padrao.search(linha):
                        secao, ultimo = nome_secao, None
                        break
                if secao is None:
                    continue
                m = self._LINHA_LMI.match(linha)
                if m:
                    franquia = m.group("franquia")
                    ultimo = ItemApolice(
                        nome=m.group("nome").strip(),
                        limite=m.group("limite"),
                        franquia=franquia if franquia else None,
                        pagina=p.numero,
                        trecho=linha.strip()[:200],
                    )
                    getattr(a, secao).append(ultimo)
                elif (
                    ultimo is not None
                    and linha.strip()
                    and len(linha.strip()) < 60
                    and not re.search(r"LMI|Cobertura\s*$|^\s*\d+/\d+\s*$", linha)
                ):
                    # continuação do nome quebrado em várias linhas
                    ultimo.nome = f"{ultimo.nome} {linha.strip()}"
                elif not linha.strip():
                    ultimo = None


def _str_or_none(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _int_or_none(v) -> int | None:
    try:
        return int(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None
