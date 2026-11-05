from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("debates", "0004_debate_result_retry"),
    ]

    operations = [
        migrations.RemoveField(model_name="debate", name="result_retry_at"),
    ]
