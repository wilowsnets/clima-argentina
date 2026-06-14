from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


SOURCE_URL = "https://ws.smn.gob.ar/map_items/weather"
OUTPUT_FILE = Path("docs/data/localidades.json")


def texto_ordenable(valor: str) -> str:
    normalizado = unicodedata.normalize("NFD", valor)
    sin_acentos = "".join(
        caracter
        for caracter in normalizado
        if unicodedata.category(caracter) != "Mn"
    )
    return sin_acentos.lower().strip()


def convertir_entero(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def convertir_decimal(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def descargar_localidades() -> list[dict[str, Any]]:
    respuesta = requests.get(
        SOURCE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 clima-argentina",
        },
        timeout=60,
    )
    respuesta.raise_for_status()

    contenido = respuesta.json()

    if not isinstance(contenido, list):
        raise RuntimeError(
            "La fuente no devolvió una lista de localidades."
        )

    return contenido


def limpiar_localidades(
    registros: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    localidades_por_id: dict[int, dict[str, Any]] = {}

    for registro in registros:
        if not isinstance(registro, dict):
            continue

        localidad_id = convertir_entero(registro.get("lid"))
        nombre = str(registro.get("name") or "").strip()
        provincia = str(registro.get("province") or "").strip()

        if localidad_id is None or not nombre:
            continue

        localidades_por_id[localidad_id] = {
            "id": localidad_id,
            "name": nombre,
            "province": provincia,
            "lat": convertir_decimal(registro.get("lat")),
            "lon": convertir_decimal(registro.get("lon")),
            "zoom": convertir_entero(registro.get("zoom")),
            "forecast_reference_id": convertir_entero(
                registro.get("fid")
            ),
            "station_number": convertir_entero(
                registro.get("int_number")
            ),
        }

    localidades = list(localidades_por_id.values())

    localidades.sort(
        key=lambda localidad: (
            texto_ordenable(localidad.get("province", "")),
            texto_ordenable(localidad.get("name", "")),
            localidad.get("id", 0),
        )
    )

    return localidades


def guardar_archivo(
    localidades: list[dict[str, Any]],
) -> None:
    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    resultado = {
        "source": "Servicio Meteorológico Nacional",
        "source_url": SOURCE_URL,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "count": len(localidades),
        "warning": (
            "Este archivo utiliza solamente los identificadores, "
            "nombres y coordenadas de la fuente histórica. "
            "Los datos meteorológicos antiguos no fueron incluidos."
        ),
        "localities": localidades,
    }

    OUTPUT_FILE.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    print("Descargando catálogo de localidades...")
    registros = descargar_localidades()

    print("Limpiando catálogo...")
    localidades = limpiar_localidades(registros)

    if len(localidades) < 50:
        raise RuntimeError(
            f"Solo se encontraron {len(localidades)} localidades."
        )

    guardar_archivo(localidades)

    print(f"Archivo generado: {OUTPUT_FILE}")
    print(f"Localidades encontradas: {len(localidades)}")


if __name__ == "__main__":
    main()
