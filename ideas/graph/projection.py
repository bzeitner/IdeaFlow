from collections import deque
import json

from django.conf import settings
from django.db.models import Q

from ideas.graph.revision import current_revision
from ideas.models import Idea, IdeaRelation


def _node(idea):
    return {
        "id": f"idea-{idea.pk}",
        "idea_id": idea.pk,
        "label": idea.title,
        "status": idea.status,
        "category": idea.category.name,
        "category_color": idea.category.color,
        "stage": idea.stage.name if idea.stage else "",
        "interest": idea.interest_level,
        "url": idea.get_absolute_url(),
        "summary": idea.exec_summary or idea.summary,
        "parent_id": idea.parent_id,
        "repo": idea.repo,
        "next_action": idea.next_action,
    }


def _explicit_edge(relation):
    return {
        "id": f"relation-{relation.pk}",
        "source": f"idea-{relation.source_id}",
        "target": f"idea-{relation.target_id}",
        "type": relation.relation_type,
        "label": relation.get_relation_type_display(),
        "description": relation.description,
        "confidence": relation.confidence,
        "provenance": relation.provenance,
        "editable": True,
        "source_label": relation.source.title,
        "target_label": relation.target.title,
    }


def graph_projection(*, idea_ids=None, include_archived=False):
    ideas = Idea.objects.select_related("category", "stage", "parent")
    if idea_ids is not None:
        ideas = ideas.filter(pk__in=idea_ids)
    if not include_archived:
        ideas = ideas.exclude(status="archived")
    idea_list = list(ideas)
    ids = {idea.pk for idea in idea_list}
    relations = IdeaRelation.objects.select_related("source", "target").filter(
        source_id__in=ids, target_id__in=ids
    )
    edges = [_explicit_edge(relation) for relation in relations]
    edges.extend(
        {
            "id": f"parent-{idea.parent_id}-{idea.pk}",
            "source": f"idea-{idea.parent_id}",
            "target": f"idea-{idea.pk}",
            "type": "parent_of",
            "label": "Parent of",
            "description": "",
            "confidence": 5,
            "provenance": "derived",
            "editable": False,
            "source_label": idea.parent.title,
            "target_label": idea.title,
        }
        for idea in idea_list
        if idea.parent_id in ids
    )
    return {
        "revision": current_revision(),
        "nodes": [_node(idea) for idea in idea_list],
        "edges": edges,
    }


def neighborhood(idea, *, depth=1, max_nodes=50, include_archived=False):
    depth = max(0, min(int(depth), 3))
    max_nodes = max(1, min(int(max_nodes), 200))
    seen, frontier = {idea.pk}, deque([(idea.pk, 0)])
    while frontier and len(seen) < max_nodes:
        current, level = frontier.popleft()
        if level >= depth:
            continue
        adjacent = set(
            IdeaRelation.objects.filter(Q(source_id=current) | Q(target_id=current))
            .values_list("source_id", "target_id")
            .iterator()
        )
        candidates = {value for pair in adjacent for value in pair}
        candidates.update(
            Idea.objects.filter(Q(pk=current) | Q(parent_id=current) | Q(pk__in=Idea.objects.filter(pk=current).values("parent_id")))
            .values_list("pk", flat=True)
        )
        if not include_archived:
            candidates = set(
                Idea.objects.filter(pk__in=candidates)
                .exclude(status="archived")
                .values_list("pk", flat=True)
            )
        for candidate in sorted(candidates - seen):
            if len(seen) >= max_nodes:
                break
            seen.add(candidate)
            frontier.append((candidate, level + 1))
    return graph_projection(idea_ids=seen, include_archived=include_archived)


def _context_row(node, edge):
    """Compact relation context: enough evidence for an agent without full idea text."""
    return {
        "idea_id": node["idea_id"],
        "title": node["label"],
        "status": node["status"],
        "category": node["category"],
        "stage": node["stage"],
        "summary": (node["summary"] or "")[:600],
        "next_action": (node["next_action"] or "")[:300],
        "relation": edge["type"],
        "relation_description": (edge.get("description", "") or "")[:300],
        "confidence": edge.get("confidence"),
        "provenance": edge.get("provenance", ""),
    }


def graph_context(idea, *, depth=1, max_nodes=30, token_budget=None, task="research"):
    default_budget = settings.IDEAFLOW_GRAPH_CONTEXT_DEFAULT_TOKEN_BUDGET
    maximum_budget = settings.IDEAFLOW_GRAPH_CONTEXT_MAX_TOKEN_BUDGET
    token_budget = default_budget if token_budget is None else int(token_budget)
    token_budget = max(500, min(token_budget, maximum_budget))
    graph = neighborhood(idea, depth=depth, max_nodes=max_nodes, include_archived=False)
    by_id = {node["id"]: node for node in graph["nodes"]}
    grouped = {
        "parents": [], "children": [], "siblings": [], "dependencies": [],
        "dependents": [], "related": [],
    }
    center = f"idea-{idea.pk}"
    for edge in graph["edges"]:
        other = None
        bucket = "related"
        if edge["type"] == "parent_of":
            if edge["target"] == center:
                other, bucket = edge["source"], "parents"
            elif edge["source"] == center:
                other, bucket = edge["target"], "children"
        elif edge["type"] == "depends_on":
            if edge["source"] == center:
                other, bucket = edge["target"], "dependencies"
            elif edge["target"] == center:
                other, bucket = edge["source"], "dependents"
        elif edge["source"] == center:
            other = edge["target"]
        elif edge["target"] == center:
            other = edge["source"]
        if other and other in by_id:
            grouped[bucket].append(_context_row(by_id[other], edge))

    if idea.parent_id:
        for sibling in Idea.objects.select_related("category", "stage").filter(
            parent_id=idea.parent_id
        ).exclude(pk=idea.pk).exclude(status="archived"):
            grouped["siblings"].append(
                _context_row(_node(sibling), {"type": "sibling", "provenance": "derived"})
            )

    priorities = {
        "execute": ["dependencies", "parents", "children", "dependents", "related"],
        "critique": ["dependencies", "related", "parents", "dependents", "children"],
        "review": ["parents", "dependencies", "children", "siblings", "related", "dependents"],
        "persona": ["parents", "children", "siblings", "dependencies", "dependents", "related"],
        "research": ["parents", "children", "dependencies", "related", "dependents"],
        # A recurring task (e.g. a podcast idea drawing on other ideas' research
        # via a "supports" relation) cares about "related" edges more than
        # structural parent/child/dependency ones — those are exactly where a
        # supports relation lands, so put them first.
        "repeat": ["related", "dependencies", "parents", "children", "dependents", "siblings"],
    }
    task = task if task in priorities else "research"
    result = {
        "idea": _context_row(_node(idea), {"type": "focus"}),
        "revision": graph["revision"],
        "task": task,
        **{key: [] for key in grouped},
    }
    included = 0
    available = sum(len(rows) for rows in grouped.values())
    for bucket in priorities[task]:
        for row in grouped[bucket]:
            candidate = {**result, bucket: [*result[bucket], row]}
            estimated_tokens = max(1, len(json.dumps(candidate, ensure_ascii=False)) // 4)
            if estimated_tokens > token_budget:
                continue
            result[bucket].append(row)
            included += 1
    result["budget"] = {
        "requested_tokens": token_budget,
        "estimated_tokens": max(1, len(json.dumps(result, ensure_ascii=False)) // 4),
        "included_relations": included,
        "omitted_relations": available - included,
    }
    return result


def graph_search(query, *, limit=20, include_archived=False):
    ideas = Idea.objects.select_related("category", "stage").filter(
        Q(title__icontains=query)
        | Q(summary__icontains=query)
        | Q(exec_summary__icontains=query)
        | Q(notes__icontains=query)
    )
    if not include_archived:
        ideas = ideas.exclude(status="archived")
    return {"revision": current_revision(), "results": [_node(i) for i in ideas[:limit]]}
