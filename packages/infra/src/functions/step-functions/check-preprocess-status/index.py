"""Check Analysis Throttle Lambda

Called by Step Functions after ParallelPreprocessing completes.
Checks if another workflow's segment analysis is currently running
to prevent concurrent analysis overload.
"""
import json

from shared.ddb_client import is_analysis_busy


def handler(event, context):
    print(f'Event: {json.dumps(event)}')

    workflow_id = event.get('workflow_id')

    if not workflow_id:
        return {
            **event,
            'preprocess_check': {
                'all_completed': True,
                'any_failed': False,
                'analysis_busy': False,
            }
        }

    analysis_busy = is_analysis_busy(workflow_id)
    print(f'Analysis busy check for {workflow_id}: {analysis_busy}')

    return {
        **event,
        'preprocess_check': {
            'all_completed': True,
            'any_failed': False,
            'analysis_busy': analysis_busy,
        }
    }
