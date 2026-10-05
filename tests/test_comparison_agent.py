from src.agents.comparison_agent import ComparisonAgent
from src.agents.normalization_agent import NormalizationAgent
from src.schemas import ApoliceExtraida, Campo, ItemApolice, StatusCampo, StatusItem


def _ap(**kw) -> ApoliceExtraida:
    return NormalizationAgent().normalize(ApoliceExtraida(**kw))


def _campo(res, nome):
    return next(d for d in res.campos if d.campo == nome)


def test_compara_campos_escalares_e_variacao():
    a = _ap(
        seguradora=Campo(valor="Tokio Marine Seguradora S.A."),
        premio_total=Campo(valor=10880.98, texto_original="R$ 10.880,98"),
        segurado=Campo(valor="EMPRESA X LTDA."),
    )
    b = _ap(
        seguradora=Campo(valor="AXA Seguros S.A."),
        premio_total=Campo(valor=5571.59, texto_original="R$ 5.571,59"),
        segurado=Campo(valor="Empresa X Ltda"),
    )
    res = ComparisonAgent().compare(a, b, "A", "B")
    assert _campo(res, "seguradora").status == StatusCampo.ALTERADO
    premio = _campo(res, "premio_total")
    assert premio.status == StatusCampo.ALTERADO
    assert premio.variacao_percentual == -48.8
    assert _campo(res, "segurado").status == StatusCampo.IGUAL  # ignora caixa/pontuação


def test_campos_ausentes_nao_viram_diferenca_inventada():
    a = _ap(premio_total=Campo(valor=100.0, texto_original="R$ 100,00"))
    b = _ap(lmg=Campo(valor=5_000_000.0, texto_original="R$ 5.000.000,00"))
    res = ComparisonAgent().compare(a, b)
    assert _campo(res, "premio_total").status == StatusCampo.SOMENTE_A
    assert _campo(res, "lmg").status == StatusCampo.SOMENTE_B
    assert _campo(res, "lmi").status == StatusCampo.NAO_IDENTIFICADO
    assert _campo(res, "lmi").valor_a is None and _campo(res, "lmi").valor_b is None


def test_compara_listas_de_coberturas():
    a = _ap(
        coberturas=[ItemApolice(nome="Cobertura A - Pagamento ao Administrador", limite="100% do LMI")],
        extensoes=[
            ItemApolice(nome="Custos Emergenciais", limite="100% do LMI"),
            ItemApolice(nome="Danos Morais", limite="50% do LMI"),
            ItemApolice(nome="Multas e Penalidades", limite="15% do LMI"),
        ],
    )
    b = _ap(
        coberturas=[ItemApolice(nome="Cobertura A - Pagamento ao Administrador", limite="100% do LMI")],
        extensoes=[
            ItemApolice(nome="Despesas Emergenciais", limite="100% do LMI"),  # sinônimo
            ItemApolice(nome="Danos Morais", limite="100% do LMI"),
            ItemApolice(nome="Práticas Trabalhistas", limite="100% do LMI"),
        ],
    )
    res = ComparisonAgent().compare(a, b)
    status = {i.chave: i.status for i in res.itens}
    assert status["cobertura_a_administradores"] == StatusItem.MANTIDO
    assert status["custos_emergenciais"] == StatusItem.MANTIDO
    assert status["danos_morais"] == StatusItem.ALTERADO
    assert status["praticas_trabalhistas"] == StatusItem.ADICIONADO
    assert status["multas_penalidades"] == StatusItem.REMOVIDO
    assert res.contagem["itens_adicionado"] == 1


def test_item_em_secoes_diferentes_e_reconhecido():
    a = _ap(extensoes=[ItemApolice(nome="Danos Morais", limite="100% do LMI")])
    b = _ap(clausulas_relevantes=[ItemApolice(nome="Danos Morais", limite="100% do LMI")])
    res = ComparisonAgent().compare(a, b)
    assert [i.status for i in res.itens] == [StatusItem.MANTIDO]


def test_lista_ausente_em_um_documento_fica_sem_base():
    a = _ap()  # ex.: apólice sem as especificações anexas
    b = _ap(extensoes=[ItemApolice(nome="Danos Morais", limite="100% do LMI")])
    res = ComparisonAgent().compare(a, b)
    assert res.itens[0].status == StatusItem.SEM_BASE
    assert any("sem base de comparação" in av for av in res.avisos)


def test_limite_ausente_de_um_lado_nao_e_alteracao():
    a = _ap(extensoes=[ItemApolice(nome="Danos Morais", limite=None)])
    b = _ap(extensoes=[ItemApolice(nome="Danos Morais", limite="100% do LMI")])
    assert ComparisonAgent().compare(a, b).itens[0].status == StatusItem.MANTIDO
