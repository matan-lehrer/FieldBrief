from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_api_key: str = ""
    llm_model_cheap: str = "gemini/gemini-2.5-flash"
    llm_model_strong: str = "gemini/gemini-2.5-pro"

    report_budget_usd_standard: float = 0.50
    report_time_budget_seconds: int = 300

    database_url: str = "postgresql+asyncpg://fieldbrief:fieldbrief@localhost:5432/fieldbrief"
    rabbitmq_url: str = "amqp://fieldbrief:fieldbrief@localhost:5672/"
    searxng_url: str = "http://localhost:8080"

    sec_user_agent: str = "Fieldbrief/0.1 your-email@example.com"

    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    api_key: str = "change-me"


settings = Settings()
