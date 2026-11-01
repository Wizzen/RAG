from __future__ import annotations

"""可审核的轻量知识图谱：业务表适配、两跳检索及原文证据回溯。"""

import json
import re
import unicodedata
from collections import defaultdict

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Document, GraphEntity, GraphEvidence, GraphRelation, StructuredDataset
from .publication import digest

PREDICATES = {"has_part": "包含部件", "documented_by": "对应图纸", "contains": "包含",
              "requires": "要求", "applies_to": "适用于", "references": "引用",
              "has_parameter": "参数为", "has_failure": "故障表现为", "prohibits": "禁止",
              "does_not_apply_to": "不适用于"}
PREDICATES.update({'is_a':'属于', 'has_subtype':'子类型为', 'defines':'定义', 'uses':'使用', 'checks':'检查'})
KINDS = {"equipment": "设备", "part": "部件", "drawing": "图纸"}
MAX_EDGES = 300


def normalize_identifier(value: str) -> str:
    """统一大小写和空白，保留连字符等编号语义，避免 A-12 与 A12 合并。"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip().casefold()


def record_digest(record) -> str:
    """绑定数据集版本、业务字段和完整原始行。"""
    values = {name: getattr(record, name) for name in (
        "equipment", "part_no", "part_name", "drawing_no", "g_code", "scp_level", "apex_no")}
    return digest(json.dumps([record.dataset.checksum, record.sheet_name, record.row_number,
                              values, record.raw_data], sort_keys=True, ensure_ascii=False))


@transaction.atomic
def seed_dataset(dataset: StructuredDataset) -> int:
    """从显式字段生成候选关系；幂等重跑不改变已有审核结论。"""
    if not dataset.active or not dataset.kb_id:
        return 0
    count = 0
    for record in dataset.records.select_related("dataset").order_by("pk").iterator():
        equipment = normalize_identifier(record.equipment)
        # 缺少设备上下文时保留数据集命名空间，不猜测跨表同名部件属于同一设备。
        namespace = "equipment:" + equipment if equipment else f"dataset:{dataset.pk}"
        entities = {}
        for kind, value in (("equipment", record.equipment), ("part", record.part_no),
                            ("drawing", record.drawing_no)):
            if not value.strip():
                continue
            entity, _ = GraphEntity.objects.get_or_create(
                kb=dataset.kb, kind=kind, namespace="" if kind == "equipment" else namespace,
                key=normalize_identifier(value), defaults={"label": value.strip()},
            )
            # 名称保留在行级证据里；共享实体上不累积可能过期或歧义的部件名称。
            entities[kind] = entity
        for subject_kind, predicate, object_kind in (
            ("equipment", "has_part", "part"), ("part", "documented_by", "drawing"),
        ):
            if subject_kind not in entities or object_kind not in entities:
                continue
            version = record_digest(record)
            identity = digest(f"row:{record.pk}:{version}:{predicate}")
            relation, created = GraphRelation.objects.get_or_create(identity=identity, defaults={
                "subject": entities[subject_kind], "predicate": predicate,
                "object": entities[object_kind], "origin_record": record,
                "qualifiers": {"equipment": record.equipment},
            })
            GraphEvidence.objects.get_or_create(relation=relation, record=record, defaults={
                "quote": json.dumps(record.raw_data, ensure_ascii=False, sort_keys=True),
                "source_digest": version,
            })
            count += int(created)
    return count


def evidence_current(evidence: GraphEvidence) -> bool:
    """来源必须仍有效且属于关系所在知识库；文本证据需原样存在。"""
    scope = evidence.relation.subject.kb_id
    if evidence.relation.object.kb_id != scope:
        return False
    if evidence.record_id:
        row = evidence.record
        return (row.dataset.active and row.dataset.kb_id == scope
                and record_digest(row) == evidence.source_digest)
    doc = evidence.document
    quote_exists = bool(doc and evidence.quote and evidence.quote in doc.md_content)
    if doc and evidence.chunks and not quote_exists:
        from .keyword_index import get_chunks
        quote_exists = (Document.objects.filter(kb=doc.kb, original_name=doc.original_name).count() == 1
                        and any(evidence.quote in row['text'] for row in get_chunks(doc.kb.slug, doc.original_name)))
    return bool(doc and doc.status == Document.Status.COMPLETED
                and (doc.kb_id == scope or doc.kb.parent_id == scope)
                and quote_exists
                and digest(doc.md_content) == evidence.source_digest)


def relation_current(relation: GraphRelation) -> bool:
    """导入关系必须保留有效原始行，补充手册不能使过期业务事实复活。"""
    evidence = list(relation.evidence.all())
    if relation.origin_record_id:
        return any(e.record_id == relation.origin_record_id and evidence_current(e) for e in evidence)
    return any(evidence_current(e) for e in evidence)


def relation_queryset():
    """集中预载证据及权限边界，供检索和审核复用。"""
    from django.db.models import Prefetch
    return GraphRelation.objects.select_related("subject__kb", "object__kb").prefetch_related(
        Prefetch("evidence", queryset=GraphEvidence.objects.select_related(
            "relation__subject", "relation__object", "record__dataset", "document__kb")))


@transaction.atomic
def review_relation(relation: GraphRelation, status: str, user) -> None:
    """核对前重读证据；审核身份只由服务器提供。"""
    from .access import can_manage_kb
    if not can_manage_kb(user, relation.subject.kb):
        raise ValueError("没有管理该知识库的权限。")
    if status not in GraphRelation.Status.values:
        raise ValueError("未知审核状态。")
    if status == GraphRelation.Status.VERIFIED and not relation_current(relation):
        raise ValueError("来源已变更或失效，请重新导入并核对。")
    relation.status = status
    relation.reviewed_by = user
    relation.reviewed_at = timezone.now()
    relation.save(update_fields=["status", "reviewed_by", "reviewed_at"])


@transaction.atomic
def attach_document(relation: GraphRelation, document: Document, quote: str, user) -> GraphEvidence:
    """人工关联原文；绑定当前文档版本，修改后必须重新核对关系。"""
    from .access import can_manage_kb, kb_accessible
    if not can_manage_kb(user, relation.subject.kb) or not kb_accessible(user, document.kb):
        raise ValueError("没有访问该证据的权限。")
    quote = quote.strip()
    if not 8 <= len(quote) <= 4000 or quote not in document.md_content:
        raise ValueError("请粘贴原文中连续的 8–4000 个字符。")
    if document.status != Document.Status.COMPLETED or not (
        document.kb_id == relation.subject.kb_id or document.kb.parent_id == relation.subject.kb_id
    ):
        raise ValueError("只能关联同一知识库内已完成的文档。")
    evidence, _ = GraphEvidence.objects.get_or_create(
        relation=relation, document=document, quote=quote,
        source_digest=digest(document.md_content),
    )
    relation.status = GraphRelation.Status.CANDIDATE
    relation.reviewed_by = None
    relation.reviewed_at = None
    relation.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    return evidence


def _mentioned(query: str, value: str) -> bool:
    """编号按完整边界匹配；中文名称允许在自然语言问题中出现。"""
    value = normalize_identifier(value)
    if not value:
        return False
    if re.search(r"[\u3400-\u9fff]", value):
        return len(value) >= 2 and value in query
    return re.search(r"(?<![\w.\-])" + re.escape(value) + r"(?![\w.\-])", query, re.ASCII) is not None


def walk_graph(scope_slugs: list[str], query: str) -> dict:
    """只遍历指定库内有效且已核对的关系，最多两跳；歧义时不猜测。"""
    query = normalize_identifier(query[:1000])
    if not query or not scope_slugs:
        return {"paths": [], "ambiguous": False, "truncated": False}
    # 先按完整编号/别名识别种子。实体上限显式返回，不把截断误当唯一命中。
    entities = list(GraphEntity.objects.filter(kb__slug__in=scope_slugs).order_by("pk")[:2001])
    if len(entities) > 2000:
        return {"paths": [], "ambiguous": False, "truncated": True}
    matched = [e for e in entities if any(_mentioned(query, v) for v in [e.key, e.label, *e.aliases])]
    nearby = list(relation_queryset().filter(
        subject__kb__slug__in=scope_slugs, object__kb__slug__in=scope_slugs,
        status=GraphRelation.Status.VERIFIED,
    ).filter(Q(subject_id__in=[e.pk for e in matched]) | Q(object_id__in=[e.pk for e in matched]))
      .order_by("pk")[:MAX_EDGES + 1]) if matched else []
    if len(nearby) > MAX_EDGES:
        return {"paths": [], "ambiguous": False, "truncated": True}
    live_ids = {pk for r in nearby if relation_current(r) for pk in (r.subject_id, r.object_id)}
    matched = [e for e in matched if e.pk in live_ids]
    # 同时点名设备时，用设备限制部件命名空间，避免同号部件跨设备扩散。
    equipment_keys = {e.key for e in matched if e.kind == "equipment"}
    if equipment_keys:
        matched = [e for e in matched if e.kind == "equipment" or e.namespace in
                   {"equipment:" + key for key in equipment_keys}]
    identities = defaultdict(set)
    for entity in matched:
        for value in [entity.key, entity.label, *entity.aliases]:
            if _mentioned(query, value):
                identities[(entity.kind, normalize_identifier(value))].add(entity.pk)
    if any(len(v) > 1 for v in identities.values()) or len(matched) > 12:
        return {"paths": [], "ambiguous": True, "truncated": False}
    frontier = {e.pk: [] for e in matched}
    visited = set(frontier)
    seen_edges = set()
    paths = []
    truncated = False
    for _hop in range(2):
        if not frontier:
            break
        relations = list(relation_queryset().filter(
            subject__kb__slug__in=scope_slugs, object__kb__slug__in=scope_slugs,
            status=GraphRelation.Status.VERIFIED,
        ).filter(Q(subject_id__in=frontier) | Q(object_id__in=frontier)).order_by("pk")[:MAX_EDGES + 1])
        if len(relations) > MAX_EDGES:
            truncated = True
        next_frontier = {}
        for relation in relations[:MAX_EDGES]:
            if relation.pk in seen_edges or not relation_current(relation):
                continue
            # 从部件逆向到设备后，不再扩散到该设备所有无关的兄弟部件。
            start = relation.subject_id if relation.subject_id in frontier else relation.object_id
            end = relation.object_id if start == relation.subject_id else relation.subject_id
            start_entity = relation.subject if start == relation.subject_id else relation.object
            if _hop and start_entity.kind == "equipment":
                continue
            seen_edges.add(relation.pk)
            path = frontier[start] + [relation]
            paths.append(path)
            if end not in visited:
                next_frontier.setdefault(end, path)
                visited.add(end)
        frontier = next_frontier
    return {"paths": paths, "ambiguous": False, "truncated": truncated}


def path_label(path: list[GraphRelation]) -> str:
    """保留每条关系的真实方向，不把逆向遍历显示为逆向事实。"""
    return "；".join(f"{r.subject.label} —{PREDICATES.get(r.predicate, r.predicate)}→ {r.object.label}" for r in path)


def retrieve_passages(scope_slugs: list[str], document_slugs: list[str], query: str,
                      limit: int = 15) -> list[dict]:
    """图谱只召回当前原文块，复用现有引用和发布核验；不生成伪原文。"""
    from .keyword_index import get_chunks
    walked = walk_graph(scope_slugs, query)
    results = {}
    cache = {}
    from .knowledge_hierarchy import retrieve_passages as hierarchy_passages
    for row in hierarchy_passages(scope_slugs,document_slugs,query,limit=limit):
        results[(row['doc_id'],row['chunk_id'])]=row
    for path in walked["paths"]:
        relation = path[-1]
        for evidence in relation.evidence.all():
            if not evidence.document_id or not evidence_current(evidence):
                continue
            doc = evidence.document
            if doc.kb.slug not in document_slugs:
                continue
            # 旧关键词索引以文件名寻址，重名时不能可靠绑定原文，拒绝猜测。
            if doc.pk not in cache:
                if Document.objects.filter(kb=doc.kb, original_name=doc.original_name).count() != 1:
                    cache[doc.pk] = []
                else:
                    cache[doc.pk] = get_chunks(doc.kb.slug, doc.original_name)
            for row in cache[doc.pk]:
                if not row.get("chunk_id") or evidence.quote not in row["text"]:
                    continue
                key = (str(doc.pk), row["chunk_id"])
                item = results.setdefault(key, {
                    "text": row["text"], "source": doc.original_name, "section": row.get("section", ""),
                    "doc_id": str(doc.pk), "kb_slug": doc.kb.slug, "chunk_id": row["chunk_id"],
                    "via": "graph", "graph_paths": [],
                })
                label = path_label(path)
                if label not in item["graph_paths"]:
                    item["graph_paths"].append(label)
                if len(results) >= limit:
                    return list(results.values())
    return list(results.values())


def fact_snapshot(relation: GraphRelation) -> dict | None:
    """构建已核对事实的完整快照，原始行与断言分别标注，不伪装成文档片段。"""
    if relation.status != GraphRelation.Status.VERIFIED or not relation_current(relation):
        return None
    evidence = [ev for ev in relation.evidence.all() if evidence_current(ev)
                and (not relation.origin_record_id or ev.record_id == relation.origin_record_id)]
    evidence.sort(key=lambda ev: ev.pk)
    if not evidence:
        return None
    sources = []
    for ev in evidence:
        if ev.record_id:
            record = ev.record
            source = f"{record.dataset.source_name} · {record.sheet_name or '数据表'} · 第 {record.row_number} 行"
            sources.append({"kind": "record", "id": record.pk, "dataset_id": record.dataset_id,
                            "digest": ev.source_digest, "source": source, "quote": ev.quote})
        else:
            sources.append({"kind": "document", "id": str(ev.document_id), "digest": ev.source_digest,
                            "source": ev.document.original_name, "quote": ev.quote})
    label = path_label([relation])
    payload = {
        "relation_id": relation.pk, "kb_slug": relation.subject.kb.slug,
        "subject": {field: getattr(relation.subject, field) for field in ("kind", "key", "label", "namespace", "aliases")},
        "predicate": relation.predicate,
        "object": {field: getattr(relation.object, field) for field in ("kind", "key", "label", "namespace", "aliases")},
        "qualifiers": relation.qualifiers, "reviewed_by": relation.reviewed_by_id,
        "reviewed_at": relation.reviewed_at.isoformat() if relation.reviewed_at else None,
        "sources": sources,
    }
    text = (f"已人工核对的关系 #{relation.pk}：{label}\n"
            f"适用条件：{json.dumps(relation.qualifiers, ensure_ascii=False, sort_keys=True)}\n"
            + "\n".join(f"来源：{source['source']}\n原始证据：{source['quote']}" for source in sources))
    # 核验必须对照完整证据，不能悄悄截断原始行中的条件/否定词。
    if len(text) > 6000:
        return None
    return {"kind": "graph", **payload, "source": "；".join(s["source"] for s in sources),
            "label": label, "text": text,
            "fact_digest": digest(json.dumps(payload, ensure_ascii=False, sort_keys=True))}


def validate_fact(snapshot: dict, user) -> bool:
    """发布和历史展示前重读权限、审核结论、实体、条件及所有来源版本。"""
    from .access import kb_accessible
    if not getattr(user, "is_active", False) or not isinstance(snapshot, dict):
        return False
    if snapshot.get('schema_version')==2:
        from .knowledge_hierarchy import validate_snapshot
        return validate_snapshot(snapshot,user)
    try:
        relation = relation_queryset().filter(pk=snapshot.get("relation_id")).first()
    except (TypeError, ValueError):
        return False
    if relation is None or not kb_accessible(user, relation.subject.kb):
        return False
    current = fact_snapshot(relation)
    if current is None or current != snapshot:
        return False
    for ev in relation.evidence.all():
        if ev.document_id and any(s["kind"] == "document" and s["id"] == str(ev.document_id)
                                  for s in current["sources"]):
            if not kb_accessible(user, ev.document.kb):
                return False
    return True


def lookup_facts(scope_slugs: list[str], query: str) -> dict:
    """返回有界的结构化事实与明确路径；歧义、缺失和截断均显式告知调用方。"""
    walked = walk_graph(scope_slugs, query)
    facts = {}
    paths = []
    size = 0
    truncated = walked["truncated"]
    from .knowledge_hierarchy import approved_matches, fact_snapshot as hierarchy_snapshot
    for item in approved_matches(scope_slugs,query,limit=13):
        snapshot=hierarchy_snapshot(item)
        if snapshot is None:continue
        if len(facts)>=12 or size+len(snapshot['text'])>16000:
            truncated=True;break
        facts[snapshot['relation_id']]=snapshot
        paths.append([snapshot['relation_id']]);size+=len(snapshot['text'])
    for path in walked["paths"]:
        ids = []
        for relation in path:
            if relation.pk not in facts:
                snapshot = fact_snapshot(relation)
                if snapshot is None or len(facts) >= 12 or size + len(snapshot["text"]) > 16000:
                    truncated = True
                    break
                facts[relation.pk] = snapshot
                size += len(snapshot["text"])
            ids.append(relation.pk)
        if len(ids) == len(path):
            paths.append(ids)
    return {"facts": list(facts.values()), "paths": paths,
            "ambiguous": walked["ambiguous"], "truncated": truncated}
