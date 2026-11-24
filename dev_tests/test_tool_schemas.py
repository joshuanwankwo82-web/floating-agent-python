"""tool_schemas.build_tool_schemas: specs and real signatures must agree.

Run: python dev_tests/test_tool_schemas.py
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent_tools  # noqa: E402
import tool_schemas  # noqa: E402


class ToolSchemaTests(unittest.TestCase):
    def test_every_tool_function_has_exactly_one_schema(self):
        names = [s["function"]["name"] for s in agent_tools.TOOL_SCHEMAS]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(agent_tools.TOOL_FUNCTIONS))

    def test_types_and_required_come_from_the_signature(self):
        def sample(query: str, limit: int = 5, exact: bool = False):
            pass
        spec = {"sample": {"description": "d", "params": {
            "query": "q", "limit": "n", "exact": {"description": "e", "enum": [True]}}}}
        original = tool_schemas.TOOL_SPECS
        tool_schemas.TOOL_SPECS = spec
        try:
            [schema] = tool_schemas.build_tool_schemas({"sample": sample})
        finally:
            tool_schemas.TOOL_SPECS = original
        params = schema["function"]["parameters"]
        self.assertEqual(params["required"], ["query"])
        self.assertEqual(params["properties"]["limit"]["type"], "integer")
        self.assertEqual(params["properties"]["exact"]["enum"], [True])

    def test_spec_that_disagrees_with_signature_is_rejected(self):
        def sample(query: str):
            pass
        original = tool_schemas.TOOL_SPECS
        tool_schemas.TOOL_SPECS = {"sample": {"description": "d", "params": {"q": "x"}}}
        try:
            with self.assertRaises(ValueError):
                tool_schemas.build_tool_schemas({"sample": sample})
        finally:
            tool_schemas.TOOL_SPECS = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
