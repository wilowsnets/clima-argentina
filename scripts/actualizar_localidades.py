from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


SOURCE_URL = "https://ws.smn.gob.ar/map_items/weather"
ARCHIVO_ADICIONALES = Path("scripts/localidades_adicionales.json")
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


def descargar_localidades_historicas() -> list[dict[str, Any]]:
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
            "La fuente histórica no devolvió una lista de localidades."
        )

    return contenido


def limpiar_localidad_historica(
    registro: dict[str, Any],
) -> dict[str, Any] | None:
    localidad_id = convertir_entero(registro.get("lid"))
    nombre = str(registro.get("name") or "").strip()
    provincia = str(registro.get("province") or "").strip()

    if localidad_id is None or not nombre:
        return None

    return {
        "id": localidad_id,
        "name": nombre,
        "department": None,
        "province": provincia,
        "lat": convertir_decimal(registro.get("lat")),
        "lon": convertir_decimal(registro.get("lon")),
        "zoom": convertir_entero(registro.get("zoom")),
        "forecast_reference_id": convertir_entero(registro.get("fid")),
        "station_number": convertir_entero(registro.get("int_number")),
        "station_name": None,
        "distance_km": None,
        "area": None,
        "catalog_source": "historical_map_items_weather",
    }


def normalizar_catalogo_existente(
    registro: dict[str, Any],
) -> dict[str, Any] | None:
    localidad_id = convertir_entero(registro.get("id"))
    nombre = str(registro.get("name") or "").strip()
    provincia = str(registro.get("province") or "").strip()

    if localidad_id is None or not nombre:
        return None

    return {
        "id": localidad_id,
        "name": nombre,
        "department": (
            str(registro.get("department") or "").strip() or None
        ),
        "province": provincia,
        "lat": convertir_decimal(registro.get("lat")),
        "lon": convertir_decimal(registro.get("lon")),
        "zoom": convertir_entero(registro.get("zoom")),
        "forecast_reference_id": convertir_entero(
            registro.get("forecast_reference_id")
        ),
        "station_number": convertir_entero(
            registro.get("station_number")
        ),
        "station_name": (
            str(registro.get("station_name") or "").strip() or None
        ),
        "distance_km": convertir_decimal(registro.get("distance_km")),
        "area": str(registro.get("area") or "").strip() or None,
        "catalog_source": (
            str(registro.get("catalog_source") or "").strip()
            or "existing_catalog"
        ),
    }


def cargar_catalogo_existente() -> list[dict[str, Any]]:
    if not OUTPUT_FILE.exists():
        return []

    contenido = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    registros = contenido.get("localities")

    if not isinstance(registros, list):
        return []

    resultado: list[dict[str, Any]] = []

    for registro in registros:
        if not isinstance(registro, dict):
            continue

        localidad = normalizar_catalogo_existente(registro)
        if localidad is not None:
            resultado.append(localidad)

    return resultado


def cargar_localidades_adicionales() -> list[dict[str, Any]]:
    if not ARCHIVO_ADICIONALES.exists():
        raise RuntimeError(
            f"No existe {ARCHIVO_ADICIONALES}. "
            "Subí el archivo de localidades adicionales a la carpeta scripts."
        )

    contenido = json.loads(
        ARCHIVO_ADICIONALES.read_text(encoding="utf-8")
    )
    registros = contenido.get("localities")

    if not isinstance(registros, list):
        raise RuntimeError(
            "El archivo de localidades adicionales no contiene una lista válida."
        )

    resultado: list[dict[str, Any]] = []

    for registro in registros:
        if not isinstance(registro, dict):
            continue

        localidad_id = convertir_entero(registro.get("id"))
        nombre = str(registro.get("name") or "").strip()
        provincia = str(registro.get("province") or "").strip()

        if localidad_id is None or not nombre or not provincia:
            continue

        resultado.append(
            {
                "id": localidad_id,
                "name": nombre,
                "department": (
                    str(registro.get("department") or "").strip() or None
                ),
                "province": provincia,
                "lat": convertir_decimal(registro.get("lat")),
                "lon": convertir_decimal(registro.get("lon")),
                "zoom": convertir_entero(registro.get("zoom")),
                "forecast_reference_id": convertir_entero(
                    registro.get("forecast_reference_id")
                ),
                "station_number": convertir_entero(
                    registro.get("station_number")
                ),
                "station_name": (
                    str(registro.get("station_name") or "").strip() or None
                ),
                "distance_km": convertir_decimal(
                    registro.get("distance_km")
                ),
                "area": str(registro.get("area") or "").strip() or None,
                "catalog_source": "smn_current_search",
            }
        )

    if not resultado:
        raise RuntimeError(
            "No se encontraron localidades adicionales utilizables."
        )

    return resultado


def combinar_localidades(
    historicas: list[dict[str, Any]],
    adicionales: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    localidades_por_id: dict[int, dict[str, Any]] = {}

    for localidad in historicas:
        localidad_id = convertir_entero(localidad.get("id"))
        if localidad_id is None:
            continue
        localidades_por_id[localidad_id] = localidad

    duplicados_actualizados = 0

    for localidad in adicionales:
        localidad_id = int(localidad["id"])

        if localidad_id in localidades_por_id:
            duplicados_actualizados += 1

        localidades_por_id[localidad_id] = localidad

    localidades = list(localidades_por_id.values())
    localidades.sort(
        key=lambda localidad: (
            texto_ordenable(str(localidad.get("province") or "")),
            texto_ordenable(str(localidad.get("name") or "")),
            int(localidad.get("id") or 0),
        )
    )

    return localidades, duplicados_actualizados


def guardar_archivo(
    localidades: list[dict[str, Any]],
    cantidad_historicas: int,
    cantidad_adicionales: int,
    duplicados_actualizados: int,
    uso_catalogo_existente: bool,
) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    resultado = {
        "source": "Servicio Meteorológico Nacional",
        "source_url": SOURCE_URL,
        "additional_source_file": str(ARCHIVO_ADICIONALES),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(localidades),
        "historical_source_count": cantidad_historicas,
        "additional_source_count": cantidad_adicionales,
        "additional_records_replacing_existing_ids": duplicados_actualizados,
        "used_existing_catalog_as_fallback": uso_catalogo_existente,
        "warning": (
            "El catálogo combina la fuente histórica del SMN con localidades "
            "obtenidas del buscador actual. Los pronósticos se descargan usando "
            "primero el ID de cada localidad y, cuando es necesario, su ID de "
            "referencia meteorológica."
        ),
        "localities": localidades,
    }

    OUTPUT_FILE.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    uso_catalogo_existente = False

    try:
        print("Descargando catálogo histórico de localidades...")
        registros_historicos = descargar_localidades_historicas()
        historicas = [
            localidad
            for registro in registros_historicos
            if isinstance(registro, dict)
            for localidad in [limpiar_localidad_historica(registro)]
            if localidad is not None
        ]
    except Exception as error:
        print(f"No se pudo descargar la fuente histórica: {error}")
        print("Intentando conservar el catálogo existente...")
        historicas = cargar_catalogo_existente()
        uso_catalogo_existente = True

        if not historicas:
            raise RuntimeError(
                "No se pudo descargar la fuente histórica y tampoco existe "
                "un catálogo anterior para conservar."
            ) from error

    print("Cargando localidades adicionales...")
    adicionales = cargar_localidades_adicionales()

    print("Combinando catálogos...")
    localidades, duplicados = combinar_localidades(
        historicas,
        adicionales,
    )

    if len(localidades) < 100:
        raise RuntimeError(
            f"Solo se encontraron {len(localidades)} localidades."
        )

    guardar_archivo(
        localidades,
        cantidad_historicas=len(historicas),
        cantidad_adicionales=len(adicionales),
        duplicados_actualizados=duplicados,
        uso_catalogo_existente=uso_catalogo_existente,
    )

    print(f"Archivo generado: {OUTPUT_FILE}")
    print(f"Registros del catálogo base: {len(historicas)}")
    print(f"Registros adicionales: {len(adicionales)}")
    print(f"IDs adicionales que ya existían: {duplicados}")
    print(f"Localidades finales sin duplicados: {len(localidades)}")


if __name__ == "__main__":
    main()
