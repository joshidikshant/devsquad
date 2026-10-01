"""Prelaunch assignment persistence, not a claim of public trial execution."""

import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from test_experiment_provenance import frozen_snapshot, spec_v2, digest, execution_digest
from test_learning import experiment
from test_lifecycle import profile
from devsquad.contracts import ContractError
from devsquad.experiment_provenance import assignment_for, paired_input_identity
from devsquad.store import ConflictError, Store, canonical_json, git_common_dir


class ExperimentAssignmentStoreTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='devsquad-assignment-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.root / 'repo'
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        self.store = Store(self.root / 'state.sqlite3', self.root / 'artifacts')
        self.addCleanup(self.store.close)
        self.common = str(git_common_dir(self.repo))
        self.snapshot = frozen_snapshot()
        self.snapshot['task']['project']['repo_path'] = str(self.repo)
        self.spec = spec_v2()
        self.spec['project_path'] = str(self.repo)
        self.spec['cases'][0].update(paired_input_identity(
            self.snapshot, role='reviewer', package_digest='e' * 64,
        ))

    def snapshot_for(self, arm='control', *, spec=None):
        spec = spec or self.spec
        snapshot = copy.deepcopy(self.snapshot)
        if arm == 'candidate':
            candidate = profile('profile-b', 'model-b')
            snapshot['routing']['roles']['reviewer']['selected'] = {
                'profile_id': candidate['id'], 'profile': candidate,
                'profile_sha256': digest(candidate),
            }
        snapshot['experiment_spec'] = copy.deepcopy(spec)
        snapshot['experiment_assignment'] = assignment_for(
            spec, 'eval-1', arm, project_common_dir=self.common,
        )
        return snapshot

    def claim(self, key):
        return self.store.claim_start(self.repo, key, {'task': self.snapshot['task']}, 'owner')

    def prepare(self, claim, snapshot):
        return self.store.complete_preparation(
            claim.run_id, claim.fencing_token, snapshot, package_digest='e' * 64,
        )

    def test_assignment_freezes_before_attempts_with_preparation_fence(self):
        claim = self.claim('first')
        snapshot = self.snapshot_for()
        version = self.prepare(claim, snapshot)
        row = self.store.connection.execute(
            'SELECT * FROM experiment_assignments WHERE run_id=?', (claim.run_id,),
        ).fetchone()
        self.assertEqual(json.loads(row['assignment_json']), snapshot['experiment_assignment'])
        self.assertEqual(json.loads(row['snapshot_json']), snapshot)
        self.assertEqual(row['assignment_sha256'], digest(snapshot['experiment_assignment']))
        self.assertEqual(row['preparation_fencing_token'], claim.fencing_token)
        self.assertEqual(row['frozen_run_version'], version)
        self.assertEqual(self.store.connection.execute(
            'SELECT COUNT(*) FROM attempts WHERE run_id=?', (claim.run_id,),
        ).fetchone()[0], 0)
        # Later mutable workflow snapshots cannot rewrite the original witness.
        self.store.connection.execute('UPDATE runs SET mutable_snapshot=? WHERE id=?',
                                      (canonical_json({'altered': True}), claim.run_id))
        self.assertEqual(self.store.connection.execute(
            'SELECT snapshot_json FROM experiment_assignments WHERE run_id=?', (claim.run_id,),
        ).fetchone()[0], canonical_json(snapshot))

    def test_distinct_arms_share_one_predeclared_spec(self):
        for arm in ('control', 'candidate'):
            self.prepare(self.claim(arm), self.snapshot_for(arm))
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_specs').fetchone()[0], 1)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_assignments').fetchone()[0], 2)

    def test_invalid_assignment_rolls_back_spec_and_preparation_atomically(self):
        for mutation in ('input', 'profile', 'project', 'missing_spec', 'digest'):
            with self.subTest(mutation=mutation):
                claim = self.claim(mutation)
                snapshot = self.snapshot_for()
                if mutation == 'input':
                    snapshot['task']['goal'] = 'A different goal.'
                elif mutation == 'profile':
                    snapshot['experiment_assignment'] = assignment_for(
                        self.spec, 'eval-1', 'candidate', project_common_dir=self.common,
                    )
                elif mutation == 'project':
                    snapshot['experiment_assignment']['project_common_dir'] = '/tmp/foreign/.git'
                elif mutation == 'missing_spec':
                    del snapshot['experiment_spec']
                else:
                    snapshot['experiment_assignment']['spec_sha256'] = '0' * 64
                with self.assertRaises(ContractError):
                    self.prepare(claim, snapshot)
                self.assertEqual(self.store.run(claim.run_id)['phase'], 'preparing')
                self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_specs').fetchone()[0], 0)
                self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_assignments').fetchone()[0], 0)

    def test_duplicate_arm_cannot_acquire_a_second_run(self):
        first = self.claim('first')
        self.prepare(first, self.snapshot_for())
        second = self.claim('second')
        with self.assertRaisesRegex(ConflictError, 'already assigned'):
            self.prepare(second, self.snapshot_for())
        self.assertEqual(self.store.run(second.run_id)['phase'], 'preparing')
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_assignments').fetchone()[0], 1)

    def test_changed_spec_and_stale_preparation_cannot_rebind(self):
        first = self.claim('first')
        self.prepare(first, self.snapshot_for())
        with self.assertRaisesRegex(ConflictError, 'stale'):
            self.prepare(first, self.snapshot_for('candidate'))
        changed = copy.deepcopy(self.spec)
        changed['hypothesis'] = 'Post-hoc replacement.'
        second = self.claim('candidate')
        with self.assertRaisesRegex(ConflictError, 'frozen differently'):
            self.prepare(second, self.snapshot_for('candidate', spec=changed))
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_assignments').fetchone()[0], 1)

    def test_plain_preparation_creates_no_experiment_records(self):
        claim = self.claim('plain')
        self.prepare(claim, self.snapshot)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_specs').fetchone()[0], 0)
        with self.assertRaisesRegex(ConflictError, 'stale'):
            self.prepare(claim, self.snapshot_for())

    def test_tested_native_version_and_binary_must_match_predeclared_execution(self):
        snapshot = copy.deepcopy(self.snapshot)
        selected = snapshot['routing']['roles']['reviewer']['selected']
        selected['profile']['harness'] = 'codex'
        selected['profile_sha256'] = digest(selected['profile'])
        adapter = {'harness': 'codex', 'harness_version': 'fixture-version-1',
                   'transport': 'native_protocol', 'model_provider': 'openai',
                   'binary_sha256': '1' * 64}
        snapshot['review_adapters'] = {'profile-a': adapter}
        spec = copy.deepcopy(self.spec)
        spec['variable']['control_profile_sha256'] = selected['profile_sha256']
        spec['variable']['control_execution_sha256'] = execution_digest(selected['profile'], adapter)
        snapshot['experiment_spec'] = spec
        snapshot['experiment_assignment'] = assignment_for(
            spec, 'eval-1', 'control', project_common_dir=self.common,
        )
        for field, replacement in (('harness_version', 'fixture-version-2'),
                                   ('binary_sha256', '2' * 64)):
            changed = copy.deepcopy(snapshot)
            changed['review_adapters']['profile-a'][field] = replacement
            with self.subTest(field=field):
                with self.assertRaisesRegex(ContractError, 'native execution'):
                    self.prepare(self.claim(field), changed)
        # Correct explicit native context is accepted; no CLI is executed.
        self.prepare(self.claim('native-context'), snapshot)
        self.assertEqual(self.store.connection.execute('SELECT COUNT(*) FROM experiment_assignments').fetchone()[0], 1)

    def test_schema_13_migration_preserves_legacy_evaluation_bytes(self):
        database = self.root / 'old.sqlite3'
        connection = sqlite3.connect(database)
        migrations = Path(__file__).resolve().parents[2] / 'plugin/core/src/devsquad/migrations'
        for path in sorted(migrations.glob('*.sql')):
            version = int(path.name.split('_', 1)[0])
            if version > 13:
                continue
            connection.executescript(path.read_text())
            connection.execute('INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)', (version, 'fixture'))
        spec = canonical_json(experiment(self.repo))
        evaluation = canonical_json({'schema_version': 1, 'historical': True})
        connection.execute(
            'INSERT INTO experiments(experiment_id,project_path,spec_json,spec_sha256,evaluation_json,'
            'evaluation_sha256,verdict,recorded_at) VALUES(?,?,?,?,?,?,?,?)',
            ('old', str(self.repo), spec, hashlib.sha256(spec.encode()).hexdigest(),
             evaluation, hashlib.sha256(evaluation.encode()).hexdigest(), 'no_change', 'fixture'),
        )
        connection.commit()
        connection.close()
        upgraded = Store(database, self.root / 'old-artifacts')
        self.addCleanup(upgraded.close)
        saved = upgraded.connection.execute('SELECT * FROM experiments').fetchone()
        self.assertEqual(saved['spec_json'], spec)
        self.assertEqual(saved['evaluation_json'], evaluation)
        self.assertEqual(upgraded.connection.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0], 14)
        self.assertEqual(upgraded.connection.execute('SELECT COUNT(*) FROM experiment_assignments').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
