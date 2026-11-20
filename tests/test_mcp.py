import asyncio

import pytest


def test_mcp_exposes_the_expected_tools():
    pytest.importorskip("mcp")
    from clipkit.mcp_server import mcp

    tools = asyncio.run(mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "clipkit_init",
        "clipkit_status",
        "clipkit_transcribe",
        "clipkit_analyze",
        "clipkit_candidates",
        "clipkit_get_transcript",
        "clipkit_render",
        "clipkit_inspect",
        "clipkit_eval",
        "clipkit_doctor",
    } <= names
