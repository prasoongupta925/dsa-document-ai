"""Graph Service Lambda (openCypher gateway to Neptune DB Serverless).

With no graph database (GRAPH_DISABLED=true or no NEPTUNE_ENDPOINT, as in the
lean build) every known action answers statusCode 200 with graph_disabled=True
and the empty result its callers read; no connection is ever opened.
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

NEPTUNE_ENDPOINT = os.environ.get('NEPTUNE_ENDPOINT', '')
NEPTUNE_PORT = os.environ.get('NEPTUNE_PORT', '8182')


def graph_disabled() -> bool:
    """True when there is no graph database: GRAPH_DISABLED is set, or no endpoint."""
    flag = os.environ.get('GRAPH_DISABLED', '').strip().lower()
    return flag in ('1', 'true', 'yes', 'on') or not NEPTUNE_ENDPOINT


_session = None


def get_session():
    global _session
    if _session is None:
        _session = boto3.Session(
            region_name=os.environ.get('AWS_REGION', 'us-east-1'),
        )
    return _session


def run_query(query: str, parameters: dict = None, _retries: int = 5) -> list:
    """Execute an openCypher query against Neptune DB Serverless via IAM-signed HTTPS."""
    if not NEPTUNE_ENDPOINT:
        raise RuntimeError('NEPTUNE_ENDPOINT environment variable is not set')

    session = get_session()
    credentials = session.get_credentials().get_frozen_credentials()
    region = session.region_name

    url = f'https://{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}/openCypher'
    body = {'query': query}
    if parameters:
        body['parameters'] = json.dumps(parameters)

    last_error = None
    for attempt in range(_retries):
        data = urllib.parse.urlencode(body).encode('utf-8')
        request = AWSRequest(
            method='POST',
            url=url,
            data=data,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Host': f'{NEPTUNE_ENDPOINT}:{NEPTUNE_PORT}',
            },
        )
        SigV4Auth(credentials, 'neptune-db', region).add_auth(request)

        req = urllib.request.Request(
            url,
            data=data,
            headers=dict(request.headers),
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
                if not raw or not raw.strip():
                    raise json.JSONDecodeError('Empty response from Neptune', '', 0)
                payload = json.loads(raw)
            return payload.get('results', [])
        except json.JSONDecodeError as e:
            last_error = e
            if attempt < _retries - 1:
                wait = (attempt + 1) * 1.5
                print(f'Neptune empty/malformed response, retrying in {wait}s (attempt {attempt + 1}/{_retries})')
                time.sleep(wait)
                continue
            raise
        except urllib.error.HTTPError as e:
            last_error = e
            if e.code in (500, 503, 429) and attempt < _retries - 1:
                wait = (attempt + 1) * 1.0
                print(f'Neptune {e.code}, retrying in {wait}s (attempt {attempt + 1}/{_retries})')
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, ConnectionResetError, OSError) as e:
            last_error = e
            if attempt < _retries - 1:
                wait = (attempt + 1) * 1.0
                print(f'Neptune connection error: {e}, retrying in {wait}s (attempt {attempt + 1}/{_retries})')
                time.sleep(wait)
                continue
            raise

    raise last_error


def run_queries_parallel(queries: list[tuple[str, dict | None]]) -> list[list]:
    """Execute multiple openCypher queries in parallel. Returns results in the same order."""
    results = [None] * len(queries)
    with ThreadPoolExecutor(max_workers=min(len(queries), 3)) as executor:
        future_to_idx = {
            executor.submit(run_query, q, p): i
            for i, (q, p) in enumerate(queries)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            results[idx] = future.result()
    return results


def entity_id(project_id: str, name: str) -> str:
    """Generate a deterministic entity ID from project_id + name."""
    key = f'{project_id}:{name.lower().strip()}'
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ========================================
# Write Actions
# ========================================

def action_add_segment_links(params: dict) -> dict:
    """Create Document + Segment nodes and BELONGS_TO, NEXT relationships.

    Supports chunked calls via start_index/end_index to avoid Lambda timeout.
    """
    project_id = params['project_id']
    workflow_id = params['workflow_id']
    document_id = params.get('document_id', '')
    file_name = params.get('file_name', '')
    file_type = params.get('file_type', '')
    segment_count = params.get('segment_count', 0)
    start_index = params.get('start_index', 0)
    end_index = params.get('end_index', segment_count)

    # Create Document node (idempotent MERGE, safe to call multiple times)
    run_query(
        'MERGE (d:Document {`~id`: $doc_id}) '
        'SET d.id = $doc_id, d.project_id = $pid, d.workflow_id = $wid, '
        'd.file_name = $fname, d.file_type = $ftype',
        {
            'doc_id': document_id,
            'pid': project_id,
            'wid': workflow_id,
            'fname': file_name,
            'ftype': file_type,
        },
    )

    # Create Segment nodes + BELONGS_TO edges for the given range
    segments = [
        {'sid': f'{workflow_id}_{i:04d}', 'idx': i}
        for i in range(start_index, end_index)
    ]
    batch_size = 50
    for start in range(0, len(segments), batch_size):
        batch = segments[start:start + batch_size]
        run_query(
            'UNWIND $segments AS seg '
            'MERGE (s:Segment {`~id`: seg.sid}) '
            'SET s.id = seg.sid, s.project_id = $pid, s.workflow_id = $wid, '
            's.document_id = $did, s.segment_index = seg.idx '
            'WITH s '
            'MATCH (d:Document {`~id`: $did}) '
            'MERGE (s)-[:BELONGS_TO]->(d)',
            {
                'segments': batch,
                'pid': project_id,
                'wid': workflow_id,
                'did': document_id,
            },
        )

    # Create NEXT relationships for the given range
    next_start = max(start_index, 1) if start_index == 0 else start_index
    pairs = [
        {'curr': f'{workflow_id}_{i - 1:04d}', 'next': f'{workflow_id}_{i:04d}'}
        for i in range(next_start, end_index)
    ]
    for start in range(0, len(pairs), batch_size):
        batch = pairs[start:start + batch_size]
        run_query(
            'UNWIND $pairs AS p '
            'MATCH (a:Segment {`~id`: p.curr}), (b:Segment {`~id`: p.next}) '
            'MERGE (a)-[:NEXT]->(b)',
            {'pairs': batch},
        )

    return {
        'success': True,
        'document_id': document_id,
        'segment_range': f'{start_index}-{end_index}',
    }


def action_add_analyses(params: dict) -> dict:
    """Create Analysis nodes and BELONGS_TO relationships to Segment nodes."""
    project_id = params['project_id']
    workflow_id = params['workflow_id']
    document_id = params.get('document_id', '')
    analyses = params.get('analyses', [])

    items = [
        {
            'aid': f'{workflow_id}_{item["segment_index"]:04d}_{item["qa_index"]:02d}',
            'sid': f'{workflow_id}_{item["segment_index"]:04d}',
            'idx': item['segment_index'],
            'qidx': item['qa_index'],
            'q': item.get('question', ''),
        }
        for item in analyses
    ]

    batch_size = 50
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        run_query(
            'UNWIND $items AS item '
            'MERGE (a:Analysis {`~id`: item.aid}) '
            'SET a.id = item.aid, a.project_id = $pid, a.workflow_id = $wid, '
            'a.document_id = $did, a.segment_index = item.idx, '
            'a.qa_index = item.qidx, a.question = item.q '
            'WITH a, item '
            'MATCH (s:Segment {`~id`: item.sid}) '
            'MERGE (a)-[:BELONGS_TO]->(s)',
            {
                'items': batch,
                'pid': project_id,
                'wid': workflow_id,
                'did': document_id,
            },
        )

    return {'success': True, 'created': len(items)}


def action_add_entities(params: dict) -> dict:
    """Add entity nodes in bulk with MENTIONED_IN relationships to Analysis nodes."""
    project_id = params['project_id']
    entities = params.get('entities', [])

    # Batch MERGE entity nodes
    entity_items = [
        {
            'eid': entity_id(project_id, ent['name']),
            'name': ent['name'],
            'etype': ent.get('entity_type', 'PRIMARY'),
            'anchor': ent.get('anchor', ''),
        }
        for ent in entities
    ]

    batch_size = 50
    for start in range(0, len(entity_items), batch_size):
        batch = entity_items[start:start + batch_size]
        run_query(
            'UNWIND $items AS item '
            'MERGE (e:Entity {`~id`: item.eid}) '
            'SET e.id = item.eid, e.project_id = $pid, e.name = item.name, '
            'e.entity_type = item.etype, e.anchor = item.anchor',
            {'items': batch, 'pid': project_id},
        )

    # Batch MERGE MENTIONED_IN relationships
    mention_items = []
    for ent in entities:
        eid = entity_id(project_id, ent['name'])
        etype = ent.get('entity_type', 'PRIMARY')
        anchor = ent.get('anchor', '')
        for mention in ent.get('mentioned_in', []):
            workflow_id = mention.get('workflow_id', '')
            segment_index = mention.get('segment_index', 0)
            qa_index = mention.get('qa_index', 0)
            mention_items.append({
                'eid': eid,
                'aid': f'{workflow_id}_{segment_index:04d}_{qa_index:02d}',
                'conf': mention.get('confidence', 1.0),
                'ctx': mention.get('context', ''),
                'etype': etype,
                'anchor': anchor,
            })

    for start in range(0, len(mention_items), batch_size):
        batch = mention_items[start:start + batch_size]
        run_query(
            'UNWIND $items AS item '
            'MATCH (e:Entity {`~id`: item.eid}), (a:Analysis {`~id`: item.aid}) '
            'MERGE (e)-[r:MENTIONED_IN]->(a) '
            'SET r.confidence = item.conf, r.context = item.ctx, '
            'r.entity_type = item.etype, r.anchor = item.anchor',
            {'items': batch},
        )

    return {'success': True, 'created': len(entity_items)}


def action_add_relationships(params: dict) -> dict:
    """Add RELATES_TO relationships between entities."""
    project_id = params['project_id']
    relationships = params.get('relationships', [])

    items = [
        {
            'src': entity_id(project_id, rel['source']),
            'tgt': entity_id(project_id, rel['target']),
            'rel': rel.get('relationship', 'RELATED'),
            'origin': rel.get('source_origin', 'auto'),
        }
        for rel in relationships
    ]

    batch_size = 50
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        run_query(
            'UNWIND $items AS item '
            'MATCH (a:Entity {`~id`: item.src}), (b:Entity {`~id`: item.tgt}) '
            'MERGE (a)-[r:RELATES_TO {relationship: item.rel}]->(b) '
            'SET r.source = item.origin',
            {'items': batch},
        )

    return {'success': True, 'created': len(items)}


def action_build_clusters(params: dict) -> dict:
    """Pre-compute Cluster nodes for a document. Disabled: entity types removed."""
    return {'success': True, 'clustered': False, 'entity_count': 0}


def action_link_documents(params: dict) -> dict:
    """Create bidirectional RELATED_TO relationships between two Document nodes."""
    doc_id_1 = params['document_id_1']
    doc_id_2 = params['document_id_2']
    reason = params.get('reason', '')
    label = params.get('label', '')

    run_query(
        'MATCH (d1:Document {`~id`: $d1}), '
        '      (d2:Document {`~id`: $d2}) '
        'MERGE (d1)-[r:RELATED_TO]->(d2) '
        'SET r.reason = $reason, r.label = $label, r.created_at = datetime()',
        {'d1': doc_id_1, 'd2': doc_id_2,
         'reason': reason, 'label': label},
    )
    run_query(
        'MATCH (d1:Document {`~id`: $d1}), '
        '      (d2:Document {`~id`: $d2}) '
        'MERGE (d2)-[r:RELATED_TO]->(d1) '
        'SET r.reason = $reason, r.label = $label, r.created_at = datetime()',
        {'d1': doc_id_1, 'd2': doc_id_2,
         'reason': reason, 'label': label},
    )
    return {'success': True}


def action_unlink_documents(params: dict) -> dict:
    """Delete bidirectional RELATED_TO relationships between two Document nodes."""
    doc_id_1 = params['document_id_1']
    doc_id_2 = params['document_id_2']

    run_query(
        'MATCH (d1:Document {`~id`: $d1})-[r:RELATED_TO]-(d2:Document {`~id`: $d2}) DELETE r',
        {'d1': doc_id_1, 'd2': doc_id_2},
    )
    return {'success': True}


def action_get_linked_documents(params: dict) -> dict:
    """Get documents linked via RELATED_TO relationships."""
    project_id = params['project_id']
    doc_id = params.get('document_id')

    if doc_id:
        result = run_query(
            'MATCH (d1:Document {`~id`: $did})-[r:RELATED_TO]->(d2:Document) '
            'RETURN d2.id AS id, d2.file_name AS file_name, '
            'r.reason AS reason, r.label AS label',
            {'did': doc_id},
        )
    else:
        result = run_query(
            'MATCH (d1:Document {project_id: $pid})-[r:RELATED_TO]->(d2:Document) '
            'WHERE d1.id < d2.id '
            'RETURN d1.id AS doc1, d1.file_name AS name1, '
            'd2.id AS doc2, d2.file_name AS name2, r.reason AS reason',
            {'pid': project_id},
        )
    return {'success': True, 'links': result}


def action_delete_analysis(params: dict) -> dict:
    """Delete a single Analysis node and its MENTIONED_IN edges, then clean up orphaned entities."""
    project_id = params['project_id']
    analysis_id = params['analysis_id']

    # Delete MENTIONED_IN edges pointing to this Analysis, then the Analysis node itself
    run_query(
        'MATCH (a:Analysis {`~id`: $aid}) DETACH DELETE a',
        {'aid': analysis_id},
    )

    # Clean up orphaned entities (no MENTIONED_IN connections)
    run_query(
        'MATCH (e:Entity {project_id: $pid}) '
        'WHERE NOT (e)-[:MENTIONED_IN]->() '
        'DELETE e',
        {'pid': project_id},
    )

    return {'success': True, 'analysis_id': analysis_id}


def action_delete_by_workflow(params: dict) -> dict:
    """Delete all graph data for a workflow."""
    project_id = params['project_id']
    workflow_id = params['workflow_id']

    # Delete Analysis nodes first
    run_query(
        'MATCH (a:Analysis {project_id: $pid, workflow_id: $wid}) DETACH DELETE a',
        {'pid': project_id, 'wid': workflow_id},
    )

    run_query(
        'MATCH (s:Segment {project_id: $pid, workflow_id: $wid}) DETACH DELETE s',
        {'pid': project_id, 'wid': workflow_id},
    )

    run_query(
        'MATCH (d:Document {project_id: $pid, workflow_id: $wid}) DETACH DELETE d',
        {'pid': project_id, 'wid': workflow_id},
    )

    run_query(
        'MATCH (e:Entity {project_id: $pid}) '
        'WHERE NOT (e)-[:MENTIONED_IN]->() '
        'DELETE e',
        {'pid': project_id},
    )

    return {'success': True}


def action_clear_all(params: dict) -> dict:
    """Delete ALL nodes and relationships in the graph database."""
    batch_size = params.get('batch_size', 500)
    deleted_edges = 0
    deleted_nodes = 0

    # 1. Delete all relationships first (much faster than DETACH DELETE)
    for rel_type in ['MENTIONED_IN', 'RELATES_TO', 'BELONGS_TO', 'NEXT', 'RELATED_TO', 'HAS_CLUSTER']:
        while True:
            result = run_query(
                f'MATCH ()-[r:{rel_type}]->() WITH r LIMIT $batch DELETE r RETURN count(*) AS cnt',
                {'batch': batch_size},
                _retries=5,
            )
            cnt = result[0]['cnt'] if result else 0
            deleted_edges += cnt
            print(f'Deleted {cnt} {rel_type} edges (total edges: {deleted_edges})')
            if cnt < batch_size:
                break

    # 2. Delete remaining unknown relationship types
    while True:
        result = run_query(
            'MATCH ()-[r]->() WITH r LIMIT $batch DELETE r RETURN count(*) AS cnt',
            {'batch': batch_size},
            _retries=5,
        )
        cnt = result[0]['cnt'] if result else 0
        deleted_edges += cnt
        if cnt < batch_size:
            break

    # 3. Delete all nodes (no relationships left, so plain DELETE)
    for label in ['Cluster', 'Analysis', 'Segment', 'Document', 'Entity']:
        while True:
            result = run_query(
                f'MATCH (n:{label}) WITH n LIMIT $batch DELETE n RETURN count(*) AS cnt',
                {'batch': batch_size},
                _retries=5,
            )
            cnt = result[0]['cnt'] if result else 0
            deleted_nodes += cnt
            print(f'Deleted {cnt} {label} nodes (total nodes: {deleted_nodes})')
            if cnt < batch_size:
                break

    # 4. Catch any remaining nodes
    while True:
        result = run_query(
            'MATCH (n) WITH n LIMIT $batch DELETE n RETURN count(*) AS cnt',
            {'batch': batch_size},
            _retries=5,
        )
        cnt = result[0]['cnt'] if result else 0
        deleted_nodes += cnt
        if cnt < batch_size:
            break

    return {'success': True, 'deleted_edges': deleted_edges, 'deleted_nodes': deleted_nodes}


# ========================================
# Read Actions
# ========================================

def action_traverse(params: dict) -> dict:
    """N-hop graph traversal from a starting node."""
    start_id = params['start_id']
    depth = params.get('depth', 2)
    limit = params.get('limit', 50)

    results = run_query(
        f'MATCH path = (start {{`~id`: $sid}})-[*1..{depth}]-(connected) '
        'RETURN DISTINCT connected.id AS id, labels(connected) AS labels, '
        'properties(connected) AS props '
        f'LIMIT {int(limit)}',
        {'sid': start_id},
    )

    return {'success': True, 'nodes': results}


def action_find_related_segments(params: dict) -> dict:
    """Find Analysis nodes related to given entities via MENTIONED_IN, return segment info."""
    entity_ids = params.get('entity_ids', [])
    limit = params.get('limit', 20)

    if not entity_ids:
        return {'success': True, 'segments': []}

    seen = set()
    all_segments = []

    for eid in entity_ids:
        results = run_query(
            'MATCH (e:Entity {`~id`: $eid}) '
            'OPTIONAL MATCH (e)-[:MENTIONED_IN]->(a:Analysis)-[:BELONGS_TO]->(s:Segment) '
            'WITH collect(DISTINCT s) AS allSegs '
            'UNWIND allSegs AS seg '
            'WITH seg WHERE seg IS NOT NULL '
            'RETURN DISTINCT seg.id AS id, seg.workflow_id AS workflow_id, '
            'seg.document_id AS document_id, seg.segment_index AS segment_index '
            f'LIMIT {int(limit)}',
            {'eid': eid},
        )
        for r in results:
            if r.get('id') and r['id'] not in seen:
                seen.add(r['id'])
                all_segments.append(r)

    return {'success': True, 'segments': all_segments[:limit]}


def action_search_graph(params: dict) -> dict:
    """Graph traversal from QA IDs to discover related pages.

    Accepts qa_ids (from LanceDB search results) as starting points,
    then traverses: Analysis <-MENTIONED_IN- Entity
    -MENTIONED_IN-> Analysis -> Segment to find related pages.
    Falls back to entity name matching when qa_ids are not provided.
    """
    project_id = params['project_id']
    document_id = params.get('document_id')
    segment_limit = params.get('segment_limit', 30)
    # QA IDs from LanceDB results (format: wf_xxx_0001_00)
    qa_ids = params.get('qa_ids', [])

    # 1. Find all entities connected to the provided Analysis IDs
    entity_results = []

    if qa_ids:
        entity_results = run_query(
            'UNWIND $qids AS qid '
            'MATCH (e:Entity)-[:MENTIONED_IN]->(a:Analysis {`~id`: qid}) '
            'WHERE e.project_id = $pid '
            'RETURN DISTINCT e.id AS id, e.name AS name',
            {'qids': qa_ids, 'pid': project_id},
        )
    if not entity_results:
        return {'success': True, 'entities': [], 'segments': []}

    # 2. From all matched entities, find related segments in a single query
    # Convert qa_ids (wf_xxx_0001_00) to segment_ids (wf_xxx_0001) for dedup
    source_seg_ids = []
    for qid in qa_ids:
        parts = qid.rsplit('_', 1)
        if len(parts) == 2:
            source_seg_ids.append(parts[0])

    # Collect document_ids from qa_ids for same-document filtering
    source_doc_ids = set()
    if qa_ids:
        doc_results = run_query(
            'UNWIND $qids AS qid '
            'MATCH (a:Analysis {`~id`: qid})-[:BELONGS_TO]->(s:Segment) '
            'RETURN DISTINCT s.document_id AS document_id',
            {'qids': qa_ids},
        )
        source_doc_ids = {r['document_id'] for r in doc_results if r.get('document_id')}
    if document_id:
        source_doc_ids.add(document_id)

    # Single query: UNWIND all entity IDs, find connected segments, exclude source segments
    entity_ids = [e['id'] for e in entity_results]
    query_params = {
        'eids': entity_ids,
        'exclude_sids': source_seg_ids,
        'pid': project_id,
    }
    doc_filter = ''
    if source_doc_ids:
        doc_filter = 'AND seg.document_id IN $doc_ids '
        query_params['doc_ids'] = list(source_doc_ids)

    traversal_results = run_query(
        'UNWIND $eids AS eid '
        'MATCH (e:Entity {`~id`: eid})-[:MENTIONED_IN]->(a:Analysis)-[:BELONGS_TO]->(seg:Segment) '
        f'WHERE seg.project_id = $pid AND NOT seg.id IN $exclude_sids {doc_filter}'
        'RETURN DISTINCT a.`~id` AS analysis_id, a.qa_index AS qa_index, '
        'seg.id AS id, seg.workflow_id AS workflow_id, '
        'seg.document_id AS document_id, seg.segment_index AS segment_index '
        f'LIMIT {int(segment_limit)}',
        query_params,
    )

    # 3. Build result segments
    all_segments = []
    for s in traversal_results:
        seg = {
            'id': s['id'],
            'workflow_id': s['workflow_id'],
            'document_id': s['document_id'],
            'segment_index': s['segment_index'],
            'qa_id': s.get('analysis_id'),
            'qa_index': s.get('qa_index', 0),
            'match_type': 'traversal',
        }
        if not document_id or seg['document_id'] == document_id:
            all_segments.append(seg)

    return {
        'success': True,
        'entities': entity_results,
        'segments': all_segments,
    }


def action_get_entity_graph(params: dict) -> dict:
    """Get project-level document relationship graph.

    Shows documents as nodes and shared entities as edges between documents.
    """
    project_id = params['project_id']
    search_term = params.get('search', '')
    tagcloud_limit = int(params.get('tagcloud_limit', 200))

    # Query 1: Get documents
    doc_results = run_query(
        'MATCH (d:Document {project_id: $pid}) '
        'RETURN d.`~id` AS did, d.file_name AS file_name, '
        'd.file_type AS file_type',
        {'pid': project_id}, _retries=5,
    )

    if not doc_results:
        return {'success': True, 'nodes': [], 'edges': [], 'tagcloud': [],
                'total_entities': 0}

    # Query 2: Find shared entities between document pairs
    pair_results = run_query(
        'MATCH (d1:Document {project_id: $pid})<-[:BELONGS_TO]-(:Segment)'
        '<-[:BELONGS_TO]-(:Analysis)<-[:MENTIONED_IN]-(e:Entity)'
        '-[:MENTIONED_IN]->(:Analysis)-[:BELONGS_TO]->(:Segment)'
        '-[:BELONGS_TO]->(d2:Document {project_id: $pid}) '
        'WHERE d1.`~id` < d2.`~id` '
        'RETURN d1.`~id` AS d1_id, d2.`~id` AS d2_id, '
        'count(DISTINCT e) AS shared_count, '
        'collect(DISTINCT e.name) AS shared_names',
        {'pid': project_id}, _retries=5,
    )

    # Query 3: Get all entities for tagcloud
    entity_results = run_query(
        'MATCH (d:Document {project_id: $pid})<-[:BELONGS_TO]-(:Segment)'
        '<-[:BELONGS_TO]-(:Analysis)<-[:MENTIONED_IN]-(e:Entity) '
        'WITH e, count(DISTINCT d) AS doc_count '
        'RETURN e.`~id` AS eid, e.name AS name, '
        'doc_count ORDER BY doc_count DESC LIMIT $tlimit',
        {'pid': project_id, 'tlimit': tagcloud_limit}, _retries=5,
    )

    total_entities = len(entity_results) if entity_results else 0

    # Build tagcloud
    tagcloud = []
    for r in (entity_results or []):
        tagcloud.append({
            'id': r['eid'],
            'name': r['name'],
            'connections': r['doc_count'],
        })

    # Build document nodes
    nodes = []
    for d in doc_results:
        nodes.append({
            'id': d['did'],
            'name': d['file_name'] or d['did'],
            'label': 'document',
            'properties': {'file_type': d.get('file_type', '')},
        })

    # Build edges between documents
    edges = []
    max_entities_per_edge = 30
    for p in (pair_results or []):
        shared_entities = []
        names = p.get('shared_names', [])[:max_entities_per_edge]
        for name in names:
            shared_entities.append({'name': name})

        # Filter by search term if provided
        if search_term:
            search_lower = search_term.lower()
            matched = [e for e in shared_entities if search_lower in e['name'].lower()]
            if not matched:
                continue
            shared_entities = matched

        edges.append({
            'source': p['d1_id'],
            'target': p['d2_id'],
            'label': str(p['shared_count']),
            'properties': {
                'shared_count': p['shared_count'],
                'shared_entities': shared_entities,
            },
        })

    # If search term, filter out isolated documents (no matching edges)
    if search_term:
        connected_docs = set()
        for e in edges:
            connected_docs.add(e['source'])
            connected_docs.add(e['target'])
        nodes = [n for n in nodes if n['id'] in connected_docs]

    return {'success': True, 'nodes': nodes, 'edges': edges, 'tagcloud': tagcloud,
            'total_entities': total_entities}


CLUSTER_THRESHOLD = 500
LARGE_DOC_SEGMENT_THRESHOLD = 500


def _build_document_graph_full(_project_id, document_id, params_both,
                               seg_results, analysis_results, next_results):
    """Build full document graph with individual entities."""
    ent_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[:MENTIONED_IN]-(e:Entity) '
        'RETURN DISTINCT e.id AS id, e.name AS name',
        params_both, _retries=5,
    )
    mention_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[r:MENTIONED_IN]-(e:Entity) '
        'RETURN e.id AS source, a.id AS target, '
        'r.confidence AS confidence, r.context AS context',
        params_both, _retries=5,
    )

    nodes, edges = _build_structure_nodes_edges(
        document_id, seg_results, analysis_results, next_results
    )

    for e in ent_results:
        nodes.append({
            'id': e['id'],
            'name': e['name'],
            'label': 'entity',
            'properties': {},
        })

    for m in mention_results:
        edges.append({
            'source': m['source'],
            'target': m['target'],
            'label': 'MENTIONED_IN',
            'properties': {
                'confidence': m.get('confidence', 1.0),
                'context': m.get('context', ''),
            },
        })

    return nodes, edges


def _build_document_graph_clustered(_project_id, document_id, params_both,
                                    seg_results, analysis_results, next_results):
    """Build clustered document graph. Clustering disabled: falls back to full graph."""
    return _build_document_graph_full(
        _project_id, document_id, params_both,
        seg_results, analysis_results, next_results,
    )


def _build_structure_nodes_edges(document_id, seg_results, analysis_results, next_results):
    """Build structural nodes (document, segments, analyses) and edges."""
    nodes = []

    doc_file_name = document_id
    if seg_results:
        doc_file_name = seg_results[0].get('doc_file_name') or document_id

    nodes.append({
        'id': document_id,
        'name': doc_file_name,
        'label': 'document',
        'properties': {},
    })

    for s in seg_results:
        nodes.append({
            'id': s['id'],
            'name': f"Page {s['segment_index']}",
            'label': 'segment',
            'properties': {
                'segment_index': s['segment_index'],
                'workflow_id': s['workflow_id'],
            },
        })

    q_counters = {}
    for a in analysis_results:
        qa_index = a.get('qa_index', 0)
        question = a.get('question', '')
        is_base = qa_index == 0 and (not question or 'Analysis' in question)
        seg_idx = a['segment_index']
        if is_base:
            name = 'Analysis'
        else:
            q_counters[seg_idx] = q_counters.get(seg_idx, 0) + 1
            name = f"Extra {q_counters[seg_idx]}"
        nodes.append({
            'id': a['id'],
            'name': name,
            'label': 'analysis',
            'properties': {
                'segment_index': seg_idx,
                'qa_index': qa_index,
                'question': question,
                'is_base': is_base,
            },
        })

    edges = []

    for n in next_results:
        edges.append({
            'source': n['source'],
            'target': n['target'],
            'label': 'NEXT',
            'properties': None,
        })

    for a in analysis_results:
        edges.append({
            'source': a['id'],
            'target': a['segment_id'],
            'label': 'BELONGS_TO',
            'properties': None,
        })

    for s in seg_results:
        edges.append({
            'source': s['id'],
            'target': document_id,
            'label': 'BELONGS_TO',
            'properties': None,
        })

    return nodes, edges


def _build_paged_graph(document_id, params_q, seg_filter):
    """Build graph for a filtered set of segments (by page range, specific page, or search)."""
    seg_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment) '
        f'{seg_filter} '
        'RETURN s.id AS id, s.segment_index AS segment_index, '
        's.workflow_id AS workflow_id, d.file_name AS doc_file_name '
        'ORDER BY s.segment_index',
        params_q, _retries=5,
    )
    analysis_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)'
        '<-[:BELONGS_TO]-(a:Analysis) '
        f'{seg_filter} '
        'RETURN a.id AS id, a.segment_index AS segment_index, '
        'a.qa_index AS qa_index, a.question AS question, s.id AS segment_id',
        params_q, _retries=5,
    )
    next_seg_ids = {s['id'] for s in seg_results}
    next_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(a:Segment)-[:NEXT]->(b:Segment)-[:BELONGS_TO]->(d) '
        f'{seg_filter.replace("s.", "a.")} '
        'RETURN a.id AS source, b.id AS target',
        params_q, _retries=5,
    )
    # Keep only NEXT edges where both ends are in the filtered set
    next_results = [n for n in next_results if n['source'] in next_seg_ids and n['target'] in next_seg_ids]

    ent_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[:MENTIONED_IN]-(e:Entity) '
        f'{seg_filter} '
        'RETURN DISTINCT e.`~id` AS id, e.name AS name',
        params_q, _retries=5,
    )
    mention_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[r:MENTIONED_IN]-(e:Entity) '
        f'{seg_filter} '
        'RETURN e.`~id` AS source, a.id AS target, '
        'r.confidence AS confidence, r.context AS context',
        params_q, _retries=5,
    )

    nodes, edges = _build_structure_nodes_edges(
        document_id, seg_results, analysis_results, next_results
    )

    for e in ent_results:
        nodes.append({
            'id': e['id'],
            'name': e['name'],
            'label': 'entity',
            'properties': {},
        })
    for m in mention_results:
        edges.append({
            'source': m['source'],
            'target': m['target'],
            'label': 'MENTIONED_IN',
            'properties': {
                'confidence': m.get('confidence', 1.0),
                'context': m.get('context', ''),
            },
        })

    return nodes, edges


def _build_search_graph(document_id, project_id, search_term):
    """Build graph from search: find entities → their segments → those segments' entities."""
    p = {'did': document_id, 'pid': project_id, 'term': search_term}

    # Find entities matching the search term
    matched = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[:MENTIONED_IN]-(e:Entity) '
        'WHERE toLower(e.name) = toLower($term) '
        'RETURN DISTINCT e.`~id` AS id, e.name AS name',
        p, _retries=5,
    )
    if not matched:
        return [], [], []

    matched_ids = [e['id'] for e in matched]

    # Find segments that mention matched entities
    seg_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[:MENTIONED_IN]-(e:Entity) '
        'WHERE e.`~id` IN $eids '
        'RETURN DISTINCT s.id AS id, s.segment_index AS segment_index, '
        's.workflow_id AS workflow_id, d.file_name AS doc_file_name',
        {'did': document_id, 'pid': project_id, 'eids': matched_ids}, _retries=5,
    )

    seg_ids = [s['id'] for s in seg_results]
    if not seg_ids:
        return [], [], []

    # Get analyses for those segments
    analysis_results = run_query(
        'MATCH (s:Segment)<-[:BELONGS_TO]-(a:Analysis) '
        'WHERE s.id IN $sids '
        'RETURN a.id AS id, a.segment_index AS segment_index, '
        'a.qa_index AS qa_index, a.question AS question, s.id AS segment_id',
        {'sids': seg_ids}, _retries=5,
    )

    # Only return the matched entities (not all entities on found segments)
    ent_results = [{'id': e['id'], 'name': e['name']} for e in matched]

    # Mentions: only edges from matched entities
    mention_results = run_query(
        'MATCH (s:Segment)<-[:BELONGS_TO]-(a:Analysis)<-[r:MENTIONED_IN]-(e:Entity) '
        'WHERE s.id IN $sids AND e.`~id` IN $eids '
        'RETURN e.`~id` AS source, a.id AS target, '
        'r.confidence AS confidence, r.context AS context',
        {'sids': seg_ids, 'eids': matched_ids}, _retries=5,
    )

    # NEXT edges between found segments
    next_results = run_query(
        'MATCH (a:Segment)-[:NEXT]->(b:Segment) '
        'WHERE a.id IN $sids AND b.id IN $sids '
        'RETURN a.id AS source, b.id AS target',
        {'sids': seg_ids},
    )

    nodes, edges = _build_structure_nodes_edges(
        document_id, seg_results, analysis_results, next_results
    )

    for e in ent_results:
        nodes.append({
            'id': e['id'],
            'name': e['name'],
            'label': 'entity',
            'properties': {
                'matched': e['id'] in matched_ids,
            },
        })
    for m in mention_results:
        edges.append({
            'source': m['source'],
            'target': m['target'],
            'label': 'MENTIONED_IN',
            'properties': {
                'confidence': m.get('confidence', 1.0),
                'context': m.get('context', ''),
            },
        })
    return nodes, edges


def action_get_document_graph(params: dict) -> dict:
    """Get document-level graph with pagination support.

    Modes:
    - from_page + to_page: page range (e.g., 0-49)
    - page: specific page and its connected pages
    - search: keyword search for entities
    - (none): legacy full/clustered mode
    """
    project_id = params['project_id']
    document_id = params['document_id']
    from_page = params.get('from_page')
    to_page = params.get('to_page')
    page = params.get('page')
    search = params.get('search')

    # Get total segment count for metadata
    params_both = {'did': document_id, 'pid': project_id}
    total_result = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment) '
        'RETURN count(s) AS total, d.file_name AS file_name',
        params_both,
    )
    total_segments = total_result[0]['total'] if total_result else 0

    # Mode: Search
    if search:
        print(f'Document graph: search mode, term="{search}"')
        nodes, edges = _build_search_graph(document_id, project_id, search)
        return {
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'clustered': False,
            'total_segments': total_segments,
            'mode': 'search',
        }

    # Mode: Specific page
    if page is not None:
        page = int(page)
        print(f'Document graph: page mode, page={page}')
        params_q = {'did': document_id, 'pid': project_id, 'page': page}
        seg_filter = 'WHERE s.segment_index = $page'

        nodes, edges = _build_paged_graph(document_id, params_q, seg_filter)
        return {
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'clustered': False,
            'total_segments': total_segments,
            'mode': 'page',
            'focus_page': page,
        }

    # Mode: Page range
    if from_page is not None and to_page is not None:
        from_page = int(from_page)
        to_page = int(to_page)
        print(f'Document graph: range mode, pages {from_page}-{to_page}')
        seg_filter = 'WHERE s.segment_index >= $fp AND s.segment_index < $tp'
        params_q = {'did': document_id, 'pid': project_id, 'fp': from_page, 'tp': to_page}
        nodes, edges = _build_paged_graph(document_id, params_q, seg_filter)
        return {
            'success': True,
            'nodes': nodes,
            'edges': edges,
            'clustered': False,
            'total_segments': total_segments,
            'mode': 'range',
            'from_page': from_page,
            'to_page': to_page,
        }

    # Legacy: full/clustered mode (no pagination)
    seg_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment) '
        'RETURN s.id AS id, s.segment_index AS segment_index, '
        's.workflow_id AS workflow_id, d.file_name AS doc_file_name '
        'ORDER BY s.segment_index',
        params_both, _retries=5,
    )
    analysis_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)'
        '<-[:BELONGS_TO]-(a:Analysis) '
        'RETURN a.id AS id, a.segment_index AS segment_index, '
        'a.qa_index AS qa_index, a.question AS question, s.id AS segment_id',
        params_both, _retries=5,
    )
    next_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(a:Segment)-[:NEXT]->(b:Segment)-[:BELONGS_TO]->(d) '
        'RETURN a.id AS source, b.id AS target',
        {'did': document_id}, _retries=5,
    )
    # Check for pre-computed Cluster nodes first (fast O(1) lookup)
    cluster_check = run_query(
        'MATCH (d:Document {`~id`: $did})-[:HAS_CLUSTER]->(c:Cluster) '
        'RETURN count(c) AS cnt LIMIT 1',
        params_both,
    )
    has_clusters = (cluster_check[0]['cnt'] if cluster_check else 0) > 0

    if has_clusters:
        clustered = True
        print('Document graph: using pre-computed clusters')
        nodes, edges = _build_document_graph_clustered(
            project_id, document_id, params_both,
            seg_results, analysis_results, next_results,
        )
    else:
        count_results = run_query(
            'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)'
            '<-[:BELONGS_TO]-(a:Analysis)<-[:MENTIONED_IN]-(e:Entity) '
            'RETURN count(DISTINCT e) AS cnt',
            params_both, _retries=5,
        )
        entity_count = count_results[0]['cnt'] if count_results else 0
        clustered = entity_count > CLUSTER_THRESHOLD
        print(f'Document graph: {entity_count} entities, clustered={clustered}')

        if clustered:
            nodes, edges = _build_document_graph_clustered(
                project_id, document_id, params_both,
                seg_results, analysis_results, next_results,
            )
        else:
            nodes, edges = _build_document_graph_full(
                project_id, document_id, params_both,
                seg_results, analysis_results, next_results,
            )

    return {
        'success': True,
        'nodes': nodes,
        'edges': edges,
        'clustered': clustered,
        'total_segments': total_segments,
    }


def action_expand_entity_cluster(params: dict) -> dict:
    """Expand a clustered entity type into individual entities for a document."""
    project_id = params['project_id']
    document_id = params['document_id']
    entity_type = params['entity_type']

    params_q = {'did': document_id, 'pid': project_id, 'etype': entity_type}

    # Sequential queries: Start from Document (~id indexed) and traverse down to entities
    ent_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[:MENTIONED_IN]-(e:Entity) '
        'RETURN DISTINCT e.id AS id, e.name AS name',
        params_q, _retries=5,
    )
    mention_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[r:MENTIONED_IN]-(e:Entity) '
        'RETURN e.id AS source, a.id AS target, '
        'r.confidence AS confidence, r.context AS context',
        params_q, _retries=5,
    )

    nodes = []
    for e in ent_results:
        nodes.append({
            'id': e['id'],
            'name': e['name'],
            'label': 'entity',
            'properties': {},
        })

    edges = []
    for m in mention_results:
        edges.append({
            'source': m['source'],
            'target': m['target'],
            'label': 'MENTIONED_IN',
            'properties': {
                'confidence': m.get('confidence', 1.0),
                'context': m.get('context', ''),
            },
        })

    return {'success': True, 'nodes': nodes, 'edges': edges, 'entity_type': entity_type}


def action_expand_all_clusters(params: dict) -> dict:
    """Expand all clustered entity types into individual entities for a document."""
    document_id = params['document_id']
    params_q = {'did': document_id}

    ent_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[:MENTIONED_IN]-(e:Entity) '
        'RETURN DISTINCT e.id AS id, e.name AS name',
        params_q, _retries=5,
    )
    mention_results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)<-[:BELONGS_TO]-(a:Analysis)'
        '<-[r:MENTIONED_IN]-(e:Entity) '
        'RETURN e.id AS source, a.id AS target, '
        'r.confidence AS confidence, r.context AS context',
        params_q, _retries=5,
    )

    nodes = []
    for e in ent_results:
        nodes.append({
            'id': e['id'],
            'name': e['name'],
            'label': 'entity',
            'properties': {},
        })

    edges = []
    for m in mention_results:
        edges.append({
            'source': m['source'],
            'target': m['target'],
            'label': 'MENTIONED_IN',
            'properties': {
                'confidence': m.get('confidence', 1.0),
                'context': m.get('context', ''),
            },
        })

    return {'success': True, 'nodes': nodes, 'edges': edges}


def action_get_document_tagcloud(params: dict) -> dict:
    """Get lightweight entity tag cloud data for a document.

    Returns entity name and connection count without full graph structure.
    """
    project_id = params['project_id']
    document_id = params['document_id']

    results = run_query(
        'MATCH (d:Document {`~id`: $did})<-[:BELONGS_TO]-(s:Segment)'
        '<-[:BELONGS_TO]-(a:Analysis)<-[r:MENTIONED_IN]-(e:Entity) '
        'RETURN e.id AS id, e.name AS name, '
        'count(r) AS connections',
        {'did': document_id, 'pid': project_id},
    )

    tags = [
        {
            'id': r['id'],
            'name': r['name'],
            'connections': r['connections'],
        }
        for r in results
    ]

    return {'success': True, 'tags': tags}


ACTIONS = {
    # Write
    'add_segment_links': action_add_segment_links,
    'add_analyses': action_add_analyses,
    'add_entities': action_add_entities,
    'add_relationships': action_add_relationships,
    'build_clusters': action_build_clusters,
    'link_documents': action_link_documents,
    'unlink_documents': action_unlink_documents,
    'get_linked_documents': action_get_linked_documents,
    'delete_analysis': action_delete_analysis,
    'delete_by_workflow': action_delete_by_workflow,
    'clear_all': action_clear_all,
    'raw_query': lambda params: {'success': True, 'results': run_query(params['query'], params.get('parameters'))},
    # Read
    'search_graph': action_search_graph,
    'traverse': action_traverse,
    'find_related_segments': action_find_related_segments,
    'get_entity_graph': action_get_entity_graph,
    'get_document_graph': action_get_document_graph,
    'expand_entity_cluster': action_expand_entity_cluster,
    'expand_all_clusters': action_expand_all_clusters,
    'get_document_tagcloud': action_get_document_tagcloud,
}


# ========================================
# Graph disabled (no graph database)
# ========================================
# Each action gets the keys of its normal result for a graph with no data, so
# callers need no change: writes and deletes are dropped, reads come back empty.

def _int_or_value(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _disabled_document_graph(params: dict) -> dict:
    """Empty get_document_graph result, with the mode keys the backend passes on."""
    result = {'nodes': [], 'edges': [], 'clustered': False, 'total_segments': 0}
    if params.get('search'):
        result['mode'] = 'search'
    elif params.get('page') is not None:
        result.update(mode='page', focus_page=_int_or_value(params['page']))
    elif params.get('from_page') is not None and params.get('to_page') is not None:
        result.update(
            mode='range',
            from_page=_int_or_value(params['from_page']),
            to_page=_int_or_value(params['to_page']),
        )
    return result


DISABLED_RESULTS = {
    # Write
    'add_segment_links': lambda p: {
        'document_id': p.get('document_id', ''),
        'segment_range': f"{p.get('start_index', 0)}-{p.get('end_index', p.get('segment_count', 0))}",
    },
    'add_analyses': lambda p: {'created': 0},
    'add_entities': lambda p: {'created': 0},
    'add_relationships': lambda p: {'created': 0},
    'build_clusters': lambda p: {'clustered': False, 'entity_count': 0, 'cluster_count': 0},
    'link_documents': lambda p: {},
    'unlink_documents': lambda p: {},
    'get_linked_documents': lambda p: {'links': []},
    'delete_analysis': lambda p: {'analysis_id': p.get('analysis_id', '')},
    'delete_by_workflow': lambda p: {},
    'clear_all': lambda p: {'deleted_edges': 0, 'deleted_nodes': 0},
    'raw_query': lambda p: {'results': []},
    # Read
    'search_graph': lambda p: {'entities': [], 'segments': []},
    'traverse': lambda p: {'nodes': []},
    'find_related_segments': lambda p: {'segments': []},
    'get_entity_graph': lambda p: {'nodes': [], 'edges': [], 'tagcloud': [], 'total_entities': 0},
    'get_document_graph': _disabled_document_graph,
    'expand_entity_cluster': lambda p: {'nodes': [], 'edges': [], 'entity_type': p.get('entity_type', '')},
    'expand_all_clusters': lambda p: {'nodes': [], 'edges': []},
    'get_document_tagcloud': lambda p: {'tags': []},
}


def _disabled_response(action, params) -> dict:
    """Answer without a graph database. Logs one short line (never the payload)."""
    if action not in ACTIONS:
        print(f'Unknown action: {action}')
        return {
            'statusCode': 400,
            'error': f'Unknown action: {action}',
        }
    print(f'Graph disabled, skipped {action}')
    empty = DISABLED_RESULTS.get(action, lambda p: {})
    return {
        'statusCode': 200,
        'success': True,
        'graph_disabled': True,
        **empty(params if isinstance(params, dict) else {}),
    }


# ========================================
# Handler
# ========================================

def handler(event, _context):
    action = event.get('action')
    params = event.get('params', {})

    if graph_disabled():
        return _disabled_response(action, params)

    print(f'Event: {json.dumps(event)}')
    print(f'Action: {action}')

    if action not in ACTIONS:
        print(f'Unknown action: {action}')
        return {
            'statusCode': 400,
            'error': f'Unknown action: {action}',
        }

    try:
        print(f'Executing action: {action}')
        result = ACTIONS[action](params)
        print(f'Action result keys: {list(result.keys())}')
        return {
            'statusCode': 200,
            **result,
        }
    except Exception as e:
        print(f'Error in action {action}: {e}')
        return {
            'statusCode': 500,
            'error': str(e),
        }
