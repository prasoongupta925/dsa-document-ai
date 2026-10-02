# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0


from aws_cdk import aws_ec2 as ec2
from constructs import Construct
import cdk_nag

class VpcConstruct(Construct):
    """
    L3 Construct that creates a new VPC with public, private, and isolated subnets.
    """

    def __init__(
        self, scope: Construct, construct_id: str, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "VoicebotVPC",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )
        self.public_subnets = self.vpc.public_subnets
        self.private_subnets = self.vpc.private_subnets
        cdk_nag.NagSuppressions.add_resource_suppressions(
            self.vpc,
            [
                {
                    'id': 'AwsSolutions-VPC7',
                    'reason': 'This VPC is used for demo purposes and is not meant for production use.'
                }
            ]
        )