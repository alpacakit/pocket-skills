from __future__ import annotations
import copy
import hashlib
import json
import fcntl
from pathlib import Path
import unittest
import test_reduction_cli as cli
from reduction_labels import (SubjectPrefix, label_closure, label_output_schema, label_shape_errors,
                              advance_prefix, subject_groups, label_reserve, worst_vocabulary)
from scan_artifacts import render_json, sha256_file

class LabelContractTests(unittest.TestCase):
    def test_label_prompt_does_not_teach_known_witness_answers(self):
        prompt=(cli.SKILL/'templates/label-reducer-prompt.md').read_text().lower()
        # These examples previously disclosed the frozen diagnostic answers.
        # This regression guard is not a semantic-quality evaluation.
        for leaked in ('project archive', 'notification', 'push delivery', 'shelving', 'fictional products'):
            self.assertNotIn(leaked,prompt)

    def output(self):
        return {'schema_version':1,'new_subjects':[{'key':'archive','definition':'Project archiving operation'},
                                                   {'key':'unused','definition':'Unused neutral subject'}],
                'assignments':[{'key':'archive','positions':[0,1]}]}

    def test_fold_reuses_keys_keeps_raw_and_discards_only_valid_unused(self):
        raw=self.output();before=copy.deepcopy(raw)
        first=advance_prefix(SubjectPrefix(),raw,[0,1],4,4,'label-001','a'*64)
        self.assertEqual(before,raw)
        self.assertEqual({'archive'},set(first.subjects))
        second={'schema_version':1,'new_subjects':[], 'assignments':[{'key':'archive','positions':[2,3]}]}
        prefix=advance_prefix(first,second,[2,3],4,4,'label-002','b'*64)
        self.assertEqual([[0,1,2,3]],subject_groups(prefix,4))
        self.assertEqual([0,1],first.subjects['archive']['positions'])
        self.assertNotEqual(first.chain_sha256,prefix.chain_sha256)
        self.assertEqual(1,prefix.statistics[1]['reused_keys'])
        self.assertEqual([['archive','Project archiving operation']],prefix.vocabulary())

    def test_shape_domain_and_cumulative_capacity_are_distinct(self):
        raw=self.output()
        self.assertEqual([],label_shape_errors(raw,2,4))
        for field in ('missing','duplicate','unknown','redeclare','duplicate_new','duplicate_assignment','bytes','unused_bytes'):
            with self.subTest(field=field):
                value=copy.deepcopy(raw)
                if field=='missing':value['assignments'][0]['positions']=[0]
                elif field=='duplicate':value['assignments'][0]['positions']=[0,0]
                elif field=='unknown':value['assignments'][0]['key']='unknown'
                elif field=='redeclare':value['new_subjects'].append({'key':'archive','definition':'Duplicate'})
                elif field=='duplicate_new':value['new_subjects'][1]['key']='archive'
                elif field=='duplicate_assignment':value['assignments']=[{'key':'archive','positions':[0]},{'key':'archive','positions':[1]}]
                elif field=='bytes':value['new_subjects'][0]['definition']='😀'*80
                else:value['new_subjects'][1]['definition']='😀'*80
                # Redeclaration count is a shape failure; all other variants
                # here are schema-shaped but semantically invalid.
                self.assertTrue(label_closure(value,[0,1],4,SubjectPrefix(),4))
        for invalid in (True,4,-1,'0'):
            value=self.output();value['assignments'][0]['positions'][0]=invalid
            self.assertTrue(label_shape_errors(value,2,4))
        first=advance_prefix(SubjectPrefix(),raw,[0,1],4,2,'label-001','a'*64)
        next_output={'schema_version':1,'new_subjects':[], 'assignments':[{'key':'archive','positions':[2]}]}
        self.assertEqual([],label_shape_errors(next_output,1,4))
        self.assertIn('capacity',' '.join(label_closure(next_output,[2],4,first,2)))
        self.assertEqual(1,label_output_schema(1,4)['properties']['assignments']['items']['properties']['positions']['maxItems'])

    def test_serialization_bound_includes_escaped_definition_and_no_sparse_assumption(self):
        definition=worst_vocabulary(1)[0][1]
        self.assertEqual(80,len(definition));self.assertEqual(240,len(definition.encode()))
        self.assertEqual(266,len(json.dumps(definition,ensure_ascii=False).encode())-2)
        reserve=label_reserve(128,1025)
        value={'schema_version':1,'new_subjects':[{'key':k,'definition':d} for k,d in worst_vocabulary(128)],
               'assignments':[{'key':k,'positions':[1024]} for k,d in worst_vocabulary(128)]}
        self.assertLessEqual(len(render_json(value).encode()),reserve['max_serialized_utf8_bytes'])
        self.assertEqual(16384,reserve['reasoning_reserve_tokens'])
        self.assertEqual([],subject_groups(SubjectPrefix(),0))

class LabeledExecutionTests(unittest.TestCase):
    setUp=cli.ReductionCliTests.setUp
    tearDown=cli.ReductionCliTests.tearDown
    run_cli=cli.ReductionCliTests.run_cli
    make_packet=cli.ReductionCliTests.make_packet
    make_reduction=cli.ReductionCliTests.make_reduction
    run_reducer=cli.ReductionCliTests.run_reducer
    calls=cli.ReductionCliTests.calls
    green=cli.ReductionCliTests.green

    def test_three_block_prefix_reuse_unused_raw_and_quote_complete_packing(self):
        groups=[[0,1,129,259],[2,130],[3,131]]+[[i] for i in range(262) if i not in (0,1,129,259,2,130,3,131)]
        plan=self.make_reduction(count=260,groups=groups)
        original=plan.read_bytes()
        run=self.run_reducer(plan,'unused')
        self.assertEqual(0,run.returncode,run.stderr)
        self.assertEqual(original,plan.read_bytes())
        for number in (2,3):
            data=json.loads((plan.parent/f'jobs/label-{number:03d}/input.json').read_text())
            self.assertTrue(all(len(r)==2 for r in data['vocabulary']))
            self.assertNotIn('unused-proposal',[r[0] for r in data['vocabulary']])
            self.assertNotIn('first evidence',json.dumps(data))
        raw=json.loads((plan.parent/'attempts/label-001/attempt-0001/raw-result.json').read_text())
        self.assertIn('unused-proposal',[r['key'] for r in raw['new_subjects']])
        manifest=json.loads((plan.parent/'partition/manifest.json').read_text())
        owner=next(j for j in manifest['jobs'] if 0 in j['positions'])
        self.assertTrue({0,1,129,259}<=set(owner['positions']))
        self.assertIn('parent_result_chain_sha256',owner)
        before=self.calls()
        self.assertEqual(0,self.run_reducer(plan).returncode)
        self.assertEqual(before,self.calls())
        validated=self.run_cli(cli.VALIDATOR,plan)
        self.assertEqual(0,validated.returncode,validated.stdout+validated.stderr)
        summary=json.loads((plan.parent/'summary.json').read_text())
        self.assertEqual(len(before),summary['execution_jobs'])
        self.assertEqual(3,summary['label_jobs'])
        inspection=(plan.parent/'inspection.md').read_text()
        self.assertIn('Labels: 3/3',inspection)
        self.assertIn('Adopted new keys',inspection)
        self.assertNotIn('neutral user-facing subject',inspection)
        self.assertIn('separate keys do not prove',inspection)
        prompt=(plan.parent/'attempts/label-001/attempt-0001/prompt.md').read_text()
        self.assertNotIn('bound is',prompt)
        self.assertNotIn('{{GROUP_LIMIT}}',prompt)

    def test_original_cardinalities_all_quotes_receipts_and_narrow_close_through_cli(self):
        from scan_artifacts import artifact_manifest_sha256
        packet_path,catalog=self.make_packet('fullscale')
        packet=json.loads(packet_path.read_text())
        scan_path=Path(packet['plan_path']);scan=json.loads(scan_path.read_text())
        shard_ids=[f'shard-{i:03d}' for i in range(1,100)]
        scan['shards']=[{'shard_id':sid} for sid in shard_ids]
        scan['execution']['shard_count']=99
        scan_path.write_text(render_json(scan))
        results={}
        for sid in shard_ids:
            path=scan_path.parent/'results'/f'{sid}.json'
            path.write_text(render_json({'shard_id':sid}))
            results[f'results/{sid}.json']=sha256_file(path)
        seed=packet['observations'][0]
        packet['observations']=[{**copy.deepcopy(seed),'observation_id':f'{shard_ids[i%99]}:{i//99+1}',
                                 'title':f'Synthetic subject {i}','claim':f'Synthetic condition {i} relevant to Question'} for i in range(1017)]
        u=packet['unresolved_questions'][0]
        packet['unresolved_questions']=[{**u,'unresolved_id':f'{sid}:unresolved:1','shard_id':sid,'ordinal':1} for sid in shard_ids[:8]]
        packet['shard_receipts']=[{'receipt_id':f'{sid}:receipt','shard_id':sid,'result_sha256':results[f'results/{sid}.json'],
                                  'status':'complete','overflow_state':'unknown' if i==98 else 'false',
                                  'observation_ids':[o['observation_id'] for o in packet['observations'] if o['observation_id'].startswith(sid+':')],
                                  'unresolved_ids':[u['unresolved_id'] for u in packet['unresolved_questions'] if u['shard_id']==sid]}
                                 for i,sid in enumerate(shard_ids)]
        validation_path=Path(packet['validation_path']);validation=json.loads(validation_path.read_text())
        validation.update({'plan_sha256':sha256_file(scan_path),'observations':1017,'unresolved_questions':8,
                           'result_artifacts':results,'results_sha256':artifact_manifest_sha256(results)})
        validation_path.write_text(render_json(validation))
        packet.update({'plan_sha256':sha256_file(scan_path),'results_sha256':validation['results_sha256'],
                       'validated_observation_count':1017,'validated_unresolved_count':8,'validated_shard_count':99})
        packet_path.write_text(render_json(packet))
        created=self.run_cli(cli.PLANNER,*cli.TEST_POLICY_ARGS,packet_path,'--context-catalog',catalog)
        self.assertEqual(0,created.returncode,created.stderr)
        plan=packet_path.parent/'reduction/plan.json'
        original=plan.read_bytes()
        ran=self.run_reducer(plan)
        self.assertEqual(0,ran.returncode,ran.stderr)
        validated=self.run_cli(cli.VALIDATOR,plan)
        self.assertEqual(0,validated.returncode,validated.stdout+validated.stderr)
        summary=json.loads((plan.parent/'summary.json').read_text())
        self.assertEqual((1017,8,99),(summary['observations'],summary['unresolved_questions'],summary['shard_receipts']))
        self.assertEqual(9,summary['label_jobs'])
        self.assertEqual(len(self.calls()),summary['execution_jobs'])
        self.assertEqual(len(self.calls())*100,summary['usage']['input_tokens'])
        self.assertIsNone(summary['resolved_model']);self.assertIsNone(summary['resolved_effort'])
        self.assertEqual(original,plan.read_bytes())
        ledger=json.loads((plan.parent/'accounting-ledger.json').read_text())
        self.assertEqual(1017,len(ledger['records']))
        self.assertTrue(all(r['observation']['evidence'][0]['quote']=='first evidence' for r in ledger['records']))
        queue=json.loads((plan.parent/'verification-queue.json').read_text())['queue']
        self.assertTrue(queue)
        entry=queue[-1];bundle=plan.parent/'verification-inputs'/f"{entry['cluster_id']}.json"
        projected=self.run_cli(cli.PROJECTOR,plan,entry['cluster_id'],'--output',bundle)
        self.assertEqual(0,projected.returncode,projected.stderr)
        self.assertEqual(len(entry['members']),len(json.loads(bundle.read_text())['members']))

        # The WHOLE synthetic queue reaches the unchanged action boundary.
        from test_reduction_contract import action_record
        actions=[]
        for index,item in enumerate(queue):
            path=plan.parent/'verification-inputs'/f"{item['cluster_id']}.json"
            if path!=bundle:
                self.assertEqual(0,self.run_cli(cli.PROJECTOR,plan,item['cluster_id'],'--output',path).returncode)
            action=action_record()
            action.update({'verification_id':f'V{index+1}','cluster_id':item['cluster_id'],
                           'verification_queue_sha256':sha256_file(plan.parent/'verification-queue.json'),
                           'verification_bundle_sha256':sha256_file(path),'source_snapshot_id':packet['snapshot_id'],
                           'required_depth':item['required_verification_depth']})
            actions.append(action)
        path=plan.parent/'synthetic-actions.json';path.write_text(render_json(actions))
        result=self.run_cli(cli.ACTION_VALIDATOR,plan,path)
        self.assertEqual(0,result.returncode,result.stdout+result.stderr)

    def test_invalid_plan_location_cannot_create_locks_or_reports_in_source(self):
        plan=self.make_reduction()
        source=self.base/'case/target'
        wrong=source/'foreign-plan.json';wrong.write_bytes(plan.read_bytes())
        (source/'accounting-ledger.json').write_text('source-owned sentinel')
        before={p.name:p.read_bytes() for p in source.iterdir()}
        for script in (cli.RUNNER,cli.VALIDATOR):
            result=self.run_cli(script,wrong)
            self.assertNotEqual(0,result.returncode)
            self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir()})
        self.assertEqual([],self.calls())

    def test_stale_evidence_revokes_green_publication_through_validator_cli(self):
        cases=('source','source_root_missing','catalog','packet','packet_invalid','packet_missing','scan_plan',
               'packet_sha256','context_catalog_sha256','scan_plan_sha256',
               'scout_validation_sha256','results_sha256','result_artifacts')
        for kind in cases:
            with self.subTest(kind=kind):
                plan=self.green(kind)
                value=json.loads(plan.read_text());bindings=value['bindings']
                immutable={str(p.relative_to(plan.parent)):p.read_bytes()
                           for owner in ('attempts','results','jobs','partition')
                           for p in (plan.parent/owner).rglob('*') if p.is_file()}
                if kind in ('source','source_root_missing'):
                    scan=json.loads(Path(bindings['scan_plan_path']).read_text())
                    path=Path(scan['root'])/'source.txt'
                    if kind=='source_root_missing':path.parent.rename(path.parent.with_name('moved-source'))
                    else:path.write_text(path.read_text()+'changed line\n')
                elif kind in ('catalog','packet','packet_invalid','packet_missing','scan_plan'):
                    key={'catalog':'context_catalog_path','scan_plan':'scan_plan_path'}.get(kind,'packet_path')
                    path=Path(bindings[key])
                    if kind=='packet_missing':path.unlink()
                    elif kind=='packet_invalid':path.write_text('{broken JSON')
                    else:path.write_bytes(path.read_bytes()+b' ')
                else:
                    bindings[kind]={} if kind=='result_artifacts' else '0'*64
                    plan.write_text(render_json(value))
                calls=self.calls()
                result=self.run_cli(cli.VALIDATOR,plan)
                self.assertEqual(1,result.returncode,result.stdout+result.stderr)
                validation=json.loads((plan.parent/'validation.json').read_text())
                self.assertEqual('red',validation['status'])
                self.assertFalse(validation['valid']);self.assertTrue(validation['errors'])
                for name in ('summary.json','accounting-ledger.json','verification-queue.json'):
                    self.assertFalse((plan.parent/name).exists(),name)
                inspection=(plan.parent/'inspection.md').read_text()
                self.assertIn('**Status:** red',inspection)
                self.assertIn(validation['errors'][0],inspection)
                self.assertNotIn('first evidence',inspection)
                self.assertNotIn('pending labels',inspection)
                self.assertEqual(calls,self.calls())
                self.assertEqual(immutable,{name:(plan.parent/name).read_bytes() for name in immutable})

    def test_stale_validation_takes_lock_before_revoking_publication(self):
        plan=self.green()
        source=self.base/'case/target/source.txt'
        source.write_text(source.read_text()+'changed line\n')
        names=('validation.json','summary.json','accounting-ledger.json','verification-queue.json','inspection.md')
        before={name:(plan.parent/name).read_bytes() for name in names}
        with (plan.parent/'runner.lock').open('a+') as handle:
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result=self.run_cli(cli.VALIDATOR,plan)
            self.assertNotEqual(0,result.returncode)
            self.assertIn('already active',result.stderr)
            self.assertEqual(before,{name:(plan.parent/name).read_bytes() for name in names})
        self.assertEqual(1,self.run_cli(cli.VALIDATOR,plan).returncode)
        self.assertFalse((plan.parent/'summary.json').exists())

    def test_wrong_scan_and_symlink_locations_do_not_write_even_when_stale(self):
        def tree(root):
            return {str(p.relative_to(root)):('symlink',str(p.readlink())) if p.is_symlink()
                    else ('file',p.read_bytes()) if p.is_file() else ('directory',)
                    for p in root.rglob('*')}
        for kind in ('fake_output','fake_scan_binding','fake_packet_binding','plan_symlink',
                     'directory_symlink','ancestor_symlink','source_scan'):
            with self.subTest(kind=kind):
                plan=self.make_reduction(kind)
                value=json.loads(plan.read_text())
                fake=self.base/kind/'fake-scan';fake.mkdir()
                wrong=fake/'reduction';wrong.mkdir()
                (wrong/'summary.json').write_text('owned sentinel')
                if kind=='fake_output':
                    path=wrong/'plan.json';path.write_bytes(plan.read_bytes())
                elif kind=='fake_scan_binding':
                    (fake/'plan.json').write_bytes(Path(value['bindings']['scan_plan_path']).read_bytes())
                    value['bindings']['scan_plan_path']=str(fake/'plan.json')
                    path=wrong/'plan.json';path.write_text(render_json(value))
                elif kind=='fake_packet_binding':
                    value['bindings']['packet_path']=str(fake/'review-packet.json')
                    plan.write_text(render_json(value));path=plan
                elif kind=='plan_symlink':
                    path=wrong/'plan.json';path.symlink_to(plan)
                elif kind=='directory_symlink':
                    alias=fake/'reduction-alias';alias.symlink_to(plan.parent,target_is_directory=True)
                    path=alias/'plan.json'
                elif kind=='ancestor_symlink':
                    alias=self.base/kind/'alias';alias.symlink_to(plan.parent.parent,target_is_directory=True)
                    path=alias/'reduction/plan.json'
                else:
                    source=self.base/kind/'target'
                    (source/'plan.json').write_bytes(Path(value['bindings']['scan_plan_path']).read_bytes())
                    for key,name in (('scan_plan_path','plan.json'),('packet_path','review-packet.json'),
                                     ('scout_validation_path','validation.json')):
                        value['bindings'][key]=str(source/name)
                    directory=source/'reduction';directory.mkdir()
                    path=directory/'plan.json';path.write_text(render_json(value))
                    (directory/'validation.json').write_text('source-owned sentinel')
                # Content staleness must not turn location rejection into cleanup.
                Path(value['bindings']['context_catalog_path']).write_text('{}')
                before=tree(self.base)
                for script in (cli.RUNNER,cli.VALIDATOR):
                    result=self.run_cli(script,path)
                    self.assertNotEqual(0,result.returncode)
                    self.assertEqual(before,tree(self.base),result.stdout+result.stderr)
                self.assertEqual([],self.calls())

    def test_result_identity_binds_next_job_even_when_vocabulary_is_unchanged(self):
        from bound_reduction import load_packet
        from reduction_jobs import make_job, context_capacity
        packet_path,catalog_path=self.make_packet()
        packet,_,catalog=load_packet(packet_path,catalog_path)
        raw={'schema_version':1,'new_subjects':[{'key':'shared','definition':'A neutral subject'}],
             'assignments':[{'key':'shared','positions':[0,1]}]}
        a=advance_prefix(SubjectPrefix(),raw,[0,1],4,4,'label-001','a'*64)
        b=advance_prefix(SubjectPrefix(),raw,[0,1],4,4,'label-001','b'*64)
        ja=make_job(packet,'label','label-002',context_capacity(catalog, cli.TEST_MODEL),4,[2,3],[],prefix=a)
        jb=make_job(packet,'label','label-002',context_capacity(catalog, cli.TEST_MODEL),4,[2,3],[],prefix=b)
        self.assertEqual(ja.input_bytes,jb.input_bytes)
        self.assertEqual(ja.descriptor['vocabulary_sha256'],jb.descriptor['vocabulary_sha256'])
        self.assertNotEqual(ja.descriptor['parent_result_chain_sha256'],jb.descriptor['parent_result_chain_sha256'])
        self.assertNotEqual(render_json(ja.descriptor),render_json(jb.descriptor))

    def test_label_label_cap_recovers_all_prefix_reds_before_third_call(self):
        plan=self.make_reduction(count=260)
        first=self.run_reducer(plan,'semantic_red,green,semantic_red',1)
        self.assertNotEqual(0,first.returncode)
        self.assertEqual(['label','label','label'],self.calls())
        (plan.parent/'blocked.json').unlink()
        for p in (plan.parent/'attempts').rglob('closure.json'):p.unlink()
        second=self.run_reducer(plan)
        self.assertNotEqual(0,second.returncode)
        self.assertIn('semantic_blocked',second.stderr)
        self.assertEqual(3,len(self.calls()))
        blocked=json.loads((plan.parent/'blocked.json').read_text())
        self.assertEqual({'label-001','label-002'},{c['job_id'] for c in blocked['semantic_red_attempts']})

    def test_unreachable_future_artifacts_are_closed_before_any_model_call(self):
        for kind in ('job','attempt','result','partition'):
            with self.subTest(kind=kind):
                plan=self.make_reduction(kind,count=260)
                if kind=='job':(plan.parent/'jobs/label-002').mkdir(parents=True)
                elif kind=='attempt':(plan.parent/'attempts/label-002/attempt-0001').mkdir(parents=True)
                elif kind=='result':
                    (plan.parent/'results').mkdir();(plan.parent/'results/label-002.json').write_text('{}')
                else:(plan.parent/'partition').mkdir()
                result=self.run_reducer(plan)
                self.assertNotEqual(0,result.returncode)
                self.assertEqual([],self.calls())

    def test_prefix_materialization_and_previous_result_tampering_are_stale(self):
        for target in ('input','manifest','earlier_result','earlier_definition'):
            with self.subTest(target=target):
                (self.base/'calls.json').unlink(missing_ok=True)
                plan=self.make_reduction(target,count=260)
                initial=self.run_reducer(plan,'green,green,exit7',0)
                self.assertNotEqual(0,initial.returncode)
                self.assertEqual(3,len(self.calls()))
                if target in ('input','manifest'):
                    path=plan.parent/('jobs/label-002/input.json' if target=='input' else 'jobs/label-002/manifest.json')
                    path.write_bytes(path.read_bytes()+b' ')
                elif target=='earlier_result':
                    path=plan.parent/'results/label-001.json';value=json.loads(path.read_text());value['output']['new_subjects'][0]['definition']='changed';path.write_text(render_json(value))
                else:
                    path=plan.parent/'attempts/label-001/attempt-0001/raw-result.json';value=json.loads(path.read_text());value['new_subjects'][0]['definition']='changed';path.write_text(render_json(value))
                rejected=self.run_reducer(plan)
                self.assertNotEqual(0,rejected.returncode)
                self.assertEqual(3,len(self.calls()))
                self.assertFalse((plan.parent/'verification-queue.json').exists())

    def test_utf8_byte_overflow_is_shape_valid_and_counts_toward_two(self):
        plan=self.make_reduction()
        result=self.run_reducer(plan,'byte_oversize',1)
        self.assertNotEqual(0,result.returncode)
        self.assertEqual(2,len(self.calls()))
        receipt=json.loads((plan.parent/'attempts/label-001/attempt-0001/receipt.json').read_text())
        self.assertEqual('valid',receipt['raw_result_status'])
        self.assertTrue((plan.parent/'blocked.json').exists())
