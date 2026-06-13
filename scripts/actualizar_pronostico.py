```python
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


PAGINA_PRONOSTICO = "https://ws2.smn.gob.ar/pronostico"
API_PRONOSTICO = "https://ws1.smn.gob.ar/v1/forecast/location/map/2"

ARCHIVO_SALIDA = Path("docs/data/pronostico.json")

TIMEOUT = 40

HEADERS_WEB = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.7",
}

HEADERS_API = {
    "User-Agent": HEADERS_WEB["User-Agent"],
    "Accept": "application/json",
    "Origin": "https://ws2.smn.gob.ar",
    "Referer": "https://ws2.smn.gob.ar/pronostico",
}


def obtener_token(sesion: requests.Session) -> str:
    """
    Descarga la página pública del pronóstico y extrae el token temporal
    que el propio sitio guarda en localStorage.
    """
    respuesta = sesion.get(
        PAGINA_PRONOSTICO,
        headers=HEADERS_WEB,
        timeout=TIMEOUT,
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

            if token.count(".") == 2:
                return token

    raise RuntimeError(
        "No se pudo encontrar el token temporal en la página del pronóstico."
    )


def descargar_pronostico(
    sesion: requests.Session,
    token: str,
) -> list[dict]:
    headers = {
        **HEADERS_API,
        "Authorization": f"JWT {token}",
    }

    respuesta = sesion.get(
        API_PRONOSTICO,
        headers=headers,
        timeout=TIMEOUT,
    )
    respuesta.raise_for_status()

    datos = respuesta.json()

    if not isinstance(datos, list):
        raise RuntimeError(
            "La API respondió, pero el contenido no es una lista de localidades."
        )

    return datos


def guardar_resultado(localidades: list[dict]) -> None:
    ARCHIVO_SALIDA.parent.mkdir(parents=True, exist_ok=True)

    ahora = datetime.now(timezone.utc)

    resultado = {
        "source": "Servicio Meteorológico Nacional",
        "source_page": PAGINA_PRONOSTICO,
        "source_url": API_PRONOSTICO,
        "generated_at": ahora.isoformat(),
        "status": "ok",
        "count": len(localidades),
        "locations": localidades,
    }

    ARCHIVO_SALIDA.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def guardar_error(mensaje: str) -> None:
    """
    Deja registrado el error sin inventar datos meteorológicos.
    """
    ARCHIVO_SALIDA.parent.mkdir(parents=True, exist_ok=True)

    resultado = {
        "source": "Servicio Meteorológico Nacional",
        "source_page": PAGINA_PRONOSTICO,
        "source_url": API_PRONOSTICO,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "error",
        "count": 0,
        "error": mensaje,
        "locations": [],
    }

    ARCHIVO_SALIDA.write_text(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    try:
        with requests.Session() as sesion:
            token = obtener_token(sesion)
            localidades = descargar_pronostico(sesion, token)
            guardar_resultado(localidades)

        print(
            f"Pronóstico actualizado correctamente: "
            f"{len(localidades)} localidades."
        )
        print(f"Archivo generado: {ARCHIVO_SALIDA}")
        return 0

    except requests.HTTPError as error:
        codigo = (
            error.response.status_code
            if error.response is not None
            else "desconocido"
        )

        mensaje = f"Error HTTP al consultar el SMN: {codigo}"
        guardar_error(mensaje)
        print(mensaje, file=sys.stderr)
        return 1

    except requests.RequestException as error:
        mensaje = f"Error de conexión con el SMN: {error}"
        guardar_error(mensaje)
        print(mensaje, file=sys.stderr)
        return 1

    except (ValueError, RuntimeError) as error:
        mensaje = str(error)
        guardar_error(mensaje)
        print(mensaje, file=sys.stderr)
        return 1

    except Exception as error:
        mensaje = f"Error inesperado: {error}"
        guardar_error(mensaje)
        print(mensaje, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```
