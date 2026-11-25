from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("economy", "0002_cointransaction_created_by_cointransaction_ref_type_and_more")]

    operations = [
        migrations.AddField(
            model_name="credithistory",
            name="credit_after",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True),
        ),
    ]
