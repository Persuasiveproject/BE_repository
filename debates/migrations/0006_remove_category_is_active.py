from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("debates", "0005_remove_debate_result_retry_at"),
    ]

    operations = [
        migrations.RemoveField(model_name="category", name="is_active"),
    ]
