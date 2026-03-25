import os

from dotenv import load_dotenv

load_dotenv()

# AWS
AWS_REGION = os.environ["AWS_REGION"]
AWS_PROFILE = os.environ["AWS_PROFILE"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Cache TTL in minutes
CACHE_TTL_MINUTES = 15

# Max parallel requests to CloudFormation API
MAX_CONCURRENCY = 10
