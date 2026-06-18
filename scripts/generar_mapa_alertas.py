from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ARCHIVO_LOCALIDADES = Path("docs/data/localidades.json")
ARCHIVO_AREAS = Path("docs/data/areas_alerta.geojson")
ARCHIVO_SALIDA = Path("docs/data/localidades_alerta.json")
ZONA_ARGENTINA = ZoneInfo("America/Argentina/Buenos_Aires")
EPSILON = 1e-9
UMBRAL_PROXIMIDAD_GRADOS = 0.005


def ahora_iso() -> str:
    return datetime.now(ZONA_ARGENTINA).isoformat(timespec="seconds")


def leer_json(ruta: Path) -> Any:
    return json.loads(ruta.read_text(encoding="utf-8"))


def escribir_json(ruta: Path, datos: Any) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporal.replace(ruta)


def numero(valor: Any) -> float | None:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def punto_sobre_segmento(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> bool:
    producto_cruzado = (y - y1) * (x2 - x1) - (x - x1) * (y2 - y1)
    if abs(producto_cruzado) > EPSILON:
        return False

    return (
        min(x1, x2) - EPSILON <= x <= max(x1, x2) + EPSILON
        and min(y1, y2) - EPSILON <= y <= max(y1, y2) + EPSILON
    )


def punto_en_anillo(x: float, y: float, anillo: list[list[float]]) -> bool:
    if len(anillo) < 3:
        return False

    dentro = False
    j = len(anillo) - 1

    for i, punto in enumerate(anillo):
        x1, y1 = float(punto[0]), float(punto[1])
        x2, y2 = float(anillo[j][0]), float(anillo[j][1])

        if punto_sobre_segmento(x, y, x1, y1, x2, y2):
            return True

        cruza = (y1 > y) != (y2 > y)
        if cruza:
            interseccion_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < interseccion_x:
                dentro = not dentro

        j = i

    return dentro


def punto_en_poligono(x: float, y: float, poligono: list[Any]) -> bool:
    if not poligono:
        return False

    exterior = poligono[0]
    if not isinstance(exterior, list) or not punto_en_anillo(x, y, exterior):
        return False

    for hueco in poligono[1:]:
        if isinstance(hueco, list) and punto_en_anillo(x, y, hueco):
            return False

    return True


def punto_en_geometria(x: float, y: float, geometria: dict[str, Any]) -> bool:
    tipo = geometria.get("type")
    coordenadas = geometria.get("coordinates")

    if tipo == "Polygon" and isinstance(coordenadas, list):
        return punto_en_poligono(x, y, coordenadas)

    if tipo == "MultiPolygon" and isinstance(coordenadas, list):
        return any(
            punto_en_poligono(x, y, poligono)
            for poligono in coordenadas
            if isinstance(poligono, list)
        )

    return False


def recorrer_pares(valor: Any) -> Iterable[tuple[float, float]]:
    if (
        isinstance(valor, list)
        and len(valor) >= 2
        and isinstance(valor[0], (int, float))
        and isinstance(valor[1], (int, float))
    ):
        yield float(valor[0]), float(valor[1])
        return

    if isinstance(valor, list):
        for elemento in valor:
            yield from recorrer_pares(elemento)


def limites_geometria(geometria: dict[str, Any]) -> tuple[float, float, float, float]:
    pares = list(recorrer_pares(geometria.get("coordinates")))
    if not pares:
        raise ValueError("Geometría sin coordenadas.")

    xs = [par[0] for par in pares]
    ys = [par[1] for par in pares]
    return min(xs), min(ys), max(xs), max(ys)


def dentro_de_limites(
    x: float,
    y: float,
    limites: tuple[float, float, float, float],
) -> bool:
    minimo_x, minimo_y, maximo_x, maximo_y = limites
    return (
        minimo_x - EPSILON <= x <= maximo_x + EPSILON
        and minimo_y - EPSILON <= y <= maximo_y + EPSILON
    )




def distancia_punto_segmento(
    x: float,
    y: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    dx = x2 - x1
    dy = y2 - y1

    if abs(dx) <= EPSILON and abs(dy) <= EPSILON:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5

    proporcion = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    proporcion = max(0.0, min(1.0, proporcion))

    cercano_x = x1 + proporcion * dx
    cercano_y = y1 + proporcion * dy
    return ((x - cercano_x) ** 2 + (y - cercano_y) ** 2) ** 0.5


def distancia_a_anillo(x: float, y: float, anillo: list[list[float]]) -> float:
    if len(anillo) < 2:
        return float("inf")

    distancia = float("inf")
    anterior = anillo[-1]

    for actual in anillo:
        try:
            distancia = min(
                distancia,
                distancia_punto_segmento(
                    x,
                    y,
                    float(anterior[0]),
                    float(anterior[1]),
                    float(actual[0]),
                    float(actual[1]),
                ),
            )
        except (TypeError, ValueError, IndexError):
            pass
        anterior = actual

    return distancia


def distancia_a_poligono(x: float, y: float, poligono: list[Any]) -> float:
    if not poligono:
        return float("inf")

    distancias = [
        distancia_a_anillo(x, y, anillo)
        for anillo in poligono
        if isinstance(anillo, list)
    ]
    return min(distancias, default=float("inf"))


def distancia_a_geometria(x: float, y: float, geometria: dict[str, Any]) -> float:
    tipo = geometria.get("type")
    coordenadas = geometria.get("coordinates")

    if tipo == "Polygon" and isinstance(coordenadas, list):
        return distancia_a_poligono(x, y, coordenadas)

    if tipo == "MultiPolygon" and isinstance(coordenadas, list):
        return min(
            (
                distancia_a_poligono(x, y, poligono)
                for poligono in coordenadas
                if isinstance(poligono, list)
            ),
            default=float("inf"),
        )

    return float("inf")


def limites_ampliados(
    limites: tuple[float, float, float, float],
    margen: float,
) -> tuple[float, float, float, float]:
    minimo_x, minimo_y, maximo_x, maximo_y = limites
    return (
        minimo_x - margen,
        minimo_y - margen,
        maximo_x + margen,
        maximo_y + margen,
    )


def cargar_localidades(datos: Any) -> list[dict[str, Any]]:
    if isinstance(datos, dict) and isinstance(datos.get("localities"), list):
        return [item for item in datos["localities"] if isinstance(item, dict)]
    if isinstance(datos, list):
        return [item for item in datos if isinstance(item, dict)]
    raise RuntimeError("El catálogo de localidades tiene una estructura desconocida.")


def main() -> int:
    localidades = cargar_localidades(leer_json(ARCHIVO_LOCALIDADES))
    geojson = leer_json(ARCHIVO_AREAS)
    features = geojson.get("features") if isinstance(geojson, dict) else None

    if not isinstance(features, list):
        raise RuntimeError("El archivo de áreas no contiene una FeatureCollection válida.")

    areas_preparadas: list[tuple[int, dict[str, Any], tuple[float, float, float, float]]] = []

    for feature in features:
        if not isinstance(feature, dict):
            continue

        propiedades = feature.get("properties")
        geometria = feature.get("geometry")
        if not isinstance(propiedades, dict) or not isinstance(geometria, dict):
            continue

        try:
            area_id = int(propiedades.get("gid"))
            limites = limites_geometria(geometria)
        except (TypeError, ValueError):
            continue

        areas_preparadas.append((area_id, geometria, limites))

    por_localidad: dict[str, list[int]] = {}
    sin_coordenadas: list[dict[str, Any]] = []
    sin_area: list[dict[str, Any]] = []
    coincidencias_multiples: list[dict[str, Any]] = []
    coincidencias_por_proximidad: list[dict[str, Any]] = []

    for localidad in localidades:
        try:
            localidad_id = int(localidad.get("id"))
        except (TypeError, ValueError):
            continue

        latitud = numero(localidad.get("lat"))
        longitud = numero(localidad.get("lon"))

        if latitud is None or longitud is None:
            sin_coordenadas.append(
                {
                    "id": localidad_id,
                    "name": localidad.get("name"),
                    "province": localidad.get("province"),
                }
            )
            continue

        coincidencias: list[int] = []

        for area_id, geometria, limites in areas_preparadas:
            if not dentro_de_limites(longitud, latitud, limites):
                continue
            if punto_en_geometria(longitud, latitud, geometria):
                coincidencias.append(area_id)

        coincidencias = sorted(set(coincidencias))
        coincidencia_por_proximidad = False
        distancia_proximidad = None

        if not coincidencias:
            candidatos: list[tuple[float, int]] = []

            for area_id, geometria, limites in areas_preparadas:
                if not dentro_de_limites(
                    longitud,
                    latitud,
                    limites_ampliados(
                        limites,
                        UMBRAL_PROXIMIDAD_GRADOS,
                    ),
                ):
                    continue

                distancia = distancia_a_geometria(
                    longitud,
                    latitud,
                    geometria,
                )

                if distancia <= UMBRAL_PROXIMIDAD_GRADOS:
                    candidatos.append((distancia, area_id))

            if candidatos:
                candidatos.sort()
                distancia_proximidad, area_proxima = candidatos[0]
                coincidencias = [area_proxima]
                coincidencia_por_proximidad = True

        por_localidad[str(localidad_id)] = coincidencias

        resumen = {
            "id": localidad_id,
            "name": localidad.get("name"),
            "province": localidad.get("province"),
            "lat": latitud,
            "lon": longitud,
            "area_ids": coincidencias,
        }

        if coincidencia_por_proximidad:
            resumen["match_method"] = "proximity"
            resumen["distance_degrees"] = round(
                float(distancia_proximidad or 0),
                8,
            )
            coincidencias_por_proximidad.append(resumen.copy())

        if not coincidencias:
            sin_area.append(resumen)
        elif len(coincidencias) > 1:
            coincidencias_multiples.append(resumen)

    total_con_area = sum(1 for ids in por_localidad.values() if ids)

    salida = {
        "generated_at": ahora_iso(),
        "source": "Polígonos del mapa público de alertas del Servicio Meteorológico Nacional",
        "areas_file": "areas_alerta.geojson",
        "area_count": len(areas_preparadas),
        "locality_count": len(localidades),
        "matched_locality_count": total_con_area,
        "unmatched_locality_count": len(sin_area),
        "without_coordinates_count": len(sin_coordenadas),
        "multiple_match_count": len(coincidencias_multiples),
        "proximity_match_count": len(coincidencias_por_proximidad),
        "proximity_threshold_degrees": UMBRAL_PROXIMIDAD_GRADOS,
        "by_locality_id": por_localidad,
        "unmatched_localities": sin_area,
        "without_coordinates": sin_coordenadas,
        "multiple_matches": coincidencias_multiples,
        "proximity_matches": coincidencias_por_proximidad,
    }

    escribir_json(ARCHIVO_SALIDA, salida)

    print("Relación entre localidades y áreas de alerta generada.")
    print(f"Áreas disponibles: {salida['area_count']}")
    print(f"Localidades procesadas: {salida['locality_count']}")
    print(f"Localidades con área: {salida['matched_locality_count']}")
    print(f"Localidades sin área: {salida['unmatched_locality_count']}")
    print(f"Localidades sin coordenadas: {salida['without_coordinates_count']}")
    print(f"Coincidencias múltiples: {salida['multiple_match_count']}")
    print(f"Coincidencias por proximidad: {salida['proximity_match_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
