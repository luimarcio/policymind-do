import pytest
from conftest import TEXTO_APOLICE_A, make_pdf, tesseract_disponivel

from src.agents.document_agent import DocumentAgent, DocumentError


def test_extrai_texto_de_pdf_textual(settings, pdf_a):
    doc = DocumentAgent(settings).process("apolice_a.pdf", pdf_a)
    assert doc.tipo_arquivo == "pdf"
    assert doc.metodo_extracao == "texto"
    assert doc.num_paginas == 1
    assert "Seguradora Alfa S.A." in doc.texto_completo
    assert "R$ 10.738,00" in doc.texto_completo
    assert len(doc.hash_sha256) == 64


def test_marcadores_de_pagina_para_citar_fonte(settings, pdf_a):
    doc = DocumentAgent(settings).process("a.pdf", pdf_a)
    assert doc.texto_com_marcadores().startswith("[PÁGINA 1]")


def test_pagina_sem_texto_aciona_ocr_como_fallback(settings, monkeypatch):
    agente = DocumentAgent(settings)
    chamadas = []

    def ocr_falso(img):
        chamadas.append(img.size)
        return "TEXTO RECONHECIDO POR OCR"

    monkeypatch.setattr(agente, "_run_tesseract", ocr_falso)
    doc = agente.process("misto.pdf", make_pdf(TEXTO_APOLICE_A, paginas_em_branco=1))
    assert len(chamadas) == 1  # só a página em branco passou pelo OCR
    assert [p.metodo for p in doc.paginas] == ["texto", "ocr"]
    assert doc.metodo_extracao == "misto"


def test_pdf_textual_nao_chama_ocr(settings, pdf_a, monkeypatch):
    agente = DocumentAgent(settings)
    monkeypatch.setattr(agente, "_run_tesseract", lambda img: pytest.fail("OCR não deveria ser chamado"))
    assert agente.process("a.pdf", pdf_a).metodo_extracao == "texto"


def test_formato_nao_suportado(settings):
    with pytest.raises(DocumentError, match="não suportado"):
        DocumentAgent(settings).process("planilha.xlsx", b"conteudo")


def test_arquivo_vazio(settings):
    with pytest.raises(DocumentError, match="vazio"):
        DocumentAgent(settings).process("a.pdf", b"")


def test_pdf_corrompido(settings):
    with pytest.raises(DocumentError):
        DocumentAgent(settings).process("a.pdf", b"isto nao e um pdf")


@tesseract_disponivel
def test_ocr_real_em_imagem(settings):
    import io

    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1400, 220), "white")
    draw = ImageDraw.Draw(img)
    try:
        fonte = ImageFont.truetype("DejaVuSans.ttf", 48)
    except OSError:
        fonte = ImageFont.load_default()
    draw.text((30, 70), "APOLICE DE SEGURO D&O FRANQUIA", fill="black", font=fonte)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    doc = DocumentAgent(settings).process("scan.png", buf.getvalue())
    assert doc.tipo_arquivo == "imagem"
    assert doc.metodo_extracao == "ocr"
    assert "FRANQUIA" in doc.texto_completo.upper()
