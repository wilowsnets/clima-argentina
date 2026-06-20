#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Actualiza el catálogo de imágenes satelitales oficiales del SMN.

No descarga ni almacena las imágenes en GitHub. Solo guarda en un JSON
pequeño las URLs de los últimos cuadros publicados por el SMN.

Salida:
    docs/data/satelite/argentina/index.json
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests


PRODUCT_ID = "TOP_C13_ARG_ALTA"
PRODUCT_NAME = "Topes Nubosos"
REGION_NAME = "Argentina"

TOKEN_PAGES = (
    "https://ws2.smn.gob.ar/pronostico",
    "https://www.smn.gob.ar/satelite",
    "https://ws2.smn.gob.ar/satelite",
)

CATALOG_URL = (
    "https://ws1.smn.gob.ar/v1/images/satellite/"
    f"{PRODUCT_ID}"
)

STATIC_BASE_URL = "https://estaticos.smn.gob.ar/vmsr/satelite/"
OUTPUT_PATH = Path("docs/data/satelite/argentina/index.json")

MAX_FRAMES = 24
HOME_FRAMES = 6
TIMEOUT_SECONDS = 35
ARGENTINA_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}


class TokenRejected(RuntimeError):
    pass


def now_argentina() -> str:
    return datetime.now(ARGENTINA_TZ).isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_existing() -> dict[str, Any] | None:
    if not OUTPUT_PATH.exists():
        return None

    try:
        data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    return data if isinstance(data, dict) else None


def extract_token(html: str) -> str | None:
    patterns = (
        r"localStorage\.setItem\(\s*[\"']token[\"']\s*,\s*[\"']([^\"']+)[\"']\s*\)",
        r"localStorage\.setItem\(\s*`token`\s*,\s*`([^`]+)`\s*\)",
        r"[\"']token[\"']\s*:\s*[\"']([^\"']+)[\"']",
        r"[\"']jwt[\"']\s*:\s*[\"']([^\"']+)[\"']",
        r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    )

    for pattern in patterns:
        match = re.search(pattern, html)
        if not match:
            continue

        token = match.group(1).strip()
        if token.count(".") == 2 and len(token) > 40:
            return token

    return None


def obtain_token(session: requests.Session) -> str:
    errors: list[str] = []

    for page_url in TOKEN_PAGES:
        try:
            response = session.get(
                page_url,
                headers={
                    **BASE_HEADERS,
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()

            token = extract_token(response.text)
            if token:
                print(f"Token temporal obtenido desde {page_url}")
                return token

            errors.append(f"{page_url}: no se encontró token")
        except Exception as exc:
            errors.append(f"{page_url}: {exc}")

    raise RuntimeError(
        "No se pudo obtener el token temporal del SMN. "
        + " | ".join(errors)
    )


def api_headers(token: str) -> dict[str, str]:
    return {
        **BASE_HEADERS,
        "Accept": "application/json",
        "Authorization": f"JWT {token}",
        "Origin": "https://www.smn.gob.ar",
        "Referer": "https://www.smn.gob.ar/satelite",
    }


def request_catalog(
    session: requests.Session,
    token: str,
) -> Any:
    response = session.get(
        CATALOG_URL,
        headers=api_headers(token),
        timeout=TIMEOUT_SECONDS,
    )

    if response.status_code in (401, 403):
        raise TokenRejected(
            "El endpoint de imágenes satelitales rechazó el token."
        )

    response.raise_for_status()

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            "El catálogo satelital no devolvió JSON válido."
        ) from exc


def find_catalog_object(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        if isinstance(data.get("list"), list):
            return data

        for key in ("data", "result", "results", "items"):
            child = data.get(key)

            if isinstance(child, dict) and isinstance(
                child.get("list"),
                list,
            ):
                return child

            if isinstance(child, list):
                for item in child:
                    if (
                        isinstance(item, dict)
                        and isinstance(item.get("list"), list)
                    ):
                        return item

    if isinstance(data, list):
        for item in data:
            if (
                isinstance(item, dict)
                and isinstance(item.get("list"), list)
            ):
                return item

    raise RuntimeError(
        "La respuesta satelital tiene una estructura desconocida."
    )


FILENAME_RE = re.compile(
    r"^TOP_C13_ARG_ALTA_(\d{8})_(\d{6})Z\."
    r"(?:jpg|jpeg|png|webp)$",
    re.IGNORECASE,
)


def parse_frame(filename: str) -> dict[str, Any] | None:
    filename = filename.strip()
    match = FILENAME_RE.match(filename)

    if not match:
        return None

    date_part, time_part = match.groups()

    try:
        utc_dt = datetime.strptime(
            date_part + time_part,
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    local_dt = utc_dt.astimezone(ARGENTINA_TZ)

    return {
        "filename": filename,
        "url": STATIC_BASE_URL + filename,
        "timestamp_utc": (
            utc_dt.isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        ),
        "timestamp_argentina": local_dt.isoformat(
            timespec="seconds"
        ),
        "date_argentina": local_dt.strftime("%Y-%m-%d"),
        "time_argentina": local_dt.strftime("%H:%M:%S"),
    }


def build_index(data: Any) -> dict[str, Any]:
    catalog = find_catalog_object(data)
    raw_list = catalog.get("list")

    if not isinstance(raw_list, list):
        raise RuntimeError(
            "El catálogo no contiene una lista válida de imágenes."
        )

    frames_by_name: dict[str, dict[str, Any]] = {}

    for raw_item in raw_list:
        if not isinstance(raw_item, str):
            continue

        frame = parse_frame(raw_item)
        if frame is not None:
            frames_by_name[frame["filename"]] = frame

    frames = sorted(
        frames_by_name.values(),
        key=lambda item: item["timestamp_utc"],
    )

    frames = frames[-MAX_FRAMES:]

    if not frames:
        raise RuntimeError(
            "El catálogo no contiene imágenes reconocibles "
            f"para {PRODUCT_ID}."
        )

    home_frames = frames[-HOME_FRAMES:]
    latest = frames[-1]

    return {
        "version": 1,
        "generated_at": now_argentina(),
        "last_success_at": now_argentina(),
        "status": "ok",
        "source": "Servicio Meteorológico Nacional",
        "product_id": str(
            catalog.get("id") or PRODUCT_ID
        ),
        "product": str(
            catalog.get("product") or PRODUCT_NAME
        ),
        "region": str(
            catalog.get("region") or REGION_NAME
        ),
        "source_endpoint": CATALOG_URL,
        "static_base_url": STATIC_BASE_URL,
        "storage_mode": "remote_urls_only",
        "stored_image_count": 0,
        "frame_count": len(frames),
        "home_frame_count": len(home_frames),
        "latest": latest,
        "home_frames": home_frames,
        "frames": frames,
    }


def preserve_previous_on_error(message: str) -> int:
    previous = load_existing()

    if previous and previous.get("frames"):
        print(
            "No se pudo actualizar el satélite. "
            "Se conserva el último index.json válido."
        )
        print(f"Motivo: {message}")
        return 0

    initial = {
        "version": 1,
        "generated_at": now_argentina(),
        "last_success_at": None,
        "status": "sin_datos",
        "source": "Servicio Meteorológico Nacional",
        "product_id": PRODUCT_ID,
        "product": PRODUCT_NAME,
        "region": REGION_NAME,
        "source_endpoint": CATALOG_URL,
        "static_base_url": STATIC_BASE_URL,
        "storage_mode": "remote_urls_only",
        "stored_image_count": 0,
        "frame_count": 0,
        "home_frame_count": 0,
        "latest": None,
        "home_frames": [],
        "frames": [],
        "last_error": message,
    }

    write_json_atomic(OUTPUT_PATH, initial)
    print("Se creó un index.json inicial sin imágenes.")
    return 0


def main() -> int:
    session = requests.Session()

    try:
        token = obtain_token(session)

        try:
            response_data = request_catalog(session, token)
        except TokenRejected:
            print("El primer token fue rechazado. Se solicita otro.")
            token = obtain_token(session)
            response_data = request_catalog(session, token)

        index = build_index(response_data)
        write_json_atomic(OUTPUT_PATH, index)

        print(
            "Satélite actualizado correctamente: "
            f"{index['frame_count']} cuadros disponibles; "
            f"{index['home_frame_count']} para la portada."
        )
        print(
            "Último cuadro: "
            f"{index['latest']['filename']} "
            f"({index['latest']['timestamp_argentina']})"
        )
        print(
            "No se almacenaron imágenes en GitHub; "
            "solo se guardaron URLs oficiales."
        )
        return 0

    except Exception as exc:
        return preserve_previous_on_error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
