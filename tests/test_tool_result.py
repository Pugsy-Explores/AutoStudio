from agent_v2.runtime.tool_result import ToolResult


class _ObjResult:
    def __init__(self, success=True, output=None, error=None):
        self.success = success
        self.output = output
        self.error = error


class _ObjMissingFields:
    def __init__(self):
        self.payload = "ignored"


def test_from_any_with_tool_result_returns_same_instance():
    original = ToolResult(success=True, output={"k": 1}, error=None)
    converted = ToolResult.from_any(original)
    assert converted is original


def test_from_any_with_dict_uses_contract_keys():
    converted = ToolResult.from_any({"success": False, "output": {"x": 1}, "error": "boom", "extra": 1})
    assert converted.success is False
    assert converted.output == {"x": 1}
    assert converted.error == "boom"


def test_from_any_with_dict_missing_fields_uses_defaults():
    converted = ToolResult.from_any({"output": "ok"})
    assert converted.success is True
    assert converted.output == "ok"
    assert converted.error is None


def test_from_any_with_object_reads_attributes():
    converted = ToolResult.from_any(_ObjResult(success=False, output=[1, 2], error="bad"))
    assert converted.success is False
    assert converted.output == [1, 2]
    assert converted.error == "bad"


def test_from_any_with_object_missing_fields_uses_defaults():
    converted = ToolResult.from_any(_ObjMissingFields())
    assert converted.success is True
    assert converted.output is None
    assert converted.error is None
