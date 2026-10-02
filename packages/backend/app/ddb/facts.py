from typing import Any

from boto3.dynamodb.conditions import Key

from app.ddb.client import get_table
from app.ddb.models import DdbKey
from app.ddb.workflows import _decimal_to_python

FACTS_SK_PREFIX = "FACTS#"


def make_facts_key(project_id: str, document_id: str) -> DdbKey:
    """Key of the per-document facts item (written by the document-facts Lambda)."""
    return {"PK": f"PROJ#{project_id}", "SK": f"{FACTS_SK_PREFIX}{document_id}"}


def delete_facts_item(project_id: str, document_id: str) -> None:
    table = get_table()
    table.delete_item(Key=make_facts_key(project_id, document_id))


def query_facts(project_id: str) -> dict[str, dict[str, Any]]:
    """Facts records (item `data`) of every document in a project, by document id.

    Base-table Query on PK with SK begins_with FACTS#, paginated. DynamoDB
    Decimals come back as int / float.
    """
    table = get_table()
    kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("PK").eq(f"PROJ#{project_id}") & Key("SK").begins_with(FACTS_SK_PREFIX)
    }
    facts: dict[str, dict[str, Any]] = {}
    while True:
        response = table.query(**kwargs)
        for item in response.get("Items", []):
            data = item.get("data")
            if isinstance(data, dict):
                facts[str(item["SK"])[len(FACTS_SK_PREFIX) :]] = _decimal_to_python(data)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return facts
        kwargs["ExclusiveStartKey"] = last_key
