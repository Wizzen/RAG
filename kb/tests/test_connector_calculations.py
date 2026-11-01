from __future__ import annotations

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase,TestCase

from kb.connector_calculations import evaluate_computed,validate_computed
from kb.connector_runtime import build_batch
from kb.connector_schema import normalize_spec,configuration_error,model_tools
from kb.connector_tools import sample_preview
from kb.connector_agent import run
from kb.models import ConnectorDraft,KnowledgeFeed
from kb.tests.test_connector import spec,sample,FakeModel,call
from kb.tests.test_connector_browser import web_spec


def field(path):return {'field':path}
def op(name,*args,**options):return {'op':name,'args':list(args),**options}


class BusinessCalculationsTests(SimpleTestCase):
    def test_show_count_is_an_integer_from_text_or_real_list(self):
        count=op('count',field('times'),separator=r'[|,;\n]+')
        self.assertEqual(evaluate_computed({'times':'11:00|13:00|16:00'},count),3)
        self.assertIs(type(evaluate_computed({'times':'11:00|13:00|16:00'},count)),int)
        self.assertEqual(evaluate_computed({'times':['11:00','13:00','16:00']},count),3)
        self.assertEqual(evaluate_computed({'times':'11:00|11:00|13:00'},op('count',field('times'),distinct=True)),2)
        self.assertEqual(evaluate_computed({'times':'Starts 11:00 and 13:00'},op('count',field('times'),pattern=r'\d{1,2}:\d{2}')),2)
        self.assertEqual(evaluate_computed({'times':[]},count),0)
        self.assertIsNone(evaluate_computed({},count))
        self.assertIsNone(evaluate_computed({'times':'Not announced'},op('count',field('times'),pattern=r'\d+:\d+')))

    def test_sales_revenue_profit_margin_and_decimal_rounding(self):
        record={'lines':[{'qty':3,'price':'19.90'},{'qty':1,'price':'10.10'}],'revenue':100,'cost':75}
        total=op('sum',op('map',field('lines'),op('multiply',field('qty'),field('price'))))
        self.assertEqual(evaluate_computed(record,total),69.8)
        margin=op('round',op('multiply',100,op('divide',op('subtract',field('revenue'),field('cost')),field('revenue'))),digits=2)
        self.assertEqual(evaluate_computed(record,margin),25)
        self.assertEqual(evaluate_computed({},op('add',.1,.2)),.3)
        self.assertEqual(evaluate_computed({},op('round',2.675,digits=2)),2.68)
        self.assertEqual(evaluate_computed({},op('round',-2.675,digits=2)),-2.68)

    def test_filter_group_and_aggregate_orders_by_region(self):
        record={'orders':[{'region':'north','status':'paid','amount':'12.50'},
            {'region':'north','status':'paid','amount':'7.50'},{'region':'east','status':'paid','amount':30},
            {'region':'east','status':'cancelled','amount':100}]}
        paid=op('filter',field('orders'),op('eq',field('status'),'paid'))
        grouped=op('group_by',paid,path='region',aggregate=op('sum',op('map',field(''),field('amount'))))
        self.assertEqual(evaluate_computed(record,grouped),{'north':20,'east':30})
        amounts=op('map',paid,field('amount'))
        for name,expected in [('sum',50),('average',50/3),('min',7.5),('max',30)]:
            self.assertAlmostEqual(evaluate_computed(record,op(name,amounts)),expected)

    def test_inventory_classification_conditionals_root_and_batch_references(self):
        condition=op('if',op('gte',op('subtract',field('stock'),field('reserved')),field('threshold')),'充足','补货')
        self.assertEqual(evaluate_computed({'stock':10,'reserved':3,'threshold':5},condition),'充足')
        self.assertIsNone(evaluate_computed({'stock':10,'threshold':5},condition))
        lines=op('map',field('lines'),op('multiply',field('qty'),field('$root.factor')))
        self.assertEqual(evaluate_computed({'factor':2,'lines':[{'qty':3}]},lines),[6])
        config=spec(body_fields=[{'label':'批次总数','computed':op('count',field('$records'))}])
        data=sample();data['data']*=2;data['data'][1]={**data['data'][1],'device':'B1'}
        self.assertEqual(build_batch(config,data)['items'][0]['fields']['批次总数'],2)

    def test_time_calculations_and_lazy_fallbacks(self):
        self.assertEqual(evaluate_computed({'open':'22:00','close':'06:00'},op('duration',field('open'),field('close'),format='hhmm',unit='hours')),8)
        self.assertEqual(evaluate_computed({},op('duration','2026-10-01T00:00:00+08:00','2026-10-31T00:00:00+08:00',unit='days')),30)
        self.assertEqual(evaluate_computed({},op('date_part','2026-10-31T08:00:00+08:00',part='date')),'2026-10-31')
        self.assertEqual(evaluate_computed({},op('if',False,op('divide',1,0),4)),4)
        self.assertEqual(evaluate_computed({},op('coalesce',field('missing'),op('count',{'literal':[]}))),0)

    def test_calculation_failures_preserve_actual_inputs_without_fabricated_zero(self):
        config=spec(on_error='raw',body_fields=[{'label':'比例','computed':op('divide',field('temperature'),field('divisor'))}])
        data=sample();data['data'][0]['divisor']=0
        item=build_batch(config,data)['items'][0]
        self.assertIn('除数为0',item['metadata']['collection_warning']);self.assertIn('divisor',item['fields']['比例'])
        self.assertNotEqual(item['fields']['比例'],0)
        data['data'][0]['divisor']=5
        self.assertEqual(build_batch(config,data)['items'][0]['fields']['比例'],5)

    def test_rules_have_no_code_execution_and_are_bounded(self):
        for expression in [{'op':'eval','args':['__import__("os")']},op('count',field('times'),pattern='(x+)+$'),op('match',field('x'),pattern='x',group=1)]:
            with self.assertRaises(ValueError):validate_computed(expression)
        nested=op('number',1)
        for _ in range(15):nested=op('abs',nested)
        with self.assertRaises(ValueError):validate_computed(nested)
        with self.assertRaises(ValueError):evaluate_computed({'rows':list(range(1001))},op('count',field('rows')))

    def test_browser_parameter_misplacement_is_repaired_without_changing_capture(self):
        candidate=web_spec();candidate['browser']['output']['max_records']=50
        fixed=normalize_spec(candidate)
        self.assertEqual(fixed['browser']['max_records'],50);self.assertNotIn('max_records',fixed['browser']['output'])
        self.assertEqual(candidate['browser']['output']['max_records'],50)
        from kb.browser_runtime import validate_plan
        self.assertEqual(validate_plan(candidate['browser'])['max_records'],50)
        candidate['browser']['output']['invented']=1
        with self.assertRaisesMessage(ValueError,'browser.output'):validate_plan(candidate['browser'])
        error=configuration_error(fixed,'网页请求量超过本次采集上限')
        self.assertEqual(error['error_type'],'resource_budget');self.assertIn('html',error['correction'])


class CalculationToolTests(TestCase):
    def test_formula_only_browser_revisions_reuse_full_capture_and_refresh_can_force_fetch(self):
        user=get_user_model().objects.create_user('calculation-reuse',is_staff=True)
        candidate=web_spec();data={'records':[{'id':'ride','name':'Ride','start':'09:00','end':'21:00','source_url':candidate['url']}]}
        parent=ConnectorDraft.objects.create(user=user,prompt='打开 '+candidate['url']+' 采集运营数据',api_url=candidate['url'])
        with patch('kb.agent._get_llm',return_value=FakeModel([call('test_browser_connector',{'spec':candidate})])),patch('kb.connector_browser.collect',return_value=data):
            parent=run(parent.pk)
        self.assertEqual(parent.status,'ready',parent.error)
        candidate['body_fields'].append({'label':'数量','computed':op('count',{'literal':[1,2,3]})})
        child=ConnectorDraft.objects.create(user=user,parent=parent,prompt=parent.prompt,api_url=parent.api_url,feedback='增加数量计算')
        with patch('kb.agent._get_llm',return_value=FakeModel([call('test_browser_connector',{'spec':candidate})])),patch('kb.connector_browser.collect') as collect:
            child=run(child.pk)
        self.assertEqual(child.status,'ready',child.error);collect.assert_not_called();self.assertTrue(child.preview['capture_reused'])
        self.assertEqual(child.preview['preview'][0]['fields']['数量'],3)
        refreshed=ConnectorDraft.objects.create(user=user,parent=child,prompt=parent.prompt,api_url=parent.api_url,feedback='刷新采集')
        with patch('kb.agent._get_llm',return_value=FakeModel([call('test_browser_connector',{'spec':candidate,'refresh':True})])),patch('kb.connector_browser.collect',return_value=data) as collect:
            refreshed=run(refreshed.pk)
        self.assertEqual(refreshed.status,'ready',refreshed.error);collect.assert_called_once();self.assertNotIn('capture_reused',refreshed.preview)

    def test_agent_can_try_calculation_then_publish_numeric_preview_without_extra_fetch(self):
        user=get_user_model().objects.create_user('calculation-admin',is_staff=True)
        data=sample();data['data'][0]['times']='11:00|13:00|16:00'
        expression=op('count',field('times'))
        candidate=spec(body_fields=[{'label':'场次数','computed':expression}])
        job=ConnectorDraft.objects.create(user=user,prompt='采集 '+candidate['url']+'，calculate how many times per day, only a number',sample=data)
        model=FakeModel([call('preview_calculation',{'computed':expression,'record':data['data'][0]}),call('show_data_preview',{'spec':candidate},'preview')])
        with patch('kb.agent._get_llm',return_value=model):result=run(job.pk)
        self.assertEqual(result.status,'ready',result.error);self.assertEqual(result.preview['preview'][0]['fields']['场次数'],3)
        self.assertEqual(KnowledgeFeed.objects.count(),0);self.assertIn('"value_type": "int"',str(model.messages[1]))
        self.assertIn('group_by',str(model.tools['preview_calculation']));self.assertIn('preview_calculation',str(model.messages[0]))
