from dataclasses import dataclass


@dataclass
class RoutesTriangular:
    name: str
    token_path: list[str]
    pool_steps: list[dict]
