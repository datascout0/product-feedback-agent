"""Static structural checks for the n8n workflow artifact.

No live n8n instance is available in this environment (see
docs/n8n-setup.md's HUMAN CHECKPOINT REQUIRED notice) -- these tests verify
everything that CAN be verified without one: the JSON is well-formed, every
connection resolves to a real node, and -- most importantly -- there is no
graph path from the intake form to a GitHub-mutating node that does not pass
through the human approval gate's "approve" branch.
"""

from __future__ import annotations

import json
from pathlib import Path

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / "n8n" / "workflows" / "product-feedback-agent.json"
)


def _load_workflow() -> dict:
    return json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _node_names(workflow: dict) -> set[str]:
    return {node["name"] for node in workflow["nodes"]}


def _adjacency(workflow: dict) -> dict[str, list[str]]:
    """node name -> list of directly-connected downstream node names (all branches)."""
    adjacency: dict[str, list[str]] = {}
    for source, connection in workflow["connections"].items():
        targets = [edge["node"] for branch in connection["main"] for edge in branch]
        adjacency[source] = targets
    return adjacency


def _reachable_from(workflow: dict, start: str, *, exclude_edge: tuple[str, str] | None = None) -> set[str]:
    """BFS reachability from `start`, optionally pretending one edge does not exist."""
    adjacency = _adjacency(workflow)
    seen = {start}
    queue = [start]
    while queue:
        current = queue.pop()
        for target in adjacency.get(current, []):
            if exclude_edge is not None and (current, target) == exclude_edge:
                continue
            if target not in seen:
                seen.add(target)
                queue.append(target)
    return seen


def test_workflow_file_is_valid_json():
    workflow = _load_workflow()
    assert workflow["name"]
    assert isinstance(workflow["nodes"], list) and len(workflow["nodes"]) > 0


def test_every_connection_target_exists_as_a_node():
    workflow = _load_workflow()
    names = _node_names(workflow)
    for source, connection in workflow["connections"].items():
        assert source in names, f"connection source '{source}' is not a node"
        for branch in connection["main"]:
            for edge in branch:
                assert edge["node"] in names, f"connection target '{edge['node']}' is not a node"


def test_every_non_trigger_node_has_an_incoming_connection():
    workflow = _load_workflow()
    names = _node_names(workflow)
    trigger_types = {"n8n-nodes-base.formTrigger"}
    triggers = {node["name"] for node in workflow["nodes"] if node["type"] in trigger_types}

    targets: set[str] = set()
    for connection in workflow["connections"].values():
        for branch in connection["main"]:
            for edge in branch:
                targets.add(edge["node"])

    unreachable = names - targets - triggers
    assert unreachable == set(), f"nodes with no incoming connection: {unreachable}"


def test_every_node_is_reachable_from_the_intake_form():
    workflow = _load_workflow()
    reachable = _reachable_from(workflow, "Intake Form")
    assert reachable == _node_names(workflow)


def test_rejected_branch_is_a_dead_end():
    workflow = _load_workflow()
    assert "No Action (Rejected)" not in workflow["connections"]


def test_no_github_node_reachable_if_approval_gate_approve_branch_is_removed():
    """The core safety property: cut the ONE edge from Approval Gate's true
    branch, and no GitHub-mutating node should remain reachable at all."""
    workflow = _load_workflow()
    github_node = "Create GitHub Issue (Approved)"

    reachable_with_edge = _reachable_from(workflow, "Intake Form")
    assert github_node in reachable_with_edge

    reachable_without_edge = _reachable_from(
        workflow,
        "Intake Form",
        exclude_edge=("Approval Gate", "Build GitHub Issue Request"),
    )
    assert github_node not in reachable_without_edge


def test_approval_gate_requires_the_literal_string_approve():
    workflow = _load_workflow()
    gate = next(node for node in workflow["nodes"] if node["name"] == "Approval Gate")
    conditions = gate["parameters"]["conditions"]["conditions"]

    decision_condition = next(c for c in conditions if "Decision" in c["leftValue"])
    assert decision_condition["rightValue"] == "approve"
    assert decision_condition["operator"]["operation"] == "equals"
    assert gate["parameters"]["conditions"]["options"]["caseSensitive"] is True
    assert gate["parameters"]["conditions"]["combinator"] == "and"


def test_approval_gate_also_requires_opportunity_id_and_repo_present():
    workflow = _load_workflow()
    gate = next(node for node in workflow["nodes"] if node["name"] == "Approval Gate")
    conditions = gate["parameters"]["conditions"]["conditions"]

    left_values = [c["leftValue"] for c in conditions]
    assert any("opportunity_id" in value for value in left_values)
    assert any("repo" in value.lower() for value in left_values)


def test_github_issue_request_is_built_with_approved_true_and_dry_run_false_only_on_the_approved_path():
    workflow = _load_workflow()
    build_node = next(
        node for node in workflow["nodes"] if node["name"] == "Build GitHub Issue Request"
    )
    code = build_node["parameters"]["jsCode"]
    assert "approved: true" in code
    assert "dry_run: false" in code

    # No other Code node's jsCode hard-codes an approved/dry_run mutation flag.
    for node in workflow["nodes"]:
        if node["name"] == "Build GitHub Issue Request":
            continue
        params = node.get("parameters", {})
        if "jsCode" in params:
            assert "approved: true" not in params["jsCode"]
            assert "dry_run: false" not in params["jsCode"]


def test_analyze_request_is_never_built_with_a_github_field():
    workflow = _load_workflow()
    analysis_node = next(node for node in workflow["nodes"] if node["name"] == "Build Analyze Request")
    code = analysis_node["parameters"]["jsCode"]
    assert "feedback_file" in code
    assert "analysis_query" in code
    assert "approved" not in code
    assert "github" not in code.lower()


def test_workflow_has_no_hardcoded_windows_user_path():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "C:\\Users" not in raw
    assert "C:/Users" not in raw
    assert "/home/" not in raw


def test_workflow_does_not_embed_a_github_token_or_gemini_key():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN" not in raw
    assert "GEMINI_API_KEY" not in raw
    assert "ghp_" not in raw


# ---------------------------------------------------------------------------
# Stage 15 repair: Execute Command -> HTTP Request boundary
#
# Live finding (2026-09-07): the workflow imported successfully into a real
# self-hosted n8n Docker instance, but both Execute Command nodes came up as
# unknown "?" nodes ("Install this node to use it") -- Execute Command was
# not available on that instance. Even if it had been, the command would
# have executed inside the n8n container, not against the Windows host's
# .venv where this project actually runs. Both Python integration boundaries
# were replaced with n8n's built-in HTTP Request node calling a small local
# HTTP service (src/product_feedback_agent/api.py) on the Windows host,
# reachable from the Docker container at host.docker.internal.
# ---------------------------------------------------------------------------


def test_workflow_contains_no_execute_command_nodes():
    workflow = _load_workflow()
    for node in workflow["nodes"]:
        assert node["type"] != "n8n-nodes-base.executeCommand", (
            f"'{node['name']}' is still an Execute Command node -- Stage 15 live "
            "finding: this node type was unavailable on the real n8n instance."
        )


def test_both_python_boundaries_use_http_request_nodes():
    workflow = _load_workflow()
    http_node_names = {
        node["name"] for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"
    }
    assert http_node_names == {"Run Analysis (HTTP)", "Create GitHub Issue (Approved)"}


def test_analysis_http_request_targets_host_docker_internal_analyze_endpoint():
    workflow = _load_workflow()
    node = next(n for n in workflow["nodes"] if n["name"] == "Run Analysis (HTTP)")
    assert node["parameters"]["method"] == "POST"
    url = node["parameters"]["url"]
    assert "host.docker.internal" in url
    assert url.rstrip("'\" }").endswith("/analyze")


def test_github_action_http_request_targets_host_docker_internal_issues_endpoint():
    workflow = _load_workflow()
    node = next(n for n in workflow["nodes"] if n["name"] == "Create GitHub Issue (Approved)")
    assert node["parameters"]["method"] == "POST"
    url = node["parameters"]["url"]
    assert "host.docker.internal" in url
    assert url.rstrip("'\" }").endswith("/github/issues")


def test_no_execute_command_node_type_anywhere_in_the_workflow_file():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "executeCommand" not in raw


def test_rejection_path_cannot_reach_the_github_http_request_node():
    """Same core safety property as before the repair, re-checked against
    the new HTTP Request node name: the rejection branch is structurally
    incapable of reaching a GitHub-mutating node."""
    workflow = _load_workflow()
    reachable_from_rejection = _reachable_from(workflow, "No Action (Rejected)")
    assert "Create GitHub Issue (Approved)" not in reachable_from_rejection
    assert reachable_from_rejection == {"No Action (Rejected)"}


def test_no_hardcoded_windows_or_posix_secret_looking_path_in_new_http_nodes():
    workflow = _load_workflow()
    for name in ("Run Analysis (HTTP)", "Create GitHub Issue (Approved)"):
        node = next(n for n in workflow["nodes"] if n["name"] == name)
        raw = json.dumps(node)
        assert "C:\\Users" not in raw
        assert "C:/Users" not in raw


# ---------------------------------------------------------------------------
# Live finding (2026-09-07): "Human Review & Approval" imported with
# Page Type = Form Ending instead of Next Form Page. The installed Form
# node's "Form Ending" page type has no further outgoing connection slot in
# the n8n UI, so a page type this node never displays would silently strand
# the approval decision. Repaired by switching the node to operation="page"
# (Next Form Page) with defineForm="json" (JSON-defined form fields) instead
# of the UI "Using Fields Below" field builder.
# ---------------------------------------------------------------------------


def _human_review_node(workflow: dict) -> dict:
    return next(n for n in workflow["nodes"] if n["name"] == "Human Review & Approval")


def _human_review_json_fields(workflow: dict) -> list[dict]:
    node = _human_review_node(workflow)
    return json.loads(node["parameters"]["jsonOutput"])


def test_human_review_node_operation_is_page_not_form_ending():
    workflow = _load_workflow()
    node = _human_review_node(workflow)
    assert node["parameters"]["operation"] == "page"
    assert node["parameters"]["operation"] != "completion"


def test_human_review_node_defines_form_via_json():
    workflow = _load_workflow()
    node = _human_review_node(workflow)
    assert node["parameters"]["defineForm"] == "json"
    assert "jsonOutput" in node["parameters"]


def test_human_review_json_fields_contain_a_required_decision_dropdown():
    workflow = _load_workflow()
    fields = _human_review_json_fields(workflow)
    decision = next(f for f in fields if f["fieldLabel"] == "Decision")
    assert decision["fieldType"] == "dropdown"
    assert decision["requiredField"] is True
    options = {v["option"] for v in decision["fieldOptions"]["values"]}
    assert options == {"approve", "reject"}


def test_human_review_json_fields_contain_a_required_opportunity_id_text_input():
    workflow = _load_workflow()
    fields = _human_review_json_fields(workflow)
    opportunity_id = next(f for f in fields if f["fieldLabel"] == "opportunity_id")
    assert opportunity_id["fieldType"] == "text"
    assert opportunity_id["requiredField"] is True


def test_human_review_json_fields_contain_a_required_repo_text_input():
    workflow = _load_workflow()
    fields = _human_review_json_fields(workflow)
    repo = next(f for f in fields if f["fieldLabel"] == "repo")
    assert repo["fieldType"] == "text"
    assert repo["requiredField"] is True
    assert repo["placeholder"] == "owner/repository"


def test_human_review_json_fields_are_exactly_decision_opportunity_id_repo():
    workflow = _load_workflow()
    fields = _human_review_json_fields(workflow)
    labels = [f["fieldLabel"] for f in fields]
    assert labels == ["Decision", "opportunity_id", "repo"]


def test_approval_gate_checks_the_exact_submitted_json_field_names():
    workflow = _load_workflow()
    gate = next(node for node in workflow["nodes"] if node["name"] == "Approval Gate")
    conditions = gate["parameters"]["conditions"]["conditions"]
    left_values = [c["leftValue"] for c in conditions]

    assert "={{ $json['Decision'] }}" in left_values
    assert "={{ $json['opportunity_id'] }}" in left_values
    assert "={{ $json['repo'] }}" in left_values


def test_build_github_issue_request_reads_the_exact_submitted_json_field_names():
    workflow = _load_workflow()
    build_node = next(
        node for node in workflow["nodes"] if node["name"] == "Build GitHub Issue Request"
    )
    code = build_node["parameters"]["jsCode"]
    assert "$json['opportunity_id']" in code
    assert "$json['repo']" in code


def test_human_review_node_is_not_a_form_ending_page():
    workflow = _load_workflow()
    node = _human_review_node(workflow)
    assert node["parameters"]["operation"] != "completion"
    raw = json.dumps(node["parameters"])
    assert "Form Ending" not in raw
