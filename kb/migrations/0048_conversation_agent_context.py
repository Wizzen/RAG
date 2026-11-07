from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('kb', '0047_conversation_relationship_origin')]
    operations = [migrations.AddField(
        model_name='conversation', name='agent_context',
        field=models.JSONField('模型会话上下文', default=dict, blank=True, editable=False),
    )]
