from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_useridentityverification_user_role_and_more")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_weekly_coin_refill_on",
            field=models.DateField(blank=True, null=True),
        ),
    ]
