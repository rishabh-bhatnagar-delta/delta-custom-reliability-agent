from datetime import datetime
from typing import Any, List
from botocore.exceptions import ClientError

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj


class EC2DimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.EC2

    def _fetch_dimensions(self, physical_id: str) -> List[DimensionOutput]:
        dimensions = []
        ec2_client = self.get_aws_client_for_resource()

        resp = ec2_client.describe_instances(InstanceIds=[physical_id])
        instance = _sanitize(resp['Reservations'][0]['Instances'][0])

        # Instance type
        dimensions.append(DimensionOutput(name='InstanceType', value=instance.get('InstanceType')))

        # State
        dimensions.append(DimensionOutput(name='State', value=instance.get('State', {}).get('Name')))

        # Availability Zone
        az = instance.get('Placement', {}).get('AvailabilityZone')
        dimensions.append(DimensionOutput(name='AvailabilityZone', value=az))

        # Platform
        dimensions.append(DimensionOutput(name='Platform', value=instance.get('PlatformDetails', 'Linux/UNIX')))

        # Architecture
        dimensions.append(DimensionOutput(name='Architecture', value=instance.get('Architecture')))

        # Public IP
        dimensions.append(DimensionOutput(name='PublicIp', value=instance.get('PublicIpAddress')))

        # IAM Instance Profile
        profile = instance.get('IamInstanceProfile', {}).get('Arn')
        dimensions.append(DimensionOutput(name='IamInstanceProfile', value=profile))

        # IMDSv2 enforcement
        metadata_options = instance.get('MetadataOptions', {})
        imdsv2 = metadata_options.get('HttpTokens') == 'required'
        dimensions.append(DimensionOutput(name='IMDSv2Enforced', value=imdsv2))

        # Monitoring (detailed vs basic)
        monitoring = instance.get('Monitoring', {}).get('State', 'disabled')
        dimensions.append(DimensionOutput(name='DetailedMonitoring', value=monitoring == 'enabled'))

        # EBS optimized
        dimensions.append(DimensionOutput(name='EbsOptimized', value=instance.get('EbsOptimized', False)))

        # Root volume encryption
        root_device = instance.get('RootDeviceName')
        root_encrypted = False
        volume_ids = []
        for bdm in instance.get('BlockDeviceMappings', []):
            vol_id = bdm.get('Ebs', {}).get('VolumeId')
            if vol_id:
                volume_ids.append(vol_id)

        if volume_ids:
            try:
                vol_resp = ec2_client.describe_volumes(VolumeIds=volume_ids)
                for vol in vol_resp.get('Volumes', []):
                    for att in vol.get('Attachments', []):
                        if att.get('Device') == root_device:
                            root_encrypted = vol.get('Encrypted', False)
                dimensions.append(DimensionOutput(name='RootVolumeEncrypted', value=root_encrypted))
                dimensions.append(DimensionOutput(name='AttachedVolumes', value=len(volume_ids)))
            except ClientError:
                dimensions.append(DimensionOutput(name='RootVolumeEncrypted', value=False))
                dimensions.append(DimensionOutput(name='AttachedVolumes', value=0))
        else:
            dimensions.append(DimensionOutput(name='RootVolumeEncrypted', value=False))
            dimensions.append(DimensionOutput(name='AttachedVolumes', value=0))

        # Security groups
        sgs = [{'GroupId': sg.get('GroupId'), 'GroupName': sg.get('GroupName')}
               for sg in instance.get('SecurityGroups', [])]
        dimensions.append(DimensionOutput(name='SecurityGroups', value=sgs))

        # Auto Scaling Group membership
        asg_client = self.get_aws_client_provider().get_client_by_service_name('autoscaling')
        try:
            asg_resp = asg_client.describe_auto_scaling_instances(InstanceIds=[physical_id])
            asg_instances = asg_resp.get('AutoScalingInstances', [])
            asg_name = asg_instances[0].get('AutoScalingGroupName') if asg_instances else None
            dimensions.append(DimensionOutput(name='AutoScalingGroup', value=asg_name))
        except ClientError:
            dimensions.append(DimensionOutput(name='AutoScalingGroup', value=None))

        # Backup (AWS Backup recovery points)
        backup_client = self.get_aws_client_provider().get_client_by_service_name('backup')
        instance_arn = f"arn:aws:ec2:{ec2_client.meta.region_name}:{instance.get('OwnerId', '')}:instance/{physical_id}"
        try:
            rp_resp = backup_client.list_recovery_points_by_resource(ResourceArn=instance_arn)
            has_backup = len(rp_resp.get('RecoveryPoints', [])) > 0
            dimensions.append(DimensionOutput(name='HasBackup', value=has_backup))
        except ClientError:
            dimensions.append(DimensionOutput(name='HasBackup', value=False))

        return dimensions


if __name__ == "__main__":
    import json
    from src.core.aws_client import AWSClientProvider

    provider = AWSClientProvider()
    fetcher = EC2DimensionFetcher(provider)

    # Example: Chaos-RHBL-EC2-Test stack EC2 instance
    instance_id = "i-0c0fb8c035be65356"
    resource_type = "AWS::EC2::Instance"

    print(f"Fetching dimensions for EC2 instance: {instance_id}\n")
    results = fetcher.get_dimensions(instance_id, resource_type=resource_type)
    print(json.dumps([d.model_dump() for d in results], indent=2))
