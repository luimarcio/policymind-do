"""PolicyMind D&O — Comparador Inteligente de Apólices (interface Streamlit).

Execute com:  streamlit run app.py
"""

from __future__ import annotations

import json
import logging

import pandas as pd
import streamlit as st

from src.config import PROJECT_ROOT, get_settings
from src.orchestrator import OrchestratorAgent
from src.schemas import (
    RESSALVA_PADRAO,
    ApoliceExtraida,
    DocumentoProcessado,
    Etapa,
    ResultadoPipeline,
    StatusCampo,
    StatusEtapa,
    StatusItem,
)
from src.storage import PolicyStore
from src.text_utils import format_date_br, format_money_br

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

st.set_page_config(page_title="PolicyMind D&O", page_icon="📑", layout="wide")

DATA_DIR = PROJECT_ROOT / "data"
EXTENSOES_ACEITAS = ["pdf", "png", "jpg", "jpeg", "tif", "tiff"]

ICONE_ETAPA = {
    StatusEtapa.PENDENTE: "⏸️",
    StatusEtapa.EXECUTANDO: "⏳",
    StatusEtapa.SUCESSO: "✅",
    StatusEtapa.ALERTA: "⚠️",
    StatusEtapa.ERRO: "❌",
}
ROTULO_STATUS_CAMPO = {
    StatusCampo.IGUAL: "🟢 igual",
    StatusCampo.ALTERADO: "🟡 alterado",
    StatusCampo.SOMENTE_A: "🔵 só em A",
    StatusCampo.SOMENTE_B: "🟣 só em B",
    StatusCampo.NAO_IDENTIFICADO: "⚪ não identificado",
}
ROTULO_STATUS_ITEM = {
    StatusItem.MANTIDO: "🟢 mantido",
    StatusItem.ALTERADO: "🟡 alterado",
    StatusItem.ADICIONADO: "🟣 adicionado (só em B)",
    StatusItem.REMOVIDO: "🔴 removido (só em A)",
    StatusItem.SEM_BASE: "⚪ sem base de comparação",
}
CAMPOS_DATA = {"vigencia_inicio", "vigencia_fim"}
CAMPOS_MOEDA = {"premio_liquido", "iof", "premio_total", "lmg", "lmi"}


# ============================================================ helpers de exibição
def md_seguro(texto: str) -> str:
    """Escapa '$' para o Streamlit não interpretar 'R$ ... R$' como fórmula LaTeX."""
    return texto.replace("$", "\\$")


def fmt_valor(campo: str, valor, texto: str | None) -> str:
    if valor is None:
        return texto or "não identificado"
    if campo in CAMPOS_DATA:
        return format_date_br(valor)
    if campo in CAMPOS_MOEDA and isinstance(valor, (int, float)):
        return format_money_br(valor)
    return str(valor)


ROTULO_ETAPA = {
    StatusEtapa.PENDENTE: "aguardando",
    StatusEtapa.EXECUTANDO: "executando...",
    StatusEtapa.SUCESSO: "concluído",
    StatusEtapa.ALERTA: "concluído com alerta",
    StatusEtapa.ERRO: "erro",
}


def render_etapas(container, etapas: list[Etapa]) -> None:
    """Linha de 'cartões' com o status de cada agente + avisos em largura total."""
    with container.container():
        cols = st.columns(len(etapas))
        for col, e in zip(cols, etapas):
            dur = f" · {e.duracao_s:.1f}s" if e.duracao_s is not None else ""
            with col.container(border=True):
                st.markdown(f"**{ICONE_ETAPA[e.status]} {e.agente}**")
                st.caption(f"{e.descricao}")
                st.caption(f"_{ROTULO_ETAPA[e.status]}{dur}_")
        for e in etapas:
            if e.status == StatusEtapa.ALERTA and e.mensagem:
                st.warning(f"**{e.agente}:** {e.mensagem}", icon="⚠️")
            elif e.status == StatusEtapa.ERRO and e.mensagem:
                st.error(f"**{e.agente}:** {e.mensagem}", icon="❌")


def log_etapas(etapas: list[Etapa]) -> None:
    with st.expander("🗒️ Log do OrchestratorAgent"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Agente": e.agente,
                        "Status": e.status.value,
                        "Início": e.inicio.strftime("%H:%M:%S") if e.inicio else "—",
                        "Duração (s)": e.duracao_s,
                        "Mensagem": e.mensagem or "",
                    }
                    for e in etapas
                ]
            ),
            width="stretch",
            hide_index=True,
        )


def resumo_documento(col, rotulo: str, doc: DocumentoProcessado | None, ap: ApoliceExtraida | None) -> None:
    col.subheader(rotulo)
    if doc is None or ap is None:
        col.info("Documento não processado.")
        return
    metodo = {"llm": f"🤖 IA generativa ({ap.modelo_llm})", "regras": "📐 Regras (SEM IA)", "nenhum": "—"}
    col.caption(
        f"📄 {doc.nome_arquivo} · {doc.num_paginas} pág. · leitura: **{doc.metodo_extracao}** · "
        f"extração: **{metodo[ap.metodo_extracao]}**"
    )
    c1, c2 = col.columns(2)
    c1.metric("Seguradora", ap.seguradora.valor or "não identificado")
    c2.metric("Prêmio total", fmt_valor("premio_total", ap.premio_total.valor, ap.premio_total.texto_original))
    c1.metric("Início de vigência", format_date_br(ap.vigencia_inicio.valor))
    c2.metric("Fim de vigência", format_date_br(ap.vigencia_fim.valor))
    c1.metric("Prêmio líquido", fmt_valor("premio_liquido", ap.premio_liquido.valor, ap.premio_liquido.texto_original))
    c2.metric("LMG", fmt_valor("lmg", ap.lmg.valor, ap.lmg.texto_original))
    col.caption(
        f"Segurado: {ap.segurado.valor or 'não identificado'} · Tipo: {ap.tipo_documento.valor or 'não identificado'} · "
        f"{len(ap.coberturas)} coberturas · {len(ap.extensoes)} extensões · "
        f"{len(ap.clausulas_relevantes)} cláusulas · {len(ap.exclusoes)} exclusões"
    )
    if ap.observacoes or ap.avisos or doc.avisos:
        with col.expander(f"Observações e avisos ({len(ap.observacoes) + len(ap.avisos) + len(doc.avisos)})"):
            for o in ap.observacoes:
                st.markdown(f"- 📝 {o}")
            for a in doc.avisos + ap.avisos:
                st.markdown(f"- ⚠️ {a}")


def tabela_campos(res: ResultadoPipeline, secao: str) -> pd.DataFrame:
    comp = res.comparacao
    linhas = []
    for d in comp.campos:
        if d.secao != secao:
            continue
        var = ""
        if d.variacao_percentual is not None:
            var = f"{d.variacao_percentual:+.2f}%".replace(".", ",")
        linhas.append(
            {
                "Campo": d.rotulo,
                comp.rotulo_a: fmt_valor(d.campo, d.valor_a, d.texto_a),
                comp.rotulo_b: fmt_valor(d.campo, d.valor_b, d.texto_b),
                "Status": ROTULO_STATUS_CAMPO[d.status],
                "Variação": var,
                f"Fonte ({comp.rotulo_a})": d.fonte_a or "—",
                f"Fonte ({comp.rotulo_b})": d.fonte_b or "—",
            }
        )
    return pd.DataFrame(linhas)


def tabela_itens(res: ResultadoPipeline, secoes: list[str]) -> pd.DataFrame:
    comp = res.comparacao
    linhas = []
    for i in comp.itens:
        if i.secao not in secoes:
            continue
        ausente = "não identificado" if i.status == StatusItem.SEM_BASE else "ausente"
        linhas.append(
            {
                "Item (padronizado)": i.nome,
                "Seção": i.secao,
                f"Limite {comp.rotulo_a}": i.limite_a or ("—" if i.nome_a else ausente),
                f"Limite {comp.rotulo_b}": i.limite_b or ("—" if i.nome_b else ausente),
                "Status": ROTULO_STATUS_ITEM[i.status],
                f"Nome original ({comp.rotulo_a})": i.nome_a or "—",
                f"Nome original ({comp.rotulo_b})": i.nome_b or "—",
                f"Fonte ({comp.rotulo_a})": i.fonte_a or "—",
                f"Fonte ({comp.rotulo_b})": i.fonte_b or "—",
            }
        )
    return pd.DataFrame(linhas)


def mostrar_tabela(df: pd.DataFrame, vazio: str) -> None:
    if df.empty:
        st.info(vazio)
    else:
        st.dataframe(df, width="stretch", hide_index=True)


# ===================================================================== sidebar
settings = get_settings()
with st.sidebar:
    st.header("⚙️ Configuração")
    if settings.llm_available:
        st.success(f"IA generativa ativa\n\nModelo: `{settings.openai_model}`")
    else:
        st.error(
            "OPENAI_API_KEY não configurada.\n\nA extração e a síntese por IA ficarão indisponíveis. "
            + ("Será usado o modo **regras (sem IA)**." if settings.allow_rule_fallback else "")
        )
    st.caption(f"OCR (fallback): {'ativado' if settings.ocr_enabled else 'desativado'} · idioma `{settings.ocr_lang}`")
    st.caption(f"Cache SQLite: {'ativado' if settings.cache_enabled else 'desativado'}")

    st.divider()
    st.subheader("📂 Documentos de exemplo")
    exemplos = sorted(p for p in DATA_DIR.glob("*") if p.suffix.lower().lstrip(".") in EXTENSOES_ACEITAS)
    usar_exemplos = st.toggle("Usar arquivos da pasta data/", value=False, disabled=not exemplos)
    sel_a = sel_b = None
    if usar_exemplos and exemplos:
        nomes = [p.name for p in exemplos]
        idx_a = next((i for i, n in enumerate(nomes) if "TOKYO" in n.upper()), 0)
        idx_b = next((i for i, n in enumerate(nomes) if "SUMITOMO" in n.upper()), min(1, len(nomes) - 1))
        sel_a = st.selectbox("Apólice A (referência)", nomes, index=idx_a)
        sel_b = st.selectbox("Apólice B (comparada)", nomes, index=idx_b)

    if settings.cache_enabled:
        with st.expander("🗄️ Apólices armazenadas (SQLite)"):
            try:
                hist = PolicyStore(settings.cache_path).list_all()
                st.dataframe(pd.DataFrame(hist), hide_index=True) if hist else st.caption("Nenhuma ainda.")
            except Exception as exc:
                st.caption(f"Indisponível: {exc}")

# ===================================================================== cabeçalho
st.title("📑 PolicyMind D&O — Comparador Inteligente de Apólices")
st.caption(
    "Arquitetura multiagente: DocumentAgent → PolicyExtractionAgent → NormalizationAgent → "
    "ComparisonAgent → AnalysisAgent, coordenados pelo OrchestratorAgent."
)

col_a, col_b = st.columns(2)
up_a = col_a.file_uploader("Apólice A (referência / anterior)", type=EXTENSOES_ACEITAS, disabled=usar_exemplos)
up_b = col_b.file_uploader("Apólice B (comparada / renovação)", type=EXTENSOES_ACEITAS, disabled=usar_exemplos)

arquivos: list[tuple[str, bytes]] = []
if usar_exemplos and sel_a and sel_b:
    arquivos = [(sel_a, (DATA_DIR / sel_a).read_bytes()), (sel_b, (DATA_DIR / sel_b).read_bytes())]
elif up_a and up_b:
    arquivos = [(up_a.name, up_a.getvalue()), (up_b.name, up_b.getvalue())]

executar = st.button("🔍 Analisar e comparar", type="primary", disabled=len(arquivos) < 2)

st.markdown("#### Etapas dos agentes")
painel_etapas = st.empty()

if executar:
    orq = OrchestratorAgent(settings=settings, on_update=lambda etapas: render_etapas(painel_etapas, etapas))
    with st.spinner("Processando documentos..."):
        st.session_state["resultado"] = orq.run(arquivos)

res: ResultadoPipeline | None = st.session_state.get("resultado")
if res is None:
    from src.orchestrator import ETAPAS

    render_etapas(painel_etapas, [Etapa(agente=a, descricao=d) for a, d in ETAPAS])
    st.info(
        "Envie duas apólices (PDF ou imagem) ou selecione os exemplos na barra lateral e clique em **Analisar e comparar**."
    )
    st.stop()

render_etapas(painel_etapas, res.etapas)
log_etapas(res.etapas)
if res.erro_fatal:
    st.error(f"O processamento foi interrompido. {res.erro_fatal}")

# ===================================================================== resultados
if res.apolices:
    st.divider()
    st.markdown("### 📄 Resumo dos documentos")
    rot = [res.comparacao.rotulo_a, res.comparacao.rotulo_b] if res.comparacao else ["Apólice A", "Apólice B"]
    cols = st.columns(2)
    for i in range(2):
        doc = res.documentos[i] if i < len(res.documentos) else None
        ap = res.apolices[i] if i < len(res.apolices) else None
        resumo_documento(cols[i], f"{'A' if i == 0 else 'B'} · {rot[i]}", doc, ap)

if res.analise:
    st.divider()
    st.markdown("### 🧠 Síntese executiva (AnalysisAgent)")
    an = res.analise
    if an.gerado_por_ia:
        st.caption(f"🤖 Texto gerado por IA generativa · modelo `{an.modelo_llm}`")
        st.markdown(md_seguro(an.sintese or ""))
        g, p = st.columns(2)
        g.markdown("**✅ Ganhos**")
        for x in an.ganhos or ["Nenhum identificado."]:
            g.markdown(f"- {md_seguro(x)}")
        p.markdown("**🔻 Perdas**")
        for x in an.perdas or ["Nenhuma identificada."]:
            p.markdown(f"- {md_seguro(x)}")
    else:
        st.warning(
            f"A síntese por IA generativa **não foi gerada**: {an.erro}\n\n"
            "Abaixo estão apenas os destaques calculados por regras (sem IA)."
        )
    st.caption(f"⚖️ {an.ressalva}")

if res.comparacao:
    st.divider()
    st.markdown("### 📊 Comparação lado a lado")
    for aviso in res.comparacao.avisos:
        st.warning(aviso)
    abas = st.tabs(
        ["Dados Gerais", "Limites", "Prêmio", "Franquias", "Coberturas", "Extensões", "Exclusões", "Pontos de Atenção"]
    )
    with abas[0]:
        mostrar_tabela(tabela_campos(res, "Dados Gerais"), "Sem dados gerais.")
    with abas[1]:
        mostrar_tabela(tabela_campos(res, "Limites"), "Sem limites identificados.")
    with abas[2]:
        mostrar_tabela(tabela_campos(res, "Prêmio"), "Sem dados de prêmio.")
    with abas[3]:
        mostrar_tabela(tabela_campos(res, "Franquias"), "Sem dados de franquia.")
    with abas[4]:
        mostrar_tabela(tabela_itens(res, ["Coberturas"]), "Nenhuma cobertura identificada.")
    with abas[5]:
        mostrar_tabela(
            tabela_itens(res, ["Extensões", "Cláusulas relevantes"]),
            "Nenhuma extensão ou cláusula identificada.",
        )
    with abas[6]:
        mostrar_tabela(tabela_itens(res, ["Exclusões"]), "Nenhuma exclusão identificada.")
    with abas[7]:
        an = res.analise
        if an and an.gerado_por_ia and an.pontos_atencao:
            st.markdown("**🤖 Pontos de atenção (IA generativa)**")
            for x in an.pontos_atencao:
                st.markdown(f"- {md_seguro(x)}")
        if an and an.destaques_regras:
            st.markdown("**📐 Fatos objetivos da comparação (calculados por regras, sem IA)**")
            for x in an.destaques_regras:
                st.markdown(f"- {md_seguro(x)}")
        st.caption(f"⚖️ {RESSALVA_PADRAO}")

    st.divider()
    with st.expander("🧾 JSON estruturado extraído"):
        j1, j2 = st.columns(2)
        for col, ap, r in zip((j1, j2), res.apolices, (res.comparacao.rotulo_a, res.comparacao.rotulo_b)):
            col.markdown(f"**{r}**")
            col.json(ap.model_dump(mode="json") if ap else {}, expanded=False)
        st.markdown("**Comparação**")
        st.json(res.comparacao.model_dump(mode="json"), expanded=False)

    st.download_button(
        "⬇️ Baixar resultado completo (JSON)",
        data=json.dumps(res.model_dump(mode="json"), ensure_ascii=False, indent=2),
        file_name="policymind_resultado.json",
        mime="application/json",
    )
