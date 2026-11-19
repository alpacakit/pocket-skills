from __future__ import annotations
import copy,json,unittest
from pathlib import Path
import test_reduction_cli as cli
from accounting_wire import accounting_wire_schema, accounting_wire_shape_errors, decode_accounting_output
from reduction_contract import reduction_closure, canonical_sha256
from reduction_jobs import make_job, build_plan, output_reserve_estimate
from scan_artifacts import render_json,sha256_file


def record(identifier,status='verify',role=None):
    r={'observation_id':identifier,'accounting_status':status,'reason':'A synthetic Question-relevant reason','falsifier':None}
    if role is not None:r['role']=role
    return r


def fixture():
    ids=[f'shard-001:{i}' for i in range(1,9)]
    merged=record(ids[1]);merged.pop('accounting_status');merged['role']='supporting'
    return {'schema_version':3,'clusters':[
        {'route':'source_verification','required_verification_depth':'source_context',
         'requested_source_relationships':['replacement_owner','negative_case'],'reason':'Synthetic related decisions',
         'ordinary':[record(ids[2],role='counterevidence'),record(ids[3],role='minority')],
         'merge_sets':[{'canonical':record(ids[0]),'merged':[merged]},
                       {'canonical':record(ids[4]),'merged':[]}]},
        {'route':'summary_only','required_verification_depth':None,'requested_source_relationships':[],
         'reason':'Synthetic summary','ordinary':[record(ids[5],'non_decision_driving','supporting')],
         'merge_sets':[{'canonical':record(ids[6],'non_decision_driving'),'merged':[]}]}],
        'unclustered_records':[{**record(ids[7],'inconclusive'),'falsifier':'A synthetic counterexample'}],
        'unresolved_records':[{'unresolved_id':f'shard-001:unresolved:{i}','disposition':'preserved','reason':'Unresolved'} for i in (1,2)],
        'shard_records':[{'receipt_id':f'shard-00{i}:receipt','disposition':'accounted','reason':'Accounted'} for i in (1,2)]}


class WireTests(unittest.TestCase):
    def test_decoder_is_pure_preserves_protected_roles_and_occurrences(self):
        raw=fixture();before=copy.deepcopy(raw)
        self.assertEqual([],accounting_wire_shape_errors(raw))
        result=decode_accounting_output(raw)
        self.assertEqual(before,raw)
        self.assertEqual(8,len(result['records']))
        rows={r['observation_id']:r for r in result['records']}
        self.assertEqual('shard-001:1',rows['shard-001:2']['canonical_observation_id'])
        self.assertEqual('merged',rows['shard-001:2']['accounting_status'])
        self.assertEqual('counterevidence',rows['shard-001:3']['role'])
        self.assertEqual('minority',rows['shard-001:4']['role'])
        self.assertEqual('supporting',rows['shard-001:6']['role'])
        self.assertIsNone(rows['shard-001:8']['cluster_index'])
        raw['clusters'][0]['ordinary'].append(copy.deepcopy(raw['clusters'][0]['ordinary'][0]))
        self.assertEqual([],accounting_wire_shape_errors(raw))
        self.assertEqual(9,len(decode_accounting_output(raw)['records']))

    def test_shape_has_no_packet_membership_or_count_or_repair_decision(self):
        raw=fixture();raw['clusters'][0]['ordinary'][0]['observation_id']='foreign'
        self.assertEqual([],accounting_wire_shape_errors(raw))
        for key in ('clusters','unclustered_records','unresolved_records','shard_records'):
            value=fixture();value[key]=[]
            self.assertEqual([],accounting_wire_shape_errors(value))
            decode_accounting_output(value)  # total for these relationally invalid inputs
        for kind in ('graph','protected','canonical_role','bool','v2','unknown'):
            value=fixture()
            if kind=='graph':value['clusters'][0]['merge_sets'][0]['merged'][0]['canonical_observation_id']='foreign'
            elif kind=='protected':value['clusters'][0]['merge_sets'][0]['merged'][0]['role']='counterevidence'
            elif kind=='canonical_role':value['clusters'][0]['ordinary'][0]['role']='canonical'
            elif kind=='bool':value['schema_version']=True
            elif kind=='v2':value['schema_version']=2
            else:value['records']=[]
            self.assertTrue(accounting_wire_shape_errors(value),kind)

    def test_nesting_envelope_bounds_valid_serializations_without_reserve_reduction(self):
        maximum_delta=0
        for n in (1,2,8,90,106,137):
            for kind in ('ordinary','canonical','many_clusters','merged','unclustered'):
                raw={'schema_version':3,'clusters':[],'unclustered_records':[],
                     'unresolved_records':[],'shard_records':[]}
                cluster={'route':'summary_only','required_verification_depth':None,'requested_source_relationships':[],
                         'reason':'x','ordinary':[],'merge_sets':[]}
                def r():return {'observation_id':'x','accounting_status':'stale','reason':'x','falsifier':None}
                if kind=='unclustered':raw['unclustered_records']=[r() for _ in range(n)]
                elif kind=='ordinary':cluster['ordinary']=[{**r(),'role':'supporting'} for _ in range(n)];raw['clusters']=[cluster]
                elif kind=='canonical':cluster['merge_sets']=[{'canonical':r(),'merged':[]} for _ in range(n)];raw['clusters']=[cluster]
                elif kind=='many_clusters':raw['clusters']=[{**copy.deepcopy(cluster),'merge_sets':[{'canonical':r(),'merged':[]}]} for _ in range(n)]
                else:
                    child={k:v for k,v in r().items() if k!='accounting_status'}
                    cluster['merge_sets']=[{'canonical':r(),'merged':[{**child,'role':'supporting'} for _ in range(n-1)]}];raw['clusters']=[cluster]
                delta=len(render_json(raw).encode())-len(render_json(decode_accounting_output(raw)).encode())
                maximum_delta=max(maximum_delta,delta)
                self.assertLessEqual(delta,64+128*n,(kind,n,delta))
                self.assertEqual(4096+448*n+96*2+64+128*n,output_reserve_estimate(n,1,1))
        self.assertGreater(maximum_delta,0)


class WireCliTests(unittest.TestCase):
    setUp=cli.ReductionCliTests.setUp
    tearDown=cli.ReductionCliTests.tearDown
    run_cli=cli.ReductionCliTests.run_cli
    make_packet=cli.ReductionCliTests.make_packet
    make_reduction=cli.ReductionCliTests.make_reduction
    run_reducer=cli.ReductionCliTests.run_reducer
    calls=cli.ReductionCliTests.calls

    def wire_plan(self,name='case',value=None):
        plan=self.make_reduction(name,count=8)
        raw=self.base/(name+'-wire.json');raw.write_text(render_json(value if value is not None else fixture()))
        self.env['FAKE_WIRE_OUTPUT']=str(raw)
        return plan

    def test_full_wire_to_public_normal_form_reuse_schema_and_lineage(self):
        plan=self.wire_plan();initial=plan.read_bytes()
        run=self.run_reducer(plan);self.assertEqual(0,run.returncode,run.stderr)
        result=self.run_cli(cli.VALIDATOR,plan);self.assertEqual(0,result.returncode,result.stdout+result.stderr)
        ledger=json.loads((plan.parent/'accounting-ledger.json').read_text());rows={r['observation_id']:r['reduction'] for r in ledger['records']}
        self.assertEqual(8,len(rows));self.assertEqual('shard-001:1',rows['shard-001:2']['canonical_observation_id'])
        self.assertEqual('canonical',rows['shard-001:5']['role'])
        self.assertEqual('counterevidence',rows['shard-001:3']['role'])
        self.assertEqual('supporting',rows['shard-001:6']['role'])
        raw=json.loads((plan.parent/'attempts/batch-001/attempt-0001/raw-result.json').read_text())
        wrapper=json.loads((plan.parent/'results/batch-001.json').read_text())
        self.assertEqual(raw,wrapper['output']);self.assertEqual(3,raw['schema_version']);self.assertNotIn('records',raw)
        self.assertEqual(5,wrapper['schema_version']);self.assertEqual(1,wrapper['job']['accounting_decoder_version'])
        signals=json.loads((plan.parent/'summary.json').read_text())['accounting_structure']
        self.assertEqual({'canonical_records':3,'merged_records':1,'counterevidence_records':1,
                          'minority_records':1,'merge_sets':3,'empty_merge_sets':2},signals)
        p=json.loads(plan.read_text());self.assertEqual(5,p['schema_version'])
        self.assertEqual(canonical_sha256(accounting_wire_schema()),p['contracts']['accounting_output_schema_sha256'])
        self.assertEqual(len(render_json(accounting_wire_schema()).encode()),p['group_capacity']['worst_case']['schema_utf8_bytes'])
        manifest=json.loads((plan.parent/'partition/manifest.json').read_text());self.assertEqual(3,manifest['schema_version'])
        schema=json.loads((plan.parent/'attempts/batch-001/attempt-0001/schema.json').read_text())
        self.assertEqual(accounting_wire_schema(),schema)
        before=self.calls();self.assertEqual(0,self.run_reducer(plan).returncode);self.assertEqual(before,self.calls())
        self.assertEqual(initial,plan.read_bytes())
        p['contracts']['accounting_decoder_version']=99;plan.write_text(render_json(p))
        self.assertNotEqual(0,self.run_reducer(plan).returncode)
        self.assertEqual(1,self.run_cli(cli.VALIDATOR,plan).returncode)
        self.assertEqual(before,self.calls());self.assertFalse((plan.parent/'verification-queue.json').exists())

    def test_relation_failures_remain_shape_valid_charge_cap_and_preserve_raw(self):
        for kind in ('missing','duplicate','foreign','empty_cluster','route','status','unclustered_verify','self_id','relationships'):
            with self.subTest(kind=kind):
                raw=fixture();c=raw['clusters'][0]
                if kind=='missing':c['ordinary'].pop()
                elif kind=='duplicate':c['ordinary'].append(copy.deepcopy(c['ordinary'][0]))
                elif kind=='foreign':c['ordinary'][0]['observation_id']='foreign'
                elif kind=='empty_cluster':raw['clusters'].append({'route':'summary_only','required_verification_depth':None,'requested_source_relationships':[],'reason':'Empty','ordinary':[],'merge_sets':[]})
                elif kind=='route':c.update({'route':'summary_only','required_verification_depth':None,'requested_source_relationships':[]})
                elif kind=='status':c['merge_sets'][0]['canonical']['accounting_status']='blocked'
                elif kind=='unclustered_verify':raw['unclustered_records'][0]['accounting_status']='verify'
                elif kind=='self_id':c['merge_sets'][0]['merged'][0]['observation_id']=c['merge_sets'][0]['canonical']['observation_id']
                else:c['requested_source_relationships']=['negative_case','negative_case']
                self.assertEqual([],accounting_wire_shape_errors(raw))
                plan=self.wire_plan(kind,raw);result=self.run_reducer(plan,retries=1)
                self.assertNotEqual(0,result.returncode)
                for number in (1,2):
                    directory=plan.parent/f'attempts/batch-001/attempt-{number:04d}'
                    receipt=json.loads((directory/'receipt.json').read_text())
                    self.assertEqual('valid',receipt['raw_result_status']);self.assertEqual('complete',receipt['status'])
                    closure=json.loads((directory/'closure.json').read_text())
                    self.assertEqual('semantic_red',closure['status']);self.assertEqual(raw,json.loads((directory/'raw-result.json').read_text()))
                blocked=json.loads((plan.parent/'blocked.json').read_text());self.assertEqual(2,len(blocked['semantic_red_attempts']))
                self.assertFalse((plan.parent/'verification-queue.json').exists())

    def test_accounting_parent_and_decoder_bind_the_persisted_result(self):
        from bound_reduction import load_packet
        from reduction_jobs import context_capacity
        for field in ('parent_result_chain_sha256','accounting_decoder_version'):
            with self.subTest(field=field):
                plan=self.wire_plan(field)
                self.assertEqual(0,self.run_reducer(plan).returncode)
                self.assertEqual(0,self.run_cli(cli.VALIDATOR,plan).returncode)
                value=json.loads(plan.read_text());p,_,c=load_packet(Path(value['bindings']['packet_path']),Path(value['bindings']['context_catalog_path']))
                a=make_job(p,'accounting','replay',context_capacity(c, cli.TEST_MODEL),8,parent_sha256='a'*64)
                b=make_job(p,'accounting','replay',context_capacity(c, cli.TEST_MODEL),8,parent_sha256='b'*64)
                self.assertNotEqual(a.descriptor['parent_result_chain_sha256'],b.descriptor['parent_result_chain_sha256'])
                self.assertEqual(1,a.descriptor['accounting_decoder_version'])
                path=plan.parent/'results/batch-001.json';wrapper=json.loads(path.read_text());wrapper['job'][field]='unbound';path.write_text(render_json(wrapper))
                before=self.calls();self.assertNotEqual(0,self.run_reducer(plan).returncode)
                self.assertEqual(1,self.run_cli(cli.VALIDATOR,plan).returncode)
                self.assertEqual(before,self.calls());self.assertFalse((plan.parent/'summary.json').exists())

    def test_old_plan_is_rejected_without_calls_or_new_artifacts(self):
        plan=self.make_reduction();value=json.loads(plan.read_text());value['schema_version']=4;plan.write_text(render_json(value))
        before={str(p.relative_to(plan.parent)):p.read_bytes() for p in plan.parent.rglob('*') if p.is_file()}
        for script in (cli.RUNNER,cli.VALIDATOR):self.assertNotEqual(0,self.run_cli(script,plan).returncode)
        self.assertEqual(before,{str(p.relative_to(plan.parent)):p.read_bytes() for p in plan.parent.rglob('*') if p.is_file()})
        self.assertEqual([],self.calls())
