from django.db import migrations,models


def retain_existing(apps,schema_editor):
    Draft=apps.get_model('kb','ConnectorDraft');ids=set(Draft.objects.exclude(feed=None).values_list('pk',flat=True));pending=set(ids)
    while pending:
        related=set(Draft.objects.filter(pk__in=pending).exclude(parent=None).values_list('parent_id',flat=True))
        related.update(Draft.objects.filter(parent_id__in=pending).values_list('pk',flat=True));related-=ids
        ids.update(related);pending=related
    Draft.objects.filter(pk__in=ids).update(retain_context=True)


class Migration(migrations.Migration):
    dependencies=[('kb','0041_tracker_feed_rows')]
    operations=[
        migrations.AddField(model_name='connectordraft',name='retain_context',field=models.BooleanField('随定时任务保留对话',default=False)),
        migrations.AddField(model_name='connectordraft',name='context_snapshot',field=models.TextField('加密代理上下文',blank=True)),
        migrations.RunPython(retain_existing,migrations.RunPython.noop),
    ]
