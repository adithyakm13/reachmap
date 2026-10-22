"""The MCP server (M14): `reachmap mcp` lets a model client read reports and record notes beside them, over standard input and output.

    server   the stdio loop: newline-delimited JSON-RPC 2.0, `initialize`, `ping`, `tools/list`, `tools/call`, errors for anything else
    tools    the seven tools (read and note only) and their strict arguments
    fence    how report content reaches a model: fenced, with a random id, labelled untrusted

No SDK, no socket, standard library only. The notes themselves, their stamps, their staleness and the check of a proposed file are in
`reachmap.notes`; the Model notes section of the reports is rendered by `reachmap.report`.
"""
