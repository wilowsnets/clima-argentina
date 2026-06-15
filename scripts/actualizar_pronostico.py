from __future__ import annotations

import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


PAGINA_PRONOSTICO = "https://ws2.smn.gob.ar/pronostico"
URL_PRONOSTICO = "https://ws1.smn.gob.ar/v1/forecast/location/{localidad_id}"

ARCHIVO_LOCALIDADES = Path("docs/data/localidades.json")
CARPETA_PRONOSTICOS = Path("docs/data/pronosticos")
CARPETA_TEMPORAL = Path("docs/data/pronosticos_tmp")
ARCHIVO_RESUMEN = CARPETA_PRONOSTICOS / "index.json"
ARCHIVO_CABA = Path("docs/data/pronostico.json")

ID_CABA = 4864
PAUSA_ENTRE_PEDIDOS = 0.20

ENCABEZADOS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}


class ErrorPronostico(RuntimeError):
    def __init__(
        self,
        mensaje: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(mensaje)
        self.status_code = status_code


def convertir_entero(valor: Any) -> int | None:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def escribir_json(ruta: Path, datos: Any) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")

    temporal.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    temporal.replace(ruta)


def cargar_localidades() -> list[dict[str, Any]]:
    if not ARCHIVO_LOCALIDADES.exists():
        raise RuntimeError(
            f"No existe {ARCHIVO_LOCALIDADES}. "
            "Ejecutá primero scripts/actualizar_localidades.py."
        )

    contenido = json.loads(
        ARCHIVO_LOCALIDADES.read_text(encoding="utf-8")
    )
    localidades = contenido.get("localities")

    if not isinstance(localidades, list) or not localidades:
        raise RuntimeError(
            "El archivo de localidades no contiene una lista válida."
        )

    resultado: list[dict[str, Any]] = []

    for localidad in localidades:
        if not isinstance(localidad, dict):
            continue

        localidad_id = convertir_entero(localidad.get("id"))
        referencia_id = convertir_entero(
            localidad.get("forecast_reference_id")
        )
        nombre = str(localidad.get("name") or "").strip()
        provincia = str(localidad.get("province") or "").strip()

        if localidad_id is None or not nombre:
            continue

        resultado.append(
            {
                **localidad,
                "id": localidad_id,
                "forecast_reference_id": referencia_id,
                "name": nombre,
                "province": provincia,
            }
        )

    if not resultado:
        raise RuntimeError("No se encontraron localidades utilizables.")

    return resultado


def obtener_token(sesion: requests.Session) -> str:
    respuesta = sesion.get(
        PAGINA_PRONOSTICO,
        headers={
            **ENCABEZADOS_BASE,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=30,
    )
    respuesta.raise_for_status()

    patrones = [
        r'''localStorage\.setItem\(\s*["']token["']\s*,\s*["']([^"']+)["']\s*\)''',
        r'''localStorage\.setItem\(\s*`token`\s*,\s*`([^`]+)`\s*\)''',
    ]

    for patron in patrones:
        coincidencia = re.search(patron, respuesta.text)
        if coincidencia:
            token = coincidencia.group(1).strip()
            if token.count(".") == 2:
                return token

    raise RuntimeError(
        "No se pudo encontrar el token temporal del SMN."
    )


def descargar_pronostico(
    sesion: requests.Session,
    token: str,
    localidad_id: int,
) -> dict[str, Any]:
    url = URL_PRONOSTICO.format(localidad_id=localidad_id)

    respuesta = sesion.get(
        url,
        headers={
            **ENCABEZADOS_BASE,
            "Accept": "application/json",
            "Authorization": f"JWT {token}",
            "Origin": "https://ws2.smn.gob.ar",
            "Referer": "https://ws2.smn.gob.ar/",
        },
        timeout=30,
    )

    if respuesta.status_code in (401, 403):
        raise PermissionError(
            f"El token fue rechazado para la localidad {localidad_id}."
        )

    if not respuesta.ok:
        raise ErrorPronostico(
            f"HTTP {respuesta.status_code} para el ID {localidad_id}",
            status_code=respuesta.status_code,
        )

    try:
        datos = respuesta.json()
    except ValueError as error:
        raise ErrorPronostico(
            f"El ID {localidad_id} no devolvió JSON válido."
        ) from error

    if not isinstance(datos, dict):
        raise ErrorPronostico(
            f"La respuesta del ID {localidad_id} no es un objeto JSON."
        )

    pronostico = datos.get("forecast")
    if not isinstance(pronostico, list) or not pronostico:
        raise ErrorPronostico(
            f"El ID {localidad_id} no contiene un pronóstico válido."
        )

    return datos


def ids_para_probar(localidad: dict[str, Any]) -> list[int]:
    localidad_id = int(localidad["id"])
    referencia_id = convertir_entero(
        localidad.get("forecast_reference_id")
    )

    ids = [localidad_id]

    if referencia_id is not None and referencia_id not in ids:
        ids.append(referencia_id)

    return ids


def preparar_salida(
    datos: dict[str, Any],
    localidad_catalogo: dict[str, Any],
    id_pronostico_usado: int,
) -> dict[str, Any]:
    localidad_id = int(localidad_catalogo["id"])
    referencia_id = convertir_entero(
        localidad_catalogo.get("forecast_reference_id")
    )

    return {
        **datos,
        "catalog_location": {
            "id": localidad_id,
            "name": localidad_catalogo.get("name"),
            "department": localidad_catalogo.get("department"),
            "province": localidad_catalogo.get("province"),
            "lat": localidad_catalogo.get("lat"),
            "lon": localidad_catalogo.get("lon"),
            "area": localidad_catalogo.get("area"),
            "station_name": localidad_catalogo.get("station_name"),
            "station_number": localidad_catalogo.get("station_number"),
            "forecast_reference_id": referencia_id,
            "forecast_location_id": id_pronostico_usado,
            "used_reference": id_pronostico_usado != localidad_id,
        },
        "source": "Servicio Meteorológico Nacional",
        "source_url": URL_PRONOSTICO.format(
            localidad_id=id_pronostico_usado
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def preparar_carpeta_temporal() -> None:
    if CARPETA_TEMPORAL.exists():
        shutil.rmtree(CARPETA_TEMPORAL)

    CARPETA_TEMPORAL.mkdir(parents=True, exist_ok=True)


def publicar_carpeta_temporal() -> None:
    if CARPETA_PRONOSTICOS.exists():
        shutil.rmtree(CARPETA_PRONOSTICOS)

    CARPETA_TEMPORAL.replace(CARPETA_PRONOSTICOS)


def main() -> None:
    localidades = cargar_localidades()
    preparar_carpeta_temporal()

    sesion = requests.Session()

    print("Obteniendo token temporal del SMN...")
    token = obtener_token(sesion)

    disponibles: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    datos_caba: dict[str, Any] | None = None
    total = len(localidades)

    cache_pronosticos: dict[int, dict[str, Any]] = {}
    cache_errores: dict[int, str] = {}
    solicitudes_realizadas = 0
    localidades_con_referencia = 0

    for numero, localidad in enumerate(localidades, start=1):
        localidad_id = int(localidad["id"])
        nombre = str(localidad["name"])
        provincia = str(localidad.get("province") or "")
        candidatos = ids_para_probar(localidad)

        print(
            f"[{numero}/{total}] Procesando {nombre}, "
            f"{provincia} (ID {localidad_id})..."
        )

        datos: dict[str, Any] | None = None
        id_pronostico_usado: int | None = None
        intentos_fallidos: list[dict[str, Any]] = []

        for candidato_id in candidatos:
            if candidato_id in cache_pronosticos:
                datos = cache_pronosticos[candidato_id]
                id_pronostico_usado = candidato_id
                print(f"  Reutilizando pronóstico del ID {candidato_id}.")
                break

            if candidato_id in cache_errores:
                mensaje_cache = cache_errores[candidato_id]
                intentos_fallidos.append(
                    {
                        "id": candidato_id,
                        "error": mensaje_cache,
                        "cached": True,
                    }
                )
                print(
                    f"  El ID {candidato_id} ya había fallado: "
                    f"{mensaje_cache}"
                )
                continue

            try:
                try:
                    datos_descargados = descargar_pronostico(
                        sesion,
                        token,
                        candidato_id,
                    )
                    solicitudes_realizadas += 1
                except PermissionError:
                    print("  El token venció. Obteniendo uno nuevo...")
                    token = obtener_token(sesion)
                    datos_descargados = descargar_pronostico(
                        sesion,
                        token,
                        candidato_id,
                    )
                    solicitudes_realizadas += 1

                cache_pronosticos[candidato_id] = datos_descargados
                datos = datos_descargados
                id_pronostico_usado = candidato_id
                break

            except Exception as error:
                mensaje = str(error)
                cache_errores[candidato_id] = mensaje
                intentos_fallidos.append(
                    {
                        "id": candidato_id,
                        "error": mensaje,
                        "cached": False,
                    }
                )
                print(f"  ID {candidato_id} no disponible: {mensaje}")

            time.sleep(PAUSA_ENTRE_PEDIDOS)

        if datos is None or id_pronostico_usado is None:
            errores.append(
                {
                    "id": localidad_id,
                    "name": nombre,
                    "department": localidad.get("department"),
                    "province": provincia,
                    "forecast_reference_id": localidad.get(
                        "forecast_reference_id"
                    ),
                    "attempts": intentos_fallidos,
                }
            )
            continue

        salida = preparar_salida(
            datos,
            localidad,
            id_pronostico_usado,
        )

        escribir_json(
            CARPETA_TEMPORAL / f"{localidad_id}.json",
            salida,
        )

        uso_referencia = id_pronostico_usado != localidad_id
        if uso_referencia:
            localidades_con_referencia += 1
            print(
                f"  Se usó la referencia meteorológica "
                f"{id_pronostico_usado}."
            )

        if localidad_id == ID_CABA:
            datos_caba = salida

        disponibles.append(
            {
                "id": localidad_id,
                "name": nombre,
                "department": localidad.get("department"),
                "province": provincia,
                "area": localidad.get("area"),
                "lat": localidad.get("lat"),
                "lon": localidad.get("lon"),
                "file": f"{localidad_id}.json",
                "days": len(salida.get("forecast", [])),
                "forecast_reference_id": localidad.get(
                    "forecast_reference_id"
                ),
                "forecast_location_id": id_pronostico_usado,
                "used_reference": uso_referencia,
                "station_name": localidad.get("station_name"),
            }
        )

        time.sleep(PAUSA_ENTRE_PEDIDOS)

    resumen = {
        "source": "Servicio Meteorológico Nacional",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_count": total,
        "available_count": len(disponibles),
        "failed_count": len(errores),
        "locations_using_reference_count": localidades_con_referencia,
        "unique_forecast_ids_downloaded": len(cache_pronosticos),
        "http_forecast_requests": solicitudes_realizadas,
        "available": disponibles,
        "failed": errores,
    }

    escribir_json(CARPETA_TEMPORAL / "index.json", resumen)

    if len(disponibles) < 20:
        shutil.rmtree(CARPETA_TEMPORAL, ignore_errors=True)
        raise RuntimeError(
            "Se descargaron menos de 20 pronósticos. "
            "La respuesta del SMN parece bloqueada o incompleta."
        )

    publicar_carpeta_temporal()

    if datos_caba is not None:
        escribir_json(ARCHIVO_CABA, datos_caba)

    print("")
    print("Proceso terminado.")
    print(f"Localidades del catálogo: {total}")
    print(f"Pronósticos disponibles: {len(disponibles)}")
    print(f"Pronósticos no disponibles: {len(errores)}")
    print(
        "Localidades que usaron referencia meteorológica: "
        f"{localidades_con_referencia}"
    )
    print(f"IDs de pronóstico descargados: {len(cache_pronosticos)}")
    print(f"Resumen: {ARCHIVO_RESUMEN}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error general: {error}", file=sys.stderr)
        sys.exit(1)
