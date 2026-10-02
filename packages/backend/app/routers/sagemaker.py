"""SageMaker Endpoint Management API

Provides endpoints for:
- Status check (current instance count, endpoint status)
- Start (scale to 1 instance)
- Stop (scale to 0 instances)
- Settings (scale-in timeout configuration)
"""

import boto3
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import get_config

router = APIRouter(prefix="/sagemaker", tags=["sagemaker"])

config = get_config()


class EndpointStatus(BaseModel):
    endpoint_name: str
    status: str
    current_instance_count: int
    desired_instance_count: int


class ScaleInSettings(BaseModel):
    evaluation_periods: int  # minutes before scale-in


class ScaleInSettingsUpdate(BaseModel):
    evaluation_periods: int  # 1-60 minutes


def get_sagemaker_client():
    return boto3.client("sagemaker", region_name=config.aws_region)


def get_cloudwatch_client():
    return boto3.client("cloudwatch", region_name=config.aws_region)


@router.get("/status", response_model=EndpointStatus)
async def get_endpoint_status():
    """Get current SageMaker endpoint status."""
    client = get_sagemaker_client()
    endpoint_name = config.paddleocr_endpoint_name

    try:
        response = client.describe_endpoint(EndpointName=endpoint_name)

        # Get production variant info
        variants = response.get("ProductionVariants", [])
        current_count = 0
        desired_count = 0

        if variants:
            current_count = variants[0].get("CurrentInstanceCount", 0)
            desired_count = variants[0].get("DesiredInstanceCount", 0)

        return EndpointStatus(
            endpoint_name=endpoint_name,
            status=response.get("EndpointStatus", "Unknown"),
            current_instance_count=current_count,
            desired_instance_count=desired_count,
        )
    except client.exceptions.ClientError as e:
        if "Could not find endpoint" in str(e):
            raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_name} not found") from None
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/start")
async def start_endpoint():
    """Start the SageMaker endpoint (scale to 1 instance)."""
    client = get_sagemaker_client()
    endpoint_name = config.paddleocr_endpoint_name

    try:
        # Check current status
        response = client.describe_endpoint(EndpointName=endpoint_name)
        status = response.get("EndpointStatus")

        if status == "Updating":
            return {"message": "Endpoint is already updating", "status": status}

        variants = response.get("ProductionVariants", [])
        if variants and variants[0].get("CurrentInstanceCount", 0) > 0:
            return {
                "message": "Endpoint is already running",
                "current_instance_count": variants[0]["CurrentInstanceCount"],
            }

        # Scale to 1 instance
        client.update_endpoint_weights_and_capacities(
            EndpointName=endpoint_name,
            DesiredWeightsAndCapacities=[
                {
                    "VariantName": "AllTraffic",
                    "DesiredInstanceCount": 1,
                }
            ],
        )

        return {"message": "Endpoint starting", "desired_instance_count": 1}

    except client.exceptions.ClientError as e:
        if "Could not find endpoint" in str(e):
            raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_name} not found") from None
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/stop")
async def stop_endpoint():
    """Stop the SageMaker endpoint (scale to 0 instances)."""
    client = get_sagemaker_client()
    endpoint_name = config.paddleocr_endpoint_name

    try:
        # Check current status
        response = client.describe_endpoint(EndpointName=endpoint_name)
        status = response.get("EndpointStatus")

        if status == "Updating":
            return {"message": "Endpoint is already updating", "status": status}

        variants = response.get("ProductionVariants", [])
        if variants and variants[0].get("CurrentInstanceCount", 0) == 0:
            return {"message": "Endpoint is already stopped", "current_instance_count": 0}

        # Scale to 0 instances
        client.update_endpoint_weights_and_capacities(
            EndpointName=endpoint_name,
            DesiredWeightsAndCapacities=[
                {
                    "VariantName": "AllTraffic",
                    "DesiredInstanceCount": 0,
                }
            ],
        )

        return {"message": "Endpoint stopping", "desired_instance_count": 0}

    except client.exceptions.ClientError as e:
        if "Could not find endpoint" in str(e):
            raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_name} not found") from None
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/settings", response_model=ScaleInSettings)
async def get_scale_in_settings():
    """Get current scale-in timeout settings."""
    client = get_cloudwatch_client()
    alarm_name = config.paddleocr_scale_in_alarm_name

    try:
        response = client.describe_alarms(AlarmNames=[alarm_name])

        alarms = response.get("MetricAlarms", [])
        if not alarms:
            raise HTTPException(status_code=404, detail=f"Alarm {alarm_name} not found")

        alarm = alarms[0]
        evaluation_periods = alarm.get("EvaluationPeriods", 10)

        return ScaleInSettings(evaluation_periods=evaluation_periods)

    except client.exceptions.ClientError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/settings", response_model=ScaleInSettings)
async def update_scale_in_settings(settings: ScaleInSettingsUpdate):
    """Update scale-in timeout settings (evaluation periods in minutes)."""
    if settings.evaluation_periods < 1 or settings.evaluation_periods > 60:
        raise HTTPException(status_code=400, detail="evaluation_periods must be between 1 and 60")

    client = get_cloudwatch_client()
    alarm_name = config.paddleocr_scale_in_alarm_name

    try:
        # Get current alarm configuration
        response = client.describe_alarms(AlarmNames=[alarm_name])
        alarms = response.get("MetricAlarms", [])

        if not alarms:
            raise HTTPException(status_code=404, detail=f"Alarm {alarm_name} not found")

        alarm = alarms[0]

        # Update alarm with new evaluation periods
        client.put_metric_alarm(
            AlarmName=alarm_name,
            MetricName=alarm["MetricName"],
            Namespace=alarm["Namespace"],
            Statistic=alarm.get("Statistic", "Average"),
            Dimensions=alarm.get("Dimensions", []),
            Period=alarm.get("Period", 60),
            EvaluationPeriods=settings.evaluation_periods,
            Threshold=alarm.get("Threshold", 0.1),
            ComparisonOperator=alarm.get("ComparisonOperator", "LessThanThreshold"),
            TreatMissingData=alarm.get("TreatMissingData", "breaching"),
            AlarmActions=alarm.get("AlarmActions", []),
            OKActions=alarm.get("OKActions", []),
            AlarmDescription=alarm.get("AlarmDescription", ""),
        )

        return ScaleInSettings(evaluation_periods=settings.evaluation_periods)

    except client.exceptions.ClientError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
