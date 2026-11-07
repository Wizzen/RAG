from __future__ import annotations

"""采集历史、模式切换和业务数据归类的回归检查。"""

import importlib
import json
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from kb.connector_runtime import validate_spec
from kb.feeds import claim, execute
from kb.models import ConnectorDraft, KnowledgeBase, KnowledgeFeed, TrackerRow
from kb.tests.test_connector import spec
from kb.tests.test_feeds import batch


class FeedHistoryTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('history-admin',is_staff=True)
        self.kb=KnowledgeBase.objects.create(name='运营时间',slug='history-test',is_folder=True,category='business',created_by=self.user)
        self.feed=KnowledgeFeed.objects.create(kb=self.kb,name='运营时间',mode='push',created_by=self.user)

    def publish(self,payload=None,key=''):
        run,fresh=claim(self.feed.pk,trigger='push',payload=payload or batch(),request_key=key)
        return execute(run.pk) if fresh else run

    def mode(self,mode):
        self.feed.write_mode=mode;self.feed.save(update_fields=['write_mode'])

    def test_default_append_keeps_identical_daily_snapshots_and_collection_times(self):
        self.assertEqual(self.feed.write_mode,'append')
        first=self.publish()
        with patch('kb.feeds.timezone.now',return_value=timezone.now()+timedelta(days=1)):
            second=self.publish()
        self.assertEqual(first.counts['created'],1);self.assertEqual(second.counts['created'],1)
        rows=list(self.feed.tracker_rows.order_by('created_at'))
        self.assertEqual(len(rows),2);self.assertNotEqual(rows[0].feed_run_id,rows[1].feed_run_id)
        self.assertEqual(rows[0].external_id,rows[1].external_id)
        self.assertNotEqual(rows[0].values['采集时间'],rows[1].values['采集时间'])
        self.assertEqual(rows[0].values['来源ID'],'report-1')
        self.assertEqual(self.feed.records.count(),1)

    def test_old_source_timestamp_can_be_historically_observed_without_overwriting(self):
        newer=batch('New');newer['items'][0]['updated_at']='2027-01-03T08:00:00Z';self.publish(newer)
        older=batch('Old');older['items'][0]['updated_at']='2027-01-02T08:00:00Z';run=self.publish(older)
        self.assertEqual(run.counts['created'],1);self.assertEqual(run.counts['stale'],0)
        self.assertEqual(set(self.feed.tracker_rows.values_list('values__内容',flat=True)),{'New','Old'})

    def test_same_batch_key_is_idempotent_and_new_batch_retains_next_observation(self):
        first=self.publish(key='delivery-1');second=self.publish(key='delivery-1')
        self.assertEqual(first.pk,second.pk);self.assertEqual(self.feed.tracker_rows.count(),1)
        self.publish(key='delivery-2');self.assertEqual(self.feed.tracker_rows.count(),2)
        row=self.feed.tracker_rows.first()
        with self.assertRaises(IntegrityError),transaction.atomic():
            TrackerRow.objects.create(tracker=row.tracker,feed=self.feed,external_id=row.external_id,feed_run=row.feed_run)

    def test_switch_modes_keeps_all_snapshots_and_only_updates_current_row(self):
        self.mode('upsert');self.publish(batch('Original current'))
        self.mode('append');self.publish(batch('Historical one'));self.publish(batch('Historical two'))
        snapshots=deepcopy(list(self.feed.tracker_rows.exclude(feed_run=None).values('pk','values')))
        self.mode('upsert');self.publish(batch('Latest current'));self.publish(batch('Latest current'))
        self.assertEqual(self.feed.tracker_rows.count(),3)
        self.assertEqual(self.feed.tracker_rows.get(feed_run=None).values['内容'],'Latest current')
        self.assertEqual(list(self.feed.tracker_rows.exclude(feed_run=None).values('pk','values')),snapshots)
        self.mode('append');self.publish(batch('Next history'));self.assertEqual(self.feed.tracker_rows.count(),4)

    def test_explicit_deletion_never_erases_historical_snapshots_in_either_mode(self):
        self.mode('upsert');self.publish()
        self.mode('append');self.publish()
        delete={'schema':'fos-rag-feed/v1','items':[],'deleted':['report-1']}
        self.publish(delete);self.assertEqual(self.feed.tracker_rows.count(),2)
        self.publish();self.mode('upsert');self.publish();self.publish(delete)
        self.assertEqual(self.feed.tracker_rows.count(),2)
        self.assertFalse(self.feed.tracker_rows.filter(feed_run=None).exists())

    def test_failed_snapshot_batch_rolls_back_and_same_delivery_can_retry(self):
        self.publish(batch('Previously saved'))
        payload=batch('New snapshot',cursor='next')
        payload['items'].append({'id':'other','title':'Other','content':'other'})
        from kb.feeds import _publish_row
        calls=[0]
        def publish_then_fail(*args):
            calls[0]+=1
            if calls[0]==2:raise ValueError('simulated second-row failure')
            return _publish_row(*args)
        with patch('kb.feeds._publish_row',side_effect=publish_then_fail):failed=self.publish(payload,key='retry')
        self.assertEqual(failed.status,'failed');self.assertEqual(self.feed.tracker_rows.count(),1)
        self.feed.refresh_from_db();self.assertEqual(self.feed.cursor,'')
        succeeded=self.publish(payload,key='retry')
        self.assertEqual(succeeded.status,'completed');self.assertEqual(self.feed.tracker_rows.count(),3)

    def test_missing_items_and_other_sources_are_preserved(self):
        self.publish();self.publish({'schema':'fos-rag-feed/v1','items':[]})
        other=KnowledgeFeed.objects.create(kb=self.kb,name='Other',mode='push',created_by=self.user)
        run,_=claim(other.pk,trigger='push',payload=batch());execute(run.pk)
        self.assertEqual(self.kb.tracker.rows.count(),2)
        self.assertEqual(self.feed.tracker_rows.count(),1)

    def test_delete_source_cascades_history_without_restrict_error(self):
        self.publish();self.feed.delete();self.assertFalse(self.kb.tracker.rows.exists())


class FeedHistoryViewsTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('history-manager',is_staff=True)
        self.client.force_login(self.user)

    def action(self,data):
        return self.client.post(reverse('kb:feed_action'),json.dumps(data),content_type='application/json',HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test_new_source_defaults_business_append_and_is_absent_from_manual_section(self):
        response=self.action({'action':'create','name':'自动运营时间','kb_name':'自动运营时间','mode':'push'})
        self.assertEqual(response.status_code,200,response.content)
        feed=KnowledgeFeed.objects.get(pk=response.json()['feed']['id'])
        self.assertEqual(feed.write_mode,'append');self.assertEqual(feed.kb.category,'business')
        page=self.client.get(reverse('kb:manage_list'))
        self.assertEqual([kb.pk for kb in page.context['business_kbs']],[feed.kb_id])
        self.assertNotIn(feed.kb,list(page.context['kbs']));self.assertContains(page,'data-business-slug="'+feed.kb.slug+'"')

    def test_business_card_offers_delete_and_it_removes_feed_rows_and_tracker(self):
        """业务数据卡片带删除入口；删除级联清掉采集库、自动更新任务与追踪记录。"""
        response=self.action({'action':'create','name':'待删除运营库','kb_name':'待删除运营库','mode':'push'})
        feed=KnowledgeFeed.objects.get(pk=response.json()['feed']['id'])
        run,fresh=claim(feed.pk,trigger='push',payload=batch());execute(run.pk)
        self.assertEqual(TrackerRow.objects.filter(feed_id=feed.pk).count(),1)
        page=self.client.get(reverse('kb:manage_list'))
        self.assertContains(page,'action="/kb/manage/'+feed.kb.slug+'/delete/"')
        result=self.client.post(reverse('kb:manage_delete',args=[feed.kb.slug]))
        self.assertEqual(result.status_code,302)
        self.assertFalse(KnowledgeBase.objects.filter(pk=feed.kb_id).exists())
        self.assertFalse(KnowledgeFeed.objects.filter(pk=feed.pk).exists())
        self.assertEqual(TrackerRow.objects.filter(feed_id=feed.pk).count(),0)

    def test_user_can_override_agent_write_mode_on_approval(self):
        job=ConnectorDraft.objects.create(user=self.user,prompt='运营时间 '+spec()['url'],status='ready',spec=spec(write_mode='upsert'))
        with patch('kb.collector_api.start_feed'):
            result=self.client.post(reverse('kb:connector_enable',args=[job.pk]),json.dumps({'write_mode':'append'}),content_type='application/json')
        self.assertEqual(result.status_code,200,result.content)
        job.refresh_from_db();self.assertEqual(job.feed.write_mode,'append')
        self.assertEqual(job.spec['write_mode'],'append');self.assertEqual(job.feed.adapter_spec['write_mode'],'append')
        self.assertEqual(job.kb.category,'business')

    def test_mode_edits_sync_script_and_saved_conversation_without_removing_rows(self):
        kb=KnowledgeBase.objects.create(name='业务',slug='business-mode',is_folder=True,category='business')
        feed=KnowledgeFeed.objects.create(kb=kb,name='Test',mode='pull',url=spec()['url'],adapter_spec=spec(),created_by=self.user)
        job=ConnectorDraft.objects.create(user=self.user,kb=kb,feed=feed,status='enabled',spec=spec())
        data={'action':'update','id':str(feed.pk),'name':feed.name,'mode':'pull','url':feed.url,'write_mode':'upsert'}
        result=self.action(data);self.assertEqual(result.status_code,200,result.content)
        feed.refresh_from_db();job.refresh_from_db()
        self.assertEqual(feed.adapter_spec['write_mode'],'upsert');self.assertEqual(job.spec['write_mode'],'upsert')
        self.assertEqual(result.json()['feed']['write_mode'],'upsert')
        data.pop('write_mode');self.action(data);feed.refresh_from_db();self.assertEqual(feed.write_mode,'upsert')
        self.assertEqual(self.action({**data,'write_mode':'invalid'}).status_code,400)

    def test_existing_auto_libraries_are_classified_without_moving_user_selected_manuals(self):
        auto=KnowledgeBase.objects.create(name='Auto',slug='auto-existing',is_folder=True)
        manual=KnowledgeBase.objects.create(name='Manual',slug='manual-existing',is_folder=True)
        for kb in (auto,manual):KnowledgeFeed.objects.create(kb=kb,name='Feed',adapter_spec=spec(),created_by=self.user)
        migration=importlib.import_module('kb.migrations.0043_feed_history_modes')
        migration.classify_existing(apps,None)
        auto.refresh_from_db();manual.refresh_from_db()
        self.assertEqual(auto.category,'business');self.assertEqual(manual.category,'manual')
        self.assertTrue(all(feed.adapter_spec['write_mode']=='append' for feed in KnowledgeFeed.objects.all()))

    def test_department_manager_can_see_own_business_library_only(self):
        from accounts.models import UserProfile
        manager=get_user_model().objects.create_user('business-department-manager')
        UserProfile.objects.update_or_create(user=manager,defaults={'department':'机械','role':UserProfile.Role.DEPT_ADMIN})
        own=KnowledgeBase.objects.create(name='Own business',slug='business-own',is_folder=True,category='business',department='机械')
        KnowledgeBase.objects.create(name='Other business',slug='business-other',is_folder=True,category='business',department='电气')
        self.client.force_login(manager);page=self.client.get(reverse('kb:manage_list'))
        self.assertEqual([kb.pk for kb in page.context['business_kbs']],[own.pk]);self.assertContains(page,'Own business')
        self.assertNotContains(page,'Other business')

    def test_runtime_accepts_only_supported_modes(self):
        for mode in ('append','upsert'):validate_spec(spec(write_mode=mode))
        with self.assertRaisesMessage(ValueError,'write_mode'):validate_spec(spec(write_mode='replace_all'))
