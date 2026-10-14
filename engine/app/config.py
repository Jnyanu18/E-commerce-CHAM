from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql://postgres:postgres@localhost:54322/postgres"
    credentials_key: str = ""  # base64, 32 bytes; see app/crypto.py


settings = Settings()
