from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_ec2_resilience_report(instance_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for EC2 instances."""
    a = ResilienceAnalyzer(instance_id, dimensions)

    asg_detail = a.dim("ASGDetail")
    asg_name = a.dim("AutoScalingGroup")

    _analyze_placement(a, asg_detail)
    _analyze_management(a, asg_name, asg_detail)
    _analyze_capacity(a, asg_name, asg_detail)
    _analyze_traffic(a, asg_detail)
    _analyze_security_and_backup(a, instance_id)

    return a.build(f"EC2 '{instance_id}'")


def _analyze_placement(a: ResilienceAnalyzer, asg_detail: dict):
    if asg_detail:
        azs = asg_detail.get("AvailabilityZones", [])
        if len(azs) > 1:
            if not asg_detail.get("TargetGroupARNs"):
                a.add_gap("Placement", "MULTI-AZ WITHOUT LOAD BALANCER",
                           "Instances span AZs but are not behind a load balancer; they are not working together.",
                           penalty=1, recommendation="Attach the ASG to a target group/load balancer so multi-AZ instances can share traffic.")
        else:
            a.add_gap("Placement", "AZ-SPOF",
                       "All instances in a single AZ; AZ failure causes full outage.",
                       penalty=2, recommendation="Configure the ASG to span multiple Availability Zones.")
    else:
        a.add_gap("Placement", "AZ-SPOF",
                   "Standalone instance in a single AZ; no redundancy.",
                   penalty=2, recommendation="Place the instance behind an Auto Scaling Group spanning multiple AZs.")


def _analyze_management(a: ResilienceAnalyzer, asg_name: str, asg_detail: dict):
    if asg_name and asg_detail:
        if asg_detail.get("MinSize", 0) == 0 and asg_detail.get("DesiredCapacity", 0) == 0:
            a.add_gap("Management", "COLD STANDBY",
                       "ASG exists but MinSize and DesiredCapacity are 0; no instances running. This is a cold standby (Active-Passive).",
                       penalty=1, recommendation="Set DesiredCapacity >= 1 for active operation, or document this as intentional cold standby.")
    else:
        a.add_gap("Management", "MANUAL/BRITTLE",
                   "No Auto Scaling Group; failed instances are not automatically replaced.",
                   penalty=2, recommendation="Create an Auto Scaling Group to enable automatic instance replacement.")


def _analyze_capacity(a: ResilienceAnalyzer, asg_name: str, asg_detail: dict):
    if not asg_detail:
        return
    desired = asg_detail.get("DesiredCapacity", 0)
    if desired == 1:
        a.add_gap("Capacity", "ACTIVE-PASSIVE",
                   "DesiredCapacity is 1; ASG will restart a failed instance but there is downtime during replacement.",
                   penalty=1, recommendation="Increase DesiredCapacity to at least 2 for Active-Active with zero-downtime failover.",
                   cli=f"aws autoscaling update-auto-scaling-group --auto-scaling-group-name {asg_name} --desired-capacity 2 --min-size 2")


def _analyze_traffic(a: ResilienceAnalyzer, asg_detail: dict):
    if asg_detail:
        tg_arns = asg_detail.get("TargetGroupARNs", [])
        tg_health = asg_detail.get("TargetGroupHealth", [])
        if tg_arns:
            healthy = sum(1 for t in tg_health if t.get("HealthState") == "healthy")
            if healthy < 2:
                a.add_gap("Traffic", f"{healthy} HEALTHY TARGET(S)",
                           "Fewer than 2 healthy targets in the target group; not truly Active-Active.",
                           penalty=1, recommendation="Ensure at least 2 healthy instances are InService in the target group for Active-Active.")
        else:
            a.add_gap("Traffic", "NO TARGET GROUP",
                       "ASG is not attached to a target group; no load-balanced traffic distribution.",
                       penalty=1, recommendation="Attach the ASG to an ALB/NLB target group for traffic distribution.")
    else:
        if not a.dim("DirectTargetGroups", []):
            a.add_gap("Traffic", "STANDALONE",
                       "Instance is not in any target group; no load-balanced traffic.",
                       recommendation="Register the instance in a target group behind a load balancer.")


def _analyze_security_and_backup(a: ResilienceAnalyzer, instance_id: str):
    if not a.dim("IMDSv2Enforced", False):
        a.add_gap("IMDSv2", "NOT ENFORCED",
                   "Instance metadata service v1 is accessible; security risk.",
                   recommendation="Enforce IMDSv2 (HttpTokens=required) on the instance.",
                   cli=f"aws ec2 modify-instance-metadata-options --instance-id {instance_id} --http-tokens required --http-endpoint enabled")

    if not a.dim("RootVolumeEncrypted", False):
        a.add_gap("Root Volume Encryption", "UNENCRYPTED",
                   "Root volume is not encrypted; data at rest is exposed.",
                   recommendation="Use encrypted EBS volumes for all instances.")

    if not a.dim("HasBackup", False):
        a.add_gap("Backup", "NO BACKUP",
                   "No AWS Backup recovery points; instance data cannot be restored.",
                   penalty=1, recommendation="Configure AWS Backup for the instance.")
