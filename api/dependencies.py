"""
api/dependencies.py

FastAPI dependency providers — database session, shared config.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url:     str = ""
    supabase_key:     str = ""
    apify_token:      str = ""
    rentcast_api_key: str = ""
    walkscore_api_key: str = ""
    google_maps_api_key: str = ""
    smtp_host:        str = ""
    smtp_port:        int = 587
    smtp_user:        str = ""
    smtp_password:    str = ""
    digest_email:     str = ""
    app_base_url:     str = "http://localhost:8000"
    # Set USE_STATIC_RENT_TABLE=false to re-enable live Rentcast API calls
    use_static_rent_table: bool = True

    model_config = {"env_file": ".env", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
