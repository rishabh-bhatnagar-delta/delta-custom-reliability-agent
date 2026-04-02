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
    _analyze_active_active_metrics(a, asg_detail)
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
                   "No ASG found (AutoScalingGroup=None). Standalone instance with no redundancy or automatic recovery.")
        return

    desired = asg_detail.get("DesiredCapacity", 0)
    min_size = asg_detail.get("MinSize", 0)
    azs = asg_detail.get("AvailabilityZones", [])
    tg_arns = asg_detail.get("TargetGroupARNs", [])
    tg_health = asg_detail.get("TargetGroupHealth", [])
    healthy_targets = sum(1 for t in tg_health if t.get("HealthState") == "healthy")

    if desired == 0 and min_size == 0:
        a.add_gap("Failover Configuration", "NO FAILOVER",
                   f"ASG '{asg_detail.get('Name', '?')}' has DesiredCapacity=0, MinSize=0. Nothing is running or standing by.")
    elif desired == 1:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   f"ASG DesiredCapacity=1, AZs={azs}. Single instance running; ASG replaces on failure but with downtime.")
    elif len(azs) <= 1:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   f"ASG DesiredCapacity={desired} but only 1 AZ ({azs}). AZ failure causes full outage.")
    elif not tg_arns:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   f"ASG DesiredCapacity={desired}, AZs={azs} (multi-AZ) but TargetGroupARNs is empty. No load balancer distributes traffic.")
    elif healthy_targets < 2:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   f"ASG DesiredCapacity={desired}, multi-AZ, has TG but only {healthy_targets} healthy target(s). Not truly Active-Active.")
    else:
        a.add_gap("Failover Configuration", "ACTIVE-ACTIVE",
                   f"ASG DesiredCapacity={desired}, AZs={azs}, {healthy_targets} healthy targets behind load balancer. Traffic distributed across multiple healthy instances in multiple AZs.")


def _analyze_placement(a: ResilienceAnalyzer, asg_detail: dict):
    if asg_detail:
        azs = asg_detail.get("AvailabilityZones", [])
        if len(azs) > 1:
            if not asg_detail.get("TargetGroupARNs"):
                a.add_gap("Placement", "MULTI-AZ WITHOUT LOAD BALANCER",
                           "Instances span AZs but are not behind a load balancer; they are not working together.",
                           recommendation="Attach the ASG to a target group/load balancer so multi-AZ instances can share traffic.")
        else:
            a.add_gap("Placement", "AZ-SPOF",
                       "All instances in a single AZ; AZ failure causes full outage.",
                       recommendation="Configure the ASG to span multiple Availability Zones.")
    else:
        a.add_gap("Placement", "AZ-SPOF",
                   "Standalone instance in a single AZ; no redundancy.",
                   recommendation="Place the instance behind an Auto Scaling Group spanning multiple AZs.")


def _analyze_management(a: ResilienceAnalyzer, asg_name: str, asg_detail: dict):
    if asg_name and asg_detail:
        if asg_detail.get("MinSize", 0) == 0 and asg_detail.get("DesiredCapacity", 0) == 0:
            a.add_gap("Management", "INACTIVE",
                       "ASG exists but MinSize and DesiredCapacity are 0; no instances running. No active or standby capacity.",
                       recommendation="Set DesiredCapacity >= 1 to activate the ASG, or remove it if unused.")
    else:
        a.add_gap("Management", "MANUAL/BRITTLE",
                   "No Auto Scaling Group; failed instances are not automatically replaced.",
                   recommendation="Create an Auto Scaling Group to enable automatic instance replacement.")


def _analyze_capacity(a: ResilienceAnalyzer, asg_name: str, asg_detail: dict):
    if not asg_detail:
        return
    desired = asg_detail.get("DesiredCapacity", 0)
    if desired == 1:
        a.add_gap("Capacity", "ACTIVE-PASSIVE",
                   "DesiredCapacity is 1; ASG will restart a failed instance but there is downtime during replacement.",
                   recommendation="Increase DesiredCapacity to at least 2 for Active-Active with zero-downtime failover.",
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
                           recommendation="Ensure at least 2 healthy instances are InService in the target group for Active-Active.")
        else:
            a.add_gap("Traffic", "NO TARGET GROUP",
                       "ASG is not attached to a target group; no load-balanced traffic distribution.",
                       recommendation="Attach the ASG to an ALB/NLB target group for traffic distribution.")
    else:
        if not a.dim("DirectTargetGroups", []):
            a.add_gap("Traffic", "STANDALONE",
                       "Instance is not in any target group; no load-balanced traffic.",
                       recommendation="Register the instance in a target group behind a load balancer.")


def _analyze_active_active_metrics(a: ResilienceAnalyzer, asg_detail: dict):
    """When classified as ACTIVE-ACTIVE, verify via CloudWatch that all instances are actually receiving traffic."""
    if not asg_detail:
        return

    metrics = a.dim("ASGInstanceMetrics")
    if not metrics:
        return

    # Only relevant when we have multi-instance, multi-AZ, load-balanced setup
    desired = asg_detail.get("DesiredCapacity", 0)
    azs = asg_detail.get("AvailabilityZones", [])
    tg_arns = asg_detail.get("TargetGroupARNs", [])
    if desired < 2 or len(azs) <= 1 or not tg_arns:
        return

    # Check for instances with no network traffic (idle behind LB)
    idle_instances = []
    no_data_instances = []
    for m in metrics:
        iid = m.get("instance_id", "?")
        avg_net = m.get("avg_network_in")
        avg_cpu = m.get("avg_cpu")

        if avg_net is None and avg_cpu is None:
            no_data_instances.append(iid)
        elif avg_net is not None and avg_net < 1000:
            # Less than 1 KB/s average network-in over the last hour — effectively idle
            idle_instances.append(iid)

    if no_data_instances:
        a.add_gap(
            "Active-Active Metrics", "NO CLOUDWATCH DATA",
            f"No CloudWatch metrics available for instance(s) {no_data_instances}. "
            "Cannot confirm these instances are actively serving traffic.",
            recommendation="Enable detailed monitoring or verify instances are healthy and receiving traffic.",
        )

    if idle_instances:
        a.add_gap(
            "Active-Active Metrics", "IDLE INSTANCES DETECTED",
            f"Instance(s) {idle_instances} show near-zero NetworkIn (<1 KB/s avg over 1h) despite being behind a load balancer. "
            "Traffic may not be distributed evenly — this is Active-Active in config but not in practice.",
            recommendation="Investigate load balancer routing rules, health check configuration, and target group weights "
                           "to ensure traffic reaches all instances.",
        )

    if not idle_instances and not no_data_instances:
        # All instances are receiving traffic — confirm healthy active-active
        instance_summary = ", ".join(
            f"{m['instance_id']}(cpu={m.get('avg_cpu', '?')}%, net_in={m.get('avg_network_in', '?')}B/s)"
            for m in metrics
        )
        a.add_gap(
            "Active-Active Metrics", "VERIFIED",
            f"All {len(metrics)} instances show active CloudWatch metrics (CPU and NetworkIn) over the last hour: {instance_summary}. "
            "Traffic is being distributed across instances.",
        )


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
                   recommendation="Configure AWS Backup for the instance.")
