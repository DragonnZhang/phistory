# Auxiliary-only runtime attempts

These original Linux artifacts are retained for diagnosis and do not count as completed captures of the registered primary surfaces. Raw traces and original attempt metadata are unchanged.

- `agent-zero/.../context-api`: only the native chat-naming utility request was emitted. The main Agent 0 loop failed before its model request because the source/development topology had no RFC password/service.
- `hive/.../queen-api`: only the native queen-routing classifier request was emitted. Session creation prepared the Queen and tools but the actual Queen chat turn had not been captured.

The corresponding primary integrations require an explicit main-model or tool-bearing-request check before publication under `captures/`. This prevents helper-only startup traffic from being reported as the intended system prompt/toolset.
