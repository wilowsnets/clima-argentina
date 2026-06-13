from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PAGINA_PRONOSTICO = "https://ws2.smn.gob.ar/pronostico"
URL_PRONOSTICO = "https://ws1.smn.gob.ar/v1/forecast/location/4864"
ARCHIVO_SALIDA = Path("docs/data/pronostico.json")

ENCABEZADOS_BASE = {
"User-Agent": (
"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
"AppleWebKit/537.36 (KHTML, like Gecko) "
"Chrome/124.0 Safari/537.36"
),
"Accept": "application/json,text/html;q=0.9,*/*;q=0.8",
"Accept-Language": "es-AR,es;q=0.9",
}

def descargar(url: str, encabezados: dict[str, str]) -> str:
solicitud = Request(url, headers=encabezados)


with urlopen(solicitud, timeout=30) as respuesta:
    return respuesta.read().decode("utf-8", errors="replace")


def obtener_token(html: str) -> str:
patrones = [
r"""localStorage.setItem(\s*["']token["']\s*,\s*["']([^%22']+)["']\s*)""",
r"""localStorage.setItem(\s*`token`\s*,\s*`([^`]+)`\s*)""",
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


def obtener_pronostico(token: str) -> dict:
encabezados = {
**ENCABEZADOS_BASE,
"Accept": "application/json",
"Authorization": f"JWT {token}",
"Origin": "https://ws2.smn.gob.ar",
"Referer": "https://ws2.smn.gob.ar/",
}


contenido = descargar(URL_PRONOSTICO, encabezados)
datos = json.loads(contenido)

if not isinstance(datos, dict):
    raise RuntimeError("La respuesta del pronóstico no es un objeto JSON válido.")

if not isinstance(datos.get("forecast"), list):
    raise RuntimeError("La respuesta no contiene la lista forecast.")

if len(datos["forecast"]) == 0:
    raise RuntimeError("El pronóstico recibido está vacío.")

return datos


def guardar_pronostico(datos: dict) -> None:
datos["source"] = "Servicio Meteorológico Nacional"
datos["source_url"] = URL_PRONOSTICO
datos["generated_at"] = datetime.now(timezone.utc).isoformat()


ARCHIVO_SALIDA.parent.mkdir(parents=True, exist_ok=True)

archivo_temporal = ARCHIVO_SALIDA.with_suffix(".json.tmp")

archivo_temporal.write_text(
    json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

archivo_temporal.replace(ARCHIVO_SALIDA)


def main() -> None:
print("Descargando página del pronóstico...")
html = descargar(PAGINA_PRONOSTICO, ENCABEZADOS_BASE)


print("Obteniendo token temporal...")
token = obtener_token(html)

print("Descargando pronóstico de Capital Federal...")
datos = obtener_pronostico(token)

guardar_pronostico(datos)

localidad = datos.get("location", {}).get("name", "Localidad desconocida")
cantidad_dias = len(datos.get("forecast", []))

print(f"Pronóstico guardado para: {localidad}")
print(f"Días recibidos: {cantidad_dias}")
print(f"Archivo generado: {ARCHIVO_SALIDA}")


if **name** == "**main**":
try:
main()


except HTTPError as error:
    detalle = error.read().decode("utf-8", errors="replace")
    print(f"Error HTTP {error.code}: {detalle}", file=sys.stderr)
    sys.exit(1)

except URLError as error:
    print(f"Error de conexión: {error}", file=sys.stderr)
    sys.exit(1)

except Exception as error:
    print(f"Error: {error}", file=sys.stderr)
    sys.exit(1)
