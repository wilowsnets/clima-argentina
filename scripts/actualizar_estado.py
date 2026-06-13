from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import requests


SOURCE_URL = "https://ws.smn.gob.ar/map_items/weather"
OUTPUT_FILE = Path("docs/data/estado_actual.json")


def main() -> None:
    response = requests.get(
        SOURCE_URL,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()

    estaciones = response.json()

    resultado = {
        "source": "Servicio Meteorológico Nacional",
        "source_url": SOURCE_URL,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(estaciones),
        "stations": estaciones,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    temporal = OUTPUT_FILE.with_suffix(".tmp")
    temporal.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporal.replace(OUTPUT_FILE)

    print(f"Guardadas {len(estaciones)} estaciones")


if __name__ == "__main__":
    main()
