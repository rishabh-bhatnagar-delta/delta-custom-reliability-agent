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

## Failover Classification Rules

The posture analyzer classifies each supported resource's failover configuration as one of:

- **ACTIVE-ACTIVE** — multiple instances actively serving traffic; automatic failover with no downtime
- **ACTIVE-PASSIVE** — standby exists but doesn't serve traffic; failover has some downtime or requires promotion
- **NO FAILOVER** — single point of failure; no redundancy

### EC2

| Condition | Classification |
|---|---|
| No ASG | NO FAILOVER |
| ASG with DesiredCapacity=0 and MinSize=0 | NO FAILOVER |
| ASG with DesiredCapacity=1 | ACTIVE-PASSIVE |
| ASG with DesiredCapacity≥2, single AZ | ACTIVE-PASSIVE |
| ASG with DesiredCapacity≥2, multi-AZ, no target group | ACTIVE-PASSIVE |
| ASG with DesiredCapacity≥2, multi-AZ, target group with <2 healthy targets | ACTIVE-PASSIVE |
| ASG with DesiredCapacity≥2, multi-AZ, target group with ≥2 healthy targets | ACTIVE-ACTIVE |

### RDS

| Condition | Classification |
|---|---|
| Single instance, no Multi-AZ, no replicas, no Global DB | NO FAILOVER |
| Read replicas exist but Multi-AZ=false | ACTIVE-PASSIVE |
| Multi-AZ=true (standalone or cluster with ≤1 reader) | ACTIVE-PASSIVE |
| Global DB with secondary region, GlobalWriteForwardingStatus=disabled | ACTIVE-PASSIVE |
| Aurora cluster with ≥2 readers and Multi-AZ=true | ACTIVE-ACTIVE |
| Global DB with secondary region, GlobalWriteForwardingStatus=enabled | ACTIVE-ACTIVE |


### Route53

Each record set (grouped by name + type) is classified independently:

| Condition | Classification |
|---|---|
| Single record, no routing policy | NO FAILOVER |
| Single alias record | NO FAILOVER (ALIAS) |
| Failover routing (PRIMARY/SECONDARY) | ACTIVE-PASSIVE |
| Weighted routing with only one non-zero weight | ACTIVE-PASSIVE |
| Weighted routing with multiple non-zero weights | ACTIVE-ACTIVE |
| Latency-based routing | ACTIVE-ACTIVE |
| Geolocation routing | ACTIVE-ACTIVE |
| Multivalue answer routing | ACTIVE-ACTIVE |

**Note:** Failover classification is only performed for EC2, RDS, DynamoDB, and Route53. Other resource types (S3, Lambda, API Gateway) are analyzed for reliability gaps but not classified into failover patterns.
