```python
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


PAGINA_PRONOSTICO = "https://ws2.smn.gob.ar/pronostico"
API_BASE = "https://ws1.smn.gob.ar/v1"
LOCATION_ID = 4864

ARCHIVO_SALIDA = Path("docs/data/pronostico.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)


def obtener_token(session: requests.Session) -> str:
    respuesta = session.get(
        PAGINA_PRONOSTICO,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        },
        timeout=30,
    )

    respuesta.raise_for_status()
    html = respuesta.text

    patrones = [
        r"""localStorage\.setItem\(\s*['"]token['"]\s*,\s*['"]([^'"]+)['"]\s*\)""",
        r"""localStorage\[['"]token['"]\]\s*=\s*['"]([^'"]+)['"]""",
    ]

    for patron in patrones:
        coincidencia = re.search(patron, html)

        if coincidencia:
            token = coincidencia.group(1).strip()

            if token:
                return token

    raise RuntimeError(
        "No se pudo encontrar el token temporal en la página del SMN."
    )


def descargar_pronostico(
    session: requests.Session,
    token: str,
    location_id: int,
) -> dict:
    url = f"{API_BASE}/forecast/location/{location_id}"

    respuesta = session.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Authorization": f"JWT {token}",
            "Origin": "https://ws2.smn.gob.ar",
            "Referer": "https://ws2.smn.gob.ar/",
        },
        timeout=30,
    )

    respuesta.raise_for_status()

    datos = respuesta.json()

    if not isinstance(datos, dict):
        raise RuntimeError("El SMN respondió con un formato inesperado.")

    if "location" not in datos:
        raise RuntimeError("La respuesta no contiene información de la localidad.")

    if "forecast" not in datos:
        raise RuntimeError("La respuesta no contiene el pronóstico.")

    if not isinstance(datos["forecast"], list):
        raise RuntimeError("El campo forecast no es una lista.")

    if len(datos["forecast"]) == 0:
        raise RuntimeError("El pronóstico recibido está vacío.")

    return datos


def preparar_salida(datos: dict) -> dict:
    location = datos.get("location", {})
    forecast = datos.get("forecast", [])

    fechas = [
        periodo.get("date")
        for periodo in forecast
        if isinstance(periodo, dict) and periodo.get("date")
    ]

    return {
        "source": "Servicio Meteorológico Nacional",
        "source_page": PAGINA_PRONOSTICO,
        "source_api": f"{API_BASE}/forecast/location/{LOCATION_ID}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "location_id": LOCATION_ID,
        "location": location,
        "forecast_updated": datos.get("updated"),
        "forecast_days": len(forecast),
        "first_date": min(fechas) if fechas else None,
        "last_date": max(fechas) if fechas else None,
        "forecast": forecast,
    }


def guardar_json(datos: dict) -> None:
    ARCHIVO_SALIDA.parent.mkdir(parents=True, exist_ok=True)

    contenido = json.dumps(
        datos,
        ensure_ascii=False,
        indent=2,
    )

    ARCHIVO_SALIDA.write_text(
        contenido + "\n",
        encoding="utf-8",
    )


def main() -> None:
    print("Abriendo la página de pronóstico del SMN...")

    with requests.Session() as session:
        token = obtener_token(session)

        print("Token temporal obtenido correctamente.")
        print("Descargando pronóstico de Capital Federal...")

        datos_originales = descargar_pronostico(
            session,
            token,
            LOCATION_ID,
        )

    datos_salida = preparar_salida(datos_originales)
    guardar_json(datos_salida)

    print(f"Archivo generado: {ARCHIVO_SALIDA}")
    print(f"Localidad: {datos_salida['location'].get('name')}")
    print(f"Días recibidos: {datos_salida['forecast_days']}")
    print(
        "Período:",
        datos_salida["first_date"],
        "a",
        datos_salida["last_date"],
    )


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as error:
        print(f"Error HTTP consultando el SMN: {error}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as error:
        print(f"Error de conexión con el SMN: {error}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)
```
