"""Versioned experiment assignment and paired-input identity contracts."""

import copy
import hashlib
import importlib
from pathlib import Path
import unittest

from test_learning import NOW, experiment, experimental_final
from test_lifecycle import profile, review_task
from devsquad.contracts import ContractError
from devsquad.learning import evaluate_experiment, validate_experiment
from devsquad.store import canonical_json


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def execution_digest(selected, adapter=None):
    return digest({'profile_sha256': digest(selected),
                   'adapter': adapter if adapter is not None else {'harness': 'fixture'}})


def spec_v2():
    spec = experiment(Path('/tmp/provenance-project'))
    spec['schema_version'] = 2
    spec['variable'].update({
        'role': 'reviewer',
        'control_profile_sha256': digest(profile('profile-a', 'model-a')),
        'candidate_profile_sha256': digest(profile('profile-b', 'model-b')),
        'control_execution_sha256': execution_digest(profile('profile-a', 'model-a')),
        'candidate_execution_sha256': execution_digest(profile('profile-b', 'model-b')),
    })
    for case in spec['cases']:
        case['input_sha256'] = digest(['input', case['case_id']])
        case['case_sha256'] = digest(['case', case['case_id']])
    return spec


def frozen_snapshot():
    selected = profile('profile-a', 'model-a')
    return {
        'task': review_task(Path('/tmp/provenance-project')),
        'base_oid': 'a' * 40,
        'target_oid': 'b' * 40,
        'workspace': {
            'candidate_sha256': 'c' * 64, 'path': '/tmp/run-a/review',
            'base_oid': 'a' * 40, 'target_oid': 'b' * 40,
        },
        'configs': {'policy_file': {'path': '/tmp/a/policy.json', 'sha256': 'd' * 64}},
        'routing': {
            'policy': {'sha256': 'd' * 64},
            'roles': {'reviewer': {
                'selected': {'profile_id': selected['id'], 'profile': selected,
                             'profile_sha256': digest(selected)},
                'fallbacks': [], 'fallback_mode': 'none',
            }},
            'capacity': {},
        },
    }


class ExperimentProvenanceContractTest(unittest.TestCase):
    def api(self):
        return importlib.import_module('devsquad.experiment_provenance')

    def test_v2_spec_retains_concrete_profiles_and_both_input_identities(self):
        spec = spec_v2()
        self.assertEqual(validate_experiment(spec), spec)
        self.assertEqual(spec['variable']['role'], 'reviewer')

    def test_v2_spec_requires_role_fingerprints_and_exact_case_fields(self):
        changes = [
            ('variable', 'role', 'lead'),
            ('variable', 'role', []),
            ('variable', 'control_profile_sha256', 'short'),
            ('variable', 'candidate_profile_sha256', True),
            ('variable', 'control_execution_sha256', None),
            ('variable', 'candidate_execution_sha256', 'short'),
            ('case', 'case_sha256', None),
            ('case', 'input_sha256', 'A' * 64),
        ]
        for target, field, value in changes:
            with self.subTest(field=field, value=value):
                spec = spec_v2()
                row = spec['variable'] if target == 'variable' else spec['cases'][0]
                row[field] = value
                with self.assertRaises(ContractError):
                    validate_experiment(spec)
        for field in ('role', 'control_profile_sha256', 'candidate_profile_sha256',
                      'control_execution_sha256', 'candidate_execution_sha256'):
            spec = spec_v2()
            del spec['variable'][field]
            with self.assertRaises(ContractError):
                validate_experiment(spec)

    def test_v2_cannot_evaluate_unbound_outcome_labels_as_provenance(self):
        spec = spec_v2()
        chains = {}
        for case in spec['cases']:
            for arm, verdict in (('control', 'failed'), ('candidate', 'succeeded')):
                outcome_id = case[f'{arm}_outcome_id']
                chains[outcome_id] = {
                    'final': experimental_final(outcome_id, verdict), 'late_corrections': [],
                }
        with self.assertRaisesRegex(ContractError, 'provenance'):
            evaluate_experiment(spec, chains, evaluated_at=NOW.isoformat())

    def test_same_corpus_case_cannot_be_relabelled_held_out_by_context_change(self):
        spec = spec_v2()
        spec['cases'][2]['case_sha256'] = spec['cases'][0]['case_sha256']
        self.assertNotEqual(spec['cases'][2]['input_sha256'], spec['cases'][0]['input_sha256'])
        with self.assertRaisesRegex(ContractError, 'case.*unique'):
            validate_experiment(spec)

    def test_assignment_is_exactly_bound_to_spec_case_split_arm_and_project(self):
        api = self.api()
        spec = spec_v2()
        assignment = api.assignment_for(
            spec, 'eval-1', 'candidate', project_common_dir='/tmp/provenance-project/.git',
        )
        self.assertEqual(assignment['spec_sha256'], digest(spec))
        self.assertEqual(assignment['profile_sha256'], spec['variable']['candidate_profile_sha256'])
        self.assertEqual(api.validate_assignment(
            assignment, spec=spec, project_common_dir='/tmp/provenance-project/.git',
        ), assignment)
        for field, replacement in (
            ('schema_version', True), ('spec_sha256', '0' * 64),
            ('experiment_id', 'foreign-experiment'), ('split', 'held_out'),
            ('arm', 'control'), ('role', 'implementer'),
            ('profile_id', 'profile-a'), ('profile_sha256', '0' * 64),
            ('execution_sha256', '0' * 64),
            ('input_sha256', '0' * 64), ('case_sha256', '0' * 64),
            ('project_common_dir', '/tmp/foreign/.git'), ('extra', 1),
        ):
            with self.subTest(field=field):
                invalid = {**assignment, field: replacement}
                with self.assertRaises(ContractError):
                    api.validate_assignment(invalid, spec=spec,
                                            project_common_dir='/tmp/provenance-project/.git')

    def test_legacy_specs_and_unknown_case_or_arm_cannot_make_assignments(self):
        api = self.api()
        for spec, case_id, arm in (
            (experiment(Path('/tmp/provenance-project')), 'eval-1', 'candidate'),
            (spec_v2(), 'missing', 'candidate'),
            (spec_v2(), 'eval-1', []),
        ):
            with self.subTest(case=case_id, arm=arm):
                with self.assertRaises(ContractError):
                    api.assignment_for(spec, case_id, arm,
                                       project_common_dir='/tmp/provenance-project/.git')

    def test_incidental_paths_observations_and_tested_profile_do_not_change_inputs(self):
        api = self.api()
        before = frozen_snapshot()
        changed = copy.deepcopy(before)
        changed['task']['project']['repo_path'] = '/tmp/other-worktree'
        changed['task']['project']['base_ref'] = 'renamed-base'
        changed['task']['origin'] = {'surface': 'another-host', 'session_ref': 'session-b'}
        changed['workspace']['path'] = '/tmp/run-b/review'
        changed['configs']['policy_file']['path'] = '/tmp/b/policy.json'
        changed['routing']['capacity'] = {'irrelevant': 'runtime observation'}
        candidate = profile('profile-b', 'model-b')
        changed['routing']['roles']['reviewer']['selected'] = {
            'profile_id': candidate['id'], 'profile': candidate,
            'profile_sha256': digest(candidate),
        }
        # Assignment is deliberately excluded: its spec hash includes input hashes.
        changed['experiment_assignment'] = {'not': 'fingerprint input'}
        self.assertEqual(api.paired_input_identity(before, role='reviewer', package_digest='e' * 64),
                         api.paired_input_identity(changed, role='reviewer', package_digest='e' * 64))

    def test_runtime_policy_and_peer_changes_affect_pair_not_corpus_identity(self):
        api = self.api()
        before = frozen_snapshot()
        base = api.paired_input_identity(before, role='reviewer', package_digest='e' * 64)
        for kind in ('runtime', 'policy', 'peer'):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(before)
                package = 'e' * 64
                if kind == 'runtime':
                    package = 'f' * 64
                elif kind == 'policy':
                    changed['configs']['policy_file']['sha256'] = 'f' * 64
                    changed['routing']['policy']['sha256'] = 'f' * 64
                else:
                    changed['task']['lead']['mode'] = 'headless'
                    peer = profile('lead', 'lead-model')
                    changed['routing']['roles']['lead'] = {
                        'selected': {'profile_id': 'lead', 'profile': peer,
                                     'profile_sha256': digest(peer)}, 'fallbacks': [],
                        'fallback_mode': 'none',
                    }
                after = api.paired_input_identity(changed, role='reviewer', package_digest=package)
                self.assertEqual(base['case_sha256'], after['case_sha256'])
                self.assertNotEqual(base['input_sha256'], after['input_sha256'])

    def test_tested_role_fallback_policy_is_not_the_experimental_variable(self):
        api = self.api()
        before = frozen_snapshot()
        base = api.paired_input_identity(before, role='reviewer', package_digest='e' * 64)
        changed = copy.deepcopy(before)
        changed['routing']['roles']['reviewer']['fallback_mode'] = 'policy'
        after = api.paired_input_identity(changed, role='reviewer', package_digest='e' * 64)
        self.assertEqual(base['case_sha256'], after['case_sha256'])
        self.assertNotEqual(base['input_sha256'], after['input_sha256'])

    def test_task_check_and_candidate_changes_cannot_share_an_input_identity(self):
        api = self.api()
        before = frozen_snapshot()
        base = api.paired_input_identity(before, role='reviewer', package_digest='e' * 64)
        for kind in ('goal', 'check', 'candidate', 'scope'):
            with self.subTest(kind=kind):
                changed = copy.deepcopy(before)
                if kind == 'goal':
                    changed['task']['goal'] = 'A different bounded task.'
                elif kind == 'check':
                    changed['task']['checks'] = [{
                        'id': 'required', 'argv': ['true'], 'cwd': '.',
                        'timeout_seconds': 1, 'required_to_pass': True,
                    }]
                elif kind == 'candidate':
                    changed['workspace']['candidate_sha256'] = 'f' * 64
                else:
                    changed['task']['scope']['read_paths'] = ['different']
                after = api.paired_input_identity(changed, role='reviewer', package_digest='e' * 64)
                self.assertNotEqual(base['case_sha256'], after['case_sha256'])
                self.assertNotEqual(base['input_sha256'], after['input_sha256'])

    def test_missing_or_inconsistent_frozen_inputs_fail_closed(self):
        api = self.api()
        for kind in ('policy', 'role', 'profile', 'candidate', 'package'):
            with self.subTest(kind=kind):
                changed = frozen_snapshot()
                package = 'e' * 64
                if kind == 'policy':
                    changed['routing']['policy']['sha256'] = '0' * 64
                elif kind == 'role':
                    changed['routing']['roles'] = {}
                elif kind == 'profile':
                    changed['routing']['roles']['reviewer']['selected']['profile_sha256'] = '0' * 64
                elif kind == 'candidate':
                    del changed['workspace']
                else:
                    package = None
                with self.assertRaises(ContractError):
                    api.paired_input_identity(changed, role='reviewer', package_digest=package)

    def test_missing_tested_native_adapter_fails_closed(self):
        snapshot = frozen_snapshot()
        selected = snapshot['routing']['roles']['reviewer']['selected']
        selected['profile']['harness'] = 'codex'
        selected['profile_sha256'] = digest(selected['profile'])
        with self.assertRaisesRegex(ContractError, 'adapter'):
            self.api().paired_input_identity(snapshot, role='reviewer', package_digest='e' * 64)


if __name__ == '__main__':
    unittest.main()
