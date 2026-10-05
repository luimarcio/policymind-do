"""DocumentAgent — recebe o arquivo e devolve texto + metadados.

Estratégia (página a página):
1. PDF: tenta extrair o texto nativo com PyMuPDF (rápido e fiel).
2. Se a página tiver menos de `min_chars_per_page` caracteres, ela é tratada
   como digitalizada e passa por OCR (Tesseract) — o OCR é apenas fallback.
3. Imagem (JPG/PNG/TIFF): vai direto para o OCR.
"""

from __future__ import annotations

import hashlib
import io
import logging
import unicodedata
from pathlib import Path

from src.config import Settings, get_settings
from src.schemas import DocumentoProcessado, PaginaTexto

logger = logging.getLogger(__name__)

EXTENSOES_PDF = {".pdf"}
EXTENSOES_IMAGEM = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


class DocumentError(Exception):
    """Erro de leitura do documento (formato inválido, arquivo corrompido...)."""


class DocumentAgent:
    nome = "DocumentAgent"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ API
    def process(self, nome_arquivo: str, conteudo: bytes) -> DocumentoProcessado:
        if not conteudo:
            raise DocumentError(f"O arquivo '{nome_arquivo}' está vazio.")
        ext = Path(nome_arquivo).suffix.lower()
        if ext in EXTENSOES_PDF:
            return self._process_pdf(nome_arquivo, conteudo)
        if ext in EXTENSOES_IMAGEM:
            return self._process_image(nome_arquivo, conteudo)
        raise DocumentError(f"Formato '{ext or 'desconhecido'}' não suportado. Envie PDF ou imagem (JPG, PNG, TIFF).")

    def process_path(self, caminho: str | Path) -> DocumentoProcessado:
        caminho = Path(caminho)
        return self.process(caminho.name, caminho.read_bytes())

    # ------------------------------------------------------------------ PDF
    def _process_pdf(self, nome: str, conteudo: bytes) -> DocumentoProcessado:
        import pymupdf  # import local: o módulo só é exigido quando há PDF

        try:
            doc = pymupdf.open(stream=conteudo, filetype="pdf")
        except Exception as exc:  # PDF corrompido ou protegido
            raise DocumentError(f"Não foi possível abrir o PDF '{nome}': {exc}") from exc

        paginas: list[PaginaTexto] = []
        avisos: list[str] = []
        with doc:
            if doc.needs_pass:
                raise DocumentError(f"O PDF '{nome}' está protegido por senha.")
            for i, page in enumerate(doc, start=1):
                # sort=True reconstrói a ordem visual (esquerda→direita, cima→baixo),
                # mantendo rótulo e valor na mesma linha em formulários tabulares.
                bruto = page.get_text("text", sort=True) or ""
                # NFKC desfaz ligaduras tipográficas (ex.: "ﬁ" -> "fi").
                bruto = unicodedata.normalize("NFKC", bruto)
                texto = "\n".join(linha.rstrip() for linha in bruto.splitlines()).strip()
                if len(texto) >= self.settings.min_chars_per_page:
                    paginas.append(PaginaTexto(numero=i, texto=texto, metodo="texto"))
                    continue
                # Página sem texto suficiente -> OCR (fallback)
                texto_ocr = self._ocr_pdf_page(page, avisos)
                if texto_ocr:
                    paginas.append(PaginaTexto(numero=i, texto=texto_ocr, metodo="ocr"))
                else:
                    paginas.append(PaginaTexto(numero=i, texto=texto, metodo="vazia" if not texto else "texto"))

        return self._build(nome, "pdf", conteudo, paginas, avisos)

    def _ocr_pdf_page(self, page, avisos: list[str]) -> str:
        if not self.settings.ocr_enabled:
            avisos.append(f"Página {page.number + 1} sem texto extraível e OCR desativado.")
            return ""
        try:
            from PIL import Image

            pix = page.get_pixmap(dpi=self.settings.ocr_dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            return self._run_tesseract(img)
        except Exception as exc:
            avisos.append(f"OCR falhou na página {page.number + 1}: {exc}")
            return ""

    # --------------------------------------------------------------- Imagem
    def _process_image(self, nome: str, conteudo: bytes) -> DocumentoProcessado:
        avisos: list[str] = []
        if not self.settings.ocr_enabled:
            raise DocumentError("Imagens exigem OCR, mas o OCR está desativado (OCR_ENABLED=false).")
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(conteudo))
            img.load()
        except Exception as exc:
            raise DocumentError(f"Não foi possível abrir a imagem '{nome}': {exc}") from exc
        try:
            texto = self._run_tesseract(img)
        except Exception as exc:
            raise DocumentError(f"OCR indisponível ou com falha: {exc}") from exc
        paginas = [PaginaTexto(numero=1, texto=texto, metodo="ocr" if texto else "vazia")]
        return self._build(nome, "imagem", conteudo, paginas, avisos)

    # ---------------------------------------------------------------- OCR
    def _run_tesseract(self, img) -> str:
        import pytesseract

        if self.settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = self.settings.tesseract_cmd
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")
        lang = self.settings.ocr_lang
        try:
            disponiveis = set(pytesseract.get_languages(config=""))
            if lang not in disponiveis:
                logger.warning("Idioma OCR '%s' não instalado; usando 'eng'.", lang)
                lang = "eng"
        except Exception:
            pass  # versões antigas do Tesseract não listam idiomas
        texto = pytesseract.image_to_string(img, lang=lang)
        return unicodedata.normalize("NFKC", texto).strip()

    # ---------------------------------------------------------------- util
    def _build(self, nome, tipo, conteudo, paginas, avisos) -> DocumentoProcessado:
        metodos = {p.metodo for p in paginas if p.metodo != "vazia"}
        if not metodos:
            metodo = "falhou"
            avisos.append("Nenhum texto foi extraído do documento.")
        elif metodos == {"texto"}:
            metodo = "texto"
        elif metodos == {"ocr"}:
            metodo = "ocr"
        else:
            metodo = "misto"
        return DocumentoProcessado(
            nome_arquivo=nome,
            tipo_arquivo=tipo,
            hash_sha256=hashlib.sha256(conteudo).hexdigest(),
            tamanho_bytes=len(conteudo),
            num_paginas=len(paginas),
            metodo_extracao=metodo,
            paginas=paginas,
            avisos=avisos,
        )
