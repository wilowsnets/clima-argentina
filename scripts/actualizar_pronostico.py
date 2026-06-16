from __future__ import annotations

import copy
import json
import re
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


PAGINA_PRONOSTICO = "https://ws2.smn.gob.ar/pronostico"
URL_PRONOSTICO_MODERNO = (
    "https://ws1.smn.gob.ar/v1/forecast/location/{localidad_id}"
)
URL_TIEMPO_ACTUAL = (
    "https://ws1.smn.gob.ar/v1/weather/location/{localidad_id}"
)
URL_PRONOSTICO_LEGADO = (
    "https://ws.smn.gob.ar/forecast/location/{localidad_id}"
)

ARCHIVO_LOCALIDADES = Path("docs/data/localidades.json")
ARCHIVO_OBSERVACIONES_ANTARTIDA = Path(
    "scripts/observaciones_antartida.json"
)
CARPETA_PRONOSTICOS = Path("docs/data/pronosticos")
CARPETA_TEMPORAL = Path("docs/data/pronosticos_tmp")
ARCHIVO_RESUMEN = CARPETA_PRONOSTICOS / "index.json"
ARCHIVO_CABA = Path("docs/data/pronostico.json")

ID_CABA = 4864
IDS_ANTARTIDA = {10806, 10810, 10811, 10814, 10817, 10818}
PAUSA_ENTRE_PEDIDOS = 0.20
DIAS_MAXIMOS_PRONOSTICO_LEGADO = 14
ZONA_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")

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


class TokenRechazado(RuntimeError):
    pass


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


def leer_json(ruta: Path) -> Any:
    return json.loads(ruta.read_text(encoding="utf-8"))


def cargar_localidades() -> list[dict[str, Any]]:
    if not ARCHIVO_LOCALIDADES.exists():
        raise RuntimeError(
            f"No existe {ARCHIVO_LOCALIDADES}. "
            "Ejecutá primero scripts/actualizar_localidades.py."
        )

    contenido = leer_json(ARCHIVO_LOCALIDADES)
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


def cargar_observaciones_iniciales() -> dict[int, dict[str, Any]]:
    if not ARCHIVO_OBSERVACIONES_ANTARTIDA.exists():
        return {}

    contenido = leer_json(ARCHIVO_OBSERVACIONES_ANTARTIDA)
    observaciones = contenido.get("observations")

    if not isinstance(observaciones, dict):
        return {}

    resultado: dict[int, dict[str, Any]] = {}

    for clave, registro in observaciones.items():
        localidad_id = convertir_entero(clave)
        if localidad_id is None or not isinstance(registro, dict):
            continue
        resultado[localidad_id] = registro

    return resultado


def cargar_archivo_anterior(localidad_id: int) -> dict[str, Any] | None:
    ruta = CARPETA_PRONOSTICOS / f"{localidad_id}.json"

    if not ruta.exists():
        return None

    try:
        contenido = leer_json(ruta)
    except (OSError, ValueError):
        return None

    if not isinstance(contenido, dict):
        return None

    pronostico = contenido.get("forecast")
    condiciones = contenido.get("current_conditions")

    if isinstance(pronostico, list) or isinstance(condiciones, dict):
        return contenido

    return None


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

    raise RuntimeError("No se pudo encontrar el token temporal del SMN.")


def encabezados_api(token: str) -> dict[str, str]:
    return {
        **ENCABEZADOS_BASE,
        "Accept": "application/json",
        "Authorization": f"JWT {token}",
        "Origin": "https://ws2.smn.gob.ar",
        "Referer": "https://ws2.smn.gob.ar/",
    }


def obtener_json_respuesta(
    respuesta: requests.Response,
    descripcion: str,
) -> Any:
    if respuesta.status_code in (401, 403):
        raise TokenRechazado(
            f"El token fue rechazado al consultar {descripcion}."
        )

    if not respuesta.ok:
        raise ErrorPronostico(
            f"HTTP {respuesta.status_code} al consultar {descripcion}",
            status_code=respuesta.status_code,
        )

    try:
        return respuesta.json()
    except ValueError as error:
        raise ErrorPronostico(
            f"La consulta de {descripcion} no devolvió JSON válido."
        ) from error


def descargar_pronostico_moderno(
    sesion: requests.Session,
    token: str,
    localidad_id: int,
) -> dict[str, Any]:
    url = URL_PRONOSTICO_MODERNO.format(localidad_id=localidad_id)
    respuesta = sesion.get(
        url,
        headers=encabezados_api(token),
        timeout=30,
    )
    datos = obtener_json_respuesta(
        respuesta,
        f"el pronóstico moderno del ID {localidad_id}",
    )

    if not isinstance(datos, dict):
        raise ErrorPronostico(
            f"El ID {localidad_id} no devolvió un objeto JSON."
        )

    pronostico = datos.get("forecast")
    if not isinstance(pronostico, list) or not pronostico:
        raise ErrorPronostico(
            f"El ID {localidad_id} no contiene un pronóstico válido."
        )

    return datos


def descargar_tiempo_actual(
    sesion: requests.Session,
    token: str,
    localidad_id: int,
) -> dict[str, Any]:
    url = URL_TIEMPO_ACTUAL.format(localidad_id=localidad_id)
    respuesta = sesion.get(
        url,
        headers=encabezados_api(token),
        timeout=30,
    )
    datos = obtener_json_respuesta(
        respuesta,
        f"el estado actual del ID {localidad_id}",
    )

    if not isinstance(datos, dict):
        raise ErrorPronostico(
            f"El estado actual del ID {localidad_id} no es un objeto JSON."
        )

    if datos.get("temperature") is None and not datos.get("weather"):
        raise ErrorPronostico(
            f"El estado actual del ID {localidad_id} está vacío."
        )

    return datos


def ordenar_clave_pronostico(clave: Any) -> tuple[int, str]:
    texto = str(clave)
    try:
        return int(texto), texto
    except ValueError:
        return 10_000, texto


def normalizar_pronostico_legado(datos: Any) -> dict[str, Any]:
    registro: dict[str, Any] | None = None

    if isinstance(datos, list):
        for elemento in datos:
            if isinstance(elemento, dict) and elemento.get("forecast"):
                registro = copy.deepcopy(elemento)
                break
    elif isinstance(datos, dict):
        registro = copy.deepcopy(datos)

    if registro is None:
        raise ErrorPronostico(
            "La fuente histórica no devolvió un registro utilizable."
        )

    pronostico_original = registro.get("forecast")

    if isinstance(pronostico_original, dict):
        pronostico = [
            valor
            for _, valor in sorted(
                pronostico_original.items(),
                key=lambda item: ordenar_clave_pronostico(item[0]),
            )
            if isinstance(valor, dict)
        ]
    elif isinstance(pronostico_original, list):
        pronostico = [
            valor
            for valor in pronostico_original
            if isinstance(valor, dict)
        ]
    else:
        pronostico = []

    if not pronostico:
        raise ErrorPronostico(
            "La fuente histórica no contiene días de pronóstico."
        )

    registro["forecast"] = pronostico
    return registro


def fechas_pronostico(datos: dict[str, Any]) -> list[date]:
    resultado: list[date] = []
    pronostico = datos.get("forecast")

    if not isinstance(pronostico, list):
        return resultado

    for dia in pronostico:
        if not isinstance(dia, dict):
            continue

        valor = str(dia.get("date") or "").strip()
        try:
            resultado.append(date.fromisoformat(valor))
        except ValueError:
            continue

    return resultado


def pronostico_legado_es_aceptable(datos: dict[str, Any]) -> bool:
    fechas = fechas_pronostico(datos)
    if not fechas:
        return False

    hoy = datetime.now(ZONA_ARGENTINA).date()
    fecha_mas_reciente = max(fechas)
    limite = hoy - timedelta(days=DIAS_MAXIMOS_PRONOSTICO_LEGADO)

    return fecha_mas_reciente >= limite


def descargar_pronostico_legado(
    sesion: requests.Session,
    localidad_id: int,
) -> dict[str, Any]:
    url = URL_PRONOSTICO_LEGADO.format(localidad_id=localidad_id)
    respuesta = sesion.get(
        url,
        headers={
            **ENCABEZADOS_BASE,
            "Accept": "application/json",
        },
        timeout=30,
    )

    if not respuesta.ok:
        raise ErrorPronostico(
            f"HTTP {respuesta.status_code} en la fuente histórica "
            f"para el ID {localidad_id}",
            status_code=respuesta.status_code,
        )

    try:
        datos = respuesta.json()
    except ValueError as error:
        raise ErrorPronostico(
            f"La fuente histórica del ID {localidad_id} no devolvió JSON."
        ) from error

    normalizado = normalizar_pronostico_legado(datos)

    if not pronostico_legado_es_aceptable(normalizado):
        fechas = fechas_pronostico(normalizado)
        ultima = max(fechas).isoformat() if fechas else "desconocida"
        raise ErrorPronostico(
            "El pronóstico histórico está demasiado desactualizado "
            f"(última fecha: {ultima})."
        )

    return normalizado


def ids_para_probar(localidad: dict[str, Any]) -> list[int]:
    localidad_id = int(localidad["id"])
    referencia_id = convertir_entero(
        localidad.get("forecast_reference_id")
    )

    ids = [localidad_id]

    if referencia_id is not None and referencia_id not in ids:
        ids.append(referencia_id)

    return ids


def ubicacion_catalogo(localidad: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(localidad["id"]),
        "name": localidad.get("name"),
        "department": localidad.get("department"),
        "province": localidad.get("province"),
        "lat": localidad.get("lat"),
        "lon": localidad.get("lon"),
    }


def metadatos_catalogo(
    localidad: dict[str, Any],
    id_pronostico_usado: int | None,
) -> dict[str, Any]:
    localidad_id = int(localidad["id"])
    referencia_id = convertir_entero(
        localidad.get("forecast_reference_id")
    )

    return {
        "id": localidad_id,
        "name": localidad.get("name"),
        "department": localidad.get("department"),
        "province": localidad.get("province"),
        "lat": localidad.get("lat"),
        "lon": localidad.get("lon"),
        "area": localidad.get("area"),
        "station_name": localidad.get("station_name"),
        "station_number": localidad.get("station_number"),
        "forecast_reference_id": referencia_id,
        "forecast_location_id": id_pronostico_usado,
        "used_reference": (
            id_pronostico_usado is not None
            and id_pronostico_usado != localidad_id
        ),
    }


def preparar_salida_pronostico(
    datos: dict[str, Any],
    localidad: dict[str, Any],
    id_pronostico_usado: int,
    tipo_fuente: str,
    url_fuente: str,
    condiciones: dict[str, Any] | None = None,
) -> dict[str, Any]:
    salida = copy.deepcopy(datos)
    ubicacion_original = salida.get("location")

    if isinstance(ubicacion_original, dict):
        salida["source_location"] = ubicacion_original

    salida["location"] = ubicacion_catalogo(localidad)
    salida["forecast"] = list(salida.get("forecast") or [])

    if condiciones is not None:
        salida["current_conditions"] = condiciones

    salida["catalog_location"] = metadatos_catalogo(
        localidad,
        id_pronostico_usado,
    )
    salida["data_status"] = "forecast_available"
    salida["forecast_source_type"] = tipo_fuente
    salida["source"] = "Servicio Meteorológico Nacional"
    salida["source_url"] = url_fuente
    salida["generated_at"] = datetime.now(timezone.utc).isoformat()

    return salida


def normalizar_observacion_smn(datos: dict[str, Any]) -> dict[str, Any]:
    salida = copy.deepcopy(datos)
    salida.pop("location", None)
    return salida


def preparar_salida_observacion(
    localidad: dict[str, Any],
    condiciones: dict[str, Any],
    tipo_fuente: str,
    url_fuente: str,
    anterior: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pronostico_anterior: list[dict[str, Any]] = []

    if anterior is not None and isinstance(anterior.get("forecast"), list):
        pronostico_anterior = copy.deepcopy(anterior["forecast"])

    salida: dict[str, Any] = {
        "location": ubicacion_catalogo(localidad),
        "forecast": pronostico_anterior,
        "current_conditions": condiciones,
        "catalog_location": metadatos_catalogo(localidad, None),
        "data_status": (
            "forecast_preserved_with_current_observation"
            if pronostico_anterior
            else "observation_only"
        ),
        "observation_source_type": tipo_fuente,
        "source": "Servicio Meteorológico Nacional",
        "source_url": url_fuente,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if anterior is not None:
        salida["previous_generated_at"] = anterior.get("generated_at")
        salida["previous_source_url"] = anterior.get("source_url")

    return salida


def fecha_observacion(datos: dict[str, Any] | None) -> datetime | None:
    if not isinstance(datos, dict):
        return None

    valor = str(datos.get("date") or "").strip()
    if not valor:
        return None

    try:
        fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None

    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=ZONA_ARGENTINA)

    return fecha.astimezone(timezone.utc)


def seleccionar_condiciones_mas_recientes(
    nuevas: dict[str, Any] | None,
    anterior: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    anteriores = None

    if anterior is not None and isinstance(
        anterior.get("current_conditions"),
        dict,
    ):
        anteriores = anterior["current_conditions"]

    if nuevas is None:
        return anteriores, False

    if anteriores is None:
        return nuevas, True

    fecha_nueva = fecha_observacion(nuevas)
    fecha_anterior = fecha_observacion(anteriores)

    if fecha_anterior is not None and fecha_nueva is None:
        return anteriores, False

    if (
        fecha_anterior is not None
        and fecha_nueva is not None
        and fecha_nueva <= fecha_anterior
    ):
        return anteriores, False

    return nuevas, True


def observacion_inicial_para_localidad(
    localidad: dict[str, Any],
    registro: dict[str, Any],
) -> dict[str, Any] | None:
    observacion = registro.get("observation")
    if not isinstance(observacion, dict):
        return None

    return preparar_salida_observacion(
        localidad=localidad,
        condiciones=copy.deepcopy(observacion),
        tipo_fuente="initial_backup",
        url_fuente="SMN Estado del Tiempo - registro inicial",
        anterior=None,
    )


def preparar_carpeta_temporal() -> None:
    if CARPETA_TEMPORAL.exists():
        shutil.rmtree(CARPETA_TEMPORAL)
    CARPETA_TEMPORAL.mkdir(parents=True, exist_ok=True)


def publicar_carpeta_temporal() -> None:
    if CARPETA_PRONOSTICOS.exists():
        shutil.rmtree(CARPETA_PRONOSTICOS)
    CARPETA_TEMPORAL.replace(CARPETA_PRONOSTICOS)


def refrescar_token(
    sesion: requests.Session,
    token_actual: str | None,
) -> str | None:
    try:
        return obtener_token(sesion)
    except Exception as error:
        if token_actual is None:
            print(f"No se pudo obtener el token temporal: {error}")
        else:
            print(f"No se pudo renovar el token temporal: {error}")
        return None


def descargar_con_token_renovable(
    sesion: requests.Session,
    token: str | None,
    funcion: Any,
    localidad_id: int,
) -> tuple[dict[str, Any], str]:
    token_util = token

    if token_util is None:
        token_util = refrescar_token(sesion, None)

    if token_util is None:
        raise RuntimeError("No hay token temporal disponible.")

    try:
        return funcion(sesion, token_util, localidad_id), token_util
    except TokenRechazado:
        print("  El token venció. Obteniendo uno nuevo...")
        token_util = refrescar_token(sesion, token_util)
        if token_util is None:
            raise RuntimeError("No se pudo renovar el token temporal.")
        return funcion(sesion, token_util, localidad_id), token_util


def registrar_disponible(
    disponibles: list[dict[str, Any]],
    localidad: dict[str, Any],
    salida: dict[str, Any],
    id_pronostico_usado: int | None,
    conservado: bool,
) -> None:
    localidad_id = int(localidad["id"])
    pronostico = salida.get("forecast")
    dias = len(pronostico) if isinstance(pronostico, list) else 0
    uso_referencia = (
        id_pronostico_usado is not None
        and id_pronostico_usado != localidad_id
    )

    disponibles.append(
        {
            "id": localidad_id,
            "name": localidad.get("name"),
            "department": localidad.get("department"),
            "province": localidad.get("province"),
            "area": localidad.get("area"),
            "lat": localidad.get("lat"),
            "lon": localidad.get("lon"),
            "file": f"{localidad_id}.json",
            "days": dias,
            "has_forecast": dias > 0,
            "has_current_conditions": isinstance(
                salida.get("current_conditions"),
                dict,
            ),
            "data_status": salida.get("data_status"),
            "forecast_reference_id": localidad.get(
                "forecast_reference_id"
            ),
            "forecast_location_id": id_pronostico_usado,
            "used_reference": uso_referencia,
            "preserved_previous_file": conservado,
            "station_name": localidad.get("station_name"),
        }
    )


def main() -> None:
    localidades = cargar_localidades()
    observaciones_iniciales = cargar_observaciones_iniciales()
    preparar_carpeta_temporal()

    sesion = requests.Session()
    print("Obteniendo token temporal del SMN...")
    token = refrescar_token(sesion, None)

    disponibles: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []
    datos_caba: dict[str, Any] | None = None
    total = len(localidades)

    cache_moderno: dict[int, dict[str, Any]] = {}
    cache_moderno_error: dict[int, str] = {}
    cache_legado: dict[int, dict[str, Any]] = {}
    cache_legado_error: dict[int, str] = {}
    cache_observaciones: dict[int, dict[str, Any]] = {}
    cache_observaciones_error: dict[int, str] = {}

    solicitudes_modernas = 0
    solicitudes_legadas = 0
    solicitudes_observaciones = 0
    localidades_con_referencia = 0
    archivos_conservados = 0
    observaciones_sin_pronostico = 0
    pronosticos_legados_usados = 0

    for numero, localidad in enumerate(localidades, start=1):
        localidad_id = int(localidad["id"])
        nombre = str(localidad["name"])
        provincia = str(localidad.get("province") or "")
        candidatos = ids_para_probar(localidad)
        anterior = cargar_archivo_anterior(localidad_id)

        print(
            f"[{numero}/{total}] Procesando {nombre}, "
            f"{provincia} (ID {localidad_id})..."
        )

        datos_pronostico: dict[str, Any] | None = None
        id_pronostico_usado: int | None = None
        tipo_fuente_pronostico: str | None = None
        url_fuente_pronostico: str | None = None
        intentos_fallidos: list[dict[str, Any]] = []

        for candidato_id in candidatos:
            if candidato_id in cache_moderno:
                datos_pronostico = cache_moderno[candidato_id]
                id_pronostico_usado = candidato_id
                tipo_fuente_pronostico = "ws1_current_api"
                url_fuente_pronostico = URL_PRONOSTICO_MODERNO.format(
                    localidad_id=candidato_id
                )
                print(f"  Reutilizando pronóstico del ID {candidato_id}.")
                break

            if candidato_id in cache_moderno_error:
                mensaje = cache_moderno_error[candidato_id]
                intentos_fallidos.append(
                    {
                        "source": "ws1_current_api",
                        "id": candidato_id,
                        "error": mensaje,
                        "cached": True,
                    }
                )
                continue

            try:
                datos_descargados, token = descargar_con_token_renovable(
                    sesion,
                    token,
                    descargar_pronostico_moderno,
                    candidato_id,
                )
                solicitudes_modernas += 1
                cache_moderno[candidato_id] = datos_descargados
                datos_pronostico = datos_descargados
                id_pronostico_usado = candidato_id
                tipo_fuente_pronostico = "ws1_current_api"
                url_fuente_pronostico = URL_PRONOSTICO_MODERNO.format(
                    localidad_id=candidato_id
                )
                break
            except Exception as error:
                mensaje = str(error)
                cache_moderno_error[candidato_id] = mensaje
                intentos_fallidos.append(
                    {
                        "source": "ws1_current_api",
                        "id": candidato_id,
                        "error": mensaje,
                        "cached": False,
                    }
                )
                print(
                    f"  Pronóstico moderno {candidato_id} "
                    f"no disponible: {mensaje}"
                )

            time.sleep(PAUSA_ENTRE_PEDIDOS)

        if datos_pronostico is None and localidad_id in IDS_ANTARTIDA:
            for candidato_id in candidatos:
                if candidato_id in cache_legado:
                    datos_pronostico = cache_legado[candidato_id]
                    id_pronostico_usado = candidato_id
                    tipo_fuente_pronostico = "ws_legacy_api"
                    url_fuente_pronostico = URL_PRONOSTICO_LEGADO.format(
                        localidad_id=candidato_id
                    )
                    break

                if candidato_id in cache_legado_error:
                    mensaje = cache_legado_error[candidato_id]
                    intentos_fallidos.append(
                        {
                            "source": "ws_legacy_api",
                            "id": candidato_id,
                            "error": mensaje,
                            "cached": True,
                        }
                    )
                    continue

                try:
                    datos_descargados = descargar_pronostico_legado(
                        sesion,
                        candidato_id,
                    )
                    solicitudes_legadas += 1
                    cache_legado[candidato_id] = datos_descargados
                    datos_pronostico = datos_descargados
                    id_pronostico_usado = candidato_id
                    tipo_fuente_pronostico = "ws_legacy_api"
                    url_fuente_pronostico = URL_PRONOSTICO_LEGADO.format(
                        localidad_id=candidato_id
                    )
                    pronosticos_legados_usados += 1
                    print(
                        f"  Se usó el pronóstico histórico reciente "
                        f"del ID {candidato_id}."
                    )
                    break
                except Exception as error:
                    mensaje = str(error)
                    cache_legado_error[candidato_id] = mensaje
                    intentos_fallidos.append(
                        {
                            "source": "ws_legacy_api",
                            "id": candidato_id,
                            "error": mensaje,
                            "cached": False,
                        }
                    )
                    print(
                        f"  Pronóstico histórico {candidato_id} "
                        f"no utilizable: {mensaje}"
                    )

                time.sleep(PAUSA_ENTRE_PEDIDOS)

        condiciones: dict[str, Any] | None = None
        tipo_fuente_observacion: str | None = None
        url_fuente_observacion: str | None = None

        if localidad_id in IDS_ANTARTIDA:
            if localidad_id in cache_observaciones:
                condiciones = cache_observaciones[localidad_id]
                tipo_fuente_observacion = "ws1_current_weather_api"
                url_fuente_observacion = URL_TIEMPO_ACTUAL.format(
                    localidad_id=localidad_id
                )
            elif localidad_id not in cache_observaciones_error:
                try:
                    observacion_descargada, token = (
                        descargar_con_token_renovable(
                            sesion,
                            token,
                            descargar_tiempo_actual,
                            localidad_id,
                        )
                    )
                    solicitudes_observaciones += 1
                    condiciones = normalizar_observacion_smn(
                        observacion_descargada
                    )
                    cache_observaciones[localidad_id] = condiciones
                    tipo_fuente_observacion = "ws1_current_weather_api"
                    url_fuente_observacion = URL_TIEMPO_ACTUAL.format(
                        localidad_id=localidad_id
                    )
                    print("  Estado meteorológico actual obtenido.")
                except Exception as error:
                    mensaje = str(error)
                    cache_observaciones_error[localidad_id] = mensaje
                    intentos_fallidos.append(
                        {
                            "source": "ws1_current_weather_api",
                            "id": localidad_id,
                            "error": mensaje,
                            "cached": False,
                        }
                    )
                    print(
                        "  Estado meteorológico actual no disponible: "
                        f"{mensaje}"
                    )

                time.sleep(PAUSA_ENTRE_PEDIDOS)

        condiciones, observacion_es_nueva = (
            seleccionar_condiciones_mas_recientes(
                condiciones,
                anterior,
            )
        )

        if condiciones is not None and not observacion_es_nueva:
            print("  Se conserva la observación más reciente ya guardada.")

        salida: dict[str, Any] | None = None
        conservado = False

        if (
            datos_pronostico is not None
            and id_pronostico_usado is not None
            and tipo_fuente_pronostico is not None
            and url_fuente_pronostico is not None
        ):
            salida = preparar_salida_pronostico(
                datos=datos_pronostico,
                localidad=localidad,
                id_pronostico_usado=id_pronostico_usado,
                tipo_fuente=tipo_fuente_pronostico,
                url_fuente=url_fuente_pronostico,
                condiciones=condiciones,
            )
        elif condiciones is not None and observacion_es_nueva:
            salida = preparar_salida_observacion(
                localidad=localidad,
                condiciones=condiciones,
                tipo_fuente=tipo_fuente_observacion
                or "ws1_current_weather_api",
                url_fuente=url_fuente_observacion
                or URL_TIEMPO_ACTUAL.format(localidad_id=localidad_id),
                anterior=anterior,
            )
            if not salida.get("forecast"):
                observaciones_sin_pronostico += 1
        elif anterior is not None:
            salida = anterior
            conservado = True
            archivos_conservados += 1
            print("  Se conserva el último archivo válido disponible.")
        else:
            registro_inicial = observaciones_iniciales.get(localidad_id)
            if registro_inicial is not None:
                salida = observacion_inicial_para_localidad(
                    localidad,
                    registro_inicial,
                )
                if salida is not None:
                    observaciones_sin_pronostico += 1
                    print("  Se usa la observación inicial de respaldo.")

        if salida is None:
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

        escribir_json(
            CARPETA_TEMPORAL / f"{localidad_id}.json",
            salida,
        )

        if (
            id_pronostico_usado is not None
            and id_pronostico_usado != localidad_id
        ):
            localidades_con_referencia += 1
            print(
                "  Se usó la referencia meteorológica "
                f"{id_pronostico_usado}."
            )

        if localidad_id == ID_CABA:
            datos_caba = salida

        registrar_disponible(
            disponibles=disponibles,
            localidad=localidad,
            salida=salida,
            id_pronostico_usado=id_pronostico_usado,
            conservado=conservado,
        )

        time.sleep(PAUSA_ENTRE_PEDIDOS)

    con_pronostico = sum(
        1 for localidad in disponibles if localidad.get("has_forecast")
    )
    solo_observacion = sum(
        1
        for localidad in disponibles
        if not localidad.get("has_forecast")
        and localidad.get("has_current_conditions")
    )

    resumen = {
        "source": "Servicio Meteorológico Nacional",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalog_count": total,
        "available_count": len(disponibles),
        "forecast_available_count": con_pronostico,
        "observation_only_count": solo_observacion,
        "failed_count": len(errores),
        "preserved_previous_count": archivos_conservados,
        "locations_using_reference_count": localidades_con_referencia,
        "legacy_forecast_count": pronosticos_legados_usados,
        "http_modern_forecast_requests": solicitudes_modernas,
        "http_legacy_forecast_requests": solicitudes_legadas,
        "http_current_weather_requests": solicitudes_observaciones,
        "available": disponibles,
        "failed": errores,
    }

    escribir_json(CARPETA_TEMPORAL / "index.json", resumen)

    if len(disponibles) < 20:
        shutil.rmtree(CARPETA_TEMPORAL, ignore_errors=True)
        raise RuntimeError(
            "Se conservaron o descargaron menos de 20 localidades. "
            "La respuesta del SMN parece bloqueada o incompleta."
        )

    publicar_carpeta_temporal()

    if datos_caba is not None:
        escribir_json(ARCHIVO_CABA, datos_caba)

    print("")
    print("Proceso terminado.")
    print(f"Localidades del catálogo: {total}")
    print(f"Archivos disponibles: {len(disponibles)}")
    print(f"Con pronóstico: {con_pronostico}")
    print(f"Solo con observación actual: {solo_observacion}")
    print(f"Archivos anteriores conservados: {archivos_conservados}")
    print(f"Localidades sin ningún archivo: {len(errores)}")
    print(
        "Localidades que usaron referencia meteorológica: "
        f"{localidades_con_referencia}"
    )
    print(f"Pronósticos históricos recientes usados: {pronosticos_legados_usados}")
    print(f"Resumen: {ARCHIVO_RESUMEN}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error general: {error}", file=sys.stderr)
        sys.exit(1)
