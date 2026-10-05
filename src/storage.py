"""Armazenamento estruturado em SQLite.

Guarda o JSON extraído de cada documento, indexado pelo hash do arquivo e pelo
modelo usado. Serve para:
- evitar chamar o LLM de novo para o mesmo arquivo (economia e demo mais rápida);
- manter um histórico consultável das apólices processadas.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from src.schemas import ApoliceExtraida

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS apolices (
    hash_sha256   TEXT NOT NULL,
    modelo        TEXT NOT NULL,
    nome_arquivo  TEXT,
    seguradora    TEXT,
    segurado      TEXT,
    numero_apolice TEXT,
    vigencia_inicio TEXT,
    vigencia_fim  TEXT,
    premio_total  REAL,
    json_extraido TEXT NOT NULL,
    criado_em     TEXT NOT NULL,
    PRIMARY KEY (hash_sha256, modelo)
);
"""


class PolicyStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(_DDL)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, hash_sha256: str, modelo: str) -> ApoliceExtraida | None:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT json_extraido FROM apolices WHERE hash_sha256=? AND modelo=?",
                    (hash_sha256, modelo),
                ).fetchone()
            return ApoliceExtraida.model_validate_json(row[0]) if row else None
        except Exception as exc:  # cache nunca deve derrubar o pipeline
            logger.warning("Falha ao ler cache: %s", exc)
            return None

    def save(self, hash_sha256: str, modelo: str, nome_arquivo: str, apolice: ApoliceExtraida) -> None:
        try:
            premio = apolice.premio_total.valor if isinstance(apolice.premio_total.valor, (int, float)) else None
            with self._conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO apolices VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        hash_sha256,
                        modelo,
                        nome_arquivo,
                        _s(apolice.seguradora.valor),
                        _s(apolice.segurado.valor),
                        _s(apolice.numero_apolice.valor),
                        _s(apolice.vigencia_inicio.valor),
                        _s(apolice.vigencia_fim.valor),
                        premio,
                        apolice.model_dump_json(),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
        except Exception as exc:
            logger.warning("Falha ao gravar cache: %s", exc)

    def list_all(self) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT nome_arquivo, modelo, seguradora, segurado, numero_apolice, vigencia_inicio,"
                " vigencia_fim, premio_total, criado_em FROM apolices ORDER BY criado_em DESC"
            ).fetchall()
        return [dict(r) for r in rows]


def _s(v) -> str | None:
    return None if v is None else str(v)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
