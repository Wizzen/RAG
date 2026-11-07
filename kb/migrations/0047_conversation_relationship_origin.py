from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('kb', '0046_knowledgebaselink_optional_summary')]
    operations = [migrations.AddField(model_name='conversation', name='relationship_origin',
        field=models.UUIDField(blank=True, editable=False, null=True, verbose_name='关联检索起点'))]
