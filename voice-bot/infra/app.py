#!/usr/bin/env python3

# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

import os
from aws_cdk import App, Environment, Aspects
from cdk_nag import AwsSolutionsChecks

from infra_stack import InfraStack

app = App()
Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

env = Environment(
    account=os.getenv('CDK_DEFAULT_ACCOUNT'),
    region=os.getenv('CDK_DEFAULT_REGION')
)
InfraStack(app, "VoicebotStack", 
    description="Voicebot with speech to speech as well as STT-LLM-TTS pipelines (uksb-qon0ui1aa6).",
    env=env, termination_protection=True)

app.synth()
