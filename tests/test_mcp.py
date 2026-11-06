"""
Unit tests for the Drift-Watchdog MCP server.
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp_server


def test_mcp_server_registers_all_expected_tools():
    tools = mcp_server.mcp._tool_manager.list_tools()
    names = {t.name for t in tools}
    expected = {
        "get_system_status",
        "get_model_metrics",
        "check_drift",
        "get_drift_events",
        "trigger_retraining",
        "get_promotion_requests",
        "decide_promotion",
        "predict",
        "get_recent_predictions",
        "get_automation_status",
        "control_automation",
    }
    assert expected.issubset(names), f"Missing tools: {expected - names}"


def test_mcp_server_registers_resources():
    resources = mcp_server.mcp._resource_manager.list_resources()
    uris = {str(r.uri) for r in resources}
    assert "drift://status" in uris
    assert "drift://stats" in uris
    assert "drift://promotions/pending" in uris


def test_mcp_server_registers_prompts():
    prompts = mcp_server.mcp._prompt_manager.list_prompts()
    names = {p.name for p in prompts}
    assert "audit_model_drift" in names
    assert "review_promotion_candidate" in names


def test_mcp_decide_promotion_input_validation():
    # Invalid decision should be rejected immediately without making an HTTP request
    res = mcp_server.decide_promotion(1, "invalid_decision")
    assert "error" in res
    assert "decision must be" in res["error"]


def test_mcp_control_automation_input_validation():
    # Invalid action should be rejected immediately without making an HTTP request
    res = mcp_server.control_automation("invalid_action")
    assert "error" in res
    assert "action must be" in res["error"]


if __name__ == "__main__":
    test_mcp_server_registers_all_expected_tools()
    print("PASS: test_mcp_server_registers_all_expected_tools")
    test_mcp_server_registers_resources()
    print("PASS: test_mcp_server_registers_resources")
    test_mcp_server_registers_prompts()
    print("PASS: test_mcp_server_registers_prompts")
    test_mcp_decide_promotion_input_validation()
    print("PASS: test_mcp_decide_promotion_input_validation")
    test_mcp_control_automation_input_validation()
    print("PASS: test_mcp_control_automation_input_validation")
    print("ALL MCP TESTS PASSED SUCCESSFULLY!")
