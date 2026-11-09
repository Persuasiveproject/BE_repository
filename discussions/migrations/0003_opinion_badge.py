import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("discussions", "0002_alter_aisummary_stance"),
        ("economy", "0002_cointransaction_created_by_cointransaction_ref_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="opinion",
            name="badge",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="economy.badge",
            ),
        ),
    ]
