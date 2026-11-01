from django.db import migrations,models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies=[('kb','0040_connector_activity')]
    operations=[
        migrations.AlterField(model_name='trackerrow',name='document',field=models.ForeignKey(null=True,blank=True,on_delete=django.db.models.deletion.CASCADE,related_name='tracker_rows',to='kb.document')),
        migrations.AddField(model_name='trackerrow',name='feed',field=models.ForeignKey(null=True,blank=True,on_delete=django.db.models.deletion.CASCADE,related_name='tracker_rows',to='kb.knowledgefeed')),
        migrations.AddField(model_name='trackerrow',name='external_id',field=models.CharField(max_length=200,blank=True,default='')),
        migrations.AddConstraint(model_name='trackerrow',constraint=models.UniqueConstraint(fields=('tracker','feed','external_id'),name='unique_tracker_feed_item')),
    ]
