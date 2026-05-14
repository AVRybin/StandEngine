from dataclasses import dataclass

@dataclass(kw_only=True)
class Server:
    location: str
    type: str
    network: str
    image: str