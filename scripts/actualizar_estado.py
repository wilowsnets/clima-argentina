from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


SOURCE_URL = "https://ws.smn.gob.ar/map_items/weather"
OUTPUT_FILE = Path("docs/data/estado_actual.json")

# Consideramos que una observación deja de ser actual después de 6 horas.
MAX_AGE_HOURS = 6


def convertir_timestamp(valor: Any) -> datetime | None:
    """Convierte un timestamp Unix a fecha UTC."""
    if valor is None:
        return None

    try:
        return datetime.fromtimestamp(float(valor), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def main() -> None:
    ahora = datetime.now(timezone.utc)

    response = requests.get(
        SOURCE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "clima-argentina/1.0",
        },
        timeout=30,
    )
    response.raise_for_status()

    estaciones = response.json()

    if not isinstance(estaciones, list):
        raise RuntimeError("La respuesta del SMN no es una lista de estaciones")

    estaciones_normalizadas = []
    fechas_validas: list[datetime] = []

    for estacion in estaciones:
        if not isinstance(estacion, dict):
            continue

        fecha_observacion = convertir_timestamp(estacion.get("updated"))

        if fecha_observacion is not None:
            fechas_validas.append(fecha_observacion)

        edad_horas = None
        vigente = False

        if fecha_observacion is not None:
            edad_horas = round(
                (ahora - fecha_observacion).total_seconds() / 3600,
                2,
            )
            vigente = 0 <= edad_horas <= MAX_AGE_HOURS

        estaciones_normalizadas.append(
            {
                **estacion,
                "observation_time": (
                    fecha_observacion.isoformat()
                    if fecha_observacion
                    else None
                ),
                "age_hours": edad_horas,
                "is_current": vigente,
            }
        )

    ultima_observacion = max(fechas_validas) if fechas_validas else None

    edad_ultima_horas = None
    fuente_actual = False

    if ultima_observacion is not None:
        edad_ultima_horas = round(
            (ahora - ultima_observacion).total_seconds() / 3600,
            2,
        )
        fuente_actual = 0 <= edad_ultima_horas <= MAX_AGE_HOURS

    cantidad_actuales = sum(
        1 for estacion in estaciones_normalizadas
        if estacion["is_current"]
    )

    resultado = {
        "source": "Servicio Meteorológico Nacional",
        "source_url": SOURCE_URL,
        "generated_at": ahora.isoformat(),
        "status": "current" if fuente_actual else "stale",
        "is_current": fuente_actual,
        "max_age_hours": MAX_AGE_HOURS,
        "latest_observation": (
            ultima_observacion.isoformat()
            if ultima_observacion
            else None
        ),
        "latest_observation_age_hours": edad_ultima_horas,
        "count": len(estaciones_normalizadas),
        "current_count": cantidad_actuales,
        "warning": (
            None
            if fuente_actual
            else (
                "La fuente respondió correctamente, pero las observaciones "
                "son antiguas y no deben mostrarse como estado actual."
            )
        ),
        "stations": estaciones_normalizadas,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    archivo_temporal = OUTPUT_FILE.with_suffix(".tmp")
    archivo_temporal.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    archivo_temporal.replace(OUTPUT_FILE)

    print(f"Estaciones recibidas: {len(estaciones_normalizadas)}")
    print(f"Estaciones actuales: {cantidad_actuales}")
    print(f"Estado de la fuente: {resultado['status']}")
    print(f"Última observación: {resultado['latest_observation']}")


if __name__ == "__main__":
    main()
