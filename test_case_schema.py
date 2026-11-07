"""The advertised tool schema must accept complete actions and reject missing arguments."""
import copy
import json
import unittest
import repair_workflow as workflow
try:
    import jsonschema
except ImportError:
    jsonschema = None

@unittest.skipUnless(jsonschema, 'Install requirements-test.txt for JSON Schema checks')
class CaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.case=json.loads((workflow.core.ROOT/'cases/checkout-local-fixed.json').read_text())
        self.schema=workflow.contract_schema()
        jsonschema.Draft202012Validator.check_schema(self.schema)
    def test_advertised_advanced_actions_have_complete_arguments(self):
        for step in [
            {'action':'press','selector':'#submit','key':'Enter'},
            {'action':'wait_for_any','conditions':[{'kind':'count','selector':'li','min':2},{'kind':'value','selector':'input','empty':True}]},
            {'action':'wait_for_load_state','state':'domcontentloaded'},
            {'action':'wait_for_timeout','ms':0},
            {'action':'wait_for_value','selector':'input','equals':''},
        ]:
            with self.subTest(step=step):
                case=copy.deepcopy(self.case);case['journey']=[step]
                jsonschema.validate(case,self.schema)
                workflow.core.validate_contract(case)
    def test_missing_or_unusable_action_arguments_are_rejected(self):
        for step in [
            {'action':'press','selector':'#submit'},
            {'action':'wait_for_any'},
            {'action':'wait_for_any','conditions':[{'kind':'value','selector':'input'}]},
            {'action':'wait_for_load_state'},
            {'action':'wait_for_timeout'},
            {'action':'wait_for_timeout','ms':60001},
            {'action':'fill','selector':'#title'},
            {'action':'wait_for_value','selector':'input','empty':False},
        ]:
            with self.subTest(step=step):
                case=copy.deepcopy(self.case);case['journey']=[step]
                with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(case,self.schema)
