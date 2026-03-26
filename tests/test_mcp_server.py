"""Tests for the MCP server definition.

Verifies that the MCP server is properly configured with all expected
tools, resources, and prompts.
"""

import pytest


def test_mcp_server_imports():
    """MCP server module imports without error."""
    from src.mcp_server import mcp
    assert mcp.name == "Commute Tracker"


def test_mcp_has_tools():
    """MCP server has all expected tools registered."""
    from src.mcp_server import mcp
    tool_names = {name for name in mcp._tool_manager._tools}
    expected = {
        "query_commute_data",
        "add_segment_label",
        "add_segment_labels_bulk",
        "count_raw_records",
        "rebuild_derived_data",
        "train_ml_model",
        "evaluate_classifier",
        "analyze_segment",
        "review_commute_labels",
        "review_recent_labels",
        "apply_label_corrections",
    }
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"


def test_mcp_has_resources():
    """MCP server has resource templates and static resources registered."""
    from src.mcp_server import mcp
    resource_count = len(mcp._resource_manager._resources) + len(mcp._resource_manager._templates)
    # We have 5 static resources + 6 templates = 11 total
    assert resource_count >= 10, f"Expected at least 10 resources, got {resource_count}"


def test_mcp_has_prompts():
    """MCP server has prompts registered."""
    from src.mcp_server import mcp
    prompt_names = set(mcp._prompt_manager._prompts.keys())
    expected = {
        "analyze_commute",
        "optimize_departure",
        "review_classifications",
        "weekly_report",
    }
    assert expected.issubset(prompt_names), f"Missing prompts: {expected - prompt_names}"


def test_mcp_stateless_config():
    """MCP server is configured for stateless HTTP."""
    from src.mcp_server import mcp
    # The server should have stateless_http and json_response set
    assert mcp.settings.stateless_http is True
    assert mcp.settings.json_response is True
