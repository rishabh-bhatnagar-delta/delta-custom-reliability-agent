from typing import List
from botocore.exceptions import ClientError

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


class Route53DimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.Route53

    def _fetch_dimensions(self, physical_id: str) -> List[DimensionOutput]:
        dimensions = []
        route53_client = self.get_aws_client_for_resource()

        hosted_zone = route53_client.get_hosted_zone(Id=physical_id)
        zone_info = hosted_zone.get('HostedZone', {})
        vpc_info = hosted_zone.get('VPCs', [])

        # Zone type (public vs private)
        is_private = zone_info.get('Config', {}).get('PrivateZone', False)
        dimensions.append(DimensionOutput(name='ZoneType', value='Private' if is_private else 'Public'))

        # Zone name
        dimensions.append(DimensionOutput(name='ZoneName', value=zone_info.get('Name', '')))

        # Record count
        dimensions.append(DimensionOutput(name='RecordCount', value=zone_info.get('ResourceRecordSetCount', 0)))

        # Associated VPCs (private zones only)
        dimensions.append(DimensionOutput(name='AssociatedVPCs', value=[
            {'VPCId': v.get('VPCId'), 'VPCRegion': v.get('VPCRegion')} for v in vpc_info
        ]))

        # DNSSEC status
        try:
            dnssec = route53_client.get_dnssec(HostedZoneId=physical_id)
            dnssec_status = dnssec.get('Status', {}).get('ServeSignature', 'NOT_SIGNING')
            dimensions.append(DimensionOutput(name='DNSSEC', value=dnssec_status == 'SIGNING'))
        except ClientError:
            dimensions.append(DimensionOutput(name='DNSSEC', value=False))

        # Query logging
        try:
            logging_configs = route53_client.list_query_logging_configs(HostedZoneId=physical_id)
            has_logging = len(logging_configs.get('QueryLoggingConfigs', [])) > 0
            dimensions.append(DimensionOutput(name='QueryLogging', value=has_logging))
        except ClientError:
            dimensions.append(DimensionOutput(name='QueryLogging', value=False))

        # Record analysis: health checks, failover details
        try:
            record_sets = route53_client.list_resource_record_sets(HostedZoneId=physical_id)
            records_with_health_checks = 0
            total_records = 0
            failover_records = []
            health_check_ids = set()

            for rs in record_sets.get('ResourceRecordSets', []):
                if rs.get('Type') in ('SOA', 'NS'):
                    continue
                total_records += 1

                hc_id = rs.get('HealthCheckId')
                if hc_id:
                    records_with_health_checks += 1
                    health_check_ids.add(hc_id)

                if rs.get('Failover'):
                    failover_records.append({
                        'Name': rs.get('Name'),
                        'Type': rs.get('Type'),
                        'Failover': rs.get('Failover'),
                        'SetIdentifier': rs.get('SetIdentifier'),
                        'HealthCheckId': hc_id,
                        'TTL': rs.get('TTL'),
                        'AliasTarget': rs.get('AliasTarget'),
                    })

            dimensions.append(DimensionOutput(name='RecordsWithHealthChecks', value=records_with_health_checks))
            dimensions.append(DimensionOutput(name='TotalUserRecords', value=total_records))
            dimensions.append(DimensionOutput(name='FailoverRecords', value=failover_records))
        except ClientError:
            dimensions.append(DimensionOutput(name='RecordsWithHealthChecks', value=0))
            dimensions.append(DimensionOutput(name='TotalUserRecords', value=0))
            dimensions.append(DimensionOutput(name='FailoverRecords', value=[]))
            health_check_ids = set()

        # Health check details
        health_checks = []
        try:
            for hc_id in health_check_ids:
                hc_resp = route53_client.get_health_check(HealthCheckId=hc_id)
                hc = hc_resp.get('HealthCheck', {})
                config = hc.get('HealthCheckConfig', {})
                health_checks.append({
                    'Id': hc.get('Id'),
                    'Type': config.get('Type'),
                    'FQDN': config.get('FullyQualifiedDomainName'),
                    'IPAddress': config.get('IPAddress'),
                    'Port': config.get('Port'),
                    'ResourcePath': config.get('ResourcePath'),
                    'RequestInterval': config.get('RequestInterval'),
                    'FailureThreshold': config.get('FailureThreshold'),
                    'MeasureLatency': config.get('MeasureLatency', False),
                    'Inverted': config.get('Inverted', False),
                    'Disabled': config.get('Disabled', False),
                    'EnableSNI': config.get('EnableSNI', False),
                    'Regions': config.get('Regions', []),
                    'InsufficientDataHealthStatus': config.get('InsufficientDataHealthStatus'),
                })
        except ClientError:
            pass
        dimensions.append(DimensionOutput(name='HealthChecks', value=health_checks))

        return dimensions

if __name__ == "__main__":
    import json
    from src.core.aws_client import AWSClientProvider

    provider = AWSClientProvider()
    fetcher = Route53DimensionFetcher(provider)

    # Private hosted zone from StackSet-AWS-Landing-Zone-Baseline-AppPrivateZone
    zone_id = "Z031275937P7R9GB2N55P"
    resource_type = "AWS::Route53::HostedZone"

    print(f"Fetching dimensions for hosted zone: {zone_id}\n")
    results = fetcher.get_dimensions(zone_id, resource_type=resource_type)
    print(json.dumps([d.model_dump() for d in results], indent=2))
