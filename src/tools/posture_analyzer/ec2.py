from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_ec2_resilience_report(instance_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for EC2 instances."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10

    asg_detail = dim_map.get("ASGDetail")
    asg_name = dim_map.get("AutoScalingGroup")

    # --- Placement ---
    if asg_detail:
        azs = asg_detail.get("AvailabilityZones", [])
        if len(azs) > 1:
            # AZ-Resilient, but check if behind a load balancer
            if not asg_detail.get("TargetGroupARNs"):
                score -= 1
                gaps.append(ResilienceGap(
                    name="Placement",
                    status="MULTI-AZ WITHOUT LOAD BALANCER",
                    impact="Instances span AZs but are not behind a load balancer; they are not working together.",
                ))
                recommendations.append(
                    "Attach the ASG to a target group/load balancer so multi-AZ instances can share traffic."
                )
        else:
            score -= 2
            gaps.append(ResilienceGap(
                name="Placement",
                status="AZ-SPOF",
                impact="All instances in a single AZ; AZ failure causes full outage.",
            ))
            recommendations.append("Configure the ASG to span multiple Availability Zones.")
    else:
        score -= 2
        gaps.append(ResilienceGap(
            name="Placement",
            status="AZ-SPOF",
            impact="Standalone instance in a single AZ; no redundancy.",
        ))
        recommendations.append("Place the instance behind an Auto Scaling Group spanning multiple AZs.")

    # --- Management ---
    if asg_name and asg_detail:
        min_size = asg_detail.get("MinSize", 0)
        desired = asg_detail.get("DesiredCapacity", 0)
        if min_size == 0 and desired == 0:
            score -= 1
            gaps.append(ResilienceGap(
                name="Management",
                status="COLD STANDBY",
                impact="ASG exists but MinSize and DesiredCapacity are 0; no instances running. This is a cold standby (Active-Passive).",
            ))
            recommendations.append(
                "Set DesiredCapacity >= 1 for active operation, or document this as intentional cold standby."
            )
        # else: Self-Healing — ASG will replace failed instances automatically
    else:
        score -= 2
        gaps.append(ResilienceGap(
            name="Management",
            status="MANUAL/BRITTLE",
            impact="No Auto Scaling Group; failed instances are not automatically replaced.",
        ))
        recommendations.append("Create an Auto Scaling Group to enable automatic instance replacement.")

    # --- Capacity ---
    if asg_detail:
        desired = asg_detail.get("DesiredCapacity", 0)
        if desired >= 2:
            pass  # Active-Active
        elif desired == 1:
            score -= 1
            gaps.append(ResilienceGap(
                name="Capacity",
                status="ACTIVE-PASSIVE",
                impact="DesiredCapacity is 1; ASG will restart a failed instance but there is downtime during replacement.",
            ))
            recommendations.append(
                "Increase DesiredCapacity to at least 2 for Active-Active with zero-downtime failover."
            )
            cli_commands.append(
                f"aws autoscaling update-auto-scaling-group --auto-scaling-group-name {asg_name} "
                f"--desired-capacity 2 --min-size 2"
            )

    # --- Traffic ---
    if asg_detail:
        tg_arns = asg_detail.get("TargetGroupARNs", [])
        tg_health = asg_detail.get("TargetGroupHealth", [])
        if tg_arns:
            healthy_targets = sum(1 for t in tg_health if t.get("HealthState") == "healthy")
            if healthy_targets < 2:
                score -= 1
                gaps.append(ResilienceGap(
                    name="Traffic",
                    status=f"{healthy_targets} HEALTHY TARGET(S)",
                    impact="Fewer than 2 healthy targets in the target group; not truly Active-Active.",
                ))
                recommendations.append(
                    "Ensure at least 2 healthy instances are InService in the target group for Active-Active."
                )
        else:
            score -= 1
            gaps.append(ResilienceGap(
                name="Traffic",
                status="NO TARGET GROUP",
                impact="ASG is not attached to a target group; no load-balanced traffic distribution.",
            ))
            recommendations.append("Attach the ASG to an ALB/NLB target group for traffic distribution.")
    else:
        # No ASG — check direct target group membership
        direct_tgs = dim_map.get("DirectTargetGroups", [])
        if not direct_tgs:
            gaps.append(ResilienceGap(
                name="Traffic",
                status="STANDALONE",
                impact="Instance is not in any target group; no load-balanced traffic.",
            ))
            recommendations.append("Register the instance in a target group behind a load balancer.")

    # --- Additional checks ---

    # IMDSv2
    if not dim_map.get("IMDSv2Enforced", False):
        gaps.append(ResilienceGap(
            name="IMDSv2",
            status="NOT ENFORCED",
            impact="Instance metadata service v1 is accessible; security risk.",
        ))
        recommendations.append("Enforce IMDSv2 (HttpTokens=required) on the instance.")
        cli_commands.append(
            f"aws ec2 modify-instance-metadata-options --instance-id {instance_id} "
            f"--http-tokens required --http-endpoint enabled"
        )

    # Root volume encryption
    if not dim_map.get("RootVolumeEncrypted", False):
        gaps.append(ResilienceGap(
            name="Root Volume Encryption",
            status="UNENCRYPTED",
            impact="Root volume is not encrypted; data at rest is exposed.",
        ))
        recommendations.append("Use encrypted EBS volumes for all instances.")

    # Backup
    if not dim_map.get("HasBackup", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="Backup",
            status="NO BACKUP",
            impact="No AWS Backup recovery points; instance data cannot be restored.",
        ))
        recommendations.append("Configure AWS Backup for the instance.")

    score = max(0, min(10, score))

    total_issues = len(gaps)
    if score >= 8:
        summary = f"EC2 '{instance_id}' has a strong reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"EC2 '{instance_id}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"EC2 '{instance_id}' has significant reliability gaps. {total_issues} issue(s) require remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=instance_id,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
