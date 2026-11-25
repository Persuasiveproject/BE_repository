from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_useridentityverification_user_role_and_more")]

    operations = [
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("provider", "provider_id"), name="user_provider_identity_uniq",
            ),
        ),
    ]
