from dotenv import find_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PATH = find_dotenv()
if not ENV_PATH:
    print("CRITICAL: .env file not found! Ensure it exists in the project root.")
else:
    print(f"Loading config from: {ENV_PATH}")


class Settings(BaseSettings):
    # Strictly required fields
    aws_region: str = Field(...)
    aws_profile: str = Field(...)
    log_level: str = Field(...)

    model_config = SettingsConfigDict(
        # Use the absolute path to ensure it's found
        env_file=ENV_PATH,
        env_file_encoding='utf-8',
        extra="ignore"
    )


# This will now correctly find the .env in the project root
settings = Settings()
