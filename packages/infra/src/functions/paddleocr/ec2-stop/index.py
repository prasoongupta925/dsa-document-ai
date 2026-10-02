"""PaddleOCR EC2 Stop Lambda

Stops the PaddleOCR EC2 instance when triggered by CloudWatch Alarm via SNS.
"""
import json
import os

import boto3


ec2 = boto3.client('ec2')
INSTANCE_ID = os.environ['INSTANCE_ID']


def handler(event, _context):
    print(f'Event: {json.dumps(event)}')

    # Check current state
    response = ec2.describe_instances(InstanceIds=[INSTANCE_ID])
    state = response['Reservations'][0]['Instances'][0]['State']['Name']

    if state == 'running':
        ec2.stop_instances(InstanceIds=[INSTANCE_ID])
        print(f'Stopping instance {INSTANCE_ID}')
        return {'status': 'stopping', 'instance_id': INSTANCE_ID}

    print(f'Instance {INSTANCE_ID} is in state: {state}')
    return {'status': f'instance_state_{state}', 'instance_id': INSTANCE_ID}
