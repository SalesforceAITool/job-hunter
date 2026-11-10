# Engine test (Settings → "Engine test")

This is a quick health check of the Job Hunter engine. Do exactly these steps, nothing else, and do not ask questions:

1. Call `mcp__jobhunt__log_step` with `message: "Engine test: Claude is connected to Job Hunter"` and `level: "success"`.
2. Call the Chrome tool `mcp__claude-in-chrome__tabs_context_mcp` with no arguments (do not create tabs, do not navigate). If the Chrome tools are not in your tool list, load it with ONE `ToolSearch` call `select:mcp__claude-in-chrome__tabs_context_mcp` first, then call it.
3. Reply with one line: `OK jobhunt=<worked|failed> chrome=<worked|failed>`.
