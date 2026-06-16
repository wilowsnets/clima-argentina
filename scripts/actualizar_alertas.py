from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


PAGINA_TOKEN = "https://ws2.smn.gob.ar/pronostico"
URL_ALERTAS = (
    "https://ws1.smn.gob.ar/v1/warning/alert/area"
    "?mode=alert&compact=true"
)
ARCHIVO_ALERTAS = Path("docs/data/alertas.json")
ZONA_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")

ENCABEZADOS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}

NIVELES = {
    1: "Sin alerta",
    2: "Sin alerta",
    3: "Amarillo",
    4: "Naranja",
    5: "Rojo",
}

# Estos nombres fueron identificados en el código público del mapa del SMN.
# Los demás identificadores se conservan sin asignarles un nombre inventado.
FENOMENOS = {
    37: "Lluvia",
    39: "Viento",
    41: "Tormenta",
    42: "Nevada",
    47: "Viento Zonda",
}


class TokenRechazado(RuntimeError):
    pass


def ahora_iso() -> str:
    return datetime.now(ZONA_ARGENTINA).isoformat(timespec="seconds")


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


def obtener_token(sesion: requests.Session) -> str:
    respuesta = sesion.get(
        PAGINA_TOKEN,
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

    raise RuntimeError("No se pudo encontrar el token temporal del SMN.")


def encabezados_api(token: str) -> dict[str, str]:
    return {
        **ENCABEZADOS_BASE,
        "Accept": "application/json",
        "Authorization": f"JWT {token}",
        "Origin": "https://www.smn.gob.ar",
        "Referer": "https://www.smn.gob.ar/alertas",
    }


def descargar_respuesta(
    sesion: requests.Session,
    token: str,
) -> Any:
    respuesta = sesion.get(
        URL_ALERTAS,
        headers=encabezados_api(token),
        timeout=45,
    )

    if respuesta.status_code in (401, 403):
        raise TokenRechazado("El token temporal fue rechazado.")

    respuesta.raise_for_status()

    try:
        return respuesta.json()
    except ValueError as error:
        raise RuntimeError("La respuesta de alertas no contiene JSON válido.") from error


def extraer_lista_areas(datos: Any) -> list[dict[str, Any]]:
    if isinstance(datos, list):
        return [registro for registro in datos if isinstance(registro, dict)]

    if isinstance(datos, dict):
        for clave in ("data", "areas", "results"):
            lista = datos.get(clave)
            if isinstance(lista, list):
                return [
                    registro
                    for registro in lista
                    if isinstance(registro, dict)
                ]

    raise RuntimeError("La respuesta de alertas tiene una estructura desconocida.")


def normalizar_evento(evento: dict[str, Any]) -> dict[str, Any] | None:
    evento_id = convertir_entero(evento.get("id"))
    nivel = convertir_entero(evento.get("max_level"))

    if evento_id is None or nivel is None or nivel < 3:
        return None

    return {
        "id": evento_id,
        "name": FENOMENOS.get(evento_id),
        "level": nivel,
        "level_name": NIVELES.get(nivel, f"Nivel {nivel}"),
    }


def construir_archivo(datos: Any) -> dict[str, Any]:
    areas = extraer_lista_areas(datos)
    alertas: list[dict[str, Any]] = []
    areas_afectadas: set[int] = set()
    nivel_maximo = 1
    ultima_actualizacion_smn: str | None = None

    for area in areas:
        area_id = convertir_entero(area.get("area_id"))
        if area_id is None:
            continue

        actualizada = area.get("updated")
        if isinstance(actualizada, str) and actualizada:
            if ultima_actualizacion_smn is None or actualizada > ultima_actualizacion_smn:
                ultima_actualizacion_smn = actualizada

        advertencias = area.get("warnings")
        if not isinstance(advertencias, list):
            continue

        for advertencia in advertencias:
            if not isinstance(advertencia, dict):
                continue

            nivel_declarado = convertir_entero(advertencia.get("max_level")) or 1
            eventos_originales = advertencia.get("events")
            eventos_activos: list[dict[str, Any]] = []

            if isinstance(eventos_originales, list):
                for evento_original in eventos_originales:
                    if not isinstance(evento_original, dict):
                        continue
                    evento = normalizar_evento(evento_original)
                    if evento is not None:
                        eventos_activos.append(evento)

            nivel_eventos = max(
                (evento["level"] for evento in eventos_activos),
                default=1,
            )
            nivel = max(nivel_declarado, nivel_eventos)

            # Para el SMN los niveles 1 y 2 significan "Sin alerta".
            if nivel < 3:
                continue

            fecha = advertencia.get("date")
            reportes = area.get("reports")

            alertas.append(
                {
                    "area_id": area_id,
                    "date": fecha if isinstance(fecha, str) else None,
                    "updated": actualizada if isinstance(actualizada, str) else None,
                    "level": nivel,
                    "level_name": NIVELES.get(nivel, f"Nivel {nivel}"),
                    "events": eventos_activos,
                    "reports": reportes if isinstance(reportes, list) else [],
                }
            )
            areas_afectadas.add(area_id)
            nivel_maximo = max(nivel_maximo, nivel)

    alertas.sort(
        key=lambda alerta: (
            alerta.get("date") or "",
            -(alerta.get("level") or 0),
            alerta.get("area_id") or 0,
        )
    )

    return {
        "generated_at": ahora_iso(),
        "last_success_at": ahora_iso(),
        "smn_updated_at": ultima_actualizacion_smn,
        "status": "con_alertas" if alertas else "sin_alertas",
        "source": "Servicio Meteorológico Nacional",
        "source_endpoint": "/v1/warning/alert/area?mode=alert&compact=true",
        "area_records_received": len(areas),
        "active_count": len(alertas),
        "affected_area_count": len(areas_afectadas),
        "max_level": nivel_maximo,
        "levels": {
            str(clave): valor
            for clave, valor in NIVELES.items()
        },
        "phenomena": {
            str(clave): valor
            for clave, valor in FENOMENOS.items()
        },
        "alerts": alertas,
    }


def main() -> int:
    sesion = requests.Session()

    try:
        token = obtener_token(sesion)

        try:
            respuesta = descargar_respuesta(sesion, token)
        except TokenRechazado:
            token = obtener_token(sesion)
            respuesta = descargar_respuesta(sesion, token)

        archivo = construir_archivo(respuesta)
        escribir_json(ARCHIVO_ALERTAS, archivo)

        print("Alertas actualizadas correctamente.")
        print(f"Registros de área recibidos: {archivo['area_records_received']}")
        print(f"Alertas activas: {archivo['active_count']}")
        print(f"Áreas afectadas: {archivo['affected_area_count']}")
        print(f"Nivel máximo: {archivo['max_level']}")
        return 0

    except Exception as error:
        # Una falla temporal no debe borrar el último archivo válido.
        print(f"No se pudieron actualizar las alertas: {error}")

        if ARCHIVO_ALERTAS.exists():
            print("Se conserva el último archivo docs/data/alertas.json.")
            return 0

        escribir_json(
            ARCHIVO_ALERTAS,
            {
                "generated_at": ahora_iso(),
                "last_success_at": None,
                "smn_updated_at": None,
                "status": "sin_datos",
                "source": "Servicio Meteorológico Nacional",
                "source_endpoint": (
                    "/v1/warning/alert/area?mode=alert&compact=true"
                ),
                "area_records_received": 0,
                "active_count": 0,
                "affected_area_count": 0,
                "max_level": 1,
                "levels": {
                    str(clave): valor
                    for clave, valor in NIVELES.items()
                },
                "phenomena": {
                    str(clave): valor
                    for clave, valor in FENOMENOS.items()
                },
                "alerts": [],
            },
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
