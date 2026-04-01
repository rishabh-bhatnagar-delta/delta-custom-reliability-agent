import os

from dotenv import load_dotenv

load_dotenv()

# AWS
AWS_REGION = os.environ["AWS_REGION"]
AWS_PROFILE = os.environ["AWS_PROFILE"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

# Cache TTL in minutes
CACHE_TTL_MINUTES = 24 * 60

# Max parallel requests to CloudFormation API
MAX_CONCURRENCY = 10

# US regions to scan
US_REGIONS = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
]
