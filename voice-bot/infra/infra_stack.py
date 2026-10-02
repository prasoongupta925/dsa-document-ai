# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from aws_cdk import (
    Stack,
    CfnOutput,
    RemovalPolicy,
    SecretValue,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_cognito as cognito,
    aws_certificatemanager as acm,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
    aws_s3_deployment as s3_deployment,
)
from constructs import Construct
from aws_cdk.aws_ecr_assets import Platform
import cdk_nag

class InfraStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        account = Stack.of(self).account
        region = Stack.of(self).region
        
        knowledgebase = self.node.try_get_context("knowledgebase")
        llm_model = self.node.try_get_context("llm_model")
        secret_name = self.node.try_get_context("secret_name")

        vpc = ec2.Vpc(
            self,
            "VoicebotVPC",
            max_azs=1,
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
        private_subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
        )
        public_subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PUBLIC
        )

        user_pool = cognito.UserPool(
            self, "VoicebotUserPool",
            removal_policy=RemovalPolicy.DESTROY,
            self_sign_up_enabled=False,
            enable_sms_role=False,
            mfa=cognito.Mfa.OFF,
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_digits=True,
                require_lowercase=True,
                require_symbols=True,
                require_uppercase=True
            ),
            standard_attributes=cognito.StandardAttributes(
                email=cognito.StandardAttribute(
                    required=True,
                    mutable=False
                )
            ),
            auto_verify=cognito.AutoVerifiedAttrs(
                email=True
            )
        )

        user_pool_client = user_pool.add_client(
            "VoicebotUserPoolClient",
            auth_flows=cognito.AuthFlow(
                admin_user_password=False,
                custom=False,
                user_password=False,
                user_srp=True
            ),
            disable_o_auth=True,
            prevent_user_existence_errors=True,
            supported_identity_providers=[]
        )

        identity_pool = cognito.CfnIdentityPool(
            self, "VoicebotIdentityPool",
            allow_unauthenticated_identities=False,
            allow_classic_flow=False,
            cognito_identity_providers=[cognito.CfnIdentityPool.CognitoIdentityProviderProperty(
                client_id=user_pool_client.user_pool_client_id,
                provider_name=user_pool.user_pool_provider_name
            )]
        )

        authenticated_role = iam.Role(
            self, "VoicebotAuthenticatedRole",
            assumed_by=iam.FederatedPrincipal(
                'cognito-identity.amazonaws.com',
                {
                    "StringEquals": {
                        "cognito-identity.amazonaws.com:aud": identity_pool.ref
                    },
                    "ForAnyValue:StringLike": {
                        "cognito-identity.amazonaws.com:amr": "authenticated"
                    }
                },
                'sts:AssumeRoleWithWebIdentity'
            )
        )
        cognito.CfnIdentityPoolRoleAttachment(
            self, "VoicebotRoleAttachment",
            identity_pool_id=identity_pool.ref,
            roles={
                'authenticated': authenticated_role.role_arn
            }
        )
        
        cluster = ecs.Cluster(
            self, "VoicebotCluster",
            vpc=vpc,
            cluster_name="voicebot-cluster",
        )

        task_definition = ecs.FargateTaskDefinition(
            self, "VoicebotTaskDef",
            memory_limit_mib=8192,
            cpu=2048,
        )
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "transcribe:StartStreamTranscriptionWebSocket",
                    "transcribe:StartStreamTranscription",
                    "polly:SynthesizeSpeech",

                ],
                resources=["*"]
            )
        )
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream"
                ],
                resources=[
                    f"arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{account}:inference-profile/*"
                ]
            )
        )
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=[f"arn:aws:bedrock:{region}:{account}:knowledge-base/{knowledgebase}"]
            )
        )

        secret = secretsmanager.Secret.from_secret_name_v2(self, "VoicebotSecret", secret_name)
        secret.grant_read(task_definition.task_role)

        # CloudWatch Log Group for ECS container logs
        log_group = logs.LogGroup(
            self, "VoicebotLogGroup",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.ONE_MONTH
        )

        # Scoped CloudWatch Logs permissions for the task role
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                resources=[
                    log_group.log_group_arn,
                    f"{log_group.log_group_arn}:*"
                ]
            )
        )
        
        container = task_definition.add_container(
            "VoicebotContainer",
            image=ecs.ContainerImage.from_asset("../backend", platform=Platform.LINUX_AMD64),
            memory_limit_mib=8192,
            cpu=2048,
            port_mappings=[
                ecs.PortMapping(container_port=8080)
            ],
            environment={
                "USER_POOL_ID": user_pool.user_pool_id,
                "APP_CLIENT_ID": user_pool_client.user_pool_client_id,
                "KNOWLEDGE_BASE_ID": knowledgebase,
                "AWS_DEFAULT_REGION": region,
                "LLM_MODEL": llm_model,
                "SMALLEST_API_KEY": secret.secret_arn
            },
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="voicebot",
                log_group=log_group
            )
        )

        # Look up the AWS-managed CloudFront origin-facing prefix list
        cloudfront_prefix_list = ec2.PrefixList.from_lookup(
            self, "CloudFrontPrefixList",
            prefix_list_name="com.amazonaws.global.cloudfront.origin-facing"
        )

        service_sg = ec2.SecurityGroup(
            self, "VoicebotServiceSG",
            vpc=vpc,
            description="Security group for Voicebot Fargate service",
            allow_all_outbound=True
        )

        service = ecs.FargateService(
            self, "VoicebotService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=False,
            security_groups=[service_sg],
            vpc_subnets=private_subnet_selection,
            min_healthy_percent=100,
            max_healthy_percent=200
        )

        nlb_sg = ec2.SecurityGroup(
            self, "VoicebotNLBSG",
            vpc=vpc,
            description="Security group for Voicebot NLB",
            allow_all_outbound=True
        )

        nlb_sg.add_ingress_rule(
            peer=ec2.Peer.prefix_list(cloudfront_prefix_list.prefix_list_id),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP/WebSocket traffic only from CloudFront"
        )

        # ECS Service SG - allow traffic only from NLB
        service_sg.add_ingress_rule(
            peer=nlb_sg,
            connection=ec2.Port.tcp(8080),
            description="Allow traffic only from NLB"
        )

        nlb = elbv2.NetworkLoadBalancer(
            self, "VoicebotNLB",
            vpc=vpc,
            internet_facing=True,
            security_groups=[nlb_sg],
            vpc_subnets=public_subnet_selection
        )

        listener_http = nlb.add_listener(
            "VoicebotListenerHttp",
            port=80,
            protocol=elbv2.Protocol.TCP
        )
        
        listener_http.add_targets(
            "VoicebotTargetHttp",
            port=8080,
            protocol=elbv2.Protocol.TCP,
            targets=[service.load_balancer_target(
                container_name="VoicebotContainer",
                container_port=8080
            )],
            health_check=elbv2.HealthCheck(
                enabled=True,
                port="8080",
                protocol=elbv2.Protocol.HTTP,
                path="/health",
            )
        )

        website_bucket = s3.Bucket(
            self, "VoicebotFrontendBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            access_control=s3.BucketAccessControl.PRIVATE,
            enforce_ssl=True
        )

        nlb_origin = origins.HttpOrigin(
            nlb.load_balancer_dns_name,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY
        )

        # Create custom origin request policy for WebSocket headers
        websocket_origin_request_policy = cloudfront.OriginRequestPolicy(
            self, "WebSocketOriginRequestPolicy",
            origin_request_policy_name=f"WebSocketPolicy-{Stack.of(self).stack_name}",
            comment="Origin request policy for WebSocket connections",
            header_behavior=cloudfront.OriginRequestHeaderBehavior.allow_list(
                "Sec-WebSocket-Key",
                "Sec-WebSocket-Version",
                "Sec-WebSocket-Protocol",
                "Sec-WebSocket-Accept"
            ),
            query_string_behavior=cloudfront.OriginRequestQueryStringBehavior.all(),
            cookie_behavior=cloudfront.OriginRequestCookieBehavior.all()
        )

        distribution = cloudfront.Distribution(
            self, "VoicebotDistribution",
            comment="Voicebot Frontend Distribution",
            geo_restriction=cloudfront.GeoRestriction.allowlist("IN"),
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(website_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_OPTIMIZED
            ),
            additional_behaviors={
                "/ws": cloudfront.BehaviorOptions(
                    origin=nlb_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    origin_request_policy=websocket_origin_request_policy
                )
            },
            default_root_object='index.html',
            minimum_protocol_version=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path='/index.html'
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path='/index.html'
                )
            ]
        )

        aws_exports_content = f"""
{{
    "amplify": {{
        "Auth": {{
            "Cognito": {{
                "userPoolClientId": "{user_pool_client.user_pool_client_id}",
                "userPoolId": "{user_pool.user_pool_id}",
                "identityPoolId": "{identity_pool.ref}",
                "region": "{Stack.of(self).region}"

            }}
        }}
    }},
    "websocket": {{
        "apiUrl": "wss://{distribution.distribution_domain_name}/ws"
    }}
}}
        """

        frontend_deployment = s3_deployment.BucketDeployment(
            self, "VoicebotFrontendDeployment",

            sources=[
                s3_deployment.Source.asset("../frontend/build"),
                s3_deployment.Source.data('aws-exports.json', aws_exports_content)
            ],
            destination_bucket=website_bucket,
            distribution=distribution,
            distribution_paths=["/*"]
        )
  
        CfnOutput(self, "CloudFrontURL", value=f"https://{distribution.distribution_domain_name}")

        # ─── CDK Nag Suppressions ────────────────────────────────────────

        cdk_nag.NagSuppressions.add_resource_suppressions(
            vpc,
            [
                {
                    'id': 'AwsSolutions-VPC7',
                    'reason': 'VPC Flow Logs not required for sample/demo code.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotUserPool/Resource',
            [
                {
                    'id': 'AwsSolutions-COG2',
                    'reason': 'MFA not required for sample/demo code. Users are admin-created only.'
                },
                {
                    'id': 'AwsSolutions-COG3',
                    'reason': 'Advanced security mode not required for sample code.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotCluster/Resource',
            [
                {
                    'id': 'AwsSolutions-ECS4',
                    'reason': 'Container insights not required for sample code.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotTaskDef/Resource',
            [
                {
                    'id': 'AwsSolutions-ECS2',
                    'reason': 'Only safe environment variables are listed; no secrets in plain text.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotTaskDef/TaskRole/DefaultPolicy/Resource',
            [
                {
                    'id': 'AwsSolutions-IAM5',
                    'reason': 'Transcribe and Polly do not support resource-level permissions (resources:* required). '
                              'Bedrock foundation-model/* wildcards needed for model flexibility within scoped regions. '
                              'CloudWatch Logs scoped to specific log group. Secrets Manager grant generated by CDK.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotTaskDef/ExecutionRole/DefaultPolicy/Resource',
            [
                {
                    'id': 'AwsSolutions-IAM5',
                    'reason': 'Wildcard postfixes generated by CDK for ECR image pull and log operations.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotNLB/Resource',
            [
                {
                    'id': 'AwsSolutions-ELB2',
                    'reason': 'Access logging not required for sample code.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotNLBSG/Resource',
            [
                {
                    'id': 'AwsSolutions-EC23',
                    'reason': 'NLB ingress restricted to CloudFront origin-facing IPs via AWS-managed prefix list.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotFrontendBucket/Resource',
            [
                {
                    'id': 'AwsSolutions-S1',
                    'reason': 'S3 access logging not required for sample code.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/VoicebotDistribution/Resource',
            [
                {
                    'id': 'AwsSolutions-CFR3',
                    'reason': 'CloudFront access logging not required for sample code.'
                },
                {
                    'id': 'AwsSolutions-CFR4',
                    'reason': 'Default CloudFront viewer certificate is sufficient for sample code. Already uses TLS 1.2 minimum.'
                },
                {
                    'id': 'AwsSolutions-CFR5',
                    'reason': 'HTTP_ONLY to origin is acceptable for prototypes as NLB is within AWS network. CloudFront terminates TLS at edge for viewer connections.'
                },
                {
                    'id': 'AwsSolutions-CFR2',
                    'reason': 'AWS WAF integration not required for sample/demo code.'
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C/ServiceRole/Resource',
            [
                {
                    'id': 'AwsSolutions-IAM4',
                    'reason': 'AWS managed policy used by CDK BucketDeployment custom resource.',
                    'appliesTo': ['Policy::arn:<AWS::Partition>:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole']
                }
            ]
        )

        cdk_nag.NagSuppressions.add_resource_suppressions_by_path(self,
            f'/{self.stack_name}/Custom::CDKBucketDeployment8693BB64968944B69AAFB0CC9EB8756C/ServiceRole/DefaultPolicy/Resource',
            [
                {
                    'id': 'AwsSolutions-IAM5',
                    'reason': 'Wildcard permissions required by CDK BucketDeployment for S3 operations.'
                }
            ]
        )

