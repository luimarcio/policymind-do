from dataclasses import replace

from conftest import FakeLLM

from src.agents.document_agent import DocumentAgent
from src.agents.policy_extraction_agent import PolicyExtractionAgent
from src.orchestrator import OrchestratorAgent
from src.schemas import StatusEtapa, StatusItem
from src.storage import PolicyStore


def _status(res):
    return {e.agente: e.status for e in res.etapas}


def test_pipeline_completo_com_ia(settings, pdf_a, pdf_b):
    llm = FakeLLM()
    atualizacoes = []
    orq = OrchestratorAgent(settings=settings, llm=llm, on_update=lambda e: atualizacoes.append(len(e)))
    res = orq.run([("a.pdf", pdf_a), ("b.pdf", pdf_b)])

    assert res.sucesso, res.erro_fatal
    assert all(s == StatusEtapa.SUCESSO for s in _status(res).values())
    assert atualizacoes  # a interface foi notificada
    a, b = res.apolices
    assert a.metodo_extracao == "llm" and a.modelo_llm == "fake-llm"
    assert a.seguradora.valor == "Seguradora Alfa S.A."
    assert b.premio_total.valor == 8590.40

    # guardrail: retroatividade inventada pelo "modelo" não existe no PDF -> descartada
    assert a.retroatividade.valor is None
    assert any("Retroatividade" in av for av in a.avisos)

    comp = res.comparacao
    assert comp.rotulo_a == "Seguradora Alfa S.A. 2024–2025"
    premio = next(d for d in comp.campos if d.campo == "premio_total")
    assert premio.variacao_percentual == -20.0
    status = {i.chave: i.status for i in comp.itens}
    assert status["praticas_trabalhistas"] == StatusItem.ADICIONADO
    assert status["custos_emergenciais"] == StatusItem.MANTIDO

    assert res.analise.gerado_por_ia is True
    assert res.analise.sintese
    assert "não substitui" in res.analise.ressalva
    assert llm.chamadas == 3  # 2 extrações + 1 análise


def test_ia_indisponivel_usa_regras_e_avisa(settings, pdf_a, pdf_b):
    res = OrchestratorAgent(settings=settings, llm=FakeLLM(falhar=True)).run([("a.pdf", pdf_a), ("b.pdf", pdf_b)])
    st = _status(res)
    assert res.sucesso
    assert st["PolicyExtractionAgent"] == StatusEtapa.ALERTA
    assert st["AnalysisAgent"] == StatusEtapa.ALERTA
    assert all(ap.metodo_extracao == "regras" for ap in res.apolices)
    assert res.analise.gerado_por_ia is False  # não finge que houve IA
    assert res.analise.sintese is None
    assert "indisponível" in res.analise.erro
    assert res.analise.destaques_regras  # fatos objetivos continuam disponíveis
    assert res.apolices[0].premio_total.valor == 10738.0


def test_ia_indisponivel_sem_fallback_registra_erro_sem_derrubar(settings, pdf_a, pdf_b):
    cfg = replace(settings, allow_rule_fallback=False)
    res = OrchestratorAgent(settings=cfg, llm=FakeLLM(falhar=True)).run([("a.pdf", pdf_a), ("b.pdf", pdf_b)])
    st = _status(res)
    assert res.sucesso is False
    assert st["PolicyExtractionAgent"] == StatusEtapa.ERRO
    assert st["ComparisonAgent"] == StatusEtapa.PENDENTE
    assert "OpenAI" in res.erro_fatal


def test_arquivo_invalido_vira_erro_na_etapa(settings, pdf_a):
    res = OrchestratorAgent(settings=settings, llm=FakeLLM()).run([("a.pdf", pdf_a), ("b.docx", b"xx")])
    assert res.sucesso is False
    assert _status(res)["DocumentAgent"] == StatusEtapa.ERRO


def test_exige_dois_documentos(settings, pdf_a):
    res = OrchestratorAgent(settings=settings, llm=FakeLLM()).run([("a.pdf", pdf_a)])
    assert res.sucesso is False and "dois" in res.erro_fatal


def test_cache_evita_nova_chamada_ao_llm(settings, pdf_a, pdf_b, tmp_path):
    store = PolicyStore(tmp_path / "cache.sqlite")
    llm = FakeLLM()
    arquivos = [("a.pdf", pdf_a), ("b.pdf", pdf_b)]
    OrchestratorAgent(settings=settings, llm=llm, store=store).run(arquivos)
    OrchestratorAgent(settings=settings, llm=llm, store=store).run(arquivos)
    assert llm.chamadas == 4  # 3 na 1ª execução + só a análise na 2ª
    assert len(store.list_all()) == 2


def test_campos_ausentes_ficam_nulos_na_extracao(settings, pdf_a):
    doc = DocumentAgent(settings).process("a.pdf", pdf_a)
    ap = PolicyExtractionAgent.parse_llm_output({"seguradora": None, "lmg": {"valor": None}, "coberturas": None})
    PolicyExtractionAgent.verify_evidence(ap, doc)
    assert ap.seguradora.valor is None
    assert ap.lmg.valor is None
    assert ap.coberturas == []


def test_llm_client_traduz_erros_e_json_invalido(settings):
    from types import SimpleNamespace

    import pytest

    from src.llm_client import LLMClient, LLMError

    def resposta(conteudo):
        msg = SimpleNamespace(message=SimpleNamespace(content=conteudo))
        return SimpleNamespace(choices=[msg])

    class ClienteFalso:
        def __init__(self, conteudo=None, erro=None):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
            self.conteudo, self.erro = conteudo, erro

        def create(self, **kw):
            if self.erro:
                raise self.erro
            return resposta(self.conteudo)

    assert LLMClient(settings, ClienteFalso('{"ok": 1}')).complete_json("s", "u") == {"ok": 1}
    with pytest.raises(LLMError, match="JSON válido"):
        LLMClient(settings, ClienteFalso("texto livre")).complete_json("s", "u")
    with pytest.raises(LLMError, match="Falha na chamada"):
        LLMClient(settings, ClienteFalso(erro=TimeoutError("timeout"))).complete_json("s", "u")
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        LLMClient(settings).complete_json("s", "u")


def test_base_url_configuravel(monkeypatch):
    from src.config import get_settings

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.x.ai/v1")
    assert get_settings().openai_base_url == "https://api.x.ai/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    assert get_settings().openai_base_url is None


def test_rate_limit_429_espera_e_repete(settings):
    from types import SimpleNamespace

    from src.llm_client import LLMClient

    class Erro429(Exception):
        status_code = 429

    chamadas, esperas = [], []

    def create(**kw):
        chamadas.append(kw)
        if len(chamadas) == 1:
            raise Erro429("Rate limit reached ... Please try again in 7.5s.")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

    cliente = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    llm = LLMClient(settings, cliente, sleep=esperas.append)
    assert llm.complete_json("s", "u") == {"ok": True}
    assert len(chamadas) == 2
    assert esperas == [8.5]
