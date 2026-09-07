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
        exclude_edge=("Approval Gate", "Build GitHub Action Command"),
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


def test_github_action_cli_is_invoked_with_approved_and_no_dry_run_only_on_the_approved_path():
    workflow = _load_workflow()
    build_node = next(
        node for node in workflow["nodes"] if node["name"] == "Build GitHub Action Command"
    )
    code = build_node["parameters"]["jsCode"]
    assert "--approved" in code
    assert "--no-dry-run" in code
    assert "github_action" in code

    # No other node's code references the github_action CLI at all.
    for node in workflow["nodes"]:
        if node["name"] == "Build GitHub Action Command":
            continue
        params = node.get("parameters", {})
        if "jsCode" in params:
            assert "github_action" not in params["jsCode"]
        if "command" in params and isinstance(params["command"], str):
            assert "github_action" not in params["command"]


def test_orchestrator_cli_is_never_called_with_a_github_flag():
    workflow = _load_workflow()
    analysis_node = next(node for node in workflow["nodes"] if node["name"] == "Build Analysis Command")
    code = analysis_node["parameters"]["jsCode"]
    assert "orchestrator" in code
    assert "--approved" not in code
    assert "github" not in code.lower()


def test_workflow_has_no_hardcoded_windows_user_path():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "C:\\Users" not in raw
    assert "C:/Users" not in raw
    assert "/home/" not in raw


def test_workflow_references_project_root_via_environment_variable_only():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "PFA_PROJECT_ROOT" in raw
    # The env var name must appear only as an $env.* lookup, never assigned
    # a literal filesystem path value in the workflow JSON itself.
    assert "\"PFA_PROJECT_ROOT\":" not in raw


def test_workflow_does_not_embed_a_github_token_or_gemini_key():
    raw = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "GITHUB_TOKEN" not in raw
    assert "GEMINI_API_KEY" not in raw
    assert "ghp_" not in raw
