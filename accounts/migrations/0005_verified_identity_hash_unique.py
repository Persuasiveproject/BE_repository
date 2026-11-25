from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_user_provider_identity_unique")]
    operations = [
        migrations.AddConstraint(
            model_name="useridentityverification",
            constraint=models.UniqueConstraint(
                fields=("identity_key_hash",),
                condition=models.Q(status="VERIFIED", identity_key_hash__isnull=False),
                name="verified_identity_hash_uniq",
            ),
        ),
    ]
