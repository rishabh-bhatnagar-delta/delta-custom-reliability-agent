generate a readme file for following:

# Custom Agent Delta

Custom Agent Delta provides MCP server tools for AWS configuration auditing and resource metrics dimension fetching.

## Features

- **Config Auditor Tool**: Fetches dimensions for requested AWS resources
- **Resource Fetcher Tool**: Reads all CloudFormation templates (CFTs) and their configured resources

## Quick Start

### Prerequisites

- AWS CLI installed and configured

**Important**: Run `aws login` before using any tools to authenticate with AWS.

### Running the Tools

```bash
# Run config auditor tool
python tools/auditor/auditor.py

# Run resource fetcher tool
python tools/fetcher.py
```

## Tools

### 1. Config Auditor (`tools/auditor/auditor.py`)

Fetches CloudWatch metrics dimensions for requested AWS resources using dimension fetchers in
`tools/auditor/dimension_fetcher/`.

**Entry Point**: `tools/auditor/auditor.py`

**Supported Resources**:

- API Gateway (`api_gateway.py`)
- DynamoDB (`dynamo_db.py`)
- Lambda (`lambda_.py`)
- RDS (`rds.py`)
- S3 (`s3.py`)

### 2. Resource Fetcher (`tools/fetcher.py`)

Scans all CloudFormation templates (CFTs) and extracts their configured resources.

**Entry Point**: `tools/fetcher.py`

## Dimension Fetchers

To quickly see what dimensions are being fetched for each resource, checkout `tools/auditor/dimension_fetcher/`
directory. Each file implements the `DimensionFetcher` base class with a `get_dimensions()` method that uses Boto3 to
query AWS APIs.
