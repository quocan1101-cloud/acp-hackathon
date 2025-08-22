from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator

class EnvSettings(BaseSettings):
    WHITELISTED_WALLET_PRIVATE_KEY: Optional[str] = '0xfb374656fc89886c1eb7628d90a230bf70216102f60ffa6d88c4fc6b67e30168'
    BUYER_AGENT_WALLET_ADDRESS: Optional[str] = '0x8Db70e529cEd3aEcD611a098F12681Ae8ec327d5'
    SELLER_AGENT_WALLET_ADDRESS: Optional[str] = '0x562A3b0d1b1786bFf3B1329F59690D6B7CdBd7b1'
    EVALUATOR_AGENT_WALLET_ADDRESS: Optional[str] = None
    BUYER_GAME_TWITTER_ACCESS_TOKEN: Optional[str] = None
    SELLER_GAME_TWITTER_ACCESS_TOKEN: Optional[str] = None
    EVALUATOR_GAME_TWITTER_ACCESS_TOKEN: Optional[str] = None
    BUYER_ENTITY_ID: Optional[int] = 1
    SELLER_ENTITY_ID: Optional[int] = 1
    EVALUATOR_ENTITY_ID: Optional[int] = None
    @field_validator("BUYER_AGENT_WALLET_ADDRESS", "SELLER_AGENT_WALLET_ADDRESS", "EVALUATOR_AGENT_WALLET_ADDRESS")
    def validate_wallet_address(cls, v: str) -> str:
        if v is None:
            return None
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError("Wallet address must start with '0x' and be 42 characters long.")
        return v
