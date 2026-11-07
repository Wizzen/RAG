from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('kb', '0044_connectordraft_created_via'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name='KnowledgeBaseLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('summary', models.CharField(max_length=1000, verbose_name='关系说明')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('left', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links_as_left', to='kb.knowledgebase')),
                ('right', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='links_as_right', to='kb.knowledgebase')),
            ],
            options={'verbose_name': '知识库关联', 'verbose_name_plural': '知识库关联', 'ordering': ['pk'],
                     'constraints': [models.UniqueConstraint(fields=('left', 'right'), name='unique_kb_link_pair'),
                                     models.CheckConstraint(condition=models.Q(('left__lt', models.F('right'))), name='ordered_distinct_kb_link')]},
        ),
    ]
