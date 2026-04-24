from __future__ import annotations


def onnx_providers(cpu_only: bool = False) -> list[str]:
    if cpu_only:
        return ["CPUExecutionProvider"]
    return ["DmlExecutionProvider", "CPUExecutionProvider"]
