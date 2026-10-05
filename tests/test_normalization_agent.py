from src.agents.normalization_agent import NormalizationAgent
from src.schemas import ApoliceExtraida, Campo, ItemApolice
from src.text_utils import parse_date_br, parse_money_br


def test_parse_money_br_preserva_valor():
    assert parse_money_br("R$ 10.880,98") == 10880.98
    assert parse_money_br("5.000.000,00") == 5_000_000.0
    assert parse_money_br("R$ 382,92") == 382.92
    assert parse_money_br("100% do LMI") is None
    assert parse_money_br(None) is None


def test_parse_date_br():
    assert parse_date_br("30/09/2024") == "2024-09-30"
    assert parse_date_br("A PARTIR DAS 24 HORAS DO DIA30/09/2025") == "2025-09-30"
    assert parse_date_br("2026-09-30") == "2026-09-30"
    assert parse_date_br("31/02/2025") is None


def test_normaliza_seguradora_preservando_original():
    ap = ApoliceExtraida(
        seguradora=Campo(valor="TOKIO MARINE SEGURADORA S.A.", texto_original="TOKIO MARINE SEGURADORA S.A.")
    )
    n = NormalizationAgent().normalize(ap)
    assert n.seguradora.valor == "Tokio Marine"
    assert n.seguradora.texto_original == "TOKIO MARINE SEGURADORA S.A."
    assert NormalizationAgent.canonical_insurer("AXA Seguros S.A.") == "AXA"


def test_converte_moeda_e_data():
    ap = ApoliceExtraida(
        premio_total=Campo(valor="R$ 5.571,59", texto_original="R$ 5.571,59"),
        vigencia_inicio=Campo(valor="30/09/2025", texto_original="30/09/2025"),
    )
    n = NormalizationAgent().normalize(ap)
    assert n.premio_total.valor == 5571.59
    assert n.premio_total.texto_original == "R$ 5.571,59"
    assert n.vigencia_inicio.valor == "2025-09-30"
    assert n.normalizado is True


def test_texto_original_prevalece_sobre_numero_divergente():
    ap = ApoliceExtraida(premio_total=Campo(valor=5571.0, texto_original="R$ 5.571,59"))
    n = NormalizationAgent().normalize(ap)
    assert n.premio_total.valor == 5571.59
    assert any("diverge" in a for a in n.avisos)


def test_campos_ausentes_permanecem_nulos():
    n = NormalizationAgent().normalize(ApoliceExtraida())
    assert n.lmg.valor is None and n.lmg.texto_original is None
    assert n.vigencia_fim.valor is None
    assert n.coberturas == []


def test_sinonimos_viram_mesma_chave():
    agente = NormalizationAgent()
    assert agente.canonical_item("Bloqueio de Conta Corrente (Penhora On-Line)")[0] == "penhora_bloqueio_contas"
    assert agente.canonical_item("5.1 Cobertura para Custos Emergenciais")[0] == "custos_emergenciais"
    assert agente.canonical_item("EXTENSÃO DE COBERTURA PARA DESPESAS EMERGENCIAIS")[0] == "custos_emergenciais"
    assert (
        agente.canonical_item("Extensão de Prazo Complementar para Segurado Aposentado")[0]
        == "prazo_aposentados_demissao"
    )


def test_item_sem_sinonimo_mantem_nome_original():
    ap = ApoliceExtraida(extensoes=[ItemApolice(nome="Cobertura Exótica XYZ", limite="10% do LMI")])
    item = NormalizationAgent().normalize(ap).extensoes[0]
    assert item.nome == "Cobertura Exótica XYZ"
    assert item.nome_normalizado == "Cobertura Exótica XYZ"
    assert item.limite == "10% do LMI"  # valor não é alterado
    assert item.chave


def test_conceito_repetido_no_mesmo_documento_nao_colide():
    ap = ApoliceExtraida(
        extensoes=[
            ItemApolice(nome="Cobertura para Entidade Externa"),
            ItemApolice(nome="Reclamação Apresentada por uma Entidade Externa"),
        ]
    )
    chaves = [i.chave for i in NormalizationAgent().normalize(ap).extensoes]
    assert len(set(chaves)) == 2
