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

        try:
            localidad_id = int(localidad.get("id"))
        except (TypeError, ValueError):
            continue

        nombre = str(localidad.get("name") or "").strip()
        provincia = str(localidad.get("province") or "").strip()

        if not nombre:
            continue

        resultado.append(
            {
                **localidad,
                "id": localidad_id,
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

    respuesta.raise_for_status()
    datos = respuesta.json()

    if not isinstance(datos, dict):
        raise RuntimeError("La respuesta no es un objeto JSON.")

    pronostico = datos.get("forecast")
    if not isinstance(pronostico, list) or not pronostico:
        raise RuntimeError(
            "La respuesta no contiene un pronóstico válido."
        )

    return datos


def preparar_salida(
    datos: dict[str, Any],
    localidad_catalogo: dict[str, Any],
    localidad_id: int,
) -> dict[str, Any]:
    return {
        **datos,
        "catalog_location": {
            "id": localidad_id,
            "name": localidad_catalogo.get("name"),
            "province": localidad_catalogo.get("province"),
            "lat": localidad_catalogo.get("lat"),
            "lon": localidad_catalogo.get("lon"),
        },
        "source": "Servicio Meteorológico Nacional",
        "source_url": URL_PRONOSTICO.format(
            localidad_id=localidad_id
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

    for numero, localidad in enumerate(localidades, start=1):
        localidad_id = int(localidad["id"])
        nombre = str(localidad["name"])
        provincia = str(localidad.get("province") or "")

        print(
            f"[{numero}/{total}] Descargando {nombre}, "
            f"{provincia} (ID {localidad_id})..."
        )

        try:
            try:
                datos = descargar_pronostico(
                    sesion,
                    token,
                    localidad_id,
                )
            except PermissionError:
                print("  El token venció. Obteniendo uno nuevo...")
                token = obtener_token(sesion)
                datos = descargar_pronostico(
                    sesion,
                    token,
                    localidad_id,
                )

            salida = preparar_salida(
                datos,
                localidad,
                localidad_id,
            )

            escribir_json(
                CARPETA_TEMPORAL / f"{localidad_id}.json",
                salida,
            )

            if localidad_id == ID_CABA:
                datos_caba = salida

            disponibles.append(
                {
                    "id": localidad_id,
                    "name": nombre,
                    "province": provincia,
                    "file": f"{localidad_id}.json",
                    "days": len(salida.get("forecast", [])),
                }
            )

        except Exception as error:
            mensaje = str(error)
            print(f"  No disponible: {mensaje}")

            errores.append(
                {
                    "id": localidad_id,
                    "name": nombre,
                    "province": provincia,
                    "error": mensaje,
                }
            )

        time.sleep(PAUSA_ENTRE_PEDIDOS)

    resumen = {
        "source": "Servicio Meteorológico Nacional",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_count": total,
        "available_count": len(disponibles),
        "failed_count": len(errores),
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
    print(f"Resumen: {ARCHIVO_RESUMEN}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error general: {error}", file=sys.stderr)
        sys.exit(1)
