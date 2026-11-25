from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("economy", "0002_cointransaction_created_by_cointransaction_ref_type_and_more")]

    operations = [
        migrations.AlterField(
            model_name="cointransaction",
            name="type",
            field=models.CharField(
                choices=[
                    ("PURCHASE", "Purchase"),
                    ("AD_REWARD", "Ad Reward"),
                    ("DEBATE_CREATE", "Debate Create"),
                    ("REWARD", "Reward"),
                    ("SIGNUP_BONUS", "Signup Bonus"),
                    ("ADMIN_GRANT", "Admin Grant"),
                    ("REFUND", "Refund"),
                    ("WEEKLY_REFILL", "Weekly Refill"),
                ],
                max_length=13,
            ),
        ),
    ]
