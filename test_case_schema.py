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
    def test_reconciliation_requires_observable_arguments(self):
        case=copy.deepcopy(self.case);case['recovery']={'mode':'reconcile'}
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(case,self.schema)
        with self.assertRaises(ValueError):workflow.core.validate_contract(case)
        case['recovery'].update(route='**/receipt?*',initial_field='intent_key',query_parameter='intent_key',response_id_path='order.id',record_id_field='id')
        jsonschema.validate(case,self.schema);workflow.core.validate_contract(case)
    def test_recovery_campaign_schema_matches_runtime_boundaries(self):
        case=copy.deepcopy(self.case)
        case['journey'].insert(2,{'action':'wait_for_fault'})
        case['recovery_campaign']=['before_delivery','after_commit','restart_after_commit']
        jsonschema.validate(case,self.schema);workflow.core.validate_contract(case)
        case.pop('recovery_campaign')
        case['journey'].insert(3,{'action':'restart_app'})
        jsonschema.validate(case,self.schema);workflow.core.validate_contract(case)
        case['fault']['mode']='prevent_before_delivery'
        jsonschema.validate(case,self.schema);workflow.core.validate_contract(case)
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

    def test_observer_modes_expose_and_require_their_arguments(self):
        case=copy.deepcopy(self.case)
        case['state']={'mode':'http','url':'/state','path':'data.orders','expect':1}
        jsonschema.validate(case,self.schema);workflow.core.validate_contract(case)
        for state in [{'mode':'http','expect':1},{'mode':'http','url':'/state','expect':1},{'mode':'sqlite','expect':1}]:
            with self.subTest(state=state):
                case['state']=state
                with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(case,self.schema)
        case=copy.deepcopy(self.case);case['app']={}
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(case,self.schema)
