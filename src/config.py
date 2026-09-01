import functools

import yaml


@functools.lru_cache(maxsize=1)
def load_settings(path: str = "config/settings.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
