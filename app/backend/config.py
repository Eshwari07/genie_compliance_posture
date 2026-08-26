"""App configuration, resolved from the Databricks Apps runtime environment.

No secrets live here. Databricks Apps injects DATABRICKS_HOST, DATABRICKS_CLIENT_ID and
DATABRICKS_CLIENT_SECRET automatically, and the SDK picks them up as the app's service
principal. GENIE_SPACE_ID and DATABRICKS_WAREHOUSE_ID arrive via `valueFrom` resource
bindings declared in app.yaml.
"""

from __future__ import annotations

import os
from functools import lru_cache


class Settings:
    # --- injected by the Apps runtime -------------------------------------
    host: str = os.getenv("DATABRICKS_HOST", "")
    app_port: int = int(os.getenv("DATABRICKS_APP_PORT", "8000"))

    # --- bound resources ---------------------------------------------------
    genie_space_id: str = os.getenv("GENIE_SPACE_ID", "")
    warehouse_id: str = os.getenv("DATABRICKS_WAREHOUSE_ID", "")

    # --- data location -----------------------------------------------------
    catalog: str = os.getenv("CATALOG", "workspace")
    schema: str = os.getenv("SCHEMA", "complylens_genie")

    # --- behaviour ---------------------------------------------------------
    # A 2X-Small warehouse is the only option on Free Edition, so Genie round trips are
    # slow enough that the timeout has to be generous and the UI has to narrate progress.
    genie_timeout_s: int = int(os.getenv("GENIE_TIMEOUT_S", "180"))
    genie_poll_interval_s: float = float(os.getenv("GENIE_POLL_INTERVAL_S", "1.2"))
    max_rows: int = int(os.getenv("MAX_ROWS", "500"))
    posture_cache_ttl_s: int = int(os.getenv("POSTURE_CACHE_TTL_S", "600"))

    @property
    def view_schema(self) -> str:
        return f"{self.catalog}.{self.schema}"

    def missing(self) -> list[str]:
        """Config the app cannot run without. Surfaced on /api/health rather than
        crashing at import, so a misconfigured resource binding is diagnosable."""
        gaps = []
        if not self.genie_space_id:
            gaps.append("GENIE_SPACE_ID (bind a Genie Agent resource with CAN RUN)")
        if not self.warehouse_id:
            gaps.append("DATABRICKS_WAREHOUSE_ID (bind a SQL warehouse resource with CAN USE)")
        return gaps


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
