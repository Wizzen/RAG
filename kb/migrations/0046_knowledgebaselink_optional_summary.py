from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('kb', '0045_knowledgebaselink')]

    operations = [
        migrations.AlterField(
            model_name='knowledgebaselink', name='summary',
            field=models.CharField(blank=True, default='', max_length=1000, verbose_name='关系说明'),
        ),
    ]
