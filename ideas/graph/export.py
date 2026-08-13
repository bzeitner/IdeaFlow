import re
from xml.etree import ElementTree as ET

from django.conf import settings

from ideas.graph.projection import graph_projection
from ideas.models import IdeaRelationSuggestion, SuggestionStatus

_INVALID_XML = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)


def _text(value, limit=4000):
    return _INVALID_XML.sub("", str(value or ""))[:limit]


def _data(parent, key, value, limit=4000):
    element = ET.SubElement(parent, "data", key=key)
    element.text = _text(value, limit)


def graphml_export(*, filters):
    graph = graph_projection(include_archived=bool(filters.get("archived")))
    max_nodes = settings.IDEAFLOW_GRAPH_EXPORT_MAX_NODES
    max_edges = settings.IDEAFLOW_GRAPH_EXPORT_MAX_EDGES
    if len(graph["nodes"]) > max_nodes:
        raise ValueError(f"Graph has {len(graph['nodes'])} nodes; export limit is {max_nodes}.")

    node_ids = {node["idea_id"] for node in graph["nodes"]}
    edges = list(graph["edges"])
    if filters.get("suggestions"):
        minimum = float(filters.get("minimum_confidence", 0.55))
        suggestions = IdeaRelationSuggestion.objects.filter(
            status=SuggestionStatus.PENDING,
            source_id__in=node_ids,
            target_id__in=node_ids,
            confidence__gte=minimum,
        ).select_related("source", "target")
        edges.extend(
            {
                "id": f"suggestion-{item.pk}",
                "source": f"idea-{item.source_id}",
                "target": f"idea-{item.target_id}",
                "type": item.relation_type,
                "label": item.get_relation_type_display(),
                "description": item.description,
                "confidence": item.confidence,
                "provenance": "semantic_suggestion",
                "evidence": item.evidence,
                "status": item.status,
            }
            for item in suggestions
        )
    if len(edges) > max_edges:
        raise ValueError(f"Graph has {len(edges)} edges; export limit is {max_edges}.")

    root = ET.Element(
        "graphml",
        xmlns="http://graphml.graphdrawing.org/xmlns",
    )
    node_keys = {
        "label": "string", "idea_id": "int", "status": "string",
        "category": "string", "stage": "string", "interest": "int",
        "summary": "string", "url": "string", "next_action": "string",
    }
    edge_keys = {
        "label": "string", "type": "string", "description": "string",
        "confidence": "double", "provenance": "string", "evidence": "string",
        "review_status": "string",
    }
    for name, value_type in node_keys.items():
        ET.SubElement(root, "key", id=f"n_{name}", **{"for": "node", "attr.name": name, "attr.type": value_type})
    for name, value_type in edge_keys.items():
        ET.SubElement(root, "key", id=f"e_{name}", **{"for": "edge", "attr.name": name, "attr.type": value_type})
    document = ET.SubElement(root, "graph", id="ideaflow", edgedefault="directed")
    for item in graph["nodes"]:
        node = ET.SubElement(document, "node", id=item["id"])
        for name in node_keys:
            _data(node, f"n_{name}", item.get(name, ""), 2000)
    for item in edges:
        edge = ET.SubElement(document, "edge", id=item["id"], source=item["source"], target=item["target"])
        values = {
            "label": item.get("label"), "type": item.get("type"),
            "description": item.get("description"), "confidence": item.get("confidence"),
            "provenance": item.get("provenance"), "evidence": item.get("evidence"),
            "review_status": item.get("status", "canonical"),
        }
        for name in edge_keys:
            _data(edge, f"e_{name}", values[name], 1000 if name == "evidence" else 2000)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(payload) > settings.IDEAFLOW_GRAPH_EXPORT_MAX_BYTES:
        raise OverflowError("Serialized graph exceeds the configured export size limit.")
    return payload, len(graph["nodes"]), len(edges), graph["revision"]
