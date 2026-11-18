from django.conf import settings
from django.db import models
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager


class UserManager(BaseUserManager):
    def create_user(self, email, nickname, password=None, **extra_fields):
        if not email:
            raise ValueError("이메일이 필요합니다.")
        if not nickname:
            raise ValueError("닉네임이 필요합니다.")

        user = self.model(
            email=self.normalize_email(email),
            nickname=nickname,
            **extra_fields,
        )
        # 소셜 회원은 비밀번호 로그인을 사용할 수 없도록 저장한다.
        user.set_password(password)
        user.save(using=self._db)
        return user


class User(AbstractBaseUser):
    class Gender(models.TextChoices):
        MALE = "M", "남성"
        FEMALE = "F", "여성"
        OTHER = "OTHER", "기타"

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "정상"
        SUSPENDED = "SUSPENDED", "이용 제한"
        DELETED = "DELETED", "탈퇴"

    class Role(models.TextChoices):
        USER = "USER", "회원"
        ADMIN = "ADMIN", "관리자"

    # ERD에 없는 AbstractBaseUser의 기본 컬럼을 제거한다.
    last_login = None

    id = models.BigAutoField(primary_key=True)
    email = models.EmailField(max_length=255)
    password = models.CharField(
        max_length=255,
        db_column="password_hash",
        null=True,
        blank=True,
    )
    provider = models.CharField(max_length=20, null=True, blank=True)
    provider_id = models.CharField(max_length=255, null=True, blank=True)
    nickname = models.CharField(max_length=30)
    profile_image_url = models.URLField(max_length=500, null=True, blank=True)
    gender = models.CharField(
        max_length=5, choices=Gender.choices, null=True, blank=True
    )
    birth_year = models.IntegerField(null=True, blank=True)
    region = models.CharField(max_length=30, null=True, blank=True)
    credit = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    coin_balance = models.IntegerField(default=0)
    notification_enabled = models.BooleanField(default=True)
    status = models.CharField(
        max_length=9, choices=Status.choices, default=Status.ACTIVE
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    role = models.CharField(max_length=5, choices=Role.choices, default=Role.USER)
    statistics_consent = models.BooleanField(default=False)
    statistics_consent_updated_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    # 내부 ID는 Django 회원 식별용이며, 카카오 인증은 provider/provider_id로 조회한다.
    USERNAME_FIELD = "id"
    REQUIRED_FIELDS = ["email", "nickname"]

    class Meta:
        db_table = "users"

    @property
    def is_active(self):
        # 제한 회원도 로그인 후 삭제·신고·탈퇴는 가능해야 한다.
        return self.status != self.Status.DELETED

    @property
    def is_staff(self):
        return self.status == self.Status.ACTIVE and self.role == self.Role.ADMIN

    def has_perm(self, perm, obj=None):
        return self.is_staff

    def has_module_perms(self, app_label):
        return self.is_staff

    def __str__(self):
        return self.nickname


class Term(models.Model):
    class Type(models.TextChoices):
        SERVICE = "SERVICE"
        PRIVACY = "PRIVACY"
        MARKETING = "MARKETING"

    type = models.CharField(max_length=9, choices=Type.choices)
    version = models.CharField(max_length=20)
    content = models.TextField()
    is_required = models.BooleanField()
    effective_at = models.DateTimeField()

    class Meta:
        db_table = "terms"
        constraints = [
            models.UniqueConstraint(
                fields=["type", "version"],
                name="term_type_version_uniq",
            ),
        ]


class UserTermAgreement(models.Model):
    pk = models.CompositePrimaryKey("user_id", "term_id")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    term = models.ForeignKey("accounts.Term", on_delete=models.CASCADE)
    agreed = models.BooleanField()
    agreed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_term_agreements"


class UserInterestCategory(models.Model):
    pk = models.CompositePrimaryKey("user_id", "category_id")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    category = models.ForeignKey("debates.Category", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_interest_categories"


class UserIdentityVerification(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING"
        VERIFIED = "VERIFIED"
        FAILED = "FAILED"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True,
    )
    provider = models.CharField(max_length=30)
    request_id = models.CharField(max_length=128)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    identity_key_hash = models.CharField(max_length=64, null=True, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    verified_at = models.DateTimeField(null=True, blank=True)
    failure_code = models.CharField(max_length=50, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_identity_verifications"


class APIIdempotencyRecord(models.Model):
    class Status(models.TextChoices):
        PROCESSING = "PROCESSING"
        COMPLETED = "COMPLETED"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    http_method = models.CharField(max_length=10)
    request_path = models.CharField(max_length=255)
    idempotency_key = models.CharField(max_length=128)
    request_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PROCESSING)
    response_status = models.SmallIntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "api_idempotency_records"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "http_method", "request_path", "idempotency_key"],
                name="api_idempotency_scope_uniq",
            ),
        ]
