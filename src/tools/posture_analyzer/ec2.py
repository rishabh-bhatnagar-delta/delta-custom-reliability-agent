from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_ec2_resilience_report(instance_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for EC2 instances."""
    a = ResilienceAnalyzer(instance_id, dimensions)

    asg_detail = a.dim("ASGDetail")
    asg_name = a.dim("AutoScalingGroup")

    # Emit failover configuration classification first
    _classify_failover_config(a, asg_detail)

    _analyze_placement(a, asg_detail)
    _analyze_management(a, asg_name, asg_detail)
    _analyze_capacity(a, asg_name, asg_detail)
    _analyze_traffic(a, asg_detail)
    _analyze_security_and_backup(a, instance_id)

    return a.build(f"EC2 '{instance_id}'")


def _classify_failover_config(a: ResilienceAnalyzer, asg_detail: dict):
    """
    Classify EC2 failover configuration as ACTIVE-ACTIVE, ACTIVE-PASSIVE, or NO FAILOVER.

    Logic:
    ┌─ No ASG?
    │   └─ SILOED: standalone instance, no auto-recovery, no redundancy.
    │
    ├─ ASG with DesiredCapacity=0 and MinSize=0?
    │   └─ SILOED: ASG exists but nothing is running or standing by.
    │
    ├─ ASG with DesiredCapacity=1?
    │   └─ ACTIVE-PASSIVE: one instance runs; ASG replaces it on failure (with downtime).
    │
    ├─ ASG with DesiredCapacity>=2, single AZ?
    │   └─ ACTIVE-PASSIVE: multiple instances but all in one AZ; AZ failure = full outage.
    │
    ├─ ASG with DesiredCapacity>=2, multi-AZ, no target group?
    │   └─ ACTIVE-PASSIVE: instances span AZs but no load balancer distributes traffic.
    │
    └─ ASG with DesiredCapacity>=2, multi-AZ, target group with >=2 healthy targets?
        └─ ACTIVE-ACTIVE: traffic distributed across multiple healthy instances in multiple AZs.
    """
    if not asg_detail:
        a.add_gap("Failover Configuration", "NO FAILOVER",
                   "Standalone instance with no Auto Scaling Group; no redundancy or automatic recovery.",
                   penalty=0)
        return

    desired = asg_detail.get("DesiredCapacity", 0)
    min_size = asg_detail.get("MinSize", 0)
    azs = asg_detail.get("AvailabilityZones", [])
    tg_arns = asg_detail.get("TargetGroupARNs", [])
    tg_health = asg_detail.get("TargetGroupHealth", [])
    healthy_targets = sum(1 for t in tg_health if t.get("HealthState") == "healthy")

    if desired == 0 and min_size == 0:
        a.add_gap("Failover Configuration", "NO FAILOVER",
                   "ASG exists but DesiredCapacity and MinSize are 0; nothing is running or standing by.",
                   penalty=0)
    elif desired == 1:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   "Single instance running; ASG replaces on failure but with downtime during replacement.",
                   penalty=0)
    elif len(azs) <= 1:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   "Multiple instances but all in a single AZ; AZ failure causes full outage.",
                   penalty=0)
    elif not tg_arns:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   "Multi-AZ instances but no load balancer; traffic is not distributed across them.",
                   penalty=0)
    elif healthy_targets < 2:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   f"Only {healthy_targets} healthy target(s) behind the load balancer; not truly Active-Active.",
                   penalty=0)
    else:
        a.add_gap("Failover Configuration", "ACTIVE-ACTIVE",
                   "Traffic distributed across multiple healthy instances in multiple AZs.",
                   penalty=0)


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
            a.add_gap("Management", "INACTIVE",
                       "ASG exists but MinSize and DesiredCapacity are 0; no instances running. No active or standby capacity.",
                       penalty=2, recommendation="Set DesiredCapacity >= 1 to activate the ASG, or remove it if unused.")
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
