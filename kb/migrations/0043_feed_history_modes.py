from __future__ import annotations

"""采集库归入业务数据，新增按批次保留历史的写入方式。"""

import django.db.models.deletion
from django.db import migrations, models


def classify_existing(apps, schema_editor):
    """只移动自动创建的采集库，已有追踪行和用户手册库保持原样。"""
    Feed=apps.get_model('kb','KnowledgeFeed');Library=apps.get_model('kb','KnowledgeBase')
    Library.objects.filter(slug__startswith='auto-',is_folder=True,parent__isnull=True,
        feeds__isnull=False).update(category='business')
    for feed in Feed.objects.exclude(adapter_spec={}):
        spec=dict(feed.adapter_spec)
        mode=spec.get('write_mode','append')
        if mode not in ('append','upsert'):mode='append'
        spec['write_mode']=mode
        Feed.objects.filter(pk=feed.pk).update(write_mode=mode,adapter_spec=spec)
    Draft=apps.get_model('kb','ConnectorDraft')
    for draft in Draft.objects.exclude(feed_id=None).exclude(spec={}):
        mode=Feed.objects.get(pk=draft.feed_id).write_mode
        Draft.objects.filter(pk=draft.pk).update(spec={**draft.spec,'write_mode':mode})


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0042_connector_saved_context"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="trackerrow",
            name="unique_tracker_feed_item",
        ),
        migrations.AddField(
            model_name="knowledgebase",
            name="category",
            field=models.CharField(
                choices=[("manual", "手册库"), ("business", "业务数据")],
                default="manual",
                max_length=16,
                verbose_name="资料分类",
            ),
        ),
        migrations.AddField(
            model_name="knowledgefeed",
            name="write_mode",
            field=models.CharField(
                choices=[
                    ("append", "追加新记录（保留历史）"),
                    ("upsert", "覆盖更新相同 ID"),
                ],
                default="append",
                max_length=10,
                verbose_name="写入模式",
            ),
        ),
        migrations.AddField(
            model_name="trackerrow",
            name="feed_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="tracker_rows",
                to="kb.feedrun",
            ),
        ),
        migrations.AddConstraint(
            model_name="trackerrow",
            constraint=models.UniqueConstraint(
                condition=models.Q(("feed_run__isnull", True)),
                fields=("tracker", "feed", "external_id"),
                name="unique_tracker_feed_current",
            ),
        ),
        migrations.AddConstraint(
            model_name="trackerrow",
            constraint=models.UniqueConstraint(
                fields=("tracker", "feed", "external_id", "feed_run"),
                name="unique_tracker_feed_snapshot",
            ),
        ),
        migrations.RunPython(classify_existing,migrations.RunPython.noop),
    ]
