"""Funções utilitárias de texto, valores monetários e datas (pt-BR)."""

from __future__ import annotations

import re
import unicodedata
from datetime import date

_MONEY_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})*(?:,\d{2})|\d+(?:,\d{2}))(?![\d,])")
_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")
_ISO_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def strip_accents(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_for_match(texto: str | None) -> str:
    """Minúsculas, sem acentos, sem pontuação e com espaços colapsados.

    Usado para comparar nomes e para verificar se uma evidência existe no documento.
    """
    if not texto:
        return ""
    t = strip_accents(unicodedata.normalize("NFKC", texto)).lower()
    t = re.sub(r"[^a-z0-9%]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def parse_money_br(valor) -> float | None:
    """Converte 'R$ 10.880,98' -> 10880.98. Não arredonda nem altera o valor.

    Retorna None quando não há um valor monetário inequívoco.
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor)
    m = _MONEY_RE.search(texto)
    if not m:
        return None
    numero = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(numero)
    except ValueError:
        return None


def parse_date_br(valor) -> str | None:
    """Converte '30/09/2024' (ou ISO) para '2024-09-30'. Retorna None se inválida."""
    if valor is None:
        return None
    if isinstance(valor, date):
        return valor.isoformat()
    texto = str(valor).strip()
    m = _ISO_RE.match(texto)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = _DATE_RE.search(texto)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def format_money_br(valor: float | None) -> str:
    if valor is None:
        return "não identificado"
    s = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def format_date_br(iso: str | None) -> str:
    if not iso:
        return "não identificado"
    m = _ISO_RE.match(str(iso))
    if not m:
        return str(iso)
    y, mo, d = m.groups()
    return f"{d}/{mo}/{y}"


def evidence_in_text(evidencia: str | None, texto_documento_normalizado: str, min_ratio: float = 0.8) -> bool:
    """Verifica se uma evidência (trecho) aparece no documento.

    1º tenta correspondência exata (após normalização); se falhar, aceita quando
    ao menos `min_ratio` das palavras do trecho existem no documento — tolera
    pequenas diferenças de quebra de linha/layout sem aceitar texto inventado.
    """
    ev = normalize_for_match(evidencia)
    if not ev:
        return False
    if ev in texto_documento_normalizado:
        return True
    if any(ch.isdigit() for ch in ev):
        # números (valores, datas, percentuais) exigem correspondência exata
        return False
    palavras = [p for p in ev.split() if len(p) > 2]
    if not palavras:
        return False
    vocab = set(texto_documento_normalizado.split())
    encontradas = sum(1 for p in palavras if p in vocab)
    return encontradas / len(palavras) >= min_ratio
